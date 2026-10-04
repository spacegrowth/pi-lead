"""The checks `pilead doctor` runs: read-only, never touches a model, never repairs anything.

A check is a function `check(ctx) -> [result, ...]`, `result` a dict `{"check", "status", "detail",
"info"}`. `ctx` (a `Context`) carries everything a check reads from the outside world — `home`, `root`
(the package root), `env` (a dict), `python_version`, `app_dirs`, `offline`, `run` (starts a program,
never a shell) and `now` (a clock) — so a test replaces it instead of depending on this machine.
`CHECKS` is the ordered list of check functions (each tagged with its display name via `check_name`,
for the `NOT CHECKED` fallback below); a later packet adds a check by appending to it.

A check never raises: `run_checks` wraps each call and turns an exception into that check's own
`NOT CHECKED (<exception type>: <first line>)` result, and keeps running the rest. `NOT CHECKED` means
exactly that a reading could not be made — it is never counted or worded as a pass.

The only thing doctor writes anywhere is one empty `tempfile.mkdtemp` directory (`Context.throwaway_dir`,
created lazily, at most once per run) used as the working directory — and, for the pi probe, the
`PI_LEAD_HOME` — of the two probes that must start a program (the git shim's refusal probe, check 17;
the `pi --mode rpc` probe, checks 19-21). It is removed before `run_checks` returns, success or not.
"""
import importlib
import json
import math
import os
import platform as platform_mod
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config as config_mod
from . import ledger as ledger_mod
from . import models as models_mod
from . import refs as refs_mod
from . import shim as shim_mod
from . import state
from . import usage as usage_mod

PASS, WARN, FAIL, SKIP, NOT_CHECKED = "PASS", "WARN", "FAIL", "SKIP", "NOT CHECKED"

MIN_GIT = (2, 31)
MIN_PI = (0, 87)
VERSION_PROBE_TIMEOUT = 10
REFUSAL_PROBE_TIMEOUT = 10
PI_PROBE_TIMEOUT = 20
PI_PROBE_KILL_GRACE = 1.0  # seconds to wait after terminate, then again after kill

# The 36 skill commands (extensions/pi-lead.ts SKILL_NAMES, in the same order) and the 38 `/pilead:<x>`
# commands the extension registers for a `role: "lead"` session — those 36 plus `status` and `route`.
# `route` is also a skill FILE (it explains the extension command), so there are 37 skill files in all.
SKILL_NAMES = (
    "mode", "spawn", "send", "check", "list", "review", "verify", "close", "diff", "board",
    "auto", "tier", "plan", "handoff", "restart", "resume", "retire", "focus", "stop",
    "adopt", "auto-close", "close-predecessor", "doctor", "gate", "keep", "lineup", "lint", "notify", "prune", "queue",
    "refs", "report", "stats", "takeover", "tidy", "whoami",
)
COMMAND_NAMES = SKILL_NAMES + ("status", "route")
SKILL_FILES = SKILL_NAMES + ("route",)


def _result(name, status, detail="", info=()):
    return {"check": name, "status": status, "detail": detail, "info": list(info)}


def _named(name):
    """Tags a check function with the display name `run_checks` uses when the function raises before
    producing its own result."""
    def deco(fn):
        fn.check_name = name
        return fn
    return deco


# ── running a program: the one gateway every check goes through ────────────────────────────────────
@dataclass
class RunResult:
    ok: bool
    returncode: int = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""  # set when ok is False: "timeout" or the reason the program could not be started


def real_run(argv, cwd=None, env=None, timeout=None):
    """Runs `argv` (never through a shell), waiting up to `timeout` seconds. `stdin` is always closed
    (`/dev/null`): none of doctor's simple probes read it. Returns a `RunResult`; never raises."""
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, error="timeout")
    except OSError as e:
        return RunResult(ok=False, error=str(e))
    return RunResult(ok=True, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)


# ── the context ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class Context:
    home: Path
    root: Path
    env: dict
    python_version: tuple
    app_dirs: tuple
    offline: bool
    run: Callable[..., RunResult]
    now: Callable[[], float]
    quick: bool = False  # --quick: the three checks that wait for pi's list of commands are skipped
    platform: str = field(default_factory=platform_mod.system)  # "Darwin", "Linux", …
    _throwaway: Path = field(default=None, init=False, repr=False)
    _pi_probe_cache: dict = field(default=None, init=False, repr=False)

    def throwaway_dir(self):
        """One empty directory, made on first use and reused for the rest of this run."""
        if self._throwaway is None:
            self._throwaway = Path(tempfile.mkdtemp(prefix="pilead-doctor-"))
        return self._throwaway

    def cleanup(self):
        if self._throwaway is not None:
            shutil.rmtree(self._throwaway, ignore_errors=True)
            self._throwaway = None

    def pi_probe(self):
        """The one `pi --mode rpc --no-session` probe checks 19-21 share, run at most once and cached
        on this context (never raises: an internal failure becomes a normal `{"ok": False, ...}`)."""
        if self._pi_probe_cache is None:
            try:
                self._pi_probe_cache = _run_pi_probe(self)
            except Exception as e:  # noqa: BLE001 — the probe result must never raise into a check
                msg = str(e).splitlines()[0] if str(e) else ""
                self._pi_probe_cache = {"ok": False, "reason": f"{type(e).__name__}: {msg}"}
        return self._pi_probe_cache


def build_context(home, offline=False):
    return Context(home=Path(home), root=Path(state.ROOT), env=dict(os.environ),
                   python_version=tuple(sys.version_info[:3]),
                   app_dirs=(Path("/Applications"), Path.home() / "Applications"),
                   offline=offline, run=real_run, now=time.time, platform=platform_mod.system())


def _which(ctx, name):
    return shutil.which(name, path=ctx.env.get("PATH"))


def _exit_detail(r):
    """`exited <code>: <first line of stderr>` for a program that ran and exited non-zero — which is
    never a pass, whatever its stdout holds."""
    lines = (r.stderr or "").strip().splitlines()
    first = lines[0].strip()[:120] if lines else "(no stderr)"
    return f"exited {r.returncode}: {first}"


def _version_pair(text):
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


# ── 1. python ────────────────────────────────────────────────────────────────────────────────────
@_named("python")
def check_python(ctx):
    v = tuple(ctx.python_version)
    detail = f"{'.'.join(str(x) for x in v)} at {sys.executable}"
    if v[:2] >= (3, 9):
        return [_result("python", PASS, detail)]
    return [_result("python", FAIL, detail)]


# ── 2. package files ─────────────────────────────────────────────────────────────────────────────
@_named("package files")
def check_package_files(ctx):
    root = Path(ctx.root)
    required = [root / "extensions" / "pi-lead.ts", root / "skills" / "executor-rules.md",
                root / "vendor" / "relay" / "backend.py", root / "vendor" / "relay" / "tmux_backend.py",
                root / "vendor" / "relay" / "platform_cmds.py"]
    missing = [str(p.relative_to(root)) for p in required if not p.is_file()]
    mismatched = []
    for name in SKILL_FILES:
        skill_path = root / "skills" / f"pilead-{name}" / "SKILL.md"
        if not skill_path.is_file():
            missing.append(str(skill_path.relative_to(root)))
            continue
        try:
            text = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing.append(str(skill_path.relative_to(root)))
            continue
        m = re.search(r"^name:\s*(.+?)\s*$", text, re.MULTILINE)
        if not m or m.group(1) != f"pilead-{name}":
            mismatched.append(str(skill_path.relative_to(root)))
    if not missing and not mismatched:
        return [_result("package files", PASS)]
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if mismatched:
        parts.append("frontmatter name mismatch: " + ", ".join(mismatched))
    return [_result("package files", FAIL, "; ".join(parts))]


# ── 3. verb modules ──────────────────────────────────────────────────────────────────────────────
@_named("verb modules")
def check_verb_modules(ctx):
    import argparse
    import pkgutil

    from . import verbs as verbs_pkg

    ap = argparse.ArgumentParser(add_help=False)
    sub = ap.add_subparsers(dest="verb")
    common = argparse.ArgumentParser(add_help=False)

    def verb(name, fn, help_):
        p = sub.add_parser(name, parents=[common], help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    failed = []
    for m in pkgutil.iter_modules(verbs_pkg.__path__):
        if m.name.startswith("_"):
            continue
        mod_name = f"{verbs_pkg.__name__}.{m.name}"
        try:
            mod = importlib.import_module(mod_name)
            if not isinstance(getattr(mod, "ORDER", None), int):
                raise AttributeError("no integer ORDER")
            if not callable(getattr(mod, "register", None)):
                raise AttributeError("no callable register")
            mod.register(verb)
        except Exception as e:  # noqa: BLE001 — one broken verb must not stop this check
            msg = " ".join(str(e).split())
            failed.append(f"{m.name}: {type(e).__name__}: {msg}".rstrip(": "))
    if failed:
        return [_result("verb modules", FAIL, "; ".join(failed))]
    return [_result("verb modules", PASS)]


# ── 4. pilead on PATH ────────────────────────────────────────────────────────────────────────────
@_named("pilead on PATH")
def check_pilead_on_path(ctx):
    want = (Path(ctx.root) / "bin" / "pilead").resolve()
    found = _which(ctx, "pilead")
    if not found:
        return [_result("pilead on PATH", WARN, "not on PATH — inside a pi session the extension adds it")]
    found_real = Path(found).resolve()
    if found_real != want:
        return [_result("pilead on PATH", WARN,
                         f"PATH resolves pilead to {found_real}, not this package's {want}")]
    return [_result("pilead on PATH", PASS, str(found_real))]


# ── 5. git ───────────────────────────────────────────────────────────────────────────────────────
@_named("git")
def check_git(ctx):
    try:
        path = refs_mod.git_path(ctx.env.get("PATH", ""))
    except refs_mod.RefsError:
        return [_result("git", FAIL, "no real git found on PATH")]
    r = ctx.run([path, "--version"], timeout=VERSION_PROBE_TIMEOUT)
    if not r.ok:
        return [_result("git", NOT_CHECKED, f"{path} --version could not be run: {r.error}")]
    if r.returncode != 0:
        return [_result("git", FAIL, f"{path} --version {_exit_detail(r)}")]
    ver = _version_pair(r.stdout)
    if ver is None:
        return [_result("git", NOT_CHECKED, f"git --version output not understood: {r.stdout.strip()[:80]!r}")]
    detail = f"{path} — {r.stdout.strip()}"
    if ver < MIN_GIT:
        return [_result("git", FAIL, detail + " — refs detection needs `rev-parse --path-format`")]
    return [_result("git", PASS, detail)]


# ── the terminal backend this run selects (b12) ─────────────────────────────────────────────────────
def _backend_module():
    """The vendored `backend` selector (vendor/relay/backend.py)."""
    from .launch import backend
    return backend


def _selected_backend(ctx):
    """The NAME of the backend `pilead` selects with this context's env and config `terminal_app`
    (vendor/relay/backend.py `select`)."""
    return _backend_module().select(env=ctx.env, configured=config_mod.terminal_app(ctx.home)).NAME


def _iterm_skip(ctx, name):
    """The SKIP result the iTerm-only rows give under tmux or on Linux, else None."""
    if ctx.platform == "Linux":
        return [_result(name, SKIP, "Linux")]
    if _selected_backend(ctx) == "tmux":
        return [_result(name, SKIP, "tmux backend selected")]
    return None


# ── 6. iTerm2 ────────────────────────────────────────────────────────────────────────────────────
@_named("iTerm2")
def check_iterm2(ctx):
    skip = _iterm_skip(ctx, "iTerm2")
    if skip:
        return skip
    for d in ctx.app_dirs:
        app = Path(d) / "iTerm.app"
        if app.exists():
            return [_result("iTerm2", PASS, str(app))]
    if ctx.env.get("TERM_PROGRAM") == "iTerm.app":
        return [_result("iTerm2", PASS, "running inside iTerm2 (TERM_PROGRAM=iTerm.app)")]
    return [_result("iTerm2", FAIL, "iTerm2 is a requirement; iTerm.app not found in "
                     + ", ".join(str(d) for d in ctx.app_dirs))]


# ── 7. iTerm2 Python API ────────────────────────────────────────────────────────────────────────
def _iterm2_spec():
    """Whether the `iterm2` module can be found — never imported, never connected. A thin, separately
    named function so a test can replace it directly, instead of fighting this process's real
    `sys.path`/`sys.modules` (which pytest's own fixtures may already have opinions about)."""
    try:
        return importlib.util.find_spec("iterm2")
    except (ImportError, ValueError):
        return None


@_named("iTerm2 Python API")
def check_iterm2_python_api(ctx):
    skip = _iterm_skip(ctx, "iTerm2 Python API")
    if skip:
        return skip
    spec = _iterm2_spec()
    if spec is not None:
        return [_result("iTerm2 Python API", PASS, getattr(spec, "origin", None) or "found")]
    return [_result("iTerm2 Python API", WARN, "executor tabs open at the end of the tab bar; "
                     "`pip3 install iterm2` and enable the Python API")]


# ── 8. osascript ─────────────────────────────────────────────────────────────────────────────────
def _osascript_fallback_exists():
    """Separately named (like `_iterm2_spec`) so a test can replace it, instead of depending on
    whether this real machine has `/usr/bin/osascript` (every real Mac does)."""
    return Path("/usr/bin/osascript").exists()


@_named("osascript")
def check_osascript(ctx):
    skip = _iterm_skip(ctx, "osascript")
    if skip:
        return skip
    found = _which(ctx, "osascript")
    if not found and _osascript_fallback_exists():
        found = "/usr/bin/osascript"
    if found:
        return [_result("osascript", PASS, found)]
    return [_result("osascript", FAIL, "osascript not found on PATH or at /usr/bin/osascript")]


# ── 9. home ──────────────────────────────────────────────────────────────────────────────────────
@_named("home")
def check_home(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("home", PASS, "not created yet; the first `pilead lead-start` creates it")]
    if not home.is_dir():
        return [_result("home", FAIL, f"{home} exists and is not a directory")]
    missing = [w for w, ok in (("readable", os.access(home, os.R_OK)), ("writable", os.access(home, os.W_OK)))
               if not ok]
    if missing:
        return [_result("home", FAIL, f"{home} is not {' or '.join(missing)}")]
    return [_result("home", PASS, str(home))]


# ── 10. config.json ──────────────────────────────────────────────────────────────────────────────
class _Const:
    """A bare NaN/Infinity/-Infinity literal in config.json (Python's json parser accepts these
    tokens; JSON itself does not) — a stand-in value so where it sits can be named."""

    __slots__ = ("token",)

    def __init__(self, token):
        self.token = token

    def __repr__(self):
        return self.token


def _const_locations(data):
    """`(path, token)` for every `_Const` left in `data`, at any depth, in document order — e.g.
    `grace_seconds`, `signoff_paths[0]`, `a.b[2]`. Iterative, so a deeply nested file cannot make it
    raise RecursionError."""
    found = []
    stack = [("", data)]
    while stack:
        path, v = stack.pop()
        if isinstance(v, _Const):
            found.append((path or "(top level)", v.token))
        elif isinstance(v, dict):
            for k, x in reversed(list(v.items())):
                stack.append((f"{path}.{k}" if path else str(k), x))
        elif isinstance(v, list):
            for i, x in reversed(list(enumerate(v))):
                stack.append((f"{path}[{i}]", x))
    return found


def _cut_json(text, room):
    """The longest start of JSON text `text` of at most `room` characters that does not end inside a
    backslash escape (`\\n`, `\\u2026`)."""
    i, in_str = 0, False
    while i < len(text):
        ch, step = text[i], 1
        if in_str and ch == "\\":
            step = 6 if text[i + 1:i + 2] == "u" else 2
        elif ch == '"':
            in_str = not in_str
        if i + step > room:
            break
        i += step
    return text[:i]


def _show(value, limit=60):
    """`value` as JSON text for a message, at most `limit` characters. A value that does not fit is cut
    and ends with `…`, whatever its type: a string is cut on its own text BEFORE it is escaped, then
    closed with `…"` (so a cut never falls inside an escape, and the message never ends inside an open
    quote); any other value is cut on its JSON text, never inside an escape, then `…`. A value that
    cannot be shown (json.dumps raising for any reason) is shown by its JSON type instead. Never
    raises."""
    try:
        text = json.dumps(value)
        if len(text) <= limit:
            return text
        if isinstance(value, str):
            k = min(len(value), limit)
            while k > 0 and len(json.dumps(value[:k])) + 1 > limit:  # + 1: the closing quote becomes `…"`
                k -= 1
            return json.dumps(value[:k])[:-1] + "…\""
        return _cut_json(text, max(limit - 1, 0)) + "…"
    except Exception:  # noqa: BLE001 — a message must never take the check down
        try:
            return f"<a {config_mod._json_type(value)} value>"
        except Exception:  # noqa: BLE001
            return "<a value>"


def _reject_reason(key, default, value):
    """Same evaluation as `config._accepts`, but the reason it failed instead of a bare bool. A key's
    range function is found the way `config._accepts` does: in `config.RANGES`, else in
    `config.CLI_RANGES`. A range function that raises gives "not a usable number", one that returns
    false gives "outside its range" — for a key of either dict. A string for `usage_limit_pattern`
    that does not compile as a regular expression gives "not a valid pattern"."""
    want, got = config_mod._json_type(default), config_mod._json_type(value)
    if key == "usage_limit_pattern" and got == "string":
        try:
            re.compile(value)
        except re.error:
            return "not a valid pattern"
    if not (got == want or (want == "null" and got == "string")):
        return f"wrong type: {got}, expected {want}"
    if key in config_mod.CHOICES and str(value).lower() not in config_mod.CHOICES[key]:
        return "not one of " + ", ".join(config_mod.CHOICES[key])
    if got == "number":
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError, ValueError):
            return "not a usable number"
        if not finite:
            return "not a usable number"
    rng = config_mod.RANGES.get(key)
    if rng is None:
        rng = config_mod.CLI_RANGES.get(key)
    if rng is not None:
        try:
            ok = bool(rng(value))
        except Exception:  # noqa: BLE001
            return "not a usable number"
        if not ok:
            return "outside its range"
    return "outside its range"


@_named("config.json")
def check_config(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("config.json", SKIP, "home does not exist")]
    p = config_mod.path(home)
    if not p.is_file():
        return [_result("config.json", PASS, "no file: the defaults apply")]
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [_result("config.json", FAIL, f"cannot read {p}: {e}")]
    tokens = []

    def _on_constant(token):
        # Called by the parser for every bare NaN/Infinity/-Infinity token in the text, at any depth
        # — including one a later duplicate key then replaces, which JSON.parse rejects all the same.
        # The word inside a string value never reaches this hook.
        tokens.append(token)
        return _Const(token)

    try:
        marked = json.loads(raw, parse_constant=_on_constant)
        data = json.loads(raw)  # each literal as a float, as the Python CLI's own json.loads reads it
    except ValueError as e:
        return [_result("config.json", FAIL,
                         f"{e}; pilead and the extension both use the default for every key")]
    if not isinstance(data, dict):
        return [_result("config.json", FAIL, f"top level is a {config_mod._json_type(data)}, not an "
                         "object; pilead and the extension both use the default for every key")]
    status = FAIL if tokens else PASS
    info = []
    for k, v in data.items():
        if k not in config_mod.DEFAULTS:
            info.append(f"{k}: not a pi-lead key; ignored")
            if status == PASS:
                status = WARN
            continue
        default = config_mod.DEFAULTS[k]
        if not config_mod._accepts(k, default, v):
            reason = _reject_reason(k, default, v)
            info.append(f"{k}: {_show(v)} ignored ({reason}); the default {_show(default)} applies")
            status = FAIL
    detail = ""
    if tokens:
        literals = ", ".join(dict.fromkeys(tokens))
        where = ", ".join(path for path, _ in _const_locations(marked))
        where = f"at {where}" if where else "in a value a later duplicate key replaces"
        detail = (f"not JSON: bare {literals} {where} — the pi extension's JSON.parse rejects it, so "
                  "the extension ignores the whole file and uses the default for every key; the Python "
                  "CLI (pilead) accepts it and does not ignore the file")
    return [_result("config.json", status, detail, info)]


# ── terminal backend / linux helpers (claude-relay 0.5.6 / 0.5.7 doctor rows) ───────────────────────────
@_named("terminal backend")
def check_terminal_backend(ctx):
    """Which terminal this invocation drives, and the tmux facts that decide whether the tmux backend can work
    here — advisory only (always PASS; nothing is gated on it). On Linux the `linux helpers` row follows it. `tmux -V` is the only program it starts, and
    only when `tmux` is on PATH; like the tmux backend's own calls it carries `-L <socket>` when
    $PILEAD_TMUX_SOCKET / $RELAY_TMUX_SOCKET is set (-V never reaches a server)."""
    name = _selected_backend(ctx)
    ver = None
    if _which(ctx, "tmux"):
        sock = ctx.env.get("PILEAD_TMUX_SOCKET") or ctx.env.get("RELAY_TMUX_SOCKET")
        argv = ["tmux"] + (["-L", sock] if sock else []) + ["-V"]
        r = ctx.run(argv, timeout=VERSION_PROBE_TIMEOUT)
        if r.ok and r.returncode == 0 and (r.stdout or "").strip():
            ver = r.stdout.strip()
        else:
            ver = f"(error: {r.error or _exit_detail(r)})"
    detail = (f"selected: {name} · tmux: {ver or 'not on PATH'} · "
              f"$TMUX={ctx.env.get('TMUX') or '(unset)'} · "
              f"$PILEAD_TMUX_SOCKET={ctx.env.get('PILEAD_TMUX_SOCKET') or '(unset)'} · "
              f"$RELAY_TMUX_SOCKET={ctx.env.get('RELAY_TMUX_SOCKET') or '(unset)'}")
    if ctx.platform == "Linux" and name != "tmux":
        detail += " — on Linux pi-lead needs tmux: run it inside tmux or set PILEAD_TERMINAL=tmux"
    # `linux helpers` rides in this entry of CHECKS (one entry, one result everywhere but Linux)
    return [_result("terminal backend", PASS, detail)] + check_linux_helpers(ctx)


def _linux_helpers():
    """{helper: on PATH?} (vendor/relay/platform_cmds.py `linux_helpers`); separately named so a test replaces it."""
    _backend_module()  # vendor/relay is on sys.path
    import platform_cmds
    return platform_cmds.linux_helpers()


@_named("linux helpers")
def check_linux_helpers(ctx):
    """Only on Linux (no row elsewhere): the optional helpers pi-lead uses when present — advisory, PASS."""
    if ctx.platform != "Linux":
        return []
    h = _linux_helpers()
    clip = "xclip" if h.get("xclip") else "wl-copy" if h.get("wl-copy") else "no"
    return [_result("linux helpers", PASS,
                    f"xdg-open: {'yes' if h.get('xdg-open') else 'no'} (--open) · clipboard: {clip} · "
                    f"notify-send: {'yes' if h.get('notify-send') else 'no'} (banner fallback) — none required")]


# ── 11. models.json ──────────────────────────────────────────────────────────────────────────────
def _is_timestamp(v):
    """Whether models.json's `fetched` is a usable number: a finite int or float, not a boolean
    (JSON's true/false is not a number, though Python's bool is an int). Never raises."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except (OverflowError, TypeError, ValueError):
        return False


@_named("models.json")
def check_models(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("models.json", SKIP, "home does not exist")]
    p = home / "models.json"
    reason = None
    try:
        d = json.loads(p.read_text())
        models = d["models"]
        fetched = d["fetched"]
    except OSError:
        reason = "absent"
    except (ValueError, KeyError, TypeError):
        reason = "unparsable"
    else:
        if not _is_timestamp(fetched) or not isinstance(models, list):
            reason = "unparsable"
        elif not models:
            reason = "empty"
        else:
            age = ctx.now() - fetched
            if age >= models_mod.MAX_AGE:
                reason = "older"
            else:
                return [_result("models.json", PASS, f"{len(models)} models, {int(age)}s old")]
    return [_result("models.json", WARN, f"{reason}: the next spawn that uses an alias fetches it")]


# ── 12. model aliases ────────────────────────────────────────────────────────────────────────────
@_named("model aliases")
def check_model_aliases(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("model aliases", SKIP, "home does not exist")]
    p = home / "models.json"
    try:
        d = json.loads(p.read_text())
        models, fetched = d["models"], d["fetched"]
        if not _is_timestamp(fetched) or not isinstance(models, list):
            raise ValueError("unparsable")
        if not models:
            raise ValueError("empty")
    except (OSError, ValueError, KeyError, TypeError):
        return [_result("model aliases", SKIP, "models.json check did not pass")]
    age = ctx.now() - fetched
    if age >= models_mod.MAX_AGE:
        return [_result("model aliases", SKIP, "models.json check did not pass")]
    if age >= models_mod.MAX_AGE - 60:
        return [_result("model aliases", SKIP, "models.json is about to go stale")]
    info, unresolved = [], []
    for alias in models_mod.ALIASES:
        try:
            info.append(f"{alias} → {models_mod.resolve(home, alias)}")
        except state.PileadError:
            unresolved.append(alias)
    status = WARN if unresolved else PASS
    detail = f"{', '.join(unresolved)} matched no model in models.json" if unresolved else ""
    return [_result("model aliases", status, detail, info)]


# ── 13. ledger ───────────────────────────────────────────────────────────────────────────────────
@_named("ledger")
def check_ledger(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("ledger", SKIP, "home does not exist")]
    p = home / ledger_mod.NAME
    if not p.is_file():
        return [_result("ledger", PASS, "no ledger yet")]
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [_result("ledger", FAIL, f"cannot read {p}: {e}")]
    good, bad, first_bad = 0, 0, None
    for i, line in enumerate(text.splitlines(), start=1):
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            good += 1
        else:
            bad += 1
            if first_bad is None:
                first_bad = i
    if bad:
        return [_result("ledger", WARN, f"{good} record(s), {bad} line(s) not a JSON object "
                         f"(first: line {first_bad}); readers skip them")]
    return [_result("ledger", PASS, f"{good} record(s)")]


# ── one definition of "open", shared by every check below ──────────────────────────────────────
def _is_open(home, sid, meta):
    """A session is open when its stored status (`state.read_status`) is neither `closed` nor `dead`.
    Doctor reads the stored status only; it never recomputes or writes one."""
    return state.read_status(home, sid, meta) not in ("closed", "dead")


# ── 14. state ────────────────────────────────────────────────────────────────────────────────────
@_named("state")
def check_state(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("state", SKIP, "home does not exist")]
    bad = []
    leads = {}
    leads_dir = home / "leads"
    for p in sorted(leads_dir.glob("*.json")) if leads_dir.is_dir() else []:
        if not usage_mod.is_lead_record(p):
            continue  # a usage cache an older build left under leads/ — not a lead record
        try:
            d = json.loads(p.read_text())
            if not isinstance(d, dict):
                raise ValueError("top level is not an object")
            leads[p.stem] = d
        except (OSError, ValueError):
            bad.append(str(p.relative_to(home)))
    sessions, unusable = {}, []
    sessions_dir = home / "sessions"
    for p in sorted(sessions_dir.glob("*/meta.json")) if sessions_dir.is_dir() else []:
        if not state.valid_session_sid(p.parent.name):
            unusable.append(p.parent.name)  # holds no session: skipped, and named below
            continue
        try:
            d = json.loads(p.read_text())
            if not isinstance(d, dict):
                raise ValueError("top level is not an object")
            sessions[p.parent.name] = d
        except (OSError, ValueError):
            bad.append(str(p.relative_to(home)))
    unusable_info = [f"sessions/{_name(n)}: not a usable session id" for n in unusable]
    if bad:
        return [_result("state", FAIL, "does not parse: " + ", ".join(bad), unusable_info)]
    orphans, open_count = [], 0
    for sid, meta in sessions.items():
        lead = meta.get("lead")
        if lead and lead not in leads:
            orphans.append(sid)
        if _is_open(home, sid, meta):
            open_count += 1
    status = WARN if orphans or unusable else PASS
    detail = f"{len(leads)} lead(s), {len(sessions)} session(s), {open_count} open"
    info = [f"{sid}: lead {sessions[sid].get('lead')!r} has no leads/{sessions[sid].get('lead')}.json"
            for sid in orphans] + unusable_info
    return [_result("state", status, detail, info)]


def _name(name):
    """A directory's name for a message line: as it is when printable, else quoted (so a newline in a
    name cannot forge a line)."""
    return name if name.isprintable() else repr(name)


# ── 15. pi session logs ──────────────────────────────────────────────────────────────────────────
def _unreadable_log_dir(worktree=None):
    """Why a directory that could hold pi session logs (the ones `usage.session_dirs(worktree)`
    searches, found the same way) could not be read — `cannot list <dir>: <reason>` — or None when
    every one that exists was listed. `usage.session_dirs` and `usage.locate` pass over such a directory
    silently; doctor must never read that as "no log here"."""
    if os.environ.get("PI_CODING_AGENT_SESSION_DIR") or usage_mod._setting_session_dir(worktree) is not None:
        dirs = usage_mod.session_dirs(worktree)  # the one directory the override names
    else:
        root = usage_mod.agent_dir() / "sessions"
        try:
            names = sorted(os.listdir(root))
        except (FileNotFoundError, NotADirectoryError):
            return None  # no pi session has run here yet
        except OSError as e:
            return f"cannot list {root}: {e.strerror or e}"
        dirs = [root / n for n in names]
    for d in dirs:
        try:
            os.listdir(d)
        except (FileNotFoundError, NotADirectoryError):
            continue  # not there, or not a directory: it holds no log
        except OSError as e:
            return f"cannot list {d}: {e.strerror or e}"
    return None


@_named("pi session logs")
def check_session_logs(ctx):
    try:
        dirs = usage_mod.session_dirs()
        problem = _unreadable_log_dir()
    except Exception as e:  # noqa: BLE001 — a reading that could not be made, never a crash
        first = (str(e).splitlines() or [""])[0]
        return [_result("pi session logs", NOT_CHECKED,
                        f"cannot find pi's session directories ({type(e).__name__}: {first})")]
    if problem:
        return [_result("pi session logs", NOT_CHECKED, problem)]
    existing = [d for d in dirs if Path(d).is_dir()]
    if not existing:
        return [_result("pi session logs", WARN, "no pi session has run here yet, or pi writes its "
                         "logs elsewhere: tokens and context will read as `-`")]
    total = 0
    for d in existing:
        try:
            names = os.listdir(d)
        except OSError as e:
            return [_result("pi session logs", NOT_CHECKED, f"cannot list {d}: {e.strerror or e}")]
        total += sum(1 for n in names if n.endswith(".jsonl"))
    noun = "directory" if len(existing) == 1 else "directories"
    return [_result("pi session logs", PASS, f"{len(existing)} {noun}, {total} *.jsonl file(s)")]


# ── 16. executor logs ────────────────────────────────────────────────────────────────────────────
def _open_sessions(home):
    """(sid, meta) of every open session (see `_is_open`), sorted by sid."""
    out = []
    sdir = home / "sessions"
    if not sdir.is_dir():
        return out
    for mp in sorted(sdir.glob("*/meta.json")):
        sid = mp.parent.name
        if not state.valid_session_sid(sid):
            continue  # holds no session (the state check names it)
        try:
            meta = json.loads(mp.read_text())
            if not isinstance(meta, dict):
                meta = {}
        except (OSError, ValueError):
            meta = {}
        if _is_open(home, sid, meta):
            out.append((sid, meta))
    return out


@_named("executor logs")
def check_executor_logs(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("executor logs", SKIP, "home does not exist")]
    open_sessions = _open_sessions(home)
    if not open_sessions:
        return [_result("executor logs", SKIP, "no open sessions")]
    missing, not_checked = [], []
    for sid, meta in open_sessions:
        worktree = meta.get("worktree")
        if usage_mod.locate(sid, worktree) is not None:
            continue
        try:
            problem = _unreadable_log_dir(worktree or None)
        except Exception as e:  # noqa: BLE001 — a reading that could not be made, never "no log"
            problem = f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}"
        if problem:  # the directory that would hold its log was not read: no verdict on this session
            not_checked.append(f"{sid}: {problem}")
        else:
            missing.append(f"{sid}: its tokens and context read as `-`, and the heaviness gate "
                            "cannot see it")
    if not_checked:
        return [_result("executor logs", NOT_CHECKED, "; ".join(not_checked + missing))]
    if missing:
        return [_result("executor logs", WARN, "; ".join(missing))]
    return [_result("executor logs", PASS, f"{len(open_sessions)} open session(s)")]


# ── 17. git shim ─────────────────────────────────────────────────────────────────────────────────
def _refusal_probe(ctx, shim_path):
    """("pass"|"fail"|"not_checked", detail) — see doctor.py module docstring and the packet's
    "Refusal probe" section for exactly what this runs and why.

    The probe exists to catch a shim that does NOT refuse, and such a shim hands `commit` to a real
    git — which would look for a repository in the working directory and then in every parent. So the
    child runs where git cannot find any repository, whatever the shim does: every inherited `GIT_*`
    variable is dropped, `GIT_DIR` names a path inside the throwaway directory that never exists,
    `GIT_CEILING_DIRECTORIES` stops discovery at the throwaway directory's parent, and no system or
    global git config is read."""
    d = ctx.throwaway_dir()
    env = {k: v for k, v in ctx.env.items() if not k.startswith("GIT_")}
    env["GIT_DIR"] = str(d / "no-such-git-dir")  # never created: git fails "not a git repository"
    env["GIT_CEILING_DIRECTORIES"] = str(d.parent)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["PILEAD_SHIM_DIR"] = str(shim_path.parent)
    env["PATH"] = str(shim_path.parent) + os.pathsep + env.get("PATH", "")
    r = ctx.run([str(shim_path), "commit"], cwd=str(d), env=env, timeout=REFUSAL_PROBE_TIMEOUT)
    if not r.ok:
        if r.error == "timeout":
            return "not_checked", f"refusal probe timed out after {REFUSAL_PROBE_TIMEOUT}s"
        return "fail", f"refusal probe could not run: {r.error}"
    if r.returncode == shim_mod.EXIT_DENIED and shim_mod.REFUSAL in (r.stderr or ""):
        return "pass", None
    first_line = (r.stderr or "").splitlines()[0] if r.stderr else "(no stderr)"
    return "fail", f"refusal probe not refused: exit {r.returncode}, stderr {first_line!r}"


@_named("git shim")
def check_git_shim(ctx):
    home = Path(ctx.home)
    if not home.exists():
        return [_result("git shim", SKIP, "home does not exist")]
    open_sessions = _open_sessions(home)
    if not open_sessions:
        return [_result("git shim", SKIP, "no open sessions")]
    fails, warns, not_checked = [], [], []
    for sid, meta in open_sessions:
        shim_path = home / "sessions" / sid / "shim" / "git"
        if not shim_path.is_file():
            fails.append(f"{sid}: shim/git missing")
            continue
        if not os.access(shim_path, os.X_OK):
            fails.append(f"{sid}: shim/git not executable")
            continue
        try:
            text = shim_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            fails.append(f"{sid}: cannot read shim/git ({e})")
            continue
        if shim_mod.MARKER not in text:
            fails.append(f"{sid}: shim/git has no {shim_mod.MARKER} marker")
            continue
        kind, detail = _refusal_probe(ctx, shim_path)
        if kind == "not_checked":
            not_checked.append(f"{sid}: {detail}")
            continue
        if kind == "fail":
            fails.append(f"{sid}: {detail}")
            continue
        # the text this pi-lead writes for the session now: guarded by its worktree's repository when
        # that resolves (as `pilead spawn` writes it), else the guard-everything shim
        worktree = meta.get("worktree")
        guard = shim_mod.resolve_guard(worktree) if worktree else None
        mirror = shim_mod.mirror_path(shim_path.parent) if guard else None
        if text != shim_mod.script(guard, mirror):
            warns.append(f"{sid}: written by another pi-lead version; the next relaunch rewrites it")
        # a guarded shim's exec-path mirror (the shim rebuilds a stale one at its next call, and fails
        # closed when it cannot): reported, never repaired, here
        if "\nPL_MIRROR='" in text and "\nPL_MIRROR=''\n" not in text:
            ok, why = shim_mod.mirror_state(shim_path.parent)
            if not ok:
                warns.append(f"{sid}: exec-path mirror {why}; the shim rebuilds it at its next call")
    if fails:
        return [_result("git shim", FAIL, "; ".join(fails))]
    if not_checked:
        return [_result("git shim", NOT_CHECKED, "; ".join(not_checked))]
    if warns:
        return [_result("git shim", WARN, "; ".join(warns))]
    return [_result("git shim", PASS, f"{len(open_sessions)} open session(s)")]


# ── 18. pi ───────────────────────────────────────────────────────────────────────────────────────
@_named("pi")
def check_pi(ctx):
    if ctx.offline:
        return [_result("pi", SKIP, "--offline")]
    found = _which(ctx, "pi")
    if not found:
        return [_result("pi", FAIL, "pi not found on PATH")]
    r = ctx.run([found, "--version"], timeout=VERSION_PROBE_TIMEOUT)
    if not r.ok:
        return [_result("pi", NOT_CHECKED, f"pi --version could not be run: {r.error}")]
    if r.returncode != 0:
        return [_result("pi", FAIL, f"{found} --version {_exit_detail(r)}")]
    ver = _version_pair(r.stdout)
    if ver is None:
        return [_result("pi", NOT_CHECKED, f"pi --version output not understood: {r.stdout.strip()[:80]!r}")]
    detail = f"{found} — {r.stdout.strip()}"
    if ver < MIN_PI:
        return [_result("pi", FAIL, detail + f" — need {'.'.join(str(x) for x in MIN_PI)}+")]
    return [_result("pi", PASS, detail)]


# ── 19-21. the pi probe ──────────────────────────────────────────────────────────────────────────
def _is_get_commands_reply(line):
    """The parsed response to the probe's `get_commands`, or None for anything else — a line that is
    not JSON, a JSON value that is not an object, or an event pi prints before its answer."""
    try:
        msg = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if isinstance(msg, dict) and msg.get("type") == "response" and msg.get("command") == "get_commands":
        return msg
    return None


def _stop_process(proc):
    """Terminate `proc`, kill it if it has not exited within `PI_PROBE_KILL_GRACE` seconds, wait (again
    bounded) for it to go, and close its pipes. Bounded: at most about twice the grace. Never raises."""
    for signal_it in (proc.terminate, proc.kill):
        try:
            if proc.poll() is not None:
                break
            signal_it()
            proc.wait(timeout=PI_PROBE_KILL_GRACE)
            break
        except subprocess.TimeoutExpired:
            continue
        except Exception:  # noqa: BLE001 — cleanup must never raise
            continue
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:  # noqa: BLE001
            pass


def _run_pi_probe(ctx):
    """One `pi --mode rpc --no-session` process, scrubbed (see the module docstring / packet's "Why
    the pi probe needs a scrubbed environment"): writes one `get_commands` line and reads stdout until
    the matching response arrives, pi closes stdout, or one overall deadline of `PI_PROBE_TIMEOUT`
    seconds (counted from before pi starts) passes. It then always stops the process — terminate, then
    kill after `PI_PROBE_KILL_GRACE` — and closes its pipes (pi never exits on its own in rpc mode —
    confirmed by tests/ts/rpc.test.ts, which `child.kill()`s at the end too).

    stdout is read unbuffered: `select` on the pipe's descriptor, then `os.read` of whatever bytes are
    there into this function's own buffer, from which complete lines are split. A buffered `readline`
    is never used — it can hold a second line where `select` cannot see it, or block on half a line
    past the deadline. Lines that are not JSON, and JSON lines that are not the response, are skipped.
    Returns {"ok": True, "commands": [...]} or {"ok": False, "reason": <why>}."""
    found = _which(ctx, "pi")
    if not found:
        return {"ok": False, "reason": "pi not found on PATH"}
    deadline = time.monotonic() + PI_PROBE_TIMEOUT
    d = ctx.throwaway_dir()
    env = dict(ctx.env)
    env["PI_LEAD_HOME"] = str(d)
    for var in ("PI_LEAD_SID", "PI_LEAD_ROLE", "PI_SESSION_ID"):
        env.pop(var, None)
    try:
        proc = subprocess.Popen([found, "--mode", "rpc", "--no-session"], cwd=str(d), env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"ok": False, "reason": f"cannot start pi: {e}"}
    reply, eof, write_error, exit_code = None, False, None, None
    try:
        try:
            proc.stdin.write((json.dumps({"id": "1", "type": "get_commands"}) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            write_error = e
        fd = proc.stdout.fileno()
        buf = b""
        while write_error is None and reply is None and not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except InterruptedError:
                continue
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                eof = True
                lines, buf = ([buf] if buf else []), b""  # a last line pi did not end with a newline
            else:
                buf += chunk
                *lines, buf = buf.split(b"\n")
            for line in lines:
                reply = _is_get_commands_reply(line)
                if reply is not None:
                    break
        if eof and reply is None:
            # stdout closed: give pi a moment (still inside the deadline) to finish exiting, so the
            # reason can carry its exit status rather than the status of our own terminate.
            try:
                exit_code = proc.wait(timeout=max(0.05, min(PI_PROBE_KILL_GRACE, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
    finally:
        _stop_process(proc)
    if reply is None:
        if write_error is not None:
            return {"ok": False, "reason": f"cannot write to pi's stdin: {write_error}"}
        if eof:
            if exit_code is None:
                return {"ok": False, "reason": "pi closed its output before answering get_commands"}
            how = f"exit code {exit_code}" if exit_code >= 0 else f"signal {-exit_code}"
            return {"ok": False, "reason": f"pi exited before answering get_commands ({how})"}
        return {"ok": False, "reason": f"pi did not answer get_commands within {PI_PROBE_TIMEOUT}s"}
    commands = (reply.get("data") or {}).get("commands")
    if not isinstance(commands, list):
        return {"ok": False, "reason": "reply carried no commands list"}
    return {"ok": True, "commands": [c for c in commands if isinstance(c, dict)]}


@_named("pi commands")
def check_pi_commands(ctx):
    if ctx.offline:
        return [_result("pi commands", SKIP, "--offline")]
    if ctx.quick:
        return [_result("pi commands", SKIP, "--quick")]
    probe = ctx.pi_probe()
    if not probe.get("ok"):
        return [_result("pi commands", NOT_CHECKED, probe.get("reason", "pi probe failed"))]
    commands = probe["commands"]
    names = {c.get("name") for c in commands}
    ext_names = {c.get("name") for c in commands if c.get("source") == "extension"}
    want = {f"pilead:{n}" for n in COMMAND_NAMES}
    missing = sorted(want - ext_names)
    if not missing:
        return [_result("pi commands", PASS, f"{len(want)} commands")]
    root = Path(ctx.root)
    if not (names & want):
        remedy = f"pi install {root}, or for one session `pi -e {root / 'extensions' / 'pi-lead.ts'}`"
        return [_result("pi commands", FAIL, f"no pilead: command listed — {remedy}")]
    return [_result("pi commands", FAIL, "missing: " + ", ".join(missing))]


@_named("pi skills")
def check_pi_skills(ctx):
    if ctx.offline:
        return [_result("pi skills", SKIP, "--offline")]
    if ctx.quick:
        return [_result("pi skills", SKIP, "--quick")]
    probe = ctx.pi_probe()
    if not probe.get("ok"):
        return [_result("pi skills", NOT_CHECKED, probe.get("reason", "pi probe failed"))]
    names = {c.get("name") for c in probe["commands"]}
    want = {f"skill:pilead-{n}" for n in SKILL_FILES}
    missing = sorted(want - names)
    if not missing:
        return [_result("pi skills", PASS, f"{len(want)} skills")]
    return [_result("pi skills", WARN, "missing: " + ", ".join(missing) + " — the /pilead: commands "
                     "still work; the model cannot load a skill by itself")]


@_named("one copy")
def check_one_copy(ctx):
    if ctx.offline:
        return [_result("one copy", SKIP, "--offline")]
    if ctx.quick:
        return [_result("one copy", SKIP, "--quick")]
    probe = ctx.pi_probe()
    if not probe.get("ok"):
        return [_result("one copy", NOT_CHECKED, probe.get("reason", "pi probe failed"))]
    ext = [c for c in probe["commands"]
           if c.get("source") == "extension" and str(c.get("name", "")).startswith("pilead:")]
    if not ext:
        return [_result("one copy", SKIP, "no pilead: command found")]
    path = None
    for c in ext:
        p = (c.get("sourceInfo") or {}).get("path")
        if p:
            path = p
            break
    if not path:
        return [_result("one copy", NOT_CHECKED, "reply carried no sourceInfo.path")]
    root = Path(ctx.root).resolve()
    try:
        Path(path).resolve().relative_to(root)
        inside = True
    except ValueError:
        inside = False
    if inside:
        return [_result("one copy", PASS, path)]
    return [_result("one copy", WARN, f"pi loads pi-lead from {path}; this pilead is {root}")]


# ── the ordered list, and the runner ────────────────────────────────────────────────────────────
CHECKS = [check_python, check_package_files, check_verb_modules, check_pilead_on_path, check_git,
          check_iterm2, check_iterm2_python_api, check_osascript, check_home, check_config,
          check_terminal_backend, check_models, check_model_aliases, check_ledger, check_state, check_session_logs,
          check_executor_logs, check_git_shim, check_pi, check_pi_commands, check_pi_skills,
          check_one_copy]


def run_checks(ctx):
    """Every check in `CHECKS`, in order, with a failing one turned into its own `NOT CHECKED` result
    rather than stopping the rest. Always cleans up `ctx`'s throwaway directory before returning."""
    results = []
    try:
        for fn in CHECKS:
            try:
                results.extend(fn(ctx))
            except Exception as e:  # noqa: BLE001 — a check must never take the others down
                msg = str(e).splitlines()[0] if str(e) else ""
                results.append(_result(fn.check_name, NOT_CHECKED, f"{type(e).__name__}: {msg}"))
    finally:
        ctx.cleanup()
    return results

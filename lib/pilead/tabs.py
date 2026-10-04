"""Telling tabs apart: a lead's tab label, one colour per lead that its executors wear, and painting a tab.

A port of claude-relay 0.5.4's `TAB_PALETTE` / `lead_color` / `pick_lead_color` (lib/lead_guard.py) and
`_owner_color` / `_paint_tab` / `_repaint_executor_tab` (bin/relay), over pi-lead's records:

- a lead's colour lives in its record (`leads/<sid>.json` `color`); an executor's colour is never stored — it is
  its CURRENT owner's, asked at launch and at every change of owner;
- a tab is painted by writing iTerm's tab-colour sequence to its terminal line (a tab's `/dev/ttysNNN`), found
  through iTerm by the tab's handle. Painting is cosmetic: every function here answers False or None for what it
  could not do and never raises.

Kill switch: `PILEAD_NO_PAINT`, set and not empty, makes `paint` write nothing (the test suite sets it for every
test, so a terminal line a stub names under `/dev` is never written; a test of painting removes it for itself and
names a plain file).

Tab order (a port of relay 0.5.4's `tidy_groups` / `tidy_order` / `tidy_follows` / `_tidy_tty_hints` / `_tidy_now`
/ `maybe_tidy_tabs`): `groups` works out, from state alone, one group per lead with a recorded iTerm2 tab and the
executors it owns; `tidy_now` paints each group and asks the vendored `iterm.reorder_tabs` to put each lead's tab
first and its executors after it, in their own window; `maybe_tidy` runs `pilead tidy --quiet` as a NEW process after
a verb that opened a tab or changed an owner (a process that already opened one connection to iTerm2's Python API
cannot open a second). `RELAY_NO_TIDY`, set to anything, turns every tidy off (the test suite sets it for every
test). A tidy is cosmetic: nothing here raises."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, ledger, ownership, state

# relay 0.5.4's six colours, in relay's order (lib/lead_guard.py TAB_PALETTE)
TAB_PALETTE = (
    (200, 140, 135),  # coral
    (210, 172, 124),  # amber
    (146, 185, 146),  # green
    (136, 164, 198),  # blue
    (172, 148, 192),  # purple
    (132, 180, 180),  # teal
)
_PALETTE = {tuple(c) for c in TAB_PALETTE}

LABEL_MAX = 60
_RUNS = re.compile(r"[\s\x00-\x1f\x7f-\x9f]+")


def _iterm():
    """The vendored iTerm backend (`backend.by_name("iterm")`), the module tests replace functions of."""
    from .launch import backend
    return backend.by_name("iterm")


def usable_color(c):
    """True for a list (or tuple) of exactly three whole numbers, each from 0 to 255. Never raises."""
    try:
        return (isinstance(c, (list, tuple)) and len(c) == 3
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in c))
    except Exception:  # noqa: BLE001
        return False


def _index(sid):
    return int(hashlib.sha256(str(sid).encode()).hexdigest(), 16) % len(TAB_PALETTE)


def lead_color(sid):
    """The palette entry at sha256(sid) mod 6, as a list: the same lead always maps to the same colour."""
    return list(TAB_PALETTE[_index(sid)])


def read_json(path):
    """The JSON object in `path`, or None (missing, unreadable, not an object)."""
    try:
        rec = json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


def lead_record_paths(home):
    """Every `leads/<sid>.json` that is a lead record, as (sid, path). Raises when `leads` cannot be listed."""
    d = Path(home) / "leads"
    if not d.exists():
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.endswith(".usage.json"):
            out.append((name[:-len(".json")], d / name))
    return out


def pick_lead_color(home, sid, exclude=()):
    """The lead's own colour when its record has one in the palette; else the first palette colour, walking
    forward from lead_color's place, that no OTHER registered lead's record holds (leads in `exclude` do not
    count as holders); all six held, or any failure: lead_color(sid). Writes nothing."""
    try:
        own = read_json(state.lead_path(home, sid)) if state.valid_lead_sid(sid) else None
        if own is not None and usable_color(own.get("color")) and tuple(own["color"]) in _PALETTE:
            return list(own["color"])
        skip = {sid, *(exclude or ())}
        held = set()
        for other, p in lead_record_paths(home):
            if other in skip:
                continue
            rec = read_json(p)
            if rec is not None and usable_color(rec.get("color")):
                held.add(tuple(rec["color"]))
        start = _index(sid)
        for i in range(len(TAB_PALETTE)):
            c = TAB_PALETTE[(start + i) % len(TAB_PALETTE)]
            if c not in held:
                return list(c)
        return lead_color(sid)
    except Exception:  # noqa: BLE001 — a colour is cosmetic
        return lead_color(sid)


def owner_color(home, lead_sid, cfg):
    """The colour lead `lead_sid`'s tabs wear, or None (tab_colors off, no usable lead id). A registered lead
    with no usable colour gets one picked and written into its record (every other field kept); a lead with no
    record, or one that cannot be read, gets a picked colour and nothing is written."""
    try:
        if not (cfg or {}).get("tab_colors", True):
            return None
        if not lead_sid or not state.valid_lead_sid(lead_sid):
            return None
        p = state.lead_path(home, lead_sid)
        rec = read_json(p) if p.is_file() else None
        if rec is not None and usable_color(rec.get("color")):
            return list(rec["color"])
        color = pick_lead_color(home, lead_sid)
        if rec is not None:
            try:
                rec["color"] = color
                state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
            except Exception:  # noqa: BLE001 — the colour is still the answer
                pass
        return color
    except Exception:  # noqa: BLE001
        return None


def lead_label(project, sid=None, suffix=False):
    """`[Lead] <project>`: every run of white space or control characters one space, the ends trimmed, cut to
    60 characters; an empty project gives `[Lead] <sid>`. `suffix` appends ` ·` and the sid's first four
    characters (a handoff successor's tab, iterm.SUCCESSOR_LABEL_MARK)."""
    name = _RUNS.sub(" ", project if isinstance(project, str) else "").strip()[:LABEL_MAX].strip()
    label = f"[Lead] {name or (sid or '')}"
    if suffix:
        label += f"{_iterm().SUCCESSOR_LABEL_MARK}{str(sid or '')[:4]}"
    return label


def paint(tty, color):
    """Write iTerm's tab-colour sequence for `color` to the terminal line `tty` (opened for appending); True
    when written. False, writing nothing, for an empty or non-text `tty`, a colour that is not usable, a
    failed write, or PILEAD_NO_PAINT set. Never raises."""
    try:
        if not isinstance(tty, str) or not tty or not usable_color(color):
            return False
        if os.environ.get("PILEAD_NO_PAINT"):
            return False
        seq = _iterm().tab_color_escape(color)
        with open(tty, "a") as f:
            f.write(seq)
        return True
    except Exception:  # noqa: BLE001
        return False


def _tmux_handle(handle):
    """The vendored tmux backend when `handle` is a tmux pane handle (`tmux:%N`), else None. Never raises."""
    try:
        from .launch import backend
        be = backend.for_handle(handle.strip()) if isinstance(handle, str) else None
        return be if be is not None and be.NAME == "tmux" else None
    except Exception:  # noqa: BLE001
        return None


def paint_tab(handle, tty, color):
    """Colour the tab `handle` names (b12): a tmux handle → its window's `window-status-style`
    (`tmux_backend.paint_tab`), only when the pane has the window to itself (`window_is_own` — a split executor
    never recolours its lead's window), else False; any other handle → `paint(tty, color)`. A colour that is not
    usable, or PILEAD_NO_PAINT set: False, nothing asked. Cosmetic: never raises."""
    try:
        tm = _tmux_handle(handle)
        if tm is None:
            return paint(tty, color)
        if not usable_color(color) or os.environ.get("PILEAD_NO_PAINT"):
            return False
        h = handle.strip()
        if not tm.window_is_own(h):
            return False
        return tm.paint_tab(h, color) is True
    except Exception:  # noqa: BLE001
        return False


def tty_by_id(handle):
    """The terminal line of the iTerm tab `handle` names, or None: the vendored `iterm.tty_by_id` script (one
    AppleScript call, through the backend's `run_osascript`), with any absolute path accepted as the answer (a
    test's stub names a plain file; iTerm itself answers `/dev/ttysNNN`). Never raises."""
    try:
        if not isinstance(handle, str) or not handle.strip():
            return None
        it = _iterm()
        uuid = handle.strip().split(":")[-1]
        script = it._for_session_by_id(uuid, "          return tty of s\n") + 'return ""'
        r = it.run_osascript(script, timeout=5)
        out = (r.stdout or "").strip()
        return out if r.returncode == 0 and out.startswith("/") else None
    except Exception:  # noqa: BLE001
        return None


def executor_handle(home, meta):
    """An executor's tab handle: the text of sessions/<sid>/iterm-id, else meta["tab"]; "" when neither."""
    try:
        sid = meta.get("sid")
        if not state.valid_session_sid(sid):
            return ""
        from .lifecycle import tab_handle
        h = tab_handle(meta, state.session_dir(Path(home), sid))
        return h.strip() if isinstance(h, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def repaint_executor(home, meta, cfg):
    """Paint executor `meta`'s tab with its owner's colour (owner_color of meta["lead"]); True when painted.
    Not on the iTerm or tmux backend, no handle, no colour, iTerm not running, or no terminal line: False, and
    nothing further is asked. A tmux executor (b12) is painted through `paint_tab` with its handle (its window,
    only when the pane has it to itself; no tmux server answering: False). Never raises."""
    try:
        backend_name = (meta.get("backend") or "iterm") if isinstance(meta, dict) else None
        if backend_name not in ("iterm", "tmux"):
            return False
        handle = executor_handle(home, meta)
        if not handle:
            return False
        color = owner_color(home, meta.get("lead"), cfg)
        if color is None:
            return False
        if backend_name == "tmux":
            tm = _tmux_handle(handle)
            if tm is None or not tm.running():
                return False
            return paint_tab(handle, None, color)
        it = _iterm()
        if not it.running():
            return False
        tty = tty_by_id(handle)
        if not tty:
            return False
        return paint(tty, color)
    except Exception:  # noqa: BLE001
        return False


# ── tab order (relay 0.5.4's `relay tidy`) ─────────────────────────────────────────────────────────────────────────
HANDLE_RE = re.compile(r"^(w\d+)t\d+p\d+:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_WINDOW_RE = re.compile(r"^(w\d+)t\d+p\d+:")
TERMINAL_STATUSES = ("closed", "dead", "superseded")  # a superseded session is stored as `closed`
NO_GROUP = "no lead with a recorded iTerm2 tab"
NO_TIDY_ENV = "RELAY_NO_TIDY"  # the vendored code reads this name
RETRY_DELAY = 1.0
TIDY_TIMEOUT = 8
PS_TIMEOUT = 3
_EXC_RE = re.compile(r"iterm2 python api unavailable \(([A-Za-z_][A-Za-z0-9_.]*)\s*:")
_RETRYABLE_RE = re.compile(r"connection|timeout|socket|eof", re.I)
_MOVED_RE = re.compile(r"(?:moved|would move) (\d+) tab\(s\)")
_WINDOWS_RE = re.compile(r"tab\(s\) in (\d+) window")
_MISSING_RE = re.compile(r"(\d+) session id\(s\) had no tab")
_sleep = time.sleep  # tests replace it: no test waits a second


def _window(handle):
    """The window part of a handle (`w0t1p0:UUID` → `w0`), or None. A hint only: iTerm2 numbers windows again."""
    m = _WINDOW_RE.match(handle) if isinstance(handle, str) else None
    return m.group(1) if m else None


def _text(v):
    return v if isinstance(v, str) else ""


def _spawn_times(home):
    """{session id: ts of its first `spawned` ledger event}. Never raises."""
    out = {}
    try:
        for e in ledger.read(home, event="spawned"):
            sid = e.get("session_id")
            if isinstance(sid, str) and sid not in out:
                out[sid] = _text(e.get("ts"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _executors_by_lead(home):
    """{owning lead sid: [(sort key, executor entry)]} over every session that can be ordered. Never raises."""
    out = {}
    d = Path(home) / "sessions"
    try:
        names = sorted(os.listdir(d)) if d.is_dir() else []
    except Exception:  # noqa: BLE001
        return out
    spawned = _spawn_times(home)
    for sid in names:
        try:
            if not state.valid_session_sid(sid):
                continue
            meta = read_json(d / sid / "meta.json")
            if meta is None:
                continue
            meta["sid"] = sid
            if state.read_status(home, sid, meta) in TERMINAL_STATUSES:
                continue
            if (meta.get("backend") or "iterm") != "iterm":
                continue
            o = ownership.owner(home, meta)
            if o.get("state") != "owned" or not state.valid_lead_sid(o.get("lead")):
                continue
            handle = executor_handle(home, meta)
            if not handle:
                continue
            label = meta.get("label") if isinstance(meta.get("label"), str) and meta.get("label") else f"[Exec] {sid}"
            key = (spawned.get(sid) or _text(meta.get("created")), sid)
            out.setdefault(o["lead"], []).append((key, {"sid": sid, "handle": handle, "label": label}))
        except Exception:  # noqa: BLE001 — a session that cannot be read belongs to no group
            continue
    return out


def groups(home):
    """One group per readable lead record with an `iterm_handle` in the handle form and a `terminal` that is not
    another application's, oldest lineage first (`lineage_started`, else `started`, then sid). Each group is
    {"lead", "project", "label", "handle", "color", "window", "executors", "elsewhere"}: `executors` the
    sessions the lead owns that are not closed or dead, on iTerm, with a known handle, in `spawned` order (else
    `created`, then sid); `elsewhere` the sids of those whose handle names another window (never ordered). Reads
    state only: asks no terminal, writes nothing, never raises."""
    try:
        home = Path(home)
        leads = []
        for sid, p in lead_record_paths(home):
            if not state.valid_lead_sid(sid):
                continue
            rec = read_json(p)
            if rec is None:
                continue
            handle = rec.get("iterm_handle")
            if not isinstance(handle, str) or not HANDLE_RE.match(handle):
                continue
            if rec.get("terminal") not in (None, "", "iTerm.app"):
                continue
            started = _text(rec.get("lineage_started")) or _text(rec.get("started"))
            leads.append(((started, sid), sid, rec, handle))
        execs = _executors_by_lead(home)
        out = []
        for _key, sid, rec, handle in sorted(leads, key=lambda t: t[0]):
            window = _window(handle)
            mine, elsewhere = [], []
            for _k, e in sorted(execs.get(sid, []), key=lambda t: t[0]):
                ew = _window(e["handle"])
                if window and ew and ew != window:
                    elsewhere.append(e["sid"])
                else:
                    mine.append(e)
            label = rec.get("label") if isinstance(rec.get("label"), str) and rec.get("label") \
                else lead_label(rec.get("project"), sid)
            out.append({"lead": sid, "project": rec.get("project") if isinstance(rec.get("project"), str) else None,
                        "label": label, "handle": handle,
                        "color": list(rec["color"]) if usable_color(rec.get("color")) else None,
                        "window": window, "executors": mine, "elsewhere": elsewhere})
        return out
    except Exception:  # noqa: BLE001
        return []


def order(groups):
    """Each lead's handle followed by its executors' handles, group after group."""
    out = []
    for g in groups or []:
        out.append(g["handle"])
        out.extend(e["handle"] for e in g["executors"])
    return out


def follows(groups):
    """{executor handle: its lead's handle}."""
    return {e["handle"]: g["handle"] for g in groups or [] for e in g["executors"]}


def _pid(v):
    """A whole number above 1 from an int or a text of digits; else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
    else:
        return None
    return n if n > 1 else None


def ttys_of(pids):
    """{pid: terminal line} for the pids `ps` lists, from ONE `ps -o pid=,tty= -p <pids>` call (3 s limit); {} on
    any failure or with no pid."""
    try:
        pids = sorted({p for p in pids if isinstance(p, int)})
        if not pids:
            return {}
        r = subprocess.run(["ps", "-o", "pid=,tty=", "-p", ",".join(map(str, pids))], capture_output=True,
                           text=True, timeout=PS_TIMEOUT, stdin=subprocess.DEVNULL)
        out = {}
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1] not in ("??", "-", "?"):  # "?": Linux
                out[int(parts[0])] = parts[1]
        return out
    except Exception:  # noqa: BLE001
        return {}


def tty_hints(home, groups):
    """{handle: terminal line} for each lead (the `pid` of its health file, when that file is readable and its
    `ended` is null) and each executor (the number in sessions/<sid>/pid), from one `ps` call. A handle no open
    tab has is then found by its terminal line. Never raises, writes nothing."""
    try:
        from . import wakehealth
        home = Path(home)
        wanted = {}
        for g in groups or []:
            try:
                h = read_json(wakehealth.health_path(home, g["lead"]))
                pid = _pid(h.get("pid")) if h is not None and h.get("ended") is None else None
            except Exception:  # noqa: BLE001
                pid = None
            if pid:
                wanted[g["handle"]] = pid
            for e in g["executors"]:
                try:
                    pid = _pid((state.session_dir(home, e["sid"]) / "pid").read_text())
                except Exception:  # noqa: BLE001
                    pid = None
                if pid:
                    wanted[e["handle"]] = pid
        ttys = ttys_of(wanted.values())
        return {h: ttys[p] for h, p in wanted.items() if p in ttys}
    except Exception:  # noqa: BLE001
        return {}


def _retryable(reason):
    m = _EXC_RE.search(reason or "")
    return bool(m and _RETRYABLE_RE.search(m.group(1)))


def _num(rx, reason):
    m = rx.search(reason or "")
    return int(m.group(1)) if m else 0


def _ledger(home, ok, reason, attempts, sid):
    try:
        if ok:
            ledger.append(home, "tidy", session_id=sid, attempts=attempts, reason=reason,
                          moved=_num(_MOVED_RE, reason), windows=_num(_WINDOWS_RE, reason),
                          missing=_num(_MISSING_RE, reason))
        else:
            ledger.append(home, "tidy_skipped", session_id=sid, attempts=attempts, reason=reason)
    except Exception:  # noqa: BLE001 — a ledger that cannot be written changes nothing else
        pass


def _paint_group(home, g, cfg):
    """The lead's own terminal line and each executor's tab get the lead's colour (owner_color, which gives a
    lead with no colour one and writes it into its record). A paint that fails is passed over."""
    try:
        color = owner_color(home, g["lead"], cfg)
        if color is None:
            return
        g["color"] = color
        rec = read_json(state.lead_path(Path(home), g["lead"])) or {}
        tty = rec.get("tty") if isinstance(rec.get("tty"), str) and rec.get("tty") else tty_by_id(g["handle"])
        if tty:
            paint_tab(g["handle"], tty, color)
    except Exception:  # noqa: BLE001
        pass
    for e in g["executors"]:
        try:
            meta = read_json(state.session_dir(Path(home), e["sid"]) / "meta.json")
            if meta is not None:
                meta["sid"] = e["sid"]
                repaint_executor(home, meta, cfg)
        except Exception:  # noqa: BLE001
            pass


def _reorder(gs, home, dry_run):
    try:
        return _iterm().reorder_tabs(order(gs), tty_hints=tty_hints(home, gs), follows=follows(gs), dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 — the vendored call never raises; a missing backend might
        return False, f"iterm2 python api unavailable ({type(e).__name__}: {e})"


def tidy_now(home, cfg, dry_run=False, lead=None):
    """Paint every group and put the tabs in order. Returns (ok, reason, groups). No group: nothing is asked and
    nothing is written. A dry run paints nothing, moves nothing and writes nothing. A failure whose exception names
    a connection, a timeout, a socket or an end of file is asked once more, 1.0 s later. Every tidy that reached
    the terminal writes ONE ledger event (`tidy` or `tidy_skipped`) whose `session_id` is `lead`, else the caller,
    else null. Never raises."""
    try:
        gs = groups(home)
        if not gs:
            return False, NO_GROUP, []
        if os.environ.get(NO_TIDY_ENV):
            return False, f"disabled by {NO_TIDY_ENV}", gs
        if not dry_run and (cfg or {}).get("tab_colors", True):
            for g in gs:
                _paint_group(home, g, cfg)
        ok, reason = _reorder(gs, home, dry_run)
        if dry_run:
            return ok, reason, gs
        attempts = 1
        if not ok and _retryable(reason):
            _sleep(RETRY_DELAY)
            attempts = 2
            ok, reason = _reorder(gs, home, dry_run)
        sid = lead
        if sid is None:
            from .lifecycle import caller
            sid = caller(home)
        _ledger(home, ok, reason, attempts, sid)
        return bool(ok), reason, gs
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", []


def maybe_tidy(home, cfg, after, err=None):
    """The tidy that follows a verb (`after` names it): `pilead tidy --quiet --home <home>` (with `--lead <caller>`
    when there is one) as a NEW process, at most 8 s. True only when tabs were ordered. `RELAY_NO_TIDY` set,
    config `tidy_tabs` false, the selected backend tmux or the platform Linux (tidy is iTerm only, relay 0.5.6
    `maybe_tidy_tabs`): False at once, with no process started and nothing printed. Any other failure prints ONE
    line on `err` (stderr) and returns False. Never raises."""
    try:
        if os.environ.get(NO_TIDY_ENV):
            return False
        if cfg is None:
            cfg = config.load(home)
        if not cfg.get("tidy_tabs", True):
            return False
        if _tidy_unsupported(home, cfg):
            return False
        from .lifecycle import caller
        cmd = [sys.executable, str(state.ROOT / "bin" / "pilead"), "tidy", "--quiet", "--home", str(home)]
        who = caller(home)
        if who:
            cmd += ["--lead", who]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIDY_TIMEOUT, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                return True
            lines = ([ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
                     or [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()])
            reason = lines[-1] if lines else f"pilead tidy exited {r.returncode}"
        except subprocess.TimeoutExpired:
            reason = f"tidy ran longer than {TIDY_TIMEOUT} s"
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
        try:
            print(f"  · tab tidy skipped ({reason}) — pilead tidy --dry-run shows the order it wanted",
                  file=err or sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception:  # noqa: BLE001
        return False


def _tidy_unsupported(home, cfg):
    """True when no tidy can apply here: the platform is Linux, or the backend this invocation selects (env,
    then config `terminal_app`, then $TMUX / $TERM_PROGRAM) is tmux. Never raises (False when unknown)."""
    try:
        from .launch import backend
        import platform_cmds
        if platform_cmds.is_linux():
            return True
        configured = str((cfg or {}).get("terminal_app") or "").lower()
        return backend.select(configured=configured if configured in config.CHOICES["terminal_app"]
                              else None).NAME == "tmux"
    except Exception:  # noqa: BLE001
        return False


def tidy_after(home, after):
    """A verb's call of `maybe_tidy`, guarded: whatever it does or raises, the verb's exit code, stdout and files
    stay as they are."""
    try:
        maybe_tidy(home, config.load(home), after=after)
    except Exception:  # noqa: BLE001 — tidy is cosmetic
        pass

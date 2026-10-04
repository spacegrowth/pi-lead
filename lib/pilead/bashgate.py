"""The lead's bash write gate: what a lead's bash command provably writes, and what the gate makes of it.

The edit gate (extensions/pi-lead.ts `routeDecision`) sees a lead's `edit`/`write` calls only; a file written
through bash passes it. This module reads the TEXT of a bash command for the shapes that provably write a
file — the vendored relay parser `vendor/relay/bash_writes.py` (`parse_targets_detailed`, pure) — resolves
each target on disk, and applies the edit gate's rule to it (claude-relay's hooks/pretool_bash_gate.py,
with pi-lead's differences: the threshold is exclusive, and pi-lead's control files are hits at any size).

`decide(home, lead_sid, command, cwd, record=False)` →
`{"verdict", "mode", "reason", "targets", "rule", "parsed"}`:

- `verdict` is `allow`, `log` or `block`; `mode` the `bash_write_gate` mode it was judged in (`None` when
  the session is not a registered lead); `reason` the refusal text for `block`, else `None`; `rule` the
  verb-log rule name (or `bash-write` for a hit no verb rule names), else `None`.
- `targets` is every resolved target, in order, as `{path, lines, new_file, control, hit}`: `lines` is
  `None` when the size is unknown, `control` the control-file kind or `None`, `hit` True for a target that is
  a hit (the one that decided, and a control file found among the command's words, which decides over any
  other), False for one judged not a hit, `None` for one not judged (after the deciding hit, or with the
  grace window open or the mode `off`, when no target is looked for at all and the list is empty).
- `parsed` is True when the parser and the word split both read the command, False when either could not
  (an unterminated quote, a NUL, anything else that makes them give up; also an error inside the gate), and
  `None` when the session is not a registered lead (the command is not looked at).

The control files (`control_kind`: `config.json`, `ledger.jsonl`, `leads/*.route` and the lead's record
`leads/*.json`) are recognised however their names are cased, on every disk. For them, and for the home
directory and `leads/` themselves (`_Controls.dir_kind`), every word of the command is looked at, not just the
parser's targets (`control_word`): a command in which any word is one of them is judged as a write to it,
unless that part of the command is a plain read (`PLAIN_READS`, a `sed` whose script writes nothing, and — for
this word check alone — a `cd`/`pushd`) with no redirect onto it. A part the gate cannot read literally
(`_not_literal`: a word holding `$`, a backquote, `*`, `?` or `[`; a command word that runs text as code,
`INTERPRETERS`, `awk` and `sed` among them; a here-document; or any part after a `cd`/`pushd`) is also judged by
its raw text (`raw_control`: a control-file name together with a home marker), and so is a command that could
not be read at all (`unread_control`). A control file found among the words or by the raw text is refused with
`names_reason`; one the parser found as a target keeps `block_reason`.

It never runs the command and never starts a shell. It fails open: any error, a shape the parser does not
know, or a fact it cannot make out gives `allow` — the one exception being a command whose raw text names a
control file. An unknown size is never read as 0 lines and never as "over the threshold" (`lines` is `null` in
the ledger). With `record=True` it appends at most one ledger event (`blocked` or `would_have_blocked`, with
`parsed: false` for a command that could not be read, or `gate_unread` for such a command that is allowed);
without it, it writes nothing anywhere."""
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, ledger, refs, state

_VENDOR = str(state.ROOT / "vendor" / "relay")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import bash_writes  # noqa: E402  (vendor/relay/bash_writes.py, byte-identical to claude-relay's)

MODES = ("deny", "log", "off")
COMMAND_MAX = 1000     # a ledger `command` is cut to this many characters
GIT_TIMEOUT = 5        # seconds, per `git ls-files`
DEFAULT_RULE = "bash-write"

# A path with a variable, command substitution, glob, brace expansion or a control character is not a
# concrete path the text proves → dropped (allow).
_UNRESOLVABLE = re.compile(r"[$`*?\[\]{}\x00-\x1f\x7f]")

# A part of a command (one simple command of a pipeline or list) whose program is one of these, and that has
# no redirect onto a control file, only reads the control files its words name.
PLAIN_READS = frozenset({"cat", "head", "tail", "less", "more", "wc", "grep", "ls", "stat", "file", "jq", "diff",
                         "cmp", "test", "[", "echo", "realpath", "readlink", "basename", "dirname"})

# The control files' names as raw text spells them (compared lower-cased) and their kinds; `leads/` (the
# directory of the grace files and the lead records) counts only when none of the others is named.
CONTROL_NAMES = (("config.json", "settings"), (ledger.NAME, "ledger"), (".route", "grace file"))
LEADS_NAME = "leads/"
# Home markers in raw text, besides the home's own path (as typed, and resolved): compared lower-cased.
HOME_MARKERS = (".pi-lead", "pi_lead_home")

# A part whose command word is one of these runs text the gate does not read as a command (a script, an
# argument list built elsewhere): it is not fully literal. `python<version>` counts as `python`.
INTERPRETERS = frozenset({"eval", "source", ".", "bash", "sh", "zsh", "dash", "node", "python", "python3", "perl",
                          "ruby", "xargs", "env", "awk", "gawk", "mawk", "sed"})
# `sed` is in INTERPRETERS, but a `sed` part whose script writes nothing (`_sed_writes`) is a plain read: its
# words are not looked at (bar the operands of its writing redirects), and nor is its raw text.
SED = "sed"
# A word holding one of these is expanded by the shell before the command sees it: not fully literal.
_EXPANDS = re.compile(r"[$`*?\[]")
CHDIRS = frozenset({"cd", "pushd"})  # for the word check alone a plain read; its target still marks what follows

# ── the verb log: claude-relay lib/lead_guard.py `CUSTODY_RULES`, `IMPLEMENTATION_RULES` and
# `classify_bash_command`, copied with their names, patterns and order. Logging only: a rule never
# blocks. A custody rule is looked for first and wins on overlap. ─────────────────────────────────
CUSTODY_RULES = [
    {"name": "git-commit-push", "pattern": r"\bgit\s+(commit|push)\b"},
    {"name": "systemctl-restart-status", "pattern": r"\bsystemctl\s+(restart|status)\b"},
    {"name": "ssh-clickhouse-sql", "pattern": r"\bclickhouse-client\b"},
    {"name": "test-suite", "pattern":
        r"\b(pytest|py\.test|npm\s+(run\s+)?test\b|yarn\s+test\b|go\s+test\b|cargo\s+test\b|"
        r"make\s+test\b|tox\b)"},
]

IMPLEMENTATION_RULES = [
    {"name": "npm-install", "pattern": r"\bnpm\s+(install|ci)\b"},
    {"name": "npm-run-build", "pattern": r"\bnpm\s+run\s+build\b"},
    {"name": "package-install", "pattern": r"\b(yarn\s+(install|add)\b|pip3?\s+install\b)"},
    {"name": "compiler", "pattern":
        r"\b(tsc|gcc|g\+\+|clang(\+\+)?|go\s+build|cargo\s+build|make)\b"},
    {"name": "git-clone", "pattern": r"\bgit\s+clone\b"},
    {"name": "service-file-write", "pattern":
        r"(/etc/systemd/system/\S*\.service|systemctl\s+(enable|daemon-reload)\b)"},
    {"name": "sed-inplace", "pattern": r"\bsed\s+-i\b"},
    {"name": "heredoc", "pattern": r"(?<!<)<<(?!<)-?~?\s*['\"]?\w+"},
    {"name": "tee-mutation", "pattern": r"\btee\b"},
    {"name": "rsync", "pattern": r"\brsync\b"},
]


def classify(cmd):
    """The matched implementation rule's name, or None for a custody verb or anything unclassified.
    Never raises."""
    try:
        s = str(cmd or "")
        for rule in CUSTODY_RULES:
            if re.search(rule["pattern"], s):
                return None
        for rule in IMPLEMENTATION_RULES:
            if re.search(rule["pattern"], s):
                return rule["name"]
        return None
    except Exception:
        return None


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────
def _result(verdict="allow", mode=None, reason=None, targets=None, rule=None, parsed=None):
    return {"verdict": verdict, "mode": mode, "reason": reason, "targets": targets or [], "rule": rule,
            "parsed": parsed}


def _under(path, root):
    """`path` is `root` or inside it (both already resolved)."""
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def _in_grace(home, sid, grace_seconds):
    """The grace file `leads/<sid>.route` is younger than `grace_seconds` (its mtime, as the extension
    judges it). A missing file, or any error, is a closed window."""
    try:
        mtime = (Path(home) / "leads" / f"{state.check_lead_sid(sid)}.route").stat().st_mtime
        return time.time() - mtime < grace_seconds
    except Exception:
        return False


def _stat_key(p):
    """(st_dev, st_ino) of what `p` names, following links; None when it cannot be made out."""
    try:
        st = os.stat(p)
        return st.st_dev, st.st_ino
    except (OSError, ValueError):
        return None


# A name directly in `<home>/leads` with one of these endings is a control file of that kind.
_LEADS_KINDS = ((".route", "grace file"), (".json", "lead record"))


class _Controls:
    """Home's four control files, and the directories that hold them, for recognising a path as one. The rule,
    in one place: a path is a control file when — as given (its directory part already resolved through
    symlinks) or wholly resolved (when the file itself is a link) — it equals `<home>/config.json` or
    `<home>/ledger.jsonl`, or its directory is `<home>/leads` and its name ends in `.route` (a grace file) or
    `.json` (a lead's record), compared WITHOUT regard to case on every disk (home itself is resolved through
    symlinks); or when it is the same file on disk (a hard link, or a spelling the text comparison misses).
    `dir_kind` recognises home itself and `<home>/leads` by the same rule."""

    def __init__(self, home):
        real = os.path.realpath(str(home))
        self.home = real
        self.cfg, self.led = os.path.join(real, "config.json"), os.path.join(real, ledger.NAME)
        self.leads = os.path.join(real, "leads")
        self._names = {self.cfg.lower(): "settings", self.led.lower(): "ledger"}
        self._leads_low = self.leads.lower()
        self._keys = {k: kind for k, kind in ((_stat_key(self.cfg), "settings"), (_stat_key(self.led), "ledger"))
                      if k is not None}
        self._leads_key = _stat_key(self.leads)
        try:  # the grace files and lead records there now, for a hard link to one under another name
            with os.scandir(self.leads) as it:
                for entry in it:
                    kind = next((k for end, k in _LEADS_KINDS if entry.name.lower().endswith(end)), None)
                    key = _stat_key(entry.path) if kind else None
                    if key is not None:
                        self._keys.setdefault(key, kind)
        except OSError:
            pass
        self._dirs = {real.lower(): "home", self._leads_low: "leads"}
        self._dir_keys = {k: kind for k, kind in ((_stat_key(real), "home"), (self._leads_key, "leads"))
                          if k is not None}
        self._all_keys = {**self._dir_keys, **self._keys}

    def _match(self, spellings, files, dirs):
        """The rule, once: the kind the first of `spellings` names by text (lower-cased), else by what is on disk
        (one `stat`, which follows links, so it is the same for every spelling). `files`/`dirs` pick what may
        match: the four control files, and home and `<home>/leads`."""
        for p in spellings:
            low = p.lower()
            if files:
                if low in self._names:
                    return self._names[low]
                name = low.rsplit("/", 1)[-1]
                leads_kind = next((k for end, k in _LEADS_KINDS if name.endswith(end)), None)
                if leads_kind and os.path.dirname(low) == self._leads_low:
                    return leads_kind
            if dirs:
                kind = self._dirs.get(low.rstrip("/") or "/")
                if kind:
                    return kind
        keys = self._all_keys if files and dirs else self._keys if files else self._dir_keys
        key = _stat_key(spellings[0]) if keys else None
        if key is not None and key in keys:
            return keys[key]
        if files and self._leads_key is not None:
            for p in spellings:
                name = p.lower().rsplit("/", 1)[-1]
                leads_kind = next((k for end, k in _LEADS_KINDS if name.endswith(end)), None)
                if leads_kind and _stat_key(os.path.dirname(p)) == self._leads_key:
                    return leads_kind
        return None

    def kind(self, path):
        """`settings`, `ledger`, `grace file` or `lead record`, else None. Never raises."""
        try:
            return self._match(tuple(dict.fromkeys((path, os.path.realpath(path)))), True, False)
        except Exception:
            return None

    def dir_kind(self, path):
        """`home` when `path` is the home directory itself, `leads` when it is `<home>/leads`, else None. Never
        raises."""
        try:
            return self._match(tuple(dict.fromkeys((path, os.path.realpath(path)))), False, True)
        except Exception:
            return None

    def word_kind(self, path):
        """`kind(path) or dir_kind(path)` for a path whose directory part is already resolved (`_resolve`), with
        no `realpath` unless the path itself is a link — every word of a command goes through here. Never
        raises."""
        try:
            spellings = (path, os.path.realpath(path)) if os.path.islink(path) else (path,)
            return self._match(spellings, True, True)
        except Exception:
            return None


def control_kind(path, home):
    """`settings`, `ledger`, `grace file` or `lead record` when `path` is one of home's control files (`_Controls`: after
    symlinks are resolved, in any case, or the same file on disk), else None. Never raises."""
    try:
        return _Controls(home).kind(path)
    except Exception:
        return None


def _resolve(raw, base, dirs=None):
    """A word or target as a concrete absolute path — `~` expanded, relative to `base`, the directory part
    resolved through symlinks — or None when it is not one (2b's rule: a variable, glob, brace, control
    character, or `/dev/`). `dirs`, when given, caches the resolved directory parts across calls."""
    if not isinstance(raw, str) or not raw or _UNRESOLVABLE.search(raw) or raw.startswith("/dev/"):
        return None
    p = os.path.expanduser(raw)
    if not os.path.isabs(p):
        p = os.path.join(base, p)
    p = os.path.normpath(p)
    d = os.path.dirname(p)
    if dirs is None:
        rd = os.path.realpath(d)
    else:
        rd = dirs.get(d)
        if rd is None:
            rd = dirs[d] = os.path.realpath(d)
    real = os.path.join(rd, os.path.basename(p))
    if real == "/dev" or real.startswith("/dev/"):
        return None
    return real


def resolve_targets(command, cwd):
    """Every target `parse_targets_detailed(command)` names that resolves to a concrete file path, as
    `[(path, lines, new_file)]` (2b of the packet: relay's `bash_write_targets` without its cwd filter —
    the hit rule applies that). Never raises."""
    out = []
    try:
        base = os.path.realpath(str(cwd))
        # SHORTCUT: no size cap on the parse. The vendored parser tokenizes the script part (heredoc bodies
        # excluded) character by character: ~2 ms for a real command, ~8 s for 1 MB of one-word script text —
        # and `split_parts` tokenizes it a second time, so ~16 s for such a command in all. Fine for anything
        # a lead types; the caller (the extension) must bound the call with a fail-open timeout, or a cap here
        # that skips the parse (allow, `parsed` false) above a size can be added if that proves too slow.
        parsed = bash_writes.parse_targets_detailed(command)
    except Exception:
        return out
    for t in parsed:
        try:
            raw = t.get("path")
            if not isinstance(raw, str) or not raw or _UNRESOLVABLE.search(raw) or raw.startswith("/dev/"):
                continue
            lines = t.get("lines")
            if isinstance(lines, bool) or not isinstance(lines, int):
                lines = None
            p = os.path.expanduser(raw)
            if not os.path.isabs(p):
                p = os.path.join(base, p)
            p = os.path.normpath(p)
            cands = [p]
            if t.get("sources") and os.path.isdir(p):
                cands = [os.path.join(p, os.path.basename(s.rstrip("/"))) for s in t["sources"]
                         if isinstance(s, str) and s.rstrip("/") and not _UNRESOLVABLE.search(s)]
            for cand in cands:
                real = os.path.join(os.path.realpath(os.path.dirname(cand)), os.path.basename(cand))
                if real == "/dev" or real.startswith("/dev/") or os.path.isdir(real):
                    continue
                out.append((real, lines, not os.path.exists(real)))
        except Exception:
            continue
    return out


# ── every word, for the control files ───────────────────────────────────────────────────────────
_OPERATOR_CHARS = set(bash_writes._PUNCT)
_HEREDOC_OPS = ("<<", "<<-")


def _is_operator(tok):
    return bool(tok) and set(tok) <= _OPERATOR_CHARS


def _is_redirect(tok):
    """A redirect operator (`>`, `>>`, `>|`, `&>`, `<`, `<>`, `>&`, `<<<`, …): punctuation that holds `<` or
    `>` and, apart from `>|`, no separator character."""
    if not _is_operator(tok) or not set(tok) & set("<>"):
        return False
    return tok == ">|" or not set(tok) & set(";|\n()")


class _Segment:
    """A top-level stretch of a command between separators (`;`, `&&`, `||`, `|`, `&`, a newline) that are
    outside every `(…)`, `$(…)`, `<(…)` and backquote: the raw text a part inside it came from, when the
    tokenizer splits that part at a substitution (`truncate -s 0 $(echo ~/.pi-lead)/ledger.jsonl`)."""
    __slots__ = ("tokens", "bodies", "_text")

    def __init__(self):
        self.tokens, self.bodies, self._text = [], [], None

    def text(self):
        """Its tokens (quotes removed) joined by spaces, then its here-document bodies; worked out once."""
        if self._text is None:
            self._text = "\n".join([" ".join(self.tokens)] + self.bodies)
        return self._text


class _Part:
    """One simple command of a pipeline or list: `program` (the parser's `_program` basename), `words` (every
    word, redirect operands included), `written` (the operands of the redirects that write), `args` (the words
    that are not redirect operands, the command word first), `heredoc` (it has a here-document) and `segment`
    (the `_Segment` it lies in; its text is the part's raw text)."""
    __slots__ = ("program", "words", "written", "args", "heredoc", "segment")

    def __init__(self, words, written, args, heredoc, segment):
        self.program = bash_writes._program(args)[0]
        self.words, self.written, self.args, self.heredoc, self.segment = words, written, args, heredoc, segment


def _split(command):
    """The command as `[_Part]`, split as the shell would with quotes removed (the vendored parser's own
    tokenizer, over the script with heredoc bodies taken out; each body goes back to the segment of the part
    whose `<<` it follows, or — when the count of `<<` operators and bodies differ — every body to every such
    segment). None when the command cannot be split: not a string, a NUL, or the tokenizer gives up (an
    unterminated quote). Never raises."""
    if not isinstance(command, str) or "\x00" in command:
        return None
    try:
        script, bodies = bash_writes._split_heredocs(command)
        tokens = bash_writes._tokens(script)
    except Exception:
        return None
    parts = []
    words, args, written = [], [], []
    ops = sum(1 for tok in tokens if tok in _HEREDOC_OPS)
    texts = [b for b, _n in bodies]
    state_ = {"ops": 0, "heredoc": False, "depth": 0, "tick": False, "seg": _Segment()}

    def flush():
        if words:
            parts.append(_Part(list(words), list(written), list(args), state_["heredoc"], state_["seg"]))
        del words[:], args[:], written[:]
        state_["heredoc"] = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        seg = state_["seg"]
        if tok.count("`") % 2:
            state_["tick"] = not state_["tick"]
        if tok in _HEREDOC_OPS:
            state_["heredoc"] = True
            new = [texts[state_["ops"]]] if ops == len(texts) else [t for t in texts if t not in seg.bodies]
            seg.bodies.extend(new)
            state_["ops"] += 1
            seg.tokens.append(tok)
            i += 2  # the delimiter word; the body is data, not words
            continue
        if _is_redirect(tok):
            seg.tokens.append(tok)
            if i + 1 < len(tokens) and not _is_operator(tokens[i + 1]):
                words.append(tokens[i + 1])
                seg.tokens.append(tokens[i + 1])
                if ">" in tok:
                    written.append(tokens[i + 1])
                i += 2
            else:
                i += 1
            continue
        if _is_operator(tok):  # `;`, `&&`, `|`, a newline, `(`, `>(` …: a new part begins
            flush()
            state_["depth"] = max(0, state_["depth"] + tok.count("(") - tok.count(")"))
            if state_["depth"] == 0 and not state_["tick"] and set(tok) & set(";&|\n") and "(" not in tok:
                state_["seg"] = _Segment()  # a separator outside every substitution: a new segment
            else:
                seg.tokens.append(tok)
            i += 1
            continue
        words.append(tok)
        args.append(tok)
        seg.tokens.append(tok)
        i += 1
    flush()
    return parts


def split_parts(command):
    """The command's words, split as the shell would with quotes removed (`_split`), grouped per simple
    command: `[(program, words, written)]` — `program` the command's basename after `VAR=` and prefix commands
    (the parser's `_program`), `words` every word including redirect operands, `written` the operands of the
    redirects that write (those holding `>`, `<>` included). None when the command cannot be split. Never
    raises."""
    parts = _split(command)
    return None if parts is None else [(p.program, p.words, p.written) for p in parts]


def _word_paths(word):
    """The spellings of a path a word may hold: the word, and what follows its first `=` (`of=<path>`,
    `--output=<path>`)."""
    out = [word]
    if "=" in word:
        out.append(word.split("=", 1)[1])
    return out


def _sed_script_writes(script):
    """A `sed` script holds a command that writes a file or runs one: `w`/`W` (a command, or the `w` flag of
    `s`), `e` (a command, or the `e` flag of `s`). Addresses, the text of `s`/`y` and of `a`/`i`/`c`, labels
    and comments are skipped. A script it cannot make out ends the scan as a write. Never raises."""
    try:
        i, n = 0, len(script)

        def skip_delimited(j, delim):
            """The index after the next `delim` from `j` that no backslash escapes; -1 when there is none."""
            while j < n:
                if script[j] == "\\":
                    j += 2
                    continue
                if script[j] == delim:
                    return j + 1
                j += 1
            return -1

        def to_eol(j):
            k = script.find("\n", j)
            return n if k < 0 else k + 1

        while i < n:
            c = script[i]
            if c in " \t\n;{}!,$~+0123456789":
                i += 1
            elif c == "/" or c == "\\":            # an address: /re/ or \cREc, then its I / M flags
                if c == "\\":
                    if i + 1 >= n:
                        return True
                    i = skip_delimited(i + 2, script[i + 1])
                else:
                    i = skip_delimited(i + 1, "/")
                if i < 0:
                    return True
                while i < n and script[i] in "IM":
                    i += 1
            elif c in "sy":
                if i + 1 >= n or script[i + 1] in "\n\\":
                    return True
                delim = script[i + 1]
                j = skip_delimited(i + 2, delim)
                j = skip_delimited(j, delim) if j >= 0 else -1
                if j < 0:
                    return True
                while j < n and script[j] not in ";\n}":
                    if c == "s" and script[j] in "wWe":
                        return True
                    j += 1
                i = j
            elif c in "wWe":
                return True
            elif c in "aicrR#":                      # text, a file read or a comment: to the line's end
                i = to_eol(i + 1)
            elif c in "btTv:":                       # a label (or a version): to the next `;` or the line's end
                j = i + 1
                while j < n and script[j] not in ";\n":
                    j += 1
                i = j
            else:                                   # a one-letter command (p d D n N g G h H x l q Q = z F …)
                i += 1
        return False
    except Exception:
        return True


def _sed_writes(part):
    """A `sed` part writes (or may): an in-place option (`-i`, `-i<suffix>`, a cluster holding `i`,
    `--in-place`), a script from a file (`-f`, `--file`), no script at all, or a script that holds a writing
    command (`_sed_script_writes`). Never raises; a part it cannot make out writes."""
    try:
        _prog, rest = bash_writes._program(part.args)
        scripts, positional, i = [], [], 0
        while i < len(rest):
            w = rest[i]
            if w == "--":
                positional.extend(rest[i + 1:])
                break
            if w.startswith("--"):
                if w.startswith("--in-place") or w.startswith("--file"):
                    return True
                if w == "--expression":
                    if i + 1 >= len(rest):
                        return True
                    scripts.append(rest[i + 1])
                    i += 2
                    continue
                if w.startswith("--expression="):
                    scripts.append(w.split("=", 1)[1])
                i += 1
                continue
            if w.startswith("-") and len(w) > 1:
                flags = w[1:]
                for k, f in enumerate(flags):
                    if f in "if":
                        return True
                    if f == "e":
                        tail = flags[k + 1:]
                        if tail:
                            scripts.append(tail)
                        elif i + 1 < len(rest):
                            scripts.append(rest[i + 1])
                            i += 1
                        else:
                            return True
                        break
                    if f == "l":                     # GNU `-l N`: the line length, not a script
                        if not flags[k + 1:]:
                            i += 1
                        break
                i += 1
                continue
            positional.append(w)
            i += 1
        if not scripts:
            if not positional:
                return True
            scripts = [positional[0]]
        return any(_sed_script_writes(s) for s in scripts)
    except Exception:
        return True


def _reads(part):
    """The part is a plain read: its program is in `PLAIN_READS`, or is `sed` with a script that writes nothing
    (`_sed_writes`), and no word of it holds a backquote (a command run inside it, which the tokenizer leaves
    among the read's words)."""
    if any("`" in w for w in part.words):
        return False
    return part.program in PLAIN_READS or (part.program == SED and not _sed_writes(part))


def _runs_code(args):
    """The command word (after `VAR=` assignments and prefix commands such as `sudo`) is one of
    `INTERPRETERS`, or a prefix command itself is."""
    i = 0
    while i < len(args):
        w = args[i]
        if re.match(r"^[A-Za-z_]\w*=", w):
            i += 1
            continue
        name = os.path.basename(w)
        if name in INTERPRETERS or bash_writes._PY_RE.match(name):
            return True
        if w not in bash_writes._PREFIX_CMDS:
            return False
        i += 1
        while i < len(args) and args[i].startswith("-"):
            i += 1
    return False


def _not_literal(part):
    """The gate cannot read this part literally: a word the shell expands, a command word that runs text as
    code, or a here-document. (A part after a `cd`/`pushd` is not literal either; the caller knows that.)"""
    return part.heredoc or any(_EXPANDS.search(w) for w in part.words) or _runs_code(part.args)


def _home_markers(home):
    """The lower-cased texts that mark the home in raw text: `.pi-lead`, `PI_LEAD_HOME`, and the home's path
    as typed (when absolute) and resolved."""
    typed = str(home)
    out = set(HOME_MARKERS)
    out.add(os.path.realpath(typed).lower())
    if os.path.isabs(typed):
        out.add(os.path.normpath(typed).lower())
    out.discard("/")
    return out


def _home_marked(text, home):
    low = text.lower()
    return any(m in low for m in _home_markers(home))


_CHUNK = r"[^\s/'\"`\\<>|;&()=\x00]*"


def raw_control(text, home, cd_home=False):
    """THE raw-text rule: `(path, kind)` when `text` names, compared without regard to case, a control-file
    name (`config.json`, `ledger.jsonl`, `.route`, `leads/`) AND a home marker (`_home_markers`; `cd_home`
    True counts as one — a `cd` earlier in the command went to the home, or somewhere the gate cannot read),
    else None. The earliest file name decides (`leads/` only when none is named); `path` is where it lands
    under the resolved home, spelled as the text spells it (`config.json`, `ledger.jsonl` in home;
    `leads/<name>` for the rest), and `kind` its control kind (`leads` for `leads/` naming no file, `lead
    record` for `leads/<name>.json`). Never raises."""
    try:
        low = text.lower()
        if not (cd_home or _home_marked(text, home)):
            return None
        real = os.path.realpath(str(home))
        found = [(low.find(name), name, kind) for name, kind in CONTROL_NAMES if name in low]
        if found:
            at, name, kind = min(found)
            if kind != "grace file":
                return os.path.join(real, text[at:at + len(name)]), kind
            m = re.search(_CHUNK + r"\.route", text, re.IGNORECASE)  # the name, from the last `/` on
            return os.path.join(real, "leads", m.group(0) if m else text[at:at + len(name)]), kind
        at = low.find(LEADS_NAME)
        if at < 0:
            return None
        rest = re.match(_CHUNK, text[at + len(LEADS_NAME):]).group(0)
        kind = "lead record" if rest.lower().endswith(".json") else "leads"
        return os.path.normpath(os.path.join(real, "leads", rest)), kind
    except Exception:
        return None


def unread_control(command, home):
    """For a command that could not be read: `raw_control` over its whole raw text. Never raises."""
    try:
        return raw_control(command, home)
    except Exception:
        return None


def control_word(parts, cwd, controls, home):
    """What the words of a command that was read name among the control files, the home and `leads/`, as
    `(path, kind)` for the first one found, else None. Part by part, in order:

    - every word (and the text after its first `=`) that resolves — from cwd, and from the literal target of an
      earlier `cd`/`pushd` — to a control file, to home itself or to `<home>/leads`, unless the part is a plain
      read (`_reads`) and the word is not the operand of a redirect that writes;
    - when the part is not fully literal (`_not_literal`, or it follows a `cd`/`pushd`): `raw_control` over the
      part's raw text — for a plain read, over the operands of its writing redirects only — with an earlier
      `cd`/`pushd` target that names the home, or that is itself not literal, counting as a home marker.

    `parts` is `_split`'s list. Never raises."""
    try:
        base = os.path.realpath(str(cwd))
        bases, seen, dirs, raw_seen, after_cd, cd_home = [base], {}, {}, {}, False, False
        for part in parts:
            reads = _reads(part)
            skip = reads or part.program in CHDIRS  # for the word check alone, a `cd`/`pushd` is a plain read
            for word in part.words:
                if skip and word not in part.written:
                    continue
                for cand in _word_paths(word):
                    for b in bases:
                        key = (cand, b)
                        if key not in seen:
                            path = _resolve(cand, b, dirs)
                            seen[key] = (path, controls.word_kind(path) if path else None)
                        path, kind = seen[key]
                        if kind:
                            return path, kind
            if after_cd or _not_literal(part):
                if reads:
                    found = raw_control("\n".join(part.written), home, cd_home) if part.written else None
                else:  # one answer per segment: its parts share its text
                    key = (id(part.segment), cd_home)
                    if key not in raw_seen:
                        raw_seen[key] = raw_control(part.segment.text(), home, cd_home)
                    found = raw_seen[key]
                if found is not None:
                    return found
            if part.program in CHDIRS:
                after_cd = True
                for target in (w for w in part.args[1:] if not w.startswith("-")):
                    if _EXPANDS.search(target) or _home_marked(target, home):
                        cd_home = True
                    path = _resolve(target, base)
                    if path and path not in bases:
                        bases.append(path)
    except Exception:
        pass
    return None


def _git_ls_files(cwd, *args):
    """(returncode, stdout) of the REAL git (`refs.git_path()`, by absolute path, argument list, no shell,
    GIT_* dropped) running `--literal-pathspecs -C <cwd> ls-files …`; (1, "") on any failure."""
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["LC_ALL"] = "C"
        r = subprocess.run(["git", "--literal-pathspecs", "-C", str(cwd), "ls-files", *args],
                           executable=refs.git_path(), capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def _tracked(cwd, rel, new_file):
    """Git tracks `rel`, or it is new and git tracks at least one file in its directory. No repository, or
    git failing, is False."""
    if not new_file:
        return _git_ls_files(cwd, "--error-unmatch", "--", rel)[0] == 0
    rc, out = _git_ls_files(cwd, "--", os.path.dirname(rel) or ".")
    return rc == 0 and bool(out.strip())


def _is_hit(path, lines, new_file, cwd, home, cfg):
    """2c for a target that is not a control file: inside cwd, not exempt, tracked (or new beside tracked
    files), and new (with `block_on_new_file`) or of a known size MORE than `edit_line_threshold`."""
    base = os.path.realpath(str(cwd))
    if not _under(path, base):
        return False
    rel = os.path.relpath(path, base)
    if os.path.basename(path).endswith("-packet.md") or "_staging" in Path(rel).parts:
        return False
    user = os.path.expanduser("~")
    for root in (os.path.join(user, ".relay-tasks"), os.path.join(user, ".pi-lead"), str(home)):
        if _under(path, os.path.realpath(root)):
            return False
    threshold = cfg["edit_line_threshold"]
    if not ((new_file and cfg["block_on_new_file"]) or (lines is not None and lines > threshold)):
        return False
    return _tracked(base, rel, new_file)


def size_text(lines, new_file):
    if new_file and lines is not None:
        return f"new file, {lines} lines"
    if lines is not None:
        return f"{lines} lines"
    return "new file" if new_file else "unknown size"


def grace_text(seconds):
    """`<n> min` for whole minutes, `<n> s` otherwise (the extension's graceText)."""
    n = int(seconds) if float(seconds).is_integer() else seconds
    if isinstance(n, int) and n % 60 == 0:
        return f"{n // 60} min"
    return f"{n} s"


def block_reason(target, grace_seconds):
    g = grace_text(grace_seconds)
    if target["control"]:
        return (f"pi-lead lead gate: this bash command writes {target['path']}, a pi-lead control file "
                f"({target['control']}) — a change to it needs a human's word; ask, or "
                f"/pilead:route retain \"<reason>\" and retry within {g}.")
    return (f"pi-lead lead gate: this bash command writes {target['path']} "
            f"({size_text(target['lines'], target['new_file'])}) — delegate this (pilead spawn/send) or "
            f"/pilead:route retain \"<reason>\" and retry within {g}.")


def names_reason(path):
    """The refusal for a control file (or home, or `leads/`) found among the command's words or by its raw text,
    not as a parser target: the gate does not know the command writes it, only that it names it."""
    return (f"pilead: this command names {path}, which controls the lead gate, and the gate cannot confirm it only "
            f"reads it — use a plain read (cat, head, grep, jq, …) or leave the change to the human")


def command_sha(command):
    """sha256 (hex) of the command's UTF-8 text: what `gate_unread` records instead of text it could not read."""
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


# ── the decision ────────────────────────────────────────────────────────────────────────────────
def decide(home, lead_sid, command, cwd, record=False):
    """The gate's verdict on one bash command of lead `lead_sid` run in `cwd`. Never raises; any error is
    `allow` with `parsed` False (and writes nothing that names a file or size it did not make out)."""
    try:
        return _decide(home, lead_sid, command, cwd, record)
    except Exception:
        return _result(parsed=False)


def _decide(home, lead_sid, command, cwd, record):
    # 1. a registered lead, or nothing at all
    if not state.valid_lead_sid(lead_sid) or home is None:
        return _result()
    home = Path(home)
    if not (home / "leads" / f"{state.check_lead_sid(lead_sid)}.json").is_file():
        return _result()
    parts = _split(command)  # None: the command could not be read
    parsed = parts is not None
    if not isinstance(command, str):
        command = ""
    cfg = config.load(home)
    # 2. the mode; a value that is not one of the three reads as `log`
    mode = cfg.get("bash_write_gate")
    mode = mode if isinstance(mode, str) and mode in MODES else "log"
    grace = cfg["grace_seconds"]
    # 3 + 4. targets, unless the grace window is open or the mode is `off`
    targets, hit, named = [], None, False  # named: found among the words or by raw text, not a parser target
    if mode != "off" and command and not _in_grace(home, lead_sid, grace):
        cwd = str(cwd) if cwd is not None else os.getcwd()
        controls = _Controls(home)
        # an unread command has no targets (the parser gave up; a NUL cannot reach a shell's argument list,
        # so a path the parser cut at one is not made out) — unless its raw text names a control file
        found = None
        if parsed:
            for path, lines, new_file in resolve_targets(command, cwd):
                t = {"path": path, "lines": lines, "new_file": new_file, "control": controls.kind(path),
                     "hit": None}
                targets.append(t)
                if hit is not None:
                    continue
                try:
                    t["hit"] = bool(t["control"]) or _is_hit(path, lines, new_file, cwd, home, cfg)
                except Exception:
                    t["hit"] = False  # a target that cannot be judged is not a hit
                if t["hit"]:
                    hit = t
            # a parser target that is a control file decides over an earlier hit that is not one
            if hit is not None and not hit["control"]:
                ctl = next((t for t in targets if t["control"]), None)
                if ctl is not None:
                    ctl["hit"], hit = True, ctl
            # for the control files, home and `leads/`: every word, and the raw text of a part not read literally
            if hit is None or not hit["control"]:
                found = control_word(parts, cwd, controls, home)
        else:
            found = unread_control(command, home)
        if found is not None:
            path, kind = found
            same = next((t for t in targets if t["path"] == path), None)
            if same is None:
                same = {"path": path, "lines": None, "new_file": not os.path.exists(path), "control": kind,
                        "hit": None}
                targets.append(same)
                named = True
            same["control"], same["hit"] = kind, True
            hit = same
    unread = {} if parsed else {"parsed": False}
    if hit is not None:
        rule = classify(command) or DEFAULT_RULE
        if hit["control"] or mode == "deny":
            reason = names_reason(hit["path"]) if named else block_reason(hit, grace)
            res = _result("block", mode, reason, targets, rule, parsed)
            if record:
                fields = {"session_id": lead_sid, "file_path": hit["path"], "lines": hit["lines"],
                          "new_file": hit["new_file"], "vector": "bash"}
                if hit["control"]:
                    fields["control"] = hit["control"]
                ledger.append(home, "blocked", **fields, **unread)
            return res
        res = _result("log", mode, None, targets, rule, parsed)
        if record:
            ledger.append(home, "would_have_blocked", session_id=lead_sid, command=command[:COMMAND_MAX],
                          rule=rule, vector="bash", file_path=hit["path"], lines=hit["lines"],
                          new_file=hit["new_file"], **unread)
        return res
    # 5. the verb log
    rule = classify(command) if cfg.get("bash_gate_logging") is True else None
    if rule is None:
        if record and not parsed:  # an unread command is recorded, allowed ones too
            ledger.append(home, "gate_unread", session_id=lead_sid, command_sha=command_sha(command), parsed=False)
        return _result("allow", mode, None, targets, None, parsed)
    if record:
        ledger.append(home, "would_have_blocked", session_id=lead_sid, command=command[:COMMAND_MAX], rule=rule,
                      **unread)
    return _result("log", mode, None, targets, rule, parsed)

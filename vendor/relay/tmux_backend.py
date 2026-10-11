"""
tmux backend for pi-lead — the same operations as iterm.py and terminal_app.py, for the place
neither of those exists: a lead running `pi` inside tmux on a Linux box over SSH (and, identically,
inside tmux on a Mac). Selected by $PILEAD_TERMINAL=tmux (or $RELAY_TERMINAL=tmux), config
`terminal_app: tmux`, or automatically when $TMUX is set (see backend.py).

Vendored from claude-relay d1cc0a7 `scripts/tmux_backend.py`, with three pi-lead adaptations:
  1. pi-lead NEVER types into a running pi. `spawn` types the launch line into the fresh shell and
     nothing after it (relay then types `/rename <label>`); `rename_by_id` renames the tmux WINDOW
     only and types nothing (relay also types `/rename`). `spawn` keeps the vendored iTerm spawn's
     signature (packet path + `pi_args` for `iterm.build_pi_cmd`); its `type_name` and
     `rename_delay` are accepted and ignored, and its result carries `placement` as the iTerm one
     does. `window_is_own(handle)` is pi-lead's: callers use it so a split executor never recolours
     or renames its lead's window.
  2. The socket is $PILEAD_TMUX_SOCKET, else $RELAY_TMUX_SOCKET.
  3. PLACEMENT NEVER MOVES A CLIENT. Vendored, the executor was a `new-window`/`split-window` made
     current, so every client attached to the lead's session jumped to it (and a lead whose pane
     could not be found got a window in whatever session was used last). Here each executor gets
     its OWN detached session (`new-session -d -s pilead-<sid>`), a `--pane` executor is
     `split-window -h -d` off the lead's pane, and a failed `new-session` fails the spawn with
     tmux's error — no fallback into another session. Spawn issues no switch-client, select-window
     or select-pane; `close` kills the executor's own session (only the pane for a split);
     `focus` switches a client only on the user's explicit focus command. The spawn result carries
     `tmux_session` (the session name; None for a split), which `pilead list --json` reports.

ADDRESSING: by tmux PANE ID, never by title. The handle written to `iterm_id_file` (and stored as a
session's `iterm-id` / a lead record's `iterm_handle`) is "tmux:%N" — `#{pane_id}`, unique for the
server's lifetime. `_pane(handle)` returns "%N" or None, and exactly like terminal_app every
id-taking operation returns False (running NOTHING) for an absent or foreign handle (an iTerm
"w0t0p0:UUID", a Terminal "twid:N"). There is no title fallback anywhere: tmux window names are
pi-lead-owned here (`automatic-rename off`), but two windows can legitimately share one (a handoff
pair), so a name match would be exactly the §2 ambiguity the id discipline exists to avoid.

EVERY tmux call goes through `_tmux(args, timeout)` — an argv list to `subprocess.run`, never
`shell=True`, never a command string — so tests mock one seam. `$PILEAD_TMUX_SOCKET` (else
`$RELAY_TMUX_SOCKET`), when set, adds `-L <name>` to every call (a user's named server; the e2e
test's private one). Inside a tmux pane without it, `tmux` finds its server through $TMUX on its
own. tmux treats an argv word ENDING in ';' as a command separator (verified live by relay: `-n
'lbl;'` split the command), so `_tmux` escapes a trailing ';' as '\\;' on every word — a label can
never smuggle in a second command. Compatible with tmux >= 3.2 (Ubuntu 22.04/24.04 ship 3.2a/3.4):
no 3.5+-only flags or formats.

SPAWN TARGETING — relay's choice (a), and why:
  The window/pane is created running the user's normal login shell (`new-window -P -F
  '#{pane_id}'`, which prints the new pane's id atomically from the create call itself), and the
  short quote-free launch line `sh <bootstrap file> %N` is then TYPED into that pane with
  `send-keys -t %N` — addressed BY ID, never "the active pane", so it has the same misfire immunity
  as iTerm's by-id write (`_for_session_by_id`): a human switching windows between the create and
  the type cannot redirect it. The alternative (b) — run `sh <bootstrap>` as the window's own
  command — was rejected because a window whose command exits closes itself, taking the error text
  of a failed launch with it; (a) leaves a shell behind exactly like an iTerm tab does, so a launch
  that dies prints its error where the human can read it, and `pilead list` reads the pane as
  `stalled` (process gone, tab open), not silently `dead`.
  The bootstrap's receiving-pane guard compares `$TMUX_PANE` against `$1` (bootstrap_file_content's
  `sid_expr`): inside tmux on a Mac, $ITERM_SESSION_ID/$TERM_SESSION_ID are INHERITED from the
  outer iTerm shell, present and wrong, so the iTerm guard text would mis-fire here. tmux sets
  $TMUX_PANE in the pane before the shell starts, so a copy typed anywhere but %N is inert.

TYPED LINES are two writes with a scaled beat (iterm.py's block comment above ENTER_GAP_MIN — the
discipline is mandatory in every backend): `send-keys -l -- <text>` (literal: nothing in the text
is read as a key name), sleep `enter_gap(text)`, then a separate `send-keys Enter`. In pi-lead the
only line ever typed is the launch line, into a fresh shell.

What tmux shows the human: the STATUS BAR window name is pi-lead's label (`rename-window` +
`automatic-rename off`), and a lead's tab color becomes that window's `window-status-style`
background, so a lead and its executors group in the status bar like iTerm tabs do. pi's own OSC
title lands in `#{pane_title}`, which is what `title_by_id` reads. A pane outlives its program (the
shell stays), so "the pane exists" is not "pi is running": a caller that needs the latter also
looks for pi on the pane's tty (`pids_on_tty`).
"""
import os
import re
import shlex
import subprocess
import time

from iterm import (build_pi_cmd, write_bootstrap_file, enter_gap, pids_on_tty, pid_on_tty,
                   PI_BIN)  # noqa: F401 — pids_on_tty/pid_on_tty/PI_BIN re-exported: the
                            # ps-by-tty match is terminal-agnostic (/dev/pts/N → "pts/N")

NAME = "tmux"  # backend key (see backend.py)
HANDLE_PREFIX = "tmux:"
SID_EXPR = "$TMUX_PANE"   # the bootstrap guard's runtime pane id (see the module docstring)
_PANE_RE = re.compile(r"^%\d+$")
DEFAULT_TIMEOUT = 5
SESSION_PREFIX = "pilead-"  # an executor's own tmux session is `pilead-<sid>` (see _session_name)


def _tmux(args, timeout=DEFAULT_TIMEOUT):
    """THE one tmux seam. Runs `tmux [-L <socket>] <args…>` as an argv list and returns a
    CompletedProcess — never raises: a missing `tmux` binary reads as returncode 127, a timeout as
    returncode 1, both with the reason in stderr. Every argv word ending in ';' is escaped (see the
    module docstring) so no caller-supplied text can split the command. The socket is
    $PILEAD_TMUX_SOCKET, else $RELAY_TMUX_SOCKET; neither set → no `-L`."""
    argv = ["tmux"]
    sock = os.environ.get("PILEAD_TMUX_SOCKET") or os.environ.get("RELAY_TMUX_SOCKET")
    if sock:
        argv += ["-L", sock]
    for a in args:
        a = str(a)
        argv.append(a[:-1] + "\\;" if a.endswith(";") else a)
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", "tmux timed out")
    except (FileNotFoundError, OSError) as e:
        return subprocess.CompletedProcess(argv, 127, "", f"tmux unavailable: {e}")


def _ok(r):
    return r is not None and r.returncode == 0


def _pane(handle):
    """Pane id from a stored handle ("tmux:%12" → "%12"); None for absent/foreign handles."""
    h = str(handle or "")
    if h.startswith(HANDLE_PREFIX) and _PANE_RE.match(h[len(HANDLE_PREFIX):]):
        return h[len(HANDLE_PREFIX):]
    return None


def handle_for(pane):
    """The stored-handle form of a pane id ("%12" → "tmux:%12")."""
    return f"{HANDLE_PREFIX}{pane}"


def _display(pane, fmt):
    """`display-message -p -t <pane> <fmt>` → the stripped value, or None on any failure."""
    r = _tmux(["display-message", "-p", "-t", pane, fmt])
    out = (r.stdout or "").strip()
    return out if _ok(r) and out else None


def running():
    """A tmux server answers (`list-sessions` exits 0) — mirrors iterm/terminal_app `running()`."""
    return _ok(_tmux(["list-sessions"]))


def _type_line(pane, text):
    """Type `text` into `pane` as two writes with a scaled beat — the ONLY way this backend writes a
    line to a session. True iff both writes landed (i.e. the pane exists)."""
    if not _ok(_tmux(["send-keys", "-t", pane, "-l", "--", text])):
        return False
    time.sleep(enter_gap(text))
    return _ok(_tmux(["send-keys", "-t", pane, "Enter"]))


def _hex(rgb):
    r, g, b = (max(0, min(255, int(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def paint_tab(handle, rgb):
    """Best-effort status-bar color for the window holding `handle`'s pane: `window-status-style
    bg=#rrggbb` — tmux's equivalent of an iTerm tab color, so a lead's executors group with it.
    Returns True only if tmux accepted it; never raises."""
    pane = _pane(handle)
    if not pane or not rgb:
        return False
    try:
        return _ok(_tmux(["set-option", "-w", "-t", pane, "window-status-style", f"bg={_hex(rgb)}"]))
    except Exception:
        return False


def _session_name(pidfile):
    """pi-lead: the executor's OWN tmux session name — `pilead-<sid>`, the sid being the name of the
    session directory that holds `pidfile` (…/sessions/<sid>/pid). Tmux-safe: anything outside
    [A-Za-z0-9_-] (notably '.' and ':', which tmux reads as target separators) becomes '-'."""
    sid = os.path.basename(os.path.dirname(os.path.abspath(str(pidfile)))) if pidfile else ""
    sid = re.sub(r"[^A-Za-z0-9_-]", "-", sid).strip("-")
    return f"{SESSION_PREFIX}{sid or 'exec'}"


def _unique_session(name):
    """`name`, or `name-2`, `name-3`, … — the first no live session already holds (a kept `[closed]`
    tab or a relaunch of the same sid must not collide). `has-session -t =<n>` is an exact match."""
    cand, n = name, 1
    while _ok(_tmux(["has-session", "-t", f"={cand}"])):
        n += 1
        cand = f"{name}-{n}"
        if n > 99:
            break
    return cand


def _create_target(cwd, label, lead_pane, layout, session_name):
    """Create the executor's pane and return (pane_id|None, stderr, how, session|None) — `how` (for
    the spawn's `placement`) is "split-window" or "new-session". NOTHING here ever changes what a
    client is showing: no switch-client, no select-window/-pane, and both creating calls are
    detached (`-d`).
      - layout="pane" + a resolvable lead pane → `split-window -h -d` off the lead's own pane (side
        by side, like iTerm's `split vertically`);
      - otherwise → `new-session -d -s <session_name> -c <cwd>`: the executor gets its OWN detached
        session. There is NO fallback into "the most recent session": if tmux refuses, the spawn
        fails with tmux's error."""
    fmt = ["-P", "-F", "#{pane_id}"]
    if lead_pane and layout == "pane" and _display(lead_pane, "#{pane_id}") == lead_pane:
        args = ["split-window", "-h", "-d", "-t", lead_pane] + fmt + ["-c", cwd]
        how, session = "split-window", None
    else:
        session = _unique_session(session_name)
        args = ["new-session", "-d", "-s", session] + fmt + ["-n", label, "-c", cwd]
        how = "new-session"
    r = _tmux(args, timeout=10)
    pane = (r.stdout or "").strip()
    if _ok(r) and _PANE_RE.match(pane):
        return pane, "", how, session
    return None, (r.stderr or "").strip() or f"tmux {args[0]} returned {pane!r}", None, None


def _placement(lead_pane, how, layout="tab", session=None):
    """pi-lead: where spawn() put the new pane, in the iTerm spawn's words — `pane` (a split of the
    lead's pane) or `own session (<name>)` (a detached session of its own; a `--pane` request whose
    lead pane could not be found says so)."""
    if how == "split-window":
        return "pane"
    if how == "new-session":
        why = "" if layout != "pane" else (" — no lead tmux handle" if not lead_pane
                                           else " — lead pane not found")
        return f"own session ({session}{why})"
    return "none (spawn failed)"


def _fail(reason, err, session_id=None, placement=None):
    out = {"ok": False, "reason": reason, "session_id": session_id, "front_title": None,
           "error": err}
    if placement is not None:
        out["placement"] = placement
    return out


def spawn(cwd, packet_path, label, pidfile, pi_args, rename_delay=1.5, env_prefix="",
          iterm_id_file=None, tab_color=None, lead_handle=None, layout="tab", type_name=True):
    """New tmux session of its own — detached, `pilead-<sid>` (or a `split-window -h -d` pane off the
    lead's pane) — running the standard launch chain (cd → pidfile via $$ →
    exec pi), written to the per-session bootstrap FILE (§15b, `write_bootstrap_file`) with the
    `$TMUX_PANE` guard, then started by typing `sh <bootstrap> %N` into the pane BY ID (targeting
    choice (a), see the module docstring). Writes "tmux:%N" to `iterm_id_file` directly — the id is
    known from the create call, so the chain needs no `echo $TMUX_PANE` capture. Names the window
    `label` with automatic-rename off (the status bar stays pi-lead-owned) and paints it with
    `tab_color` — both only for a session of its own, never a split. `layout`/`lead_handle`: see
    `_create_target`. `env_prefix`: same meaning as iterm.spawn.

    pi-lead: the signature is the vendored iterm.spawn's, so every caller works unchanged. The launch
    line is the ONLY text typed: `type_name` and `rename_delay` are accepted and ignored (no `/name`,
    no `/rename`, no sleep for them — pi-lead never types into a running pi). No colour escape is
    ever printed: `tab_color` becomes `window-status-style` through `paint_tab`.

    Returns `{"ok", "reason", "session_id", "front_title", "placement", "tmux_session"}` (front_title is always None
    — nothing is typed into a focus-dependent target here). Any tmux failure → `{"ok": False,
    "reason": "script-failed", …, "error": <stderr>}`; a pane that vanished between create and type
    → "target-gone" (nothing was launched). Never raises."""
    lead_pane = _pane(lead_handle)
    session_name = _session_name(pidfile)
    try:
        base = build_pi_cmd(packet_path, **pi_args)
        # The iTerm spawn's chain minus its colour printf and id capture: `base` already starts
        # with the PI_LEAD_* assignments and ends in `exec pi …`.
        cmd = (f"cd {shlex.quote(cwd)} && {env_prefix}echo $$ > {shlex.quote(pidfile)} "
               f"&& {base}")
        boot_path = write_bootstrap_file(pidfile, cmd, sid_expr=SID_EXPR)
    except Exception as e:
        return _fail("script-failed", f"bootstrap: {e}", placement=_placement(lead_pane, None))
    pane, err, how, session = _create_target(cwd, label, lead_pane, layout, session_name)
    placement = _placement(lead_pane, how, layout, session)
    if not pane:
        return _fail("script-failed", err, placement=placement)
    handle = handle_for(pane)
    if iterm_id_file:
        try:
            with open(iterm_id_file, "w") as f:
                f.write(handle)
        except OSError:
            pass
    if how == "new-session":
        # The window is the new session's only one: name it `label` with automatic-rename off and
        # paint it. (A split pane shares the LEAD's window: renaming or recoloring "its" window
        # would clobber the lead's own status-bar entry, so a split gets neither.)
        _rename_window(pane, label)
        if tab_color:
            paint_tab(handle, tab_color)
    # Sacrificial Enter first (§15b): a just-created pane's shell may still be starting; a blank
    # line absorbs anything that eats the head of the input. Then the short launch line, by id.
    if not _ok(_tmux(["send-keys", "-t", pane, "Enter"])):
        return _fail("target-gone", "pane vanished before the launch line was typed", handle,
                     placement=placement)
    time.sleep(0.2)
    if not _type_line(pane, f"sh {boot_path} {pane}"):
        return _fail("script-failed", "send-keys of the launch line failed", handle,
                     placement=placement)
    # pi-lead: nothing is typed after the launch line (relay types `/rename <label>` here).
    return {"ok": True, "reason": "ok", "session_id": handle, "front_title": None,
            "placement": placement, "tmux_session": session}


def _rename_window(pane, name):
    r1 = _tmux(["rename-window", "-t", pane, "--", name])
    r2 = _tmux(["set-option", "-w", "-t", pane, "automatic-rename", "off"])
    return _ok(r1) and _ok(r2)


def window_is_own(handle):
    """pi-lead: True only when the pane exists and has its window to itself (`#{window_panes}` is
    "1"). A split-pane executor shares its LEAD's window, so callers never rename or recolour it."""
    pane = _pane(handle)
    if not pane:
        return False
    return _display(pane, "#{window_panes}") == "1"


def exists_by_id(handle):
    """HANDLE-ONLY liveness: the pane id is in `list-panes -a`. No title fallback (see the module
    docstring)."""
    pane = _pane(handle)
    if not pane:
        return False
    r = _tmux(["list-panes", "-a", "-F", "#{pane_id}"])
    return _ok(r) and pane in (r.stdout or "").split()


def is_alive(label, handle=None, pid=None):
    """The pane still exists. NO title fallback, deliberately: tmux window names are pi-lead-owned,
    but two windows can share one (a handoff predecessor/successor pair), so a name match could
    answer about the wrong pane. The caller tracks the PROCESS separately via its recorded pid."""
    return exists_by_id(handle)


def send(label, prompt, handle=None, pid=None):
    """Type `prompt` into the session's pane (literal `send-keys -l`, scaled beat, separate Enter).
    True iff the pane exists and both writes landed. Handle-only — no title fallback. (Kept for the
    shared backend surface; pi-lead has no caller — it never types into a running pi.)"""
    pane = _pane(handle)
    if not pane:
        return False
    return _type_line(pane, prompt)


def press_enter(label, handle=None, pid=None):
    """One bare Enter into the pane — the retry when a typed line didn't submit. (Kept for the
    shared backend surface; pi-lead has no caller.)"""
    pane = _pane(handle)
    if not pane:
        return False
    return _ok(_tmux(["send-keys", "-t", pane, "Enter"]))


def rename_by_id(handle, new_name):
    """Retitle the status-bar WINDOW name (`rename-window` + automatic-rename off) — only when the
    pane has the window to itself, so a split-pane executor inside its lead's window is never
    renamed. pi-lead types NOTHING (relay also types `/rename <new_name>`). True only when the pane
    exists and the rename succeeded; a split pane → False, and nothing runs after the
    `window_panes` question."""
    pane = _pane(handle)
    if not pane:
        return False
    if _display(pane, "#{window_panes}") != "1":
        return False
    return _rename_window(pane, new_name)


def _own_session(pane):
    """pi-lead: the name of the executor's OWN session when `pane` is the sole pane of a session
    named `pilead-…` (what `spawn` creates), else None — a split pane lives in its lead's session,
    which close must never kill."""
    name = _display(pane, "#{session_name}")
    if not name or not name.startswith(SESSION_PREFIX):
        return None
    r = _tmux(["list-panes", "-s", "-t", pane, "-F", "#{pane_id}"])
    return name if _ok(r) and (r.stdout or "").split() == [pane] else None


def close_by_id(handle):
    """HANDLE-ONLY close. An executor with a session of its own → `kill-session -t =<name>` (the
    session goes with its only pane, so no empty session lingers); a split pane → `kill-pane -t %N`
    only. The caller kills the process first, as for every backend. True iff tmux killed it."""
    pane = _pane(handle)
    if not pane:
        return False
    own = _own_session(pane)
    if own:
        return _ok(_tmux(["kill-session", "-t", f"={own}"]))
    return _ok(_tmux(["kill-pane", "-t", pane]))


def close(label, handle=None, pid=None):
    """Close the session's pane (by id only — see close_by_id)."""
    return close_by_id(handle)


def focus(label, handle=None, pid=None):
    """Bring the pane to the front — ONLY ever called for the user's explicit focus command (spawn
    and every other path never switch a client): switch the client to the pane's session when it is
    showing a different one (best-effort — a detached server has no client), then `select-window` +
    `select-pane`. True iff the pane was selected."""
    pane = _pane(handle)
    if not pane:
        return False
    target_session = _display(pane, "#{session_id}")
    if not target_session:
        return False
    current = (_tmux(["display-message", "-p", "#{session_id}"]).stdout or "").strip()
    if current != target_session:
        _tmux(["switch-client", "-t", target_session])
    _tmux(["select-window", "-t", pane])
    return _ok(_tmux(["select-pane", "-t", pane]))


def tty_by_id(handle):
    """The pane's tty (`#{pane_tty}`: /dev/ttysNNN on macOS, /dev/pts/N on Linux), or None."""
    pane = _pane(handle)
    if not pane:
        return None
    out = _display(pane, "#{pane_tty}")
    return out if out and out.startswith("/dev/") else None


def title_by_id(handle):
    """The pane's current title (`#{pane_title}` — pi's OSC title lands here), or None when the
    pane doesn't resolve. None means "unknown", exactly as for iterm.title_by_id."""
    pane = _pane(handle)
    if not pane:
        return None
    return _display(pane, "#{pane_title}")


def notify(handle, title, body):
    """A status-line message on the client showing the pane: `display-message -t %N -d 4000`
    (`-d` exists since tmux 3.2). '#' is doubled — the message is a tmux format. Best-effort: True
    iff tmux accepted it, never raises. The tmux analogue of iterm.notify_via_tty's OSC 777 banner
    (tmux does not forward OSC 777 to the outer terminal)."""
    pane = _pane(handle)
    if not pane:
        return False

    def clean(s):
        return (s or "").replace("\n", " ").replace("\r", " ").replace("#", "##")
    msg = f"{clean(title)} — {clean(body)}"
    try:
        return _ok(_tmux(["display-message", "-t", pane, "-d", "4000", "--", msg]))
    except Exception:
        return False


def version():
    """`tmux -V` output, or None when tmux isn't on PATH (for `pilead doctor`)."""
    r = _tmux(["-V"])
    return (r.stdout or "").strip() if _ok(r) else None

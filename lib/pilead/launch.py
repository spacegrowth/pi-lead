"""Tab launch helpers shared by the spawn / send / close verbs: pick the terminal backend, build pi's
launch args, find the lead's tab, and start or stop an executor's pi."""
import os
import shlex
import signal
import sys

from . import config, shim, state, tabs
from .state import PileadError

sys.path.insert(0, str(state.ROOT / "vendor" / "relay"))
import backend  # noqa: E402


def selected(home=None, env=None):
    """The backend this invocation selects (vendor/relay/backend.py `select`): $PILEAD_TERMINAL /
    $RELAY_TERMINAL, then config.json's `terminal_app` (when `home` is given), then $TMUX /
    $TERM_PROGRAM, iTerm by default. `env` defaults to os.environ."""
    return backend.select(env=env, configured=config.terminal_app(home) if home is not None else None)


def live_handle(home=None, env=None):
    """The caller's own tab/pane handle under the selected backend (`$TMUX_PANE` as `tmux:%N` under
    tmux, `$ITERM_SESSION_ID` under iTerm), or None — the one rule for "the tab I am in"."""
    return backend.live_handle_from_env(selected(home, env), env=env)


def _backend_for(meta=None, home=None):
    """The backend a record is addressed through: its recorded `backend`, else the backend its handle
    names (`iterm_handle` of a lead record, `tab` of an executor's meta), else the selected one."""
    meta = meta or {}
    return (backend.by_name(meta.get("backend"))
            or backend.for_handle(meta.get("iterm_handle") or meta.get("tab"))
            or selected(home))


def _pi_args(home, meta, resume):
    sid = meta["sid"]
    return dict(session_id=sid, model=meta["model"], extension=str(state.EXTENSION), rules=str(state.RULES),
                home=str(home), lead=meta["lead"], inbox=str(state.session_dir(home, sid) / "inbox.md"),
                resume=resume, thinking=meta.get("effort"))


def _lead_handle(home, lead_sid):
    """The lead's tab handle (iTerm `w#t#p#:UUID`, tmux `tmux:%N`) to place a new tab next to, or None.
    When the caller IS that lead (pi exports PI_SESSION_ID to its bash tool) its own live handle
    (`live_handle`: $ITERM_SESSION_ID, or $TMUX_PANE under tmux) wins over the one recorded at
    lead-start when `backend.is_live_handle` accepts it: an iTerm restart re-ids every restored tab,
    which leaves the recorded handle stale (claude-relay's `_live_lead_handle`).
    An id that is not a usable lead id gives None, with nothing read."""
    if not state.valid_lead_sid(lead_sid):
        return None
    if os.environ.get("PI_SESSION_ID") == lead_sid:
        live = live_handle(home) or ""
        if backend.is_live_handle(live):
            return live
    try:
        return state.load_lead(home, lead_sid).get("iterm_handle") or None
    except PileadError:
        return None


def _shim_prefix(shim_dir):
    """Bootstrap fragment (before `echo $$ > pid … && exec pi`): clear the inherited git env the shim
    refuses on, then put the git shim dir first on PATH. Passed as the vendored backends' `env_prefix`
    (documented there as a test hook; production relies on it — see VENDOR.md)."""
    return (shim.launch_env_reset() + f"export PILEAD_SHIM_DIR={shlex.quote(str(shim_dir))} && "
            'export PATH="$PILEAD_SHIM_DIR:$PATH" && ')


def _launch(home, meta, packet, resume):
    """Type the pi launch line into a new tab; on success record the tab in meta. Raises on failure.
    Returns (tab handle, placement) — placement as `iterm.placement_line` words it. Every launch
    (spawn and the send-relaunch) rewrites the session's git shim — guarding the session's worktree's
    repository — and puts it first on pi's PATH.
    A relaunch (`resume`) first removes the previous run's pid file: until the new tab's shell writes
    its own, liveness is unknown and the status engine claims nothing (lib/pilead/status.py).
    The tab wears its CURRENT owner's colour (tabs.owner_color, asked now, never stored in meta.json), and nothing is
    typed into it after the launch line (`type_name=False`: the executor's extension names its tab)."""
    be = _backend_for(meta, home)
    sdir = state.session_dir(home, meta["sid"])
    if resume:
        (sdir / "pid").unlink(missing_ok=True)
    shim_git = shim.write_git_shim(sdir, guarded=meta["worktree"])  # the policy guards this repository
    meta["shim"] = True
    meta.pop("tab_title", None)  # a new tab is named from `label` (a kept tab's `[closed] <sid>` does not follow it)
    r = be.spawn(meta["worktree"], str(packet), meta["label"], str(sdir / "pid"), _pi_args(home, meta, resume),
                 iterm_id_file=str(sdir / "iterm-id"), lead_handle=_lead_handle(home, meta["lead"]),
                 layout=meta.get("layout", "tab"), env_prefix=_shim_prefix(shim_git.parent),
                 tab_color=tabs.owner_color(home, meta.get("lead"), config.load(home)), type_name=False)
    if not r.get("ok"):
        raise PileadError(f"tab launch failed ({r.get('reason')}); nothing was started", 1)
    meta["backend"] = be.NAME
    meta["tab"] = r.get("session_id") or ""
    state.save_meta(home, meta)
    return meta["tab"], r.get("placement") or f"end-of-bar ({be.NAME} backend has no tab placement)"


def _kill_pi(pidfile):
    """SIGTERM the pid recorded at spawn (the tab shell exec'd into pi, so it IS the pi process).
    No pid file, a bad pid, or a process that is already gone: do nothing."""
    try:
        pid = int(pidfile.read_text().strip())
        if pid <= 1:  # 0 would signal our own process group, 1 is init
            return
        os.kill(pid, 0)  # alive?
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass

"""pilead close-predecessor — close the outgoing lead's tab after a handoff (claude-relay 0.5.4's
`cmd_close_predecessor`). The tab is the one recorded as `predecessor` in the calling lead's record by
`pilead handoff`; the successor runs this only after the human has said yes. pilead never runs it by itself.

A tab is closed only by its recorded handle, never by its title, and never the caller's own (relay's backlog row
74: a close-predecessor that matched by title closed the caller's own tab). Refused with exit code 1, nothing
closed, nothing signalled and `predecessor` kept: the predecessor is still a registered lead; no handle is
recorded; the handle names the caller's own tab (checked before any call to the terminal); the handle does not
resolve. Otherwise the `pi` on that tab's terminal line gets SIGTERM (so iTerm asks no "close a running process?"
question), and 0.3 s later the tab is closed by its id."""
import json
import os
import signal
import sys
import time

from .. import ledger, lifecycle, notify, state, succession, tabs
from ..state import PileadError

ORDER = 17
RESOLVE = ()
SETTLE_SECONDS = 0.3


def _refuse(home, caller, pred_sid, msg, logged=True):
    print(f"pilead: close-predecessor: {msg}", file=sys.stderr)
    if logged:
        ledger.append(home, "predecessor_closed", session_id=caller, predecessor=pred_sid, tab_closed=False)
    return 1


def cmd_close_predecessor(a):
    home = state.home_dir(a.home)
    caller = lifecycle.caller(home)
    if caller is None:
        raise PileadError("close-predecessor: no calling lead — run it inside the registered lead session that "
                          "took the role over", 2)
    rec_p = state.lead_path(home, caller)
    try:
        rec = json.loads(rec_p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # noqa: BLE001
        raise PileadError(f"close-predecessor: this lead's record {rec_p} cannot be read ({type(e).__name__})", 1)
    pred = rec.get("predecessor")
    if not isinstance(pred, dict):
        print("no predecessor recorded — nothing to close")
        return 0
    pred_sid = pred.get("sid")
    if state.valid_lead_sid(pred_sid) and state.lead_path(home, pred_sid).is_file():
        return _refuse(home, caller, pred_sid, f"the predecessor {pred_sid} is still a registered lead; it steps down "
                       f"with pilead stop {pred_sid} — its tab was not closed", logged=False)
    handle = pred.get("iterm_handle")
    if not (isinstance(handle, str) and handle.strip()):
        return _refuse(home, caller, pred_sid, f"no tab handle is recorded for the predecessor {pred_sid}; close its "
                       "tab by hand", logged=False)
    from ..launch import backend, live_handle
    # the caller's own tab: its record's handle, its live handle under the selected backend, and — inside tmux in
    # iTerm both identities are the caller's — its live iTerm handle (the env is read only in backend.py)
    if succession.same_tab(handle, rec.get("iterm_handle")) or \
            succession.same_tab(handle, live_handle(home)) or \
            succession.same_tab(handle, backend.live_handle_from_env(backend.by_name("iterm"))):
        return _refuse(home, caller, pred_sid, f"the recorded handle {handle} names this session's own tab; it is "
                       "never closed from here")
    be = backend.for_handle(handle.strip())
    # a tmux handle is addressed through tmux; every other handle through iTerm, as before b12
    it = be if be is not None and be.NAME == "tmux" else notify.iterm_module()
    if it.exists_by_id(handle) is not True:
        return _refuse(home, caller, pred_sid, f"the outgoing lead's tab ({handle}) was not found; it is not looked "
                       "for by title — close it by hand")
    tty = it.tty_by_id(handle)
    pid = it.pid_on_tty(tty) if tty else None
    if isinstance(pid, int) and pid > 1 and pid != os.getpid():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(SETTLE_SECONDS)
    closed = it.close_by_id(handle)
    if not closed and it.exists_by_id(handle) is True:
        print("the tab did not close — close it by hand")  # stdout: the command ran; `predecessor` is kept
        ledger.append(home, "predecessor_closed", session_id=caller, predecessor=pred_sid, tab_closed=False)
        return 1
    rec = json.loads(rec_p.read_text())  # read again: only `predecessor` goes, every other field as it is now
    rec.pop("predecessor", None)
    state.atomic_write(rec_p, json.dumps(rec, indent=2) + "\n")
    ledger.append(home, "predecessor_closed", session_id=caller, predecessor=pred_sid, tab_closed=True)
    print(f"closed the outgoing lead's tab ({pred_sid})")
    tabs.tidy_after(home, "close-predecessor")  # the tab was closed; cosmetic, last
    return 0


def register(verb):
    verb("close-predecessor", cmd_close_predecessor,
         "close the outgoing lead's tab recorded by pilead handoff (by its handle only; ask the human first)")

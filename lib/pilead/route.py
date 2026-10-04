"""The route contract: open a registered lead's edit-gate grace window, as `/pilead:route retain "<reason>"`
does in the lead's own session (extensions/pi-lead.ts). The extension keeps its own code; this is the same
contract in Python for `pilead route retain` (lib/pilead/verbs/route.py), and tests/route-cases.json, read
by both test suites, pins that the two agree.

The window is open while leads/<sid>.route is younger than config `grace_seconds`, by its modification
time. A refusal writes nothing: no file, no ledger line."""
import os
import re
import time

from . import config, ledger, state
from .state import PileadError

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_reason(reason):
    """`reason` with white space at both ends removed and every line break or other control character
    made one space; "" when nothing is left."""
    text = reason if isinstance(reason, str) else ""
    return _CONTROL.sub(" ", text.strip()).strip()


def stamp(now):
    """`now` (seconds since the epoch) in UTC as YYYY-MM-DDTHH:MM:SS.mmmZ, as JavaScript's toISOString."""
    ms = int(round(now * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def grace_file(home, sid):
    return state.lead_path(home, sid).with_suffix(".route")


def retain(home, sid, reason, now=None):
    """Open the grace window of the registered lead `sid` and ledger it; the grace time in seconds."""
    why = clean_reason(reason)
    if not why:
        raise PileadError('route: give a reason — pilead route retain "<why this edit is lead work>"', 2)
    state.check_lead_sid(sid)
    if not state.lead_path(home, sid).is_file():
        raise PileadError(f"route: '{sid}' is not a registered lead — the gate is off; run pilead lead-start "
                          "to register", 2)
    now = time.time() if now is None else now
    p = grace_file(home, sid)
    state.atomic_write(p, f"{stamp(now)} {why}\n")
    os.utime(p, (now, now))
    ledger.append(home, "retained", session_id=sid, reason=why)
    return config.load(home)["grace_seconds"]

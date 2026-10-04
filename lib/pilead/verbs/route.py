"""pilead route retain <reason> [--session SID] — open a registered lead's edit-gate grace window from a shell,
a script or the lead's own bash tool, where `/pilead:route retain` cannot be typed (claude-relay 0.5.4's
`cmd_route`). The same contract as the extension command (lib/pilead/route.py); the extension is not called.
An executor's or a reviewer's session is refused: it cannot change what gates its lead."""
import os

from .. import route, state
from ..state import PileadError

ORDER = 85
RESOLVE = ("session",)


def cmd_route(a):
    if os.environ.get("PI_LEAD_ROLE") in ("executor", "reviewer"):
        raise PileadError("route: an executor cannot open a lead's gate", 2)
    sid = a.session if a.session is not None else (os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID"))
    if not sid:
        raise PileadError("route: no session given, and neither $PI_LEAD_SID nor $PI_SESSION_ID is set", 2)
    grace = route.retain(state.home_dir(a.home), sid, a.reason)
    print(f"retain window open for {int(grace)}s — reason: {route.clean_reason(a.reason)}")
    return 0


def register(verb):
    p = verb("route", cmd_route, "open a registered lead's edit-gate grace window, as /pilead:route retain does")
    p.add_argument("action", choices=("retain",))
    p.add_argument("reason", help="why this edit is lead work")
    p.add_argument("--session", metavar="SID", help="the lead (default $PI_LEAD_SID, else $PI_SESSION_ID)")

"""pilead turn-end — what to tell a lead now that its turn has ended (lib/pilead/turnend.py).

The lead's extension runs `pilead turn-end --session <sid> --cwd <cwd> --json [--banner]` when pi has settled and
sends the lines it gets back as one message. By hand, `--dry-run` shows what it would say and writes nothing.
Exit code 0 whenever the lead was found."""
import json
import os

from .. import notify, review, state, turnend
from ..state import PileadError

ORDER = 83
RESOLVE = ("session",)


def cmd_turn_end(a):
    sid = a.session if a.session is not None else (os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID"))
    if sid is None:
        raise PileadError("turn-end: no lead session id — pass --session, or run it inside the lead's own pi session")
    state.check_lead_sid(sid)  # before anything is read or written
    home = state.home_dir(a.home)
    rec = notify.lead_record(home, sid)
    if rec is None:
        raise PileadError(f"turn-end: {sid} is not a registered lead")
    cwd = a.cwd if a.cwd is not None else rec.get("cwd")
    result = turnend.run(home, sid, cwd, dry_run=a.dry_run, banner=a.banner)
    if a.json:
        print(json.dumps(result))
    else:
        if a.dry_run:
            print("dry run — nothing was written")
        print("\n".join(result["lines"]) if result["lines"] else "nothing to say")
    if not a.dry_run:
        review.refresh_after(home)  # last: a live board is rewritten
    return 0


def register(verb):
    p = verb("turn-end", cmd_turn_end, "say what a lead should hear now that its turn has ended (the extension runs it)")
    p.add_argument("--session", metavar="SID", help="the lead (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.add_argument("--cwd", metavar="DIR", help="where the lead works (default: its record's cwd)")
    p.add_argument("--json", action="store_true", help="print one JSON line: {lines, kinds, parked}")
    p.add_argument("--banner", action="store_true", help="post a desktop banner when there is something to review")
    p.add_argument("--dry-run", action="store_true", help="say what would be said and write nothing")

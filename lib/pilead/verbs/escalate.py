"""pilead escalate — what happens to an executor's report that finds no listener (lib/pilead/escalation.py).
The executor's extension runs it when pi has settled with a report; a human may run it with --dry-run to see
what it would do.

stdout: `escalation: <outcome> — <detail>`; for `fallback` a second line, `then: <outcome> — <detail>`, for the
new owner. `--json` prints the decision as one JSON line instead. `--dry-run` writes nothing and prints
`dry run — nothing was written` first. Exit code 0 for every outcome; 2 for an unusable or unknown session."""
import json

from .. import escalation, state
from ..state import PileadError

ORDER = 86  # 85 is kept for `route`
RESOLVE = ("sid",)


def cmd_escalate(a):
    state.check_session_sid(a.sid)  # first: an unusable id reads and writes nothing
    if a.packet is not None and a.packet < 1:
        raise PileadError("escalate: --packet must be 1 or more")
    home = state.home_dir(a.home)
    if not (state.session_dir(home, a.sid) / "meta.json").is_file():
        raise PileadError(f"unknown session {a.sid}")
    res = escalation.decide(home, a.sid, packet=a.packet, dry_run=a.dry_run)
    if a.dry_run:
        print("dry run — nothing was written")
    if a.json:
        print(json.dumps(res))
        return 0
    print(f"escalation: {res['outcome']} — {res['detail']}")
    then = res.get("then")
    if then:
        print(f"then: {then['outcome']} — {then['detail']}")
    return 0


def register(verb):
    p = verb("escalate", cmd_escalate,
             "say where a report that finds no listener goes (the executor's extension runs it)")
    p.add_argument("sid", help="the executor session")
    p.add_argument("--packet", type=int, metavar="N", help="the packet whose report it is (default the current one)")
    p.add_argument("--json", action="store_true", help="print the decision as one JSON line")
    p.add_argument("--dry-run", action="store_true", help="say what would be done; write nothing")

"""pilead takeover — move a lead's registration to another session id (lib/pilead/succession.py `move_lead`): its
executors, pending wake lines and plan go to the new id, and a forward note `handoffs/<old>.json` sends whatever
still names the old id to the new one. The new id is `--as`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`; it need
not be registered. The lead's extension runs `pilead takeover <previous> --as <new> --force` after `/new` or
`/fork` in a lead's tab.

A lead that may still be running (LIVE `live`, `unreachable`, `broken`, or a LIVE that could not be read) is
taken over only with `--force`, or when the move is in its own tab. Every refusal is exit code 2 with one line on
stderr, and nothing is written."""
import json
import os

from .. import succession, state, tabs
from ..state import PileadError

ORDER = 18
RESOLVE = ("old_sid",)


def _refuse(msg):
    raise PileadError(f"takeover: {msg}")


def cmd_takeover(a):
    state.check_lead_sid(a.old_sid)
    new = a.as_sid or os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID") or None
    if new is None:
        _refuse("no new session id — give --as NEW_SID, or run it inside the pi session that takes over")
    state.check_lead_sid(new)
    if new == a.old_sid:
        _refuse(f"{new} is already {a.old_sid} — nothing to take over")
    if os.environ.get("PI_LEAD_ROLE") == "executor":
        _refuse("an executor cannot take over a lead")
    home = state.home_dir(a.home)
    old_rec_p = state.lead_path(home, a.old_sid)
    live = None
    if os.path.lexists(old_rec_p):
        rec = _record(old_rec_p)
        live = (succession.old_live(home) or {}).get(a.old_sid, "unknown")
        same = succession.in_same_tab(rec, home=home)
        if live != "ghost" and not same and not a.force:
            _refuse(f"{a.old_sid} is {live}: a lead that may still be running would lose its executors to "
                    f"{new} — pilead takeover {a.old_sid} --force takes it anyway")
    else:
        note = succession.read_note(home, a.old_sid)
        if not note or note.get("to") != new:
            _refuse(f"{a.old_sid} is not a registered lead")
    res = succession.move_lead(home, a.old_sid, new, "takeover", project=a.project, forced=a.force, live=live)
    _print(res)
    tabs.tidy_after(home, "takeover")  # an owner changed; cosmetic, last
    return 0


def _record(p):
    """The old record as a dict, or {} when it cannot be read (move_lead then refuses it)."""
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _print(res):
    old, new, project = res["from"], res["to"], res["project"]
    if res["again"]:
        print(f"already moved to {new}; finished what was left")
    else:
        print(f"lead {old} → {new} ({project or '-'})")
    for _sid, line in res["sessions"]:
        print(line)
    print(f"moved {res['wake_lines']} pending wake line(s)")
    for p, n in res["claims_left"]:
        count = f"{n} line(s)" if n is not None else "lines unknown (unreadable)"
        print(f"kept {p} — a claim file of {old}, {count}: its process may still be delivering it")
    for p, why in res["kept"]:
        print(f"kept {p} — {why}")
    plan = res["plan"]
    if plan == "moved":
        print("plan moved")
    elif isinstance(plan, tuple):
        print(f"plan not moved: {new} has its own plan {plan[2]}; {plan[1]} stays where it is")
    if not res["again"]:
        print(f"{new} is the lead now — run pilead lead-start {new} --project '{project or ''}' in that session "
              f"to refresh its tab and posture")


def register(verb):
    p = verb("takeover", cmd_takeover, "move a lead's registration, executors, wakes and plan to another session id")
    p.add_argument("old_sid", help="the lead whose role moves")
    p.add_argument("--as", dest="as_sid", metavar="NEW_SID",
                   help="the session that becomes the lead (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.add_argument("--project", metavar="NAME", help="the project of the new record (default the old lead's)")
    p.add_argument("--force", action="store_true", help="take over a lead that may still be running")

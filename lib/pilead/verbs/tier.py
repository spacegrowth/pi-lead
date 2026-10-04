"""pilead tier auto|manual|lead|status|ask — who chooses an executor's model when `--model` is not given
(claude-relay 0.5.4's `relay tier`; lib/pilead/posture.py `model_choice`).

`auto`: the lead decides by the rubric (the default). `manual`: the human decides at every spawn, rotate and
upgrade — those refuse without `--model` and point at `pilead tier ask`, under the autonomous posture too.
`lead`: an executor with no `--model` runs on the lead's own class (the ceiling still applies). The posture
lives in the lead's own record, so it is per lead session, and `pilead lead-start` resets it to `auto`.
`status` and `ask` change nothing; the three words each write one ledger event (`tier_set`, also when the
posture did not change). An executor (PI_LEAD_ROLE=executor) cannot change a lead's posture."""
import json
import os

from .. import config, ledger, lineup, models, posture, state
from ..state import PileadError

ORDER = 13
RESOLVE = ("session",)

MEANS = {
    "auto": "the lead chooses each executor's model by the rubric.",
    "manual": "the human chooses at every spawn, rotate and upgrade: those refuse without --model and point at "
              "pilead tier ask.",
    "lead": "an executor with no --model runs on this lead's own class; the ceiling still applies.",
}
RESET = " pilead lead-start resets it to auto."


def _sid(a):
    """--session, else $PI_LEAD_SID, else $PI_SESSION_ID (the caller); PileadError (2) with none."""
    sid = a.session if a.session is not None else (os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID"))
    if sid is None or (a.session is None and not sid):
        raise PileadError("tier: no lead session id — pass --session, or run it inside the lead's own pi session", 2)
    return sid


def _record(home, sid):
    """The lead's record: PileadError 2 when there is none, 1 (naming the file) when it cannot be read."""
    p = state.lead_path(home, sid)
    if not p.is_file():
        raise PileadError(f"tier: {sid} is not a registered lead — run pilead lead-start first", 2)
    try:
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
        return rec
    except Exception as e:  # noqa: BLE001
        raise PileadError(f"tier: lead record {p} cannot be read ({type(e).__name__})", 1)


def _status(rec, sid):
    """(line, exit code) for `pilead tier status`."""
    tier, source, readable = posture.tier_state(rec)
    name = rec.get("project") or sid
    if not readable:
        return (f"model-tier posture could not be read for lead '{name}' (tier is {json.dumps(rec.get('tier'))}) "
                "— pilead tier auto sets it again"), 1
    origin = "set by pilead tier in this session" if source == "command" else "the default at lead-start"
    return f"model-tier posture is {tier.upper()} for lead '{name}' ({origin}) — {MEANS[tier]}", 0


def _set_line(tier, name, sid):
    if tier == "auto":
        body = f"model-tier posture AUTO for lead '{name}' — the lead chooses each executor's model by the rubric again."
    elif tier == "manual":
        body = (f"model-tier posture MANUAL for lead '{name}' — every spawn, rotate and upgrade without --model now "
                f"refuses, under the autonomous posture too. Run pilead tier ask --session {sid} --packet <packet> "
                "first, then pass the answer as --model.")
    else:
        body = (f"model-tier posture LEAD for lead '{name}' — an executor with no --model now runs on this lead's "
                "own class; the ceiling still applies and --model still wins.")
    return body + RESET


def _ask(a, home):
    sid = a.session
    if sid is not None:
        state.check_lead_sid(sid)  # an unusable id reads nothing
    else:
        sid = lineup.caller_sid(home)
    model = lineup.lead_model(home, sid) if sid else None
    lead_class = models.tier(model) if model else None
    d = lineup.ask_data(home, a.packet, lead_class, config.executor_models(home)[2])
    if d is None:
        raise PileadError(lineup.NO_MODEL_LINE, 1)
    if a.json:
        print(json.dumps(d, indent=2))
    else:
        for line in lineup.ask_lines(d):
            print(line)
    return 0


def cmd_tier(a):
    home = state.home_dir(a.home)
    if a.action == "ask":
        return _ask(a, home)
    sid = state.check_lead_sid(_sid(a))  # first: an unusable id reads and writes nothing
    if a.action != "status" and os.environ.get("PI_LEAD_ROLE") == "executor":
        raise PileadError("tier: an executor cannot change a lead's posture", 2)
    rec = _record(home, sid)
    if a.action == "status":
        line, code = _status(rec, sid)
        print(line)
        return code
    was, _source, readable = posture.set_tier(home, sid, a.action)
    ledger.append(home, "tier_set", session_id=sid, project=rec.get("project"), tier=a.action,
                  was=was if readable else None, source="command")
    print(_set_line(a.action, rec.get("project") or sid, sid))
    return 0


def register(verb):
    p = verb("tier", cmd_tier, "set who chooses an executor's model without --model, or show it, or ask")
    p.add_argument("action", choices=("auto", "manual", "lead", "status", "ask"))
    p.add_argument("--session", metavar="SID", help="the lead's session id (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.add_argument("--packet", metavar="PATH", help="ask: the packet the question is about (its lint picks the "
                                                    "recommended class)")
    p.add_argument("--json", action="store_true", help="ask: print the question as JSON")

"""pilead auto on|off|status — a lead's autonomous posture (claude-relay's `cmd_auto`; lib/pilead/posture.py).

The posture changes the lead's DEFAULT on routine in-plan steps: manual (the default) waits for the human,
autonomous proceeds and announces. The stop-list stops under either posture, and committing an executor's
work has its own gate (`pilead verify <sid> --for-autocommit`). It lives in the lead's own record, so it is
per lead session, and `pilead lead-start` resets it to config `autonomous_mode`. `status` changes nothing;
`on` / `off` each write one ledger event (also when the posture did not change), so "the human was not
asked" can be reconstructed afterwards. An executor (PI_LEAD_ROLE=executor) cannot change a lead's posture."""
import json
import os

from .. import ledger, posture, state
from ..state import PileadError

ORDER = 12
RESOLVE = ("session",)


def _sid(a):
    """--session, else $PI_LEAD_SID, else $PI_SESSION_ID (the caller); PileadError (2) with none."""
    sid = a.session if a.session is not None else (os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID"))
    if sid is None or (a.session is None and not sid):
        raise PileadError("auto: no lead session id — pass --session, or run it inside the lead's own pi session", 2)
    return sid


def _record(home, sid):
    """The lead's record: PileadError 2 when there is none, 1 (naming the file) when it cannot be read."""
    p = state.lead_path(home, sid)
    if not p.is_file():
        raise PileadError(f"auto: {sid} is not a registered lead — run pilead lead-start first", 2)
    try:
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
        return rec
    except Exception as e:  # noqa: BLE001
        raise PileadError(f"auto: lead record {p} cannot be read ({type(e).__name__})", 1)


def status_line(rec, sid):
    on, source = posture.autonomous_state(rec)
    name = rec.get("project") or sid
    origin = ("set by pilead auto in this session" if source == "command"
              else f"from config autonomous_mode: {json.dumps(on)}")
    if on:
        return (f"autonomous mode is ON for lead '{name}' ({origin}) — proceeding by default on routine in-plan "
                f"steps; the stop-list still stops, and committing an executor's work needs every condition of "
                f"pilead verify <sid> --for-autocommit.")
    return (f"autonomous mode is OFF for lead '{name}' ({origin}) — waiting for you at every approval. "
            f"pilead auto on turns it on for this session.")


def on_lines(name):
    """The three lines `pilead auto on` prints."""
    return [
        f"autonomous mode ON for lead '{name}' — proceed by default WITHIN the approved plan; announce every "
        f"autonomous action together with what you would have asked.",
        f"  Still stops, under either posture: {posture.STOP_LIST}.",
        "  Committing an executor's work has its own gate: run pilead verify <sid> --for-autocommit --in-plan "
        "--diff-reviewed --findings <path> and pass the two attestation flags only when they are true. "
        "AUTO-COMMIT: CLEARED permits the commit; anything else means stop and ask, naming the condition. "
        "pilead auto off reverts; pilead lead-start resets to the config value.",
    ]


def off_line(name):
    return f"autonomous mode OFF for lead '{name}' — back to announce-and-wait at every approval."


def cmd_auto(a):
    sid = state.check_lead_sid(_sid(a))  # first: an unusable id reads and writes nothing
    home = state.home_dir(a.home)
    if a.action != "status" and os.environ.get("PI_LEAD_ROLE") == "executor":
        raise PileadError("auto: an executor cannot change a lead's posture", 2)
    rec = _record(home, sid)
    if a.action == "status":
        print(status_line(rec, sid))
        return 0
    want = a.action == "on"
    was, _source = posture.set_autonomous(home, sid, want)
    ledger.append(home, "auto_mode_on" if want else "auto_mode_off", session_id=sid, project=rec.get("project"),
                  was=was, source="command")
    name = rec.get("project") or sid
    for line in (on_lines(name) if want else [off_line(name)]):
        print(line)
    return 0


def register(verb):
    p = verb("auto", cmd_auto, "turn a lead's autonomous posture on or off, or show it")
    p.add_argument("action", choices=("on", "off", "status"))
    p.add_argument("--session", metavar="SID", help="the lead's session id (default $PI_LEAD_SID, else $PI_SESSION_ID)")

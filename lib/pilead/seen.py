"""Has the lead looked at this report? (claude-relay 0.5.4's `_surfacing_caller` / `_mark_report_surfaced`.)

The auto-close sweep (lib/pilead/autoclose.py) and escalation (lib/pilead/escalation.py) both wait until the
lead has looked at a report. A look is a ledger record, for this session and packet, of one of
`usage_snapshot` at "reported", `report_verify`, `report_reviewed` or `report_looked_at` — but only one
written by the lead, or by a process that cannot say who it is (a human at a plain terminal, a script). An
executor that runs `pilead check`, `diff` or `verify` on itself never counts: the packet footer tells every
executor to run `pilead diff <own sid>` after its report, and its own look must not park it or resolve its
escalation. relay closed the same hole in 0.4 (`surfaced_stamp_declined`).

Who is asking is read from `PI_SESSION_ID` only: pi exports the session's own id into every command its
bash tool runs, and an executor's pi session id is its sid."""
import os
from collections.abc import Mapping

from . import ledger, state

LOOK_EVENTS = ("report_verify", "report_reviewed", "report_looked_at")
COUNTING_CALLERS = ("lead", "unknown")


def caller(home, meta, env=None):
    """`unknown` (no `PI_SESSION_ID`), `self` (the session's own sid), `lead` (`meta["lead"]`) or `other`.
    Reads nothing but `meta` and `env`, writes nothing, never raises (anything unexpected → `unknown`)."""
    try:
        env = os.environ if env is None else env
        if not isinstance(env, Mapping):
            return "unknown"
        who = env.get("PI_SESSION_ID")
        if not isinstance(who, str) or not who:
            return "unknown"
        if not isinstance(meta, Mapping):
            return "unknown"
        if who == meta.get("sid"):
            return "self"
        lead = meta.get("lead")
        if isinstance(lead, str) and lead and who == lead:
            return "lead"
        return "other"
    except Exception:  # noqa: BLE001
        return "unknown"


def _packet_of(rec):
    try:
        return int(rec.get("packet"))
    except (TypeError, ValueError):
        return None


def counts(rec):
    """True when one ledger record is a look by the lead (or by a caller that could not be named, or by a
    record written before `caller` existed)."""
    if not isinstance(rec, dict):
        return False
    ev = rec.get("event")
    if not (ev in LOOK_EVENTS or (ev == "usage_snapshot" and rec.get("at") == "reported")):
        return False
    return "caller" not in rec or rec.get("caller") in COUNTING_CALLERS


def looked_at(records, sid, n):
    """True when `records` hold a look (see `counts`) at session `sid`'s packet `n`."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False
    for r in records or ():
        if isinstance(r, dict) and r.get("session_id") == sid and _packet_of(r) == n and counts(r):
            return True
    return False


def _read(home):
    """The ledger's records; raises when the ledger exists but cannot be read (so nothing is written)."""
    p = ledger.path(home)
    if p.exists() or p.is_symlink():
        p.read_text(encoding="utf-8", errors="replace")
    return ledger.read(home)


def note(home, sid, meta, via, records=None, env=None):
    """After a verb that looks at a report (`check`, `diff`, `close`, `retire`): record who looked.
    The lead (or an unnamed caller) → `report_looked_at`, once, unless a look already counts. The executor
    itself → `surfaced_stamp_declined`, once per session and packet. Anyone else → nothing. Only when the
    current packet's report exists. Best-effort: never raises, prints nothing."""
    try:
        n = int((meta or {}).get("packets") or 1)
        if not state.report_path(home, sid, n).is_file():
            return
        who = caller(home, meta, env)
        if who == "other":
            return
        recs = list(records) if records is not None else _read(home)
        if who in COUNTING_CALLERS:
            if looked_at(recs, sid, n):
                return
            ledger.append(home, "report_looked_at", session_id=sid, packet=n, caller=who, via=via)
            return
        if any(isinstance(r, dict) and r.get("event") == "surfaced_stamp_declined" and r.get("session_id") == sid
               and _packet_of(r) == n for r in recs):
            return
        ledger.append(home, "surfaced_stamp_declined", session_id=sid, packet=n, owner_lead=meta.get("lead"),
                      caller="self", via=via)
    except Exception:  # noqa: BLE001
        pass

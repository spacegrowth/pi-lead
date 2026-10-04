"""pilead gate — what the lead's bash write gate makes of a command (lib/pilead/bashgate.py). The command is
ALL of stdin. Read-only unless `--record` (the extension's flag), which appends at most one ledger event.
A command the gate could not read (`parsed` false) gets a second line, `parsed: no`.
Exit code 0 for every verdict; 2 when there is no session id or it is not a usable lead id."""
import json
import os
import sys

from .. import bashgate, state
from ..state import PileadError

ORDER = 84
RESOLVE = ("session",)


def line(res):
    """The one plain-text line for a decide() result."""
    if res["verdict"] == "block":
        return f"block: {res['reason']}"
    if res["verdict"] == "log":
        hit = next((t for t in res["targets"] if t.get("hit")), None)
        if hit is None:
            return f"log: {res['rule']}"
        return f"log: {res['rule']} — {hit['path']} ({bashgate.size_text(hit['lines'], hit['new_file'])})"
    return "allow"


def cmd_gate(a):
    sid = a.session if a.session is not None else (os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID"))
    if sid is None or (a.session is None and not sid):
        raise PileadError("gate: no session id (give --session, or set $PI_LEAD_SID or $PI_SESSION_ID)", 2)
    state.check_lead_sid(sid)
    command = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    cwd = a.cwd if a.cwd is not None else os.getcwd()
    res = bashgate.decide(state.home_dir(a.home), sid, command, cwd, record=a.record)
    sys.stdout.reconfigure(errors="replace")
    if a.json:
        print(json.dumps(res))
    else:
        print(line(res))
        if res.get("parsed") is False:
            print("parsed: no")  # the check could not be made; it must not read as one that passed
    return 0


def register(verb):
    p = verb("gate", cmd_gate, "say what the lead's bash write gate makes of the command on stdin (read-only)")
    p.add_argument("--session", help="the lead's session id (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.add_argument("--cwd", help="the directory the command runs in (default the current directory)")
    p.add_argument("--record", action="store_true", help="append the ledger event (for the extension)")
    p.add_argument("--json", action="store_true", help="print the whole decision as one JSON line")

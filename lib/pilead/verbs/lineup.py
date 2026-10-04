"""pilead lineup — which model classes this machine's pi really offers, and where the lead sits among them
(claude-relay 0.5.4's `relay lineup`; lib/pilead/lineup.py).

Read-only: it writes nothing but what `models.load` writes to `<home>/models.json`. The first line printed is
the `Model check:` line a lead says as the first line of its answer; `STOP:` follows it when the lead's class
is the weakest available and it should be switched before it goes on. The exit code is 0 whenever the verb
could run, `STOP` or not."""
import json
import sys

from .. import config, lineup, state

ORDER = 11
RESOLVE = ("session",)


def cmd_lineup(a):
    if a.session is not None:
        state.check_lead_sid(a.session)  # first: an unusable id reads nothing
    home = state.home_dir(a.home)
    sid = a.session if a.session is not None else lineup.caller_sid(home)
    d = lineup.data(home, sid, config.executor_models(home)[2])
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    sys.stdout.reconfigure(errors="replace")  # a model id outside stdout's encoding is replaced, not fatal
    for line in lineup.text_lines(d, home):
        print(line)
    return 0


def register(verb):
    p = verb("lineup", cmd_lineup, "say which model classes this pi offers and where the lead sits")
    p.add_argument("--session", metavar="SID",
                   help="the lead's session id (default $PI_LEAD_SID, else $PI_SESSION_ID when it is a registered lead)")
    p.add_argument("--json", action="store_true")

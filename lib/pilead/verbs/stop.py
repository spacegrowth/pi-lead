"""pilead stop — the lead steps down (the same as `pilead close --self <lead_sid>`): its record, route and
usage cache are removed, with `leads/<sid>.launch/` (where a `pilead resume` of it launched from), `wake/<sid>/`
(its health file), `handoffs/<sid>.md` (the memo copy `pilead handoff` started it from), and its inbox when no wake
line is pending. No process is signalled, no tab is
touched, no executor's file changes (lib/pilead/lifecycle.py `step_down`)."""
from .. import lifecycle, state

ORDER = 92
RESOLVE = ("sid",)


def cmd_stop(a):
    state.check_lead_sid(a.sid)  # first: nothing is read for an id that is not usable
    return lifecycle.step_down(state.home_dir(a.home), a.sid)


def register(verb):
    p = verb("stop", cmd_stop, "step down as lead: remove its record, route and usage cache (wakes are off)")
    p.add_argument("sid", help="the lead's session id")

"""pilead auto-close — park this lead's finished executors now (lib/pilead/autoclose.py): close, or retire
when heavy, each session whose report the lead has seen and whose work has landed or has sat reported for
`auto_close_idle_minutes`. `--dry-run` says what it would park and writes nothing. A lead only parks its
own executors (`--lead`, else the calling lead); `pilead list` and `pilead check` run the same sweep."""
import sys

from .. import autoclose, config, lifecycle, state

ORDER = 94
RESOLVE = ("lead",)


def cmd_auto_close(a):
    home = state.home_dir(a.home)
    lead = state.check_lead_sid(a.lead) if a.lead is not None else lifecycle.caller(home)
    if lead is None:
        print("pilead: auto-close needs --lead <sid>, or a registered lead calling it ($PI_LEAD_SID); "
              "nothing was parked", file=sys.stderr)
        return 2
    if not config.load(home)["auto_close"]:
        print("auto-close is off (config auto_close)")
        return 0
    acted = autoclose.sweep(home, "manual", lead=lead, dry_run=a.dry_run)
    for line in autoclose.lines(home, acted, dry_run=a.dry_run) or ["nothing to park"]:
        print(line)
    return 0


def register(verb):
    p = verb("auto-close", cmd_auto_close,
             "park this lead's finished executors: close (retire when heavy) the seen, landed or idle ones")
    p.add_argument("--lead", metavar="SID", help="the lead whose executors to sweep (default: the calling lead)")
    p.add_argument("--dry-run", action="store_true", help="print what would be parked; change nothing")

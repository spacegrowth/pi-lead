"""pilead close — close an executor's tab and mark it closed (files kept). `--supersede BY` records what took
over its work; `--keep-tab` leaves its process and tab running; `--self LEAD_SID` is `pilead stop`.
A queue is neither moved nor cancelled: when the session has queued packets, or a queue that cannot be
read, one more line on stderr says so (queue_note)."""
import sys

from .. import lifecycle, queue as queue_mod, review, seen, state
from ..state import PileadError

ORDER = 90
RESOLVE = ("sid", "supersede", "self_sid")


def cmd_close(a):
    if (a.sid is None) == (a.self_sid is None):
        print("pilead: close needs an executor's session id, or --self <lead_sid> to step down (not both)",
              file=sys.stderr)
        return 2
    if a.self_sid is not None:
        if a.supersede is not None or a.keep_tab:
            print("pilead: --supersede and --keep-tab close an executor; they do not go with --self",
                  file=sys.stderr)
            return 2
        home = state.home_dir(a.home)
        rc = lifecycle.step_down(home, a.self_sid)
        if not rc:
            review.refresh_after(home)  # after a success: a live board is rewritten
        return rc
    sid = state.check_session_sid(a.sid)
    by = a.supersede
    if by is not None and not by.strip():
        raise PileadError("--supersede needs what took over (a session id, or a word such as successor-seed); "
                          "an empty one is refused. Nothing was closed.")
    by = by.strip() if by is not None else None
    home = state.home_dir(a.home)
    meta = state.load_meta(home, sid)
    lifecycle.close_executor(home, sid, meta, by=by, keep_tab=a.keep_tab)
    seen.note(home, sid, meta, "close")  # who looked at the report (lib/pilead/seen.py); best-effort
    note = queue_note(home, sid, "close")
    if note:
        print(note, file=sys.stderr)
    review.refresh_after(home)  # last: a live board is rewritten


def queue_note(home, sid, verb):
    """The stderr line `close` / `retire` (`verb`) print after their work when `sid` still has queued
    packets or a queue that cannot be read; None when its queue is empty or absent."""
    n = queue_mod.count(home, sid)
    if n == 0:
        return None
    if n is None:
        what, advice = f"a queue that cannot be read ({queue_mod.path(home, sid)})", "inspect that file"
    elif verb == "retire":
        what = f"{n} queued packet(s)"
        advice = (f"send each one to the successor (bodies under {queue_mod.body_dir(home, sid)}), then pilead "
                  f"queue {sid} --cancel all")
    else:
        what = f"{n} queued packet(s)"
        advice = (f"pilead queue {sid} --deliver reopens it and delivers the first, pilead queue {sid} --cancel "
                  "all empties the queue")
    ends = ("a retired session receives no packet" if verb == "retire"
            else "none will be delivered while it is closed")
    return f"pilead: {sid} has {what}; {ends} — {advice}"


def register(verb):
    p = verb("close", cmd_close, "close an executor's tab and mark it closed (files kept)")
    p.add_argument("sid", nargs="?", help="the executor to close")
    p.add_argument("--supersede", metavar="BY",
                   help="record what took over its work (a successor's sid, or a word such as successor-seed); "
                        "shown as `superseded`, and `pilead send` refuses it")
    p.add_argument("--keep-tab", action="store_true",
                   help="leave its process and tab running (the tab is retitled `[closed] <sid>`)")
    p.add_argument("--self", dest="self_sid", metavar="LEAD_SID",
                   help="step down as lead instead (the same as `pilead stop LEAD_SID`)")

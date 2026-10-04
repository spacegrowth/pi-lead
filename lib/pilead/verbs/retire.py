"""pilead retire — close an executor AND write its successor seed (lib/pilead/seed.py), so a fresh session
over the same territory costs one packet-read (`pilead spawn … --seed <sid>`).

Refused, with nothing changed: a session already retired, a pinned one, and one that is busy with no
report for its current packet unless `--force` (retiring it ends that work unreported). The seed is
written to sessions/<sid>/successor-seed.md, then the session is closed through `pilead close`'s own
function (lifecycle.close_executor) as superseded by `successor-seed`. Nothing is deleted. A queue is
neither moved nor cancelled: when the session has queued packets, or a queue that cannot be read, one more
line on stderr says so (close.queue_note)."""
import sys

from .. import config, ledger, lifecycle, seed, seen, state, status as status_mod, usage
from ..state import PileadError

ORDER = 91
RESOLVE = ("sid",)


def status_now(home, sid, meta):
    """What status.refresh would make of the session now, worked out without storing anything."""
    stored = state.read_status(home, sid, meta)
    if stored in status_mod.TERMINAL:
        return stored
    try:
        return status_mod._decide(home, meta, stored)[0]
    except Exception:  # noqa: BLE001 — as refresh: on any failure the stored status stands
        return stored


def check(home, sid, meta, force=False, via_send=False):
    """Retire's refusals (PileadError, exit code 2), checked before anything changes; returns the status
    now. `via_send`: the refusal is `pilead send --rotate/--upgrade`'s (which has no --force)."""
    what = "sent" if via_send else "retired"
    if seed.is_retired(meta):
        raise PileadError(f"session {sid} is already retired; its seed is at {seed.path(home, sid)} — spawn its "
                          f"successor with: {seed.spawn_command(meta, sid)}. Nothing was {what}.")
    if meta.get("keep"):
        raise PileadError(f"session {sid} is pinned; `pilead keep {sid} --off` releases it. Nothing was {what}.")
    now = status_now(home, sid, meta)
    n = int(meta.get("packets") or 1)
    if now == "busy" and not state.report_path(home, sid, n).is_file() and not force:
        if via_send:
            raise PileadError(
                f"session {sid} is busy on packet {n:04d} and has not reported: retiring it now ends that work "
                f"unreported. Wait for it (`pilead check {sid}`), or retire it anyway with `pilead retire {sid} "
                f"--force` and then `{seed.spawn_command(meta, sid)}`. Nothing was sent.")
        raise PileadError(
            f"session {sid} is busy on packet {n:04d} and has not reported: retiring now ends that work unreported "
            f"(the seed lists it as NO REPORT). Wait for it (`pilead check {sid}`), or retire anyway with --force. "
            "Nothing was retired.")
    return now


def retire(home, sid, meta, force=False, keep_tab=False, out=print, err=None):
    """Write the seed and close the session as superseded by `successor-seed` (check() must have passed).
    Ledgers `retired`; prints close's line, then where the seed is and the spawn command. Returns
    (the seed's path, the number of packets in it)."""
    if state.read_status(home, sid, meta) not in status_mod.TERMINAL:
        status_mod.refresh(home, meta)  # stores (and logs) what check() worked out, as list or check would
        meta = state.load_meta(home, sid)
    entries = seed.entries(home, sid)
    wt = meta.get("worktree") or None
    u, mb = usage.session_reading(home, sid, wt, write_cache=False)
    _, nudge, mb_th = config.thresholds(home)
    heavy = usage.is_heavy(u, mb, nudge, mb_th)
    p = seed.path(home, sid)
    state.atomic_write(p, seed.build(sid, meta, entries, state.now_iso(), heavy,
                                     usage.window(home, meta.get("model")),
                                     directory=str(state.session_dir(home, sid))))
    lifecycle.close_executor(home, sid, meta, by=seed.RETIRED_BY, keep_tab=keep_tab, out=out, err=err)
    ledger.append(home, "retired", session_id=sid, seed=str(p), packets=len(entries), forced=bool(force))
    out(f"retired {sid} — successor seed ({len(entries)} packet(s)) at:")
    out(f"  {p}")
    out(f"  spawn its successor with: {seed.spawn_command(meta, sid)}")
    return p, len(entries)


def cmd_retire(a):
    sid = state.check_session_sid(a.sid)
    home = state.home_dir(a.home)
    meta = state.load_meta(home, sid)
    check(home, sid, meta, force=a.force)
    retire(home, sid, meta, force=a.force, keep_tab=a.keep_tab)
    seen.note(home, sid, meta, "retire")  # who looked at the report (lib/pilead/seen.py); best-effort
    from .close import queue_note  # at run time: a verb module that fails to load never takes retire down
    note = queue_note(home, sid, "retire")
    if note:
        print(note, file=sys.stderr)
    return 0


def register(verb):
    p = verb("retire", cmd_retire, "close an executor and write its successor seed (an index of its packets)")
    p.add_argument("sid", help="the executor to retire")
    p.add_argument("--force", action="store_true",
                   help="retire even when it is busy with no report (that packet is seeded as NO REPORT)")
    p.add_argument("--keep-tab", action="store_true",
                   help="leave its process and tab running (the tab is retitled `[closed] <sid>`)")

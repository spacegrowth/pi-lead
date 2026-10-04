"""pilead refs accept — make the repository's refs as they are now the packet's baseline (after the
lead's own commit), so `check` / `verify` stop reporting the lead's hand as REFS MOVED. On a session
whose baseline is missing or unreadable it stores one, so the same state then compares as unchanged."""
from .. import ledger, refs, state
from ..state import PileadError

ORDER = 65
RESOLVE = ("sid",)


def cmd_refs(a):
    home = state.home_dir(a.home)
    meta = state.load_meta(home, a.sid)
    n = int(meta.get("packets") or 1)
    try:
        snap = refs.snapshot(meta.get("worktree") or "")
    except refs.RefsError as e:
        raise PileadError(f"refs snapshot of {a.sid} failed: {e}", 1)
    snap["accepted"] = {"reason": a.reason, "ts": state.now_iso()}
    try:
        refs.store(home, a.sid, n, snap)
    except refs.RefsError as e:
        raise PileadError(f"refs baseline of {a.sid} not stored: {e}", 1)
    ledger.append(home, "refs_accepted", session_id=a.sid, packet=n, reason=a.reason, head=snap["head"])
    print(f"refs accepted for {a.sid} packet {n:04d} at {(snap['head'] or '(no HEAD)')[:12]}")


def register(verb):
    p = verb("refs", cmd_refs, "accept: make the refs as they are now the packet's baseline (after your commit)")
    p.add_argument("action", choices=["accept"])
    p.add_argument("sid")
    p.add_argument("--reason", help="why the refs moved (e.g. 'lead committed packet 0001')")

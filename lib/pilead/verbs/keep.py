"""pilead keep — pin an executor so it is left alone (`close` refuses it, `prune` never removes it), or
release the pin with --off. The pin records who set it (the calling lead, else null) and when."""
from .. import ledger, lifecycle, state

ORDER = 93
RESOLVE = ("sid",)


def cmd_keep(a):
    home = state.home_dir(a.home)
    meta = state.load_meta(home, a.sid)  # the session id is checked first, inside
    on = not a.off
    meta["keep"] = on
    meta["keep_by"] = lifecycle.caller(home) if on else None
    meta["keep_at"] = state.now_iso() if on else None
    state.save_meta(home, meta)
    ledger.append(home, "keep", session_id=a.sid, keep=on, keep_by=meta["keep_by"])
    print(f"pinned {a.sid} — it will be left alone" if on else f"unpinned {a.sid}")


def register(verb):
    p = verb("keep", cmd_keep, "pin an executor so it is left alone (close refuses it, prune keeps it)")
    p.add_argument("sid", help="the executor to pin")
    p.add_argument("--off", action="store_true", help="release the pin")

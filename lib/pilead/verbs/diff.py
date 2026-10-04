"""pilead diff — render an executor's staged diff to a review page (review.cmd_diff)."""
from .. import review, seen, state

ORDER = 70
RESOLVE = ("sid",)


def cmd_diff(a):
    """review.cmd_diff, then who looked at the report (lib/pilead/seen.py; best-effort, after the page)."""
    rc = review.cmd_diff(a)
    try:
        home = state.home_dir(a.home)
        seen.note(home, a.sid, state.load_meta(home, a.sid), "diff")
    except Exception:  # noqa: BLE001 — never changes diff's output or exit code
        pass
    return rc


def register(verb):
    p = verb("diff", cmd_diff, "render an executor's staged diff to sessions/<sid>/diff-NNNN.html")
    p.add_argument("sid"); p.add_argument("--open", action="store_true")
    p.add_argument("--all", action="store_true", help="whole staged diff, not just files the report names")

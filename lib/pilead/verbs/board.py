"""pilead board — render <home>/board.html (review.cmd_board); `--live` keeps it fresh (review.refresh_live_board)."""
from .. import review

ORDER = 80
RESOLVE = ("lead",)


def register(verb):
    p = verb("board", review.cmd_board, "render <home>/board.html from every lead and session")
    p.add_argument("--open", action="store_true", help="open the page afterwards")
    p.add_argument("--out", metavar="PATH", help="write the page to PATH (its directory is made), not <home>/board.html")
    p.add_argument("--lead", metavar="SID", help="only this lead's executors (and those with no lead)")
    p.add_argument("--json", action="store_true",
                   help="print the data the page is drawn from as JSON; write no page and no data file")
    p.add_argument("--live", nargs="?", const="on", choices=("on", "off"), metavar="on|off",
                   help="on (also with no value): a page that reloads itself, and a data file beside it that "
                        "later verbs keep rewritten; off: remove the data file")

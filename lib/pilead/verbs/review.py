"""pilead review — a review of an executor's staged work by a separate headless pi on the lead's own model,
with read-only tools and, by default, a copy of the lead's context (lib/pilead/reviewer.py). The verb
measures the repository before and after: a review during which anything moved is INVALID. With --why it
asks the same kind of process a question about the session's state instead, never with the lead's context."""
import argparse

from .. import reviewer, state

ORDER = 61
# The arguments `cli.main` resolves from a project name or a unique id prefix, once the names packet
# (p23c) lands; until then nothing reads it.
RESOLVE = ("sid", "lead")


def _timeout(text):
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number of seconds: {text!r}")
    if not reviewer.TIMEOUT_MIN <= v <= reviewer.TIMEOUT_MAX:
        raise argparse.ArgumentTypeError(f"must be from {reviewer.TIMEOUT_MIN} to {reviewer.TIMEOUT_MAX}: {v}")
    return v


def cmd_review(a):
    state.check_session_sid(a.sid)  # first: an unusable id reads, creates and writes nothing
    return reviewer.run(state.home_dir(a.home), a.sid, a)


def register(verb):
    p = verb("review", cmd_review,
             "review staged work with a separate headless pi on read-only tools (costs tokens)")
    p.add_argument("sid")
    p.add_argument("--packet", type=int, help="the packet to review (default: the newest report)")
    p.add_argument("--lead", metavar="SID", help="the calling lead, when $PI_LEAD_SID does not name it")
    p.add_argument("--model", metavar="SPEC",
                   help="the reviewer's model, an alias or provider/id (default: the lead's own model)")
    p.add_argument("--fresh", action="store_true", help="start the reviewer without a copy of the lead's context")
    p.add_argument("--no-rerun", action="store_true", help="do not re-run the report's declared test commands")
    p.add_argument("--timeout", type=_timeout, default=None, metavar="SECONDS",
                   help=f"stop the reviewer after this long, {reviewer.TIMEOUT_MIN}-{reviewer.TIMEOUT_MAX} "
                        f"(default {reviewer.TIMEOUT_DEFAULT})")
    p.add_argument("--large", action="store_true",
                   help=f"allow a staged diff of more than {reviewer.LARGE_DIFF:,} bytes")
    p.add_argument("--dry-run", action="store_true", help="print what would be started; start and write nothing")
    p.add_argument("--json", action="store_true", help="print the result as one JSON object")
    p.add_argument("--why", action="store_true",
                   help="ask about the session's state instead (why it is stalled, what it is doing): an "
                        f"answer of at most {reviewer.WHY_LINES} lines, no review, never the lead's context "
                        f"(timeout default {reviewer.WHY_TIMEOUT_DEFAULT})")

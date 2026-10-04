"""pilead verify — machine-check an executor's report against its staged diff (review.cmd_verify)."""
from .. import review

ORDER = 60
RESOLVE = ("sid",)


def register(verb):
    p = verb("verify", review.cmd_verify, "machine-check an executor's report against its staged diff")
    p.add_argument("sid"); p.add_argument("--packet", type=int)
    p.add_argument("--rerun", action="store_true",
                   help="also re-run the report's declared test commands in its worktree — pytest, or exactly "
                        "npm test, npm run check or node --test (argv only, never a shell, the git shim in force, "
                        "600s each) — and compare the counts; anything else is refused and named. A re-run "
                        "executes code from the worktree. With --for-autocommit, clearance then also needs the "
                        "re-run to match")
    p.add_argument("--for-autocommit", action="store_true"); p.add_argument("--in-plan", action="store_true")
    p.add_argument("--diff-reviewed", action="store_true"); p.add_argument("--findings")

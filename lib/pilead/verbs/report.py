"""pilead report — print an executor's whole report in a green frame (claude-relay 0.5.4's `cmd_report`).
`pilead check` prints only its TL;DR. It reads and prints; it writes nothing (no "looked at" note either)."""
import os
import subprocess

from .. import refs, state, views
from ..state import PileadError

ORDER = 51
RESOLVE = ("sid",)
BAR = "═" * 74
GIT_TIMEOUT = 10


def _has_staged(worktree):
    """True / False from the real git's `diff --cached --quiet` in `worktree`; None when it cannot be asked
    (no worktree, no git, a git that fails or does not answer in time)."""
    if not isinstance(worktree, str) or not worktree or not os.path.isdir(worktree):
        return None
    try:
        git = refs.git_path()
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        r = subprocess.run([git, "diff", "--cached", "--quiet"], cwd=worktree, env=env, timeout=GIT_TIMEOUT,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 — RefsError, OSError, TimeoutExpired: not known
        return None
    return {0: False, 1: True}.get(r.returncode)


def cmd_report(a):
    home = state.home_dir(a.home)
    if a.packet is not None and a.packet < 1:
        raise PileadError("report: --packet must be 1 or more", 2)
    try:
        meta = state.load_meta(home, a.sid)
    except ValueError:  # meta.json that is not JSON
        raise PileadError(f"report: the record of '{a.sid}' cannot be read", 1) from None
    if not isinstance(meta, dict):
        raise PileadError(f"report: the record of '{a.sid}' cannot be read", 1)
    n = a.packet
    if n is None:
        n = meta.get("packets")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise PileadError(f"report: the current packet of '{a.sid}' cannot be read — give --packet N", 1)
    rp = state.report_path(home, a.sid, n)
    if not rp.exists():
        raise PileadError(f"no report yet for '{a.sid}' packet {n:04d}", 1)
    try:
        text = rp.read_bytes().decode("utf-8", "replace")
    except OSError:
        raise PileadError(f"the report of '{a.sid}' packet {n:04d} cannot be read", 1) from None
    bar = views.c(BAR, "green", "bold")
    print(bar)
    print(views.c(f"  ✅ REPORT READY   ·   {a.sid}   ·   packet {n:04d}", "green", "bold"))
    print(views.c(f"  {rp}", "dim"))
    print(bar)
    print(text.rstrip())
    print(bar)
    if _has_staged(meta.get("worktree")) is True:
        print(views.c(f"  📄 review the staged diff: pilead diff {a.sid} --open", "dim"))
    return 0


def register(verb):
    p = verb("report", cmd_report, "print an executor's whole report (pilead check prints its TL;DR)")
    p.add_argument("sid")
    p.add_argument("--packet", type=int, metavar="N", help="the packet whose report to print (default: the current one)")

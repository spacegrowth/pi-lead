"""pilead lint — check a packet file before it is sent (advisory; read-only)."""
import json
import sys
from pathlib import Path

from .. import lint
from ..state import PileadError

ORDER = 15
RESOLVE = ()


def cmd_lint(a):
    sys.stdout.reconfigure(errors="replace")  # a character outside stdout's encoding is replaced, not fatal
    p = Path(a.packet)
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise PileadError(f"cannot read packet {a.packet}: {e.strerror or e}", 2)
    cwd = str(Path(a.worktree).expanduser().resolve()) if a.worktree else None
    findings = lint.lint_packet(body, cwd=cwd, model=a.model)
    if a.json:
        print(json.dumps([{"level": level, "code": code, "message": message}
                           for level, code, message in findings], indent=2))
    else:
        print(lint.format_findings(findings))
    if a.strict and any(level == "warn" for level, _, _ in findings):
        return 1
    return 0


def register(verb):
    p = verb("lint", cmd_lint, "check a packet file before sending it (advisory; --strict exits 1 on warnings)")
    p.add_argument("packet")
    p.add_argument("--worktree", help="resolve the packet's relative file references against this directory")
    p.add_argument("--model", help="the model text the executor would get (used as text only, never resolved)")
    p.add_argument("--strict", action="store_true", help="exit 1 when any finding is a warn")
    p.add_argument("--json", action="store_true")

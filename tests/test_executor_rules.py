"""skills/executor-rules.md carries claude-relay a0d3161's CONTEXT HYGIENE rules between GATES and REPORT FORMAT,
and the optional `Context: …` report line changes no verdict of `pilead verify` (vendor/relay/report_verify.py)."""
import re
from pathlib import Path

from test_verify_diff_board import SID, make_session, report, run, stage, write_report  # noqa: F401
from test_cli_core import home, pilead  # noqa: F401  (fixtures)

RULES = Path(__file__).resolve().parents[1] / "skills" / "executor-rules.md"


def test_context_hygiene_sits_between_gates_and_report_format():
    text = RULES.read_text()
    heads = [m.start() for m in (re.search(rf"^{h}$", text, re.M) for h in ("GATES", "CONTEXT HYGIENE", "REPORT FORMAT"))]
    assert all(isinstance(i, int) for i in heads) and heads == sorted(heads)
    section = text[heads[1]:heads[2]]
    for word in ("line range", "grep", "screenshot", "offset"):
        assert word in section, word
    assert "TREAT EVERY PACKET COLD means cold per packet, not per call" in section
    assert "~100k tokens" in section


def test_report_format_carries_the_optional_context_line_and_runner_counts():
    tail = RULES.read_text().split("REPORT FORMAT", 1)[1]
    assert "Context: hit the 120k warn" in tail and "Context: no warn" in tail and "Its absence is not malformed" in tail
    assert "`pytest: N passed`" in tail and "`node: N passed`" in tail


def _verdict(stdout):
    return [ln.strip() for ln in stdout.splitlines() if "VERDICT:" in ln]


def test_a_context_line_does_not_change_the_verdict(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    without = run("verify", SID)
    write_report(sdir, report() + "\nContext: no warn\n")
    with_ = run("verify", SID)
    assert without.returncode == with_.returncode == 0
    assert _verdict(without.stdout) == _verdict(with_.stdout)
    assert any("VERDICT: COUNTS-MATCH" in ln for ln in _verdict(with_.stdout))

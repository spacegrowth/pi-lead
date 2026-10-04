"""`lib/pilead/lint.py` (pure) and `pilead lint` (the CLI wrapper): see README "Packet lint"."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from pilead import lint  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"

# A body that satisfies every rule: long enough, has a Preconditions heading, no MCP/CONTEXT/EFFORT
# line, no commit/push or ask-someone phrasing, no acceptance-command block or files: list (so
# shape-haiku's has_acceptance_cmd/has_file_list precondition is false) and no investigate/diagnose
# words (so shape-opus's precondition is false).
CLEAN_BODY = """GOAL: rename the deprecated helper across the module; the call sites are already covered by
the existing test suite, so this is a mechanical rename with no behaviour change expected anywhere.

## Preconditions
- the worktree is clean and the branch is up to date
"""


def _codes(findings):
    return [c for _, c, _ in findings]


# ── one rule at a time: a packet that fires it, and one that does not ──────────────────────────────
def test_short_packet():
    assert "short-packet" in _codes(lint.lint_packet("too short"))
    assert "short-packet" not in _codes(lint.lint_packet(CLEAN_BODY))


def test_no_preconditions():
    body = "GOAL: " + ("x" * 100) + "\n"  # long enough, no heading
    assert "no-preconditions" in _codes(lint.lint_packet(body))
    assert "no-preconditions" not in _codes(lint.lint_packet(CLEAN_BODY))


def test_line_ignored_mcp_alone():
    body = CLEAN_BODY + "\nMCP: inherit\n"
    findings = [f for f in lint.lint_packet(body) if f[1] == "line-ignored"]
    assert len(findings) == 1
    assert "pi has no MCP support" in findings[0][2]
    assert "line-ignored" not in _codes(lint.lint_packet(CLEAN_BODY))


def test_line_ignored_context_alone():
    body = CLEAN_BODY + "\nCONTEXT: 1m\n"
    findings = [f for f in lint.lint_packet(body) if f[1] == "line-ignored"]
    assert len(findings) == 1
    assert "context window comes from pi's model list" in findings[0][2]


def test_effort_line_with_a_level_gives_no_finding():
    codes = _codes(lint.lint_packet(CLEAN_BODY + "\nEFFORT: high\n"))
    assert "line-ignored" not in codes and "effort-unparsable" not in codes


def test_line_ignored_all_three_give_two_findings():
    body = CLEAN_BODY + "\nMCP: inherit\nCONTEXT: 1m\nEFFORT: high\n"
    findings = [f for f in lint.lint_packet(body) if f[1] == "line-ignored"]
    assert len(findings) == 2


def test_effort_unparsable_value_gives_one_finding():
    body = CLEAN_BODY + "\nEFFORT: ultra\nEFFORT: also-bad\n"
    findings = [f for f in lint.lint_packet(body) if f[1] == "effort-unparsable"]
    assert findings == [("warn", "effort-unparsable", "EFFORT: line present but value isn't one of "
                         "off/minimal/low/medium/high/xhigh/max: 'ultra' — pilead ignores it")]


@pytest.mark.parametrize("level", ["off", "minimal", "low", "medium", "high", "xhigh", "max"])
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_effort_levels_in_any_case_give_no_finding(level, case):
    for line in (f"EFFORT: {case(level)}", f"- **EFFORT**:   {case(level)}  ", f"> effort: {case(level)}"):
        codes = _codes(lint.lint_packet(CLEAN_BODY + "\n" + line + "\n"))
        assert "effort-unparsable" not in codes and "line-ignored" not in codes, line


def test_effort_unparsable_is_listed_where_line_ignored_is():
    body = CLEAN_BODY + "\nMCP: inherit\nEFFORT: nope\nWhen done, commit your changes.\n"
    codes = _codes(lint.lint_packet(body))
    assert codes.index("line-ignored") < codes.index("effort-unparsable") < codes.index("asks-to-commit")
    assert lint.EFFORT_LEVELS == ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def test_asks_to_commit():
    body = CLEAN_BODY + "\nWhen done, commit your changes.\n"
    assert "asks-to-commit" in _codes(lint.lint_packet(body))
    assert "asks-to-commit" not in _codes(lint.lint_packet(CLEAN_BODY))


def test_asks_to_ask():
    body = CLEAN_BODY + "\nIf anything is unclear, ask the user before proceeding.\n"
    assert "asks-to-ask" in _codes(lint.lint_packet(body))
    assert "asks-to-ask" not in _codes(lint.lint_packet(CLEAN_BODY))


SHAPED_BODY = CLEAN_BODY + """
## Acceptance
```
pytest tests
```
files:
- `lib/x.py`
"""


def test_shape_haiku():
    findings = _codes(lint.lint_packet(SHAPED_BODY, model="sonnet"))
    assert "shape-haiku" in findings
    # does not fire: same shape, but already on a cheap tier
    assert "shape-haiku" not in _codes(lint.lint_packet(SHAPED_BODY, model="haiku"))
    assert "shape-haiku" not in _codes(lint.lint_packet(SHAPED_BODY, model="dsflash"))
    # does not fire: no file list / acceptance command at all
    assert "shape-haiku" not in _codes(lint.lint_packet(CLEAN_BODY, model="sonnet"))


OPUS_SHAPED_BODY = CLEAN_BODY + "\nInvestigate why the retry counter drifts under load.\n"


def test_shape_opus():
    findings = _codes(lint.lint_packet(OPUS_SHAPED_BODY, model="sonnet"))
    assert "shape-opus" in findings
    assert "shape-opus" not in _codes(lint.lint_packet(OPUS_SHAPED_BODY, model="opus"))
    assert "shape-opus" not in _codes(lint.lint_packet(OPUS_SHAPED_BODY, model="fable"))
    # a repro/steps-to section disqualifies it
    with_repro = OPUS_SHAPED_BODY + "\nRepro: run `make crash` twice in a row.\n"
    assert "shape-opus" not in _codes(lint.lint_packet(with_repro, model="sonnet"))
    assert "shape-opus" not in _codes(lint.lint_packet(CLEAN_BODY, model="sonnet"))


# ── model_tier ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model,want", [
    (None, None),
    ("haiku", "haiku"),
    ("sonnet", "sonnet"),
    ("opus", "opus"),
    ("fable", "fable"),
    ("dsflash", "dsflash"),
    ("dspro", "dspro"),
    ("anthropic/claude-haiku-4-5", "haiku"),
    ("deepseek/deepseek-flash", "dsflash"),
    ("deepseek/deepseek-v4-pro", "dspro"),
    ("something-else-entirely", None),
])
def test_model_tier(model, want):
    assert lint.model_tier(model) == want


# ── reading-front-loaded ─────────────────────────────────────────────────────────────────────────
def _write(p, n):
    p.write_bytes(b"x" * n)


def test_reading_front_loaded_below_threshold(tmp_path):
    _write(tmp_path / "big.py", lint.READING_FRONT_LOADED_BYTES - 1)
    body = CLEAN_BODY + "\nSee `big.py` for the details.\n"
    findings = lint.lint_packet(body, cwd=str(tmp_path))
    assert "reading-front-loaded" not in _codes(findings)


def test_reading_front_loaded_at_threshold(tmp_path):
    _write(tmp_path / "big.py", lint.READING_FRONT_LOADED_BYTES)
    body = CLEAN_BODY + "\nSee `big.py` for the details.\n"
    findings = lint.lint_packet(body, cwd=str(tmp_path))
    assert "reading-front-loaded" in _codes(findings)


def test_reading_front_loaded_missing_path_counts_nothing(tmp_path):
    body = CLEAN_BODY + "\nSee `does-not-exist.py` for the details.\n"
    findings = lint.lint_packet(body, cwd=str(tmp_path))
    assert "reading-front-loaded" not in _codes(findings)


def test_reading_front_loaded_unknown_users_home_does_not_raise(tmp_path):
    # ~name/... for a name pathlib cannot resolve a home directory for: expanduser() raises
    # RuntimeError on some platforms — packet_reading_bytes must swallow it, not crash.
    body = CLEAN_BODY + "\nSee ~nosuchuserxyz123/deploy.sh for context.\n"
    findings = lint.lint_packet(body, cwd=str(tmp_path))  # must not raise
    assert "reading-front-loaded" not in _codes(findings)


# ── format_findings ──────────────────────────────────────────────────────────────────────────────
def test_format_findings_no_findings():
    assert lint.format_findings([]) == "✓ packet lint: no findings"


def test_format_findings_marks():
    findings = [("warn", "asks-to-commit", "msg1"), ("info", "shape-opus", "msg2")]
    out = lint.format_findings(findings)
    assert out == "⚠ [asks-to-commit] msg1\nℹ [shape-opus] msg2"


# ── the CLI wrapper ──────────────────────────────────────────────────────────────────────────────
def _run(args, env=None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(PILEAD), "lint", *args], capture_output=True, text=True,
                           env=full_env)


def test_cli_clean_packet_prints_exactly_no_findings(tmp_path):
    p = tmp_path / "packet.md"
    p.write_text(CLEAN_BODY)
    r = _run([str(p)])
    assert r.returncode == 0
    assert r.stdout == "✓ packet lint: no findings\n"


def test_cli_json(tmp_path):
    p = tmp_path / "packet.md"
    p.write_text(CLEAN_BODY + "\nMCP: inherit\n")
    r = _run([str(p), "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert isinstance(data, list) and len(data) == 1
    assert set(data[0]) == {"level", "code", "message"}
    assert data[0]["code"] == "line-ignored"


def test_cli_strict_exits_1_on_warn(tmp_path):
    p = tmp_path / "packet.md"
    p.write_text(CLEAN_BODY + "\nMCP: inherit\n")
    r = _run([str(p), "--strict"])
    assert r.returncode == 1


def test_cli_strict_exits_0_when_only_info(tmp_path):
    p = tmp_path / "packet.md"
    p.write_text(SHAPED_BODY)
    r = _run([str(p), "--strict", "--model", "sonnet"])
    findings = lint.lint_packet(SHAPED_BODY, model="sonnet")
    assert all(level == "info" for level, _, _ in findings) and findings  # sanity: only info fired
    assert r.returncode == 0


def test_cli_missing_packet_file(tmp_path):
    r = _run([str(tmp_path / "nope.md")])
    assert r.returncode == 2
    assert r.stdout == ""
    lines = r.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("pilead: ")
    assert "Traceback" not in r.stderr


def test_cli_writes_nothing(tmp_path):
    p = tmp_path / "packet.md"
    p.write_text(CLEAN_BODY)
    fresh_home = tmp_path / "fresh-home"
    fake_pi_log = Path(os.environ["FAKE_PI_LOG"])
    if fake_pi_log.exists():
        fake_pi_log.unlink()
    r = _run([str(p), "--home", str(fresh_home)])
    assert r.returncode == 0
    assert not fresh_home.exists()
    assert not fake_pi_log.exists() or fake_pi_log.read_text() == ""


# ── `spawn` / `send` lint the outgoing packet and advise on stderr (change 1) ───────────────────────
from test_cli_core import (  # noqa: E402  (fixtures/helpers; PILEAD_NO_LINT is off by default — see conftest)
    env, heavy_entries, home, make_reported, pilead, spawn, spawn_with_log, start_lead, stub_osascript)


# spawn's whole stdout here, the session's time (`-HHMMSS`, the only part that varies) replaced by a
# token, as tests/test_cli_surface.py replaces the home.
TIME_TOKEN = "{TIME}"
SPAWNED = (f"spawned topic-{TIME_TOKEN} model=anthropic/claude-sonnet-5 tab=FAKE-UUID\n"
           "effort=high scope=topic\n"
           "placement=end-of-bar (no lead iTerm handle)\n")


def _timeless(out):
    return re.sub(r"\Aspawned topic-\d{6} ", f"spawned topic-{TIME_TOKEN} ", out)


def _advice_lines(findings):
    return [f"  {'⚠' if level == 'warn' else 'ℹ'} lint[{code}]: {message}" for level, code, message in findings]


def test_spawn_advises_tiny_packet_on_stderr(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    start_lead()
    body = (env / "packet.md").read_text()
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    assert _timeless(r.stdout) == SPAWNED
    assert r.stderr.splitlines() == _advice_lines(lint.lint_packet(body))
    assert r.stderr.splitlines()  # sanity: the tiny packet does fire findings


def test_spawn_advises_nothing_for_a_clean_packet(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    (env / "clean.md").write_text(CLEAN_BODY)
    start_lead()
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "clean.md"), "--model", "sonnet", "--lead", "lead-1")
    assert r.stderr == ""


def test_send_advises_tiny_packet_on_stderr(env, monkeypatch):
    sid = spawn(env)
    make_reported(env, sid)
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    body = (env / "packet.md").read_text()
    r = pilead("send", sid, str(env / "packet.md"))
    assert r.stderr.splitlines() == _advice_lines(lint.lint_packet(body))
    assert r.stderr.splitlines()


def test_send_advises_nothing_for_a_clean_packet(env, monkeypatch):
    sid = spawn(env)
    make_reported(env, sid)
    (env / "clean.md").write_text(CLEAN_BODY)
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    r = pilead("send", sid, str(env / "clean.md"))
    assert r.stderr == ""


def test_send_advises_once_on_the_relaunch_path(env, monkeypatch):
    sid = spawn(env)
    pilead("close", sid)
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    body = (env / "packet.md").read_text()
    r = pilead("send", sid, str(env / "packet.md"))
    assert r.stderr.splitlines() == _advice_lines(lint.lint_packet(body))


def test_send_refusing_a_heavy_session_prints_no_lint_line(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    sid = spawn_with_log(env, *heavy_entries(), git=True)
    r = pilead("send", sid, str(env / "p2.md"), check=False)
    assert r.returncode == 2
    assert "lint[" not in r.stderr


def test_no_lint_switch_silences_both_verbs(env, monkeypatch):
    monkeypatch.setenv("PILEAD_NO_LINT", "1")
    start_lead()
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid = r.stdout.split()[1]
    assert r.stderr == ""
    make_reported(env, sid)
    r2 = pilead("send", sid, str(env / "packet.md"))
    assert r2.stderr == ""


def test_advise_swallows_a_raising_lint_packet_and_spawn_still_succeeds(env, monkeypatch, capsys):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    start_lead()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lint, "lint_packet", boom)
    from pilead.cli import main
    rc = main(["spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert _timeless(out) == SPAWNED
    assert err == ""


def test_spawn_ascii_ioencoding_replaces_the_marks(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    start_lead()
    r = subprocess.run(
        [str(PILEAD), "spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet",
         "--lead", "lead-1"],
        capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "ascii"})
    assert r.returncode == 0
    assert _timeless(r.stdout) == SPAWNED
    assert "Traceback" not in r.stderr
    lines = r.stderr.splitlines()
    assert len(lines) == 2
    assert "⚠" not in r.stderr and "ℹ" not in r.stderr
    assert "short-packet" in lines[0] and "no-preconditions" in lines[1]


def _sans_ts_and_sid(rec, sid):
    out = {}
    for k, v in rec.items():
        if k == "ts":
            continue
        if isinstance(v, str) and sid in v:
            v = v.replace(sid, "<SID>")
        out[k] = v
    return out


def test_ledger_unchanged_by_lint_findings(env, monkeypatch):
    """A spawn + send that both print lint advice write the same ledger events (names and field names/
    values, `ts` and the session id aside) as the same sequence without lint."""
    from pilead import ledger

    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    home_on = env / "home-on"
    monkeypatch.setenv("PI_LEAD_HOME", str(home_on))
    pilead("lead-start", "lead-1", "--project", "proj")
    r_on = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid_on = r_on.stdout.split()[1]
    assert r_on.stderr  # lint did fire
    make_reported(env, sid_on, home=env / "home-on")
    pilead("send", sid_on, str(env / "packet.md"))
    events_on = [_sans_ts_and_sid(rec, sid_on) for rec in ledger.read(home_on)]

    monkeypatch.setenv("PILEAD_NO_LINT", "1")
    home_off = env / "home-off"
    monkeypatch.setenv("PI_LEAD_HOME", str(home_off))
    pilead("lead-start", "lead-1", "--project", "proj")
    r_off = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid_off = r_off.stdout.split()[1]
    assert r_off.stderr == ""
    make_reported(env, sid_off, home=env / "home-off")
    pilead("send", sid_off, str(env / "packet.md"))
    events_off = [_sans_ts_and_sid(rec, sid_off) for rec in ledger.read(home_off)]

    assert events_on == events_off

"""`pilead status [sid]`: one line for a lead or an executor, and nothing written — the home directory is
byte-identical (every file's content and modification time) before and after. Through bin/pilead."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"


@pytest.fixture(autouse=True)
def no_identity(monkeypatch):
    for var in ("PI_LEAD_SID", "PI_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def H():
    return Path(os.environ["PI_LEAD_HOME"])


def status(*args, **env):
    r = subprocess.run([sys.executable, str(PILEAD), "status", *args], capture_output=True, text=True,
                       env={**os.environ, **env})
    assert r.returncode == 0 and r.stderr == "", r.stderr
    return r.stdout


def lead(sid, project, cwd):
    (H() / "leads").mkdir(parents=True, exist_ok=True)
    (H() / "leads" / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": project, "cwd": str(cwd)}))


def session(tmp_path, sid, lead_sid="lead-1", st="busy", report=False, packets=1):
    sdir = H() / "sessions" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": lead_sid, "worktree": str(tmp_path / f"wt-{sid}"), "topic": sid,
        "model": "anthropic/claude-sonnet-5", "status": st, "created": "-", "packets": packets}))
    (sdir / "status").write_text(st + "\n")
    if report:
        (sdir / f"report-{packets:04d}.md").write_text("done\n")


def snapshot_home():
    return {str(p.relative_to(H())): (p.stat().st_mtime_ns, p.read_bytes()) for p in H().rglob("*") if p.is_file()}


def fixture(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    write_log("lead-1", tmp_path / "lc", [assistant(u(10, 5, 146_000, 0))])
    (H() / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": "anthropic", "id": "claude-sonnet-5", "context": "1M"}]}))
    session(tmp_path, "a-busy")
    session(tmp_path, "b-busy")
    session(tmp_path, "c-rep", st="busy", report=True)       # the report file upgrades it
    session(tmp_path, "d-rep", st="reported", report=True)
    session(tmp_path, "e-closed", st="closed", report=True)  # closed: not named
    session(tmp_path, "f-dead", st="dead")
    session(tmp_path, "g-idle", st="idle")
    session(tmp_path, "h-other", lead_sid="lead-2")
    write_log("a-busy", tmp_path / "wt-a-busy", [assistant(u(10, 5, 12_000, 0))])


def test_lead_line(tmp_path):
    fixture(tmp_path)
    assert status("lead-1") == "lead proj · busy: a-busy,b-busy · reported: c-rep,d-rep · ctx 146k/1M\n"


def test_lead_line_segments_absent(tmp_path):
    lead("lead-9", "lonely", tmp_path / "nowhere")
    assert status("lead-9") == "lead lonely · ctx -\n"  # no busy, no reported; ctx unknown is `-`
    session(tmp_path, "r1", lead_sid="lead-9", report=True)
    assert status("lead-9") == "lead lonely · reported: r1 · ctx -\n"


def test_executor_line(tmp_path):
    fixture(tmp_path)
    assert status("a-busy") == "pkt 0001 busy · for proj · ctx 12k/1M\n"
    assert status("c-rep") == "pkt 0001 reported · for proj · ctx -\n"
    assert status("e-closed") == "pkt 0001 reported · for proj · ctx -\n"  # the report file upgrades any stored status
    assert status("f-dead") == "pkt 0001 dead · for proj · ctx -\n"


def test_executor_line_without_a_readable_lead_record(tmp_path):
    fixture(tmp_path)
    assert status("h-other") == "pkt 0001 busy · ctx -\n"  # lead-2 has no record
    (H() / "leads" / "lead-1.json").write_text("{broken")
    assert status("b-busy") == "pkt 0001 busy · ctx -\n"


def test_sid_from_the_environment(tmp_path):
    fixture(tmp_path)
    assert status(PI_LEAD_SID="a-busy") == "pkt 0001 busy · for proj · ctx 12k/1M\n"
    assert status(PI_SESSION_ID="lead-1").startswith("lead proj · ")
    assert status("b-busy", PI_LEAD_SID="a-busy").startswith("pkt 0001 busy")  # the argument wins
    assert status(PI_LEAD_SID="a-busy", PI_SESSION_ID="lead-1").startswith("pkt 0001")  # PI_LEAD_SID first


def test_unknown_and_no_sid_print_nothing_and_exit_0(tmp_path):
    fixture(tmp_path)
    assert status("nobody") == ""
    assert status() == ""
    assert status("../leads/lead-1") == ""


def test_status_writes_nothing(tmp_path):
    fixture(tmp_path)
    (H() / "sessions" / "a-busy" / "pid").write_text("999999\n")  # a stale pid: `status` must not act on it
    old = time.time() - 50_000
    for p in H().rglob("*"):
        os.utime(p, (old, old))
    before = snapshot_home()
    for sid in ("lead-1", "a-busy", "c-rep", "f-dead", "h-other", "nobody"):
        status(sid)
    status()
    after = snapshot_home()
    assert after == before
    assert not any(k.endswith("usage.json") for k in after) and "ledger.jsonl" not in after


# ── auto-close: `pilead status` never parks, even run by a lead ────────────────────────────────────
def test_status_run_by_a_lead_parks_nothing(tmp_path):
    from test_autoclose import session as landed_session
    from test_close_variants import tree
    landed_session(tmp_path, "s-landed")
    before = tree(H())
    for args in (["s-landed"], ["lead-1"], []):
        out = status(*args, PI_LEAD_SID="lead-1")
        assert "auto-clos" not in out
    assert tree(H()) == before
    assert (H() / "sessions" / "s-landed" / "status").read_text() == "reported\n"


# ── --statusline: the line pi's footer shows ─────────────────────────────────────────────────────
def health(sid="lead-1", **fields):
    """wake/<sid>/health.json of a live, healthy lead (its wake is `ok`) unless `fields` say otherwise."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    h = {"sid": sid, "pid": os.getpid(), "started": now, "beat": now, "watching": True, "poll_seconds": 3,
         "delivered": 0, "last_delivered": None, "pending": 0, "last_error": None, "last_error_at": None,
         "ended": None, **fields}
    d = H() / "wake" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps(h))


def sl(sid, *args):
    return status(sid, "--statusline", *args)


def test_statusline_for_a_lead_with_no_executor(tmp_path):
    lead("lead-9", "lonely", tmp_path / "lc")
    health("lead-9")
    assert sl("lead-9") == "🚦 lonely\n"
    rec = json.loads((H() / "leads" / "lead-9.json").read_text())
    rec.pop("project")
    (H() / "leads" / "lead-9.json").write_text(json.dumps(rec))
    assert sl("lead-9") == "🚦 lead\n"


def test_statusline_with_one_busy_and_one_reported(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    health()
    session(tmp_path, "a-busy")
    session(tmp_path, "b-rep", report=True)
    session(tmp_path, "c-closed", st="closed", report=True)
    assert sl("lead-1") == "🚦 proj · busy: a-busy · ✅ b-rep\n"


def test_statusline_names_three_then_the_rest_as_a_count(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    health()
    for n in "abcde":
        session(tmp_path, f"{n}-busy")
    assert sl("lead-1") == "🚦 proj · busy: a-busy,b-busy,c-busy +2\n"


def test_statusline_shows_the_wake_cell_when_it_is_not_ok(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    session(tmp_path, "a-busy")
    assert sl("lead-1") == "🚦 proj · busy: a-busy · WAKE off\n"
    health()
    assert sl("lead-1") == "🚦 proj · busy: a-busy\n"
    health(beat="2020-01-01T00:00:00Z")
    assert sl("lead-1") == "🚦 proj · busy: a-busy · WAKE STALE\n"
    health(last_error="x", last_error_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    assert sl("lead-1") == "🚦 proj · busy: a-busy · WAKE STUCK\n"
    (H() / "wake" / "lead-1" / "health.json").write_text("[1]")
    assert sl("lead-1") == "🚦 proj · busy: a-busy · WAKE ?\n"
    health(ended="2020-01-01T00:00:00Z")
    assert sl("lead-1") == "🚦 proj · busy: a-busy · WAKE off\n"


def test_statusline_weight_segment_against_the_leads_line(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    health()
    # the default line is lead_nudge_tokens 300k: 60% is 180k
    assert sl("lead-1", "--ctx-tokens", "177000") == "🚦 proj\n"  # 59%
    assert sl("lead-1", "--ctx-tokens", "180000") == "🚦 proj · 180k ctx\n"  # 60%
    assert sl("lead-1", "--ctx-tokens", "299999") == "🚦 proj · 300k ctx\n"
    assert sl("lead-1", "--ctx-tokens", "300000") == "🚦 proj · 300k ctx → hand off\n"  # 100%
    assert sl("lead-1", "--ctx-tokens", "0") == "🚦 proj\n"
    # a 200000 window caps the line at context_nudge_tokens (150k): 60% is 90k
    assert sl("lead-1", "--ctx-tokens", "89999", "--ctx-window", "200000") == "🚦 proj\n"
    assert sl("lead-1", "--ctx-tokens", "90000", "--ctx-window", "200000") == "🚦 proj · 90k ctx\n"
    assert sl("lead-1", "--ctx-tokens", "150000", "--ctx-window", "200000") == "🚦 proj · 150k ctx → hand off\n"
    assert sl("lead-1", "--ctx-tokens", "150000", "--ctx-window", "1000000") == "🚦 proj\n"
    assert sl("lead-1", "--ctx-tokens", "150000", "--ctx-window", "0") == "🚦 proj\n", "a window of 0 is not one"


def test_statusline_ctx_tokens_that_are_not_a_whole_number_print_no_weight_segment(tmp_path):
    lead("lead-1", "proj", tmp_path / "lc")
    health()
    for v in ("abc", "-1", "1.5", "", "٣٠٠٠٠٠", "²"):
        assert sl("lead-1", f"--ctx-tokens={v}") == "🚦 proj\n", v
    assert sl("lead-1", "--ctx-window", "200000") == "🚦 proj\n", "no tokens: no weight segment"


def test_statusline_for_an_executor(tmp_path):
    fixture(tmp_path)
    assert sl("a-busy") == "pkt 0001 busy · for proj\n"
    assert sl("c-rep") == "pkt 0001 reported · for proj\n"
    assert sl("h-other") == "pkt 0001 busy\n"  # its lead has no record


def test_statusline_for_an_unknown_sid_prints_nothing(tmp_path):
    fixture(tmp_path)
    assert sl("nobody") == ""
    assert sl("../leads/lead-1") == ""
    assert status("--statusline") == ""


def test_statusline_writes_nothing(tmp_path):
    fixture(tmp_path)
    health()
    (H() / "sessions" / "a-busy" / "pid").write_text("999999\n")
    old = time.time() - 50_000
    for p in H().rglob("*"):
        os.utime(p, (old, old))
    before = snapshot_home()
    paths = {str(p.relative_to(H())) for p in H().rglob("*")}  # directories too
    for sid in ("lead-1", "a-busy", "c-rep", "h-other", "nobody"):
        sl(sid)
        sl(sid, "--ctx-tokens", "200000", "--ctx-window", "200000")
    assert snapshot_home() == before  # every file's content and modification time
    assert {str(p.relative_to(H())) for p in H().rglob("*")} == paths  # nothing added or removed


def test_statusline_never_opens_a_session_log(tmp_path, monkeypatch, capsys):
    """The session logs are directories where pi would put them (a read would raise); and in-process every
    reader of a session log raises: the lines are printed all the same."""
    lead("lead-1", "proj", tmp_path / "lc")
    health()
    session(tmp_path, "a-busy")
    from test_usage import agent_dir, encoded
    for sid, cwd in (("lead-1", tmp_path / "lc"), ("a-busy", tmp_path / "wt-a-busy")):
        (agent_dir() / "sessions" / encoded(cwd) / f"2026-01-01T00-00-00-000Z_{sid}.jsonl").mkdir(parents=True)
    assert sl("lead-1") == "🚦 proj · busy: a-busy\n"
    assert sl("a-busy") == "pkt 0001 busy · for proj\n"
    sys.path.insert(0, str(ROOT / "lib"))
    from pilead import cli, usage

    def boom(*a, **k):
        raise AssertionError("a session log was read")
    for name in ("locate", "for_lead", "for_session", "lead_reading", "session_reading", "read_usage"):
        if hasattr(usage, name):
            monkeypatch.setattr(usage, name, boom)
    for sid, want in (("lead-1", "🚦 proj · busy: a-busy\n"), ("a-busy", "pkt 0001 busy · for proj\n")):
        assert cli.main(["status", sid, "--statusline"]) in (0, None)
        assert capsys.readouterr().out == want

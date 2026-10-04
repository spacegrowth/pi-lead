"""The verbs that look at a report (`check`, `diff`, `verify`, `close`, `retire`) and who they say looked
(lib/pilead/seen.py): an executor's own look never counts as the lead's, so it never parks the executor and never
resolves its escalation. Drives bin/pilead with PI_SESSION_ID set by the test; homes under the test's temp
directory; the terminal is conftest's osascript stub."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pilead import autoclose, escalation, ledger, seen  # noqa: E402
from test_autoclose import H, session  # noqa: E402

PILEAD = ROOT / "bin" / "pilead"
SID, LEAD = "s-look", "lead-1"


def run(tmp_path, *argv, who=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PI_SESSION_ID", "PI_LEAD_SID")}
    env["PI_LEAD_HOME"] = str(H(tmp_path))
    if who is not None:
        env["PI_SESSION_ID"] = who
    return subprocess.run([sys.executable, str(PILEAD), *argv], capture_output=True, text=True, env=env)


def recs(tmp_path, event=None):
    return [r for r in ledger.read(H(tmp_path)) if r.get("session_id") == SID and (event is None or r["event"] == event)]


def looked(tmp_path):
    return seen.looked_at(ledger.read(H(tmp_path)), SID, 1)


@pytest.fixture
def reported(tmp_path):
    session(tmp_path, SID, seen=None)
    return tmp_path


def own_looks(tmp_path):
    for argv in (["check", SID], ["diff", SID], ["verify", SID]):
        r = run(tmp_path, *argv, who=SID)
        assert "Traceback" not in r.stderr, (argv, r.stderr)


def test_an_executors_own_check_diff_and_verify_do_not_count_then_the_leads_check_does(reported):
    tmp_path = reported
    own_looks(tmp_path)
    assert looked(tmp_path) is False
    (declined,) = recs(tmp_path, "surfaced_stamp_declined")
    assert declined["caller"] == "self" and declined["owner_lead"] == LEAD and declined["via"] == "check"
    assert {r["caller"] for r in recs(tmp_path) if r["event"] in ("usage_snapshot", "report_verify")} == {"self"}
    assert recs(tmp_path, "report_looked_at") == []
    run(tmp_path, "check", SID, who=LEAD)
    assert looked(tmp_path) is True
    (look,) = recs(tmp_path, "report_looked_at")
    assert (look["caller"], look["via"], look["packet"]) == ("lead", "check", 1)
    assert len(recs(tmp_path, "surfaced_stamp_declined")) == 1


def test_the_same_with_no_pi_session_id(reported):
    tmp_path = reported
    own_looks(tmp_path)
    assert looked(tmp_path) is False
    run(tmp_path, "check", SID)
    (look,) = recs(tmp_path, "report_looked_at")
    assert (look["caller"], look["via"]) == ("unknown", "check")
    assert looked(tmp_path) is True


def test_a_leads_first_check_counts_through_its_reported_snapshot(reported):
    tmp_path = reported
    run(tmp_path, "check", SID, who=LEAD)
    (snap,) = recs(tmp_path, "usage_snapshot")
    assert snap["caller"] == "lead" and snap["at"] == "reported"
    assert recs(tmp_path, "report_looked_at") == []  # the snapshot already counts
    assert looked(tmp_path) is True


@pytest.mark.parametrize("who", [SID, LEAD, None])
def test_check_refs_only_writes_no_look(reported, who):
    tmp_path = reported
    r = run(tmp_path, "check", SID, "--refs-only", "--wake", who=who)
    assert r.returncode == 0
    assert recs(tmp_path, "report_looked_at") == [] and recs(tmp_path, "surfaced_stamp_declined") == []


@pytest.mark.parametrize("verb", ["close", "retire"])
def test_close_and_retire_by_the_lead_write_report_looked_at(reported, verb):
    tmp_path = reported
    r = run(tmp_path, verb, SID, who=LEAD)
    assert r.returncode == 0, r.stderr
    (look,) = recs(tmp_path, "report_looked_at")
    assert (look["caller"], look["via"]) == ("lead", verb)


def test_diff_by_the_lead_writes_report_looked_at(reported):
    tmp_path = reported
    run(tmp_path, "diff", SID, who=LEAD)
    (look,) = recs(tmp_path, "report_looked_at")
    assert (look["caller"], look["via"]) == ("lead", "diff")


def test_the_sweep_and_escalation_wait_for_the_lead_not_the_executor(reported):
    tmp_path = reported
    own_looks(tmp_path)
    assert escalation.decide(H(tmp_path), SID, dry_run=True)["outcome"] != "resolved"
    assert autoclose.sweep(H(tmp_path), "manual", dry_run=True) == []
    # the executor's own `check` ran the sweep too (it is not a lead, so nothing of its own) — still open
    assert (H(tmp_path) / "sessions" / SID / "status").read_text().strip() == "reported"
    assert recs(tmp_path, "auto_closed") == []
    # once the lead has looked: escalation is resolved, and the sweep parks it
    ledger.append(H(tmp_path), "report_looked_at", session_id=SID, packet=1, caller="lead", via="check")
    assert escalation.decide(H(tmp_path), SID, dry_run=True)["outcome"] == "resolved"
    assert autoclose.sweep(H(tmp_path), "manual") == [(SID, "close", "landed")]


def test_the_leads_own_check_parks_what_its_executors_looks_did_not(reported):
    tmp_path = reported
    own_looks(tmp_path)
    assert recs(tmp_path, "auto_closed") == []
    r = run(tmp_path, "check", SID, who=LEAD)
    assert r.stdout.rstrip().splitlines()[-1].startswith(f"auto-closed {SID} (landed)")
    (ev,) = recs(tmp_path, "auto_closed")
    assert ev["trigger"] == "check"

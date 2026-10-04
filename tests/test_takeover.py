"""`pilead takeover` (lib/pilead/verbs/takeover.py), driven through bin/pilead. Whether a lead's tab exists is
answered by the terminal seam of tests/test_adopt.py only: a recording `osascript` stub answering $FAKE_TAB and a
`ps` stub saying iTerm runs. Homes are under the test's temp directory; no tab is opened or closed."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_adopt import HANDLE, OSA, PS, iso

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
OLD, NEW = "lead-old", "lead-new"


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "true")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    (h / "leads").mkdir(parents=True)
    return h


def run(home, *args, **env):
    e = {**os.environ, **env}
    return subprocess.run([str(PILEAD), "takeover", *args, "--home", str(home)], capture_output=True, text=True, env=e)


def lead(home, live="ghost", sid=OLD, project="proj"):
    p = home / "leads" / f"{sid}.json"
    if live == "broken":
        p.write_text("{not json")
        return p
    rec = {"sid": sid, "project": project, "cwd": "/repo", "inbox": str(home / "leads" / f"{sid}.inbox.md"),
           "started": iso(60 if live == "unreachable" else 86400), "terminal": "iTerm.app",
           "iterm_handle": HANDLE if live == "live" else None}
    p.write_text(json.dumps(rec))
    (home / "leads" / f"{sid}.inbox.md").write_text("")
    return p


def session(home, sid, lead_sid=OLD):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"sid": sid, "lead": lead_sid, "worktree": "/wt", "packets": 1}))


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
            for p in sorted(root.rglob("*"))}


def refused(home, r, before, text):
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.count("\n") == 1 and text in r.stderr, r.stderr
    assert tree(home) == before


def test_every_refusal_is_exit_2_with_home_byte_identical(home):
    lead(home, "live")
    session(home, "exec-a")
    before = tree(home)
    refused(home, run(home, "../x", "--as", NEW), before, "lead id '../x' is not usable")
    refused(home, run(home, OLD, "--as", "a/b"), before, "lead id 'a/b' is not usable")
    refused(home, run(home, OLD), before, "no new session id")
    refused(home, run(home, OLD, "--as", OLD), before, "nothing to take over")
    refused(home, run(home, OLD, "--as", NEW, PI_LEAD_ROLE="executor"), before, "an executor cannot take over a lead")
    refused(home, run(home, "lead-nobody", "--as", NEW), before, "lead-nobody is not a registered lead")
    refused(home, run(home, OLD, "--as", NEW), before,
            f"takeover: {OLD} is live: a lead that may still be running would lose its executors to {NEW} — "
            f"pilead takeover {OLD} --force takes it anyway")


@pytest.mark.parametrize("live", ["unreachable", "broken"])
def test_an_unreachable_or_broken_lead_is_refused_without_force(home, live):
    lead(home, live)
    before = tree(home)
    refused(home, run(home, OLD, "--as", NEW), before, f"{OLD} is {live}:")


def test_a_broken_record_with_force_changes_nothing(home):
    lead(home, "broken")
    before = tree(home)
    refused(home, run(home, OLD, "--as", NEW, "--force"), before, "cannot be read")


def test_a_ghost_is_taken_over_without_force_with_the_exact_lines(home):
    lead(home, "ghost")
    session(home, "exec-a")
    session(home, "exec-b")
    (home / "leads" / f"{OLD}.inbox.md").write_text("reported exec-a /r/a-1.md\n")
    (home / "plans").mkdir()
    (home / "plans" / f"{OLD}.json").write_text("{}")
    r = run(home, OLD, "--as", NEW)
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == (f"lead {OLD} → {NEW} (proj)\n"
                        f"adopted exec-a from {OLD} (ghost lead)\n"
                        f"adopted exec-b from {OLD} (ghost lead)\n"
                        "moved 1 pending wake line(s)\n"
                        "plan moved\n"
                        f"{NEW} is the lead now — run pilead lead-start {NEW} --project 'proj' in that session to "
                        "refresh its tab and posture\n")
    assert (home / "leads" / f"{NEW}.inbox.md").read_text() == "reported exec-a /r/a-1.md\n"
    r = run(home, OLD, "--as", NEW)
    assert (r.returncode, r.stdout) == (0, f"already moved to {NEW}; finished what was left\nmoved 0 pending wake line(s)\n")


def test_a_live_lead_is_refused_then_taken_with_force(home):
    lead(home, "live")
    session(home, "exec-a")
    before = tree(home)
    refused(home, run(home, OLD, "--as", NEW), before, "--force")
    r = run(home, OLD, "--as", NEW, "--force")
    assert r.returncode == 0 and f"adopted exec-a from {OLD} (forced)\n" in r.stdout
    ev = [json.loads(l) for l in (home / "ledger.jsonl").read_text().splitlines()]
    moved = [e for e in ev if e["event"] == "lead_moved"]
    assert len(moved) == 1 and moved[0]["forced"] is True and moved[0]["sessions"] == 1


def test_a_live_lead_in_the_same_tab_is_taken_without_force(home):
    lead(home, "live")
    session(home, "exec-a")
    claim = home / "leads" / f"{OLD}.inbox.md.claim-1-2-3"
    claim.write_text("reported exec-a /r/a-1.md\n")
    r = run(home, OLD, "--as", NEW, ITERM_SESSION_ID=f"w5t5p5:{HANDLE.split(':')[1]}")
    assert (r.returncode, r.stderr) == (0, "")
    assert f"adopted exec-a from {OLD} (same tab)\n" in r.stdout and "moved 1 pending wake line(s)\n" in r.stdout
    assert not claim.exists()


def test_a_claim_file_left_behind_is_named(home):
    lead(home, "live")
    claim = home / "leads" / f"{OLD}.inbox.md.claim-1-2-3"
    claim.write_text("reported exec-a /r/a-1.md\nreported exec-b /r/b-1.md\n")
    r = run(home, OLD, "--as", NEW, "--force")
    assert f"kept {claim} — a claim file of {OLD}, 2 line(s): its process may still be delivering it\n" in r.stdout
    assert claim.exists()


@pytest.mark.parametrize("how", ["as", "PI_LEAD_SID", "PI_SESSION_ID", "both-env"])
def test_the_new_id_comes_from_as_then_the_environment(home, how):
    lead(home, "ghost")
    if how == "as":
        r = run(home, OLD, "--as", NEW, PI_LEAD_SID="lead-env", PI_SESSION_ID="lead-pi")
        want = NEW
    elif how == "PI_LEAD_SID":
        r = run(home, OLD, PI_LEAD_SID="lead-env")
        want = "lead-env"
    elif how == "PI_SESSION_ID":
        r = run(home, OLD, PI_SESSION_ID="lead-pi")
        want = "lead-pi"
    else:
        r = run(home, OLD, PI_LEAD_SID="lead-env", PI_SESSION_ID="lead-pi")
        want = "lead-env"
    assert r.returncode == 0 and r.stdout.startswith(f"lead {OLD} → {want} (proj)\n")
    assert (home / "leads" / f"{want}.json").exists() and not (home / "leads" / f"{OLD}.json").exists()


def test_project_flag_names_the_new_records_project(home):
    lead(home, "ghost")
    r = run(home, OLD, "--as", NEW, "--project", "p2")
    assert r.stdout.startswith(f"lead {OLD} → {NEW} (p2)\n") and "--project 'p2'" in r.stdout
    assert json.loads((home / "leads" / f"{NEW}.json").read_text())["project"] == "p2"


def test_a_new_lead_with_its_own_plan(home):
    lead(home, "ghost")
    (home / "plans").mkdir()
    (home / "plans" / f"{OLD}.json").write_text("{}")
    (home / "plans" / f"{NEW}.json").write_text("{}")
    r = run(home, OLD, "--as", NEW)
    assert (f"plan not moved: {NEW} has its own plan {home / 'plans' / (NEW + '.json')}; "
            f"{home / 'plans' / (OLD + '.json')} stays where it is\n") in r.stdout

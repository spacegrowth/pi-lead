"""`pilead auto-close [--lead SID] [--dry-run]`, through bin/pilead over fixture homes written by
tests/test_autoclose.py's helpers (throwaway git repositories in the test's temp directory). The terminal
is tests/test_resume.py's seam: a recording `osascript` stub and a `ps` stub first on PATH, so no real
tab is closed and the real pi never runs."""
import os
import subprocess

from test_autoclose import H, NO_CHANGE, events, heavy_log, meta_of, session, status_of  # noqa: F401
from test_close_variants import tree
from test_resume import PILEAD, terminal  # noqa: F401  (autouse: the osascript / ps stubs)


def pilead(*argv, env=None):
    return subprocess.run([str(PILEAD), *argv], capture_output=True, text=True, env={**os.environ, **(env or {})})


def close_line(sid, reason="landed", dry=False):
    head = "would auto-close" if dry else "auto-closed"
    return f"{head} {sid} ({reason}) — pilead send {sid} <packet> reopens it with its conversation"


def retire_line(tmp_path, sid, reason="landed", dry=False):
    head = "would auto-retire" if dry else "auto-retired"
    wt = meta_of(tmp_path, sid)["worktree"]
    return f"{head} {sid} ({reason}) — pilead spawn {wt} topic <packet> --seed {sid} starts its successor"


def test_close_and_retire_lines(tmp_path):
    session(tmp_path, "s-1-close")
    session(tmp_path, "s-2-retire")
    heavy_log("s-2-retire", meta_of(tmp_path, "s-2-retire")["worktree"])
    r = pilead("auto-close", "--lead", "lead-1")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout.splitlines() == [close_line("s-1-close"), retire_line(tmp_path, "s-2-retire")]
    assert status_of(tmp_path, "s-1-close") == status_of(tmp_path, "s-2-retire") == "closed"
    assert (H(tmp_path) / "sessions" / "s-2-retire" / "successor-seed.md").is_file()


def test_dry_run_lines_and_nothing_written(tmp_path):
    session(tmp_path, "s-1-close", report=NO_CHANGE)
    session(tmp_path, "s-2-retire")
    heavy_log("s-2-retire", meta_of(tmp_path, "s-2-retire")["worktree"])
    before = tree(H(tmp_path))
    r = pilead("auto-close", "--lead", "lead-1", "--dry-run")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout.splitlines() == [close_line("s-1-close", dry=True),
                                     retire_line(tmp_path, "s-2-retire", dry=True)]
    assert tree(H(tmp_path)) == before


def test_the_calling_lead_is_used_without_lead(tmp_path):
    session(tmp_path, "s-mine")
    session(tmp_path, "s-theirs", lead="lead-2")
    r = pilead("auto-close", env={"PI_LEAD_SID": "lead-1"})
    assert r.returncode == 0 and r.stdout.splitlines() == [close_line("s-mine")]
    assert status_of(tmp_path, "s-theirs") == "reported"
    r = pilead("auto-close", env={"PI_LEAD_SID": "lead-1"})
    assert r.stdout == "nothing to park\n"


def test_nothing_to_park(tmp_path):
    session(tmp_path, "s-unseen", seen=None)
    before = tree(H(tmp_path))
    for extra in ([], ["--dry-run"]):
        r = pilead("auto-close", "--lead", "lead-1", *extra)
        assert (r.returncode, r.stdout, r.stderr) == (0, "nothing to park\n", "")
    assert tree(H(tmp_path)) == before


def test_the_off_line(tmp_path):
    session(tmp_path, "s-off")
    (H(tmp_path) / "config.json").write_text('{"auto_close": false}\n')
    before = tree(H(tmp_path))
    r = pilead("auto-close", "--lead", "lead-1")
    assert (r.returncode, r.stdout, r.stderr) == (0, "auto-close is off (config auto_close)\n", "")
    assert tree(H(tmp_path)) == before


def test_no_lead_and_no_caller_exits_2(tmp_path):
    session(tmp_path, "s-x")
    before = tree(H(tmp_path))
    for env in ({}, {"PI_LEAD_SID": "not-a-registered-lead"}):
        r = pilead("auto-close", env=env)
        assert r.returncode == 2 and r.stdout == ""
        assert len(r.stderr.splitlines()) == 1 and "--lead" in r.stderr
    assert tree(H(tmp_path)) == before


def test_an_unusable_lead_id_is_refused(tmp_path):
    r = pilead("auto-close", "--lead", "../x")
    assert r.returncode == 2 and r.stdout == "" and "lead id" in r.stderr


def test_the_trigger_is_manual(tmp_path):
    session(tmp_path, "s-trig")
    pilead("auto-close", "--lead", "lead-1")
    (ev,) = events(tmp_path, "auto_closed")
    assert (ev["session_id"], ev["action"], ev["reason"], ev["trigger"]) == ("s-trig", "close", "landed", "manual")

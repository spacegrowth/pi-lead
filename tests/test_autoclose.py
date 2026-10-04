"""lib/pilead/autoclose.py: `decide` (pure, no file system) and `sweep` over fixture homes this file writes,
with throwaway git repositories made in the test's temp directory by the real git, and report times the
tests set. Parks go through `pilead close`'s and `pilead retire`'s own functions; the terminal is the
seam of tests/test_resume.py (a recording `osascript` stub and a `ps` stub first on PATH), so no real
tab is closed and the real pi never runs. Session logs are small synthetic files."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import terminal  # noqa: F401  (autouse: the osascript / ps stubs)
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import autoclose, ledger, lifecycle, refs, seed  # noqa: E402

REPORT = """Parser rewrite landed; 4 tests, suite green, staged.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: lib/x.py
"""
NO_CHANGE = """Ran the ops check; nothing to change.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: none — no files changed
"""
OLD = 600  # seconds: past the landed grace, short of the 60-minute idle rule


# ── decide ────────────────────────────────────────────────────────────────────────────────────
META = {"sid": "s-1", "lead": "lead-1"}


def args(**over):
    a = dict(report_age=600.0, seen=True, inbox_pending=False, claimed=["lib/x.py"], dirty=set(), landed=True,
             heavy=False, no_change=False, idle_minutes=60)
    a.update(over)
    return a


def test_decide_landed_only_when_claims_landed_is_true():
    assert autoclose.decide(META, "reported", **args()) == ("close", "landed")
    assert autoclose.decide(META, "reported", **args(dirty={"other.py"})) == ("close", "landed")
    assert autoclose.decide(META, "reported", **args(landed=False)) is None
    assert autoclose.decide(META, "reported", **args(landed=None)) is None  # unknown is not clean
    assert autoclose.decide(META, "reported", **args(landed=None, report_age=3600)) == ("close", "idle 60m")


def test_decide_landed_by_a_clean_no_change_report_and_a_clean_worktree():
    assert autoclose.decide(META, "reported", **args(claimed=[], no_change=True)) == ("close", "landed")
    assert autoclose.decide(META, "reported", **args(claimed=[], no_change=True, dirty={"x"})) is None
    assert autoclose.decide(META, "reported", **args(claimed=[], no_change=False)) is None


@pytest.mark.parametrize("status", ["busy", "idle", "stalled", "paused", "dead", "closed", "unknown", None])
def test_decide_not_reported_is_none(status):
    assert autoclose.decide(META, status, **args()) is None


@pytest.mark.parametrize("meta", [{**META, "keep": True}, {**META, "lead": None}, {"sid": "s-1"},
                                  {**META, "lead": ""}, {**META, "superseded_by": "s-1-r2"}])
def test_decide_pinned_no_lead_or_superseded_is_none(meta):
    assert autoclose.decide(meta, "reported", **args()) is None


@pytest.mark.parametrize("over", [{"report_age": None}, {"seen": False}, {"inbox_pending": True}])
def test_decide_unknown_age_unseen_or_pending_inbox_is_none(over):
    assert autoclose.decide(META, "reported", **args()) == ("close", "landed")  # the otherwise-parked case
    assert autoclose.decide(META, "reported", **args(**over)) is None
    if "report_age" not in over:  # idle for days does not change that
        assert autoclose.decide(META, "reported", **args(**over, report_age=10 ** 6, dirty=None)) is None


def test_decide_a_dirty_claimed_path_is_not_landed():
    assert autoclose.decide(META, "reported", **args(dirty={"lib/x.py"}, landed=False)) is None
    assert autoclose.decide(META, "reported", **args(claimed=["lib/x.py", "b.py"], dirty={"b.py"}, landed=False)) \
        is None


def test_decide_unknown_dirty_paths_never_land_and_fall_to_the_idle_rule():
    assert autoclose.decide(META, "reported", **args(dirty=None)) is None
    assert autoclose.decide(META, "reported", **args(dirty=None, claimed=[], no_change=True)) is None
    assert autoclose.decide(META, "reported", **args(dirty=None, report_age=3600)) == ("close", "idle 60m")


def test_decide_under_the_grace_age_nothing_lands():
    assert autoclose.decide(META, "reported", **args(report_age=119.9)) is None
    assert autoclose.decide(META, "reported", **args(report_age=119.9, claimed=[], no_change=True)) is None
    assert autoclose.decide(META, "reported", **args(report_age=120)) == ("close", "landed")


def test_decide_idle_rule_at_the_threshold_and_one_second_below():
    dirty = dict(dirty={"lib/x.py"}, landed=False)
    assert autoclose.decide(META, "reported", **args(**dirty, report_age=3600)) == ("close", "idle 60m")
    assert autoclose.decide(META, "reported", **args(**dirty, report_age=3599)) is None
    assert autoclose.decide(META, "reported", **args(**dirty, report_age=5000, idle_minutes=30)) == \
        ("close", "idle 83m")


def test_decide_idle_minutes_zero_turns_the_idle_rule_off_and_leaves_landed_on():
    assert autoclose.decide(META, "reported", **args(dirty={"lib/x.py"}, landed=False, report_age=10 ** 6,
                                                     idle_minutes=0)) is None
    assert autoclose.decide(META, "reported", **args(idle_minutes=0)) == ("close", "landed")


def test_decide_heavy_retires_with_the_same_reason():
    assert autoclose.decide(META, "reported", **args(heavy=True)) == ("retire", "landed")
    assert autoclose.decide(META, "reported", **args(heavy=True, dirty=None, report_age=3600)) == \
        ("retire", "idle 60m")


# ── claims_landed (pure): claude-relay 0.5.1's rule ──────────────────────────────────────────
FILES = {"lib/pilead/verbs/resume.py", "lib/x.py", "a/util.py", "b/util.py", "README.md"}
T = 1_790_000_000  # a report's mtime; HEAD at T is "at least as new"


def test_resolve_claims_by_itself_or_by_one_unique_suffix():
    assert autoclose.resolve_claims(["lib/x.py"], FILES) == ["lib/x.py"]
    assert autoclose.resolve_claims(["resume.py"], FILES) == ["lib/pilead/verbs/resume.py"]
    assert autoclose.resolve_claims(["verbs/resume.py", "README.md"], FILES) == \
        ["lib/pilead/verbs/resume.py", "README.md"]
    assert autoclose.resolve_claims(["ilead/verbs/resume.py"], FILES) is None  # suffixes are whole segments


@pytest.mark.parametrize("claims", [["nothere.py"], ["util.py"], ["resume.py", "nothere.py"], ["lib/x.py", ""]])
def test_resolve_claims_is_unknown_when_any_claim_resolves_to_nothing_or_to_several(claims):
    assert autoclose.resolve_claims(claims, FILES) is None


def test_claims_landed_true_false_and_unknown():
    assert autoclose.claims_landed(["resume.py"], FILES, set(), T + 5, T + 0.9) is True
    assert autoclose.claims_landed(["resume.py"], FILES, set(), T, T + 0.9) is True  # whole seconds, relay's >=
    assert autoclose.claims_landed(["resume.py"], FILES, {"lib/pilead/verbs/resume.py"}, T + 5, T) is False
    assert autoclose.claims_landed(["resume.py"], FILES, {"README.md"}, T + 5, T) is True


@pytest.mark.parametrize("over", [
    dict(claimed=[]), dict(claimed=["nothere.py"]), dict(claimed=["util.py"]), dict(files=None),
    dict(dirty=None), dict(head=None), dict(head=T - 1), dict(report_mtime=None),
    dict(head=T - 1, dirty={"lib/pilead/verbs/resume.py"}),  # HEAD older than the report: unknown, not False
])
def test_claims_landed_is_unknown_when_it_cannot_be_proven(over):
    a = dict(claimed=["resume.py"], files=FILES, dirty=set(), head=T + 5, report_mtime=T)
    a.update(over)
    assert autoclose.claims_landed(a["claimed"], a["files"], a["dirty"], a["head"], a["report_mtime"]) is None


# ── sweep fixtures ────────────────────────────────────────────────────────────────────────────
def H(tmp_path):
    return tmp_path / "pi-lead-home"


def git(wt, *argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run([refs.git_path(), "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *argv],
                          check=True, capture_output=True, text=True, env=env)


def repo(tmp_path, name):
    """A git repository with lib/x.py committed: clean."""
    wt = (tmp_path / "repos" / name)
    (wt / "lib").mkdir(parents=True)
    wt = wt.resolve()
    git(wt, "init", "-q")
    (wt / "lib" / "x.py").write_text("x = 1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "init")
    return wt


def set_age(path, secs):
    t = time.time() - secs
    os.utime(path, (t, t))


def mark_seen(tmp_path, sid, n=1, how="report_verify"):
    hm = H(tmp_path)
    if how == "report_verify":
        ledger.append(hm, "report_verify", session_id=sid, packet=n, verdict="COUNTS-MATCH", rerun=False, mismatches=0)
    elif how == "report_reviewed":
        ledger.append(hm, "report_reviewed", session_id=sid, packet=n, findings="inline")
    elif how == "usage_snapshot":
        ledger.append(hm, "usage_snapshot", session_id=sid, packet=n, at="reported", usage=None)


def session(tmp_path, sid, wt=None, *, lead="lead-1", status="reported", report=REPORT, age=OLD, seen="report_verify",
            packets=1, **extra):
    """sessions/<sid>: meta.json, status, packet(s), the current packet's report `age` seconds old (none when
    `report` is None), and a `seen` ledger event for the current packet (none when `seen` is None)."""
    hm = H(tmp_path)
    (hm / "leads").mkdir(parents=True, exist_ok=True)
    for ld in ("lead-1", "lead-2"):
        (hm / "leads" / f"{ld}.json").write_text(json.dumps({"sid": ld, "project": "proj"}) + "\n")
    if wt is None:
        wt = repo(tmp_path, sid)
    d = hm / "sessions" / sid
    d.mkdir(parents=True)
    meta = {"sid": sid, "lead": lead, "worktree": str(wt), "topic": "topic", "model": "anthropic/claude-sonnet-5",
            "status": status, "created": "2026-09-28T10:00:00Z", "packets": packets, "label": f"[Exec] {sid}",
            "layout": "tab", "backend": "iterm", **extra}
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (d / "status").write_text(status + "\n")
    for n in range(1, packets + 1):
        (d / f"packet-{n:04d}.md").write_text(f"GOAL: packet {n}\n")
    if report is not None:
        rp = d / f"report-{packets:04d}.md"
        rp.write_text(report)
        set_age(rp, age)
    if seen:
        mark_seen(tmp_path, sid, packets, seen)
    return d


def meta_of(tmp_path, sid):
    return json.loads((H(tmp_path) / "sessions" / sid / "meta.json").read_text())


def status_of(tmp_path, sid):
    return (H(tmp_path) / "sessions" / sid / "status").read_text().strip()


def events(tmp_path, name=None):
    return [r for r in ledger.read(H(tmp_path)) if name is None or r["event"] == name]


def heavy_log(sid, wt):
    write_log(sid, wt, [assistant(u(200_000, 10))])  # live context 200k: past the 150k heavy line


# ── sweep ─────────────────────────────────────────────────────────────────────────────────────
def test_a_reported_seen_landed_session_is_closed(tmp_path):
    session(tmp_path, "s-landed")
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-landed", "close", "landed")]
    assert status_of(tmp_path, "s-landed") == "closed"
    m = meta_of(tmp_path, "s-landed")
    assert m["auto_closed"] == "landed" and "superseded_by" not in m
    (ev,) = events(tmp_path, "auto_closed")
    assert {k: v for k, v in ev.items() if k != "ts"} == {
        "event": "auto_closed", "session_id": "s-landed", "action": "close", "reason": "landed", "trigger": "manual"}
    (closed,) = events(tmp_path, "closed")
    assert closed["reason"] == "auto-close (landed)"
    assert events(tmp_path, "auto_close_error") == []


def test_close_output_is_captured_not_printed(tmp_path, capfd):
    session(tmp_path, "s-quiet")
    capfd.readouterr()
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-quiet", "close", "landed")]
    out = capfd.readouterr()
    assert (out.out, out.err) == ("", "")


def test_a_clean_no_change_report_in_a_clean_worktree_lands(tmp_path):
    session(tmp_path, "s-ops", report=NO_CHANGE)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-ops", "close", "landed")]


@pytest.mark.parametrize("how", ["report_verify", "report_reviewed", "usage_snapshot"])
def test_each_seen_event_is_enough_on_its_own(tmp_path, how):
    session(tmp_path, "s-seen", seen=how)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-seen", "close", "landed")]


def test_with_no_seen_event_nothing_is_parked(tmp_path):
    session(tmp_path, "s-unseen", seen=None, age=10 ** 6)  # landed AND idle for days: still never parked
    hm = H(tmp_path)
    ledger.append(hm, "usage_snapshot", session_id="s-unseen", packet=1, at="sent", usage=None)  # not a look
    ledger.append(hm, "report_verify", session_id="s-unseen", packet=2, verdict="x")  # another packet's
    ledger.append(hm, "report_verify", session_id="s-other", packet=1, verdict="x")  # another session's
    before = tree(hm)
    assert autoclose.sweep(hm, "manual") == []
    assert tree(hm) == before


def test_a_heavy_session_is_retired_and_its_seed_exists(tmp_path):
    d = session(tmp_path, "s-heavy")
    heavy_log("s-heavy", meta_of(tmp_path, "s-heavy")["worktree"])
    assert autoclose.sweep(H(tmp_path), "list") == [("s-heavy", "retire", "landed")]
    assert (d / "successor-seed.md").is_file()
    assert seed.is_retired(meta_of(tmp_path, "s-heavy")) and meta_of(tmp_path, "s-heavy")["auto_closed"] == "landed"
    assert status_of(tmp_path, "s-heavy") == "closed"
    assert [e["event"] for e in events(tmp_path) if e["event"] in ("superseded", "retired", "auto_closed")] == \
        ["superseded", "retired", "auto_closed"]
    assert events(tmp_path, "auto_closed")[0]["action"] == "retire"
    assert not (d / "usage.json").exists()  # the sweep reads usage without writing its cache


@pytest.mark.parametrize("st", ["busy", "idle", "stalled", "paused"])
def test_a_session_working_a_packet_is_never_parked(tmp_path, st):
    # packet 2 is current and has no report; packet 1's report is old, seen and landed
    d = session(tmp_path, "s-work", status=st, packets=2, report=None, seen=None)
    (d / "report-0001.md").write_text(REPORT)
    set_age(d / "report-0001.md", 10 ** 6)
    mark_seen(tmp_path, "s-work", 1)
    mark_seen(tmp_path, "s-work", 2)
    before = tree(H(tmp_path))
    assert autoclose.sweep(H(tmp_path), "manual") == []
    assert tree(H(tmp_path)) == before


@pytest.mark.parametrize("st", ["dead", "closed"])
def test_a_dead_or_closed_session_is_never_parked(tmp_path, st):
    session(tmp_path, "s-ended", status=st, age=10 ** 6)
    before = tree(H(tmp_path))
    assert autoclose.sweep(H(tmp_path), "manual") == []
    assert tree(H(tmp_path)) == before


@pytest.mark.parametrize("what", ["pinned", "inbox", "no-lead", "superseded"])
def test_pinned_pending_inbox_no_lead_or_superseded_is_never_parked(tmp_path, what):
    extra = {"keep": True} if what == "pinned" else {"superseded_by": "x-r2"} if what == "superseded" else {}
    d = session(tmp_path, "s-stay", lead=None if what == "no-lead" else "lead-1", age=10 ** 6, **extra)
    if what == "inbox":
        (d / "inbox.md").write_text("GOAL: the next packet\n")
    before = tree(H(tmp_path))
    assert autoclose.sweep(H(tmp_path), "manual") == []
    assert tree(H(tmp_path)) == before


def test_an_empty_inbox_does_not_keep_a_session(tmp_path):
    d = session(tmp_path, "s-inbox")
    (d / "inbox.md").write_text("\n  \n")
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-inbox", "close", "landed")]


def test_lead_limits_the_sweep_to_that_leads_executors(tmp_path):
    session(tmp_path, "s-mine")
    session(tmp_path, "s-theirs", lead="lead-2")
    theirs = tree(H(tmp_path) / "sessions" / "s-theirs")
    assert autoclose.sweep(H(tmp_path), "manual", lead="lead-1") == [("s-mine", "close", "landed")]
    assert tree(H(tmp_path) / "sessions" / "s-theirs") == theirs
    assert autoclose.sweep(H(tmp_path), "manual", sids=["s-theirs"], lead="lead-1") == []
    assert tree(H(tmp_path) / "sessions" / "s-theirs") == theirs


def test_sids_limit_the_sweep(tmp_path):
    session(tmp_path, "s-a")
    session(tmp_path, "s-b")
    assert autoclose.sweep(H(tmp_path), "check", sids=["s-b"]) == [("s-b", "close", "landed")]
    assert status_of(tmp_path, "s-a") == "reported"


def test_a_claimed_path_still_dirty_is_not_landed(tmp_path):
    session(tmp_path, "s-dirty")
    wt = Path(meta_of(tmp_path, "s-dirty")["worktree"])
    (wt / "lib" / "x.py").write_text("x = 2\n")
    git(wt, "add", "lib/x.py")
    assert autoclose.sweep(H(tmp_path), "manual") == []
    set_age(H(tmp_path) / "sessions" / "s-dirty" / "report-0001.md", 3700)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-dirty", "close", "idle 61m")]


def test_an_untracked_claimed_path_is_not_landed(tmp_path):
    session(tmp_path, "s-new", report=REPORT.replace("lib/x.py", "lib/new.py"))
    (Path(meta_of(tmp_path, "s-new")["worktree"]) / "lib" / "new.py").write_text("y\n")
    assert autoclose.sweep(H(tmp_path), "manual") == []


def test_a_worktree_that_is_not_a_git_repository_falls_to_the_idle_rule(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    session(tmp_path, "s-plain", wt=plain.resolve())
    session(tmp_path, "s-plain-ops", wt=plain.resolve(), report=NO_CHANGE)
    assert autoclose.dirty_paths(plain) is None
    assert autoclose.sweep(H(tmp_path), "manual") == []
    set_age(H(tmp_path) / "sessions" / "s-plain" / "report-0001.md", 3600)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-plain", "close", "idle 60m")]


def test_a_missing_worktree_is_unknown_not_clean(tmp_path):
    session(tmp_path, "s-gone", wt=tmp_path / "no-such-dir", report=NO_CHANGE)
    assert autoclose.dirty_paths(tmp_path / "no-such-dir") is None
    assert autoclose.sweep(H(tmp_path), "manual") == []


def test_dirty_paths_are_read_with_the_real_git_even_when_path_has_a_failing_git(tmp_path, monkeypatch):
    session(tmp_path, "s-path")
    bad = tmp_path / "badgit"
    bad.mkdir()
    (bad / "git").write_text(f"#!/bin/sh\necho called >> {tmp_path / 'badgit.log'}\nexit 1\n")
    (bad / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(bad) + os.pathsep + os.environ["PATH"])
    assert autoclose.dirty_paths(meta_of(tmp_path, "s-path")["worktree"]) == set()
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-path", "close", "landed")]
    # the failing git was at most asked `--version` (git_path's own probe), never `status`
    log = tmp_path / "badgit.log"
    assert not log.exists() or "status" not in log.read_text()


def test_dirty_paths_lists_staged_modified_untracked_and_both_sides_of_a_rename(tmp_path):
    wt = repo(tmp_path, "r")
    (wt / "lib" / "y.py").write_text("y\n")
    git(wt, "add", "lib/y.py")
    git(wt, "commit", "-q", "-m", "y")
    (wt / "lib" / "x.py").write_text("changed\n")
    (wt / "new file.txt").write_text("n\n")
    git(wt, "mv", "lib/y.py", "lib/z.py")
    (wt / "staged.py").write_text("s\n")
    git(wt, "add", "staged.py")
    assert autoclose.dirty_paths(wt) == {"lib/x.py", "new file.txt", "lib/y.py", "lib/z.py", "staged.py"}


def test_an_error_in_one_session_is_ledgered_and_the_next_is_still_parked(tmp_path, monkeypatch):
    session(tmp_path, "s-a-fails")
    session(tmp_path, "s-b-parks")
    real = lifecycle.close_executor

    def close(home, sid, meta, **kw):
        if sid == "s-a-fails":
            raise RuntimeError("boom " + "x" * 400)
        return real(home, sid, meta, **kw)

    monkeypatch.setattr(lifecycle, "close_executor", close)
    before = tree(H(tmp_path) / "sessions" / "s-a-fails")
    assert autoclose.sweep(H(tmp_path), "list") == [("s-b-parks", "close", "landed")]
    assert tree(H(tmp_path) / "sessions" / "s-a-fails") == before
    (err,) = events(tmp_path, "auto_close_error")
    assert err["session_id"] == "s-a-fails" and err["trigger"] == "list"
    assert err["error"].startswith("boom xxx") and len(err["error"]) == 200
    assert status_of(tmp_path, "s-b-parks") == "closed"


def test_an_unreadable_meta_between_listing_and_handling_is_an_error_and_the_sweep_goes_on(tmp_path):
    d = session(tmp_path, "s-a-broken")
    session(tmp_path, "s-b-fine")
    (d / "meta.json").write_text("{not json")
    before = tree(d)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-b-fine", "close", "landed")]
    assert tree(d) == before
    (err,) = events(tmp_path, "auto_close_error")
    assert err["session_id"] == "s-a-broken" and err["trigger"] == "manual" and err["error"]


def test_an_unreadable_meta_is_left_out_of_a_lead_scoped_sweep(tmp_path):
    d = session(tmp_path, "s-a-broken")
    (d / "meta.json").write_text("{not json")
    before = tree(H(tmp_path))
    assert autoclose.sweep(H(tmp_path), "list", lead="lead-1") == []
    assert tree(H(tmp_path)) == before


def test_dry_run_writes_nothing_and_names_what_would_be_parked(tmp_path):
    session(tmp_path, "s-1-landed")
    session(tmp_path, "s-2-heavy")
    heavy_log("s-2-heavy", meta_of(tmp_path, "s-2-heavy")["worktree"])
    session(tmp_path, "s-3-idle", report=REPORT.replace("lib/x.py", "lib/other.py"), age=4000)
    (Path(meta_of(tmp_path, "s-3-idle")["worktree"]) / "lib" / "other.py").write_text("o\n")
    session(tmp_path, "s-4-unseen", seen=None)
    # a session whose stored status is stale: its report exists, so refresh would store `reported`
    session(tmp_path, "s-5-stale", status="busy")
    hm = H(tmp_path)
    repos = tree(tmp_path / "repos")
    before = tree(hm)
    got = autoclose.sweep(hm, "manual", dry_run=True)
    assert got == [("s-1-landed", "close", "landed"), ("s-2-heavy", "retire", "landed"),
                   ("s-3-idle", "close", "idle 66m"), ("s-5-stale", "close", "landed")]
    assert tree(hm) == before  # every file's bytes and modification time
    assert tree(tmp_path / "repos") == repos  # git status wrote nothing there either (no index refresh)
    assert autoclose.sweep(hm, "manual") == got  # and a real sweep does what the dry run said


def test_after_a_real_sweep_every_session_not_parked_is_byte_identical(tmp_path):
    session(tmp_path, "s-park-1")
    session(tmp_path, "s-park-2", report=NO_CHANGE)
    stay = {
        "s-busy": dict(status="busy", packets=2, report=None),
        "s-pinned": dict(keep=True),
        "s-unseen": dict(seen=None),
        "s-young": dict(age=30),
        "s-other-lead": dict(lead="lead-2"),
        "s-no-lead": dict(lead=None),
        "s-closed": dict(status="closed"),
    }
    for sid, kw in stay.items():
        session(tmp_path, sid, **kw)
    session(tmp_path, "s-dirty")
    (Path(meta_of(tmp_path, "s-dirty")["worktree"]) / "lib" / "x.py").write_text("dirty\n")
    stay["s-dirty"] = {}
    heavy_log("s-pinned", meta_of(tmp_path, "s-pinned")["worktree"])  # a log: its usage cache is not written
    before = {sid: tree(H(tmp_path) / "sessions" / sid) for sid in stay}
    acted = autoclose.sweep(H(tmp_path), "manual", lead="lead-1")
    assert sorted(a[0] for a in acted) == ["s-park-1", "s-park-2"]
    for sid in stay:
        assert tree(H(tmp_path) / "sessions" / sid) == before[sid], sid


def test_config_auto_close_false_parks_nothing(tmp_path):
    session(tmp_path, "s-off", age=10 ** 6)
    hm = H(tmp_path)
    (hm / "config.json").write_text('{"auto_close": false}\n')
    before = tree(hm)
    assert autoclose.sweep(hm, "manual") == []
    assert tree(hm) == before


def test_config_idle_minutes_is_read_and_zero_leaves_landed_on(tmp_path):
    session(tmp_path, "s-idle", report=REPORT.replace("lib/x.py", "lib/other.py"), age=700)
    (Path(meta_of(tmp_path, "s-idle")["worktree"]) / "lib" / "other.py").write_text("o\n")
    session(tmp_path, "s-landed", age=10 ** 5)
    hm = H(tmp_path)
    (hm / "config.json").write_text('{"auto_close_idle_minutes": 0}\n')
    assert autoclose.sweep(hm, "manual") == [("s-landed", "close", "landed")]
    (hm / "config.json").write_text('{"auto_close_idle_minutes": 10}\n')
    assert autoclose.sweep(hm, "manual") == [("s-idle", "close", "idle 11m")]


def test_a_negative_idle_minutes_is_ignored_and_the_default_applies(tmp_path):
    session(tmp_path, "s-idle", report=REPORT.replace("lib/x.py", "lib/other.py"), age=3000)
    (Path(meta_of(tmp_path, "s-idle")["worktree"]) / "lib" / "other.py").write_text("o\n")
    (H(tmp_path) / "config.json").write_text('{"auto_close_idle_minutes": -5}\n')
    assert autoclose.sweep(H(tmp_path), "manual") == []  # 50 minutes < the default 60
    set_age(H(tmp_path) / "sessions" / "s-idle" / "report-0001.md", 3600)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-idle", "close", "idle 60m")]


def test_the_ledger_is_read_once_per_sweep(tmp_path, monkeypatch):
    for sid in ("s-1", "s-2", "s-3"):
        session(tmp_path, sid, seen=None)
    calls = []
    real = ledger.read
    monkeypatch.setattr(ledger, "read", lambda *a, **k: calls.append(a) or real(*a, **k))
    autoclose.sweep(H(tmp_path), "manual")
    assert len(calls) == 1


def test_a_park_deletes_no_file(tmp_path):
    d = session(tmp_path, "s-keepfiles")
    heavy_log("s-keepfiles", meta_of(tmp_path, "s-keepfiles")["worktree"])
    names = set(tree(H(tmp_path)))
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-keepfiles", "retire", "landed")]
    assert names <= set(tree(H(tmp_path)))
    assert (d / "report-0001.md").read_text() == REPORT


def test_sweep_never_raises(tmp_path, monkeypatch):
    session(tmp_path, "s-x")
    monkeypatch.setattr(autoclose.config, "load", lambda home: 1 / 0)
    assert autoclose.sweep(H(tmp_path), "manual") == []
    assert autoclose.sweep(tmp_path / "nowhere", "manual") == []


# ── the landed rule against real repositories (claude-relay 0.5.1, row 93) ──────────────────────
def repo_with(tmp_path, name, files):
    """A git repository with `files` ({path: text}) committed: clean."""
    wt = (tmp_path / "repos" / name)
    wt.mkdir(parents=True)
    wt = wt.resolve()
    git(wt, "init", "-q")
    for rel, text in files.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "init")
    return wt


def commit_at(wt, epoch, msg="work"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_COMMITTER_DATE=f"@{int(epoch)} +0000", GIT_AUTHOR_DATE=f"@{int(epoch)} +0000")
    subprocess.run([refs.git_path(), "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-q", "-am", msg],
                   check=True, capture_output=True, text=True, env=env)


def landed_of(tmp_path, sid):
    """claims_landed for the session's current report, from what the sweep itself reads."""
    from pilead.review import report_verify
    wt = meta_of(tmp_path, sid)["worktree"]
    rp = H(tmp_path) / "sessions" / sid / "report-0001.md"
    claimed = report_verify.claimed_paths(rp.read_text())[0]
    return autoclose.claims_landed(claimed, autoclose.repo_files(wt), autoclose.dirty_paths(wt),
                                   autoclose.head_time(wt), rp.stat().st_mtime)


RESUME = REPORT.replace("Changed: lib/x.py", "Changed: resume.py")


def test_a_bare_basename_claim_whose_full_path_is_dirty_is_not_parked_until_it_is_committed(tmp_path):
    wt = repo_with(tmp_path, "s-bare", {"lib/pilead/verbs/resume.py": "r = 1\n", "lib/x.py": "x = 1\n"})
    session(tmp_path, "s-bare", wt=wt, report=RESUME)
    (wt / "lib/pilead/verbs/resume.py").write_text("r = 2\n")
    git(wt, "add", "-A")  # staged: the lead has not reviewed or committed it
    assert autoclose.dirty_paths(wt) == {"lib/pilead/verbs/resume.py"}  # the old rule read no overlap as landed
    assert landed_of(tmp_path, "s-bare") is False
    before = tree(H(tmp_path))
    assert autoclose.sweep(H(tmp_path), "list", lead="lead-1") == []
    assert tree(H(tmp_path)) == before and status_of(tmp_path, "s-bare") == "reported"
    commit_at(wt, time.time())  # the lead commits: newer than the report
    assert landed_of(tmp_path, "s-bare") is True
    assert autoclose.sweep(H(tmp_path), "list", lead="lead-1") == [("s-bare", "close", "landed")]
    assert status_of(tmp_path, "s-bare") == "closed"


def test_an_unresolvable_claim_is_unknown_and_not_parked(tmp_path):
    session(tmp_path, "s-nowhere", report=REPORT.replace("Changed: lib/x.py", "Changed: lib/gone.py"))
    assert landed_of(tmp_path, "s-nowhere") is None  # unknown, not "no overlap = clean"
    assert autoclose.sweep(H(tmp_path), "manual", dry_run=True) == []
    assert autoclose.sweep(H(tmp_path), "manual") == []
    set_age(H(tmp_path) / "sessions" / "s-nowhere" / "report-0001.md", 3600)
    assert autoclose.sweep(H(tmp_path), "manual") == [("s-nowhere", "close", "idle 60m")]  # only the timer


def test_an_ambiguous_suffix_is_unknown_and_not_parked(tmp_path):
    wt = repo_with(tmp_path, "s-twice", {"a/util.py": "a\n", "b/util.py": "b\n"})
    session(tmp_path, "s-twice", wt=wt, report=REPORT.replace("Changed: lib/x.py", "Changed: util.py"))
    assert autoclose.dirty_paths(wt) == set()
    assert landed_of(tmp_path, "s-twice") is None
    assert autoclose.sweep(H(tmp_path), "manual") == []


def test_a_head_older_than_the_report_is_unknown_even_with_the_claim_clean(tmp_path):
    wt = repo_with(tmp_path, "s-stale", {"lib/x.py": "x = 1\n"})
    (wt / "lib/x.py").write_text("x = 0\n")
    commit_at(wt, time.time() - 2 * OLD)  # the last commit predates the report (OLD seconds old)
    session(tmp_path, "s-stale", wt=wt)
    assert autoclose.dirty_paths(wt) == set()
    assert autoclose.head_time(wt) < (H(tmp_path) / "sessions" / "s-stale" / "report-0001.md").stat().st_mtime
    assert landed_of(tmp_path, "s-stale") is None
    assert autoclose.sweep(H(tmp_path), "manual") == []
    assert status_of(tmp_path, "s-stale") == "reported"


def test_repo_files_and_head_time_are_unknown_outside_a_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    for wt in (plain, tmp_path / "no-such-dir", None):
        assert autoclose.repo_files(wt) is None and autoclose.head_time(wt) is None
    wt = repo_with(tmp_path, "r", {"lib/x.py": "x\n", "new file.txt": "n\n"})
    (wt / "staged.py").write_text("s\n")
    git(wt, "add", "staged.py")
    (wt / "untracked.py").write_text("u\n")
    assert autoclose.repo_files(wt) == {"lib/x.py", "new file.txt", "staged.py"}
    assert abs(autoclose.head_time(wt) - time.time()) < 60


# ── the queue (lib/pilead/queue.py) and the sweep ────────────────────────────────────────────
from pilead import queue as queue_mod  # noqa: E402


def test_decide_an_unreadable_queue_is_never_parked():
    assert autoclose.decide(META, "reported", **args(), queued=None) is None
    assert autoclose.decide(META, "reported", **args(report_age=10 ** 6), queued=None, queue_stuck_heavy=True) is None


def test_decide_a_waiting_queue_keeps_the_session_open():
    assert autoclose.decide(META, "reported", **args(), queued=2) is None
    assert autoclose.decide(META, "reported", **args(report_age=10 ** 6, heavy=True), queued=1) is None


def test_decide_a_queue_stuck_on_heaviness_leaves_it_to_the_other_rules():
    assert autoclose.decide(META, "reported", **args(heavy=True), queued=2, queue_stuck_heavy=True) == \
        ("retire", "landed")
    assert autoclose.decide(META, "reported", **args(landed=None, report_age=3600), queued=1,
                            queue_stuck_heavy=True) == ("close", "idle 60m")
    assert autoclose.decide(META, "reported", **args(landed=None), queued=1, queue_stuck_heavy=True) is None
    assert autoclose.decide({**META, "keep": True}, "reported", **args(), queued=1, queue_stuck_heavy=True) is None


def test_decide_without_the_queue_inputs_is_what_it_was():
    cases = [args(), args(heavy=True), args(landed=None), args(landed=None, report_age=3600), args(dirty=None),
             args(claimed=[], no_change=True), args(seen=False), args(inbox_pending=True), args(report_age=None),
             args(idle_minutes=0, landed=False, report_age=10 ** 6), args(report_age=119.9)]
    for status in ("reported", "busy", "closed"):
        for meta in (META, {**META, "keep": True}, {"sid": "s-1"}):
            for a in cases:
                assert autoclose.decide(meta, status, **a) == \
                    autoclose.decide(meta, status, **a, queued=0, queue_stuck_heavy=False)
    assert autoclose.decide(META, "reported", **args(), queued=0, queue_stuck_heavy=True) == ("close", "landed")


def queued_items(tmp_path, sid, n=2, stuck=False):
    hm = H(tmp_path)
    for i in range(n):
        p = tmp_path / f"{sid}-q{i + 1}.md"
        p.write_text(f"GOAL: queued {i + 1}\n")
        queue_mod.enqueue(hm, sid, p.read_text(), p)
    if stuck:
        qp = queue_mod.path(hm, sid)
        data = json.loads(qp.read_text())
        data["items"][0].update(last_error="session is heavy: 200k", error_kind="heavy")
        qp.write_text(json.dumps(data, indent=2) + "\n")
    return queue_mod.read(hm, sid)[0]


def test_a_session_with_a_waiting_queue_is_not_parked(tmp_path):
    session(tmp_path, "s-queued")
    queued_items(tmp_path, "s-queued", 1)
    hm = H(tmp_path)
    before = tree(hm)
    assert autoclose.sweep(hm, "manual") == []
    assert tree(hm) == before


def test_a_session_with_an_unreadable_queue_is_not_parked(tmp_path):
    d = session(tmp_path, "s-unreadable", age=10 ** 6)
    (d / "queue.json").write_text("{not json")
    hm = H(tmp_path)
    before = tree(hm)
    assert autoclose.sweep(hm, "manual") == []
    assert autoclose.sweep(hm, "manual", dry_run=True) == []
    assert tree(hm) == before


def test_a_heavy_session_whose_queue_is_stuck_on_heaviness_is_retired_and_its_queue_emptied(tmp_path):
    d = session(tmp_path, "s-stuck")
    heavy_log("s-stuck", meta_of(tmp_path, "s-stuck")["worktree"])
    items = queued_items(tmp_path, "s-stuck", 2, stuck=True)
    hm = H(tmp_path)
    before = tree(hm)
    dry = autoclose.sweep(hm, "manual", dry_run=True)
    assert dry == [("s-stuck", "retire", "landed")]
    assert tree(hm) == before
    bodies = ", ".join(it["body_path"] for it in items)
    dry_lines = autoclose.lines(hm, dry, dry_run=True)
    assert dry_lines[0].startswith("would auto-retire s-stuck (landed)")
    assert dry_lines[1] == f"  would cancel 2 queued packet(s) that were not delivered — bodies: {bodies}"

    got = autoclose.sweep(hm, "manual")
    assert got == [("s-stuck", "retire", "landed")]
    assert status_of(tmp_path, "s-stuck") == "closed" and (d / "successor-seed.md").is_file()
    data = json.loads((d / "queue.json").read_text())
    assert data == {"next_id": 3, "items": []}
    for it in items:
        assert Path(it["body_path"]).read_text() == f"GOAL: queued {it['id']}\n"
    evs = events(tmp_path, "queue_cancelled_on_park")
    assert [{k: v for k, v in e.items() if k != "ts"} for e in evs] == [
        {"event": "queue_cancelled_on_park", "session_id": "s-stuck", "queue_id": it["id"], "source": it["source"],
         "body_path": it["body_path"], "action": "retire", "reason": "landed", "trigger": "manual"} for it in items]
    out = autoclose.lines(hm, got)
    assert out[0].startswith("auto-retired s-stuck (landed)")
    assert out[1] == f"  2 queued packet(s) were not delivered and are no longer queued — bodies: {bodies}"
    assert len(out) == 2


def test_the_cancel_line_follows_its_park_line_through_after_command(tmp_path, monkeypatch, capsys):
    session(tmp_path, "s-a")
    session(tmp_path, "s-b")
    heavy_log("s-b", meta_of(tmp_path, "s-b")["worktree"])
    items = queued_items(tmp_path, "s-b", 1, stuck=True)
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")
    capsys.readouterr()
    acted = autoclose.after_command(H(tmp_path), "list")
    assert acted == [("s-a", "close", "landed"), ("s-b", "retire", "landed")]
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("auto-closed s-a (landed)") and lines[1].startswith("auto-retired s-b (landed)")
    assert lines[2] == f"  1 queued packet(s) were not delivered and are no longer queued — bodies: {items[0]['body_path']}"
    assert len(lines) == 3

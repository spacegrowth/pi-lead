"""lib/pilead/refs.py — detecting, after the fact, that history or refs moved while an executor worked —
against throwaway repositories made in a temp dir with the real git; and its wiring into spawn / send /
check / verify / `refs accept` / the board, through bin/pilead (fake_pi + the stub osascript: no real tab)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_cli_core import ROOT, env, home, make_reported, pilead, spawn, stub_osascript  # noqa: F401  (fixtures)
from test_verify_diff_board import SID, make_session, report, stage, write_report

sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, refs, review  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "Exec Utor", "GIT_AUTHOR_EMAIL": "exec@x", "GIT_COMMITTER_NAME": "Exec Utor",
           "GIT_COMMITTER_EMAIL": "exec@x", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": os.environ.get("HOME", "/tmp")}


def git(wt, *args):
    return subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """main: seed → second; branch `feature` at seed; one tracked file."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", "seed")
    git(r, "branch", "feature")
    (r / "a.txt").write_text("two\n")
    git(r, "commit", "-q", "-am", "second")
    return r


def kinds(changes):
    return sorted(c["kind"] for c in changes)


def around(repo, action):
    before = refs.snapshot(repo)
    action()
    after = refs.snapshot(repo)
    return before, after, refs.compare(before, after)


# ── snapshot ─────────────────────────────────────────────────────────────────────────────────────
def test_snapshot_shape(repo):
    s = refs.snapshot(repo)
    assert s["head"] == git(repo, "rev-parse", "HEAD") and s["branch"] == "main"
    assert s["refs"] == {"refs/heads/main": s["head"], "refs/heads/feature": git(repo, "rev-parse", "feature")}
    assert s["stash"] is None
    assert [w["path"] for w in s["worktrees"]] == [os.path.realpath(repo)]
    assert s["common_dir"] == os.path.realpath(repo / ".git")
    json.dumps(s)  # stored as JSON


def test_snapshot_of_a_plain_directory_is_not_a_repo(tmp_path):
    with pytest.raises(refs.NotARepo):
        refs.snapshot(tmp_path)
    with pytest.raises(refs.RefsError, match="no worktree recorded"):
        refs.snapshot("")  # never the current directory


# ── every change kind is detected ────────────────────────────────────────────────────────────────
def test_commit(repo):
    def act():
        (repo / "a.txt").write_text("three\n")
        git(repo, "commit", "-q", "-am", "an executor's commit")
    before, after, ch = around(repo, act)
    assert kinds(ch) == ["head_moved", "ref_moved"]
    assert {"kind": "ref_moved", "ref": "refs/heads/main", "from": before["head"], "to": after["head"]} in ch
    commits, total = refs.new_commits(repo, before, after, ch)
    assert total == 1 and commits[0]["subject"] == "an executor's commit"
    assert commits[0]["author"] == "Exec Utor <exec@x>" and commits[0]["sha"] == after["head"]
    assert commits[0]["date"][:4].isdigit()


def test_amend(repo):
    before, after, ch = around(repo, lambda: git(repo, "commit", "-q", "--amend", "-m", "second, amended"))
    assert kinds(ch) == ["head_moved", "ref_moved"]
    commits, total = refs.new_commits(repo, before, after, ch)
    assert total == 1 and commits[0]["subject"] == "second, amended"


def test_new_branch(repo):
    _, after, ch = around(repo, lambda: git(repo, "branch", "sneaky"))
    assert ch == [{"kind": "ref_added", "ref": "refs/heads/sneaky", "to": after["head"]}]


def test_moved_branch(repo):
    before, after, ch = around(repo, lambda: git(repo, "branch", "-f", "feature", "main"))
    assert ch == [{"kind": "ref_moved", "ref": "refs/heads/feature",
                   "from": before["refs"]["refs/heads/feature"], "to": after["head"]}]


def test_deleted_branch(repo):
    before, _, ch = around(repo, lambda: git(repo, "branch", "-D", "feature"))
    assert ch == [{"kind": "ref_removed", "ref": "refs/heads/feature", "from": before["refs"]["refs/heads/feature"]}]


@pytest.mark.parametrize("annotated", [False, True])
def test_tag(repo, annotated):
    args = ["tag", "-a", "v1", "-m", "release"] if annotated else ["tag", "v1"]
    _, after, ch = around(repo, lambda: git(repo, *args))
    assert kinds(ch) == ["ref_added"] and ch[0]["ref"] == "refs/tags/v1"
    assert ch[0]["to"] == git(repo, "rev-parse", "refs/tags/v1")


def test_stash(repo):
    def act():
        (repo / "a.txt").write_text("dirty\n")
        git(repo, "stash", "-q")
    before, after, ch = around(repo, act)
    assert ch == [{"kind": "stash_changed", "from": None, "to": after["stash"]}] and after["stash"]
    assert refs.new_commits(repo, before, after, ch) == ([], 0)  # the stash's WIP commits are not "new commits"


def test_update_ref(repo):
    seed = git(repo, "rev-parse", "main~1")
    before, _, ch = around(repo, lambda: git(repo, "update-ref", "refs/heads/main", seed))
    assert {"kind": "ref_moved", "ref": "refs/heads/main", "from": before["head"], "to": seed} in ch
    assert {"kind": "head_moved", "from": before["head"], "to": seed} in ch


def test_update_ref_of_a_new_namespace(repo):
    _, after, ch = around(repo, lambda: git(repo, "update-ref", "refs/hidden/x", "HEAD"))
    assert ch == [{"kind": "ref_added", "ref": "refs/hidden/x", "to": after["head"]}]


def test_reset_to_an_older_commit(repo):
    before, after, ch = around(repo, lambda: git(repo, "reset", "-q", "--hard", "HEAD~1"))
    assert kinds(ch) == ["head_moved", "ref_moved"] and after["head"] == git(repo, "rev-parse", "feature")
    assert refs.new_commits(repo, before, after, ch) == ([], 0)  # nothing new — history went backwards


def test_new_linked_worktree(repo, tmp_path):
    wt2 = tmp_path / "wt2"
    _, _, ch = around(repo, lambda: git(repo, "worktree", "add", "-q", "--detach", str(wt2)))
    assert ch == [{"kind": "worktree_added", "path": os.path.realpath(wt2)}]


def test_removed_linked_worktree(repo, tmp_path):
    wt2 = tmp_path / "wt2"
    git(repo, "worktree", "add", "-q", "--detach", str(wt2))
    _, _, ch = around(repo, lambda: git(repo, "worktree", "remove", str(wt2)))
    assert ch == [{"kind": "worktree_removed", "path": os.path.realpath(wt2)}]


def test_commit_on_a_detached_head_in_another_linked_worktree(repo, tmp_path):
    """No ref moves at all — only that worktree's HEAD. Still detected."""
    wt2 = tmp_path / "wt2"
    git(repo, "worktree", "add", "-q", "--detach", str(wt2))
    before, after, ch = around(repo, lambda: git(wt2, "commit", "-q", "--allow-empty", "-m", "hidden"))
    assert kinds(ch) == ["head_moved"] and ch[0]["worktree"] == os.path.realpath(wt2)
    commits, _ = refs.new_commits(repo, before, after, ch)
    assert [c["subject"] for c in commits] == ["hidden"]


def test_branch_switch(repo):
    _, _, ch = around(repo, lambda: git(repo, "checkout", "-q", "feature"))
    assert {"kind": "branch_changed", "from": "main", "to": "feature"} in ch
    assert kinds(ch) == ["branch_changed", "head_moved"]


def test_detach(repo):
    _, _, ch = around(repo, lambda: git(repo, "checkout", "-q", "--detach"))
    assert ch == [{"kind": "branch_changed", "from": "main", "to": None}]


# ── the index and the working tree are not refs ──────────────────────────────────────────────────
def test_staging_unstaging_and_editing_are_not_reported(repo):
    def act():
        (repo / "a.txt").write_text("edited\n")
        (repo / "new.txt").write_text("new\n")
        git(repo, "add", "a.txt", "new.txt")
        git(repo, "restore", "--staged", "new.txt")
        (repo / "a.txt").write_text("edited again\n")
        git(repo, "restore", "--staged", "a.txt")  # unstage
        git(repo, "add", "a.txt")                  # restage
    assert around(repo, act)[2] == []


def test_commits_between_accepts_single_revisions(repo):
    commits, total = refs.commits_between(repo, "feature", "main")
    assert total == 1 and [c["subject"] for c in commits] == ["second"]
    assert refs.commits_between(repo, "main", "feature") == ([], 0)


# ── the real git, whatever PATH says ─────────────────────────────────────────────────────────────
def failing_git_dir(tmp_path):
    d = tmp_path / "evil-bin"
    d.mkdir()
    (d / "git").write_text("#!/bin/sh\necho 'I am not git' >&2\nexit 1\n")
    (d / "git").chmod(0o755)
    return d


def test_snapshot_uses_the_real_git_when_path_starts_with_a_failing_git(repo, tmp_path, monkeypatch):
    bad = failing_git_dir(tmp_path)
    monkeypatch.setenv("PATH", f"{bad}{os.pathsep}{os.environ['PATH']}")
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 1  # PATH's git fails
    refs._resolved.clear()
    try:
        assert refs.git_path() != str(bad / "git") and os.path.isabs(refs.git_path())
        s = refs.snapshot(repo)
    finally:
        refs._resolved.clear()
    assert s["head"] == git(repo, "rev-parse", "HEAD")


def test_the_executors_shim_is_never_the_git_used(repo, tmp_path, monkeypatch):
    from pilead import shim
    shim_git = shim.write_git_shim(tmp_path / "sess")
    monkeypatch.setenv("PATH", f"{shim_git.parent}{os.pathsep}{os.environ['PATH']}")
    refs._resolved.clear()
    try:
        assert Path(refs.git_path()).resolve() != shim_git.resolve()
    finally:
        refs._resolved.clear()


def test_cli_check_uses_the_real_git_with_a_failing_git_first_on_path(tmp_path):
    wt, _ = make_session(tmp_path)
    refs.record_baseline(home(tmp_path), SID, 1, wt)
    bad = failing_git_dir(tmp_path)
    r = subprocess.run([str(ROOT / "bin" / "pilead"), "check", SID, "--refs-only"], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bad}{os.pathsep}{os.environ['PATH']}"})
    assert (r.returncode, r.stdout) == (0, "refs: unchanged since packet 0001\n")


# ── spawn / send store a baseline per packet ─────────────────────────────────────────────────────
def test_spawn_and_send_store_a_baseline_per_packet(env):
    git(env / "wt", "init", "-q", "-b", "main")
    git(env / "wt", "commit", "-q", "--allow-empty", "-m", "seed")
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    b1 = json.loads((sdir / "refs-0001.json").read_text())
    assert b1["head"] == git(env / "wt", "rev-parse", "HEAD") and b1["branch"] == "main"
    git(env / "wt", "commit", "-q", "--allow-empty", "-m", "the lead committed packet 1")
    (env / "p2.md").write_text("second\n")
    pilead("close", sid)
    r = pilead("send", sid, str(env / "p2.md"))  # the relaunch path
    assert r.stderr == ""
    b2 = json.loads((sdir / "refs-0002.json").read_text())
    assert b2["head"] == git(env / "wt", "rev-parse", "HEAD") != b1["head"]
    recs = [{k: v for k, v in rec.items() if k not in ("ts", "event")}
            for rec in ledger.read(home(env), event="refs_snapshot")]
    assert recs == [{"session_id": sid, "packet": 1, "head": b1["head"], "branch": "main", "refs": 1},
                    {"session_id": sid, "packet": 2, "head": b2["head"], "branch": "main", "refs": 1}]
    assert pilead("check", sid, "--refs-only").stdout == "refs: unchanged since packet 0002\n"


def test_spawn_in_a_non_git_worktree_stores_nothing_and_says_nothing(env):
    sid = spawn(env)
    assert not list((home(env) / "sessions" / sid).glob("refs-*.json"))
    assert ledger.read(home(env), event="refs_snapshot") == []
    assert ledger.read(home(env), event="refs_snapshot_failed") == []
    assert pilead("check", sid, "--refs-only").stdout == "refs: not a git repository\n"


# ── check / verify / refs accept ─────────────────────────────────────────────────────────────────
def sneaky_commit(wt, msg="sneaky"):
    """A commit that leaves the index alone (commit-tree + update-ref: no porcelain, no `git commit`)."""
    sha = git(wt, "commit-tree", git(wt, "rev-parse", "HEAD^{tree}"), "-p", "HEAD", "-m", msg)
    git(wt, "update-ref", "HEAD", sha)
    return sha


def reported_session(tmp_path):
    wt, sdir = make_session(tmp_path)
    refs.record_baseline(home(tmp_path), SID, 1, wt)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    return wt, sdir


def test_check_unchanged_and_moved(tmp_path):
    wt, _ = reported_session(tmp_path)
    r = pilead("check", SID)
    assert r.stdout.splitlines()[0] == "reported" and r.stdout.splitlines()[-1] == "refs: unchanged since packet 0001"
    sha = sneaky_commit(wt, "made by a python script")
    r = pilead("check", SID)
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[0].startswith("REFS MOVED since packet 0001 of " + SID)
    assert lines.index("reported") > 3  # the block is above the status line
    assert f"{sha[:12]}" in r.stdout and "made by a python script" in r.stdout
    assert "refs: unchanged" not in r.stdout
    (rec,) = ledger.read(home(tmp_path), event="refs_moved")
    assert rec["session_id"] == SID and rec["packet"] == 1 and kinds(rec["changes"]) == ["head_moved", "ref_moved"]
    assert pilead("check", SID, "--refs-only").stdout == "REFS MOVED: 2 change(s) since packet 0001\n"


def test_refs_only_always_exits_0_with_one_line(tmp_path):
    r = pilead("check", "nope", "--refs-only", check=False)
    assert (r.returncode, r.stdout) == (0, "refs: not compared (unknown session nope)\n")


def test_verify_mismatch_on_a_moved_ref_then_accept_clears_it(tmp_path):
    wt, _ = reported_session(tmp_path)
    auto = ("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    r = pilead(*auto, check=False)
    assert r.returncode == 0 and "AUTO-COMMIT: CLEARED" in r.stdout
    assert r.stdout.splitlines()[0] == "refs: unchanged since packet 0001"

    git(wt, "branch", "-f", "side", "HEAD")  # a ref appears
    sneaky_commit(wt)                        # and one moves
    r = pilead("verify", SID, check=False)
    assert r.returncode != 0 and "VERDICT: MISMATCH" in r.stdout and "REFS MOVED" in r.stdout
    assert "refs moved since packet 0001: 3 change(s)" in r.stdout
    r = pilead(*auto, check=False)
    assert r.returncode != 0 and "AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-moved" in r.stdout
    assert ledger.read(home(tmp_path), event="auto_commit")[-1]["reason"] == "refs-moved"
    assert ledger.read(home(tmp_path), event="report_verify")[-1]["verdict"] == "MISMATCH"

    r = pilead("refs", "accept", SID, "--reason", "lead committed packet 0001")
    head = git(wt, "rev-parse", "HEAD")
    assert r.stdout == f"refs accepted for {SID} packet 0001 at {head[:12]}\n"
    (rec,) = ledger.read(home(tmp_path), event="refs_accepted")
    assert {k: v for k, v in rec.items() if k not in ("ts", "event")} == {
        "session_id": SID, "packet": 1, "reason": "lead committed packet 0001", "head": head}
    r = pilead(*auto, check=False)
    assert r.returncode == 0 and "VERDICT: COUNTS-MATCH" in r.stdout and "AUTO-COMMIT: CLEARED" in r.stdout
    assert pilead("verify", SID).returncode == 0


# ── the table: a comparison that could not be made is "not compared", never silence ──────────────
LYING_GIT = ("#!/bin/sh\ncase \"$1\" in --version) echo 'git version 2.99.0 (stub)' ;;\n"
             "*) echo 'fatal: simulated git failure' >&2; exit 128 ;;\nesac\n")
AUTO = ("--for-autocommit", "--in-plan", "--diff-reviewed")


def lying_git_path(tmp_path):
    """PATH with a `git` first that answers `--version` like git (so the resolver takes it) and fails
    every real command — git failing, as opposed to a `git` the resolver skips."""
    d = tmp_path / "lying-bin"
    d.mkdir(exist_ok=True)
    (d / "git").write_text(LYING_GIT)
    (d / "git").chmod(0o755)
    return f"{d}{os.pathsep}{os.environ['PATH']}"


def refs_breaking_git_path(tmp_path):
    """PATH with a `git` first that is the real git for everything except `for-each-ref`, which fails:
    git failing for the refs snapshot only, while `verify`'s own staged-file reads still work."""
    d = tmp_path / "refs-breaking-bin"
    d.mkdir(exist_ok=True)
    (d / "git").write_text("#!/bin/sh\nfor a; do\n  if [ \"$a\" = for-each-ref ]; then\n"
                           "    echo 'fatal: simulated git failure' >&2; exit 128\n  fi\ndone\n"
                           f"exec {refs.git_path()} \"$@\"\n")
    (d / "git").chmod(0o755)
    return f"{d}{os.pathsep}{os.environ['PATH']}"


def cli(*args, path=None):
    env = {**os.environ, **({"PATH": path} if path else {})}
    return subprocess.run([str(ROOT / "bin" / "pilead"), *args], capture_output=True, text=True, env=env)


def outcome(sid=SID, path=None):
    """(refs-only line, check result, verify result, --for-autocommit result) for one session."""
    return (cli("check", sid, "--refs-only", path=path), cli("check", sid, path=path),
            cli("verify", sid, path=path), cli("verify", sid, *AUTO, path=path))


def verdict(r):
    return next(ln.split("VERDICT: ")[1].strip() for ln in r.stdout.splitlines() if "VERDICT: " in ln)


def autocommit_line(r):
    return next(ln.strip() for ln in r.stdout.splitlines() if "AUTO-COMMIT: " in ln and "CLEARANCE" not in ln)


def test_row_unchanged(tmp_path):
    reported_session(tmp_path)
    only, chk, ver, auto = outcome()
    assert (only.returncode, only.stdout) == (0, "refs: unchanged since packet 0001\n")
    assert chk.returncode == 0 and chk.stdout.splitlines()[0] == "reported"
    assert chk.stdout.splitlines()[-1] == "refs: unchanged since packet 0001"
    assert (verdict(ver), ver.returncode) == ("COUNTS-MATCH", 0)
    assert (autocommit_line(auto), auto.returncode) == ("AUTO-COMMIT: CLEARED", 0)


def test_row_moved(tmp_path):
    wt, _ = reported_session(tmp_path)
    sneaky_commit(wt)
    only, chk, ver, auto = outcome()
    assert (only.returncode, only.stdout) == (0, "REFS MOVED: 2 change(s) since packet 0001\n")
    assert chk.returncode == 0 and chk.stdout.startswith("REFS MOVED since packet 0001")
    assert (verdict(ver), ver.returncode) == ("MISMATCH", 1)
    assert autocommit_line(auto) == "AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-moved" and auto.returncode == 1


def _missing(p):
    p.unlink()


def _not_json(p):
    p.write_text("this is not JSON {")


def _directory(p):
    p.unlink()
    p.mkdir()


def _unreadable(p):
    p.chmod(0)


BAD_BASELINES = {"deleted": (_missing, "no baseline stored"), "not-json": (_not_json, "baseline unreadable"),
                 "directory": (_directory, "baseline unreadable"), "unreadable": (_unreadable, "baseline unreadable")}


def assert_not_compared(why, only, chk, ver, auto):
    line = f"refs: not compared ({why})"
    assert (only.returncode, only.stdout) == (0, line + "\n")
    assert chk.returncode == 0 and chk.stdout.splitlines()[0] == line  # the line comes first
    assert "refs: unchanged" not in chk.stdout
    assert ver.stdout.splitlines()[0] == line
    assert (verdict(ver), ver.returncode) == ("INCONCLUSIVE", 3)
    assert f"refs for packet 0001 were not compared: {why}" in ver.stdout
    assert autocommit_line(auto) == "AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-not-compared" and auto.returncode == 3


@pytest.mark.parametrize("kind", list(BAD_BASELINES))
def test_row_baseline_missing_unreadable_or_corrupt(tmp_path, kind):
    if kind == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    reported_session(tmp_path)
    p = refs.baseline_path(home(tmp_path), SID, 1)
    make_bad, why = BAD_BASELINES[kind]
    make_bad(p)
    try:
        assert_not_compared(why, *outcome())
    finally:
        if p.is_file():
            p.chmod(0o644)


def test_row_git_fails_now(tmp_path):
    reported_session(tmp_path)  # a good baseline, taken with the real git
    path = refs_breaking_git_path(tmp_path)
    assert cli("verify", SID).returncode == 0  # sanity: COUNTS-MATCH without the stub
    assert_not_compared("git failed: fatal: simulated git failure", *outcome(path=path))


def test_row_git_failed_at_spawn(env):
    """spawn succeeds, says so on stderr and in the ledger, and every later comparison says why."""
    git(env / "wt", "init", "-q", "-b", "main")
    git(env / "wt", "commit", "-q", "--allow-empty", "-m", "seed")
    assert cli("lead-start", "lead-1", "--project", "proj").returncode == 0
    r = cli("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1",
            path=lying_git_path(env))
    assert r.returncode == 0 and r.stdout.startswith("spawned ")
    sid = r.stdout.split()[1]
    assert r.stderr == ("pilead: refs snapshot for packet 0001 not taken: git failed: fatal: simulated git "
                        "failure\n")
    (rec,) = ledger.read(home(env), event="refs_snapshot_failed")
    assert {k: v for k, v in rec.items() if k not in ("ts", "event")} == {
        "session_id": sid, "packet": 1, "reason": "git failed: fatal: simulated git failure"}
    assert not refs.baseline_path(home(env), sid, 1).exists()
    stage(env / "wt", "src/alpha.py", "src/beta.py")
    write_report(home(env) / "sessions" / sid, report())
    (home(env) / "sessions" / sid / "status").write_text("reported\n")
    assert_not_compared("no baseline stored", *outcome(sid))


def test_send_whose_snapshot_fails_still_sends_and_logs(env):
    git(env / "wt", "init", "-q", "-b", "main")
    git(env / "wt", "commit", "-q", "--allow-empty", "-m", "seed")
    sid = spawn(env)
    (env / "p2.md").write_text("second\n")
    make_reported(env, sid)
    r = cli("send", sid, str(env / "p2.md"), path=lying_git_path(env))
    assert (r.returncode, r.stdout) == (0, f"sent {sid} packet 0002\n")
    assert "packet 0002 not taken: git failed" in r.stderr
    (rec,) = ledger.read(home(env), event="refs_snapshot_failed")
    assert (rec["session_id"], rec["packet"]) == (sid, 2)
    assert cli("check", sid, "--refs-only").stdout == "refs: not compared (no baseline stored)\n"


def test_row_not_a_repository(env):
    sid = spawn(env)  # env/wt is a plain directory
    sdir = home(env) / "sessions" / sid
    write_report(sdir, report())
    (sdir / "status").write_text("reported\n")
    only, chk, ver, auto = outcome(sid)
    assert (only.returncode, only.stdout) == (0, "refs: not a git repository\n")
    assert chk.returncode == 0 and "refs:" not in chk.stdout and "REFS" not in chk.stdout
    assert ver.stdout.splitlines()[0] == "refs: not a git repository"
    # the verdict is the report checker's own (claims over an empty index), untouched by refs
    assert (verdict(ver), ver.returncode) == ("MISMATCH", 1) and "refs-not-compared" not in ver.stdout
    assert "were not compared" not in ver.stdout
    assert autocommit_line(auto) == "AUTO-COMMIT: NOT-CLEARED-BECAUSE-verdict-is-MISMATCH"


@pytest.mark.parametrize("already,report_text,staged,rc", [
    ("MISMATCH", report(), ("src/alpha.py",), 1),        # claims src/beta.py, which is not staged
    ("MALFORMED", "no tldr here\n", ("src/alpha.py",), 2),
    ("INCONCLUSIVE", report(changed="nothing was needed", staged=False), (), 3),
])
def test_not_compared_never_improves_a_verdict(tmp_path, already, report_text, staged, rc):
    wt, sdir = make_session(tmp_path)
    if staged:
        stage(wt, *staged)
    write_report(sdir, report_text)
    assert verdict(cli("verify", SID)) == already  # the verdict without any refs problem
    refs.baseline_path(home(tmp_path), SID, 1).unlink()
    ver = cli("verify", SID)
    assert ver.stdout.splitlines()[0] == "refs: not compared (no baseline stored)"
    assert (verdict(ver), ver.returncode) == (already, rc)
    auto = cli("verify", SID, *AUTO)
    assert autocommit_line(auto) == "AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-not-compared" and auto.returncode == rc


@pytest.mark.parametrize("kind", ["deleted", "not-json", "directory"])
def test_accept_stores_a_baseline_where_none_is_readable(tmp_path, kind):
    reported_session(tmp_path)
    p = refs.baseline_path(home(tmp_path), SID, 1)
    BAD_BASELINES[kind][0](p)
    assert cli("check", SID, "--refs-only").stdout.startswith("refs: not compared (")
    r = cli("refs", "accept", SID, "--reason", "re-baseline")
    assert r.returncode == 0, r.stderr
    only, chk, ver, auto = outcome()
    assert only.stdout == "refs: unchanged since packet 0001\n"
    assert (verdict(ver), ver.returncode) == ("COUNTS-MATCH", 0)
    assert autocommit_line(auto) == "AUTO-COMMIT: CLEARED"


def test_accept_refuses_to_replace_a_non_empty_directory(tmp_path):
    reported_session(tmp_path)
    p = refs.baseline_path(home(tmp_path), SID, 1)
    p.unlink()
    p.mkdir()
    (p / "keep.txt").write_text("x")
    r = cli("refs", "accept", SID)
    assert r.returncode == 1 and r.stderr.startswith(f"pilead: refs baseline of {SID} not stored: ")
    assert (p / "keep.txt").is_file()


# ── one move is logged once ──────────────────────────────────────────────────────────────────────
def test_one_move_is_logged_once_until_it_changes_or_is_accepted(tmp_path):
    wt, _ = reported_session(tmp_path)

    def moved_events():
        return ledger.read(home(tmp_path), event="refs_moved")

    sneaky_commit(wt, "one")
    for args in (("check", SID), ("check", SID, "--refs-only"), ("verify", SID)):
        cli(*args)
    assert len(moved_events()) == 1
    git(wt, "tag", "t1")  # a further change: the list differs
    cli("check", SID)
    cli("check", SID)
    assert len(moved_events()) == 2 and kinds(moved_events()[1]["changes"]) == ["head_moved", "ref_added", "ref_moved"]
    assert cli("refs", "accept", SID).returncode == 0
    cli("check", SID)
    assert len(moved_events()) == 2  # accepted and unchanged: nothing moved
    git(wt, "tag", "-d", "t1")  # moves again after the accept
    cli("check", SID)
    cli("check", SID)
    assert len(moved_events()) == 3 and kinds(moved_events()[2]["changes"]) == ["ref_removed"]


def test_accept_resets_the_dedupe_for_an_identical_move(tmp_path):
    """move A → accept → back to the baseline (a move that happens to equal A reversed is a different
    list) → the SAME list of changes as before the accept is logged again."""
    wt, _ = reported_session(tmp_path)
    git(wt, "tag", "t1")
    cli("check", SID)
    cli("refs", "accept", SID)
    git(wt, "tag", "t2")
    cli("check", SID)
    git(wt, "tag", "-d", "t2")
    cli("refs", "accept", SID)
    git(wt, "tag", "t2")
    cli("check", SID)
    changes = [e["changes"] for e in ledger.read(home(tmp_path), event="refs_moved")]
    assert len(changes) == 3 and changes[1] == changes[2]  # identical move, logged again after the accept


def test_verify_a_malformed_report_stays_malformed_when_refs_moved(tmp_path):
    wt, sdir = reported_session(tmp_path)
    write_report(sdir, "no tldr here\n")
    sneaky_commit(wt)
    r = pilead("verify", SID, check=False)
    assert r.returncode == 2 and "VERDICT: MALFORMED" in r.stdout and "REFS MOVED" in r.stdout


def test_accept_on_a_non_git_worktree_fails_cleanly(env):
    sid = spawn(env)
    r = pilead("refs", "accept", sid, check=False)
    assert r.returncode == 1 and r.stderr.startswith(f"pilead: refs snapshot of {sid} failed:")


def test_the_block_names_other_sessions_in_the_same_repository(tmp_path):
    wt, _ = reported_session(tmp_path)
    other = home(tmp_path) / "sessions" / "other-1"
    other.mkdir(parents=True)
    (other / "meta.json").write_text(json.dumps({"sid": "other-1", "worktree": str(wt), "packets": 1,
                                                 "status": "busy"}))
    elsewhere = make_session(tmp_path, sid="elsewhere-2")[0]
    assert elsewhere != wt
    sneaky_commit(wt)
    r = pilead("check", SID)
    assert "other sessions in this repository (their work moves these refs too): other-1 (busy)" in r.stdout
    assert "elsewhere-2" not in r.stdout


def test_board_flags_moved_refs(tmp_path):
    wt, _ = reported_session(tmp_path)
    make_session(tmp_path, sid="calm-1")
    rows = {e["session_id"]: e for e in review.board_data(home(tmp_path))["executors"]}
    assert rows[SID]["refs_moved"] is False and rows["calm-1"]["refs_moved"] is False
    sneaky_commit(wt)
    data = review.board_data(home(tmp_path))
    rows = {e["session_id"]: e for e in data["executors"]}
    assert rows[SID]["refs_moved"] is True and rows["calm-1"]["refs_moved"] is False
    warnings = [w for w in data["warnings"] if "no longer registered" not in w["text"]]  # p22: not the orphan entry
    assert [w["level"] for w in warnings] == ["bad"] and SID in warnings[0]["text"]
    assert rows[SID]["refs_not_compared"] is False and rows["calm-1"]["refs_not_compared"] is False
    assert ledger.read(home(tmp_path), event="refs_moved") == []  # rendering the board logs nothing
    assert pilead("board").returncode == 0
    assert f"REFS MOVED in {SID}" in (home(tmp_path) / "board.html").read_text()


def test_board_flags_refs_not_compared(tmp_path):
    reported_session(tmp_path)
    make_session(tmp_path, sid="calm-1")
    refs.baseline_path(home(tmp_path), SID, 1).unlink()
    data = review.board_data(home(tmp_path))
    rows = {e["session_id"]: e for e in data["executors"]}
    assert (rows[SID]["refs_not_compared"], rows[SID]["refs_moved"]) == (True, False)
    assert rows["calm-1"]["refs_not_compared"] is False
    (w,) = [x for x in data["warnings"] if "no longer registered" not in x["text"]]  # p22: not the orphan entry
    assert w["level"] == "warn" and f"refs of {SID} NOT COMPARED for packet 0001 (no baseline stored)" in w["text"]
    assert pilead("board").returncode == 0
    assert f"refs of {SID} NOT COMPARED" in (home(tmp_path) / "board.html").read_text()


# ── a baseline of the wrong shape is "baseline unreadable", never a traceback ─────────────────────
def no_traceback(*results):
    for r in results:
        assert "Traceback" not in r.stderr, r.stderr


def stored_fields(tmp_path):
    """The baseline packet 0001's spawn stored, as a dict — the fields `snapshot()` actually writes."""
    return json.loads(refs.baseline_path(home(tmp_path), SID, 1).read_text())


def test_the_shape_checked_is_every_field_snapshot_writes(tmp_path):
    reported_session(tmp_path)
    assert set(stored_fields(tmp_path)) == set(refs.SHAPE)
    assert refs.load(home(tmp_path), SID, 1)[1] is None


def _rewrite(tmp_path, change):
    p = refs.baseline_path(home(tmp_path), SID, 1)
    d = json.loads(p.read_text())
    change(d)
    p.write_text(json.dumps(d))


SHAPE_DAMAGE = {
    **{f"{f}-missing": (lambda f: lambda d: d.pop(f))(f) for f in refs.SHAPE},
    **{f"{f}-wrong-type": (lambda f: lambda d: d.__setitem__(f, 5))(f) for f in refs.SHAPE},
    "worktrees-of-strings": lambda d: d.__setitem__("worktrees", ["a"]),
    "worktree-entry-path-missing": lambda d: d["worktrees"][0].pop("path"),
    "worktree-entry-head-wrong-type": lambda d: d["worktrees"][0].__setitem__("head", ["x"]),
    "refs-value-wrong-type": lambda d: d["refs"].__setitem__(next(iter(d["refs"])), 5),
}


@pytest.mark.parametrize("damage", list(SHAPE_DAMAGE))
def test_row_baseline_of_the_wrong_shape(tmp_path, damage):
    reported_session(tmp_path)
    _rewrite(tmp_path, SHAPE_DAMAGE[damage])
    assert refs.load(home(tmp_path), SID, 1) == (None, "baseline unreadable")
    results = outcome()
    no_traceback(*results)
    assert_not_compared("baseline unreadable", *results)


def test_an_accepted_baseline_keeps_its_extra_field_and_still_compares(tmp_path):
    reported_session(tmp_path)
    assert pilead("refs", "accept", SID, "--reason", "r").returncode == 0
    assert "accepted" in stored_fields(tmp_path)
    assert cli("check", SID, "--refs-only").stdout == "refs: unchanged since packet 0001\n"


def test_board_survives_a_session_with_a_wrong_shaped_baseline(tmp_path):
    reported_session(tmp_path)
    make_session(tmp_path, sid="calm-1")
    _rewrite(tmp_path, lambda d: d.__setitem__("worktrees", 5))
    data = review.board_data(home(tmp_path))
    rows = {e["session_id"]: e for e in data["executors"]}
    assert set(rows) == {SID, "calm-1"}
    assert (rows[SID]["refs_not_compared"], rows[SID]["refs_moved"]) == (True, False)
    assert (rows["calm-1"]["refs_not_compared"], rows["calm-1"]["refs_moved"]) == (False, False)
    (w,) = [x for x in data["warnings"] if "no longer registered" not in x["text"]]  # p22: not the orphan entry
    assert f"refs of {SID} NOT COMPARED for packet 0001 (baseline unreadable)" in w["text"]
    r = pilead("board")
    assert r.returncode == 0 and "Traceback" not in r.stderr


def test_board_survives_a_refs_check_that_raises(tmp_path, monkeypatch):
    reported_session(tmp_path)
    make_session(tmp_path, sid="calm-1")
    real = refs.check

    def check(home_, sid, *a, **k):
        if sid == SID:
            raise TypeError("unexpected\nshape")
        return real(home_, sid, *a, **k)

    monkeypatch.setattr(refs, "check", check)
    data = review.board_data(home(tmp_path))
    rows = {e["session_id"]: e for e in data["executors"]}
    assert set(rows) == {SID, "calm-1"}
    assert rows[SID]["refs_not_compared"] is True
    assert (rows["calm-1"]["refs_not_compared"], rows["calm-1"]["refs_moved"]) == (False, False)
    (w,) = [x for x in data["warnings"] if "no longer registered" not in x["text"]]  # p22: not the orphan entry
    assert f"refs of {SID} NOT COMPARED for packet 0001 (TypeError: unexpected shape)" in w["text"]


# ── a baseline was stored and the repository is gone ─────────────────────────────────────────────
def _drop_git_dir(wt):
    shutil.rmtree(wt / ".git")


def _drop_worktree(wt):
    shutil.rmtree(wt)


@pytest.mark.parametrize("gone", [_drop_git_dir, _drop_worktree], ids=["git-dir-removed", "worktree-deleted"])
def test_row_repository_missing_with_a_stored_baseline(tmp_path, gone):
    wt, _ = reported_session(tmp_path)
    assert verdict(cli("verify", SID)) == "COUNTS-MATCH"
    gone(wt)
    only, chk, ver, auto = results = outcome()
    no_traceback(*results)
    line = "refs: not compared (repository missing)"
    assert (only.returncode, only.stdout) == (0, line + "\n")
    assert chk.returncode == 0 and chk.stdout.splitlines()[0] == line
    assert ver.stdout.splitlines()[0] == line
    assert "refs for packet 0001 were not compared: repository missing" in ver.stdout
    # the report claims two staged files the gone repository no longer shows: already MISMATCH
    assert (verdict(ver), ver.returncode) == ("MISMATCH", 1)
    assert autocommit_line(auto) == "AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-not-compared" and auto.returncode == 1


def test_row_repository_missing_with_an_unreadable_baseline(tmp_path):
    wt, _ = reported_session(tmp_path)
    _not_json(refs.baseline_path(home(tmp_path), SID, 1))
    _drop_git_dir(wt)
    assert cli("check", SID, "--refs-only").stdout == "refs: not compared (repository missing)\n"


def test_row_not_a_repository_and_no_baseline_is_unchanged(tmp_path):
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    refs.baseline_path(home(tmp_path), SID, 1).unlink()
    _drop_git_dir(wt)  # never a repository as far as any baseline knows
    only, chk, ver, auto = results = outcome()
    no_traceback(*results)
    assert (only.returncode, only.stdout) == (0, "refs: not a git repository\n")
    assert chk.returncode == 0 and "refs:" not in chk.stdout
    assert ver.stdout.splitlines()[0] == "refs: not a git repository"
    assert "refs-not-compared" not in auto.stdout

"""lib/pilead/wakefacts.py — the size of a staged diff and whether a report's work landed — and its three
users: `check --refs-only --wake`, the `diff:` line of a plain `check` (and `check --json`), and the footnote
and `diff` key of `pilead list`. Repositories are made in the test's temp directory with the real git, homes
live under the test's temp directory, `bin/pilead` runs against the stub osascript of tests/test_cli_core.py
(no real tab, no real pi). One shared table, tests/briefs.json, holds reports and their briefs (the TypeScript
suite reads it too)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cli_core import ROOT, home, pilead, stub_osascript  # noqa: F401  (fixtures)

sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, notify, refs, wakefacts  # noqa: E402

REPORT = """Parser rewrite landed; 4 tests, suite green, staged.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: lib/x.py
"""
FOOTNOTE_TAIL = " — read with pilead check <sid>; the wake is tried again, do not wait for it."
OLD = 3600  # the report is an hour old: a commit made now is newer


def H(tmp_path):
    return home(tmp_path)


def git(wt, *argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run([refs.git_path(), "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *argv],
                          check=True, capture_output=True, text=True, env=env)


def repo(tmp_path, name):
    """A repository with lib/x.py committed: clean."""
    wt = (tmp_path / "repos" / name)
    (wt / "lib").mkdir(parents=True)
    wt = wt.resolve()
    git(wt, "init", "-q")
    (wt / "lib" / "x.py").write_text("x = 1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "init")
    return wt


def stage(wt, name, text):
    (Path(wt) / name).write_text(text)
    git(wt, "add", name)


def session(tmp_path, sid, wt, *, lead="lead-1", status="reported", report=REPORT, age=OLD, packets=1, worktree=None):
    """sessions/<sid>: meta.json, status, packet(s), and the current packet's report `age` seconds old (none
    when `report` is None). `worktree` overrides what meta.json records (default: `wt`)."""
    hm = H(tmp_path)
    (hm / "leads").mkdir(parents=True, exist_ok=True)
    (hm / "leads" / "lead-1.json").write_text(json.dumps({"sid": "lead-1", "project": "proj"}) + "\n")
    d = hm / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "lead": lead, "worktree": str(wt) if worktree is None else worktree, "topic": sid,
            "model": "anthropic/claude-sonnet-5", "status": status, "created": "2026-09-28T10:00:00Z",
            "packets": packets, "label": f"[Exec] {sid}", "layout": "tab"}
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (d / "status").write_text(status + "\n")
    for n in range(1, packets + 1):
        (d / f"packet-{n:04d}.md").write_text(f"GOAL: packet {n}\n")
    if report is not None:
        rp = d / f"report-{packets:04d}.md"
        rp.write_text(report)
        t = time.time() - age
        os.utime(rp, (t, t))
    return d


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


# ── diff_size ─────────────────────────────────────────────────────────────────────────────────────
def whole(state, files=None, ins=None, dels=None, text=None, why=None):
    return {"state": state, "files": files, "insertions": ins, "deletions": dels, "text": text, "why": why}


def test_no_worktree_is_no_worktree(tmp_path):
    assert wakefacts.diff_size("") == whole("no-worktree")
    assert wakefacts.diff_size(None) == whole("no-worktree")
    assert wakefacts.diff_size(tmp_path / "gone") == whole("no-worktree")
    (tmp_path / "afile").write_text("x")
    assert wakefacts.diff_size(tmp_path / "afile") == whole("no-worktree")  # not a directory


def test_a_plain_directory_is_not_a_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert wakefacts.diff_size(d) == whole("not-a-repo")


def test_nothing_staged_is_empty_with_zero_counts_and_no_text(tmp_path):
    wt = repo(tmp_path, "e")
    assert wakefacts.diff_size(wt) == whole("empty", 0, 0, 0)
    (wt / "lib" / "x.py").write_text("x = 2\n")  # modified, not staged: still nothing staged
    (wt / "u.txt").write_text("untracked\n")
    assert wakefacts.diff_size(wt) == whole("empty", 0, 0, 0)


def test_three_files_plus_ten_minus_two_gives_the_exact_text(tmp_path):
    wt = repo(tmp_path, "t")
    (wt / "lib" / "x.py").write_text("x = 1\nb\nc\nd\n")  # +3, -0
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "four lines")
    (wt / "lib" / "x.py").write_text("x = 1\nb\n")  # -2
    (wt / "p.txt").write_text("1\n2\n3\n4\n5\n")  # +5
    (wt / "q.txt").write_text("1\n2\n3\n4\n5\n")  # +5
    git(wt, "add", "-A")
    assert wakefacts.diff_size(wt) == whole("staged", 3, 10, 2, "(diff: 3 files +10/-2)")


def test_a_binary_file_counts_as_a_file_and_adds_nothing(tmp_path):
    wt = repo(tmp_path, "b")
    (wt / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 64)
    (wt / "n.txt").write_text("1\n2\n")
    git(wt, "add", "-A")
    assert wakefacts.diff_size(wt) == whole("staged", 2, 2, 0, "(diff: 2 files +2/-0)")


def test_a_git_that_does_not_answer_is_not_read_inside_the_time_limit(tmp_path, monkeypatch):
    wt = repo(tmp_path, "slow")
    stub = tmp_path / "slowgit"
    stub.write_text("#!/bin/sh\nexec sleep 30\n")
    stub.chmod(0o755)
    monkeypatch.setattr(refs, "git_path", lambda env_path=None: str(stub))
    assert wakefacts.GIT_TIMEOUT_SECONDS == 10
    monkeypatch.setattr(wakefacts, "GIT_TIMEOUT_SECONDS", 1)
    t0 = time.monotonic()
    d = wakefacts.diff_size(wt)
    assert time.monotonic() - t0 < 5
    assert d == whole("not-read", why="git did not answer in 1 seconds")


def test_a_git_that_fails_or_is_missing_is_not_read(tmp_path, monkeypatch):
    wt = repo(tmp_path, "fail")
    stub = tmp_path / "failgit"
    stub.write_text("#!/bin/sh\necho 'fatal: boom' >&2\nexit 128\n")
    stub.chmod(0o755)
    monkeypatch.setattr(refs, "git_path", lambda env_path=None: str(stub))
    assert wakefacts.diff_size(wt) == whole("not-read", why="git failed: fatal: boom")

    def none(env_path=None):
        raise refs.RefsError("git failed: no real git found on PATH")
    monkeypatch.setattr(refs, "git_path", none)
    assert wakefacts.diff_size(wt) == whole("not-read", why="git failed: no real git found on PATH")


def test_diff_line_forms(tmp_path):
    assert wakefacts.diff_line(whole("staged", 3, 10, 2, "(diff: 3 files +10/-2)")) == "diff: 3 files +10/-2"
    assert wakefacts.diff_line(whole("not-read", why="git failed: boom")) == "diff: not read (git failed: boom)"
    for st in ("empty", "not-a-repo", "no-worktree"):
        assert wakefacts.diff_line(whole(st)) is None


# ── landed ────────────────────────────────────────────────────────────────────────────────────────
def test_landed_true_when_every_claim_is_clean_and_head_is_newer(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    assert wakefacts.landed(H(tmp_path), "s", 1) == (True, False)


def test_landed_false_when_a_claimed_path_is_still_dirty(tmp_path):
    wt = repo(tmp_path, "d")
    session(tmp_path, "s", wt)
    (wt / "lib" / "x.py").write_text("x = 2\n")
    assert wakefacts.landed(H(tmp_path), "s", 1) == (False, False)


@pytest.mark.parametrize("why", ["no worktree", "a directory that is no repository", "no report",
                                 "no claimed path", "a claim naming no tracked file", "HEAD older than the report"])
def test_landed_is_none_for_each_reason_it_cannot_be_told(tmp_path, why):
    wt = repo(tmp_path, "n")
    kw = {"worktree": None}
    if why == "no worktree":
        kw = {"worktree": ""}
    elif why == "a directory that is no repository":
        plain = tmp_path / "plain"
        plain.mkdir()
        kw = {"worktree": str(plain)}
    elif why == "no report":
        kw = {"report": None}
    elif why == "no claimed path":
        kw = {"report": REPORT.replace("Changed: lib/x.py", "Changed: none")}
    elif why == "a claim naming no tracked file":
        kw = {"report": REPORT.replace("lib/x.py", "lib/nothing.py")}
    elif why == "HEAD older than the report":
        kw = {"age": -3600}  # the report file is an hour in the future
    session(tmp_path, "s", wt, **kw)
    assert wakefacts.landed(H(tmp_path), "s", 1) == (None, False)


def test_a_claim_outside_the_repository_is_none(tmp_path):
    wt = repo(tmp_path, "o")
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    session(tmp_path, "s", wt, report=REPORT.replace("lib/x.py", "../../outside.txt"))
    assert wakefacts.landed(H(tmp_path), "s", 1) == (None, False)
    session(tmp_path, "t", wt, report=REPORT.replace("lib/x.py", str(outside)))
    assert wakefacts.landed(H(tmp_path), "t", 1) == (None, False)


def test_landed_of_an_unknown_session_or_bad_id_is_none(tmp_path):
    assert wakefacts.landed(H(tmp_path), "nobody", 1) == (None, False)
    assert wakefacts.landed(H(tmp_path), "../x", 1) == (None, False)


def ledger_of(tmp_path, *events):
    for ev, fields in events:
        ledger.append(H(tmp_path), ev, **fields)


def test_proven_by_each_of_the_three_events_and_by_none(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    hm = H(tmp_path)
    assert wakefacts.landed(hm, "s", 1) == (True, False)  # no event
    ledger.append(hm, "refs_accepted", session_id="other", packet=1, reason="x", head="h")
    ledger.append(hm, "refs_accepted", session_id="s", packet=2, reason="x", head="h")
    ledger.append(hm, "auto_commit", session_id="s", packet=1, cleared=False, reason="no")
    ledger.append(hm, "auto_closed", session_id="s", action="close", reason="idle 30m", trigger="manual")
    assert wakefacts.landed(hm, "s", 1) == (True, False)  # events for another session/packet, uncleared, other reason
    ledger.append(hm, "refs_accepted", session_id="s", packet=1, reason="x", head="h")
    assert wakefacts.landed(hm, "s", 1) == (True, True)


def test_proven_by_a_cleared_auto_commit(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    ledger.append(H(tmp_path), "auto_commit", session_id="s", packet=1, cleared=True, reason="ok")
    assert wakefacts.landed(H(tmp_path), "s", 1) == (True, True)


def test_proven_by_an_auto_closed_landed_after_the_spawn_for_packet_one(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    hm = H(tmp_path)
    ledger.append(hm, "auto_closed", session_id="s", action="close", reason="landed", trigger="manual")
    ledger.append(hm, "spawned", session_id="s", lead="lead-1", worktree="/x", topic="t")
    assert wakefacts.landed(hm, "s", 1) == (True, False)  # written before the spawn: proves nothing
    ledger.append(hm, "auto_closed", session_id="s", action="close", reason="landed", trigger="manual")
    assert wakefacts.landed(hm, "s", 1) == (True, True)


def test_an_auto_closed_landed_from_an_earlier_packet_is_not_proof(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"), packets=2)
    hm = H(tmp_path)
    ledger.append(hm, "spawned", session_id="s", lead="lead-1", worktree="/x", topic="t")
    ledger.append(hm, "auto_closed", session_id="s", action="close", reason="landed", trigger="manual")
    ledger.append(hm, "packet_sent", session_id="s", packet=2, source="p")
    assert wakefacts.landed(hm, "s", 2) == (True, False)
    ledger.append(hm, "auto_closed", session_id="s", action="close", reason="landed", trigger="manual")
    assert wakefacts.landed(hm, "s", 2) == (True, True)


def test_proof_needs_the_landing_itself(tmp_path):
    wt = repo(tmp_path, "d")
    session(tmp_path, "s", wt)
    ledger.append(H(tmp_path), "refs_accepted", session_id="s", packet=1, reason="x", head="h")
    (wt / "lib" / "x.py").write_text("x = 2\n")
    assert wakefacts.landed(H(tmp_path), "s", 1) == (False, False)  # dirty: not landed, so nothing is proven


# ── facts ─────────────────────────────────────────────────────────────────────────────────────────
def test_facts_of_a_session(tmp_path):
    wt = repo(tmp_path, "f")
    session(tmp_path, "s", wt)
    stage(wt, "new.txt", "one\n")
    assert wakefacts.facts(H(tmp_path), "s") == {"packet": 1, "diff": "(diff: 1 files +1/-0)",
                                                 "diff_state": "staged", "landed": True, "proven": False}
    ledger.append(H(tmp_path), "refs_accepted", session_id="s", packet=1, reason="x", head="h")
    assert wakefacts.facts(H(tmp_path), "s")["proven"] is True


def test_facts_of_a_session_that_cannot_be_read(tmp_path):
    want = {"packet": None, "diff": None, "diff_state": "no-worktree", "landed": None, "proven": False}
    assert wakefacts.facts(H(tmp_path), "nobody") == want
    assert wakefacts.facts(H(tmp_path), "../x") == want


def test_facts_of_an_empty_diff_has_no_text(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    f = wakefacts.facts(H(tmp_path), "s")
    assert (f["diff"], f["diff_state"]) == (None, "empty")


# ── the shared table of briefs (the TypeScript suite reads it too) ────────────────────────────────
BRIEFS = json.loads((Path(__file__).resolve().parent / "briefs.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", BRIEFS, ids=[r["name"] for r in BRIEFS])
def test_briefs_table_agrees_with_pythons_brief(tmp_path, row):
    assert notify.brief(row["report"]) == row["brief"]
    d = H(tmp_path) / "sessions" / "s"
    d.mkdir(parents=True)
    (d / "report-0001.md").write_bytes(row["report"].encode("utf-8"))
    assert notify.report_brief(H(tmp_path), "s", 1) == row["brief"]


# ── check --refs-only --wake ──────────────────────────────────────────────────────────────────────
def test_refs_only_wake_prints_the_refs_line_then_the_facts(tmp_path):
    wt = repo(tmp_path, "w")
    session(tmp_path, "s", wt)
    stage(wt, "new.txt", "one\n")
    plain = pilead("check", "s", "--refs-only")
    both = pilead("check", "s", "--refs-only", "--wake")
    lines = both.stdout.split("\n")
    assert len(plain.stdout.splitlines()) == 1
    assert lines[0] + "\n" == plain.stdout  # byte-identical first line
    assert lines[2:] == [""] and lines[1].startswith("wake: ")
    assert json.loads(lines[1][len("wake: "):]) == {"packet": 1, "diff": "(diff: 1 files +1/-0)",
                                                    "diff_state": "staged", "landed": True, "proven": False}
    assert both.returncode == 0


def test_refs_only_wake_of_an_unknown_session_has_a_second_line_of_no_facts(tmp_path):
    r = pilead("check", "nobody", "--refs-only", "--wake")
    lines = r.stdout.splitlines()
    assert lines[0] == "refs: not compared (unknown session nobody)" and len(lines) == 2
    assert json.loads(lines[1][len("wake: "):]) == {"packet": None, "diff": None, "diff_state": "no-worktree",
                                                    "landed": None, "proven": False}
    assert pilead("check", "nobody", "--refs-only").stdout == lines[0] + "\n"


def test_refs_only_wake_writes_nothing(tmp_path):
    wt = repo(tmp_path, "w")
    session(tmp_path, "s", wt)
    stage(wt, "new.txt", "one\n")
    before = tree(H(tmp_path))
    pilead("check", "s", "--refs-only", "--wake")
    pilead("check", "nobody", "--refs-only", "--wake")
    assert tree(H(tmp_path)) == before


def test_wake_without_refs_only_exits_2_with_its_line(tmp_path):
    session(tmp_path, "s", repo(tmp_path, "s"))
    before = tree(H(tmp_path))
    for args in (["check", "s", "--wake"], ["check", "--wake"], ["check", "--all", "--wake"]):
        r = pilead(*args, check=False)
        assert r.returncode == 2 and r.stdout == ""
        assert r.stderr == "pilead: check --wake needs --refs-only\n"
    assert tree(H(tmp_path)) == before


def test_refs_only_wake_without_a_sid_keeps_its_usage_line(tmp_path):
    r = pilead("check", "--refs-only", "--wake", check=False)
    assert r.returncode == 2 and r.stdout == "" and r.stderr == "pilead: check --refs-only needs a session id\n"


# ── plain check and check --json ──────────────────────────────────────────────────────────────────
def with_stub_git(tmp_path, monkeypatch, body):
    d = tmp_path / "stubgit"
    d.mkdir(exist_ok=True)
    (d / "git").write_text(body)
    (d / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")


def test_check_prints_the_diff_line_directly_after_the_reports_path_when_staged(tmp_path):
    wt = repo(tmp_path, "c")
    d = session(tmp_path, "s", wt)
    stage(wt, "new.txt", "one\ntwo\n")
    out = pilead("check", "s").stdout.splitlines()
    i = out.index(str(d / "report-0001.md"))
    assert out[i + 1] == "diff: 1 files +2/-0"
    assert out[i + 2] == "Parser rewrite landed; 4 tests, suite green, staged."
    (only,) = [x for x in json.loads(pilead("check", "s", "--json").stdout)]
    assert only["diff"] == {"state": "staged", "files": 1, "insertions": 2, "deletions": 0, "why": None}


def test_check_says_not_read_when_git_cannot_be_read(tmp_path, monkeypatch):
    wt = repo(tmp_path, "c")
    d = session(tmp_path, "s", wt)
    with_stub_git(tmp_path, monkeypatch,
                  "#!/bin/sh\ncase \"$1\" in --version) echo 'git version 2.40.0';; *) echo 'fatal: boom' >&2; exit 128;; esac\n")
    out = pilead("check", "s").stdout.splitlines()
    i = out.index(str(d / "report-0001.md"))
    assert out[i + 1] == "diff: not read (git failed: fatal: boom)"
    (only,) = json.loads(pilead("check", "s", "--json").stdout)
    assert only["diff"] == {"state": "not-read", "files": None, "insertions": None, "deletions": None,
                            "why": "git failed: fatal: boom"}


def test_check_prints_no_diff_line_for_the_other_three_states_and_json_has_the_key(tmp_path):
    empty = repo(tmp_path, "e")
    plain = tmp_path / "plain"
    plain.mkdir()
    d = {}
    d["s-empty"] = session(tmp_path, "s-empty", empty)
    d["s-plain"] = session(tmp_path, "s-plain", plain)
    d["s-none"] = session(tmp_path, "s-none", "", worktree="")
    want = {"s-empty": whole("empty", 0, 0, 0), "s-plain": whole("not-a-repo"), "s-none": whole("no-worktree")}
    for sid, dd in d.items():
        out = pilead("check", sid).stdout.splitlines()
        i = out.index(str(dd / "report-0001.md"))
        assert not out[i + 1].startswith("diff:") and not any(x.startswith("diff:") for x in out)
        (row,) = json.loads(pilead("check", sid, "--json").stdout)
        assert row["diff"] == {k: v for k, v in want[sid].items() if k != "text"}


def test_check_has_no_diff_line_and_a_null_key_without_a_report(tmp_path):
    wt = repo(tmp_path, "n")
    session(tmp_path, "s", wt, status="busy", report=None)
    stage(wt, "new.txt", "one\n")
    assert not any(x.startswith("diff:") for x in pilead("check", "s").stdout.splitlines())
    (row,) = json.loads(pilead("check", "s", "--json").stdout)
    assert "diff" in row and row["diff"] is None and row["reported"] is False


# ── list ──────────────────────────────────────────────────────────────────────────────────────────
def footnote(out):
    lines = [x for x in out.splitlines() if x.startswith("  🚦")]
    assert len(lines) <= 1
    return lines[0] if lines else None


def two_reported(tmp_path):
    a, b = repo(tmp_path, "a"), repo(tmp_path, "b")
    session(tmp_path, "a-staged", a)
    session(tmp_path, "b-plain", b)
    stage(a, "new.txt", "one\ntwo\nthree\n")
    return a, b


def test_list_footnote_names_the_unhanded_reports_with_their_size(tmp_path):
    two_reported(tmp_path)
    want = ("  🚦 2 report(s) not yet handed to their lead: a-staged (packet 0001) (diff: 1 files +3/-0), "
            "b-plain (packet 0001)" + FOOTNOTE_TAIL)
    out = pilead("list").stdout
    assert footnote(out) == want
    lines = out.splitlines()
    assert lines.index(want) > lines.index("EXECUTORS")  # under the executors table


def test_list_footnote_goes_once_wake_delivered_is_in_the_ledger(tmp_path):
    two_reported(tmp_path)
    hm = H(tmp_path)
    ledger.append(hm, "wake_delivered", session_id="lead-1", executor="a-staged", packet=1, report="r")
    assert footnote(pilead("list").stdout) == ("  🚦 1 report(s) not yet handed to their lead: "
                                               "b-plain (packet 0001)" + FOOTNOTE_TAIL)
    ledger.append(hm, "wake_delivered", session_id="lead-1", executor="b-plain", packet=2, report="r")  # other packet
    assert footnote(pilead("list").stdout) is not None
    ledger.append(hm, "wake_delivered", session_id="lead-1", executor="b-plain", packet=1, report="r")
    assert footnote(pilead("list").stdout) is None


def test_list_footnote_goes_for_a_proven_landing_and_needs_a_lead(tmp_path):
    two_reported(tmp_path)
    ledger.append(H(tmp_path), "refs_accepted", session_id="b-plain", packet=1, reason="x", head="h")
    assert footnote(pilead("list").stdout).startswith("  🚦 1 report(s) not yet handed to their lead: a-staged ")
    session(tmp_path, "c-orphan", repo(tmp_path, "c"), lead="")
    ledger.append(H(tmp_path), "refs_accepted", session_id="a-staged", packet=1, reason="x", head="h")
    assert footnote(pilead("list").stdout) is None  # c-orphan has no lead; the other two are proven


def test_list_footnote_ignores_sessions_that_are_not_reported_or_have_no_report(tmp_path):
    session(tmp_path, "s-busy", repo(tmp_path, "a"), status="busy", report=None)
    session(tmp_path, "s-noreport", repo(tmp_path, "b"), status="reported", report=None)
    assert footnote(pilead("list").stdout) is None


def test_list_json_rows_carry_the_diff_text_only_for_reported_rows(tmp_path):
    a, _ = two_reported(tmp_path)
    session(tmp_path, "c-busy", repo(tmp_path, "c"), status="busy", report=None)
    stage(tmp_path / "repos" / "c", "n.txt", "x\n")
    rows = {r["sid"]: r for r in json.loads(pilead("list", "--json").stdout)["executors"]}
    assert rows["a-staged"]["diff"] == "(diff: 1 files +3/-0)"
    assert rows["b-plain"]["diff"] is None  # reported, nothing staged
    assert rows["c-busy"]["diff"] is None  # staged files, but not reported: not read
    assert all(list(r)[-2:] == ["diff", "orphan"] for r in rows.values())  # p22: `orphan` is the last key

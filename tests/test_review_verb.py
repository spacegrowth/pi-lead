"""pilead review driven through bin/pilead, over a session made as test_verify_diff_board makes one, with
tests/fake_pi as `pi` (conftest) and a lead record and lead session log written here under the test's own
agent directory. No real pi, no model, no real tab."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cli_core import PILEAD, home, pilead  # noqa: F401  (also brings the osascript stub fixture)
from test_usage import assistant, u, user, write_log
from test_verify_diff_board import SID, git, make_session, report, stage, write_report

ROOT = Path(__file__).resolve().parents[1]
LEAD = "lead-1"
LEAD_MODEL = "anthropic/claude-sonnet-5"

ANSWER = """Verify: VERDICT: COUNTS-MATCH
TL;DR: Status: clean / Risk flags: none / UNVERIFIED: none / Changed: src/alpha.py, src/beta.py
Tests: not re-run
Existing lines edited: none
Findings:
1. src/alpha.py:1 — should-fix — the file holds only a placeholder line.
2. src/beta.py:1 — note — same as above.
Recommendation: fix-list — one item needs a change.
"""


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────
def models_json(tmp_path, window="1M"):
    h = home(tmp_path)
    h.mkdir(parents=True, exist_ok=True)
    (h / "models.json").write_text(json.dumps({"fetched": int(time.time()), "models": [
        {"provider": "anthropic", "id": "claude-sonnet-5", "context": window},
        {"provider": "deepseek", "id": "deepseek-flash", "context": "1M"}]}) + "\n")


def make_lead(tmp_path, entries=None):
    """A lead record, its inbox and its session log (pi's default place for its cwd); the log's path."""
    h = home(tmp_path)
    cwd = tmp_path / "lead-cwd"
    cwd.mkdir(exist_ok=True)
    (h / "leads").mkdir(parents=True, exist_ok=True)
    (h / "leads" / f"{LEAD}.json").write_text(json.dumps({"sid": LEAD, "project": "proj", "cwd": str(cwd),
                                                          "inbox": str(h / "leads" / f"{LEAD}.inbox.md")}) + "\n")
    (h / "leads" / f"{LEAD}.inbox.md").write_text("")
    if entries is None:
        entries = [user("plan it"), assistant(u(3, 1200, 10000, 800, cost=0.1))]
    return write_log(LEAD, cwd, entries)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A reported session with two staged files, a lead (the caller, by $PI_LEAD_SID) with a log whose
    live context is 12003 tokens, and a fresh models.json."""
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    models_json(tmp_path)
    log = make_lead(tmp_path)
    monkeypatch.setenv("PI_LEAD_SID", LEAD)
    answer = tmp_path / "answer.txt"
    answer.write_text(ANSWER)
    monkeypatch.setenv("FAKE_PI_STDOUT", str(answer))
    return {"wt": wt, "sdir": sdir, "log": log, "work": sdir / "review-0001", "answer": answer}


def run(*args, env=None):
    return subprocess.run([sys.executable, str(PILEAD), "review", *args], capture_output=True, text=True,
                          env=env)


def stub_log(tmp_path):
    p = tmp_path / "fake-pi.log"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else None


def ledger(tmp_path):
    p = home(tmp_path) / "ledger.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def tree_bytes(root):
    """{relative path: bytes} of every file under root (symlinks as their target text)."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_file():
                out[rel] = p.read_bytes()
            else:
                out[rel] = "dir"
    return out


def measured(wt):
    g = lambda *a: subprocess.run(["git", "-C", str(wt), *a], capture_output=True, check=True).stdout  # noqa: E731
    diff = g("diff", "--cached", "--no-ext-diff", "--no-textconv")
    return g("write-tree").decode().strip(), hashlib.sha1(diff).hexdigest()[:12], diff


def act(tmp_path, monkeypatch, script):
    p = tmp_path / "act.sh"
    p.write_text(script)
    monkeypatch.setenv("FAKE_PI_ACT", str(p))


def expected_argv(s, model=LEAD_MODEL, fork=True):
    work = s["work"]
    return (["-p", "--model", model, "--tools", "read,grep,find,ls", "--no-extensions", "--no-skills",
             "--no-prompt-templates", "--no-context-files", "--no-approve", "--session-dir", str(work / "session"),
             "--name", f"[Review] {SID} 0001"] + (["--fork", str(s["log"])] if fork else [])
            + [f"@{work / 'prompt.md'}"])


# ── a valid review ───────────────────────────────────────────────────────────────────────────
def test_valid_review(tmp_path, monkeypatch, setup):
    s = setup
    # the reviewer's own session file: three entries copied from the lead's log, two new ones
    lead_entries = [json.loads(ln) for ln in s["log"].read_text().splitlines()]
    new = [assistant(u(100, 40, 2000, 0, cost=0.01)), assistant(u(50, 10, 2150, 0, cost=0.02))]
    sess_lines = [{"type": "session", "id": "rev-1"}] + lead_entries[1:] + new
    payload = tmp_path / "session.jsonl"
    payload.write_text("\n".join(json.dumps(e) for e in sess_lines) + "\n")
    act(tmp_path, monkeypatch, f'mkdir -p "{s["work"]}/session" && cp "{payload}" "{s["work"]}/session/x_rev-1.jsonl"\n')
    tree, sha, _ = measured(s["wt"])
    r = run(SID, "--no-rerun")
    assert r.returncode == 0, r.stderr
    rev = s["sdir"] / "review-0001.md"
    want = (f"# Review of {SID} packet 0001 — pilead review\n"
            "Review: VALID\n"
            f"Reviewer: headless pi, {LEAD_MODEL}, with the lead's context; tools read, grep, find, ls\n"
            f"Hash: start {tree} / end {tree} — match; refs unchanged; diff sha {sha}\n"
            "Measured: verify COUNTS-MATCH, exit 0; re-run not asked for\n"
            "Cost: 4.3k/50 tokens over 2 requests, cost $0.0300\n"
            "---\n" + ANSWER)
    assert rev.read_text() == want
    assert r.stdout == want + f"saved: {rev}\n"
    ev = [e for e in ledger(tmp_path) if e["event"] in ("report_reviewed", "review_failed")]
    assert len(ev) == 1 and ev[0]["event"] == "report_reviewed"
    assert set(ev[0]) == {"ts", "event", "session_id", "packet", "findings", "reviewer", "model", "tree",
                          "recommendation", "blockers", "should_fix", "notes"}
    assert {k: ev[0][k] for k in ev[0] if k not in ("ts", "event")} == {
        "session_id": SID, "packet": 1, "findings": str(rev), "reviewer": "headless", "model": LEAD_MODEL,
        "tree": tree, "recommendation": "fix-list", "blockers": 0, "should_fix": 1, "notes": 1}
    rec, = stub_log(tmp_path)
    assert rec["argv"] == expected_argv(s)
    assert rec["cwd"] == str(s["wt"])
    assert "PI_LEAD_SID" not in rec["env"] and rec["env"]["PI_LEAD_ROLE"] == "reviewer"
    # the inputs
    w = s["work"]
    assert (w / "packet.md").read_text() == (s["sdir"] / "packet-0001.md").read_text()
    assert (w / "report.md").read_text() == (s["sdir"] / "report-0001.md").read_text()
    assert (w / "diff.patch").read_bytes() == measured(s["wt"])[2]
    assert "VERDICT: COUNTS-MATCH" in (w / "verify.txt").read_text()
    assert (w / "prompt.md").read_text().startswith("You are a copy of the lead's session")


def test_start_line_lead_model_and_from_option(tmp_path, setup):
    s = setup
    kb = (len(measured(s["wt"])[2]) + 1023) // 1024
    r = run(SID, "--no-rerun")
    assert r.returncode == 0
    assert r.stderr == (f"pilead review: starting a headless pi on {LEAD_MODEL} (the lead's own model), with the "
                        f"lead's context, about 12k tokens; staged diff {kb} KB; window 1M; timeout 1800s. "
                        "This costs tokens.\n")
    r = run(SID, "--no-rerun", "--model", "dsflash", "--timeout", "600")
    assert r.returncode == 0
    assert r.stderr == ("pilead review: starting a headless pi on deepseek/deepseek-flash (from --model), with the "
                        f"lead's context, about 12k tokens; staged diff {kb} KB; window 1M; timeout 600s. "
                        "This costs tokens.\n")
    assert stub_log(tmp_path)[-1]["argv"][:3] == ["-p", "--model", "deepseek/deepseek-flash"]


def test_default_reruns_through_verify(tmp_path, setup):
    r = run(SID)
    assert r.returncode == 0, r.stderr
    assert "; re-run nothing to re-run\n" in (setup["sdir"] / "review-0001.md").read_text()


# ── INVALID: something moved ─────────────────────────────────────────────────────────────────
def test_staging_a_file_during_the_review_is_invalid(tmp_path, monkeypatch, setup):
    act(tmp_path, monkeypatch, "echo more > more.txt && git add more.txt\n")
    r = run(SID, "--no-rerun")
    assert r.returncode == 1
    text = (setup["sdir"] / "review-0001.md").read_text()
    head = text.splitlines()[1]
    assert head.startswith("Review: INVALID (") and head.endswith(" changed during the review)")
    assert "tree" in head and "status" in head
    assert " — differ; refs unchanged;" in text
    events = [e["event"] for e in ledger(tmp_path)]
    assert "review_failed" in events and "report_reviewed" not in events
    ev = [e for e in ledger(tmp_path) if e["event"] == "review_failed"][0]
    assert set(ev) == {"ts", "event", "session_id", "packet", "outcome", "model", "tree"}
    assert ev["outcome"] == head[len("Review: "):]


def test_a_new_branch_is_invalid(tmp_path, monkeypatch, setup):
    act(tmp_path, monkeypatch, "git branch sneaky\n")
    r = run(SID, "--no-rerun")
    assert r.returncode == 1
    text = (setup["sdir"] / "review-0001.md").read_text()
    assert "Review: INVALID (refs changed during the review)" in text
    assert "; refs changed;" in text


def test_a_new_stash_is_invalid(tmp_path, monkeypatch, setup):
    act(tmp_path, monkeypatch, 'git stash store -m t "$(git -c user.name=t -c user.email=t@t stash create)"\n')
    r = run(SID, "--no-rerun")
    assert r.returncode == 1
    head = (setup["sdir"] / "review-0001.md").read_text().splitlines()[1]
    assert head == "Review: INVALID (refs, stash changed during the review)"


# ── INCOMPLETE ───────────────────────────────────────────────────────────────────────────────
def test_answer_without_recommendation_is_incomplete(tmp_path, setup):
    bad = "\n".join(ln for ln in ANSWER.splitlines() if not ln.startswith("Recommendation:")) + "\n"
    setup["answer"].write_text(bad)
    r = run(SID, "--no-rerun")
    assert r.returncode == 3
    text = (setup["sdir"] / "review-0001.md").read_text()
    assert text.splitlines()[1] == "Review: INCOMPLETE (the answer is not in the fixed shape)"
    assert text.split("---\n", 1)[1] == bad
    assert [e["event"] for e in ledger(tmp_path) if "review" in e["event"]] == ["review_failed"]


def test_pi_exit_code_is_incomplete(tmp_path, monkeypatch, setup):
    monkeypatch.setenv("FAKE_PI_EXIT", "1")
    act(tmp_path, monkeypatch, "echo 'boom: no credits' >&2\n")
    r = run(SID, "--no-rerun")
    assert r.returncode == 3
    assert (setup["sdir"] / "review-0001.md").read_text().splitlines()[1] == \
        "Review: INCOMPLETE (pi exited 1: boom: no credits)"


def test_timeout_stops_the_whole_process_group(tmp_path, monkeypatch, setup, capsys):
    sys.path.insert(0, str(ROOT / "lib"))
    from pilead import cli, reviewer
    monkeypatch.setattr(reviewer, "_effective_timeout", lambda t: 2)
    monkeypatch.setenv("FAKE_PI_SLEEP", "30")
    pidfile = tmp_path / "pi.pid"
    act(tmp_path, monkeypatch, f'echo $PPID > "{pidfile}"\n')
    t0 = time.monotonic()
    rc = cli.main(["review", SID, "--no-rerun", "--timeout", "60"])
    assert time.monotonic() - t0 < 25
    assert rc == 3
    assert (setup["sdir"] / "review-0001.md").read_text().splitlines()[1] == \
        "Review: INCOMPLETE (timed out after 60s)"
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)  # nothing is left in the stub's process group (its `sleep` included)
    capsys.readouterr()


# ── refusals: one line, nothing written, nothing started ─────────────────────────────────────
def refused(tmp_path, args, code, line, env=None):
    before = tree_bytes(home(tmp_path))
    r = run(*args, env=env)
    assert r.returncode == code, r.stderr
    assert r.stderr == line + "\n"
    assert r.stdout == ""
    assert tree_bytes(home(tmp_path)) == before
    assert not (home(tmp_path) / "sessions" / SID / "review-0001").exists()
    assert not (tmp_path / "fake-pi.log").exists()
    return r


def test_refused_without_a_report(tmp_path, setup):
    (setup["sdir"] / "report-0001.md").unlink()
    refused(tmp_path, [SID], 2, f"review: no report yet for {SID} packet 0001")


def test_refused_without_a_worktree(tmp_path, setup):
    import shutil
    shutil.rmtree(setup["wt"])
    refused(tmp_path, [SID], 2, f"review: {SID} has no worktree on disk")


def test_refused_without_the_leads_model(tmp_path, monkeypatch, setup):
    msg = "review: this lead's model could not be read (no session log, or no answer in it yet) — pass --model"
    monkeypatch.delenv("PI_LEAD_SID")
    refused(tmp_path, [SID], 2, msg)  # no caller at all
    monkeypatch.setenv("PI_LEAD_SID", LEAD)
    make_lead(tmp_path, entries=[user("only a question so far")])
    setup["log"].unlink()  # the log with an answer is gone; a log without one replaces it
    refused(tmp_path, [SID], 2, msg)


def test_refused_without_the_leads_log(tmp_path, monkeypatch, setup):
    setup["log"].unlink()
    refused(tmp_path, [SID, "--model", LEAD_MODEL], 2,
            "review: this lead's session log was not found — pass --fresh to review without the lead's context")


def test_refused_when_the_repository_cannot_be_measured(tmp_path, setup):
    import shutil
    shutil.rmtree(setup["wt"] / ".git")
    r = subprocess.run(["git", "-C", str(setup["wt"]), "write-tree"], capture_output=True, text=True)
    first = r.stderr.strip().splitlines()[0]
    refused(tmp_path, [SID], 3, f"review: the repository could not be measured ({first}) — nothing was started")


def test_refused_with_unstaged_changes(tmp_path, setup):
    (setup["wt"] / "seed.txt").write_text("edited, not staged\n")
    refused(tmp_path, [SID], 3, "review: 1 tracked file(s) have unstaged changes — the staged tree is not what a "
                                "test run would see")


def test_refused_with_nothing_staged(tmp_path, setup):
    git(setup["wt"], "reset", "-q")
    refused(tmp_path, [SID], 3, f"review: nothing is staged in {setup['wt']}")


def test_refused_with_a_large_diff(tmp_path, setup):
    (setup["wt"] / "big.txt").write_text(("x" * 99 + "\n") * 16000)
    git(setup["wt"], "add", "big.txt")
    kb = (len(measured(setup["wt"])[2]) + 1023) // 1024
    refused(tmp_path, [SID], 3, f"review: the staged diff is {kb} KB — pass --large to review it anyway")


def test_refused_when_it_does_not_fit_the_window(tmp_path, setup):
    models_json(tmp_path, window="10K")
    refused(tmp_path, [SID], 3, f"review: the lead's context (12k) and the diff do not fit {LEAD_MODEL}'s window "
                                "(10k) — pass --fresh")


def test_refused_when_pi_is_not_on_path(tmp_path, setup):
    only = tmp_path / "only-git"
    only.mkdir()
    real_git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
    (only / "git").symlink_to(real_git)
    env = {**os.environ, "PATH": str(only)}
    refused(tmp_path, [SID], 3, "review: pi was not found on PATH", env=env)


def test_model_and_fresh_run_with_no_lead(tmp_path, monkeypatch, setup):
    monkeypatch.delenv("PI_LEAD_SID")
    r = run(SID, "--no-rerun", "--model", "dsflash", "--fresh")
    assert r.returncode == 0, r.stderr
    rec, = stub_log(tmp_path)
    assert rec["argv"] == expected_argv(setup, model="deepseek/deepseek-flash", fork=False)
    assert "without the lead's context" in r.stderr
    assert not rec["files"][str(setup["work"] / "prompt.md")].startswith("You are a copy")


# ── dry run, a second review, --json, the lead's files, a bad id ─────────────────────────────
def test_dry_run_starts_and_writes_nothing(tmp_path, setup):
    before = tree_bytes(home(tmp_path))
    r = run(SID, "--dry-run")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0].startswith(f"pilead review: starting a headless pi on {LEAD_MODEL}")
    assert lines[1:] == ["pi"] + expected_argv(setup)
    assert tree_bytes(home(tmp_path)) == before
    assert not (tmp_path / "fake-pi.log").exists()


def test_second_review_keeps_the_first_as_previous(tmp_path, setup):
    assert run(SID, "--no-rerun").returncode == 0
    first = (setup["sdir"] / "review-0001.md").read_text()
    setup["answer"].write_text(ANSWER.replace("fix-list — one item", "commit — nothing"))
    assert run(SID, "--no-rerun").returncode == 0
    assert (setup["work"] / "previous-1.md").read_text() == first
    assert "Recommendation: commit — nothing" in (setup["sdir"] / "review-0001.md").read_text()


def test_json_keys(tmp_path, setup):
    r = run(SID, "--no-rerun", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert set(d) == {"outcome", "valid", "model", "context", "tree_start", "tree_end", "verify", "rerun",
                      "recommendation", "findings", "cost", "file"}
    assert d["outcome"] == "VALID" and d["valid"] is True and d["context"] == "lead" and d["verify"] == "COUNTS-MATCH"
    assert d["findings"][0] == {"n": 1, "where": "src/alpha.py:1", "level": "should-fix",
                                "text": "the file holds only a placeholder line."}
    assert d["cost"] is None and d["file"] == str(setup["sdir"] / "review-0001.md")


def test_the_leads_files_are_untouched(tmp_path, setup):
    h = home(tmp_path)
    files = [setup["log"], h / "leads" / f"{LEAD}.json", h / "leads" / f"{LEAD}.inbox.md"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    assert run(SID, "--no-rerun").returncode == 0
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in files] == before
    assert not (h / "usage").exists()  # no usage cache written for the lead either


def test_a_bad_session_id_touches_nothing(tmp_path):
    before = tree_bytes(tmp_path)
    r = pilead("review", "../../x", check=False)
    assert r.returncode == 2
    assert "not usable" in r.stderr
    assert tree_bytes(tmp_path) == before

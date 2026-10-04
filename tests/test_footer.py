"""The packet footer (state.footer): the report path, the one command an executor runs after its report
(`pilead diff`), and the exact closing line naming the page that command writes. Driven through bin/pilead
with the fake `pi` and the recording `osascript` stub conftest puts first on PATH; homes, worktrees and
repositories are all under the test's temp directory. No real tab is opened and the real pi never runs."""
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_close_variants import tree

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import shim, state  # noqa: E402

LEAD_TEXT = "GOAL: do the thing\n\nsecond paragraph  \n\n\n"
# git variables the shim refuses on or that would point git somewhere else: never inherited by a test's git
CLEAN = set(shim.CFG_VARS) | set(shim.CMD_VARS) | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "PILEAD_SHIM_DIR"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "my home"  # a space in the home: the command must quote it
    monkeypatch.setenv("PI_LEAD_HOME", str(h))
    return h


def pilead(*args, check=True):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def spawn(tmp_path, worktree):
    (tmp_path / "packet.md").write_text(LEAD_TEXT)
    pilead("lead-start", "lead-1", "--project", "proj")
    r = pilead("spawn", str(worktree), "topic", str(tmp_path / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    return r.stdout.split()[1]  # "spawned <sid> ..."


def git_env(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(HOME=str(tmp_path / "git-home"), GIT_CONFIG_NOSYSTEM="1")
    return env


def repo_with_one_staged_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = git_env(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, env=env, check=True, capture_output=True)
    return repo


def footer_command(packet_text):
    """The command line the footer names: the line after `AFTER writing it, run:`, as an argument list."""
    lines = packet_text.splitlines()
    return shlex.split(lines[lines.index("AFTER writing it, run:") + 1])


def run_footer_command(tmp_path, home, sid):
    """Run the footer's command as an executor would: its git shim first on PATH, then the package's bin/."""
    argv = footer_command(state.packet_path(home, sid, 1).read_text())
    shim_dir = state.session_dir(home, sid) / "shim"
    assert (shim_dir / "git").is_file()
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}  # the test's own HOME: python's cache lands there
    env.update(PILEAD_SHIM_DIR=str(shim_dir),
               PATH=os.pathsep.join([str(shim_dir), str(ROOT / "bin"), env.get("PATH", "")]))
    return argv, subprocess.run(argv, capture_output=True, text=True, env=env)


# ── the text ────────────────────────────────────────────────────────────────────────────────────
def test_footer_text_whole_for_a_home_with_a_space(tmp_path):
    home = tmp_path / "a home"
    sdir = home / "sessions" / "topic-101010"
    address = (sdir / "diff-0003.html").resolve().as_uri()
    assert "%20" in address
    expected = (
        "\n"
        "\n"
        "---\n"
        "(pi-lead — do not remove or reword this section)\n"
        "\n"
        "REPORT: write your full report (the outcome in one plain sentence first, then the TL;DR block) to\n"
        f"  {sdir}/report-0003.md\n"
        "AFTER writing it, run:\n"
        f"  pilead diff topic-101010 --home '{home}'\n"
        "(it makes the review page of your staged changes; do not open it and do not attach it anywhere; run it once)\n"
        "Then end your final turn with EXACTLY this one line, nothing after it:\n"
        f"      ✅ [pi-lead] — staged + report written — diff: {address} — idle, awaiting the lead's review.\n")
    assert state.footer(home, "topic-101010", 3) == expected


# ── spawn and send ──────────────────────────────────────────────────────────────────────────────
def test_spawn_and_send_end_each_packet_with_its_own_footer(tmp_path, home):
    (tmp_path / "wt").mkdir()
    sid = spawn(tmp_path, tmp_path / "wt")
    p1 = state.packet_path(home, sid, 1).read_text()
    assert p1 == LEAD_TEXT.rstrip("\n") + state.footer(home, sid, 1)
    state.report_path(home, sid, 1).write_text("Done.\nStatus: clean\n")
    state.write_status(home, sid, "reported")
    (tmp_path / "p2.md").write_text("GOAL: the follow-up\n")
    assert pilead("send", sid, str(tmp_path / "p2.md")).stdout.strip() == f"sent {sid} packet 0002"
    p2 = state.packet_path(home, sid, 2).read_text()
    assert p2 == "GOAL: the follow-up" + state.footer(home, sid, 2)
    assert "report-0002.md" in p2 and "diff-0002.html" in p2 and "report-0001.md" not in p2


# ── the command the footer names ────────────────────────────────────────────────────────────────
def test_the_footer_command_writes_the_page_its_closing_line_names(tmp_path, home):
    repo = repo_with_one_staged_file(tmp_path)
    sid = spawn(tmp_path, repo)
    ledger = home / "ledger.jsonl"
    before = ledger.read_bytes()
    argv, r = run_footer_command(tmp_path, home, sid)
    assert argv == ["pilead", "diff", sid, "--home", str(home)]
    assert r.returncode == 0, r.stderr
    page = state.session_dir(home, sid) / "diff-0001.html"
    assert page.is_file() and "a.txt" in page.read_text()
    out = r.stdout.splitlines()
    assert out == [str(page), page.resolve().as_uri()]
    closing = state.packet_path(home, sid, 1).read_text().splitlines()[-1]
    assert closing.split(" — diff: ")[1].split(" — ")[0] == out[1]
    assert ledger.read_bytes() == before


def test_the_footer_command_outside_a_repository_exits_as_pilead_diff_does(tmp_path, home):
    wt = tmp_path / "wt"
    wt.mkdir()
    sid = spawn(tmp_path, wt)
    sdir = state.session_dir(home, sid)
    outside = lambda: {k: v for k, v in tree(tmp_path).items()  # noqa: E731
                       if not Path(tmp_path, k).is_relative_to(sdir)}
    before = outside()
    _, r = run_footer_command(tmp_path, home, sid)
    assert outside() == before
    # what `pilead diff` does in a worktree that is not a repository: git's error is dropped, the page
    # is written (empty), and it exits 0 with its two lines
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [str(sdir / "diff-0001.html"), (sdir / "diff-0001.html").resolve().as_uri()]

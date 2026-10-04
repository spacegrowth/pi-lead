"""`pilead report <sid> [--packet N]` (lib/pilead/verbs/report.py), driven through bin/pilead: an executor's whole
report in a green frame. It reads and prints; home and the worktree's index are the same after every run."""
import json
import os
import pty
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
SID = "alpha-20261004-101010"
L1 = "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d"
BAR = "═" * 74
REPORT = "Did the thing; 3 tests, staged.\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: x\n\n## Detail\n"


def _content(p):
    if not p.is_file():
        return None
    try:
        return p.read_bytes()
    except PermissionError:
        return ("unreadable", p.stat().st_mode, p.stat().st_mtime_ns)


def tree(root):
    return {str(p.relative_to(root)): _content(p) for p in sorted(Path(root).rglob("*"))}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def wt(tmp_path):
    w = tmp_path / "wt"
    w.mkdir()
    git(w, "init", "-q")
    git(w, "commit", "-q", "--allow-empty", "-m", "init")
    return w


@pytest.fixture
def home(tmp_path, wt):
    h = tmp_path / "a" / "b" / "home"
    (h / "leads").mkdir(parents=True)
    (h / "leads" / f"{L1}.json").write_text(json.dumps({"sid": L1, "project": "proj"}))
    d = h / "sessions" / SID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"sid": SID, "lead": L1, "worktree": str(wt), "packets": 2}))
    (d / "report-0001.md").write_text("First report.\n")
    (d / "report-0002.md").write_text(REPORT + "\n\n   \n")
    return h


def report(home, *args, env=None, wt=None):
    before = tree(home)
    index = git(wt, "write-tree") if wt else None
    r = subprocess.run([str(PILEAD), "report", *args, "--home", str(home)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    assert tree(home) == before, "report wrote under home"
    if wt:
        assert git(wt, "write-tree") == index
    return r


def block(home, n, text):
    path = home / "sessions" / SID / f"report-{n:04d}.md"
    return (f"{BAR}\n  ✅ REPORT READY   ·   {SID}   ·   packet {n:04d}\n  {path}\n{BAR}\n"
            f"{text.rstrip()}\n{BAR}\n")


STAGED_LINE = f"  📄 review the staged diff: pilead diff {SID} --open\n"


# ── the rows ───────────────────────────────────────────────────────────────────────────────────────────

def test_the_current_packet_by_default(home, wt):
    r = report(home, SID, wt=wt)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 2, REPORT), "")


def test_an_earlier_packet(home, wt):
    r = report(home, SID, "--packet", "1", wt=wt)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 1, "First report.\n"), "")


def test_no_such_session(home):
    r = report(home, "nobody-20261004-000000")
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session nobody-20261004-000000\n")


def test_an_id_that_is_not_usable(home, tmp_path):
    (home / "x").mkdir()
    (home / "x" / "meta.json").write_text(json.dumps({"packets": 1}))
    (home / "x" / "report-0001.md").write_text("planted\n")
    before = tree(tmp_path)
    r = report(home, "../x")
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr.startswith("pilead: session id '../x' is not usable: ") and len(r.stderr.splitlines()) == 1
    assert tree(tmp_path) == before


@pytest.mark.parametrize("value", ["0", "-1"])
def test_packet_below_one(home, value):
    r = report(home, SID, "--packet", value)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: report: --packet must be 1 or more\n")


def test_packet_not_a_number(home):
    r = report(home, SID, "--packet", "two")
    assert (r.returncode, r.stdout) == (2, "")
    assert "argument --packet: invalid int value: 'two'" in r.stderr


@pytest.mark.parametrize("packets", ["2", None, True, 0])
def test_the_current_packet_cannot_be_read(home, packets):
    mp = home / "sessions" / SID / "meta.json"
    meta = json.loads(mp.read_text())
    meta["packets"] = packets
    mp.write_text(json.dumps(meta))
    r = report(home, SID)
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr == f"pilead: report: the current packet of '{SID}' cannot be read — give --packet N\n"
    assert report(home, SID, "--packet", "1").returncode == 0


def test_no_report_yet(home):
    r = report(home, SID, "--packet", "3")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", f"pilead: no report yet for '{SID}' packet 0003\n")


def test_a_report_that_cannot_be_read(home):
    p = home / "sessions" / SID / "report-0002.md"
    p.chmod(0o000)
    try:
        r = report(home, SID)
    finally:
        p.chmod(0o644)
    assert (r.returncode, r.stdout, r.stderr) == (1, "", f"pilead: the report of '{SID}' packet 0002 cannot be read\n")


def test_a_report_that_is_a_directory(home):
    p = home / "sessions" / SID / "report-0003.md"
    p.mkdir()
    r = report(home, SID, "--packet", "3")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", f"pilead: the report of '{SID}' packet 0003 cannot be read\n")


def test_bytes_that_are_not_utf8(home):
    (home / "sessions" / SID / "report-0002.md").write_bytes(b"ok \xff\xfe bad\n")
    r = report(home, SID)
    assert r.returncode == 0 and "Traceback" not in r.stderr
    assert "ok �� bad\n" in r.stdout


# ── names ─────────────────────────────────────────────────────────────────────────────────────────────

def test_a_project_names_a_lead_not_an_executor(home):
    r = report(home, "proj")
    assert (r.returncode, r.stdout, r.stderr) == (2, "", f"pilead: unknown session {L1}\n")


def test_a_unique_prefix_of_eight(home, wt):
    r = report(home, SID[:8], wt=wt)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 2, REPORT), "")


# ── colour ────────────────────────────────────────────────────────────────────────────────────────────

def test_no_colour_when_not_a_terminal_or_no_color(home, wt):
    for env in ({}, {"NO_COLOR": "1"}):
        r = report(home, SID, env=env, wt=wt)
        assert "\033[" not in r.stdout


def _on_a_terminal(home, env):
    """stdout a pseudo-terminal: the bytes the verb wrote there."""
    master, slave = pty.openpty()
    base = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    p = subprocess.Popen([str(PILEAD), "report", SID, "--home", str(home)], stdout=slave, stderr=subprocess.PIPE,
                         env={**base, **env})
    os.close(slave)
    out = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    p.wait()
    os.close(master)
    return p.returncode, out.decode("utf-8", "replace")


def test_colour_on_a_terminal_and_none_with_no_color(home):
    before = tree(home)
    rc, out = _on_a_terminal(home, {})
    assert rc == 0
    assert f"\033[32m\033[1m{BAR}\033[0m" in out
    assert f"\033[32m\033[1m  ✅ REPORT READY   ·   {SID}   ·   packet 0002\033[0m" in out
    assert f"\033[2m  {home / 'sessions' / SID / 'report-0002.md'}\033[0m" in out
    rc, out = _on_a_terminal(home, {"NO_COLOR": "1"})
    assert rc == 0 and "\033[" not in out and "REPORT READY" in out
    assert tree(home) == before


# ── the staged-diff line ──────────────────────────────────────────────────────────────────────────────

def test_the_staged_diff_line_for_a_worktree_with_a_staged_file(home, wt):
    (wt / "f.txt").write_text("x\n")
    git(wt, "add", "f.txt")
    r = report(home, SID, wt=wt)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 2, REPORT) + STAGED_LINE, "")


def test_no_line_for_a_clean_worktree_with_unstaged_changes(home, wt):
    (wt / "f.txt").write_text("x\n")  # untracked, not staged
    r = report(home, SID, wt=wt)
    assert r.stdout == block(home, 2, REPORT)


def test_no_line_for_a_worktree_that_is_gone(home, tmp_path):
    mp = home / "sessions" / SID / "meta.json"
    meta = json.loads(mp.read_text())
    meta["worktree"] = str(tmp_path / "gone")
    mp.write_text(json.dumps(meta))
    r = report(home, SID)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 2, REPORT), "")


def test_no_line_without_a_worktree(home):
    mp = home / "sessions" / SID / "meta.json"
    meta = json.loads(mp.read_text())
    del meta["worktree"]
    mp.write_text(json.dumps(meta))
    assert report(home, SID).stdout == block(home, 2, REPORT)


@pytest.mark.parametrize("rc", [128, 2])
def test_no_line_for_a_git_that_fails(home, wt, tmp_path, rc):
    """A `git` first on PATH that answers --version as git does and fails at everything else."""
    (wt / "f.txt").write_text("x\n")
    git(wt, "add", "f.txt")
    stub_dir = tmp_path / "gitstub"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "git version 2.99.0"; exit 0; fi\n'
                    f'echo "$@" >> "{tmp_path}/gitstub.log"\nexit {rc}\n')
    stub.chmod(0o755)
    r = report(home, SID, env={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}, wt=wt)
    assert (r.returncode, r.stdout, r.stderr) == (0, block(home, 2, REPORT), "")
    assert "diff --cached --quiet" in (tmp_path / "gitstub.log").read_text()

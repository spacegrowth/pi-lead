"""`state.home_dir` returns an absolute path — `~` expanded, relative made absolute against the current directory,
symlinks NOT resolved — from `--home`, `$PI_LEAD_HOME` and the default alike, so a packet footer names the same place from
any directory. Driven through bin/pilead with the fake `pi` and the recording `osascript` stub conftest puts first on PATH;
everything is under the test's temp directory. No real tab is opened and the real pi never runs."""
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import state  # noqa: E402


def test_a_relative_pi_lead_home_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PI_LEAD_HOME", "rel/home")
    got = state.home_dir()
    assert got.is_absolute() and got == Path(os.path.abspath(tmp_path / "rel" / "home"))


def test_a_relative_home_flag_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PI_LEAD_HOME", "ignored/by/the/flag")
    got = state.home_dir("flag-home")
    assert got.is_absolute() and got == Path(os.path.abspath(tmp_path / "flag-home"))
    assert state.home_dir("./a/../b") == Path(os.path.abspath(tmp_path / "b"))


def test_tilde_is_expanded_from_every_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PI_LEAD_HOME", "~/from-env")
    assert state.home_dir() == tmp_path / "from-env"
    assert state.home_dir("~/from-flag") == tmp_path / "from-flag"
    monkeypatch.delenv("PI_LEAD_HOME")
    assert state.home_dir() == tmp_path / ".pi-lead" and state.home_dir().is_absolute()


def test_an_absolute_value_is_unchanged_and_symlinks_are_not_resolved(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("PI_LEAD_HOME", str(link))
    assert state.home_dir() == link and str(state.home_dir()) == str(link)
    assert state.home_dir(str(tmp_path / "plain")) == tmp_path / "plain"


def test_spawn_with_a_relative_home_writes_an_absolute_footer(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    env = dict(os.environ, PI_LEAD_HOME="rel-home")  # relative to the directory pilead runs in
    env.pop("PI_LEAD_SID", None)
    env.pop("PI_SESSION_ID", None)

    def run(*args):
        r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, cwd=tmp_path, env=env)
        assert r.returncode == 0, r.stderr
        return r

    run("lead-start", "lead-1", "--project", "proj")
    r = run("spawn", str(work), "topic", str(tmp_path / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid = r.stdout.split()[1]
    home = tmp_path / "rel-home"
    text = (home / "sessions" / sid / "packet-0001.md").read_text()
    lines = text.splitlines()
    report_line = lines[lines.index("REPORT: write your full report (the outcome in one plain sentence first, then the TL;DR block) to") + 1]
    assert report_line.strip() == f"{home}/sessions/{sid}/report-0001.md"
    argv = shlex.split(lines[lines.index("AFTER writing it, run:") + 1])
    assert argv[:3] == ["pilead", "diff", sid] and argv[3] == "--home" and argv[4] == str(home)
    assert Path(argv[4]).is_absolute()

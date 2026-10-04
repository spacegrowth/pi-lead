"""`pilead board [--open] [--out PATH] [--lead SID] [--json] [--live [on|off]]`, driven through bin/pilead over
homes the test builds, with an `open` (and `xdg-open`) stub first on PATH that logs its arguments. The
conftest stubs (`osascript`, `pi`, `tmux`) apply to every test; nothing here opens a real tab or a real page."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_autoclose import session as landed_session
from test_close_variants import tree
from test_list import H, lead, session

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
REFRESH = re.compile(r'<meta http-equiv="refresh" content="([^"]*)"')


@pytest.fixture
def openlog(tmp_path, monkeypatch):
    """`open` and `xdg-open` stubs at the FRONT of PATH; each appends its arguments to the returned log."""
    log = tmp_path / "open.log"
    bindir = tmp_path / "openbin"
    bindir.mkdir()
    for name in ("open", "xdg-open"):
        (bindir / name).write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$OPEN_LOG"\n')
        (bindir / name).chmod(0o755)
    monkeypatch.setenv("OPEN_LOG", str(log))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return log


def board(*args, env=None):
    return subprocess.run([sys.executable, str(PILEAD), "board", *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def build(tmp_path):
    """Two leads, an executor of each, and one with no lead."""
    lead(tmp_path, "lead-a", "pa")
    lead(tmp_path, "lead-b", "pb")
    session(tmp_path, "s-a", lead_sid="lead-a")
    session(tmp_path, "s-b", lead_sid="lead-b")
    session(tmp_path, "s-u", lead_sid=None)
    return H(tmp_path)


def config(home, **kv):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(kv))


def page_of(home):
    return (home / "board.html").read_text()


def no_tmp(root):
    assert [p for p in Path(root).rglob("*") if p.name.endswith(".tmp")] == []


def without_generated(d):
    return {k: v for k, v in d.items() if k != "generated"}


def row_ids(d):
    return sorted(e["session_id"] for e in d["executors"])


def test_the_plain_board_writes_the_page_and_no_data_file(tmp_path):
    home = build(tmp_path)
    r = board()
    assert r.returncode == 0, r.stderr
    assert (home / "board.html").is_file() and not (home / "board.json").exists()
    assert not REFRESH.search(page_of(home))
    assert 'id="board-updated"' not in page_of(home)
    no_tmp(tmp_path)


def test_live_writes_both_and_the_data_file_is_what_json_prints(tmp_path):
    home = build(tmp_path)
    config(home, board_refresh_seconds=30)
    r = board("--live")
    assert r.returncode == 0, r.stderr
    page = page_of(home)
    assert REFRESH.search(page).group(1) == "30"
    assert re.search(r'<span id="board-updated" class="upd"[^>]*>updated \d\d:\d\d:\d\d</span>', page)
    data = json.loads((home / "board.json").read_text())
    j = board("--json")
    assert j.returncode == 0
    assert without_generated(json.loads(j.stdout)) == without_generated(data)
    no_tmp(tmp_path)


def test_live_on_is_live(tmp_path):
    home = build(tmp_path)
    assert board("--live", "on").returncode == 0
    assert REFRESH.search(page_of(home)) and (home / "board.json").is_file()


def test_out_makes_the_directories_and_writes_there_not_under_home(tmp_path):
    home = build(tmp_path)
    out = tmp_path / "a" / "b" / "page.html"
    r = board("--out", str(out))
    assert r.returncode == 0, r.stderr
    assert out.is_file() and not (home / "board.html").exists() and not (tmp_path / "a" / "b" / "page.json").exists()
    r = board("--out", str(out), "--live")
    assert r.returncode == 0, r.stderr
    assert REFRESH.search(out.read_text())
    assert json.loads((tmp_path / "a" / "b" / "page.json").read_text())["executors"]
    assert not (home / "board.json").exists()
    no_tmp(tmp_path)


def test_out_expands_the_tilde(tmp_path):
    build(tmp_path)
    fake_home = tmp_path / "userhome"
    fake_home.mkdir()
    r = board("--out", "~/sub/p.html", env={"HOME": str(fake_home)})
    assert r.returncode == 0, r.stderr
    assert (fake_home / "sub" / "p.html").is_file()


def test_lead_keeps_that_leads_executors_and_the_unowned_ones(tmp_path):
    build(tmp_path)
    r = board("--lead", "lead-a", "--json")
    assert r.returncode == 0, r.stderr
    assert row_ids(json.loads(r.stdout)) == ["s-a", "s-u"]
    assert row_ids(json.loads(board("--json").stdout)) == ["s-a", "s-b", "s-u"]


def test_an_unusable_lead_exits_2_and_changes_nothing(tmp_path):
    home = build(tmp_path)
    before = tree(tmp_path)
    r = board("--lead", "../../x")
    assert r.returncode == 2
    assert len(r.stderr.strip().splitlines()) == 1 and r.stdout == ""
    assert tree(tmp_path) == before
    assert not (home / "board.html").exists()


def test_a_leads_project_and_its_id_give_the_same_document(tmp_path):
    build(tmp_path)
    by_project = board("--lead", "pa", "--json")
    by_id = board("--lead", "lead-a", "--json")
    assert by_project.returncode == by_id.returncode == 0
    assert without_generated(json.loads(by_project.stdout)) == without_generated(json.loads(by_id.stdout))


def test_an_ambiguous_prefix_gives_the_candidates_message(tmp_path):
    build(tmp_path)
    lead(tmp_path, "lead-abc111", "px")
    lead(tmp_path, "lead-abc222", "py")
    before = tree(tmp_path)
    r = board("--lead", "lead-abc", "--json")
    assert r.returncode == 2
    assert "lead-abc111" in r.stderr and "lead-abc222" in r.stderr
    assert r.stdout == "" and tree(tmp_path) == before


def test_json_prints_json_and_nothing_else_and_writes_no_page(tmp_path):
    home = build(tmp_path)
    r = board("--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)  # the whole of stdout
    assert r.stdout.startswith('{\n  "') and {"leads", "executors", "warnings", "version"} <= set(d)
    assert not (home / "board.html").exists() and not (home / "board.json").exists()


def test_json_puts_the_park_lines_of_a_sweep_on_stderr(tmp_path):
    landed_session(tmp_path, "s-landed")
    r = board("--json", env={"PI_LEAD_SID": "lead-1"})
    assert r.returncode == 0, r.stderr
    json.loads(r.stdout)
    assert "s-landed" in r.stderr
    assert not (H(tmp_path) / "board.html").exists()


def test_the_park_lines_follow_the_two_lines_on_stdout(tmp_path):
    landed_session(tmp_path, "s-landed")
    r = board(env={"PI_LEAD_SID": "lead-1"})
    lines = r.stdout.splitlines()
    assert lines[0] == str(H(tmp_path) / "board.html") and lines[1].startswith("file://")
    assert "s-landed" in "\n".join(lines[2:]) and r.stderr == ""


def test_live_off_removes_the_data_file_and_says_so(tmp_path):
    home = build(tmp_path)
    board("--live")
    data = home / "board.json"
    assert data.is_file()
    r = board("--live", "off")
    assert (r.returncode, r.stdout, r.stderr) == (0, f"live mode off ({data} removed)\n", "")
    assert not data.exists()
    r = board("--live", "off")
    assert (r.returncode, r.stdout) == (0, f"live mode was already off (no {data})\n")
    config(home, board_live=True)
    r = board("--live", "off")
    assert r.stdout == ("config board_live is true and keeps live mode on — "
                        "set it to false to turn live mode off\n")
    data.write_text("{}")
    r = board("--live", "off")
    assert r.stdout == ("data file removed, but config board_live is true and keeps live mode on — "
                        "set it to false to turn live mode off\n")
    assert not data.exists()


def test_live_off_with_out_removes_the_data_file_beside_that_page(tmp_path):
    home = build(tmp_path)
    out = tmp_path / "x" / "p.html"
    board("--out", str(out), "--live")
    assert (tmp_path / "x" / "p.json").is_file()
    r = board("--out", str(out), "--live", "off")
    assert r.stdout == f"live mode off ({tmp_path / 'x' / 'p.json'} removed)\n"
    assert out.is_file() and not (tmp_path / "x" / "p.json").exists() and not (home / "board.html").exists()


@pytest.mark.parametrize("args", [
    ("--json", "--open"), ("--json", "--out", "p.html"), ("--json", "--live"), ("--json", "--live", "off"),
    ("--live", "off", "--open"), ("--live", "off", "--json"), ("--live", "off", "--lead", "lead-a"),
])
def test_each_refused_pair_exits_2_with_one_line_and_changes_nothing(tmp_path, args, openlog):
    build(tmp_path)
    board("--live")  # something to remove, if a refusal let it
    before = tree(tmp_path)
    r = board(*args)
    assert r.returncode == 2
    assert r.stdout == "" and len(r.stderr.strip().splitlines()) == 1
    assert tree(tmp_path) == before
    assert not openlog.exists()


def test_board_live_in_config_makes_the_plain_board_live(tmp_path):
    home = build(tmp_path)
    config(home, board_live=True)
    assert board().returncode == 0
    assert REFRESH.search(page_of(home)).group(1) == "10" and (home / "board.json").is_file()


def test_the_data_file_is_neither_written_nor_removed_by_a_plain_board_with_live_mode_off(tmp_path):
    home = build(tmp_path)
    board("--live")
    (home / "config.json").write_text("{}")
    assert board().returncode == 0
    assert (home / "board.json").is_file() and REFRESH.search(page_of(home)) is None


def test_the_refresh_seconds_come_from_config_and_bad_values_are_ignored(tmp_path):
    home = build(tmp_path)
    config(home, board_refresh_seconds=30)
    board("--live")
    assert REFRESH.search(page_of(home)).group(1) == "30"
    for bad in (0, 2.5, -1, "10"):
        config(home, board_refresh_seconds=bad)
        board("--live")
        assert REFRESH.search(page_of(home)).group(1) == "10", bad


def test_a_page_that_cannot_be_written_exits_1_and_leaves_nothing(tmp_path):
    home = build(tmp_path)
    (tmp_path / "plain").write_text("a file where the directory should be")
    for extra in ((), ("--live",)):
        r = board("--out", str(tmp_path / "plain" / "page.html"), *extra)
        assert r.returncode == 1
        assert r.stdout == "" and len(r.stderr.strip().splitlines()) == 1
        assert str(tmp_path / "plain" / "page.html") in r.stderr
        assert not (tmp_path / "plain" / "page.json").exists()
    no_tmp(tmp_path)
    assert (tmp_path / "plain").read_text() == "a file where the directory should be"
    assert not (home / "board.json").exists()


def test_a_page_named_like_its_data_file_is_refused_when_live(tmp_path):
    build(tmp_path)
    before = tree(tmp_path)
    r = board("--out", str(tmp_path / "p.json"), "--live")
    assert r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
    assert tree(tmp_path) == before


def test_stdout_is_the_path_then_the_address_and_open_runs_once(tmp_path, openlog):
    home = build(tmp_path)
    out = tmp_path / "o" / "p.html"
    r = board("--out", str(out), "--open")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [str(out), out.resolve().as_uri()]
    assert openlog.read_text().splitlines() == [str(out)]
    r = board()
    assert r.stdout.splitlines() == [str(home / "board.html"), (home / "board.html").resolve().as_uri()]
    assert openlog.read_text().splitlines() == [str(out)]  # no --open, no second run


def test_a_live_board_with_the_board_data_after_a_wait_has_a_newer_generated(tmp_path):
    home = build(tmp_path)
    board("--live")
    first = json.loads((home / "board.json").read_text())["generated"]
    time.sleep(1.1)
    board("--live")
    assert json.loads((home / "board.json").read_text())["generated"] > first

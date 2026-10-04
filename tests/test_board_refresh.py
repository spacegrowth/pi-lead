"""`review.refresh_live_board(home)` and the verbs that call it (lib/pilead/verbs/{list,check,send,spawn,close,
turn_end}.py). It rewrites `<home>/board.html` and `<home>/board.json` when live mode is on, and changes nothing
else of home; it never raises and prints nothing; a verb's exit code, stdout and files are the same whether it
runs or raises. In-process runs use the conftest stubs (`osascript`, `pi`): no real tab is touched."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_autoclose import session as landed_session
from test_board_data import full_home, hm
from test_close_variants import tree
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

import board_render  # noqa: E402
from pilead import autoclose, cli, review  # noqa: E402

LIVE_FILES = ("board.html", "board.json")


def live_home(tmp_path, how):
    """A busy home in which a status would change (s-done: stored busy, its report is there) and a usage
    cache would be written (s-done has a pi log); live mode on by `how`: "sidecar" or "config"."""
    full_home(tmp_path)
    from test_list import session
    session(tmp_path, "s-done", status="busy", report=True)
    write_log("s-done", tmp_path / "wt-s-done", [assistant(u(10, 5, 1000, 0))])
    home = hm(tmp_path)
    if how == "sidecar":
        (home / "board.json").write_text("{}")
    else:
        (home / "config.json").write_text(json.dumps({"board_live": True}))
    (home / "board.html").write_text("old page")
    return home


def no_tmp(home):
    assert [p for p in Path(home).rglob("*") if p.name.endswith(".tmp")] == []


def others(t):
    """A `tree` without the two live files and without directories' own times (a file made and renamed in a
    directory changes that directory's time)."""
    return {k: v for k, v in t.items() if k not in LIVE_FILES and v[0] != "dir"}


def test_with_live_mode_off_nothing_is_written(tmp_path):
    full_home(tmp_path)
    home = hm(tmp_path)
    before = tree(home)
    time.sleep(0.02)
    review.refresh_live_board(home)
    assert tree(home) == before
    assert not (home / "board.html").exists()


@pytest.mark.parametrize("how", ["sidecar", "config"])
def test_live_mode_on_rewrites_the_two_files_and_nothing_else(tmp_path, how, capsys):
    home = live_home(tmp_path, how)
    before = tree(home)
    time.sleep(0.02)
    review.refresh_live_board(home)
    after = tree(home)
    assert others(after) == others(before)  # content and modification time of every other file
    assert {k for k, v in after.items() if v[0] == "dir"} == {k for k, v in before.items() if v[0] == "dir"}
    page = (home / "board.html").read_text()
    assert page != "old page" and '<meta http-equiv="refresh" content="10">' in page
    data = json.loads((home / "board.json").read_text())
    assert {e["session_id"] for e in data["executors"]} >= {"s-done", "s-busy"}
    assert next(e for e in data["executors"] if e["session_id"] == "s-done")["status"] == "reported"
    assert (home / "sessions" / "s-done" / "status").read_text().strip() == "busy"  # worked out, not stored
    assert capsys.readouterr() == ("", "")
    no_tmp(home)


def test_it_uses_the_configured_refresh_seconds(tmp_path):
    home = live_home(tmp_path, "sidecar")
    (home / "config.json").write_text(json.dumps({"board_refresh_seconds": 30}))
    review.refresh_live_board(home)
    assert 'content="30"' in (home / "board.html").read_text()


def test_it_never_sweeps(tmp_path, monkeypatch):
    landed_session(tmp_path, "s-landed")
    home = hm(tmp_path)
    (home / "board.json").write_text("{}")
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")
    calls = []
    monkeypatch.setattr(autoclose, "sweep", lambda *a, **k: calls.append((a, k)) or [])
    review.refresh_live_board(home)
    assert calls == []
    assert "s-landed" in (home / "board.json").read_text()  # it did run
    assert (home / "sessions" / "s-landed" / "status").read_text().strip() == "reported"


def test_a_raising_render_leaves_both_files_as_they_were(tmp_path, monkeypatch):
    home = live_home(tmp_path, "sidecar")
    before = tree(home)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(board_render, "render", boom)
    review.refresh_live_board(home)
    assert tree(home) == before
    no_tmp(home)


def test_a_page_path_that_cannot_be_written_leaves_both_files_as_they_were(tmp_path):
    home = live_home(tmp_path, "sidecar")
    (home / "board.html").unlink()
    (home / "board.html").mkdir()  # the page is a directory
    before = tree(home)
    review.refresh_live_board(home)
    assert tree(home) == before
    assert (home / "board.json").read_text() == "{}"
    no_tmp(home)


def test_a_data_path_that_cannot_be_written_puts_the_page_back(tmp_path):
    home = live_home(tmp_path, "sidecar")
    (home / "board.json").unlink()
    (home / "board.json").mkdir()  # live mode is on (the path exists) and cannot be written
    review.refresh_live_board(home)
    assert (home / "board.html").read_text() == "old page"
    assert (home / "board.json").is_dir()
    no_tmp(home)
    (home / "board.html").unlink()  # and with no page before, none is left
    review.refresh_live_board(home)
    assert not (home / "board.html").exists()
    no_tmp(home)


def test_a_failure_to_read_the_state_is_swallowed(tmp_path, monkeypatch):
    home = live_home(tmp_path, "sidecar")
    before = tree(home)
    monkeypatch.setattr(review, "board_data", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    review.refresh_live_board(home)
    assert tree(home) == before


def test_it_rewrites_the_default_page_only(tmp_path):
    home = live_home(tmp_path, "sidecar")
    out = tmp_path / "elsewhere" / "p.html"
    out.parent.mkdir()
    out.write_text("mine")
    review.refresh_live_board(home)
    assert out.read_text() == "mine"


# ── the verbs ────────────────────────────────────────────────────────────────────────────────────
def go(*argv):
    rc = cli.main(list(argv))
    return rc


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A lead-1 registered through the CLI, a worktree and a packet; the stubs of conftest."""
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    monkeypatch.setenv("PI_LEAD_HOME", str(tmp_path / "home"))
    assert go("lead-start", "lead-1", "--project", "proj") == 0
    return tmp_path


def spawn_one(w):
    assert go("spawn", str(w / "wt"), "topic", str(w / "packet.md"), "--model", "sonnet", "--lead", "lead-1") == 0
    return sorted(p.name for p in (w / "home" / "sessions").iterdir())[0]


def report_landed(w, sid):
    sdir = w / "home" / "sessions" / sid
    (sdir / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    (sdir / "status").write_text("reported\n")


# (id, state, argv with {sid} {wt} {packet}, expected calls)
CASES = [
    ("list", "busy", ["list"], 1),
    ("list-json", "busy", ["list", "--json"], 1),
    ("check", "reported", ["check", "{sid}"], 1),
    ("check-all", "busy", ["check", "--all"], 1),
    ("check-json", "reported", ["check", "{sid}", "--json"], 1),
    ("check-refs-only", "busy", ["check", "--refs-only", "{sid}"], 0),
    ("send", "reported", ["send", "{sid}", "{packet}"], 1),
    ("send-when-idle-queued", "busy", ["send", "{sid}", "{packet}", "--when-idle"], 1),
    ("send-refused", "busy", ["send", "{sid}", "{packet}"], 0),
    ("spawn", "lead", ["spawn", "{wt}", "second", "{packet}", "--model", "sonnet", "--lead", "lead-1"], 1),
    ("spawn-refused", "lead", ["spawn", "{wt}/nope", "second", "{packet}", "--model", "sonnet", "--lead", "lead-1"], 0),
    ("close", "busy", ["close", "{sid}"], 1),
    ("close-self", "lead", ["close", "--self", "lead-1"], 1),
    ("close-refused", "busy", ["close", "no-such-session"], 0),
    ("turn-end", "busy", ["turn-end", "--session", "lead-1"], 1),
    ("turn-end-json", "busy", ["turn-end", "--session", "lead-1", "--json"], 1),
    ("turn-end-dry-run", "busy", ["turn-end", "--session", "lead-1", "--dry-run"], 0),
    ("status", "busy", ["status"], 0),
    ("stats", "busy", ["stats"], 0),
    ("focus", "busy", ["focus", "{sid}"], 0),
    ("doctor", "busy", ["doctor", "--offline"], 0),
    ("lint", "busy", ["lint", "{packet}"], 0),
]
IDS = [c[0] for c in CASES]


def arrange(w, state):
    if state == "lead":
        return None
    sid = spawn_one(w)
    if state == "reported":
        report_landed(w, sid)
    return sid


def argv_of(w, argv, sid):
    return [x.format(sid=sid, wt=w / "wt", packet=w / "packet.md") for x in argv]


@pytest.mark.parametrize("name,state,argv,calls", CASES, ids=IDS)
def test_each_verb_calls_the_refresh_when_the_table_says_so(world, monkeypatch, name, state, argv, calls):
    sid = arrange(world, state)
    seen = []
    monkeypatch.setattr(review, "refresh_live_board", lambda home: seen.append(Path(home)))
    go(*argv_of(world, argv, sid))
    assert seen == [world / "home"] * calls, name


def normal(text, base):
    """`text` with the run's own directory and every digit (clock, pid, ids made of the time) masked."""
    return re.sub(r"\d", "#", text.replace(str(base), "BASE"))


@pytest.mark.parametrize("name,state,argv,calls", [c for c in CASES if c[3]], ids=[c[0] for c in CASES if c[3]])
def test_a_raising_refresh_changes_nothing_a_verb_prints_or_writes(tmp_path, monkeypatch, capsys, name, state, argv,
                                                                   calls):
    results = []
    for variant in ("returns", "raises"):
        base = tmp_path / variant
        (base / "wt").mkdir(parents=True)
        (base / "packet.md").write_text("GOAL: do the thing\n")
        monkeypatch.setenv("PI_LEAD_HOME", str(base / "home"))
        assert go("lead-start", "lead-1", "--project", "proj") == 0
        sid = arrange(base, state)
        capsys.readouterr()

        def boom(home):
            raise RuntimeError("boom")

        monkeypatch.setattr(review, "refresh_live_board", (lambda home: None) if variant == "returns" else boom)
        rc = go(*argv_of(base, argv, sid))
        out, err = capsys.readouterr()
        files = {}
        for p in sorted((base / "home").rglob("*")):
            if p.is_file() and "/.tmp" not in str(p):
                try:
                    files[normal(str(p.relative_to(base / "home")), base)] = normal(p.read_text(), base)
                except UnicodeDecodeError:
                    files[str(p.relative_to(base / "home"))] = "<binary>"
        results.append((rc, normal(out, base), normal(err, base), files))
    assert results[0][:3] == results[1][:3], name
    assert results[0][3] == results[1][3], (name, sorted(k for k in set(results[0][3]) | set(results[1][3])
                                                         if results[0][3].get(k) != results[1][3].get(k)))


# ── bin/pilead end to end: no board run by hand ──────────────────────────────────────────────────
def run(*args):
    r = subprocess.run([sys.executable, str(PILEAD), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def test_after_board_live_a_check_changes_the_page(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    home = tmp_path / "pi-lead-home"
    run("lead-start", "lead-1", "--project", "proj")
    sid = run("spawn", str(tmp_path / "wt"), "topic", str(tmp_path / "packet.md"), "--model", "sonnet",
              "--lead", "lead-1").stdout.split()[1]
    run("board", "--live")
    page = home / "board.html"
    first = page.read_text()
    assert '<span class="pill busy">' in first and '<span class="pill reported">' not in first
    # the report lands (the stored status is still busy); only `check` is run, never `board`
    (home / "sessions" / sid / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    run("check", sid)
    after = page.read_text()
    assert after != first
    assert '<span class="pill reported">' in after
    data = json.loads((home / "board.json").read_text())
    assert next(e for e in data["executors"] if e["session_id"] == sid)["status"] == "reported"
    no_tmp(home)


def test_without_live_mode_a_check_leaves_no_page(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    run("lead-start", "lead-1", "--project", "proj")
    sid = run("spawn", str(tmp_path / "wt"), "topic", str(tmp_path / "packet.md"), "--model", "sonnet",
              "--lead", "lead-1").stdout.split()[1]
    run("check", sid)
    run("list")
    assert not (tmp_path / "pi-lead-home" / "board.html").exists()

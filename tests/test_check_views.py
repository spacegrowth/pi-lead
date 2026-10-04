"""`pilead check` without a sid: `--all`, `--json`, and the usage line for a log that was found but could
not be read. Sessions are written here (no pid file, so the stored status is kept); bin/pilead runs
against the stub osascript of tests/test_cli_core.py, and the ledger count runs in-process."""
import json
import sys

from test_cli_core import ROOT, home, pilead, stub_osascript  # noqa: F401  (fixtures)
from test_ledger import _count_ledger_reads
from test_usage import assistant, u, write_log

sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, ledger  # noqa: E402

REPORT = "Did {n}.\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: a.py\n\n## More\nx\n"


def session(tmp_path, sid, status="busy", report=False, raw=None):
    sdir = home(tmp_path) / "sessions" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (sdir / "meta.json").write_text(raw)
        return sdir
    wt = tmp_path / f"wt-{sid}"
    wt.mkdir(exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": "lead-1", "worktree": str(wt), "topic": sid, "model": "anthropic/claude-sonnet-5",
        "status": status, "created": "-", "packets": 1, "label": f"[Exec] {sid}", "layout": "tab"}))
    (sdir / "status").write_text(status + "\n")
    (sdir / "packet-0001.md").write_text("GOAL\n")
    if report:
        (sdir / "report-0001.md").write_text(REPORT.format(n=sid))
    return sdir


def test_neither_sid_nor_all_exits_2_with_one_stderr_line(tmp_path):
    for args in ([], ["--json"]):
        r = pilead("check", *args, check=False)
        assert r.returncode == 2 and r.stdout == ""
        assert r.stderr == "pilead: check needs a session id, or --all\n"
    r = pilead("check", "--refs-only", check=False)
    assert r.returncode == 2 and r.stdout == "" and len(r.stderr.splitlines()) == 1


def test_all_prints_a_header_and_a_block_per_live_session_in_sid_order(tmp_path):
    session(tmp_path, "c-three", report=True)
    session(tmp_path, "a-one")
    session(tmp_path, "b-closed", status="closed")
    session(tmp_path, "d-dead", status="dead")
    session(tmp_path, "b-two", status="idle")
    r = pilead("check", "--all")
    sd = home(tmp_path) / "sessions"
    assert r.stdout == ("== a-one ==\nbusy\n"
                        "== b-two ==\nidle\n"
                        f"== c-three ==\nreported\n{sd / 'c-three' / 'report-0001.md'}\n"
                        "Did c-three.\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: a.py\n")
    # each block is what `check <sid>` prints for that session
    assert pilead("check", "c-three").stdout == r.stdout.split("== c-three ==\n")[1]


def test_json_prints_only_json(tmp_path):
    session(tmp_path, "a-one", report=True)
    session(tmp_path, "b-two")
    write_log("b-two", tmp_path / "wt-b-two", [assistant(u(3, 40, 125_000, 100))])
    r = pilead("check", "--all", "--json")
    data = json.loads(r.stdout)  # nothing else on stdout
    assert [d["sid"] for d in data] == ["a-one", "b-two"]
    a, b = data
    assert set(a) == {"sid", "status", "packet", "reported", "report", "tldr", "refs", "usage", "ctx_window",
                      "heavy", "approaching", "queued", "queue_error", "diff", "orphan"}
    assert (a["status"], a["packet"], a["reported"]) == ("reported", 1, True)
    assert a["report"] == str(home(tmp_path) / "sessions" / "a-one" / "report-0001.md")
    assert a["tldr"].splitlines()[0] == "Did a-one." and a["usage"] is None and a["heavy"] is False
    assert a["refs"] == {"state": "not-a-repo", "line": "refs: not a git repository"}
    assert (b["status"], b["reported"], b["report"], b["tldr"]) == ("busy", False, None, None)
    assert b["usage"]["prompt"] == 125_103 and "path" not in b["usage"] and b["approaching"] is True
    # what `check` would have printed goes to stderr in this mode
    assert "tokens 125k/40 over 1 requests" in r.stderr and "reported" in r.stderr
    one = json.loads(pilead("check", "b-two", "--json").stdout)
    assert [d["sid"] for d in one] == ["b-two"]


def test_an_unreadable_record_is_reported_and_does_not_stop_the_others(tmp_path):
    session(tmp_path, "a-one")
    session(tmp_path, "b-bad", raw="{not json")
    session(tmp_path, "c-three")
    data = json.loads(pilead("check", "--all", "--json").stdout)
    assert [d["sid"] for d in data] == ["a-one", "b-bad", "c-three"]
    assert set(data[1]) == {"sid", "error"} and data[1]["error"]
    assert data[0]["status"] == data[2]["status"] == "busy"
    r = pilead("check", "--all")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[:2] == ["== a-one ==", "busy"] and lines[2] == "== b-bad ==" and lines[3].startswith("error: ")
    assert lines[4:] == ["== c-three ==", "busy"]


def test_one_check_all_over_three_sessions_reads_the_ledger_once(tmp_path, monkeypatch, capsys):
    for sid in ("a-one", "b-two", "c-three"):
        session(tmp_path, sid, report=True)
    counts = _count_ledger_reads(monkeypatch)
    assert cli.main(["check", "--all"]) == 0
    assert counts == {"read": 1, "open": 1}
    assert capsys.readouterr().out.count("== ") == 3
    # every session needed it: each got its "reported" snapshot
    assert sorted(r["session_id"] for r in ledger.read(home(tmp_path), event="usage_snapshot")) == \
        ["a-one", "b-two", "c-three"]
    counts.update(read=0, open=0)
    assert cli.main(["check", "--all", "--json"]) == 0
    assert counts == {"read": 1, "open": 1}


# ── auto-close: `pilead check` run by a lead parks its finished executors, after its own output ───────
def _check(*argv, caller=None):
    import os
    import subprocess
    from test_cli_core import PILEAD
    env = dict(os.environ)
    if caller:
        env["PI_LEAD_SID"] = caller
    return subprocess.run([str(PILEAD), "check", *argv], capture_output=True, text=True, env=env)


PARK = "auto-closed {0} (landed) — pilead send {0} <packet> reopens it with its conversation"


def test_check_sid_by_a_caller_parks_it_and_prints_the_park_line_last(tmp_path):
    from test_autoclose import session, status_of
    session(tmp_path, "s-landed", seen=None)  # check's own first look (usage_snapshot at "reported") is the look
    r = _check("s-landed", caller="lead-1")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[-1] == PARK.format("s-landed")
    assert "reported" in lines[:-1]  # check's own output shows the state it found
    assert status_of(tmp_path, "s-landed") == "closed"
    (ev,) = [e for e in ledger.read(home(tmp_path)) if e["event"] == "auto_closed"]
    assert (ev["session_id"], ev["trigger"]) == ("s-landed", "check")


def test_check_sid_parks_only_that_session_and_only_the_callers(tmp_path):
    from test_autoclose import session, status_of
    session(tmp_path, "s-a")
    session(tmp_path, "s-b")
    session(tmp_path, "s-theirs", lead="lead-2")
    r = _check("s-a", caller="lead-1")
    assert r.stdout.splitlines()[-1] == PARK.format("s-a")
    assert status_of(tmp_path, "s-b") == "reported"
    r = _check("s-theirs", caller="lead-1")
    assert "auto-clos" not in r.stdout + r.stderr and status_of(tmp_path, "s-theirs") == "reported"


def test_check_all_by_a_caller_sweeps_its_executors(tmp_path):
    from test_autoclose import session, status_of
    session(tmp_path, "s-a")
    session(tmp_path, "s-b")
    session(tmp_path, "s-theirs", lead="lead-2")
    r = _check("--all", caller="lead-1")
    lines = r.stdout.splitlines()
    assert lines[-2:] == [PARK.format("s-a"), PARK.format("s-b")]
    assert "== s-theirs ==" in lines and status_of(tmp_path, "s-theirs") == "reported"


def test_check_json_keeps_stdout_json_and_puts_the_park_line_on_stderr(tmp_path):
    from test_autoclose import session
    session(tmp_path, "s-a")
    r = _check("s-a", "--json", caller="lead-1")
    assert r.returncode == 0
    assert [x["status"] for x in json.loads(r.stdout)] == ["reported"]
    assert r.stderr.splitlines()[-1] == PARK.format("s-a")
    session(tmp_path, "s-b")
    r = _check("--all", "--json", caller="lead-1")
    assert r.returncode == 0
    assert [(x["sid"], x["status"]) for x in json.loads(r.stdout)] == [("s-b", "reported")]
    assert r.stderr.splitlines()[-1] == PARK.format("s-b")


def test_check_with_no_caller_parks_nothing(tmp_path):
    from test_autoclose import session, status_of
    session(tmp_path, "s-a")
    for argv in (["s-a"], ["--all"], ["s-a", "--json"], ["--all", "--json"]):
        r = _check(*argv)
        assert r.returncode == 0 and "auto-clos" not in r.stdout + r.stderr
    assert status_of(tmp_path, "s-a") == "reported"
    assert [e for e in ledger.read(home(tmp_path)) if e["event"].startswith("auto_clos")] == []


def test_check_refs_only_parks_nothing(tmp_path):
    from test_autoclose import session, status_of
    from test_close_variants import tree
    session(tmp_path, "s-a")
    before = tree(home(tmp_path))
    r = _check("s-a", "--refs-only", caller="lead-1")
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 1 and "auto-clos" not in r.stdout + r.stderr
    assert status_of(tmp_path, "s-a") == "reported" and tree(home(tmp_path)) == before

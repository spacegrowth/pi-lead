"""lib/pilead/status.py: one test per row of the status table, over sessions this file writes. The
process is a child the test starts and stops (`sleep`); the tab is the vendored iterm backend with its
`running` / `run_osascript` monkeypatched (the backend asks `ps` and `osascript`, never the iterm2 package),
so no real tab or terminal is ever asked."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cli_core import ROOT, env, home, make_reported, pilead, run_bootstrap, spawn, stub_osascript  # noqa: F401  (fixtures)
from test_usage import assistant, u, user, write_log

sys.path.insert(0, str(ROOT / "lib"))
from pilead import config, ledger, state, status  # noqa: E402
from pilead.launch import backend  # noqa: E402

SID = "exec-100000"
LIMIT = "You're out of extra usage · resets 4pm (Europe/London)"


@pytest.fixture
def child():
    """A process this test owns; stopped (and reaped) at the end."""
    procs = []

    def start():
        p = subprocess.Popen(["sleep", "300"])
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.terminate()
            p.wait()


@pytest.fixture
def tab(monkeypatch):
    """The iterm backend, pinned: `tab.state` is "open", "gone" or "unaskable" (iTerm not running);
    "failing" makes osascript exit non-zero. Every osascript call is recorded."""
    it = backend.by_name("iterm")

    class Tab:
        state = "gone"
        calls = []

    def running():
        return Tab.state != "unaskable"

    def run_osascript(script, timeout=None):
        Tab.calls.append(script)
        if Tab.state == "failing":
            return subprocess.CompletedProcess(["osascript"], 1, "", "boom")
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if Tab.state == "open" else "false\n", "")

    monkeypatch.setattr(it, "running", running)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    return Tab


def h(tmp_path):
    return home(tmp_path)


def make(tmp_path, stored="busy", packets=1, report=False, pid=None, wt=None):
    """sessions/<SID> with meta, status, packet(s); `pid` → a pid file written now."""
    hm = h(tmp_path)
    sdir = state.session_dir(hm, SID)
    sdir.mkdir(parents=True, exist_ok=True)
    wt = wt or tmp_path / "wt"
    Path(wt).mkdir(exist_ok=True)
    meta = {"sid": SID, "lead": "lead-1", "worktree": str(wt), "topic": "t", "model": "anthropic/claude-sonnet-5",
            "status": stored, "created": state.now_iso(), "packets": packets, "label": f"[Exec] {SID}",
            "layout": "tab", "backend": "iterm"}
    (sdir / "meta.json").write_text(json.dumps(meta))
    (sdir / "status").write_text(stored + "\n")
    (sdir / "iterm-id").write_text("w0t1p0:11111111-2222-3333-4444-555555555555\n")
    for n in range(1, packets + 1):
        (sdir / f"packet-{n:04d}.md").write_text("GOAL: x\n")
    if report:
        (sdir / f"report-{packets:04d}.md").write_text("Done.\nStatus: clean\n")
    if pid is not None:
        (sdir / "pid").write_text(f"{pid}\n")
    return meta, sdir


def refresh(tmp_path, meta):
    return status.refresh(h(tmp_path), dict(meta))


def events(tmp_path):
    return [(r["event"], r.get("reason")) for r in ledger.read(h(tmp_path)) if r["event"] in ("stalled", "paused", "dead")]


def stored(sdir):
    return (sdir / "status").read_text().strip()


def age(path, secs):
    t = time.time() - secs
    os.utime(path, (t, t))


def gone_pid(child):
    p = child()
    p.terminate()
    p.wait()
    return p.pid


# ── the rows, in order ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("terminal", ["closed", "dead"])
def test_row1_closed_and_dead_never_change(tmp_path, child, tab, terminal):
    meta, sdir = make(tmp_path, stored=terminal, report=True, pid=gone_pid(child))
    tab.state = "open"
    assert refresh(tmp_path, meta) == terminal
    assert stored(sdir) == terminal and events(tmp_path) == [] and tab.calls == []


def test_row2_a_report_of_the_current_packet_is_reported(tmp_path, child, tab):
    meta, sdir = make(tmp_path, stored="busy", report=True, pid=gone_pid(child))
    assert refresh(tmp_path, meta) == "reported"
    assert stored(sdir) == "reported" and json.loads((sdir / "meta.json").read_text())["status"] == "reported"
    assert tab.calls == [] and events(tmp_path) == []


def test_row2_an_older_packets_report_does_not_count(tmp_path, child):
    meta, sdir = make(tmp_path, stored="busy", packets=2)
    (sdir / "report-0001.md").write_text("old\n")
    assert refresh(tmp_path, meta) == "busy"


@pytest.mark.parametrize("pidtext", [None, "", "not-a-pid\n"])
def test_row3_no_readable_pid_file_keeps_the_stored_status_and_writes_nothing(tmp_path, tab, pidtext):
    meta, sdir = make(tmp_path, stored="busy")
    if pidtext is not None:
        (sdir / "pid").write_text(pidtext)
    age(sdir / "packet-0001.md", 10_000)  # would be stalled if liveness were claimed
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sdir.iterdir()}
    assert refresh(tmp_path, meta) == "busy"
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sdir.iterdir()} == before
    assert not ledger.path(h(tmp_path)).exists() and tab.calls == []


@pytest.mark.parametrize("was", ["busy", "idle", "stalled", "paused"])
def test_row4_a_usage_limit_error_pauses(tmp_path, child, was):
    p = child()
    meta, sdir = make(tmp_path, stored=was, pid=p.pid)
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT
    write_log(SID, tmp_path / "wt", [assistant(u(100, 10)), err])
    assert refresh(tmp_path, meta) == "paused"
    assert stored(sdir) == "paused"
    if was != "paused":  # a status that did not change is not rewritten
        assert json.loads((sdir / "meta.json").read_text())["pause_text"] == LIMIT[:120]
    assert events(tmp_path) == ([] if was == "paused" else [("paused", LIMIT)])


def test_row4_pause_text_is_cut_at_120_characters(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = "quota exceeded " + "x" * 300
    write_log(SID, tmp_path / "wt", [err])
    assert refresh(tmp_path, meta) == "paused"
    assert json.loads((sdir / "meta.json").read_text())["pause_text"] == ("quota exceeded " + "x" * 300)[:120]


def test_row4_paused_goes_back_to_busy_once_a_good_request_follows(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT
    write_log(SID, tmp_path / "wt", [err])
    assert refresh(tmp_path, meta) == "paused"
    write_log(SID, tmp_path / "wt", [err, assistant(u(200, 20))])
    assert refresh(tmp_path, json.loads((sdir / "meta.json").read_text())) == "busy"
    assert stored(sdir) == "busy" and "pause_text" not in json.loads((sdir / "meta.json").read_text())
    assert events(tmp_path) == [("paused", LIMIT)]  # going back to busy logs nothing


def test_row4_an_error_that_is_not_a_usage_limit_does_not_pause(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [assistant(u(0, 0), stop="error")])  # "synthetic failure"
    assert refresh(tmp_path, meta) == "busy"


def test_row4_a_usage_limit_error_that_is_not_the_last_assistant_message_does_not_pause(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT
    write_log(SID, tmp_path / "wt", [err, assistant(u(10, 1))])
    assert refresh(tmp_path, meta) == "busy"


@pytest.mark.parametrize("value", [None, 12, ["x"], True, "(unclosed"])
def test_row4_null_wrong_typed_or_invalid_pattern_uses_the_default(tmp_path, child, value):
    (h(tmp_path)).mkdir(parents=True, exist_ok=True)
    config.path(h(tmp_path)).write_text(json.dumps({"usage_limit_pattern": value}))
    meta, sdir = make(tmp_path, pid=child().pid)
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT
    write_log(SID, tmp_path / "wt", [err])
    assert refresh(tmp_path, meta) == "paused"


def test_row4_a_string_pattern_is_used_as_given(tmp_path, child):
    h(tmp_path).mkdir(parents=True, exist_ok=True)
    config.path(h(tmp_path)).write_text(json.dumps({"usage_limit_pattern": "SYNTHETIC fail"}))
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [assistant(u(0, 0), stop="error")])  # "synthetic failure", any case
    assert refresh(tmp_path, meta) == "paused"
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT  # the default's wording no longer pauses
    write_log(SID, tmp_path / "wt", [err])
    assert refresh(tmp_path, json.loads((sdir / "meta.json").read_text())) == "busy"


@pytest.mark.parametrize("was", ["busy", "stalled", "paused"])
def test_row5_busy_too_long_with_an_old_log_is_stalled(tmp_path, child, was):
    meta, sdir = make(tmp_path, stored=was, pid=child().pid)
    log = write_log(SID, tmp_path / "wt", [assistant(u(100, 10))])
    age(sdir / "packet-0001.md", 3000)
    age(log, 2800)
    assert refresh(tmp_path, meta) == "stalled"
    assert events(tmp_path) == ([] if was == "stalled" else [("stalled", "busy 50min, no report; session log last written 46min ago")])


def test_row5_busy_too_long_and_no_log_found_is_stalled(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    age(sdir / "packet-0001.md", 3000)
    assert refresh(tmp_path, meta) == "stalled"
    assert events(tmp_path) == [("stalled", "busy 50min, no report; session log not found")]


def test_row5_a_fresh_log_keeps_a_long_packet_busy(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [assistant(u(100, 10))])  # written just now
    age(sdir / "packet-0001.md", 10_000)
    assert refresh(tmp_path, meta) == "busy"


def test_row5_the_threshold_comes_from_config(tmp_path, child):
    h(tmp_path).mkdir(parents=True, exist_ok=True)
    config.path(h(tmp_path)).write_text(json.dumps({"stall_threshold_seconds": 60}))
    meta, sdir = make(tmp_path, pid=child().pid)
    age(sdir / "packet-0001.md", 120)
    assert refresh(tmp_path, meta) == "stalled"


@pytest.mark.parametrize("was", ["busy", "stalled", "paused"])
def test_row6_alive_and_otherwise_is_busy(tmp_path, child, was):
    meta, sdir = make(tmp_path, stored=was, pid=child().pid)
    assert refresh(tmp_path, meta) == "busy"
    assert stored(sdir) == "busy" and events(tmp_path) == []


def test_row7_idle_and_alive_stays_idle(tmp_path, child):
    meta, sdir = make(tmp_path, stored="idle", pid=child().pid)
    age(sdir / "packet-0001.md", 10_000)  # idle is never stalled
    assert refresh(tmp_path, meta) == "idle"


@pytest.mark.parametrize("was", ["busy", "idle", "paused", "reported"])
def test_row8_process_gone_and_tab_open_is_stalled(tmp_path, child, tab, was):
    meta, sdir = make(tmp_path, stored=was, pid=gone_pid(child))
    tab.state = "open"
    assert refresh(tmp_path, meta) == "stalled"
    assert events(tmp_path) == [("stalled", "process gone, no report; tab still open")]
    assert tab.calls and "11111111-2222-3333-4444-555555555555" in tab.calls[0]


@pytest.mark.parametrize("terminal", ["gone", "unaskable", "failing"])
def test_row9_process_gone_and_no_tab_or_no_answer_is_dead(tmp_path, child, tab, terminal):
    meta, sdir = make(tmp_path, stored="busy", pid=gone_pid(child))
    tab.state = terminal
    assert refresh(tmp_path, meta) == "dead"
    assert stored(sdir) == "dead"
    assert [e for e, _ in events(tmp_path)] == ["dead"]
    if terminal == "unaskable":
        assert tab.calls == []  # an app that is not running is never told anything (it would launch)
    assert refresh(tmp_path, json.loads((sdir / "meta.json").read_text())) == "dead"  # and it stays dead
    assert len(events(tmp_path)) == 1


# ── liveness ─────────────────────────────────────────────────────────────────────────────────────
def test_a_pid_whose_process_started_after_the_pid_file_was_written_is_not_alive(tmp_path, child):
    pf = tmp_path / "pid"
    p = child()
    pf.write_text(f"{p.pid}\n")
    assert status.pid_alive(pf) is True
    age(pf, 120)  # the file was written before this process existed: the number was reused
    assert status.pid_alive(pf) is False


def test_pid_alive_reads(tmp_path, child):
    assert status.pid_alive(tmp_path / "missing") is None
    (tmp_path / "bad").write_text("x\n")
    assert status.pid_alive(tmp_path / "bad") is None
    (tmp_path / "one").write_text("1\n")
    assert status.pid_alive(tmp_path / "one") is False
    (tmp_path / "gone").write_text(f"{gone_pid(child)}\n")
    assert status.pid_alive(tmp_path / "gone") is False


def test_reused_pid_reads_as_gone_in_refresh(tmp_path, child, tab):
    p = child()
    meta, sdir = make(tmp_path, stored="busy", pid=p.pid)
    age(sdir / "pid", 120)
    tab.state = "gone"
    assert refresh(tmp_path, meta) == "dead"


# ── no change writes nothing ─────────────────────────────────────────────────────────────────────
def test_an_unchanged_status_writes_no_event_and_does_not_rewrite_the_status_file(tmp_path, child):
    meta, sdir = make(tmp_path, stored="busy", pid=child().pid)
    age(sdir / "status", 500)
    age(sdir / "meta.json", 500)
    before = [(sdir / f).stat().st_mtime_ns for f in ("status", "meta.json")]
    for _ in range(3):
        assert refresh(tmp_path, meta) == "busy"
    assert [(sdir / f).stat().st_mtime_ns for f in ("status", "meta.json")] == before
    assert not ledger.path(h(tmp_path)).exists()


def test_refresh_never_raises(tmp_path):
    assert status.refresh(h(tmp_path), {"no": "sid"}) == "unknown"
    assert status.refresh(h(tmp_path), {"sid": "nope", "packets": "x"}) == "unknown"


def test_a_change_is_logged_once_and_added_to_the_callers_records(tmp_path, child, tab):
    meta, sdir = make(tmp_path, stored="busy", pid=gone_pid(child))
    recs = []
    assert status.refresh(h(tmp_path), dict(meta), recs) == "dead"
    assert [r["event"] for r in recs] == ["dead"] and recs[0]["session_id"] == SID and recs[0]["packet"] == 1


# ── send to a dead session relaunches it ─────────────────────────────────────────────────────────
def test_send_to_dead_relaunches_resume(env):
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    (sdir / "status").write_text("dead\n")
    (env / "p2.md").write_text("again\n")
    r = pilead("send", sid, str(env / "p2.md"))
    assert "placement=" in r.stdout
    assert (sdir / "status").read_text().strip() == "busy"
    assert "again" in (sdir / "inbox.md").read_text()
    recs = run_bootstrap(env, sid)
    argv = recs[-1]["argv"]
    assert "--session-id" in argv and "--model" not in argv and not any(a.startswith("@") for a in argv)
    assert [json.loads(l)["via"] for l in ledger.path(home(env)).read_text().splitlines()
            if json.loads(l)["event"] == "packet_sent"] == ["relaunch"]


# ── a relaunch starts with no pid file ───────────────────────────────────────────────────────────
def _events_of(env, sid):
    p = ledger.path(home(env))
    return [json.loads(l)["event"] for l in p.read_text().splitlines()
            if json.loads(l).get("session_id") == sid] if p.exists() else []


@pytest.mark.parametrize("ended", ["closed", "dead"])
def test_relaunch_removes_the_previous_runs_pid_file_so_check_reads_busy(env, child, ended):
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    (sdir / "pid").write_text(f"{gone_pid(child)}\n")  # the previous run's pi, gone now
    if ended == "closed":
        pilead("close", sid)
    else:
        (sdir / "status").write_text("dead\n")
    (env / "p2.md").write_text("again\n")
    assert "placement=" in pilead("send", sid, str(env / "p2.md")).stdout  # relaunched (stub osascript)
    assert not (sdir / "pid").exists()
    assert pilead("check", sid).stdout.strip() == "busy"
    assert (sdir / "status").read_text().strip() == "busy"
    assert not {"stalled", "dead"} & set(_events_of(env, sid))


def test_send_to_a_live_session_leaves_its_pid_file_alone(env, child):
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    p = child()
    (sdir / "pid").write_text(f"{p.pid}\n")
    age(sdir / "pid", 30)
    before = ((sdir / "pid").read_bytes(), (sdir / "pid").stat().st_mtime_ns)
    make_reported(env, sid)
    (env / "p2.md").write_text("more\n")
    assert "placement=" not in pilead("send", sid, str(env / "p2.md")).stdout  # via the inbox
    assert ((sdir / "pid").read_bytes(), (sdir / "pid").stat().st_mtime_ns) == before


def test_spawn_of_a_new_session_writes_no_pid_file(env):
    sid = spawn(env)
    assert not (home(env) / "sessions" / sid / "pid").exists()  # the tab's shell writes it, not pilead


# ── "process alive" from the process' age (`ps -o etime=`) ───────────────────────────────────────
@pytest.mark.parametrize("text,secs", [("00:05", 5), ("12:34", 754), ("01:02:03", 3723),
                                       ("3-04:05:06", ((3 * 24 + 4) * 60 + 5) * 60 + 6),
                                       ("   1:02\n", 62)])
def test_parse_etime_shapes(text, secs):
    assert status.parse_etime(text) == secs


@pytest.mark.parametrize("text", ["", "x", "05", "1-02:03", "12:34:56:78", "Mon Sep 28 01:30:00 2026"])
def test_parse_etime_other_text_is_none(text):
    assert status.parse_etime(text) is None


@pytest.fixture
def stub_ps(tmp_path, monkeypatch):
    """A `ps` first on PATH that prints $STUB_PS_OUT and exits $STUB_PS_RC; `stub_ps(out, rc)`."""
    d = tmp_path / "psbin"
    d.mkdir()
    (d / "ps").write_text('#!/bin/sh\nprintf "%s\\n" "$STUB_PS_OUT"\nexit "${STUB_PS_RC:-0}"\n')
    (d / "ps").chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])

    def set_(out, rc=0):
        monkeypatch.setenv("STUB_PS_OUT", out)
        monkeypatch.setenv("STUB_PS_RC", str(rc))
    return set_


def _etime(secs):
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _pidfile(tmp_path, pid, written_ago):
    pf = tmp_path / "pid"
    pf.write_text(f"{pid}\n")
    age(pf, written_ago)
    return pf


def test_a_process_older_than_its_pid_file_is_alive(tmp_path, child, stub_ps):
    pf = _pidfile(tmp_path, child().pid, 10)
    stub_ps(_etime(3600))  # started an hour before the file was written
    assert status.pid_alive(pf) is True


def test_a_process_that_started_one_second_after_the_pid_file_is_alive(tmp_path, child, stub_ps):
    pf = _pidfile(tmp_path, child().pid, 100)
    stub_ps(_etime(99))  # etime's one-second resolution
    assert status.pid_alive(pf) is True


def test_a_process_that_started_a_minute_after_the_pid_file_is_another_one(tmp_path, child, stub_ps):
    pf = _pidfile(tmp_path, child().pid, 100)
    stub_ps(_etime(40))
    assert status.pid_alive(pf) is False


@pytest.mark.parametrize("out,rc", [("Sun Mar 29 01:30:00 2026", 0), ("", 0), ("garbage", 0), ("00:05", 1)])
def test_an_unreadable_age_counts_as_alive(tmp_path, child, stub_ps, out, rc):
    pf = _pidfile(tmp_path, child().pid, 3600)  # a start after the file would read as "another one"
    stub_ps(out, rc)
    assert status.pid_alive(pf) is True


def test_no_ps_on_path_counts_as_alive(tmp_path, child, monkeypatch):
    pf = _pidfile(tmp_path, child().pid, 3600)
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert status._elapsed(os.getpid()) is None
    assert status.pid_alive(pf) is True


def test_a_ps_that_times_out_counts_as_alive(tmp_path, child, monkeypatch):
    pf = _pidfile(tmp_path, child().pid, 3600)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("ps", 5)
    monkeypatch.setattr(status.subprocess, "run", slow)
    assert status.pid_alive(pf) is True


def test_the_real_ps_reads_this_process_age(child):
    p = child()
    e = status._elapsed(p.pid)
    assert e is not None and 0 <= e < 60


# ── the usage-limit pause follows the active branch ─────────────────────────────────────────────
def _limit():
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = LIMIT
    return err


def _link(e, eid, parent):
    return {**e, "id": eid, "parentId": parent}


def _branched(tmp_path, active_end, abandoned_end):
    """a1 → y1 = active_end, then a1 → x1 = abandoned_end written AFTER it, then the leaf z1 (a user
    message, the file's last line) whose parent is y1. File order's last assistant message is x1; the
    active branch's is y1."""
    return write_log(SID, tmp_path / "wt", [
        _link(assistant(u(100, 10)), "a1", None), _link(active_end, "y1", "a1"),
        _link(abandoned_end, "x1", "a1"), _link(user("go on"), "z1", "y1")])


def test_a_limit_error_on_the_abandoned_branch_does_not_pause(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    _branched(tmp_path, active_end=assistant(u(200, 20)), abandoned_end=_limit())
    assert refresh(tmp_path, meta) == "busy" and events(tmp_path) == []


def test_a_limit_error_ending_the_active_branch_pauses(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    _branched(tmp_path, active_end=_limit(), abandoned_end=assistant(u(200, 20)))
    assert refresh(tmp_path, meta) == "paused" and events(tmp_path) == [("paused", LIMIT)]


def test_a_log_with_no_parent_links_pauses_by_file_order_as_before(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [assistant(u(100, 10)), _limit()])  # parentId null throughout
    assert refresh(tmp_path, meta) == "paused"


def test_refresh_uses_the_callers_reading_and_does_not_parse_again(tmp_path, child, monkeypatch):
    from pilead import usage
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [_limit()])
    reading = usage.session_reading(h(tmp_path), SID, str(tmp_path / "wt"), write_cache=False)
    calls = []
    monkeypatch.setattr(usage, "read", lambda p: calls.append(p))
    assert status.refresh(h(tmp_path), dict(meta), reading=reading) == "paused"
    assert calls == []


# ── refresh(…, store=False): the status it would store, nothing written ───────────────────────────
def _with_ledger(tmp_path):
    ledger.append(h(tmp_path), "spawned", session_id=SID)  # a ledger exists, so "unchanged" means something


def test_store_false_returns_reported_and_writes_nothing(tmp_path, child, tab):
    from test_close_variants import tree
    meta, sdir = make(tmp_path, stored="busy", report=True, pid=gone_pid(child))
    _with_ledger(tmp_path)
    before, records = tree(h(tmp_path)), []
    m = dict(meta)
    assert status.refresh(h(tmp_path), m, records, store=False) == "reported"
    assert tree(h(tmp_path)) == before and records == [] and m == meta
    assert status.refresh(h(tmp_path), dict(meta)) == "reported"  # what refresh then stores
    assert stored(sdir) == "reported"


def test_store_false_returns_dead_and_writes_nothing(tmp_path, child, tab):
    from test_close_variants import tree
    meta, sdir = make(tmp_path, stored="busy", pid=gone_pid(child))
    tab.state = "gone"
    _with_ledger(tmp_path)
    before, records = tree(h(tmp_path)), []
    m = dict(meta)
    assert status.refresh(h(tmp_path), m, records, store=False) == "dead"
    assert tree(h(tmp_path)) == before and records == [] and m == meta
    assert events(tmp_path) == []
    assert status.refresh(h(tmp_path), dict(meta)) == "dead"  # what refresh then stores, and logs
    assert stored(sdir) == "dead" and [e for e, _ in events(tmp_path)] == ["dead"]


# ── the pause, read through the usage cache's `limit` (p24 change 6) ──────────────────────────────
def _late_limit():
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = ("e" * 500 + LIMIT).ljust(1000, "x")
    return err


def test_a_limit_wording_at_character_500_pauses_on_a_first_read_and_from_the_cache(tmp_path, child):
    from pilead import usage
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [_late_limit()])
    first = usage.session_reading(h(tmp_path), SID, str(tmp_path / "wt"))
    assert first[0]["last_stop"]["limit"] is True and (sdir / "usage.json").is_file()
    assert status.refresh(h(tmp_path), dict(meta), reading=first) == "paused"
    assert json.loads((sdir / "meta.json").read_text())["pause_text"] == "e" * 120
    (sdir / "status").write_text("busy\n")
    cached = usage.session_reading(h(tmp_path), SID, str(tmp_path / "wt"))
    assert cached == first
    assert status.refresh(h(tmp_path), json.loads((sdir / "meta.json").read_text()), reading=cached) == "paused"


def test_a_limit_wording_at_character_500_pauses_on_an_uncached_read(tmp_path, child):
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [_late_limit()])
    assert refresh(tmp_path, meta) == "paused"


def test_a_cached_limit_false_does_not_pause_and_true_does(tmp_path):
    hm = h(tmp_path)
    hm.mkdir(parents=True, exist_ok=True)
    base = {"stopReason": "error", "errorMessage": "quota exceeded"}
    assert status.pause_text(hm, {"last_stop": {**base, "limit": False}}) is None  # the cache said no
    assert status.pause_text(hm, {"last_stop": {**base, "errorMessage": "x" * 200, "limit": True}}) == "x" * 120
    assert status.pause_text(hm, {"last_stop": {**base, "stopReason": "stop", "limit": True}}) is None
    assert status.pause_text(hm, {"last_stop": base}) == "quota exceeded"  # no `limit`: today's rule


def test_changing_the_pattern_changes_the_cached_answer(tmp_path, child):
    from pilead import usage
    meta, sdir = make(tmp_path, pid=child().pid)
    write_log(SID, tmp_path / "wt", [_late_limit()])
    r = usage.session_reading(h(tmp_path), SID, str(tmp_path / "wt"))
    assert status.refresh(h(tmp_path), dict(meta), reading=r) == "paused"
    config.path(h(tmp_path)).write_text(json.dumps({"usage_limit_pattern": "nothing like it"}))
    (sdir / "status").write_text("busy\n")
    r = usage.session_reading(h(tmp_path), SID, str(tmp_path / "wt"))
    assert r[0]["last_stop"]["limit"] is False
    assert status.refresh(h(tmp_path), json.loads((sdir / "meta.json").read_text()), reading=r) == "busy"


# ── b12: a tmux lead is live only while pi runs in its pane ───────────────────────────────────────────
@pytest.fixture
def tmux_seam(monkeypatch):
    """The tmux backend's one seam replaced by a recorder (no `tmux` runs): the server answers, pane %4 exists
    with tty /dev/ttys044; `ps_out` is what the vendored `pids_on_tty` reads from `ps`."""
    tm = backend.by_name("tmux")
    it = backend.by_name("iterm")

    class Seam:
        calls = []
        ps_out = ""

    def fake_tmux(args, timeout=5):
        args = [str(a) for a in args]
        Seam.calls.append(args)
        out = ""
        if args[0] == "list-panes":
            out = "%1\n%4\n"
        elif args[0] == "display-message" and args[-1] == "#{pane_tty}":
            out = "/dev/ttys044\n"
        return subprocess.CompletedProcess(["tmux"] + args, 0, out, "")

    def fake_run(argv, **kw):
        assert argv[0] == "ps"
        return subprocess.CompletedProcess(argv, 0, Seam.ps_out, "")

    monkeypatch.setattr(tm, "_tmux", fake_tmux)
    monkeypatch.setattr(it.subprocess, "run", fake_run)
    return Seam


def test_tmux_lead_with_pi_on_its_pane_is_live(tmux_seam):
    tmux_seam.ps_out = "  501 ttys044  /usr/local/bin/pi --session-id x\n  502 ttys044  -sh\n"
    assert status.lead_tab_exists({"iterm_handle": "tmux:%4"}) is True
    assert ["list-panes", "-a", "-F", "#{pane_id}"] in tmux_seam.calls


def test_tmux_lead_pane_without_pi_is_not_live(tmux_seam):
    tmux_seam.ps_out = "  502 ttys044  -sh\n  503 ttys044  /usr/bin/api\n"
    assert status.lead_tab_exists({"iterm_handle": "tmux:%4"}) is False


def test_tmux_lead_pane_gone_is_not_live(tmux_seam):
    tmux_seam.ps_out = "  501 ttys044  pi\n"
    assert status.lead_tab_exists({"iterm_handle": "tmux:%9"}) is False


def test_an_iterm_lead_asks_no_tmux(tmux_seam, tab):
    tab.state = "open"
    assert status.lead_tab_exists({"iterm_handle": "w0t0p0:11111111-2222-3333-4444-555555555555"}) is True
    assert tmux_seam.calls == []
    assert status.lead_tab_exists({"iterm_handle": "junk"}) is False and tmux_seam.calls == []

"""`pilead check` delivers one queued packet per session it checks (lib/pilead/queue.py `deliver`,
trigger `check`, never for `--refs-only`, never reopening a `closed`/`dead` session), and `check`,
`list` and `status` show what is queued — without changing what any of them prints for a session that
has no `queue.json`. Driven through bin/pilead with the fake `pi` and the terminal seam of
tests/test_resume.py (a recording `osascript` stub and a `ps` stub first on PATH), reusing
tests/test_queue_deliver.py's queue-shape helpers. No real tab is opened or closed and the real pi
never runs."""
import json
import sys
from pathlib import Path

import pytest

from test_queue_deliver import enqueue, osa_log, qdata, qitems, set_head
from test_resume import H, mark_reported, env, meta, pilead, sdir, spawn, terminal  # noqa: F401
from test_send_waiting import tree_bytes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))  # `pilead` is importable in-process (test_a_session_with_no_queue_json...)


def stdout_lines(r):
    return r.stdout.splitlines()


def footnotes(out):
    return [ln for ln in out.splitlines() if ln.startswith("  ")]


# ── check: delivers before showing the session ──────────────────────────────────────────────────
def test_check_delivers_the_head_before_the_sessions_own_lines(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    mark_reported(env, sid)
    r = pilead("check", sid)
    lines = stdout_lines(r)
    assert lines[0] == f"sent {sid} packet 0002"
    assert lines[1] == f"delivered queued packet #1 to {sid} as packet 0002 (trigger: check); 1 still queued"
    assert lines[2] == "busy"
    assert f"queued: 1 waiting — pilead queue {sid}" in lines
    assert meta(env, sid)["packets"] == 2
    assert [it["id"] for it in qitems(env, sid)] == [2]


def test_check_all_delivers_at_most_one_item_per_session(env):
    sid1 = spawn(env, log=False)
    enqueue(env, sid1, "GOAL: a1\n")
    enqueue(env, sid1, "GOAL: a2\n")
    sid2 = spawn(env, log=False)
    enqueue(env, sid2, "GOAL: b1\n")
    mark_reported(env, sid1)
    mark_reported(env, sid2)
    r = pilead("check", "--all")
    assert r.stdout.count("delivered queued packet") == 2
    assert meta(env, sid1)["packets"] == 2 and len(qitems(env, sid1)) == 1
    assert meta(env, sid2)["packets"] == 2 and qdata(env, sid2)["items"] == []


@pytest.mark.parametrize("status", ["busy", "stalled", "paused"])
def test_check_delivers_nothing_while_waiting(env, status):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    (sdir(env, sid) / "status").write_text(status + "\n")
    before = qdata(env, sid)
    r = pilead("check", sid)
    assert "delivered queued packet" not in r.stdout
    assert qdata(env, sid) == before
    assert meta(env, sid)["packets"] == 1


def test_check_on_a_closed_session_launches_nothing_and_reports_the_stuck_head(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    pilead("close", sid)
    before_osa = osa_log(env)
    r = pilead("check", sid)
    assert osa_log(env) == before_osa  # no tab launched for a `check`-triggered delivery of a closed session
    lines = stdout_lines(r)
    assert "queued: 2 waiting" in "\n".join(lines)
    stuck = next(l for l in lines if l.startswith("queue: #1 could not be delivered"))
    assert "session is closed" in stuck and "pilead queue" in stuck and "--deliver" in stuck
    head = qitems(env, sid)[0]
    assert head["id"] == 1 and head["last_error"] and "session is closed" in head["last_error"]
    assert meta(env, sid)["packets"] == 1
    # the queued: line comes before the stuck-head line, both after the status line
    assert lines.index("closed") < lines.index(next(l for l in lines if l.startswith("queued: 2 waiting"))) \
        < lines.index(stuck)


def test_check_json_stdout_is_json_only_and_delivery_goes_to_stderr(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    r = pilead("check", sid, "--json")
    data = json.loads(r.stdout)
    assert len(data) == 1
    obj = data[0]
    assert obj["queued"] == 0 and obj["queue_error"] is None
    assert "delivered queued packet #1" in r.stderr and "busy" in r.stderr
    assert meta(env, sid)["packets"] == 2


def test_check_json_names_the_path_of_an_unreadable_queue(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "queue.json").write_text("not json")
    r = pilead("check", sid, "--json")
    obj = json.loads(r.stdout)[0]
    assert obj["queued"] is None
    assert str(sdir(env, sid) / "queue.json") in obj["queue_error"]


def test_check_refs_only_delivers_nothing(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    before = tree_bytes(H(env))
    r = pilead("check", sid, "--refs-only")
    assert len(r.stdout.splitlines()) == 1
    assert tree_bytes(H(env)) == before


def test_a_session_with_no_queue_json_is_unchanged_from_head(env, monkeypatch):
    """No `queue.json` at all: check's stdout is exactly tests/snapshots/check-busy.txt /
    check-reported.txt over the same fixture (run through bin/pilead directly, so stderr — which
    those snapshots do not cover — is captured too: neither fixture session has a pi log, so it is
    the one deterministic `_stderr_unknown` line, untouched by this packet). The ledger is read no
    more than the existing zero-reads case in tests/test_ledger.py asserts for a busy session with
    nothing queued."""
    import os
    import subprocess
    from test_ledger import _count_ledger_reads
    import test_cli_surface as surface
    home = surface.build_fixture(H(env).parent / "surface-home")
    for sid, snap in (("alpha-120000", "check-reported"), ("beta-130000", "check-busy")):
        r = subprocess.run([sys.executable, str(surface.PILEAD), "check", sid], capture_output=True, text=True,
                           env={**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(home)})
        assert r.returncode == 0
        want = (surface.SNAP / f"{snap}.txt").read_text().replace(surface.HOME_TOKEN, str(home))
        assert r.stdout == want
        assert r.stderr == f"pilead: no session log found for {sid}; tokens and context are unknown\n"
        assert not (home / "sessions" / sid / "queue.json").exists()

    counts = _count_ledger_reads(monkeypatch)
    from pilead import cli
    assert cli.main(["check", "beta-130000", "--home", str(home)]) == 0
    assert counts == {"read": 0, "open": 0}


# ── list: the four footnotes, mutually exclusive, table unaffected ─────────────────────────────────
def test_list_footnote_queue_unreadable(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "queue.json").write_text("not json")
    line = next(l for l in footnotes(pilead("list").stdout) if l.startswith(f"  queue unreadable: {sid}"))
    assert "cannot be read; nothing is delivered until it is repaired or removed" in line


def test_list_footnote_queue_stuck_heavy(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    set_head(env, sid, last_error=f"session {sid} is heavy: 210k tokens", error_kind="heavy")
    line = next(l for l in footnotes(pilead("list").stdout) if l.startswith(f"  queue stuck: {sid}"))
    assert "cannot be delivered because the session is heavy" in line
    assert f"pilead send {sid} " in line and "--rotate carries the head to a successor" in line


def test_list_footnote_queue_failing(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    set_head(env, sid, last_error=f"session {sid} is superseded by succ-1", error_kind=None)
    line = next(l for l in footnotes(pilead("list").stdout) if l.startswith(f"  queue failing: {sid}"))
    assert f"could not be delivered — session {sid} is superseded by succ-1" in line


def test_list_footnote_queued_waiting(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    line = next(l for l in footnotes(pilead("list").stdout) if l.startswith(f"  queued: {sid}"))
    assert "waiting for the session to finish its current packet — pilead queue" in line


def test_list_footnote_picks_the_first_match_only(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    set_head(env, sid, last_error="boom", error_kind=None)  # matches "failing" AND would match "queued"
    matches = [l for l in footnotes(pilead("list").stdout) if sid in l]
    assert len(matches) == 1 and matches[0].startswith(f"  queue failing: {sid}")


def executor_table_lines(out):
    """The EXECUTORS header, table and data rows (never a footnote line, never the LEADS section,
    whose LAST ACTIVE column is wall-clock and would make an otherwise-identical run differ)."""
    lines = out.splitlines()
    return [l for l in lines[lines.index("EXECUTORS"):] if not l.startswith("  ")]


def test_list_table_cells_unaffected_by_a_queue(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    table_with = executor_table_lines(pilead("list").stdout)
    (sdir(env, sid) / "queue.json").unlink()
    for p in (sdir(env, sid) / "queue").glob("*"):
        p.unlink()
    (sdir(env, sid) / "queue").rmdir()
    table_without = executor_table_lines(pilead("list").stdout)
    assert table_with == table_without


def test_list_never_delivers_and_leaves_queue_json_byte_identical(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    before = qdata(env, sid)
    pilead("list")
    pilead("list", "--json")
    assert qdata(env, sid) == before
    assert meta(env, sid)["packets"] == 1


def test_list_json_queued_field(env):
    sid1 = spawn(env, log=False)
    enqueue(env, sid1, "GOAL: one\n")
    sid2 = spawn(env, log=False)
    (sdir(env, sid2) / "queue.json").write_text("not json")
    data = json.loads(pilead("list", "--json").stdout)
    rows = {r["sid"]: r for r in data["executors"]}
    assert rows[sid1]["queued"] == 1
    assert rows[sid2]["queued"] is None


# ── status: read-only, gains `queued <n>` / `queued ?` ──────────────────────────────────────────────
def test_status_queued_segment(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    before = tree_bytes(H(env))
    out = pilead("status", sid).stdout
    assert "queued 2" in out
    assert tree_bytes(H(env)) == before


def test_status_queued_unreadable(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "queue.json").write_text("not json")
    before = tree_bytes(H(env))
    out = pilead("status", sid).stdout
    assert "queued ?" in out
    assert tree_bytes(H(env)) == before


def test_status_no_segment_for_missing_or_empty_queue(env):
    sid = spawn(env, log=False)
    out = pilead("status", sid).stdout
    assert "queued" not in out
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    pilead("queue", sid, "--deliver")  # empties it: queue.json now exists with items: []
    assert qdata(env, sid)["items"] == []
    out2 = pilead("status", sid).stdout
    assert "queued" not in out2

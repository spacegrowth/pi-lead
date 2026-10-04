"""Two crash windows of lib/pilead/queue.py (p18c review findings 1 and 2):

B1 — a delivery that died after its claim, followed by someone else's send, must not record that send
as the queued item's packet: recovery compares packet `claimed_packets + 1`'s body with the queued body.
B2 — a `move` that died midway and is run again puts each item in the successor exactly once.

Sessions are spawned through bin/pilead with the fake `pi` and the terminal seam of tests/test_resume.py;
`deliver` and `move` run in-process so they can be made to die where a killed process would. No real tab
is opened or closed and the real pi never runs."""
import json
import sys
from pathlib import Path

import pytest

from test_queue_move import NEW, OLD, home, ledger_events, qjson, three  # noqa: F401
from test_queue_recovery import TEXT, dies, head, inbox_count, queued
from test_resume import H, env, events, mark_reported, meta, pilead, sdir, terminal  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import queue, state  # noqa: E402


def quiet(_line):
    pass


# ── B1: recovery tells its own packet from someone else's ─────────────────────────────────────────
def test_the_footer_mark_recovery_splits_on_is_the_one_write_packet_appends(tmp_path):
    for n in (1, 2, 17):
        assert state.footer(tmp_path / "h", "s-1", n).startswith(queue._FOOTER_MARK)


def test_a_plain_send_after_a_claim_that_died_does_not_swallow_the_queued_packet(env, monkeypatch):
    sid = queued(env)
    real = state.save_meta
    monkeypatch.setattr(state, "save_meta", dies("before"))
    with pytest.raises(KeyboardInterrupt):
        queue.deliver(H(env), sid, "check", out=quiet)
    monkeypatch.setattr(state, "save_meta", real)  # not undo(): that would drop the env fixture's own setenv
    assert meta(env, sid)["packets"] == 1 and head(env, sid)["claimed_packets"] == 1
    # the lead's own plain send takes packet 2 while the queued item is still claimed
    lead = env / "lead.md"
    lead.write_text("GOAL: the lead's own packet\n")
    pilead("send", sid, str(lead))
    assert meta(env, sid)["packets"] == 2
    assert (sdir(env, sid) / "packet-0002.md").read_text().startswith("GOAL: the lead's own packet\n")
    # the session is busy with packet 2: the queued item waits, its claim cleared, nothing recorded
    r = queue.deliver(H(env), sid, "check", out=quiet)
    assert (r.kind, r.reason) == ("nothing", "waiting")
    assert not head(env, sid)["claimed_at"] and "claimed_packets" not in head(env, sid)
    assert [e.get("queue_id") for e in events(env, "packet_sent")] == [None]
    mark_reported(env, sid)
    lines = []
    r = queue.deliver(H(env), sid, "check", out=lines.append)
    assert (r.kind, r.packet, r.recovered) == ("delivered", 3, False)
    assert meta(env, sid)["packets"] == 3
    assert (sdir(env, sid) / "packet-0003.md").read_text().startswith(TEXT)
    assert inbox_count(env, sid) == 1
    lead_sent, queued_sent = events(env, "packet_sent")
    assert (lead_sent["packet"], lead_sent.get("queue_id")) == (2, None)
    assert (queued_sent["packet"], queued_sent["via"], queued_sent["queue_id"]) == (3, "inbox", 1)
    (qd,) = events(env, "queue_delivered")
    assert (qd["packet"], qd["recovered"], qd["remaining"]) == (3, False, 0)
    assert lines[-1] == f"delivered queued packet #1 to {sid} as packet 0003 (trigger: check)"
    assert f"sent {sid} packet 0003" in lines and not any("recovered" in ln for ln in lines)
    assert json.loads((sdir(env, sid) / "queue.json").read_text())["items"] == []


def test_a_numbered_packet_is_still_recovered_when_a_plain_send_followed_it(env, monkeypatch):
    sid = queued(env)
    real = state.save_meta
    monkeypatch.setattr(state, "save_meta", dies("after"))
    with pytest.raises(KeyboardInterrupt):
        queue.deliver(H(env), sid, "check", out=quiet)
    monkeypatch.setattr(state, "save_meta", real)
    assert meta(env, sid)["packets"] == 2 and head(env, sid)["claimed_packets"] == 1
    mark_reported(env, sid)
    lead = env / "lead.md"
    lead.write_text("GOAL: the lead's own packet\n")
    pilead("send", sid, str(lead))
    assert meta(env, sid)["packets"] == 3
    r = queue.deliver(H(env), sid, "check", out=quiet)
    assert (r.kind, r.packet, r.recovered) == ("delivered", 2, True)
    assert meta(env, sid)["packets"] == 3  # nothing sent a second time
    rec = [e for e in events(env, "packet_sent") if e.get("queue_id") == 1]
    assert [(e["packet"], e["via"]) for e in rec] == [(2, "recovered")]


def test_a_rise_whose_packet_cannot_be_read_keeps_the_claim_and_sends_nothing(env, monkeypatch):
    sid = queued(env)
    real = state.save_meta
    monkeypatch.setattr(state, "save_meta", dies("after"))
    with pytest.raises(KeyboardInterrupt):
        queue.deliver(H(env), sid, "check", out=quiet)
    monkeypatch.setattr(state, "save_meta", real)
    (sdir(env, sid) / "packet-0002.md").unlink()
    r = queue.deliver(H(env), sid, "check", out=quiet)
    assert (r.kind, r.reason) == ("nothing", "refused") and "FileNotFoundError" in r.error
    assert head(env, sid)["claimed_at"] and head(env, sid)["claimed_packets"] == 1
    assert meta(env, sid)["packets"] == 2 and events(env, "packet_sent") == []


# ── B2: a move that died midway, run again ────────────────────────────────────────────────────────
def dies_on_call(real, k, after=False):
    """`real`, except that its `k`-th call (1-based) raises KeyboardInterrupt — before it runs, or right
    `after` it has run."""
    calls = []

    def f(*a, **kw):
        calls.append(1)
        if len(calls) != k:
            return real(*a, **kw)
        if after:
            real(*a, **kw)
        raise KeyboardInterrupt
    return f


def moved_sources(home):
    return [Path(it["source"]).name for it in qjson(home, NEW)["items"]]


def test_a_move_that_died_after_the_first_of_three_items_moves_each_once_on_retry(home, tmp_path, monkeypatch):
    three(home, tmp_path)
    real_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", dies_on_call(real_enqueue, 2))
    with pytest.raises(KeyboardInterrupt):
        queue.move(home, OLD, NEW)
    monkeypatch.setattr(queue, "enqueue", real_enqueue)
    assert moved_sources(home) == ["a.md"]
    assert len(qjson(home, OLD)["items"]) == 3  # the old queue is emptied only at the end
    assert not queue.lock_path(home, OLD).exists() and not queue.lock_path(home, NEW).exists()
    assert queue.move(home, OLD, NEW) == (3, 0)
    assert moved_sources(home) == ["a.md", "b.md", "c.md"]
    assert [it["moved_from"] for it in qjson(home, NEW)["items"]] == [
        {"session_id": OLD, "queue_id": q} for q in (1, 2, 3)]
    assert [it["heavy_override"] for it in qjson(home, NEW)["items"]] == [None, "big, but it is the fix", None]
    assert qjson(home, OLD) == {"next_id": 4, "items": []}
    assert [(e["queue_id"], e["new_queue_id"]) for e in ledger_events(home, "queue_moved")] == [(1, 1), (2, 2), (3, 3)]
    assert [e["queue_id"] for e in ledger_events(home, "packet_queued") if e["session_id"] == NEW] == [1, 2, 3]


def test_a_move_that_died_between_enqueue_and_its_ledger_line_moves_each_once_on_retry(home, tmp_path, monkeypatch):
    three(home, tmp_path)
    real_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", dies_on_call(real_enqueue, 2, after=True))
    with pytest.raises(KeyboardInterrupt):
        queue.move(home, OLD, NEW)
    monkeypatch.setattr(queue, "enqueue", real_enqueue)
    assert moved_sources(home) == ["a.md", "b.md"]
    assert [e["queue_id"] for e in ledger_events(home, "queue_moved")] == [1]
    assert queue.move(home, OLD, NEW) == (3, 0)
    assert moved_sources(home) == ["a.md", "b.md", "c.md"]
    assert [(e["queue_id"], e["new_queue_id"]) for e in ledger_events(home, "queue_moved")] == [(1, 1), (2, 2), (3, 3)]
    assert qjson(home, OLD) == {"next_id": 4, "items": []}


def test_a_retried_move_does_not_requeue_an_item_the_successor_already_took(home, tmp_path, monkeypatch):
    three(home, tmp_path)
    real_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", dies_on_call(real_enqueue, 2))
    with pytest.raises(KeyboardInterrupt):
        queue.move(home, OLD, NEW)
    monkeypatch.setattr(queue, "enqueue", real_enqueue)
    queue.cancel(home, NEW, "1")  # gone from the successor's queue (as a delivery would take it): the ledger remembers
    assert qjson(home, NEW)["items"] == []
    assert queue.move(home, OLD, NEW) == (3, 0)
    assert moved_sources(home) == ["b.md", "c.md"]
    assert qjson(home, OLD) == {"next_id": 4, "items": []}

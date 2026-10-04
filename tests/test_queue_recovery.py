"""A claimed delivery is never sent twice (lib/pilead/queue.py, p18 review finding 1), and a slow delivery
keeps its lock (finding 2, the mtime part). Sessions are spawned through bin/pilead with the fake `pi` and
the terminal seam of tests/test_resume.py; `deliver` runs in-process so `send_packet`'s steps can be made
to die where a killed process would. No real tab is opened or closed and the real pi never runs."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from test_resume import H, env, events, mark_reported, meta, pilead, sdir, spawn, terminal  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import queue, state  # noqa: E402
from pilead.verbs import send as send_mod  # noqa: E402

TEXT = "GOAL: the queued one\n"


def queued(env, n=1):
    sid = spawn(env, log=False)
    for i in range(n):
        p = env / f"q{i}.md"
        p.write_text(TEXT if i == 0 else f"GOAL: queued {i}\n")
        pilead("send", sid, str(p), "--when-idle")
    mark_reported(env, sid)
    return sid


def head(env, sid):
    return json.loads((sdir(env, sid) / "queue.json").read_text())["items"][0]


def dies(where):
    """A `state.save_meta` that kills the delivery (KeyboardInterrupt, as a signal would) the first time it
    is called — `before` it writes, or right `after`."""
    real = state.save_meta
    calls = []

    def save_meta(home, m):
        if calls:
            return real(home, m)
        calls.append(1)
        if where == "after":
            real(home, m)
        raise KeyboardInterrupt
    return save_meta


def inbox_count(env, sid):
    p = sdir(env, sid) / "inbox.md"
    return p.read_text().count("the queued one") if p.is_file() else 0


def test_a_claim_stores_the_packet_count(env, monkeypatch):
    sid = queued(env)
    seen = []
    real = send_mod.send_packet

    def spy(home, s, *a, **k):
        seen.append(head(env, s))
        return real(home, s, *a, **k)
    monkeypatch.setattr(send_mod, "send_packet", spy)
    assert queue.deliver(H(env), sid, "manual", out=lambda _l: None).kind == "delivered"
    assert seen[0]["claimed_packets"] == 1 and seen[0]["claimed_at"]


def test_a_delivery_that_died_after_numbering_its_packet_is_recorded_not_sent_again(env, monkeypatch):
    sid = queued(env, 2)
    monkeypatch.setattr(state, "save_meta", dies("after"))
    with pytest.raises(KeyboardInterrupt):
        queue.deliver(H(env), sid, "check", out=lambda _l: None)
    monkeypatch.undo()
    assert meta(env, sid)["packets"] == 2
    assert head(env, sid)["claimed_packets"] == 1 and head(env, sid)["claimed_at"]
    assert events(env, "packet_sent") == []
    lines = []
    r = queue.deliver(H(env), sid, "check", out=lines.append)
    assert (r.kind, r.packet, r.recovered) == ("delivered", 2, True)
    assert meta(env, sid)["packets"] == 2  # rose exactly once overall
    assert inbox_count(env, sid) <= 1
    (sent,) = events(env, "packet_sent")
    assert (sent["packet"], sent["via"], sent["queue_id"]) == (2, "recovered", 1)
    assert sent["source"] == str((env / "q0.md").resolve())
    (qd,) = events(env, "queue_delivered")
    assert qd["recovered"] is True and qd["packet"] == 2 and qd["remaining"] == 1
    assert lines == [f"delivered queued packet #1 to {sid} as packet 0002 (trigger: check); 1 still queued",
                     f"  (recovered after an interrupted delivery — pilead check {sid} shows whether packet 0002 "
                     "reached it)"]
    assert [it["id"] for it in json.loads((sdir(env, sid) / "queue.json").read_text())["items"]] == [2]
    assert not queue.lock_path(H(env), sid).exists()


def test_a_delivery_that_died_before_numbering_its_packet_is_delivered_once(env, monkeypatch):
    sid = queued(env)
    monkeypatch.setattr(state, "save_meta", dies("before"))
    with pytest.raises(KeyboardInterrupt):
        queue.deliver(H(env), sid, "check", out=lambda _l: None)
    monkeypatch.undo()
    assert meta(env, sid)["packets"] == 1 and head(env, sid)["claimed_packets"] == 1
    assert (sdir(env, sid) / "packet-0002.md").is_file()  # half-done: written, not numbered
    r = queue.deliver(H(env), sid, "check", out=lambda _l: None)
    assert (r.kind, r.packet, r.recovered) == ("delivered", 2, False)
    assert meta(env, sid)["packets"] == 2 and inbox_count(env, sid) == 1
    (sent,) = events(env, "packet_sent")
    assert sent["via"] == "inbox" and sent["packet"] == 2
    (qd,) = events(env, "queue_delivered")
    assert qd["recovered"] is False
    assert (sdir(env, sid) / "packet-0002.md").read_text().startswith(TEXT)


def test_a_claimed_head_without_claimed_packets_keeps_the_rule_it_had(env):
    sid = queued(env)
    qp = sdir(env, sid) / "queue.json"
    d = json.loads(qp.read_text())
    d["items"][0]["claimed_at"] = "2026-09-28T09:00:00Z"
    d["items"][0].pop("claimed_packets", None)
    qp.write_text(json.dumps(d, indent=2) + "\n")
    r = queue.deliver(H(env), sid, "manual", out=lambda _l: None)
    assert (r.kind, r.packet, r.recovered) == ("delivered", 2, False)
    (sent,) = events(env, "packet_sent")
    assert sent["via"] == "inbox"


def test_a_slow_delivery_keeps_its_lock(env, monkeypatch):
    sid = queued(env)
    monkeypatch.setattr(queue, "LOCK_STALE_SECONDS", 0.4)
    real = send_mod.send_packet

    def slow(*a, **k):
        time.sleep(1.2)  # three stale windows
        return real(*a, **k)
    monkeypatch.setattr(send_mod, "send_packet", slow)
    results = []
    t = threading.Thread(target=lambda: results.append(queue.deliver(H(env), sid, "manual", out=lambda _l: None)))
    t.start()
    try:
        time.sleep(0.9)
        second = queue.deliver(H(env), sid, "manual", out=lambda _l: None)
    finally:
        t.join(10)
    assert (second.kind, second.reason) == ("nothing", "locked")
    assert results[0].kind == "delivered"
    assert len(events(env, "packet_sent")) == 1 and meta(env, sid)["packets"] == 2
    assert not queue.lock_path(H(env), sid).exists()

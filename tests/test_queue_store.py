"""The queue store, lib/pilead/queue.py, in-process: reading (and refusing an unreadable queue), ids that
are never reused, the body file written once at queue time, and the lock. The session is a bare
directory under the test's home: nothing here launches anything."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import queue  # noqa: E402
from pilead.state import PileadError  # noqa: E402

SID = "topic-1"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    (h / "sessions" / SID).mkdir(parents=True)
    return h


def qfile(home):
    return home / "sessions" / SID / "queue.json"


def lockfile(home):
    return home / "sessions" / SID / "queue.lock"


def events(home, name=None):
    p = home / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if name is None or r["event"] == name]


def src(tmp_path, name="p.md", text="GOAL: the thing to do\nmore\n"):
    p = tmp_path / name
    p.write_text(text)
    return p


# ── read ──────────────────────────────────────────────────────────────────────────────────────────
def test_read_of_no_file_is_empty_with_next_id_1(home):
    assert queue.read(home, SID) == ([], 1)
    assert queue.count(home, SID) == 0
    assert not qfile(home).exists()


UNREADABLE = [
    b"not json at all",
    b"\xff\xfe\x00",
    b"[]",
    b'"a string"',
    b'{"next_id": 2}',
    b'{"next_id": 2, "items": {}}',
    b'{"next_id": 2, "items": [3]}',
    b'{"next_id": 2, "items": [{"summary": "no id"}]}',
    b'{"next_id": 2, "items": [{"id": "1"}]}',
    b'{"next_id": 2, "items": [{"id": 1.5}]}',
    b'{"next_id": 2, "items": [{"id": true}]}',
    b'{"items": [{"id": 1}]}',
    b'{"next_id": "2", "items": [{"id": 1}]}',
    b'{"next_id": 1, "items": [{"id": 1}]}',
    b'{"next_id": 0, "items": []}',
    b'{"next_id": 3, "items": [{"id": 1}, {"id": 5}]}',
]


@pytest.mark.parametrize("raw", UNREADABLE, ids=range(len(UNREADABLE)))
def test_each_unreadable_shape_raises_and_leaves_the_file_as_it_was(home, tmp_path, raw):
    qfile(home).write_bytes(raw)
    with pytest.raises(queue.QueueUnreadable) as e:
        queue.read(home, SID)
    assert e.value.path == qfile(home)
    assert queue.count(home, SID) is None
    with pytest.raises(queue.QueueUnreadable):
        queue.enqueue(home, SID, "GOAL: x\n", src(tmp_path))
    with pytest.raises(queue.QueueUnreadable):
        queue.cancel(home, SID, "all")
    r = queue.deliver(home, SID, "manual")
    assert (r.kind, r.reason) == ("nothing", "unreadable")
    assert qfile(home).read_bytes() == raw
    assert not (home / "sessions" / SID / "queue").exists()
    assert not lockfile(home).exists()
    assert events(home) == []


def test_a_directory_in_place_of_the_file_is_unreadable(home):
    qfile(home).mkdir()
    with pytest.raises(queue.QueueUnreadable):
        queue.read(home, SID)
    assert queue.count(home, SID) is None


# ── enqueue ───────────────────────────────────────────────────────────────────────────────────────
def test_enqueue_stores_the_item_and_the_body(home, tmp_path):
    p = src(tmp_path, text="\n\n  GOAL:   " + "x" * 200 + "\nsecond line\n")
    item = queue.enqueue(home, SID, p.read_text(), p, heavy_override="big but fine")
    assert item["id"] == 1
    assert item["summary"] == "x" * 120
    assert item["source"] == str(p.resolve())
    assert item["heavy_override"] == "big but fine"
    assert item["last_error"] is None and item["claimed_at"] is None and item["error_kind"] is None
    assert Path(item["body_path"]) == (home / "sessions" / SID / "queue" / "0001-queued.md").resolve()
    assert Path(item["body_path"]).read_text() == p.read_text()
    assert item["queued_at"].endswith("Z") and len(item["queued_at"]) == 20
    data = json.loads(qfile(home).read_text())
    assert data == {"next_id": 2, "items": [item]}
    ev = events(home, "packet_queued")
    assert len(ev) == 1
    assert {k: v for k, v in ev[0].items() if k != "ts"} == {"event": "packet_queued", "session_id": SID,
                                                             "queue_id": 1, "source": str(p.resolve())}
    assert not lockfile(home).exists()


def test_a_blank_body_has_no_summary(home, tmp_path):
    p = src(tmp_path, text="\n   \n")
    assert queue.enqueue(home, SID, p.read_text(), p)["summary"] is None


def test_ids_are_never_reused_after_the_queue_was_emptied_by_cancel(home, tmp_path):
    p = src(tmp_path)
    ids = [queue.enqueue(home, SID, f"GOAL: packet {i}\n", p)["id"] for i in range(3)]
    assert ids == [1, 2, 3]
    done = queue.cancel(home, SID, "all")
    assert [it["id"] for it in done.cancelled] == [1, 2, 3] and done.remaining == []
    assert json.loads(qfile(home).read_text()) == {"next_id": 4, "items": []}
    assert queue.enqueue(home, SID, "GOAL: packet 3\n", p)["id"] == 4
    body1 = home / "sessions" / SID / "queue" / "0001-queued.md"
    assert body1.read_text() == "GOAL: packet 0\n"
    assert len(events(home, "queue_cancelled")) == 3


def test_an_existing_body_file_of_the_next_name_fails_and_changes_nothing(home, tmp_path):
    p = src(tmp_path)
    queue.enqueue(home, SID, "GOAL: one\n", p)
    squat = home / "sessions" / SID / "queue" / "0002-queued.md"
    squat.write_text("someone else's file\n")
    before_q, before_l = qfile(home).read_bytes(), (home / "ledger.jsonl").read_bytes()
    with pytest.raises(PileadError, match="0002-queued.md already exists"):
        queue.enqueue(home, SID, "GOAL: two\n", p)
    assert squat.read_text() == "someone else's file\n"
    assert qfile(home).read_bytes() == before_q
    assert (home / "ledger.jsonl").read_bytes() == before_l
    assert not lockfile(home).exists()


def test_a_body_file_left_without_a_queue_file_is_not_overwritten(home, tmp_path):
    (home / "sessions" / SID / "queue").mkdir()
    squat = home / "sessions" / SID / "queue" / "0001-queued.md"
    squat.write_text("left over\n")
    with pytest.raises(PileadError):
        queue.enqueue(home, SID, "GOAL: one\n", src(tmp_path))
    assert squat.read_text() == "left over\n"
    assert not qfile(home).exists()


def test_the_body_is_the_text_at_queue_time(home, tmp_path):
    p = src(tmp_path, text="GOAL: first version\n")
    item = queue.enqueue(home, SID, p.read_text(), p)
    p.write_text("GOAL: edited later\n")
    assert Path(item["body_path"]).read_text() == "GOAL: first version\n"
    p.unlink()
    assert Path(item["body_path"]).read_text() == "GOAL: first version\n"
    assert queue.read(home, SID)[0][0]["summary"] == "first version"


def test_source_is_absolute_for_a_relative_argument(home, tmp_path, monkeypatch):
    (tmp_path / "sub").mkdir()
    src(tmp_path / "sub", name="rel.md")
    monkeypatch.chdir(tmp_path)
    item = queue.enqueue(home, SID, "GOAL: x\n", "sub/rel.md")
    assert os.path.isabs(item["source"])
    assert item["source"] == str((tmp_path / "sub" / "rel.md").resolve())


# ── cancel ────────────────────────────────────────────────────────────────────────────────────────
def test_cancel_one_keeps_the_others_and_the_body(home, tmp_path):
    p = src(tmp_path)
    for i in range(3):
        queue.enqueue(home, SID, f"GOAL: {i}\n", p)
    done = queue.cancel(home, SID, "2")
    assert [it["id"] for it in done.cancelled] == [2]
    assert [it["id"] for it in done.remaining] == [1, 3]
    assert (home / "sessions" / SID / "queue" / "0002-queued.md").is_file()
    ev = events(home, "queue_cancelled")
    assert [(e["queue_id"], e["source"]) for e in ev] == [(2, str(p.resolve()))]


def test_cancel_of_an_id_not_queued_changes_nothing(home, tmp_path):
    queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    before = qfile(home).read_bytes()
    done = queue.cancel(home, SID, "9")
    assert done.missing and not done.cancelled
    assert qfile(home).read_bytes() == before


def test_a_claimed_head_is_never_cancelled(home, tmp_path):
    p = src(tmp_path)
    for i in range(2):
        queue.enqueue(home, SID, f"GOAL: {i}\n", p)
    data = json.loads(qfile(home).read_text())
    data["items"][0]["claimed_at"] = "2026-09-28T10:00:00Z"
    qfile(home).write_text(json.dumps(data))
    done = queue.cancel(home, SID, "all")
    assert [it["id"] for it in done.cancelled] == [2]
    assert [it["id"] for it in done.claimed] == [1]
    assert [it["id"] for it in queue.read(home, SID)[0]] == [1]
    done = queue.cancel(home, SID, "1")
    assert not done.cancelled and [it["id"] for it in done.claimed] == [1] and not done.missing


# ── the lock ──────────────────────────────────────────────────────────────────────────────────────
def test_a_held_fresh_lock_makes_deliver_return_locked(home, tmp_path):
    queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    lockfile(home).write_text("99999\n")
    before = qfile(home).read_bytes()
    r = queue.deliver(home, SID, "manual")
    assert (r.kind, r.reason) == ("nothing", "locked")
    assert qfile(home).read_bytes() == before
    assert lockfile(home).read_text() == "99999\n"  # someone else's lock is left alone


def test_a_held_fresh_lock_makes_enqueue_and_cancel_fail_after_the_wait(home, tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "LOCK_WAIT_SECONDS", 0.3)
    queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    lockfile(home).write_text("99999\n")
    before = qfile(home).read_bytes()
    t0 = time.monotonic()
    with pytest.raises(PileadError, match="queue.lock"):
        queue.enqueue(home, SID, "GOAL: 2\n", src(tmp_path))
    assert time.monotonic() - t0 >= 0.3
    with pytest.raises(PileadError, match="queue.lock"):
        queue.cancel(home, SID, "all")
    assert qfile(home).read_bytes() == before
    assert not (home / "sessions" / SID / "queue" / "0002-queued.md").exists()
    assert lockfile(home).read_text() == "99999\n"


def test_a_lock_older_than_120_seconds_is_taken(home, tmp_path):
    lockfile(home).write_text("99999\n")
    old = time.time() - 121
    os.utime(lockfile(home), (old, old))
    item = queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    assert item["id"] == 1
    assert not lockfile(home).exists()
    assert not list((home / "sessions" / SID).glob("queue.lock*"))


def test_the_lock_holds_the_process_id_while_held(home):
    p = queue.lock(home, SID)
    try:
        assert p == lockfile(home)
        assert lockfile(home).read_text().split()[0] == str(os.getpid())
        assert queue.lock(home, SID) is None
    finally:
        queue.release(p)
    assert not lockfile(home).exists()


def test_the_lock_is_gone_after_a_call_that_raised(home, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(queue, "_write", boom)
    with pytest.raises(OSError):
        queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    assert not lockfile(home).exists()
    assert not (home / "sessions" / SID / "queue" / "0001-queued.md").exists()  # no orphan body
    monkeypatch.undo()
    queue.enqueue(home, SID, "GOAL: 1\n", src(tmp_path))
    monkeypatch.setattr(queue, "_write", boom)
    with pytest.raises(OSError):
        queue.cancel(home, SID, "all")
    assert not lockfile(home).exists()
    r = queue.deliver(home, SID, "manual")  # never raises; its lock is released too
    assert r.kind == "nothing"
    assert not lockfile(home).exists()


# ── stuck_on_heaviness ────────────────────────────────────────────────────────────────────────────
def test_stuck_on_heaviness_reads_the_stored_kind_not_the_text():
    assert queue.stuck_on_heaviness({"last_error": "anything", "error_kind": "heavy"})
    assert not queue.stuck_on_heaviness({"last_error": "session x is heavy: …", "error_kind": None})
    assert not queue.stuck_on_heaviness({"last_error": None, "error_kind": "heavy"})
    assert not queue.stuck_on_heaviness(None)

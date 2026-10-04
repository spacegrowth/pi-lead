"""The queue lock's owner token (lib/pilead/queue.py, p18 review finding 2): the lock file holds one line
`<pid> <token>`, `release` removes only a lock whose token is still its own, a lock in the old pid-only
format is still understood, and a delivery that outlives the stale age keeps its lock. The clock the
lock's age is judged by is patched, so nothing here sleeps for a stale window. Sessions for the delivery
test are spawned through bin/pilead with the fake `pi` and the terminal seam of tests/test_resume.py; no
real tab is opened or closed and the real pi never runs."""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from test_resume import H, env, events, mark_reported, meta, pilead, sdir, spawn, terminal  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import queue  # noqa: E402
from pilead.verbs import send as send_mod  # noqa: E402

SID = "topic-1"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    (h / "sessions" / SID).mkdir(parents=True)
    return h


def lockfile(home):
    return home / "sessions" / SID / "queue.lock"


def test_the_lock_file_is_one_line_of_pid_and_token(home):
    p = queue.lock(home, SID)
    try:
        text = lockfile(home).read_text()
        assert text.endswith("\n") and text.count("\n") == 1
        pid, token = text.split()
        assert pid == str(os.getpid())
        int(token, 16)  # hex
        assert p.token == token
        assert queue._owner(lockfile(home)) == (os.getpid(), token)
    finally:
        queue.release(p)
    q = queue.lock(home, SID)
    try:
        assert q.token != p.token  # a new token each time
    finally:
        queue.release(q)
    assert not lockfile(home).exists()


def test_release_leaves_another_holders_lock_alone(home):
    p = queue.lock(home, SID)
    lockfile(home).write_text("12345 0123456789abcdef\n")  # broken as stale and taken by another process
    queue.release(p)
    assert lockfile(home).read_text() == "12345 0123456789abcdef\n"


def test_release_leaves_an_old_format_lock_alone(home):
    p = queue.lock(home, SID)
    lockfile(home).write_text("99999\n")  # a pid-only lock: its token is absent, so not ours
    queue.release(p)
    assert lockfile(home).read_text() == "99999\n"


def test_an_old_format_lock_is_understood(home):
    lockfile(home).write_text("99999\n")
    assert queue._owner(lockfile(home)) == (99999, None)
    assert queue.lock(home, SID) is None  # fresh: it holds
    old = time.time() - queue.LOCK_STALE_SECONDS - 1
    os.utime(lockfile(home), (old, old))
    p = queue.lock(home, SID)  # stale: it is taken, and the new lock has a token
    try:
        assert p is not None and lockfile(home).read_text().split() == [str(os.getpid()), p.token]
    finally:
        queue.release(p)
    assert not lockfile(home).exists()


def test_release_never_raises(home, tmp_path):
    queue.release(None)
    p = queue.lock(home, SID)
    lockfile(home).unlink()
    queue.release(p)  # already gone
    lockfile(home).mkdir()  # something that cannot be read as a lock file
    queue.release(p)
    assert lockfile(home).is_dir()
    queue.release(tmp_path / "nowhere" / "queue.lock")  # a plain path, no token: nothing removed


def test_the_freshness_beat_does_not_refresh_another_holders_lock(home):
    p = queue.lock(home, SID)
    lockfile(home).write_text("12345 0123456789abcdef\n")
    old = time.time() - 1000
    os.utime(lockfile(home), (old, old))
    queue._touch(p)
    assert lockfile(home).stat().st_mtime == pytest.approx(old, abs=1)


def test_a_delivery_that_outlives_the_stale_age_keeps_its_lock(env, monkeypatch):
    sid = spawn(env, log=False)
    src = env / "q0.md"
    src.write_text("GOAL: the queued one\n")
    pilead("send", sid, str(src), "--when-idle")
    mark_reported(env, sid)

    clock = [time.time()]
    monkeypatch.setattr(queue, "_now", lambda: clock[0])
    monkeypatch.setattr(queue, "LOCK_STALE_SECONDS", 0.2)  # the beat runs every 0.05s of real time
    beat = threading.Event()
    real_touch = queue._touch

    def touch(p):
        real_touch(p)
        beat.set()
    monkeypatch.setattr(queue, "_touch", touch)

    real_send = send_mod.send_packet
    seconds = []

    def slow(*a, **k):
        if seconds:  # a second delivery got in (the lock was broken): send, do not loop again
            return real_send(*a, **k)
        for _ in range(6):  # 6 x 0.15 = 0.9s on the lock's clock: 4.5 stale windows
            beat.clear()
            clock[0] += 0.15
            beat.wait(2)  # the next beat (none comes without the heartbeat, and the lock goes stale)
            seconds.append(queue.deliver(H(env), sid, "manual", out=lambda _l: None))
        return real_send(*a, **k)
    monkeypatch.setattr(send_mod, "send_packet", slow)

    first = queue.deliver(H(env), sid, "manual", out=lambda _l: None)
    assert [(d.kind, d.reason) for d in seconds] == [("nothing", "locked")] * 6
    assert first.kind == "delivered"
    assert len(events(env, "packet_sent")) == 1 and meta(env, sid)["packets"] == 2
    assert not queue.lock_path(H(env), sid).exists()  # its own lock, released by its token
    assert not list(sdir(env, sid).glob("queue.lock*"))

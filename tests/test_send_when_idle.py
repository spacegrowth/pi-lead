"""`pilead send --when-idle` queues a packet for a session that has not finished its current packet (or
that already has packets queued), and sends as a plain send otherwise. Driven through bin/pilead with
the fake `pi` and the terminal seam of tests/test_resume.py (a recording `osascript` stub and a `ps`
stub first on PATH), so no real tab is opened or closed and the real pi never runs."""
import json
from pathlib import Path

import pytest

from test_resume import H, mark_reported, env, events, meta, pilead, sdir, spawn, terminal  # noqa: F401
from test_send_waiting import packet2, tree_bytes, unreadable_line, waiting_line
from test_usage import write_log


def queued_lines(sid, qid, n):
    return (f"queued {sid} #{qid} — delivers when the session has finished its current packet ({n} queued)\n"
            f"  show or cancel: pilead queue {sid} [--cancel ID|all]\n")


def session_files(env, sid):
    """What queueing must not touch: meta.json, status, inbox.md, every packet and refs file."""
    d = sdir(env, sid)
    names = ["meta.json", "status", "inbox.md"] + [p.name for p in d.glob("packet-*.md")] + \
        [p.name for p in d.glob("refs-*.json")]
    return {n: (d / n).read_bytes() for n in names if (d / n).is_file()}


def qitems(env, sid):
    return json.loads((sdir(env, sid) / "queue.json").read_text())["items"]


# ── queued ────────────────────────────────────────────────────────────────────────────────────────
def test_a_busy_session_queues_and_touches_nothing_else(env):
    sid = spawn(env, log=False)
    before = session_files(env, sid)
    n_events = len(events(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2, "--when-idle")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == queued_lines(sid, 1, 1)
    assert session_files(env, sid) == before
    new = events(env)[n_events:]
    assert [e["event"] for e in new] == ["packet_queued"]
    assert new[0]["session_id"] == sid and new[0]["queue_id"] == 1 and new[0]["source"] == str(Path(p2).resolve())
    items = qitems(env, sid)
    assert [it["id"] for it in items] == [1]
    assert Path(items[0]["body_path"]).read_text() == "GOAL: the next thing\n"
    assert not (sdir(env, sid) / "queue.lock").exists()
    assert not list(sdir(env, sid).glob("packet-0002*"))


@pytest.mark.parametrize("status", ["busy", "stalled", "paused"])
def test_each_waiting_status_is_queued(env, status):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text(status + "\n")
    before = session_files(env, sid)
    r = pilead("send", sid, packet2(env), "--when-idle")
    assert r.stdout == queued_lines(sid, 1, 1)
    assert session_files(env, sid) == before
    assert meta(env, sid)["packets"] == 1


def test_a_second_and_third_packet_queue_behind_the_first(env):
    sid = spawn(env, log=False)
    for qid in (1, 2, 3):
        r = pilead("send", sid, packet2(env, f"GOAL: number {qid}\n"), "--when-idle")
        assert r.stdout == queued_lines(sid, qid, qid)
    assert [it["summary"] for it in qitems(env, sid)] == ["number 1", "number 2", "number 3"]


def test_a_heavy_busy_session_is_queued_with_no_refusal(env):
    from test_cli_core import heavy_entries
    sid = spawn(env, log=False)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    r = pilead("send", sid, packet2(env), "--when-idle")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == queued_lines(sid, 1, 1)
    assert events(env, "heavy_override") == []


def test_the_override_reason_is_stored_in_the_item(env):
    sid = spawn(env, log=False)
    pilead("send", sid, packet2(env), "--when-idle", "--heavy-override", "the big refactor needs it")
    assert qitems(env, sid)[0]["heavy_override"] == "the big refactor needs it"
    assert events(env, "heavy_override") == []


def test_a_reported_session_with_a_queued_item_queues_behind_it(env):
    sid = spawn(env, log=False)
    pilead("send", sid, packet2(env, "GOAL: first\n"), "--when-idle")
    mark_reported(env, sid)
    before = session_files(env, sid)
    r = pilead("send", sid, packet2(env, "GOAL: second\n"), "--when-idle")
    assert r.stdout == queued_lines(sid, 2, 2)
    assert session_files(env, sid) == before
    assert [it["summary"] for it in qitems(env, sid)] == ["first", "second"]
    assert events(env, "packet_sent") == []


def test_an_unreadable_status_with_a_queued_item_is_queued(env):
    sid = spawn(env, log=False)
    pilead("send", sid, packet2(env), "--when-idle")
    (sdir(env, sid) / "status").write_text("weird\n")
    r = pilead("send", sid, packet2(env, "GOAL: next\n"), "--when-idle")
    assert r.stdout == queued_lines(sid, 2, 2)


# ── sent as a plain send ──────────────────────────────────────────────────────────────────────────
def _normalise(text, sid):
    return text.replace(sid, "<sid>")


def test_a_reported_session_with_an_empty_queue_gets_a_plain_send(env):
    a = spawn(env, log=False)
    b = spawn(env, log=False)
    mark_reported(env, a)
    mark_reported(env, b)
    pa = packet2(env)
    ra = pilead("send", a, pa)
    rb = pilead("send", b, pa, "--when-idle")
    assert rb.stdout == f"sent {b} packet 0002\n"
    assert _normalise(rb.stdout, b) == _normalise(ra.stdout, a)
    assert _normalise(rb.stderr, b) == _normalise(ra.stderr, a)
    assert sorted(p.name for p in sdir(env, b).iterdir()) == sorted(p.name for p in sdir(env, a).iterdir())
    for name in ("status", "inbox.md", "packet-0002.md"):
        assert _normalise((sdir(env, b) / name).read_text(), b) == _normalise((sdir(env, a) / name).read_text(), a)
    assert meta(env, b)["packets"] == 2
    kinds = lambda sid: [(e["event"], sorted(e)) for e in events(env) if e.get("session_id") == sid]  # noqa: E731
    assert kinds(b)[-3:] == kinds(a)[-3:]
    sent = [e for e in events(env, "packet_sent") if e["session_id"] == b]
    assert sorted(sent[0]) == ["event", "packet", "session_id", "source", "ts", "via"]
    assert not (sdir(env, b) / "queue.json").exists()
    assert events(env, "packet_queued") == []


def test_a_closed_session_with_an_empty_queue_is_relaunched_as_a_plain_send(env):
    sid = spawn(env, log=False)
    pilead("close", sid)
    r = pilead("send", sid, packet2(env), "--when-idle")
    assert r.stdout.splitlines()[0] == f"sent {sid} packet 0002"
    assert r.stdout.splitlines()[-1].startswith("placement=")
    assert events(env, "packet_sent")[-1]["via"] == "relaunch"


# ── refused, nothing changed ──────────────────────────────────────────────────────────────────────
def test_a_blank_override_reason_is_refused(env):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    before = tree_bytes(H(env))
    r = pilead("send", sid, p2, "--when-idle", "--heavy-override", "  ", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == "pilead: --heavy-override needs a reason; an empty one is refused. Nothing was sent.\n"
    assert tree_bytes(H(env)) == before


def test_a_superseded_session_is_refused_with_the_plain_send_line(env):
    sid = spawn(env, log=False)
    pilead("close", sid, "--supersede", "succ-1")
    p2 = packet2(env)
    before = tree_bytes(H(env))
    plain = pilead("send", sid, p2, check=False)
    r = pilead("send", sid, p2, "--when-idle", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == plain.stderr
    assert "succ-1" in r.stderr and len(r.stderr.splitlines()) == 1
    assert tree_bytes(H(env)) == before


def test_a_missing_packet_file_is_refused(env):
    sid = spawn(env, log=False)
    before = tree_bytes(H(env))
    r = pilead("send", sid, str(env / "nope.md"), "--when-idle", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == f"pilead: packet {env / 'nope.md'} not found\n"
    assert tree_bytes(H(env)) == before


@pytest.mark.parametrize("stored", ["busy", "reported"])
def test_an_unreadable_queue_is_refused(env, stored):
    sid = spawn(env, log=False)
    if stored == "reported":
        mark_reported(env, sid)
    q = sdir(env, sid) / "queue.json"
    q.write_text("{ not json")
    p2 = packet2(env)
    before = tree_bytes(H(env))
    r = pilead("send", sid, p2, "--when-idle", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and str(q) in r.stderr
    assert r.stderr.startswith(f"pilead: the queue of {sid} cannot be read: {q}")
    assert tree_bytes(H(env)) == before


@pytest.mark.parametrize("flag", ["--rotate", "--upgrade"])
def test_when_idle_with_rotate_or_upgrade_is_refused(env, flag):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    before = tree_bytes(H(env))
    r = pilead("send", sid, p2, "--when-idle", flag, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and "--when-idle cannot be combined" in r.stderr
    assert tree_bytes(H(env)) == before


def test_an_unreadable_status_with_an_empty_queue_is_refused_with_the_unreadable_line(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text("weird\n")
    p2 = packet2(env)
    before = tree_bytes(H(env))
    r = pilead("send", sid, p2, "--when-idle", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == unreadable_line(sid, "weird", 1)
    assert tree_bytes(H(env)) == before
    assert not (sdir(env, sid) / "queue.json").exists() and not (sdir(env, sid) / "queue").exists()


# ── a plain send is as at HEAD, apart from the waiting line ───────────────────────────────────────
def test_a_plain_send_to_a_busy_session_is_refused_with_the_new_waiting_line(env):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    before = tree_bytes(H(env))
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == waiting_line(sid, "busy", 1, p2)
    assert f"Queue this packet with pilead send {sid} {p2} --when-idle, wait for the report" in r.stderr
    assert tree_bytes(H(env)) == before


def test_a_plain_send_to_a_reported_session_is_as_at_head(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    n_events = len(events(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2)
    assert r.stdout == f"sent {sid} packet 0002\n" and r.stderr == ""
    new = events(env)[n_events:]
    assert [e["event"] for e in new] == ["packet_sent", "usage_snapshot"]
    assert sorted(k for k in new[0] if k not in ("ts", "event")) == ["packet", "session_id", "source", "via"]
    assert new[0]["source"] == str(Path(p2).resolve()) and new[0]["via"] == "inbox"
    assert not (sdir(env, sid) / "queue.json").exists()

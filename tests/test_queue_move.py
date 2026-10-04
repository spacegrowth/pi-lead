"""A queue follows its session's work to a successor: lib/pilead/queue.py `move`, and `pilead send --rotate`
/ `--upgrade` and `pilead restart`, which call it once the successor has launched. Driven through bin/pilead
with the fake `pi` and the terminal seam of tests/test_resume.py (a recording `osascript` stub, a `ps` stub
first on PATH); `move` itself is called in-process on a home this file writes. No real tab is opened or
closed and the real pi never runs."""
import json
import os
import sys
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import H, env, events, mark_reported, meta, pilead, refused, sdir, spawn, terminal  # noqa: F401
from test_rotate import packet2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import queue  # noqa: E402

OLD, NEW = "old-1", "old-1-r2"


# ── move, in-process ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "sessions" / OLD).mkdir(parents=True)
    (h / "sessions" / NEW).mkdir(parents=True)
    return h


def src(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def qjson(home, sid):
    return json.loads(queue.path(home, sid).read_text())


def ledger_events(home, name=None):
    p = home / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if name is None or r["event"] == name]


def three(home, tmp_path):
    a = src(tmp_path, "a.md", "GOAL: a\n")
    b = src(tmp_path, "b.md", "GOAL: b\n")
    c = src(tmp_path, "c.md", "GOAL: c\n")
    queue.enqueue(home, OLD, a.read_text(), a)
    queue.enqueue(home, OLD, b.read_text(), b, heavy_override="big, but it is the fix")
    queue.enqueue(home, OLD, c.read_text(), c)
    d = qjson(home, OLD)
    d["items"][1].update(last_error="session old-1 is heavy: 200k", error_kind="heavy")
    queue.path(home, OLD).write_text(json.dumps(d, indent=2) + "\n")
    return a, b, c


def test_move_keeps_order_gives_new_ids_copies_bodies_and_empties_the_old_queue(home, tmp_path):
    a, b, c = three(home, tmp_path)
    # the successor already has an item of its own: moved ones come after it, with ids of its own
    own = src(tmp_path, "own.md", "GOAL: own\n")
    queue.enqueue(home, NEW, own.read_text(), own)
    old_items = qjson(home, OLD)["items"]
    bodies = {Path(it["body_path"]): Path(it["body_path"]).read_bytes() for it in old_items}
    n_events = len(ledger_events(home))
    assert queue.move(home, OLD, NEW) == (3, 0)
    new = qjson(home, NEW)
    assert [it["id"] for it in new["items"]] == [1, 2, 3, 4] and new["next_id"] == 5
    moved = new["items"][1:]
    assert [it["source"] for it in moved] == [str(a.resolve()), str(b.resolve()), str(c.resolve())]
    assert [Path(it["body_path"]).read_text() for it in moved] == ["GOAL: a\n", "GOAL: b\n", "GOAL: c\n"]
    assert all(Path(it["body_path"]).parent == queue.body_dir(home, NEW).resolve() for it in moved)
    assert [it["heavy_override"] for it in moved] == [None, "big, but it is the fix", None]
    assert all(it["last_error"] is None and it["error_kind"] is None and it["claimed_at"] is None for it in moved)
    # the old queue: no items, next_id unchanged, every body file byte-identical
    assert qjson(home, OLD) == {"next_id": 4, "items": []}
    assert {p: p.read_bytes() for p in bodies} == bodies
    mv = ledger_events(home, "queue_moved")
    assert [{k: v for k, v in e.items() if k != "ts"} for e in mv] == [
        {"event": "queue_moved", "session_id": OLD, "successor": NEW, "queue_id": q, "new_queue_id": nq}
        for q, nq in ((1, 2), (2, 3), (3, 4))]
    assert ledger_events(home, "queue_deduped") == []
    # one queue_moved per item; the successor's queue records each item as queued (enqueue's own event)
    assert [e["event"] for e in ledger_events(home)[n_events:]] == ["packet_queued", "queue_moved"] * 3
    assert not queue.lock_path(home, OLD).exists() and not queue.lock_path(home, NEW).exists()


@pytest.mark.parametrize("by", ["source", "body_path", "relative source"])
def test_move_drops_the_item_that_is_the_packet_being_sent(home, tmp_path, monkeypatch, by):
    a, b, c = three(home, tmp_path)
    if by == "source":
        sending = str(b.resolve())
    elif by == "body_path":
        sending = qjson(home, OLD)["items"][1]["body_path"]
    else:
        monkeypatch.chdir(tmp_path)
        sending = "b.md"
    assert queue.move(home, OLD, NEW, sending=sending) == (2, 1)
    assert [it["source"] for it in qjson(home, NEW)["items"]] == [str(a.resolve()), str(c.resolve())]
    (dd,) = ledger_events(home, "queue_deduped")
    assert {k: v for k, v in dd.items() if k != "ts"} == {
        "event": "queue_deduped", "session_id": OLD, "successor": NEW, "queue_id": 2, "source": str(b.resolve())}
    assert [e["queue_id"] for e in ledger_events(home, "queue_moved")] == [1, 3]
    assert qjson(home, OLD) == {"next_id": 4, "items": []}


def test_move_with_no_queue_file_writes_nothing(home):
    before = tree(home)
    assert queue.move(home, OLD, NEW) == (0, 0)
    assert tree(home) == before


def test_move_of_an_unreadable_queue_raises_and_changes_nothing(home):
    queue.path(home, OLD).write_text("{not json")
    before = tree(home)
    with pytest.raises(queue.QueueUnreadable):
        queue.move(home, OLD, NEW)
    assert tree(home) == before


def test_move_of_a_claimed_head_raises_and_changes_nothing(home, tmp_path):
    three(home, tmp_path)
    d = qjson(home, OLD)
    d["items"][0]["claimed_at"] = "2026-09-29T10:00:00Z"
    queue.path(home, OLD).write_text(json.dumps(d, indent=2) + "\n")
    before = tree(home)
    with pytest.raises(queue.PileadError, match="--deliver"):
        queue.move(home, OLD, NEW)
    assert tree(home) == before


# ── send --rotate / --upgrade and restart ──────────────────────────────────────────────────────────
def queue_two(env, sid):
    """Two packets queued with send --when-idle while `sid` works packet 1; then packet 1 is reported."""
    paths = []
    for i in (1, 2):
        p = env / f"queued-{i}.md"
        p.write_text(f"GOAL: queued {i}\n")
        pilead("send", sid, str(p), "--when-idle")
        paths.append(p)
    mark_reported(env, sid)
    return paths


def run_verb(env, verb, sid, check=True, extra_env=None):
    if verb == "restart":
        return pilead("restart", sid, check=check, env=extra_env)
    return pilead("send", sid, packet2(env), f"--{verb}", check=check, env=extra_env)


LAST = {"rotate": "successor: {succ}", "upgrade": "successor: {succ}",
        "restart": "restarted {sid} as {succ} — packet 0001 runs again as a new conversation"}


@pytest.mark.parametrize("verb", ["rotate", "upgrade", "restart"])
def test_each_verb_moves_a_queue_of_two_and_says_so(env, verb):
    sid = spawn(env)
    paths = queue_two(env, sid)
    r = run_verb(env, verb, sid)
    succ = f"{sid}-r2"
    lines = r.stdout.splitlines()
    assert lines[-1] == LAST[verb].format(sid=sid, succ=succ)
    assert lines[-2] == f"moved 2 queued packet(s) from {sid} to {succ}"
    assert "not queued a second time" not in r.stdout
    assert [it["source"] for it in json.loads((sdir(env, succ) / "queue.json").read_text())["items"]] == \
        [str(p.resolve()) for p in paths]
    assert json.loads((sdir(env, sid) / "queue.json").read_text()) == {"next_id": 3, "items": []}
    assert len(events(env, "queue_moved")) == 2


def test_rotating_the_packet_that_is_queued_does_not_queue_it_a_second_time(env):
    sid = spawn(env)
    p2 = packet2(env)
    pilead("send", sid, p2, "--when-idle")
    other = env / "other.md"
    other.write_text("GOAL: other\n")
    pilead("send", sid, str(other), "--when-idle")
    mark_reported(env, sid)
    r = pilead("send", sid, p2, "--rotate")
    succ = f"{sid}-r2"
    lines = r.stdout.splitlines()
    assert lines[-3:] == [f"moved 1 queued packet(s) from {sid} to {succ}",
                          "1 queued packet(s) were the packet being sent — not queued a second time",
                          f"successor: {succ}"]
    assert [it["source"] for it in json.loads((sdir(env, succ) / "queue.json").read_text())["items"]] == \
        [str(other.resolve())]


@pytest.mark.parametrize("verb", ["rotate", "upgrade", "restart"])
@pytest.mark.parametrize("empty", ["no queue file", "no items"])
def test_with_an_empty_queue_each_verb_prints_what_it_did_before(env, verb, empty):
    sid = spawn(env)
    if empty == "no items":
        queue_two(env, sid)
        pilead("queue", sid, "--cancel", "all")
    mark_reported(env, sid)
    r = run_verb(env, verb, sid)
    succ = f"{sid}-r2"
    lines = r.stdout.splitlines()
    assert lines[-1] == LAST[verb].format(sid=sid, succ=succ)
    assert "queued packet(s)" not in r.stdout and r.stderr == ""
    if verb == "restart":
        assert lines[-2].startswith("placement=")
    else:
        assert lines[-3].startswith("seed=") and lines[-2].startswith("placement=")
    assert not (sdir(env, succ) / "queue.json").exists()
    assert events(env, "queue_moved") == [] and events(env, "queue_deduped") == []


def unreadable(env, sid):
    (sdir(env, sid) / "queue.json").write_text("{not json")


def claimed(env, sid):
    qp = sdir(env, sid) / "queue.json"
    d = json.loads(qp.read_text())
    d["items"][0]["claimed_at"] = "2026-09-29T10:00:00Z"
    qp.write_text(json.dumps(d, indent=2) + "\n")


@pytest.mark.parametrize("verb", ["rotate", "upgrade", "restart"])
@pytest.mark.parametrize("how", ["unreadable", "claimed"])
def test_an_unreadable_queue_or_a_claimed_head_refuses_each_verb_and_changes_nothing(env, verb, how):
    sid = spawn(env)
    queue_two(env, sid)
    (unreadable if how == "unreadable" else claimed)(env, sid)
    before = tree(H(env))
    r = run_verb(env, verb, sid, check=False)
    if how == "unreadable":
        refused(r, str(sdir(env, sid) / "queue.json"))
    else:
        refused(r, f"pilead queue {sid} --deliver")
    assert tree(H(env)) == before


@pytest.mark.parametrize("verb", ["rotate", "restart"])
def test_a_failed_launch_of_the_successor_leaves_the_old_queue_byte_identical(env, verb):
    sid = spawn(env)
    queue_two(env, sid)
    d = sdir(env, sid)
    q_before = {k: v for k, v in tree(d).items() if k == "queue.json" or k.startswith("queue/") or k == "queue"}
    r = run_verb(env, verb, sid, check=False, extra_env={"FAKE_SPAWN": "fail"})
    assert r.returncode == 1
    q_after = {k: v for k, v in tree(d).items() if k == "queue.json" or k.startswith("queue/") or k == "queue"}
    assert q_after == q_before and len(q_before) == 4  # queue.json, queue/, two bodies
    assert events(env, "queue_moved") == []
    assert "queued packet(s)" not in r.stdout

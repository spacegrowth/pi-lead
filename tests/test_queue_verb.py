"""`pilead queue <sid> [--cancel ID|all] [--deliver] [--trigger …] [--json]`, through bin/pilead with the
fake `pi` and the terminal seam of tests/test_resume.py. No real tab is opened or closed and the real
pi never runs."""
import json

import pytest

from test_resume import H, env, events, mark_reported, meta, pilead, sdir, spawn, terminal  # noqa: F401
from test_send_waiting import tree_bytes


def enqueue(env, sid, text):
    p = env / f"q-{abs(hash(text))}.md"
    p.write_text(text)
    pilead("send", sid, str(p), "--when-idle")
    return p


def qpath(env, sid):
    return sdir(env, sid) / "queue.json"


def qitems(env, sid):
    return json.loads(qpath(env, sid).read_text())["items"]


def edit_item(env, sid, i, **fields):
    d = json.loads(qpath(env, sid).read_text())
    d["items"][i].update(fields)
    qpath(env, sid).write_text(json.dumps(d, indent=2) + "\n")


def unreadable_text(env, sid):
    return (f"the queue of {sid} cannot be read: {qpath(env, sid)} — nothing was changed; the packet bodies are "
            f"under {sdir(env, sid) / 'queue'}")


@pytest.fixture
def sid(env):
    return spawn(env, log=False)


# ── the listing ───────────────────────────────────────────────────────────────────────────────────
def test_nothing_queued(env, sid):
    r = pilead("queue", sid)
    assert r.stdout == f"{sid}: nothing queued\n" and r.stderr == ""


def test_the_listing(env, sid):
    enqueue(env, sid, "GOAL: first thing\nbody\n")
    enqueue(env, sid, "\n\n")
    enqueue(env, sid, "third, no goal prefix\n")
    edit_item(env, sid, 0, claimed_at="2026-09-28T10:00:00Z", last_error="session x is heavy: 164k")
    items = qitems(env, sid)
    r = pilead("queue", sid)
    q = [it["queued_at"] for it in items]
    b = [it["body_path"] for it in items]
    assert r.stdout == (
        f"{sid}: 3 queued packet(s), delivered oldest first, one each time the session finishes a packet\n"
        f"  #1  queued {q[0]}  first thing\n"
        f"       body: {b[0]}\n"
        f"       being delivered since 2026-09-28T10:00:00Z\n"
        f"       last delivery attempt failed: session x is heavy: 164k\n"
        f"  #2  queued {q[1]}  (no summary)\n"
        f"       body: {b[1]}\n"
        f"  #3  queued {q[2]}  third, no goal prefix\n"
        f"       body: {b[2]}\n"
        f"  cancel with: pilead queue {sid} --cancel <id|all>\n")
    assert b[0] == str(sdir(env, sid) / "queue" / "0001-queued.md")


def test_json_for_a_queue_and_an_empty_one(env, sid):
    r = pilead("queue", sid, "--json")
    assert json.loads(r.stdout) == {"sid": sid, "queued": 0, "error": None, "items": []}
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    r = pilead("queue", sid, "--json")
    assert json.loads(r.stdout) == {"sid": sid, "queued": 2, "error": None, "items": qitems(env, sid)}
    pilead("queue", sid, "--cancel", "all")
    assert json.loads(pilead("queue", sid, "--json").stdout)["queued"] == 0


def test_an_unreadable_queue_is_said_to_be_so(env, sid):
    qpath(env, sid).write_text('{"next_id": 1, "items": [{"id": 1}]}')
    before = tree_bytes(H(env))
    r = pilead("queue", sid, check=False)
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr == f"pilead: {unreadable_text(env, sid)}\n"
    r = pilead("queue", sid, "--json", check=False)
    assert r.returncode == 1
    assert json.loads(r.stdout) == {"sid": sid, "queued": None, "error": unreadable_text(env, sid), "items": None}
    for args in (["--cancel", "all"], ["--cancel", "1"], ["--deliver"], ["--deliver", "--trigger", "check"]):
        r = pilead("queue", sid, *args, check=False)
        assert r.returncode == 1 and r.stdout == ""
        assert r.stderr == f"pilead: {unreadable_text(env, sid)}\n"
    assert tree_bytes(H(env)) == before


def test_an_unknown_session_exits_2(env):
    r = pilead("queue", "nobody-1", check=False)
    assert r.returncode == 2 and r.stderr == "pilead: unknown session nobody-1\n"


# ── option rules ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [["--cancel", "all", "--deliver"], ["--trigger", "check"],
                                  ["--cancel", "1", "--trigger", "check"], ["--json", "--deliver"],
                                  ["--json", "--cancel", "all"], ["--deliver", "--trigger", "later"]])
def test_option_combinations_that_exit_2(env, sid, args):
    enqueue(env, sid, "GOAL: one\n")
    before = tree_bytes(H(env))
    r = pilead("queue", sid, *args, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert tree_bytes(H(env)) == before


# ── cancel ────────────────────────────────────────────────────────────────────────────────────────
def test_cancel_by_id(env, sid):
    for t in ("GOAL: a\n", "GOAL: b\n", "GOAL: c\n"):
        enqueue(env, sid, t)
    r = pilead("queue", sid, "--cancel", "2")
    assert r.stdout == f"cancelled 1 queued packet(s) for {sid} — 2 still queued\n"
    assert [it["id"] for it in qitems(env, sid)] == [1, 3]
    ev = events(env, "queue_cancelled")
    assert [(e["session_id"], e["queue_id"]) for e in ev] == [(sid, 2)] and "source" in ev[0]
    assert (sdir(env, sid) / "queue" / "0002-queued.md").is_file()


def test_cancel_all(env, sid):
    for t in ("GOAL: a\n", "GOAL: b\n"):
        enqueue(env, sid, t)
    r = pilead("queue", sid, "--cancel", "all")
    assert r.stdout == f"cancelled 2 queued packet(s) for {sid}\n"
    assert qitems(env, sid) == []
    assert [e["queue_id"] for e in events(env, "queue_cancelled")] == [1, 2]


def test_cancel_of_an_unknown_id_lists_what_is_queued(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    enqueue(env, sid, "GOAL: b\n")
    before = tree_bytes(H(env))
    r = pilead("queue", sid, "--cancel", "9", check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == f"pilead: {sid} has no queued packet #9 (queued: #1, #2). Nothing was cancelled.\n"
    assert tree_bytes(H(env)) == before


def test_cancel_with_a_value_that_is_not_an_id(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    before = tree_bytes(H(env))
    for bad in ("two", "-1", "1.0", "ALL", ""):
        r = pilead("queue", sid, "--cancel", bad, check=False)
        assert r.returncode == 2 and r.stdout == "", bad
    assert tree_bytes(H(env)) == before


def test_cancel_on_an_empty_queue(env, sid):
    for which in ("all", "3"):
        r = pilead("queue", sid, "--cancel", which)
        assert r.returncode == 0 and r.stdout == f"{sid} has nothing queued — nothing to cancel\n"
    assert not qpath(env, sid).exists()


def test_a_claimed_head_is_not_cancelled(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    edit_item(env, sid, 0, claimed_at="2026-09-28T10:00:00Z")
    before = tree_bytes(H(env))
    r = pilead("queue", sid, "--cancel", "1", check=False)
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr == "#1 is being delivered and was not cancelled\n"
    r = pilead("queue", sid, "--cancel", "all", check=False)
    assert r.returncode == 1 and r.stderr == "#1 is being delivered and was not cancelled\n"
    assert tree_bytes(H(env)) == before
    enqueue(env, sid, "GOAL: b\n")
    r = pilead("queue", sid, "--cancel", "all")
    assert r.returncode == 0
    assert r.stdout == f"cancelled 1 queued packet(s) for {sid} — 1 still queued\n"
    assert r.stderr == "#1 is being delivered and was not cancelled\n"
    assert [it["id"] for it in qitems(env, sid)] == [1]


# ── deliver ───────────────────────────────────────────────────────────────────────────────────────
def test_deliver_with_nothing_queued(env, sid):
    r = pilead("queue", sid, "--deliver")
    assert r.returncode == 0 and r.stdout == f"{sid}: nothing queued\n"


def test_deliver_while_the_session_works(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    r = pilead("queue", sid, "--deliver")
    assert r.returncode == 0 and r.stdout == f"{sid}: still busy — the queue waits\n"


def test_deliver_while_another_delivery_runs(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    mark_reported(env, sid)
    (sdir(env, sid) / "queue.lock").write_text("99999\n")
    r = pilead("queue", sid, "--deliver")
    assert r.returncode == 0 and r.stdout == f"{sid}: another delivery is running\n"
    assert meta(env, sid)["packets"] == 1


def test_deliver_refused(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    pilead("close", sid, "--supersede", "succ-1")
    r = pilead("queue", sid, "--deliver", check=False)
    assert r.returncode == 1 and r.stdout == ""
    last = qitems(env, sid)[0]["last_error"]
    assert r.stderr == f"{sid}: not delivered — {last}\n" and "succ-1" in last
    listing = pilead("queue", sid).stdout
    assert f"       last delivery attempt failed: {last}\n" in listing


def test_deliver_success(env, sid):
    enqueue(env, sid, "GOAL: a\n")
    enqueue(env, sid, "GOAL: b\n")
    mark_reported(env, sid)
    r = pilead("queue", sid, "--deliver", "--trigger", "settle")
    assert r.returncode == 0
    assert r.stdout == (f"sent {sid} packet 0002\n"
                        f"delivered queued packet #1 to {sid} as packet 0002 (trigger: settle); 1 still queued\n")
    assert events(env, "queue_delivered")[0]["trigger"] == "settle"

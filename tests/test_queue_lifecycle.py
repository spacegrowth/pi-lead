"""What `pilead close`, `pilead retire` and `pilead prune` do with a session's queue (lib/pilead/queue.py):
close and retire leave it as it is and say so on stderr; prune never removes a session whose queue has
items or cannot be read. close and retire run through bin/pilead with the fake `pi` and the terminal seam
of tests/test_resume.py; prune runs in-process with tests/test_prune.py's terminal stub. No real tab is
opened or closed and the real pi never runs."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import H, env, events, mark_reported, meta, pilead, sdir, spawn, terminal  # noqa: F401
from test_prune import LIVE_UUID, age_files, prune, session as prune_session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import queue, seed  # noqa: E402
from pilead.launch import backend  # noqa: E402


def queued_session(env, n=2):
    sid = spawn(env)
    for i in range(n):
        p = env / f"queued-{i + 1}.md"
        p.write_text(f"GOAL: queued {i + 1}\n")
        pilead("send", sid, str(p), "--when-idle")
    mark_reported(env, sid)
    return sid


def queue_files(env, sid):
    d = sdir(env, sid)
    return {k: v[1] for k, v in tree(d).items() if k == "queue.json" or k == "queue" or k.startswith("queue/")}


def retire_stdout(env, sid):
    p = sdir(env, sid) / "successor-seed.md"
    return (f"closed {sid} (superseded by successor-seed)\n"
            f"retired {sid} — successor seed (1 packet(s)) at:\n"
            f"  {p}\n"
            f"  spawn its successor with: {seed.spawn_command(meta(env, sid), sid)}\n")


# ── close and retire ──────────────────────────────────────────────────────────────────────────────
def test_close_with_queued_packets_says_so_on_stderr_and_leaves_the_queue(env):
    sid = queued_session(env)
    before = queue_files(env, sid)
    r = pilead("close", sid)
    assert r.stdout == f"closed {sid}\n"
    assert r.stderr == (f"pilead: {sid} has 2 queued packet(s); none will be delivered while it is closed — "
                        f"pilead queue {sid} --deliver reopens it and delivers the first, pilead queue {sid} "
                        "--cancel all empties the queue\n")
    assert queue_files(env, sid) == before


def test_retire_with_queued_packets_says_so_on_stderr_and_leaves_the_queue(env):
    sid = queued_session(env, 1)
    before = queue_files(env, sid)
    r = pilead("retire", sid)
    assert r.stdout == retire_stdout(env, sid)
    assert r.stderr == (f"pilead: {sid} has 1 queued packet(s); a retired session receives no packet — send each "
                        f"one to the successor (bodies under {sdir(env, sid) / 'queue'}), then pilead queue {sid} "
                        "--cancel all\n")
    assert queue_files(env, sid) == before


@pytest.mark.parametrize("verb", ["close", "retire"])
def test_an_unreadable_queue_is_named_and_left_byte_identical(env, verb):
    sid = queued_session(env, 1)
    qp = sdir(env, sid) / "queue.json"
    qp.write_text("{not json")
    before = queue_files(env, sid)
    r = pilead(verb, sid)
    assert r.stdout == (f"closed {sid}\n" if verb == "close" else retire_stdout(env, sid))
    ends = "none will be delivered while it is closed" if verb == "close" else "a retired session receives no packet"
    assert r.stderr == f"pilead: {sid} has a queue that cannot be read ({qp}); {ends} — inspect that file\n"
    assert queue_files(env, sid) == before


@pytest.mark.parametrize("verb", ["close", "retire"])
@pytest.mark.parametrize("empty", ["no queue file", "no items"])
def test_with_no_queued_packet_stderr_is_what_it_was(env, verb, empty):
    sid = queued_session(env, 1) if empty == "no items" else spawn(env)
    if empty == "no items":
        pilead("queue", sid, "--cancel", "all")
    mark_reported(env, sid)
    r = pilead(verb, sid)
    assert r.stderr == ""
    assert r.stdout == (f"closed {sid}\n" if verb == "close" else retire_stdout(env, sid))


# ── prune ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def pruning(monkeypatch, tmp_path):
    """tests/test_prune.py's terminal stub (in-process: the iterm backend's `running` / `run_osascript`)."""
    it = backend.by_name("iterm")

    def run_osascript(script, timeout=None):
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    monkeypatch.chdir(tmp_path)


def put_queue(s, text=None):
    """sessions/<sid>/queue.json with one item (or `text` as the file), and its body, aged with the rest."""
    (s / "queue").mkdir()
    body = s / "queue" / "0001-queued.md"
    body.write_text("GOAL: q\n")
    item = {"id": 1, "queued_at": "2026-09-01T00:00:00Z", "source": str(body), "body_path": str(body),
            "summary": "q", "heavy_override": None, "last_error": None, "error_kind": None, "claimed_at": None}
    (s / "queue.json").write_text(text if text is not None else json.dumps({"next_id": 2, "items": [item]}))
    age_files(s, 10)


def test_prune_keeps_a_session_with_a_queued_packet_or_an_unreadable_queue(tmp_path, capsys, pruning):
    h = H(tmp_path)
    q = prune_session(h, "s-queued")
    put_queue(q)
    u = prune_session(h, "s-unreadable")
    put_queue(u, text="{not json")
    prune_session(h, "s-plain")
    before = {sid: tree(h / "sessions" / sid) for sid in ("s-queued", "s-unreadable")}
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0
    assert "  kept s-queued: queued packets" in out.splitlines()
    assert "  kept s-unreadable: queue unreadable" in out.splitlines()
    assert not (h / "sessions" / "s-plain").exists()
    assert {sid: tree(h / "sessions" / sid) for sid in before} == before

    # its queue emptied, the same session is removed
    (q / "queue.json").write_text(json.dumps({"next_id": 2, "items": []}))
    age_files(q, 10)
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0 and not (h / "sessions" / "s-queued").exists()
    assert "  s-queued (closed, " in out
    assert "  kept s-unreadable: queue unreadable" in out.splitlines()
    assert queue.count(h, "s-unreadable") is None

"""Delivery of a queued packet (lib/pilead/queue.py `deliver`): oldest first, one per call, only when the
session has finished its current packet, through the plain send — and never twice, never lost, never
recorded as delivered when it was not. Driven through `pilead queue <sid> --deliver` (bin/pilead) with
the fake `pi` and the terminal seam of tests/test_resume.py; the cases that patch `send_packet` call
`deliver` in-process. No real tab is opened or closed and the real pi never runs."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_cli_core import _git, heavy_entries
from test_resume import H, mark_reported, PILEAD, env, events, meta, pilead, sdir, spawn, terminal  # noqa: F401
from test_send_waiting import packet2, tree_bytes
from test_usage import write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import queue  # noqa: E402
from pilead.verbs import send as send_mod  # noqa: E402


def enqueue(env, sid, text, *extra):
    p = env / f"q-{abs(hash(text))}.md"
    p.write_text(text)
    pilead("send", sid, str(p), "--when-idle", *extra)
    return p


def qdata(env, sid):
    return json.loads((sdir(env, sid) / "queue.json").read_text())


def qitems(env, sid):
    return qdata(env, sid)["items"]


def set_head(env, sid, **fields):
    d = qdata(env, sid)
    d["items"][0].update(fields)
    (sdir(env, sid) / "queue.json").write_text(json.dumps(d, indent=2) + "\n")


def deliver(sid, *extra, check=False):
    return pilead("queue", sid, "--deliver", *extra, check=check)


def ledger_bytes(env):
    return (H(env) / "ledger.jsonl").read_bytes()


def osa_log(env):
    p = env / "osa.log"
    return p.read_text() if p.is_file() else ""


# ── oldest first, one per call ────────────────────────────────────────────────────────────────────
def test_oldest_first_one_per_call(env):
    sid = spawn(env, log=False)
    src1 = enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    enqueue(env, sid, "GOAL: three\n")
    mark_reported(env, sid)
    r = deliver(sid)
    assert r.returncode == 0, r.stderr
    assert r.stdout == (f"sent {sid} packet 0002\n"
                        f"delivered queued packet #1 to {sid} as packet 0002 (trigger: manual); 2 still queued\n")
    assert meta(env, sid)["packets"] == 2
    assert [it["id"] for it in qitems(env, sid)] == [2, 3]
    assert (sdir(env, sid) / "packet-0002.md").read_text().startswith("GOAL: one\n")
    sent = events(env, "packet_sent")[-1]
    assert sent["source"] == str(src1.resolve()) and sent["queue_id"] == 1
    # at once again: the session is busy now, and nothing is written
    before = tree_bytes(H(env))
    r = deliver(sid)
    assert r.returncode == 0 and r.stdout == f"{sid}: still busy — the queue waits\n"
    assert tree_bytes(H(env)) == before


def test_all_three_over_three_reports_then_the_next_id_is_4(env):
    sid = spawn(env, log=False)
    for i in (1, 2, 3):
        enqueue(env, sid, f"GOAL: number {i}\n")
    for n in (2, 3, 4):
        mark_reported(env, sid)
        r = deliver(sid, check=True)
        assert f"as packet {n:04d}" in r.stdout
    assert meta(env, sid)["packets"] == 4
    assert qdata(env, sid) == {"next_id": 4, "items": []}
    assert deliver(sid).stdout == f"{sid}: nothing queued\n"
    r = pilead("send", sid, packet2(env), "--when-idle")
    assert "#4 " in r.stdout
    assert (sdir(env, sid) / "queue" / "0001-queued.md").read_text() == "GOAL: number 1\n"
    assert [e["queue_id"] for e in events(env, "queue_delivered")] == [1, 2, 3]
    assert [e["remaining"] for e in events(env, "queue_delivered")] == [2, 1, 0]


def test_a_delivered_packet_gets_the_third_packet_note(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: a\n")
    enqueue(env, sid, "GOAL: b\n")
    mark_reported(env, sid)
    assert "this is packet" not in deliver(sid, check=True).stderr
    mark_reported(env, sid)
    r = deliver(sid, check=True)
    assert r.stderr.startswith("pilead: this is packet 3 into a sonnet session")


@pytest.mark.parametrize("status", ["busy", "stalled", "paused"])
def test_a_waiting_status_waits_and_writes_nothing(env, status):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    (sdir(env, sid) / "status").write_text(status + "\n")
    before = tree_bytes(H(env))
    for trigger in ("manual", "check", "settle"):
        r = deliver(sid, "--trigger", trigger)
        assert r.returncode == 0 and r.stdout == f"{sid}: still {status} — the queue waits\n"
    assert tree_bytes(H(env)) == before


# ── delivery is a plain send ──────────────────────────────────────────────────────────────────────
def test_the_delivered_packet_has_its_footer_refs_baseline_and_ledger(env):
    _git(env / "wt", "init", "-q")
    _git(env / "wt", "commit", "-q", "--allow-empty", "-m", "seed")
    sid = spawn(env, log=False)
    src = enqueue(env, sid, "GOAL: the queued one\n")
    mark_reported(env, sid)
    deliver(sid, "--trigger", "check", check=True)
    packet = (sdir(env, sid) / "packet-0002.md").read_text()
    assert str(sdir(env, sid) / "report-0002.md") in packet
    assert "report-0001.md" not in packet
    assert (sdir(env, sid) / "refs-0002.json").is_file()
    assert (sdir(env, sid) / "inbox.md").read_text().endswith(packet)
    sent = [e for e in events(env, "packet_sent") if e["packet"] == 2]
    assert len(sent) == 1
    assert sent[0]["source"] == str(src.resolve()) and sent[0]["queue_id"] == 1 and sent[0]["via"] == "inbox"
    qd = events(env, "queue_delivered")
    assert len(qd) == 1
    assert sorted(k for k in qd[0] if k not in ("ts", "event")) == \
        ["packet", "queue_id", "recovered", "remaining", "session_id", "trigger"]
    assert (qd[0]["session_id"], qd[0]["queue_id"], qd[0]["packet"], qd[0]["trigger"], qd[0]["remaining"],
            qd[0]["recovered"]) == (sid, 1, 2, "check", 0, False)
    kinds = [e["event"] for e in events(env)]
    assert kinds.index("packet_sent", kinds.index("packet_queued")) < kinds.index("queue_delivered")


# ── refusals ──────────────────────────────────────────────────────────────────────────────────────
def test_a_heavy_session_keeps_the_head_and_logs_the_failure_once(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    r = deliver(sid)
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr.startswith(f"{sid}: not delivered — session {sid} is heavy:")
    head = qitems(env, sid)[0]
    assert head["id"] == 1 and head["claimed_at"] is None
    assert head["last_error"].startswith(f"session {sid} is heavy:") and len(head["last_error"]) <= 300
    assert "\n" not in head["last_error"]
    assert head["error_kind"] == "heavy" and queue.stuck_on_heaviness(head)
    failed = events(env, "queue_delivery_failed")
    assert len(failed) == 1
    assert (failed[0]["queue_id"], failed[0]["trigger"], failed[0]["error"]) == (1, "manual", head["last_error"])
    before = ledger_bytes(env)
    for trigger in ("check", "manual"):
        assert deliver(sid, "--trigger", trigger).returncode == 1
    assert ledger_bytes(env) == before
    assert meta(env, sid)["packets"] == 1
    assert events(env, "queue_delivered") == []


def test_an_item_queued_with_an_override_is_delivered_to_a_heavy_session(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n", "--heavy-override", "finishing touches only")
    mark_reported(env, sid)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    r = deliver(sid, check=True)
    assert f"delivered queued packet #1 to {sid} as packet 0002" in r.stdout
    ho = events(env, "heavy_override")
    assert len(ho) == 1 and ho[0]["reason"] == "finishing touches only" and ho[0]["packet"] == 2


def test_a_superseded_session_is_refused_and_the_head_kept(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    pilead("close", sid, "--supersede", "succ-1")
    before_osa = osa_log(env)
    r = deliver(sid)
    assert r.returncode == 1 and "succ-1" in r.stderr
    head = qitems(env, sid)[0]
    assert head["id"] == 1 and head["error_kind"] is None and "succ-1" in head["last_error"]
    assert not queue.stuck_on_heaviness(head)
    assert osa_log(env) == before_osa
    assert meta(env, sid)["packets"] == 1


@pytest.mark.parametrize("how", ["closed", "dead"])
def test_check_never_reopens_a_session_and_manual_does(env, how):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    if how == "closed":
        pilead("close", sid)
    else:
        (sdir(env, sid) / "status").write_text("dead\n")
    before_osa = osa_log(env)
    boot = (sdir(env, sid) / "bootstrap.sh").read_bytes()
    for trigger in ("check", "settle"):
        r = deliver(sid, "--trigger", trigger)
        assert r.returncode == 1
        assert r.stderr == (f"{sid}: not delivered — session is {how}; pilead queue {sid} --deliver reopens it "
                            f"and delivers, --cancel all empties the queue\n")
    assert osa_log(env) == before_osa
    assert (sdir(env, sid) / "bootstrap.sh").read_bytes() == boot
    head = qitems(env, sid)[0]
    assert f"pilead queue {sid} --deliver" in head["last_error"] and head["error_kind"] is None
    assert len(events(env, "queue_delivery_failed")) == 1  # the same error twice: one line
    assert meta(env, sid)["packets"] == 1
    r = deliver(sid, check=True)
    assert f"delivered queued packet #1 to {sid} as packet 0002 (trigger: manual)" in r.stdout
    assert "placement=" in r.stdout
    assert events(env, "packet_sent")[-1]["via"] == "relaunch"
    assert "targetFound" in osa_log(env)[len(before_osa):]
    assert qitems(env, sid) == []


# ── interrupted deliveries ────────────────────────────────────────────────────────────────────────
def test_an_interrupted_delivery_whose_send_did_not_happen_is_delivered_once(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    set_head(env, sid, claimed_at="2026-09-28T09:00:00Z")
    r = deliver(sid, check=True)
    assert f"delivered queued packet #1 to {sid} as packet 0002" in r.stdout
    assert len(events(env, "packet_sent")) == 1
    assert qitems(env, sid) == []
    assert events(env, "queue_delivered")[0]["recovered"] is False
    assert deliver(sid).stdout == f"{sid}: nothing queued\n"


def test_an_interrupted_delivery_whose_send_happened_is_not_sent_again(env):
    from pilead import ledger
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    mark_reported(env, sid)
    set_head(env, sid, claimed_at="2026-09-28T09:00:00Z")
    ledger.append(H(env), "packet_sent", session_id=sid, packet=2, source="x", via="inbox", queue_id=1)
    inbox = (sdir(env, sid) / "inbox.md").read_bytes()
    r = deliver(sid, check=True)
    assert r.stdout == f"delivered queued packet #1 to {sid} as packet 0002 (trigger: manual); 1 still queued\n"
    assert meta(env, sid)["packets"] == 1
    assert (sdir(env, sid) / "inbox.md").read_bytes() == inbox
    assert len(events(env, "packet_sent")) == 1
    qd = events(env, "queue_delivered")
    assert len(qd) == 1 and qd[0]["recovered"] is True and qd[0]["packet"] == 2 and qd[0]["remaining"] == 1
    assert [it["id"] for it in qitems(env, sid)] == [2]


def test_a_claim_of_another_queue_id_does_not_count_as_this_ones_send(env):
    from pilead import ledger
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    set_head(env, sid, claimed_at="2026-09-28T09:00:00Z")
    ledger.append(H(env), "packet_sent", session_id=sid, packet=2, source="x", via="inbox", queue_id=7)
    ledger.append(H(env), "packet_sent", session_id="other-1", packet=2, source="x", via="inbox", queue_id=1)
    deliver(sid, check=True)
    assert meta(env, sid)["packets"] == 2
    assert events(env, "queue_delivered")[0]["recovered"] is False


# ── two deliveries at once ────────────────────────────────────────────────────────────────────────
def test_two_deliveries_started_together_deliver_one_item(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    enqueue(env, sid, "GOAL: two\n")
    mark_reported(env, sid)
    procs = [subprocess.Popen([str(PILEAD), "queue", sid, "--deliver"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=dict(os.environ)) for _ in range(2)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert len(events(env, "packet_sent")) == 1
    assert len(events(env, "queue_delivered")) == 1
    assert meta(env, sid)["packets"] == 2
    assert [it["id"] for it in qitems(env, sid)] == [2]
    assert not (sdir(env, sid) / "queue.lock").exists()
    joined = "".join(o for o, _ in outs)
    assert joined.count("delivered queued packet #1") == 1


# ── deliver never raises ──────────────────────────────────────────────────────────────────────────
def test_deliver_never_raises_when_the_send_does(env, monkeypatch):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)

    def boom(*a, **k):
        raise RuntimeError("the tab went\naway")
    monkeypatch.setattr(send_mod, "send_packet", boom)
    lines = []
    r = queue.deliver(H(env), sid, "check", out=lines.append)
    assert (r.kind, r.reason) == ("nothing", "refused")
    head = qitems(env, sid)[0]
    assert head["id"] == 1 and head["claimed_at"] is None
    assert head["last_error"] == "RuntimeError: the tab went away" and head["error_kind"] is None
    assert len(events(env, "queue_delivery_failed")) == 1
    assert events(env, "queue_delivered") == []
    assert not (sdir(env, sid) / "queue.lock").exists()


def test_a_waiting_refusal_from_the_send_keeps_the_head_untouched(env, monkeypatch):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    q_before = (sdir(env, sid) / "queue.json").read_bytes()
    before = ledger_bytes(env)

    def busy_again(*a, **k):
        raise send_mod.WaitingRefused("session is busy again")
    monkeypatch.setattr(send_mod, "send_packet", busy_again)
    r = queue.deliver(H(env), sid, "settle", out=lambda line: None)
    assert (r.kind, r.reason) == ("nothing", "waiting")
    head = qitems(env, sid)[0]
    assert head["id"] == 1 and head["claimed_at"] is None and head["last_error"] is None
    assert (sdir(env, sid) / "queue.json").read_bytes() == q_before
    assert ledger_bytes(env) == before


def test_the_send_output_goes_to_out(env):
    sid = spawn(env, log=False)
    enqueue(env, sid, "GOAL: one\n")
    mark_reported(env, sid)
    lines = []
    r = queue.deliver(H(env), sid, "check", out=lines.append)
    assert r.kind == "delivered" and r.packet == 2 and r.item["id"] == 1
    assert lines == [f"sent {sid} packet 0002", f"delivered queued packet #1 to {sid} as packet 0002 (trigger: check)"]

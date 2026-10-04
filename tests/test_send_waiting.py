"""A plain `pilead send` refuses a session that has not finished its current packet — `busy`, `stalled`
or `paused` — or whose status cannot be read at all, before anything is written (see README "Sending to
a session that is still working"). Driven through bin/pilead with the fake `pi` and the terminal seam of
tests/test_resume.py (a recording `osascript` stub and a `ps` stub first on PATH), so no real tab is
opened or closed and the real pi never runs."""
import json
import sys
from pathlib import Path

import pytest

from test_resume import (H, child, ended_pid, env, events, mark_reported, meta, pilead, sdir, spawn,  # noqa: F401
                         terminal)
from test_usage import write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def waiting_line(sid, status, n, packet):
    return (f"pilead: session {sid} is {status}: packet {n:04d} has no report yet, and a send now would "
            f"take its place as the current packet. Nothing was sent. Queue this packet with pilead send "
            f"{sid} {packet} --when-idle, wait for the report (pilead check {sid}), or hand this packet to a "
            f"successor with pilead send {sid} {packet} --rotate.\n")


def unreadable_line(sid, found, n):
    return (f"pilead: the status of session {sid} could not be read (found: {found}), so it is not known "
            f"whether packet {n:04d} is finished. Nothing was sent. pilead check {sid} shows what is on "
            f"disk.\n")


def packet2(env, text="GOAL: the next thing\n"):
    (env / "p2.md").write_text(text)
    return str(env / "p2.md")


def tree_bytes(root):
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── busy / stalled / paused: refused with the waiting line ───────────────────────────────────────
def test_a_freshly_spawned_busy_session_is_refused(env):
    sid = spawn(env, log=False)
    before = tree_bytes(H(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == waiting_line(sid, "busy", 1, p2)
    assert tree_bytes(H(env)) == before


@pytest.mark.parametrize("status", ["stalled", "paused"])
def test_a_stored_stalled_or_paused_session_is_refused(env, status):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text(status + "\n")
    before = tree_bytes(H(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == waiting_line(sid, status, 1, p2)
    assert tree_bytes(H(env)) == before


# ── idle / reported: sent as at HEAD ──────────────────────────────────────────────────────────────
def test_a_stored_idle_session_is_sent(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text("idle\n")
    p2 = packet2(env)
    r = pilead("send", sid, p2)
    assert r.stdout.splitlines()[-1] == f"sent {sid} packet 0002"
    assert "placement=" not in r.stdout


def test_stored_busy_with_a_live_process_and_the_report_on_disk_is_sent(env, child):
    """The report of the current packet exists: `status.refresh` reads `reported` regardless of the
    word stored on disk (busy here)."""
    sid = spawn(env, log=False)
    p = child()
    (sdir(env, sid) / "pid").write_text(f"{p.pid}\n")
    (sdir(env, sid) / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    p2 = packet2(env)
    r = pilead("send", sid, p2)
    assert r.stdout.splitlines()[-1] == f"sent {sid} packet 0002"


# ── a status that cannot be read ──────────────────────────────────────────────────────────────────
def test_an_unrecognised_stored_status_is_unreadable(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text("weird\n")
    before = tree_bytes(H(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == unreadable_line(sid, "weird", 1)
    assert tree_bytes(H(env)) == before


def test_an_empty_status_file_with_no_status_key_in_meta_reads_as_nothing(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text("")
    m = meta(env, sid)
    del m["status"]
    (sdir(env, sid) / "meta.json").write_text(json.dumps(m))
    before = tree_bytes(H(env))
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == unreadable_line(sid, "nothing", 1)
    assert tree_bytes(H(env)) == before


# ── the process is gone: `status.refresh` decides, and send acts on what it just found ────────────
def test_process_gone_tab_still_there_is_refused_as_stalled(env, child):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    before_packet = (sdir(env, sid) / "packet-0001.md").read_bytes()
    before_inbox = (sdir(env, sid) / "inbox.md").read_bytes()
    before_packets_n = meta(env, sid)["packets"]
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False, env={"FAKE_TAB": "true"})
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == waiting_line(sid, "stalled", 1, p2)
    assert (sdir(env, sid) / "status").read_text().strip() == "stalled"
    stalled = events(env, "stalled")
    assert len(stalled) == 1 and stalled[0]["session_id"] == sid
    assert (sdir(env, sid) / "packet-0001.md").read_bytes() == before_packet
    assert (sdir(env, sid) / "inbox.md").read_bytes() == before_inbox
    assert meta(env, sid)["packets"] == before_packets_n


def test_process_and_tab_both_gone_relaunches(env, child):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    p2 = packet2(env)
    r = pilead("send", sid, p2, env={"FAKE_TAB": "false"})
    assert r.stdout.splitlines()[-1].startswith("placement=")
    sent = events(env, "packet_sent")
    assert sent[-1]["via"] == "relaunch"
    dead = events(env, "dead")
    assert len(dead) == 1 and dead[0]["session_id"] == sid


# ── order: the earlier refusals still come first, and the heaviness gate comes after ──────────────
def test_a_blank_override_reason_is_checked_before_the_waiting_refusal(env):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    r = pilead("send", sid, p2, "--heavy-override", "", check=False)
    assert r.returncode == 2
    assert "--heavy-override needs a reason" in r.stderr
    assert "would take its place" not in r.stderr


def test_a_superseded_busy_session_is_checked_before_the_waiting_refusal(env):
    sid = spawn(env, log=False)
    pilead("close", sid, "--supersede", "succ-1")
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2
    assert "succ-1" in r.stderr
    assert "would take its place" not in r.stderr


def test_a_heavy_busy_session_gives_the_waiting_line_not_the_heavy_one(env):
    from test_cli_core import heavy_entries
    sid = spawn(env, log=False)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2
    assert r.stderr == waiting_line(sid, "busy", 1, p2)
    assert "is heavy" not in r.stderr


# ── --rotate and --upgrade are unaffected ─────────────────────────────────────────────────────────
def test_rotate_of_a_stalled_session_rotates(env):
    sid = spawn(env, log=False)
    (sdir(env, sid) / "status").write_text("stalled\n")
    p2 = packet2(env)
    r = pilead("send", sid, p2, "--rotate")
    assert "successor:" in r.stdout
    assert "would take its place" not in r.stderr


def test_rotate_of_a_busy_unreported_session_prints_the_head_refusal(env):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    r = pilead("send", sid, p2, "--rotate", check=False)
    assert r.returncode == 2
    assert f"pilead retire {sid} --force" in r.stderr
    assert "would take its place" not in r.stderr


# ── the defect this packet closes ─────────────────────────────────────────────────────────────────
def test_a_report_written_after_a_refused_send_is_announced_by_check(env):
    sid = spawn(env, log=False)
    p2 = packet2(env)
    r = pilead("send", sid, p2, check=False)
    assert r.returncode == 2
    (sdir(env, sid) / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    out = pilead("check", sid).stdout.splitlines()
    assert out[0] == "reported"
    assert out[1] == str(sdir(env, sid) / "report-0001.md")


# ── p22: a send from a lead is a claim of the session (verbs/adopt.py `claim`) ──────────────────────
def claimer(env):
    """A second registered lead, `lead-2`, and the environment that makes it the caller."""
    pilead("lead-start", "lead-2", "--project", "other")
    return {"PI_LEAD_SID": "lead-2"}


def orphaned(env, sid):
    """lead-1's record gone: the session names a lead that is no longer registered."""
    (H(env) / "leads" / "lead-1.json").unlink()


def adopted(env):
    return [(e["session_id"], e["from_lead"], e["to_lead"], e["reason"]) for e in events(env, "adopted")]


def test_a_send_from_a_lead_to_an_orphan_adopts_it_first(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    orphaned(env, sid)
    r = pilead("send", sid, packet2(env), env=claimer(env))
    lines = r.stdout.splitlines()
    assert lines[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert lines[-1] == f"sent {sid} packet 0002" and r.stderr == ""
    assert meta(env, sid)["lead"] == "lead-2" and meta(env, sid)["packets"] == 2
    assert adopted(env) == [(sid, "lead-1", "lead-2", "claim")]


def test_a_send_to_a_session_of_a_live_lead_warns_adopts_nothing_and_sends(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    rec_file = H(env) / "leads" / "lead-1.json"
    rec = json.loads(rec_file.read_text())
    rec["iterm_handle"] = "w0t0p0:11111111-2222-3333-4444-555555555555"
    rec_file.write_text(json.dumps(rec))
    r = pilead("send", sid, packet2(env), env={**claimer(env), "FAKE_TAB": "true"})
    assert r.stderr == (f"pilead: {sid} is owned by lead-1 (proj) — its report will wake that lead, not you; "
                        f"pilead adopt {sid} --force takes it\n")
    assert r.stdout.splitlines()[-1] == f"sent {sid} packet 0002" and "adopted" not in r.stdout
    assert meta(env, sid)["lead"] == "lead-1" and adopted(env) == []


def test_a_send_to_a_session_of_an_unreachable_lead_warns_too(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    r = pilead("send", sid, packet2(env), env=claimer(env))  # lead-1 started seconds ago, no tab: unreachable
    assert r.stderr.startswith(f"pilead: {sid} is owned by lead-1 (proj) — its report will wake that lead")
    assert meta(env, sid)["lead"] == "lead-1"


def test_a_refused_send_adopts_nothing(env):
    from test_cli_core import heavy_entries
    busy = spawn(env, log=False)
    heavy = spawn(env, log=False)
    mark_reported(env, heavy)
    write_log(heavy, (env / "wt").resolve(), heavy_entries())
    orphaned(env, busy)
    caller = claimer(env)
    before = tree_bytes(H(env))
    p2 = packet2(env)
    r = pilead("send", busy, p2, check=False, env=caller)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", waiting_line(busy, "busy", 1, p2))
    r = pilead("send", heavy, p2, check=False, env=caller)
    assert r.returncode == 2 and r.stdout == "" and "is heavy" in r.stderr
    assert tree_bytes(H(env)) == before, "no adoption, no event"


def test_with_no_caller_a_send_to_an_orphan_is_as_today(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    orphaned(env, sid)
    r = pilead("send", sid, packet2(env))
    assert (r.stdout, r.stderr) == (f"sent {sid} packet 0002\n", "")
    assert meta(env, sid)["lead"] == "lead-1" and adopted(env) == []


def test_a_queued_send_from_a_lead_adopts_and_the_delivery_never_does(env):
    sid = spawn(env, log=False)
    orphaned(env, sid)
    caller = claimer(env)
    r = pilead("send", sid, packet2(env), "--when-idle", env=caller)
    assert r.stdout.splitlines()[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert r.stdout.splitlines()[1].startswith(f"queued {sid} #")
    # a delivery (here: by a check with a caller) never adopts — hand the session back to an orphan owner first
    m = meta(env, sid)
    m["lead"] = "lead-gone"
    (sdir(env, sid) / "meta.json").write_text(json.dumps(m))
    mark_reported(env, sid)
    r = pilead("queue", sid, "--deliver", env=caller)
    assert f"sent {sid} packet 0002" in r.stdout and "adopted" not in r.stdout + r.stderr
    assert meta(env, sid)["lead"] == "lead-gone" and len(adopted(env)) == 1


def test_a_rotate_from_a_lead_adopts_and_its_successor_names_the_caller(env):
    sid = spawn(env, log=False)
    mark_reported(env, sid)
    orphaned(env, sid)
    r = pilead("send", sid, packet2(env), "--rotate", env=claimer(env))
    assert r.stdout.splitlines()[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert r.stdout.splitlines()[1].startswith(f"rotating {sid} → retire + spawn {sid}-r2")
    assert meta(env, sid)["lead"] == "lead-2" and meta(env, f"{sid}-r2")["lead"] == "lead-2"

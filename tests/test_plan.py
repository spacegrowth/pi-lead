"""`pilead plan` and lib/pilead/plan.py: the plan file, how an item is bound and what state it is in, every
sub-verb, and the refusals that leave the file byte-identical. Driven in-process for the plan module and
through bin/pilead for the verbs; a spawn or a send goes through the recording `osascript` stub of
tests/test_resume.py only (no real tab is opened, the real pi never runs). Every home is under tmp_path."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import mark_reported, terminal  # noqa: F401  (the autouse terminal seam)

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import plan as plan_mod  # noqa: E402
from pilead.state import PileadError  # noqa: E402

T0 = "2026-01-01T00:00:00Z"


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def run(*args, cwd=None, env=None):
    """bin/pilead; an `env` value of None removes that variable."""
    e = {**os.environ, **(env or {})}
    e = {k: v for k, v in e.items() if v is not None}
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, cwd=cwd, env=e)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A registered lead `lead-1` whose cwd is tmp_path/leadcwd; the test's own cwd is tmp_path/here."""
    h = H(tmp_path)
    (h / "leads").mkdir(parents=True)
    lcwd, here = tmp_path / "leadcwd", tmp_path / "here"
    lcwd.mkdir()
    here.mkdir()
    (h / "leads" / "lead-1.json").write_text(json.dumps({"sid": "lead-1", "project": "proj", "cwd": str(lcwd)}))
    monkeypatch.chdir(here)
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")  # the caller, unless a test says otherwise
    return tmp_path


def lead_cwd(w):
    return w / "leadcwd"


def packet(w, name="a-packet.md", where=None):
    d = where or lead_cwd(w)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("GOAL: x\n")
    return d / name


def session(w, sid, lead="lead-1", status="busy", packets=1, raw=None, superseded_by=None):
    s = H(w) / "sessions" / sid
    s.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (s / "meta.json").write_text(raw)
        return s
    meta = {"sid": sid, "lead": lead, "worktree": "/nowhere", "topic": "t", "model": "m", "status": status,
            "packets": packets, "label": f"[Exec] {sid}"}
    if superseded_by:
        meta["superseded_by"] = superseded_by
    (s / "meta.json").write_text(json.dumps(meta))
    (s / "status").write_text(status + "\n")
    return s


def item(pkt, **kw):
    return {"n": 0, "packet": str(pkt), "target": "fresh", "model": None, "note": None, "executor": None,
            "packet_n": None, "added": T0, "done": None, **kw}


def seed(w, items, sid="lead-1"):
    p = H(w) / "plans" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "items": items}, indent=2) + "\n")
    return p


def ev(w, ts, event, **fields):
    """A ledger line with a chosen time."""
    p = H(w) / "ledger.jsonl"
    with open(p, "a") as f:
        f.write(json.dumps({"ts": ts, "event": event, **fields}) + "\n")


def events(w, name=None):
    p = H(w) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if name is None or r["event"] == name]


def stored(w, sid="lead-1"):
    return json.loads((H(w) / "plans" / f"{sid}.json").read_text())


def rows(w, sid="lead-1"):
    return plan_mod.rows(H(w), sid)


def states(w):
    return [r["state"] for r in rows(w)]


# ── the plan file ─────────────────────────────────────────────────────────────────────────────────
def test_read_a_missing_file_is_an_empty_plan(tmp_path):
    assert plan_mod.read(tmp_path, "lead-1") == ({"version": 1, "items": []}, "none")
    assert not (tmp_path / "plans").exists()  # reading creates nothing


def test_read_a_good_file(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "lead-1.json").write_text(json.dumps({"version": 1, "items": [{"packet": "x"}]}))
    plan, st = plan_mod.read(tmp_path, "lead-1")
    assert st == "ok" and plan["items"] == [{"packet": "x"}]


@pytest.mark.parametrize("text", ["not json {", "[]", "null", '"s"', '{"items": {}}', '{"version": 1}',
                                  '{"items": ["a string"]}', '{"items": [{"packet": "x"}, 3]}', ""])
def test_read_each_unreadable_shape(tmp_path, text):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "lead-1.json").write_text(text)
    assert plan_mod.read(tmp_path, "lead-1") == (None, "unreadable")
    assert plan_mod.rows(tmp_path, "lead-1") is None


def test_read_a_directory_or_a_dangling_link_is_unreadable_not_empty(tmp_path):
    (tmp_path / "plans" / "lead-1.json").mkdir(parents=True)
    assert plan_mod.read(tmp_path, "lead-1") == (None, "unreadable")
    (tmp_path / "plans" / "lead-2.json").symlink_to(tmp_path / "nowhere")
    assert plan_mod.read(tmp_path, "lead-2") == (None, "unreadable")


def test_the_id_is_checked_before_a_path_is_made(tmp_path):
    for bad in ("../x", "a/b", "", "-x", "x.usage"):
        with pytest.raises(PileadError):
            plan_mod.path(tmp_path, bad)
        with pytest.raises(PileadError):
            plan_mod.read(tmp_path, bad)


def test_write_numbers_the_items_and_leaves_no_temporary_file(tmp_path):
    plan = {"version": 1, "items": [{"n": 9, "packet": "a"}, {"packet": "b"}, {"n": 0, "packet": "c"}]}
    plan_mod.write(tmp_path, "lead-1", plan)
    assert [it["n"] for it in json.loads((tmp_path / "plans" / "lead-1.json").read_text())["items"]] == [1, 2, 3]
    assert [p.name for p in (tmp_path / "plans").iterdir()] == ["lead-1.json"]


# ── add ───────────────────────────────────────────────────────────────────────────────────────────
def test_add_a_relative_packet_found_from_the_leads_cwd_is_stored_as_given(world):
    packet(world)
    r = run("plan", "add", "a-packet.md")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "plan: added #1 a-packet.md → fresh\n"
    (it,) = stored(world)["items"]
    assert it["packet"] == "a-packet.md" and it["target"] == "fresh" and it["executor"] is None
    assert it["n"] == 1 and it["done"] is None and it["model"] is None and it["note"] is None


def test_add_a_packet_found_only_from_the_current_directory_is_stored_absolute(world):
    packet(world, where=world / "here")
    r = run("plan", "add", "a-packet.md")
    assert r.returncode == 0, r.stderr
    (it,) = stored(world)["items"]
    assert it["packet"] == str((world / "here" / "a-packet.md").resolve())
    assert r.stdout == "plan: added #1 a-packet.md → fresh\n"


def test_add_a_missing_packet_exits_2_and_names_both_places(world):
    r = run("plan", "add", "nope-packet.md")
    assert r.returncode == 2 and r.stdout == ""
    assert str(lead_cwd(world)) in r.stderr and os.path.realpath(world / "here") in r.stderr
    assert "nope-packet.md" in r.stderr
    assert not (H(world) / "plans").exists() and events(world) == []


def test_add_a_directory_is_not_a_packet(world):
    (lead_cwd(world) / "adir").mkdir()
    assert run("plan", "add", "adir").returncode == 2


def test_add_target_a_session_that_exists_and_one_that_does_not(world):
    packet(world)
    session(world, "exec-1")
    r = run("plan", "add", "a-packet.md", "--target", "exec-1", "--model", "sonnet", "--note", "hello there")
    assert r.returncode == 0 and r.stdout == "plan: added #1 a-packet.md → exec-1\n"
    (it,) = stored(world)["items"]
    assert (it["target"], it["model"], it["note"]) == ("exec-1", "sonnet", "hello there")
    before = (H(world) / "plans" / "lead-1.json").read_bytes()
    r = run("plan", "add", "a-packet.md", "--target", "ghost")
    assert r.returncode == 2 and (H(world) / "plans" / "lead-1.json").read_bytes() == before
    r = run("plan", "add", "a-packet.md", "--target", "../x")
    assert r.returncode == 2 and (H(world) / "plans" / "lead-1.json").read_bytes() == before


def test_add_before_puts_the_item_ahead_and_renumbers(world):
    for name in ("one-packet.md", "two-packet.md", "three-packet.md"):
        packet(world, name)
    assert run("plan", "add", "one-packet.md").returncode == 0
    assert run("plan", "add", "two-packet.md").returncode == 0
    r = run("plan", "add", "three-packet.md", "--before", "1")
    assert r.stdout == "plan: added #1 three-packet.md → fresh\n"
    assert [(it["n"], it["packet"]) for it in stored(world)["items"]] == [
        (1, "three-packet.md"), (2, "one-packet.md"), (3, "two-packet.md")]
    before = (H(world) / "plans" / "lead-1.json").read_bytes()
    for bad in ("0", "4", "x", "-1"):
        r = run("plan", "add", "three-packet.md", "--before", bad)
        assert r.returncode == 2, bad
        assert "plan: no item #" in r.stderr and "(the plan has 3)" in r.stderr
    assert (H(world) / "plans" / "lead-1.json").read_bytes() == before


def test_add_before_1_on_an_empty_plan_exits_2(world):
    packet(world)
    r = run("plan", "add", "a-packet.md", "--before", "1")
    assert r.returncode == 2 and "(the plan has 0)" in r.stderr and not (H(world) / "plans").exists()


def test_add_writes_a_plan_add_event(world):
    packet(world)
    run("plan", "add", "a-packet.md", "--model", "opus")
    (e,) = events(world, "plan_add")
    assert (e["session_id"], e["n"], e["packet"], e["target"], e["model"]) == (
        "lead-1", 1, "a-packet.md", "fresh", "opus")


# ── rm, done, bind ───────────────────────────────────────────────────────────────────────────────
def two_items(w):
    packet(w, "one-packet.md")
    packet(w, "two-packet.md")
    assert run("plan", "add", "one-packet.md").returncode == 0
    assert run("plan", "add", "two-packet.md").returncode == 0
    return H(w) / "plans" / "lead-1.json"


def test_rm(world):
    f = two_items(world)
    r = run("plan", "rm", "1")
    assert r.returncode == 0 and r.stdout == "plan: removed #1 one-packet.md\n"
    assert [(it["n"], it["packet"]) for it in json.loads(f.read_text())["items"]] == [(1, "two-packet.md")]
    (e,) = events(world, "plan_rm")
    assert (e["session_id"], e["n"], e["packet"]) == ("lead-1", 1, "one-packet.md")


def test_done_without_and_with_a_note(world):
    f = two_items(world)
    r = run("plan", "done", "1")
    assert r.returncode == 0 and r.stdout == "plan: marked #1 one-packet.md done\n"
    it = json.loads(f.read_text())["items"][0]
    assert it["done"] and it["note"] is None
    r = run("plan", "done", "2", "--note", "by hand")
    assert r.stdout == "plan: marked #2 two-packet.md done\n"
    it = json.loads(f.read_text())["items"][1]
    assert it["done"] and it["note"] == "by hand"
    assert [(e["n"], e["packet"]) for e in events(world, "plan_done")] == [(1, "one-packet.md"), (2, "two-packet.md")]


def test_bind_the_default_packet_and_an_explicit_one(world):
    f = two_items(world)
    session(world, "exec-1", packets=3)
    r = run("plan", "bind", "1", "exec-1")
    assert r.returncode == 0 and r.stdout == "plan: bound #1 to exec-1 packet 3\n"
    r = run("plan", "bind", "2", "exec-1", "--packet", "2")
    assert r.stdout == "plan: bound #2 to exec-1 packet 2\n"
    a, b = json.loads(f.read_text())["items"]
    assert (a["executor"], a["packet_n"], b["executor"], b["packet_n"]) == ("exec-1", 3, "exec-1", 2)
    assert [(e["n"], e["executor"], e["packet"]) for e in events(world, "plan_bind")] == [
        (1, "exec-1", 3), (2, "exec-1", 2)]
    assert all(e["session_id"] == "lead-1" for e in events(world, "plan_bind"))


def test_bind_a_session_that_does_not_exist_exits_2(world):
    f = two_items(world)
    before = f.read_bytes()
    for sid in ("ghost", "../x"):
        r = run("plan", "bind", "1", sid)
        assert r.returncode == 2, sid
    r = run("plan", "bind", "1", "ghost", "--packet", "1")
    assert r.returncode == 2 and f.read_bytes() == before and events(world, "plan_bind") == []


def test_bind_a_bad_packet_number_exits_2(world):
    f = two_items(world)
    session(world, "exec-1")
    before = f.read_bytes()
    for bad in ("0", "x", "-1", "1.5"):
        assert run("plan", "bind", "1", "exec-1", "--packet", bad).returncode == 2, bad
    assert f.read_bytes() == before


@pytest.mark.parametrize("verb", [["rm"], ["done"], ["bind"]])
@pytest.mark.parametrize("n", ["0", "3", "x", "-1", "1.0", "01x"])
def test_an_item_number_outside_the_plan_exits_2_and_writes_nothing(world, verb, n):
    two_items(world)
    session(world, "exec-1")
    before = tree(H(world))
    args = ["plan", *verb, n] + (["exec-1"] if verb == ["bind"] else [])
    r = run(*args)
    assert r.returncode == 2 and r.stdout == ""
    assert f"plan: no item #{n} (the plan has 2)" in r.stderr
    assert tree(H(world)) == before


# ── binding ───────────────────────────────────────────────────────────────────────────────────────
def test_an_item_added_then_spawned_under_this_lead_is_bound_to_packet_1(world):
    (world / "wt").mkdir()
    pkt = packet(world, "topic-packet.md")
    assert run("plan", "add", str(pkt)).returncode == 0
    r = run("spawn", str(world / "wt"), "topic", str(pkt), "--model", "sonnet", "--lead", "lead-1")
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    out = json.loads(run("plan", "status", "--json").stdout)
    (row,) = out["items"]
    assert out["lead"] == "lead-1"
    assert (row["executor"], row["packet_n"], row["state"]) == (sid, 1, "in-flight")
    assert (stored(world)["items"][0]["executor"], stored(world)["items"][0]["packet_n"]) == (sid, 1)


def test_an_item_sent_as_a_follow_up_is_bound_to_that_packet_number(world):
    (world / "wt").mkdir()
    first, second = packet(world, "first-packet.md"), packet(world, "second-packet.md")
    r = run("spawn", str(world / "wt"), "topic", str(first), "--model", "sonnet", "--lead", "lead-1")
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    assert run("plan", "add", str(second)).returncode == 0
    mark_reported(world, sid)  # a plain send to a busy session is refused
    r = run("send", sid, str(second))
    assert r.returncode == 0, r.stderr
    (row,) = json.loads(run("plan", "status", "--json").stdout)["items"]
    assert (row["executor"], row["packet_n"]) == (sid, 2)
    assert row["state"] == "in-flight"


def test_an_event_older_than_added_binds_nothing(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item(pkt, added="2026-06-01T00:00:00Z")])
    ev(world, "2026-05-31T23:59:59Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    assert states(world) == ["queued"]
    ev(world, "2026-06-01T00:00:00Z", "packet_sent", session_id="exec-1", packet=2, source=str(pkt.resolve()))
    (r,) = rows(world)  # at the very second: bound
    assert (r["executor"], r["packet_n"]) == ("exec-1", 2)


def test_an_event_on_another_leads_session_binds_nothing(world):
    pkt = packet(world)
    session(world, "exec-1", lead="lead-2")
    seed(world, [item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    assert states(world) == ["queued"]
    assert stored(world)["items"][0]["executor"] is None


def test_an_event_with_another_source_binds_nothing(world):
    pkt = packet(world)
    other = packet(world, "other-packet.md")
    session(world, "exec-1")
    seed(world, [item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(other.resolve()))
    ev(world, "2026-02-01T00:00:01Z", "packet_sent", session_id="exec-1", packet=2, source=None)
    ev(world, "2026-02-01T00:00:02Z", "packet_sent", session_id="exec-1", packet=3)
    assert states(world) == ["queued"]


def test_a_session_whose_record_cannot_be_read_binds_nothing(world):
    pkt = packet(world)
    session(world, "exec-1", raw="{not json")
    seed(world, [item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    assert states(world) == ["queued"]


def test_two_items_with_the_same_packet_bind_to_two_different_events_in_order(world):
    pkt = packet(world)
    session(world, "exec-1")
    session(world, "exec-2")
    seed(world, [item(pkt), item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    ev(world, "2026-02-01T00:00:05Z", "packet_sent", session_id="exec-1", packet=2, source=str(pkt.resolve()))
    a, b = rows(world)
    assert [(a["executor"], a["packet_n"]), (b["executor"], b["packet_n"])] == [("exec-1", 1), ("exec-1", 2)]
    ev(world, "2026-02-01T00:00:09Z", "spawned", session_id="exec-2", lead="lead-1", source=str(pkt.resolve()))
    seed(world, [item(pkt), item(pkt), item(pkt)])
    assert [(r["executor"], r["packet_n"]) for r in rows(world)] == [("exec-1", 1), ("exec-1", 2), ("exec-2", 1)]


def test_an_item_marked_done_is_not_bound(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item(pkt, done="2026-01-02T00:00:00Z")])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    (r,) = rows(world)
    assert r["state"] == "done" and r["executor"] is None


def test_a_bound_pair_is_not_taken_again_by_another_item(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item(pkt, executor="exec-1", packet_n=1), item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    assert [r["state"] for r in rows(world)] == ["in-flight", "queued"]


def test_a_relative_packet_is_matched_from_the_leads_cwd(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item("a-packet.md")])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    (r,) = rows(world)
    assert r["executor"] == "exec-1" and r["packet_path"] == str(pkt.resolve())


# ── the states ────────────────────────────────────────────────────────────────────────────────────
def bound(world, **session_kw):
    pkt = packet(world)
    session(world, "exec-1", **session_kw)
    seed(world, [item(pkt, executor="exec-1", packet_n=1)])
    return pkt


def test_state_done_by_hand(world):
    seed(world, [item(packet(world), done="2026-01-02T00:00:00Z")])
    assert states(world) == ["done"]


def test_state_done_when_refs_are_accepted_without_a_hand_mark(world):
    bound(world)
    assert states(world) == ["in-flight"]
    ev(world, "2026-03-01T00:00:00Z", "refs_accepted", session_id="exec-1", packet=2, reason="x", head="h")
    assert states(world) == ["in-flight"]  # another packet's acceptance is not this item's
    ev(world, "2026-03-01T00:00:01Z", "refs_accepted", session_id="exec-1", packet=1, reason="x", head="h")
    assert states(world) == ["done"]
    assert stored(world)["items"][0]["done"] is None  # derived, not written


def test_an_auto_closed_event_does_not_mark_an_item_done(world):
    bound(world)
    ev(world, "2026-03-01T00:00:00Z", "auto_closed", session_id="exec-1", reason="landed")
    assert states(world) == ["in-flight"]


def test_state_queued(world):
    seed(world, [item(packet(world))])
    assert states(world) == ["queued"]


def test_state_reported_when_the_report_file_exists(world):
    bound(world)
    (H(world) / "sessions" / "exec-1" / "report-0001.md").write_text("Done.\n")
    assert states(world) == ["reported"]


def test_state_in_flight_for_a_live_status(world):
    for status in ("busy", "idle", "stalled", "paused", "reported"):
        bound(world, status=status)
        assert states(world) == ["in-flight"], status


def test_state_closed_with_no_report(world):
    bound(world, status="closed")
    assert states(world) == ["closed"]


def test_state_superseded(world):
    bound(world, status="closed", superseded_by="exec-2")
    assert states(world) == ["superseded"]


def test_state_dead(world):
    bound(world, status="dead")
    assert states(world) == ["dead"]


def test_a_report_wins_over_a_closed_session(world):
    bound(world, status="closed")
    (H(world) / "sessions" / "exec-1" / "report-0001.md").write_text("Done.\n")
    assert states(world) == ["reported"]


def test_state_unknown_when_the_session_record_is_not_json(world):
    bound(world, raw="{nope")
    assert states(world) == ["unknown"]


def test_state_unknown_for_a_session_that_is_gone_or_a_bad_id(world):
    pkt = packet(world)
    seed(world, [item(pkt, executor="gone", packet_n=1), item(pkt, executor="../x", packet_n=1),
                 item(pkt, executor="gone", packet_n=None)])
    assert states(world) == ["unknown", "unknown", "unknown"]


def test_state_unknown_when_no_status_can_be_read(world):
    s = bound(world)
    del s
    sdir = H(world) / "sessions" / "exec-1"
    (sdir / "status").unlink()
    meta = json.loads((sdir / "meta.json").read_text())
    del meta["status"]
    (sdir / "meta.json").write_text(json.dumps(meta))
    assert states(world) == ["unknown"]


def test_an_unreadable_state_is_never_queued_and_never_done(world):
    bound(world, raw="[]")
    assert states(world) == ["unknown"]


# ── status ────────────────────────────────────────────────────────────────────────────────────────
def test_status_table_and_counts(world):
    p1, p2, p3, p4 = (packet(world, f"p{i}-packet.md") for i in range(1, 5))
    session(world, "exec-1")
    session(world, "exec-2", status="closed")
    session(world, "exec-3")
    (H(world) / "sessions" / "exec-3" / "report-0001.md").write_text("Done.\n")
    seed(world, [item(p1, model="sonnet", note="first one"), item(p2, executor="exec-1", packet_n=1),
                 item(p3, executor="exec-2", packet_n=1), item(p4, executor="exec-3", packet_n=1),
                 item(p1, done=T0, target="exec-9")])
    r = run("plan")
    assert r.returncode == 0 and r.stderr == ""
    lines = r.stdout.splitlines()
    assert lines[0].split() == ["#", "STATE", "PACKET", "TARGET", "MODEL", "NOTE"]
    assert lines[1].split() == ["1", "queued", "p1-packet.md", "fresh", "sonnet", "first", "one"]
    assert lines[2].split() == ["2", "in-flight", "p2-packet.md", "exec-1", "-", "-"]
    assert lines[3].split() == ["3", "closed", "p3-packet.md", "exec-2", "-", "-"]
    assert lines[4].split() == ["4", "reported", "p4-packet.md", "exec-3", "-", "-"]
    assert lines[5].split() == ["5", "done", "p1-packet.md", "exec-9", "-", "-"]
    assert lines[6] == "  1 queued · 2 in flight · 1 reported · 1 done" and len(lines) == 7
    assert r.stdout == run("plan", "status").stdout  # no sub-verb is status


def test_status_counts_unknown_and_superseded_as_in_flight(world):
    pkt = packet(world)
    session(world, "exec-1", status="closed", superseded_by="x")
    session(world, "exec-2", raw="{")
    seed(world, [item(pkt, executor="exec-1", packet_n=1), item(pkt, executor="exec-2", packet_n=1)])
    out = run("plan").stdout.splitlines()
    assert out[1].split()[1] == "superseded" and out[2].split()[1] == "unknown"
    assert out[-1] == "  0 queued · 2 in flight · 0 reported · 0 done"


def test_status_an_empty_plan(world):
    r = run("plan", "status")
    assert r.returncode == 0 and r.stdout == "  (plan is empty — pilead plan add <packet>)\n"
    assert not (H(world) / "plans").exists()


def test_status_json(world):
    pkt = packet(world)
    seed(world, [item(pkt, model="m", note="n")])
    out = json.loads(run("plan", "status", "--json").stdout)
    assert out["lead"] == "lead-1"
    (row,) = out["items"]
    assert row == {"n": 1, "packet": str(pkt), "target": "fresh", "model": "m", "note": "n", "executor": None,
                   "packet_n": None, "added": T0, "done": None, "state": "queued", "packet_path": str(pkt.resolve())}
    assert json.loads(run("plan", "status", "--json").stdout) == out


def test_status_writes_the_binding_back_but_makes_no_ledger_event(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item(pkt)])
    ev(world, "2026-02-01T00:00:00Z", "spawned", session_id="exec-1", lead="lead-1", source=str(pkt.resolve()))
    before = events(world)
    run("plan", "status")
    assert stored(world)["items"][0]["executor"] == "exec-1" and events(world) == before


# ── an unreadable plan is never written over ─────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["not json", "[]", '{"items": 3}', '{"items": ["x"]}'])
@pytest.mark.parametrize("args", [["status"], ["status", "--json"], ["add", "a-packet.md"], ["rm", "1"],
                                  ["done", "1"], ["bind", "1", "exec-1"], ["next"], []])
def test_an_unreadable_plan_exits_1_and_is_byte_identical(world, text, args):
    packet(world)
    session(world, "exec-1")
    f = seed(world, [])
    f.write_text(text)
    before = tree(H(world))
    r = run("plan", *args)
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr.strip().endswith(f"plan: {f} cannot be read — inspect that file; nothing was changed")
    assert tree(H(world)) == before


# ── next ──────────────────────────────────────────────────────────────────────────────────────────
def test_next_prints_the_spawn_command_with_and_without_a_model(world):
    pkt = packet(world, "alpha-packet.md")
    seed(world, [item(pkt, model="claude sonnet")])
    r = run("plan", "next")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == (f"pilead spawn {shlex.quote(str(lead_cwd(world)))} alpha {shlex.quote(str(pkt.resolve()))} "
                        f"--model {shlex.quote('claude sonnet')} --lead lead-1\n")
    seed(world, [item(pkt)])
    assert run("plan", "next").stdout == (f"pilead spawn {shlex.quote(str(lead_cwd(world)))} alpha "
                                          f"{shlex.quote(str(pkt.resolve()))} --lead lead-1\n")


def test_next_topic_of_a_packet_without_the_packet_suffix_is_its_stem(world):
    pkt = packet(world, "notes.v2.md")
    seed(world, [item(pkt)])
    assert " notes.v2 " in run("plan", "next").stdout


def test_next_quotes_a_packet_path_with_a_space(world):
    pkt = packet(world, "my-packet.md", where=world / "my dir")
    seed(world, [item(pkt)])
    out = run("plan", "next").stdout
    assert shlex.quote(str(pkt.resolve())) in out and "'" in shlex.quote(str(pkt.resolve()))
    assert shlex.split(out)[4] == str(pkt.resolve())


def test_next_a_target_session_gets_the_send_command(world):
    pkt = packet(world)
    session(world, "exec-1")
    seed(world, [item(pkt, target="exec-1")])
    assert run("plan", "next").stdout == f"pilead send exec-1 {shlex.quote(str(pkt.resolve()))}\n"


def test_next_is_the_first_queued_item_only(world):
    p1, p2 = packet(world, "one-packet.md"), packet(world, "two-packet.md")
    seed(world, [item(p1, done=T0), item(p2)])
    assert " two " in run("plan", "next").stdout


def test_next_with_nothing_queued_exits_1_and_prints_nothing(world):
    pkt = packet(world)
    r = run("plan", "next")
    assert r.returncode == 1 and r.stdout == "" and r.stderr == ""
    seed(world, [item(pkt, done=T0)])
    r = run("plan", "next")
    assert r.returncode == 1 and r.stdout == "" and r.stderr == ""


def test_next_creates_no_session_and_no_ledger_event(world):
    pkt = packet(world)
    seed(world, [item(pkt)])
    run("plan", "next")
    run("plan", "next")
    assert not (H(world) / "sessions").exists() and not (H(world) / "ledger.jsonl").exists()


# ── whose plan ────────────────────────────────────────────────────────────────────────────────────
def test_the_sid_from_session_from_the_environment_and_none(world):
    pkt = packet(world)
    session(world, "exec-1")
    (H(world) / "leads" / "lead-2.json").write_text(json.dumps({"sid": "lead-2", "project": "p2",
                                                                "cwd": str(lead_cwd(world))}))
    seed(world, [item(pkt)], sid="lead-2")
    assert "p-packet" not in run("plan", "--session", "lead-2").stdout
    assert json.loads(run("plan", "--session", "lead-2", "status", "--json").stdout)["lead"] == "lead-2"
    assert json.loads(run("plan", "status", "--json", "--session", "lead-2").stdout)["lead"] == "lead-2"
    assert json.loads(run("plan", "status", "--json", env={"PI_LEAD_SID": "lead-2"}).stdout)["lead"] == "lead-2"
    assert json.loads(run("plan", "status", "--json", env={"PI_LEAD_SID": None,
                                                           "PI_SESSION_ID": "lead-2"}).stdout)["lead"] == "lead-2"
    assert json.loads(run("plan", "status", "--json", "--session", "lead-2",
                          env={"PI_LEAD_SID": "lead-1"}).stdout)["lead"] == "lead-2"  # --session wins
    none = {"PI_LEAD_SID": None, "PI_SESSION_ID": None}
    r = run("plan", env=none)
    assert r.returncode == 2 and r.stdout == "" and "no lead session id" in r.stderr
    r = run("plan", "add", "a-packet.md", env=none)
    assert r.returncode == 2 and not (H(world) / "plans" / "lead-1.json").exists()


def test_the_home_after_the_sub_verb(world):
    seed(world, [])
    assert run("plan", "status", "--session", "lead-1", "--home", str(H(world))).returncode == 0


def test_a_session_that_is_not_a_usable_lead_id_is_refused_and_nothing_is_created(tmp_path):
    home = tmp_path / "a" / "b"
    home.mkdir(parents=True)
    (tmp_path / "x.json").write_text("{}")
    before = tree(tmp_path)
    for sid in ("../../x", "../x", "a/b", "", "x.usage"):
        for args in (["status"], ["add", "p.md"], ["rm", "1"], ["next"]):
            r = run("plan", "--home", str(home), "--session", sid, *args)
            assert r.returncode == 2, (sid, args)
    assert tree(tmp_path) == before


def test_an_unregistered_lead_exits_2(world):
    before = tree(world)
    r = run("plan", "--session", "nobody")
    assert r.returncode == 2 and "plan: nobody is not a registered lead — run pilead lead-start first" in r.stderr
    r = run("plan", "add", "a-packet.md", "--session", "nobody")
    assert r.returncode == 2
    assert tree(world) == before


@pytest.mark.parametrize("args", [["add", "a-packet.md"], ["rm", "1"], ["done", "1"], ["bind", "1", "exec-1"]])
def test_an_executor_cannot_change_a_plan(world, args):
    pkt = packet(world)
    session(world, "exec-1")
    f = seed(world, [item(pkt)])
    before = tree(H(world))
    r = run("plan", *args, env={"PI_LEAD_ROLE": "executor", "PI_LEAD_SID": "lead-1"})
    assert r.returncode == 2 and "plan: an executor cannot change a lead's plan" in r.stderr
    assert tree(H(world)) == before and f.read_bytes() == before["plans/lead-1.json"][1]


def test_an_executor_can_read_status_and_next(world):
    pkt = packet(world)
    seed(world, [item(pkt)])
    env = {"PI_LEAD_ROLE": "executor"}
    assert run("plan", "--session", "lead-1", "status", env=env).returncode == 0
    r = run("plan", "--session", "lead-1", "next", env=env)
    assert r.returncode == 0 and r.stdout.startswith("pilead spawn ")


# ── leads/ is not touched ────────────────────────────────────────────────────────────────────────
def test_no_sub_verb_creates_a_file_in_leads_and_list_shows_the_same_leads(world):
    pkt = packet(world)
    session(world, "exec-1")
    leads_before = tree(H(world) / "leads")

    def lead_sids():
        r = run("list", "--json")
        assert r.returncode == 0, r.stderr
        return sorted(ld["sid"] for ld in json.loads(r.stdout)["leads"])

    before = lead_sids()
    for args in (["add", "a-packet.md"], ["add", "a-packet.md", "--before", "1"], ["status"], ["next"],
                 ["bind", "1", "exec-1"], ["done", "2"], ["rm", "1"]):
        assert run("plan", *args).returncode == 0, args
        assert tree(H(world) / "leads") == leads_before, args
    assert (H(world) / "plans" / "lead-1.json").is_file()
    assert lead_sids() == before == ["lead-1"]
    del pkt

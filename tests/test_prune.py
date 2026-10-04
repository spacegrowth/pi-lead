"""`pilead prune` over fixture homes whose file times the tests set. Run in-process (`cli.main`) with the
vendored iterm backend's `running` / `run_osascript` monkeypatched (a lead's tab exists only when its handle
names LIVE_UUID), and through bin/pilead where an id must be refused before anything is read. Every home is
under the test's temp directory."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_close_variants import tree
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli  # noqa: E402
from pilead.launch import backend  # noqa: E402

LIVE_UUID = "AAAAAAAA-0000-4000-8000-000000000001"
DAY = 86400


@pytest.fixture(autouse=True)
def terminal(monkeypatch, tmp_path):
    it = backend.by_name("iterm")

    def run_osascript(script, timeout=None):
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    monkeypatch.chdir(tmp_path)


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def iso(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def age_files(d, days):
    """Set every entry directly inside `d` to `days` old."""
    t = time.time() - days * DAY
    for e in os.scandir(d):
        os.utime(e.path, (t, t), follow_symlinks=False)


def session(h, sid, lead="lead-1", status="closed", days=10, keep=False, **extra):
    s = h / "sessions" / sid
    s.mkdir(parents=True, exist_ok=True)
    m = {"sid": sid, "lead": lead, "worktree": "/nowhere", "topic": "t", "model": "m", "status": status,
         "created": "-", "packets": 1, "label": f"[Exec] {sid}", **extra}
    if keep:
        m["keep"] = True
    (s / "meta.json").write_text(json.dumps(m))
    (s / "status").write_text(status + "\n")
    (s / "packet-0001.md").write_text("GOAL\n")
    age_files(s, days)
    return s


def lead(h, sid, project="proj", days=10, handle=None, inbox="", record_sid=None, cwd=None):
    d = h / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": record_sid or sid, "project": project, "cwd": str(cwd or h.parent / f"cwd-{sid}"),
           "inbox": str(d / f"{sid}.inbox.md"), "iterm_handle": handle,
           "started": iso(days * DAY + 60) if days else iso(0)}  # a minute past: `started` has whole seconds
    (d / f"{sid}.json").write_text(json.dumps(rec))
    (d / f"{sid}.inbox.md").write_text(inbox)
    (d / f"{sid}.route").write_text("{}\n")
    return d / f"{sid}.json"


def prune(capsys, *args):
    rc = cli.main(["prune", *args])
    out = capsys.readouterr()
    return rc, out.out, out.err


def ledger(h, event=None):
    p = h / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if event is None or r["event"] == event]


def gone(h, sid):
    return not os.path.lexists(h / "sessions" / sid)


# ── the five session conditions, one test each ───────────────────────────────
def test_condition_1_scope(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "mine")
    session(h, "theirs", lead="lead-2")
    before = tree(h / "sessions" / "theirs")
    rc, out, _ = prune(capsys, "--lead", "lead-1")
    assert rc == 0 and gone(h, "mine") and not gone(h, "theirs")
    assert tree(h / "sessions" / "theirs") == before
    assert "theirs" not in out


def test_condition_2_stored_status_closed_or_dead(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "c-closed", status="closed")
    session(h, "d-dead", status="dead")
    session(h, "i-idle", status="idle")
    rc, out, _ = prune(capsys, "--lead", "lead-1")
    assert gone(h, "c-closed") and gone(h, "d-dead") and not gone(h, "i-idle")
    assert out.splitlines()[0] == "pruned 2 session(s) and 0 lead record(s):"
    assert "  c-closed (closed, 10d)" in out.splitlines() and "  d-dead (dead, 10d)" in out.splitlines()


def test_condition_3_not_pinned(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "free")
    s = session(h, "pinned", keep=True)
    before = tree(s)
    rc, out, _ = prune(capsys, "--lead", "lead-1")
    assert gone(h, "free") and tree(s) == before
    assert "  kept pinned: pinned" in out.splitlines()


def test_condition_4_age_known_and_older_than_n_days(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "old", days=8)
    young = session(h, "young", days=6)
    unknown = session(h, "unknown", days=30)
    before = {"young": tree(young), "unknown": tree(unknown)}
    unknown.chmod(0o300)  # the directory cannot be listed: its age is unknown (meta.json still reads)
    try:
        rc, out, _ = prune(capsys, "--lead", "lead-1", "--days", "7")
    finally:
        unknown.chmod(0o755)
    assert gone(h, "old") and not gone(h, "young") and not gone(h, "unknown")
    assert {"young": tree(young), "unknown": tree(unknown)} == before
    lines = out.splitlines()
    assert "  kept young: newer than 7 days" in lines and "  kept unknown: age unknown" in lines


def test_condition_5_resolves_directly_inside_home_sessions(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "real")
    outside = tmp_path / "elsewhere" / "target"
    session(tmp_path / "elsewhere", "target")
    os.replace(tmp_path / "elsewhere" / "sessions" / "target", outside)
    (h / "sessions" / "linked").symlink_to(outside, target_is_directory=True)
    before = tree(tmp_path / "elsewhere")
    rc, out, _ = prune(capsys, "--lead", "lead-1")
    assert gone(h, "real") and (h / "sessions" / "linked").is_symlink()
    assert tree(tmp_path / "elsewhere") == before
    assert "  kept linked: outside home" in out.splitlines()


# ── what is never removed ────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["busy", "idle", "reported", "stalled", "paused"])
def test_a_session_that_has_not_ended_is_never_removed(tmp_path, capsys, status):
    h = H(tmp_path)
    s = session(h, f"s-{status}", status=status, days=100)
    before = tree(h)
    rc, out, _ = prune(capsys, "--all", "--days", "0")
    assert rc == 0 and tree(h) == before and out == "nothing to prune\n"
    assert s.is_dir()


def test_an_unreadable_record_is_kept_and_named(tmp_path, capsys):
    h = H(tmp_path)
    s = session(h, "bad")
    (s / "meta.json").write_text("{not json")
    age_files(s, 30)
    before = tree(s)
    rc, out, _ = prune(capsys, "--all")
    assert tree(s) == before and "  kept bad: unreadable" in out.splitlines()


# ── scope ────────────────────────────────────────────────────────────────────
def test_lead_removes_only_that_leads_sessions(tmp_path, capsys):
    h = H(tmp_path)
    for sid, ld in (("a-1", "lead-1"), ("a-2", "lead-1"), ("b-1", "lead-2"), ("c-1", None)):
        session(h, sid, lead=ld)
    prune(capsys, "--lead", "lead-2")
    assert [gone(h, s) for s in ("a-1", "a-2", "b-1", "c-1")] == [False, False, True, False]


def test_the_caller_is_the_scope_when_no_option_is_given(tmp_path, capsys, monkeypatch):
    h = H(tmp_path)
    lead(h, "lead-1", handle=f"w0t0p0:{LIVE_UUID}")
    session(h, "mine")
    session(h, "theirs", lead="lead-2")
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")
    rc, out, _ = prune(capsys)
    assert rc == 0 and gone(h, "mine") and not gone(h, "theirs")


def test_no_scope_exits_2_and_changes_nothing(tmp_path, capsys, monkeypatch):
    h = H(tmp_path)
    session(h, "s-1")
    monkeypatch.setenv("PI_LEAD_SID", "no-such-lead")  # not a registered lead: no caller
    before = tree(tmp_path)
    rc, out, err = prune(capsys)
    assert (rc, out) == (2, "") and len(err.splitlines()) == 1
    assert "--lead" in err and "--all" in err
    assert tree(tmp_path) == before


def test_all_covers_every_leads_sessions(tmp_path, capsys):
    h = H(tmp_path)
    for sid, ld in (("a-1", "lead-1"), ("b-1", "lead-2"), ("c-1", None)):
        session(h, sid, lead=ld)
    rc, out, _ = prune(capsys, "--all")
    assert all(gone(h, s) for s in ("a-1", "b-1", "c-1"))
    assert out.splitlines()[0] == "pruned 3 session(s) and 0 lead record(s):"


@pytest.mark.parametrize("days", ["-1", "1.5", "x", "", " 7"])
def test_days_must_be_a_whole_number(tmp_path, capsys, days):
    h = H(tmp_path)
    session(h, "s-1")
    before = tree(tmp_path)
    rc, out, err = prune(capsys, "--all", "--days", days)
    assert (rc, out) == (2, "") and len(err.splitlines()) == 1
    assert tree(tmp_path) == before


def test_days_zero_is_accepted(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "s-1", days=1)
    prune(capsys, "--all", "--days", "0")
    assert gone(h, "s-1")


# ── ids ──────────────────────────────────────────────────────────────────────
def test_an_unusable_lead_id_is_refused_and_the_temp_dir_is_byte_identical(tmp_path):
    h = tmp_path / "outer" / "home"
    session(h, "s-1", lead="../../x")
    lead(h, "lead-1")
    (tmp_path / "x.json").write_text('{"sid": "x"}\n')  # where leads/../../x.json points
    before = tree(tmp_path)
    r = subprocess.run([str(PILEAD), "prune", "--lead", "../../x", "--home", str(h)], capture_output=True, text=True)
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr.startswith("pilead: lead id '../../x' is not usable:") and len(r.stderr.splitlines()) == 1
    assert tree(tmp_path) == before


def test_a_ghost_record_is_removed_by_its_file_name_only(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "g-1", record_sid="../../x", days=30)
    decoys = [h / "leads" / ".." / ".." / name for name in ("x.json", "x.route", "x.inbox.md", "x.usage.json")]
    for p in decoys:
        p.write_text("decoy\n")
    before = {str(p): p.read_bytes() for p in decoys}
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0 and not (h / "leads" / "g-1.json").exists() and not (h / "leads" / "g-1.route").exists()
    assert "  [lead] proj (g-1, 30d)" in out.splitlines()
    assert {str(p): p.read_bytes() for p in decoys} == before
    assert [(e["session_id"], e["project"]) for e in ledger(h, "lead_pruned")] == [("g-1", "proj")]


def test_a_record_whose_file_name_is_not_a_usable_id_is_kept(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "-bad", days=30)  # no lead id starts with `-`; leads/*.json still lists it
    (h.parent / "-bad.json").write_text("decoy\n")
    before, decoy = tree(h / "leads"), (h.parent / "-bad.json").read_bytes()
    rc, out, _ = prune(capsys, "--all")
    assert tree(h / "leads") == before and (h.parent / "-bad.json").read_bytes() == decoy
    assert "  kept [lead] proj (-bad): unusable id" in out.splitlines()


# ── lead records ─────────────────────────────────────────────────────────────
def test_lead_records(tmp_path, capsys, monkeypatch):
    h = H(tmp_path)
    lead(h, "caller", days=0, handle=f"w0t0p0:{LIVE_UUID}")   # the caller (live; the scope's project)
    lead(h, "ghost-old", days=30)
    lead(h, "ghost-young", days=3)
    lead(h, "live", days=30, handle=f"w0t0p0:{LIVE_UUID}")
    lead(h, "unreachable", days=0)                            # no tab, started seconds ago
    lead(h, "pending", days=30, inbox="a wake\n")
    lead(h, "claimed", days=30)
    (h / "leads" / "claimed.inbox.md.claim-1").write_text("a claimed wake\n")
    lead(h, "other-project", project="other", days=30)
    usage_cache = h / "usage" / "leads" / "ghost-old.json"
    usage_cache.parent.mkdir(parents=True)
    usage_cache.write_text("{}\n")
    (h / "leads" / "ghost-old.usage.json").write_text("{}\n")
    monkeypatch.setenv("PI_LEAD_SID", "caller")
    keep = [p for p in (h / "leads").iterdir() if not p.name.startswith("ghost-old")]
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in keep}
    rc, out, _ = prune(capsys, "--days", "7")
    assert rc == 0
    assert not any(os.path.lexists(p) for p in (h / "leads" / "ghost-old.json", h / "leads" / "ghost-old.route",
                                                 h / "leads" / "ghost-old.inbox.md", usage_cache,
                                                 h / "leads" / "ghost-old.usage.json"))
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in keep} == before
    lines = out.splitlines()
    assert lines[0] == "pruned 0 session(s) and 1 lead record(s):" and lines[1] == "  [lead] proj (ghost-old, 30d)"
    assert "  kept [lead] proj (ghost-young): newer than 7 days" in lines
    assert "  kept [lead] proj (pending): pending wake lines" in lines
    assert "  kept [lead] proj (claimed): pending wake lines" in lines
    assert not any(n in out for n in ("(live)", "(unreachable)", "other-project"))
    assert [(e["session_id"], e["project"]) for e in ledger(h, "lead_pruned")] == [("ghost-old", "proj")]
    # with --all, the other project's ghost goes too; the caller never does
    rc, out, _ = prune(capsys, "--all", "--days", "7")
    assert not (h / "leads" / "other-project.json").exists() and (h / "leads" / "caller.json").exists()


def test_the_caller_is_kept_even_as_an_old_ghost(tmp_path, capsys, monkeypatch):
    h = H(tmp_path)
    lead(h, "caller", days=30)
    monkeypatch.setenv("PI_LEAD_SID", "caller")
    before = tree(h)
    rc, out, _ = prune(capsys, "--all")
    assert tree(h) == before
    assert "  kept [lead] proj (caller): the calling lead" in out.splitlines()


# ── --dry-run, and a real run's untouched state ──────────────────────────────
def build_mixed(tmp_path):
    h = H(tmp_path)
    session(h, "old-closed")
    session(h, "old-dead", status="dead", lead="lead-2")
    session(h, "young-closed", days=1)
    session(h, "busy-old", status="busy", days=30)
    session(h, "pinned-old", keep=True)
    lead(h, "ghost-old", days=30, cwd=tmp_path / "cwd-ghost")
    log = write_log("ghost-old", tmp_path / "cwd-ghost", [assistant(u(3, 4, 5, 6))])
    t = time.time() - 30 * DAY
    os.utime(log, (t, t))
    lead(h, "ghost-pending", days=30, inbox="wake\n")
    lead(h, "live", days=30, handle=f"w0t0p0:{LIVE_UUID}")
    return h


def test_dry_run_leaves_home_byte_identical_and_lists_what_it_would_remove(tmp_path, capsys):
    h = build_mixed(tmp_path)
    before, agent = tree(h), tree(tmp_path / "pi-agent")
    rc, out, _ = prune(capsys, "--all", "--dry-run")
    assert rc == 0
    assert tree(h) == before and tree(tmp_path / "pi-agent") == agent
    assert not (h / "usage" / "leads" / "ghost-old.json").exists()  # no usage cache written
    lines = out.splitlines()
    assert lines[0] == "would prune 2 session(s) and 1 lead record(s):"
    assert lines[1:4] == ["  old-closed (closed, 10d)", "  old-dead (dead, 10d)", "  [lead] proj (ghost-old, 30d)"]
    assert "  kept pinned-old: pinned" in lines and "  kept young-closed: newer than 7 days" in lines
    assert "  kept [lead] proj (ghost-pending): pending wake lines" in lines
    assert not (h / "ledger.jsonl").exists()


def test_a_real_run_leaves_everything_it_did_not_remove_byte_identical(tmp_path, capsys):
    h = build_mixed(tmp_path)
    neighbour = tmp_path / "neighbour"
    (neighbour / "sessions" / "old-closed").mkdir(parents=True)
    (neighbour / "sessions" / "old-closed" / "meta.json").write_text("{}")
    before, near = tree(h), tree(neighbour)
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0
    after = tree(h)
    removed = {k for k in before if k.startswith(("sessions/old-closed", "sessions/old-dead"))
               or k.startswith(("leads/ghost-old.", "usage/leads/ghost-old"))}
    assert removed and not (removed & set(after))
    # the directories that held what was removed have a new mtime; nothing else differs
    containers = {"ledger.jsonl", "sessions", "leads", "usage/leads"}
    assert {k: v for k, v in after.items() if k not in containers} == \
        {k: v for k, v in before.items() if k not in removed and k not in containers}
    assert tree(neighbour) == near
    assert not (h / "usage" / "leads" / "ghost-old.json").exists()


def test_the_ledger_gains_one_event_per_removal(tmp_path, capsys):
    h = build_mixed(tmp_path)
    prune(capsys, "--all")
    evs = ledger(h)
    assert sorted((e["event"], e["session_id"], e.get("status"), e.get("project")) for e in evs) == [
        ("lead_pruned", "ghost-old", None, "proj"),
        ("pruned", "old-closed", "closed", None),
        ("pruned", "old-dead", "dead", None)]
    assert all(set(e) == {"ts", "event", "session_id", "status"} for e in evs if e["event"] == "pruned")
    assert all(set(e) == {"ts", "event", "session_id", "project"} for e in evs if e["event"] == "lead_pruned")


def test_a_superseded_session_is_shown_as_superseded(tmp_path, capsys):
    h = H(tmp_path)
    session(h, "old-sup", superseded_by="succ-1")
    rc, out, _ = prune(capsys, "--all")
    assert "  old-sup (superseded, 10d)" in out.splitlines()
    assert ledger(h, "pruned")[0]["status"] == "closed"


# ── the plan of a ghost lead ─────────────────────────────────────────────────────────────────────
def plan_for(h, sid, items=None, raw=None):
    p = h / "plans" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps({"version": 1, "items": items}))
    return p


def pitem(done=None):
    return {"n": 1, "packet": "p-packet.md", "target": "fresh", "model": None, "note": None, "executor": None,
            "packet_n": None, "added": "2026-01-01T00:00:00Z", "done": done}


def test_a_ghost_lead_with_a_finished_plan_goes_with_that_plan(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    lead(h, "ghost-none", days=30)
    p = plan_for(h, "ghost-a", [pitem(done="2026-01-02T00:00:00Z")])
    other = plan_for(h, "someone-else", [pitem()])
    other_before = other.read_bytes()
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0
    assert not p.exists() and not (h / "leads" / "ghost-a.json").exists() and not (h / "leads" / "ghost-none.json").exists()
    assert other.read_bytes() == other_before
    lines = out.splitlines()
    assert lines[0] == "pruned 0 session(s) and 2 lead record(s):"
    assert f"    removed plan {p}" in lines and lines.index(f"    removed plan {p}") == lines.index(
        "  [lead] proj (ghost-a, 30d)") + 1
    assert sorted(e["session_id"] for e in ledger(h, "lead_pruned")) == ["ghost-a", "ghost-none"]


def test_a_ghost_lead_with_an_empty_plan_goes_with_it(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    p = plan_for(h, "ghost-a", [])
    prune(capsys, "--all")
    assert not p.exists() and not (h / "leads" / "ghost-a.json").exists()


def test_a_ghost_lead_whose_plan_has_open_items_is_kept_with_the_reason(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    p = plan_for(h, "ghost-a", [pitem(done="2026-01-02T00:00:00Z"), pitem()])
    before = tree(h)
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0 and tree(h) == before
    assert "  kept [lead] proj (ghost-a): plan has open items" in out.splitlines()
    assert p.is_file()


@pytest.mark.parametrize("raw", ["{not json", "[]", '{"items": 1}'])
def test_a_ghost_lead_whose_plan_cannot_be_read_is_kept_with_the_reason(tmp_path, capsys, raw):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    plan_for(h, "ghost-a", raw=raw)
    before = tree(h)
    rc, out, _ = prune(capsys, "--all")
    assert rc == 0 and tree(h) == before
    assert "  kept [lead] proj (ghost-a): plan cannot be read" in out.splitlines()


def test_dry_run_with_plans_leaves_home_byte_identical(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-done", days=30)
    lead(h, "ghost-open", days=30)
    lead(h, "ghost-bad", days=30)
    p = plan_for(h, "ghost-done", [pitem(done="2026-01-02T00:00:00Z")])
    plan_for(h, "ghost-open", [pitem()])
    plan_for(h, "ghost-bad", raw="nope")
    before = tree(h)
    rc, out, _ = prune(capsys, "--all", "--dry-run")
    assert rc == 0 and tree(h) == before and not (h / "ledger.jsonl").exists()
    lines = out.splitlines()
    assert lines[0] == "would prune 0 session(s) and 1 lead record(s):"
    assert f"    would remove plan {p}" in lines
    assert "  kept [lead] proj (ghost-open): plan has open items" in lines
    assert "  kept [lead] proj (ghost-bad): plan cannot be read" in lines


def test_prune_binds_nothing_and_writes_no_plan(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    session(h, "s-1", lead="ghost-a", status="busy", days=30)
    p = plan_for(h, "ghost-a", [pitem()])
    (h / "ledger.jsonl").write_text(json.dumps({"ts": "2026-02-01T00:00:00Z", "event": "spawned",
                                                "session_id": "s-1", "lead": "ghost-a", "source": "p-packet.md"}) + "\n")
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    prune(capsys, "--all")
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


def test_a_plan_directory_that_points_outside_home_keeps_the_record(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-a", days=30)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ghost-a.json").write_text(json.dumps({"version": 1, "items": []}))
    (h / "plans").symlink_to(outside)
    before = tree(h)
    rc, out, _ = prune(capsys, "--all")
    assert tree(h) == before and (outside / "ghost-a.json").is_file()
    assert "  kept [lead] proj (ghost-a): outside home" in out.splitlines()


# ── wake/<sid>/: a pruned lead's health file goes with it ────────────────────────────────────────
def test_a_pruned_lead_takes_its_wake_directory_and_names_it(tmp_path, capsys):
    h = H(tmp_path)
    lead(h, "ghost-old", days=30)
    lead(h, "ghost-bare", days=30)
    lead(h, "ghost-young", days=3)
    for sid in ("ghost-old", "ghost-young"):
        (h / "wake" / sid).mkdir(parents=True)
        (h / "wake" / sid / "health.json").write_text("{}\n")
    before = tree(h)
    rc, out, _ = prune(capsys, "--all", "--days", "7", "--dry-run")
    assert rc == 0 and tree(h) == before
    lines = out.splitlines()
    i = lines.index("  [lead] proj (ghost-old, 30d)")
    assert lines[i + 1] == f"    would remove {h / 'wake' / 'ghost-old'}"
    rc, out, _ = prune(capsys, "--all", "--days", "7")
    lines = out.splitlines()
    i = lines.index("  [lead] proj (ghost-old, 30d)")
    assert lines[i + 1] == f"    removed {h / 'wake' / 'ghost-old'}"
    assert not os.path.lexists(h / "wake" / "ghost-old")
    assert (h / "wake" / "ghost-young" / "health.json").is_file(), "a lead that is kept keeps its wake directory"
    assert "  [lead] proj (ghost-bare, 30d)" in lines
    assert not any(str(h / "wake" / "ghost-bare") in ln for ln in lines), "a lead with no wake directory prints no line for it"
    assert [ln for ln in lines if str(h / "wake") in ln] == [f"    removed {h / 'wake' / 'ghost-old'}"]

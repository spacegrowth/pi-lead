"""`pilead stop <lead_sid>` and `pilead close --self <lead_sid>`: the lead steps down. Every test runs
against a home under its own temp directory; no process is signalled and no tab is touched."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_close_variants import tree

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
STEPPED = "lead lead-1 stepped down — wakes are off"


def pilead(*args, check=True, env=None):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stderr
    return r


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def lead_files(h, sid="lead-1", project="proj", inbox="", route=True, caches=True):
    d = h / "leads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": project, "cwd": "/nowhere",
                                               "inbox": str(d / f"{sid}.inbox.md"), "iterm_handle": None}))
    (d / f"{sid}.inbox.md").write_text(inbox)
    if route:
        (d / f"{sid}.route").write_text("{}\n")
    if caches:
        (d / f"{sid}.usage.json").write_text("{}\n")  # an older build's cache
        (h / "usage" / "leads").mkdir(parents=True, exist_ok=True)
        (h / "usage" / "leads" / f"{sid}.json").write_text("{}\n")
    return d


def session(h, sid, lead="lead-1", status="busy"):
    s = h / "sessions" / sid
    s.mkdir(parents=True, exist_ok=True)
    (s / "meta.json").write_text(json.dumps({"sid": sid, "lead": lead, "worktree": "/nowhere", "topic": "t",
                                             "model": "m", "status": status, "packets": 1,
                                             "label": f"[Exec] {sid}"}))
    (s / "status").write_text(status + "\n")
    (s / "packet-0001.md").write_text("GOAL\n")
    return s


def ledger(h):
    p = h / "ledger.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


@pytest.mark.parametrize("verb", [["stop"], ["close", "--self"]])
def test_each_file_is_removed_and_named(tmp_path, verb):
    h = H(tmp_path)
    d = lead_files(h)
    lead_files(h, "lead-2", "other")  # another lead's files are untouched
    other = {k: v for k, v in tree(h).items() if "lead-2" in k}
    r = pilead(*verb, "lead-1")
    want = [d / "lead-1.json", d / "lead-1.route", h / "usage" / "leads" / "lead-1.json",
            d / "lead-1.usage.json", d / "lead-1.inbox.md"]
    assert r.stdout.splitlines() == [f"removed {p}" for p in want] + [STEPPED]
    assert r.stderr == ""
    assert not any(os.path.lexists(p) for p in want)
    assert {k: v for k, v in tree(h).items() if "lead-2" in k} == other
    (ev,) = [e for e in ledger(h) if e["event"] == "lead_stepped_down"]
    assert (ev["session_id"], ev["project"]) == ("lead-1", "proj")


def test_stop_and_close_self_do_the_same(tmp_path):
    outs = []
    for i, verb in enumerate((["stop"], ["close", "--self"])):
        h = tmp_path / f"h{i}"
        lead_files(h, inbox="one wake\n")
        (h / "leads" / "lead-1.inbox.md.claim-1").write_text("two\n")
        r = pilead(*verb, "lead-1", "--home", str(h))
        after = {k: (kind, data) for k, (kind, data, _) in tree(h).items() if k != "ledger.jsonl"}
        outs.append((r.returncode, r.stdout.replace(str(h), "{H}"), r.stderr, after,
                     [(e["event"], e["session_id"], e["project"]) for e in ledger(h)]))
    assert outs[0] == outs[1]


def test_an_inbox_with_pending_lines_and_a_claim_file_are_kept_with_the_count(tmp_path):
    h = H(tmp_path)
    d = lead_files(h, inbox="wake one\n\nwake two\n")
    claim = d / "lead-1.inbox.md.claim-123"
    claim.write_text("claimed wake\n")
    keep = {k: v for k, v in tree(h).items() if "inbox" in k}
    r = pilead("stop", "lead-1")
    lines = r.stdout.splitlines()
    assert f"kept {d / 'lead-1.inbox.md'} — inbox, 2 pending wake line(s)" in lines
    assert f"kept {claim} — claimed inbox, 1 pending wake line(s)" in lines
    assert lines[-1] == STEPPED and f"removed {d / 'lead-1.json'}" in lines
    assert {k: v for k, v in tree(h).items() if "inbox" in k} == keep
    assert not (d / "lead-1.json").exists()


def test_a_claim_file_alone_with_pending_lines_keeps_the_inbox(tmp_path):
    h = H(tmp_path)
    d = lead_files(h, inbox="")
    (d / "lead-1.inbox.md.claim-9").write_text("pending\n")
    lines = pilead("stop", "lead-1").stdout.splitlines()
    assert f"kept {d / 'lead-1.inbox.md'} — inbox, 0 pending wake line(s)" in lines
    assert f"kept {d / 'lead-1.inbox.md.claim-9'} — claimed inbox, 1 pending wake line(s)" in lines
    assert (d / "lead-1.inbox.md").is_file()


def test_an_unknown_sid_prints_the_line_and_changes_nothing(tmp_path):
    h = H(tmp_path)
    lead_files(h, "lead-2")
    before = tree(tmp_path)
    for verb in (["stop"], ["close", "--self"]):
        r = pilead(*verb, "nope")
        assert (r.returncode, r.stdout, r.stderr) == (0, "nope is not a registered lead — nothing to stop\n", "")
    assert tree(tmp_path) == before


@pytest.mark.parametrize("verb", [["stop"], ["close", "--self"]])
def test_an_unusable_id_is_refused_and_the_temp_dir_is_byte_identical(tmp_path, verb):
    h = tmp_path / "outer" / "home"  # two directories deep in the temp directory
    lead_files(h)
    decoy = h / "leads" / ".." / ".." / "x.json"  # where leads/../../x.json points
    decoy.write_text('{"sid": "x", "project": "decoy"}\n')
    (h / "leads" / ".." / ".." / "x.route").write_text("decoy\n")
    before = tree(tmp_path)
    r = pilead(*verb, "../../x", "--home", str(h), check=False)
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr.startswith("pilead: lead id '../../x' is not usable:") and len(r.stderr.splitlines()) == 1
    assert tree(tmp_path) == before
    assert decoy.resolve().is_file()


def test_a_lead_with_a_live_executor_is_told_and_still_steps_down(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    busy = session(h, "exec-busy")
    session(h, "exec-closed", status="closed")
    session(h, "exec-other", lead="lead-2")
    child = subprocess.Popen(["sleep", "120"])  # a throwaway stand-in for the executor's pi
    try:
        (busy / "pid").write_text(f"{child.pid}\n")
        sessions = tree(h / "sessions")
        r = pilead("stop", "lead-1")
        assert r.stdout.splitlines()[-1] == STEPPED
        assert len(r.stderr.splitlines()) == 1 and "exec-busy" in r.stderr
        assert "exec-closed" not in r.stderr and "exec-other" not in r.stderr
        assert not (h / "leads" / "lead-1.json").exists()
        assert tree(h / "sessions") == sessions  # no session directory changes
        assert child.poll() is None, "a process was signalled"
    finally:
        child.kill()
        child.wait()
    assert not (tmp_path / "osa.log").exists()  # no tab was asked about or touched


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────
def plan_file(h, sid="lead-1", items=None, raw=None):
    p = h / "plans" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps({"version": 1, "items": items}))
    return p


def pitem(done=None):
    return {"n": 1, "packet": "p-packet.md", "target": "fresh", "model": None, "note": None, "executor": None,
            "packet_n": None, "added": "2026-01-01T00:00:00Z", "done": done}


@pytest.mark.parametrize("verb", [["stop"], ["close", "--self"]])
def test_a_plan_with_no_open_item_is_removed_and_named(tmp_path, verb):
    h = H(tmp_path)
    d = lead_files(h)
    p = plan_file(h, items=[pitem(done="2026-01-02T00:00:00Z"), pitem(done="2026-01-03T00:00:00Z")])
    other = plan_file(h, "lead-2", items=[pitem()])
    other_before = other.read_bytes()
    r = pilead(*verb, "lead-1")
    lines = r.stdout.splitlines()
    assert f"removed {p}" in lines and lines[-1] == STEPPED and lines.index(f"removed {p}") > lines.index(
        f"removed {d / 'lead-1.inbox.md'}")
    assert not p.exists() and other.read_bytes() == other_before


def test_an_empty_plan_has_no_open_item(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    p = plan_file(h, items=[])
    assert f"removed {p}" in pilead("stop", "lead-1").stdout.splitlines()
    assert not p.exists()


@pytest.mark.parametrize("verb", [["stop"], ["close", "--self"]])
def test_a_plan_with_open_items_is_kept_and_named_with_the_count(tmp_path, verb):
    h = H(tmp_path)
    lead_files(h)
    p = plan_file(h, items=[pitem(done="2026-01-02T00:00:00Z"), pitem(), pitem()])
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    r = pilead(*verb, "lead-1")
    lines = r.stdout.splitlines()
    assert f"kept {p} — 2 open item(s)" in lines and lines[-1] == STEPPED
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before  # not bound, not rewritten
    assert not (h / "leads" / "lead-1.json").exists()


@pytest.mark.parametrize("raw", ["{not json", "[]", '{"items": 3}', '{"items": ["x"]}'])
def test_an_unreadable_plan_is_kept_and_named(tmp_path, raw):
    h = H(tmp_path)
    lead_files(h)
    p = plan_file(h, raw=raw)
    r = pilead("stop", "lead-1")
    assert f"kept {p} — cannot be read" in r.stdout.splitlines() and r.stdout.splitlines()[-1] == STEPPED
    assert p.read_text() == raw


def test_a_lead_with_no_plan_prints_nothing_about_one(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    plan_file(h, "lead-2", items=[pitem()])  # another lead's
    out = pilead("stop", "lead-1").stdout
    assert "plans" not in out and "open item" not in out and "cannot be read" not in out
    assert out.splitlines()[-1] == STEPPED


def test_a_plan_link_is_removed_itself_never_its_target(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"version": 1, "items": []}))
    (h / "plans").mkdir()
    (h / "plans" / "lead-1.json").symlink_to(target)
    r = pilead("stop", "lead-1")
    assert f"removed {h / 'plans' / 'lead-1.json'}" in r.stdout.splitlines()
    assert target.is_file() and not os.path.lexists(h / "plans" / "lead-1.json")


def test_a_plans_directory_that_points_outside_home_is_never_written_through(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "lead-1.json").write_text(json.dumps({"version": 1, "items": []}))
    (h / "plans").symlink_to(outside)
    r = pilead("stop", "lead-1")
    assert f"kept {h / 'plans' / 'lead-1.json'} — outside home" in r.stdout.splitlines()
    assert (outside / "lead-1.json").is_file()


# ── wake/<sid>/: the lead's health file goes with it ─────────────────────────────────────────────
def wake_dir(h, sid="lead-1"):
    d = h / "wake" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text('{"sid": "%s"}\n' % sid)
    (d / "health.json.tmp-123").write_text("{}\n")  # a write the extension did not finish
    return d


@pytest.mark.parametrize("verb", [["stop"], ["close", "--self"]])
def test_the_wake_directory_is_removed_and_named(tmp_path, verb):
    h = H(tmp_path)
    d = lead_files(h)
    w = wake_dir(h)
    other = wake_dir(h, "lead-2")
    r = pilead(*verb, "lead-1")
    want = [d / "lead-1.json", d / "lead-1.route", h / "usage" / "leads" / "lead-1.json",
            d / "lead-1.usage.json", w, d / "lead-1.inbox.md"]
    assert r.stdout.splitlines() == [f"removed {p}" for p in want] + [STEPPED]
    assert not os.path.lexists(w)
    assert (other / "health.json").is_file(), "another lead's wake directory is untouched"


def test_a_lead_with_no_wake_directory_prints_no_line_for_it(tmp_path):
    h = H(tmp_path)
    lead_files(h)
    (h / "wake").mkdir()
    r = pilead("stop", "lead-1")
    assert not any(str(h / "wake") in ln for ln in r.stdout.splitlines()), r.stdout
    assert r.stdout.splitlines()[-1] == STEPPED

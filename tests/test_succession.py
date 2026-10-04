"""lib/pilead/succession.py `move_lead`, in-process, homes under the test's temp directory. The terminal is the
vendored iterm backend with `running` / `run_osascript` replaced (as tests/test_list.py does): a lead's tab exists
only when its handle names LIVE_UUID. A lead with no handle, started a day ago, is a ghost."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, state, succession, usage  # noqa: E402
from pilead.launch import backend  # noqa: E402
from pilead.state import PileadError  # noqa: E402

LIVE_UUID = "AAAAAAAA-0000-4000-8000-000000000001"
LIVE = f"w0t0p0:{LIVE_UUID}"
OLD, NEW = "lead-old", "lead-new"


@pytest.fixture(autouse=True)
def terminal(monkeypatch, tmp_path):
    it = backend.by_name("iterm")

    def run_osascript(script, timeout=None):
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    d = tmp_path / "cwd"
    d.mkdir()
    monkeypatch.chdir(d)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    (h / "leads").mkdir(parents=True)
    return h


def iso(ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def old_lead(home, handle=None, **extra):
    rec = {"sid": OLD, "project": "proj", "cwd": "/repo", "inbox": str(home / "leads" / f"{OLD}.inbox.md"),
           "iterm_handle": handle, "terminal": "iTerm.app", "started": iso(86400), "autonomous": True,
           "autonomous_source": "command", "tier": "lead", "tier_source": "command", **extra}
    (home / "leads" / f"{OLD}.json").write_text(json.dumps(rec))
    (home / "leads" / f"{OLD}.inbox.md").write_text("")
    return rec


def session(home, sid, lead=OLD, status="busy", report=False):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"sid": sid, "lead": lead, "worktree": "/wt", "packets": 1,
                                             "status": status}))
    (d / "status").write_text(status + "\n")
    if report:
        (d / "report-0001.md").write_text("Done.\n")
    return d


def meta(home, sid):
    return json.loads((home / "sessions" / sid / "meta.json").read_text())


def lines_of(p):
    return Path(p).read_text()


def events(home, name):
    return [{k: v for k, v in r.items() if k != "ts"} for r in ledger.read(home, event=name)]


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
            for p in sorted(root.rglob("*"))}


def full_setup(home, handle=None):
    old_lead(home, handle=handle)
    for sid, st in (("exec-a", "busy"), ("exec-b", "reported"), ("exec-c", "closed")):
        session(home, sid, status=st)
    session(home, "exec-other", lead="lead-x")
    (home / "leads" / f"{OLD}.inbox.md").write_text("reported exec-a /r/a-1.md\nreported exec-b /r/b-1.md\n")
    (home / "leads" / f"{NEW}.inbox.md").write_text("reported exec-z /r/z-1.md\n")  # what it held
    (home / "leads" / f"{OLD}.route").write_text("retain\n")
    launch = home / "leads" / f"{OLD}.launch"
    launch.mkdir()
    (launch / "pid").write_text("1\n")
    (home / "plans").mkdir()
    (home / "plans" / f"{OLD}.json").write_text(json.dumps({"items": []}))
    cache = usage.lead_cache_path(home, OLD)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}")
    return cache


def test_a_full_move(home):
    cache = full_setup(home)
    res = succession.move_lead(home, OLD, NEW, "takeover", env={})
    for sid in ("exec-a", "exec-b", "exec-c"):
        assert meta(home, sid)["lead"] == NEW
    assert meta(home, "exec-other")["lead"] == "lead-x"
    assert lines_of(home / "leads" / f"{NEW}.inbox.md") == (
        "reported exec-z /r/z-1.md\nreported exec-a /r/a-1.md\nreported exec-b /r/b-1.md\n")
    assert (home / "plans" / f"{NEW}.json").exists() and not (home / "plans" / f"{OLD}.json").exists()
    note = json.loads((home / "handoffs" / f"{OLD}.json").read_text())
    assert set(note) == {"from", "to", "project", "at", "reason"}
    assert (note["from"], note["to"], note["project"], note["reason"]) == (OLD, NEW, "proj", "takeover")
    for gone in (home / "leads" / f"{OLD}.json", home / "leads" / f"{OLD}.route", home / "leads" / f"{OLD}.launch",
                 cache, home / "leads" / f"{OLD}.inbox.md"):
        assert not os.path.lexists(gone), gone
    assert list((home / "leads").glob(f"{OLD}.inbox.md*")) == []
    assert events(home, "lead_moved") == [{"event": "lead_moved", "from_lead": OLD, "to_lead": NEW, "project": "proj",
                                           "reason": "takeover", "forced": False, "sessions": 3, "wake_lines": 2}]
    assert [(e["session_id"], e["to_lead"], e["reason"]) for e in events(home, "adopted")] == [
        ("exec-a", NEW, "takeover"), ("exec-b", NEW, "takeover"), ("exec-c", NEW, "takeover")]
    assert (res["again"], res["wake_lines"], res["plan"], res["claims_left"]) == (False, 2, "moved", [])
    assert [line for _s, line in res["sessions"]] == [f"adopted exec-a from {OLD} (ghost lead)",
                                                      f"adopted exec-b from {OLD} (ghost lead)",
                                                      f"adopted exec-c from {OLD} (ghost lead)"]


def test_the_new_record_carries_no_posture(home):
    rec = old_lead(home)
    res = succession.move_lead(home, OLD, NEW, "takeover",
                               env={"PI_SESSION_ID": NEW, "ITERM_SESSION_ID": "w1t2p0:BBBB"})
    got = json.loads((home / "leads" / f"{NEW}.json").read_text())
    started = got.pop("started")
    assert state.age_seconds(started) is not None and state.age_seconds(started) < 60
    assert got == {"sid": NEW, "project": "proj", "cwd": "/repo", "inbox": str(home / "leads" / f"{NEW}.inbox.md"),
                   "iterm_handle": "w1t2p0:BBBB", "terminal": "iTerm.app", "lineage_started": rec["started"],
                   "moved_from": OLD, "autonomous": False, "autonomous_source": "config", "tier": "auto",
                   "tier_source": "config"}
    assert (home / "leads" / f"{NEW}.inbox.md").read_text() == ""
    assert res["created"] is True


def test_the_handle_is_kept_only_when_the_environment_is_the_new_session(home):
    old_lead(home, lineage_started="2025-01-01T00:00:00Z")
    succession.move_lead(home, OLD, NEW, "takeover", project="p2", env={"ITERM_SESSION_ID": "w1t2p0:BBBB"})
    got = json.loads((home / "leads" / f"{NEW}.json").read_text())
    assert (got["iterm_handle"], got["project"], got["lineage_started"]) == (None, "p2", "2025-01-01T00:00:00Z")


def test_an_existing_new_record_is_kept_and_gains_only_two_fields(home):
    rec = old_lead(home)
    mine = {"sid": NEW, "project": "mine", "inbox": str(home / "leads" / f"{NEW}.inbox.md"), "autonomous": True,
            "started": "2026-01-01T00:00:00Z"}
    (home / "leads" / f"{NEW}.json").write_text(json.dumps(mine))
    res = succession.move_lead(home, OLD, NEW, "takeover", env={})
    got = json.loads((home / "leads" / f"{NEW}.json").read_text())
    assert got == {**mine, "lineage_started": rec["started"], "moved_from": OLD}
    assert res["created"] is False


def test_a_new_lead_with_a_plan_keeps_it_and_the_old_plan_stays(home):
    old_lead(home)
    (home / "plans").mkdir()
    (home / "plans" / f"{OLD}.json").write_text('{"old": 1}')
    (home / "plans" / f"{NEW}.json").write_text('{"new": 1}')
    res = succession.move_lead(home, OLD, NEW, "takeover", env={})
    assert (home / "plans" / f"{OLD}.json").read_text() == '{"old": 1}'
    assert (home / "plans" / f"{NEW}.json").read_text() == '{"new": 1}'
    assert res["plan"] == ("kept", home / "plans" / f"{OLD}.json", home / "plans" / f"{NEW}.json")


@pytest.mark.parametrize("case,moved", [("ghost", True), ("same-tab", True), ("live-elsewhere", False)])
def test_a_claim_file_moves_for_a_ghost_and_in_the_same_tab_only(home, case, moved):
    old_lead(home, handle=None if case == "ghost" else LIVE)
    claim = home / "leads" / f"{OLD}.inbox.md.claim-4242-1-1"
    claim.write_text("reported exec-q /r/q-1.md\n")
    env = {"ITERM_SESSION_ID": f"w9t9p9:{LIVE_UUID}"} if case == "same-tab" else {"ITERM_SESSION_ID": "w0t0p0:OTHER"}
    res = succession.move_lead(home, OLD, NEW, "takeover", forced=(case == "live-elsewhere"), env=env)
    inbox = (home / "leads" / f"{NEW}.inbox.md").read_text()
    if moved:
        assert inbox == "reported exec-q /r/q-1.md\n" and not claim.exists() and res["claims_left"] == []
    else:
        assert inbox == "" and claim.read_text() == "reported exec-q /r/q-1.md\n"
        assert res["claims_left"] == [(claim, 1)]


def test_a_second_call_after_a_complete_move_writes_no_second_event(home):
    full_setup(home)
    succession.move_lead(home, OLD, NEW, "takeover", env={})
    session(home, "exec-late")  # a session that named OLD after the move, and one more wake line
    (home / "leads" / f"{OLD}.inbox.md").write_text("reported exec-late /r/l-1.md\n")
    res = succession.move_lead(home, OLD, NEW, "takeover", env={})
    assert res["again"] is True
    assert [line for _s, line in res["sessions"]] == [f"adopted exec-late from {OLD} (no longer a registered lead)"]
    assert meta(home, "exec-late")["lead"] == NEW
    assert lines_of(home / "leads" / f"{NEW}.inbox.md").endswith("reported exec-late /r/l-1.md\n")
    assert len(events(home, "lead_moved")) == 1


def test_an_old_lead_with_neither_record_nor_note_raises(home):
    with pytest.raises(PileadError, match=f"{OLD} is not a registered lead"):
        succession.move_lead(home, OLD, NEW, "takeover", env={})


def _stable(home):
    """The state compared for the interrupted move: every file but the ledger, with the times in records left out."""
    out = {}
    for p in sorted(home.rglob("*")):
        rel = str(p.relative_to(home))
        if p.is_dir() or rel == "ledger.jsonl":
            out[rel] = None
            continue
        text = p.read_text().replace(str(home), "{HOME}")
        if p.suffix == ".json":
            d = json.loads(text)
            if isinstance(d, dict):
                for k in ("at", "started"):
                    d.pop(k, None)
            text = json.dumps(d, sort_keys=True)
        out[rel] = text
    return out


def test_an_interrupted_move_repeated_ends_as_an_uninterrupted_one(tmp_path, monkeypatch):
    a, b = tmp_path / "a" / "pi-lead-home", tmp_path / "b" / "pi-lead-home"
    for h in (a, b):
        (h / "leads").mkdir(parents=True)
        full_setup(h)
    succession.move_lead(a, OLD, NEW, "takeover", env={})
    real = state.atomic_write

    def boom(path, text):
        if Path(path).parent.name == "handoffs":
            raise OSError("interrupted at step 4")
        return real(path, text)

    monkeypatch.setattr(state, "atomic_write", boom)
    with pytest.raises(OSError):
        succession.move_lead(b, OLD, NEW, "takeover", env={})
    assert meta(b, "exec-a")["lead"] == NEW and (b / "leads" / f"{OLD}.json").exists()  # step 2 done, 5 not
    monkeypatch.setattr(state, "atomic_write", real)
    succession.move_lead(b, OLD, NEW, "takeover", env={})
    assert _stable(b) == _stable(a)


def test_an_unreadable_old_record_changes_nothing(home):
    full_setup(home)
    (home / "leads" / f"{OLD}.json").write_text("{nope")
    before = tree(home)
    with pytest.raises(PileadError, match="cannot be read"):
        succession.move_lead(home, OLD, NEW, "takeover", env={})
    assert tree(home) == before


def test_a_line_adopt_announced_is_not_written_twice(home):
    old_lead(home)
    d = session(home, "exec-r", status="reported", report=True)
    rep = str(d / "report-0001.md")
    (d / ".notified").write_text(f"{rep} {(d / 'report-0001.md').stat().st_mtime * 1000}\n")
    (home / "leads" / f"{OLD}.inbox.md").write_text(f"reported exec-r {rep}\n")
    res = succession.move_lead(home, OLD, NEW, "takeover", env={})
    assert lines_of(home / "leads" / f"{NEW}.inbox.md").count(f"reported exec-r ") == 1
    assert res["wake_lines"] == 0


def test_ids_are_checked_first(home):
    for old, new in (("../x", NEW), (OLD, "a/b"), (OLD, OLD)):
        before = tree(home)
        with pytest.raises(PileadError):
            succession.move_lead(home, old, new, "takeover", env={})
        assert tree(home) == before


def test_same_tab():
    assert succession.same_tab(f"w0t0p0:{LIVE_UUID}", f"w3t1p2:{LIVE_UUID}")
    assert not succession.same_tab(f"w0t0p0:{LIVE_UUID}", "w0t0p0:OTHER")
    assert not succession.same_tab(None, LIVE) and not succession.same_tab("", "")

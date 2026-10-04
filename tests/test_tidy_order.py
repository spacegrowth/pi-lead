"""lib/pilead/tabs.py, the wanted tab order (p23b change 1), in-process over homes the test builds by hand: `groups`,
`order`, `follows` and `tty_hints`. None of them asks the terminal (the backend's `run_osascript` is replaced with
one that fails the test) or writes anything (home is byte-identical afterwards). `tty_hints` asks a stub `ps`, first
on PATH, that logs its arguments."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import tabs  # noqa: E402
from pilead.launch import backend  # noqa: E402

it = backend.by_name("iterm")


def U(n):
    return f"{n:08d}-0000-4000-8000-{n:012d}"


def H(n, w=0, t=0):
    """A tab handle in window `w` whose UUID is U(n)."""
    return f"w{w}t{t}p0:{U(n)}"


def make_lead(home, sid, n, started="2026-10-01T09:00:00Z", w=0, **extra):
    d = Path(home) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": sid, "project": f"p-{sid}", "iterm_handle": H(n, w), "terminal": "iTerm.app", "started": started,
           **extra}
    (d / f"{sid}.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def make_exec(home, sid, lead, n, w=0, created="2026-10-01T10:00:00Z", status="busy", backend_name="iterm",
              handle=True, label=None, **extra):
    d = Path(home) / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "lead": lead, "created": created, "status": status, "label": label or f"[Exec] {sid}",
            **extra}
    if backend_name is not None:
        meta["backend"] = backend_name
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (d / "status").write_text(status + "\n")
    if handle:
        (d / "iterm-id").write_text(H(n, w) + "\n")
    return meta


def spawned(home, sid, ts):
    with open(Path(home) / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"ts": ts, "event": "spawned", "session_id": sid}) + "\n")


def tree(root):
    """{relative path: bytes, or "dir"} for everything under `root`."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs:
            out[str((Path(dirpath) / name).relative_to(root))] = "dir"
        for name in files:
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def sids(g):
    return [e["sid"] for e in g["executors"]]


@pytest.fixture
def home(tmp_path, monkeypatch):
    def no_terminal(*a, **k):
        raise AssertionError("the terminal was asked")
    monkeypatch.setattr(it, "run_osascript", no_terminal)
    h = tmp_path / "home"
    (h / "leads").mkdir(parents=True)
    return h


def two_groups(home):
    make_lead(home, "lead-a", 1, started="2026-10-01T08:00:00Z")
    make_lead(home, "lead-b", 2, started="2026-10-01T09:00:00Z")
    make_exec(home, "a-1", "lead-a", 11, created="2026-10-01T10:00:00Z")
    make_exec(home, "a-2", "lead-a", 12, created="2026-10-01T10:01:00Z")
    make_exec(home, "b-1", "lead-b", 21, created="2026-10-01T10:00:00Z")
    make_exec(home, "b-2", "lead-b", 22, created="2026-10-01T10:01:00Z")


# ── groups / order / follows ──────────────────────────────────────────────────────────────────────
def test_two_leads_with_two_executors_each(home):
    two_groups(home)
    gs = tabs.groups(home)
    assert [g["lead"] for g in gs] == ["lead-a", "lead-b"]
    assert set(gs[0]) == {"lead", "project", "label", "handle", "color", "window", "executors", "elsewhere"}
    assert gs[0]["project"] == "p-lead-a" and gs[0]["label"] == "[Lead] p-lead-a" and gs[0]["window"] == "w0"
    assert gs[0]["executors"][0] == {"sid": "a-1", "handle": H(11), "label": "[Exec] a-1"}
    assert tabs.order(gs) == [H(1), H(11), H(12), H(2), H(21), H(22)]
    assert tabs.follows(gs) == {H(11): H(1), H(12): H(1), H(21): H(2), H(22): H(2)}


def test_leads_sort_by_lineage_then_started_then_sid(home):
    make_lead(home, "lead-c", 3, started="2026-10-01T07:00:00Z", lineage_started="2026-10-01T10:00:00Z")
    make_lead(home, "lead-b", 2, started="2026-10-01T09:00:00Z")
    make_lead(home, "lead-a", 1, started="2026-10-01T09:00:00Z")
    make_lead(home, "lead-0", 4, started=None)
    assert [g["lead"] for g in tabs.groups(home)] == ["lead-0", "lead-a", "lead-b", "lead-c"]


def test_executors_follow_their_spawned_event_not_their_name(home):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11, created="2026-10-01T10:00:00Z")
    make_exec(home, "z-9", "lead-a", 12, created="2026-10-01T11:00:00Z")
    spawned(home, "z-9", "2026-10-01T09:00:00Z")
    spawned(home, "a-1", "2026-10-01T09:30:00Z")
    assert sids(tabs.groups(home)[0]) == ["z-9", "a-1"]


def test_without_a_spawned_event_executors_follow_created(home):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11, created="2026-10-01T12:00:00Z")
    make_exec(home, "z-9", "lead-a", 12, created="2026-10-01T11:00:00Z")
    assert sids(tabs.groups(home)[0]) == ["z-9", "a-1"]


def test_sessions_that_belong_to_no_group(home):
    make_lead(home, "lead-a", 1)
    make_exec(home, "ok", "lead-a", 10)
    make_exec(home, "closed", "lead-a", 11, status="closed")
    make_exec(home, "dead", "lead-a", 12, status="dead")
    make_exec(home, "superseded", "lead-a", 13, status="closed", superseded_by="ok")
    make_exec(home, "orphan", "lead-gone", 14)
    make_exec(home, "unowned", None, 15)
    make_exec(home, "unknown", "../x", 16)
    make_exec(home, "nohandle", "lead-a", 17, handle=False)
    make_exec(home, "terminal-app", "lead-a", 18, backend_name="terminal")
    make_exec(home, "noback", "lead-a", 19, backend_name=None)
    gs = tabs.groups(home)
    assert len(gs) == 1 and sids(gs[0]) == ["noback", "ok"] and gs[0]["elsewhere"] == []


def test_a_stored_status_in_the_meta_counts_when_there_is_no_status_file(home):
    make_lead(home, "lead-a", 1)
    make_exec(home, "x", "lead-a", 11, status="dead")
    (home / "sessions" / "x" / "status").unlink()
    assert sids(tabs.groups(home)[0]) == []


def test_an_executor_owned_through_a_forward_note_is_in_the_new_leads_group(home):
    make_lead(home, "lead-new", 1)
    (home / "handoffs").mkdir()
    (home / "handoffs" / "lead-old.json").write_text(json.dumps({"to": "lead-new"}))
    make_exec(home, "x", "lead-old", 11)
    assert sids(tabs.groups(home)[0]) == ["x"]


def test_meta_tab_is_the_handle_when_there_is_no_iterm_id(home):
    make_lead(home, "lead-a", 1)
    make_exec(home, "x", "lead-a", 11, handle=False, tab=H(11))
    assert tabs.groups(home)[0]["executors"][0]["handle"] == H(11)


def test_an_executor_in_another_window_is_elsewhere_and_not_ordered(home):
    make_lead(home, "lead-a", 1, w=0)
    make_exec(home, "here", "lead-a", 11, w=0)
    make_exec(home, "there", "lead-a", 12, w=3)
    g = tabs.groups(home)[0]
    assert sids(g) == ["here"] and g["elsewhere"] == ["there"]
    assert tabs.order([g]) == [H(1), H(11)] and H(12) not in tabs.follows([g])


@pytest.mark.parametrize("kind", ["no handle", "other form", "bare uuid", "unreadable", "not an object",
                                  "other terminal"])
def test_leads_that_give_no_group(home, kind):
    p = home / "leads" / "lead-x.json"
    if kind == "no handle":
        make_lead(home, "lead-x", 1, iterm_handle=None)
    elif kind == "other form":
        make_lead(home, "lead-x", 1, iterm_handle="FAKE-UUID")
    elif kind == "bare uuid":
        make_lead(home, "lead-x", 1, iterm_handle=U(1))
    elif kind == "unreadable":
        p.write_text("{not json")
    elif kind == "not an object":
        p.write_text("[1, 2]")
    else:
        make_lead(home, "lead-x", 1, terminal="Apple_Terminal")
    make_exec(home, "x", "lead-x", 11)
    assert tabs.groups(home) == []
    assert tabs.order([]) == [] and tabs.follows([]) == {}


def test_a_stray_usage_file_gives_no_group_and_no_error(home):
    (home / "leads" / "lead-a.usage.json").write_text(json.dumps({"iterm_handle": H(1), "terminal": "iTerm.app"}))
    make_lead(home, "lead-b", 2)
    assert [g["lead"] for g in tabs.groups(home)] == ["lead-b"]


def test_a_lead_with_no_terminal_recorded_is_grouped(home):
    make_lead(home, "lead-a", 1, terminal=None)
    assert [g["lead"] for g in tabs.groups(home)] == ["lead-a"]


def test_no_leads_directory_gives_no_group(tmp_path):
    assert tabs.groups(tmp_path / "nothing-here") == []


def test_colour_is_the_records_when_usable(home):
    make_lead(home, "lead-a", 1, color=[1, 2, 3])
    make_lead(home, "lead-b", 2, color="red")
    gs = tabs.groups(home)
    assert gs[0]["color"] == [1, 2, 3] and gs[1]["color"] is None


# ── a successor made by `pilead handoff` ──────────────────────────────────────────────────────────
@pytest.fixture
def handoff_terminal(tmp_path, monkeypatch):
    from test_handoff import OSA, PS
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "false")
    monkeypatch.setenv("FAKE_SEEN", str(tmp_path / "seen-at-spawn.txt"))
    monkeypatch.delenv("FAKE_SPAWN", raising=False)


def test_a_handoff_successor_sorts_where_its_predecessor_sorted(tmp_path, handoff_terminal):
    import test_handoff as th
    h = th.H(tmp_path)
    th.lead(tmp_path)  # lead-out, started 2026-10-01T09:00:00Z, window w0
    make_lead(h, "lead-early", 2, started="2026-10-01T08:00:00Z")
    make_lead(h, "lead-late", 3, started="2026-10-01T10:00:00Z")
    assert [g["lead"] for g in tabs.groups(h)] == ["lead-early", th.LEAD, "lead-late"]
    r = th.pilead("handoff", str(th.memo(tmp_path)))
    assert r.returncode == 0, r.stderr
    succ = th.successor_of(r)
    rec = json.loads((h / "leads" / f"{succ}.json").read_text())
    assert rec["lineage_started"] == "2026-10-01T09:00:00Z"
    # the successor's own tab is recorded when lead-start runs in it
    env = {"ITERM_SESSION_ID": H(9), "TERM_PROGRAM": "iTerm.app"}
    r2 = th.pilead("lead-start", succ, "--project", "proj", env=env, caller=None)
    assert r2.returncode == 0, r2.stderr
    assert [g["lead"] for g in tabs.groups(h)] == ["lead-early", succ, "lead-late"]
    r3 = th.pilead("lead-start", succ, "--project", "proj", env=env, caller=None)
    assert r3.returncode == 0, r3.stderr
    assert [g["lead"] for g in tabs.groups(h)] == ["lead-early", succ, "lead-late"]


# ── tty_hints ─────────────────────────────────────────────────────────────────────────────────────
PS_STUB = """#!/bin/sh
echo "$@" >> "$PS_LOG"
if [ -n "$PS_FAIL" ]; then exit 1; fi
printf '%s\\n' "  4242 ttys004" "  5151 ttys009"
"""


@pytest.fixture
def ps(tmp_path, monkeypatch):
    d = tmp_path / "psbin"
    d.mkdir()
    p = d / "ps"
    p.write_text(PS_STUB)
    p.chmod(0o755)
    log = tmp_path / "ps.log"
    monkeypatch.setenv("PS_LOG", str(log))
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])
    return log


def health(home, sid, pid, ended=None):
    d = Path(home) / "wake" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps({"pid": pid, "ended": ended}))


def pidfile(home, sid, text):
    (Path(home) / "sessions" / sid / "pid").write_text(text)


def test_tty_hints_from_the_health_file_and_the_pid_file_in_one_ps_call(home, ps):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11)
    health(home, "lead-a", 4242)
    pidfile(home, "a-1", "5151\n")
    gs = tabs.groups(home)
    before = tree(home)
    assert tabs.tty_hints(home, gs) == {H(1): "ttys004", H(11): "ttys009"}
    assert ps.read_text().splitlines() == ["-o pid=,tty= -p 4242,5151"]
    assert tree(home) == before


@pytest.mark.parametrize("case", ["ended", "pid 1", "text pid", "ps fails", "not listed"])
def test_tty_hints_that_give_no_hint(home, ps, monkeypatch, case):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11)
    health(home, "lead-a", 4242, ended="2026-10-01T11:00:00Z" if case == "ended" else None)
    pidfile(home, "a-1", {"pid 1": "1\n", "text pid": "abc\n", "not listed": "777\n"}.get(case, "5151\n"))
    if case == "ps fails":
        monkeypatch.setenv("PS_FAIL", "1")
    hints = tabs.tty_hints(home, tabs.groups(home))
    if case == "ended":
        assert hints == {H(11): "ttys009"}
    elif case == "ps fails":
        assert hints == {}
    else:
        assert hints == {H(1): "ttys004"}


def test_tty_hints_with_no_pid_asks_no_ps(home, ps):
    make_lead(home, "lead-a", 1)
    assert tabs.tty_hints(home, tabs.groups(home)) == {}
    assert not ps.exists()


def test_ttys_of_a_ps_that_cannot_run_gives_nothing(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")
    monkeypatch.setattr(subprocess, "run", boom)
    assert tabs.ttys_of([4242]) == {}


# ── nothing is written ────────────────────────────────────────────────────────────────────────────
def test_home_is_byte_identical_after_every_function(home, ps):
    two_groups(home)
    make_exec(home, "there", "lead-a", 30, w=2)
    make_exec(home, "orphan", "lead-gone", 31)
    (home / "leads" / "lead-c.json").write_text("{broken")
    make_lead(home, "lead-d", 4, color=None)
    spawned(home, "a-2", "2026-10-01T09:00:00Z")
    health(home, "lead-a", 4242)
    pidfile(home, "b-1", "5151")
    before = tree(home)
    gs = tabs.groups(home)
    tabs.order(gs)
    tabs.follows(gs)
    tabs.tty_hints(home, gs)
    assert tree(home) == before

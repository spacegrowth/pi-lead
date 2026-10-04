"""lib/pilead/ownership.py — who owns a session (`resolve`, `owner`) and `adopt`, in-process. The owner cases are
tests/owner-cases.json, which tests/ts/pi-lead.test.ts reads too (`resolveOwner`)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, ownership, state  # noqa: E402
from pilead.state import PileadError  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "owner-cases.json").read_text())
SID = "exec-1"


def tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        out[str(p.relative_to(root))] = "dir" if p.is_dir() else p.read_bytes()
    return out


def put(path, spec):
    path.parent.mkdir(parents=True, exist_ok=True)
    if spec.get("dir"):
        path.mkdir()
    elif "raw" in spec:
        path.write_text(spec["raw"])
    else:
        path.write_text(json.dumps(spec["json"]))


def build_case(home, case):
    for sid, spec in case["records"].items():
        put(home / "leads" / f"{sid}.json", spec)
    for sid, spec in case["notes"].items():
        put(home / "handoffs" / f"{sid}.json", spec)
    meta = {"sid": SID, "topic": "t", "packets": 1}
    if "lead" in case:
        meta["lead"] = case["lead"]
    d = home / "sessions" / SID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps(meta))
    return meta


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_owner_cases(tmp_path, case):
    home = tmp_path / "home"
    meta = build_case(home, case)
    before = tree(home)
    o = ownership.owner(home, meta)
    assert (o["state"], o["lead"]) == (case["state"], case["owner_lead"])
    assert (o["why"] is None) == (case["state"] != "unknown"), o
    if "resolve" in case:
        assert list(ownership.resolve(home, case["lead"])) == case["resolve"]
    assert tree(home) == before, "resolve and owner write nothing"


def test_the_four_states_are_all_in_the_table():
    assert {c["state"] for c in CASES} == {"owned", "orphan", "unowned", "unknown"}


def test_resolve_and_owner_never_raise(tmp_path):
    for bad in (None, 5, "", "../x", "a" * 500, ["x"]):
        assert ownership.resolve(tmp_path / "nope", bad)[1] == "unknown"
    assert ownership.owner(tmp_path, None)["state"] == "unowned"
    assert ownership.owner(tmp_path, "not a dict")["state"] == "unowned"
    f = tmp_path / "a-file"
    f.write_text("x")
    assert ownership.resolve(f, "L1") == ("L1", "orphan")  # nothing there: no record, no note


# ── adopt ───────────────────────────────────────────────────────────────────────────────────────
def lead(home, sid, inbox=None):
    (home / "leads").mkdir(parents=True, exist_ok=True)
    rec = {"sid": sid, "project": f"proj-{sid}"}
    if inbox is not None:
        rec["inbox"] = inbox
    (home / "leads" / f"{sid}.json").write_text(json.dumps(rec))
    return home / "leads" / (inbox or f"{sid}.inbox.md")


def session(home, sid=SID, lead_sid="old", packets=1, report=None):
    d = home / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "lead": lead_sid, "worktree": "/wt", "topic": "t", "model": "m", "status": "busy",
            "created": "-", "packets": packets, "label": f"[Exec] {sid}", "layout": "tab", "extra": {"k": [1, 2]}}
    (d / "meta.json").write_text(json.dumps(meta))
    if report is not None:
        (d / f"report-{packets:04d}.md").write_text(report)
    return d, meta


def appends(monkeypatch):
    calls = []
    real = ownership._append

    def spy(path, text):
        calls.append((Path(path), text))
        return real(path, text)

    monkeypatch.setattr(ownership, "_append", spy)
    return calls


def test_adopt_keeps_every_other_field_of_meta(tmp_path):
    home = tmp_path / "h"
    lead(home, "new")
    d, meta = session(home)
    assert ownership.adopt(home, SID, "new", "adopt") == "old"
    got = json.loads((d / "meta.json").read_text())
    assert got == {**meta, "lead": "new", "owner_project": "proj-new"}


def test_adopt_moves_only_this_sessions_lines_from_an_unregistered_owners_inbox(tmp_path, monkeypatch):
    home = tmp_path / "h"
    new_inbox = lead(home, "new")
    new_inbox.write_text("reported other-9 /r/other.md\n")
    d, _ = session(home)
    old_inbox = home / "leads" / "old.inbox.md"
    old_inbox.write_text(f"reported {SID} /r/report-0001.md\nreported other-2 /r/o.md\nreported {SID} /r/report-0002.md\n")
    calls = appends(monkeypatch)
    ownership.adopt(home, SID, "new", "claim")
    assert calls == [(new_inbox, f"reported {SID} /r/report-0001.md\nreported {SID} /r/report-0002.md\n")], "one write"
    assert new_inbox.read_text() == (f"reported other-9 /r/other.md\nreported {SID} /r/report-0001.md\n"
                                     f"reported {SID} /r/report-0002.md\n")
    assert old_inbox.read_text() == "reported other-2 /r/o.md\n"


def test_adopt_leaves_a_registered_owners_inbox_byte_identical(tmp_path):
    home = tmp_path / "h"
    lead(home, "new")
    lead(home, "old")
    session(home)
    old_inbox = home / "leads" / "old.inbox.md"
    old_inbox.write_bytes(f"reported {SID} /r/report-0001.md\nx\n".encode())
    before = old_inbox.read_bytes()
    ownership.adopt(home, SID, "new", "adopt", forced=True)
    assert old_inbox.read_bytes() == before
    assert not (home / "leads" / "new.inbox.md").exists()


def test_adopt_announces_a_report_not_announced_yet_once_with_an_ns_marker(tmp_path, monkeypatch):
    home = tmp_path / "h"
    new_inbox = lead(home, "new", inbox="custom.inbox")
    d, _ = session(home, packets=2, report="Done.\n")
    calls = appends(monkeypatch)
    ownership.adopt(home, SID, "new", "adopt")
    report = d / "report-0002.md"
    assert new_inbox == home / "leads" / "custom.inbox"
    assert new_inbox.read_text() == f"reported {SID} {os.path.abspath(report)}\n"
    assert len(calls) == 1
    assert (d / ".notified").read_text() == f"{os.path.abspath(report)} ns:{report.stat().st_mtime_ns}\n"
    ownership.adopt(home, SID, "new", "adopt")  # the marker names it now: nothing more
    assert new_inbox.read_text().count("reported") == 1


@pytest.mark.parametrize("marker", ["{r} 1727740000000.123", "{r} ns:5"])
def test_adopt_with_a_marker_naming_the_report_appends_nothing(tmp_path, marker):
    home = tmp_path / "h"
    new_inbox = lead(home, "new")
    d, _ = session(home, report="Done.\n")
    (d / ".notified").write_text(marker.format(r=d / "report-0001.md") + "\n")
    before = (d / ".notified").read_bytes()
    ownership.adopt(home, SID, "new", "adopt")
    assert not new_inbox.exists() and (d / ".notified").read_bytes() == before


def test_adopt_with_no_report_or_an_empty_one_appends_nothing(tmp_path):
    home = tmp_path / "h"
    new_inbox = lead(home, "new")
    d, _ = session(home)
    ownership.adopt(home, SID, "new", "adopt")
    (d / "report-0001.md").write_text("")
    ownership.adopt(home, SID, "new", "adopt")
    assert not new_inbox.exists() and not (d / ".notified").exists()


def test_adopt_does_not_announce_twice_a_report_whose_line_it_moved(tmp_path):
    home = tmp_path / "h"
    new_inbox = lead(home, "new")
    d, _ = session(home, report="Done.\n")
    report = d / "report-0001.md"
    (home / "leads" / "old.inbox.md").write_text(f"reported {SID} {report}\n")
    ownership.adopt(home, SID, "new", "claim")
    assert new_inbox.read_text() == f"reported {SID} {report}\n"
    assert not (d / ".notified").exists()


def test_adopt_writes_the_adopted_event(tmp_path):
    home = tmp_path / "h"
    lead(home, "new")
    session(home, lead_sid=None)
    for reason, forced in (("adopt", False), ("claim", False), ("handoff", False), ("takeover", True)):
        ownership.adopt(home, SID, "new", reason, forced=forced)
    evs = [{k: v for k, v in r.items() if k != "ts"} for r in ledger.read(home, event="adopted")]
    assert evs[0] == {"event": "adopted", "session_id": SID, "from_lead": None, "to_lead": "new", "forced": False,
                      "reason": "adopt"}
    assert [(e["from_lead"], e["reason"], e["forced"]) for e in evs[1:]] == [
        ("new", "claim", False), ("new", "handoff", False), ("new", "takeover", True)]
    with pytest.raises(PileadError):
        ownership.adopt(home, SID, "new", "because")


@pytest.mark.parametrize("raw", ["{not json", "[1]"])
def test_adopt_of_a_meta_that_cannot_be_read_raises_and_writes_nothing(tmp_path, raw):
    home = tmp_path / "h"
    lead(home, "new")
    d = home / "sessions" / SID
    d.mkdir(parents=True)
    (d / "meta.json").write_text(raw)
    before = tree(home)
    with pytest.raises(PileadError):
        ownership.adopt(home, SID, "new", "adopt")
    assert tree(home) == before
    with pytest.raises(PileadError):
        ownership.adopt(home, "no-such", "new", "adopt")
    assert tree(home) == before


def test_the_module_holds_resolve_owner_and_adopt():
    public = {n for n in vars(ownership) if not n.startswith("_") and callable(getattr(ownership, n))
              and getattr(getattr(ownership, n), "__module__", None) == ownership.__name__}
    assert public == {"resolve", "owner", "adopt"}
    assert state.valid_lead_sid("new")


def test_adopt_with_the_reason_fallback_sets_owner_project(tmp_path):
    home = tmp_path / "h"
    lead(home, "new")
    d, meta = session(home)
    assert ownership.adopt(home, SID, "new", "fallback") == "old"
    got = json.loads((d / "meta.json").read_text())
    assert (got["lead"], got["owner_project"]) == ("new", "proj-new")
    ev = [r for r in ledger.read(home) if r.get("event") == "adopted"]
    assert [(r["to_lead"], r["reason"]) for r in ev] == [("new", "fallback")]


def test_adopt_to_a_lead_with_no_project_leaves_owner_project(tmp_path):
    home = tmp_path / "h"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "bare.json").write_text(json.dumps({"sid": "bare"}))
    d, meta = session(home)
    meta["owner_project"] = "before"
    (d / "meta.json").write_text(json.dumps(meta))
    ownership.adopt(home, SID, "bare", "fallback")
    assert json.loads((d / "meta.json").read_text())["owner_project"] == "before"

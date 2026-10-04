"""lib/pilead/seen.py — who is asking (`caller`), has the lead looked (`looked_at`), and the record a look leaves
(`note`). In-process; every home is under the test's temp directory."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import ledger, seen  # noqa: E402

SID, LEAD = "s-1", "lead-1"
META = {"sid": SID, "lead": LEAD, "packets": 1}
LOOKS = [("usage_snapshot", {"at": "reported"}), ("report_verify", {}), ("report_reviewed", {}),
         ("report_looked_at", {})]


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def with_report(tmp_path, n=1):
    d = H(tmp_path) / "sessions" / SID
    d.mkdir(parents=True, exist_ok=True)
    (d / f"report-{n:04d}.md").write_text("Done; staged.\n")
    return H(tmp_path)


def strip(recs):
    return [{k: v for k, v in r.items() if k != "ts"} for r in recs]


# ── caller ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("env,word", [
    ({}, "unknown"),
    ({"PI_SESSION_ID": ""}, "unknown"),
    ({"PI_SESSION_ID": SID}, "self"),
    ({"PI_SESSION_ID": LEAD}, "lead"),
    ({"PI_SESSION_ID": "someone-else"}, "other"),
])
def test_caller_each_row(tmp_path, env, word):
    assert seen.caller(H(tmp_path), META, env) == word


def test_caller_meta_with_no_lead(tmp_path):
    meta = {"sid": SID}
    assert seen.caller(H(tmp_path), meta, {"PI_SESSION_ID": LEAD}) == "other"
    assert seen.caller(H(tmp_path), meta, {"PI_SESSION_ID": SID}) == "self"
    assert seen.caller(H(tmp_path), meta, {}) == "unknown"


@pytest.mark.parametrize("env", [["PI_SESSION_ID"], "PI_SESSION_ID=s-1", 7, object()])
def test_caller_env_that_is_not_a_mapping_is_unknown(tmp_path, env):
    assert seen.caller(H(tmp_path), META, env) == "unknown"


def test_caller_reads_os_environ_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_SESSION_ID", LEAD)
    assert seen.caller(H(tmp_path), META) == "lead"
    monkeypatch.delenv("PI_SESSION_ID")
    assert seen.caller(H(tmp_path), META) == "unknown"


def test_caller_meta_that_is_not_a_mapping_is_unknown(tmp_path):
    assert seen.caller(H(tmp_path), None, {"PI_SESSION_ID": LEAD}) == "unknown"


# ── looked_at ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("event,extra", LOOKS)
@pytest.mark.parametrize("who", ["lead", "unknown", None])
def test_looked_at_counts_each_event_by_lead_unknown_or_missing_caller(event, extra, who):
    rec = {"event": event, "session_id": SID, "packet": 1, **extra}
    if who is not None:
        rec["caller"] = who
    assert seen.looked_at([rec], SID, 1) is True


@pytest.mark.parametrize("event,extra", LOOKS)
@pytest.mark.parametrize("who", ["self", "other"])
def test_looked_at_never_counts_self_or_other(event, extra, who):
    rec = {"event": event, "session_id": SID, "packet": 1, "caller": who, **extra}
    assert seen.looked_at([rec], SID, 1) is False


def test_looked_at_ignores_another_packet_another_session_and_other_events():
    recs = [
        {"event": "report_verify", "session_id": SID, "packet": 2},
        {"event": "report_verify", "session_id": "s-other", "packet": 1},
        {"event": "usage_snapshot", "session_id": SID, "packet": 1, "at": "sent"},
        {"event": "surfaced_stamp_declined", "session_id": SID, "packet": 1, "caller": "self"},
        {"event": "wake_delivered", "session_id": LEAD, "executor": SID, "packet": 1},
    ]
    assert seen.looked_at(recs, SID, 1) is False
    assert seen.looked_at([], SID, 1) is False


# ── note ──────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("env,who", [({"PI_SESSION_ID": LEAD}, "lead"), ({}, "unknown")])
def test_note_lead_or_unknown_with_no_look_writes_report_looked_at(tmp_path, env, who):
    hm = with_report(tmp_path)
    seen.note(hm, SID, META, "check", env=env)
    assert strip(ledger.read(hm)) == [
        {"event": "report_looked_at", "session_id": SID, "packet": 1, "caller": who, "via": "check"}]
    seen.note(hm, SID, META, "diff", env=env)  # a second call writes nothing
    assert len(ledger.read(hm)) == 1


@pytest.mark.parametrize("env", [{"PI_SESSION_ID": LEAD}, {}])
def test_note_lead_or_unknown_with_a_counting_look_writes_nothing(tmp_path, env):
    hm = with_report(tmp_path)
    ledger.append(hm, "report_verify", session_id=SID, packet=1, verdict="x")  # no caller: counts
    before = ledger.path(hm).read_text()
    seen.note(hm, SID, META, "check", env=env)
    assert ledger.path(hm).read_text() == before


def test_note_lead_after_only_a_self_look_writes_report_looked_at(tmp_path):
    hm = with_report(tmp_path)
    ledger.append(hm, "usage_snapshot", session_id=SID, packet=1, at="reported", usage=None, caller="self")
    seen.note(hm, SID, META, "check", env={"PI_SESSION_ID": LEAD})
    assert [r["event"] for r in ledger.read(hm)] == ["usage_snapshot", "report_looked_at"]


def test_note_self_writes_surfaced_stamp_declined_once(tmp_path):
    hm = with_report(tmp_path)
    env = {"PI_SESSION_ID": SID}
    seen.note(hm, SID, META, "diff", env=env)
    assert strip(ledger.read(hm)) == [
        {"event": "surfaced_stamp_declined", "session_id": SID, "packet": 1, "owner_lead": LEAD, "caller": "self",
         "via": "diff"}]
    seen.note(hm, SID, META, "check", env=env)  # one already: nothing
    assert len(ledger.read(hm)) == 1
    assert seen.looked_at(ledger.read(hm), SID, 1) is False


def test_note_other_writes_nothing(tmp_path):
    hm = with_report(tmp_path)
    seen.note(hm, SID, META, "check", env={"PI_SESSION_ID": "someone-else"})
    assert not ledger.path(hm).exists()


@pytest.mark.parametrize("env", [{"PI_SESSION_ID": LEAD}, {}, {"PI_SESSION_ID": SID}])
def test_note_with_no_report_writes_nothing(tmp_path, env):
    hm = H(tmp_path)
    (hm / "sessions" / SID).mkdir(parents=True)
    seen.note(hm, SID, META, "check", env=env)
    with_report(tmp_path, n=2)  # a report of another packet is not the current one's
    seen.note(hm, SID, META, "check", env=env)
    assert not ledger.path(hm).exists()


def test_note_uses_the_records_it_is_given(tmp_path):
    hm = with_report(tmp_path)
    given = [{"event": "report_reviewed", "session_id": SID, "packet": 1, "caller": "lead"}]
    seen.note(hm, SID, META, "check", records=given, env={})
    assert not ledger.path(hm).exists()


@pytest.mark.parametrize("env", [{"PI_SESSION_ID": LEAD}, {"PI_SESSION_ID": SID}])
def test_note_with_an_unreadable_ledger_writes_nothing_and_never_raises(tmp_path, env):
    hm = with_report(tmp_path)
    ledger.path(hm).mkdir()  # a directory where the ledger file should be: cannot be read
    seen.note(hm, SID, META, "check", env=env)
    assert ledger.path(hm).is_dir() and list(ledger.path(hm).iterdir()) == []


def test_note_never_raises_on_a_bad_meta(tmp_path):
    hm = with_report(tmp_path)
    seen.note(hm, SID, {"sid": SID, "packets": "x"}, "check", env={})
    seen.note(hm, SID, None, "check", env={})
    seen.note(hm, SID, json, "check", env={})

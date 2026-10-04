"""`pilead escalate` and lib/pilead/escalation.py `decide`: every row of its table with the result dict compared
whole and the stored entry read back. In-process (`escalation.decide`) with the vendored iterm backend's
`running` / `run_osascript` replaced as tests/test_list.py replaces them — a lead's tab exists only when its
handle names LIVE_UUID — and through bin/pilead for the verb. Banners go to conftest's recording `osascript`
(`notify_via` is "osascript" wherever a banner is looked at, so no tty is ever asked); homes are under the
test's temp directory."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_notify import script

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import escalation, ledger, notify, ownership  # noqa: E402
from pilead.launch import backend  # noqa: E402

LIVE_UUID = "AAAAAAAA-0000-4000-8000-000000000001"
LIVE = f"w0t0p0:{LIVE_UUID}"
SID = "exec-a"


@pytest.fixture(autouse=True)
def terminal(monkeypatch, tmp_path):
    """iTerm "running"; a session exists only when its id is LIVE_UUID. No real osascript, ever."""
    it = backend.by_name("iterm")

    def run_osascript(script_, timeout=None):
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script_ else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    nowhere = tmp_path / "no-such-project-dir"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    h.mkdir()
    return h


@pytest.fixture
def banners(monkeypatch, tmp_path):
    """The kill switch off; returns a function giving the `osascript` scripts posted so far."""
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.delenv("PILEAD_NO_NOTIFY", raising=False)
    log = Path(os.environ["FAKE_OSA_LOG"])

    def posted():
        if not log.exists():
            return []
        return [c.strip("\n") for c in log.read_text().split("=====\n") if c.strip()]
    return posted


def iso(ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def config(home, **cfg):
    (home / "config.json").write_text(json.dumps({"notify_via": "osascript", **cfg}))


def lead(home, sid, project="proj", live=True, word="ok", started=None, inbox=None):
    """A lead record: `live` = its tab exists (else a ghost: started a day ago); `word` = its wake word."""
    d = home / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": sid, "project": project, "cwd": str(home.parent / f"cwd-{sid}"),
           "inbox": inbox or str(d / f"{sid}.inbox.md"), "iterm_handle": LIVE if live else None,
           "terminal": "iTerm.app" if live else None, "started": started or (iso(60) if live else iso(86400))}
    (d / f"{sid}.json").write_text(json.dumps(rec))
    if inbox is None:
        (d / f"{sid}.inbox.md").write_text("")
    health(home, sid, word)
    return rec


def health(home, sid, word):
    d = home / "wake" / sid
    p = d / "health.json"
    if word == "off":
        if p.exists():
            p.unlink()
        return
    d.mkdir(parents=True, exist_ok=True)
    if word == "unknown":
        p.write_text("not json")
        return
    h = {"sid": sid, "pid": os.getpid(), "started": iso(600), "beat": iso(0), "watching": True, "poll_seconds": 3,
         "delivered": 0, "last_delivered": None, "pending": 0, "last_error": None, "last_error_at": None,
         "ended": None}
    if word == "stale":
        h["beat"] = iso(600)
    if word == "stuck":
        h.update(last_error="boom", last_error_at=iso(5))
    p.write_text(json.dumps(h))


def session(home, sid=SID, lead_sid="lead-1", n=1, report="# Done the thing\n\nbody\n", **extra):
    d = home / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "worktree": "/wt", "packets": n, "status": "reported", **extra}
    if lead_sid is not None:
        meta["lead"] = lead_sid
    (d / "meta.json").write_text(json.dumps(meta))
    if report is not None:
        (d / f"report-{n:04d}.md").write_text(report)
    return d


def report(home, sid=SID, n=1):
    return str(home / "sessions" / sid / f"report-{n:04d}.md")


def line(home, sid=SID, n=1):
    return f"reported {sid} {report(home, sid, n)}\n"


def entry(home, sid=SID):
    p = home / "sessions" / sid / "escalation.json"
    return json.loads(p.read_text()) if p.exists() else None


def events(home, name=None):
    return [{k: v for k, v in r.items() if k != "ts"} for r in ledger.read(home, event=name)]


def tree(root):
    """Every path under root: its bytes and modification time for a file, None for a directory."""
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (p.read_bytes(), st.st_mtime_ns) if p.is_file() else None
    return out


def res(outcome, packet=1, owner=None, fallback=None, banner=None, stored=None, detail=""):
    return {"sid": SID, "packet": packet, "outcome": outcome, "owner": owner, "fallback": fallback,
            "banner": banner, "stored": stored, "detail": detail}


def decide(home, **kw):
    return escalation.decide(home, SID, **kw)


def strip_at(e):
    return {k: {f: v for f, v in x.items() if f != "at"} for k, x in (e or {}).items()}


# ── rows 1–3 ────────────────────────────────────────────────────────────────────────────────────────
def test_row1_off_writes_nothing(home):
    config(home, executor_escalation=False)
    lead(home, "lead-1", word="off")
    session(home)
    before = tree(home)
    assert decide(home) == res("off", packet=None, detail="executor_escalation is off")
    assert tree(home) == before


def test_row2_no_report(home):
    lead(home, "lead-1")
    session(home, report=None)
    before = tree(home)
    assert decide(home) == res("no-report", detail="packet 1 has no report")
    assert tree(home) == before


@pytest.mark.parametrize("status", ["resolved", "notified", "failed"])
def test_row3_a_second_call_after_a_final_status_does_nothing(home, status):
    lead(home, "lead-1")
    d = session(home)
    (d / "escalation.json").write_text(json.dumps({"1": {"status": status, "confirmed": False, "at": iso()}}))
    before = tree(home)
    assert decide(home) == res("handled", stored=status, detail=f"packet 1 was handled already ({status})")
    assert tree(home) == before


def test_row3_a_confirmed_push_is_handled(home):
    lead(home, "lead-1")
    d = session(home)
    (d / "escalation.json").write_text(json.dumps({"1": {"status": "sent", "confirmed": True, "at": iso()}}))
    before = tree(home)
    assert decide(home)["outcome"] == "handled"
    assert tree(home) == before


def test_row3_after_real_runs_of_each_final_status(home):
    """resolved, notified (no-lead) and failed reached by a real call: the next call writes nothing."""
    lead(home, "lead-1")
    session(home)
    ledger.append(home, "report_verify", session_id=SID, packet=1, verdict="COUNTS-MATCH")
    assert decide(home)["outcome"] == "resolved"
    before = tree(home)
    assert decide(home)["outcome"] == "handled"
    assert tree(home) == before
    session(home, sid="exec-b", lead_sid="gone-lead")
    assert escalation.decide(home, "exec-b")["outcome"] == "no-lead"
    before = tree(home)
    assert escalation.decide(home, "exec-b")["outcome"] == "handled"
    assert tree(home) == before


# ── row 4 ───────────────────────────────────────────────────────────────────────────────────────────
RESOLVING = [
    ("wake_delivered", {"session_id": "lead-1", "executor": SID, "packet": 1}),
    ("wake_skipped_landed", {"session_id": "lead-1", "executor": SID, "packet": 1, "reason": "landed"}),
    ("report_verify", {"session_id": SID, "packet": 1, "verdict": "COUNTS-MATCH"}),
    ("report_reviewed", {"session_id": SID, "packet": 1, "findings": "inline"}),
    ("usage_snapshot", {"session_id": SID, "packet": 1, "at": "reported", "usage": {}}),
]


@pytest.mark.parametrize("event,fields", RESOLVING, ids=[e for e, _f in RESOLVING])
def test_row4_each_event_alone_resolves(home, event, fields):
    lead(home, "lead-1")
    session(home)
    ledger.append(home, event, **fields)
    assert decide(home) == res("resolved", owner="lead-1", stored="resolved", detail="the lead has the report")
    assert strip_at(entry(home)) == {"1": {"status": "resolved", "confirmed": False}}
    assert events(home, "escalation_resolved") == [
        {"event": "escalation_resolved", "session_id": SID, "packet": 1, "owner_lead": "lead-1"}]
    assert (home / "leads" / "lead-1.inbox.md").read_text() == ""


@pytest.mark.parametrize("event,fields", RESOLVING, ids=[e for e, _f in RESOLVING])
def test_row4_with_a_sent_entry_it_is_confirmed_and_no_event(home, event, fields):
    lead(home, "lead-1")
    session(home)
    assert decide(home)["outcome"] == "send"
    assert strip_at(entry(home)) == {"1": {"status": "sent", "confirmed": False}}
    ledger.append(home, event, **fields)
    assert decide(home) == res("resolved", owner="lead-1", stored="sent",
                               detail="the lead has the report; the earlier push is confirmed")
    assert strip_at(entry(home)) == {"1": {"status": "sent", "confirmed": True}}
    assert events(home, "escalation_resolved") == []
    assert decide(home)["outcome"] == "handled"


@pytest.mark.parametrize("event,fields", RESOLVING, ids=[e for e, _f in RESOLVING])
def test_row4_an_event_for_another_packet_is_not_enough(home, event, fields):
    lead(home, "lead-1")
    session(home, n=2)
    ledger.append(home, event, **fields)  # packet 1
    assert decide(home)["outcome"] == "send"


# ── row 5 ───────────────────────────────────────────────────────────────────────────────────────────
def test_row5_an_unreadable_owner_record(home):
    lead(home, "lead-1")
    (home / "leads" / "lead-1.json").write_text("{nope")
    session(home)
    r = decide(home)
    assert r == res("owner-unknown", owner="lead-1", banner="A", stored="notified",
                    detail=f"its owner could not be read (lead record {home / 'leads' / 'lead-1.json'} cannot be read)")
    assert strip_at(entry(home)) == {"1": {"status": "notified", "confirmed": False}}
    assert events(home, "owner_fallback") == [] and events(home, "adopted") == []


def test_row5_an_unusable_lead_id_is_unknown_not_unowned(home):
    lead(home, "lead-2", project="proj")  # a live lead of a project: it is never taken for "no lead" here
    session(home, lead_sid="../x", owner_project="proj")
    assert decide(home)["outcome"] == "owner-unknown"


def test_row5_a_meta_that_is_not_json_is_owner_unknown(home):
    d = session(home)
    (d / "meta.json").write_text("{nope")
    r = decide(home)
    assert r["outcome"] == "owner-unknown" and r["banner"] == "A"


# ── rows 6 and 7 ────────────────────────────────────────────────────────────────────────────────────
def test_row6_an_orphan_is_handed_to_the_one_live_lead_of_its_project(home):
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead", owner_project="proj")
    r = decide(home)
    then = r.pop("then")
    assert r == res("fallback", owner="gone-lead", fallback="lead-2", banner="C", stored="sent",
                    detail="gone-lead cannot take it; lead-2 of project proj is its owner now")
    assert then == {"outcome": "send", "owner": "lead-2", "banner": "C", "stored": "sent",
                    "detail": "the report is in lead-2's inbox; its lead is listening (ok)"}
    meta = json.loads((home / "sessions" / SID / "meta.json").read_text())
    assert (meta["lead"], meta["owner_project"]) == ("lead-2", "proj")
    adopted = events(home, "adopted")
    assert [(a["from_lead"], a["to_lead"], a["reason"]) for a in adopted] == [("gone-lead", "lead-2", "fallback")]
    assert events(home, "owner_fallback") == [{"event": "owner_fallback", "session_id": SID, "packet": 1,
                                               "old_owner": "gone-lead", "new_owner": "lead-2", "project": "proj"}]
    assert (home / "leads" / "lead-2.inbox.md").read_text() == line(home)
    assert strip_at(entry(home)) == {"1": {"status": "sent", "confirmed": False}}


def test_row6_the_orphans_waiting_line_moves_and_is_not_doubled(home):
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead", owner_project="proj")
    (home / "leads" / "gone-lead.inbox.md").write_text(line(home))
    assert decide(home)["outcome"] == "fallback"
    assert (home / "leads" / "lead-2.inbox.md").read_text() == line(home)
    assert (home / "leads" / "gone-lead.inbox.md").read_text() == ""


def test_row6_a_ghost_owner_is_handed_over_and_its_line_written_once(home):
    lead(home, "lead-1", project="proj", live=False, word="off")
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="lead-1")
    (home / "leads" / "lead-1.inbox.md").write_text(line(home))  # the executor's line, in the ghost's inbox
    r = decide(home)
    assert (r["outcome"], r["owner"], r["fallback"], r["then"]["outcome"]) == ("fallback", "lead-1", "lead-2", "send")
    assert (home / "leads" / "lead-2.inbox.md").read_text() == line(home)


def test_row6_the_project_comes_from_the_owner_record_or_the_forward_note(home):
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead")  # no owner_project
    (home / "handoffs").mkdir()
    (home / "handoffs" / "gone-lead.json").write_text(json.dumps(
        {"from": "gone-lead", "to": "elsewhere", "project": "proj", "at": iso(), "reason": "takeover"}))
    assert decide(home)["fallback"] == "lead-2"


@pytest.mark.parametrize("case", ["two", "other-project", "ghost", "off", "no-project"])
def test_row7_no_single_fallback_lead_gives_no_lead(home, case):
    if case == "two":
        lead(home, "lead-2", project="proj")
        lead(home, "lead-3", project="proj")
    elif case == "other-project":
        lead(home, "lead-2", project="other")
    elif case == "ghost":
        lead(home, "lead-2", project="proj", live=False)
    elif case == "off":
        lead(home, "lead-2", project="proj", word="off")
    else:
        lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead", **({} if case == "no-project" else {"owner_project": "proj"}))
    r = decide(home)
    project = "(none)" if case == "no-project" else "proj"
    assert r == res("no-lead", owner="gone-lead", banner="B", stored="notified",
                    detail=f"its lead gone-lead has no record, and no single live lead of project {project} can take it")
    assert events(home, "adopted") == [] and events(home, "owner_fallback") == []
    assert json.loads((home / "sessions" / SID / "meta.json").read_text())["lead"] == "gone-lead"
    assert strip_at(entry(home)) == {"1": {"status": "notified", "confirmed": False}}


def test_row7_an_unowned_session(home):
    session(home, lead_sid=None)
    assert decide(home) == res("no-lead", banner="B", stored="notified",
                               detail="it names no lead, and no single live lead of project (none) can take it")


def test_the_owner_itself_is_never_its_own_fallback(home):
    lead(home, "lead-1", project="proj")
    rows = escalation._lead_rows(home, escalation.config.load(home))
    assert rows["lead-1"]["live"] == "live"
    assert escalation._fallback(home, rows, {"lead-1"}, "proj") is None
    assert escalation._fallback(home, rows, set(), "proj") == "lead-1"


def test_a_live_owner_whose_wake_is_stale_is_not_listening_and_not_adopted(home):
    lead(home, "lead-1", project="proj", word="stale")
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="lead-1")
    r = decide(home)
    assert r == res("not-listening", owner="lead-1", banner="D", stored="sent",
                    detail="the report waits in lead-1's inbox; its lead is not listening (stale)")
    assert events(home, "adopted") == []
    assert (home / "leads" / "lead-1.inbox.md").read_text() == line(home)
    assert (home / "leads" / "lead-2.inbox.md").read_text() == ""


# ── rows 8 and 9 ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("word,outcome", [("ok", "send"), ("stuck", "send"), ("stale", "not-listening"),
                                          ("off", "not-listening"), ("unknown", "not-listening")])
def test_rows_8_9_a_line_that_is_nowhere_is_appended_once(home, word, outcome):
    lead(home, "lead-1", word=word)
    session(home)
    r = decide(home)
    assert (r["outcome"], r["stored"], r["banner"]) == (outcome, "sent", "C" if outcome == "send" else "D")
    assert (home / "leads" / "lead-1.inbox.md").read_text() == line(home)
    # a later settle while it is unconfirmed: the line is waiting, so it is not written again
    assert decide(home)["outcome"] == outcome
    assert (home / "leads" / "lead-1.inbox.md").read_text() == line(home)


def test_rows_8_9_a_waiting_line_is_not_written_twice(home):
    lead(home, "lead-1")
    session(home)
    (home / "leads" / "lead-1.inbox.md").write_text("reported other x\n" + line(home))
    assert decide(home)["outcome"] == "send"
    assert (home / "leads" / "lead-1.inbox.md").read_text() == "reported other x\n" + line(home)


def test_rows_8_9_a_line_in_a_claim_file_is_not_written_again(home):
    lead(home, "lead-1", word="stale")
    session(home)
    claim = home / "leads" / "lead-1.inbox.md.claim-4242-1-1"
    claim.write_text(line(home))
    assert decide(home)["outcome"] == "not-listening"
    assert (home / "leads" / "lead-1.inbox.md").read_text() == ""
    assert claim.read_text() == line(home)


@pytest.mark.parametrize("word,letter", [("ok", "C"), ("off", "D")])
def test_rows_8_9_an_inbox_that_cannot_be_written(home, banners, word, letter):
    config(home)
    (home / "blocker").write_text("a file, not a directory")
    lead(home, "lead-1", word=word, inbox=str(home / "blocker" / "lead-1.inbox.md"))
    session(home)
    r = decide(home)
    assert (r["outcome"], r["stored"], r["banner"]) == ("send" if word == "ok" else "not-listening", "failed", letter)
    assert r["detail"].startswith("the report could not be put in lead-1's inbox")
    ev = events(home, "escalation_push_failed")
    assert len(ev) == 1 and ev[0]["owner_lead"] == "lead-1" and ev[0]["packet"] == 1 and ev[0]["session_id"] == SID
    assert ev[0]["error"] and "\n" not in ev[0]["error"] and len(ev[0]["error"]) <= 200
    assert strip_at(entry(home)) == {"1": {"status": "failed", "confirmed": False}}
    assert len(banners()) == 1
    assert decide(home)["outcome"] == "handled"


# ── banners ─────────────────────────────────────────────────────────────────────────────────────────
def test_banner_a(home, banners):
    config(home)
    session(home, lead_sid="../x")
    decide(home)
    assert banners() == [script("pi-lead — report ready", f"{SID} reported — its owner could not be read — pilead check {SID}")]
    assert (home / "wake" / "_no-lead" / "notified" / f"{SID}.no-lead-1").exists()


def test_banner_b(home, banners):
    config(home)
    session(home, lead_sid="gone-lead")
    decide(home)
    assert banners() == [script("pi-lead — report ready", f"{SID} reported — no lead is registered for it — pilead check {SID}")]
    assert (home / "wake" / "_no-lead" / "notified" / f"{SID}.no-lead-1").exists()


def test_banner_c_and_its_brief(home, banners):
    config(home)
    lead(home, "lead-1", project="proj")
    session(home)
    decide(home)
    assert banners() == [script("pi-lead · proj", f"{SID} reported — Done the thing")]
    assert (home / "wake" / "lead-1" / "notified" / f"{SID}.report-1").exists()


def test_banner_c_report_ready_when_the_report_has_no_brief(home, banners):
    config(home)
    lead(home, "lead-1", project="proj")
    session(home, report="\n\n")
    decide(home)
    assert banners() == [script("pi-lead · proj", f"{SID} reported — report ready")]


def test_banner_d(home, banners):
    config(home)
    lead(home, "lead-1", word="stale")
    session(home)
    decide(home)
    assert banners() == [script("pi-lead — report ready",
                                f"{SID} reported — its lead is not listening (stale) — the report waits in the inbox")]
    assert (home / "wake" / "lead-1" / "notified" / f"{SID}.no-lead-1").exists()


def test_banner_c_shares_the_key_of_the_leads_own_wake_banner(home, banners):
    config(home)
    lead(home, "lead-1")
    session(home)
    assert notify.claim(home, "lead-1", f"{SID}.report-1")  # the lead's own wake banner came first
    decide(home)
    assert banners() == []
    # and the other way round: after banner C the lead's own one is already announced
    session(home, sid="exec-b")
    escalation.decide(home, "exec-b")
    assert len(banners()) == 1
    r = subprocess.run([str(PILEAD), "notify", "lead-1", "--executor", "exec-b", "--packet", "1", "--home", str(home)],
                       capture_output=True, text=True)
    assert r.stdout == "banner not posted (already announced)\n"


@pytest.mark.parametrize("off", ["kill-switch", "notify_on_wake"])
def test_no_banner_and_no_claim_when_switched_off(home, banners, monkeypatch, off):
    if off == "kill-switch":
        config(home)
        monkeypatch.setenv("PILEAD_NO_NOTIFY", "1")
    else:
        config(home, notify_on_wake=False)
    lead(home, "lead-1")
    session(home)
    session(home, sid="exec-b", lead_sid="gone-lead")
    lead(home, "lead-3", word="stale")
    session(home, sid="exec-c", lead_sid="lead-3")
    for s in (SID, "exec-b", "exec-c"):
        escalation.decide(home, s)
    assert banners() == []
    assert not (home / "wake" / "lead-1" / "notified").exists()
    assert not (home / "wake" / "_no-lead").exists()
    assert not (home / "wake" / "lead-3" / "notified").exists()


# ── dry run ─────────────────────────────────────────────────────────────────────────────────────────
def _send(home):
    lead(home, "lead-1")
    session(home)


def _fallback(home):
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead", owner_project="proj")
    (home / "leads" / "gone-lead.inbox.md").write_text(line(home))


def _no_lead(home):
    session(home, lead_sid="gone-lead")


def _resolved(home):
    lead(home, "lead-1")
    session(home)
    ledger.append(home, "wake_delivered", session_id="lead-1", executor=SID, packet=1)


def _confirm(home):
    _resolved(home)
    (home / "sessions" / SID / "escalation.json").write_text(json.dumps({"1": {"status": "sent", "confirmed": False,
                                                                              "at": iso()}}))


def _failed(home):
    (home / "blocker").write_text("x")
    lead(home, "lead-1", inbox=str(home / "blocker" / "lead-1.inbox.md"))
    session(home)


def _unknown(home):
    session(home, lead_sid="../x")


@pytest.mark.parametrize("build", [_send, _fallback, _no_lead, _resolved, _confirm, _failed, _unknown])
def test_dry_run_writes_nothing_and_equals_the_real_run(home, banners, build):
    config(home)
    build(home)
    before = tree(home)
    dry = decide(home, dry_run=True)
    assert tree(home) == before
    assert banners() == []
    assert decide(home) == dry


# ── never raises ────────────────────────────────────────────────────────────────────────────────────
def test_a_home_that_does_not_exist(tmp_path):
    h = tmp_path / "nowhere"
    assert escalation.decide(h, SID) == res("no-report", packet=None, detail="no packet has a report")
    assert not h.exists()


def test_an_escalation_file_that_is_a_directory(home):
    lead(home, "lead-1")
    d = session(home)
    (d / "escalation.json").mkdir()
    r = decide(home)
    assert r["outcome"] == "send"
    assert (d / "escalation.json").is_dir()


def test_an_unusable_sid_in_process(home):
    r = escalation.decide(home, "../../x")
    assert r["outcome"] == "error" and r["sid"] == "../../x"


def test_owner_project_of_spawn_and_adopt_is_read_first(home):
    assert escalation.project_of(home, {"owner_project": "p", "lead": "x"}) == "p"
    lead(home, "lead-1", project="from-record")
    assert escalation.project_of(home, {"owner_project": "", "lead": "lead-1"}) == "from-record"
    assert escalation.project_of(home, {"lead": "nobody"}) is None
    assert escalation.project_of(home, "not a dict") is None


# ── the verb ────────────────────────────────────────────────────────────────────────────────────────
def run(*args, home=None, cwd=None):
    argv = [sys.executable, str(PILEAD), "escalate", *args] + (["--home", str(home)] if home else [])
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd)


def test_verb_one_line_per_outcome(home):
    lead(home, "lead-1", live=False, started=iso(60))  # unreachable, no tab asked: wake word ok
    session(home)
    r = run(SID, home=home)
    assert (r.returncode, r.stdout, r.stderr) == (
        0, "escalation: send — the report is in lead-1's inbox; its lead is listening (ok)\n", "")
    r = run(SID, home=home)
    assert r.stdout == "escalation: send — the report is in lead-1's inbox; its lead is listening (ok)\n"
    ledger.append(home, "wake_delivered", session_id="lead-1", executor=SID, packet=1)
    assert run(SID, home=home).stdout == "escalation: resolved — the lead has the report; the earlier push is confirmed\n"
    assert run(SID, home=home).stdout == "escalation: handled — packet 1 was handled already (sent)\n"
    assert run(SID, "--packet", "2", home=home).stdout == "escalation: no-report — packet 2 has no report\n"
    config(home, executor_escalation=False)
    assert run(SID, home=home).stdout == "escalation: off — executor_escalation is off\n"


def test_verb_an_unreachable_lead_is_no_fallback(home):
    lead(home, "lead-2", project="proj", live=False, started=iso(60))  # unreachable: not a fall-back lead
    session(home, lead_sid="gone-lead", owner_project="proj")
    r = run(SID, home=home)
    assert r.stdout == ("escalation: no-lead — its lead gone-lead has no record, and no single live lead of project "
                        "proj can take it\n")


def test_verb_fallback_second_line_in_process(home, capsys):
    from pilead import cli
    lead(home, "lead-2", project="proj")
    session(home, lead_sid="gone-lead", owner_project="proj")
    assert cli.main(["escalate", SID, "--home", str(home)]) == 0
    assert capsys.readouterr().out == (
        "escalation: fallback — gone-lead cannot take it; lead-2 of project proj is its owner now\n"
        "then: send — the report is in lead-2's inbox; its lead is listening (ok)\n")


def test_verb_json_and_dry_run(home):
    lead(home, "lead-1", live=False, started=iso(60))
    session(home)
    before = tree(home)
    r = run(SID, "--json", "--dry-run", home=home)
    out = r.stdout.splitlines()
    assert r.returncode == 0 and out[0] == "dry run — nothing was written" and len(out) == 2
    dry = json.loads(out[1])
    assert tree(home) == before
    r = run(SID, "--json", home=home)
    assert len(r.stdout.splitlines()) == 1 and json.loads(r.stdout) == dry
    assert dry == res("send", owner="lead-1", banner="C", stored="sent",
                      detail="the report is in lead-1's inbox; its lead is listening (ok)")
    r = run(SID, "--dry-run", home=home)
    assert r.stdout.splitlines()[0] == "dry run — nothing was written"


def test_verb_an_unusable_sid_reads_and_writes_nothing(tmp_path):
    h = tmp_path / "a" / "b" / "c"
    h.mkdir(parents=True)
    before = tree(tmp_path)
    r = run("../../x", home=h, cwd=h)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("pilead: session id '../../x' is not usable: ") and r.stderr.count("\n") == 1
    assert tree(tmp_path) == before


def test_verb_an_unknown_session(home):
    before = tree(home)
    r = run("exec-nope", home=home)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session exec-nope\n")
    assert tree(home) == before


# ── owner_project from adopt ────────────────────────────────────────────────────────────────────────
def test_adopt_with_a_project_less_new_owner_keeps_owner_project(home):
    (home / "leads").mkdir()
    (home / "leads" / "lead-9.json").write_text(json.dumps({"sid": "lead-9"}))
    session(home, owner_project="kept")
    ownership.adopt(home, SID, "lead-9", "fallback")
    assert json.loads((home / "sessions" / SID / "meta.json").read_text())["owner_project"] == "kept"

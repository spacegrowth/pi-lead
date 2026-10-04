"""lib/pilead/review.py `board_data`: the board read through the functions `pilead list` reads through
(verbs/list.py `read_leads` / `read_sessions`), with every reading that could not be made shown as unknown.
In-process, over homes built here by hand; the terminal backend's `run_osascript` and `running` are pinned
as tests/test_list.py pins them (no real osascript, no real tab, no real pi). Logs are synthetic."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_autoclose import repo, git, session as landed_session
from test_close_variants import tree
from test_list import H, LIVE_UUID, health, iso, lead, session
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, ledger, ownership, queue as queue_mod, refs, review, wakehealth  # noqa: E402
from pilead.launch import backend  # noqa: E402
from pilead.state import PileadError  # noqa: E402

LIVE = f"w0t0p0:{LIVE_UUID}"

LEAD_KEYS = ("session_id", "project", "label", "model", "color", "liveness", "wake", "wake_detail", "waiting",
             "auto", "tier", "last_active", "last_active_age", "ctx", "mb", "heavy", "heavy_reading")
EXEC_KEYS = ("session_id", "owner_lead", "status", "rendered_status", "topic", "model", "worktree", "pkt",
             "packets", "refs_moved", "refs_not_compared", "orphan",  # the keys the rows had before p24
             "owner_project", "scope", "effort", "tokens", "hit_rate", "ctx_cell", "ctx_contradiction", "mb",
             "usage", "heavy", "approaching", "heavy_reading", "approaching_reading", "keep", "queued",
             "auto_closed", "unannounced", "reported")


@pytest.fixture(autouse=True)
def terminal(monkeypatch, tmp_path):
    """iTerm "running"; a tab exists only when its id is LIVE_UUID. No real osascript, ever. No lead identity
    in the environment; the cwd is a directory named after no project."""
    it = backend.by_name("iterm")
    calls = []

    def run_osascript(script, timeout=None):
        calls.append(script)
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    for var in ("PI_LEAD_SID", "PI_SESSION_ID", "NO_COLOR"):
        monkeypatch.delenv(var, raising=False)
    nowhere = tmp_path / "no-such-project-dir"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    return calls


def hm(tmp_path):
    return H(tmp_path)


def meta_patch(tmp_path, sid, **fields):
    p = hm(tmp_path) / "sessions" / sid / "meta.json"
    m = json.loads(p.read_text())
    m.update(fields)
    p.write_text(json.dumps(m))


def rec_patch(tmp_path, sid, **fields):
    p = hm(tmp_path) / "leads" / f"{sid}.json"
    r = json.loads(p.read_text())
    r.update(fields)
    p.write_text(json.dumps(r))


def by_id(rows):
    return {r["session_id"]: r for r in rows}


def full_home(tmp_path):
    """Two leads (live, ghost with no health file) and one session of each kind the board shows, plus a
    landed session lead-1's sweep parks (the sweep's fixture writes leads lead-1 and lead-2)."""
    landed_session(tmp_path, "s-landed")
    lead(tmp_path, "lead-a", "pa", handle=LIVE, cwd=tmp_path / "lc")
    rec_patch(tmp_path, "lead-a", color=[10, 20, 30], label="[Lead] pa")
    health(tmp_path, "lead-a")
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 20_000, 0))])
    lead(tmp_path, "lead-g", "pg", started=iso(86400))
    session(tmp_path, "s-busy")
    write_log("s-busy", tmp_path / "wt-s-busy", [assistant(u(1000, 50, 30_000, 2000))])
    session(tmp_path, "s-rep", status="reported", report=True)
    session(tmp_path, "s-closed", status="closed")
    session(tmp_path, "s-super", status="closed")
    meta_patch(tmp_path, "s-super", superseded_by="s-busy")
    session(tmp_path, "s-pin")
    meta_patch(tmp_path, "s-pin", keep=True, scope="lib/", effort="high")
    session(tmp_path, "s-queue")
    queue_mod.enqueue(hm(tmp_path), "s-queue", "GOAL: one\n", "test")
    queue_mod.enqueue(hm(tmp_path), "s-queue", "GOAL: two\n", "test")


# ── the keys ────────────────────────────────────────────────────────────────────────────────────
def test_every_key_is_on_every_row(tmp_path, monkeypatch):
    full_home(tmp_path)
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")
    d = review.board_data(hm(tmp_path))
    assert set(d) >= {"generated", "version", "relay_bin", "home", "leads", "executors", "warnings", "ledger_tail"}
    assert d["relay_bin"] == "pilead" and d["home"] == str(hm(tmp_path))
    leads, execs = by_id(d["leads"]), by_id(d["executors"])
    assert set(leads) == {"lead-1", "lead-2", "lead-a", "lead-g"}
    assert set(execs) == {"s-landed", "s-busy", "s-rep", "s-closed", "s-super", "s-pin", "s-queue"}
    for r in d["leads"]:
        assert not r.get("broken") and all(k in r for k in LEAD_KEYS), (r["session_id"], set(LEAD_KEYS) - set(r))
    for r in d["executors"]:
        assert not r.get("broken") and all(k in r for k in EXEC_KEYS), (r["session_id"], set(EXEC_KEYS) - set(r))
    la, lg = leads["lead-a"], leads["lead-g"]
    assert (la["liveness"], la["wake"], la["color"], la["label"], la["project"]) == \
        ("live", "ok", [10, 20, 30], "[Lead] pa", "pa")
    assert la["ctx"] == "20k" and la["heavy"] is False and la["heavy_reading"] == "20k ctx, lead line 300k"
    assert la["last_active"].endswith("Z") and la["last_active_age"].endswith("ago")
    assert (lg["liveness"], lg["wake"], lg["color"], lg["ctx"], lg["mb"]) == ("ghost", "off", None, "-", "-")
    assert lg["heavy"] is None and lg["heavy_reading"] is None  # no reading and no size: not known
    assert lg["auto"] is False and lg["tier"] == "auto" and lg["waiting"] == 0
    b = execs["s-busy"]
    assert b["tokens"] != "-" and b["hit_rate"] is not None and b["ctx_cell"] != "-" and b["usage"]["live"]
    assert b["owner_lead"] == "lead-a" and b["owner_project"] == "pa" and b["unannounced"] is False
    assert execs["s-rep"]["reported"] is True and execs["s-rep"]["unannounced"] is True
    assert execs["s-closed"]["rendered_status"] == "closed" and execs["s-super"]["rendered_status"] == "superseded"
    assert execs["s-pin"]["keep"] is True and execs["s-pin"]["scope"] == "lib/" and execs["s-pin"]["effort"] == "high"
    assert execs["s-queue"]["queued"] == 2 and execs["s-busy"]["queued"] == 0
    assert execs["s-landed"]["status"] == "closed" and execs["s-landed"]["rendered_status"] == "closed (auto)"
    assert execs["s-landed"]["auto_closed"] == "landed"


# ── each unknown ──────────────────────────────────────────────────────────────────────────────────
def test_no_session_log(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    session(tmp_path, "s-1")
    from pilead import config
    from pilead.verbs import list as list_verb
    (r,) = review.board_data(hm(tmp_path))["executors"]
    (s,) = list_verb.read_sessions(hm(tmp_path), config.load(hm(tmp_path)))
    assert (r["tokens"], r["hit_rate"], r["ctx_cell"], r["mb"], r["usage"]) == ("-", None, "-", "-", None)
    assert r["heavy"] == s["heavy"] and r["heavy_reading"] is None


def test_a_queue_that_cannot_be_read(tmp_path):
    session(tmp_path, "s-1", lead_sid="")
    queue_mod.path(hm(tmp_path), "s-1").write_text("{not json")
    d = review.board_data(hm(tmp_path))
    assert d["executors"][0]["queued"] is None
    assert {"level": "warn", "text": "queue of s-1 cannot be read"} in d["warnings"]


def test_a_health_file_that_cannot_be_read(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    p = wakehealth.health_path(hm(tmp_path), "lead-a")
    p.mkdir(parents=True)  # a directory where the file should be: it cannot be read
    (r,) = review.board_data(hm(tmp_path))["leads"]
    assert r["wake"] == "unknown" and r["wake_detail"]


def test_a_posture_that_cannot_be_read(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    rec_patch(tmp_path, "lead-a", tier="sometimes", autonomous=True)
    (r,) = review.board_data(hm(tmp_path))["leads"]
    assert r["auto"] is None and r["tier"] is None


def test_an_owner_state_that_is_unknown(tmp_path):
    session(tmp_path, "s-1", status="reported", report=True, lead_sid="lead-a")
    lead(tmp_path, "lead-a", None, raw="{not json")  # the owner's record exists and cannot be read
    assert ownership.owner(hm(tmp_path), {"lead": "lead-a"})["state"] == "unknown"
    (r,) = review.board_data(hm(tmp_path))["executors"]
    assert r["unannounced"] is None and r["orphan"] is None


# ── unannounced ───────────────────────────────────────────────────────────────────────────────────
def test_unannounced_follows_the_wake_delivered_event(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    session(tmp_path, "s-1", status="reported", report=True)
    h = hm(tmp_path)
    assert review.board_data(h)["executors"][0]["unannounced"] is True
    ledger.append(h, "wake_delivered", lead="lead-a", executor="s-1", packet=2)  # another packet
    assert review.board_data(h)["executors"][0]["unannounced"] is True
    ledger.append(h, "wake_delivered", lead="lead-a", executor="s-other", packet=1)  # another executor
    assert review.board_data(h)["executors"][0]["unannounced"] is True
    ledger.append(h, "wake_delivered", lead="lead-a", executor="s-1", packet=1)
    assert review.board_data(h)["executors"][0]["unannounced"] is False


def test_unannounced_is_false_for_a_session_not_reported_or_terminal(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    session(tmp_path, "s-busy")
    session(tmp_path, "s-closed", status="closed", report=True)
    rows = by_id(review.board_data(hm(tmp_path))["executors"])
    assert rows["s-busy"]["unannounced"] is False and rows["s-closed"]["unannounced"] is False


def test_an_unreadable_ledger_makes_unannounced_unknown_and_warns(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    session(tmp_path, "s-1", status="reported", report=True)
    ledger.path(hm(tmp_path)).mkdir(parents=True)  # a directory: it cannot be read
    d = review.board_data(hm(tmp_path))
    assert d["executors"][0]["unannounced"] is None and d["ledger_tail"] == []
    assert [w["level"] for w in d["warnings"] if "ledger" in w["text"]] == ["warn"]


def test_the_ledger_tail_is_the_last_40_events_cut(tmp_path):
    h = hm(tmp_path)
    h.mkdir(parents=True)
    for i in range(45):
        ledger.append(h, "probe", n=i, blob="x" * 300)
    tail = review.board_data(h)["ledger_tail"]
    assert len(tail) == 40 and '"n": 5,' in tail[0] and '"n": 44,' in tail[-1]
    assert all(" probe {" in t and len(t.split(" probe ", 1)[1]) == 160 for t in tail)


# ── the same answers as `pilead list --json` ────────────────────────────────────────────────────────
def test_the_rows_agree_with_list_json(tmp_path, capsys):
    full_home(tmp_path)
    session(tmp_path, "s-heavy")
    write_log("s-heavy", tmp_path / "wt-s-heavy", [assistant(u(10, 5, 160_000, 0))])
    session(tmp_path, "s-warm")
    write_log("s-warm", tmp_path / "wt-s-warm", [assistant(u(10, 5, 125_000, 0))])
    d = review.board_data(hm(tmp_path))
    assert cli.main(["list", "--json"]) in (0, None)
    lst = json.loads(capsys.readouterr().out)
    bl, be = by_id(d["leads"]), by_id(d["executors"])
    for r in lst["leads"]:
        b = bl[r["sid"]]
        assert b["liveness"] == r["live"]
        assert b["heavy"] == r["heavy"] or (b["heavy"] is None and r["usage"] is None)
    for r in lst["executors"]:
        b = be[r["sid"]]
        assert (b["status"], b["reported"], b["heavy"], b["approaching"]) == \
            (r["status"], r["reported"], r["heavy"], r["approaching"] and not r["heavy"]), r["sid"]
    assert be["s-heavy"]["heavy"] is True and be["s-warm"]["approaching"] is True
    assert be["s-heavy"]["heavy_reading"] == "160k ctx" and be["s-warm"]["approaching_reading"] == "125k ctx"


# ── broken rows ───────────────────────────────────────────────────────────────────────────────────
def test_unreadable_records_are_broken_rows_and_the_rest_is_whole(tmp_path):
    full_home(tmp_path)
    lead(tmp_path, "lead-x", None, raw="{not json")
    session(tmp_path, "s-bad", raw="{not json")
    d = review.board_data(hm(tmp_path))
    leads, execs = by_id(d["leads"]), by_id(d["executors"])
    assert leads["lead-x"] == {"session_id": "lead-x", "broken": True, "liveness": "broken"}
    assert execs["s-bad"] == {"session_id": "s-bad", "broken": True}
    assert all(all(k in r for k in LEAD_KEYS) for s, r in leads.items() if s != "lead-x")
    assert all(all(k in r for k in EXEC_KEYS) for s, r in execs.items() if s != "s-bad")


def test_a_row_whose_reading_raises_is_broken(tmp_path, monkeypatch):
    full_home(tmp_path)
    real_state, real_owner = wakehealth.state, ownership.owner

    def state_(home, sid, now=None):
        if sid == "lead-g":
            raise RuntimeError("boom")
        return real_state(home, sid, now)

    def owner_(home, meta):
        if meta.get("sid") == "s-pin":
            raise RuntimeError("boom")
        return real_owner(home, meta)

    monkeypatch.setattr(wakehealth, "state", state_)
    monkeypatch.setattr(ownership, "owner", owner_)
    d = review.board_data(hm(tmp_path))
    leads, execs = by_id(d["leads"]), by_id(d["executors"])
    assert leads["lead-g"]["broken"] is True and execs["s-pin"]["broken"] is True
    assert all(k in leads["lead-a"] for k in LEAD_KEYS) and all(k in execs["s-busy"] for k in EXEC_KEYS)
    assert any(w["text"].startswith("2 record(s) cannot be read: lead-g, s-pin") for w in d["warnings"])


def test_read_leads_raising_breaks_every_lead_row_and_not_the_board(tmp_path, monkeypatch):
    from pilead.verbs import list as list_verb
    lead(tmp_path, "lead-a", "pa")
    session(tmp_path, "s-1")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(list_verb, "read_leads", boom)
    d = review.board_data(hm(tmp_path))
    assert d["leads"] == [{"session_id": "lead-a", "broken": True, "liveness": "broken"}]
    assert d["executors"][0]["session_id"] == "s-1" and not d["executors"][0].get("broken")


# ── write=False writes nothing ────────────────────────────────────────────────────────────────────
def test_write_false_leaves_home_byte_identical(tmp_path, monkeypatch):
    full_home(tmp_path)
    write_log("s-rep", tmp_path / "wt-s-rep", [assistant(u(10, 5, 1000, 0))])
    session(tmp_path, "s-done", status="busy", report=True)  # stored busy, report there: would become reported
    write_log("s-done", tmp_path / "wt-s-done", [assistant(u(10, 5, 1000, 0))])
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")  # a caller: still no sweep
    before = tree(hm(tmp_path))
    time.sleep(0.01)
    d = review.board_data(hm(tmp_path), write=False)
    assert tree(hm(tmp_path)) == before
    execs = by_id(d["executors"])
    assert execs["s-done"]["status"] == "reported"  # worked out, not stored
    assert execs["s-landed"]["status"] == "reported"  # not parked
    assert not any("auto-closed" in w["text"] for w in d["warnings"])
    review.board_data(hm(tmp_path))  # the writing run does write: the test can tell
    assert tree(hm(tmp_path)) != before


# ── the sweep ─────────────────────────────────────────────────────────────────────────────────────
def test_the_sweep_runs_only_with_a_caller_sweep_and_write(tmp_path, monkeypatch):
    landed_session(tmp_path, "s-landed")
    h = hm(tmp_path)
    for kw in ({}, {"sweep": False}, {"write": False}):
        if kw:
            monkeypatch.setenv("PI_LEAD_SID", "lead-1")
        else:
            monkeypatch.delenv("PI_LEAD_SID", raising=False)
        d = review.board_data(h, **kw)
        assert by_id(d["executors"])["s-landed"]["status"] == "reported", kw
        assert not any("auto-" in w["text"] for w in d["warnings"])
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")
    parked = []
    d = review.board_data(h, parked=parked)
    row = by_id(d["executors"])["s-landed"]
    assert row["rendered_status"] == "closed (auto)" and row["auto_closed"] == "landed"  # swept before the rows
    assert parked == [("s-landed", "close", "landed")]
    assert d["warnings"][-1] == {"level": "info", "text": "auto-closed s-landed (landed) just now"}
    assert [e["trigger"] for e in ledger.read(h) if e["event"] == "auto_closed"] == ["board"]


def test_an_unregistered_caller_sweeps_nothing(tmp_path, monkeypatch):
    landed_session(tmp_path, "s-landed")
    monkeypatch.setenv("PI_LEAD_SID", "not-a-lead")
    d = review.board_data(hm(tmp_path))
    assert by_id(d["executors"])["s-landed"]["status"] == "reported"


# ── lead= ─────────────────────────────────────────────────────────────────────────────────────────
def test_an_unusable_lead_id_is_refused_and_nothing_changes(tmp_path):
    full_home(tmp_path)
    before = tree(tmp_path)
    with pytest.raises(PileadError):
        review.board_data(hm(tmp_path), lead="../../x")
    assert tree(tmp_path) == before


def test_lead_keeps_that_leads_executors_and_the_unowned_ones(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    lead(tmp_path, "lead-b", "pb")
    session(tmp_path, "s-a", lead_sid="lead-a")
    session(tmp_path, "s-b", lead_sid="lead-b")
    session(tmp_path, "s-none", lead_sid="")
    d = review.board_data(hm(tmp_path), lead="lead-a")
    assert [e["session_id"] for e in d["executors"]] == ["s-a", "s-none"]
    assert {r["session_id"] for r in d["leads"]} == {"lead-a", "lead-b"}  # the LEADS rows are not filtered


def test_lead_follows_a_forward_note(tmp_path):
    lead(tmp_path, "lead-new", "pa")
    session(tmp_path, "s-a", lead_sid="lead-old")
    (hm(tmp_path) / "handoffs").mkdir()
    (hm(tmp_path) / "handoffs" / "lead-old.json").write_text(json.dumps(
        {"from": "lead-old", "to": "lead-new", "project": "pa", "at": iso(0), "reason": "handoff"}))
    (r,) = review.board_data(hm(tmp_path), lead="lead-new")["executors"]
    assert r["owner_lead"] == "lead-new" and r["owner_project"] == "pa"


# ── version ───────────────────────────────────────────────────────────────────────────────────────
def test_version_is_package_json(tmp_path):
    want = json.loads((ROOT / "package.json").read_text())["version"]
    assert review.board_data(hm(tmp_path))["version"] == want


def test_version_is_null_when_package_json_cannot_be_read(tmp_path):
    copy = tmp_path / "tree"
    shutil.copytree(ROOT / "lib", copy / "lib")
    shutil.copytree(ROOT / "vendor", copy / "vendor")
    (copy / "package.json").write_text("{")
    home = tmp_path / "h"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PI_LEAD_SID", "PI_SESSION_ID")}
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from pilead import review; "
            "print(json.dumps(review.board_data(sys.argv[2])['version']))")
    r = subprocess.run([sys.executable, "-c", code, str(copy / "lib"), str(home)], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "null"


# ── the warnings ──────────────────────────────────────────────────────────────────────────────────
def base(tmp_path):
    """A home with a live lead and a busy session: no warning at all."""
    lead(tmp_path, "lead-a", "pa", handle=LIVE, cwd=tmp_path / "lc")
    health(tmp_path, "lead-a")
    session(tmp_path, "s-ok")


def _moved(tmp_path):
    wt = repo(tmp_path, "rm")
    session(tmp_path, "s-moved")
    meta_patch(tmp_path, "s-moved", worktree=str(wt))
    refs.take_and_store(hm(tmp_path), "s-moved", 1, str(wt))
    (wt / "lib" / "x.py").write_text("x = 2\n")
    git(wt, "commit", "-qam", "moved")


def _not_compared(tmp_path):
    wt = repo(tmp_path, "rn")
    session(tmp_path, "s-nc")
    meta_patch(tmp_path, "s-nc", worktree=str(wt))  # a repository, no baseline stored


def _orphan(tmp_path):
    session(tmp_path, "s-orphan", lead_sid="lead-gone")


def _unannounced(tmp_path):
    session(tmp_path, "s-rep", status="reported", report=True)


def _heavy(tmp_path):
    session(tmp_path, "s-heavy")
    write_log("s-heavy", tmp_path / "wt-s-heavy", [assistant(u(10, 5, 160_000, 0))])


def _approaching(tmp_path):
    session(tmp_path, "s-warm")
    write_log("s-warm", tmp_path / "wt-s-warm", [assistant(u(10, 5, 125_000, 0))])


def _heavy_lead(tmp_path):
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 320_000, 0))])


def _stuck(tmp_path):
    lead(tmp_path, "lead-s", "ps", handle=LIVE)
    health(tmp_path, "lead-s", last_error="send broke", last_error_at=iso(5))


def _stale(tmp_path):
    lead(tmp_path, "lead-t", "pt", handle=LIVE)
    health(tmp_path, "lead-t", beat=iso(120))


def _unknown(tmp_path):
    lead(tmp_path, "lead-u", "pu", handle=LIVE)
    wakehealth.health_path(hm(tmp_path), "lead-u").mkdir(parents=True)


def _queue(tmp_path):
    session(tmp_path, "s-q")
    queue_mod.path(hm(tmp_path), "s-q").write_text("[]")


def _broken(tmp_path):
    session(tmp_path, "s-bad", raw="{nope")


def _swept(tmp_path, monkeypatch):
    landed_session(tmp_path, "s-landed", wt=repo(tmp_path, "rl"))
    refs.take_and_store(hm(tmp_path), "s-landed", 1, str(tmp_path / "repos" / "rl"))
    monkeypatch.setenv("PI_LEAD_SID", "lead-1")


BUILDERS = [(_moved, "bad", "REFS MOVED in s-moved since packet 0001: "),
            (_not_compared, "warn", "refs of s-nc NOT COMPARED for packet 0001 "),
            (_orphan, "bad", "⚠ 1 executor(s) owned by a lead that is no longer registered: s-orphan — "),
            (_unannounced, "warn", "1 report(s) not delivered to their lead yet: s-rep — pilead check <sid>"),
            (_heavy, "info", "heavy: s-heavy — pilead send <sid> <packet> --rotate retires the session and starts "
                             "a seeded one"),
            (_approaching, "info", "approaching heavy: s-warm — plan a fresh session for the next packet"),
            (_heavy_lead, "info", "heavy lead: pa — pilead handoff <note.md> starts a fresh lead session"),
            (_stuck, "bad", "wake STUCK: ps (send broke) — see pilead list"),
            (_stale, "bad", "wake STALE: pt (last beat "),
            (_unknown, "warn", "wake not known for: pu (cannot be read: "),
            (_queue, "warn", "queue of s-q cannot be read"),
            (_broken, "bad", "1 record(s) cannot be read: s-bad — see pilead list"),
            (_swept, "info", "auto-closed s-landed (landed) just now")]


def _build(fn, tmp_path, monkeypatch):
    fn(tmp_path, monkeypatch) if fn is _swept else fn(tmp_path)


def test_a_clean_home_has_no_warning(tmp_path):
    base(tmp_path)
    assert review.board_data(hm(tmp_path))["warnings"] == []


@pytest.mark.parametrize("fn,level,text", BUILDERS, ids=[b[0].__name__ for b in BUILDERS])
def test_each_warning_appears_when_it_applies_and_alone(tmp_path, monkeypatch, fn, level, text):
    base(tmp_path)
    _build(fn, tmp_path, monkeypatch)
    ws = review.board_data(hm(tmp_path))["warnings"]
    assert len(ws) == 1, ws
    assert ws[0]["level"] == level and ws[0]["text"].startswith(text), ws


def test_every_warning_at_once_in_the_tables_order(tmp_path, monkeypatch):
    base(tmp_path)
    for fn, _level, _text in BUILDERS:
        _build(fn, tmp_path, monkeypatch)
    ws = review.board_data(hm(tmp_path))["warnings"]
    got = [w["text"] for w in ws]
    assert len(got) == len(BUILDERS), got
    for (fn, level, text), w in zip(BUILDERS, ws):
        assert w["text"].startswith(text) and w["level"] == level, (fn.__name__, w)


def test_several_names_are_listed_in_one_warning(tmp_path):
    base(tmp_path)
    _stale(tmp_path)
    lead(tmp_path, "lead-t2", "pt2", handle=LIVE)
    health(tmp_path, "lead-t2", beat=iso(200))
    (w,) = review.board_data(hm(tmp_path))["warnings"]
    assert w["text"].startswith("wake STALE: pt, pt2 (pt: last beat ") and "; pt2: last beat " in w["text"]


# ── `pilead board` prints the park lines after the path ────────────────────────────────────────────
def _board(caller=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PI_LEAD_SID", "PI_SESSION_ID")}
    if caller:
        env["PI_LEAD_SID"] = caller
    return subprocess.run([str(ROOT / "bin" / "pilead"), "board"], capture_output=True, text=True, env=env, timeout=120)


def test_pilead_board_by_a_caller_prints_the_park_line_after_the_path(tmp_path, monkeypatch):
    landed_session(tmp_path, "s-landed")
    monkeypatch.setenv("PI_LEAD_HOME", str(hm(tmp_path)))
    r = _board()
    page = hm(tmp_path) / "board.html"
    assert r.returncode == 0 and r.stdout == f"{page}\n{page.resolve().as_uri()}\n"  # the path, then its address
    assert (hm(tmp_path) / "sessions" / "s-landed" / "status").read_text().strip() == "reported"
    r = _board(caller="lead-1")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [str(hm(tmp_path) / "board.html"), page.resolve().as_uri(),
                                     "auto-closed s-landed (landed) — pilead send s-landed <packet> reopens it "
                                     "with its conversation"]
    assert "closed (auto)" in (hm(tmp_path) / "board.html").read_text()


def test_lead_leaves_out_the_refs_warnings_of_the_executors_it_leaves_out(tmp_path):
    lead(tmp_path, "lead-a", "pa")
    lead(tmp_path, "lead-b", "pb")
    for sid, owner in (("s-a", "lead-a"), ("s-b", "lead-b")):
        session(tmp_path, sid, lead_sid=owner)
        meta_patch(tmp_path, sid, worktree=str(repo(tmp_path, sid)))  # a repository, no baseline: not compared
    texts = [w["text"] for w in review.board_data(hm(tmp_path), lead="lead-a")["warnings"]]
    assert len(texts) == 1 and texts[0].startswith("refs of s-a NOT COMPARED")
    assert len(review.board_data(hm(tmp_path))["warnings"]) == 2

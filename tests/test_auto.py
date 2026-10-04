"""`pilead auto on|off|status` and the posture reset in `pilead lead-start`, driven through bin/pilead
(no terminal is asked: conftest's recording osascript stub is first on PATH)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_cli_core import PILEAD, home  # noqa: F401  (test_cli_core's osascript stub fixture is not needed)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, posture  # noqa: E402

STOP_LIST = ("a report with a risk flag, failing tests, or an UNVERIFIED claim that bears on correctness; core logic, "
             "ledgers, parity or golden tests, migrations, deploys; an action that cannot be undone or that leaves "
             "this machine (a push, a delete, a publish, a message to someone); work that is not in the approved "
             "plan; real ambiguity the packet and the plan do not settle")


def on_text(name):
    return (f"autonomous mode ON for lead '{name}' — proceed by default WITHIN the approved plan; announce every "
            f"autonomous action together with what you would have asked.\n"
            f"  Still stops, under either posture: {STOP_LIST}.\n"
            "  Committing an executor's work has its own gate: run pilead verify <sid> --for-autocommit --in-plan "
            "--diff-reviewed --findings <path> and pass the two attestation flags only when they are true. "
            "AUTO-COMMIT: CLEARED permits the commit; anything else means stop and ask, naming the condition. "
            "pilead auto off reverts; pilead lead-start resets to the config value.\n")


def off_text(name):
    return f"autonomous mode OFF for lead '{name}' — back to announce-and-wait at every approval.\n"


def status_on(name, origin):
    return (f"autonomous mode is ON for lead '{name}' ({origin}) — proceeding by default on routine in-plan steps; "
            f"the stop-list still stops, and committing an executor's work needs every condition of pilead verify "
            f"<sid> --for-autocommit.\n")


def status_off(name, origin):
    return (f"autonomous mode is OFF for lead '{name}' ({origin}) — waiting for you at every approval. pilead auto "
            f"on turns it on for this session.\n")


AUTO_ON_CONFIG_LINE = ("AUTONOMOUS MODE ON (config autonomous_mode: true) — this lead proceeds by default on routine "
                       "in-plan steps instead of waiting. pilead auto off reverts it for this session.")
BY_COMMAND = "set by pilead auto in this session"


def run(*args, env=None, drop=()):
    e = {**os.environ, **(env or {})}
    for k in drop:
        e.pop(k, None)
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)


def ok(*args, **kw):
    r = run(*args, **kw)
    assert r.returncode == 0, r.stderr
    return r


def H(tmp_path):
    return home(tmp_path)


def rec_path(tmp_path, sid="lead-1"):
    return H(tmp_path) / "leads" / f"{sid}.json"


def rec(tmp_path, sid="lead-1"):
    return json.loads(rec_path(tmp_path, sid).read_text())


def events(tmp_path, *names):
    return [r for r in ledger.read(H(tmp_path)) if r["event"] in names]


def fields(r):
    return {k: v for k, v in r.items() if k not in ("ts", "event")}


POSTURE_EVENTS = ("auto_mode_on", "auto_mode_off")


def set_config(tmp_path, **kw):
    H(tmp_path).mkdir(parents=True, exist_ok=True)
    (H(tmp_path) / "config.json").write_text(json.dumps(kw))


def snapshot(root):
    """Every path under root with its bytes (None for a directory) and its modification time."""
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


# ── lead-start ───────────────────────────────────────────────────────────────────────────────────
def test_lead_start_with_the_default_config_is_off_one_line_no_posture_event(tmp_path):
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert r.stdout.splitlines() == [f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}"]
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"]) == (False, "config")
    assert events(tmp_path, *POSTURE_EVENTS) == []
    (ls,) = events(tmp_path, "lead_started")
    assert fields(ls) == {"session_id": "lead-1", "project": "proj"}


def test_lead_start_with_autonomous_mode_true_turns_it_on(tmp_path):
    set_config(tmp_path, autonomous_mode=True)
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert r.stdout.splitlines() == [f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}",
                                     AUTO_ON_CONFIG_LINE]
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"]) == (True, "config")
    assert [(e["event"], fields(e)) for e in events(tmp_path, "lead_started", *POSTURE_EVENTS)] == [
        ("lead_started", {"session_id": "lead-1", "project": "proj"}),
        ("auto_mode_on", {"session_id": "lead-1", "project": "proj", "was": False, "source": "config"}),
    ]
    # a second run while on: on again, `was` true
    ok("lead-start", "lead-1", "--project", "proj")
    assert fields(events(tmp_path, *POSTURE_EVENTS)[-1]) == {"session_id": "lead-1", "project": "proj", "was": True,
                                                             "source": "config"}


@pytest.mark.parametrize("value", ["yes", 1, None, "true"])
def test_an_autonomous_mode_that_is_not_a_boolean_is_off(tmp_path, value):
    set_config(tmp_path, autonomous_mode=value)
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert len(r.stdout.splitlines()) == 1
    assert rec(tmp_path)["autonomous"] is False and events(tmp_path, *POSTURE_EVENTS) == []


def test_lead_start_resets_a_command_set_posture_and_keeps_started(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    started = rec(tmp_path)["started"]
    ok("auto", "on", "--session", "lead-1")
    assert (rec(tmp_path)["autonomous"], rec(tmp_path)["autonomous_source"]) == (True, "command")
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert len(r.stdout.splitlines()) == 1
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"], got["started"]) == (False, "config", started)
    assert fields(events(tmp_path, *POSTURE_EVENTS)[-1]) == {"session_id": "lead-1", "project": "proj", "was": True,
                                                             "source": "arm"}
    assert events(tmp_path, *POSTURE_EVENTS)[-1]["event"] == "auto_mode_off"
    # off before and off after: no posture event
    n = len(events(tmp_path, *POSTURE_EVENTS))
    ok("lead-start", "lead-1", "--project", "proj")
    assert len(events(tmp_path, *POSTURE_EVENTS)) == n


# ── on / off ─────────────────────────────────────────────────────────────────────────────────────
def test_auto_on_and_off(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    before = rec(tmp_path)
    others = {k: v for k, v in before.items() if k not in ("autonomous", "autonomous_source")}

    r = ok("auto", "on", "--session", "lead-1")
    assert (r.stdout, r.stderr) == (on_text("proj"), "")
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"]) == (True, "command")
    assert {k: v for k, v in got.items() if k not in ("autonomous", "autonomous_source")} == others
    (e,) = events(tmp_path, *POSTURE_EVENTS)
    assert (e["event"], fields(e)) == ("auto_mode_on", {"session_id": "lead-1", "project": "proj", "was": False,
                                                        "source": "command"})

    r = ok("auto", "on", "--session", "lead-1")  # unchanged, still ledgered, with was true
    assert r.stdout == on_text("proj")
    assert fields(events(tmp_path, *POSTURE_EVENTS)[-1])["was"] is True

    r = ok("auto", "off", "--session", "lead-1")
    assert (r.stdout, r.stderr) == (off_text("proj"), "")
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"]) == (False, "command")
    assert {k: v for k, v in got.items() if k not in ("autonomous", "autonomous_source")} == others
    e = events(tmp_path, *POSTURE_EVENTS)[-1]
    assert (e["event"], fields(e)) == ("auto_mode_off", {"session_id": "lead-1", "project": "proj", "was": True,
                                                         "source": "command"})
    ok("auto", "off", "--session", "lead-1")
    assert fields(events(tmp_path, *POSTURE_EVENTS)[-1])["was"] is False
    assert len(events(tmp_path, *POSTURE_EVENTS)) == 4


def test_a_lead_with_no_project_is_named_by_its_sid(tmp_path):
    p = rec_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"sid": "lead-1", "project": None}))
    assert ok("auto", "on", "--session", "lead-1").stdout == on_text("lead-1")
    assert ok("auto", "status", "--session", "lead-1").stdout == status_on("lead-1", BY_COMMAND)
    assert ok("auto", "off", "--session", "lead-1").stdout == off_text("lead-1")


# ── status ───────────────────────────────────────────────────────────────────────────────────────
def _status_unchanged(tmp_path, *args, **kw):
    before = snapshot(H(tmp_path))
    r = ok("auto", "status", *args, **kw)
    assert snapshot(H(tmp_path)) == before
    return r.stdout


@pytest.mark.parametrize("config_on", [False, True])
def test_status_for_both_origins_changes_nothing(tmp_path, config_on):
    if config_on:
        set_config(tmp_path, autonomous_mode=True)
    ok("lead-start", "lead-1", "--project", "proj")
    origin = f"from config autonomous_mode: {'true' if config_on else 'false'}"
    want = status_on("proj", origin) if config_on else status_off("proj", origin)
    assert _status_unchanged(tmp_path, "--session", "lead-1") == want
    ok("auto", "on", "--session", "lead-1")
    assert _status_unchanged(tmp_path, "--session", "lead-1") == status_on("proj", BY_COMMAND)
    ok("auto", "off", "--session", "lead-1")
    assert _status_unchanged(tmp_path, "--session", "lead-1") == status_off("proj", BY_COMMAND)


def test_status_of_a_non_boolean_posture_is_off_from_config(tmp_path):
    p = rec_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"sid": "lead-1", "project": "proj", "autonomous": "yes"}))
    assert _status_unchanged(tmp_path, "--session", "lead-1") == status_off("proj", "from config autonomous_mode: false")


# ── which lead ───────────────────────────────────────────────────────────────────────────────────
def test_the_sid_comes_from_session_then_pi_lead_sid_then_pi_session_id(tmp_path):
    for sid, project in (("lead-a", "pa"), ("lead-b", "pb"), ("lead-c", "pc")):
        ok("lead-start", sid, "--project", project)
    both = {"PI_LEAD_SID": "lead-b", "PI_SESSION_ID": "lead-c"}
    assert "'pa'" in ok("auto", "status", "--session", "lead-a", env=both).stdout
    assert "'pb'" in ok("auto", "status", env=both).stdout
    assert "'pc'" in ok("auto", "status", env={"PI_SESSION_ID": "lead-c"}).stdout
    ok("auto", "on", env=both)
    assert (rec(tmp_path, "lead-a")["autonomous"], rec(tmp_path, "lead-b")["autonomous"],
            rec(tmp_path, "lead-c")["autonomous"]) == (False, True, False)
    ok("auto", "on", env={"PI_SESSION_ID": "lead-c"})
    assert rec(tmp_path, "lead-c")["autonomous"] is True


@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_with_no_sid_it_exits_2(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("auto", action, drop=("PI_LEAD_SID", "PI_SESSION_ID"))
    assert r.returncode == 2 and r.stdout == ""
    assert "auto: no lead session id — pass --session, or run it inside the lead's own pi session" in r.stderr
    r = run("auto", action, env={"PI_LEAD_SID": "", "PI_SESSION_ID": ""})
    assert r.returncode == 2 and "no lead session id" in r.stderr
    assert snapshot(H(tmp_path)) == before


@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_an_unusable_sid_is_refused_and_nothing_is_written(tmp_path, action):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    h = deep / "home"
    (h / "leads").mkdir(parents=True)
    # a record where ../../x would land, relative to leads/: nothing may read or write it
    (deep / "x.json").write_text(json.dumps({"sid": "x", "project": "p"}))
    before = snapshot(tmp_path)
    r = run("auto", action, "--session", "../../x", "--home", str(h))
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    r = run("auto", action, "--home", str(h), env={"PI_LEAD_SID": "../../x"})
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_an_unregistered_sid_exits_2(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("auto", action, "--session", "lead-2")
    assert r.returncode == 2 and r.stdout == ""
    assert "auto: lead-2 is not a registered lead — run pilead lead-start first" in r.stderr
    assert snapshot(H(tmp_path)) == before


@pytest.mark.parametrize("action", ["on", "off", "status"])
@pytest.mark.parametrize("text", ["{not json", "[]"])
def test_an_unreadable_record_exits_1_naming_the_file(tmp_path, action, text):
    p = rec_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(text)
    before = snapshot(H(tmp_path))
    r = run("auto", action, "--session", "lead-1")
    assert r.returncode == 1 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and str(p) in r.stderr
    assert snapshot(H(tmp_path)) == before


# ── an executor ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("action", ["on", "off"])
def test_an_executor_cannot_change_a_leads_posture(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("auto", action, "--session", "lead-1", env={"PI_LEAD_ROLE": "executor"})
    assert r.returncode == 2 and r.stdout == ""
    assert "auto: an executor cannot change a lead's posture" in r.stderr
    r = run("auto", action, env={"PI_LEAD_ROLE": "executor", "PI_LEAD_SID": "lead-1"})
    assert r.returncode == 2
    assert snapshot(H(tmp_path)) == before


def test_an_executor_may_read_the_status(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = ok("auto", "status", "--session", "lead-1", env={"PI_LEAD_ROLE": "executor"})
    assert r.stdout == status_off("proj", "from config autonomous_mode: false")
    assert snapshot(H(tmp_path)) == before


def test_posture_module_agrees_with_the_verb(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    ok("auto", "on", "--session", "lead-1")
    assert posture.autonomous_state(rec(tmp_path)) == (True, "command")

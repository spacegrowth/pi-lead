"""The route contract (lib/pilead/route.py) against tests/route-cases.json, the table the extension's test suite
reads too, and `pilead route retain <reason> [--session SID]` (lib/pilead/verbs/route.py) through bin/pilead."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

from pilead import route  # noqa: E402
from pilead.state import PileadError  # noqa: E402

CASES = json.loads((ROOT / "tests" / "route-cases.json").read_text())
NOW = 1767225600.123  # 2026-01-01T00:00:00.123Z, the moment tests/ts/pi-lead.test.ts injects too
L1 = "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d"
L2 = "4c1d0e2b-5a49-4382-8170-6f5e4d3c2b1a"


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob("*"))}


def deep_home(tmp_path):
    home = tmp_path / "a" / "b" / "c" / "home"
    (home / "leads").mkdir(parents=True)
    return home


def register(home, sid, project="proj"):
    p = home / "leads" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sid": sid, "project": project}))


def ledger(home):
    p = home / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── the contract, case by case ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_retain_against_the_shared_table(tmp_path, case):
    home = deep_home(tmp_path)
    if case["registered"]:
        register(home, case["sid"])  # for an unusable id: a record where that id would point
    before = tree(tmp_path)
    grace = home / "leads" / f"{case['sid']}.route"
    if case["written"]:
        assert route.retain(home, case["sid"], case["reason"], now=NOW) == 120
        assert grace.read_text() == case["file_text"]
        assert abs(grace.stat().st_mtime - NOW) < 0.001
    else:
        with pytest.raises(PileadError) as ei:
            route.retain(home, case["sid"], case["reason"], now=NOW)
        assert ei.value.code == 2
        assert tree(tmp_path) == before, "a refusal wrote something"
        assert case["file_text"] is None
    events = [r for r in ledger(home) if r.get("event") == "retained"]
    if case["event"]:
        (rec,) = events
        assert {k: v for k, v in rec.items() if k not in ("ts", "event")} == \
            {"session_id": case["sid"], "reason": case["file_text"].split(" ", 1)[1].rstrip("\n")}
    else:
        assert events == []


def test_the_refusal_lines(tmp_path):
    home = deep_home(tmp_path)
    with pytest.raises(PileadError) as ei:
        route.retain(home, L1, "  \n ", now=NOW)
    assert str(ei.value) == 'route: give a reason — pilead route retain "<why this edit is lead work>"'
    with pytest.raises(PileadError) as ei:
        route.retain(home, L1, "why", now=NOW)
    assert str(ei.value) == (f"route: '{L1}' is not a registered lead — the gate is off; run pilead lead-start "
                             "to register")
    with pytest.raises(PileadError) as ei:
        route.retain(home, "../../x", "why", now=NOW)
    assert str(ei.value).startswith("lead id '../../x' is not usable: ")


def test_control_characters_become_spaces(tmp_path):
    home = deep_home(tmp_path)
    register(home, L1)
    route.retain(home, L1, " first\nsecond\tthird\r\x07end ", now=NOW)
    assert (home / "leads" / f"{L1}.route").read_text() == "2026-01-01T00:00:00.123Z first second third  end\n"


def test_the_stamp_is_javascripts_to_iso_string():
    assert route.stamp(NOW) == "2026-01-01T00:00:00.123Z"
    assert route.stamp(0) == "1970-01-01T00:00:00.000Z"
    assert route.stamp(1767225600.9996) == "2026-01-01T00:00:01.000Z"


# ── the verb ──────────────────────────────────────────────────────────────────────────────────────────

def run(home, *args, env=None, unset=()):
    e = {k: v for k, v in os.environ.items() if k not in unset}
    e.update(env or {})
    return subprocess.run([str(PILEAD), "route", *args, "--home", str(home)], capture_output=True, text=True, env=e)


@pytest.fixture
def home(tmp_path):
    h = deep_home(tmp_path)
    register(h, L1, "proj")
    register(h, L2, "other")
    return h


def opened(home, sid):
    return (home / "leads" / f"{sid}.route").is_file()


def test_the_verb_with_session(home):
    r = run(home, "retain", "schema bump", "--session", L1)
    assert (r.returncode, r.stdout, r.stderr) == (0, "retain window open for 120s — reason: schema bump\n", "")
    assert opened(home, L1) and not opened(home, L2)
    (rec,) = [x for x in ledger(home) if x["event"] == "retained"]
    assert (rec["session_id"], rec["reason"]) == (L1, "schema bump")


def test_the_verb_with_grace_seconds_90(home):
    (home / "config.json").write_text(json.dumps({"grace_seconds": 90}))
    r = run(home, "retain", "x", "--session", L1)
    assert (r.returncode, r.stdout) == (0, "retain window open for 90s — reason: x\n")


def test_the_caller_from_pi_lead_sid_then_pi_session_id(home):
    r = run(home, "retain", "by env", env={"PI_LEAD_SID": L2, "PI_SESSION_ID": L1})
    assert r.returncode == 0 and opened(home, L2) and not opened(home, L1)
    r = run(home, "retain", "by pi", env={"PI_SESSION_ID": L1}, unset=("PI_LEAD_SID",))
    assert r.returncode == 0 and opened(home, L1)


def test_no_session_at_all(home):
    before = tree(home)
    r = run(home, "retain", "x", unset=("PI_LEAD_SID", "PI_SESSION_ID"))
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == "pilead: route: no session given, and neither $PI_LEAD_SID nor $PI_SESSION_ID is set\n"
    assert tree(home) == before


@pytest.mark.parametrize("token", ["other", L2[:8]])
def test_a_project_or_a_prefix_as_session(home, token):
    r = run(home, "retain", "named", "--session", token)
    assert (r.returncode, r.stdout) == (0, "retain window open for 120s — reason: named\n")
    assert opened(home, L2) and not opened(home, L1)
    assert not (home / "leads" / f"{token}.route").exists()


@pytest.mark.parametrize("role", ["executor", "reviewer"])
def test_an_executor_or_reviewer_is_refused(home, role):
    before = tree(home)
    for args in (["retain", "x", "--session", L1], ["retain", "x"]):
        r = run(home, *args, env={"PI_LEAD_ROLE": role, "PI_LEAD_SID": L1})
        assert (r.returncode, r.stdout) == (2, "")
        assert r.stderr == "pilead: route: an executor cannot open a lead's gate\n"
    assert tree(home) == before


def test_a_session_that_is_not_registered(home):
    before = tree(home)
    r = run(home, "retain", "x", "--session", "nobody-registered")
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == ("pilead: route: 'nobody-registered' is not a registered lead — the gate is off; run "
                        "pilead lead-start to register\n")
    assert tree(home) == before


def test_an_empty_reason(home):
    before = tree(home)
    r = run(home, "retain", "   ", "--session", L1)
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == 'pilead: route: give a reason — pilead route retain "<why this edit is lead work>"\n'
    assert tree(home) == before


def test_another_word_is_an_invalid_choice(home):
    before = tree(home)
    r = run(home, "open", "x", "--session", L1)
    assert r.returncode == 2 and "invalid choice" in r.stderr and r.stdout == ""
    assert tree(home) == before


def test_an_id_that_climbs_out_of_home(tmp_path):
    home = deep_home(tmp_path)
    (home.parent / "x.json").write_text(json.dumps({"sid": "x", "project": "planted"}))  # leads/../../x.json
    before = tree(tmp_path)
    r = run(home, "retain", "x", "--session", "../../x")
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr.startswith("pilead: lead id '../../x' is not usable: ")
    assert len(r.stderr.splitlines()) == 1
    assert tree(tmp_path) == before

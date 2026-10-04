"""`pilead whoami [token] [--json]` (lib/pilead/verbs/whoami.py), driven through bin/pilead: who a session
is, read from state. Home is byte-identical after every run."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
L1 = "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d"
L2 = "9e8d7c6b-5a49-4382-8170-6f5e4d3c2b1a"
E1 = "alpha-20261004-101010"
E2 = "beta-20261004-111111"


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob("*"))}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "a" / "b" / "home"
    (h / "leads").mkdir(parents=True)
    (h / "sessions").mkdir()
    return h


def lead(home, sid, **rec):
    (home / "leads" / f"{sid}.json").write_text(json.dumps({"sid": sid, **rec}))


def executor(home, sid, status=None, **meta):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"sid": sid, **meta}))
    if status:
        (d / "status").write_text(status + "\n")
    return d


def whoami(home, *args, env=None, unset=()):
    root = home.parents[2]
    before = tree(root)
    e = {k: v for k, v in os.environ.items() if k not in unset}
    e.update(env or {})
    r = subprocess.run([str(PILEAD), "whoami", *args, "--home", str(home)], capture_output=True, text=True, env=e)
    assert tree(root) == before, "whoami wrote something"
    return r


def ok(r):
    assert r.returncode == 0 and r.stderr == "", r.stderr
    return r.stdout


FULL_LEAD = {"project": "proj", "terminal": "iTerm.app", "label": "[Lead] proj",
             "iterm_handle": "w0t0p0:AAAA", "started": "2026-10-01T00:00:00Z"}


# ── a lead ────────────────────────────────────────────────────────────────────────────────────────────

def test_a_lead_with_no_executor(home):
    lead(home, L1, **FULL_LEAD)
    assert ok(whoami(home, L1)) == (f"name       : proj\n"
                                    f"session    : {L1}\n"
                                    "role       : lead\n"
                                    "backend    : iTerm.app             tab '[Lead] proj'\n"
                                    "execs      : -\n")
    assert json.loads(ok(whoami(home, L1, "--json"))) == {
        "name": "proj", "session": L1, "role": "lead", "backend": "iTerm.app", "tab_label": "[Lead] proj",
        "iterm_session": "w0t0p0:AAAA", "execs": []}


def test_a_lead_with_two_executors(home):
    lead(home, L1, **FULL_LEAD)
    lead(home, L2, project="other")
    executor(home, E2, status="busy", lead=L1)
    executor(home, E1, lead=L1, status="reported")       # no status file: the record's own
    executor(home, "gamma-20261004-121212", status="busy", lead=L2)
    assert ok(whoami(home, L1)).splitlines()[-1] == f"execs      : {E1} (reported), {E2} (busy)"
    data = json.loads(ok(whoami(home, L1, "--json")))
    assert data["execs"] == [{"name": E1, "status": "reported"}, {"name": E2, "status": "busy"}]


def test_a_lead_with_a_bare_record(home):
    lead(home, L2)
    assert ok(whoami(home, L2)) == (f"name       : {L2}\n"
                                    f"session    : {L2}\n"
                                    "role       : lead\n"
                                    "backend    : -             tab '-'\n"
                                    "execs      : -\n")
    out = whoami(home, L2, "--json").stdout
    assert out == json.dumps({"name": L2, "session": L2, "role": "lead", "backend": None, "tab_label": None,
                              "iterm_session": None, "execs": []}, indent=2) + "\n"


# ── an executor ───────────────────────────────────────────────────────────────────────────────────────

def test_an_executor_with_an_owner(home):
    lead(home, L1, **FULL_LEAD)
    d = executor(home, E1, status="reported", lead=L1, packets=2)
    (d / "report-0002.md").write_text("done\n")
    assert ok(whoami(home, E1)) == (f"name       : {E1}\n"
                                    f"session    : {E1}\n"
                                    "role       : executor\n"
                                    "status     : reported          (packet 0002)\n"
                                    f"lead       : {L1}  proj  [iTerm.app]  tab '[Lead] proj'\n")
    assert json.loads(ok(whoami(home, E1, "--json"))) == {
        "name": E1, "session": E1, "role": "executor", "status": "reported", "owner_lead": L1,
        "current_packet": 2, "report_path": str(d / "report-0002.md"), "report_exists": True,
        "lead_backend": "iTerm.app", "lead_tab_label": "[Lead] proj", "lead_iterm_session": "w0t0p0:AAAA"}


def test_an_executor_with_no_owner(home):
    d = executor(home, E1, status="busy", packets=1)
    assert ok(whoami(home, E1)) == (f"name       : {E1}\n"
                                    f"session    : {E1}\n"
                                    "role       : executor\n"
                                    "status     : busy          (packet 0001)\n"
                                    "lead       : -  (unowned)\n")
    assert json.loads(ok(whoami(home, E1, "--json"))) == {
        "name": E1, "session": E1, "role": "executor", "status": "busy", "owner_lead": None,
        "current_packet": 1, "report_path": str(d / "report-0001.md"), "report_exists": False,
        "lead_backend": None, "lead_tab_label": None, "lead_iterm_session": None}


def test_an_executor_whose_owners_record_is_gone(home):
    executor(home, E1, status="busy", lead=L2, packets=3)
    assert ok(whoami(home, E1)).splitlines()[-1] == f"lead       : {L2}  -  [?]  tab '-'"
    data = json.loads(ok(whoami(home, E1, "--json")))
    assert (data["owner_lead"], data["lead_backend"], data["lead_tab_label"], data["lead_iterm_session"]) == \
        (L2, None, None, None)


def test_an_executor_whose_owner_id_is_not_usable(home):
    (home / "x.json").write_text(json.dumps({"terminal": "planted"}))
    executor(home, E1, status="busy", lead="../x", packets=1)
    data = json.loads(ok(whoami(home, E1, "--json")))
    assert (data["owner_lead"], data["lead_backend"]) == ("../x", None)


def test_an_executor_whose_packets_is_a_text(home):
    executor(home, E1, status="busy", lead=L1, packets="2")
    lead(home, L1, **FULL_LEAD)
    assert ok(whoami(home, E1)).splitlines()[3] == "status     : busy          (packet -)"
    data = json.loads(ok(whoami(home, E1, "--json")))
    assert (data["current_packet"], data["report_path"], data["report_exists"]) == (None, None, None)


# ── where the token comes from ────────────────────────────────────────────────────────────────────────

def test_the_token_from_pi_lead_sid_then_pi_session_id(home):
    lead(home, L1, **FULL_LEAD)
    executor(home, E1, status="busy", lead=L1, packets=1)
    assert json.loads(ok(whoami(home, "--json", env={"PI_LEAD_SID": L1, "PI_SESSION_ID": E1})))["session"] == L1
    assert json.loads(ok(whoami(home, "--json", env={"PI_SESSION_ID": E1})))["session"] == E1
    assert json.loads(ok(whoami(home, E1, "--json", env={"PI_LEAD_SID": L1})))["session"] == E1


def test_no_token_at_all(home):
    r = whoami(home, unset=("PI_LEAD_SID", "PI_SESSION_ID"))
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == "pilead: whoami: no token given, and neither $PI_LEAD_SID nor $PI_SESSION_ID is set\n"


def test_an_id_from_the_environment_is_not_resolved(home):
    lead(home, L1, **FULL_LEAD)
    r = whoami(home, env={"PI_LEAD_SID": "proj"})
    assert r.returncode == 1
    assert r.stderr == "pilead: whoami: could not resolve 'proj' to a known executor or lead\n"


def test_a_project_and_a_prefix_as_the_token(home):
    lead(home, L1, **FULL_LEAD)
    executor(home, E1, status="busy", lead=L1, packets=1)
    assert ok(whoami(home, "proj")) == ok(whoami(home, L1))
    assert ok(whoami(home, L1[:8], "--json")) == ok(whoami(home, L1, "--json"))
    assert ok(whoami(home, E1[:8])) == ok(whoami(home, E1))


def test_an_unknown_token(home):
    r = whoami(home, "nobody-here")
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr == "pilead: whoami: could not resolve 'nobody-here' to a known executor or lead\n"


def test_an_id_that_is_not_usable(home):
    # a readable record where the id would point, one level above leads/ and sessions/
    (home / "x.json").write_text(json.dumps({"project": "planted"}))
    (home / "x").mkdir()
    (home / "x" / "meta.json").write_text(json.dumps({"sid": "x"}))
    for token in ("../x", "../../x", str(home / "x")):
        r = whoami(home, token)
        assert (r.returncode, r.stdout) == (1, "")
        assert r.stderr == f"pilead: whoami: could not resolve '{token}' to a known executor or lead\n"


def test_a_record_that_cannot_be_read(home):
    (home / "leads" / f"{L1}.json").write_text("{broken")
    r = whoami(home, L1)
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr == f"pilead: whoami: the record of '{L1}' cannot be read\n"
    executor(home, E1)
    (home / "sessions" / E1 / "meta.json").write_text("[1, 2]")
    r = whoami(home, E1, "--json")
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr == f"pilead: whoami: the record of '{E1}' cannot be read\n"

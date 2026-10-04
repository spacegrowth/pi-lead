"""pilead review <sid> --why changes nothing about the session it looks at: check.json is built read-only
(no `pilead check`, so no auto-close sweep, no status stored, no report marked seen, no refs move logged).
tests/fake_pi is `pi` (conftest); the terminal is the osascript stub test_cli_core brings, so no real tab."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cli_core import PILEAD, home  # noqa: F401  (also brings the osascript stub fixture)
from test_review_verb import LEAD, make_lead, models_json, tree_bytes
from test_verify_diff_board import SID, git, make_session, write_report

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import autoclose, ledger as ledger_mod  # noqa: E402

NO_CHANGE = """Ran the ops check; nothing to change.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: none — no files changed
"""
ANSWER = "It reported and went idle.\n"


@pytest.fixture
def parkable(tmp_path, monkeypatch):
    """A session auto-close would park: reported (a clean no-change report, 10 minutes old), its report seen
    (a report_verify event), its worktree clean, its lead the calling lead. Its refs also moved since the
    packet's baseline (a commit after it), which `check` would log as refs_moved."""
    wt, sdir = make_session(tmp_path)
    (wt / "later.txt").write_text("later\n")
    git(wt, "add", "later.txt")
    git(wt, "commit", "-q", "-m", "later")
    write_report(sdir, NO_CHANGE)
    t = time.time() - 600
    os.utime(sdir / "report-0001.md", (t, t))
    ledger_mod.append(home(tmp_path), "report_verify", session_id=SID, packet=1, verdict="COUNTS-MATCH",
                      rerun=False, mismatches=0)
    models_json(tmp_path)
    make_lead(tmp_path)
    monkeypatch.setenv("PI_LEAD_SID", LEAD)
    ans = tmp_path / "answer.txt"
    ans.write_text(ANSWER)
    monkeypatch.setenv("FAKE_PI_STDOUT", str(ans))
    return {"wt": wt, "sdir": sdir}


def run(*args):
    return subprocess.run([sys.executable, str(PILEAD), "review", *args], capture_output=True, text=True)


def without_why(tree):
    """The home's bytes, less what --why writes by design: sessions/<sid>/why-* and its one ledger line."""
    out = {k: v for k, v in tree.items() if not k.startswith(f"sessions/{SID}/why-")}
    led = out.get(ledger_mod.NAME)
    if isinstance(led, bytes):
        out[ledger_mod.NAME] = b"".join(ln for ln in led.splitlines(keepends=True)
                                        if json.loads(ln).get("event") != "session_diagnosed")
    return out


def test_the_fixture_is_one_auto_close_would_park(tmp_path, parkable):
    assert autoclose.sweep(home(tmp_path), "manual", lead=LEAD, dry_run=True) == [(SID, "close", "landed")]


def test_why_leaves_a_parkable_session_byte_identical(tmp_path, parkable):
    h = home(tmp_path)
    before = tree_bytes(h)
    wt_before = tree_bytes(parkable["wt"])
    r = run(SID, "--why")
    assert r.returncode == 0, r.stderr
    assert "auto-closed" not in r.stdout + r.stderr
    after = tree_bytes(h)
    # what --why adds, and nothing else: its work directory, its saved answer, its one ledger event
    (stamp,) = {k.split("/")[2] for k in after if k.startswith(f"sessions/{SID}/why-") and k.endswith(".md")
                and k.count("/") == 2}
    assert {k.split("/")[2] for k in after if k.startswith(f"sessions/{SID}/why-")} == {stamp, stamp[:-3]}
    assert ledger_mod.read(h)[-1]["event"] == "session_diagnosed"
    assert without_why(after) == before
    assert tree_bytes(parkable["wt"]) == wt_before
    # the session is still what it was: reported, not parked, no refs move logged, nothing newly "seen"
    assert (parkable["sdir"] / "status").read_text() == "reported\n"
    assert "auto_closed" not in json.loads((parkable["sdir"] / "meta.json").read_text())
    events = {r_["event"] for r_ in ledger_mod.read(h)}
    assert not events & {"auto_closed", "closed", "refs_moved", "usage_snapshot", "auto_close_error"}
    # and a sweep afterwards would still park it: --why did not do it
    assert autoclose.sweep(h, "manual", lead=LEAD, dry_run=True) == [(SID, "close", "landed")]


def test_check_json_is_what_check_would_say(tmp_path, parkable, monkeypatch):
    """check.json holds the object `pilead check <sid> --json` prints for the session — read without a
    sweep. `check` is run here only afterwards, with no calling lead, to compare."""
    assert run(SID, "--why").returncode == 0
    (work,) = [p for p in parkable["sdir"].glob("why-*") if p.is_dir()]
    got = json.loads((work / "check.json").read_text())
    assert isinstance(got, list) and len(got) == 1
    assert got[0]["sid"] == SID and got[0]["status"] == "reported" and got[0]["packet"] == 1
    assert got[0]["reported"] is True and got[0]["refs"]["state"] == "moved"
    monkeypatch.delenv("PI_LEAD_SID")
    c = subprocess.run([sys.executable, str(PILEAD), "check", SID, "--json"], capture_output=True, text=True)
    assert c.returncode == 0, c.stderr
    assert got == json.loads(c.stdout)

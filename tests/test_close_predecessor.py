"""`pilead close-predecessor` driven through bin/pilead with a recording `osascript` stub and a `ps` stub first on
PATH: the stub records every call to the terminal and answers from the test's environment. A child process this
file starts stands for the `pi` on the outgoing lead's tab; no other process is ever signalled. No real tab is
looked up or closed."""
import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
LEAD, PRED = "lead-new", "lead-old"
PRED_UUID = "BBBBBBBB-0000-4000-8000-0000000000BB"
OWN_UUID = "CCCCCCCC-0000-4000-8000-0000000000CC"
TTY = "/dev/ttys042"

# exists → $FAKE_EXISTS (after a close that took, "false"); tty → $FAKE_TTY; close → $FAKE_CLOSE ("true" makes it
# take). Every script is logged.
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *"tell s to close"*)
    if [ "$FAKE_CLOSE" = true ]; then : > "$FAKE_CLOSED"; echo true; else echo false; fi ;;
  *"return tty of s"*) echo "$FAKE_TTY" ;;
  *)
    if [ -e "$FAKE_CLOSED" ]; then echo false; else echo "${FAKE_EXISTS:-false}"; fi ;;
esac
"""
# `ps -axo pid=,tty=,args=` lists the child as the `pi` on $FAKE_TTY; `ps -axo comm=` says iTerm runs.
PS = """#!/bin/sh
if [ "$1" = "-axo" ] && [ "$2" = "pid=,tty=,args=" ]; then
  [ -n "$FAKE_CHILD" ] && printf '%s %s /usr/local/bin/pi\\n' "$FAKE_CHILD" "${FAKE_TTY#/dev/}"
  exit 0
fi
if [ "$1" = "-axo" ]; then echo /Applications/iTerm.app/Contents/MacOS/iTerm2; exit 0; fi
exec /bin/ps "$@"
"""


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_EXISTS", "true")
    monkeypatch.setenv("FAKE_CLOSE", "true")
    monkeypatch.setenv("FAKE_TTY", TTY)
    monkeypatch.setenv("FAKE_CLOSED", str(tmp_path / "closed-marker"))


@pytest.fixture
def child(monkeypatch):
    p = subprocess.Popen(["sleep", "120"])
    monkeypatch.setenv("FAKE_CHILD", str(p.pid))
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def osa_calls(tmp_path):
    p = tmp_path / "osa.log"
    return [s for s in p.read_text().split("\n=====\n") if s.strip()] if p.is_file() else []


def events(tmp_path, name):
    p = H(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [{k: v for k, v in r.items() if k != "ts"} for r in recs if r["event"] == name]


def pilead(*args, env=None, caller=LEAD):
    e = {**os.environ, **(env or {})}
    if caller is not None:
        e["PI_LEAD_SID"] = caller
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)


def successor(tmp_path, pred=True, own_handle=f"w0t2p0:{OWN_UUID}", **pred_over):
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": LEAD, "project": "proj", "cwd": str(tmp_path), "inbox": str(d / f"{LEAD}.inbox.md"),
           "iterm_handle": own_handle, "terminal": "iTerm.app", "launch_model": "a/b", "handoff_memo": "/m.md"}
    if pred:
        rec["predecessor"] = {"sid": PRED, "project": "proj", "iterm_handle": f"w0t1p0:{PRED_UUID}",
                              "terminal": "iTerm.app", **pred_over}
    (d / f"{LEAD}.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def record(tmp_path):
    return json.loads((H(tmp_path) / "leads" / f"{LEAD}.json").read_text())


def assert_untouched(tmp_path, child, rec):
    assert child.poll() is None  # not signalled
    assert record(tmp_path)["predecessor"] == rec["predecessor"]
    assert not any("tell s to close" in s for s in osa_calls(tmp_path))


def test_no_caller_is_exit_2(tmp_path):
    successor(tmp_path)
    r = pilead("close-predecessor", caller=None)
    assert r.returncode == 2 and r.stdout == ""
    assert osa_calls(tmp_path) == []


def test_no_predecessor_recorded_is_nothing_to_close(tmp_path):
    successor(tmp_path, pred=False)
    r = pilead("close-predecessor")
    assert (r.returncode, r.stdout) == (0, "no predecessor recorded — nothing to close\n")
    assert osa_calls(tmp_path) == [] and events(tmp_path, "predecessor_closed") == []


def test_a_predecessor_still_registered_is_refused_naming_stop(tmp_path, child):
    rec = successor(tmp_path)
    (H(tmp_path) / "leads" / f"{PRED}.json").write_text(json.dumps({"sid": PRED, "project": "proj"}))
    r = pilead("close-predecessor")
    assert r.returncode == 1 and f"pilead stop {PRED}" in r.stderr and len(r.stderr.strip().splitlines()) == 1
    assert_untouched(tmp_path, child, rec)
    assert osa_calls(tmp_path) == [] and events(tmp_path, "predecessor_closed") == []


@pytest.mark.parametrize("handle", [None, ""])
def test_no_handle_recorded_is_refused(tmp_path, child, handle):
    rec = successor(tmp_path, iterm_handle=handle)
    r = pilead("close-predecessor")
    assert r.returncode == 1 and "by hand" in r.stderr
    assert_untouched(tmp_path, child, rec)
    assert osa_calls(tmp_path) == [] and events(tmp_path, "predecessor_closed") == []


@pytest.mark.parametrize("how", ["recorded", "env"])
def test_the_callers_own_tab_is_refused_with_no_call_to_the_terminal(tmp_path, child, how):
    if how == "recorded":
        rec = successor(tmp_path, own_handle=f"w9t9p9:{PRED_UUID}")  # same UUID, another w/t/p prefix
        env = {}
    else:
        rec = successor(tmp_path, own_handle=None)
        env = {"ITERM_SESSION_ID": f"w0t1p0:{PRED_UUID}"}
    r = pilead("close-predecessor", env=env)
    assert r.returncode == 1 and "own tab" in r.stderr
    assert osa_calls(tmp_path) == []  # not one call to the terminal
    assert_untouched(tmp_path, child, rec)
    assert events(tmp_path, "predecessor_closed") == [
        {"event": "predecessor_closed", "session_id": LEAD, "predecessor": PRED, "tab_closed": False}]


def test_a_handle_that_does_not_resolve_is_refused_not_looked_for_by_title(tmp_path, child, monkeypatch):
    monkeypatch.setenv("FAKE_EXISTS", "false")
    rec = successor(tmp_path)
    r = pilead("close-predecessor")
    assert r.returncode == 1
    assert "not found" in r.stderr and "title" in r.stderr and "by hand" in r.stderr
    assert_untouched(tmp_path, child, rec)
    assert all("name of" not in s for s in osa_calls(tmp_path))
    assert events(tmp_path, "predecessor_closed")[-1]["tab_closed"] is False


def test_success_signals_the_pi_on_the_tab_and_closes_it_by_id(tmp_path, child):
    rec = successor(tmp_path)
    r = pilead("close-predecessor")
    assert (r.returncode, r.stdout) == (0, f"closed the outgoing lead's tab ({PRED})\n"), r.stderr
    assert child.wait(timeout=5) == -signal.SIGTERM
    calls = osa_calls(tmp_path)
    closes = [s for s in calls if "tell s to close" in s]
    assert len(closes) == 1 and PRED_UUID in closes[0]
    assert all(PRED_UUID in s for s in calls)  # every question was about that handle
    assert all("name of" not in s and "title" not in s.lower() for s in calls)  # no title is ever matched
    after = record(tmp_path)
    assert "predecessor" not in after
    assert after == {k: v for k, v in rec.items() if k != "predecessor"}
    assert events(tmp_path, "predecessor_closed") == [
        {"event": "predecessor_closed", "session_id": LEAD, "predecessor": PRED, "tab_closed": True}]


def test_a_close_that_does_not_take_is_exit_1_and_keeps_the_predecessor(tmp_path, child, monkeypatch):
    monkeypatch.setenv("FAKE_CLOSE", "false")
    rec = successor(tmp_path)
    r = pilead("close-predecessor")
    assert (r.returncode, r.stdout) == (1, "the tab did not close — close it by hand\n")
    assert record(tmp_path)["predecessor"] == rec["predecessor"]
    assert events(tmp_path, "predecessor_closed") == [
        {"event": "predecessor_closed", "session_id": LEAD, "predecessor": PRED, "tab_closed": False}]


def test_no_pi_on_the_tab_still_closes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_CHILD", raising=False)
    successor(tmp_path)
    r = pilead("close-predecessor")
    assert r.returncode == 0 and r.stdout.startswith("closed the outgoing lead's tab")

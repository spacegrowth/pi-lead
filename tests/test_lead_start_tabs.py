"""`pilead lead-start` gives the lead's record a tab `label` and a `color`, and paints the lead's own terminal line
(lib/pilead/verbs/lead_start.py, lib/pilead/tabs.py). Driven through bin/pilead; the terminal is a recording
`osascript` stub (it answers a tty lookup with $TTY_OUT, a plain file under the test's temp directory, and any
other script with $FAKE_TAB) and a `ps` stub saying iTerm runs."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "vendor" / "relay"))

import iterm  # noqa: E402
from pilead import tabs  # noqa: E402

HANDLE_A = "w0t0p0:11111111-2222-3333-4444-555555555555"
HANDLE_B = "w0t1p0:AAAAAAAA-0000-4000-8000-0000000000AA"
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *"tty of s"*) [ -n "$TTY_OUT" ] && printf '%s\\n' "$TTY_OUT" ;;
  *) echo "${FAKE_TAB:-false}" ;;
esac
"""
PS = """#!/bin/sh
if [ "$1" = "-axo" ]; then
  [ -n "$FAKE_ITERM_RUNNING" ] && echo /Applications/iTerm.app/Contents/MacOS/iTerm2
  exit 0
fi
exec /bin/ps "$@"
"""


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.delenv("PILEAD_NO_PAINT", raising=False)  # every terminal line here is a plain file


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def start(sid, project="proj", **env):
    return subprocess.run([str(PILEAD), "lead-start", sid, "--project", project], capture_output=True, text=True,
                          env={**os.environ, **env})


def rec(tmp_path, sid):
    return json.loads((H(tmp_path) / "leads" / f"{sid}.json").read_text())


def ledger(tmp_path):
    return [json.loads(ln) for ln in (H(tmp_path) / "ledger.jsonl").read_text().splitlines()]


def test_lead_start_writes_a_label_and_a_colour_and_keeps_them(tmp_path):
    r = start("lead-1")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}\n"
    first = rec(tmp_path, "lead-1")
    assert first["label"] == "[Lead] proj" and first["color"] == tabs.lead_color("lead-1")
    ev = [e for e in ledger(tmp_path) if e["event"] == "lead_started"]
    assert [{k: v for k, v in e.items() if k != "ts"} for e in ev] == [
        {"event": "lead_started", "session_id": "lead-1", "project": "proj"}]
    r2 = start("lead-1")
    assert r2.returncode == 0 and r2.stdout == r.stdout
    again = rec(tmp_path, "lead-1")
    assert again["label"] == first["label"] and again["color"] == first["color"]


def test_lead_start_picks_a_colour_no_other_lead_holds(tmp_path):
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True)
    (d / "lead-other.json").write_text(json.dumps({"sid": "lead-other", "color": tabs.lead_color("lead-1")}))
    assert start("lead-1").returncode == 0
    c = rec(tmp_path, "lead-1")["color"]
    assert tuple(c) in tabs.TAB_PALETTE and c != tabs.lead_color("lead-1")


def test_with_tab_colors_false_the_colour_is_null_and_no_line_is_written(tmp_path):
    H(tmp_path).mkdir(parents=True)
    (H(tmp_path) / "config.json").write_text(json.dumps({"tab_colors": False}))
    tty = tmp_path / "lead-tty"
    r = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(tty))
    assert r.returncode == 0
    got = rec(tmp_path, "lead-1")
    assert got["color"] is None and got["tty"] == str(tty) and got["label"] == "[Lead] proj"
    assert not tty.exists()


def test_the_lead_tty_holds_exactly_the_colour_sequence(tmp_path):
    tty = tmp_path / "lead-tty"
    r = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(tty))
    assert r.returncode == 0 and r.stdout == f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}\n"
    color = rec(tmp_path, "lead-1")["color"]
    assert rec(tmp_path, "lead-1")["tty"] == str(tty)
    assert tty.read_text() == iterm.tab_color_escape(color)


def test_no_tty_found_paints_nothing_and_still_succeeds(tmp_path):
    r = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT="")
    assert r.returncode == 0 and rec(tmp_path, "lead-1")["tty"] is None


def test_a_second_lead_with_the_same_project_while_the_first_is_live_gets_the_suffix(tmp_path):
    assert start("lead-1", ITERM_SESSION_ID=HANDLE_A).returncode == 0
    r = start("b7f0-two", ITERM_SESSION_ID=HANDLE_B, FAKE_TAB="true")  # the seam says lead-1's tab exists
    assert r.returncode == 0
    assert rec(tmp_path, "b7f0-two")["label"] == "[Lead] proj ·b7f0"
    assert rec(tmp_path, "lead-1")["label"] == "[Lead] proj"


def test_a_second_lead_with_the_same_project_while_the_first_is_not_live_gets_the_plain_label(tmp_path):
    assert start("lead-1", ITERM_SESSION_ID=HANDLE_A).returncode == 0
    r = start("lead-2", ITERM_SESSION_ID=HANDLE_B, FAKE_TAB="false")
    assert r.returncode == 0 and rec(tmp_path, "lead-2")["label"] == "[Lead] proj"


def test_a_record_with_a_predecessor_keeps_its_label_whatever_project_says(tmp_path):
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True)
    (d / "lead-s.json").write_text(json.dumps({"sid": "lead-s", "project": "proj", "label": "[Lead] proj ·lead",
                                               "predecessor": {"sid": "lead-p"}, "started": "2026-01-01T00:00:00Z"}))
    r = start("lead-s", project="other")
    assert r.returncode == 0
    assert rec(tmp_path, "lead-s")["label"] == "[Lead] proj ·lead"


# ── --no-rename (p23d) ───────────────────────────────────────────────────────────────────────────
def start_no_rename(sid, project="proj", **env):
    return subprocess.run([str(PILEAD), "lead-start", sid, "--project", project, "--no-rename"], capture_output=True,
                          text=True, env={**os.environ, **env})


def test_no_rename_writes_the_same_label_and_colour_and_paints_nothing(tmp_path):
    painted, plain = tmp_path / "tty-painted", tmp_path / "tty-plain"
    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"
    with_paint = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(painted), PI_LEAD_HOME=str(home_a))
    without = start_no_rename("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(plain), PI_LEAD_HOME=str(home_b))
    assert (without.returncode, without.stderr) == (0, "")
    assert without.stdout == with_paint.stdout.replace(str(home_a), str(home_b))
    a = json.loads((home_a / "leads" / "lead-1.json").read_text())
    b = json.loads((home_b / "leads" / "lead-1.json").read_text())
    assert (b["label"], b["color"]) == (a["label"], a["color"])
    assert (a["no_rename"], b["no_rename"]) == (False, True)
    assert b["tty"] == str(plain)
    assert painted.read_text() == iterm.tab_color_escape(a["color"])
    assert not plain.exists(), "the terminal line was written"
    ev_a = [{k: v for k, v in e.items() if k != "ts"} for e in
            map(json.loads, (home_a / "ledger.jsonl").read_text().splitlines())]
    ev_b = [{k: v for k, v in e.items() if k != "ts"} for e in
            map(json.loads, (home_b / "ledger.jsonl").read_text().splitlines())]
    assert ev_b == ev_a


def test_a_later_lead_start_without_the_option_writes_false_and_paints(tmp_path):
    tty = tmp_path / "lead-tty"
    assert start_no_rename("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(tty)).returncode == 0
    assert rec(tmp_path, "lead-1")["no_rename"] is True and not tty.exists()
    r = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TTY_OUT=str(tty))
    assert r.returncode == 0
    assert rec(tmp_path, "lead-1")["no_rename"] is False
    assert tty.read_text() == iterm.tab_color_escape(rec(tmp_path, "lead-1")["color"])


# ── b12: a lead inside tmux ──────────────────────────────────────────────────────────────────────────
TMUX = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
case "$*" in
  *"#{pane_tty}"*) echo /dev/ttys-pilead-test-none ;;
  *"#{window_panes}"*) echo 1 ;;
esac
exit 0
"""


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    """A recording `tmux` stub in place of conftest's failing one; the lines it was called with."""
    p = tmp_path / "fakebin" / "tmux"
    p.write_text(TMUX)
    p.chmod(0o755)
    log = tmp_path / "tmux.log"
    monkeypatch.setenv("FAKE_TMUX_LOG", str(log))
    return lambda: [ln.split(" ", 2)[2] for ln in log.read_text().splitlines()] if log.exists() else []


def _hex(c):
    return "#" + "".join(f"{v:02x}" for v in c)


def test_under_tmux_the_record_names_the_pane_and_its_window_is_named_and_painted(tmp_path, tmux):
    r = start("lead-1", PILEAD_TERMINAL="tmux", TMUX_PANE="%3", ITERM_SESSION_ID=HANDLE_A)
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}\n"
    got = rec(tmp_path, "lead-1")
    assert (got["backend"], got["iterm_handle"], got["tty"]) == ("tmux", "tmux:%3", "/dev/ttys-pilead-test-none")
    calls = tmux()
    assert "rename-window -t %3 -- [Lead] proj" in calls
    assert "set-option -w -t %3 automatic-rename off" in calls
    assert f"set-option -w -t %3 window-status-style bg={_hex(got['color'])}" in calls
    assert not any(c.startswith("send-keys") for c in calls)
    assert not (tmp_path / "osa.log").exists() or "tty of s" not in (tmp_path / "osa.log").read_text()


def test_under_tmux_no_rename_neither_renames_nor_paints(tmp_path, tmux):
    r = subprocess.run([str(PILEAD), "lead-start", "lead-1", "--project", "proj", "--no-rename"], capture_output=True,
                       text=True, env={**os.environ, "PILEAD_TERMINAL": "tmux", "TMUX_PANE": "%3"})
    assert r.returncode == 0 and r.stderr == ""
    got = rec(tmp_path, "lead-1")
    assert (got["backend"], got["iterm_handle"], got["no_rename"]) == ("tmux", "tmux:%3", True)
    assert not any(c.startswith(("rename-window", "set-option", "send-keys")) for c in tmux())


def test_under_iterm_the_record_says_iterm(tmp_path):
    r = start("lead-1", ITERM_SESSION_ID=HANDLE_A, TMUX_PANE="%3")  # conftest pins RELAY_TERMINAL=iterm
    assert r.returncode == 0
    got = rec(tmp_path, "lead-1")
    assert (got["backend"], got["iterm_handle"]) == ("iterm", HANDLE_A)

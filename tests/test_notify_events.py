"""Who posts a banner, and when: `pilead check` and `pilead list` (an executor approaching heavy), `pilead send` (the
third-packet note) and the tty `pilead lead-start` records. Driven through bin/pilead against the suite's stub
`osascript` (tests/conftest.py), which records every script it is given in $FAKE_OSA_LOG: a banner is the script
that starts `display notification`. Nothing real is posted; the kill switch is switched off only inside a test."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from test_check_views import session
from test_cli_core import PILEAD, ROOT, env, home, note_line, pilead, reported, spawn, start_lead, stub_osascript  # noqa: F401
from test_notify import script
from test_usage import assistant, u, write_log


def banners(tmp_path):
    log = tmp_path / "osa.log"
    if not log.exists():
        return []
    return [c.strip("\n") for c in log.read_text().split("=====\n") if c.strip().startswith("display notification")]


@pytest.fixture
def live(monkeypatch):
    """The kill switch off (a test of a banner needs it)."""
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.delenv("PILEAD_NO_NOTIFY", raising=False)


def warm(tmp_path, sid="s-warm", **kw):
    """A busy executor of lead-1 at 125k live context: approaching heavy at the default thresholds."""
    d = session(tmp_path, sid, **kw)
    write_log(sid, tmp_path / f"wt-{sid}", [assistant(u(3, 40, 125_000, 100))])
    return d


WARM = script("pi-lead · proj", "s-warm is approaching heavy — 125k ctx, warn line 120k, rotate line 150k — plan a rotate: pilead retire s-warm")


def out(r):
    """(exit code, stdout, stderr) with the clock's own text (`1s ago`, a timestamp to the second) masked."""
    mask = lambda t: re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<time>", re.sub(r"\b\d+[smhd] ago", "<ago>", t))  # noqa: E731
    return (r.returncode, mask(r.stdout), mask(r.stderr))


# ── check and list ───────────────────────────────────────────────────────────────────────────────
def test_check_of_an_approaching_session_posts_once_and_prints_what_it_always_printed(tmp_path, live, monkeypatch):
    start_lead()
    warm(tmp_path)
    monkeypatch.setenv("RELAY_NO_NOTIFY", "1")
    quiet = pilead("check", "s-warm")
    assert banners(tmp_path) == []
    monkeypatch.delenv("RELAY_NO_NOTIFY")
    first = pilead("check", "s-warm")
    assert out(first) == out(quiet) and "approaching heavy" in first.stdout
    assert banners(tmp_path)[0] == WARM
    again = pilead("check", "s-warm")
    assert out(again) == out(quiet)
    assert len(banners(tmp_path)) == 1
    lst = pilead("list")
    assert len(banners(tmp_path)) == 1 and "approaching heavy" in lst.stdout
    assert (home(tmp_path) / "wake" / "lead-1" / "notified" / "s-warm.ctx-warn").is_file()


def test_list_is_the_one_that_posts_when_it_runs_first_and_is_unchanged_by_it(tmp_path, live, monkeypatch):
    start_lead()
    warm(tmp_path)
    monkeypatch.setenv("RELAY_NO_NOTIFY", "1")
    quiet = pilead("list"), pilead("list", "--json")
    assert banners(tmp_path) == []
    monkeypatch.delenv("RELAY_NO_NOTIFY")
    loud = pilead("list")
    assert out(loud) == out(quiet[0])
    assert banners(tmp_path) == [WARM]
    assert out(pilead("list", "--json")) == out(quiet[1])
    assert pilead("check", "s-warm").returncode == 0
    assert banners(tmp_path) == [WARM]


def test_check_all_and_json_post_the_same_banner_and_print_the_same(tmp_path, live, monkeypatch):
    start_lead()
    warm(tmp_path)
    monkeypatch.setenv("RELAY_NO_NOTIFY", "1")
    quiet = pilead("check", "--all", "--json")
    monkeypatch.delenv("RELAY_NO_NOTIFY")
    loud = pilead("check", "--all", "--json")
    assert out(loud) == out(quiet) and banners(tmp_path) == [WARM]


def test_a_session_with_no_lead_and_one_that_is_heavy_post_nothing(tmp_path, live):
    start_lead()
    d = warm(tmp_path, "s-orphan")
    meta = json.loads((d / "meta.json").read_text())
    del meta["lead"]
    (d / "meta.json").write_text(json.dumps(meta))
    session(tmp_path, "s-gone-lead")
    write_log("s-gone-lead", tmp_path / "wt-s-gone-lead", [assistant(u(3, 40, 125_000, 100))])
    m = json.loads((home(tmp_path) / "sessions" / "s-gone-lead" / "meta.json").read_text())
    m["lead"] = "lead-not-registered"
    (home(tmp_path) / "sessions" / "s-gone-lead" / "meta.json").write_text(json.dumps(m))
    session(tmp_path, "s-heavy")
    write_log("s-heavy", tmp_path / "wt-s-heavy", [assistant(u(3, 40, 160_000, 100))])
    session(tmp_path, "s-cool")
    write_log("s-cool", tmp_path / "wt-s-cool", [assistant(u(3, 40, 20_000, 100))])
    session(tmp_path, "s-closed", status="closed")
    write_log("s-closed", tmp_path / "wt-s-closed", [assistant(u(3, 40, 125_000, 100))])
    for sid in ("s-orphan", "s-gone-lead", "s-heavy", "s-cool", "s-closed"):
        assert pilead("check", sid).returncode == 0
    pilead("list")
    pilead("check", "--all")
    assert banners(tmp_path) == []
    assert not (home(tmp_path) / "wake").exists()


def test_notify_on_wake_false_posts_nothing_and_keeps_the_claim_unused(tmp_path, live):
    start_lead()
    warm(tmp_path)
    (home(tmp_path) / "config.json").write_text(json.dumps({"notify_on_wake": False}))
    pilead("check", "s-warm")
    pilead("list")
    assert banners(tmp_path) == [] and not (home(tmp_path) / "wake").exists()
    (home(tmp_path) / "config.json").write_text(json.dumps({"notify_on_wake": True}))
    pilead("check", "s-warm")
    assert banners(tmp_path) == [WARM]


def test_refs_only_and_status_post_nothing(tmp_path, live):
    start_lead()
    warm(tmp_path)
    assert pilead("check", "s-warm", "--refs-only").returncode == 0
    assert pilead("status", "s-warm").returncode == 0
    assert banners(tmp_path) == []


def test_the_thresholds_are_read_from_config_in_the_banner(tmp_path, live):
    start_lead()
    warm(tmp_path)
    (home(tmp_path) / "config.json").write_text(json.dumps({"context_warn_tokens": 100_000, "context_nudge_tokens": 200_000}))
    pilead("check", "s-warm")
    assert banners(tmp_path) == [script("pi-lead · proj", "s-warm is approaching heavy — 125k ctx, warn line 100k, "
                                                          "rotate line 200k — plan a rotate: pilead retire s-warm")]


# ── send ─────────────────────────────────────────────────────────────────────────────────────────
def test_the_third_send_posts_with_the_notes_text_and_the_first_two_do_not(env, live):
    sid = spawn(env)
    for n in (2, 3):
        (env / f"p{n}.md").write_text("more\n")
    reported(env, sid, 1)
    assert pilead("send", sid, str(env / "p2.md")).stderr == ""
    assert banners(env) == []
    reported(env, sid, 2)
    r = pilead("send", sid, str(env / "p3.md"))
    note = note_line(sid, 3, "sonnet", "--upgrade moves it one tier up with a seeded successor").rstrip("\n")
    assert (r.stdout, r.stderr) == (f"sent {sid} packet 0003\n", note + "\n")
    assert banners(env) == [script("pi-lead · proj", f"{sid}: packet 3 — " + note[:180])]
    # the note prints every time, and so does its banner (it is not claimed)
    (env / "p4.md").write_text("more\n")
    reported(env, sid, 3)
    assert pilead("send", sid, str(env / "p4.md")).stderr.endswith("\n")
    assert len(banners(env)) == 2


def test_send_prints_the_same_under_the_kill_switch(env, live, monkeypatch):
    sid = spawn(env)
    for n in (2, 3):
        (env / f"p{n}.md").write_text("more\n")
    reported(env, sid, 1)
    pilead("send", sid, str(env / "p2.md"))
    reported(env, sid, 2)
    monkeypatch.setenv("RELAY_NO_NOTIFY", "1")
    r = pilead("send", sid, str(env / "p3.md"))
    assert r.stderr.startswith("pilead: this is packet 3") and banners(env) == []


# ── lead-start ───────────────────────────────────────────────────────────────────────────────────
TTY_STUB = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$TTY_LOG"
[ -n "$TTY_OUT" ] && printf '%s\\n' "$TTY_OUT"
exit ${TTY_RC:-0}
"""


@pytest.fixture
def tty_stub(tmp_path, monkeypatch):
    d = tmp_path / "ttybin"
    d.mkdir()
    (d / "osascript").write_text(TTY_STUB)
    (d / "osascript").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TTY_LOG", str(tmp_path / "tty.log"))
    return tmp_path / "tty.log"


def lead_start(**env_):
    return subprocess.run([str(PILEAD), "lead-start", "lead-1", "--project", "proj"], capture_output=True, text=True,
                          env={**os.environ, **env_})


def record(tmp_path):
    return json.loads((home(tmp_path) / "leads" / "lead-1.json").read_text())


def test_lead_start_records_the_tty_found_through_iterm(tmp_path, tty_stub):
    r = lead_start(ITERM_SESSION_ID="w0t0p0:UUID-1", TTY_OUT="/dev/ttys009")
    assert r.returncode == 0 and r.stdout == f"lead lead-1 ready inbox={home(tmp_path) / 'leads' / 'lead-1.inbox.md'}\n"
    rec = record(tmp_path)
    assert rec["tty"] == "/dev/ttys009" and rec["iterm_handle"] == "w0t0p0:UUID-1"
    assert tty_stub.read_text().count("=====") == 1 and "UUID-1" in tty_stub.read_text()


def test_lead_start_records_null_when_the_lookup_fails(tmp_path, tty_stub):
    for extra in ({"TTY_RC": "1", "TTY_OUT": "/dev/ttys009"}, {"TTY_OUT": ""}, {"TTY_OUT": "not a device"}):
        r = lead_start(ITERM_SESSION_ID="w0t0p0:UUID-1", **extra)
        assert r.returncode == 0, r
        rec = record(tmp_path)
        assert "tty" in rec and rec["tty"] is None, extra


def test_lead_start_without_iterm_has_no_tty_key_and_asks_nothing(tmp_path, tty_stub):
    r = lead_start(TTY_OUT="/dev/ttys009")
    assert r.returncode == 0
    assert "tty" not in record(tmp_path) and not tty_stub.exists()
    # a re-run outside iTerm drops the tty a run inside it recorded, with the handle it belonged to
    lead_start(ITERM_SESSION_ID="w0t0p0:UUID-1", TTY_OUT="/dev/ttys009")
    assert record(tmp_path)["tty"] == "/dev/ttys009"
    lead_start()
    rec = record(tmp_path)
    assert "tty" not in rec and rec["iterm_handle"] is None

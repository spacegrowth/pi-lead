"""`pilead notify`: each row of its table with title, subtitle and message compared, driven through bin/pilead
with a stub `osascript` (it records its arguments in a file) and a home under the test's temp directory. No
lead here has a tty or a tab, so every banner is tier 2 and nothing real is posted."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_notify import STUB, script, stub_dir, tree

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
NOTHING = "banner not posted"


@pytest.fixture
def h(tmp_path, monkeypatch):
    """The home, with the kill switch off and the recording stub first on PATH."""
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.delenv("PILEAD_NO_NOTIFY", raising=False)
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    monkeypatch.setenv("PATH", f"{stub_dir(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "pi-lead-home"
    home.mkdir()
    return home


def posted(tmp_path):
    log = tmp_path / "stub.log"
    if not log.exists():
        return []
    return [c.strip("\n").split("\n") for c in log.read_text().split("=====\n") if c.strip()]


def run(*args, home=None, check=None):
    argv = [str(PILEAD), *args] + (["--home", str(home)] if home else [])
    return subprocess.run(argv, capture_output=True, text=True)


def write_lead(home, sid="lead-1", project="proj", **fields):
    d = home / "leads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": project,
                                               "inbox": str(d / f"{sid}.inbox.md"), **fields}))
    (d / f"{sid}.inbox.md").write_text("")


def write_session(home, sid="exec-a", lead="lead-1", report=None, n=1):
    d = home / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "worktree": "/wt", "packets": n}
    if lead is not None:
        meta["lead"] = lead
    (d / "meta.json").write_text(json.dumps(meta))
    if report is not None:
        (d / f"report-{n:04d}.md").write_text(report)


def write_config(home, **cfg):
    (home / "config.json").write_text(json.dumps(cfg))


def iso(ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def write_health(home, sid="lead-1", **fields):
    d = home / "wake" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps({
        "sid": sid, "pid": os.getpid(), "started": iso(600), "beat": iso(0), "watching": True, "poll_seconds": 3,
        "delivered": 0, "last_delivered": None, "pending": 0, "last_error": None, "last_error_at": None,
        "ended": None, **fields}))


# ── <lead> --executor S --packet N ───────────────────────────────────────────────────────────────
def test_the_lead_form_posts_title_subtitle_and_the_brief(h, tmp_path):
    write_lead(h)
    write_session(h, report="# Done: x\n\nStatus: clean\n")
    r = run("notify", "lead-1", "--executor", "exec-a", "--packet", "1", home=h)
    assert (r.returncode, r.stdout, r.stderr) == (0, "banner posted via osascript\n", "")
    assert posted(tmp_path) == [["-e", script("pi-lead · proj", "exec-a reported — Done: x")]]
    assert (h / "wake" / "lead-1" / "notified" / "exec-a.report-1").read_bytes() == b""


def test_the_brief_skips_an_empty_first_line_and_falls_back_to_report_ready(h, tmp_path):
    write_lead(h)
    write_session(h, "exec-a", report="\n   \n## Fixed   the\tthing \nmore\n")
    write_session(h, "exec-b", report=None)
    write_session(h, "exec-c", report="")
    for sid in ("exec-a", "exec-b", "exec-c"):
        assert run("notify", "lead-1", "--executor", sid, "--packet", "1", home=h).stdout == "banner posted via osascript\n"
    assert [c[1] for c in posted(tmp_path)] == [script("pi-lead · proj", "exec-a reported — Fixed the thing"),
                                                script("pi-lead · proj", "exec-b reported — report ready"),
                                                script("pi-lead · proj", "exec-c reported — report ready")]


def test_a_second_run_is_already_announced_and_posts_nothing(h, tmp_path):
    write_lead(h)
    write_session(h, report="Done.\n")
    args = ("notify", "lead-1", "--executor", "exec-a", "--packet", "1")
    assert run(*args, home=h).stdout == "banner posted via osascript\n"
    r = run(*args, home=h)
    assert (r.returncode, r.stdout) == (0, f"{NOTHING} (already announced)\n")
    assert len(posted(tmp_path)) == 1
    # another packet of the same executor is another thing to announce
    assert run("notify", "lead-1", "--executor", "exec-a", "--packet", "2", home=h).stdout == "banner posted via osascript\n"
    assert len(posted(tmp_path)) == 2


def test_notify_on_wake_false_leaves_the_claim_unused(h, tmp_path):
    write_lead(h)
    write_session(h, report="Done.\n")
    write_config(h, notify_on_wake=False)
    r = run("notify", "lead-1", "--executor", "exec-a", "--packet", "1", home=h)
    assert (r.returncode, r.stdout) == (0, f"{NOTHING} (notify_on_wake is off)\n")
    assert posted(tmp_path) == [] and not (h / "wake").exists()
    write_config(h, notify_on_wake=True)
    assert run("notify", "lead-1", "--executor", "exec-a", "--packet", "1", home=h).stdout == "banner posted via osascript\n"


def test_a_lead_without_a_project_is_titled_by_its_id(h, tmp_path):
    write_lead(h, project=None)
    write_session(h, report="Done.\n")
    run("notify", "lead-1", "--executor", "exec-a", "--packet", "1", home=h)
    assert posted(tmp_path) == [["-e", script("pi-lead · lead-1", "exec-a reported — Done.")]]


# ── --no-lead ────────────────────────────────────────────────────────────────────────────────────
NO_LEAD = ("notify", "--no-lead", "--executor", "exec-a", "--packet", "1")


def test_no_lead_for_an_executor_that_names_no_lead(h, tmp_path):
    write_session(h, lead=None)
    r = run(*NO_LEAD, home=h)
    assert (r.returncode, r.stdout) == (0, "banner posted via osascript\n")
    assert posted(tmp_path) == [["-e", script("pi-lead — report ready",
                                              "exec-a reported — no lead is registered for it — pilead check exec-a")]]
    assert (h / "wake" / "_no-lead" / "notified" / "exec-a.no-lead-1").is_file()
    assert run(*NO_LEAD, home=h).stdout == f"{NOTHING} (already announced)\n"
    assert len(posted(tmp_path)) == 1


def test_no_lead_for_an_executor_with_no_record_at_all(h, tmp_path):
    assert run(*NO_LEAD, home=h).stdout == "banner posted via osascript\n"
    assert posted(tmp_path) == [["-e", script("pi-lead — report ready",
                                              "exec-a reported — no lead is registered for it — pilead check exec-a")]]


def test_no_lead_for_a_lead_that_has_no_record(h, tmp_path):
    write_session(h, lead="lead-gone")
    assert run(*NO_LEAD, home=h).stdout == "banner posted via osascript\n"
    assert posted(tmp_path) == [["-e", script("pi-lead — report ready",
                                              "exec-a reported — no lead is registered for it — pilead check exec-a")]]
    assert (h / "wake" / "lead-gone" / "notified" / "exec-a.no-lead-1").is_file()


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


WORDS = {
    "off": lambda h: None,  # no health file
    "stale": lambda h: write_health(h, pid=dead_pid()),
    "unknown": lambda h: (h / "wake" / "lead-1").mkdir(parents=True) or (h / "wake" / "lead-1" / "health.json").write_text("{nope"),
    "ok": lambda h: write_health(h),
    "stuck": lambda h: write_health(h, last_error="boom", last_error_at=iso(5)),
}


@pytest.mark.parametrize("word", ["off", "stale", "unknown"])
def test_no_lead_when_the_leads_wake_is_not_listening(h, tmp_path, word):
    write_lead(h)
    write_session(h)
    WORDS[word](h)
    r = run(*NO_LEAD, home=h)
    assert (r.returncode, r.stdout) == (0, "banner posted via osascript\n")
    assert posted(tmp_path) == [["-e", script("pi-lead — report ready",
        f"exec-a reported — its lead is not listening ({word}) — the report waits in the inbox")]]
    assert (h / "wake" / "lead-1" / "notified" / "exec-a.no-lead-1").is_file()


@pytest.mark.parametrize("word", ["ok", "stuck"])
def test_no_lead_posts_nothing_when_the_lead_is_listening(h, tmp_path, word):
    write_lead(h)
    write_session(h)
    WORDS[word](h)
    r = run(*NO_LEAD, home=h)
    assert (r.returncode, r.stdout) == (0, f"{NOTHING} (lead is listening)\n")
    assert posted(tmp_path) == [] and not (h / "wake" / "lead-1" / "notified").exists()


def test_no_lead_needs_notify_on_wake_and_executor_escalation_and_uses_no_claim_when_off(h, tmp_path):
    write_session(h, lead=None)
    write_config(h, notify_on_wake=False)
    assert run(*NO_LEAD, home=h).stdout == f"{NOTHING} (notify_on_wake is off)\n"
    write_config(h, executor_escalation=False)
    assert run(*NO_LEAD, home=h).stdout == f"{NOTHING} (executor_escalation is off)\n"
    assert posted(tmp_path) == [] and not (h / "wake").exists()
    write_config(h, executor_escalation=True)
    assert run(*NO_LEAD, home=h).stdout == "banner posted via osascript\n"
    # executor_escalation is not needed by the lead form
    write_lead(h)
    write_session(h, "exec-b", report="Done.\n")
    write_config(h, executor_escalation=False)
    assert run("notify", "lead-1", "--executor", "exec-b", "--packet", "1", home=h).stdout == "banner posted via osascript\n"


# ── --test ───────────────────────────────────────────────────────────────────────────────────────
def test_test_posts_although_notify_on_wake_is_false_and_claims_nothing(h, tmp_path):
    write_lead(h)
    write_config(h, notify_on_wake=False, executor_escalation=False)
    for _ in range(2):
        r = run("notify", "lead-1", "--test", home=h)
        assert (r.returncode, r.stdout) == (0, "banner posted via osascript\n")
    assert posted(tmp_path) == [["-e", script("pi-lead · proj", "test banner — If you can read this, banners work.")]] * 2
    assert not (h / "wake").exists()


@pytest.mark.parametrize("var", ["PILEAD_NO_NOTIFY", "RELAY_NO_NOTIFY"])
def test_the_kill_switch_posts_nothing_and_claims_nothing(h, tmp_path, monkeypatch, var):
    write_lead(h)
    write_session(h, report="Done.\n")
    write_session(h, "exec-b", lead=None)
    monkeypatch.setenv(var, "1")
    for args in (("notify", "lead-1", "--test"), ("notify", "lead-1", "--executor", "exec-a", "--packet", "1"),
                 ("notify", "--no-lead", "--executor", "exec-b", "--packet", "1")):
        r = run(*args, home=h)
        assert (r.returncode, r.stdout) == (0, f"{NOTHING} (kill switch)\n"), args
    assert posted(tmp_path) == [] and not (h / "wake").exists()


# ── refusals ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    ("notify", "../../x", "--executor", "exec-a", "--packet", "1"),
    ("notify", "../../x", "--test"),
    ("notify", "lead-1", "--executor", "../../x", "--packet", "1"),
    ("notify", "--no-lead", "--executor", "../../x", "--packet", "1"),
    ("notify", "lead-1.usage", "--test"),
])
def test_an_id_that_is_not_usable_exits_2_and_touches_nothing(h, tmp_path, args):
    deep = tmp_path / "a" / "b" / "home"
    deep.mkdir(parents=True)
    before = tree(tmp_path)
    r = run(*args, home=deep)
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr, r
    assert tree(tmp_path) == before
    assert not (tmp_path / "x").exists() and not (tmp_path / "a" / "x").exists()


def test_a_lead_without_a_record_exits_2(h, tmp_path):
    before = tree(tmp_path)
    for args in (("notify", "lead-9", "--test"), ("notify", "lead-9", "--executor", "exec-a", "--packet", "1")):
        r = run(*args, home=h)
        assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: notify: lead-9 is not a registered lead\n")
    assert tree(tmp_path) == before and posted(tmp_path) == []


@pytest.mark.parametrize("args", [
    ("notify",), ("notify", "lead-1"), ("notify", "lead-1", "--executor", "exec-a"), ("notify", "lead-1", "--packet", "1"),
    ("notify", "lead-1", "--test", "--executor", "exec-a"), ("notify", "--no-lead"), ("notify", "--no-lead", "--test"),
    ("notify", "--no-lead", "lead-1", "--executor", "exec-a", "--packet", "1"),
    ("notify", "lead-1", "--executor", "exec-a", "--packet", "0"),
])
def test_a_call_that_is_not_one_of_the_forms_exits_2(h, tmp_path, args):
    write_lead(h)
    r = run(*args, home=h)
    assert r.returncode == 2 and r.stdout == "" and posted(tmp_path) == [], r


def test_a_non_numeric_packet_exits_2(h, tmp_path):
    write_lead(h)
    r = run("notify", "lead-1", "--executor", "exec-a", "--packet", "x", home=h)
    assert r.returncode == 2 and posted(tmp_path) == []


def test_help_lists_the_verb():
    r = subprocess.run([sys.executable, str(PILEAD), "notify", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--no-lead" in r.stdout and "--test" in r.stdout

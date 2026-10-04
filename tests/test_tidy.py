"""`pilead tidy` and tabs.tidy_now (p23b changes 2-4). In-process tests replace the backend's `reorder_tabs` with a
recorder; tests that drive bin/pilead set FAKE_ITERM2 to `ok` with a layout, so the vendored code talks to the test
stand-in (tests/fake_iterm2), which logs every `async_set_tabs`. Each test that lets a tidy run removes
RELAY_NO_TIDY for itself, and only together with one of those two stand-ins: no test can move a real tab. No test
sleeps (tabs._sleep is replaced in-process; a bin/pilead test never meets a failure that is asked again)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import config, tabs  # noqa: E402
from pilead.launch import backend  # noqa: E402
from test_tidy_order import H, U, make_exec, make_lead, tree  # noqa: E402

it = backend.by_name("iterm")
PILEAD = ROOT / "bin" / "pilead"
CFG = config.DEFAULTS
OFF = {**config.DEFAULTS, "tab_colors": False}
SKIP = "  · tab tidy skipped"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_NO_TIDY", raising=False)
    h = tmp_path / "home"
    (h / "leads").mkdir(parents=True)
    return h


def events(home, *names):
    p = Path(home) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if r["event"] in names]


def two_groups(home, **lead_extra):
    make_lead(home, "lead-a", 1, started="2026-10-01T08:00:00Z", color=[1, 2, 3], **lead_extra)
    make_lead(home, "lead-b", 2, started="2026-10-01T09:00:00Z")
    make_exec(home, "a-1", "lead-a", 11, created="2026-10-01T10:00:00Z")
    make_exec(home, "a-2", "lead-a", 12, created="2026-10-01T10:01:00Z")
    make_exec(home, "b-1", "lead-b", 21, created="2026-10-01T10:00:00Z")


# ── in-process: tidy_now with a recorder ──────────────────────────────────────────────────────────
@pytest.fixture
def recorder(monkeypatch):
    """`reorder_tabs` replaced: each call is recorded and answered from st["answers"] (the last one repeats)."""
    st = {"calls": [], "answers": [(True, "moved 2 tab(s) in 1 window(s)")], "sleeps": []}

    def reorder_tabs(order, timeout=None, tty_hints=None, follows=None, dry_run=False):
        st["calls"].append({"order": order, "tty_hints": tty_hints, "follows": follows, "dry_run": dry_run})
        return st["answers"][min(len(st["calls"]), len(st["answers"])) - 1]

    monkeypatch.setattr(it, "reorder_tabs", reorder_tabs)
    monkeypatch.setattr(tabs, "_sleep", lambda s: st["sleeps"].append(s))
    monkeypatch.setattr(tabs, "ttys_of", lambda pids: {})
    return st


def test_the_recorder_gets_order_follows_and_tty_hints(home, recorder, monkeypatch):
    two_groups(home)
    monkeypatch.setattr(tabs, "tty_hints", lambda h, gs: {H(1): "ttys004"})
    ok, reason, gs = tabs.tidy_now(home, OFF)
    assert (ok, reason) == (True, "moved 2 tab(s) in 1 window(s)")
    assert recorder["calls"] == [{"order": tabs.order(gs), "tty_hints": {H(1): "ttys004"},
                                  "follows": tabs.follows(gs), "dry_run": False}]
    assert tabs.order(gs) == [H(1), H(11), H(12), H(2), H(21)]


def test_no_group_asks_nothing_and_writes_no_event(home, recorder):
    assert tabs.tidy_now(home, CFG) == (False, "no lead with a recorded iTerm2 tab", [])
    assert recorder["calls"] == [] and events(home, "tidy", "tidy_skipped") == []


def test_relay_no_tidy_asks_nothing_paints_nothing_writes_no_event(home, recorder, monkeypatch):
    two_groups(home)
    make_lead(home, "lead-c", 3)  # no colour: a paint would give it one
    monkeypatch.setenv("RELAY_NO_TIDY", "1")
    before = tree(home)
    ok, reason, gs = tabs.tidy_now(home, CFG)
    assert (ok, reason) == (False, "disabled by RELAY_NO_TIDY") and len(gs) == 3
    assert recorder["calls"] == [] and tree(home) == before


@pytest.mark.parametrize("reason", ["iterm2 python api unavailable (ConnectionClosedError: no close frame received "
                                    "or sent)", "iterm2 python api unavailable (TimeoutError: )",
                                    "iterm2 python api unavailable (websockets.exceptions.WebSocketException: x)",
                                    "iterm2 python api unavailable (ConnectionRefusedError: refused)",
                                    "iterm2 python api unavailable (EOFError: eof)"])
def test_a_connection_failure_is_asked_once_more(home, recorder, reason):
    two_groups(home)
    recorder["answers"] = [(False, reason), (True, "moved 1 tab(s) in 1 window(s)")]
    ok, got, _ = tabs.tidy_now(home, OFF)
    assert (ok, got) == (True, "moved 1 tab(s) in 1 window(s)")
    assert len(recorder["calls"]) == 2 and recorder["sleeps"] == [1.0]
    [e] = events(home, "tidy", "tidy_skipped")
    assert e["event"] == "tidy" and e["attempts"] == 2


def test_a_second_failure_is_recorded_as_skipped_after_two_attempts(home, recorder):
    two_groups(home)
    bad = "iterm2 python api unavailable (ConnectionClosedError: gone)"
    recorder["answers"] = [(False, bad)]
    assert tabs.tidy_now(home, OFF)[:2] == (False, bad)
    assert len(recorder["calls"]) == 2
    [e] = events(home, "tidy", "tidy_skipped")
    assert {k: e[k] for k in ("event", "session_id", "attempts", "reason")} == {
        "event": "tidy_skipped", "session_id": None, "attempts": 2, "reason": bad}


@pytest.mark.parametrize("reason", ["no iTerm tab found for any of the ordered session ids",
                                    "iterm2 python api unavailable (ModuleNotFoundError: No module named 'iterm2')",
                                    "iterm2 python api unavailable (OSError: socket closed)",  # the TYPE names none
                                    "nothing to order"])
def test_other_failures_are_not_asked_again(home, recorder, reason):
    two_groups(home)
    recorder["answers"] = [(False, reason)]
    assert tabs.tidy_now(home, OFF)[:2] == (False, reason)
    assert len(recorder["calls"]) == 1 and recorder["sleeps"] == []
    [e] = events(home, "tidy", "tidy_skipped")
    assert (e["event"], e["attempts"], e["reason"]) == ("tidy_skipped", 1, reason)


def test_the_tidy_event_carries_the_numbers_of_the_reason(home, recorder):
    two_groups(home)
    reason = ("moved 3 tab(s) in 2 window(s); 1 session id(s) had no tab; 1 executor tab(s) left in place (lead "
              "tab not in their window)")
    recorder["answers"] = [(True, reason)]
    tabs.tidy_now(home, OFF)
    [e] = events(home, "tidy", "tidy_skipped")
    assert {k: v for k, v in e.items() if k != "ts"} == {
        "event": "tidy", "session_id": None, "attempts": 1, "reason": reason, "moved": 3, "windows": 2, "missing": 1}


def test_numbers_the_reason_does_not_name_are_zero(home, recorder):
    two_groups(home)
    recorder["answers"] = [(True, "something else")]
    tabs.tidy_now(home, OFF)
    [e] = events(home, "tidy")
    assert (e["moved"], e["windows"], e["missing"]) == (0, 0, 0)


def test_session_id_is_lead_else_the_caller_else_null(home, recorder, monkeypatch):
    two_groups(home)
    tabs.tidy_now(home, OFF, lead="lead-b")
    monkeypatch.setenv("PI_LEAD_SID", "lead-a")
    tabs.tidy_now(home, OFF)
    tabs.tidy_now(home, OFF, lead="lead-b")
    monkeypatch.setenv("PI_LEAD_SID", "lead-unregistered")
    tabs.tidy_now(home, OFF)
    assert [e["session_id"] for e in events(home, "tidy")] == ["lead-b", "lead-a", "lead-b", None]


def test_a_ledger_that_cannot_be_written_changes_nothing_else(home, recorder):
    two_groups(home)
    (home / "ledger.jsonl").mkdir()
    assert tabs.tidy_now(home, OFF)[:2] == (True, "moved 2 tab(s) in 1 window(s)")


def test_a_dry_run_paints_nothing_writes_nothing_and_asks_once(home, recorder, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_PAINT", raising=False)
    tty = home.parent / "lead-tty"
    tty.write_text("")
    two_groups(home, tty=str(tty))
    make_lead(home, "lead-c", 3)
    recorder["answers"] = [(False, "iterm2 python api unavailable (ConnectionClosedError: x)")]
    before = tree(home.parent)
    ok, reason, _ = tabs.tidy_now(home, CFG, dry_run=True)
    assert not ok and len(recorder["calls"]) == 1 and recorder["calls"][0]["dry_run"] is True
    assert recorder["sleeps"] == [] and tree(home.parent) == before


# ── painting ──────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def seam(monkeypatch, tmp_path):
    """iTerm `running`; `run_osascript` answers, for the tab whose UUID the script names, the plain file
    tmp/tty-<uuid>."""
    monkeypatch.delenv("PILEAD_NO_PAINT", raising=False)
    monkeypatch.setattr(it, "running", lambda: True)

    def run_osascript(script, timeout=None):
        for n in (1, 2, 3, 11, 12, 21):
            if U(n) in script:
                return subprocess.CompletedProcess(["osascript"], 0, f"{tmp_path / f'tty-{n}'}\n", "")
        return subprocess.CompletedProcess(["osascript"], 1, "", "no such tab")

    monkeypatch.setattr(it, "run_osascript", run_osascript)
    for n in (1, 2, 3, 11, 12, 21):
        (tmp_path / f"tty-{n}").write_text("")
    return lambda n: (tmp_path / f"tty-{n}").read_text()


def test_with_tab_colors_each_group_wears_its_leads_colour(home, recorder, seam):
    two_groups(home)
    rec_b = json.loads((home / "leads" / "lead-b.json").read_text())
    assert "color" not in rec_b
    tabs.tidy_now(home, CFG)
    b_color = json.loads((home / "leads" / "lead-b.json").read_text())["color"]
    assert tabs.usable_color(b_color) and b_color != [1, 2, 3]  # a lead with no colour gets one and keeps it
    a_seq, b_seq = it.tab_color_escape([1, 2, 3]), it.tab_color_escape(b_color)
    assert (seam(1), seam(11), seam(12)) == (a_seq, a_seq, a_seq)
    assert (seam(2), seam(21)) == (b_seq, b_seq)


def test_the_leads_recorded_line_is_painted_without_asking(home, recorder, seam, tmp_path):
    own = tmp_path / "lead-own-tty"
    own.write_text("")
    make_lead(home, "lead-a", 1, color=[4, 5, 6], tty=str(own))
    tabs.tidy_now(home, CFG)
    assert own.read_text() == it.tab_color_escape([4, 5, 6]) and seam(1) == ""


def test_with_tab_colors_false_nothing_is_painted_or_written(home, recorder, seam):
    two_groups(home)
    before = json.loads((home / "leads" / "lead-b.json").read_text())
    tabs.tidy_now(home, OFF)
    assert all(seam(n) == "" for n in (1, 2, 11, 12, 21))
    assert json.loads((home / "leads" / "lead-b.json").read_text()) == before


def test_a_paint_that_fails_is_passed_over(home, recorder, seam, monkeypatch):
    two_groups(home)
    monkeypatch.setattr(tabs, "paint", lambda tty, color: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(tabs, "repaint_executor", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    assert tabs.tidy_now(home, CFG)[:2] == (True, "moved 2 tab(s) in 1 window(s)")


# ── through the stand-in: the vendored reorder_tabs talks to tests/fake_iterm2 ─────────────────────
@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ITERM2", "ok")
    log = tmp_path / "iterm2.log"
    monkeypatch.setenv("FAKE_ITERM2_LOG", str(log))

    def layout(windows):
        monkeypatch.setenv("FAKE_ITERM2_LAYOUT", json.dumps([[[U(n) for n in tab] for tab in w] for w in windows]))
    layout.log = log
    layout.lines = lambda: [json.loads(ln) for ln in log.read_text().splitlines()] if log.is_file() else []
    return layout


def test_the_stand_in_gets_one_set_tabs_in_the_wanted_order(home, fake, monkeypatch):
    monkeypatch.setattr(tabs, "ttys_of", lambda pids: {})
    two_groups(home)
    fake([[[21], [99], [11], [1], [2], [12]]])
    ok, reason, _ = tabs.tidy_now(home, OFF)
    assert ok, reason
    assert fake.lines() == [{"window": 0, "set_tabs": [[U(n)] for n in (1, 11, 12, 2, 21, 99)]}]
    assert reason == "moved 6 tab(s) in 1 window(s)"


def test_a_window_already_in_order_gets_no_set_tabs(home, fake, monkeypatch):
    monkeypatch.setattr(tabs, "ttys_of", lambda pids: {})
    make_lead(home, "lead-a", 1, started="2026-10-01T08:00:00Z")
    make_exec(home, "a-1", "lead-a", 11)
    make_lead(home, "lead-b", 2, started="2026-10-01T09:00:00Z", w=1)
    make_exec(home, "b-1", "lead-b", 21, w=1)
    fake([[[1], [11], [50]], [[21], [2], [51]]])
    ok, reason, _ = tabs.tidy_now(home, OFF)
    assert (ok, reason) == (True, "moved 2 tab(s) in 1 window(s)")
    assert fake.lines() == [{"window": 1, "set_tabs": [[U(2)], [U(21)], [U(51)]]}]


def test_a_stale_handle_is_found_by_its_terminal_line(home, fake, monkeypatch):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11)
    d = home / "wake" / "lead-a"
    d.mkdir(parents=True)
    (d / "health.json").write_text(json.dumps({"pid": 4242, "ended": None}))
    monkeypatch.setattr(tabs, "ttys_of", lambda pids: {4242: "ttys004"} if list(pids) == [4242] else {})
    monkeypatch.setenv("FAKE_ITERM2_TTYS", json.dumps({U(7): "/dev/ttys004"}))
    fake([[[11], [7]]])  # iTerm2 was restarted: the lead's tab now has the UUID U(7)
    ok, reason, _ = tabs.tidy_now(home, OFF)
    assert (ok, reason) == (True, "moved 2 tab(s) in 1 window(s); 1 stale handle(s) "
                                  "resolved by tty")
    assert fake.lines() == [{"window": 0, "set_tabs": [[U(7)], [U(11)]]}]


# ── `pilead tidy`, every row of its table ─────────────────────────────────────────────────────────
def pilead(home, *args, env=None):
    e = {**os.environ, "COLUMNS": "100", **(env or {})}
    return subprocess.run([sys.executable, str(PILEAD), "tidy", "--home", str(home), *args], capture_output=True,
                          text=True, env=e)


LISTING = ("[Lead] p-lead-a (lead-a…) colour 1,2,3\n"
           "  1. [Exec] a-1\n"
           "  2. [Exec] a-2\n"
           "  · far — in another window, left where it is\n"
           "[Lead] p-lead-b (lead-b…) colour -\n"
           "  1. [Exec] b-1\n")


@pytest.fixture
def world(home, fake):
    two_groups(home)
    make_exec(home, "far", "lead-a", 30, w=4, created="2026-10-01T10:02:00Z")
    fake([[[21], [11], [1], [12], [2]]])
    return home


def test_no_group(home):
    r = pilead(home)
    assert (r.returncode, r.stdout) == (0, "tidy: no lead with a recorded iTerm2 tab — nothing to order\n")


def test_no_group_quiet(home):
    r = pilead(home, "--quiet")
    assert (r.returncode, r.stdout) == (1, "no lead with a recorded iTerm2 tab\n")


def test_dry_run_order_read(world, fake):
    before = tree(world)
    r = pilead(world, "--dry-run")
    assert (r.returncode, r.stdout) == (0, LISTING + "dry run — 4 tab(s) would be moved (would move 4 tab(s) in 1 "
                                           "window(s)); nothing moved\n")
    assert fake.lines() == [] and tree(world) == before


def test_dry_run_order_not_read(world, monkeypatch):
    monkeypatch.delenv("FAKE_ITERM2")
    before = tree(world)
    r = pilead(world, "--dry-run")
    assert (r.returncode, r.stdout) == (1, LISTING + "dry run — the tab order could not be read (iterm2 python api "
                                           "unavailable (ImportError: fake iterm2: not installed (tests))); "
                                           "nothing moved\n")
    assert tree(world) == before


def test_ordered(world, fake):
    r = pilead(world)
    assert (r.returncode, r.stdout) == (0, LISTING + "tidy: moved 4 tab(s) in 1 window(s)\n")
    assert fake.lines() == [{"window": 0, "set_tabs": [[U(n)] for n in (1, 11, 12, 2, 21)]}]
    [e] = events(world, "tidy", "tidy_skipped")
    assert (e["event"], e["session_id"], e["moved"], e["windows"], e["missing"]) == ("tidy", None, 4, 1, 0)


def test_not_ordered(world, fake):
    fake([[[90], [91]]])
    r = pilead(world)
    assert (r.returncode, r.stdout) == (1, LISTING + "tidy: not applied — no iTerm tab found for any of the ordered "
                                           "session ids\n")
    assert fake.lines() == []
    [e] = events(world, "tidy", "tidy_skipped")
    assert (e["event"], e["attempts"]) == ("tidy_skipped", 1)


def test_quiet_ordered(world, fake):
    r = pilead(world, "--quiet")
    assert (r.returncode, r.stdout) == (0, "")
    assert len(fake.lines()) == 1


def test_quiet_not_ordered(world, fake):
    fake([[[90]]])
    r = pilead(world, "--quiet")
    assert (r.returncode, r.stdout) == (1, "no iTerm tab found for any of the ordered session ids\n")


def test_quiet_dry_run(world, fake):
    before = tree(world)
    r = pilead(world, "--quiet", "--dry-run")
    assert (r.returncode, r.stdout) == (0, "")
    assert fake.lines() == [] and tree(world) == before


def test_lead_names_the_events_session_id(world):
    r = pilead(world, "--lead", "lead-b", "--quiet")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [e["session_id"] for e in events(world, "tidy")] == ["lead-b"]


def test_the_caller_names_the_events_session_id(world):
    r = pilead(world, "--quiet", env={"PI_LEAD_SID": "lead-a"})
    assert r.returncode == 0
    assert [e["session_id"] for e in events(world, "tidy")] == ["lead-a"]


def test_no_event_with_relay_no_tidy(world, monkeypatch):
    monkeypatch.setenv("RELAY_NO_TIDY", "1")
    before = tree(world)
    r = pilead(world)
    assert (r.returncode, r.stdout) == (1, LISTING + "tidy: not applied — disabled by RELAY_NO_TIDY\n")
    assert tree(world) == before


def test_an_unusable_lead_is_refused_first(tmp_path, world):
    before = tree(tmp_path)
    r = pilead(world, "--lead", "../../x")
    assert r.returncode == 2 and r.stdout == "" and "not usable" in r.stderr
    assert tree(tmp_path) == before

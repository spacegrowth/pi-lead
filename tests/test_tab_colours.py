"""Executors wear their lead's colour (lib/pilead/tabs.py, launch.py, ownership.py, succession.py, verbs/handoff.py),
driven through bin/pilead. The terminal is a recording `osascript` stub (a spawn answers OK; a tty lookup answers
$TTY_OUT, a plain file under the test's temp directory; with FAKE_OSA_FAIL every call but a spawn fails) and a `ps`
stub saying iTerm runs. The colour reaches a new tab as a `printf` in its launch file (bootstrap.sh) and an open
tab as the sequence written to its terminal line."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_handoff import lead as handoff_lead, memo, session as handoff_session, successor_of, LEAD as OUT

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "vendor" / "relay"))

import iterm  # noqa: E402
from pilead import tabs  # noqa: E402

HANDLE = "w0t0p0:11111111-2222-3333-4444-555555555555"
RED, BLUE = [200, 140, 135], [136, 164, 198]
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*) printf 'OK\\nFAKE-UUID\\nfront\\n' ;;
  *) [ -n "$FAKE_OSA_FAIL" ] && exit 1
     case "$2" in
       *"tty of s"*) [ -n "$TTY_OUT" ] && printf '%s\\n' "$TTY_OUT" ;;
       *) echo "${FAKE_TAB:-false}" ;;
     esac ;;
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
    monkeypatch.setenv("FAKE_TAB", "false")
    monkeypatch.setenv("FAKE_SEEN", str(tmp_path / "seen-at-spawn.txt"))
    monkeypatch.setenv("TTY_OUT", str(tmp_path / "exec-tty"))
    monkeypatch.delenv("PILEAD_NO_PAINT", raising=False)  # every terminal line here is a plain file
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def pilead(*args, caller=None, **env):
    e = {**os.environ, **env}
    if caller is not None:
        e["PI_LEAD_SID"] = caller
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)


def lead(tmp_path, sid, color=..., **extra):
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": sid, "project": f"proj-{sid}", "inbox": str(d / f"{sid}.inbox.md"), "cwd": str(tmp_path),
           "started": "2026-01-01T00:00:00Z", "terminal": "iTerm.app", "iterm_handle": None, **extra}
    if color is not ...:
        rec["color"] = color
    (d / f"{sid}.json").write_text(json.dumps(rec, indent=2) + "\n")
    (d / f"{sid}.inbox.md").write_text("")
    return d / f"{sid}.json"


def rec(tmp_path, sid):
    return json.loads((H(tmp_path) / "leads" / f"{sid}.json").read_text())


def spawn(tmp_path, lead_sid, topic="topic"):
    r = pilead("spawn", str(tmp_path / "wt"), topic, str(tmp_path / "packet.md"), "--lead", lead_sid)
    assert r.returncode == 0, r.stderr
    return r, r.stdout.split()[1]


def boot(tmp_path, sid):
    return (H(tmp_path) / "sessions" / sid / "bootstrap.sh").read_text()


def printfs(text):
    """Every colour printf in a launch file (the file holds the escape as the text `\\033`)."""
    return re.findall(r"printf '\\033\]6;1;bg;[^']*'", text)


def session(tmp_path, sid, lead_sid, handle=HANDLE):
    d = H(tmp_path) / "sessions" / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": lead_sid, "worktree": str(tmp_path / "wt"), "topic": "t", "model": "m", "status": "busy",
        "created": "-", "packets": 1, "label": f"[Exec] {sid}", "layout": "tab", "backend": "iterm"}))
    (d / "status").write_text("busy\n")
    (d / "packet-0001.md").write_text("GOAL\n")
    (d / "iterm-id").write_text(handle + "\n")


def ledger_shape(tmp_path):
    p = H(tmp_path) / "ledger.jsonl"
    out = []
    for ln in (p.read_text().splitlines() if p.is_file() else []):
        e = json.loads(ln)
        out.append({k: v for k, v in e.items() if k != "ts"})
    return out


# ── a new tab ─────────────────────────────────────────────────────────────────────────────────────
def test_a_spawn_under_a_lead_with_a_colour_prints_it_before_exec_pi(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    _, sid = spawn(tmp_path, "lead-1")
    text = boot(tmp_path, sid)
    want = f"printf '{iterm.tab_color_printf(RED)}'"
    assert text.count(want) == 1 and len(printfs(text)) == 1
    assert text.index(want) < text.index("exec pi")


def test_with_tab_colors_false_no_launch_file_prints_a_colour(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    (H(tmp_path) / "config.json").write_text(json.dumps({"tab_colors": False}))
    _, sid = spawn(tmp_path, "lead-1")
    assert printfs(boot(tmp_path, sid)) == [] and "brightness" not in boot(tmp_path, sid)


def test_a_spawn_under_a_registered_lead_without_a_colour_gives_it_one(tmp_path):
    lead(tmp_path, "lead-1")
    _, sid = spawn(tmp_path, "lead-1")
    c = rec(tmp_path, "lead-1")["color"]
    assert tuple(c) in tabs.TAB_PALETTE
    assert boot(tmp_path, sid).count(f"printf '{iterm.tab_color_printf(c)}'") == 1


def test_a_relaunch_under_a_lead_with_no_record_prints_a_colour_and_creates_no_record(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    _, sid = spawn(tmp_path, "lead-1")
    (H(tmp_path) / "leads" / "lead-1.json").unlink()  # the owner is gone: an orphan
    sdir = H(tmp_path) / "sessions" / sid
    (sdir / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    (sdir / "status").write_text("closed\n")
    r = pilead("send", sid, str(tmp_path / "packet.md"))
    assert r.returncode == 0, r.stderr
    text = boot(tmp_path, sid)
    assert text.count(f"printf '{iterm.tab_color_printf(tabs.lead_color('lead-1'))}'") == 1 and len(printfs(text)) == 1
    assert not (H(tmp_path) / "leads" / "lead-1.json").exists()


def test_meta_json_never_stores_a_colour(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    _, sid = spawn(tmp_path, "lead-1")
    meta = json.loads((H(tmp_path) / "sessions" / sid / "meta.json").read_text())
    assert "color" not in meta and "tab_color" not in meta


# ── a change of owner ─────────────────────────────────────────────────────────────────────────────
def test_after_adopt_the_executor_line_holds_the_new_owners_colour(tmp_path):
    lead(tmp_path, "lead-me", color=BLUE)
    session(tmp_path, "s-1", "lead-gone")
    r = pilead("adopt", "s-1", caller="lead-me")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "exec-tty").read_text() == iterm.tab_color_escape(BLUE)


def test_after_takeover_the_executor_line_holds_the_new_owners_colour(tmp_path):
    lead(tmp_path, "lead-old", color=RED, started="2020-01-01T00:00:00Z")
    lead(tmp_path, "lead-new", color=BLUE)
    session(tmp_path, "s-1", "lead-old")
    r = pilead("takeover", "lead-old", "--as", "lead-new")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "exec-tty").read_text() == iterm.tab_color_escape(BLUE)


def test_a_takeover_into_a_new_record_carries_the_colour_and_label(tmp_path):
    lead(tmp_path, "lead-old", color=RED, label="[Lead] proj-lead-old", started="2020-01-01T00:00:00Z")
    session(tmp_path, "s-1", "lead-old")
    r = pilead("takeover", "lead-old", "--as", "lead-new")
    assert r.returncode == 0, r.stderr
    new = rec(tmp_path, "lead-new")
    assert new["color"] == RED and new["label"] == "[Lead] proj-lead-old"
    assert (tmp_path / "exec-tty").read_text() == iterm.tab_color_escape(RED)


def test_a_relaunch_after_an_adoption_prints_the_new_owners_colour(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    lead(tmp_path, "lead-me", color=BLUE)
    _, sid = spawn(tmp_path, "lead-1")
    sdir = H(tmp_path) / "sessions" / sid
    (sdir / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    (sdir / "status").write_text("closed\n")
    assert pilead("adopt", sid, "--force", caller="lead-me").returncode == 0
    r = pilead("send", sid, str(tmp_path / "packet.md"), caller="lead-me")
    assert r.returncode == 0, r.stderr
    text = boot(tmp_path, sid)
    assert text.count(f"printf '{iterm.tab_color_printf(BLUE)}'") == 1 and len(printfs(text)) == 1


# ── a handoff ─────────────────────────────────────────────────────────────────────────────────────
def test_a_handoff_successor_has_the_callers_colour_and_the_suffixed_label(tmp_path):
    handoff_lead(tmp_path, color=RED)
    handoff_session(tmp_path, "s-a")
    r = pilead("handoff", str(memo(tmp_path)), caller=OUT, ITERM_SESSION_ID="w0t1p0:AAAAAAAA-0000-4000-8000-0000000000AA")
    assert r.returncode == 0, r.stderr
    succ = successor_of(r)
    new = rec(tmp_path, succ)
    assert new["color"] == RED and new["label"] == f"[Lead] proj ·{succ[:4]}"
    launch = (H(tmp_path) / "leads" / f"{succ}.launch" / "bootstrap.sh").read_text()
    assert launch.count(f"printf '{iterm.tab_color_printf(RED)}'") == 1


def test_a_handoff_from_a_caller_with_no_colour_picks_one_and_leaves_the_caller_alone(tmp_path):
    handoff_lead(tmp_path)
    p = H(tmp_path) / "leads" / f"{OUT}.json"
    before = p.read_bytes()
    r = pilead("handoff", str(memo(tmp_path)), caller=OUT)
    assert r.returncode == 0, r.stderr
    succ = successor_of(r)
    assert tuple(rec(tmp_path, succ)["color"]) in tabs.TAB_PALETTE
    assert not p.exists() or p.read_bytes() == before  # the move removes the caller's record; it is never painted


def test_a_handoff_with_tab_colors_false_gives_a_null_colour(tmp_path):
    handoff_lead(tmp_path, color=RED)
    (H(tmp_path) / "config.json").write_text(json.dumps({"tab_colors": False}))
    r = pilead("handoff", str(memo(tmp_path)), caller=OUT)
    assert r.returncode == 0, r.stderr
    succ = successor_of(r)
    assert rec(tmp_path, succ)["color"] is None
    assert printfs((H(tmp_path) / "leads" / f"{succ}.launch" / "bootstrap.sh").read_text()) == []


# ── painting is cosmetic ──────────────────────────────────────────────────────────────────────────
def _norm(text, *ids):
    text = text.replace(os.environ["PI_LEAD_HOME"].rsplit("/", 1)[0], "<dir>")
    for i in ids:
        text = text.replace(i, "<id>")
    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text)


def _scenario(tmp_path, fail):
    env = {"FAKE_OSA_FAIL": "1"} if fail else {}
    lead(tmp_path, "lead-1", color=RED)
    lead(tmp_path, "lead-me", color=BLUE)
    lead(tmp_path, "lead-old", started="2020-01-01T00:00:00Z")
    session(tmp_path, "s-1", "lead-gone")
    session(tmp_path, "s-2", "lead-old")
    out = []
    r = pilead("spawn", str(tmp_path / "wt"), "topic", str(tmp_path / "packet.md"), "--lead", "lead-1", **env)
    sid = r.stdout.split()[1] if r.returncode == 0 else ""
    out.append((r.returncode, _norm(r.stdout, sid)))
    r = pilead("adopt", "s-1", caller="lead-me", **env)
    out.append((r.returncode, r.stdout))
    r = pilead("takeover", "lead-old", "--as", "lead-new", **env)
    out.append((r.returncode, r.stdout))
    handoff_lead(tmp_path, color=[1, 2, 3])
    r = pilead("handoff", str(memo(tmp_path)), caller=OUT, **env)
    out.append((r.returncode, _norm(r.stdout)))
    shape = [{k: (_norm(v, sid) if isinstance(v, str) else v) for k, v in e.items()} for e in ledger_shape(tmp_path)]
    return out, shape


def test_a_stub_that_fails_at_every_call_changes_no_exit_code_stdout_or_ledger_line(tmp_path, monkeypatch):
    ok_dir, bad_dir = tmp_path / "ok", tmp_path / "bad"
    results = []
    for d, fail in ((ok_dir, False), (bad_dir, True)):
        d.mkdir()
        (d / "wt").mkdir()
        (d / "packet.md").write_text("GOAL: do the thing\n")
        monkeypatch.setenv("PI_LEAD_HOME", str(H(d)))
        monkeypatch.setenv("TTY_OUT", str(d / "exec-tty"))
        results.append(_scenario(d, fail))
    (ok_out, ok_led), (bad_out, bad_led) = results
    assert [rc for rc, _ in ok_out] == [0, 0, 0, 0]
    assert bad_out == ok_out
    assert [e["event"] for e in bad_led] == [e["event"] for e in ok_led]
    strip = lambda led: [{k: v for k, v in e.items() if k not in ("to_lead", "session_id", "memo")} for e in led]
    assert strip(bad_led) == strip(ok_led)
    assert (ok_dir / "exec-tty").exists() and not (bad_dir / "exec-tty").exists()


# ── nothing is typed into a running pi (relay 0.5.4 row 97) ───────────────────────────────────────
def _scripts(tmp_path):
    p = tmp_path / "osa.log"
    return [s for s in p.read_text().split("=====\n") if s.strip()] if p.exists() else []


def _typed(script):
    """Every `write text` of a script, as written."""
    return re.findall(r"write text [^\n]*", script)


def _only_the_launch_line(tmp_path):
    """The spawn script types its blank line and the launch line (`sh <bootstrap> <sid>`) and nothing else; no other
    script types anything."""
    for s in _scripts(tmp_path):
        assert "/name" not in s
        if "targetFound" in s:
            typed = _typed(s)
            assert typed[0] == 'write text ""' and len(typed) == 2 and typed[1].startswith('write text ("sh ')
        else:
            assert _typed(s) == []


def test_a_spawn_types_nothing_but_the_launch_line(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    spawn(tmp_path, "lead-1")
    assert any("targetFound" in s for s in _scripts(tmp_path))
    _only_the_launch_line(tmp_path)


def test_a_relaunch_by_send_types_nothing_but_the_launch_line(tmp_path):
    lead(tmp_path, "lead-1", color=RED)
    _, sid = spawn(tmp_path, "lead-1")
    sdir = H(tmp_path) / "sessions" / sid
    (sdir / "report-0001.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    (sdir / "status").write_text("closed\n")
    (tmp_path / "osa.log").unlink()
    assert pilead("send", sid, str(tmp_path / "packet.md")).returncode == 0
    assert sum("targetFound" in s for s in _scripts(tmp_path)) == 1
    _only_the_launch_line(tmp_path)


def test_the_vendored_spawn_with_type_name_false_drops_only_the_rename(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(iterm, "run_osascript",
                        lambda s, timeout=None: got.append(s) or subprocess.CompletedProcess([], 0, "OK\nX\nf\n", ""))
    args = dict(session_id="s", model="m", extension="e", rules="r", home="h", lead="l", inbox="i")
    for flag in ({}, {"type_name": True}, {"type_name": False}):
        iterm.spawn(str(tmp_path), "p.md", "[Exec] s", str(tmp_path / "pid"), args, tab_color=(1, 2, 3), **flag)
    default, typed, quiet = got
    assert default == typed and "/name [Exec] s" in typed and "delay 1.5" in typed
    assert "/name" not in quiet and "delay 1.5" not in quiet
    head, rest = typed.split("  delay 1.5\n", 1)
    tail = rest[rest.index('  set leadFlag to ""'):]
    assert quiet == head + tail


def test_rename_tab_writes_tab_title_and_asks_no_backend(tmp_path, monkeypatch):
    from pilead import lifecycle
    from pilead.launch import backend
    for be in (backend.by_name("iterm"), backend.by_name("terminal")):
        if be is not None:
            monkeypatch.setattr(be, "rename_by_id", lambda *a, **k: pytest.fail("a backend was asked"), raising=False)
            monkeypatch.setattr(be, "run_osascript", lambda *a, **k: pytest.fail("the terminal was asked"))
    home = H(tmp_path)
    session(tmp_path, "s-1", "lead-1")
    meta = json.loads((home / "sessions" / "s-1" / "meta.json").read_text())
    assert lifecycle.rename_tab(meta, HANDLE, "[closed] s-1", home=home) is True
    on_disk = json.loads((home / "sessions" / "s-1" / "meta.json").read_text())
    assert on_disk["tab_title"] == "[closed] s-1" and meta["tab_title"] == "[closed] s-1"
    assert on_disk["label"] == "[Exec] s-1"
    assert [p.name for p in (home / "sessions" / "s-1").iterdir() if ".tmp" in p.name] == []
    assert lifecycle.rename_tab(meta, "", "x", home=home) is False  # no known tab: nothing named
    assert lifecycle.rename_tab({"sid": "s-none"}, HANDLE, "x", home=home) is False

"""Tab placement next to the lead: `pilead lead-start` records the lead's iTerm handle, `pilead spawn`
hands it to the iTerm backend, which tries iTerm2's Python API (tests/fake_iterm2 — never the real
one) for a tab at the lead's index + 1 and degrades to AppleScript's end-of-bar tab. osascript is a
recording stub; no test can open, close or rename a real tab."""
import json
import os
import subprocess

import pytest

import iterm
import iterm_pyapi

from test_cli_core import PILEAD, env, home  # noqa: F401  (env is a fixture)

LEAD_UUID = "11111111-2222-4333-8444-555555555555"
LEAD_HANDLE = f"w0t1p0:{LEAD_UUID}"
OTHER = "99999999-2222-4333-8444-555555555555"
NEW = "00000000-0000-4000-8000-00000000BEEF"  # tests/fake_iterm2's new-tab session id

# Like test_cli_core's stub, plus the verdict's 4th line: "lead" when FAKE_OSA_LEAD=1 AND the script
# actually carries the find-the-lead branch (so a pyapi-placed spawn can't claim it by accident).
OSA_STUB = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*)
    lead=""
    case "$2" in *"set foundLeadWindow to true"*) [ "$FAKE_OSA_LEAD" = 1 ] && lead=lead ;; esac
    printf 'OK\\nFAKE-UUID\\nfront\\n%s\\n' "$lead" ;;
  *) echo true ;;
esac
"""


@pytest.fixture(autouse=True)
def stub_osascript(tmp_path, monkeypatch):
    stub = tmp_path / "fakebin" / "osascript"
    stub.write_text(OSA_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))
    monkeypatch.setenv("FAKE_ITERM2_LOG", str(tmp_path / "iterm2.log"))


def run(*args, check=True, **extra_env):
    """test_cli_core.pilead, plus per-call env overrides (fake iterm2 mode, the caller's identity)."""
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **extra_env})
    if check:
        assert r.returncode == 0, r.stderr
    return r


def layout(*windows):
    return json.dumps([list(w) for w in windows])


def created_tabs(tmp_path):
    p = tmp_path / "iterm2.log"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.is_file() else []


def osa_scripts(tmp_path):
    return (tmp_path / "osa.log").read_text()


def lead_start(**extra_env):
    extra_env.setdefault("ITERM_SESSION_ID", LEAD_HANDLE)
    extra_env.setdefault("TERM_PROGRAM", "iTerm.app")
    run("lead-start", "lead-1", "--project", "proj", **extra_env)


def spawn(env, *flags, **extra_env):
    return run("spawn", str(env / "wt"), "t", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1",
               *flags, **extra_env)


# ── iterm_pyapi.create_adjacent_tab: every reason, in-process ────────────────────────────────────
def test_pyapi_no_handle():
    assert iterm_pyapi.create_adjacent_tab(None) == (None, "no lead iTerm handle")


def test_pyapi_module_missing():  # conftest leaves FAKE_ITERM2 unset → the fake raises ImportError
    assert iterm_pyapi.create_adjacent_tab(LEAD_HANDLE) == (None, "iterm2 python module not installed")


def test_pyapi_timeout(monkeypatch):
    monkeypatch.setenv("FAKE_ITERM2", "hang")
    assert iterm_pyapi.create_adjacent_tab(LEAD_HANDLE, timeout=0.2) == (None, "python api timed out after 0.2s")


def test_pyapi_refused(monkeypatch):
    monkeypatch.setenv("FAKE_ITERM2", "refuse")
    sid, why = iterm_pyapi.create_adjacent_tab(LEAD_HANDLE)
    assert sid is None and why.startswith("python api unavailable (ConnectionRefusedError")


def test_pyapi_stale_handle(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_ITERM2", "ok")
    monkeypatch.setenv("FAKE_ITERM2_LAYOUT", layout([[OTHER]]))
    assert iterm_pyapi.create_adjacent_tab(LEAD_HANDLE) == (None, "stale lead handle: no iTerm tab holds session 11111111")
    assert created_tabs(tmp_path) == []


def test_pyapi_adjacent_in_the_leads_own_window(monkeypatch, tmp_path):
    """Lead is tab 2 of the SECOND window (split pane, not the first session): the tab goes to that
    window at index 3 — right of the lead, not the end of the bar, not the front window."""
    monkeypatch.setenv("FAKE_ITERM2", "ok")
    monkeypatch.setenv("FAKE_ITERM2_LAYOUT", layout([[OTHER]], [["a"], ["b"], ["c", LEAD_UUID], ["d"]]))
    assert iterm_pyapi.create_adjacent_tab(LEAD_HANDLE) == (NEW, None)
    assert iterm_pyapi.try_create_adjacent_tab(LEAD_HANDLE) == NEW
    assert created_tabs(tmp_path) == [{"window": 1, "index": 3}] * 2


# ── iterm.spawn: which AppleScript it runs and what placement it reports ─────────────────────────
@pytest.fixture
def osa(monkeypatch):
    """In-process run_osascript stub: records the script, answers with `lines`."""
    calls = {"lines": "OK\nNEWSID\nfront\n"}

    def fake(script, timeout=None):
        calls["script"] = script
        return subprocess.CompletedProcess([], 0, stdout=calls["lines"], stderr="")
    monkeypatch.setattr(iterm, "run_osascript", fake)
    return calls


def ispawn(tmp_path, **kw):
    args = dict(session_id="s", model="m", extension="e", rules="r", home="h", lead="l", inbox="i", resume=False)
    return iterm.spawn(str(tmp_path), str(tmp_path / "p.md"), "[Exec] s", str(tmp_path / "pid"), args,
                       rename_delay=0, **kw)


def test_spawn_adjacent_targets_the_new_tab_by_id(tmp_path, osa, monkeypatch):
    seen = []
    monkeypatch.setattr(iterm_pyapi, "create_adjacent_tab", lambda h: seen.append(h) or (NEW, None))
    r = ispawn(tmp_path, lead_handle=LEAD_HANDLE)
    assert r["ok"] and r["placement"] == "adjacent" and seen == [LEAD_HANDLE]
    assert f'(id of s) is "{NEW}"' in osa["script"] and "create tab" not in osa["script"]


@pytest.mark.parametrize("found, suffix", [(True, ""), (False, "; lead window not found, opened in the front window")])
def test_spawn_degrades_to_end_of_bar_with_the_reason(tmp_path, osa, monkeypatch, found, suffix):
    monkeypatch.setattr(iterm_pyapi, "create_adjacent_tab", lambda h: (None, "python api timed out after 2s"))
    osa["lines"] = "OK\nNEWSID\nfront\n" + ("lead\n" if found else "\n")
    r = ispawn(tmp_path, lead_handle=LEAD_HANDLE)
    assert r["ok"] and r["placement"] == f"end-of-bar (python api timed out after 2s{suffix})"
    assert "tell leadWindow to set newTab to (create tab" in osa["script"]


def test_spawn_without_a_handle_never_tries_the_api(tmp_path, osa, monkeypatch):
    monkeypatch.setattr(iterm_pyapi, "create_adjacent_tab", lambda h: pytest.fail("called"))
    r = ispawn(tmp_path)
    assert r["ok"] and r["placement"] == "end-of-bar (no lead iTerm handle)"
    assert "tell current window to set newTab" in osa["script"] and "leadWindow" not in osa["script"]


@pytest.mark.parametrize("found, want", [(True, "pane"),
                                         (False, "end-of-bar (pane: lead session not found, opened a tab in the front window)")])
def test_spawn_pane_splits_the_leads_session(tmp_path, osa, monkeypatch, found, want):
    monkeypatch.setattr(iterm_pyapi, "create_adjacent_tab", lambda h: pytest.fail("pane never needs the api"))
    osa["lines"] = "OK\nNEWSID\nfront\n" + ("lead\n" if found else "")
    r = ispawn(tmp_path, lead_handle=LEAD_HANDLE, layout="pane")
    assert r["ok"] and r["placement"] == want
    assert "tell leadSession to set targetSession to (split vertically" in osa["script"]


def test_spawn_failure_still_reports_not_ok(tmp_path, osa, monkeypatch):
    monkeypatch.setattr(iterm_pyapi, "create_adjacent_tab", lambda h: (None, "iterm2 python module not installed"))
    osa["lines"] = "NOTARGET\n\nfront\n"
    r = ispawn(tmp_path, lead_handle=LEAD_HANDLE)
    assert not r["ok"] and r["reason"] == "no-target"


# ── bin/pilead end to end ────────────────────────────────────────────────────────────────────────
def test_lead_start_records_and_refreshes_the_handle(env):
    lead_start()
    p = home(env) / "leads" / "lead-1.json"
    rec = json.loads(p.read_text())
    assert rec["iterm_handle"] == LEAD_HANDLE and rec["terminal"] == "iTerm.app"
    lead_start(ITERM_SESSION_ID=f"w1t0p0:{OTHER}", TERM_PROGRAM="WezTerm")
    again = json.loads(p.read_text())
    assert again["iterm_handle"] == f"w1t0p0:{OTHER}" and again["terminal"] == "WezTerm"
    assert again["started"] == rec["started"]


def test_spawn_adjacent(env):
    lead_start()
    r = spawn(env, FAKE_ITERM2="ok", FAKE_ITERM2_LAYOUT=layout([[OTHER]], [["x"], [LEAD_UUID], ["y"]]))
    assert r.stdout.splitlines()[-1] == "placement=adjacent"
    assert created_tabs(env) == [{"window": 1, "index": 2}]
    scripts = osa_scripts(env)
    assert f'(id of s) is "{NEW}"' in scripts and "create tab" not in scripts
    sid = r.stdout.split()[1]
    assert json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["layout"] == "tab"


@pytest.mark.parametrize("mode, extra, reason", [
    ("missing", {}, "iterm2 python module not installed"),
    ("hang", {}, "python api timed out after 2s"),
    ("ok", {"FAKE_ITERM2_LAYOUT": layout([[OTHER]])}, "stale lead handle: no iTerm tab holds session 11111111"),
    ("refuse", {}, "python api unavailable (ConnectionRefusedError: fake iterm2: python api disabled)"),
])
def test_spawn_degrades_but_succeeds(env, mode, extra, reason):
    lead_start()
    r = spawn(env, FAKE_ITERM2=mode, FAKE_OSA_LEAD="1", **extra)
    assert r.returncode == 0 and r.stdout.startswith("spawned t-")
    assert r.stdout.splitlines()[-1] == f"placement=end-of-bar ({reason})"
    assert created_tabs(env) == []
    assert "tell leadWindow to set newTab to (create tab" in osa_scripts(env)  # the lead's window, at the end


def test_spawn_lead_not_in_any_window_says_so(env):
    lead_start()
    r = spawn(env)  # FAKE_OSA_LEAD unset: AppleScript didn't find the lead either
    assert r.stdout.splitlines()[-1] == ("placement=end-of-bar (iterm2 python module not installed; "
                                         "lead window not found, opened in the front window)")


def test_spawn_lead_outside_iterm(env):
    lead_start(ITERM_SESSION_ID="")
    assert json.loads((home(env) / "leads" / "lead-1.json").read_text())["iterm_handle"] is None
    r = spawn(env, FAKE_ITERM2="ok")
    assert r.stdout.splitlines()[-1] == "placement=end-of-bar (no lead iTerm handle)"
    assert "leadWindow" not in osa_scripts(env)


def test_live_handle_beats_a_stale_recorded_one_when_the_caller_is_the_lead(env):
    lead_start(ITERM_SESSION_ID=f"w0t0p0:{OTHER}")  # recorded before an iTerm restart re-id'd the tab
    lay = layout([["x", LEAD_UUID]])
    r = spawn(env, FAKE_ITERM2="ok", FAKE_ITERM2_LAYOUT=lay, PI_SESSION_ID="lead-1", ITERM_SESSION_ID=LEAD_HANDLE)
    assert r.stdout.splitlines()[-1] == "placement=adjacent"
    # …but only for the lead itself: another caller (an executor's own bash) gets the recorded handle
    r = spawn(env, FAKE_ITERM2="ok", FAKE_ITERM2_LAYOUT=lay, PI_SESSION_ID="exec-9", ITERM_SESSION_ID=LEAD_HANDLE)
    assert r.stdout.splitlines()[-1].startswith("placement=end-of-bar (stale lead handle")


def test_spawn_pane_and_tab_flags(env):
    lead_start()
    r = spawn(env, "--pane", FAKE_ITERM2="ok", FAKE_ITERM2_LAYOUT=layout([[LEAD_UUID]]), FAKE_OSA_LEAD="1")
    assert r.stdout.splitlines()[-1] == "placement=pane"
    assert "split vertically" in osa_scripts(env) and created_tabs(env) == []
    sid = r.stdout.split()[1]
    assert json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["layout"] == "pane"
    r = spawn(env, "--tab", FAKE_ITERM2="ok", FAKE_ITERM2_LAYOUT=layout([[LEAD_UUID]]))
    assert r.stdout.splitlines()[-1] == "placement=adjacent" and created_tabs(env) == [{"window": 0, "index": 1}]
    r = spawn(env, "--pane", "--tab", check=False)
    assert r.returncode == 2 and "not allowed with" in r.stderr


def test_send_relaunch_reuses_handle_and_layout(env):
    lead_start()
    sid = spawn(env, "--pane", FAKE_OSA_LEAD="1").stdout.split()[1]
    run("close", sid)
    (env / "osa.log").write_text("")
    r = run("send", sid, str(env / "packet.md"), FAKE_OSA_LEAD="1")
    assert r.stdout.splitlines() == [f"sent {sid} packet 0002", "placement=pane"]
    assert "split vertically" in osa_scripts(env)

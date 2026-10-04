"""`pilead focus <name>` (lib/pilead/verbs/focus.py), driven through bin/pilead. The terminal is a recording
`osascript` stub answering $FAKE_TAB and a `ps` stub saying whether iTerm runs; no real tab is reached. Focus reads
state and asks the terminal: home is byte-identical after every run."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
UUID_E = "11111111-2222-3333-4444-555555555555"
UUID_L = "AAAAAAAA-0000-4000-8000-0000000000AA"
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
echo "${FAKE_TAB:-false}"
"""
PS = """#!/bin/sh
if [ "$1" = "-axo" ]; then
  [ -n "$FAKE_ITERM_RUNNING" ] && echo /Applications/iTerm.app/Contents/MacOS/iTerm2
  exit 0
fi
exec /bin/ps "$@"
"""
RESUME_HINT = ("no open tab has its handle. pilead resume {s} reopens it with its conversation; pilead restart {s} "
               "runs its packet again.")
LEAD_HINT = ("no open tab has its handle. Run pilead lead-start again in that lead's tab to record the tab it is in "
             "now.")


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "true")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "pi-lead-home"
    (h / "leads").mkdir(parents=True)
    return h


def lead(home, sid, project, handle=f"w0t0p0:{UUID_L}", **extra):
    (home / "leads" / f"{sid}.json").write_text(json.dumps(
        {"sid": sid, "project": project, "iterm_handle": handle, "terminal": "iTerm.app", **extra}))


def executor(home, sid, handle=f"w0t1p0:{UUID_E}", backend="iterm", tab=None):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    meta = {"sid": sid, "lead": "lead-1", "label": f"[Exec] {sid}", "backend": backend}
    if tab:
        meta["tab"] = tab
    (d / "meta.json").write_text(json.dumps(meta))
    if handle:
        (d / "iterm-id").write_text(handle + "\n")


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob("*"))}


def focus(home, name, **env):
    before = tree(home)
    r = subprocess.run([str(PILEAD), "focus", name, "--home", str(home)], capture_output=True, text=True,
                       env={**os.environ, **env})
    assert tree(home) == before, "focus wrote under home"
    return r


def scripts(tmp_path):
    p = tmp_path / "osa.log"
    return [s for s in p.read_text().split("=====\n") if s.strip()] if p.exists() else []


def no_tab_mutation(tmp_path):
    for s in scripts(tmp_path):
        assert "write text" not in s and "close" not in s and "create tab" not in s


# ── what <name> names ─────────────────────────────────────────────────────────────────────────────
def test_an_executor_by_its_sid_is_focused_by_handle(home, tmp_path):
    executor(home, "s-1")
    r = focus(home, "s-1")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused s-1 (tab '[Exec] s-1')\n", "")
    (s,) = scripts(tmp_path)
    assert UUID_E in s and "select" in s
    no_tab_mutation(tmp_path)


def test_an_executor_with_no_iterm_id_is_focused_by_its_meta_tab(home, tmp_path):
    executor(home, "s-1", handle=None, tab=f"w0t1p0:{UUID_E}")
    r = focus(home, "s-1")
    assert r.returncode == 0 and UUID_E in scripts(tmp_path)[0]


def test_a_lead_by_its_sid_is_focused_by_handle(home, tmp_path):
    lead(home, "lead-1", "proj")
    r = focus(home, "lead-1")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused lead proj (lead-1)\n", "")
    (s,) = scripts(tmp_path)
    assert UUID_L in s and "select" in s
    no_tab_mutation(tmp_path)


def test_a_lead_by_its_project_trimmed_and_in_any_case(home, tmp_path):
    lead(home, "lead-1", "My Proj ")
    lead(home, "lead-2", "other")
    r = focus(home, "  my proj")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused lead My Proj  (lead-1)\n", "")


def test_a_project_held_by_two_leads_is_refused(home, tmp_path):
    lead(home, "lead-1", "proj")
    lead(home, "lead-2", "Proj")
    r = focus(home, "proj")
    assert (r.returncode, r.stdout, r.stderr) == (
        2, "", "pilead: 'proj' names 2 leads: lead-1, lead-2 — give the sid\n")
    assert scripts(tmp_path) == []


def test_nothing_named(home, tmp_path):
    lead(home, "lead-1", "proj")
    r = focus(home, "nobody")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: no session or lead named 'nobody'\n")
    assert scripts(tmp_path) == []


def test_an_executor_sid_wins_over_a_lead_project(home, tmp_path):
    executor(home, "s-1")
    lead(home, "lead-1", "s-1")
    assert focus(home, "s-1").stdout == "focused s-1 (tab '[Exec] s-1')\n"


@pytest.mark.parametrize("kind", ["executor", "lead"])
def test_a_record_that_cannot_be_read(home, tmp_path, kind):
    if kind == "executor":
        (home / "sessions" / "s-1").mkdir(parents=True)
        (home / "sessions" / "s-1" / "meta.json").write_text("{not json")
        name = "s-1"
    else:
        (home / "leads" / "lead-1.json").write_text("[1, 2]")
        name = "lead-1"
    r = focus(home, name)
    assert (r.returncode, r.stdout, r.stderr) == (1, "", f"pilead: the record of '{name}' cannot be read\n")
    assert scripts(tmp_path) == []


def test_an_executor_on_another_backend(home, tmp_path):
    executor(home, "s-1", backend="terminal")
    r = focus(home, "s-1")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: s-1 is not in an iTerm2 tab\n")
    assert scripts(tmp_path) == []


# ── the terminal ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["executor", "lead"])
def test_iterm_not_running_asks_nothing(home, tmp_path, kind):
    executor(home, "s-1")
    lead(home, "lead-1", "proj")
    r = focus(home, "s-1" if kind == "executor" else "proj", FAKE_ITERM_RUNNING="")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: iTerm2 is not running\n")
    assert scripts(tmp_path) == []


def test_no_handle_asks_nothing(home, tmp_path):
    executor(home, "s-1", handle=None)
    lead(home, "lead-1", "proj", handle=None)
    r = focus(home, "s-1")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: could not focus s-1 — "
                                                  + RESUME_HINT.format(s="s-1") + "\n")
    r = focus(home, "lead-1")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: could not focus lead lead-1 — " + LEAD_HINT + "\n")
    assert scripts(tmp_path) == []


def test_no_tab_matched(home, tmp_path):
    executor(home, "s-1")
    lead(home, "lead-1", "proj")
    r = focus(home, "s-1", FAKE_TAB="false")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: could not focus s-1 — "
                                                  + RESUME_HINT.format(s="s-1") + "\n")
    r = focus(home, "proj", FAKE_TAB="false")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: could not focus lead lead-1 — " + LEAD_HINT + "\n")
    no_tab_mutation(tmp_path)


def test_a_name_that_climbs_out_of_home_names_nothing(tmp_path):
    root = tmp_path / "outer"
    home = root / "a" / "b"
    (home / "leads").mkdir(parents=True)
    (home / "sessions").mkdir()
    # where `../../x` would point from sessions/ and from leads/
    (root / "a" / "x").mkdir(parents=True)
    (root / "a" / "x" / "meta.json").write_text(json.dumps({"sid": "x", "label": "evil", "backend": "iterm"}))
    (root / "a" / "x.json").write_text(json.dumps({"sid": "x", "project": "evil", "iterm_handle": f"w0t0p0:{UUID_L}"}))
    before = tree(tmp_path)
    r = subprocess.run([str(PILEAD), "focus", "../../x", "--home", str(home)], capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: no session or lead named '../../x'\n")
    assert tree(tmp_path) == before


def test_the_ledger_is_untouched(home, tmp_path):
    (home / "ledger.jsonl").write_text('{"ts": "x", "event": "spawned", "session_id": "s-1"}\n')
    executor(home, "s-1")
    for name in ("s-1", "nobody"):
        focus(home, name)
    assert (home / "ledger.jsonl").read_text() == '{"ts": "x", "event": "spawned", "session_id": "s-1"}\n'


# ── a unique id prefix (lib/pilead/names.py) ──────────────────────────────────────────────────────
def test_a_unique_prefix_of_eight_focuses_the_session_it_names(home, tmp_path):
    executor(home, "alpha-x-20261004-101010")
    executor(home, "beta-20261004-111111", handle=None)
    r = focus(home, "alpha-x-")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused alpha-x-20261004-101010 (tab '[Exec] alpha-x-20261004-101010')\n", "")
    (s,) = scripts(tmp_path)
    assert UUID_E in s and "select" in s
    lead(home, "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d", "proj")
    r = focus(home, "4c1d0e2a")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused lead proj (4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d)\n", "")


def test_an_ambiguous_prefix_gives_the_candidates_and_asks_nothing(home, tmp_path):
    executor(home, "alpha-x-20261004-101010")
    executor(home, "alpha-y-20261004-111111")
    r = focus(home, "alpha-")
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == ("pilead: ambiguous session 'alpha-' — matches multiple sessions:\n"
                        "  alpha-x-20261004-101010  (executor '-', -)\n"
                        "  alpha-y-20261004-111111  (executor '-', -)\n"
                        "pass the session id (or a unique prefix of it) instead\n")
    assert scripts(tmp_path) == []


# ── b12: a tmux handle is focused through tmux ───────────────────────────────────────────────────────
TMUX = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
[ -n "$FAKE_TMUX_DOWN" ] && exit 1
case "$*" in
  *"#{session_id}"*) echo '$1' ;;
  *select-pane*) [ -n "$FAKE_TMUX_NOPANE" ] && exit 1 ;;
esac
exit 0
"""


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    """A recording `tmux` stub in place of conftest's failing one (same fakebin dir); its log's lines."""
    p = tmp_path / "fakebin" / "tmux"
    p.write_text(TMUX)
    p.chmod(0o755)
    log = tmp_path / "tmux.log"
    monkeypatch.setenv("FAKE_TMUX_LOG", str(log))
    return lambda: log.read_text().splitlines() if log.exists() else []


def test_a_tmux_executor_is_focused_through_tmux(home, tmp_path, tmux):
    executor(home, "s-1", handle="tmux:%4", backend="tmux")
    r = focus(home, "s-1")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused s-1 (tab '[Exec] s-1')\n", "")
    assert any(ln.endswith("select-pane -t %4") for ln in tmux())
    assert all(" -L pilead-test-none-" in f" {ln}" for ln in tmux())  # conftest's dead socket, never the default
    assert scripts(tmp_path) == []


def test_a_tmux_lead_is_focused_through_tmux(home, tmp_path, tmux):
    lead(home, "lead-1", "proj", handle="tmux:%7", backend="tmux")
    r = focus(home, "proj")
    assert (r.returncode, r.stdout, r.stderr) == (0, "focused lead proj (lead-1)\n", "")
    assert any(ln.endswith("select-pane -t %7") for ln in tmux()) and scripts(tmp_path) == []


def test_no_tmux_server_answers(home, tmp_path, tmux):
    executor(home, "s-1", handle="tmux:%4", backend="tmux")
    r = focus(home, "s-1", FAKE_TMUX_DOWN="1")
    assert (r.returncode, r.stdout, r.stderr) == (1, "", "pilead: no tmux server answers\n")
    assert scripts(tmp_path) == []


def test_a_tmux_pane_that_cannot_be_selected(home, tmp_path, tmux):
    executor(home, "s-1", handle="tmux:%4", backend="tmux")
    r = focus(home, "s-1", FAKE_TMUX_NOPANE="1")
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr == "pilead: could not focus s-1 — " + RESUME_HINT.format(s="s-1") + "\n"

"""vendor/relay/backend.py: `select` across every step of its order (env dicts, never os.environ), the live
handle read from an env, `for_handle`, `is_live_handle` — and the suite's own pin (conftest RELAY_TERMINAL=iterm)
beating a `TMUX` a test sets."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "relay"))
sys.path.insert(0, str(ROOT / "lib"))
import backend  # noqa: E402
import iterm  # noqa: E402
import terminal_app  # noqa: E402
import tmux_backend  # noqa: E402

UUID = "0F2A9C4E-0000-4000-8000-000000000000"


@pytest.mark.parametrize("env,configured,want", [
    # 1. PILEAD_TERMINAL, else RELAY_TERMINAL — lower-cased, a registered name only
    ({"PILEAD_TERMINAL": "tmux", "RELAY_TERMINAL": "iterm"}, "iterm", tmux_backend),
    ({"PILEAD_TERMINAL": "ITERM", "TMUX": "/tmp/x,1,0"}, "tmux", iterm),
    ({"RELAY_TERMINAL": "Tmux"}, None, tmux_backend),
    ({"RELAY_TERMINAL": "terminal"}, None, terminal_app),
    ({"PILEAD_TERMINAL": "kitty", "RELAY_TERMINAL": "tmux"}, None, tmux_backend),
    ({"PILEAD_TERMINAL": "kitty", "RELAY_TERMINAL": "wezterm"}, None, iterm),
    # 2. configured: a registered name wins, "auto" and anything else fall through
    ({}, "tmux", tmux_backend),
    ({"TMUX": "/tmp/x,1,0"}, "iterm", iterm),
    ({"TMUX": "/tmp/x,1,0"}, "auto", tmux_backend),
    ({}, "nonsense", iterm),
    # 3. inside tmux — before the Terminal.app test
    ({"TMUX": "/tmp/x,1,0", "TERM_PROGRAM": "Apple_Terminal"}, None, tmux_backend),
    ({"TERM_PROGRAM": "tmux"}, None, tmux_backend),
    ({"TMUX": ""}, None, iterm),
    # 4. TERM_PROGRAM: Apple_Terminal → Terminal.app; anything else → iTerm
    ({"TERM_PROGRAM": "Apple_Terminal"}, None, terminal_app),
    ({"TERM_PROGRAM": "iTerm.app"}, None, iterm),
    ({}, None, iterm),
])
def test_select_order(env, configured, want):
    assert backend.select(env=env, configured=configured) is want


def test_select_without_arguments_reads_os_environ(monkeypatch):
    monkeypatch.delenv("RELAY_TERMINAL", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert backend.select() is iterm
    monkeypatch.setenv("TMUX", "/tmp/x,1,0")
    assert backend.select() is tmux_backend


@pytest.mark.parametrize("bk,env,want", [
    (tmux_backend, {"TMUX_PANE": "%12"}, "tmux:%12"),
    (tmux_backend, {"TMUX_PANE": "12"}, None),
    (tmux_backend, {}, None),
    (iterm, {"ITERM_SESSION_ID": f"w0t1p0:{UUID}"}, f"w0t1p0:{UUID}"),
    (iterm, {"ITERM_SESSION_ID": UUID}, None),
    (iterm, {"ITERM_SESSION_ID": ""}, None),
    (iterm, {"TERM_SESSION_ID": f"w0t1p0:{UUID}"}, None),  # pi-lead never reads $TERM_SESSION_ID
    (terminal_app, {"ITERM_SESSION_ID": f"w0t1p0:{UUID}", "TMUX_PANE": "%1"}, None),
])
def test_live_handle_from_env(bk, env, want):
    assert backend.live_handle_from_env(bk, env=env) == want


def test_live_handle_from_env_defaults_to_the_selected_backend():
    env = {"TMUX": "/tmp/x,1,0", "TMUX_PANE": "%3", "ITERM_SESSION_ID": f"w0t0p0:{UUID}"}
    assert backend.live_handle_from_env(env=env) == "tmux:%3"
    assert backend.live_handle_from_env(backend.by_name("iterm"), env=env) == f"w0t0p0:{UUID}"


@pytest.mark.parametrize("h,want", [
    ("tmux:%4", tmux_backend), (f"w1t2p0:{UUID}", iterm), ("w0t0p0:FAKE-UUID", iterm),
    ("tmux:4", None), ("twid:1", None), (UUID, None), ("", None), (None, None),
])
def test_for_handle(h, want):
    assert backend.for_handle(h) is want


@pytest.mark.parametrize("v,want", [
    ("tmux:%4", True), (f"w0t0p0:{UUID}", True), ("w0t0p0:FAKE-UUID", False), (UUID, False),
    ("tmux:%", False), ("", False), (None, False),
])
def test_is_live_handle(v, want):
    assert backend.is_live_handle(v) is want


def test_by_name_registers_tmux():
    assert backend.by_name("TMUX") is tmux_backend and backend.by_name("terminal") is terminal_app


def test_suite_pin_beats_tmux(monkeypatch):
    """conftest sets RELAY_TERMINAL=iterm for every test: a TMUX a test sets does not move the suite to tmux."""
    monkeypatch.setenv("TMUX", "/tmp/x,1,0")
    monkeypatch.setenv("TMUX_PANE", "%9")
    from pilead import launch
    assert launch._backend_for({}) is iterm
    assert launch._backend_for({"backend": "tmux"}) is tmux_backend
    assert launch._backend_for({"iterm_handle": "tmux:%2"}) is tmux_backend

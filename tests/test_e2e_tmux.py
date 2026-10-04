"""Real-tmux end-to-end test of vendor/relay/tmux_backend.py, against tests/fake_pi. The ONE real thing it drives
is a PRIVATE tmux server `pilead-e2e-<pid>` (PILEAD_TMUX_SOCKET), started detached with its own config
(`default-shell /bin/sh`, so no human shell rc runs inside it) and killed in the fixture's `finally`. Every tmux
command here carries `-L <that name>`; the default server is never addressed. Skipped when `tmux` is not on PATH.
(claude-relay 0.5.6 tests/test_e2e_tmux.py is the model; pi-lead's spawn types nothing after the launch line.)"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "relay"))
import tmux_backend  # noqa: E402

# Found at import, before conftest's autouse fixture puts its failing `tmux` stub first on PATH.
REAL_TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(REAL_TMUX is None, reason="tmux not installed")

SID = "e2e-101010"


def _poll(fn, timeout=10, interval=0.2):
    deadline = time.time() + timeout
    result = fn()
    while not result and time.time() < deadline:
        time.sleep(interval)
        result = fn()
    return result


@pytest.fixture
def tmux_server(tmp_path, monkeypatch):
    """A private detached tmux server with its own config. conftest's autouse fixture has already removed the
    ambient TMUX/TMUX_PANE and set a dead PILEAD_TMUX_SOCKET; setting the socket here (after it) scopes every
    tmux_backend call to this server."""
    name = f"pilead-e2e-{os.getpid()}"
    conf = tmp_path / "tmux.conf"
    conf.write_text("set -g default-shell /bin/sh\n")
    monkeypatch.setenv("PILEAD_TMUX_SOCKET", name)
    # only the real `tmux` goes in front of conftest's stub (a symlink in a dir of its own: nothing else on the
    # real binary's PATH entry — a real `pi`, say — moves forward)
    real = tmp_path / "real-tmux"
    real.mkdir()
    (real / "tmux").symlink_to(REAL_TMUX)
    monkeypatch.setenv("PATH", str(real) + os.pathsep + os.environ["PATH"])
    sock = None
    try:
        # The panes' interactive /bin/sh (bash in sh mode on macOS) keeps a history file under $HOME: the server
        # gets a HOME and HISTFILE of its own, so nothing typed in a pane lands in the human's own history.
        own_home = tmp_path / "e2e-home"
        own_home.mkdir()
        senv = {**os.environ, "HOME": str(own_home), "HISTFILE": str(own_home / ".sh_history")}
        subprocess.run(["tmux", "-L", name, "-f", str(conf), "new-session", "-d", "-s", "e2e", "-x", "200",
                        "-y", "50"], check=True, capture_output=True, timeout=10, env=senv)
        sock = subprocess.run(["tmux", "-L", name, "display-message", "-p", "#{socket_path}"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        yield name
    finally:
        subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, timeout=10)
        # tmux leaves the socket file behind; remove this server's own (its name is ours alone)
        if sock and Path(sock).name == name:
            Path(sock).unlink(missing_ok=True)


def _display(pane, fmt):
    return (tmux_backend._tmux(["display-message", "-p", "-t", pane, fmt]).stdout or "").strip()


def _opt(pane, option):
    return (tmux_backend._tmux(["show-options", "-w", "-v", "-t", pane, option]).stdout or "").strip()


def _capture(pane):
    return tmux_backend._tmux(["capture-pane", "-p", "-t", pane]).stdout or ""


def test_tmux_backend_against_a_private_server(tmux_server, tmp_path):
    fakebin = tmp_path / "e2e-bin"
    fakebin.mkdir()
    shutil.copy(ROOT / "tests" / "fake_pi", fakebin / "pi")
    (fakebin / "pi").chmod(0o755)
    log = tmp_path / "e2e-pi.log"
    sdir = tmp_path / "sessions" / SID
    sdir.mkdir(parents=True)
    packet = sdir / "packet-0001.md"
    packet.write_text("e2e tmux packet\n")
    pidfile = sdir / "pid"
    idfile = sdir / "iterm-id"
    pi_args = dict(session_id=SID, model="anthropic/claude-sonnet-5", extension="/ext/pi-lead.ts",
                   rules=str(packet), home=str(tmp_path / "h"), lead="lead-1", inbox=str(sdir / "inbox.md"))

    res = tmux_backend.spawn(str(tmp_path), str(packet), "[Exec] e2e", str(pidfile), pi_args,
                             env_prefix=f'export PATH="{fakebin}:$PATH" && export FAKE_PI_LOG="{log}" && ',
                             iterm_id_file=str(idfile), tab_color=(1, 2, 3), type_name=True)
    assert res["ok"] is True and res["reason"] == "ok", res
    handle = res["session_id"]
    assert handle.startswith("tmux:%") and idfile.read_text() == handle
    assert res["placement"] == "end-of-bar (no lead tmux handle)"
    pane = handle[len("tmux:"):]

    assert _poll(pidfile.exists), _capture(pane)
    assert int(pidfile.read_text().strip()) > 0
    assert _poll(lambda: log.exists() and log.read_text().strip()), _capture(pane)
    lines = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    argv = lines[0]["argv"]
    assert argv[argv.index("--session-id") + 1] == SID

    # liveness / tty: the pane stays (its shell remains after fake_pi exits)
    assert tmux_backend.running() is True
    assert tmux_backend.exists_by_id(handle) is True and tmux_backend.is_alive("x", handle) is True
    tty = tmux_backend.tty_by_id(handle)
    assert tty and tty.startswith("/dev/")

    # spawn-time name and colour
    assert _display(pane, "#{window_name}") == "[Exec] e2e"
    assert _opt(pane, "automatic-rename") == "off"
    assert _opt(pane, "window-status-style") == "bg=#010203"
    assert tmux_backend.window_is_own(handle) is True

    # rename_by_id renames the window and types nothing
    assert tmux_backend.rename_by_id(handle, "[Exec] renamed") is True
    assert _poll(lambda: _display(pane, "#{window_name}") == "[Exec] renamed")
    assert _opt(pane, "automatic-rename") == "off"
    time.sleep(0.5)
    assert "/rename" not in _capture(pane) and "/name" not in _capture(pane)

    assert tmux_backend.paint_tab(handle, (255, 0, 16)) is True
    assert _opt(pane, "window-status-style") == "bg=#ff0010"

    assert tmux_backend.focus("[Exec] e2e", handle) is True  # a detached server: no client to switch, no error
    assert tmux_backend.notify(handle, "pi-lead #1", "e2e") in (True, False)  # no client: either answer, no raise
    assert tmux_backend.version().startswith("tmux ")

    assert tmux_backend.close_by_id(handle) is True
    assert _poll(lambda: tmux_backend.exists_by_id(handle) is False)

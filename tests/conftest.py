import itertools
import os
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FAKE_ITERM2_DIR = TESTS / "fake_iterm2"
sys.path.insert(0, str(ROOT / "vendor" / "relay"))

# The recording `osascript` every test gets (tests/test_cli_core.py and others import it from here): a
# real tab's shell never sees the test's env, so the only safe place to stop a real tab is before
# `osascript` runs. A test file needs a fixture of its own only to change what the stub answers.
OSA_STUB = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*) printf 'OK\\nFAKE-UUID\\nfront\\n' ;;
  *) echo true ;;
esac
"""
TMUX_STUB = """#!/bin/sh
echo "pilead tests: tmux is stubbed (only tests/test_e2e_tmux.py runs the real one)" >&2
exit 1
"""

_TMUX_SOCKETS = itertools.count(1)


@pytest.fixture(autouse=True)
def _pi_lead_env(tmp_path, monkeypatch):
    """Hermetic env: state under tmp, no notifications/tidy/real tabs, `pi` -> tests/fake_pi (tests/ is
    also on PATH, per the packet)."""
    monkeypatch.setenv("PI_LEAD_HOME", str(tmp_path / "pi-lead-home"))
    monkeypatch.setenv("PILEAD_NO_LINT", "1")  # existing tests send tiny packets; lint advice is opt-in per test
    monkeypatch.setenv("RELAY_NO_NOTIFY", "1")
    monkeypatch.setenv("RELAY_NO_TIDY", "1")
    # no tab colour is written to a terminal line a stub names (it may be a real /dev/ttysNNN); a test of
    # painting removes it for itself and names a plain file (lib/pilead/tabs.py `paint`)
    monkeypatch.setenv("PILEAD_NO_PAINT", "1")
    monkeypatch.setenv("RELAY_TERMINAL", "iterm")
    # No test runs inside the caller's tmux (b12): drop every tmux identity and backend override, and point
    # pi-lead's tmux backend at a socket no server listens on, so a tmux call a test makes by mistake fails
    # and can never reach a human's default server. A tmux test sets what it needs after this fixture.
    for var in ("TMUX", "TMUX_PANE", "PILEAD_TERMINAL", "RELAY_TMUX_SOCKET"):
        monkeypatch.delenv(var, raising=False)
    if os.environ.get("TERM_PROGRAM") == "tmux":
        monkeypatch.delenv("TERM_PROGRAM")
    monkeypatch.setenv("PILEAD_TMUX_SOCKET", f"pilead-test-none-{os.getpid()}-{next(_TMUX_SOCKETS)}")
    monkeypatch.setenv("FAKE_PI_LOG", str(tmp_path / "fake-pi.log"))
    # `pi` must resolve to fake_pi and NEVER the real binary: a tmp bin dir holds the `pi` symlink.
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "pi").symlink_to(TESTS / "fake_pi")
    # ...and `osascript` resolves to a recording stub, for EVERY test: no spawn/send can open a real tab.
    osa = bindir / "osascript"
    osa.write_text(OSA_STUB)
    osa.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))
    # ...and `tmux` by name is a stub that fails (b12): no test but tests/test_e2e_tmux.py runs the real one (that
    # test puts the real binary's directory in front for itself).
    tmux = bindir / "tmux"
    tmux.write_text(TMUX_STUB)
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + str(TESTS) + os.pathsep + os.environ.get("PATH", ""))
    # No test may place a tab next to the REAL terminal it runs in: drop the caller's own iTerm/pi
    # identity, and shadow the `iterm2` package with tests/fake_iterm2 (ImportError unless a test sets
    # FAKE_ITERM2) — PYTHONPATH for bin/pilead subprocesses, sys.path/sys.modules in-process. User
    # site-packages (where a real `pip3 install iterm2` lands) is off for subprocesses as well.
    for var in ("ITERM_SESSION_ID", "TERM_SESSION_ID", "PI_SESSION_ID", "FAKE_ITERM2"):
        monkeypatch.delenv(var, raising=False)
    # ...nor run as the caller's own lead or executor: no test has a calling lead unless it sets one.
    for var in ("PI_LEAD_SID", "PI_LEAD_ROLE", "PI_LEAD_LEAD", "PI_LEAD_INBOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYTHONPATH", str(FAKE_ITERM2_DIR))
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    # No test may read this machine's real pi session logs (lib/pilead/usage.py): pi's agent dir is an
    # empty dir under tmp, and a session-dir override from the caller's shell is dropped.
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.syspath_prepend(str(FAKE_ITERM2_DIR))
    monkeypatch.delitem(sys.modules, "iterm2", raising=False)
    yield
    sys.modules.pop("iterm2", None)

"""vendor/relay/platform_cmds.py (byte-identical to claude-relay d1cc0a7) and the vendored iterm `pids_on_tty`'s
args match on both platforms. `platform.system`, `shutil.which` and `subprocess.run` are replaced: nothing here
opens a file, copies to a clipboard or lists real processes."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "relay"))
import iterm  # noqa: E402
import platform_cmds  # noqa: E402


@pytest.fixture
def system(monkeypatch):
    def set_(name):
        monkeypatch.setattr(platform_cmds.platform, "system", lambda: name)
    return set_


@pytest.fixture
def runs(monkeypatch):
    calls = []
    state = {"rc": 0, "out": "", "raise": None}

    def run(argv, **kw):
        calls.append(list(argv))
        if state["raise"]:
            raise state["raise"]
        return subprocess.CompletedProcess(argv, state["rc"], state["out"], "")
    monkeypatch.setattr(platform_cmds.subprocess, "run", run)
    monkeypatch.setattr(iterm.subprocess, "run", run)
    return calls, state


@pytest.fixture
def which(monkeypatch):
    present = set()
    monkeypatch.setattr(platform_cmds.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    return present


def test_vendored_file_is_relays():
    """The vendored copy stays identical to relay's d1cc0a7 file (VENDOR.md): the header names relay."""
    text = (ROOT / "vendor" / "relay" / "platform_cmds.py").read_text()
    assert text.startswith('"""\nThe ONE platform seam: every OS-specific helper relay shells out to')


def test_open_path_macos(system, runs, which):
    calls, _ = runs
    system("Darwin")
    assert platform_cmds.open_path("/tmp/x.html") is True
    assert calls == [["open", "/tmp/x.html"]]


def test_open_path_linux_with_xdg_open(system, runs, which):
    calls, _ = runs
    system("Linux")
    which.add("xdg-open")
    assert platform_cmds.open_path("/tmp/x.html") is True
    assert calls == [["xdg-open", "/tmp/x.html"]]


def test_open_path_linux_without_xdg_open(system, runs, which):
    calls, _ = runs
    system("Linux")
    assert platform_cmds.open_path("/tmp/x.html") is False
    assert calls == []


def test_open_path_other_platform(system, runs, which):
    calls, _ = runs
    system("Windows")
    assert platform_cmds.open_path("/tmp/x.html") is False and calls == []


def test_open_path_failures_are_false(system, runs, which):
    calls, state = runs
    system("Darwin")
    state["rc"] = 1
    assert platform_cmds.open_path("/tmp/x.html") is False
    state["raise"] = OSError("no open")
    assert platform_cmds.open_path("/tmp/x.html") is False


def test_linux_helpers(which):
    which.update({"xdg-open", "notify-send"})
    assert platform_cmds.linux_helpers() == {"xdg-open": True, "xclip": False, "wl-copy": False, "notify-send": True}


def test_clipboard_argv(system, which):
    system("Linux")
    assert platform_cmds.clipboard_argv() is None
    which.add("wl-copy")
    assert platform_cmds.clipboard_argv() == ["wl-copy"]
    which.add("xclip")
    assert platform_cmds.clipboard_argv() == ["xclip", "-selection", "clipboard"]
    system("Darwin")
    assert platform_cmds.clipboard_argv() == ["pbcopy"]


# ── pids_on_tty: args= on both platforms ──────────────────────────────────────────────────────────
PS_LINUX = """\
  101 pts/3    /usr/bin/node /usr/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js --session-id x
  102 pts/3    -bash
  103 pts/3    /usr/local/bin/api --serve
  104 pts/3    /usr/local/bin/pi --session-id y
  105 ?        /usr/local/bin/pi
  106 pts/4    /usr/local/bin/pi
  107 pts/3    node --inspect /opt/tools/pi --x
  108 pts/3    node /opt/tools/spin.js
"""


def test_pids_on_tty_linux(system, runs):
    calls, state = runs
    system("Linux")
    state["out"] = PS_LINUX
    assert iterm.pids_on_tty("/dev/pts/3") == [101, 104, 107]
    assert calls == [["ps", "-eo", "pid=,tty=,args="]]
    assert iterm.pid_on_tty("/dev/pts/3") == 101
    assert iterm.pids_on_tty("/dev/pts/9") == []


PS_MAC = """\
  201 ttys010  /opt/homebrew/Cellar/node/24.0.0/bin/node /opt/homebrew/bin/pi --session-id a
  202 ttys010  -zsh
  203 ttys010  /usr/local/bin/api
  204 ??       /usr/local/bin/pi
  205 ttys011  pi
"""


def test_pids_on_tty_macos(system, runs):
    calls, state = runs
    system("Darwin")
    state["out"] = PS_MAC
    assert iterm.pids_on_tty("/dev/ttys010") == [201]
    assert calls == [["ps", "-axo", "pid=,tty=,args="]]
    assert iterm.pids_on_tty("/dev/ttys011") == [205]


def test_pids_on_tty_failures(system, runs):
    calls, state = runs
    system("Darwin")
    assert iterm.pids_on_tty("") == [] and iterm.pids_on_tty(None) == [] and calls == []
    state["rc"] = 1
    state["out"] = PS_MAC
    assert iterm.pids_on_tty("/dev/ttys010") == []
    state["raise"] = OSError("no ps")
    assert iterm.pids_on_tty("/dev/ttys010") == [] and iterm.pid_on_tty("/dev/ttys010") is None


@pytest.mark.parametrize("args,suffix,want", [
    ("/usr/local/bin/pi", "pi", True),
    ("pi --x", "pi", True),
    ("/usr/bin/api", "pi", False),
    ("/usr/bin/node /x/pi-coding-agent/dist/cli.js", "pi", True),
    ("/usr/bin/node /x/pi-coding-agent/dist/cli.js", "claude", False),
    ("bun run /x/pi", "pi", False),  # the first non-flag argument is `run`
    ("deno --allow-all /x/pi", "pi", True),
    ("node", "pi", False),
    ("", "pi", False),
    ("'unbalanced /usr/local/bin/pi", "pi", False),
])
def test_args_match(args, suffix, want):
    assert iterm._args_match(args, suffix) is want


def test_tty_name():
    assert iterm._tty_name("/dev/ttys010") == "ttys010" and iterm._tty_name("pts/3") == "pts/3"
    assert iterm._tty_name(None) == ""

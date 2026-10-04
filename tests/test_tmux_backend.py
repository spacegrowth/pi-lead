"""vendor/relay/tmux_backend.py through its one seam: `_tmux` is replaced by a recorder, so no test here runs
`tmux` (claude-relay's tests/test_backends.py tmux classes are the model). The real-tmux test is
tests/test_e2e_tmux.py."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "relay"))
import iterm  # noqa: E402
import tmux_backend  # noqa: E402

FOREIGN = ["w0t0p0:0F2A9C4E-0000-4000-8000-000000000000", "twid:1", "", None, "tmux:12", "tmux:%x"]
PI_ARGS = dict(session_id="topic-101010", model="anthropic/claude-sonnet-5", extension="/ext/pi-lead.ts",
               rules="/rules.md", home="/h", lead="lead-1", inbox="/h/sessions/topic-101010/inbox.md")


class Rec:
    """A recorder standing in for `tmux_backend._tmux`: every call's argv is kept; `answers` maps a
    predicate on the argv to (returncode, stdout); anything unmatched exits 0 with no output."""

    def __init__(self, answers=()):
        self.calls = []
        self.answers = list(answers)

    def __call__(self, args, timeout=tmux_backend.DEFAULT_TIMEOUT):
        args = [str(a) for a in args]
        self.calls.append(args)
        for pred, (rc, out) in self.answers:
            if pred(args):
                return subprocess.CompletedProcess(["tmux"] + args, rc, out, "" if rc == 0 else "boom")
        return subprocess.CompletedProcess(["tmux"] + args, 0, "", "")

    def verbs(self):
        return [c[0] for c in self.calls]


def disp(fmt, pane=None):
    """Predicate: a `display-message -p [-t pane] <fmt>` call."""
    def pred(a):
        return a[0] == "display-message" and a[-1] == fmt and (pane is None or pane in a)
    return pred


@pytest.fixture
def rec(monkeypatch):
    r = Rec()
    monkeypatch.setattr(tmux_backend, "_tmux", r)
    monkeypatch.setattr(tmux_backend.time, "sleep", lambda s: None)
    return r


# ── foreign / empty handles: False or None, and no tmux call ─────────────────────────────────────────
@pytest.mark.parametrize("h", FOREIGN)
def test_foreign_handle_runs_nothing(rec, h):
    assert tmux_backend.exists_by_id(h) is False
    assert tmux_backend.is_alive("x", h) is False
    assert tmux_backend.send("x", "nope", h) is False
    assert tmux_backend.press_enter("x", h) is False
    assert tmux_backend.rename_by_id(h, "n") is False
    assert tmux_backend.paint_tab(h, (1, 2, 3)) is False
    assert tmux_backend.window_is_own(h) is False
    assert tmux_backend.focus("x", h) is False
    assert tmux_backend.close_by_id(h) is False
    assert tmux_backend.close("x", h) is False
    assert tmux_backend.tty_by_id(h) is None
    assert tmux_backend.title_by_id(h) is None
    assert tmux_backend.notify(h, "t", "b") is False
    assert rec.calls == []


# ── the seam itself ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def argv_log(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(tmux_backend.subprocess, "run", run)
    return seen


def test_seam_escapes_a_trailing_semicolon_in_every_word(argv_log, monkeypatch):
    monkeypatch.delenv("PILEAD_TMUX_SOCKET", raising=False)
    monkeypatch.delenv("RELAY_TMUX_SOCKET", raising=False)
    tmux_backend._tmux(["rename-window", "-t", "%1;", "--", "lbl;", "mid;dle"])
    assert argv_log == [["tmux", "rename-window", "-t", "%1\\;", "--", "lbl\\;", "mid;dle"]]


@pytest.mark.parametrize("pilead,relay,want", [
    ("pp", "rr", ["-L", "pp"]),
    (None, "rr", ["-L", "rr"]),
    (None, None, []),
])
def test_seam_socket_order(argv_log, monkeypatch, pilead, relay, want):
    for var, val in (("PILEAD_TMUX_SOCKET", pilead), ("RELAY_TMUX_SOCKET", relay)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    tmux_backend._tmux(["list-sessions"])
    assert argv_log == [["tmux"] + want + ["list-sessions"]]


def test_seam_missing_binary_and_timeout_never_raise(monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(tmux_backend.subprocess, "run", missing)
    r = tmux_backend._tmux(["list-sessions"])
    assert r.returncode == 127 and "unavailable" in r.stderr

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)
    monkeypatch.setattr(tmux_backend.subprocess, "run", slow)
    r = tmux_backend._tmux(["list-sessions"])
    assert r.returncode == 1 and "timed out" in r.stderr
    assert tmux_backend.running() is False and tmux_backend.version() is None


# ── spawn ────────────────────────────────────────────────────────────────────────────────────────────
def _spawn(tmp_path, **kw):
    sdir = tmp_path / "sessions" / "topic-101010"
    sdir.mkdir(parents=True, exist_ok=True)
    kw.setdefault("iterm_id_file", str(sdir / "iterm-id"))
    res = tmux_backend.spawn(str(tmp_path / "wt"), "/h/packet.md", "[Exec] topic", str(sdir / "pid"), PI_ARGS, **kw)
    return res, sdir


def _create(rec):
    return [c for c in rec.calls if c[0] in ("new-window", "split-window")]


def test_spawn_new_window_without_lead(tmp_path, rec):
    rec.answers.append((lambda a: a[0] == "new-window", (0, "%7\n")))
    res, sdir = _spawn(tmp_path, tab_color=(1, 2, 3))
    assert res["ok"] is True and res["reason"] == "ok" and res["session_id"] == "tmux:%7"
    assert res["placement"] == "end-of-bar (no lead tmux handle)"
    (create,) = _create(rec)
    assert create[:4] == ["new-window", "-P", "-F", "#{pane_id}"] and "-a" not in create
    assert create[create.index("-n") + 1] == "[Exec] topic" and create[create.index("-c") + 1] == str(tmp_path / "wt")
    assert (sdir / "iterm-id").read_text() == "tmux:%7"
    boot = (sdir / "bootstrap.sh").read_text()
    assert '_relay_sid="$TMUX_PANE"' in boot
    want_line = (f"cd {tmp_path / 'wt'} && echo $$ > {sdir / 'pid'} && "
                 + iterm.build_pi_cmd("/h/packet.md", **PI_ARGS))
    assert want_line in boot.splitlines()
    # window named (automatic-rename off) and painted
    assert ["rename-window", "-t", "%7", "--", "[Exec] topic"] in rec.calls
    assert ["set-option", "-w", "-t", "%7", "automatic-rename", "off"] in rec.calls
    assert ["set-option", "-w", "-t", "%7", "window-status-style", "bg=#010203"] in rec.calls


@pytest.mark.parametrize("type_name", [True, False])
def test_spawn_types_only_the_launch_line(tmp_path, rec, type_name):
    rec.answers.append((lambda a: a[0] == "new-window", (0, "%7\n")))
    res, sdir = _spawn(tmp_path, type_name=type_name, rename_delay=0.5)
    assert res["ok"] is True
    literal = [c for c in rec.calls if c[0] == "send-keys" and "-l" in c]
    assert literal == [["send-keys", "-t", "%7", "-l", "--", f"sh {sdir / 'bootstrap.sh'} %7"]]
    assert not any("/rename" in w or "/name" in w for c in rec.calls for w in c)
    # sacrificial Enter, the line, its Enter — and nothing typed after it
    sends = [c for c in rec.calls if c[0] == "send-keys"]
    assert sends[0] == ["send-keys", "-t", "%7", "Enter"] and sends[-1] == ["send-keys", "-t", "%7", "Enter"]
    assert rec.calls[-1] == ["send-keys", "-t", "%7", "Enter"]


def test_spawn_tab_next_to_the_lead(tmp_path, rec):
    rec.answers += [(disp("#{window_id}", "%3"), (0, "@4\n")), (lambda a: a[0] == "new-window", (0, "%8\n"))]
    res, _ = _spawn(tmp_path, lead_handle="tmux:%3", layout="tab")
    (create,) = _create(rec)
    assert create[:3] == ["new-window", "-a", "-t"] and create[3] == "@4"
    assert res["placement"] == "adjacent"


def test_spawn_pane_splits_the_lead(tmp_path, rec):
    rec.answers += [(lambda a: a[0] == "split-window", (0, "%9\n")), (disp("#{window_panes}", "%9"), (0, "2\n"))]
    res, _ = _spawn(tmp_path, lead_handle="tmux:%3", layout="pane", tab_color=(9, 9, 9))
    (create,) = _create(rec)
    assert create[:4] == ["split-window", "-h", "-t", "%3"] and "-n" not in create
    assert res["placement"] == "pane" and res["session_id"] == "tmux:%9"
    # a split shares the lead's window: never renamed, never painted
    assert "rename-window" not in rec.verbs()
    assert not any("window-status-style" in c for c in rec.calls)


def test_spawn_lead_pane_not_found_falls_back_to_a_plain_window(tmp_path, rec):
    rec.answers += [(disp("#{window_id}", "%3"), (1, "")), (lambda a: a[0] == "new-window", (0, "%5\n"))]
    res, _ = _spawn(tmp_path, lead_handle="tmux:%3")
    (create,) = _create(rec)
    assert create[:2] == ["new-window", "-P"]
    assert res["placement"] == "end-of-bar (lead pane not found)"


def test_spawn_create_failure_is_script_failed(tmp_path, rec):
    rec.answers.append((lambda a: a[0] == "new-window", (1, "")))
    res, sdir = _spawn(tmp_path)
    assert res["ok"] is False and res["reason"] == "script-failed" and res["error"]
    assert not (sdir / "iterm-id").exists()
    assert "send-keys" not in rec.verbs()


def test_spawn_target_gone(tmp_path, rec):
    rec.answers += [(lambda a: a[0] == "new-window", (0, "%7\n")), (lambda a: a[0] == "send-keys", (1, ""))]
    res, _ = _spawn(tmp_path)
    assert res["ok"] is False and res["reason"] == "target-gone" and res["session_id"] == "tmux:%7"


# ── rename / paint / window_is_own ──────────────────────────────────────────────────────────────────
def test_rename_by_id_renames_the_window_and_types_nothing(rec):
    rec.answers.append((disp("#{window_panes}", "%4"), (0, "1\n")))
    assert tmux_backend.rename_by_id("tmux:%4", "[Exec] new") is True
    assert ["rename-window", "-t", "%4", "--", "[Exec] new"] in rec.calls
    assert ["set-option", "-w", "-t", "%4", "automatic-rename", "off"] in rec.calls
    assert "send-keys" not in rec.verbs()


def test_rename_by_id_leaves_a_split_pane_alone(rec):
    rec.answers.append((disp("#{window_panes}", "%4"), (0, "2\n")))
    assert tmux_backend.rename_by_id("tmux:%4", "x") is False
    assert rec.verbs() == ["display-message"]


def test_window_is_own(rec):
    rec.answers.append((disp("#{window_panes}", "%4"), (0, "1\n")))
    assert tmux_backend.window_is_own("tmux:%4") is True
    rec.answers.insert(0, (disp("#{window_panes}", "%5"), (0, "3\n")))
    assert tmux_backend.window_is_own("tmux:%5") is False
    rec.answers.insert(0, (disp("#{window_panes}", "%6"), (1, "")))
    assert tmux_backend.window_is_own("tmux:%6") is False


def test_paint_tab(rec):
    assert tmux_backend.paint_tab("tmux:%4", (255, 0, 16)) is True
    assert rec.calls == [["set-option", "-w", "-t", "%4", "window-status-style", "bg=#ff0010"]]


# ── focus / notify / close / liveness ───────────────────────────────────────────────────────────────
def test_focus_switches_client_only_for_another_session(rec):
    rec.answers += [(disp("#{session_id}", "%4"), (0, "$2\n")), (disp("#{session_id}"), (0, "$1\n"))]
    assert tmux_backend.focus("x", "tmux:%4") is True
    assert ["switch-client", "-t", "$2"] in rec.calls
    assert rec.calls[-2:] == [["select-window", "-t", "%4"], ["select-pane", "-t", "%4"]]


def test_focus_same_session_does_not_switch(rec):
    rec.answers += [(disp("#{session_id}"), (0, "$2\n"))]
    assert tmux_backend.focus("x", "tmux:%4") is True
    assert "switch-client" not in rec.verbs()


def test_focus_false_when_select_pane_fails(rec):
    rec.answers += [(disp("#{session_id}"), (0, "$2\n")), (lambda a: a[0] == "select-pane", (1, ""))]
    assert tmux_backend.focus("x", "tmux:%4") is False


def test_notify_doubles_hash_and_flattens_newlines(rec):
    assert tmux_backend.notify("tmux:%4", "pi-lead #1", "line\nsecond #x") is True
    assert rec.calls == [["display-message", "-t", "%4", "-d", "4000", "--", "pi-lead ##1 — line second ##x"]]


def test_close_by_id_kills_the_pane(rec):
    assert tmux_backend.close_by_id("tmux:%4") is True
    assert tmux_backend.close("x", "tmux:%4") is True
    assert rec.calls == [["kill-pane", "-t", "%4"]] * 2


def test_exists_tty_title(rec):
    rec.answers += [(lambda a: a[0] == "list-panes", (0, "%1\n%4\n")),
                    (disp("#{pane_tty}", "%4"), (0, "/dev/ttys012\n")),
                    (disp("#{pane_tty}", "%1"), (0, "weird\n")),
                    (disp("#{pane_title}", "%4"), (0, "pi — topic\n"))]
    assert tmux_backend.exists_by_id("tmux:%4") is True and tmux_backend.exists_by_id("tmux:%2") is False
    assert tmux_backend.is_alive("x", "tmux:%4") is True
    assert tmux_backend.tty_by_id("tmux:%4") == "/dev/ttys012" and tmux_backend.tty_by_id("tmux:%1") is None
    assert tmux_backend.title_by_id("tmux:%4") == "pi — topic"


def test_reexports():
    assert tmux_backend.pids_on_tty is iterm.pids_on_tty and tmux_backend.pid_on_tty is iterm.pid_on_tty
    assert tmux_backend.PI_BIN == "pi" and tmux_backend.NAME == "tmux"


# ── the bootstrap text (Acceptance 6) ───────────────────────────────────────────────────────────────
def test_bootstrap_default_and_tmux_guard():
    assert iterm.bootstrap_file_content("CMD") == (
        "#!/bin/sh\n"
        "# relay bootstrap — written per launch by relay (see scripts/iterm.py, §15b).\n"
        "# Invoked as: sh <this file> <intended terminal session id (iTerm session id, or a tmux pane id)>.\n"
        "# A copy run anywhere else (or with a truncated/missing id) prints one line and exits 0 —\n"
        "# inert by construction.\n"
        '_relay_sid="${ITERM_SESSION_ID:-$TERM_SESSION_ID}"\n'
        'if [ "x${_relay_sid##*:}" != "x$1" ] || [ "x$1" = "x" ]; then\n'
        '  echo "relay: ignored a mis-delivered bootstrap meant for session ${1:-<missing>}"\n'
        "  exit 0\n"
        "fi\n"
        "CMD\n")
    tm = iterm.bootstrap_file_content("CMD", sid_expr=tmux_backend.SID_EXPR)
    assert '_relay_sid="$TMUX_PANE"\n' in tm
    assert tm == iterm.bootstrap_file_content("CMD").replace("${ITERM_SESSION_ID:-$TERM_SESSION_ID}", "$TMUX_PANE")


# ── the tmux guard, run for real through a shell (relay 0.5.8's SHELLS: zsh skips where it is missing) ───
SHELLS = ["bash", pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed"))]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("pane,ran", [("%5", True), ("%6", False)])
def test_tmux_guard_runs_only_in_its_own_pane(tmp_path, shell, pane, ran):
    out = tmp_path / "ran"
    boot = iterm.write_bootstrap_file(str(tmp_path / "pid"), f"echo ok > {out}", sid_expr=tmux_backend.SID_EXPR)
    env = {"PATH": "/usr/bin:/bin", "TMUX_PANE": pane, "ITERM_SESSION_ID": "w0t0p0:%5"}
    r = subprocess.run([shell, "-c", f"sh {boot} %5"], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert out.exists() is ran
    if not ran:
        assert "ignored a mis-delivered bootstrap meant for session %5" in r.stdout

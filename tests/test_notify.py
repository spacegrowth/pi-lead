"""lib/pilead/notify.py: the banner (two tiers, the kill switch, the ledger event for a tier 1 that did not post)
and `claim` (announced once). In-process, homes under the test's temp directory. The stub `osascript` records its
arguments in a file; the tty is a plain file; a lookup through iTerm is a monkeypatch of the vendored `iterm`
functions (a real `/dev/tty…` is never opened)."""
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, notify  # noqa: E402

STUB = textwrap.dedent("""\
    #!/bin/sh
    { printf '%s\\n' "$@"; echo =====; } >> "$STUB_LOG"
    exit 0
    """)


def stub_dir(tmp_path, body=STUB):
    d = tmp_path / "stubbin"
    d.mkdir(exist_ok=True)
    (d / "osascript").write_text(body)
    (d / "osascript").chmod(0o755)
    return d


@pytest.fixture
def osa(tmp_path, monkeypatch):
    """The kill switch off and the recording stub first on PATH. Returns a function: the calls so far."""
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.delenv("PILEAD_NO_NOTIFY", raising=False)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("PATH", f"{stub_dir(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    def calls():
        if not log.exists():
            return []
        return [c.strip("\n").split("\n") for c in log.read_text().split("=====\n") if c.strip()]

    return calls


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def tree(root):
    """Every file under root with its bytes (the set of files and their contents)."""
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def osc(title, body):
    return f"\033]777;notify;{title};{body}\007"


def script(title, body):
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    return f'display notification "{q(body)}" with title "{q(title)}" sound name "Glass"'


# ── the kill switch ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("var", ["PILEAD_NO_NOTIFY", "RELAY_NO_NOTIFY"])
def test_kill_switch_posts_nothing_reads_nothing_writes_nothing(osa, tmp_path, home, monkeypatch, var):
    monkeypatch.setenv(var, "1")
    tty = tmp_path / "tty"
    before = tree(tmp_path)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"tty": str(tty), "iterm_handle": "w0t0p0:X"}, lead_sid="lead-1") is None
    assert osa() == [] and not tty.exists() and not home.exists()
    assert tree(tmp_path) == before


def test_an_empty_kill_switch_variable_is_off(osa, tmp_path, home, monkeypatch):
    monkeypatch.setenv("RELAY_NO_NOTIFY", "")
    monkeypatch.setenv("PILEAD_NO_NOTIFY", "")
    assert notify.banner(home, {}, "T", "S", "M") == "osascript"
    assert len(osa()) == 1


# ── tier 1 ──────────────────────────────────────────────────────────────────────────────────────
def test_tier_1_with_a_tty_in_the_record_writes_exactly_the_osc_sequence(osa, tmp_path, home):
    tty = tmp_path / "tty"
    tier = notify.banner(home, {"notify_via": "auto"}, "pi-lead · proj", "exec-a reported", "line one\nline\ttwo\033 \007end",
                         lead_rec={"tty": str(tty)}, lead_sid="lead-1")
    assert tier == "tty"
    assert tty.read_text() == osc("pi-lead · proj", "exec-a reported — line one line\ttwo end")
    assert osa() == [] and not (home / "ledger.jsonl").exists()


def test_title_line_breaks_and_escape_characters_are_removed(osa, tmp_path, home):
    tty = tmp_path / "tty"
    notify.banner(home, {}, "a\nb\033c\007d", "s", "m", lead_rec={"tty": str(tty)}, lead_sid="l")
    assert tty.read_text() == osc("a bcd", "s — m")


@pytest.mark.parametrize("via", ["osascript", "terminal-notifier"])
def test_notify_via_osascript_skips_tier_1(osa, tmp_path, home, via):
    tty = tmp_path / "tty"
    tier = notify.banner(home, {"notify_via": via}, 'ti"tle\\', "sub", 'say "hi" \\ there',
                         lead_rec={"tty": str(tty), "iterm_handle": "w0t0p0:X"}, lead_sid="lead-1")
    assert tier == "osascript"
    assert osa() == [["-e", script('ti"tle\\', 'sub — say "hi" \\ there')]]
    assert not tty.exists() and not home.exists()


@pytest.mark.parametrize("via", [None, "auto", "bogus", 3, ""])
def test_any_other_notify_via_reads_as_auto(osa, tmp_path, home, via):
    tty = tmp_path / "tty"
    cfg = {} if via is None else {"notify_via": via}
    assert notify.banner(home, cfg, "T", "S", "M", lead_rec={"tty": str(tty)}, lead_sid="l") == "tty"
    assert osa() == []


def test_a_tty_that_cannot_be_written_falls_to_tier_2_and_is_ledgered(osa, tmp_path, home):
    tty = tmp_path / "a-directory"
    tty.mkdir()
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"tty": str(tty)}, lead_sid="lead-1") == "osascript"
    assert osa() == [["-e", script("T", "S — M")]]
    recs = ledger.read(home, event="banner_fallback")
    assert [(r["session_id"], r["reason"]) for r in recs] == [("lead-1", "write-failed")]


@pytest.mark.parametrize("rec", [None, {}, {"tty": None, "iterm_handle": None}, {"tty": "", "iterm_handle": ""},
                                 {"tty": 7, "iterm_handle": []}, "text"])
def test_no_tty_and_no_handle_goes_to_tier_2_without_a_ledger_event(osa, home, rec):
    assert notify.banner(home, {}, "T", "S", "M", lead_rec=rec, lead_sid="lead-1") == "osascript"
    assert osa() == [["-e", script("T", "S — M")]]
    assert not home.exists()


def test_an_empty_tty_falls_back_to_the_handle(osa, tmp_path, home, monkeypatch):
    it = notify.iterm_module()
    asked = []
    monkeypatch.setattr(it, "tty_by_id", lambda h: asked.append(h) or str(tmp_path / "tty"))
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"tty": "", "iterm_handle": "w0t0p0:U"}, lead_sid="l") == "tty"
    assert asked == ["w0t0p0:U"] and (tmp_path / "tty").read_text() == osc("T", "S — M")


def test_the_handle_is_looked_up_when_there_is_no_tty_and_the_lookup_can_fail(osa, tmp_path, home, monkeypatch):
    it = notify.iterm_module()
    monkeypatch.setattr(it, "tty_by_id", lambda h: None)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"iterm_handle": "w0t0p0:U"}, lead_sid="lead-1") == "osascript"

    def boom(h):
        raise RuntimeError("no iterm")

    monkeypatch.setattr(it, "tty_by_id", boom)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"iterm_handle": "w0t0p0:U"}, lead_sid="lead-1") == "osascript"
    assert [r["reason"] for r in ledger.read(home, event="banner_fallback")] == ["no-tty", "tty-lookup-failed"]
    assert len(osa()) == 2


def test_a_notify_via_tty_that_raises_is_a_write_failure(osa, home, monkeypatch):
    it = notify.iterm_module()

    def boom(*a):
        raise OSError("x")

    monkeypatch.setattr(it, "notify_via_tty", boom)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={"tty": "/tmp/whatever"}, lead_sid="l") == "osascript"
    assert [r["reason"] for r in ledger.read(home, event="banner_fallback")] == ["write-failed"]


# ── tier 2 and never raising ────────────────────────────────────────────────────────────────────
def test_a_message_longer_than_180_characters_is_cut(osa, tmp_path, home):
    msg = "x" * 300
    notify.banner(home, {}, "T", "S", msg)
    assert osa() == [["-e", script("T", "S — " + "x" * 180)]]
    tty = tmp_path / "tty"
    notify.banner(home, {}, "T", "S", msg, lead_rec={"tty": str(tty)}, lead_sid="l")
    assert tty.read_text() == osc("T", "S — " + "x" * 180)


def test_banner_never_raises_when_osascript_is_missing(tmp_path, home, monkeypatch):
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert notify.banner(home, {}, "T", "S", "M") is None
    assert notify.banner(home, None, None, None, None, lead_rec=object()) is None


def test_osascript_is_run_as_an_argument_list_with_a_timeout(osa, tmp_path, home, monkeypatch):
    seen = {}
    real = subprocess.run

    def spy(args, **kw):
        seen.update(args=args, **kw)
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    notify.banner(home, {}, "T", "S", "M")
    assert seen["args"] == ["osascript", "-e", script("T", "S — M")]
    assert seen["timeout"] == 5 and not seen.get("shell")


# ── claim ───────────────────────────────────────────────────────────────────────────────────────
def test_claim_is_true_once_then_false(home):
    assert notify.claim(home, "lead-1", "exec-a.report-1") is True
    assert notify.claim(home, "lead-1", "exec-a.report-1") is False
    assert notify.claim(home, "lead-1", "exec-a.report-2") is True
    assert notify.claim(home, "lead-2", "exec-a.report-1") is True
    assert notify.claim(home, notify.NO_LEAD, "exec-a.no-lead-1") is True
    assert notify.claim(home, notify.NO_LEAD, "exec-a.no-lead-1") is False


def test_claim_makes_only_the_empty_file_wake_owner_notified_key(home):
    assert notify.claim(home, "lead-1", "exec.a.ctx-warn") is True  # a session id may hold a dot
    made = tree(home)
    assert made == {"wake": None, "wake/lead-1": None, "wake/lead-1/notified": None,
                    "wake/lead-1/notified/exec.a.ctx-warn": b""}


def test_two_processes_started_together_get_one_true(home, tmp_path):
    code = ("import sys; sys.path.insert(0, %r); from pilead import notify; "
            "sys.stdout.write(str(notify.claim(sys.argv[1], 'lead-1', 'exec-a.report-1')))" % str(ROOT / "lib"))
    go = tmp_path / "go"
    procs = [subprocess.Popen(["sh", "-c", f'while [ ! -e {go} ]; do :; done; exec {sys.executable} -c "$0" "$@"', code, str(home)],
                              stdout=subprocess.PIPE, text=True) for _ in range(6)]
    go.write_text("")
    outs = sorted(p.communicate()[0] for p in procs)
    assert outs == ["False"] * 5 + ["True"]


@pytest.mark.parametrize("owner,key", [
    ("../x", "exec-a.report-1"), ("lead-1.usage", "exec-a.report-1"), ("", "exec-a.report-1"), (None, "exec-a.report-1"),
    ("a/b", "exec-a.report-1"), ("_other", "exec-a.report-1"),
    ("lead-1", "../x.report-1"), ("lead-1", "exec-a"), ("lead-1", "exec-a."), ("lead-1", ".report-1"),
    ("lead-1", "exec-a.re/port"), ("lead-1", "exec-a.re port"), ("lead-1", "a..b.report-1"), ("lead-1", None),
    ("lead-1", "exec-a/../../x.report-1"), ("lead-1", "-exec.report-1"),
])
def test_claim_with_an_owner_or_key_that_is_not_usable_is_false_and_writes_nothing(tmp_path, owner, key):
    h = tmp_path / "a" / "b" / "home"
    h.mkdir(parents=True)
    before = tree(tmp_path)
    assert notify.claim(h, owner, key) is False
    assert tree(tmp_path) == before


def test_claim_that_cannot_be_recorded_is_false(home):
    (home / "wake").mkdir(parents=True)
    (home / "wake" / "lead-1").write_text("a file where the directory belongs")
    assert notify.claim(home, "lead-1", "exec-a.report-1") is False
    assert notify.claim(home / "nowhere" / "\0x", "lead-1", "exec-a.report-1") is False


# ── the helpers the verbs share ─────────────────────────────────────────────────────────────────
def test_brief(tmp_path):
    assert notify.brief("# Done: x\nmore") == "Done: x"
    assert notify.brief("\n  \n## \t A   b\t c \nlater") == "A b c"
    assert notify.brief("") == "" and notify.brief(None) == "" and notify.brief("# \n \n") == ""
    assert notify.brief("y" * 500) == "y" * 200
    assert notify.report_brief(tmp_path, "exec-a", 1) == ""
    assert notify.report_brief(tmp_path, "../x", 1) == ""


# ── b12: a tmux lead's tier 1, and tier 2 on Linux ───────────────────────────────────────────────────
@pytest.fixture
def tmux_notify(monkeypatch):
    """The vendored tmux backend's `notify`, replaced: records (handle, title, body); `posts` is its answer."""
    tm = notify.tmux_module()
    seen = {"calls": [], "posts": True}

    def fake(handle, title, body):
        seen["calls"].append((handle, title, body))
        return seen["posts"]
    monkeypatch.setattr(tm, "notify", fake)
    return seen


def test_a_tmux_lead_gets_a_status_line_message_and_no_tty_write(osa, tmp_path, home, tmux_notify):
    tty = tmp_path / "tty"
    rec = {"iterm_handle": "tmux:%4", "tty": str(tty)}
    assert notify.banner(home, {}, "T", "S", "M", lead_rec=rec, lead_sid="lead-1") == "tmux"
    assert tmux_notify["calls"] == [("tmux:%4", "T", "S — M")]
    assert not tty.exists() and osa() == []
    assert ledger.read(home, event="banner_fallback") == []


def test_a_tmux_message_that_does_not_post_is_ledgered_then_tier_2(osa, tmp_path, home, tmux_notify):
    tmux_notify["posts"] = False
    tty = tmp_path / "tty"
    rec = {"iterm_handle": "tmux:%4", "tty": str(tty)}
    assert notify.banner(home, {}, "T", "S", "M", lead_rec=rec, lead_sid="lead-1") == "osascript"
    assert not tty.exists() and osa() == [["-e", script("T", "S — M")]]
    recs = ledger.read(home, event="banner_fallback")
    assert [(r["session_id"], r["reason"]) for r in recs] == [("lead-1", "write-failed")]


def test_notify_via_osascript_skips_the_tmux_tier(osa, home, tmux_notify):
    rec = {"iterm_handle": "tmux:%4"}
    assert notify.banner(home, {"notify_via": "osascript"}, "T", "S", "M", lead_rec=rec, lead_sid="lead-1") == "osascript"
    assert tmux_notify["calls"] == []


@pytest.fixture
def linux(monkeypatch, tmp_path):
    """platform_cmds says Linux; `notify-send` is a recording stub on PATH when `present` is set."""
    notify.iterm_module()
    import platform_cmds
    monkeypatch.setattr(platform_cmds, "is_linux", lambda: True)
    d = tmp_path / "ns-bin"
    d.mkdir()
    log = tmp_path / "ns.log"
    state = {"log": log, "dir": d}

    def present(rc=0):
        (d / "notify-send").write_text(f'#!/bin/sh\n{{ printf "%s\\n" "$@"; echo =====; }} >> "{log}"\nexit {rc}\n')
        (d / "notify-send").chmod(0o755)
        monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    state["present"] = present
    return state


def test_linux_tier_2_is_notify_send(osa, home, linux, tmux_notify):
    linux["present"]()
    tmux_notify["posts"] = False
    rec = {"iterm_handle": "tmux:%4"}
    assert notify.banner(home, {}, "T", "S", "M", lead_rec=rec, lead_sid="lead-1") == "notify-send"
    assert linux["log"].read_text() == "T\nS — M\n=====\n"
    assert osa() == []


def test_linux_without_notify_send_is_silent(osa, home, linux, monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={}, lead_sid="lead-1") is None
    assert osa() == []


def test_linux_notify_send_that_fails_is_none(osa, home, linux):
    linux["present"](rc=1)
    assert notify.banner(home, {}, "T", "S", "M", lead_rec={}, lead_sid="lead-1") is None
    assert linux["log"].exists() and osa() == []

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import iterm
import terminal_app

TESTS = Path(__file__).resolve().parent

BASE = dict(session_id="topic-101010", model="anthropic/claude-sonnet-5",
            extension="/repo/extensions/pi-lead.ts", rules="/repo/skills/executor-rules.md",
            home="/h", lead="lead-1", inbox="/h/sessions/topic-101010/inbox.md")


def _ok(out=""):
    return subprocess.CompletedProcess(["osascript"], 0, out, "")


def test_fake_pi_on_path():
    assert Path(shutil.which("pi")).resolve() == (TESTS / "fake_pi").resolve()


def test_fresh_cmd_has_env_and_flags():
    cmd = iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", **BASE)
    assert cmd == (
        "PI_LEAD_ROLE=executor PI_LEAD_SID=topic-101010 PI_LEAD_HOME=/h "
        "PI_LEAD_INBOX=/h/sessions/topic-101010/inbox.md PI_LEAD_LEAD=lead-1 "
        "exec pi --session-id topic-101010 --model anthropic/claude-sonnet-5 "
        "-e /repo/extensions/pi-lead.ts --append-system-prompt /repo/skills/executor-rules.md "
        "@/h/sessions/topic-101010/packet-0001.md")


def test_exclude_tools_joined():
    cmd = iterm.build_pi_cmd("/p.md", exclude_tools=["web", "edit"], **BASE)
    assert "--exclude-tools web,edit " in cmd
    assert "--exclude-tools" not in iterm.build_pi_cmd("/p.md", **BASE)


def test_resume_shape():
    cmd = iterm.build_pi_cmd("/p.md", resume=True, **BASE)
    assert "--session-id topic-101010" in cmd
    assert "--model" not in cmd and "@" not in cmd
    assert "PI_LEAD_ROLE=executor" in cmd and "-e /repo/extensions/pi-lead.ts" in cmd


def test_model_passes_through_verbatim():
    cmd = iterm.build_pi_cmd("/p.md", **{**BASE, "model": "deepseek/deepseek-v4-pro"})
    assert "--model deepseek/deepseek-v4-pro" in cmd


def test_tier_alias_not_resolved_here():
    cmd = iterm.build_pi_cmd("/p.md", **{**BASE, "model": "sonnet"})
    assert "--model sonnet " in cmd and "anthropic" not in cmd


def test_packet_path_with_space_is_quoted():
    cmd = iterm.build_pi_cmd("/h/my dir/packet-0001.md", **BASE)
    assert shlex.split(cmd)[-1] == "@/h/my dir/packet-0001.md"


def test_terminal_app_builds_same_base_line(tmp_path):
    with mock.patch.object(terminal_app, "run_osascript", return_value=_ok("42")) as osa:
        terminal_app.spawn("/work", "/p.md", "lbl", str(tmp_path / "pid"), BASE)
    script = osa.call_args_list[0][0][0]
    assert iterm.build_pi_cmd("/p.md", **BASE) in script
    assert "&& PI_LEAD_ROLE=executor" in script and "exec exec" not in script


def test_iterm_spawn_bootstrap_has_same_line(tmp_path):
    with mock.patch.object(iterm, "run_osascript", return_value=_ok("OK\nsid\nfront")):
        r = iterm.spawn("/work dir", "/p.md", "lbl", str(tmp_path / "pid"), BASE, rename_delay=0)
    assert r["ok"]
    boot = (tmp_path / iterm.BOOTSTRAP_FILENAME).read_text()
    assert f"&& echo $$ > {tmp_path}/pid && {iterm.build_pi_cmd('/p.md', **BASE)}\n" in boot
    assert "cd '/work dir' &&" in boot


def test_fake_pi_records_and_replies(tmp_path, monkeypatch):
    packet = tmp_path / "packet.md"
    packet.write_text("do it")
    monkeypatch.setenv("PI_LEAD_SID", "s1")
    monkeypatch.setenv("PI_LEAD_FAKE_REPLY", "TL;DR ok")
    subprocess.run(["pi", "--session-id", "s1", f"@{packet}"], check=True)
    rec = json.loads((tmp_path / "fake-pi.log").read_text().splitlines()[0])
    assert rec["argv"] == ["--session-id", "s1", f"@{packet}"]
    assert rec["files"][str(packet)] == "do it" and rec["env"]["PI_LEAD_SID"] == "s1"
    home = tmp_path / "pi-lead-home"
    assert (home / "sessions/s1/report-0001.md").read_text() == "TL;DR ok\n"
    out = subprocess.run(["pi", "--list-models"], capture_output=True, text=True).stdout
    assert "anthropic  claude-sonnet-5  1M" in out and "deepseek   deepseek-flash   1M" in out


# ── thinking= (pilead spawn's effort) ───────────────────────────────────────────────────────────
def test_thinking_follows_model_on_a_fresh_launch():
    cmd = iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", thinking="high", **BASE)
    assert "exec pi --session-id topic-101010 --model anthropic/claude-sonnet-5 --thinking high " \
           "-e /repo/extensions/pi-lead.ts " in cmd
    argv = shlex.split(cmd)
    assert argv[argv.index("--thinking") + 1] == "high" and argv.count("--thinking") == 1


def test_thinking_follows_session_id_on_resume():
    cmd = iterm.build_pi_cmd("/p.md", resume=True, thinking="xhigh", **BASE)
    assert "exec pi --session-id topic-101010 --thinking xhigh -e /repo/extensions/pi-lead.ts " in cmd
    assert "--model" not in cmd and "@" not in cmd


def test_no_thinking_is_byte_identical():
    fresh = ("PI_LEAD_ROLE=executor PI_LEAD_SID=topic-101010 PI_LEAD_HOME=/h "
             "PI_LEAD_INBOX=/h/sessions/topic-101010/inbox.md PI_LEAD_LEAD=lead-1 "
             "exec pi --session-id topic-101010 --model anthropic/claude-sonnet-5 "
             "-e /repo/extensions/pi-lead.ts --append-system-prompt /repo/skills/executor-rules.md "
             "@/h/sessions/topic-101010/packet-0001.md")
    assert iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", **BASE) == fresh
    assert iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", thinking=None, **BASE) == fresh
    resumed = iterm.build_pi_cmd("/p.md", resume=True, **BASE)
    assert iterm.build_pi_cmd("/p.md", resume=True, thinking=None, **BASE) == resumed
    assert "--thinking" not in resumed


# ── role= and message= (pilead resume of a lead) ────────────────────────────────────────────────
LEAD_LINE = "PI_LEAD_HOME=/h exec pi --session-id topic-101010 -e /repo/extensions/pi-lead.ts"


def test_lead_role_line():
    assert iterm.build_pi_cmd("/p.md", role="lead", **BASE) == LEAD_LINE
    for word in ("PI_LEAD_ROLE", "PI_LEAD_SID", "PI_LEAD_INBOX", "PI_LEAD_LEAD", "--model", "--append-system-prompt",
                 "@"):
        assert word not in iterm.build_pi_cmd("/p.md", role="lead", thinking="high", message="go", **BASE)


def test_lead_role_line_with_thinking_and_message():
    assert iterm.build_pi_cmd("/p.md", role="lead", thinking="high", **BASE) == (
        "PI_LEAD_HOME=/h exec pi --session-id topic-101010 --thinking high -e /repo/extensions/pi-lead.ts")
    assert iterm.build_pi_cmd("/p.md", role="lead", message="hello", **BASE) == LEAD_LINE + " hello"
    assert iterm.build_pi_cmd("/p.md", role="lead", thinking="low", message="hi", **BASE) == (
        "PI_LEAD_HOME=/h exec pi --session-id topic-101010 --thinking low -e /repo/extensions/pi-lead.ts hi")


def test_lead_role_message_with_quotes_and_spaces_is_one_argument():
    msg = "You are the resumed lead for project 'my proj'. First run: pilead lead-start \"$PI_SESSION_ID\" --x"
    cmd = iterm.build_pi_cmd("/p.md", role="lead", message=msg, **{**BASE, "home": "/my home"})
    argv = shlex.split(cmd)
    assert argv[-1] == msg and argv[0] == "PI_LEAD_HOME=/my home"
    assert argv[1:5] == ["exec", "pi", "--session-id", "topic-101010"]


def test_default_role_and_no_message_are_byte_identical():
    fresh = ("PI_LEAD_ROLE=executor PI_LEAD_SID=topic-101010 PI_LEAD_HOME=/h "
             "PI_LEAD_INBOX=/h/sessions/topic-101010/inbox.md PI_LEAD_LEAD=lead-1 "
             "exec pi --session-id topic-101010 --model anthropic/claude-sonnet-5 "
             "-e /repo/extensions/pi-lead.ts --append-system-prompt /repo/skills/executor-rules.md "
             "@/h/sessions/topic-101010/packet-0001.md")
    for kw in ({}, {"role": "executor"}, {"message": None}, {"role": "executor", "message": None}):
        assert iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", **kw, **BASE) == fresh
    resumed = iterm.build_pi_cmd("/p.md", resume=True, **BASE)
    assert resumed == ("PI_LEAD_ROLE=executor PI_LEAD_SID=topic-101010 PI_LEAD_HOME=/h "
                       "PI_LEAD_INBOX=/h/sessions/topic-101010/inbox.md PI_LEAD_LEAD=lead-1 "
                       "exec pi --session-id topic-101010 -e /repo/extensions/pi-lead.ts "
                       "--append-system-prompt /repo/skills/executor-rules.md")
    assert iterm.build_pi_cmd("/p.md", resume=True, role="executor", message=None, **BASE) == resumed


def test_an_executor_message_is_the_last_argument():
    cmd = iterm.build_pi_cmd("/p.md", resume=True, message="pick up where you left off", **BASE)
    assert shlex.split(cmd)[-1] == "pick up where you left off"


# ── lead_model= (pilead handoff) ─────────────────────────────────────────────────────────────────
FRESH_LINE = ("PI_LEAD_ROLE=executor PI_LEAD_SID=topic-101010 PI_LEAD_HOME=/h "
              "PI_LEAD_INBOX=/h/sessions/topic-101010/inbox.md PI_LEAD_LEAD=lead-1 "
              "exec pi --session-id topic-101010 --model anthropic/claude-sonnet-5 "
              "-e /repo/extensions/pi-lead.ts --append-system-prompt /repo/skills/executor-rules.md "
              "@/h/sessions/topic-101010/packet-0001.md")


def test_lead_model_follows_the_session_id_on_a_lead_line():
    assert iterm.build_pi_cmd("/p.md", role="lead", lead_model="anthropic/claude-opus-5", **BASE) == (
        "PI_LEAD_HOME=/h exec pi --session-id topic-101010 --model anthropic/claude-opus-5 "
        "-e /repo/extensions/pi-lead.ts")
    assert iterm.build_pi_cmd("/p.md", role="lead", lead_model="deepseek/deepseek-v4-flash", thinking="high",
                              message="go on", **BASE) == (
        "PI_LEAD_HOME=/h exec pi --session-id topic-101010 --model deepseek/deepseek-v4-flash --thinking high "
        "-e /repo/extensions/pi-lead.ts 'go on'")


def test_a_lead_model_with_a_slash_and_a_colon_is_one_argument():
    model = "openrouter/anthropic/claude-opus-5:thinking"
    argv = shlex.split(iterm.build_pi_cmd("/p.md", role="lead", lead_model=model, message="m", **BASE))
    assert argv[argv.index("--model") + 1] == model
    assert argv[3:7] == ["--session-id", "topic-101010", "--model", model]


def test_no_lead_model_is_byte_identical():
    for kw in ({"lead_model": None}, {"lead_model": ""}):
        assert iterm.build_pi_cmd("/p.md", role="lead", **kw, **BASE) == LEAD_LINE
        assert iterm.build_pi_cmd("/p.md", role="lead", message="hello", **kw, **BASE) == LEAD_LINE + " hello"
        assert iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", **kw, **BASE) == FRESH_LINE
    # an executor line ignores it: its model is `model`
    assert iterm.build_pi_cmd("/h/sessions/topic-101010/packet-0001.md", lead_model="x/y", **BASE) == FRESH_LINE

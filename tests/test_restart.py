"""`pilead restart`: an executor's current packet runs again as a new conversation (a successor session).
Driven through bin/pilead with the fake `pi` and the terminal seam of tests/test_resume.py (a recording
`osascript` stub and a `ps` stub first on PATH); a live process is a child this file starts and stops.
No real tab is opened or closed and the real pi never runs."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import (H, child, env, events, fake_pi_calls, mark_reported, meta, pilead, refused,  # noqa: F401
                         run_bootstrap, sdir, spawn, terminal)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import lifecycle, state  # noqa: E402


def footer_of(env, sid, n=1):
    return state.footer(H(env), sid, n)


def config(env, **kv):
    (H(env) / "config.json").write_text(json.dumps(kv) + "\n")


def age(path, secs):
    t = time.time() - secs
    os.utime(path, (t, t))


def child_gone(d):
    """The child named in the old session's pid file was signalled by the close (it is this test's own)."""
    pid = int((d / "pid").read_text())
    for _ in range(50):
        try:
            os.waitpid(pid, os.WNOHANG)
            os.kill(pid, 0)
        except (ChildProcessError, ProcessLookupError):
            return True
        time.sleep(0.05)
    return False


def restarted_line(sid, succ, n=1):
    return f"restarted {sid} as {succ} — packet {n:04d} runs again as a new conversation"


# ── refusals: home byte-identical ───────────────────────────────────────────────────────────────
def test_a_lead_sid_is_refused(env):
    before = tree(H(env))
    refused(pilead("restart", "lead-1", check=False), "is a lead")
    assert tree(H(env)) == before


def test_an_unknown_sid_gives_the_existing_error(env):
    r = pilead("restart", "nobody-here", check=False)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session nobody-here\n")


def test_a_pinned_session_is_refused_naming_keep_off(env):
    sid = spawn(env)
    pilead("keep", sid)
    before = tree(H(env))
    refused(pilead("restart", sid, "--force", check=False), f"pilead keep {sid} --off")
    assert tree(H(env)) == before


def test_an_alive_busy_session_is_refused_and_force_restarts_it(env, child):
    sid = spawn(env)
    (sdir(env, sid) / "pid").write_text(f"{child().pid}\n")
    before = tree(H(env))
    refused(pilead("restart", sid, check=False), "still alive", "--force")
    assert tree(H(env)) == before
    r = pilead("restart", sid, "--force")
    assert r.stdout.splitlines()[-1] == restarted_line(sid, f"{sid}-r2")
    assert child_gone(sdir(env, sid))


def test_a_missing_packet_file_is_refused(env):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "packet-0001.md").unlink()
    before = tree(H(env))
    refused(pilead("restart", sid, check=False), "packet 0001", "missing")
    assert tree(H(env)) == before


@pytest.mark.parametrize("name,why", [("taken", "already exists"), ("bad name", "not usable"),
                                      ("-dash", "not usable"), ("a..b", "not usable"),
                                      ("trailing.", "not usable")])
def test_an_unusable_or_taken_name_is_refused(env, name, why):
    sid = spawn(env)
    pilead("close", sid)
    if name == "taken":
        (H(env) / "sessions" / "taken").mkdir()
    before = tree(H(env))
    refused(pilead("restart", sid, f"--name={name}", check=False), why)
    assert tree(H(env)) == before


def test_a_model_above_the_ceiling_needs_an_override(env):
    sid = spawn(env)
    pilead("close", sid)
    before = tree(H(env))
    refused(pilead("restart", sid, "--model", "anthropic/claude-fable-5", check=False), "above the ceiling",
            "--model-override")
    assert tree(H(env)) == before
    r = pilead("restart", sid, "--model", "anthropic/claude-fable-5", "--model-override", "hard bug")
    assert "model: anthropic/claude-fable-5 is above the ceiling opus; override recorded" in r.stdout


def test_an_effort_that_is_no_level_is_refused(env):
    sid = spawn(env)
    pilead("close", sid)
    before = tree(H(env))
    refused(pilead("restart", sid, "--effort", "extreme", check=False), "not a thinking level")
    assert tree(H(env)) == before


# ── a restart ───────────────────────────────────────────────────────────────────────────────────
def test_a_stalled_alive_session_restarts_without_force(env, child):
    sid = spawn(env, log=False)  # no session log: busy longer than the threshold alone makes it stalled
    (sdir(env, sid) / "pid").write_text(f"{child().pid}\n")
    age(sdir(env, sid) / "packet-0001.md", 10 * 3600)
    r = pilead("restart", sid)
    succ = f"{sid}-r2"
    assert r.stdout.splitlines()[-1] == restarted_line(sid, succ)
    evs = [e for e in events(env) if e.get("session_id") == sid]
    assert [e["event"] for e in evs][-3:] == ["stalled", "closed", "restarted"]


def test_the_successor_runs_the_same_packet_text_with_its_own_footer(env):
    sid = spawn(env)
    pilead("close", sid)
    r = pilead("restart", sid)
    succ = f"{sid}-r2"
    lines = r.stdout.splitlines()
    assert lines[0] == f"spawned {succ} model=anthropic/claude-sonnet-5 tab=FAKE-UUID"
    assert lines[1] == f"effort=high scope={meta(env, sid)['scope']}"
    assert lines[2].startswith("placement=") and lines[3] == restarted_line(sid, succ) and len(lines) == 4
    new = (sdir(env, succ) / "packet-0001.md").read_text()
    assert new == "GOAL: do the thing" + footer_of(env, succ)
    assert str(sdir(env, sid) / "report-0001.md") not in new
    run_bootstrap(sdir(env, succ) / "bootstrap.sh")
    argv = fake_pi_calls(env)[-1]["argv"]
    assert argv[argv.index("--session-id") + 1] == succ and argv[-1] == f"@{sdir(env, succ)}/packet-0001.md"


def test_the_current_packet_after_a_send_is_the_one_run_again(env):
    sid = spawn(env)
    mark_reported(env, sid)
    (env / "p2.md").write_text("GOAL: the second thing\n\n\n")
    pilead("send", sid, str(env / "p2.md"))
    pilead("close", sid)
    r = pilead("restart", sid)
    assert r.stdout.splitlines()[-1] == restarted_line(sid, f"{sid}-r2", 2)
    new = (sdir(env, f"{sid}-r2") / "packet-0001.md").read_text()
    assert new == "GOAL: the second thing" + footer_of(env, f"{sid}-r2")


def test_a_packet_without_the_expected_footer_is_used_whole(env):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "packet-0001.md").write_text("GOAL: hand-edited\nno footer here\n")
    pilead("restart", sid)
    new = (sdir(env, f"{sid}-r2") / "packet-0001.md").read_text()
    assert new == "GOAL: hand-edited\nno footer here" + footer_of(env, f"{sid}-r2")


def test_the_successor_inherits_and_is_not_pinned(env):
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1",
               "--effort", "low", "--scope", "the parser", "--pane")
    sid = r.stdout.split()[1]
    pilead("close", sid)
    pilead("restart", sid)
    old, new = meta(env, sid), meta(env, f"{sid}-r2")
    for k in ("worktree", "topic", "scope", "lead", "layout", "effort", "model"):
        assert new[k] == old[k], k
    assert (new["effort"], new["scope"], new["layout"]) == ("low", "the parser", "pane")
    assert not new.get("keep") and new["packets"] == 1 and new["label"] == f"[Exec] {sid}-r2"
    (opts,) = [e for e in events(env, "launch_options") if e["session_id"] == f"{sid}-r2"]
    assert (opts["effort"], opts["effort_source"], opts["keep"]) == ("low", "inherited", False)
    snaps = [e for e in events(env, "usage_snapshot") if e["session_id"] == f"{sid}-r2"]
    assert snaps == [{"ts": snaps[0]["ts"], "event": "usage_snapshot", "session_id": f"{sid}-r2", "packet": 1,
                      "at": "sent", "usage": {"prompt": 0, "output": 0, "requests": 0}}]


def test_model_effort_and_name_are_honoured(env):
    sid = spawn(env)
    pilead("close", sid)
    r = pilead("restart", sid, "--model", "dspro", "--effort", "minimal", "--name", "fresh-start")
    assert r.stdout.splitlines()[0] == "spawned fresh-start model=deepseek/deepseek-v4-pro tab=FAKE-UUID"
    m = meta(env, "fresh-start")
    assert (m["model"], m["effort"]) == ("deepseek/deepseek-v4-pro", "minimal")
    assert meta(env, sid)["superseded_by"] == "fresh-start"
    (ev,) = events(env, "restarted")
    assert ev == {"ts": ev["ts"], "event": "restarted", "session_id": sid, "successor": "fresh-start", "packet": 1,
                  "model": "deepseek/deepseek-v4-pro"}


def test_a_paused_session_restarts_on_the_fallback(env):
    config(env, executor_fallback_model="dsflash")
    sid = spawn(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    r = pilead("restart", sid)
    succ = f"{sid}-r2"
    assert meta(env, succ)["model"] == "deepseek/deepseek-flash"
    assert any("fallback deepseek/deepseek-flash" in ln for ln in r.stdout.splitlines())
    assert r.stdout.splitlines()[-1] == restarted_line(sid, succ)


def test_a_paused_session_without_a_fallback_keeps_its_model(env):
    sid = spawn(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    r = pilead("restart", sid)
    assert meta(env, f"{sid}-r2")["model"] == "anthropic/claude-sonnet-5"
    assert "fallback" not in r.stdout


def test_the_old_session_is_closed_superseded_and_keeps_every_file(env):
    sid = spawn(env)
    names = sorted(p.name for p in sdir(env, sid).iterdir())
    pilead("restart", sid)
    succ = f"{sid}-r2"
    m = meta(env, sid)
    assert (sdir(env, sid) / "status").read_text() == "closed\n" and m["superseded_by"] == succ
    assert pilead("check", sid).stdout.splitlines()[0] == "superseded"
    assert set(names) <= {p.name for p in sdir(env, sid).iterdir()}
    (closed,) = [e for e in events(env, "closed") if e["session_id"] == sid]
    assert closed["reason"] == "restart"


def test_a_failing_launch_leaves_the_old_session_closed_and_no_successor(env):
    sid = spawn(env)
    r = pilead("restart", sid, check=False, env={"FAKE_SPAWN": "fail"})
    assert r.returncode == 1 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and f"{sid} was closed, but the restart did not happen" in r.stderr
    assert (sdir(env, sid) / "status").read_text() == "closed\n"
    assert "superseded_by" not in meta(env, sid)
    assert not (H(env) / "sessions" / f"{sid}-r2").exists()
    assert not events(env, "restarted")


def test_other_sessions_are_untouched(env):
    a = spawn(env)
    b = spawn(env)
    other = {k: v for k, v in tree(H(env)).items() if k.startswith(f"sessions/{b}") or k.startswith("leads/")}
    pilead("restart", a)
    assert {k: v for k, v in tree(H(env)).items()
            if k.startswith(f"sessions/{b}") or k.startswith("leads/")} == other


# ── successor_name ──────────────────────────────────────────────────────────────────────────────
def test_successor_name(tmp_path):
    h = tmp_path / "h"
    for sid in ("foo", "foo-r2", "foo-r9"):
        (h / "sessions" / sid).mkdir(parents=True)
    assert lifecycle.successor_name(h, "foo") == "foo-r3"  # foo-r2 is taken
    assert lifecycle.successor_name(h, "foo-r2") == "foo-r3"  # never foo-r2-r2
    assert lifecycle.successor_name(h, "foo-r9") == "foo-r3"  # the smallest free N, not 10
    (h / "sessions" / "foo-r3").mkdir()
    assert lifecycle.successor_name(h, "foo") == "foo-r4"  # -r2 and -r3 exist
    assert lifecycle.successor_name(h, "foo-r2") == "foo-r4"


def test_successor_name_of_a_first_session(tmp_path):
    h = tmp_path / "h"
    (h / "sessions" / "foo").mkdir(parents=True)
    assert lifecycle.successor_name(h, "foo") == "foo-r2"
    assert lifecycle.successor_name(h, "bar-r12x") == "bar-r12x-r2"  # only a trailing -r<digits> is a rotation


def test_a_session_with_no_stored_effort_restarts_on_the_config_default_recorded_as_config(env):
    config(env, executor_default_effort="low")
    sid = spawn(env)
    m = meta(env, sid)
    del m["effort"]
    (sdir(env, sid) / "meta.json").write_text(json.dumps(m, indent=2) + "\n")
    pilead("close", sid)
    pilead("restart", sid)
    (opts,) = [e for e in events(env, "launch_options") if e["session_id"] == f"{sid}-r2"]
    assert (opts["effort"], opts["effort_source"]) == ("low", "config")


# ── p22: restarting from a lead is a claim of the session ──────────────────────────────────────────
def test_a_restart_from_a_lead_adopts_an_orphan_and_the_successor_names_the_caller(env):
    sid = spawn(env)
    (H(env) / "leads" / "lead-1.json").unlink()
    pilead("lead-start", "lead-2", "--project", "other")
    r = pilead("restart", sid, env={"PI_LEAD_SID": "lead-2"})
    assert r.stdout.splitlines()[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert r.stdout.splitlines()[-1] == restarted_line(sid, f"{sid}-r2")
    assert meta(env, sid)["lead"] == "lead-2" and meta(env, f"{sid}-r2")["lead"] == "lead-2"


def test_a_refused_restart_adopts_nothing_and_no_caller_adopts_nothing(env):
    sid = spawn(env)
    pilead("keep", sid)
    (H(env) / "leads" / "lead-1.json").unlink()
    pilead("lead-start", "lead-2", "--project", "other")
    before = tree(H(env))
    refused(pilead("restart", sid, check=False, env={"PI_LEAD_SID": "lead-2"}), f"pilead keep {sid} --off")
    assert tree(H(env)) == before
    pilead("keep", sid, "--off")
    r = pilead("restart", sid)
    assert not r.stdout.startswith("adopted") and meta(env, f"{sid}-r2")["lead"] == "lead-1"

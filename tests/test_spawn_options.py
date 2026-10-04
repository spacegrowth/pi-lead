"""`pilead spawn`'s launch choices (README "Model and effort"): the model (`--model`, config
`executor_default_model`, `executor_fallback_model`, `executor_model_ceiling` and `--model-override`), the
thinking effort (`--effort`, a packet's `EFFORT:` line, config `executor_default_effort`), `--scope` and
`--keep`; what spawn prints and ledgers for them. bin/pilead is driven as tests/test_cli_core.py does it
(fake `pi`, the recording `osascript` stub); the launch line is read from what tests/fake_pi recorded."""
import json
import os
import re
import sys
import time

import pytest

from test_cli_core import ROOT, env, home, pilead, run_bootstrap, start_lead, stub_osascript  # noqa: F401

sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, lint  # noqa: E402

LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
# models.json as tests/fake_pi lists it, less the models a test takes out to make an alias match nothing
ALL_MODELS = [("anthropic", "claude-sonnet-5"), ("deepseek", "deepseek-flash"), ("anthropic", "claude-haiku-4-5"),
              ("anthropic", "claude-sonnet-4-6"), ("anthropic", "claude-opus-5"), ("anthropic", "claude-fable-5"),
              ("deepseek", "deepseek-v4-pro")]


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────
def config(env, **kv):
    h = home(env)
    h.mkdir(parents=True, exist_ok=True)
    (h / "config.json").write_text(json.dumps(kv))


def models_json(env, without=()):
    """A fresh models.json cache (no `pi --list-models` run), minus the ids in `without`."""
    h = home(env)
    h.mkdir(parents=True, exist_ok=True)
    (h / "models.json").write_text(json.dumps({"fetched": int(time.time()), "models": [
        {"provider": p, "id": i, "context": "1M"} for p, i in ALL_MODELS if i not in without]}))


def run_spawn(env, *flags, packet="packet.md", topic="topic", check=True, lead=True):
    if lead:
        start_lead()
    return pilead("spawn", str(env / "wt"), topic, str(env / packet), "--lead", "lead-1", *flags, check=check)


def sid_of(r):
    return r.stdout.split()[1]


def meta(env, sid):
    return json.loads((home(env) / "sessions" / sid / "meta.json").read_text())


def events(env, name):
    return [{k: v for k, v in e.items() if k not in ("ts", "event")}
            for e in ledger.read(home(env), event=name)]


def launch_argv(env, sid):
    (rec,) = run_bootstrap(env, sid)
    return rec["argv"]


def flag_value(argv, flag):
    assert argv.count(flag) == 1, argv
    return argv[argv.index(flag) + 1]


def tree(path):
    """{relative path: bytes} of every file under `path`: what "byte-identical" compares."""
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def refused_leaves_home(env, *flags):
    """Spawn with `flags` after a lead exists and models.json is warm; returns the result and asserts
    home is byte-identical afterwards and no session was created."""
    start_lead()
    models_json(env)
    before = tree(home(env))
    r = run_spawn(env, *flags, check=False, lead=False)
    assert tree(home(env)) == before
    assert not (home(env) / "sessions").exists()
    return r


# ── model: default, flag, fallback ───────────────────────────────────────────────────────────────
def test_no_model_uses_the_configured_default(env):
    r = run_spawn(env)
    assert meta(env, sid_of(r))["model"] == "anthropic/claude-sonnet-5"  # the built-in default: sonnet
    config(env, executor_default_model="dspro")
    r = run_spawn(env)
    sid = sid_of(r)
    assert meta(env, sid)["model"] == "deepseek/deepseek-v4-pro"
    assert flag_value(launch_argv(env, sid), "--model") == "deepseek/deepseek-v4-pro"


def test_model_flag_wins_over_config(env):
    config(env, executor_default_model="dspro")
    r = run_spawn(env, "--model", "haiku")
    assert meta(env, sid_of(r))["model"] == "anthropic/claude-haiku-4-5"
    assert r.stdout.splitlines()[0].endswith("model=anthropic/claude-haiku-4-5 tab=FAKE-UUID")


def test_unmatched_alias_uses_the_fallback_and_says_so(env):
    models_json(env, without=("claude-haiku-4-5",))
    config(env, executor_fallback_model="sonnet")
    r = run_spawn(env, "--model", "haiku")
    sid = sid_of(r)
    assert meta(env, sid)["model"] == "anthropic/claude-sonnet-5"
    assert "model: haiku is not available here; using the fallback anthropic/claude-sonnet-5" in r.stdout.splitlines()
    assert events(env, "model_fallback") == [{"session_id": sid, "asked": "haiku", "model": "anthropic/claude-sonnet-5"}]
    assert flag_value(launch_argv(env, sid), "--model") == "anthropic/claude-sonnet-5"


def test_fallback_can_be_a_provider_id(env):
    models_json(env, without=("claude-haiku-4-5",))
    config(env, executor_fallback_model="deepseek/deepseek-flash")
    r = run_spawn(env, "--model", "haiku")
    assert meta(env, sid_of(r))["model"] == "deepseek/deepseek-flash"


def test_unmatched_alias_without_a_fallback_is_todays_error(env):
    models_json(env, without=("claude-haiku-4-5",))
    r = run_spawn(env, "--model", "haiku", check=False)
    assert r.returncode == 2
    assert r.stderr.startswith("pilead: alias 'haiku' matches no model in models.json; known aliases:")
    assert r.stdout == "" and not (home(env) / "sessions").exists()
    assert events(env, "model_fallback") == []


def test_fallback_that_does_not_resolve_gives_the_original_error(env):
    models_json(env, without=("claude-haiku-4-5", "claude-opus-5"))
    config(env, executor_fallback_model="opus")
    r = run_spawn(env, "--model", "haiku", check=False)
    assert r.returncode == 2 and r.stderr.startswith("pilead: alias 'haiku' matches no model in models.json")
    config(env, executor_fallback_model="nonsense")
    r = run_spawn(env, "--model", "haiku", check=False)
    assert r.returncode == 2 and r.stderr.startswith("pilead: alias 'haiku' matches no model in models.json")


def test_unknown_alias_is_todays_error_even_with_a_fallback(env):
    config(env, executor_fallback_model="sonnet")
    r = run_spawn(env, "--model", "gpt5", check=False)
    assert r.returncode == 2
    assert r.stderr.startswith("pilead: unknown model alias 'gpt5'; known aliases:")
    assert not (home(env) / "sessions").exists() and events(env, "model_fallback") == []


# ── model: the ceiling ───────────────────────────────────────────────────────────────────────────
def test_above_the_ceiling_is_refused_and_home_is_untouched(env):
    r = refused_leaves_home(env, "--model", "fable")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == ('pilead: model anthropic/claude-fable-5 is above the ceiling opus; to launch it anyway '
                        'pass --model-override "<reason>"\n')


def test_override_launches_and_records_the_reason(env):
    r = run_spawn(env, "--model", "fable", "--model-override", "needs the deepest model")
    sid = sid_of(r)
    assert meta(env, sid)["model"] == "anthropic/claude-fable-5"
    assert "model: anthropic/claude-fable-5 is above the ceiling opus; override recorded" in r.stdout.splitlines()
    assert events(env, "model_ceiling_override") == [{"session_id": sid, "model": "anthropic/claude-fable-5",
                                                      "ceiling": "opus", "reason": "needs the deepest model"}]


@pytest.mark.parametrize("reason", ["", "   ", "\t"])
def test_a_blank_override_reason_is_refused(env, reason):
    r = refused_leaves_home(env, "--model", "fable", "--model-override", reason)
    assert r.returncode == 2 and "above the ceiling opus" in r.stderr


def test_at_the_ceiling_is_allowed_and_an_unneeded_override_records_nothing(env):
    r = run_spawn(env, "--model", "opus")
    assert meta(env, sid_of(r))["model"] == "anthropic/claude-opus-5"
    r = run_spawn(env, "--model", "sonnet", "--model-override", "not needed")
    assert r.returncode == 0 and "override recorded" not in r.stdout
    assert events(env, "model_ceiling_override") == []


def test_an_anthropic_id_with_no_tier_word_is_above_the_ceiling(env):
    r = refused_leaves_home(env, "--model", "anthropic/claude-mystery-1")
    assert r.returncode == 2 and "model anthropic/claude-mystery-1 is above the ceiling opus" in r.stderr
    r = run_spawn(env, "--model", "anthropic/claude-mystery-1", "--model-override", "trying it")
    assert meta(env, sid_of(r))["model"] == "anthropic/claude-mystery-1"


def test_a_ceiling_ranks_the_tiers(env):
    config(env, executor_model_ceiling="haiku")
    assert run_spawn(env, "--model", "sonnet", check=False).returncode == 2
    assert run_spawn(env, "--model", "haiku").returncode == 0
    config(env, executor_model_ceiling="fable")
    assert run_spawn(env, "--model", "fable").returncode == 0


@pytest.mark.parametrize("spec", ["dspro", "dsflash", "deepseek/deepseek-v4-pro"])
@pytest.mark.parametrize("ceiling", ["haiku", "not-a-tier"])
def test_a_deepseek_model_is_never_refused(env, spec, ceiling):
    config(env, executor_model_ceiling=ceiling)
    r = run_spawn(env, "--model", spec)
    assert meta(env, sid_of(r))["model"].startswith("deepseek/")
    assert events(env, "model_ceiling_override") == []


@pytest.mark.parametrize("spec", ["haiku", "sonnet", "opus", "anthropic/claude-sonnet-5"])
def test_a_ceiling_that_is_not_a_tier_word_refuses_every_anthropic_model(env, spec):
    config(env, executor_model_ceiling="gpt")
    r = run_spawn(env, "--model", spec, check=False)
    assert r.returncode == 2 and "is above the ceiling gpt" in r.stderr
    assert not (home(env) / "sessions").exists()


def test_the_ceiling_applies_to_the_fallback(env):
    models_json(env, without=("claude-opus-5",))
    config(env, executor_fallback_model="fable")
    r = run_spawn(env, "--model", "opus", check=False)
    assert r.returncode == 2 and "model anthropic/claude-fable-5 is above the ceiling opus" in r.stderr


# ── effort ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("level", LEVELS)
def test_each_level_is_accepted_and_reaches_the_launch_line(env, level):
    r = run_spawn(env, "--effort", level)
    sid = sid_of(r)
    assert meta(env, sid)["effort"] == level
    argv = launch_argv(env, sid)
    assert flag_value(argv, "--thinking") == level
    assert flag_value(argv, "--model") == "anthropic/claude-sonnet-5"
    assert argv.index("--session-id") < argv.index("--model") < argv.index("--thinking") < argv.index("-e")


def test_effort_order_flag_then_packet_then_config(env):
    (env / "eff.md").write_text("GOAL: do the thing\n- **EFFORT**: low\n")
    config(env, executor_default_effort="minimal")
    r = run_spawn(env, "--effort", "max", packet="eff.md")
    assert meta(env, sid_of(r))["effort"] == "max"
    r = run_spawn(env, packet="eff.md")
    assert meta(env, sid_of(r))["effort"] == "low"
    r = run_spawn(env)
    assert meta(env, sid_of(r))["effort"] == "minimal"
    assert [e["effort_source"] for e in events(env, "launch_options")] == ["flag", "packet", "config"]


def test_no_effort_anywhere_is_high(env):
    r = run_spawn(env)
    sid = sid_of(r)
    assert meta(env, sid)["effort"] == "high" and flag_value(launch_argv(env, sid), "--thinking") == "high"


@pytest.mark.parametrize("bad", ["ultra", "HIGHEST", "", "1"])
def test_an_invalid_effort_flag_exits_2(env, bad):
    r = run_spawn(env, "--effort", bad, check=False)
    assert r.returncode == 2
    assert "off, minimal, low, medium, high, xhigh, max" in r.stderr
    assert not (home(env) / "sessions").exists()


def test_an_invalid_packet_line_is_ignored_with_one_stderr_line(env):
    (env / "bad.md").write_text("GOAL: do the thing\nEFFORT: turbo\n")
    config(env, executor_default_effort="low")
    r = run_spawn(env, packet="bad.md")
    assert meta(env, sid_of(r))["effort"] == "low"
    assert r.stderr.splitlines() == [
        "effort: the packet's EFFORT: line 'turbo' is not one of off/minimal/low/medium/high/xhigh/max — ignored"]
    assert events(env, "launch_options")[0]["effort_source"] == "config"


@pytest.mark.parametrize("bad", ["turbo", 3, None])
def test_an_invalid_config_effort_gives_high(env, bad):
    config(env, executor_default_effort=bad)
    r = run_spawn(env)
    assert meta(env, sid_of(r))["effort"] == "high"


# ── scope and keep ───────────────────────────────────────────────────────────────────────────────
def test_scope_flag_and_its_default(env):
    r = run_spawn(env, "--scope", "the auth module", topic="auth work")
    assert meta(env, sid_of(r))["scope"] == "the auth module"
    assert "effort=high scope=the auth module" in r.stdout.splitlines()
    r = run_spawn(env, topic="auth work")
    m = meta(env, sid_of(r))
    assert m["scope"] == m["topic"] == "auth-work"


def test_keep_pins_at_birth(env):
    r = run_spawn(env, "--keep")
    m = meta(env, sid_of(r))
    assert m["keep"] is True and m["keep_by"] == "lead-1"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", m["keep_at"])
    assert "pinned" in r.stdout.splitlines()
    assert events(env, "launch_options")[0]["keep"] is True
    r = run_spawn(env)
    m = meta(env, sid_of(r))
    assert "keep" not in m and "pinned" not in r.stdout.splitlines()
    assert events(env, "launch_options")[1]["keep"] is False


# ── stdout ───────────────────────────────────────────────────────────────────────────────────────
SPAWNED_RE = r"spawned topic-\d{6} model=\S+ tab=FAKE-UUID"
PLACEMENT = "placement=end-of-bar (no lead iTerm handle)"


def test_stdout_plain_spawn(env):
    lines = run_spawn(env, "--model", "sonnet").stdout.splitlines()
    assert len(lines) == 3
    assert re.fullmatch(SPAWNED_RE, lines[0]) and lines[1] == "effort=high scope=topic" and lines[2] == PLACEMENT


def test_stdout_every_line_in_order(env):
    models_json(env, without=("claude-opus-5",))
    config(env, executor_fallback_model="fable")
    r = run_spawn(env, "--model", "opus", "--model-override", "no opus here", "--effort", "xhigh",
                  "--scope", "all of it", "--keep")
    assert r.stdout.splitlines() == [
        f"spawned {sid_of(r)} model=anthropic/claude-fable-5 tab=FAKE-UUID",
        "effort=xhigh scope=all of it",
        "model: opus is not available here; using the fallback anthropic/claude-fable-5",
        "model: anthropic/claude-fable-5 is above the ceiling opus; override recorded",
        "pinned",
        PLACEMENT,
    ]
    assert re.fullmatch(SPAWNED_RE, r.stdout.splitlines()[0])


# ── lint advice ──────────────────────────────────────────────────────────────────────────────────
def _advice_lines(findings):
    return [f"  {'⚠' if level == 'warn' else 'ℹ'} lint[{code}]: {message}" for level, code, message in findings]


def test_lint_advice_is_printed_once_with_the_launched_model(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    body = "GOAL: investigate why the importer drops rows and find the root cause.\n"
    (env / "inv.md").write_text(body)
    models_json(env, without=("claude-opus-5",))
    config(env, executor_fallback_model="sonnet")
    r = run_spawn(env, "--model", "opus", packet="inv.md")
    want = _advice_lines(lint.lint_packet(body, cwd=str(env / "wt"), model="anthropic/claude-sonnet-5"))
    assert r.stderr.splitlines() == want
    assert any("lint[shape-opus]" in ln for ln in want)  # the fallback sonnet is linted, not the opus asked


def test_lint_advice_reports_an_unparsable_effort_line(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    body = "GOAL: do the thing\nEFFORT: turbo\n"
    (env / "bad.md").write_text(body)
    r = run_spawn(env, packet="bad.md")
    lines = r.stderr.splitlines()
    assert lines[0].startswith("effort: the packet's EFFORT: line 'turbo'")
    assert lines[1:] == _advice_lines(lint.lint_packet(body, cwd=str(env / "wt"), model="anthropic/claude-sonnet-5"))
    assert sum("lint[effort-unparsable]" in ln for ln in lines) == 1


def test_a_ceiling_refusal_prints_no_lint_line(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    r = run_spawn(env, "--model", "fable", check=False)
    assert r.returncode == 2 and "lint[" not in r.stderr and "lint[" not in r.stdout


# ── ledger ───────────────────────────────────────────────────────────────────────────────────────
def test_spawned_keeps_its_fields_and_launch_options_has_its_own(env):
    r = run_spawn(env, "--effort", "low", "--scope", "sc")
    sid = sid_of(r)
    (sp,) = events(env, "spawned")
    assert set(sp) == {"session_id", "lead", "worktree", "topic", "model", "tab_label", "source"}
    assert events(env, "launch_options") == [{"session_id": sid, "effort": "low", "effort_source": "flag",
                                              "scope": "sc", "keep": False}]
    assert events(env, "model_fallback") == [] and events(env, "model_ceiling_override") == []


def test_the_launch_args_carry_the_effort_fresh_and_resumed(env):
    """launch._pi_args hands meta.json's `effort` to build_pi_cmd as `thinking` for a fresh launch and a
    relaunch alike (change 1); a record with no `effort` hands None, so its line is unchanged."""
    from pilead import launch, state
    sid = sid_of(run_spawn(env, "--effort", "minimal"))
    m = meta(env, sid)
    for resume in (False, True):
        args = launch._pi_args(home(env), m, resume)
        assert args["thinking"] == "minimal" and args["resume"] is resume
        cmd = launch.backend.by_name("iterm").build_pi_cmd(
            str(state.session_dir(home(env), sid) / "packet-0001.md"), **args)
        assert f"--session-id {sid} " in cmd and "--thinking minimal -e " in cmd
    old = {k: v for k, v in m.items() if k != "effort"}
    assert launch._pi_args(home(env), old, True)["thinking"] is None


# ── --seed (a retired session's successor seed) ─────────────────────────────────────────────────
def retired(env):
    """A spawned session, reported and retired: its sid (its seed is sessions/<sid>/successor-seed.md)."""
    sid = sid_of(run_spawn(env))
    d = home(env) / "sessions" / sid
    (d / "report-0001.md").write_text("Did it.\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: x\n")
    (d / "status").write_text("reported\n")
    pilead("retire", sid)
    return sid


def test_seed_with_a_path(env):
    (env / "my-seed.md").write_text("# Successor seed — old\n\nthe index\n")
    r = run_spawn(env, "--seed", str(env / "my-seed.md"))
    sid = sid_of(r)
    path = str((env / "my-seed.md").resolve())
    assert meta(env, sid)["seed"] == path
    assert events(env, "seed_inherited") == [{"session_id": sid, "seed": path}]
    lines = r.stdout.splitlines()
    assert lines[-2:] == [f"seed={path}", PLACEMENT] and len(lines) == 4
    assert "the index" in (home(env) / "sessions" / sid / "packet-0001.md").read_text()


def test_seed_with_a_retired_sid(env):
    old = retired(env)
    r = run_spawn(env, "--seed", old)
    sid = sid_of(r)
    path = str((home(env) / "sessions" / old / "successor-seed.md").resolve())
    assert meta(env, sid)["seed"] == path
    assert r.stdout.splitlines()[-2:] == [f"seed={path}", PLACEMENT]
    text = (home(env) / "sessions" / sid / "packet-0001.md").read_text()
    assert f"Seed source: {path}\n\n# Successor seed — {old}\n" in text


def test_seed_that_is_neither_a_file_nor_a_retired_sid_exits_2(env):
    r = refused_leaves_home(env, "--seed", "no-such-thing")
    assert r.returncode == 2 and r.stdout == "" and len(r.stderr.splitlines()) == 1
    assert "no-such-thing" in r.stderr and "sessions/no-such-thing/successor-seed.md" in r.stderr
    assert "pilead retire" in r.stderr


def test_seed_of_a_session_that_was_never_retired_exits_2(env):
    sid = sid_of(run_spawn(env))
    before = tree(home(env))
    r = run_spawn(env, "--seed", sid, check=False, lead=False)
    assert r.returncode == 2 and f"sessions/{sid}/successor-seed.md" in r.stderr
    assert tree(home(env)) == before


def test_a_path_wins_over_a_sid_of_the_same_name(env, monkeypatch):
    old = retired(env)
    monkeypatch.chdir(env)  # bin/pilead runs here, so the relative path `old` names env/<old>
    (env / old).write_text("# a seed file that happens to share the sid's name\n")
    r = run_spawn(env, "--seed", old)
    sid = sid_of(r)
    assert meta(env, sid)["seed"] == str((env / old).resolve())
    text = (home(env) / "sessions" / sid / "packet-0001.md").read_text()
    assert "happens to share the sid's name" in text and f"# Successor seed — {old}" not in text


def test_spawned_keeps_its_fields_with_a_seed(env):
    (env / "s.md").write_text("seed\n")
    run_spawn(env, "--seed", str(env / "s.md"))
    (sp,) = events(env, "spawned")
    assert set(sp) == {"session_id", "lead", "worktree", "topic", "model", "tab_label", "source"}

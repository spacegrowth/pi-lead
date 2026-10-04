"""The model-tier posture at `pilead spawn`, `send --rotate` and `send --upgrade` (README "Model check and
model tier"): `manual` refuses without `--model`, `lead` runs the executor on the lead's own class, an
unreadable posture refuses, and nothing else changes. Driven through bin/pilead with the fake `pi` and the
recording `osascript` stub of tests/test_resume.py; `models.json` is written by the test and the lead's model
comes from a synthetic session log. No real tab is opened and the real pi never runs."""
import json
import sys
from pathlib import Path

import pytest

from test_close_variants import tree
from test_lineup import ALL, FABLE, HAIKU, OPUS, SONNET, models_json
from test_resume import (H, child, env, events, fake_pi_calls, mark_reported, meta, pilead,  # noqa: F401
                         run_bootstrap, sdir, terminal)
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import state  # noqa: E402

ASK = "pilead: tier is manual — no model chosen. Run: pilead tier ask --session lead-1 --packet {p}, then pass the answer as --model.\n"
SONNET_ID, HAIKU_ID, OPUS_ID, FABLE_ID = (f"anthropic/{m}" for m in (SONNET, HAIKU, OPUS, FABLE))
PRO_ID = "deepseek/deepseek-v4-pro"


@pytest.fixture(autouse=True)
def _models(env):
    models_json(env)


def tier(env, word):
    pilead("tier", word, "--session", "lead-1")


def lead_on(env, model, provider="anthropic"):
    """The lead's own session log: its last answer was on `model`."""
    cwd = json.loads((H(env) / "leads" / "lead-1.json").read_text())["cwd"]
    write_log("lead-1", cwd, [assistant(u(10, 5), provider=provider, model=model)])


def packet_at(env, name="p2.md", text="GOAL: the next thing\n"):
    (env / name).write_text(text)
    return str(env / name)


def spawn_cmd(env, *flags, topic="topic", check=False):
    return pilead("spawn", str(env / "wt"), topic, str(env / "packet.md"), "--lead", "lead-1", *flags, check=check)


def unreported(env, model="sonnet"):
    """A spawned session (under the posture then in force) with a session log and no report yet."""
    r = spawn_cmd(env, "--model", model, check=True)
    sid = r.stdout.split()[1]
    write_log(sid, (env / "wt").resolve(), [assistant(u(10, 5))])
    return sid


def ready(env, model="sonnet"):
    """As `unreported`, with packet 1 reported: what a rotate or a plain send starts from."""
    sid = unreported(env, model)
    mark_reported(env, sid)
    return sid


def launch_model(env, sid):
    """The `--model` of the launch line of session `sid` (the bootstrap run for real, fake_pi recording)."""
    n = len(fake_pi_calls(env))
    run_bootstrap(sdir(env, sid) / "bootstrap.sh")
    argv = fake_pi_calls(env)[n]["argv"]
    assert argv.count("--model") == 1, argv
    return argv[argv.index("--model") + 1]


def strip(ev):
    return {k: v for k, v in ev.items() if k not in ("ts", "event")}


def refused_unchanged(env, r, line):
    assert (r.returncode, r.stdout, r.stderr) == (2, "", line)


# ── manual ───────────────────────────────────────────────────────────────────────────────────────
def test_manual_refuses_a_spawn_with_no_model_and_leaves_home_as_it_was(env):
    tier(env, "manual")
    before = tree(H(env))
    r = spawn_cmd(env)
    refused_unchanged(env, r, ASK.format(p=env / "packet.md"))
    assert tree(H(env)) == before


def test_manual_refuses_a_rotate_and_an_upgrade_with_no_model(env):
    sid = ready(env)
    tier(env, "manual")
    before = tree(H(env))
    p2 = packet_at(env)
    for flag in ("--rotate", "--upgrade"):
        refused_unchanged(env, pilead("send", sid, p2, flag, check=False), ASK.format(p=p2))
        assert tree(H(env)) == before


def test_manual_with_a_model_behaves_as_under_auto(env):
    tier(env, "manual")
    r = spawn_cmd(env, "--model", "haiku")
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    assert meta(env, sid)["model"] == HAIKU_ID and r.stdout.splitlines()[0].startswith(f"spawned {sid} model={HAIKU_ID}")
    mark_reported(env, sid)
    r = pilead("send", sid, packet_at(env), "--rotate", "--model", "dspro", check=False)
    assert r.returncode == 0 and meta(env, f"{sid}-r2")["model"] == PRO_ID
    mark_reported(env, f"{sid}-r2")
    r = pilead("send", f"{sid}-r2", packet_at(env), "--upgrade", "--model", "opus", check=False)
    assert r.returncode == 0 and meta(env, f"{sid}-r3")["model"] == OPUS_ID
    assert events(env, "model_from_tier") == []


def test_manual_is_not_relaxed_by_the_autonomous_posture(env):
    sid = ready(env)
    tier(env, "manual")
    pilead("auto", "on", "--session", "lead-1")
    before = tree(H(env))
    refused_unchanged(env, spawn_cmd(env), ASK.format(p=env / "packet.md"))
    p2 = packet_at(env)
    for flag in ("--rotate", "--upgrade"):
        refused_unchanged(env, pilead("send", sid, p2, flag, check=False), ASK.format(p=p2))
    assert tree(H(env)) == before


def test_manual_with_no_option_available_gives_the_no_model_line(env):
    models_json(env, [("other", "x-1")])
    tier(env, "manual")
    before = tree(H(env))
    refused_unchanged(env, spawn_cmd(env), "pilead: no executor model is available on this machine — run pilead doctor\n")
    assert tree(H(env)) == before


# ── lead ─────────────────────────────────────────────────────────────────────────────────────────
def test_lead_spawns_on_the_leads_class_and_says_so(env):
    tier(env, "lead")
    lead_on(env, HAIKU)
    r = spawn_cmd(env)
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    lines = r.stdout.splitlines()
    assert lines[0] == f"spawned {sid} model={HAIKU_ID} tab=FAKE-UUID"
    assert lines[1] == f"effort=high scope=topic"
    assert lines[2] == f"model: tier lead — this lead runs {HAIKU_ID}; using {HAIKU_ID}"
    assert lines[3].startswith("placement=") and len(lines) == 4
    assert launch_model(env, sid) == HAIKU_ID
    (ev,) = events(env, "model_from_tier")
    assert strip(ev) == {"session_id": sid, "lead": "lead-1", "lead_model": HAIKU_ID, "model": HAIKU_ID}


def test_the_lead_class_is_resolved_like_any_alias(env):
    """The lead runs an older id of its class; the executor gets what the list resolves the class to."""
    tier(env, "lead")
    lead_on(env, "claude-opus-4-1")
    r = spawn_cmd(env)
    sid = r.stdout.split()[1]
    assert r.stdout.splitlines()[2] == f"model: tier lead — this lead runs anthropic/claude-opus-4-1; using {OPUS_ID}"
    assert launch_model(env, sid) == OPUS_ID
    assert strip(events(env, "model_from_tier")[0])["lead_model"] == "anthropic/claude-opus-4-1"


def test_the_line_sits_between_the_lines_printed_today(env):
    tier(env, "auto")
    def norm(r):
        sid = r.stdout.split()[1]
        return [ln.replace(sid, "SID") for ln in r.stdout.splitlines()]

    plain = norm(spawn_cmd(env, "--model", "haiku"))
    tier(env, "lead")
    lead_on(env, HAIKU)
    chosen = norm(spawn_cmd(env))
    tier_line = [ln for ln in chosen if ln.startswith("model: tier lead")]
    assert len(tier_line) == 1
    assert [ln for ln in chosen if ln not in tier_line] == plain  # nothing else moved or changed
    assert 0 < chosen.index(tier_line[0]) < len(chosen) - 1  # after `spawned`, before `placement=`


def test_a_lead_on_an_unranked_model_gives_that_exact_model(env):
    tier(env, "lead")
    lead_on(env, "deepseek-v4-pro", provider="deepseek")
    r = spawn_cmd(env)
    sid = r.stdout.split()[1]
    assert r.stdout.splitlines()[2] == f"model: tier lead — this lead runs {PRO_ID}; using {PRO_ID}"
    assert launch_model(env, sid) == PRO_ID
    assert strip(events(env, "model_from_tier")[0]) == {"session_id": sid, "lead": "lead-1", "lead_model": PRO_ID,
                                                       "model": PRO_ID}


def test_a_lead_whose_model_is_unknown_is_refused_and_home_is_unchanged(env):
    tier(env, "lead")
    before = tree(H(env))
    r = spawn_cmd(env)
    refused_unchanged(env, r, "pilead: tier is lead — but the model of lead lead-1 could not be read (its session "
                              "log holds no answer yet). Pass --model.\n")
    assert tree(H(env)) == before


def test_a_lead_class_above_the_ceiling_is_refused_by_the_ceiling_rule_and_launches_with_an_override(env):
    tier(env, "lead")
    lead_on(env, FABLE)
    before = tree(H(env))
    r = spawn_cmd(env)
    refused_unchanged(env, r, f'pilead: model {FABLE_ID} is above the ceiling opus; to launch it anyway pass '
                              '--model-override "<reason>"\n')
    assert tree(H(env)) == before
    r = spawn_cmd(env, "--model-override", "the lead wants it")
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    lines = r.stdout.splitlines()
    assert lines[2] == f"model: tier lead — this lead runs {FABLE_ID}; using {FABLE_ID}"
    assert lines[3] == f"model: {FABLE_ID} is above the ceiling opus; override recorded"
    assert launch_model(env, sid) == FABLE_ID
    assert [e["event"] for e in events(env) if e["event"] in ("model_ceiling_override", "model_from_tier")] == [
        "model_ceiling_override", "model_from_tier"]


def test_an_explicit_model_wins_over_the_lead_posture(env):
    tier(env, "lead")
    lead_on(env, HAIKU)
    r = spawn_cmd(env, "--model", "opus")
    sid = r.stdout.split()[1]
    assert not any(ln.startswith("model: tier") for ln in r.stdout.splitlines())
    assert launch_model(env, sid) == OPUS_ID and events(env, "model_from_tier") == []


def test_the_fallback_applies_to_the_leads_class(env):
    """The lead's class matches nothing here: the configured fallback is used, as for any chosen model."""
    tier(env, "lead")
    lead_on(env, HAIKU)
    models_json(env, [m for m in ALL if m[1] != HAIKU])
    (H(env) / "config.json").write_text(json.dumps({"executor_fallback_model": "dsflash"}))
    r = spawn_cmd(env)
    sid = r.stdout.split()[1]
    lines = r.stdout.splitlines()
    assert lines[2] == f"model: tier lead — this lead runs {HAIKU_ID}; using deepseek/deepseek-flash"
    assert lines[3] == "model: haiku is not available here; using the fallback deepseek/deepseek-flash"
    assert meta(env, sid)["model"] == "deepseek/deepseek-flash"


def test_lead_rotate_with_no_model_uses_the_leads_class(env):
    sid = ready(env)
    tier(env, "lead")
    lead_on(env, HAIKU)
    r = pilead("send", sid, packet_at(env), "--rotate", check=False)
    assert r.returncode == 0, r.stderr
    succ = f"{sid}-r2"
    lines = r.stdout.splitlines()
    assert lines[0] == f"rotating {sid} → retire + spawn {succ} on {HAIKU_ID}"
    assert lines[1] == f"model: tier lead — this lead runs {HAIKU_ID}; using {HAIKU_ID}"
    assert meta(env, succ)["model"] == HAIKU_ID and launch_model(env, succ) == HAIKU_ID
    (ev,) = events(env, "model_from_tier")
    assert strip(ev) == {"session_id": succ, "lead": "lead-1", "lead_model": HAIKU_ID, "model": HAIKU_ID}


def test_lead_rotate_of_a_paused_session_with_a_fallback_uses_the_fallback(env):
    (H(env) / "config.json").write_text(json.dumps({"executor_fallback_model": "dsflash"}))
    sid = unreported(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    tier(env, "lead")
    lead_on(env, HAIKU)
    r = pilead("send", sid, packet_at(env), "--rotate", check=False)
    assert r.returncode == 0, r.stderr
    assert meta(env, f"{sid}-r2")["model"] == "deepseek/deepseek-flash"
    assert not any(ln.startswith("model: tier") for ln in r.stdout.splitlines())
    assert events(env, "model_from_tier") == []


def test_lead_rotate_of_a_paused_session_with_a_fallback_needs_no_readable_lead_model(env):
    (H(env) / "config.json").write_text(json.dumps({"executor_fallback_model": "dsflash"}))
    sid = unreported(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    tier(env, "lead")  # no lead log: the lead model is unknown, and is not needed
    assert pilead("send", sid, packet_at(env), "--rotate", check=False).returncode == 0


def test_lead_rotate_with_an_unknown_lead_model_is_refused_and_home_is_unchanged(env):
    sid = ready(env)
    tier(env, "lead")
    before = tree(H(env))
    r = pilead("send", sid, packet_at(env), "--rotate", check=False)
    assert r.returncode == 2 and r.stdout == "" and "tier is lead — but the model of lead lead-1" in r.stderr
    assert tree(H(env)) == before


def test_lead_upgrade_moves_one_tier_up_from_the_sessions_own_model(env):
    sid = ready(env)  # sonnet
    tier(env, "lead")
    lead_on(env, HAIKU)  # the lead's own class does not matter to an upgrade
    r = pilead("send", sid, packet_at(env), "--upgrade", check=False)
    assert r.returncode == 0, r.stderr
    succ = f"{sid}-r2"
    assert meta(env, succ)["model"] == OPUS_ID
    assert not any(ln.startswith("model: tier") for ln in r.stdout.splitlines())
    assert events(env, "model_from_tier") == []


def test_lead_upgrade_needs_no_readable_lead_model(env):
    sid = ready(env)
    tier(env, "lead")
    assert pilead("send", sid, packet_at(env), "--upgrade", check=False).returncode == 0


def test_a_rotate_with_a_model_wins_over_the_lead_posture(env):
    sid = ready(env)
    tier(env, "lead")
    lead_on(env, HAIKU)
    r = pilead("send", sid, packet_at(env), "--rotate", "--model", "opus", check=False)
    assert meta(env, f"{sid}-r2")["model"] == OPUS_ID
    assert not any(ln.startswith("model: tier") for ln in r.stdout.splitlines())
    assert events(env, "model_from_tier") == []


# ── a posture that could not be read ─────────────────────────────────────────────────────────────
def unreadable(env, value="manul"):
    p = H(env) / "leads" / "lead-1.json"
    rec = json.loads(p.read_text())
    rec["tier"] = value
    p.write_text(json.dumps(rec))


@pytest.mark.parametrize("value", ["manul", 7, None])
def test_an_unreadable_posture_refuses_with_no_model_and_allows_one(env, value):
    sid = ready(env)
    unreadable(env, value)
    before = tree(H(env))
    line = "pilead: tier posture of lead lead-1 could not be read — run pilead tier auto, or pass --model\n"
    refused_unchanged(env, spawn_cmd(env), line)
    p2 = packet_at(env)
    for flag in ("--rotate", "--upgrade"):
        refused_unchanged(env, pilead("send", sid, p2, flag, check=False), line)
    assert tree(H(env)) == before
    r = spawn_cmd(env, "--model", "haiku")
    assert r.returncode == 0, r.stderr
    assert meta(env, r.stdout.split()[1])["model"] == HAIKU_ID
    assert pilead("send", sid, p2, "--rotate", "--model", "sonnet", check=False).returncode == 0


# ── what the posture does not touch ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("word", ["auto", "manual", "lead"])
def test_a_plain_send_resume_and_restart_are_the_same_under_every_posture(env, word):
    sid = ready(env)  # sonnet
    tier(env, word)
    lead_on(env, HAIKU)  # under `lead` this would differ if the posture reached these verbs
    before = len(events(env))
    r = pilead("send", sid, packet_at(env), check=False)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == f"sent {sid} packet 0002"
    pilead("close", sid)
    r = pilead("resume", sid, check=False)
    assert r.returncode == 0 and r.stdout.splitlines()[0] == f"resumed {sid} tab=FAKE-UUID"
    assert meta(env, sid)["model"] == SONNET_ID
    pilead("close", sid)
    r = pilead("restart", sid, check=False)
    assert r.returncode == 0, r.stderr
    succ = f"{sid}-r2"
    assert meta(env, succ)["model"] == SONNET_ID and r.stdout.splitlines()[0].startswith(f"spawned {succ} model={SONNET_ID}")
    assert not any(ln.startswith("model: tier") for ln in r.stdout.splitlines())
    assert events(env, "model_from_tier") == [] and len(events(env)) > before


@pytest.mark.parametrize("word", ["auto", "manual", "lead"])
def test_a_queued_send_is_the_same_under_every_posture(env, word):
    sid = unreported(env)
    tier(env, word)
    r = pilead("send", sid, packet_at(env), "--when-idle", check=False)
    assert r.returncode == 0 and r.stdout.splitlines()[0].startswith(f"queued {sid} #")


@pytest.mark.parametrize("word", ["auto", "lead"])
def test_the_events_keep_exactly_their_fields(env, word):
    tier(env, word)
    if word == "lead":
        lead_on(env, HAIKU)
    r = spawn_cmd(env, *([] if word == "lead" else ["--model", "haiku"]))
    sid = r.stdout.split()[1]
    mark_reported(env, sid)
    pilead("send", sid, packet_at(env), "--rotate", "--model", "sonnet")
    (spawned,) = [e for e in events(env, "spawned") if e["session_id"] == sid]
    assert set(spawned) - {"ts", "event"} == {"session_id", "lead", "worktree", "topic", "model", "tab_label", "source"}
    (opts,) = [e for e in events(env, "launch_options") if e["session_id"] == sid]
    assert set(opts) - {"ts", "event"} == {"session_id", "effort", "effort_source", "scope", "keep"}
    (rot,) = events(env, "rotated")
    assert set(rot) - {"ts", "event"} == {"session_id", "successor", "model", "upgrade", "from_tier"}

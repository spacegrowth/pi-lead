"""`pilead send --rotate` / `--upgrade`: retire the session (`pilead retire`) and spawn its successor over the
same territory with the retired session's seed and THIS packet as its packet 1 — `--upgrade` one tier up.
Driven through bin/pilead with the fake `pi` and the terminal seam of tests/test_resume.py (a recording
`osascript` stub, a `ps` stub first on PATH). No real tab is opened or closed and the real pi never runs."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_close_variants import tree
from test_resume import (H, env, events, mark_reported, meta, pilead, refused, sdir, spawn,  # noqa: F401
                         terminal)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import lint, seed, state  # noqa: E402

ALL_MODELS = [("anthropic", "claude-sonnet-5"), ("deepseek", "deepseek-flash"), ("anthropic", "claude-haiku-4-5"),
              ("anthropic", "claude-opus-5"), ("anthropic", "claude-fable-5"), ("deepseek", "deepseek-v4-pro")]


def git_wt(env):
    wt = env / "wt"
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(wt), "init", "-q"], check=True, env=e)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "seed"], check=True, env=e)


def config(env, **kv):
    (H(env) / "config.json").write_text(json.dumps(kv) + "\n")


def models_json(env, without=()):
    (H(env) / "models.json").write_text(json.dumps({"fetched": int(time.time()), "models": [
        {"provider": p, "id": i, "context": "1M"} for p, i in ALL_MODELS if i not in without]}))


def packet2(env, text="GOAL: the next thing\n"):
    (env / "p2.md").write_text(text)
    return str(env / "p2.md")


def ready(env, **kw):
    """A spawned session whose packet 1 is reported: what a rotate starts from."""
    sid = spawn(env, **kw)
    mark_reported(env, sid)
    return sid


def spawn_on(env, model, *extra):
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", model, "--lead", "lead-1",
               *extra)
    sid = r.stdout.split()[1]
    mark_reported(env, sid)
    return sid


def rotate(env, sid, *flags, check=True, extra_env=None):
    return pilead("send", sid, packet2(env), *flags, check=check, env=extra_env)


# ── a rotate ────────────────────────────────────────────────────────────────────────────────────
def test_rotate_retires_and_spawns_the_seeded_successor(env):
    sid = ready(env)
    r = rotate(env, sid, "--rotate")
    succ = f"{sid}-r2"
    seed_path = sdir(env, sid) / "successor-seed.md"
    lines = r.stdout.splitlines()
    assert lines[0] == f"rotating {sid} → retire + spawn {succ} on anthropic/claude-sonnet-5"
    assert lines[1:5] == [f"closed {sid} (superseded by successor-seed)",
                          f"retired {sid} — successor seed (1 packet(s)) at:", f"  {seed_path}",
                          f"  spawn its successor with: {seed.spawn_command(meta(env, sid), sid)}"]
    assert lines[5] == f"spawned {succ} model=anthropic/claude-sonnet-5 tab=FAKE-UUID"
    assert lines[-3] == f"seed={seed_path.resolve()}" and lines[-2].startswith("placement=")
    assert lines[-1] == f"successor: {succ}"
    assert r.stderr == ""
    # the successor's packet 1 is THIS packet, the seed inherited, the footer last
    text = (sdir(env, succ) / "packet-0001.md").read_text()
    footer = state.footer(H(env), succ, 1)
    assert text.startswith("GOAL: the next thing\n\n---\n(pilead — INHERITED CONTEXT") and text.endswith(footer)
    assert seed_path.read_text().strip() in text
    new = meta(env, succ)
    assert new["seed"] == str(seed_path.resolve()) and new["packets"] == 1 and not new.get("keep")
    old = meta(env, sid)
    for k in ("worktree", "topic", "scope", "lead", "layout", "effort", "model"):
        assert new[k] == old[k], k
    assert old["superseded_by"] == "successor-seed" and (sdir(env, sid) / "status").read_text() == "closed\n"
    assert old["packets"] == 1  # the packet went to the successor, not into the retired session


def test_the_rotated_event_and_the_events_around_it(env):
    sid = ready(env)
    rotate(env, sid, "--rotate")
    succ = f"{sid}-r2"
    (ev,) = events(env, "rotated")
    assert {k: v for k, v in ev.items() if k != "ts"} == {
        "event": "rotated", "session_id": sid, "successor": succ, "model": "anthropic/claude-sonnet-5",
        "upgrade": False, "from_tier": "sonnet"}
    names = [e["event"] for e in events(env)]
    i = names.index("retired")
    assert names[i - 1] == "superseded" and names[i + 1:i + 3] == ["spawned", "launch_options"]
    assert "seed_inherited" in names[i:] and names[-1] == "rotated"
    (opts,) = [e for e in events(env, "launch_options") if e["session_id"] == succ]
    assert (opts["effort"], opts["effort_source"]) == ("high", "inherited")
    (sp,) = [e for e in events(env, "spawned") if e["session_id"] == succ]
    assert set(sp) - {"ts", "event"} == {"session_id", "lead", "worktree", "topic", "model", "tab_label", "source"}
    assert sp["source"] == str((env / "p2.md").resolve())


def test_the_successor_has_its_own_refs_baseline_and_the_old_ones_are_untouched(env):
    git_wt(env)
    sid = ready(env)
    refs_before = {k: v for k, v in tree(sdir(env, sid)).items() if k.startswith("refs-")}
    assert "refs-0001.json" in refs_before
    rotate(env, sid, "--rotate")
    assert (sdir(env, f"{sid}-r2") / "refs-0001.json").is_file()
    assert {k: v for k, v in tree(sdir(env, sid)).items() if k.startswith("refs-")} == refs_before


def test_name_gives_the_successor_exactly_that_sid(env):
    sid = ready(env)
    r = rotate(env, sid, "--rotate", "--name", "fresh-one")
    assert r.stdout.splitlines()[-1] == "successor: fresh-one"
    assert meta(env, "fresh-one")["seed"].endswith(f"/{sid}/successor-seed.md")
    assert not (H(env) / "sessions" / f"{sid}-r2").exists()


def test_a_heavy_session_rotates_without_heavy_override(env):
    from test_cli_core import heavy_entries
    from test_usage import write_log
    sid = ready(env, log=False)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    refused_plain = pilead("send", sid, packet2(env), check=False)
    assert refused_plain.returncode == 2 and "is heavy" in refused_plain.stderr
    r = rotate(env, sid, "--rotate")
    assert r.stdout.splitlines()[-1] == f"successor: {sid}-r2"
    assert not events(env, "heavy_override")
    assert "- Hint: this session ran HEAVY" in (sdir(env, sid) / "successor-seed.md").read_text()


def test_the_heavy_refusal_names_rotate_on_one_line(env):
    from test_cli_core import heavy_entries
    from test_usage import write_log
    sid = ready(env, log=False)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    r = pilead("send", sid, packet2(env), check=False)
    assert len(r.stderr.splitlines()) == 1
    assert f"`pilead send {sid} <packet> --rotate`" in r.stderr


def test_a_paused_session_rotates_onto_the_fallback(env):
    config(env, executor_fallback_model="dsflash")
    sid = spawn(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    r = rotate(env, sid, "--rotate")
    succ = f"{sid}-r2"
    assert meta(env, succ)["model"] == "deepseek/deepseek-flash"
    assert r.stdout.splitlines()[0] == f"rotating {sid} → retire + spawn {succ} on deepseek/deepseek-flash"
    assert (f"model: {sid} was paused at its usage limit on anthropic/claude-sonnet-5; its successor runs on the "
            "fallback deepseek/deepseek-flash") in r.stdout.splitlines()


def test_a_paused_session_without_a_fallback_keeps_its_model(env):
    sid = spawn(env)
    (sdir(env, sid) / "status").write_text("paused\n")
    rotate(env, sid, "--rotate")
    assert meta(env, f"{sid}-r2")["model"] == "anthropic/claude-sonnet-5"


def test_rotate_with_model(env):
    sid = ready(env)
    rotate(env, sid, "--rotate", "--model", "dspro")
    assert meta(env, f"{sid}-r2")["model"] == "deepseek/deepseek-v4-pro"


def test_a_session_with_no_stored_effort_takes_the_config_default(env):
    config(env, executor_default_effort="low")
    sid = ready(env)
    m = meta(env, sid)
    del m["effort"]
    (sdir(env, sid) / "meta.json").write_text(json.dumps(m, indent=2) + "\n")
    rotate(env, sid, "--rotate")
    (opts,) = [e for e in events(env, "launch_options") if e["session_id"] == f"{sid}-r2"]
    assert (opts["effort"], opts["effort_source"]) == ("low", "config")


# ── --upgrade ───────────────────────────────────────────────────────────────────────────────────
def test_upgrade_from_sonnet_goes_to_opus(env):
    sid = ready(env)
    r = rotate(env, sid, "--upgrade")
    succ = f"{sid}-r2"
    assert r.stdout.splitlines()[0] == (f"upgrading {sid} sonnet → opus: retire + spawn {succ} on "
                                        "anthropic/claude-opus-5")
    assert meta(env, succ)["model"] == "anthropic/claude-opus-5"
    (ev,) = events(env, "rotated")
    assert (ev["upgrade"], ev["from_tier"], ev["model"]) == (True, "sonnet", "anthropic/claude-opus-5")


def test_upgrade_skips_a_tier_that_does_not_resolve(env):
    sid = ready(env)
    models_json(env, without=("claude-opus-5",))
    config(env, executor_model_ceiling="fable")
    r = rotate(env, sid, "--upgrade")
    assert meta(env, f"{sid}-r2")["model"] == "anthropic/claude-fable-5"
    assert "model: skipped opus (no model for it in models.json)" in r.stdout.splitlines()


def test_upgrade_refuses_when_no_tier_above_resolves(env):
    sid = ready(env)
    models_json(env, without=("claude-opus-5", "claude-fable-5"))
    before = tree(H(env))
    refused(rotate(env, sid, "--upgrade", check=False), "no tier above it resolves", "opus, fable")
    assert tree(H(env)) == before


def test_upgrade_refuses_at_the_top_tier(env):
    sid = spawn_on(env, "fable", "--model-override", "test")
    before = tree(H(env))
    refused(rotate(env, sid, "--upgrade", check=False), "already runs fable, the top tier")
    assert tree(H(env)) == before


def test_upgrade_refuses_a_model_with_no_tier_naming_model(env):
    sid = spawn_on(env, "dspro")
    before = tree(H(env))
    refused(rotate(env, sid, "--upgrade", check=False), "has no tier", "--model")
    assert tree(H(env)) == before


def test_upgrade_with_model_uses_that_model(env):
    sid = spawn_on(env, "dspro")
    r = rotate(env, sid, "--upgrade", "--model", "opus")
    assert r.stdout.splitlines()[0].startswith(f"upgrading {sid} - → opus: ")
    (ev,) = events(env, "rotated")
    assert (ev["from_tier"], ev["model"]) == (None, "anthropic/claude-opus-5")


def test_upgrade_respects_the_ceiling_and_model_override(env):
    sid = spawn_on(env, "opus")
    before = tree(H(env))
    refused(rotate(env, sid, "--upgrade", check=False), "above the ceiling opus", "--model-override")
    assert tree(H(env)) == before
    r = rotate(env, sid, "--upgrade", "--model-override", "the hard part")
    assert meta(env, f"{sid}-r2")["model"] == "anthropic/claude-fable-5"
    assert "model: anthropic/claude-fable-5 is above the ceiling opus; override recorded" in r.stdout
    (ov,) = events(env, "model_ceiling_override")
    assert (ov["session_id"], ov["reason"]) == (f"{sid}-r2", "the hard part")


# ── refusals: home byte-identical ───────────────────────────────────────────────────────────────
def test_a_busy_unreported_session_is_refused_naming_retire_force_then_spawn(env):
    sid = spawn(env)
    before = tree(H(env))
    r = rotate(env, sid, "--rotate", check=False)
    refused(r, "busy on packet 0001", f"pilead retire {sid} --force", f"--seed {sid}", "Nothing was sent")
    assert r.stderr.index(f"pilead retire {sid} --force") < r.stderr.index("pilead spawn ")
    assert tree(H(env)) == before


def test_a_pinned_session_is_refused(env):
    sid = ready(env)
    pilead("keep", sid)
    before = tree(H(env))
    refused(rotate(env, sid, "--upgrade", check=False), "pinned", f"pilead keep {sid} --off")
    assert tree(H(env)) == before


def test_an_already_retired_session_is_refused(env):
    sid = ready(env)
    pilead("retire", sid)
    before = tree(H(env))
    refused(rotate(env, sid, "--rotate", check=False), "already retired", f"--seed {sid}")
    assert tree(H(env)) == before


def test_a_missing_packet_is_refused(env):
    sid = ready(env)
    before = tree(H(env))
    r = pilead("send", sid, str(env / "nope.md"), "--rotate", check=False)
    refused(r, "not found")
    assert tree(H(env)) == before


def test_a_model_above_the_ceiling_is_refused(env):
    sid = ready(env)
    before = tree(H(env))
    refused(rotate(env, sid, "--rotate", "--model", "fable", check=False), "above the ceiling")
    assert tree(H(env)) == before


@pytest.mark.parametrize("name,why", [("taken", "already exists"), ("bad name", "not usable"),
                                      ("-dash", "not usable"), ("a..b", "not usable"), ("trailing.", "not usable")])
def test_a_taken_or_unusable_name_is_refused(env, name, why):
    sid = ready(env)
    if name == "taken":
        (H(env) / "sessions" / "taken").mkdir()
    before = tree(H(env))
    refused(rotate(env, sid, "--rotate", f"--name={name}", check=False), why)
    assert tree(H(env)) == before


@pytest.mark.parametrize("flags", [["--rotate", "--upgrade"], ["--model", "opus"], ["--name", "x"],
                                   ["--model-override", "why"]])
def test_flag_combinations_exit_2(env, flags):
    sid = ready(env)
    before = tree(H(env))
    refused(rotate(env, sid, *flags, check=False))
    assert tree(H(env)) == before


def test_other_sessions_are_untouched(env):
    a = ready(env)
    b = ready(env)
    other = {k: v for k, v in tree(H(env)).items() if k.startswith(f"sessions/{b}") or k.startswith("leads/")}
    rotate(env, a, "--rotate")
    assert {k: v for k, v in tree(H(env)).items()
            if k.startswith(f"sessions/{b}") or k.startswith("leads/")} == other


def test_rotate_deletes_no_file_of_the_retired_session(env):
    sid = ready(env)
    names = {p.name for p in sdir(env, sid).iterdir()}
    rotate(env, sid, "--rotate")
    assert names <= {p.name for p in sdir(env, sid).iterdir()}


# ── a failing successor launch ──────────────────────────────────────────────────────────────────
def test_a_failing_launch_leaves_the_session_retired_and_no_successor(env):
    sid = ready(env)
    r = rotate(env, sid, "--rotate", check=False, extra_env={"FAKE_SPAWN": "fail"})
    assert r.returncode == 1
    assert len(r.stderr.splitlines()) == 1
    assert f"{sid} was retired, but its successor {sid}-r2 did not launch" in r.stderr
    assert "pilead spawn " in r.stderr and f"--seed {sid}" in r.stderr
    assert meta(env, sid)["superseded_by"] == "successor-seed"
    assert (sdir(env, sid) / "successor-seed.md").is_file()
    assert not (H(env) / "sessions" / f"{sid}-r2").exists()
    assert not events(env, "rotated") and events(env, "retired")


# ── lint advice ─────────────────────────────────────────────────────────────────────────────────
def test_lint_advice_once_with_the_successors_model(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    body = "GOAL: investigate why the importer drops rows and find the root cause.\n"
    sid = ready(env)
    r = pilead("send", sid, packet2(env, body), "--upgrade")
    want = [f"  {'⚠' if lv == 'warn' else 'ℹ'} lint[{code}]: {msg}"
            for lv, code, msg in lint.lint_packet(body, cwd=str((env / "wt").resolve()),
                                                  model="anthropic/claude-opus-5")]
    assert want and r.stderr.splitlines() == want


def test_a_refused_rotate_prints_no_lint_line(env, monkeypatch):
    monkeypatch.delenv("PILEAD_NO_LINT", raising=False)
    sid = spawn(env)  # busy, unreported: refused
    r = pilead("send", sid, packet2(env, "GOAL: investigate the root cause.\n"), "--rotate", check=False)
    assert r.returncode == 2 and "lint[" not in r.stderr and "lint[" not in r.stdout


# ── p22: the successor names the lead its predecessor names after the claim ─────────────────────────
@pytest.mark.parametrize("flag", ["--rotate", "--upgrade"])
def test_a_rotate_or_upgrade_from_a_lead_adopts_an_orphan_and_the_successor_names_the_caller(env, flag):
    sid = ready(env)
    (H(env) / "leads" / "lead-1.json").unlink()
    pilead("lead-start", "lead-2", "--project", "other")
    r = rotate(env, sid, flag, extra_env={"PI_LEAD_SID": "lead-2"})
    succ = f"{sid}-r2"
    assert r.stdout.splitlines()[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert r.stdout.splitlines()[-1] == f"successor: {succ}"
    assert meta(env, sid)["lead"] == "lead-2" and meta(env, succ)["lead"] == "lead-2"


def test_a_rotate_with_no_caller_keeps_the_old_lead(env):
    sid = ready(env)
    (H(env) / "leads" / "lead-1.json").unlink()
    r = rotate(env, sid, "--rotate")
    assert not r.stdout.startswith("adopted") and meta(env, f"{sid}-r2")["lead"] == "lead-1"

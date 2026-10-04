"""`pilead tier auto|manual|lead|status|ask` and the posture reset in `pilead lead-start`, driven through
bin/pilead (no terminal is asked: conftest's recording osascript stub is first on PATH; `models.json` is
written by the test, and the lead's model comes from a synthetic session log)."""
import json
import os
import sys
from pathlib import Path

import pytest

from test_auto import (H, events, fields, ok, rec, rec_path, run, set_config,  # noqa: F401
                       snapshot)
from test_lineup import ALL, FABLE, HAIKU, OPUS, SONNET, failing_pi, lead as make_lead, models_json
from test_lint import CLEAN_BODY, OPUS_SHAPED_BODY, SHAPED_BODY

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import posture  # noqa: E402

RESET = " pilead lead-start resets it to auto."
BY_COMMAND = "set by pilead tier in this session"
AT_START = "the default at lead-start"
MEANS = {"auto": "the lead chooses each executor's model by the rubric.",
         "manual": "the human chooses at every spawn, rotate and upgrade: those refuse without --model and point "
                   "at pilead tier ask.",
         "lead": "an executor with no --model runs on this lead's own class; the ceiling still applies."}


def status_line(tier, name, origin):
    return f"model-tier posture is {tier.upper()} for lead '{name}' ({origin}) — {MEANS[tier]}\n"


SET_LINES = {
    "auto": "model-tier posture AUTO for lead '{n}' — the lead chooses each executor's model by the rubric again."
            + RESET + "\n",
    "manual": "model-tier posture MANUAL for lead '{n}' — every spawn, rotate and upgrade without --model now "
              "refuses, under the autonomous posture too. Run pilead tier ask --session lead-1 --packet <packet> "
              "first, then pass the answer as --model." + RESET + "\n",
    "lead": "model-tier posture LEAD for lead '{n}' — an executor with no --model now runs on this lead's own "
            "class; the ceiling still applies and --model still wins." + RESET + "\n",
}


def tier_events(tmp_path):
    return events(tmp_path, "tier_set")


def write_rec(tmp_path, rec_, sid="lead-1"):
    p = rec_path(tmp_path, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec_))
    return p


# ── lead-start ───────────────────────────────────────────────────────────────────────────────────
def test_lead_start_writes_tier_auto_from_config_and_prints_nothing_about_it(tmp_path):
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert r.stdout.splitlines() == [f"lead lead-1 ready inbox={H(tmp_path) / 'leads' / 'lead-1.inbox.md'}"]
    got = rec(tmp_path)
    assert (got["tier"], got["tier_source"]) == ("auto", "config")
    assert tier_events(tmp_path) == []


@pytest.mark.parametrize("word", ["manual", "lead"])
def test_lead_start_resets_a_posture_and_ledgers_it(tmp_path, word):
    ok("lead-start", "lead-1", "--project", "proj")
    started = rec(tmp_path)["started"]
    ok("tier", word, "--session", "lead-1")
    assert rec(tmp_path)["tier"] == word
    r = ok("lead-start", "lead-1", "--project", "proj")
    assert len(r.stdout.splitlines()) == 1
    got = rec(tmp_path)
    assert (got["tier"], got["tier_source"], got["started"]) == ("auto", "config", started)
    last = tier_events(tmp_path)[-1]
    assert fields(last) == {"session_id": "lead-1", "project": "proj", "tier": "auto", "was": word, "source": "arm"}
    n = len(tier_events(tmp_path))
    ok("lead-start", "lead-1", "--project", "proj")  # auto before, auto after: no event
    assert len(tier_events(tmp_path)) == n


def test_lead_start_after_tier_auto_writes_no_event(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    ok("tier", "manual", "--session", "lead-1")
    ok("tier", "auto", "--session", "lead-1")
    n = len(tier_events(tmp_path))
    assert n == 2
    ok("lead-start", "lead-1", "--project", "proj")
    assert len(tier_events(tmp_path)) == n
    assert rec(tmp_path)["tier_source"] == "config"


def test_lead_start_never_reads_an_unreadable_posture_as_manual_or_lead(tmp_path):
    write_rec(tmp_path, {"sid": "lead-1", "project": "proj", "tier": "manul"})
    ok("lead-start", "lead-1", "--project", "proj")
    assert rec(tmp_path)["tier"] == "auto" and tier_events(tmp_path) == []


# ── auto / manual / lead ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("word", ["auto", "manual", "lead"])
def test_each_word_sets_the_record_the_event_and_prints_its_line(tmp_path, word):
    ok("lead-start", "lead-1", "--project", "proj")
    before = rec(tmp_path)
    others = {k: v for k, v in before.items() if k not in ("tier", "tier_source")}
    r = ok("tier", word, "--session", "lead-1")
    assert (r.stdout, r.stderr) == (SET_LINES[word].format(n="proj"), "")
    got = rec(tmp_path)
    assert (got["tier"], got["tier_source"]) == (word, "command")
    assert {k: v for k, v in got.items() if k not in ("tier", "tier_source")} == others
    (e,) = tier_events(tmp_path)
    assert fields(e) == {"session_id": "lead-1", "project": "proj", "tier": word, "was": "auto",
                         "source": "command"}


def test_the_event_carries_the_earlier_posture_also_when_nothing_changes(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    for word, was in (("manual", "auto"), ("manual", "manual"), ("lead", "manual"), ("auto", "lead"),
                      ("auto", "auto")):
        ok("tier", word, "--session", "lead-1")
        e = tier_events(tmp_path)[-1]
        assert (e["tier"], e["was"], e["source"]) == (word, was, "command")
    assert len(tier_events(tmp_path)) == 5


def test_a_posture_that_could_not_be_read_is_set_again_with_was_null(tmp_path):
    write_rec(tmp_path, {"sid": "lead-1", "project": "proj", "tier": 7})
    ok("tier", "lead", "--session", "lead-1")
    assert rec(tmp_path)["tier"] == "lead" and tier_events(tmp_path)[-1]["was"] is None


def test_a_lead_with_no_project_is_named_by_its_sid(tmp_path):
    write_rec(tmp_path, {"sid": "lead-1", "project": None})
    assert ok("tier", "manual", "--session", "lead-1").stdout == SET_LINES["manual"].format(n="lead-1")
    assert ok("tier", "status", "--session", "lead-1").stdout == status_line("manual", "lead-1", BY_COMMAND)


def test_the_tier_leaves_the_autonomous_posture_alone(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    ok("auto", "on", "--session", "lead-1")
    ok("tier", "manual", "--session", "lead-1")
    got = rec(tmp_path)
    assert (got["autonomous"], got["autonomous_source"], got["tier"]) == (True, "command", "manual")


# ── status ───────────────────────────────────────────────────────────────────────────────────────
def test_status_for_each_posture_and_both_origins_changes_nothing(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    for word in (None, "manual", "lead", "auto"):
        if word:
            ok("tier", word, "--session", "lead-1")
        before = snapshot(H(tmp_path))
        r = ok("tier", "status", "--session", "lead-1")
        assert snapshot(H(tmp_path)) == before
        if word is None:
            assert r.stdout == status_line("auto", "proj", AT_START)
        else:
            assert r.stdout == status_line(word, "proj", BY_COMMAND)
        assert r.stderr == ""


def test_status_of_a_record_with_no_tier_field_is_auto_from_the_start(tmp_path):
    write_rec(tmp_path, {"sid": "lead-1", "project": "proj"})
    assert ok("tier", "status", "--session", "lead-1").stdout == status_line("auto", "proj", AT_START)


@pytest.mark.parametrize("value,shown", [("manul", '"manul"'), (7, "7"), (None, "null"), (["a"], '["a"]')])
def test_a_posture_that_could_not_be_read_exits_1_with_its_line(tmp_path, value, shown):
    write_rec(tmp_path, {"sid": "lead-1", "project": "proj", "tier": value})
    before = snapshot(H(tmp_path))
    r = run("tier", "status", "--session", "lead-1")
    assert r.returncode == 1 and r.stderr == ""
    assert r.stdout == (f"model-tier posture could not be read for lead 'proj' (tier is {shown}) — pilead tier "
                        "auto sets it again\n")
    assert snapshot(H(tmp_path)) == before


# ── which lead, and the refusals of `pilead auto` ────────────────────────────────────────────────
def test_the_sid_comes_from_session_then_pi_lead_sid_then_pi_session_id(tmp_path):
    for sid, project in (("lead-a", "pa"), ("lead-b", "pb"), ("lead-c", "pc")):
        ok("lead-start", sid, "--project", project)
    both = {"PI_LEAD_SID": "lead-b", "PI_SESSION_ID": "lead-c"}
    assert "'pa'" in ok("tier", "status", "--session", "lead-a", env=both).stdout
    assert "'pb'" in ok("tier", "status", env=both).stdout
    assert "'pc'" in ok("tier", "status", env={"PI_SESSION_ID": "lead-c"}).stdout
    ok("tier", "manual", env=both)
    assert [rec(tmp_path, s)["tier"] for s in ("lead-a", "lead-b", "lead-c")] == ["auto", "manual", "auto"]


@pytest.mark.parametrize("action", ["auto", "manual", "lead", "status"])
def test_with_no_sid_it_exits_2(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("tier", action, drop=("PI_LEAD_SID", "PI_SESSION_ID"))
    assert r.returncode == 2 and r.stdout == ""
    assert "tier: no lead session id — pass --session, or run it inside the lead's own pi session" in r.stderr
    r = run("tier", action, env={"PI_LEAD_SID": "", "PI_SESSION_ID": ""})
    assert r.returncode == 2 and "no lead session id" in r.stderr
    assert snapshot(H(tmp_path)) == before


@pytest.mark.parametrize("action", ["auto", "manual", "lead", "status"])
def test_an_unusable_sid_is_refused_and_nothing_is_written(tmp_path, action):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    h = deep / "home"
    (h / "leads").mkdir(parents=True)
    (deep / "x.json").write_text(json.dumps({"sid": "x", "project": "p"}))
    before = snapshot(tmp_path)
    r = run("tier", action, "--session", "../../x", "--home", str(h))
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    r = run("tier", action, "--home", str(h), env={"PI_LEAD_SID": "../../x"})
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("action", ["auto", "manual", "lead", "status"])
def test_an_unregistered_sid_exits_2(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("tier", action, "--session", "lead-2")
    assert r.returncode == 2 and r.stdout == ""
    assert "tier: lead-2 is not a registered lead — run pilead lead-start first" in r.stderr
    assert snapshot(H(tmp_path)) == before


@pytest.mark.parametrize("action", ["auto", "manual", "lead", "status"])
@pytest.mark.parametrize("text", ["{not json", "[]"])
def test_an_unreadable_record_exits_1_naming_the_file(tmp_path, action, text):
    p = rec_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(text)
    before = snapshot(H(tmp_path))
    r = run("tier", action, "--session", "lead-1")
    assert r.returncode == 1 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and str(p) in r.stderr and "tier:" in r.stderr
    assert snapshot(H(tmp_path)) == before


@pytest.mark.parametrize("action", ["auto", "manual", "lead"])
def test_an_executor_cannot_change_a_leads_posture(tmp_path, action):
    ok("lead-start", "lead-1", "--project", "proj")
    before = snapshot(H(tmp_path))
    r = run("tier", action, "--session", "lead-1", env={"PI_LEAD_ROLE": "executor"})
    assert r.returncode == 2 and r.stdout == ""
    assert "tier: an executor cannot change a lead's posture" in r.stderr
    r = run("tier", action, env={"PI_LEAD_ROLE": "executor", "PI_LEAD_SID": "lead-1"})
    assert r.returncode == 2
    assert snapshot(H(tmp_path)) == before


def test_an_executor_may_read_the_status_and_ask(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    models_json(tmp_path)
    r = ok("tier", "status", "--session", "lead-1", env={"PI_LEAD_ROLE": "executor"})
    assert r.stdout == status_line("auto", "proj", AT_START)
    assert ok("tier", "ask", env={"PI_LEAD_ROLE": "executor"}).returncode == 0


# ── ask ──────────────────────────────────────────────────────────────────────────────────────────
def packet(tmp_path, body, name="the-packet.md"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


PROV = "resolved from pi --list-models, {n} option(s) available, ceiling {c}\n"


def ask_lines(tmp_path, *args, env=None):
    r = ok("tier", "ask", *args, env=env)
    assert r.stderr == ""
    return r.stdout.splitlines()


def test_ask_recommends_by_the_packets_lint_codes(tmp_path):
    models_json(tmp_path)
    head = "Executor model for the-packet.md?"
    sonnet = f"  sonnet → anthropic/{SONNET} — workhorse default"
    opus = f"  opus → anthropic/{OPUS} — judgment where a wrong-but-plausible result survives review"
    haiku = f"  haiku → anthropic/{HAIKU} — mechanical"
    tail = ["  fable — above ceiling (opus); not offered"]
    unranked = ["  dsflash → deepseek/deepseek-flash — unranked; the ceiling does not apply",
                "  dspro → deepseek/deepseek-v4-pro — unranked; the ceiling does not apply"]
    end = ["", "resolved from pi --list-models, 5 option(s) available, ceiling opus"]
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, OPUS_SHAPED_BODY))
    assert lines == [head, "", opus + " (Recommended)", haiku, sonnet, *unranked, *tail, *end]
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, SHAPED_BODY))
    assert lines == [head, "", haiku + " (Recommended)", sonnet, opus, *unranked, *tail, *end]
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, CLEAN_BODY))
    assert lines == [head, "", sonnet + " (Recommended)", haiku, opus, *unranked, *tail, *end]


def test_ask_with_no_packet_or_one_that_cannot_be_read_recommends_sonnet(tmp_path):
    models_json(tmp_path)
    lines = ask_lines(tmp_path)
    assert lines[0] == "Executor model for this spawn?" and lines[1] == ""
    assert lines[2] == f"  sonnet → anthropic/{SONNET} — workhorse default (Recommended)"
    lines = ask_lines(tmp_path, "--packet", str(tmp_path / "no-such-packet.md"))
    assert lines[0] == "Executor model for no-such-packet.md?"
    assert lines[2] == f"  sonnet → anthropic/{SONNET} — workhorse default (Recommended)"


def test_a_packet_with_both_shapes_is_recommended_opus(tmp_path):
    models_json(tmp_path)
    both = SHAPED_BODY + "\nInvestigate why the retry counter drifts under load.\n"
    assert ask_lines(tmp_path, "--packet", packet(tmp_path, both))[2].endswith("(Recommended)")
    assert ask_lines(tmp_path, "--packet", packet(tmp_path, both))[2].startswith("  opus →")


def test_a_recommended_class_above_the_ceiling_or_not_available_falls_to_the_first_option(tmp_path):
    models_json(tmp_path)
    (H(tmp_path) / "config.json").write_text(json.dumps({"executor_model_ceiling": "sonnet"}))
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, OPUS_SHAPED_BODY))  # opus is above sonnet
    assert lines[2] == f"  haiku → anthropic/{HAIKU} — mechanical (Recommended)"
    assert lines[3] == f"  sonnet → anthropic/{SONNET} — workhorse default"
    assert "  opus — above ceiling (sonnet); not offered" in lines
    assert "  fable — above ceiling (sonnet); not offered" in lines
    assert lines[-1] == "resolved from pi --list-models, 4 option(s) available, ceiling sonnet"
    (H(tmp_path) / "config.json").unlink()
    models_json(tmp_path, [m for m in ALL if m[1] != OPUS])  # not available
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, OPUS_SHAPED_BODY))
    assert lines[2] == f"  haiku → anthropic/{HAIKU} — mechanical (Recommended)"
    assert not any(ln.startswith("  opus") for ln in lines)  # a class that is not available is not named


def test_the_lead_class_is_tagged_and_both_tags_share_a_line(tmp_path):
    models_json(tmp_path)
    make_lead(tmp_path, model=OPUS)
    lines = ask_lines(tmp_path, "--session", "lead-1", "--packet", packet(tmp_path, CLEAN_BODY))
    assert f"  sonnet → anthropic/{SONNET} — workhorse default (Recommended)" in lines
    assert f"  opus → anthropic/{OPUS} — judgment where a wrong-but-plausible result survives review (lead's tier)" \
        in lines
    lines = ask_lines(tmp_path, "--session", "lead-1", "--packet", packet(tmp_path, OPUS_SHAPED_BODY))
    assert lines[2] == (f"  opus → anthropic/{OPUS} — judgment where a wrong-but-plausible result survives review "
                        "(Recommended, lead's tier)")
    # the caller's own lead is found without --session
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, CLEAN_BODY), env={"PI_LEAD_SID": "lead-1"})
    assert any("(lead's tier)" in ln and ln.startswith("  opus") for ln in lines)


def test_a_lead_class_above_the_ceiling_is_named_only_as_not_offered(tmp_path):
    models_json(tmp_path)
    make_lead(tmp_path, model=FABLE)
    lines = ask_lines(tmp_path, "--session", "lead-1")
    assert "  fable — above ceiling (opus); not offered" in lines
    assert not any("lead's tier" in ln for ln in lines)


def test_the_unranked_aliases_come_after_the_classes(tmp_path):
    models_json(tmp_path)
    lines = ask_lines(tmp_path, "--packet", packet(tmp_path, CLEAN_BODY))
    aliases = [ln.split()[0] for ln in lines[2:] if " → " in ln]
    assert aliases == ["sonnet", "haiku", "opus", "dsflash", "dspro"]


def test_only_unranked_models_available(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[0] == "deepseek"])
    lines = ask_lines(tmp_path)
    assert lines[2] == ("  dsflash → deepseek/deepseek-flash — unranked; the ceiling does not apply (Recommended)")
    assert lines[3] == "  dspro → deepseek/deepseek-v4-pro — unranked; the ceiling does not apply"
    assert lines[-1] == "resolved from pi --list-models, 2 option(s) available, ceiling opus"


def test_json_holds_the_same_options_in_the_same_order(tmp_path):
    models_json(tmp_path)
    make_lead(tmp_path, model=OPUS)
    args = ("--session", "lead-1", "--packet", packet(tmp_path, OPUS_SHAPED_BODY))
    text = ask_lines(tmp_path, *args)
    d = json.loads(ok("tier", "ask", *args, "--json").stdout)
    assert list(d) == ["header", "options", "omitted_above_ceiling", "provenance"]
    assert d["header"] == text[0] == "Executor model for the-packet.md?"
    assert [o["alias"] for o in d["options"]] == [ln.split()[0] for ln in text[2:] if " → " in ln]
    assert d["options"][0] == {"alias": "opus", "resolved": f"anthropic/{OPUS}",
                               "description": "judgment where a wrong-but-plausible result survives review",
                               "recommended": True, "lead_tier": True}
    assert [o["recommended"] for o in d["options"]] == [True] + [False] * 4
    assert [o["lead_tier"] for o in d["options"]] == [True] + [False] * 4
    assert d["omitted_above_ceiling"] == ["fable"]
    assert d["provenance"] == {"available": 5, "ceiling": "opus"}
    assert list(d["options"][0]) == ["alias", "resolved", "description", "recommended", "lead_tier"]


def test_no_option_available_exits_1_with_its_line_and_no_header(tmp_path):
    models_json(tmp_path, [("other", "x-1")])
    for args in ((), ("--json",)):
        r = run("tier", "ask", *args)
        assert r.returncode == 1 and r.stdout == ""
        assert r.stderr == "pilead: no executor model is available on this machine — run pilead doctor\n"
    models_json(tmp_path, [m for m in ALL if m[1] == FABLE])  # only a class above the ceiling
    r = run("tier", "ask")
    assert r.returncode == 1 and r.stdout == "" and "no executor model is available" in r.stderr


def test_an_unknown_model_list_exits_1_with_one_line(tmp_path, monkeypatch):
    failing_pi(tmp_path, monkeypatch)
    r = run("tier", "ask")
    assert r.returncode == 1 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1 and "could not be read" in r.stderr and "returned no models" in r.stderr


def test_ask_changes_no_file(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    models_json(tmp_path)
    make_lead(tmp_path, "lead-1", model=SONNET)
    before = snapshot(H(tmp_path))
    ask_lines(tmp_path, "--session", "lead-1", "--packet", packet(tmp_path, CLEAN_BODY))
    ok("tier", "ask", "--json")
    assert snapshot(H(tmp_path)) == before
    assert not (H(tmp_path) / "usage").exists()


def test_ask_needs_no_registered_lead_and_refuses_an_unusable_one(tmp_path):
    models_json(tmp_path)
    assert run("tier", "ask", "--session", "lead-9").returncode == 0
    before = snapshot(H(tmp_path))
    r = run("tier", "ask", "--session", "../../x")
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    assert snapshot(H(tmp_path)) == before


def test_posture_module_agrees_with_the_verb(tmp_path):
    ok("lead-start", "lead-1", "--project", "proj")
    ok("tier", "lead", "--session", "lead-1")
    assert posture.tier_state(rec(tmp_path)) == ("lead", "command", True)

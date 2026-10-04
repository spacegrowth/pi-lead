"""lib/pilead/posture.py in-process: reading and writing a lead's autonomous posture, and the stop-list
text shared with extensions/pi-lead.ts."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import posture  # noqa: E402
from pilead.state import PileadError  # noqa: E402


@pytest.mark.parametrize("value,on", [
    (True, True), (False, False), ("yes", False), (1, False), (None, False), ("true", False), (1.0, False),
])
def test_only_json_true_is_on(value, on):
    assert posture.autonomous_state({"autonomous": value})[0] is on
    assert posture.autonomous_state(json.loads(json.dumps({"autonomous": value})))[0] is on


def test_no_field_is_off():
    assert posture.autonomous_state({"sid": "l", "project": "p"}) == (False, "config")


@pytest.mark.parametrize("rec", [None, [], ["autonomous"], "autonomous", 1, True, [{"autonomous": True}]])
def test_a_record_that_is_not_a_dict_is_off(rec):
    assert posture.autonomous_state(rec) == (False, "config")


@pytest.mark.parametrize("source,want", [
    ("command", "command"), ("config", "config"), (None, "config"), ("arm", "config"), ("COMMAND", "config"),
    (True, "config"),
])
def test_source(source, want):
    assert posture.autonomous_state({"autonomous": True, "autonomous_source": source}) == (True, want)
    assert posture.autonomous_state({"autonomous": True}) == (True, "config")


def _write(home, sid, rec):
    p = home / "leads" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2) + "\n")
    return p


REC = {"sid": "lead-1", "project": "prøj ✓", "cwd": "/w", "inbox": "/h/leads/lead-1.inbox.md",
       "iterm_handle": None, "terminal": "iTerm.app", "started": "2026-09-01T00:00:00Z",
       "extra": {"n": 1.5, "big": 12345678901234567890, "list": [1, "a", None]}}


def test_set_autonomous_keeps_every_other_field_and_returns_the_earlier_state(tmp_path):
    p = _write(tmp_path, "lead-1", REC)
    assert posture.set_autonomous(tmp_path, "lead-1", True) == (False, "config")
    after = json.loads(p.read_text())
    assert (after["autonomous"], after["autonomous_source"]) == (True, "command")
    assert {k: v for k, v in after.items() if k not in ("autonomous", "autonomous_source")} == REC
    for k in REC:  # each other field serialises to the same bytes as before
        assert json.dumps(after[k]) == json.dumps(REC[k]), k
    assert list(after)[:len(REC)] == list(REC)  # and keeps its place
    assert posture.set_autonomous(tmp_path, "lead-1", False) == (True, "command")
    after = json.loads(p.read_text())
    assert (after["autonomous"], after["autonomous_source"]) == (False, "command")
    assert {k: v for k, v in after.items() if k not in ("autonomous", "autonomous_source")} == REC
    assert posture.set_autonomous(tmp_path, "lead-1", False) == (False, "command")


def test_set_autonomous_writes_a_boolean(tmp_path):
    p = _write(tmp_path, "lead-1", {**REC, "autonomous": "yes", "autonomous_source": "config"})
    assert posture.set_autonomous(tmp_path, "lead-1", 1) == (False, "config")
    assert json.loads(p.read_text())["autonomous"] is True


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def test_set_autonomous_on_a_missing_record_raises_and_writes_nothing(tmp_path):
    (tmp_path / "leads").mkdir()
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError):
        posture.set_autonomous(tmp_path, "lead-1", True)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("text", ["{not json", "", "[]", "null", '"x"'])
def test_set_autonomous_on_an_unreadable_record_raises_and_writes_nothing(tmp_path, text):
    p = tmp_path / "leads" / "lead-1.json"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError):
        posture.set_autonomous(tmp_path, "lead-1", True)
    assert _snapshot(tmp_path) == before


def test_set_autonomous_refuses_an_unusable_id_before_any_file(tmp_path):
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError) as e:
        posture.set_autonomous(tmp_path, "../x", True)
    assert e.value.code == 2
    assert _snapshot(tmp_path) == before


def test_stop_list_equals_the_extension_constant():
    ts = (ROOT / "extensions" / "pi-lead.ts").read_text()
    m = re.search(r'^export const AUTONOMOUS_STOP_LIST\s*=\s*\n?\s*"((?:[^"\\]|\\.)*)";', ts, re.M)
    assert m, "AUTONOMOUS_STOP_LIST not found as one double-quoted string"
    assert json.loads(f'"{m.group(1)}"') == posture.STOP_LIST
    assert posture.STOP_LIST == (
        "a report with a risk flag, failing tests, or an UNVERIFIED claim that bears on correctness; core logic, "
        "ledgers, parity or golden tests, migrations, deploys; an action that cannot be undone or that leaves "
        "this machine (a push, a delete, a publish, a message to someone); work that is not in the approved "
        "plan; real ambiguity the packet and the plan do not settle")


# ── the model-tier posture ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("word", ["auto", "manual", "lead"])
def test_tier_state_of_each_word(word):
    assert posture.tier_state({"tier": word}) == (word, "config", True)
    assert posture.tier_state({"tier": word, "tier_source": "command"}) == (word, "command", True)
    assert posture.tier_state({"tier": word, "tier_source": "arm"}) == (word, "config", True)


def test_tier_state_with_no_field_is_auto_from_config():
    assert posture.tier_state({"sid": "l", "project": "p"}) == ("auto", "config", True)
    assert posture.tier_state({"tier_source": "command"}) == ("auto", "config", True)


@pytest.mark.parametrize("value", ["manul", 7, None, "MANUAL", "", True, ["auto"], {"tier": "auto"}])
def test_tier_state_of_a_value_that_is_not_a_word_is_not_readable(value):
    assert posture.tier_state({"tier": value, "tier_source": "command"}) == ("auto", "config", False)


@pytest.mark.parametrize("rec", [None, [], "auto", 1])
def test_tier_state_of_a_record_that_is_not_a_dict_is_not_readable(rec):
    assert posture.tier_state(rec) == ("auto", "config", False)


def test_tiers_are_the_three_words():
    assert posture.TIERS == ("auto", "manual", "lead")


def test_set_tier_keeps_every_other_field_and_returns_the_earlier_state(tmp_path):
    p = _write(tmp_path, "lead-1", REC)
    assert posture.set_tier(tmp_path, "lead-1", "manual") == ("auto", "config", True)
    after = json.loads(p.read_text())
    assert (after["tier"], after["tier_source"]) == ("manual", "command")
    assert {k: v for k, v in after.items() if k not in ("tier", "tier_source")} == REC
    for k in REC:
        assert json.dumps(after[k]) == json.dumps(REC[k]), k
    assert list(after)[:len(REC)] == list(REC)
    assert posture.set_tier(tmp_path, "lead-1", "lead") == ("manual", "command", True)
    assert posture.set_tier(tmp_path, "lead-1", "auto") == ("lead", "command", True)
    assert json.loads(p.read_text())["tier"] == "auto"
    assert posture.set_tier(tmp_path, "lead-1", "auto") == ("auto", "command", True)


def test_set_tier_leaves_the_autonomous_posture_alone(tmp_path):
    p = _write(tmp_path, "lead-1", {**REC, "autonomous": True, "autonomous_source": "command"})
    posture.set_tier(tmp_path, "lead-1", "lead")
    after = json.loads(p.read_text())
    assert (after["autonomous"], after["autonomous_source"]) == (True, "command")


def test_set_tier_over_a_value_that_could_not_be_read_reports_it(tmp_path):
    _write(tmp_path, "lead-1", {**REC, "tier": "manul"})
    assert posture.set_tier(tmp_path, "lead-1", "auto") == ("auto", "config", False)


@pytest.mark.parametrize("word", ["manul", "", "AUTO", None, 7, "on"])
def test_set_tier_with_a_word_outside_tiers_raises_and_writes_nothing(tmp_path, word):
    _write(tmp_path, "lead-1", REC)
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError):
        posture.set_tier(tmp_path, "lead-1", word)
    assert _snapshot(tmp_path) == before


def test_set_tier_on_a_missing_record_raises_and_writes_nothing(tmp_path):
    (tmp_path / "leads").mkdir()
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError):
        posture.set_tier(tmp_path, "lead-1", "manual")
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("text", ["{not json", "", "[]", "null", '"x"'])
def test_set_tier_on_an_unreadable_record_raises_and_writes_nothing(tmp_path, text):
    p = tmp_path / "leads" / "lead-1.json"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError):
        posture.set_tier(tmp_path, "lead-1", "manual")
    assert _snapshot(tmp_path) == before


def test_set_tier_refuses_an_unusable_id_before_any_file(tmp_path):
    before = _snapshot(tmp_path)
    with pytest.raises(PileadError) as e:
        posture.set_tier(tmp_path, "../x", "manual")
    assert e.value.code == 2
    assert _snapshot(tmp_path) == before

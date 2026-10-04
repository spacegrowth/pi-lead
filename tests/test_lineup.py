"""`pilead lineup` and lib/pilead/lineup.py: which model classes this machine's pi offers (a `models.json`
this file writes — no `pi --list-models` runs unless a test puts a failing `pi` first on PATH) and where the
lead sits among them (its own model from a synthetic pi session log, as tests/test_list.py writes them).
Driven in-process for the data and through bin/pilead for the verb."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import lineup  # noqa: E402

SONNET, HAIKU, OPUS, FABLE = ("claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5", "claude-fable-5")
ALL = [("anthropic", HAIKU), ("anthropic", SONNET), ("anthropic", OPUS), ("anthropic", FABLE),
       ("deepseek", "deepseek-flash"), ("deepseek", "deepseek-v4-pro")]
NO_LIST = "`pi --list-models` returned no models"


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def models_json(tmp_path, entries=ALL):
    H(tmp_path).mkdir(parents=True, exist_ok=True)
    (H(tmp_path) / "models.json").write_text(json.dumps({"fetched": int(time.time()), "models": [
        {"provider": p, "id": i, "context": "1M"} for p, i in entries]}))


def lead(tmp_path, sid="lead-1", model=None, provider="anthropic"):
    """A registered lead; with `model`, a session log whose last answer is on it."""
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    cwd = tmp_path / "lead-cwd"
    cwd.mkdir(exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": "proj", "cwd": str(cwd)}))
    if model is not None:
        write_log(sid, str(cwd), [assistant(u(10, 5), provider=provider, model=model)])
    return sid


def failing_pi(tmp_path, monkeypatch):
    """A `pi` first on PATH that lists nothing and exits 1: the model list cannot be read."""
    d = tmp_path / "failing-pi"
    d.mkdir()
    (d / "pi").write_text("#!/bin/sh\nexit 1\n")
    (d / "pi").chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])


def data(tmp_path, sid, ceiling="opus"):
    return lineup.data(H(tmp_path), sid, ceiling)


def run(*args, env=None):
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(Path(root).rglob("*"))}


# ── availability ─────────────────────────────────────────────────────────────────────────────────
def test_every_alias_available(tmp_path):
    models_json(tmp_path)
    got = lineup.availability(H(tmp_path))
    assert [(e["alias"], e["ranked"], e["state"], e["model"]) for e in got] == [
        ("haiku", True, "available", f"anthropic/{HAIKU}"), ("sonnet", True, "available", f"anthropic/{SONNET}"),
        ("opus", True, "available", f"anthropic/{OPUS}"), ("fable", True, "available", f"anthropic/{FABLE}"),
        ("dsflash", False, "available", "deepseek/deepseek-flash"),
        ("dspro", False, "available", "deepseek/deepseek-v4-pro")]


def test_a_class_missing_from_the_list_is_not_available(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[1] != FABLE])
    got = {e["alias"]: e for e in lineup.availability(H(tmp_path))}
    assert got["fable"] == {"alias": "fable", "ranked": True, "state": "not available"}
    assert got["opus"]["state"] == "available"


def test_an_unreadable_list_makes_every_alias_unknown(tmp_path, monkeypatch):
    failing_pi(tmp_path, monkeypatch)
    got = lineup.availability(H(tmp_path))
    assert [e["alias"] for e in got] == ["haiku", "sonnet", "opus", "fable", "dsflash", "dspro"]
    assert all(e["state"] == "unknown" and e["why"] == NO_LIST and "model" not in e for e in got)
    assert not any(e["state"] in ("available", "not available") for e in got)


def test_the_list_is_loaded_once_per_call(tmp_path, monkeypatch):
    models_json(tmp_path)
    calls = []
    real = lineup.models.load
    monkeypatch.setattr(lineup.models, "load", lambda home: calls.append(1) or real(home))
    lineup.availability(H(tmp_path))
    assert calls == [1]


# ── the model check, one test per row of the table ───────────────────────────────────────────────
def test_row_the_model_list_is_unknown(tmp_path, monkeypatch):
    failing_pi(tmp_path, monkeypatch)
    lead(tmp_path, model=SONNET)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{SONNET} — pi's model list could not be read ({NO_LIST}), "
                                "so nothing can be said about the classes on this machine.")
    assert d["stop"] is False and d["available"] == [] and d["executors"] == []
    assert (d["above"], d["below"], d["class"]) == ([], [], "sonnet")
    lead(tmp_path, "lead-2")  # and with no model either
    assert data(tmp_path, "lead-2")["model_check"] == (
        f"Model check: unknown model — pi's model list could not be read ({NO_LIST}), so nothing can be said "
        "about the classes on this machine.")


def test_row_the_lead_model_is_unknown(tmp_path):
    models_json(tmp_path)
    lead(tmp_path)  # registered, no log
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == ("Model check: unknown model — this session's log holds no answer yet, so its model "
                                "could not be read. Executors available here: haiku, sonnet, opus, dsflash, dspro.")
    assert (d["stop"], d["model"], d["model_source"], d["class"]) == (False, None, None, None)
    assert data(tmp_path, None)["model_check"] == d["model_check"]  # no lead at all: the same


def test_row_the_lead_model_is_unranked(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model="deepseek-v4-pro", provider="deepseek")
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == ("Model check: deepseek/deepseek-v4-pro — not one of the ranked classes, so it cannot "
                                "be placed among them. Executors available here: haiku, sonnet, opus, dsflash, "
                                "dspro.")
    assert (d["stop"], d["model"], d["model_source"], d["class"]) == (False, "deepseek/deepseek-v4-pro",
                                                                      "session log", None)
    assert d["above"] == [] and d["below"] == []


def test_row_no_class_is_available(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[0] == "deepseek"])
    lead(tmp_path, model=SONNET)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{SONNET} — no ranked class is available on this machine. "
                                "Executors available here: dsflash, dspro.")
    assert d["stop"] is False and d["available"] == []


def test_row_no_class_is_available_and_nothing_else_either(tmp_path):
    models_json(tmp_path, [("other", "x-1")])
    lead(tmp_path, model=SONNET)
    assert data(tmp_path, "lead-1")["model_check"] == (
        f"Model check: anthropic/{SONNET} — no ranked class is available on this machine. Executors available "
        "here: none.")


def test_row_nothing_available_below(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=HAIKU)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{HAIKU} — the weakest class available on this machine; "
                                "nothing to delegate down to. Please switch this session to fable with /model "
                                "first.")
    assert d["stop"] is True and d["below"] == [] and d["above"] == ["sonnet", "opus", "fable"]


def test_row_nothing_available_below_when_the_lead_class_is_not_on_the_list(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[1] not in (HAIKU, SONNET)])
    lead(tmp_path, model=SONNET)
    d = data(tmp_path, "lead-1")
    assert d["stop"] is True and "Please switch this session to fable with /model first." in d["model_check"]


def test_row_nothing_available_above(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=FABLE)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{FABLE} — the strongest class available on this machine. "
                                "Proceeding as lead; executors available here: haiku, sonnet, opus, dsflash, "
                                "dspro.")
    assert d["stop"] is False and d["above"] == [] and d["below"] == ["haiku", "sonnet", "opus"]


def test_row_one_above(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=OPUS)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{OPUS} — one stronger class (fable) is available here if "
                                "you want it; this class suits lead work. Executors available here: haiku, "
                                "sonnet, opus, dsflash, dspro.")
    assert d["stop"] is False and d["above"] == ["fable"]


def test_row_several_above(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=SONNET)
    d = data(tmp_path, "lead-1")
    assert d["model_check"] == (f"Model check: anthropic/{SONNET} — 2 stronger classes (opus, fable) are available "
                                "here. Recommend /model fable now; otherwise proceeding as it is. Executors "
                                "available here: haiku, sonnet, opus, dsflash, dspro.")
    assert d["stop"] is False and d["above"] == ["opus", "fable"] and d["below"] == ["haiku"]
    assert (d["model"], d["model_source"], d["class"], d["session"]) == (f"anthropic/{SONNET}", "session log",
                                                                         "sonnet", "lead-1")


def test_a_class_that_is_not_available_is_never_named_above(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[1] != FABLE])
    lead(tmp_path, model=SONNET)
    d = data(tmp_path, "lead-1")
    assert d["above"] == ["opus"] and "one stronger class (opus)" in d["model_check"]
    assert "fable" not in d["model_check"]


def test_the_keys_of_the_payload(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=SONNET)
    assert list(data(tmp_path, "lead-1")) == ["session", "model", "model_source", "class", "classes", "available",
                                             "above", "below", "executor_ceiling", "executors", "model_check", "stop"]


# ── the executors ────────────────────────────────────────────────────────────────────────────────
def test_executors_leave_out_a_class_above_the_ceiling_and_keep_the_unranked(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=FABLE)
    assert data(tmp_path, "lead-1", "opus")["executors"] == ["haiku", "sonnet", "opus", "dsflash", "dspro"]
    assert data(tmp_path, "lead-1", "sonnet")["executors"] == ["haiku", "sonnet", "dsflash", "dspro"]
    assert data(tmp_path, "lead-1", "fable")["executors"] == ["haiku", "sonnet", "opus", "fable", "dsflash", "dspro"]
    d = data(tmp_path, "lead-1", "haiku")
    assert d["executors"] == ["haiku", "dsflash", "dspro"] and d["executor_ceiling"] == "haiku"


def test_executors_are_only_what_is_available(tmp_path):
    models_json(tmp_path, [m for m in ALL if m[1] not in (SONNET, "deepseek-flash")])
    lead(tmp_path, model=FABLE)
    assert data(tmp_path, "lead-1")["executors"] == ["haiku", "opus", "dspro"]


# ── the verb ─────────────────────────────────────────────────────────────────────────────────────
def test_the_text_and_the_json_over_the_same_home(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=SONNET)
    r = run("lineup", "--session", "lead-1")
    assert r.returncode == 0 and r.stderr == ""
    d = json.loads(run("lineup", "--session", "lead-1", "--json").stdout)
    assert d == data(tmp_path, "lead-1")
    assert r.stdout.splitlines() == [
        d["model_check"],
        f"  this session: anthropic/{SONNET} (from the session log), class sonnet",
        f"  haiku  available → anthropic/{HAIKU}",
        f"  sonnet  available → anthropic/{SONNET}",
        f"  opus  available → anthropic/{OPUS}",
        f"  fable  available → anthropic/{FABLE}  [above executor ceiling opus]",
        "  dsflash  available → deepseek/deepseek-flash",
        "  dspro  available → deepseek/deepseek-v4-pro",
        f"  source: pi --list-models, cached in {H(tmp_path) / 'models.json'}"]


def test_a_class_that_is_not_available_and_a_list_that_is_unknown_print_their_lines(tmp_path, monkeypatch):
    models_json(tmp_path, [m for m in ALL if m[1] != FABLE])
    lead(tmp_path, model=OPUS)
    out = run("lineup", "--session", "lead-1").stdout.splitlines()
    assert "  fable  NOT available — pi's model list has no such model" in out
    (H(tmp_path) / "models.json").unlink()
    failing_pi(tmp_path, monkeypatch)
    r = run("lineup", "--session", "lead-1")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[1] == f"  this session: anthropic/{OPUS} (from the session log), class opus"
    assert lines[2:8] == [f"  {a}  unknown — {NO_LIST}" for a in ("haiku", "sonnet", "opus", "fable", "dsflash",
                                                                "dspro")]


def test_stop_prints_its_line_and_still_exits_0(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=HAIKU)
    r = run("lineup", "--session", "lead-1")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[1] == "STOP: wait for the user to switch models before continuing as lead."
    assert lines[2] == f"  this session: anthropic/{HAIKU} (from the session log), class haiku"
    j = run("lineup", "--session", "lead-1", "--json")
    assert j.returncode == 0 and json.loads(j.stdout)["stop"] is True


def test_no_session_prints_an_unknown_lead_model(tmp_path):
    models_json(tmp_path)
    r = run("lineup")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[0].startswith("Model check: unknown model — this session's log holds no answer yet")
    assert lines[1] == "  this session: -, class ?"


def test_the_caller_is_the_registered_lead_of_the_environment(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=FABLE)
    for env in ({"PI_LEAD_SID": "lead-1"}, {"PI_SESSION_ID": "lead-1"}):
        assert json.loads(run("lineup", "--json", env=env).stdout)["session"] == "lead-1"
    # not registered, or not usable: no caller
    for env in ({"PI_LEAD_SID": "lead-9"}, {"PI_SESSION_ID": "../x"}, {"PI_LEAD_SID": ""}):
        assert json.loads(run("lineup", "--json", env=env).stdout)["session"] is None
    # --session wins
    assert json.loads(run("lineup", "--json", "--session", "lead-2", env={"PI_LEAD_SID": "lead-1"}
                          ).stdout)["session"] == "lead-2"


def test_an_unusable_session_exits_2_and_reads_nothing(tmp_path):
    models_json(tmp_path)
    before = snapshot(tmp_path)
    r = run("lineup", "--session", "../../x")
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    assert snapshot(tmp_path) == before


def test_home_is_byte_identical_apart_from_models_json(tmp_path):
    models_json(tmp_path)
    lead(tmp_path, model=SONNET)
    before = snapshot(H(tmp_path))
    run("lineup", "--session", "lead-1")
    run("lineup", "--session", "lead-1", "--json")
    assert snapshot(H(tmp_path)) == before  # a fresh models.json is not even rewritten
    assert not (H(tmp_path) / "usage").exists()


def test_a_stale_models_json_is_the_only_file_the_verb_writes(tmp_path):
    models_json(tmp_path)
    (H(tmp_path) / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": "anthropic", "id": "claude-sonnet-5", "context": "1M"}]}))
    lead(tmp_path, model=SONNET)
    before = snapshot(H(tmp_path))
    assert run("lineup", "--session", "lead-1").returncode == 0  # fake pi refreshes the cache
    after = snapshot(H(tmp_path))
    assert {k for k in after if after[k] != before.get(k)} == {"models.json"}
    assert set(before) <= set(after)
    assert not (H(tmp_path) / "usage").exists()

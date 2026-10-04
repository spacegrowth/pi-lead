"""lib/pilead/doctor.py's checks, driven entirely through the Context of change 3: a temp home, stub
programs this file writes, and a fake clock — never this machine's real `pi`, real iTerm2 or real git
version. See README "Doctor" and the packet's table for what each check does."""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from pilead import doctor, models, refs, shim  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
# Real wall-clock time, captured once: `models.resolve` -> `models.load` decides for itself (via the
# real `time.time()`, never `ctx.now`, since lib/pilead/models.py is out of scope) whether a
# `models.json` is stale enough to fetch — so a "fresh" fixture's `fetched` must agree with actual
# wall-clock time, not an arbitrary fake epoch, or `load()` would fetch for real.
FIXED_NOW = time.time()


# ── context / fixtures ───────────────────────────────────────────────────────────────────────────
def make_ctx(tmp_path, **kw):
    defaults = dict(
        home=tmp_path / "home",
        root=doctor.state.ROOT,
        env={"PATH": ""},
        python_version=(3, 9, 6),
        app_dirs=(tmp_path / "no-such-Applications-dir",),
        offline=False,
        run=doctor.real_run,
        now=lambda: FIXED_NOW,
    )
    defaults.update(kw)
    return doctor.Context(**defaults)


@pytest.fixture(autouse=True)
def _no_stray_gitpath_cache(monkeypatch):
    """`refs.git_path` caches its resolution per PATH string at module scope; give every test a
    fresh cache so one test's stub `git` can never leak into another's resolution."""
    monkeypatch.setattr(refs, "_resolved", {})


def _bin(tmp_path, name="bin"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def _write_version_stub(path, line, rc=0):
    """A stub program that answers `--version` with one chosen line and exits `rc`, for anything
    else."""
    path.write_text(f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then printf '%s\\n' {line!r}; exit {rc}; fi\n"
                     "exit 1\n")
    path.chmod(0o755)


def _minimal_root(tmp_path, name="root"):
    """Just enough of a package root for check_package_files: the extension, executor rules, vendor
    backend, and every skill file (doctor.SKILL_FILES) with correct frontmatter."""
    root = tmp_path / name
    (root / "extensions").mkdir(parents=True)
    (root / "extensions" / "pi-lead.ts").write_text("// stub extension\n")
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "executor-rules.md").write_text("stub rules\n")
    (root / "vendor" / "relay").mkdir(parents=True)
    (root / "vendor" / "relay" / "backend.py").write_text("# stub backend\n")
    for name_ in doctor.SKILL_FILES:
        d = root / "skills" / f"pilead-{name_}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: pilead-{name_}\ndescription: x\n---\nbody\n")
    return root


def _write_open_session(home, sid, *, lead=None, status="busy", worktree=None):
    sdir = home / "sessions" / sid
    sdir.mkdir(parents=True)
    meta = {"sid": sid, "lead": lead, "worktree": worktree or "/nowhere", "topic": "t", "model": "m",
             "status": status, "created": "-", "packets": 1, "label": f"[Exec] {sid}", "layout": "tab"}
    (sdir / "meta.json").write_text(json.dumps(meta))
    (sdir / "status").write_text(status + "\n")
    return sdir


def _one(results):
    assert len(results) == 1, results
    return results[0]


def _by_check(results, name):
    return next(r for r in results if r["check"] == name)


# ── 1. python ────────────────────────────────────────────────────────────────────────────────────
def test_python_pass(tmp_path):
    r = _one(doctor.check_python(make_ctx(tmp_path, python_version=(3, 9, 0))))
    assert r["status"] == doctor.PASS and "3.9.0" in r["detail"]


def test_python_fail(tmp_path):
    r = _one(doctor.check_python(make_ctx(tmp_path, python_version=(3, 8, 10))))
    assert r["status"] == doctor.FAIL


# ── 2. package files ─────────────────────────────────────────────────────────────────────────────
def test_package_files_pass_real_package(tmp_path):
    r = _one(doctor.check_package_files(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS


def test_package_files_fail_missing(tmp_path):
    root = _minimal_root(tmp_path)
    (root / "extensions" / "pi-lead.ts").unlink()
    r = _one(doctor.check_package_files(make_ctx(tmp_path, root=root)))
    assert r["status"] == doctor.FAIL
    assert "extensions/pi-lead.ts" in r["detail"]


def test_package_files_fail_mismatched_frontmatter(tmp_path):
    root = _minimal_root(tmp_path)
    p = root / "skills" / "pilead-mode" / "SKILL.md"
    p.write_text(p.read_text().replace("name: pilead-mode", "name: something-else"))
    r = _one(doctor.check_package_files(make_ctx(tmp_path, root=root)))
    assert r["status"] == doctor.FAIL
    assert "pilead-mode/SKILL.md" in r["detail"]


# ── 3. verb modules ──────────────────────────────────────────────────────────────────────────────
def test_verb_modules_pass_real_package(tmp_path):
    r = _one(doctor.check_verb_modules(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS


def test_verb_modules_fail_broken_module(tmp_path):
    """check_verb_modules always introspects the running `pilead.verbs` package (it is part of it),
    so the FAIL path is exercised through a temp copy of the tree, like test_cli_surface.py's own
    broken-verb tests."""
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "bin", tree / "bin")
    shutil.copytree(ROOT / "lib", tree / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "vendor").symlink_to(ROOT / "vendor")
    (tree / "lib" / "pilead" / "verbs" / "zz_broken.py").write_text("raise RuntimeError('boom')\n")
    env = {**os.environ, "PI_LEAD_HOME": str(tmp_path / "h")}
    r = subprocess.run([sys.executable, str(tree / "bin" / "pilead"), "doctor", "--offline", "--json"],
                        capture_output=True, text=True, env=env)
    assert r.returncode in (1, 3)  # verb modules FAIL -> 1 either way
    data = json.loads(r.stdout)
    row = next(x for x in data if x["check"] == "verb modules")
    assert row["status"] == "FAIL"
    assert "zz_broken" in row["detail"] and "RuntimeError" in row["detail"]


# ── 4. pilead on PATH ────────────────────────────────────────────────────────────────────────────
def test_pilead_on_path_warn_not_on_path(tmp_path):
    r = _one(doctor.check_pilead_on_path(make_ctx(tmp_path, env={"PATH": str(_bin(tmp_path, "empty"))})))
    assert r["status"] == doctor.WARN and "not on PATH" in r["detail"]


def test_pilead_on_path_pass(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    real = root / "bin" / "pilead"
    real.write_text("#!/bin/sh\n"); real.chmod(0o755)
    r = _one(doctor.check_pilead_on_path(make_ctx(tmp_path, root=root, env={"PATH": str(root / "bin")})))
    assert r["status"] == doctor.PASS


def test_pilead_on_path_warn_another_copy_first(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    real = root / "bin" / "pilead"
    real.write_text("#!/bin/sh\n"); real.chmod(0o755)
    other_dir = _bin(tmp_path, "other")
    other = other_dir / "pilead"
    other.write_text("#!/bin/sh\n"); other.chmod(0o755)
    path = f"{other_dir}{os.pathsep}{root / 'bin'}"
    r = _one(doctor.check_pilead_on_path(make_ctx(tmp_path, root=root, env={"PATH": path})))
    assert r["status"] == doctor.WARN
    assert str(other.resolve()) in r["detail"] and str(real.resolve()) in r["detail"]


# ── 5. git ───────────────────────────────────────────────────────────────────────────────────────
def test_git_pass(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "git", "git version 2.31.0")
    r = _one(doctor.check_git(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.PASS and "2.31.0" in r["detail"]


def test_git_fail_too_old(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "git", "git version 2.30.9")
    r = _one(doctor.check_git(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.FAIL and "path-format" in r["detail"]


def test_git_not_checked_unparsable_version(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "git", "git version banana")
    r = _one(doctor.check_git(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.NOT_CHECKED


def test_git_fail_no_real_git_found(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "FALLBACK_GITS", ())
    r = _one(doctor.check_git(make_ctx(tmp_path, env={"PATH": str(_bin(tmp_path, "empty"))})))
    assert r["status"] == doctor.FAIL


# ── 6. iTerm2 ────────────────────────────────────────────────────────────────────────────────────
def test_iterm2_pass_app_dir(tmp_path):
    apps = tmp_path / "Applications"
    (apps / "iTerm.app").mkdir(parents=True)
    r = _one(doctor.check_iterm2(make_ctx(tmp_path, app_dirs=(apps,))))
    assert r["status"] == doctor.PASS


def test_iterm2_pass_term_program(tmp_path):
    r = _one(doctor.check_iterm2(make_ctx(tmp_path, env={"PATH": "", "TERM_PROGRAM": "iTerm.app"})))
    assert r["status"] == doctor.PASS


def test_iterm2_fail(tmp_path):
    r = _one(doctor.check_iterm2(make_ctx(tmp_path, env={"PATH": ""})))
    assert r["status"] == doctor.FAIL


# ── 7. iTerm2 Python API ─────────────────────────────────────────────────────────────────────────
def test_iterm2_python_api_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_iterm2_spec", lambda: SimpleNamespace(origin="/fake/iterm2/__init__.py"))
    r = _one(doctor.check_iterm2_python_api(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS


def test_iterm2_python_api_warn(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_iterm2_spec", lambda: None)
    r = _one(doctor.check_iterm2_python_api(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN


# ── 8. osascript ─────────────────────────────────────────────────────────────────────────────────
def test_osascript_pass_on_path(tmp_path):
    d = _bin(tmp_path)
    (d / "osascript").write_text("#!/bin/sh\nexit 0\n"); (d / "osascript").chmod(0o755)
    r = _one(doctor.check_osascript(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.PASS


def test_osascript_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_osascript_fallback_exists", lambda: False)
    r = _one(doctor.check_osascript(make_ctx(tmp_path, env={"PATH": str(_bin(tmp_path, "empty"))})))
    assert r["status"] == doctor.FAIL


# ── 9. home ──────────────────────────────────────────────────────────────────────────────────────
def test_home_pass_not_created(tmp_path):
    r = _one(doctor.check_home(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "not created yet" in r["detail"]


def test_home_pass_directory(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_home(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS


def test_home_fail_not_a_directory(tmp_path):
    (tmp_path / "home").write_text("x")
    r = _one(doctor.check_home(make_ctx(tmp_path)))
    assert r["status"] == doctor.FAIL


def test_home_fail_not_writable(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    os.chmod(home, 0o500)
    try:
        r = _one(doctor.check_home(make_ctx(tmp_path)))
        assert r["status"] == doctor.FAIL
    finally:
        os.chmod(home, 0o700)


# ── 10. config.json ──────────────────────────────────────────────────────────────────────────────
def _cfg_ctx(tmp_path, text):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(text)
    return make_ctx(tmp_path)


def test_config_no_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _one(doctor.check_config(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "defaults apply" in r["detail"]


def test_config_home_missing_skips(tmp_path):
    r = _one(doctor.check_config(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_config_not_json(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, "{not json")))
    assert r["status"] == doctor.FAIL and "default for every key" in r["detail"]


def test_config_json_list(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, "[1, 2]")))
    assert r["status"] == doctor.FAIL and "not an object" in r["detail"]


def test_config_bare_nan(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"grace_seconds": NaN}')))
    assert r["status"] == doctor.FAIL
    assert "not JSON" in r["detail"] and "grace_seconds" in r["detail"]


def test_config_bare_infinity(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"poll_interval": Infinity}')))
    assert r["status"] == doctor.FAIL
    assert "not JSON" in r["detail"] and "poll_interval" in r["detail"]


def test_config_wrong_type(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"edit_line_threshold": "abc"}')))
    assert r["status"] == doctor.FAIL
    assert any("wrong type" in line for line in r["info"])


def test_config_outside_range(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"grace_seconds": 0}')))
    assert r["status"] == doctor.FAIL
    assert any("outside its range" in line for line in r["info"])


def test_config_400_digit_integer(tmp_path):
    huge = "9" * 400
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, f'{{"edit_line_threshold": {huge}}}')))
    assert r["status"] == doctor.FAIL
    assert any("not a usable number" in line for line in r["info"])


def test_config_unrecognized_key(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"not_a_real_key": 1}')))
    assert r["status"] == doctor.WARN
    assert any("not a pi-lead key" in line for line in r["info"])


def test_config_mixed_fail_and_warn(tmp_path):
    body = '{"grace_seconds": 0, "not_a_real_key": 1}'
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, body)))
    assert r["status"] == doctor.FAIL
    joined = "\n".join(r["info"])
    assert "outside its range" in joined and "not a pi-lead key" in joined


def test_config_valid_file(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"grace_seconds": 600, "poll_interval": 3}')))
    assert r["status"] == doctor.PASS
    assert r["info"] == []


# ── 10b. config.json: CLI_RANGES keys, and a bad usage_limit_pattern (change 4) ────────────────────
@pytest.mark.parametrize("key", ["context_warn_tokens", "context_nudge_tokens", "handoff_nudge_mb",
                                  "stall_threshold_seconds", "lead_nudge_tokens"])
def test_config_cli_range_key_outside_range_is_fail(tmp_path, key):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, json.dumps({key: 0}))))
    assert r["status"] == doctor.FAIL
    assert any(line.startswith(f"{key}:") and "(outside its range)" in line for line in r["info"])


def test_reject_reason_cli_range_that_raises_gives_not_a_usable_number(monkeypatch):
    calls = []

    def raiser(v):
        calls.append(v)
        raise ValueError("boom")

    monkeypatch.setitem(doctor.config_mod.CLI_RANGES, "stall_threshold_seconds", raiser)
    default = doctor.config_mod.DEFAULTS["stall_threshold_seconds"]
    reason = doctor._reject_reason("stall_threshold_seconds", default, 5)
    assert reason == "not a usable number"
    assert calls == [5]


def test_config_usage_limit_pattern_invalid_regex_is_fail(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, json.dumps({"usage_limit_pattern": "("}))))
    assert r["status"] == doctor.FAIL
    assert any(line.startswith("usage_limit_pattern:") and "(not a valid pattern)" in line for line in r["info"])


def test_config_usage_limit_pattern_null_stays_pass(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, json.dumps({"usage_limit_pattern": None}))))
    assert r["status"] == doctor.PASS
    assert r["info"] == []


def test_show_cuts_the_long_default_pattern_and_closes_the_quote():
    """Today's bug: a value cut to `_show`'s limit can end inside an open string, with no closing
    quote. Pinned whole: the built-in `usage_limit_pattern` cut at 60 characters, closed with `…"`."""
    assert doctor._show(doctor.config_mod.USAGE_LIMIT_PATTERN) == \
        '"out of extra usage|monthly usage limit reached|insufficie…"'


# ── 11. models.json ──────────────────────────────────────────────────────────────────────────────
def _write_models(home, *, age, count=1):
    home.mkdir(exist_ok=True)
    fetched = FIXED_NOW - age
    ms = [{"provider": "anthropic", "id": f"claude-haiku-4-{i}", "context": "200K"} for i in range(count)]
    (home / "models.json").write_text(json.dumps({"fetched": fetched, "models": ms}))


def test_models_home_missing_skips(tmp_path):
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_models_absent(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and "absent" in r["detail"]


def test_models_unparsable(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "models.json").write_text("not json")
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and "unparsable" in r["detail"]


def test_models_empty(tmp_path):
    home = tmp_path / "home"
    _write_models(home, age=10, count=0)
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and "empty" in r["detail"]


def test_models_older(tmp_path):
    home = tmp_path / "home"
    _write_models(home, age=models.MAX_AGE + 10)
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and "older" in r["detail"]


def test_models_pass(tmp_path):
    home = tmp_path / "home"
    _write_models(home, age=10, count=3)
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "3 models" in r["detail"]


# ── 12. model aliases ────────────────────────────────────────────────────────────────────────────
FULL_MODEL_LIST = [
    {"provider": "anthropic", "id": "claude-haiku-4-5", "context": "200K"},
    {"provider": "anthropic", "id": "claude-sonnet-5", "context": "1M"},
    {"provider": "anthropic", "id": "claude-opus-5", "context": "1M"},
    {"provider": "anthropic", "id": "claude-fable-5", "context": "1M"},
    {"provider": "deepseek", "id": "deepseek-flash", "context": "1M"},
    {"provider": "deepseek", "id": "deepseek-v4-pro", "context": "1M"},
]


def _write_full_models(home, *, age=10):
    home.mkdir(exist_ok=True)
    (home / "models.json").write_text(json.dumps({"fetched": FIXED_NOW - age, "models": FULL_MODEL_LIST}))


def test_model_aliases_home_missing_skips(tmp_path):
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_model_aliases_skip_when_models_not_pass(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_model_aliases_skip_near_expiry(tmp_path):
    home = tmp_path / "home"
    _write_full_models(home, age=models.MAX_AGE - 30)  # within 60s of MAX_AGE
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_model_aliases_pass(tmp_path):
    home = tmp_path / "home"
    _write_full_models(home)
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert len(r["info"]) == len(models.ALIASES)
    assert r["info"][0].startswith("haiku → ")


def test_model_aliases_warn_unmatched(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    partial = [m for m in FULL_MODEL_LIST if m["provider"] != "deepseek"]
    (home / "models.json").write_text(json.dumps({"fetched": FIXED_NOW - 10, "models": partial}))
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert "dsflash" in r["detail"] and "dspro" in r["detail"]


# ── 13. ledger ───────────────────────────────────────────────────────────────────────────────────
def test_ledger_home_missing_skips(tmp_path):
    r = _one(doctor.check_ledger(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_ledger_no_file(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_ledger(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "no ledger yet" in r["detail"]


def test_ledger_all_good(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "ledger.jsonl").write_text('{"event": "a"}\n{"event": "b"}\n')
    r = _one(doctor.check_ledger(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "2 record" in r["detail"]


def test_ledger_some_bad_lines(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "ledger.jsonl").write_text('{"event": "a"}\nnot json\n{"event": "b"}\n')
    r = _one(doctor.check_ledger(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert "line 2" in r["detail"] and "readers skip them" in r["detail"]


def test_ledger_fail_unreadable(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    p = home / "ledger.jsonl"
    p.write_text("x")
    os.chmod(p, 0o000)
    try:
        r = _one(doctor.check_ledger(make_ctx(tmp_path)))
        assert r["status"] == doctor.FAIL
    finally:
        os.chmod(p, 0o644)


# ── 14. state ────────────────────────────────────────────────────────────────────────────────────
def test_state_home_missing_skips(tmp_path):
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_state_pass_empty(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS and "0 lead" in r["detail"]


def test_state_fail_unparsable(tmp_path):
    home = tmp_path / "home"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "lead-1.json").write_text("not json")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.FAIL
    assert "leads/lead-1.json" in r["detail"]


def test_state_warn_orphan_session(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", lead="no-such-lead")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert any("sess-1" in line for line in r["info"])


def test_state_open_count(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-open", lead=None, status="busy")
    _write_open_session(home, "sess-closed", lead=None, status="closed")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert "2 session" in r["detail"] and "1 open" in r["detail"]


# ── 14b. state: a lead usage cache is not a lead record (change 5) ─────────────────────────────────
def test_state_usage_cache_under_leads_is_not_a_lead_record(tmp_path):
    home = tmp_path / "home"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "lead-1.json").write_text(json.dumps({"started": "-", "project": "p"}))
    (home / "leads" / "lead-1.usage.json").write_text("not json")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert "1 lead(s)" in r["detail"]


def test_state_orphan_ignores_a_lead_usage_cache_suffix(tmp_path):
    home = tmp_path / "home"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "lead-1.json").write_text(json.dumps({"started": "-", "project": "p"}))
    _write_open_session(home, "sess-1", lead="lead-1")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert r["info"] == []  # lead-1 is a real lead record: sess-1 is no orphan


def test_state_orphan_when_only_a_usage_cache_matches_the_lead_name(tmp_path):
    home = tmp_path / "home"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "lead-1.usage.json").write_text(json.dumps({"key": [], "usage": {}}))
    _write_open_session(home, "sess-1", lead="lead-1.usage")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert any("sess-1" in line for line in r["info"])


def test_state_open_count_across_five_statuses(tmp_path):
    home = tmp_path / "home"
    for sid, status in [("s-busy", "busy"), ("s-stalled", "stalled"), ("s-paused", "paused"),
                         ("s-closed", "closed"), ("s-dead", "dead")]:
        _write_open_session(home, sid, status=status)
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert "5 session(s)" in r["detail"] and "3 open" in r["detail"]


# ── 15. pi session logs (change 3) ──────────────────────────────────────────────────────────────────
def test_session_logs_warn_when_nothing_has_run(tmp_path):
    """conftest points PI_CODING_AGENT_DIR at an empty dir: no `sessions/` subdirectory exists yet."""
    r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert "tokens and context will read as `-`" in r["detail"]


def test_session_logs_pass_counts_directories_and_jsonl_files(tmp_path):
    from test_usage import assistant, u, write_log
    write_log("sess-a", tmp_path / "wt-a", [assistant(u(10, 5))])
    r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS
    assert "1 directory" in r["detail"] and "1 *.jsonl" in r["detail"]


def test_session_logs_not_checked_when_a_directory_cannot_be_listed(tmp_path):
    """A real permission bit, not a monkeypatched `os.listdir` — patching that globally also breaks
    `Path.iterdir()` (used by `session_dirs()` itself) on some Python builds."""
    from test_usage import agent_dir
    d = agent_dir() / "sessions" / "proj"
    d.mkdir(parents=True)
    os.chmod(d, 0o000)
    try:
        r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
        assert r["status"] == doctor.NOT_CHECKED
    finally:
        os.chmod(d, 0o755)


# ── 16. executor logs (change 3) ────────────────────────────────────────────────────────────────────
def test_executor_logs_home_missing_skips(tmp_path):
    r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_executor_logs_no_open_sessions_skips(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", status="dead")
    r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP and "no open sessions" in r["detail"]


def test_executor_logs_pass_when_every_open_session_has_a_log(tmp_path):
    from test_usage import assistant, u, write_log
    home = tmp_path / "home"
    wt = tmp_path / "wt"
    _write_open_session(home, "sess-1", worktree=str(wt), status="busy")
    write_log("sess-1", wt, [assistant(u(10, 5))])
    r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.PASS


def test_executor_logs_warn_names_sessions_with_no_log(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", worktree=str(tmp_path / "wt-1"), status="busy")
    r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert "sess-1" in r["detail"] and "heaviness gate cannot see it" in r["detail"]


def test_only_dead_session_skips_git_shim_and_executor_logs(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", status="dead")
    r_shim = _one(_run_and_cleanup(doctor.check_git_shim, make_ctx(tmp_path)))
    assert r_shim["status"] == doctor.SKIP and "no open sessions" in r_shim["detail"]
    r_logs = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r_logs["status"] == doctor.SKIP and "no open sessions" in r_logs["detail"]


# ── 17. git shim ─────────────────────────────────────────────────────────────────────────────────
def _run_and_cleanup(fn, ctx):
    try:
        return fn(ctx)
    finally:
        ctx.cleanup()


def test_git_shim_home_missing_skips(tmp_path):
    r = _one(doctor.check_git_shim(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP


def test_git_shim_no_open_sessions(tmp_path):
    (tmp_path / "home").mkdir()
    r = _one(doctor.check_git_shim(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP and "no open sessions" in r["detail"]


def test_git_shim_pass_real_shim(tmp_path):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim.write_git_shim(sdir)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.PASS


def test_git_shim_fail_stub_allows_commit(tmp_path):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim_dir = sdir / "shim"
    shim_dir.mkdir()
    (shim_dir / "git").write_text(f"#!/bin/sh\n# {shim.MARKER}\nexit 0\n")
    (shim_dir / "git").chmod(0o755)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.FAIL
    assert "sess-1" in r["detail"]


def test_git_shim_warn_older_version_text(tmp_path):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim_dir = sdir / "shim"
    shim_dir.mkdir()
    text = shim.script().replace("git shim", "git shim (older build)")
    (shim_dir / "git").write_text(text)
    (shim_dir / "git").chmod(0o755)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.WARN
    assert "another pi-lead version" in r["detail"]


def test_git_shim_fail_missing(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1")  # no shim/git written at all
    ctx = make_ctx(tmp_path)
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.FAIL and "missing" in r["detail"]


def test_git_shim_refusal_probe_env_and_cwd(tmp_path):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim_dir = sdir / "shim"
    shim_dir.mkdir()
    env_out, cwd_out = tmp_path / "env.out", tmp_path / "cwd.out"
    script = textwrap.dedent(f"""\
        #!/bin/sh
        # {shim.MARKER}
        env > {env_out}
        pwd > {cwd_out}
        printf '%s\\n' '{shim.REFUSAL}' >&2
        exit {shim.EXIT_DENIED}
        """)
    (shim_dir / "git").write_text(script)
    (shim_dir / "git").chmod(0o755)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", ""), "GIT_DIR": "/should/not/appear"})
    expected_dir = ctx.throwaway_dir()  # force creation now, so it is still there to compare against
    try:
        r = _one(doctor.check_git_shim(ctx))
    finally:
        ctx.cleanup()
    # this hand-written probe script's text is not shim.script(), so it is also flagged WARN
    # ("written by another pi-lead version"); test_git_shim_pass_real_shim covers the exact-match case.
    assert r["status"] == doctor.WARN
    recorded_env = env_out.read_text()
    git_vars = dict(line.split("=", 1) for line in recorded_env.splitlines() if line.startswith("GIT_"))
    assert set(git_vars) == {"GIT_DIR", "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"}
    assert git_vars["GIT_DIR"] != "/should/not/appear"
    assert Path(git_vars["GIT_DIR"]).parent == expected_dir and not Path(git_vars["GIT_DIR"]).exists()
    assert git_vars["GIT_CEILING_DIRECTORIES"] == str(expected_dir.parent)
    assert git_vars["GIT_CONFIG_NOSYSTEM"] == "1" and git_vars["GIT_CONFIG_GLOBAL"] == "/dev/null"
    recorded_cwd = Path(cwd_out.read_text().strip()).resolve()
    assert recorded_cwd == expected_dir.resolve()


def test_git_shim_refusal_probe_timeout_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "REFUSAL_PROBE_TIMEOUT", 0.2)
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim_dir = sdir / "shim"
    shim_dir.mkdir()
    (shim_dir / "git").write_text(f"#!/bin/sh\n# {shim.MARKER}\nsleep 5\n")
    (shim_dir / "git").chmod(0o755)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.NOT_CHECKED



def _guarded_session(tmp_path):
    """An open session whose worktree is a real throwaway repository, with the guarded shim
    `pilead spawn` writes for it; returns (session dir, worktree)."""
    home = tmp_path / "home"
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(tmp_path.resolve().parent))
    subprocess.run([shim._real_git(), "init", "-q", str(wt)], env=env, check=True, capture_output=True)
    sdir = _write_open_session(home, "sess-1", worktree=str(wt))
    shim.write_git_shim(sdir, guarded=wt)
    return sdir, wt


def test_git_shim_pass_real_guarded_shim(tmp_path):
    """A shim written with the session's worktree as its guard is the current text: PASS, not WARN."""
    sdir, wt = _guarded_session(tmp_path)
    text = (sdir / "shim" / "git").read_text()
    assert "PL_GUARD_TOP=" in text and text != shim.script()
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert r["status"] == doctor.PASS, r["detail"]


def test_git_shim_warn_stale_guarded_shim(tmp_path):
    """A guarded shim that is not what this pi-lead writes for the session still reads WARN: an older
    build's text, and the guard-everything text written before the session had a guard."""
    sdir, wt = _guarded_session(tmp_path)
    path = sdir / "shim" / "git"
    current = path.read_text()
    for stale in (current.replace("git shim", "git shim (older build)"), shim.script()):
        path.write_text(stale)
        ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
        r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
        assert r["status"] == doctor.WARN and "another pi-lead version" in r["detail"], r["detail"]

# ── 16. pi ───────────────────────────────────────────────────────────────────────────────────────
def test_pi_offline_skips(tmp_path):
    r = _one(doctor.check_pi(make_ctx(tmp_path, offline=True)))
    assert r["status"] == doctor.SKIP


def test_pi_not_found(tmp_path):
    r = _one(doctor.check_pi(make_ctx(tmp_path, env={"PATH": str(_bin(tmp_path, "empty"))})))
    assert r["status"] == doctor.FAIL


def test_pi_pass(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "pi", "0.87.1")
    r = _one(doctor.check_pi(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.PASS and "0.87.1" in r["detail"]


def test_pi_fail_too_old(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "pi", "0.86.0")
    r = _one(doctor.check_pi(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.FAIL


def test_pi_not_checked_unparsable(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "pi", "banana")
    r = _one(doctor.check_pi(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.NOT_CHECKED


# ── 17-19. the pi probe ──────────────────────────────────────────────────────────────────────────
def _cmd(name, source="extension", path=None):
    d = {"name": name, "source": source}
    if path is not None:
        d["sourceInfo"] = {"path": path}
    return d


def _pi_env(d):
    """`_run_pi_probe` passes `ctx.env` wholesale as the child's own environment (scrubbed of
    PI_LEAD_*), unlike the version-only checks above (which never override `env=`, so the real
    process environment resolves their `#!/bin/sh` stubs). A `#!/usr/bin/env python3` stub therefore
    needs a real PATH behind the stub dir — `d` goes first, so it still wins over any real `pi`."""
    return {"PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}"}


def _write_pi_rpc_stub(path, *, mode="reply", commands=None, pid_file=None):
    """A stub `pi` that reads one stdin line, then (mode="reply") answers `get_commands` with
    `commands` and keeps running like real pi's rpc mode does (never exits on its own — confirmed by
    tests/ts/rpc.test.ts, which kills it too); (mode="hang") never answers at all; (mode="bad_json")
    answers with a line that is not JSON. Always writes its own pid first, so a test can confirm the
    probe leaves no process running."""
    body = ["#!/usr/bin/env python3", "import sys, json, os, time"]
    if pid_file:
        body.append(f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))")
    body.append("sys.stdin.readline()")
    if mode == "reply":
        reply = {"type": "response", "command": "get_commands", "data": {"commands": commands or []}}
        body.append(f"sys.stdout.write(json.dumps({reply!r}) + chr(10)); sys.stdout.flush()")
    elif mode == "bad_json":
        body.append("sys.stdout.write('not json at all' + chr(10)); sys.stdout.flush()")
    body.append("time.sleep(3600)")
    path.write_text("\n".join(body) + "\n")
    path.chmod(0o755)


FULL_COMMANDS = ([_cmd(f"pilead:{n}", path=str(ROOT / "extensions" / "pi-lead.ts")) for n in doctor.COMMAND_NAMES]
                  + [_cmd(f"skill:pilead-{n}", source="skill") for n in doctor.SKILL_FILES])


def test_pi_probe_offline_skips_all_three(tmp_path):
    ctx = make_ctx(tmp_path, offline=True)
    assert _one(doctor.check_pi_commands(ctx))["status"] == doctor.SKIP
    assert _one(doctor.check_pi_skills(ctx))["status"] == doctor.SKIP
    assert _one(doctor.check_one_copy(ctx))["status"] == doctor.SKIP


def test_pi_probe_pass_all_three(tmp_path):
    d = _bin(tmp_path)
    _write_pi_rpc_stub(d / "pi", commands=FULL_COMMANDS)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        assert _one(doctor.check_pi_commands(ctx))["status"] == doctor.PASS
        assert _one(doctor.check_pi_skills(ctx))["status"] == doctor.PASS
        r = _one(doctor.check_one_copy(ctx))
        assert r["status"] == doctor.PASS
    finally:
        ctx.cleanup()


def test_pi_probe_runs_pi_exactly_once_for_all_three(tmp_path):
    d = _bin(tmp_path)
    log = tmp_path / "calls.log"
    script = f"""#!/bin/sh
printf 'x\\n' >> {log}
exec /usr/bin/env python3 - <<'PY'
import sys, json, time
sys.stdin.readline()
sys.stdout.write(json.dumps({{"type": "response", "command": "get_commands", "data": {{"commands": []}}}}) + "\\n")
sys.stdout.flush()
time.sleep(3600)
PY
"""
    (d / "pi").write_text(script)
    (d / "pi").chmod(0o755)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        doctor.check_pi_commands(ctx)
        doctor.check_pi_skills(ctx)
        doctor.check_one_copy(ctx)
    finally:
        ctx.cleanup()
    assert len(log.read_text().splitlines()) == 1


def test_pi_probe_missing_commands_and_skills(tmp_path):
    d = _bin(tmp_path)
    _write_pi_rpc_stub(d / "pi", commands=[_cmd("pilead:mode", path=str(ROOT))])
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_pi_commands(ctx))
        assert r["status"] == doctor.FAIL and "pilead:spawn" in r["detail"]
        r = _one(doctor.check_pi_skills(ctx))
        assert r["status"] == doctor.WARN and "skill:pilead-spawn" in r["detail"]
    finally:
        ctx.cleanup()


def test_pi_probe_no_pilead_commands_at_all(tmp_path):
    d = _bin(tmp_path)
    _write_pi_rpc_stub(d / "pi", commands=[_cmd("something-else")])
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_pi_commands(ctx))
        assert r["status"] == doctor.FAIL and "no pilead: command listed" in r["detail"]
        r = _one(doctor.check_one_copy(ctx))
        assert r["status"] == doctor.SKIP and "no pilead: command found" in r["detail"]
    finally:
        ctx.cleanup()


def test_pi_probe_one_copy_warn_outside_root(tmp_path):
    d = _bin(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "pi-lead.ts"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("// elsewhere\n")
    cmds = [_cmd(f"pilead:{n}", path=str(elsewhere)) for n in doctor.COMMAND_NAMES]
    _write_pi_rpc_stub(d / "pi", commands=cmds)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_one_copy(ctx))
        assert r["status"] == doctor.WARN and str(elsewhere) in r["detail"]
    finally:
        ctx.cleanup()


def test_pi_probe_one_copy_not_checked_no_source_path(tmp_path):
    d = _bin(tmp_path)
    cmds = [_cmd(f"pilead:{n}") for n in doctor.COMMAND_NAMES]  # no sourceInfo at all
    _write_pi_rpc_stub(d / "pi", commands=cmds)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_one_copy(ctx))
        assert r["status"] == doctor.NOT_CHECKED
    finally:
        ctx.cleanup()


def test_pi_probe_bad_json_not_checked(tmp_path):
    d = _bin(tmp_path)
    _write_pi_rpc_stub(d / "pi", mode="bad_json")
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_pi_commands(ctx))
        assert r["status"] == doctor.NOT_CHECKED
    finally:
        ctx.cleanup()


def test_pi_probe_timeout_not_checked_and_leaves_no_process(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "PI_PROBE_TIMEOUT", 2)  # short, but enough headroom for a slow python3 start
    d = _bin(tmp_path)
    pid_file = tmp_path / "pid.out"
    _write_pi_rpc_stub(d / "pi", mode="hang", pid_file=pid_file)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    try:
        r = _one(doctor.check_pi_commands(ctx))
        assert r["status"] == doctor.NOT_CHECKED
    finally:
        ctx.cleanup()
    # give the killed process a moment to actually leave the process table
    import time as _time
    pid = int(pid_file.read_text().strip())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        _time.sleep(0.1)
    else:
        pytest.fail(f"pi probe left pid {pid} running")


# ── throwaway directory ──────────────────────────────────────────────────────────────────────────
def test_throwaway_dir_removed_after_run_checks_success(tmp_path):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim.write_git_shim(sdir)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")}, offline=True)
    doctor.run_checks(ctx)
    assert ctx._throwaway is None  # cleaned up


def test_throwaway_dir_removed_after_a_check_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    shim.write_git_shim(sdir)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")}, offline=True)
    made_dir = ctx.throwaway_dir()
    assert made_dir.is_dir()

    @doctor._named("zz")
    def _boom(_ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "CHECKS", doctor.CHECKS + [_boom])
    doctor.run_checks(ctx)
    assert not made_dir.exists()


# ── a check that raises becomes NOT CHECKED; the rest still run ────────────────────────────────────
def test_a_raising_check_becomes_not_checked_others_still_run(tmp_path, monkeypatch):
    @doctor._named("python")
    def _boom(ctx):
        raise ValueError("kaboom\nsecond line")

    monkeypatch.setattr(doctor, "CHECKS", [_boom] + doctor.CHECKS[1:])
    ctx = make_ctx(tmp_path, offline=True)
    results = doctor.run_checks(ctx)
    assert results[0]["status"] == doctor.NOT_CHECKED
    assert results[0]["detail"] == "ValueError: kaboom"
    assert results[0]["check"] == "python"
    # every other check still ran (produced a result)
    assert len(results) == len(doctor.CHECKS)


def test_session_log_checks_sit_directly_after_state():
    names = [fn.check_name for fn in doctor.CHECKS]
    i = names.index("state")
    assert names[i + 1 : i + 3] == ["pi session logs", "executor logs"]


# ── read-only ────────────────────────────────────────────────────────────────────────────────────
def _snapshot(home):
    out = {}
    for p in sorted(home.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(home))] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


@pytest.mark.parametrize("models_state", ["absent", "fresh", "stale"])
def test_doctor_is_read_only(tmp_path, models_state):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"grace_seconds": 600}')
    (home / "ledger.jsonl").write_text('{"event": "a"}\n')
    if models_state == "fresh":
        _write_full_models(home, age=10)
    elif models_state == "stale":
        _write_full_models(home, age=models.MAX_AGE + 100)
    sdir = _write_open_session(home, "sess-1")
    shim.write_git_shim(sdir)
    before = _snapshot(home)
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")}, offline=True)
    doctor.run_checks(ctx)
    after = _snapshot(home)
    assert before == after


# ── the CLI wrapper: exit codes, summary, --json ─────────────────────────────────────────────────
def _patch_results(monkeypatch, results):
    import pilead.verbs.doctor as verbs_doctor
    monkeypatch.setattr(verbs_doctor.doctor, "run_checks", lambda ctx: results)


def _ns(**kw):
    base = dict(home=None, offline=False, strict=False, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _fake(status, check="x", detail="", info=()):
    return {"check": check, "status": status, "detail": detail, "info": list(info)}


def test_exit_code_all_pass(tmp_path, monkeypatch, capsys):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.PASS)])
    rc = verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h")))
    assert rc == 0


def test_exit_code_one_warn(tmp_path, monkeypatch):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.PASS), _fake(doctor.WARN)])
    assert verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"))) == 0
    assert verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"), strict=True)) == 1


def test_exit_code_one_fail(tmp_path, monkeypatch):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.FAIL)])
    assert verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"))) == 1


def test_exit_code_not_checked_no_fail(tmp_path, monkeypatch):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.PASS), _fake(doctor.NOT_CHECKED)])
    assert verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"))) == 3


def test_exit_code_fail_and_not_checked_together(tmp_path, monkeypatch):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.FAIL), _fake(doctor.NOT_CHECKED)])
    assert verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"))) == 1


def test_summary_line_and_never_says_all_passed(tmp_path, monkeypatch, capsys):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.PASS), _fake(doctor.WARN), _fake(doctor.FAIL),
                                  _fake(doctor.NOT_CHECKED), _fake(doctor.SKIP)])
    verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h")))
    out = capsys.readouterr().out
    assert "pilead doctor: 1 passed, 1 warnings, 1 failed, 1 not checked, 1 skipped" in out
    assert "all checks passed" not in out


def test_json_output(tmp_path, monkeypatch, capsys):
    import pilead.verbs.doctor as verbs_doctor
    _patch_results(monkeypatch, [_fake(doctor.PASS, check="a", detail="d", info=["i1"])])
    verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h"), json=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert set(data[0]) == {"check", "status", "detail", "info"}


# ── --offline starts no pi ──────────────────────────────────────────────────────────────────────
def test_offline_starts_no_pi(tmp_path):
    fake_pi_log = Path(os.environ["FAKE_PI_LOG"])
    if fake_pi_log.exists():
        fake_pi_log.unlink()
    home = tmp_path / "home"
    home.mkdir()
    ctx = doctor.build_context(home, offline=True)
    results = doctor.run_checks(ctx)
    for name in ("pi", "pi commands", "pi skills", "one copy"):
        assert _by_check(results, name)["status"] == doctor.SKIP
    assert not fake_pi_log.exists() or fake_pi_log.read_text() == ""


# ── manual end-to-end sanity (also exercised by acceptance item 4/5 by hand) ────────────────────
def test_cli_offline_smoke(tmp_path):
    r = subprocess.run([sys.executable, str(PILEAD), "doctor", "--offline", "--home", str(tmp_path / "h")],
                        capture_output=True, text=True)
    assert r.returncode in (0, 1, 3)
    assert "pilead doctor:" in r.stdout
    assert not (tmp_path / "h").exists()  # doctor is read-only: it never creates home


# ── review fixes: config.json literals at any depth ─────────────────────────────────────────────
@pytest.mark.parametrize("body, where", [
    ('{"grace_seconds": NaN}', "grace_seconds"),
    ('{"signoff_paths": [NaN]}', "signoff_paths[0]"),
    ('{"not_a_real_key": {"inner": Infinity}}', "not_a_real_key.inner"),
    ('{"signoff_paths": [{"deep": [-Infinity]}]}', "signoff_paths[0].deep[0]"),
])
def test_config_bare_literal_at_any_depth_fails(tmp_path, body, where):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, body)))
    assert r["status"] == doctor.FAIL
    token = next(t for t in ("-Infinity", "Infinity", "NaN") if t in body)
    assert token in r["detail"] and where in r["detail"]
    assert "pi extension" in r["detail"] and "ignores the whole file" in r["detail"]
    assert "Python CLI" in r["detail"] and "does not" in r["detail"]


def test_config_literal_replaced_by_duplicate_key_still_fails(tmp_path):
    """JSON.parse rejects the file even though Python's dict keeps only the later value."""
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"grace_seconds": NaN, "grace_seconds": 600}')))
    assert r["status"] == doctor.FAIL
    assert "NaN" in r["detail"] and "ignores the whole file" in r["detail"]


@pytest.mark.parametrize("body", ['{"executor_default_model": "NaN"}', '{"signoff_paths": ["NaN", "Infinity"]}'])
def test_config_the_word_nan_in_a_string_is_not_a_literal(tmp_path, body):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, body)))
    assert r["status"] == doctor.PASS and r["detail"] == "" and r["info"] == []


def test_config_wrong_type_holding_nan_is_fail_not_not_checked(tmp_path):
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, '{"grace_seconds": [NaN]}')))
    assert r["status"] == doctor.FAIL
    assert "grace_seconds[0]" in r["detail"]
    assert any("grace_seconds" in line and "wrong type" in line for line in r["info"])


def test_config_value_that_cannot_be_shown_is_shown_by_type():
    class Unshowable:
        pass
    assert doctor._show(Unshowable()).startswith("<a ")
    assert doctor._show([float("nan")]) == "[NaN]"


def test_config_clean_file_unchanged(tmp_path):
    body = '{"grace_seconds": 600, "signoff_paths": ["a", "b"], "executor_fallback_model": null}'
    r = _one(doctor.check_config(_cfg_ctx(tmp_path, body)))
    assert r == {"check": "config.json", "status": doctor.PASS, "detail": "", "info": []}


# ── review fixes: the pi probe's reader ──────────────────────────────────────────────────────────
RESPONSE = {"type": "response", "command": "get_commands", "data": {"commands": FULL_COMMANDS}}
EVENT = {"type": "agent_start"}


def _write_pi_writes_stub(path, writes, *, pid_file, then="sleep", ignore_term=False, version=None,
                          ready_file=None):
    """A stub `pi` that reads the request line, then performs each `(bytes, pause_after)` in
    `writes` as ONE raw `os.write` to fd 1 (no Python buffering), then either sleeps (`then="sleep"`,
    like real rpc-mode pi) or exits 0 (`then="exit"`). Writes its pid first.

    With `version`, `pi --version` prints that line and exits 0 before anything else (no pid
    written), so only the rpc probe writes `pid_file`. With `ready_file`, that file is created once
    the request line has been read — proof the caller is inside the probe, past starting pi and
    writing to it."""
    body = ["#!/usr/bin/env python3", "import os, sys, time, signal"]
    if version is not None:
        body.append(f"if sys.argv[1:2] == ['--version']: print({version!r}); sys.exit(0)")
    body.append(f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))")
    if ignore_term:
        body.append("signal.signal(signal.SIGTERM, signal.SIG_IGN)")
    body.append("sys.stdin.buffer.readline()")
    if ready_file is not None:
        body.append(f"open({str(ready_file)!r}, 'w').close()")
    for data, pause in writes:
        body.append(f"os.write(1, {data!r})")
        if pause:
            body.append(f"time.sleep({pause!r})")
    body.append("time.sleep(3600)" if then == "sleep" else "sys.exit(0)")
    path.write_text("\n".join(body) + "\n")
    path.chmod(0o755)


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


def _assert_gone(pid_file):
    pid = int(pid_file.read_text().strip())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"pi probe left pid {pid} running")


def _probe(tmp_path, monkeypatch, writes, timeout=20, **stub_kw):
    monkeypatch.setattr(doctor, "PI_PROBE_TIMEOUT", timeout)
    d = _bin(tmp_path)
    pid_file = tmp_path / "pid.out"
    _write_pi_writes_stub(d / "pi", writes, pid_file=pid_file, **stub_kw)
    ctx = make_ctx(tmp_path, env=_pi_env(d))
    t0 = time.monotonic()
    try:
        r = _one(doctor.check_pi_commands(ctx))
    finally:
        ctx.cleanup()
    elapsed = time.monotonic() - t0
    _assert_gone(pid_file)
    return r, elapsed, ctx


def test_pi_reader_event_and_response_in_one_write(tmp_path, monkeypatch):
    r, elapsed, _ = _probe(tmp_path, monkeypatch, [(_line(EVENT) + _line(RESPONSE), 0)])
    assert r["status"] == doctor.PASS
    assert elapsed < 5  # the full timeout is 20 s: the old reader lost the second line and waited it out


def test_pi_reader_response_split_across_two_writes(tmp_path, monkeypatch):
    whole = _line(RESPONSE)
    r, elapsed, _ = _probe(tmp_path, monkeypatch, [(whole[:25], 0.3), (whole[25:], 0)])
    assert r["status"] == doctor.PASS and elapsed < 5


def test_pi_reader_many_events_before_the_response(tmp_path, monkeypatch):
    events = b"".join(_line({**EVENT, "n": i}) for i in range(2000))  # > one 64 KiB pipe read
    writes = [(events[:70000], 0.05), (b"not json\n[1, 2]\n" + events[70000:], 0), (_line(RESPONSE), 0)]
    r, elapsed, _ = _probe(tmp_path, monkeypatch, writes)
    assert r["status"] == doctor.PASS and elapsed < 5


def test_pi_reader_partial_line_then_stall_honours_deadline(tmp_path, monkeypatch):
    r, elapsed, _ = _probe(tmp_path, monkeypatch, [(b'{"type": "response", "comm', 0)], timeout=1)
    assert r["status"] == doctor.NOT_CHECKED and "within 1s" in r["detail"]
    assert elapsed < 5  # deadline 1 s + the bounded kill grace


def test_pi_reader_stub_ignoring_sigterm_is_killed_within_grace(tmp_path, monkeypatch):
    r, elapsed, _ = _probe(tmp_path, monkeypatch, [(_line(EVENT), 0)], timeout=1, ignore_term=True)
    assert r["status"] == doctor.NOT_CHECKED
    assert elapsed < 5  # deadline 1 s + terminate grace + kill


def test_pi_reader_pi_exits_with_no_output(tmp_path, monkeypatch):
    r, elapsed, _ = _probe(tmp_path, monkeypatch, [], then="exit")
    assert r["status"] == doctor.NOT_CHECKED
    assert "exited before answering" in r["detail"] and "exit code 0" in r["detail"]
    assert elapsed < 5


# ── review fixes: smaller corrections ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fetched", ['"yesterday"', "null", "true", "[1]", "NaN"])
def test_models_fetched_not_a_number_is_warn_unparsable(tmp_path, fetched):
    home = tmp_path / "home"
    home.mkdir()
    ms = json.dumps(FULL_MODEL_LIST)
    (home / "models.json").write_text(f'{{"fetched": {fetched}, "models": {ms}}}')
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and r["detail"].startswith("unparsable")
    # check 12 follows the packet's table: SKIP whenever check 11 is not PASS (never NOT CHECKED)
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP and "did not pass" in r["detail"]


def _write_failing_version_stub(path, stdout_line, stderr_line, rc):
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' {stdout_line!r}\nprintf '%s\\n' {stderr_line!r} >&2\n"
                    f"exit {rc}\n")
    path.chmod(0o755)


def test_git_version_nonzero_exit_is_fail_even_with_a_version(tmp_path, monkeypatch):
    """`refs.git_path` itself skips a git whose `--version` exits non-zero, so the stub resolves
    normally and doctor's own `--version` run (through the `run` seam) is the one that fails."""
    monkeypatch.setattr(refs, "FALLBACK_GITS", ())
    d = _bin(tmp_path)
    _write_version_stub(d / "git", "git version 2.45.0")

    def run(argv, **kw):
        return doctor.RunResult(ok=True, returncode=3, stdout="git version 2.45.0\n",
                                stderr="fatal: something broke\nsecond line\n")

    r = _one(doctor.check_git(make_ctx(tmp_path, env={"PATH": str(d)}, run=run)))
    assert r["status"] == doctor.FAIL
    assert "exited 3" in r["detail"] and "fatal: something broke" in r["detail"]


def test_pi_version_nonzero_exit_is_fail_even_with_a_version(tmp_path):
    d = _bin(tmp_path)
    _write_failing_version_stub(d / "pi", "0.90.0", "Error: config broken", 2)
    r = _one(doctor.check_pi(make_ctx(tmp_path, env={"PATH": str(d)})))
    assert r["status"] == doctor.FAIL
    assert "exited 2" in r["detail"] and "Error: config broken" in r["detail"]


def test_version_program_that_cannot_run_or_times_out_stays_not_checked(tmp_path):
    d = _bin(tmp_path)
    _write_version_stub(d / "pi", "0.90.0")
    _write_version_stub(d / "git", "git version 2.45.0")
    for err in ("timeout", "[Errno 2] No such file or directory"):
        ctx = make_ctx(tmp_path, env={"PATH": str(d)},
                       run=lambda *a, _e=err, **k: doctor.RunResult(ok=False, error=_e))
        assert _one(doctor.check_pi(ctx))["status"] == doctor.NOT_CHECKED
        assert _one(doctor.check_git(ctx))["status"] == doctor.NOT_CHECKED


def test_ctrl_c_prints_one_line_and_exits_130(tmp_path, monkeypatch, capsys):
    import pilead.verbs.doctor as verbs_doctor
    made = {}
    real_build = doctor.build_context

    def build(home, offline=False):
        ctx = real_build(home, offline=offline)
        made["dir"] = ctx.throwaway_dir()
        return ctx

    def interrupted(ctx):
        raise KeyboardInterrupt

    monkeypatch.setattr(verbs_doctor.doctor, "build_context", build)
    monkeypatch.setattr(verbs_doctor.doctor, "run_checks", interrupted)
    rc = verbs_doctor.cmd_doctor(_ns(home=str(tmp_path / "h")))
    captured = capsys.readouterr()
    assert rc == 130
    assert captured.err == "pilead: doctor interrupted\n"
    assert "Traceback" not in captured.out + captured.err
    assert not made["dir"].exists()


def test_ctrl_c_during_the_pi_probe_end_to_end(tmp_path):
    """The real CLI, a real SIGINT while doctor waits on a stub pi: exit 130, one stderr line, no
    traceback, and the stub pi is gone afterwards."""
    import signal
    d = _bin(tmp_path)
    pid_file = tmp_path / "pid.out"
    ready_file = tmp_path / "probe-ready"
    # `version`: doctor's earlier `pi --version` check answers at once and writes no pid, so the pid
    # file and the ready file both come from the rpc probe. The SIGINT goes only after the stub has
    # read doctor's request line — doctor is then inside the probe (past starting pi and writing to
    # it), not in `pi --version` or mid-Popen. Generous deadlines: a loaded machine is slow, not wrong.
    _write_pi_writes_stub(d / "pi", [], pid_file=pid_file, version="0.99.0", ready_file=ready_file)
    env = {**os.environ, "PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}",
           "PI_LEAD_HOME": str(tmp_path / "h")}
    # A pytest started in the background (`&` without job control) inherits SIGINT as ignored, and
    # Python then installs no KeyboardInterrupt handler; restore the default so the child sees ctrl-c.
    proc = subprocess.Popen([sys.executable, str(PILEAD), "doctor"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        deadline = time.monotonic() + 60
        while not ready_file.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                pytest.fail("the stub pi probe never became ready")
            time.sleep(0.05)
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 130
    assert err == "pilead: doctor interrupted\n"
    assert "Traceback" not in out + err
    _assert_gone(pid_file)


# ── review fixes: the refusal probe cannot reach a real repository ──────────────────────────────
def _tree_hash(root):
    """One hash over every entry under `root` (`.git` included): each relative path, its kind, and a
    file's bytes or a symlink's target — so HEAD, refs, index and working tree all count."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).encode()
        if p.is_symlink():
            h.update(b"L" + rel + b"\0" + os.readlink(p).encode() + b"\0")
        elif p.is_file():
            h.update(b"F" + rel + b"\0" + p.read_bytes() + b"\0")
        else:
            h.update(b"D" + rel + b"\0")
    return h.hexdigest()


def test_git_shim_refusal_probe_cannot_reach_an_enclosing_repository(tmp_path, monkeypatch):
    """A shim that passes `commit` straight through to the real git, and a throwaway directory made
    INSIDE a scratch repository (one commit, one staged file) — with an inherited GIT_DIR and
    GIT_WORK_TREE pointing at that repository as well. The probe must report FAIL, and git must not
    have found the repository: every byte under it is the same before and after. No editor is ever
    reached (git stops at repository discovery), and the environment names a no-op one anyway."""
    real_git = refs.git_path(os.environ.get("PATH", ""))
    iso = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path)}
    iso = {k: v for k, v in iso.items() if not k.startswith("GIT_") or k.startswith("GIT_CONFIG_")}

    def git(*argv):
        subprocess.run([real_git, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *argv], check=True, capture_output=True, env=iso)

    repo = tmp_path / "scratch-repo"
    repo.mkdir()
    git("init", "-q")
    (repo / "committed.txt").write_text("one\n")
    git("add", "committed.txt")
    git("commit", "-q", "-m", "first")
    (repo / "staged.txt").write_text("two\n")
    git("add", "staged.txt")
    inside = repo / "tmp-inside"
    inside.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(inside))  # doctor's mkdtemp lands inside the repository
    monkeypatch.setattr(doctor, "REFUSAL_PROBE_TIMEOUT", 5)  # bounded even if something did prompt

    home = tmp_path / "home"
    sdir = _write_open_session(home, "sess-1")
    (sdir / "shim").mkdir()
    (sdir / "shim" / "git").write_text(f'#!/bin/sh\n# {shim.MARKER}\nexec {real_git} "$@"\n')
    (sdir / "shim" / "git").chmod(0o755)

    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "EDITOR": "true", "VISUAL": "true",
           "GIT_EDITOR": "true", "GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo)}
    ctx = make_ctx(tmp_path, env=env)
    before = _tree_hash(repo)
    r = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    after = _tree_hash(repo)

    assert r["status"] == doctor.FAIL and "not refused" in r["detail"]
    assert "no-such-git-dir" in r["detail"]  # git looked only at the GIT_DIR doctor set, and it did not exist
    assert after == before
    assert not list(inside.iterdir())  # the throwaway directory is gone too


@pytest.mark.parametrize("models_value", ['"claude"', "3", '{"a": {"provider": "x", "id": "y"}}'])
def test_models_value_not_a_list_is_warn_unparsable(tmp_path, models_value):
    home = tmp_path / "home"
    home.mkdir()
    (home / "models.json").write_text(f'{{"fetched": {FIXED_NOW - 10}, "models": {models_value}}}')
    r = _one(doctor.check_models(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN and r["detail"].startswith("unparsable")
    r = _one(doctor.check_model_aliases(make_ctx(tmp_path)))
    assert r["status"] == doctor.SKIP and "did not pass" in r["detail"]


# ── review notes: a directory under sessions/ whose name is not a session id ───────────────────────
def test_state_skips_and_names_a_session_directory_with_an_unusable_name(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1")
    _write_open_session(home, "a b")
    (home / "sessions" / "a b" / "meta.json").write_text("not json either")  # never read
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert r["detail"] == "0 lead(s), 1 session(s), 1 open"
    assert r["info"] == ["sessions/a b: not a usable session id"]


def test_state_names_an_unusable_name_that_holds_a_newline_quoted(tmp_path):
    home = tmp_path / "home"
    _write_open_session(home, "a\nb")
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert r["info"] == ["sessions/'a\\nb': not a usable session id"]


def test_an_unusable_session_directory_leaves_the_other_sessions_checked(tmp_path):
    from test_usage import assistant, u, write_log
    home, wt = tmp_path / "home", tmp_path / "wt"
    sdir = _write_open_session(home, "sess-1", worktree=str(wt))
    shim.write_git_shim(sdir)
    write_log("sess-1", wt, [assistant(u(10, 5))])
    _write_open_session(home, "a b")
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    r_logs = _one(doctor.check_executor_logs(ctx))
    assert (r_logs["status"], r_logs["detail"]) == (doctor.PASS, "1 open session(s)")
    r_shim = _one(_run_and_cleanup(doctor.check_git_shim, ctx))
    assert (r_shim["status"], r_shim["detail"]) == (doctor.PASS, "1 open session(s)")


# ── review notes: executor logs — a directory that could not be listed is not "no log" ─────────────
def test_executor_logs_not_checked_when_the_log_directory_cannot_be_listed(tmp_path):
    from test_usage import agent_dir
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", worktree=str(tmp_path / "wt-1"))
    d = agent_dir() / "sessions" / "proj"
    d.mkdir(parents=True)
    os.chmod(d, 0o000)
    try:
        r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    finally:
        os.chmod(d, 0o755)
    assert r["status"] == doctor.NOT_CHECKED
    assert r["detail"] == f"sess-1: cannot list {d}: Permission denied"
    assert "heaviness gate" not in r["detail"]


def test_executor_logs_not_checked_when_the_session_dir_override_cannot_be_listed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", worktree=str(tmp_path / "wt-1"))
    d = tmp_path / "flat-sessions"
    d.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(d))
    os.chmod(d, 0o000)
    try:
        r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    finally:
        os.chmod(d, 0o755)
    assert (r["status"], r["detail"]) == (doctor.NOT_CHECKED, f"sess-1: cannot list {d}: Permission denied")


def test_executor_logs_no_log_only_when_the_directory_was_listed_and_holds_none(tmp_path):
    from test_usage import agent_dir
    home = tmp_path / "home"
    _write_open_session(home, "sess-1", worktree=str(tmp_path / "wt-1"))
    (agent_dir() / "sessions" / "proj").mkdir(parents=True)  # listed, and holds no log
    r = _one(doctor.check_executor_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert r["detail"] == "sess-1: its tokens and context read as `-`, and the heaviness gate cannot see it"


# ── review notes: pi session logs — a reading that could not be made is NOT CHECKED ────────────────
def test_session_logs_not_checked_when_session_dirs_raises(tmp_path, monkeypatch):
    def boom(worktree=None):
        raise RuntimeError("settings unreadable\nsecond line")

    monkeypatch.setattr(doctor.usage_mod, "session_dirs", boom)
    r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.NOT_CHECKED
    assert r["detail"] == "cannot find pi's session directories (RuntimeError: settings unreadable)"


def test_session_logs_not_checked_when_a_directory_s_parent_cannot_be_read(tmp_path):
    """sessions/ can be listed (read) but not searched (no execute bit): usage.session_dirs drops
    every directory in it without a word; doctor says the reading was not made."""
    from test_usage import agent_dir
    root = agent_dir() / "sessions"
    (root / "proj").mkdir(parents=True)
    os.chmod(root, 0o400)
    try:
        r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    finally:
        os.chmod(root, 0o755)
    assert r["status"] == doctor.NOT_CHECKED
    assert r["detail"] == f"cannot list {root / 'proj'}: Permission denied"


def test_session_logs_not_checked_when_the_sessions_directory_cannot_be_listed(tmp_path):
    from test_usage import agent_dir
    root = agent_dir() / "sessions"
    root.mkdir()
    os.chmod(root, 0o000)
    try:
        r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    finally:
        os.chmod(root, 0o755)
    assert (r["status"], r["detail"]) == (doctor.NOT_CHECKED, f"cannot list {root}: Permission denied")


def test_session_logs_warn_kept_when_the_agent_directory_was_read_and_holds_no_session(tmp_path):
    from test_usage import agent_dir
    (agent_dir() / "sessions").mkdir()
    r = _one(doctor.check_session_logs(make_ctx(tmp_path)))
    assert r["status"] == doctor.WARN
    assert r["detail"].startswith("no pi session has run here yet")


# ── review notes: `_show` never cuts inside an escape, and marks every cut ─────────────────────────
def test_show_cuts_a_string_before_it_is_escaped():
    # cut on the JSON text, 58 characters of this end in a lone backslash (`\` of `\n`)
    value = "a" * 54 + "\n" * 10
    assert json.dumps(value)[:58].endswith("\\")
    assert doctor._show(value) == '"' + "a" * 54 + '\\n…"'


def test_show_cuts_a_non_ascii_string_on_whole_escapes():
    out = doctor._show("é" * 80)
    assert out == '"' + "\\u00e9" * 9 + '…"'
    assert len(out) <= 60


@pytest.mark.parametrize("value", [list(range(100)), {"k": "x" * 100}, ["x\n" * 40], [1.5] * 40])
def test_show_marks_every_cut_value_whatever_its_type(value):
    out = doctor._show(value)
    assert out.endswith("…") and len(out) <= 60
    assert json.dumps(value).startswith(out[:-1])  # a start of its JSON text...
    assert not re.search(r"(?<!\\)(\\\\)*\\(u[0-9a-fA-F]{0,3})?$", out[:-1])  # ...never cut inside an escape


def test_show_leaves_a_value_that_fits_as_it_is():
    assert doctor._show([1, 2, 3]) == "[1, 2, 3]"
    assert doctor._show("x" * 58) == json.dumps("x" * 58)


# ── review notes: a lead record that is not JSON FAILs with its usage cache beside it ───────────────
def test_state_fail_unparsable_lead_record_with_its_usage_cache_present(tmp_path):
    home = tmp_path / "home"
    (home / "leads").mkdir(parents=True)
    (home / "leads" / "lead-1.json").write_text("not json")
    (home / "usage" / "leads").mkdir(parents=True)
    (home / "usage" / "leads" / "lead-1.json").write_text(json.dumps({"key": [], "usage": {}}))
    r = _one(doctor.check_state(make_ctx(tmp_path)))
    assert r["status"] == doctor.FAIL
    assert r["detail"] == "does not parse: leads/lead-1.json"


# ── 17. git shim: the exec-path mirror beside a guarded shim (p11b r4) ─────────────────────────────────
def _mirror_check(tmp_path):
    ctx = make_ctx(tmp_path, env={"PATH": os.environ.get("PATH", "")})
    return _one(_run_and_cleanup(doctor.check_git_shim, ctx))


def test_git_shim_mirror_current_reads_ok(tmp_path):
    sdir, _ = _guarded_session(tmp_path)
    assert shim.mirror_state(sdir / "shim") == (True, "current")
    r = _mirror_check(tmp_path)
    assert r["status"] == doctor.PASS, r["detail"]


@pytest.mark.parametrize("how", ["missing", "no record", "git mtime", "exec-path replaced", "git not the shim"])
def test_git_shim_mirror_stale_or_missing_warns(tmp_path, how):
    """A missing mirror, one without its record, one whose real git has another mtime, one whose recorded
    inodes no longer match, and one whose `git` is not the shim: WARN (the shim rebuilds it at its next
    call), and doctor repairs nothing."""
    sdir, _ = _guarded_session(tmp_path)
    m = Path(shim.mirror_path(sdir / "shim"))
    rec = m / shim.MIRROR_RECORD
    if how == "missing":
        shutil.rmtree(m)
    elif how == "no record":
        rec.unlink()
    elif how == "git mtime":
        st = os.stat(m / shim.MIRROR_GIT_MTIME)
        os.utime(m / shim.MIRROR_GIT_MTIME, (st.st_atime, st.st_mtime - 100))
    elif how == "exec-path replaced":
        lines = rec.read_text().split("\n")
        lines[3] = "1 " + lines[3].split(" ", 1)[1]
        rec.write_text("\n".join(lines))
    else:
        (m / "git").unlink()
        (m / "git").symlink_to(shim._real_git())
    before = sorted(str(p) for p in m.rglob("*")) if m.exists() else None
    r = _mirror_check(tmp_path)
    assert r["status"] == doctor.WARN and "exec-path mirror" in r["detail"], r["detail"]
    assert (sorted(str(p) for p in m.rglob("*")) if m.exists() else None) == before


# ── --quick (p23d) ──────────────────────────────────────────────────────────────────────────────
QUICK_SKIPPED = ("pi commands", "pi skills", "one copy")


def _doctor_cli(tmp_path, *args):
    """bin/pilead doctor against tests/fake_pi (conftest), its log emptied first: (rc, stdout, the log's calls)."""
    log = Path(os.environ["FAKE_PI_LOG"])
    if log.exists():
        log.unlink()
    r = subprocess.run([sys.executable, str(PILEAD), "doctor", *args, "--home", str(tmp_path / "h")],
                       capture_output=True, text=True)
    calls = [json.loads(ln)["argv"] for ln in log.read_text().splitlines()] if log.exists() else []
    return r.returncode, r.stdout, calls


def _rule(results, strict=False):
    statuses = {x["status"] for x in results}
    if doctor.FAIL in statuses or (strict and doctor.WARN in statuses):
        return 1
    return 3 if doctor.NOT_CHECKED in statuses else 0


def test_quick_skips_the_three_and_starts_pi_only_for_its_version(tmp_path):
    rc_full, out_full, _calls = _doctor_cli(tmp_path, "--json")
    rc, out, calls = _doctor_cli(tmp_path, "--quick", "--json")
    full, quick = json.loads(out_full), json.loads(out)
    assert [x["check"] for x in quick] == [x["check"] for x in full]
    for f, q in zip(full, quick):
        if q["check"] in QUICK_SKIPPED:
            assert (q["status"], q["detail"]) == (doctor.SKIP, "--quick"), q
        else:
            assert q == f, q["check"]
    assert _by_check(quick, "pi")["status"] != doctor.SKIP
    assert calls == [["--version"]], calls  # no RPC probe
    assert not any("rpc" in a for argv in calls for a in argv)
    assert rc == _rule(quick) and rc_full == _rule(full)


def test_quick_with_offline_gives_the_four_the_offline_detail(tmp_path):
    rc, out, calls = _doctor_cli(tmp_path, "--quick", "--offline", "--json")
    results = json.loads(out)
    for name in ("pi",) + QUICK_SKIPPED:
        assert (_by_check(results, name)["status"], _by_check(results, name)["detail"]) == (doctor.SKIP, "--offline")
    assert calls == []
    assert rc == _rule(results)


def test_quick_with_strict(tmp_path):
    rc, out, _calls = _doctor_cli(tmp_path, "--quick", "--strict", "--json")
    results = json.loads(out)
    assert rc == _rule(results, strict=True)
    assert all(_by_check(results, n)["detail"] == "--quick" for n in QUICK_SKIPPED)


def test_quick_summary_counts_the_three_as_skipped(tmp_path):
    _rc, out_full, _ = _doctor_cli(tmp_path)
    _rc, out_quick, _ = _doctor_cli(tmp_path, "--quick")
    _rc, js_full, _ = _doctor_cli(tmp_path, "--json")
    full = json.loads(js_full)
    n = {s: sum(1 for x in full if x["status"] == s) for s in (doctor.PASS, doctor.WARN, doctor.FAIL,
                                                                doctor.NOT_CHECKED, doctor.SKIP)}
    moved = {s: sum(1 for x in full if x["check"] in QUICK_SKIPPED and x["status"] == s) for s in n}
    want = (f"pilead doctor: {n[doctor.PASS] - moved[doctor.PASS]} passed, {n[doctor.WARN] - moved[doctor.WARN]} "
            f"warnings, {n[doctor.FAIL] - moved[doctor.FAIL]} failed, "
            f"{n[doctor.NOT_CHECKED] - moved[doctor.NOT_CHECKED]} not checked, "
            f"{n[doctor.SKIP] - moved[doctor.SKIP] + 3} skipped")
    assert out_quick.splitlines()[-1] == want
    for name in QUICK_SKIPPED:
        assert f"SKIP         {name}  — --quick" in out_quick.splitlines()


def test_quick_in_process_context(tmp_path):
    ctx = make_ctx(tmp_path, quick=True)
    for check in (doctor.check_pi_commands, doctor.check_pi_skills, doctor.check_one_copy):
        r = _one(check(ctx))
        assert (r["status"], r["detail"]) == (doctor.SKIP, "--quick")
    assert ctx._pi_probe_cache is None  # the probe never ran


# ── b12: terminal backend / linux helpers, and the iTerm rows under tmux or on Linux ─────────────────────
def _tmux_on_path(tmp_path):
    d = _bin(tmp_path, "tmuxbin")
    (d / "tmux").write_text("#!/bin/sh\nexit 99\n")  # never run: ctx.run is replaced
    (d / "tmux").chmod(0o755)
    return str(d)


def _recording_run(answer):
    calls = []

    def run(argv, cwd=None, env=None, timeout=None):
        calls.append(list(argv))
        return answer
    return run, calls


def test_terminal_backend_iterm_without_tmux(tmp_path):
    run, calls = _recording_run(None)
    r = _one(doctor.check_terminal_backend(make_ctx(tmp_path, run=run, platform="Darwin")))
    assert r["check"] == "terminal backend" and r["status"] == doctor.PASS
    assert r["detail"] == ("selected: iterm · tmux: not on PATH · $TMUX=(unset) · $PILEAD_TMUX_SOCKET=(unset) · "
                           "$RELAY_TMUX_SOCKET=(unset)")
    assert calls == []


def test_terminal_backend_inside_tmux_runs_only_tmux_v(tmp_path):
    run, calls = _recording_run(doctor.RunResult(ok=True, returncode=0, stdout="tmux 3.4\n"))
    env = {"PATH": _tmux_on_path(tmp_path), "TMUX": "/tmp/tmux-501/default,1,0", "PILEAD_TMUX_SOCKET": "pp",
           "RELAY_TMUX_SOCKET": "rr"}
    r = _one(doctor.check_terminal_backend(make_ctx(tmp_path, env=env, run=run, platform="Darwin")))
    assert r["status"] == doctor.PASS
    assert r["detail"] == ("selected: tmux · tmux: tmux 3.4 · $TMUX=/tmp/tmux-501/default,1,0 · "
                           "$PILEAD_TMUX_SOCKET=pp · $RELAY_TMUX_SOCKET=rr")
    assert calls == [["tmux", "-L", "pp", "-V"]]


def test_terminal_backend_config_and_a_failing_tmux_still_pass(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"terminal_app": "tmux"}))
    run, calls = _recording_run(doctor.RunResult(ok=True, returncode=1, stderr="no\n"))
    r = _one(doctor.check_terminal_backend(make_ctx(tmp_path, env={"PATH": _tmux_on_path(tmp_path)}, run=run,
                                                    platform="Darwin")))
    assert r["status"] == doctor.PASS
    assert r["detail"].startswith("selected: tmux · tmux: (error: exited 1: no) · $TMUX=(unset)")
    assert calls == [["tmux", "-V"]]


def test_terminal_backend_env_beats_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"terminal_app": "tmux"}))
    r = _one(doctor.check_terminal_backend(make_ctx(tmp_path, env={"PILEAD_TERMINAL": "iterm"}, platform="Darwin")))
    assert r["detail"].startswith("selected: iterm ·")


def test_terminal_backend_on_linux_without_tmux_says_so_and_adds_the_helpers_row(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_linux_helpers",
                        lambda: {"xdg-open": True, "xclip": False, "wl-copy": True, "notify-send": False})
    results = doctor.check_terminal_backend(make_ctx(tmp_path, platform="Linux"))
    assert [r["check"] for r in results] == ["terminal backend", "linux helpers"]
    assert all(r["status"] == doctor.PASS for r in results)
    assert results[0]["detail"].endswith(" — on Linux pi-lead needs tmux: run it inside tmux or set "
                                         "PILEAD_TERMINAL=tmux")
    assert results[1]["detail"] == ("xdg-open: yes (--open) · clipboard: wl-copy · notify-send: no (banner fallback) "
                                    "— none required")


def test_terminal_backend_on_linux_inside_tmux_has_no_advice(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_linux_helpers", lambda: {"xdg-open": False, "xclip": True, "wl-copy": True,
                                                            "notify-send": True})
    results = doctor.check_terminal_backend(make_ctx(tmp_path, env={"TMUX": "x"}, platform="Linux"))
    assert "needs tmux" not in results[0]["detail"]
    assert results[1]["detail"] == ("xdg-open: no (--open) · clipboard: xclip · notify-send: yes (banner fallback) "
                                    "— none required")


def test_linux_helpers_row_only_on_linux(tmp_path):
    assert doctor.check_linux_helpers(make_ctx(tmp_path, platform="Darwin")) == []
    names = [r["check"] for r in doctor.check_terminal_backend(make_ctx(tmp_path, platform="Darwin"))]
    assert names == ["terminal backend"]


def test_terminal_backend_sits_right_after_config(tmp_path):
    names = [fn.check_name for fn in doctor.CHECKS]
    assert names[names.index("config.json") + 1] == "terminal backend"


@pytest.mark.parametrize("fn,name", [(doctor.check_iterm2, "iTerm2"),
                                     (doctor.check_iterm2_python_api, "iTerm2 Python API"),
                                     (doctor.check_osascript, "osascript")])
def test_iterm_rows_skip_under_tmux_and_on_linux(tmp_path, fn, name):
    r = _one(fn(make_ctx(tmp_path, env={"TMUX": "x"}, platform="Darwin")))
    assert (r["check"], r["status"], r["detail"]) == (name, doctor.SKIP, "tmux backend selected")
    r = _one(fn(make_ctx(tmp_path, platform="Linux")))
    assert (r["check"], r["status"], r["detail"]) == (name, doctor.SKIP, "Linux")
    r = _one(fn(make_ctx(tmp_path, env={"TERM_PROGRAM": "iTerm.app"}, platform="Darwin")))
    assert r["status"] != doctor.SKIP


def test_package_files_require_the_tmux_and_platform_modules(tmp_path):
    root = _minimal_root(tmp_path)
    r = _one(doctor.check_package_files(make_ctx(tmp_path, root=root)))
    assert r["status"] == doctor.FAIL
    assert "vendor/relay/tmux_backend.py" in r["detail"] and "vendor/relay/platform_cmds.py" in r["detail"]
    (root / "vendor" / "relay" / "tmux_backend.py").write_text("# stub\n")
    (root / "vendor" / "relay" / "platform_cmds.py").write_text("# stub\n")
    assert _one(doctor.check_package_files(make_ctx(tmp_path, root=root)))["status"] == doctor.PASS


def test_a_terminal_app_value_doctor_does_not_know_is_reported(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"terminal_app": "terminal"}))
    r = _one(doctor.check_config(make_ctx(tmp_path)))
    assert r["status"] == doctor.FAIL
    assert r["info"] == ['terminal_app: "terminal" ignored (not one of auto, iterm, tmux); the default "auto" applies']
    (home / "config.json").write_text(json.dumps({"terminal_app": "TMUX"}))
    assert _one(doctor.check_config(make_ctx(tmp_path)))["status"] == doctor.PASS


@pytest.mark.parametrize("flag", ["--quick", "--offline"])
def test_quick_and_offline_run_the_terminal_backend_check(tmp_path, flag):
    env = {**os.environ, "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(PILEAD), "doctor", flag, "--json", "--home", str(tmp_path / "h")],
                       capture_output=True, text=True, env=env, timeout=120)
    rows = {x["check"]: x for x in json.loads(r.stdout)}
    assert rows["terminal backend"]["status"] == "PASS"
    assert rows["terminal backend"]["detail"].startswith("selected: iterm · ")

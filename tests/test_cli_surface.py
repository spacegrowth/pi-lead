"""The CLI surface is frozen across the verb-module split: `pilead --help`, every `pilead <verb> --help`,
and `list` / `list --json` / `check` over a fixture home are byte-identical to snapshots captured from
the single-cli.py tree (HEAD 01a798e) with COLUMNS=100 — except help.txt and help-check.txt, updated for
the `refs` verb and `check --refs-only` only, and the new help-refs.txt. Also: a verb is a file — a throwaway module
dropped into a copy of pilead/verbs/ shows up in --help with cli.py untouched. New: help-lint.txt and
help-doctor.txt snapshot `pilead lint --help` and `pilead doctor --help`."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
SNAP = Path(__file__).resolve().parent / "snapshots"
VERBS = ("lead-start", "lineup", "auto", "tier", "plan", "lint", "handoff", "close-predecessor", "takeover", "spawn", "send", "queue", "resume", "restart", "adopt", "focus", "tidy", "whoami", "list", "check", "report", "status", "stats", "verify", "review", "refs", "diff", "board", "notify", "turn-end",
         "gate", "route", "escalate", "close", "retire", "stop", "keep", "auto-close", "prune", "doctor")
HOME_TOKEN = "{HOME}"

REPORT = """Alpha did the thing; 3 tests, suite green, staged.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: lib/x.py

## Detail
more text
"""


def build_fixture(home):
    """A small state dir: one lead, a reported session (status file) and a busy one (meta fallback).
    `created` is unparsable on purpose so the AGE column is `-`, not wall-clock dependent."""
    home = Path(home)
    (home / "leads").mkdir(parents=True, exist_ok=True)
    (home / "leads" / "lead-1.json").write_text(json.dumps({"sid": "lead-1", "project": "proj"}) + "\n")
    a = home / "sessions" / "alpha-120000"
    b = home / "sessions" / "beta-130000"
    for d in (a, b):
        d.mkdir(parents=True, exist_ok=True)
    (a / "meta.json").write_text(json.dumps({
        "sid": "alpha-120000", "lead": "lead-1", "worktree": "/wt/alpha", "topic": "alpha",
        "model": "anthropic/claude-sonnet-5", "status": "busy", "created": "-", "packets": 1,
        "label": "[Exec] alpha-120000", "layout": "tab"}, indent=2) + "\n")
    (a / "status").write_text("reported\n")
    (a / "packet-0001.md").write_text("GOAL: alpha\n")
    (a / "report-0001.md").write_text(REPORT)
    (b / "meta.json").write_text(json.dumps({
        "sid": "beta-130000", "lead": "lead-1", "worktree": "/wt/beta", "topic": "beta-longer-topic",
        "model": "deepseek/deepseek-v4-flash", "status": "busy", "created": "", "packets": 12,
        "label": "[Exec] beta-130000", "layout": "pane"}, indent=2) + "\n")
    (b / "packet-0012.md").write_text("GOAL: beta\n")
    return home


# (snapshot name, argv, needs the fixture home)
CASES = [("help", ["--help"], False)] + [(f"help-{v}", [v, "--help"], False) for v in VERBS] + [
    ("list", ["list"], True),
    ("list-json", ["list", "--json"], True),
    ("check-reported", ["check", "alpha-120000"], True),
    ("check-busy", ["check", "beta-130000"], True),
    ("check-usage", ["check", "gamma-140000"], True),
]

USAGE_CASE = "check-usage"


def build_usage_session(home):
    """The fixture's third session, built only for the check-usage case (a session in the shared fixture
    would add a row to list.txt / list-json.txt): gamma-140000, reported, with a synthetic pi session log
    at pi's default place under $PI_CODING_AGENT_DIR (conftest points it at tmp) and a models.json with
    its window. Live context 130k: `approaching heavy` at the default thresholds."""
    from test_usage import assistant, u, write_log
    home = Path(home)
    g = home / "sessions" / "gamma-140000"
    g.mkdir(parents=True, exist_ok=True)
    (g / "meta.json").write_text(json.dumps({
        "sid": "gamma-140000", "lead": "lead-1", "worktree": "/wt/gamma", "topic": "gamma",
        "model": "anthropic/claude-sonnet-5", "status": "busy", "created": "-", "packets": 1,
        "label": "[Exec] gamma-140000", "layout": "tab"}, indent=2) + "\n")
    (g / "status").write_text("reported\n")
    (g / "packet-0001.md").write_text("GOAL: gamma\n")
    (g / "report-0001.md").write_text(REPORT.replace("Alpha", "Gamma"))
    (home / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": "anthropic", "id": "claude-sonnet-5", "context": "1M"}]}) + "\n")
    write_log("gamma-140000", "/wt/gamma", [
        assistant(u(3, 1200, 20000, 4000, cost=0.1)),
        assistant(u(2, 3400, 60000, 1000, cost=0.2)),
        assistant(u(5, 5000, 120000, 4995, cost=0.3)),
    ])


@pytest.fixture(autouse=True)
def _usage_session(request, _pi_lead_env):
    """Adds gamma-140000 to the fixture home before the check-usage case runs (the home is the one
    test_output_matches_snapshot builds: tmp_path / "surface-home")."""
    if getattr(request.node, "callspec", None) and request.node.callspec.params.get("name") == USAGE_CASE:
        build_usage_session(request.getfixturevalue("tmp_path") / "surface-home")


def run_case(argv, home, bin_=PILEAD):
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(home)}
    r = subprocess.run([sys.executable, str(bin_), *argv], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.replace(str(home), HOME_TOKEN)


@pytest.mark.parametrize("name,argv,fixture", CASES, ids=[c[0] for c in CASES])
def test_output_matches_snapshot(tmp_path, name, argv, fixture):
    home = tmp_path / "surface-home"
    if fixture:
        build_fixture(home)
    rc, out = run_case(argv, home)
    assert rc == 0
    want = (SNAP / f"{name}.txt").read_text()
    got, want = argparse_heading(out), argparse_heading(want)
    if name == "help" and sys.version_info >= (3, 13):
        # 3.13's argparse counts a subcommand's indent when sizing the help column (10 → 12), which
        # re-wraps only whitespace in the top-level help; words and order must still match exactly.
        got, want = " ".join(got.split()), " ".join(want.split())
    assert got == want, f"{name} drifted from its HEAD snapshot"


def argparse_heading(text):
    """The snapshots were captured under Python 3.9, whose argparse titles the flags section
    `optional arguments:`; 3.10+ says `options:`. Normalise that one heading line and nothing else."""
    return re.sub(r"^optional arguments:$", "options:", text, flags=re.M)


def test_heading_normalisation_touches_only_that_line():
    assert argparse_heading("x\noptional arguments:\n  -h\n") == "x\noptions:\n  -h\n"
    for other in ("  optional arguments:\n", "optional arguments: x\n", "positional arguments:\n", "options:\n"):
        assert argparse_heading(other) == other


def test_new_verb_is_a_new_file(tmp_path):
    """Copy the package, add verbs/zz_hello.py, and the verb appears in --help and runs; cli.py is
    byte-identical to the shipped one."""
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "bin", tree / "bin")
    shutil.copytree(ROOT / "lib", tree / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "vendor").symlink_to(ROOT / "vendor")
    (tree / "lib" / "pilead" / "verbs" / "zz_hello.py").write_text(
        "ORDER = 999\n\n\n"
        "def cmd_hello(a):\n    print('hello from a throwaway verb')\n\n\n"
        "def register(verb):\n    verb('zz-hello', cmd_hello, 'a throwaway verb for the discovery test')\n")
    assert (tree / "lib" / "pilead" / "cli.py").read_bytes() == (ROOT / "lib" / "pilead" / "cli.py").read_bytes()
    rc, out = run_case(["--help"], tmp_path / "h", bin_=tree / "bin" / "pilead")
    assert rc == 0 and "zz-hello" in out and "a throwaway verb for the discovery test" in out
    rc, out = run_case(["zz-hello"], tmp_path / "h", bin_=tree / "bin" / "pilead")
    assert rc == 0 and out == "hello from a throwaway verb\n"
    # the shipped tree does not have it
    assert "zz-hello" not in run_case(["--help"], tmp_path / "h")[1]


# ── one broken verb module never takes the others down ───────────────────────────────────────────
SHIPPED_VERBS = ["lead-start", "lineup", "auto", "tier", "plan", "lint", "handoff", "close-predecessor", "takeover", "spawn", "send", "queue", "resume", "restart", "adopt", "focus", "tidy", "whoami", "list", "check", "report", "status", "stats", "verify", "review", "refs", "diff", "board", "notify", "turn-end",
                 "gate", "route", "escalate", "close", "retire", "stop", "keep", "auto-close", "prune", "doctor"]


def test_real_package_discovers_exactly_the_shipped_verbs(capsys):
    """Pins the verb set: a shipped verb that silently fails to load (and is skipped) fails here."""
    sys.path.insert(0, str(ROOT / "lib"))
    try:
        from pilead import cli
        ap = cli.build_parser()
    finally:
        sys.path.remove(str(ROOT / "lib"))
    sub = next(a for a in ap._actions if a.dest == "verb")
    assert list(sub.choices) == SHIPPED_VERBS
    assert capsys.readouterr().err == ""


BROKEN = {
    "raises": "raise RuntimeError('boom at import')\n",
    "no_order": "def register(verb):\n    verb('no-order', lambda a: None, 'x')\n",
    "syntax": "def (:\n",
    "bad_register": "ORDER = 45\n\n\ndef register(verb):\n    raise ValueError('boom\\nin register')\n",
}


def copy_tree(tmp_path, **modules):
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "bin", tree / "bin")
    shutil.copytree(ROOT / "lib", tree / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "vendor").symlink_to(ROOT / "vendor")
    for name, body in modules.items():
        (tree / "lib" / "pilead" / "verbs" / f"zz_{name}.py").write_text(body)
    return tree / "bin" / "pilead"


@pytest.mark.parametrize("kind", list(BROKEN))
def test_a_broken_verb_module_is_skipped_with_one_stderr_line(tmp_path, kind):
    bin_ = copy_tree(tmp_path, **{kind: BROKEN[kind]})
    home = build_fixture(tmp_path / "surface-home")
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(home)}
    r = subprocess.run([sys.executable, str(bin_), "list"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert r.stdout.replace(str(home), HOME_TOKEN) == (SNAP / "list.txt").read_text()
    lines = r.stderr.splitlines()
    assert len(lines) == 1, r.stderr
    assert lines[0].startswith(f"pilead: verb module 'pilead.verbs.zz_{kind}' failed to load: ")
    want = {"raises": "RuntimeError: boom at import", "no_order": "AttributeError: no integer ORDER",
            "syntax": "SyntaxError: ", "bad_register": "ValueError: boom in register"}[kind]
    assert want in lines[0]
    # --help still lists every shipped verb (running a verb for real: the test below)
    r = subprocess.run([sys.executable, str(bin_), "--help"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and all(f"    {v}" in r.stdout for v in SHIPPED_VERBS)


def test_several_broken_modules_one_line_each_and_order_is_stable(tmp_path):
    good = ("ORDER = 5\n\n\ndef register(verb):\n    verb('aa-first', lambda a: print('first'), 'sorted by ORDER')\n")
    bin_ = copy_tree(tmp_path, raises=BROKEN["raises"], syntax=BROKEN["syntax"], first=good)
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(tmp_path / "h")}
    r = subprocess.run([sys.executable, str(bin_), "--help"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert len(r.stderr.splitlines()) == 2
    sub = [ln.split()[0] for ln in r.stdout.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert sub == ["aa-first"] + SHIPPED_VERBS


def test_running_a_skipped_verb_is_an_argparse_error_and_other_verbs_still_run(tmp_path):
    """verbs/list.py fails on import (in a temp copy of the tree): `list` is an unknown verb — exit 2,
    one `failed to load` line plus argparse's `invalid choice`, nothing on stdout, no traceback — while
    `check <sid>` and `lead-start` run with real arguments exactly as they do normally."""
    bin_ = copy_tree(tmp_path)
    (bin_.parents[1] / "lib" / "pilead" / "verbs" / "list.py").write_text(BROKEN["raises"])
    home = build_fixture(tmp_path / "surface-home")
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(home)}

    def run(*argv):
        return subprocess.run([sys.executable, str(bin_), *argv], capture_output=True, text=True, env=env)

    r = run("list")
    assert r.returncode == 2 and r.stdout == ""
    assert "Traceback" not in r.stderr
    failed = [ln for ln in r.stderr.splitlines() if "failed to load" in ln]
    assert failed == ["pilead: verb module 'pilead.verbs.list' failed to load: RuntimeError: boom at import"]
    assert "invalid choice: 'list'" in r.stderr

    r = run("check", "alpha-120000")
    assert r.returncode == 0 and "Traceback" not in r.stderr
    assert r.stdout.replace(str(home), HOME_TOKEN) == (SNAP / "check-reported.txt").read_text()
    assert r.stderr == ("pilead: verb module 'pilead.verbs.list' failed to load: RuntimeError: boom at import\n"
                        "pilead: no session log found for alpha-120000; tokens and context are unknown\n")

    r = run("lead-start", "lead-2", "--project", "proj")
    assert r.returncode == 0 and r.stdout == f"lead lead-2 ready inbox={home / 'leads' / 'lead-2.inbox.md'}\n"
    assert json.loads((home / "leads" / "lead-2.json").read_text())["project"] == "proj"


def test_a_register_that_adds_a_verb_then_raises_leaves_nothing_behind(tmp_path):
    """A module whose `register` calls verb('zz-half', …) and then raises: one stderr line, --help lists
    every shipped verb and no `zz-half`, and `pilead zz-half` is argparse's `invalid choice` (exit 2)."""
    half = ("ORDER = 45\n\n\ndef register(verb):\n    verb('zz-half', lambda a: print('half'), 'half registered')\n"
            "    raise ValueError('boom after verb')\n")
    bin_ = copy_tree(tmp_path, half=half)
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(tmp_path / "h")}

    def run(*argv):
        return subprocess.run([sys.executable, str(bin_), *argv], capture_output=True, text=True, env=env)

    r = run("--help")
    assert r.returncode == 0
    assert r.stderr.splitlines() == ["pilead: verb module 'pilead.verbs.zz_half' failed to load: ValueError: boom after verb"]
    sub = [ln.split()[0] for ln in r.stdout.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert sub == SHIPPED_VERBS
    assert "zz-half" not in r.stdout

    r = run("zz-half")
    assert r.returncode == 2 and r.stdout == ""
    assert "invalid choice: 'zz-half'" in r.stderr and "Traceback" not in r.stderr


def test_a_register_that_passes_its_trial_then_raises_on_the_real_parser_is_skipped(tmp_path):
    """The second pass in build_parser: `register` returns on its trial run and raises on its second call
    (the real parser) before adding a verb — one stderr line, no traceback, --help lists every shipped verb
    and no `zz-second`, and `pilead zz-second` is argparse's `invalid choice` (exit 2)."""
    second = ("ORDER = 45\n_calls = []\n\n\ndef register(verb):\n    _calls.append(1)\n"
              "    if len(_calls) == 2:\n        raise ValueError('boom on the real parser')\n"
              "    verb('zz-second', lambda a: print('second'), 'trial only')\n")
    bin_ = copy_tree(tmp_path, second=second)
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(tmp_path / "h")}

    def run(*argv):
        return subprocess.run([sys.executable, str(bin_), *argv], capture_output=True, text=True, env=env)

    r = run("--help")
    assert r.returncode == 0
    assert r.stderr.splitlines() == [
        "pilead: verb module 'pilead.verbs.zz_second' failed to load: ValueError: boom on the real parser"]
    sub = [ln.split()[0] for ln in r.stdout.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert sub == SHIPPED_VERBS
    assert "zz-second" not in r.stdout

    r = run("zz-second")
    assert r.returncode == 2 and r.stdout == ""
    assert "invalid choice: 'zz-second'" in r.stderr and "Traceback" not in r.stderr


def test_a_register_that_adds_verbs_then_raises_on_the_real_parser_leaves_nothing_behind(tmp_path):
    """The second pass again, but `register` adds `zz-x` and `zz-y` on the real parser before raising: the
    real parser is rebuilt without the module — one stderr line, neither verb in --help, `pilead zz-x` is
    argparse's `invalid choice`, and the modules before and after it (and the shipped verbs) still run."""
    before = "ORDER = 5\n\n\ndef register(verb):\n    verb('aa-first', lambda a: print('first'), 'before it')\n"
    after = "ORDER = 99\n\n\ndef register(verb):\n    verb('zz-last', lambda a: print('last'), 'after it')\n"
    half = ("ORDER = 45\n_calls = []\n\n\ndef register(verb):\n    _calls.append(1)\n"
            "    verb('zz-x', lambda a: print('x'), 'added, then the module raises')\n"
            "    verb('zz-y', lambda a: print('y'), 'added, then the module raises')\n"
            "    if len(_calls) == 2:\n        raise ValueError('boom after two verbs')\n")
    bin_ = copy_tree(tmp_path, first=before, half=half, last=after)
    home = tmp_path / "h"
    env = {**os.environ, "COLUMNS": "100", "PI_LEAD_HOME": str(home)}
    failed = ["pilead: verb module 'pilead.verbs.zz_half' failed to load: ValueError: boom after two verbs"]

    def run(*argv):
        return subprocess.run([sys.executable, str(bin_), *argv], capture_output=True, text=True, env=env)

    r = run("--help")
    assert r.returncode == 0 and r.stderr.splitlines() == failed
    sub = [ln.split()[0] for ln in r.stdout.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert sub == ["aa-first"] + SHIPPED_VERBS + ["zz-last"]
    assert "zz-x" not in r.stdout and "zz-y" not in r.stdout

    for gone in ("zz-x", "zz-y"):
        r = run(gone)
        assert r.returncode == 2 and r.stdout == ""
        assert f"invalid choice: '{gone}'" in r.stderr and "Traceback" not in r.stderr

    for verb_, out in (("aa-first", "first\n"), ("zz-last", "last\n")):
        r = run(verb_)
        assert (r.returncode, r.stdout, r.stderr.splitlines()) == (0, out, failed)
    r = run("lead-start", "lead-2", "--project", "proj")
    assert r.returncode == 0 and r.stdout == f"lead lead-2 ready inbox={home / 'leads' / 'lead-2.inbox.md'}\n"


# ── list over the surface fixture never asks the terminal ────────────────────────────────────────
@pytest.mark.parametrize("name,argv", [("list", ["list"]), ("list-json", ["list", "--json"])])
def test_list_over_the_fixture_asks_no_terminal(tmp_path, monkeypatch, name, argv):
    """An `osascript` and a `ps` first on PATH that record the call and fail: producing list.txt and
    list-json.txt runs neither (no fixture session has a pid file; the fixture lead has no tab handle)."""
    trap = tmp_path / "trap"
    trap.mkdir()
    hits = tmp_path / "terminal-asked.log"
    for tool in ("osascript", "ps"):
        (trap / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{hits}"\nexit 1\n')
        (trap / tool).chmod(0o755)
    monkeypatch.setenv("PATH", str(trap) + os.pathsep + os.environ["PATH"])
    home = build_fixture(tmp_path / "surface-home")
    rc, out = run_case(argv, home)
    assert rc == 0
    assert not hits.exists(), hits.read_text()
    assert out == (SNAP / f"{name}.txt").read_text()

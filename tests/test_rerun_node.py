"""`pilead verify <sid> --rerun` for `npm test`, `npm run check` and `node --test`, end to end over a generated
worktree. `npm` and `node` are STUBS this file writes into its tmp dir, first on PATH: shell scripts that log
their argument list, working directory and a few environment values, print canned output and exit with a
chosen code. The real npm and the real node are never started here, nor pi, nor a model, nor the network."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import report_verify as rv

from test_cli_core import ROOT
from test_verify_diff_board import SID, home, make_session, run, stage, write_report
from test_verify_rerun import CMD, GREEN, py_env, session  # noqa: F401  (py_env is a fixture)

sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, review  # noqa: E402

BOTH = "....\n808 passed, 1 skipped in 1.00s\nTAP version 13\n# tests 71\n# pass 71\n# fail 0\n# cancelled 0\n"
FLAGS = ("--for-autocommit", "--in-plan", "--diff-reviewed")


def report(cmds="`npm test`", counts="pytest 808 passed, node 71/71"):
    return ("Built it.\n\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: src/a.py\n\n"
            f"## What changed\n- src/a.py: edited\n\nRan {cmds}.\nCounts: {counts}.\n"
            "Changes are staged (not committed).\n")


def write_stub(bindir, name, log, out="", rc=0, extra=""):
    """A `name` stub: logs, runs `extra` (sh), prints `out`, exits `rc`."""
    bindir.mkdir(exist_ok=True)
    outfile = bindir / f"{name}.out"
    outfile.write_text(out)
    body = f"""#!/bin/sh
{{
  printf 'prog: {name}\\n'
  printf 'argv:'; for a in "$@"; do printf ' %s' "$a"; done; printf '\\n'
  printf 'cwd: %s\\n' "$(pwd -P)"
  printf 'PATH: %s\\n' "$PATH"
  printf 'PILEAD_SHIM_DIR: %s\\n' "${{PILEAD_SHIM_DIR-<unset>}}"
  printf 'GIT_CONFIG_GLOBAL: %s\\n' "${{GIT_CONFIG_GLOBAL-<unset>}}"
  printf 'PI_LEAD_SID: %s\\n' "${{PI_LEAD_SID-<unset>}}"
  printf 'PI_SESSION_ID: %s\\n' "${{PI_SESSION_ID-<unset>}}"
}} >> '{log}'
{extra}
cat '{outfile}'
exit {rc}
"""
    p = bindir / name
    p.write_text(body)
    p.chmod(0o755)
    return p


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """A stub dir first on PATH; returns (bindir, log). The stubs themselves are written per test."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return bindir, tmp_path / "stub.log"


def npm_session(tmp_path, text, package_json=True):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/a.py")
    if package_json:
        (wt / "package.json").write_text('{"name": "x", "scripts": {"test": "true", "check": "true"}}\n')
    write_report(sdir, text)
    return wt, sdir


def log_of(log):
    """The stub log as a list of {key: value} dicts, one per stub run."""
    runs = []
    for line in log.read_text().splitlines():
        k, _, v = line.partition(": ")
        if line.startswith("prog: "):
            runs.append({})
        if line == "argv:":
            k, v = "argv", ""
        runs[-1][k.rstrip(":")] = v
    return runs


def _in_process(capsys, *args):
    rc = cli.main(["verify", SID, *args])
    return rc, capsys.readouterr().out


# ── verdicts ─────────────────────────────────────────────────────────────────────────────────
def test_both_counts_match(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERDICT: COUNTS-MATCH" in r.stdout
    lines = r.stdout.splitlines()
    assert "    RE-RAN `npm test` (pytest) → 808 passed, exit 0 (report declares: 808)" in lines
    assert "    RE-RAN `npm test` (node) → 71 passed, exit 0 (report declares: 71)" in lines
    assert "pilead verify: re-running `npm test` (timeout 600s)…" in r.stderr


def test_node_count_differs_is_mismatch(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH.replace("# pass 71", "# pass 70"))
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun")
    assert r.returncode == 1 and "VERDICT: MISMATCH" in r.stdout
    assert "`npm test` (node) re-ran: 70 passed, but the report declares 71 passed" in r.stdout


def test_node_red_is_mismatch(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH.replace("# fail 0", "# fail 2"))
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun")
    assert r.returncode == 1 and "VERDICT: MISMATCH" in r.stdout
    assert "`npm test` (node) re-ran RED: 2 failed/errored" in r.stdout


def test_nonzero_exit_and_no_count_is_inconclusive(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, "npm ERR! missing script: test\n", rc=1)
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun")
    assert r.returncode == rv.EXIT_CODES[rv.INCONCLUSIVE], r.stdout
    assert "VERDICT: INCONCLUSIVE" in r.stdout
    assert "RE-RAN `npm test` (npm) → NO `N passed` — did not run, exit 1" in r.stdout
    assert "`npm test` (npm) was re-run but produced no `N passed` count" in r.stdout


def test_declared_node_count_with_only_a_pytest_summary_is_not_compared(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, "808 passed, 1 skipped in 1.00s\n")
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun")
    assert r.returncode == rv.EXIT_CODES[rv.INCONCLUSIVE], r.stdout
    assert "VERDICT: INCONCLUSIVE" in r.stdout
    assert ("the report declares node: 71 passed, but no re-run produced a node count — that number was "
            "not compared") in r.stdout


def test_a_count_with_nothing_declared_for_its_runner_is_inconclusive(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    npm_session(tmp_path, report(counts="pytest 808 passed"))
    r = run("verify", SID, "--rerun")
    assert r.returncode == rv.EXIT_CODES[rv.INCONCLUSIVE], r.stdout
    assert "`npm test` (node) re-ran: 71 passed — but the report declares no count" in r.stdout
    assert "RE-RAN `npm test` (node) → 71 passed, exit 0 (report declares: nothing declared)" in r.stdout


def test_node_only_project_may_declare_plain_passed(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "node", log, "ℹ tests 12\nℹ pass 12\nℹ fail 0\nℹ cancelled 0\n")
    npm_session(tmp_path, report(cmds="`node --test`", counts="12 passed"), package_json=False)
    r = run("verify", SID, "--rerun")
    assert r.returncode == 0, r.stdout
    assert "RE-RAN `node --test` (node) → 12 passed, exit 0 (report declares: 12)" in r.stdout


# ── what a run gets ──────────────────────────────────────────────────────────────────────────
def test_argv_cwd_and_environment(tmp_path, stubs, monkeypatch):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    write_stub(bindir, "node", log, "# pass 71\n")
    wt, sdir = npm_session(tmp_path, report(cmds="`npm run check` and `node --test`"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "evil.gitconfig"))
    monkeypatch.setenv("PI_LEAD_SID", "someone-else")
    monkeypatch.setenv("PI_SESSION_ID", "caller-session")
    r = run("verify", SID, "--rerun")
    assert "VERDICT: COUNTS-MATCH" in r.stdout, r.stdout + r.stderr
    runs = log_of(log)
    assert [(x["prog"], x["argv"]) for x in runs] == [("npm", "run check"), ("node", "--test")]
    shim_dir = str(sdir / "shim")
    for x in runs:
        assert x["cwd"] == str(wt.resolve())
        assert x["PATH"].split(os.pathsep)[0] == shim_dir
        assert x["PILEAD_SHIM_DIR"] == shim_dir
        assert x["GIT_CONFIG_GLOBAL"] == x["PI_LEAD_SID"] == x["PI_SESSION_ID"] == "<unset>"
    assert (sdir / "shim" / "git").is_file()


def test_git_inside_a_rerun_is_the_shim(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH, extra=f"git commit -m x; echo \"git-rc: $?\" >> '{log}'")
    wt, _ = npm_session(tmp_path, report())  # src/a.py is staged: a real `git commit` would succeed
    before = subprocess.run(["/usr/bin/git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout
    r = run("verify", SID, "--rerun")
    assert "git-rc: 77" in log.read_text(), log.read_text() + r.stdout + r.stderr
    after = subprocess.run(["/usr/bin/git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True,
                           text=True).stdout
    assert before == after
    count = subprocess.run(["/usr/bin/git", "-C", str(wt), "rev-list", "--count", "HEAD"], capture_output=True,
                           text=True).stdout.strip()
    assert count == "1"
    assert "REFS MOVED" not in r.stdout


# ── what is not started ──────────────────────────────────────────────────────────────────────
def test_no_package_json_nothing_started_named_not_rerun(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    npm_session(tmp_path, report(), package_json=False)
    r = run("verify", SID, "--rerun")
    assert not log.exists()
    assert "    NOT RE-RUN: `npm test` — refused, not re-runnable: no package.json in the worktree" in r.stdout
    assert "NOT RE-RUNNABLE — `npm test` is declared but was not executed: no package.json" in r.stdout
    assert "VERDICT: INCONCLUSIVE" in r.stdout and r.returncode == rv.EXIT_CODES[rv.INCONCLUSIVE]


def test_no_package_json_beside_a_pytest_run_follows_the_pytest_row(tmp_path, stubs, py_env):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    session(tmp_path, GREEN + "And `npm test` green.\n", passing=3)
    r = run("verify", SID, "--rerun")
    assert not log.exists()
    assert r.returncode == 0 and "VERDICT: COUNTS-MATCH" in r.stdout, r.stdout
    assert f"RE-RAN `{CMD}` → 3 passed, exit 0 (report declares: 3)" in r.stdout
    assert "NOT RE-RUN: `npm test` — refused, not re-runnable: no package.json in the worktree" in r.stdout


def test_no_npm_on_path_could_not_start(tmp_path, monkeypatch, capsys):
    npm_session(tmp_path, report())
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin")
    assert not any(Path(d, "npm").exists() for d in ("/usr/bin", "/bin"))
    rc, out = _in_process(capsys, "--rerun")
    assert rc == rv.EXIT_CODES[rv.INCONCLUSIVE], out
    assert "COULD NOT START `npm test` (npm) — npm was not found outside the worktree" in out
    assert "VERDICT: INCONCLUSIVE" in out


def test_an_npm_inside_the_worktree_is_not_started(tmp_path, monkeypatch, capsys):
    wt, _ = npm_session(tmp_path, report())
    log = tmp_path / "stub.log"
    write_stub(wt / "bin", "npm", log, BOTH)
    monkeypatch.setenv("PATH", f"{wt / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin")
    rc, out = _in_process(capsys, "--rerun")
    assert not log.exists()
    assert "COULD NOT START `npm test` (npm) — npm was not found outside the worktree" in out
    assert rc == rv.EXIT_CODES[rv.INCONCLUSIVE]


@pytest.mark.parametrize("cmd", ["npm test -- -k x", "node --test tests/x.test.js", "FOO=1 npm test"])
def test_near_misses_are_named_and_never_started(tmp_path, stubs, cmd):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    write_stub(bindir, "node", log, BOTH)
    npm_session(tmp_path, report(cmds=f"`{cmd}`"))
    r = run("verify", SID, "--rerun")
    assert not log.exists()
    assert f"NOT RE-RUN: `{cmd}` — refused, not re-runnable:" in r.stdout
    assert "VERDICT: INCONCLUSIVE" in r.stdout


def test_timeout_kills_the_stub_and_its_child(tmp_path, stubs, monkeypatch, capsys):
    bindir, log = stubs
    pidfile = tmp_path / "child.pid"
    write_stub(bindir, "npm", log, BOTH, extra=f"sleep 30 & echo $! > '{pidfile}'; wait")
    npm_session(tmp_path, report())
    monkeypatch.setattr(review, "RERUN_TIMEOUT", 3)
    rc, out = _in_process(capsys, "--rerun")
    assert rc == rv.EXIT_CODES[rv.INCONCLUSIVE] and "VERDICT: INCONCLUSIVE" in out
    assert "RERUN TIMEOUT — `npm test` (npm) did not finish within 3s" in out
    assert "TIMED OUT `npm test` (npm) after 3s — killed (report declares: nothing declared)" in out
    pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail(f"child sleep {pid} survived the timeout")


def test_popen_gets_argv_no_shell_and_the_found_program(tmp_path, stubs, monkeypatch, capsys):
    bindir, log = stubs
    stub = write_stub(bindir, "npm", log, BOTH)
    npm_session(tmp_path, report())
    real_popen, calls = subprocess.Popen, []

    def spy(argv, *a, **kw):
        calls.append((argv, kw))
        return real_popen(argv, *a, **kw)

    monkeypatch.setattr(review.subprocess, "Popen", spy)
    _in_process(capsys, "--rerun")
    runs = [(a, k) for a, k in calls if a[0] != "git"]
    assert [a for a, _ in runs] == [["npm", "test"]]
    argv, kw = runs[0]
    assert kw["shell"] is False and kw["start_new_session"] is True
    assert kw["executable"] == str(stub) and os.path.isabs(kw["executable"])
    assert kw["env"]["PATH"].startswith(str(home(tmp_path) / "sessions" / SID / "shim") + os.pathsep)


# ── the commit gate ──────────────────────────────────────────────────────────────────────────
def test_autocommit_clears_when_every_row_matches(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun", *FLAGS)
    assert r.returncode == 0 and "AUTO-COMMIT: CLEARED" in r.stdout, r.stdout


def test_autocommit_not_cleared_when_the_node_row_is_red(tmp_path, stubs):
    bindir, log = stubs
    # the report itself declares node 70/71, so the red row is a note and the verdict stays COUNTS-MATCH:
    # condition 1 still refuses, because the re-run did not come back green
    write_stub(bindir, "npm", log, BOTH.replace("# pass 71\n# fail 0", "# pass 70\n# fail 1"), rc=1)
    npm_session(tmp_path, report(counts="pytest 808 passed, node 70/71"))
    r = run("verify", SID, "--rerun", *FLAGS)
    assert "VERDICT: COUNTS-MATCH" in r.stdout, r.stdout
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-rerun-did-not-match" in r.stdout


def test_autocommit_not_cleared_when_the_node_row_is_red_and_undeclared(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH.replace("# fail 0", "# fail 1"), rc=1)
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun", *FLAGS)
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-verdict-is-MISMATCH" in r.stdout


def test_autocommit_not_cleared_when_the_node_count_was_not_compared(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, "808 passed in 1.00s\n")
    npm_session(tmp_path, report())
    r = run("verify", SID, "--rerun", *FLAGS)
    assert r.returncode != 0 and "AUTO-COMMIT: CLEARED" not in r.stdout
    assert "NOT-CLEARED-BECAUSE-verdict-is-INCONCLUSIVE" in r.stdout


def test_without_rerun_nothing_is_started_and_no_shim_is_written(tmp_path, stubs):
    bindir, log = stubs
    write_stub(bindir, "npm", log, BOTH)
    write_stub(bindir, "node", log, BOTH)
    _, sdir = npm_session(tmp_path, report(cmds="`npm test` and `node --test`"))
    r = run("verify", SID)
    r2 = run("verify", SID, *FLAGS)
    assert r.returncode == 0 and "NOT RE-RUN (default" in r.stdout
    assert "AUTO-COMMIT: CLEARED" in r2.stdout
    assert not log.exists()
    assert not (sdir / "shim").exists()


# ── the pytest path gets the shim too ────────────────────────────────────────────────────────
def test_a_pytest_rerun_has_the_shim_first_on_path(tmp_path, py_env):
    wt, sdir = session(tmp_path, GREEN.replace("3 passed", "1 passed"), passing=0)
    (wt / "tests" / "test_git.py").write_text(
        "import subprocess\n"
        "def test_git_commit_is_refused():\n"
        "    r = subprocess.run(['git', 'commit', '-m', 'x'], capture_output=True, text=True)\n"
        "    assert r.returncode == 77, r.stderr\n")
    r = run("verify", SID, "--rerun")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"RE-RAN `{CMD}` → 1 passed, exit 0 (report declares: 1)" in r.stdout
    assert (sdir / "shim" / "git").is_file()

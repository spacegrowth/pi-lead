"""`--rerun`'s list of what may run, and how counts are read and declared per runner — pure, no process is
started. Besides pytest, exactly three whole strings run: `npm test`, `npm run check`, `node --test`."""
import pytest
import report_verify as rv

EXACT = ["npm test", "npm run check", "node --test"]
SHELL_REASON = "shell metacharacter — --rerun never runs anything through a shell"


# ── the list ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", EXACT + ["npm  test", "npm   run  check", "node  --test", " npm test",
                                         "npm run check ", "  node --test  ", "npm\ttest"])
def test_the_three_exact_commands_are_accepted(cmd):
    assert rv.rerun_refusal(cmd) is None
    assert rv.rerunnable(cmd)


NOT_EXACT = ["npm test -- -k x", "npm run check -- -k x", "npm run test", "npm run build", "npm t",
             "npm --prefix x test", "node --test tests/ts/x.test.ts", "node --test --import tsx",
             "node --test=x", "node test", "/usr/local/bin/npm test", "./node_modules/.bin/npm test",
             "FOO=1 npm test", "env npm test", "cd x && npm test", "npx npm test", "yarn test", "pnpm test",
             "NPM TEST"]


@pytest.mark.parametrize("cmd", NOT_EXACT)
def test_every_near_miss_is_refused_with_a_reason(cmd):
    why = rv.rerun_refusal(cmd)
    assert isinstance(why, str) and why
    assert not rv.rerunnable(cmd)
    assert rv.rerun_runner(cmd) is None


@pytest.mark.parametrize("cmd", ["npm test -- -k x", "npm run test", "npm t", "node --test=x", "node test",
                                 "/usr/local/bin/npm test", "./node_modules/.bin/npm test", "npx npm test",
                                 "yarn test", "pnpm test"])
def test_a_js_tool_near_miss_names_the_exact_three(cmd):
    assert rv.rerun_refusal(cmd) == ("only npm test, npm run check and node --test are re-run, exactly as "
                                     "written: no further argument, no option, no path in front")


@pytest.mark.parametrize("cmd", ["FOO=1 npm test", "env npm test", "NPM TEST", "rm -rf /", "make test"])
def test_anything_else_gets_the_general_reason(cmd):
    assert rv.rerun_refusal(cmd) == ("not a command --rerun runs (pytest or python[3] -m pytest; or exactly "
                                     "npm test, npm run check, node --test)")


@pytest.mark.parametrize("cmd", ["npm test; rm -rf ~", "npm test && x", "npm test | sh", "npm test > out",
                                 "npm test `id`", "npm test $(id)", "node --test &", "cd x && npm test"])
def test_a_shell_character_gets_the_shell_reason_even_after_an_exact_command(cmd):
    assert rv.rerun_refusal(cmd) == SHELL_REASON
    assert rv.rerun_runner(cmd) is None


@pytest.mark.parametrize("cmd,runner", [
    ("pytest", "pytest"), ("pytest tests", "pytest"), ("python3 -m pytest tests -q", "pytest"),
    ("python -m pytest", "pytest"), ("/usr/bin/python3 -m pytest tests -q", "pytest"),
    (".venv/bin/python -m pytest", "pytest"),
    ("npm test", "npm"), ("npm run check", "npm"), ("node --test", "node"), (" npm  run check ", "npm")])
def test_rerun_runner(cmd, runner):
    assert rv.rerun_runner(cmd) == runner


def test_declared_commands_keep_the_exact_ones_and_refused_names_the_near_misses():
    text = "Ran `npm test`, `npm run check`, `node --test`, `npm test -- -k x`, `yarn test`, `FOO=1 npm test`."
    assert rv.declared_commands(text) == EXACT
    assert [c for c, _ in rv.refused_commands(text)] == ["npm test -- -k x", "yarn test", "FOO=1 npm test"]


# ── node's counts ─────────────────────────────────────────────────────────────────────────────
TAP = "TAP version 13\nok 1 - a\n1..71\n# tests 71\n# suites 0\n# pass 71\n# fail 0\n# cancelled 0\n# skipped 0\n"
SPEC = "✔ a (1ms)\nℹ tests 71\nℹ suites 0\nℹ pass 71\nℹ fail 0\nℹ cancelled 0\nℹ skipped 0\n"


def test_node_counts_tap():
    assert rv.node_counts(TAP) == (71, 0)


def test_node_counts_spec():
    assert rv.node_counts(SPEC) == (71, 0)


def test_node_counts_the_last_pass_line_wins():
    assert rv.node_counts("# pass 5\n# fail 0\nmore\n# pass 9\n") == (9, 0)


def test_node_counts_fail_plus_cancelled():
    assert rv.node_counts("# pass 3\n# fail 1\n# cancelled 2\n") == (3, 3)


def test_node_counts_no_pass_line_is_unknown():
    assert rv.node_counts("# fail 2\n# cancelled 0\n") == (None, None)
    assert rv.node_counts("") == (None, None)


def test_node_counts_a_line_that_merely_contains_the_text_is_not_counted():
    assert rv.node_counts("not ok 1 - # pass 9 in a name\n") == (None, None)
    assert rv.node_counts("not ok 1 - # pass 9 in a name\n# pass 2\n") == (2, 0)


# ── npm's output: both runners, one, or neither ───────────────────────────────────────────────
PYTEST_OUT = "....\n808 passed, 1 skipped in 12.00s\n"


def test_runner_counts_npm_both():
    assert rv.runner_counts("npm", PYTEST_OUT + TAP) == [("pytest", 808, 0), ("node", 71, 0)]


def test_runner_counts_npm_pytest_only():
    assert rv.runner_counts("npm", PYTEST_OUT) == [("pytest", 808, 0)]


def test_runner_counts_npm_node_only():
    assert rv.runner_counts("npm", SPEC) == [("node", 71, 0)]


def test_runner_counts_npm_neither_is_one_unknown_entry():
    assert rv.runner_counts("npm", "Tests: 5 passing (jest)\n") == [("npm", None, None)]


def test_runner_counts_single_runners():
    assert rv.runner_counts("pytest", PYTEST_OUT) == [("pytest", 808, 0)]
    assert rv.runner_counts("node", TAP) == [("node", 71, 0)]
    assert rv.runner_counts("node", "nothing") == [("node", None, None)]


# ── what a report declares, per runner ────────────────────────────────────────────────────────
def test_declared_for_both():
    assert rv.declared_for("pytest 808 passed / 1 skipped, node 71/71") == {
        "pytest": 808, "node": 71, "node_total": 71}


def test_declared_for_node_before_pytest():
    assert rv.declared_for("node: 71 passed; then the suite: 808 passed") == {
        "pytest": 808, "node": 71, "node_total": None}


def test_declared_for_node_short_of_its_total():
    assert rv.declared_for("node 70/71") == {"pytest": None, "node": 70, "node_total": 71}


def test_declared_for_pytest_alone():
    assert rv.declared_for("3 passed") == {"pytest": 3, "node": None, "node_total": None}


def test_declared_for_nothing():
    assert rv.declared_for("it works") == {"pytest": None, "node": None, "node_total": None}


@pytest.mark.parametrize("text", ["nodes 5 passed", "node_modules 5 passed"])
def test_declared_for_near_words_are_not_node(text):
    assert rv.declared_for(text) == {"pytest": 5, "node": None, "node_total": None}


def test_declared_for_pytest_matches_the_old_rule_without_a_node_declaration():
    text = "Ran it: 12 passed, 1 failed; later 14 passed."
    old = next((c for c, k in rv.declared_counts(text) if k == "passed"), None)
    assert rv.declared_for(text)["pytest"] == old == 12


# ── verdicts over rows (pure) ─────────────────────────────────────────────────────────────────
REPORT = ("Built it.\n\nStatus: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: src/a.py\n\n"
          "## What changed\n- src/a.py: edited\n\nRan `npm test`: pytest 808 passed, node 71/71.\n"
          "Changes are staged (not committed).\n")


def _verify(rerun, text=REPORT, **extra):
    return rv.verify(text, {"staged": ["src/a.py"], "modified": [], "untracked": [], "repo_entries": {"src"},
                            "repo_files": set(), "commits_since": 0, "rerun": rerun, **extra})


def _row(runner, passed, declared, cmd="npm test", failed=0, rc=0):
    return {"cmd": cmd, "runner": runner, "passed": passed, "failed": failed, "declared": declared,
            "returncode": rc, "timed_out": False, "error": None, "timeout": 600}


def test_rows_render_with_their_runner_and_match():
    res = _verify([_row("pytest", 808, 808), _row("node", 71, 71)])
    assert res["verdict"] == rv.COUNTS_MATCH and rv.rerun_matched(res)
    lines = [t for t, _ in rv.render(res, "s", 1)]
    assert "    RE-RAN `npm test` (pytest) → 808 passed, exit 0 (report declares: 808)" in lines
    assert "    RE-RAN `npm test` (node) → 71 passed, exit 0 (report declares: 71)" in lines


def test_node_declared_but_no_node_row_is_not_compared():
    res = _verify([_row("pytest", 808, 808)])
    assert res["verdict"] == rv.INCONCLUSIVE
    texts = [f["text"] for f in res["findings"] if f["code"] == "rerun-declared-not-compared"]
    assert texts == ["the report declares node: 71 passed, but no re-run produced a node count — that "
                     "number was not compared"]


def test_node_short_of_total_counts_as_declared_failures():
    res = _verify([_row("pytest", 808, 808), _row("node", 70, 70, failed=1, rc=1)],
                  REPORT.replace("node 71/71", "node 70/71"))
    assert "rerun-red" not in {f["code"] for f in res["findings"]}
    assert res["verdict"] == rv.COUNTS_MATCH and not rv.rerun_matched(res)


def test_an_unknown_npm_row_never_matches():
    res = _verify([_row("npm", None, None)], REPORT.replace(", node 71/71", ""))
    assert res["verdict"] == rv.INCONCLUSIVE and not rv.rerun_matched(res)
    assert "rerun-unparsable" in {f["code"] for f in res["findings"]}


def test_refused_here_is_named_like_a_refusal_from_the_text():
    res = _verify([], rerun_refused_here=[("npm test", "no package.json in the worktree")])
    assert res["verdict"] == rv.INCONCLUSIVE
    codes = [f["code"] for f in res["findings"]]
    assert "rerun-nothing-to-run" in codes and "rerun-not-rerunnable" in codes
    lines = [t for t, _ in rv.render(res, "s", 1)]
    assert ("    NOT RE-RUN: `npm test` — refused, not re-runnable: no package.json in the worktree"
            in lines)


def test_the_three_kinds_are_named_where_pytest_shaped_was():
    res = _verify([])
    assert any("pytest, or exactly npm test / npm run check / node --test" in f["text"]
               for f in res["findings"] if f["code"] == "rerun-nothing-to-run")
    lines = [t for t, _ in rv.render(res, "s", 1)]
    assert ("    --rerun given, but the report declared no re-runnable (pytest, or exactly npm test / "
            "npm run check / node --test) command") in lines
    quiet = [t for t, _ in rv.render(_verify(None, "nothing to run here"), "s", 1)]
    assert ("    commands declared: (none this tool would re-run — pytest, or exactly npm test / npm run "
            "check / node --test)") in quiet

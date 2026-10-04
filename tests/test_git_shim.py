"""lib/pilead/shim.py — the executor's git shim, exercised against REAL git in throwaway repos (never this
repo). Every shim call goes through `subprocess` with shell=False and the shim first on PATH, exactly how a
python/node/make child of the executor would reach it."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import shim  # noqa: E402

REFUSAL = "pi-lead executor: stage your work and report; the lead commits."


def _real_git():
    """The real git, skipping any pi-lead shim (this suite may itself run under an executor's PATH)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / "git"
        if d.startswith("/") and p.is_file() and os.access(p, os.X_OK):
            with open(p, "rb") as f:
                if shim.MARKER.encode() not in f.read(4096):
                    return str(p)
    pytest.skip("no real git on PATH")


REAL_GIT = _real_git()
CLEAN = set(shim.CFG_VARS) | set(shim.CMD_VARS) | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "PILEAD_SHIM_DIR"}


def _base_env(home):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    return env


@pytest.fixture
def sh(tmp_path):
    """(shim path, env with the shim first on PATH, throwaway repo with one seed commit)."""
    home = tmp_path / "home"
    home.mkdir()
    git = shim.write_git_shim(tmp_path / "session")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _base_env(home)
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "seed"]):
        subprocess.run([REAL_GIT, *args], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "a.txt").write_text("one\n")
    env.update(PILEAD_SHIM_DIR=str(git.parent), PATH=f"{git.parent}{os.pathsep}{env['PATH']}")
    return git, env, repo


def run(env, repo, *args, extra=None):
    """`git <args>` resolved through PATH — i.e. through the shim."""
    return subprocess.run(["git", *args], cwd=repo, env={**env, **(extra or {})}, capture_output=True, text=True)


def real(env, repo, *args):
    return subprocess.run([REAL_GIT, *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout


def snapshot(env, repo):
    """Everything a denied call must leave alone: refs, HEAD, index, worktree status, stash."""
    return (real(env, repo, "for-each-ref"), real(env, repo, "rev-parse", "HEAD"),
            real(env, repo, "symbolic-ref", "HEAD"), real(env, repo, "status", "--porcelain"),
            real(env, repo, "diff", "--cached"), (repo / "a.txt").read_text())


# ── the file itself ──────────────────────────────────────────────────────────────────────────────────
def test_write_git_shim_is_an_executable_posix_sh_script(tmp_path):
    p = shim.write_git_shim(tmp_path / "s")
    assert p == tmp_path / "s" / "shim" / "git"
    assert stat.S_IMODE(p.stat().st_mode) == 0o755
    text = p.read_text()
    assert text.startswith("#!/bin/sh\n") and shim.MARKER in text
    assert "/usr/bin/git" not in text  # the real git is found on PATH, never hardcoded
    for checker in ("sh", "dash"):
        if shutil.which(checker):
            assert subprocess.run([checker, "-n", str(p)], capture_output=True).returncode == 0, checker
    p.write_text("stale")
    assert shim.write_git_shim(tmp_path / "s").read_text() == text  # rewritten per launch


def test_shellcheck_clean(tmp_path):
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    p = shim.write_git_shim(tmp_path / "s")
    r = subprocess.run(["shellcheck", "-s", "sh", "-S", "warning", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


# ── pass-through: the everyday calls behave exactly like git ────────────────────────────────────────
def test_add_status_diff_cached_log(sh):
    _, env, repo = sh
    assert run(env, repo, "status", "--porcelain").stdout == "?? a.txt\n"
    assert run(env, repo, "add", "a.txt").returncode == 0
    assert real(env, repo, "diff", "--cached", "--name-only") == "a.txt\n"
    assert "+one" in run(env, repo, "diff", "--cached").stdout
    log = run(env, repo, "log", "--format=%s")
    assert log.returncode == 0 and log.stdout == "seed\n"


def test_stash_push_and_checkout_b(sh):
    _, env, repo = sh
    real(env, repo, "add", "a.txt")
    assert run(env, repo, "stash", "push", "-m", "wip").returncode == 0
    assert "wip" in real(env, repo, "stash", "list") and not (repo / "a.txt").exists()
    assert run(env, repo, "checkout", "-q", "-b", "x").returncode == 0
    assert real(env, repo, "symbolic-ref", "--short", "HEAD") == "x\n"


def test_apply(sh):
    _, env, repo = sh
    patch = repo.parent / "p.diff"
    patch.write_text("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+hello\n")
    r = run(env, repo, "apply", str(patch))
    assert r.returncode == 0, r.stderr
    assert (repo / "new.txt").read_text() == "hello\n"


def test_version_help_and_bare_git_pass_through(sh):
    _, env, repo = sh
    real_version = real(env, repo, "--version")
    assert run(env, repo, "--version").stdout == real_version
    assert run(env, repo, "-h").stdout.startswith("usage: git")
    bare = run(env, repo)
    assert bare.returncode == 1 and "usage: git" in bare.stdout and REFUSAL not in bare.stderr


def test_argv_reaches_real_git_untouched(tmp_path):
    """Spaces, newlines, globs, empty strings and dashes arrive byte-for-byte (no word splitting, no globbing)."""
    fake = tmp_path / "fakegit"
    fake.mkdir()
    (fake / "git").write_text("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    (fake / "git").chmod(0o755)
    g = shim.write_git_shim(tmp_path / "s")
    env = {**_base_env(tmp_path), "PILEAD_SHIM_DIR": str(g.parent), "PATH": f"{g.parent}:{fake}:/usr/bin:/bin"}
    (tmp_path / "x.ts").write_text("")
    args = ["-C", str(tmp_path), "log", "--format=%H %s", "a b", "*", "*.ts", "", "line1\nline2", "-", "--", "$HOME"]
    r = subprocess.run(["git", *args], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == args


# ── denials: exit 77, one refusal line, and nothing in the repo moved ───────────────────────────────
DENIED = [
    (["commit", "-m", "x"], None),
    (["-C", "{repo}", "commit", "-m", "x"], None),
    (["-c", "user.name=t", "commit", "-m", "x"], None),
    (["push"], None),
    (["branch", "-D", "x"], "moves or deletes a ref"),
    (["reset", "--hard"], None),
    (["tag", "v1"], None),
    (["update-ref", "refs/heads/main", "HEAD"], "not an allowed git subcommand"),
    (["checkout", "-B", "main"], "moves or deletes a ref"),
    (["stash", "clear"], "moves or deletes a ref"),
    (["-c", "alias.st=commit", "st", "-m", "x"], "can run arbitrary commands"),
    (["-c", "core.pager=git commit -am x", "log"], "can run arbitrary commands"),
    (["config", "alias.ci", "commit"], "can run arbitrary commands"),
]


@pytest.mark.parametrize("args,reason", DENIED, ids=[" ".join(a) for a, _ in DENIED])
def test_denied_exit_77_and_repo_untouched(sh, args, reason):
    _, env, repo = sh
    real(env, repo, "branch", "x")
    real(env, repo, "add", "a.txt")
    (repo / "a.txt").write_text("one\ntwo\n")  # an unstaged edit reset --hard / stash would destroy
    before = snapshot(env, repo)
    args = [a.replace("{repo}", str(repo)) for a in args]
    r = run(env, repo, *args)
    assert r.returncode == shim.EXIT_DENIED, (r.stdout, r.stderr)
    assert r.stdout == ""
    line, = r.stderr.splitlines()
    assert line.startswith(f"{REFUSAL} (blocked by git shim: git {args[0]}")
    if reason:
        assert reason in line
    else:
        assert line == f"{REFUSAL} (blocked by git shim: git {' '.join(args)})"
    assert snapshot(env, repo) == before


def test_python_subprocess_commit_is_refused(sh):
    """The whole point: a program the text fence never sees still cannot commit."""
    _, env, repo = sh
    real(env, repo, "add", "a.txt")
    before = snapshot(env, repo)
    r = subprocess.run([sys.executable, "-c", "import subprocess; subprocess.run(['git','commit','-m','t'])"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.stderr == f"{REFUSAL} (blocked by git shim: git commit -m t)\n"
    assert snapshot(env, repo) == before
    assert real(env, repo, "log", "--format=%s") == "seed\n"


def test_sh_and_env_wrappers_reach_the_shim(sh):
    _, env, repo = sh
    for argv in (["/bin/sh", "-c", "git commit -m x"], ["env", "git", "push"], ["/usr/bin/env", "git", "tag", "v9"]):
        r = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True)
        assert r.returncode == shim.EXIT_DENIED and r.stderr.startswith(REFUSAL), argv
    assert real(env, repo, "tag") == ""


def test_env_driven_code_execution(sh):
    _, env, repo = sh
    for extra, var in (({"GIT_PAGER": "git commit -am x"}, "GIT_PAGER"), ({"GIT_EDITOR": "/usr/bin/git push"}, "GIT_EDITOR"),
                       ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "alias.s", "GIT_CONFIG_VALUE_0": "commit"}, "GIT_CONFIG_COUNT"),
                       ({"GIT_CONFIG_PARAMETERS": "'alias.s'='commit'"}, "GIT_CONFIG_PARAMETERS"),
                       ({"GIT_EXEC_PATH": "/tmp"}, "GIT_EXEC_PATH")):
        r = run(env, repo, "status", extra=extra)
        assert r.returncode == shim.EXIT_DENIED and var in r.stderr, (extra, r.stderr)
    # ordinary values in the human's own environment must not stall every git call
    ok = {"EDITOR": "vim", "PAGER": "less -R", "GIT_EDITOR": "true", "GIT_SSH_COMMAND": "ssh -i ~/.ssh/github_key",
          "VISUAL": "code --wait"}
    r = run(env, repo, "status", "--porcelain", extra=ok)
    assert r.returncode == 0 and r.stdout == "?? a.txt\n", r.stderr


# a value names git only when git (or git-*) is the program's basename, never a `…/git/…` directory
GIT_WORD_REFUSED = ["git commit", "/usr/bin/git push", 'sh -c "git commit"', "env git push", "less; git tag v1",
                    "$(git commit)", "/usr/local/git/bin/git", "git-credential-x", "x\ngit commit"]
GIT_WORD_ALLOWED = ["/x/extensions/git/dist/askpass.sh", "/usr/local/git/bin/ssh", "/opt/git/tools/vim",
                    "/opt/git-tools/vim", "ssh -F ~/.ssh/github", "code --wait ~/src/.git/x", "id_git", "gitk"]


@pytest.mark.parametrize("value", GIT_WORD_REFUSED)
def test_command_var_naming_git_as_a_command_is_refused(sh, value):
    _, env, repo = sh
    for var in ("GIT_EDITOR", "GIT_ASKPASS", "GIT_SSH"):
        r = run(env, repo, "status", extra={var: value})
        assert r.returncode == shim.EXIT_DENIED and var in r.stderr, (var, value, r.stderr)


@pytest.mark.parametrize("value", GIT_WORD_ALLOWED)
def test_command_var_with_a_git_directory_in_its_path_passes(sh, value):
    _, env, repo = sh
    for var in ("GIT_ASKPASS", "GIT_SSH", "GIT_EDITOR"):
        r = run(env, repo, "status", "--porcelain", extra={var: value})
        assert (r.returncode, r.stdout) == (0, "?? a.txt\n"), (var, value, r.stderr)


def test_exec_path_option_refused_but_the_query_form_passes(sh):
    _, env, repo = sh
    for args in (["--exec-path=/tmp", "status"], ["-C", str(repo), "--exec-path=", "log"], ["--exec-path=/tmp"]):
        r = run(env, repo, *args)
        assert r.returncode == shim.EXIT_DENIED and "--exec-path" in r.stderr, (args, r.stderr)
    q = run(env, repo, "--exec-path")
    assert q.returncode == 0 and q.stdout.strip() == real(env, repo, "--exec-path").strip(), q.stderr


def test_launch_env_reset_clears_exactly_what_the_shim_refuses(tmp_path):
    """The bootstrap fragment, run under sh with the refused/kept values inherited: refused ones are gone."""
    refused = {**{v: "/x" for v in shim.CFG_VARS}, "GIT_EDITOR": "git commit", "PAGER": "/usr/bin/git log"}
    kept = {"EDITOR": "vim", "GIT_ASKPASS": "/x/extensions/git/dist/askpass.sh", "GIT_SSH": "/usr/local/git/bin/ssh"}
    script = shim.launch_env_reset() + "env"
    r = subprocess.run(["/bin/sh", "-c", script], env={**_base_env(tmp_path), **refused, **kept},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    assert sorted(set(refused) & set(got)) == []
    assert {k: got.get(k) for k in kept} == kept


# ── finding the real git ────────────────────────────────────────────────────────────────────────────
def test_real_git_lookup_skips_self_and_relative_entries(sh):
    git, env, repo = sh
    decoy = repo / "git"  # a repo-local ./git must never run
    decoy.write_text("#!/bin/sh\necho DECOY; exit 3\n")
    decoy.chmod(0o755)
    path = f"{git.parent}/:.:{git.parent}:{env['PATH']}"
    r = run(env, repo, "rev-parse", "--is-inside-work-tree", extra={"PATH": path})
    assert (r.returncode, r.stdout) == (0, "true\n"), r.stderr
    # same file reached through PILEAD_SHIM_DIR unset: -ef against $0 still skips it
    env2 = {k: v for k, v in env.items() if k != "PILEAD_SHIM_DIR"}
    r = subprocess.run([str(git), "rev-parse", "--is-inside-work-tree"], cwd=repo, env=env2, capture_output=True, text=True)
    assert r.stdout == "true\n", r.stderr


def test_no_real_git_exits_127(tmp_path):
    g = shim.write_git_shim(tmp_path / "s")
    env = {**_base_env(tmp_path), "PILEAD_SHIM_DIR": str(g.parent), "PATH": f"{g.parent}:{tmp_path}"}
    r = subprocess.run(["/bin/sh", str(g), "status"], env=env, capture_output=True, text=True)
    assert r.returncode == 127 and "no real git found" in r.stderr


# ── one policy, two layers ──────────────────────────────────────────────────────────────────────────
FENCE = (ROOT / "extensions" / "bash-fence.ts").read_text()


def _ts_const(name):
    m = re.search(rf"const {name}\b[^=]*=\s*(.*?);\n", FENCE, re.S)
    assert m, name
    return m.group(1)


def test_constants_match_bash_fence_ts():
    words = lambda src: " ".join(re.findall(r'"([^"]*)"', src)).split()  # noqa: E731
    assert words(_ts_const("GIT_ALLOW")) == shim.GIT_ALLOW
    assert words(_ts_const("NAMED_DENY")) == shim.NAMED_DENY
    assert words(_ts_const("GIT_OPT_WITH_ARG")) == shim.GIT_OPT_WITH_ARG
    assert re.findall(r'"([^"]*)"', _ts_const("CMD_VARS")) == shim.CMD_VARS
    assert _ts_const("DANGEROUS_KEY").strip() == f"/{shim.DANGEROUS_KEY}/i"
    cfg = re.compile(_ts_const("GIT_CFG_VAR").strip()[1:-1])
    assert [v for v in shim.CFG_VARS if not cfg.match(v)] == ["GIT_CONFIG_COUNT"]  # stands in for KEY_<n>
    assert cfg.match("GIT_CONFIG_KEY_0")
    guard = re.sub(r"//[^\n]*", "", _ts_const("REF_GUARD"))
    guard = re.sub(r"([{,]\s*)([A-Za-z]+)\s*:", r'\1"\2":', guard)
    assert json.loads(re.sub(r",\s*}", "}", guard)) == shim.REF_GUARD


PARITY = [  # argv after `git`; each row's verdict must be the same in both layers
    "status", "add -A", "diff --cached", "log --oneline", "stash push -m wip", "stash", "stash pop", "checkout -b x",
    "checkout -bBugfix", "checkout main", "checkout -- f", "switch -c x", "switch --force main", "branch", "branch new",
    "branch --sort -committerdate", "branch -vv --list", "symbolic-ref HEAD", "symbolic-ref --short -q HEAD",
    "worktree list", "worktree add ../x", "worktree add -b f ../x", "remote -v", "remote add up u", "reset HEAD f",
    "reset -- --hard", "reset --mixed", "config user.name", "config --get alias.ci", "config --get include.path",
    "-C /r status", "-c color.ui=always diff", "--no-pager log -1", "--version", "help commit", "",
    "commit -m x", "push", "merge x", "rebase main", "tag v1", "pull", "cherry-pick a", "revert a", "am p",
    "update-ref refs/heads/main HEAD", "replace a b", "notes add", "filter-branch", "ci", "COMMIT", "init", "clone u",
    "branch -d x", "branch -vD x", "branch --del x", "branch -f main", "branch --sort=refname -D main", "branch -u o/x",
    "checkout -B main", "checkout -fB main", "checkout --orph x", "switch -C main", "switch -Cmain", "switch --orphan x",
    "symbolic-ref HEAD refs/heads/x", "symbolic-ref -m why HEAD refs/heads/x", "symbolic-ref -d HEAD",
    "worktree remove ../x", "worktree prune", "worktree add -B main ../x", "worktree add --orphan -b y ../x",
    "stash clear", "remote set-url o x", "remote -v rename a b", "remote set-head o -d",
    "reset --hard", "reset --har", "reset HEAD --merge", "reset --keep HEAD~", "--git-dir=.git reset --me x",
    "-c alias.x=!sh x", "-c core.pager=less log", "-c core.editor=vi add -e", "--config-env=alias.x=E x",
    "--config-env alias.x=E status", "config alias.ci commit", "config --global core.hooksPath /h",
    "config --add include.path /e", "-C /r -c color.ui=false --git-dir=.git --no-pager commit", "--work-tree x push",
    "--exec-path", "--exec-path=/tmp status", "-C /r --exec-path= log",
]


def test_verdicts_match_the_text_fence(tmp_path):
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules" / "tsx").is_dir():
        pytest.skip("node/tsx unavailable")
    import shlex
    cmds = [f"git {row}".rstrip() for row in PARITY]
    js = ('import { deniedGitVerdict } from "./extensions/bash-fence.ts"; let d = ""; process.stdin.on("data", c => d += c)'
          '.on("end", () => console.log(JSON.stringify(JSON.parse(d).map(c => deniedGitVerdict(c) !== null))));')
    fence = subprocess.run([node, "--import", "tsx", "--input-type=module", "-e", js], cwd=ROOT,
                           input=json.dumps(cmds), capture_output=True, text=True, check=True)
    fence_denied = json.loads(fence.stdout)
    fake = tmp_path / "fakegit"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 0\n")  # pass-through lands here: no repo is touched
    (fake / "git").chmod(0o755)
    g = shim.write_git_shim(tmp_path / "s")
    env = {**_base_env(tmp_path), "PILEAD_SHIM_DIR": str(g.parent), "PATH": f"{g.parent}:{fake}:/usr/bin:/bin"}
    mismatches = []
    for cmd, fd in zip(cmds, fence_denied):
        r = subprocess.run(shlex.split(cmd), env=env, cwd=tmp_path, capture_output=True, text=True)
        if (r.returncode == shim.EXIT_DENIED) != fd:
            mismatches.append(f"{cmd!r}: fence {'denies' if fd else 'allows'}, shim rc={r.returncode} {r.stderr.strip()}")
    assert sum(fence_denied) > 40 and len(cmds) - sum(fence_denied) > 30
    assert mismatches == []


# ── the guarded repository: the policy applies to one repository (and its worktrees) only ──────────
# sha256 of shim.script() at 0fe99fd, before the guard existed: a shim written with no `guarded` must
# stay byte-for-byte that text (update only with a deliberate policy change, alongside bash-fence.ts).
UNGUARDED_SHA256 = "84cf82a61784c64f87e952df4025788f31e31d1b496d45e6937ed5e435c88e3e"


def test_unguarded_shim_bytes_are_unchanged(tmp_path):
    import hashlib
    a = shim.write_git_shim(tmp_path / "a").read_bytes()
    b = shim.write_git_shim(tmp_path / "b", guarded=None).read_bytes()
    assert a == b == shim.script().encode()
    assert hashlib.sha256(a).hexdigest() == UNGUARDED_SHA256


def _init_repo(env, path, seed=True):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([REAL_GIT, "init", "-q", "-b", "main"], cwd=path, env=env, check=True, capture_output=True)
    if seed:
        subprocess.run([REAL_GIT, "commit", "-q", "--allow-empty", "-m", "seed"], cwd=path, env=env, check=True,
                       capture_output=True)
    return path


@pytest.fixture
def gsh(tmp_path):
    """(shim guarding G, env with it first on PATH, G) — G is a throwaway repo with one seed commit and
    an untracked a.txt, like `sh`'s; discovery never climbs above tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    env = _base_env(home)
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path.resolve().parent)
    g = _init_repo(env, tmp_path / "G")
    (g / "a.txt").write_text("one\n")
    git = shim.write_git_shim(tmp_path / "session", guarded=g)
    env.update(PILEAD_SHIM_DIR=str(git.parent), PATH=f"{git.parent}{os.pathsep}{env['PATH']}")
    return git, env, g


def raw_state(repo):
    """G's refs, index and HEAD as bytes, straight from the git dir."""
    gd = repo / ".git"
    files = {str(p.relative_to(gd)): p.read_bytes() for p in sorted(gd.rglob("*"))
             if p.is_file() and (p.name in ("HEAD", "index", "packed-refs", "ORIG_HEAD")
                                 or p.relative_to(gd).parts[0] in ("refs", "worktrees"))}
    return files


def test_guarded_shim_text(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    g = _init_repo(_base_env(home), tmp_path / "G")
    p = shim.write_git_shim(tmp_path / "s", guarded=g)
    text = p.read_text()
    top = os.path.realpath(g)
    assert f"PL_GUARD_TOP='{top}'\n" in text and f"PL_GUARD_COMMON='{top}/.git'\n" in text
    assert text != shim.script() and text.startswith("#!/bin/sh\n") and shim.MARKER in text
    assert stat.S_IMODE(p.stat().st_mode) == 0o755
    for checker in ("sh", "dash", "bash"):
        if shutil.which(checker):
            assert subprocess.run([checker, "-n", str(p)], capture_output=True).returncode == 0, checker
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", "-s", "sh", "-S", "warning", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout


def test_guarded_path_with_a_quote_is_quoted(tmp_path):
    text = shim.script(("/x/it's/.git", "/x/it's"))
    assert "PL_GUARD_TOP='/x/it'\\''s'\n" in text


@pytest.mark.parametrize("what", ["plain dir", "missing", "file"])
def test_unresolvable_guard_writes_the_guard_everything_script(tmp_path, what):
    target = tmp_path / "t"
    if what == "plain dir":
        target.mkdir()
    elif what == "file":
        target.write_text("x")
    env_ceiling = str(tmp_path.resolve().parent)
    old = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = env_ceiling
    try:
        assert shim.write_git_shim(tmp_path / "s", guarded=target).read_text() == shim.script()
    finally:
        if old is None:
            del os.environ["GIT_CEILING_DIRECTORIES"]
        else:
            os.environ["GIT_CEILING_DIRECTORIES"] = old


def test_resolve_guard_ignores_the_callers_git_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    g = _init_repo(_base_env(home), tmp_path / "G")
    o = _init_repo(_base_env(home), tmp_path / "O")
    for var in ("GIT_DIR", "GIT_COMMON_DIR"):
        monkeypatch.setenv(var, str(o / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(o))
    top = os.path.realpath(g)
    assert shim.resolve_guard(g / "") == (f"{top}/.git", top)


@pytest.mark.parametrize("args,reason", DENIED, ids=[" ".join(a) for a, _ in DENIED])
def test_guarded_repo_denied_exit_77_and_untouched(gsh, args, reason):
    """Every DENIED row, in the guarded repository, exactly as the guard-everything shim refuses it."""
    before = raw_state(gsh[2])
    test_denied_exit_77_and_repo_untouched(gsh, args, reason)
    after = raw_state(gsh[2])
    assert set(after) - set(before) <= {"refs/heads/x", "index"}  # the row's own setup: `branch x`, `add`


def test_another_repository_passes(gsh, tmp_path):
    _, env, g = gsh
    real(env, g, "add", "a.txt")
    before = raw_state(g)
    o = tmp_path / "O"
    r = run(env, tmp_path, "init", "-q", "-b", "main", str(o))
    assert r.returncode == 0, r.stderr
    (o / "f.txt").write_text("f\n")
    for args in (["add", "f.txt"], ["commit", "-q", "-m", "x"], ["commit", "-q", "--allow-empty", "-m", "y"],
                 ["tag", "v1"], ["branch", "-f", "side", "HEAD~1"], ["reset", "-q", "--hard", "HEAD~1"]):
        r = run(env, o, *args)
        assert r.returncode == 0, (args, r.stderr)
    assert real(env, o, "log", "--format=%s") == "x\n"
    assert real(env, o, "rev-parse", "v1^{commit}") != real(env, o, "rev-parse", "HEAD")
    assert real(env, o, "rev-parse", "side") == real(env, o, "rev-parse", "HEAD")
    assert raw_state(g) == before
    assert real(env, g, "log", "--format=%s") == "seed\n"


def _refused(r):
    return r.returncode == shim.EXIT_DENIED and r.stderr.startswith(REFUSAL) and r.stdout == ""


def test_init_and_clone_targets(gsh, tmp_path):
    _, env, g = gsh
    before = raw_state(g)
    new = tmp_path / "new" / "deep"
    r = run(env, tmp_path, "init", "-q", str(new))
    assert r.returncode == 0 and (new / ".git" / "HEAD").is_file(), r.stderr
    for cwd, args in ((g, ["init", str(g / "sub")]), (g, ["init", "sub"]), (g, ["init"]), (tmp_path, ["init", str(g)]),
                      (tmp_path, ["init", str(g / ".git")]), (tmp_path, ["-C", str(g), "init"]),
                      (tmp_path, ["init", "-b", "x", str(g / "a" / "b")]), (tmp_path, ["init", str(g / "a" / ".." / "b")]),
                      (tmp_path, ["init", "--separate-git-dir", str(g / ".git"), str(tmp_path / "sep")]),
                      (tmp_path, ["init", f"--separate-git-dir={g / 'x'}", str(tmp_path / "sep")]),
                      (tmp_path, ["--git-dir", str(g / ".git"), "init"]),
                      (tmp_path, ["clone", str(new)]), (g, ["clone", str(new)]),
                      (tmp_path, ["clone", str(new), str(g / "c")]), (tmp_path, ["clone", "-q", str(new), str(g)])):
        r = run(env, cwd, *args)
        assert _refused(r), (args, r.returncode, r.stderr)
        assert "is not an allowed git subcommand" in r.stderr  # today's refusal, word for word
    r = run(env, tmp_path, "init", "-q", extra={"GIT_DIR": str(tmp_path / "elsewhere")})
    assert r.returncode == 0 and (tmp_path / "elsewhere" / "HEAD").is_file(), r.stderr
    r = run(env, g, "init", "-q", extra={"GIT_DIR": str(tmp_path / "elsewhere2")})
    assert _refused(r), r.stderr  # no directory argument: the -C / current directory decides, and that is G
    r = run(env, tmp_path, "init", "-q", str(tmp_path / "x"), extra={"GIT_DIR": str(g / ".git")})
    assert _refused(r), r.stderr
    assert not (g / "sub").exists() and not (g / "c").exists() and not (tmp_path / "sep").exists()
    assert raw_state(g) == before
    src = _init_repo(env, tmp_path / "src")
    r = run(env, g, "clone", "-q", str(src), str(tmp_path / "copy"))
    assert r.returncode == 0 and (tmp_path / "copy" / ".git").is_dir(), r.stderr


def test_linked_worktree_is_the_guarded_repository(gsh, tmp_path):
    _, env, g = gsh
    w = tmp_path / "W"
    real(env, g, "worktree", "add", "-q", "--detach", str(w))
    before = raw_state(g)
    for cwd, args in ((w, ["commit", "--allow-empty", "-m", "x"]), (tmp_path, ["-C", str(w), "tag", "v1"]),
                      (tmp_path, ["init", str(w)]), (tmp_path, ["init", str(w / "sub")])):
        r = run(env, cwd, *args)
        assert _refused(r), (args, r.stderr)
    assert raw_state(g) == before


def test_global_options_pick_the_repository(gsh, tmp_path):
    _, env, g = gsh
    o = _init_repo(env, tmp_path / "O")
    for args in (["-C", str(o), "commit", "-q", "--allow-empty", "-m", "c"],
                 [f"--git-dir={o / '.git'}", "commit", "-q", "--allow-empty", "-m", "d"],
                 ["--git-dir", str(o / ".git"), "tag", "t1"]):
        r = run(env, tmp_path / "home", *args)
        assert r.returncode == 0, (args, r.stderr)
    assert real(env, o, "log", "--format=%s") == "d\nc\nseed\n"
    before = raw_state(g)
    for args in (["-C", str(g), "commit", "--allow-empty", "-m", "x"],
                 [f"--git-dir={g / '.git'}", "commit", "--allow-empty", "-m", "x"],
                 ["-C", str(tmp_path), "-C", "G", "tag", "v1"],
                 ["-C", str(o), "--git-dir", str(g / ".git"), "tag", "v1"]):
        r = run(env, o, *args)
        assert _refused(r), (args, r.stderr)
    r = run(env, o, "commit", "--allow-empty", "-m", "x", extra={"GIT_DIR": str(g / ".git")})
    assert _refused(r), r.stderr
    assert raw_state(g) == before


def test_not_a_repository_gets_the_policy(gsh, tmp_path):
    _, env, _ = gsh
    plain = tmp_path / "plain"
    plain.mkdir()
    r = run(env, plain, "commit", "-m", "x")
    assert _refused(r) and r.stderr == f"{REFUSAL} (blocked by git shim: git commit -m x)\n", r.stderr
    r = run(env, plain, "-C", str(tmp_path / "missing"), "tag", "v1")
    assert _refused(r), r.stderr


def test_symlinked_paths_to_the_guarded_repository(gsh, tmp_path):
    _, env, g = gsh
    o = _init_repo(env, tmp_path / "O")
    link = tmp_path / "link"
    link.symlink_to(g)
    before = raw_state(g)
    for cwd, args in ((o, ["-C", str(link), "commit", "--allow-empty", "-m", "x"]),
                      (link, ["commit", "--allow-empty", "-m", "x"]),
                      (o, ["--git-dir", str(link / ".git"), "tag", "v1"]),
                      (o, ["init", str(link / "sub")])):
        r = run(env, cwd, *args)
        assert _refused(r), (args, r.stderr)
    assert raw_state(g) == before


def test_git_common_dir_refused_and_cleared_at_launch(gsh, tmp_path):
    _, env, g = gsh
    o = _init_repo(env, tmp_path / "O")
    for cwd in (g, o):
        r = run(env, cwd, "status", extra={"GIT_COMMON_DIR": str(g / ".git")})
        assert _refused(r) and "GIT_COMMON_DIR" in r.stderr, r.stderr
    r = subprocess.run(["/bin/sh", "-c", shim.launch_env_reset() + "env"],
                       env={**_base_env(tmp_path), "GIT_COMMON_DIR": "/x"}, capture_output=True, text=True)
    assert r.returncode == 0 and "GIT_COMMON_DIR=" not in r.stdout, r.stderr


def test_guarded_shim_keeps_row_1_everywhere(gsh, tmp_path):
    """Refused environment and risky -c keys are refused in ANY repository, as today."""
    _, env, _ = gsh
    o = _init_repo(env, tmp_path / "O")
    for extra, args in (({"GIT_PAGER": "git commit -am x"}, ["status"]), ({"GIT_EXEC_PATH": "/tmp"}, ["status"]),
                        ({}, ["-c", "alias.st=commit", "st"]), ({}, ["-c", "core.pager=git commit", "log"]),
                        ({}, ["--exec-path=/tmp", "status"]), ({}, ["--config-env=alias.x=E", "x"])):
        r = run(env, o, *args, extra=extra)
        assert _refused(r), (extra, args, r.stderr)
    assert run(env, o, "--version").returncode == 0
    assert real(env, o, "log", "--format=%s") == "seed\n"


def test_guarded_shim_where_the_repository_cannot_be_told_matches_the_text_fence(tmp_path):
    """With a git that answers nothing (so no repository can be worked out), the guarded shim gives the
    guard-everything shim's verdict on every PARITY row — except `init` of a directory outside the
    guarded repository, which is exactly what it now lets through."""
    home = tmp_path / "home"
    home.mkdir()
    g = _init_repo(_base_env(home), tmp_path / "G")
    fake = tmp_path / "fakegit"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 0\n")
    (fake / "git").chmod(0o755)
    import shlex
    verdicts = {}
    for name, guarded in (("all", None), ("one", g)):
        s = shim.write_git_shim(tmp_path / name, guarded=guarded)
        env = {**_base_env(tmp_path), "PILEAD_SHIM_DIR": str(s.parent), "PATH": f"{s.parent}:{fake}:/usr/bin:/bin"}
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        verdicts[name] = [subprocess.run(shlex.split(f"git {row}".rstrip()), env=env, cwd=work, capture_output=True,
                                         text=True).returncode == shim.EXIT_DENIED for row in PARITY]
    differ = [row for row, a, b in zip(PARITY, verdicts["all"], verdicts["one"]) if a != b]
    # p11b r4: `init` outside G is a pass-through call, and a pass-through call runs with the exec-path
    # mirror; a git that answers nothing names no exec-path to mirror, so the call fails closed to G's
    # policy, with a line saying why — no PARITY row differs any more. (With a real git, `init` outside G
    # still passes: test_init_and_clone_targets.)
    assert differ == []
    r = subprocess.run(["git", "init", "-q", str(tmp_path / "fresh")], env=env, cwd=work, capture_output=True,
                       text=True)
    assert r.returncode == shim.EXIT_DENIED and "exec-path mirror" in r.stderr, r.stderr
    assert not (tmp_path / "fresh").exists()


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_guarded_shim_under_other_shells(gsh, tmp_path, shell):
    """The same verdicts with the script run by dash (and bash), not only by macOS's /bin/sh."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    git, env, g = gsh
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\nexec {shutil.which(shell)} "{git}" "$@"\n')
    (wrap / "git").chmod(0o755)
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(git.parent), str(wrap), 1)}
    o = tmp_path / "O"
    w = tmp_path / "W"
    real(env, g, "worktree", "add", "-q", "--detach", str(w))
    before = raw_state(g)
    for cwd, args in ((tmp_path, ["init", "-q", str(o)]), (o, ["commit", "-q", "--allow-empty", "-m", "x"]),
                      (g, ["-C", str(o), "tag", "v1"]), (g, ["status", "--porcelain"])):
        r = run(env, cwd, *args)
        assert r.returncode == 0, (args, r.stderr)
    for cwd, args in ((g, ["commit", "--allow-empty", "-m", "x"]), (w, ["tag", "v1"]), (g, ["init", "sub"]),
                      (o, ["-C", str(g), "branch", "-D", "main"]), (tmp_path, ["clone", str(o)])):
        r = run(env, cwd, *args)
        assert _refused(r), (args, r.stderr)
    assert raw_state(g) == before and real(env, o, "tag") == "v1\n"


# ── a test suite that builds its own repositories runs under the shim ──────────────────────────────
TINY_SUITE = '''
import subprocess

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

def test_builds_its_own_repo(tmp_path):
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "fixture"]):
        r = _git(tmp_path, *args)
        assert r.returncode == 0, r.stderr
    assert _git(tmp_path, "log", "--format=%s").stdout == "fixture\\n"
'''


@pytest.mark.parametrize("guarded", [True, False], ids=["guarding-the-worktree", "guarding-every-repo"])
def test_a_suite_that_builds_repos_runs_through_rerun_env(tmp_path, guarded):
    from pilead import review
    home = tmp_path / "home"
    home.mkdir()
    wt = _init_repo(_base_env(home), tmp_path / "wt")
    (wt / "tests").mkdir()
    (wt / "tests" / "test_tiny.py").write_text(TINY_SUITE)
    sdir = tmp_path / "session"
    git = shim.write_git_shim(sdir, guarded=wt if guarded else None)
    env = review._rerun_env(git.parent)
    env.update({k: v for k, v in _base_env(home).items() if k.startswith(("GIT_", "HOME"))})
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (env.get("PYTHONPATH"), str(Path(pytest.__file__).resolve().parents[1]))))
    for var in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(var, None)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp",
                        str(tmp_path / "bt"), "tests"], cwd=wt, env=env, capture_output=True, text=True)
    if guarded:
        assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr
    else:  # the finding this pins: the guard-everything shim refuses the fixture's `git init`
        assert r.returncode == 1 and "1 failed" in r.stdout and REFUSAL in r.stdout, r.stdout + r.stderr
    assert subprocess.run([REAL_GIT, "rev-list", "--count", "HEAD"], cwd=wt, capture_output=True,
                          text=True).stdout == "1\n"


# ── another repository reaching the guarded one: a borrowed work tree, a push (p11b review, 1–2) ─────
def _tree_files(top):
    """Every file under a work tree (its .git excluded), as bytes."""
    return {str(p.relative_to(top)): p.read_bytes() for p in sorted(top.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(top).parts}


@pytest.fixture
def gwo(gsh, tmp_path):
    """gsh plus W (a linked worktree of G with a staged and an unstaged change), O (another repository
    with two commits) and B (an unrelated bare repository); returns (env, G, W, O, B, snap), where
    snap() is everything a refused call must leave alone in G and W."""
    _, env, g = gsh
    w = tmp_path / "W"
    real(env, g, "worktree", "add", "-q", "--detach", str(w))
    (w / "w.txt").write_text("staged\n")
    real(env, w, "add", "w.txt")
    (w / "w.txt").write_text("staged\nunstaged\n")
    o = _init_repo(env, tmp_path / "O")
    (o / "o.txt").write_text("o\n")
    real(env, o, "add", "o.txt")
    real(env, o, "commit", "-q", "-m", "o")
    b = tmp_path / "B"
    subprocess.run([REAL_GIT, "init", "-q", "--bare", str(b)], env=env, check=True, capture_output=True)

    def snap():
        return (real(env, g, "for-each-ref"), raw_state(g), _tree_files(g), _tree_files(w),
                real(env, w, "status", "--porcelain"))
    return env, g, w, o, b, snap


def _all_refused(env, calls, snap):
    before = snap()
    for cwd, args, extra in calls:
        r = run(env, cwd, *args, extra=extra)
        assert _refused(r), (args, extra, r.returncode, r.stderr)
    assert snap() == before


def test_borrowed_work_tree_gets_the_guarded_policy(gwo, tmp_path):
    """Finding 1: a call from another repository whose work tree is G or W is refused as it is in W."""
    env, g, w, o, _, snap = gwo
    (tmp_path / "fixed").mkdir()
    calls = [(o, [f"--work-tree={w}", "reset", "--hard", "HEAD"], None),
             (o, ["--work-tree", str(w), "reset", "--hard"], None),
             (o, ["--work-tree=../W", "reset", "--hard"], None),
             (o, [f"--work-tree={g}", "reset", "--hard"], None),
             (o, [f"--work-tree={g / 'sub'}", "reset", "--hard"], None),
             (o, [f"--work-tree={w}", "commit", "--allow-empty", "-m", "x"], None),
             (o, [f"--work-tree={w}", "stash", "clear"], None),
             (o, ["reset", "--hard"], {"GIT_WORK_TREE": str(w)}),
             (o, ["reset", "--hard"], {"GIT_WORK_TREE": str(g)}),
             (o, ["-c", f"core.worktree={w}", "reset", "--hard"], None),
             (tmp_path, ["-C", str(o), "-c", "user.name=x", "-c", f"CORE.WorkTree={w}", "reset", "--hard"], None),
             (o, [f"--config-env=core.worktree=PL_WT", "reset", "--hard"], {"PL_WT": str(w)}),
             (o, ["--config-env", "core.worktree=PL_WT", "reset", "--hard"], {"PL_WT": str(w)}),
             (o, ["-c", "core.worktree", "reset", "--hard"], None),
             # the top level git names: --git-dir alone makes the current directory the work tree
             (g, [f"--git-dir={o / '.git'}", "reset", "--hard"], None),
             (w, ["--git-dir", str(o / ".git"), "reset", "--hard"], None),
             (g / ".git" / "..", [f"--git-dir={o / '.git'}", "commit", "--allow-empty", "-m", "x"], None),
             (w, ["reset", "--hard"], {"GIT_DIR": str(o / ".git")}),
             (tmp_path / "fixed", ["-C", str(g), f"--git-dir={o / '.git'}", "reset", "--hard"], None)]
    _all_refused(env, calls, snap)
    assert (w / "w.txt").read_text() == "staged\nunstaged\n" and (g / "a.txt").read_text() == "one\n"


def test_work_tree_from_the_other_repositorys_config(gwo):
    """O's own core.worktree naming W: the top level git names is W, so W's policy applies."""
    env, _, w, o, _, snap = gwo
    real(env, o, "config", "core.worktree", str(w))
    _all_refused(env, [(o, ["reset", "--hard"], None), (o, ["commit", "--allow-empty", "-m", "x"], None)], snap)
    real(env, o, "config", "--unset", "core.worktree")


def test_worktree_add_into_the_guarded_repository(gwo):
    """`worktree add` from O whose new worktree lands in G or W gets G's policy (here: -B / --orphan)."""
    env, g, w, o, _, snap = gwo
    calls = [(o, ["worktree", "add", "-B", "nb", str(g / "nw")], None),
             (o, ["worktree", "add", "-B", "nb", str(w / "nw")], None),
             (o, ["worktree", "add", "--orphan", "-b", "nb", str(g / "nw")], None),
             (o, ["worktree", "add", "-f", "-B", "nb", "--", str(g / "nw")], None)]
    _all_refused(env, calls, snap)
    assert not (g / "nw").exists() and not (w / "nw").exists()


def test_other_repositories_work_trees_still_pass(gwo, tmp_path):
    """O's own operations, a --work-tree of O itself or of an unrelated directory, and a worktree added
    outside G all pass and do what the real git does."""
    env, _, _, o, _, snap = gwo
    before = snap()
    plain = tmp_path / "plain"
    plain.mkdir()
    for cwd, args, extra in ((o, [f"--work-tree={plain}", "reset", "-q", "--hard", "main"], None),
                             (o, ["reset", "-q", "--hard", "HEAD~1"], None),
                             (o, [f"--work-tree={o}", "status"], None),
                             (o, ["-c", f"core.worktree={o}", "tag", "t1"], None),
                             (o, ["reset", "-q", "--hard"], {"GIT_WORK_TREE": str(o)}),
                             (o, ["worktree", "add", "-q", "-B", "side", str(tmp_path / "O2")], None),
                             (plain, ["--git-dir", str(o / ".git"), "tag", "t2"], None),
                             (o, ["commit", "-q", "--allow-empty", "-m", "c"], None)):
        r = run(env, cwd, *args, extra=extra)
        assert r.returncode == 0, (args, r.stderr)
    assert (plain / "o.txt").read_text() == "o\n" and (tmp_path / "O2" / ".git").is_file()
    assert real(env, o, "tag") == "t1\nt2\n" and real(env, o, "log", "--format=%s") == "c\nseed\n"
    assert snap() == before


def test_push_into_the_guarded_repository_is_refused(gwo, tmp_path):
    """Finding 2: every push / send-pack / receive-pack from another repository into G or W is refused,
    whatever refs it names; G's refs stay byte-identical."""
    env, g, w, o, _, snap = gwo
    real(env, g, "branch", "pushed")
    clone = tmp_path / "clone"
    r = run(env, tmp_path, "clone", "-q", str(g), str(clone))
    assert r.returncode == 0, r.stderr
    assert run(env, clone, "commit", "-q", "--allow-empty", "-m", "c").returncode == 0
    real(env, clone, "remote", "add", "viapushurl", "https://example.invalid/x.git")
    real(env, clone, "config", "remote.viapushurl.pushurl", str(w))
    real(env, clone, "config", f"url.{g}.insteadOf", "alias:")
    real(env, clone, "config", f"url.{g}.pushInsteadOf", "palias:")
    real(env, clone, "remote", "add", "pa", "palias:")
    gitfile = tmp_path / "gitfile"
    gitfile.write_text(f"gitdir: {g / '.git'}\n")
    (tmp_path / "Gs.git").symlink_to(g)
    (tmp_path / "home" / "Gh").symlink_to(g)
    calls = [(o, ["push", str(g), "HEAD:refs/heads/x"], None),
             (o, ["push", str(g / ".git"), "HEAD:refs/heads/x"], None),
             (o, ["push", f"file://{g}", "HEAD:refs/heads/x"], None),
             (o, ["push", f"file://{g}/.git", "HEAD:refs/heads/x"], None),
             (o, ["push", "../G", "HEAD:refs/heads/x"], None),
             (o, ["push", str(w), "HEAD:refs/heads/x"], None),
             (o, ["push", "--force", "--repo", str(g), "HEAD:refs/heads/x"], None),
             (o, ["push", f"--repo={g}", "HEAD:refs/heads/x"], None),
             (o, ["push", str(gitfile), "HEAD:refs/heads/x"], None),
             (o, ["push", str(tmp_path / "Gs"), "HEAD:refs/heads/x"], None),
             (o, ["push", "~/Gh", "HEAD:refs/heads/x"], None),
             (o, ["-c", f"remote.cl.url={g}", "push", "cl", "HEAD:refs/heads/x"], None),
             (o, ["send-pack", str(g), "HEAD:refs/heads/x"], None),
             (o, ["receive-pack", str(g)], None),
             (clone, ["push", "origin", "HEAD:refs/heads/master"], None),
             (clone, ["push", "origin", ":refs/heads/pushed"], None),
             (clone, ["push", "--delete", "origin", "pushed"], None),
             (clone, ["push"], None),
             (clone, ["push", "-u", "origin", "main"], None),
             (clone, ["push", "viapushurl", "HEAD:refs/heads/x"], None),
             (clone, ["push", "alias:", "HEAD:refs/heads/x"], None),
             (clone, ["push", "pa", "HEAD:refs/heads/x"], None),
             (clone, ["push", "palias:", "HEAD:refs/heads/x"], None)]
    _all_refused(env, calls, snap)
    assert "pushed" in real(env, g, "branch")


def test_push_destination_that_cannot_be_told_is_refused(gwo):
    """A local-looking destination the shim cannot resolve, or a program choosing where the push goes,
    is refused (fail closed) — even when the destination named is an unrelated bare repository."""
    env, _, _, o, b, snap = gwo
    calls = [(o, ["push", "file://relative/path", "HEAD"], None),
             (o, ["push", "file:///tmp/a%20b", "HEAD"], None),
             (o, ["push", "~nosuchuser/x", "HEAD"], None),
             (o, ["push", "helper::whatever", "HEAD"], None),
             (o, ["push", "s3://bucket/x", "HEAD"], None),
             (o, ["push", "--receive-pack=git-receive-pack", str(b), "HEAD:refs/heads/x"], None),
             (o, ["push", "--exec", "git-receive-pack", str(b), "HEAD:refs/heads/x"], None),
             (o, ["send-pack", "--receive-pack=git-receive-pack", str(b), "HEAD:refs/heads/x"], None),
             (o, ["-c", "remote.rb.url=" + str(b), "-c", "remote.rb.receivepack=git-receive-pack", "push", "rb",
                  "HEAD:refs/heads/x"], None)]
    _all_refused(env, calls, snap)
    for r in (run(env, o, *args) for _, args, _ in calls):
        assert "cannot tell" in r.stderr or "decides where the push goes" in r.stderr, r.stderr
    assert real(env, b, "for-each-ref") == ""


def test_push_elsewhere_still_passes(gwo, tmp_path):
    """A push from O to an unrelated local bare repository (by path, file:// URL, remote name, default
    remote) passes and lands; ssh / scp-form destinations are not refused (git fails on its own)."""
    env, _, _, o, b, snap = gwo
    before = snap()
    real(env, o, "remote", "add", "bare", str(b))
    for args in (["push", "-q", str(b), "HEAD:refs/heads/a"], ["push", "-q", f"file://{b}", "HEAD:refs/heads/b"],
                 ["push", "-q", "bare", "HEAD:refs/heads/c"], ["push", "-q", "-u", "bare", "main"],
                 ["push", "-q"], ["push", "-q", "-o", "opt", "--repo", str(b), "HEAD:refs/heads/d"],
                 ["send-pack", str(b), "HEAD:refs/heads/e"]):
        r = run(env, o, *args, extra={"GIT_SSH_COMMAND": "false"})
        if "-o" in args:  # the bare repository does not take push options: git refuses, the shim did not
            assert r.returncode not in (0, shim.EXIT_DENIED) and REFUSAL not in r.stderr, r.stderr
            continue
        assert r.returncode == 0, (args, r.stderr)
    assert real(env, b, "for-each-ref", "--format=%(refname)") == "".join(
        f"refs/heads/{n}\n" for n in ("a", "b", "c", "e", "main"))
    for dest in ("ssh://nohost.invalid/x.git", "git@nohost.invalid:x.git", "nohost.invalid:x.git"):
        r = run(env, o, "push", dest, "HEAD", extra={"GIT_SSH_COMMAND": "false"})
        assert r.returncode not in (0, shim.EXIT_DENIED) and REFUSAL not in r.stderr, (dest, r.stderr)
    assert snap() == before


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_borrowed_work_tree_and_push_under_other_shells(gwo, tmp_path, shell):
    """The new rows give the same verdicts with the script run by dash (and bash)."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    env, g, w, o, b, snap = gwo
    git = Path(env["PILEAD_SHIM_DIR"]) / "git"
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\nexec {shutil.which(shell)} "{git}" "$@"\n')
    (wrap / "git").chmod(0o755)
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(git.parent), str(wrap), 1)}
    real(env, o, "config", f"url.{g}.pushInsteadOf", "palias:")
    _all_refused(env, [(o, [f"--work-tree={w}", "reset", "--hard"], None),
                       (o, ["reset", "--hard"], {"GIT_WORK_TREE": str(w)}),
                       (o, ["-c", f"core.worktree={w}", "reset", "--hard"], None),
                       (g, [f"--git-dir={o / '.git'}", "reset", "--hard"], None),
                       (o, ["push", str(g), "HEAD:refs/heads/x"], None),
                       (o, ["push", "palias:", "HEAD:refs/heads/x"], None),
                       (o, ["push", "-q", "-o", "x", f"--repo=file://{w}", "HEAD:refs/heads/x"], None)], snap)
    r = run(env, o, "push", "-q", str(b), "HEAD:refs/heads/a")
    assert r.returncode == 0 and real(env, b, "for-each-ref", "--format=%(refname)") == "refs/heads/a\n", r.stderr


# ── a work tree that contains the guarded repository (lead ruling on the p11b fix round) ────────────
def test_git_dir_of_another_repository_run_inside_the_guarded_one_is_refused(gwo, tmp_path):
    """`cd G; git --git-dir=<O>/.git <write>`: git makes the current directory (G) the work tree, so the
    call gets G's policy; the same call from an unrelated sibling directory passes."""
    env, g, w, o, _, snap = gwo
    _all_refused(env, [(g, [f"--git-dir={o / '.git'}", "reset", "--hard"], None),
                       (g, ["--git-dir", str(o / ".git"), "reset", "--hard", "HEAD"], None),
                       (w, [f"--git-dir={o / '.git'}", "commit", "--allow-empty", "-m", "x"], None)], snap)
    assert (g / "a.txt").read_text() == "one\n" and not (g / "o.txt").exists()
    sib = tmp_path / "sib"
    sib.mkdir()
    r = run(env, sib, f"--git-dir={o / '.git'}", "reset", "-q", "--hard")
    assert r.returncode == 0 and (sib / "o.txt").read_text() == "o\n", r.stderr


def test_work_tree_containing_the_guarded_repository_is_refused(gwo, tmp_path):
    """A work tree that is an ANCESTOR of G or of one of G's worktrees gets G's policy (it could clean or
    overwrite them), however the work tree is named; init/clone into such a directory is refused."""
    env, g, w, o, _, snap = gwo
    w2 = tmp_path / "wparent" / "W2"
    real(env, g, "worktree", "add", "-q", "--detach", str(w2))
    (w2 / "keep.txt").write_text("keep\n")
    parent = tmp_path
    calls = [(parent, ["init", "-q", str(parent)], None),
             (o, ["init", "-q", ".."], None),
             (parent, ["init", "-q"], None),
             (tmp_path / "home", ["-C", str(parent), "init", "-q"], None),
             (o, ["--work-tree", str(parent), "init", "-q", str(tmp_path / "x")], None),
             (o, ["clone", "-q", str(o), str(tmp_path / "wparent")], None),
             (o, [f"--git-dir={o / '.git'}", f"--work-tree={parent}", "clean", "-ffdx"], None),
             (o, [f"--git-dir={o / '.git'}", f"--work-tree={parent}", "reset", "--hard"], None),
             (o, [f"--work-tree={parent}", "reset", "--hard"], None),
             (o, ["clean", "-ffdx"], {"GIT_WORK_TREE": str(parent)}),
             (o, ["-c", f"core.worktree={parent}", "clean", "-ffdx"], None),
             (o, [f"--work-tree={tmp_path / 'wparent'}", "reset", "--hard"], None),  # contains W2 only
             (o, ["--work-tree=/", "reset", "--hard"], None),
             (o, ["worktree", "add", "-B", "nb", str(tmp_path / "wparent")], None)]
    _all_refused(env, calls, snap)
    assert (w2 / "keep.txt").read_text() == "keep\n" and not (parent / ".git").exists()
    assert not (tmp_path / "x").exists()


def test_repository_whose_top_level_contains_the_guarded_one(gwo, tmp_path):
    """A scratch "home" repository with G, W and O nested under it: its own calls get G's policy; O's
    calls, and calls in a directory outside the home repository, still pass."""
    env, g, w, o, _, snap = gwo
    home_repo = tmp_path
    subprocess.run([REAL_GIT, "init", "-q", str(home_repo)], env=env, check=True, capture_output=True)
    (home_repo / "sub").mkdir()
    _all_refused(env, [(home_repo, ["clean", "-ffdx"], None), (home_repo / "sub", ["clean", "-ffdx"], None),
                       (home_repo, ["reset", "--hard"], None), (home_repo / "sub", ["-C", "..", "clean", "-ffdx"], None),
                       (home_repo, ["commit", "--allow-empty", "-m", "x"], None),
                       (o, ["-C", str(home_repo), "clean", "-ffdx"], None)], snap)
    assert run(env, home_repo, "status", "--porcelain").returncode == 0  # G's policy allows status
    assert real(env, home_repo, "rev-list", "--all") == ""
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "still-passes")
    assert r.returncode == 0, r.stderr
    outside = Path(os.path.realpath(tmp_path.parent)) / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        r = run(env, outside, "init", "-q", "fresh")
        assert r.returncode == 0 and (outside / "fresh" / ".git").is_dir(), r.stderr
        r = run(env, outside / "fresh", "commit", "-q", "--allow-empty", "-m", "c")
        assert r.returncode == 0, r.stderr
    finally:
        shutil.rmtree(outside)


def test_unrelated_sibling_work_trees_still_pass(gwo, tmp_path):
    """Siblings of G — a new repository, a --work-tree / $GIT_WORK_TREE next to G — are not containers."""
    env, _, _, o, _, snap = gwo
    before = snap()
    sib = tmp_path / "sib"
    sib.mkdir()
    for cwd, args, extra in ((sib, ["init", "-q", "new"], None), (sib / "new", ["commit", "-q", "--allow-empty", "-m", "n"], None),
                             (o, [f"--work-tree={sib}", "reset", "-q", "--hard"], None),
                             (o, ["reset", "-q", "--hard"], {"GIT_WORK_TREE": str(sib)}),
                             (o, ["clean", "-q", "-fdx"], {"GIT_WORK_TREE": str(sib / "new")})):
        r = run(env, cwd, *args, extra=extra)
        assert r.returncode == 0, (args, r.stderr)
    assert (sib / "o.txt").read_text() == "o\n"
    assert snap() == before


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_containing_work_tree_under_other_shells(gwo, tmp_path, shell):
    """The containment rule gives the same verdicts with the script run by dash (and bash)."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    env, g, _, o, _, snap = gwo
    git = Path(env["PILEAD_SHIM_DIR"]) / "git"
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\nexec {shutil.which(shell)} "{git}" "$@"\n')
    (wrap / "git").chmod(0o755)
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(git.parent), str(wrap), 1)}
    _all_refused(env, [(o, [f"--work-tree={tmp_path}", "clean", "-ffdx"], None),
                       (tmp_path, ["init", "-q"], None),
                       (g, [f"--git-dir={o / '.git'}", "reset", "--hard"], None)], snap)
    sib = tmp_path / "sib"
    sib.mkdir()
    r = run(env, o, f"--work-tree={sib}", "reset", "-q", "--hard")
    assert r.returncode == 0 and (sib / "o.txt").is_file(), r.stderr


# ── git's path-taking environment variables, --shallow-file / --attr-source, config --file (p11b r2) ─
def _deep_state(env, g, w):
    """Everything a refused call must leave alone in G and W, down to the bytes: fsck clean, the index
    and config files as bytes, `ls-files -s`, refs, the shallow file, the global config, the work trees."""
    gd = g / ".git"
    fsck = subprocess.run([REAL_GIT, "fsck", "--full"], cwd=g, env=env, capture_output=True, text=True)
    assert fsck.returncode == 0, fsck.stdout + fsck.stderr
    raw = {str(p.relative_to(gd)): p.read_bytes() for p in sorted(gd.rglob("*"))
           if p.is_file() and p.name in ("index", "config", "config.worktree", "shallow")}
    home_cfg = Path(env["HOME"]) / ".gitconfig"
    return (fsck.stdout, raw, real(env, g, "ls-files", "-s"), real(env, w, "ls-files", "-s"),
            real(env, g, "for-each-ref"), (gd / "shallow").exists(),
            home_cfg.read_bytes() if home_cfg.exists() else None, _tree_files(g), _tree_files(w))


@pytest.fixture
def gpv(gwo):
    """gwo with a staged blob in G too (a.txt) — unreachable from any ref, so a prune in G's object
    store deletes it; returns (env, G, W, O, blobs, state) where state() is _deep_state of G and W."""
    env, g, w, o, _, _ = gwo
    real(env, g, "add", "a.txt")
    blobs = [real(env, g, "rev-parse", ":a.txt").strip(), real(env, w, "rev-parse", ":w.txt").strip()]
    return env, g, w, o, blobs, (lambda: _deep_state(env, g, w))


def _refused_and_untouched(env, calls, state, g=None, blobs=()):
    before = state()
    for cwd, args, extra in calls:
        r = run(env, cwd, *args, extra=extra)
        assert _refused(r), (args, extra, r.returncode, r.stderr)
    assert state() == before
    for b in blobs:
        assert subprocess.run([REAL_GIT, "cat-file", "-e", b], cwd=g, env=env).returncode == 0, b


def test_object_directory_in_the_guarded_repository_is_refused(gpv, tmp_path):
    """Change 1: GIT_OBJECT_DIRECTORY, or any GIT_ALTERNATE_OBJECT_DIRECTORIES entry, landing in G makes a
    call from O get G's policy — `gc --prune=now` / `prune` there would delete G's and W's staged blobs."""
    env, g, w, o, blobs, state = gpv
    objs = str(g / ".git" / "objects")
    (tmp_path / "elsewhere").mkdir()
    calls = [(o, ["gc", "--prune=now"], {"GIT_OBJECT_DIRECTORY": objs}),
             (o, ["prune", "--expire=now"], {"GIT_OBJECT_DIRECTORY": objs}),
             (o, ["prune", "--expire=now"], {"GIT_OBJECT_DIRECTORY": "../G/.git/objects"}),
             (o / ".git", ["prune", "--expire=now"], {"GIT_OBJECT_DIRECTORY": "../../G/.git/objects"}),
             (o, ["repack", "-a", "-d"], {"GIT_OBJECT_DIRECTORY": objs + "/"}),
             (o, ["gc", "--prune=now"], {"GIT_ALTERNATE_OBJECT_DIRECTORIES": objs}),
             (o, ["gc", "--prune=now"],
              {"GIT_ALTERNATE_OBJECT_DIRECTORIES": f"{tmp_path / 'elsewhere'}::{objs}"}),
             (o, ["gc", "--prune=now"], {"GIT_ALTERNATE_OBJECT_DIRECTORIES": f'"{tmp_path / "elsewhere"}"'}),
             (o, ["init", "-q", str(tmp_path / "new")], {"GIT_OBJECT_DIRECTORY": objs}),
             (o, ["clone", "-q", str(o), str(tmp_path / "new")], {"GIT_OBJECT_DIRECTORY": objs}),
             # a bogus object directory must not make the guard's own probes fail open
             (o, [f"--work-tree={w}", "reset", "--hard"], {"GIT_OBJECT_DIRECTORY": str(tmp_path / "nope")})]
    _refused_and_untouched(env, calls, state, g, blobs)
    assert not (tmp_path / "new").exists()


def test_index_file_in_the_guarded_repository_is_refused(gpv, tmp_path):
    """Change 2: GIT_INDEX_FILE landing in G (its index, a worktree's) makes a call from O get G's policy —
    `read-tree --empty` would empty it, `clone` would overwrite it."""
    env, g, w, o, blobs, state = gpv
    idx, widx = str(g / ".git" / "index"), str(g / ".git" / "worktrees" / "W" / "index")
    (o / "sub").mkdir()
    calls = [(o, ["read-tree", "--empty"], {"GIT_INDEX_FILE": idx}),
             (o, ["read-tree", "--empty"], {"GIT_INDEX_FILE": widx}),
             (o, ["read-tree", "--empty"], {"GIT_INDEX_FILE": "../G/.git/index"}),
             (o, ["-C", "..", "-C", "O", "read-tree", "--empty"], {"GIT_INDEX_FILE": "../G/.git/index"}),
             (o / "sub", ["read-tree", "--empty"], {"GIT_INDEX_FILE": "../G/.git/index"}),  # from O's top
             (o, ["update-index", "--force-remove", "a.txt"], {"GIT_INDEX_FILE": idx}),
             (o, ["clone", "-q", str(o), str(tmp_path / "new")], {"GIT_INDEX_FILE": idx}),
             (o, ["init", "-q", str(tmp_path / "new")], {"GIT_INDEX_FILE": "relative-index"})]
    _refused_and_untouched(env, calls, state, g, blobs)
    assert not (tmp_path / "new").exists()


def test_config_file_of_the_guarded_repository_is_refused(gpv, tmp_path):
    """Change 3: `config --file / -f` naming G's config (or a worktree's config.worktree), in any spelling
    git accepts, and `--global` / `--system` / the global file by path (G reads them too) get G's
    policy from O: a key G's policy refuses is refused; a read passes, as it does in W."""
    env, g, w, o, blobs, state = gpv
    cfg, wcfg = str(g / ".git" / "config"), str(g / ".git" / "worktrees" / "W" / "config.worktree")
    home_cfg = str(Path(env["HOME"]) / ".gitconfig")
    key = ["core.fsmonitor", "/bin/echo"]
    calls = [(o, ["config", "--file", cfg, *key], None), (o, ["config", "-f", cfg, *key], None),
             (o, ["config", f"--file={cfg}", *key], None), (o, ["config", f"-f{cfg}", *key], None),
             (o, ["config", "--fil", cfg, *key], None), (o, ["config", "-zf", cfg, *key], None),
             (o, ["config", "--file", "../G/.git/config", *key], None),
             (o, ["config", "set", "--file", cfg, *key], None),
             (o, ["config", "--file", wcfg, *key], None),
             (o, ["config", "--file", cfg, "alias.x", "!true"], None),
             (o, ["config", "--global", "core.pager", "less"], None),
             (o, ["config", "--gl", "core.pager", "less"], None),
             (o, ["config", "set", "--global", "core.hooksPath", str(tmp_path)], None),
             (o, ["config", "--system", "core.pager", "less"], None),
             (o, ["config", "--file", home_cfg, "core.pager", "less"], None),
             (o, ["config", "--file", "../home/.gitconfig", "core.pager", "less"], None)]
    _refused_and_untouched(env, calls, state, g, blobs)
    assert not Path(wcfg).exists()
    r = run(env, o, "config", "--file", cfg, "--get", "core.bare")  # a read: G's policy allows it
    assert r.returncode == 0 and r.stdout == "false\n", r.stderr


def test_shallow_file_and_global_options_that_take_a_word(gpv, tmp_path):
    """Sweep: GIT_SHALLOW_FILE / --shallow-file / GIT_GRAFT_FILE landing in G get G's policy;
    `--attr-source` and `--shallow-file` take the NEXT word, so `git --attr-source HEAD -C <G> reset --hard` is G's call (it
    was passed as O's and emptied G's staged work); an option the shim does not know gets the policy."""
    env, g, w, o, blobs, state = gpv
    shallow = str(g / ".git" / "shallow")
    gcl = tmp_path / "gclone"
    real(env, tmp_path, "clone", "-q", str(g), str(gcl))
    calls = [(o, ["prune", "--expire=now"], {"GIT_SHALLOW_FILE": shallow}),
             (o, ["--shallow-file", shallow, "prune", "--expire=now"], None),
             # --convert-graft-file deletes the graft file, and from a clone of G, G's loose ref parses as one
             (gcl, ["replace", "--convert-graft-file"], {"GIT_GRAFT_FILE": str(g / ".git" / "refs" / "heads" / "main")}),
             (o, ["--attr-source", "HEAD", "-C", str(g), "reset", "--hard"], None),
             (o, ["--attr-source", "HEAD", "-C", str(w), "stash", "clear"], None),
             (o, ["--shallow-file", str(tmp_path / "x"), "-C", str(g), "reset", "--hard"], None),
             (o, ["--attr-source", "HEAD", f"--git-dir={o / '.git'}", f"--work-tree={w}", "reset", "--hard"],
              None),
             (o, ["--no-such-option", "-C", str(g), "reset", "--hard"], None),
             (o, ["--list-cmds", "main", "-C", str(g), "reset", "--hard"], None)]
    _refused_and_untouched(env, calls, state, g, blobs)
    assert (g / "a.txt").read_text() == "one\n" and (w / "w.txt").read_text() == "staged\nunstaged\n"
    assert (g / ".git" / "refs" / "heads" / "main").is_file()


def test_own_index_objects_and_config_file_still_pass(gpv, tmp_path):
    """O using its own GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY / alternates / config --file in a directory
    unrelated to G, --global reads and harmless writes, and the new global options on O, all pass."""
    env, _, _, o, _, state = gpv
    before = state()
    d = tmp_path / "scratch"
    d.mkdir()
    real(env, o, "clone", "-q", "--bare", str(o), str(d / "mirror.git"))
    (d / "objs").mkdir()
    (d / "grafts").write_text("")
    for args, extra in ((["read-tree", "HEAD"], {"GIT_INDEX_FILE": str(d / "idx")}),
                        (["read-tree", "HEAD"], {"GIT_INDEX_FILE": "rel-index"}),
                        (["update-index", "--add", "o.txt"], {"GIT_INDEX_FILE": str(d / "idx")}),
                        (["hash-object", "-w", "o.txt"], {"GIT_OBJECT_DIRECTORY": str(d / "objs")}),
                        (["gc", "-q", "--prune=now"],
                         {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(d / "mirror.git" / "objects")}),
                        (["init", "-q", str(d / "fresh")], {"GIT_OBJECT_DIRECTORY": str(d / "objs")}),
                        (["config", "--file", str(d / "cfg"), "core.fsmonitor", "/bin/echo"], None),
                        (["config", "-f", str(d / "cfg"), "user.name", "x"], None),
                        (["config", "set", "--file=" + str(d / "cfg"), "alias.x", "!true"], None),
                        (["config", "--global", "user.name", "x"], None),
                        (["config", "--global", "--get", "user.name"], None),
                        (["--attr-source", "HEAD", "status"], None),
                        (["--shallow-file", str(d / "sh"), "log", "-1"], None),
                        (["prune", "--expire=now"], {"GIT_SHALLOW_FILE": str(d / "sh")}),
                        (["replace", "--convert-graft-file"], {"GIT_GRAFT_FILE": str(d / "grafts")})):
        r = run(env, o, *args, extra=extra)
        assert r.returncode == 0, (args, extra, r.stderr)
    assert (d / "idx").is_file() and (o / "rel-index").is_file() and (d / "fresh" / ".git").is_dir()
    assert any((d / "objs").iterdir())
    assert not (d / "grafts").exists()  # what --convert-graft-file does to a graft file outside G
    assert real(env, o, "config", "--file", str(d / "cfg"), "core.fsmonitor") == "/bin/echo\n"
    assert (Path(env["HOME"]) / ".gitconfig").read_text() == "[user]\n\tname = x\n"
    (Path(env["HOME"]) / ".gitconfig").unlink()
    assert state() == before


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_path_variables_and_config_file_under_other_shells(gpv, tmp_path, shell):
    """The new rows give the same verdicts with the script run by dash (and bash)."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    env, g, w, o, blobs, state = gpv
    git = Path(env["PILEAD_SHIM_DIR"]) / "git"
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\nexec {shutil.which(shell)} "{git}" "$@"\n')
    (wrap / "git").chmod(0o755)
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(git.parent), str(wrap), 1)}
    objs = str(g / ".git" / "objects")
    _refused_and_untouched(env, [
        (o, ["gc", "--prune=now"], {"GIT_OBJECT_DIRECTORY": objs}),
        (o, ["gc", "--prune=now"], {"GIT_ALTERNATE_OBJECT_DIRECTORIES": f"/nonexistent-x:{objs}"}),
        (o, ["read-tree", "--empty"], {"GIT_INDEX_FILE": str(g / ".git" / "worktrees" / "W" / "index")}),
        (o, ["config", f"-f{g / '.git' / 'config'}", "core.fsmonitor", "/bin/echo"], None),
        (o, ["config", "--global", "core.pager", "less"], None),
        (o, ["--attr-source", "HEAD", "-C", str(g), "reset", "--hard"], None)], state, g, blobs)
    r = run(env, o, "read-tree", "HEAD", extra={"GIT_INDEX_FILE": str(tmp_path / "idx")})
    assert r.returncode == 0 and (tmp_path / "idx").is_file(), r.stderr


# ── a subcommand that is not a git builtin: an alias, an external git-<name> (p11b r3) ────────────────
def _aliases(env, o, g, w):
    """O's config gets one alias per way the third review reached G; returns the alias names."""
    als = {"p": f"push {g} HEAD:refs/heads/x",
           "cf": f"config --file {g / '.git' / 'config'} core.fsmonitor /bin/echo",
           "wa": f"worktree add {g / 'sub'}",
           "sp": f"send-pack {g / '.git'} HEAD:refs/heads/x",
           "sh": f"!git -C {g} read-tree --empty",
           "shw": f"!git -C {w} read-tree --empty",
           "st": "status"}  # an alias of an allowlisted builtin: it gets the policy too, not expanded
    for name, value in als.items():
        real(env, o, "config", f"alias.{name}", value)
    return list(als)


def _zap(tmp_path, g):
    """A directory holding `git-zap` (runs `git -C <G> read-tree --empty`), to put on PATH after the shim."""
    d = tmp_path / "ext"
    d.mkdir()
    (d / "git-zap").write_text(f'#!/bin/sh\nexec git -C "{g}" read-tree --empty\n')
    (d / "git-zap").chmod(0o755)
    return d


def test_alias_or_external_subcommand_from_another_repository_gets_the_policy(gpv, tmp_path):
    """Findings 1–2: a subcommand that is not a git builtin — O's alias (plain, `!shell`, or of a builtin),
    one given by `-c alias.x=…`, or an external `git-zap` on PATH — gets G's policy from O, which refuses
    it, so G's refs, index, config and work tree are unchanged."""
    env, g, w, o, blobs, state = gpv
    names = _aliases(env, o, g, w)
    env = {**env, "PATH": env["PATH"] + os.pathsep + str(_zap(tmp_path, g))}
    calls = [(o, [n], None) for n in names]
    calls += [(o, ["-c", f"alias.x=!git -C {g} read-tree --empty", "x"], None),
              (o, ["-c", f"alias.y=push {g} HEAD:refs/heads/x", "y"], None),
              (o, ["zap"], None), (tmp_path, ["-C", str(o), "zap"], None), (o, ["-C", ".", "zap"], None),
              (o, [f"--git-dir={o / '.git'}", "sh"], None), (o, ["--no-pager", "p"], None),
              (o, ["P"], None)]  # not a builtin either (git's builtin names are case-sensitive)
    _refused_and_untouched(env, calls, state, g, blobs)
    for n in ("p", "sh", "zap"):
        assert f'"{n}" is not an allowed git subcommand' in run(env, o, n).stderr
    assert not (g / "sub").exists() and "x" not in real(env, g, "branch", "--list", "x")
    # the control: the same kind of alias run by the real git DOES reach the repository it names
    probe = tmp_path / "Ocopy"
    real(env, tmp_path, "clone", "-q", str(o), str(probe))
    real(env, o, "config", "alias.shc", f"!git -C {probe} read-tree --empty")
    assert real(env, probe, "ls-files") != ""
    subprocess.run([REAL_GIT, "shc"], cwd=o, env=env, check=True, capture_output=True)
    assert real(env, probe, "ls-files") == ""


def test_builtins_in_another_repository_still_pass(gwo, tmp_path):
    """O's own builtins pass even with aliases of the same names in O's config (git runs the builtin: an
    alias cannot override one): commit, tag, branch -D, rebase, and a push to an unrelated bare repository,
    which lands. `-c alias.push=…` itself is refused by row 1 (-c alias.* anywhere), as before."""
    env, g, w, o, b, snap = gwo
    before = snap()
    for name in ("push", "commit", "tag", "branch", "rebase", "status"):
        real(env, o, "config", f"alias.{name}", f"!git -C {g} read-tree --empty")
    real(env, o, "branch", "doomed")
    for args in (["commit", "-q", "--allow-empty", "-m", "c1"], ["tag", "v1"], ["branch", "-D", "doomed"],
                 ["commit", "-q", "--allow-empty", "-m", "c2"], ["rebase", "-q", "HEAD~2", "--onto", "HEAD~3"],
                 ["push", "-q", str(b), "HEAD:refs/heads/pushed"], ["status", "--short"]):
        r = run(env, o, *args)
        assert r.returncode == 0, (args, r.stderr)
    assert real(env, o, "log", "--format=%s") == "c2\nc1\nseed\n"  # the rebase dropped "o"
    assert real(env, o, "tag") == "v1\n" and "doomed" not in real(env, o, "branch")
    assert real(env, b, "for-each-ref", "--format=%(refname)") == "refs/heads/pushed\n"
    r = run(env, o, "-c", "alias.push=status", "push", "-q", str(b), "HEAD:refs/heads/p2")
    assert _refused(r) and "can run arbitrary commands" in r.stderr, r.stderr
    assert real(env, b, "for-each-ref", "--format=%(refname)") == "refs/heads/pushed\n"
    assert snap() == before


HARMLESS_ALIAS_SUITE = '''
import subprocess

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

def test_uses_a_harmless_alias(tmp_path):
    for args in (["init", "-q", "-b", "main"], ["config", "alias.co", "checkout"],
                 ["commit", "-q", "--allow-empty", "-m", "fixture"]):
        r = _git(tmp_path, *args)
        assert r.returncode == 0, r.stderr
    r = _git(tmp_path, "co", "-q", "-b", "side")
    print("ALIAS-RC", r.returncode, r.stderr.strip())
    assert r.returncode == 0, r.stderr
'''


def test_a_suite_using_a_harmless_alias_in_its_own_repo_is_refused(tmp_path):
    """What the change costs: a test suite that sets `alias.co = checkout` in its own temp repository and
    runs `git co` gets the policy — a REFUSAL (exit 77, "co" is not an allowed git subcommand), so that test
    fails under the guarded shim; the same suite passes when the alias is spelled out as `checkout`. The
    same goes for git's own non-builtin commands run in another repository (`submodule` is a script in
    git 2.50), and for any external `git-<name>` (git-lfs, …)."""
    from pilead import review
    home = tmp_path / "home"
    home.mkdir()
    wt = _init_repo(_base_env(home), tmp_path / "wt")
    (wt / "tests").mkdir()
    (wt / "tests" / "test_alias.py").write_text(HARMLESS_ALIAS_SUITE)
    git = shim.write_git_shim(tmp_path / "session", guarded=wt)
    env = review._rerun_env(git.parent)
    env.update({k: v for k, v in _base_env(home).items() if k.startswith(("GIT_", "HOME"))})
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (env.get("PYTHONPATH"), str(Path(pytest.__file__).resolve().parents[1]))))
    for var in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(var, None)
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
    r = subprocess.run([*args, "--basetemp", str(tmp_path / "bt")], cwd=wt, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "1 failed" in r.stdout, r.stdout + r.stderr
    assert f"ALIAS-RC {shim.EXIT_DENIED} {REFUSAL}" in r.stdout and '"co" is not an allowed' in r.stdout
    (wt / "tests" / "test_alias.py").write_text(HARMLESS_ALIAS_SUITE.replace('"co", "-q"', '"checkout", "-q"'))
    r = subprocess.run([*args, "--basetemp", str(tmp_path / "bt2")], cwd=wt, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr
    o = _init_repo(env, tmp_path / "O")
    r = run(env, o, "submodule", "status")
    assert _refused(r) and '"submodule" is not an allowed' in r.stderr, r.stderr


def test_the_builtin_probe_runs_no_program(gwo, tmp_path):
    """The guard's new probe (`--list-cmds=builtins`) fires no fsmonitor and no hook: a pass-through call
    from O fires O's core.fsmonitor and pre-commit hook exactly as often as the real git does, and a
    refused alias fires neither."""
    env, _, _, o, _, _ = gwo
    count = tmp_path / "fired"
    hook = tmp_path / "fsm"
    hook.write_text(f'#!/bin/sh\necho fsmonitor >> "{count}"\nexit 1\n')
    hook.chmod(0o755)
    real(env, o, "config", "core.fsmonitor", str(hook))
    pre = o / ".git" / "hooks" / "pre-commit"
    pre.write_text(f'#!/bin/sh\necho hook >> "{count}"\n')
    pre.chmod(0o755)
    real(env, o, "config", "alias.st", "status")

    def fired(argv):
        count.write_text("")
        subprocess.run(argv, cwd=o, env=env, capture_output=True)
        return count.read_text().split()
    for args in (["status"], ["commit", "-q", "--allow-empty", "-m", "f"]):
        via_real, via_shim = fired([REAL_GIT, *args]), fired(["git", *args])
        assert via_shim == via_real and via_real, (args, via_real, via_shim)
    assert fired(["git", "st"]) == []


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_alias_and_external_subcommand_under_other_shells(gpv, tmp_path, shell):
    """The builtin check gives the same verdicts with the script run by dash (and bash)."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    env, g, w, o, blobs, state = gpv
    git = Path(env["PILEAD_SHIM_DIR"]) / "git"
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\nexec {shutil.which(shell)} "{git}" "$@"\n')
    (wrap / "git").chmod(0o755)
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(git.parent), str(wrap), 1)}
    _aliases(env, o, g, w)
    env["PATH"] += os.pathsep + str(_zap(tmp_path, g))
    _refused_and_untouched(env, [(o, ["p"], None), (o, ["sh"], None), (o, ["cf"], None), (o, ["zap"], None)],
                           state, g, blobs)
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "ok")
    assert r.returncode == 0, r.stderr


def test_builtin_list_is_git_2_50s():
    """GUARD_BUILTINS is git 2.50's `--list-cmds=builtins` (checked when the real git is 2.50), and every
    subcommand the policy allows is in it (so W's allowlist and O's pass-through agree on what runs)."""
    assert set(shim.GIT_ALLOW) <= set(shim.GUARD_BUILTINS) and len(set(shim.GUARD_BUILTINS)) == 144
    ver = subprocess.run([REAL_GIT, "--version"], capture_output=True, text=True).stdout
    if not re.match(r"git version 2\.50\.", ver):
        pytest.skip(f"real git is not 2.50: {ver.strip()}")
    listed = subprocess.run([REAL_GIT, "--list-cmds=builtins"], capture_output=True, text=True).stdout.split()
    assert sorted(listed) == sorted(shim.GUARD_BUILTINS)


@pytest.mark.parametrize("listing", ["without-rebase", "fails", "empty"])
def test_installed_git_list_narrows_the_builtins(gwo, tmp_path, listing):
    """The installed git's `--list-cmds=builtins` can only narrow git 2.50's list: a builtin it does not
    name gets the policy (a newer git that dropped one would run the alias instead); when it prints no
    list, git 2.50's decides — never wider."""
    env, _, _, o, _, snap = gwo
    before = snap()
    fake = tmp_path / "fake"
    fake.mkdir()
    listed = " ".join(n for n in shim.GUARD_BUILTINS if n != "rebase")
    body = {"without-rebase": f'for n in {listed}; do echo "$n"; done; exit 0',
            "fails": "exit 129", "empty": "exit 0"}[listing]
    (fake / "git").write_text(f'#!/bin/sh\nif [ "x$1" = x--list-cmds=builtins ]; then {body}; fi\n'
                              f'exec "{REAL_GIT}" "$@"\n')
    (fake / "git").chmod(0o755)
    shim_dir = env["PILEAD_SHIM_DIR"]
    env = {**env, "PATH": env["PATH"].replace(shim_dir, f"{shim_dir}{os.pathsep}{fake}", 1)}
    real(env, o, "config", "alias.zz", "status")
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "c")
    assert r.returncode == 0, r.stderr
    r = run(env, o, "rebase", "-q", "HEAD~1")
    if listing == "without-rebase":
        assert _refused(r) and "rebase" in r.stderr, r.stderr
    else:
        assert r.returncode == 0, r.stderr
    assert _refused(run(env, o, "zz"))
    assert snap() == before


# ── the exec-path mirror: every git that git starts meets the shim again (p11b r4) ────────────────────
def _objects(g):
    """G's object store, by file name (a loose object or pack written there shows up)."""
    od = g / ".git" / "objects"
    return sorted(str(p.relative_to(od)) for p in od.rglob("*") if p.is_file())


@pytest.fixture
def gm(gpv, tmp_path):
    """gpv plus O with four commits (so `bisect` / `rebase -x` have work), RC (a file each nested git's
    exit status is appended to) and state() = _deep_state of G and W plus G's object store."""
    env, g, w, o, blobs, deep = gpv
    for n in ("c1", "c2"):
        (o / f"{n}.txt").write_text(n)
        real(env, o, "add", f"{n}.txt")
        real(env, o, "commit", "-q", "-m", n)
    rc = tmp_path / "RC"
    return env, g, w, o, blobs, (lambda: (deep(), _objects(g))), rc


def _hook(repo_hooks, name, body):
    repo_hooks.mkdir(parents=True, exist_ok=True)
    (repo_hooks / name).write_text(f"#!/bin/sh\n{body}\n")
    (repo_hooks / name).chmod(0o755)


def _script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def _rcs(rc):
    return rc.read_text().split() if rc.exists() else []


def test_mirror_is_git_s_exec_path_with_the_shim_as_git(gsh):
    """The mirror: `git` and the three pack programs git runs by dashed name are the shim; no other
    dashed builtin is there (so `git-read-tree` is no way round it); git's own scripts, their libraries,
    mergetools/ and the remote helpers are symlinks to the real entries; `git --exec-path` prints it."""
    git, env, g = gsh
    m = Path(shim.mirror_path(git.parent))
    assert m.is_dir() and shim.mirror_state(git.parent) == (True, "current")
    xp = Path(subprocess.run([REAL_GIT, "--exec-path"], capture_output=True, text=True).stdout.strip())
    for name in ["git"] + shim.MIRROR_SHIMMED:
        if name == "git" or (xp / name).exists():
            assert os.path.samefile(m / name, git), name
    names = {p.name for p in m.iterdir()}
    for e in xp.iterdir():
        n = e.name
        if n == "git" or n in shim.MIRROR_SHIMMED:
            continue
        builtin = n.startswith("git-") and (e.samefile(xp / "git") or n[4:] in shim.GUARD_BUILTINS)
        if builtin and not n.startswith("git-remote-"):
            assert n not in names, n
        else:
            assert n in names and os.path.samefile(m / n, e), n
    assert "git-read-tree" not in names and "git-commit" not in names
    rec = (m / shim.MIRROR_RECORD).read_text().split("\n")
    assert rec[1] == str(xp) and rec[2].startswith("git version")
    for cwd in (g, git.parent):
        r = run(env, cwd, "--exec-path")
        assert r.returncode == 0 and r.stdout.strip() == str(m), r.stderr


def test_review_4_vectors_from_another_repository_are_refused(gm, tmp_path):
    """Every way the fourth review reached G from O — a program git starts running `git -C <G> …` —
    now reaches the shim, which refuses it (77), and G's refs, index, objects, config and work trees are
    unchanged: pre-commit and post-checkout hooks, core.hooksPath, `rebase -x`, `for-each-repo`,
    `fetch --upload-pack=`, `bisect run`, diff.external + `diff --ext-diff`, filter.<x>.clean + `add`."""
    env, g, w, o, blobs, state, rc = gm
    before = state()
    rcs = {}

    def zap(vector, sub="read-tree --empty"):
        rcs[vector] = tmp_path / f"rc-{vector}"
        return f'git -C "{g}" {sub}; echo $? >> "{rcs[vector]}"'
    hooks = o / ".git" / "hooks"
    _hook(hooks, "pre-commit", zap("pre-commit"))
    run(env, o, "commit", "-q", "--allow-empty", "-m", "h1")
    (hooks / "pre-commit").unlink()
    _hook(hooks, "post-checkout", zap("post-checkout", "tag viahook"))
    run(env, o, "checkout", "-q", "-b", "nb")
    (hooks / "post-checkout").unlink()
    _hook(tmp_path / "hp", "pre-commit", zap("hooksPath"))
    real(env, o, "config", "core.hooksPath", str(tmp_path / "hp"))
    run(env, o, "commit", "-q", "--allow-empty", "-m", "h2")
    real(env, o, "config", "--unset", "core.hooksPath")
    run(env, o, "rebase", "-q", "-x", zap("rebase-x"), "HEAD~1")
    if (o / ".git" / "rebase-merge").exists():
        real(env, o, "rebase", "--abort")
    real(env, o, "config", "maintenance.repo", str(g))
    r = run(env, o, "for-each-repo", "--config=maintenance.repo", "branch", "-f", "zz", "HEAD")
    assert r.returncode != 0 and REFUSAL in r.stderr and "moves or deletes a ref" in r.stderr, r.stderr
    run(env, o, "fetch", "-q", f"--upload-pack={zap('upload-pack', 'tag viaup')}; git-upload-pack", ".")
    run(env, o, "bisect", "start", "HEAD", "HEAD~3")
    run(env, o, "bisect", "run", "sh", "-c", f"{zap('bisect-run')}; exit 0")
    real(env, o, "bisect", "reset")
    ext = _script(tmp_path / "ext", zap("diff.external"))
    real(env, o, "config", "diff.external", str(ext))
    (o / "o.txt").write_text("o changed\n")
    run(env, o, "diff", "--ext-diff")
    real(env, o, "config", "--unset", "diff.external")
    real(env, o, "config", "filter.x.clean", f"sh -c '{zap('filter-clean')}; cat'")
    (o / ".gitattributes").write_text("*.x filter=x\n")
    (o / "f.x").write_text("x\n")
    run(env, o, "add", "f.x")
    got = {v: _rcs(p) for v, p in rcs.items()}
    assert all(got.values()) and all(set(v) == {str(shim.EXIT_DENIED)} for v in got.values()), got
    assert state() == before
    for ref in ("refs/tags/viahook", "refs/tags/viaup", "refs/heads/zz"):
        assert subprocess.run([REAL_GIT, "show-ref", "-q", ref], cwd=g, env=env).returncode != 0, ref
    for b in blobs:
        assert subprocess.run([REAL_GIT, "cat-file", "-e", b], cwd=g, env=env).returncode == 0, b


def test_dashed_builtin_and_pack_programs_from_another_repository(gm, tmp_path):
    """The live bypass of the blocked report: O's diff.external running `GIT_DIR=<G>/.git git-read-tree
    --empty` — the mirror has no git-read-tree, so the name is not found (127) and G's index is kept.
    The dashed pack programs the mirror does have are the shim: `git-receive-pack <G>` is refused (77)."""
    env, g, w, o, blobs, state, rc = gm
    before = state()
    ext = _script(tmp_path / "ext", f'GIT_DIR="{g}/.git" git-read-tree --empty; echo $? >> "{rc}"; '
                                    f'git-receive-pack "{g}" </dev/null; echo $? >> "{rc}"')
    real(env, o, "config", "diff.external", str(ext))
    (o / "o.txt").write_text("o changed\n")
    run(env, o, "diff", "--ext-diff")
    assert _rcs(rc) == ["127", str(shim.EXIT_DENIED)]
    assert state() == before


def test_nested_config_parameters(gm, tmp_path):
    """A `git -c …` in O hands its settings to every git it starts in GIT_CONFIG_PARAMETERS: a nested call
    still passes in O (so `-c user.name=… commit` with a hook works), still reaches the shim for G, and a
    value set by the hook itself is read — a key a -c may not set (core.hooksPath) is refused by row 1,
    and a core.worktree=<W> there is what git makes of it (ignored: the call stays O's)."""
    env, g, w, o, blobs, state, rc = gm
    before = state()
    out = tmp_path / "out"
    _hook(o / ".git" / "hooks", "pre-commit",
          f'git log -1 --format=%s > "{out}" || exit 1\n'
          f'git -C "{g}" read-tree --empty; echo $? >> "{rc}"\n'
          f'GIT_CONFIG_PARAMETERS="$GIT_CONFIG_PARAMETERS \'core.worktree\'=\'{w}\'" git reset -q --hard; echo $? >> "{rc}"\n'
          f'GIT_CONFIG_PARAMETERS="\'core.hooksPath\'=\'/x\'" git status; echo $? >> "{rc}"\n'
          'exit 0')
    r = run(env, o, "-c", "user.name=Suite", "-c", "user.email=s@x", "commit", "-q", "--allow-empty", "-m", "cp")
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "c2\n" and real(env, o, "log", "-1", "--format=%s") == "cp\n"
    # git 2.50 does not read core.worktree from command-line config when it finds the repository, and the
    # shim's probes (which run with the same GIT_CONFIG_PARAMETERS) see what git sees: that reset is O's own
    # and passes, W untouched; G's read-tree and the core.hooksPath key are refused
    assert _rcs(rc) == [str(shim.EXIT_DENIED), "0", str(shim.EXIT_DENIED)]
    assert state() == before


def test_git_s_own_work_in_another_repository_passes(gm, tmp_path):
    """Pass cases: a test suite's temporary repository whose hook runs git on its OWN repository, rebase
    with `-x "git log -1"`, O's builtins, a push to an unrelated bare repository (git-receive-pack is the
    shim), a clone / fetch / archive FROM G (upload-pack / upload-archive serving G pass), and a clone run
    from inside W into another directory (its index-pack runs with GIT_DIR=<new repo> in W)."""
    env, g, w, o, blobs, state, rc = gm
    before = state()
    t = _init_repo(env, tmp_path / "suite")
    out = tmp_path / "hook.out"
    _hook(t / ".git" / "hooks", "pre-commit", f'git diff --cached --name-only > "{out}" && git status --porcelain >> "{out}"')
    (t / "f").write_text("f\n")
    assert run(env, t, "add", "f").returncode == 0
    r = run(env, t, "commit", "-q", "-m", "suite")
    assert r.returncode == 0 and out.read_text().startswith("f\n"), r.stderr
    r = run(env, o, "rebase", "-q", "-x", "git log -1 --format=%s", "HEAD~2")
    assert r.returncode == 0 and "c1" in r.stdout and "c2" in r.stdout, r.stderr
    for args in (["tag", "t1"], ["branch", "b1"], ["branch", "-D", "b1"], ["stash", "list"], ["gc", "-q"]):
        assert run(env, o, *args).returncode == 0, args
    b = tmp_path / "B"
    r = run(env, o, "push", "-q", str(b), "HEAD:refs/heads/x")
    assert r.returncode == 0 and real(env, b, "for-each-ref", "--format=%(refname)") == "refs/heads/x\n", r.stderr
    r = run(env, o, "clone", "-q", "--no-local", str(g), str(tmp_path / "fromg"))
    assert r.returncode == 0 and (tmp_path / "fromg" / ".git").is_dir(), r.stderr
    r = run(env, o, "fetch", "-q", str(g), "main")
    assert r.returncode == 0, r.stderr
    r = run(env, o, "archive", f"--remote={g}", "--format=tar", "-o", str(tmp_path / "g.tar"), "HEAD")
    assert r.returncode == 0 and (tmp_path / "g.tar").stat().st_size > 0, r.stderr
    r = run(env, w, "clone", "-q", "--no-local", str(o), str(tmp_path / "fromw"))
    assert r.returncode == 0 and (tmp_path / "fromw" / "c2.txt").is_file(), r.stderr
    assert state() == before


def test_allowed_commands_in_the_worktree_still_work(gm, tmp_path):
    """A call that gets G's policy runs with git's own exec-path, as before the mirror: in W, `stash`
    (whose children are update-index and `reset --hard`), a fetch from a local repository (unpack-objects,
    rev-list, maintenance) and `worktree add` (branch, `reset --hard`) all work."""
    env, g, w, o, blobs, state, rc = gm
    r = run(env, w, "stash")
    assert r.returncode == 0 and "stash@{0}" in real(env, w, "stash", "list"), r.stderr
    r = run(env, w, "stash", "pop", "-q", "--index")
    assert r.returncode == 0 and real(env, w, "status", "--porcelain") == "AM w.txt\n", r.stderr
    r = run(env, w, "fetch", "-q", str(o), "HEAD")
    assert r.returncode == 0, r.stderr
    assert real(env, w, "rev-parse", "FETCH_HEAD") == real(env, o, "rev-parse", "HEAD")
    r = run(env, w, "worktree", "add", "-q", "--detach", str(tmp_path / "W3"))
    assert r.returncode == 0 and (tmp_path / "W3" / ".git").is_file(), r.stderr


def _closed_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_remote_helpers_run_for_another_repository(gm, tmp_path):
    """D2: git starts `git remote-http(s)` for an http(s) URL — no builtin, but a nested remote helper
    passes, so O's ls-remote / clone to a closed local port fail with git's network error, not 77."""
    env, g, w, o, blobs, state, rc = gm
    port = _closed_port()
    for scheme in ("http", "https"):
        r = run(env, o, "ls-remote", f"{scheme}://127.0.0.1:{port}/x")
        assert r.returncode == 128 and REFUSAL not in r.stderr and "127.0.0.1" in r.stderr, r.stderr
    r = run(env, w, "clone", "-q", f"http://127.0.0.1:{port}/x", str(tmp_path / "net"))
    assert r.returncode == 128 and REFUSAL not in r.stderr, r.stderr
    r = run(env, o, "remote-http", "x", f"http://127.0.0.1:{port}/x")  # not nested: the builtin gate
    assert _refused(r), r.stderr


def test_exec_path_from_the_caller(gm, tmp_path):
    """A GIT_EXEC_PATH or --exec-path=<dir> the caller sets would bring the real git back: refused (row
    1), whatever the directory — the mirror's own path included for --exec-path=; plain --exec-path prints."""
    env, g, w, o, blobs, state, rc = gm
    m = shim.mirror_path(Path(env["PILEAD_SHIM_DIR"]))
    xp = subprocess.run([REAL_GIT, "--exec-path"], capture_output=True, text=True).stdout.strip()
    before = state()
    for extra, args in (({"GIT_EXEC_PATH": xp}, ["status"]), ({"GIT_EXEC_PATH": str(tmp_path)}, ["tag", "x"]),
                        (None, [f"--exec-path={xp}", "status"]), (None, [f"--exec-path={m}", "status"])):
        r = run(env, o, *args, extra=extra)
        assert _refused(r) and "exec" in r.stderr.lower(), (args, r.stderr)
    assert run(env, o, "--exec-path").stdout.strip() == m
    assert state() == before


def _fake_git(d, body=""):
    """A `git` in d that runs `body` first, then the real git (a different file: the mirror is stale)."""
    d.mkdir(exist_ok=True)
    return _script(d / "git", f'{body}\nexec "{REAL_GIT}" "$@"')


def test_a_changed_git_rebuilds_the_mirror_once(gm, tmp_path):
    """Staleness: a different real git (another path and inode, a different --version) is noticed without
    running git; the mirror is rebuilt once (one --exec-path probe), then read as current."""
    env, g, w, o, blobs, state, rc = gm
    count = tmp_path / "probes"
    fake = _fake_git(tmp_path / "fake", f'case "$1" in --exec-path) echo x >> "{count}" ;; '
                                        f'--version) echo "git version 9.9.9 (fake)"; exit 0 ;; esac')
    sd = env["PILEAD_SHIM_DIR"]
    env = {**env, "PATH": env["PATH"].replace(sd, f"{sd}{os.pathsep}{fake.parent}", 1)}
    for _ in range(3):
        assert run(env, o, "status", "--porcelain").returncode == 0
    assert _rcs(count) == ["x"]
    rec = (Path(shim.mirror_path(Path(sd))) / shim.MIRROR_RECORD).read_text().split("\n")
    assert rec[0] == str(fake) and rec[2] == "git version 9.9.9 (fake)"
    assert shim.mirror_state(Path(sd)) == (True, "current")
    rc.write_text("")
    _hook(o / ".git" / "hooks", "pre-commit", f'git -C "{g}" read-tree --empty; echo $? >> "{rc}"')
    run(env, o, "commit", "-q", "--allow-empty", "-m", "x")
    assert _rcs(rc) == [str(shim.EXIT_DENIED)]


def test_a_mirror_that_cannot_be_built_fails_closed(gm, tmp_path):
    """A git whose exec-path is no directory, or a shim directory the mirror cannot be written in: a call
    on another repository gets G's policy — refused when the policy refuses it (77), with a line saying
    why; passed with git's own exec-path when the policy allows it."""
    env, g, w, o, blobs, state, rc = gm
    sd = Path(env["PILEAD_SHIM_DIR"])
    before = real(env, o, "for-each-ref")
    fake = _fake_git(tmp_path / "fake", f'[ "x$1" = x--exec-path ] && {{ echo "{tmp_path}/nowhere"; exit 0; }}')
    fenv = {**env, "PATH": env["PATH"].replace(str(sd), f"{sd}{os.pathsep}{fake.parent}", 1)}
    r = run(fenv, o, "tag", "t9")
    assert _refused(r.__class__(r.args, r.returncode, r.stdout, r.stderr.split("\n", 1)[1])), r.stderr
    assert r.stderr.startswith("pi-lead git shim: the exec-path mirror") and "could not be rebuilt" in r.stderr
    r = run(fenv, o, "status", "--porcelain")
    assert r.returncode == 0 and "exec-path mirror" in r.stderr, r.stderr
    assert real(env, o, "for-each-ref") == before
    shutil.rmtree(shim.mirror_path(sd))
    sd.chmod(0o555)
    try:
        r = run(env, o, "tag", "t9")
        assert r.returncode == shim.EXIT_DENIED and "exec-path mirror" in r.stderr, r.stderr
    finally:
        sd.chmod(0o755)
    assert run(env, o, "tag", "t9").returncode == 0 and shim.mirror_state(sd) == (True, "current")


@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_mirror_under_other_shells(gm, tmp_path, shell):
    """The top-level call run by dash / bash: a hook in O is still refused on G, and O's own work passes."""
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    env, g, w, o, blobs, state, rc = gm
    sd = Path(env["PILEAD_SHIM_DIR"])
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    _script(wrap / "git", f'exec {shutil.which(shell)} "{sd / "git"}" "$@"')
    # the wrapper is the shim's directory now (as in test_guarded_shim_under_other_shells): a PATH entry
    # holding a `git` that runs the shim, but not skipped as the shim's own, would be taken as the real git
    env = {**env, "PILEAD_SHIM_DIR": str(wrap), "PATH": env["PATH"].replace(str(sd), str(wrap), 1)}
    before = state()
    _hook(o / ".git" / "hooks", "pre-commit", f'git -C "{g}" read-tree --empty; echo $? >> "{rc}"; git log -1 >/dev/null')
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "sh")
    assert r.returncode == 0 and _rcs(rc) == [str(shim.EXIT_DENIED)], r.stderr
    assert run(env, o, "--exec-path").stdout.strip() == shim.mirror_path(sd)
    assert state() == before


# ── git's own nested helpers: credential-<name>, http-fetch (p11b r5) ──────────────────────────────────
def _no_proxy_env(env):
    """env without the proxy variables curl reads (so http.proxy in config is what curl uses)."""
    drop = {"NO_PROXY", "no_proxy", "http_proxy", "https_proxy", "HTTPS_PROXY", "ALL_PROXY", "all_proxy",
            shim.DEPTH_VAR}
    return {k: v for k, v in env.items() if k not in drop}


def test_nested_credential_helper_runs_for_another_repository(gm, tmp_path):
    """Finding 1: git starts a credential helper as `git credential-<name>` (Apple's system gitconfig sets
    credential.helper=osxkeychain). Nested, in O, it passes: an https fetch through a proxy that names a
    user asks the helper (set in O's config, then in the global one) and fails with git's network error,
    not 77. A helper that itself runs `git -C <G> read-tree --empty` is refused at that inner git (77),
    G unchanged. Top level, `git credential-<name>` and `-c credential.helper=…` stay refused."""
    env, g, w, o, blobs, state, rc = gm
    env = _no_proxy_env(env)
    before = state()
    port = _closed_port()
    bin_ = tmp_path / "helpers"
    bin_.mkdir()
    calls = tmp_path / "calls"
    _script(bin_ / "git-credential-fake",
            f'echo "$1" >> "{calls}"\ncat >/dev/null\n'
            f'[ -n "$ZAP" ] && {{ git -C "{g}" read-tree --empty; echo $? >> "{rc}"; }}\n'
            'echo username=u; echo password=p')
    env = {**env, "PATH": f"{env['PATH']}{os.pathsep}{bin_}"}
    real(env, o, "config", "http.proxy", f"http://u@127.0.0.1:{port}")
    url = f"https://127.0.0.1:{port}/x"
    real(env, o, "config", "credential.helper", "fake")
    r = run(env, o, "fetch", url)
    assert r.returncode == 128 and REFUSAL not in r.stderr and f"port {port}" in r.stderr, r.stderr
    assert _rcs(calls) == ["get"]
    real(env, o, "config", "--unset", "credential.helper")
    home_cfg = Path(env["HOME"]) / ".gitconfig"
    home_cfg.write_text("[credential]\n\thelper = fake\n")
    try:
        r = run(env, o, "fetch", url)
        assert r.returncode == 128 and REFUSAL not in r.stderr and f"port {port}" in r.stderr, r.stderr
        assert _rcs(calls) == ["get", "get"]
        r = run(env, o, "fetch", url, extra={"ZAP": "1"})  # the inner git's refusal, then the network error
        assert r.returncode == 128 and REFUSAL in r.stderr and "read-tree" in r.stderr, r.stderr
        assert f"port {port}" in r.stderr.split(REFUSAL, 1)[1], r.stderr
        assert _rcs(calls) == ["get", "get", "get"] and _rcs(rc) == [str(shim.EXIT_DENIED)]
    finally:
        home_cfg.unlink()
    assert state() == before
    for args in (["credential-fake", "get"], ["credential-osxkeychain", "get"],
                 ["-c", "credential.helper=fake", "fetch", url], ["http-fetch", "-a", "HEAD", url]):
        r = subprocess.run(["git", *args], cwd=o, env=env, input="", capture_output=True, text=True)
        assert _refused(r), (args, r.returncode, r.stderr)
    assert _rcs(calls) == ["get", "get", "get"] and state() == before


def test_nested_http_fetch_and_credential_helpers_are_scoped(gm, tmp_path):
    """Nested (a hook in O): `git http-fetch` and `git credential-<name>` pass the builtin gate and run
    for O — http-fetch fails on the closed port, not 77; the helper runs. Pointed at G, or with a `/` in
    the helper's name, they get the policy (77); G unchanged."""
    env, g, w, o, blobs, state, rc = gm
    env = _no_proxy_env(env)
    before = state()
    port = _closed_port()
    bin_ = tmp_path / "helpers"
    bin_.mkdir()
    calls = tmp_path / "calls"
    _script(bin_ / "git-credential-fake", f'echo "$1" >> "{calls}"; cat >/dev/null; echo username=u')
    env = {**env, "PATH": f"{env['PATH']}{os.pathsep}{bin_}"}
    url = f"http://127.0.0.1:{port}/x"
    _hook(o / ".git" / "hooks", "pre-commit",
          f'git http-fetch -a HEAD "{url}" </dev/null; echo $? >> "{rc}"\n'
          f'git credential-fake get </dev/null >/dev/null; echo $? >> "{rc}"\n'
          f'git -C "{g}" http-fetch -a HEAD "{url}" </dev/null; echo $? >> "{rc}"\n'
          f'git -C "{g}" credential-fake store </dev/null; echo $? >> "{rc}"\n'
          f'git credential-../helpers/x get </dev/null; echo $? >> "{rc}"\n'
          'exit 0')
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "helpers")
    assert r.returncode == 0, r.stderr
    got = _rcs(rc)
    denied = str(shim.EXIT_DENIED)
    assert len(got) == 5 and got[0] not in ("0", denied) and got[1] == "0", got
    assert got[2:] == [denied, denied, denied], got
    assert _rcs(calls) == ["get"]
    assert state() == before


# ── the depth guard: a loop through the shim ends at DEPTH_LIMIT (p11b r5) ─────────────────────────────
def _looping_git(d, shim_path, count, brake=200):
    """A `git` in d that logs `<PILEAD_SHIM_DEPTH> <args>` to count and runs the shim again (so the shim,
    finding it on PATH, takes it for the real git: a loop). It stops by itself after `brake` calls, so a
    shim without a working guard fails this test instead of filling the process table."""
    d.mkdir(exist_ok=True)
    return _script(d / "git", f'[ -f "{count}" ] && [ "$(wc -l < "{count}")" -ge {brake} ] && exit 99\n'
                              f'echo "${{{shim.DEPTH_VAR}-}} $*" >> "{count}"\n'
                              f'exec "{shim_path}" "$@"')


def test_depth_guard_stops_a_loop_through_the_shim(gm, tmp_path):
    """Finding 2: a `git` earlier on PATH than the real one that runs the shim again. The chain stops at
    the limit with 77 and a line naming the loop; the calls it started are bounded — at most limit + 1
    carry the caller's own command, and the shim's probes, which run at the limit, end at once."""
    env, g, w, o, blobs, state, rc = gm
    env = _no_proxy_env(env)
    sd = Path(env["PILEAD_SHIM_DIR"])
    count = tmp_path / "count"
    fake = _looping_git(tmp_path / "loop", sd / "git", count)
    lenv = {**env, "PATH": env["PATH"].replace(str(sd), f"{sd}{os.pathsep}{fake.parent}", 1)}
    before = state()
    r = run(lenv, o, "status", "--porcelain")
    assert r.returncode == shim.EXIT_DENIED, (r.returncode, r.stderr)
    assert f"{shim.DEPTH_VAR}={shim.DEPTH_LIMIT}" in r.stderr and "loop" in r.stderr, r.stderr
    lines = count.read_text().splitlines()
    own = [ln for ln in lines if ln.split(" ", 1)[1:] == ["status --porcelain"]]
    assert [ln.split()[0] for ln in own] == [str(n) for n in range(1, shim.DEPTH_LIMIT + 1)], lines
    assert len(own) <= shim.DEPTH_LIMIT + 1
    assert len(lines) < 100, len(lines)  # the fake's brake (200) was never reached
    assert all(ln.split()[0] == str(shim.DEPTH_LIMIT) for ln in lines if ln not in own), lines
    assert state() == before


def test_depth_guard_refuses_before_starting_anything(gm, tmp_path):
    """A call that arrives at the limit, or with a value the shim never writes, is refused (77) before it
    starts anything: the looping `git` on PATH is never called."""
    env, g, w, o, blobs, state, rc = gm
    env = _no_proxy_env(env)
    sd = Path(env["PILEAD_SHIM_DIR"])
    count = tmp_path / "count"
    fake = _looping_git(tmp_path / "loop", sd / "git", count)
    lenv = {**env, "PATH": env["PATH"].replace(str(sd), f"{sd}{os.pathsep}{fake.parent}", 1)}
    for value in (str(shim.DEPTH_LIMIT), "99", "08", "x", "-1"):
        for args in (["status"], ["--version"]):
            r = run(lenv, o, *args, extra={shim.DEPTH_VAR: value})
            assert r.returncode == shim.EXIT_DENIED and "loop" in r.stderr, (value, args, r.stderr)
    assert not count.exists()
    r = run(env, o, "status", "--porcelain", extra={shim.DEPTH_VAR: str(shim.DEPTH_LIMIT - 1)})
    assert r.returncode == 0, r.stderr


def test_normal_nested_chains_stay_under_the_depth_limit(gm, tmp_path):
    """hook → git → hook → git: O's pre-commit commits in a second repository whose own pre-commit runs
    git; every call passes, and the depth each hook sees is small (the real git never counts)."""
    env, g, w, o, blobs, state, rc = gm
    env = _no_proxy_env(env)
    o2 = _init_repo(env, tmp_path / "O2")
    seen = tmp_path / "seen"
    _hook(o2 / ".git" / "hooks", "pre-commit",
          f'echo "${shim.DEPTH_VAR}" >> "{seen}"; git log -1 --format=%s > /dev/null || exit 1')
    _hook(o / ".git" / "hooks", "pre-commit",
          f'echo "${shim.DEPTH_VAR}" >> "{seen}"; git -C "{o2}" commit -q --allow-empty -m inner || exit 1')
    r = run(env, o, "commit", "-q", "--allow-empty", "-m", "outer")
    assert r.returncode == 0, r.stderr
    assert _rcs(seen) == ["1", "2"]
    assert real(env, o2, "log", "-1", "--format=%s") == "inner\n"

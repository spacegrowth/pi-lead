"""The executor's process-level git fence: a POSIX `sh` script named `git`, first on the executor's PATH.

`pilead spawn` writes it to `<session dir>/shim/git` and the bootstrap exports
`PILEAD_SHIM_DIR=<that dir>` and `PATH="$PILEAD_SHIM_DIR:$PATH"` before `exec pi`, so every program the
executor runs that calls `git` BY NAME (its bash tool, a python subprocess, node, make, a script file)
lands here — the text fence in extensions/bash-fence.ts only ever sees the bash string.

POLICY = bash-fence.ts's `checkGit`, copied, not reinvented: the constants below mirror GIT_ALLOW,
NAMED_DENY, REF_GUARD, DANGEROUS_KEY, CMD_VARS, GIT_CFG_VAR and GIT_OPT_WITH_ARG, and
tests/test_git_shim.py parses the .ts to fail on any drift. Allowed calls are `exec`ed on the real git
with argv untouched; denied ones print the refusal sentence to stderr and exit 77.

Differences forced by working on a process instead of a string (both refuse, never allow, more):
- Command-running env vars (GIT_PAGER, GIT_EDITOR, GIT_SSH_COMMAND, …): the fence parses the value as a
  command; the shim cannot parse shell, so it refuses when the value names `git` as a command word
  (GIT_WORD: program basename `git`/`git-*`, not a `…/git/…` directory). It must
  refuse there: whatever git spawns runs with git's own exec-path FIRST on PATH, so a nested `git
  commit` in a pager/editor/`!alias` reaches the real git without passing this shim again.
- Config-injecting env vars are checked as inherited state, not as assignments: the fence's
  `GIT_CONFIG_KEY_<n>` is covered by GIT_CONFIG_COUNT (git reads no KEY_<n> without it). So that an
  executor is never bricked by what the human's shell exported, the launch line first clears every
  variable the shim refuses on (`launch_env_reset`); only values set inside the session meet the shim.
- The fence's xargs-only rules (`stdinArgs`) have no process-level equivalent: argv is final here.

THE GUARDED REPOSITORY. `write_git_shim(session_dir, guarded=<dir>)` resolves, with the real git, the
common git directory and top level of the repository `guarded` is in and writes both into the script;
the policy then applies to that repository and its worktrees (same common directory) only. Per call:
row 1, the environment and -c / --config-env / --exec-path= checks, applies everywhere (and
GIT_COMMON_DIR, which moves where refs are written, is refused); row 2, the no-subcommand pass-throughs,
as before; rows 3–4, `init`/`clone` are refused when the directory they create or initialise (or any
--separate-git-dir / --git-dir / --work-tree / $GIT_DIR / $GIT_WORK_TREE) lands in the guarded
repository, else passed; row 5, any other call is passed untouched when `git <its -C / --git-dir /
--work-tree / --bare / -c / --config-env> rev-parse --git-common-dir` names another existing directory
(compared physically, `cd -P && pwd -P` and `-ef`) AND its work tree neither lands in the guarded
repository nor CONTAINS it or one of its worktrees (an ancestor of the top level, the common dir or a
path `worktree list` names; undecidable counts as containing) — not --work-tree, $GIT_WORK_TREE, -c /
--config-env core.worktree, nor the top level `rev-parse --show-toplevel` names (so `--git-dir=<other>`
run inside the guarded repository, whose work tree is the current directory, is the guarded
repository, and so is a repository whose top level is a parent of it) — AND, for `worktree add`, no
word of it lands there or contains it (init/clone likewise refuse a target that contains it). Row 5 (and
rows 3–4) also need every global option to be one git 2.50 has (--attr-source and --shallow-file take
the next word; an unknown option gets the policy), a subcommand that is a git builtin (GUARD_BUILTINS,
narrowed by the installed git's `--list-cmds=builtins` when it prints one: an alias or an external
`git-<name>` gets the policy), and no path git would write to land in the guarded
repository: GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_SHALLOW_FILE / --shallow-file, GIT_GRAFT_FILE (which
`replace --convert-graft-file` deletes), or an entry of GIT_ALTERNATE_OBJECT_DIRECTORIES — a relative one
checked from the -C directory, the top level and the git dir (for init/clone it counts as landing), a
C-quoted alternates entry counting as landing; and a `config` with --global or --system, or with a
--file / -f naming the guarded repository's config (config.worktree included) or a file it reads (what
`config --list --show-origin` names for it, $HOME/.gitconfig, $XDG_CONFIG_HOME/git/config), gets the
policy (so a read passes, a key the policy refuses is refused). Row 6, everything else — the guarded
repository, or one that cannot be worked out — gets the policy. A push, send-pack or receive-pack that
row 5 passes is refused when its destination lands in the guarded repository: a local path (as git enters
it: PATH, PATH/.git, PATH.git, a gitfile, `~`), a file:// URL, or a remote name whose push URLs (`remote
get-url --push --all`, and every remote.<name>.pushurl / .url `config --get-all` reads) do, each also
under any matching url.<base>.insteadOf / pushInsteadOf; which refs it names does not matter. A clearly
remote destination (ssh://, git://, http(s)://, ftp(s)://, the scp form host:path) passes; one that looks
local and cannot be resolved (a relative file:// URL, `~user`, a `<transport>::` or unknown scheme), and
--receive-pack / --exec / remote.<name>.receivepack, are refused. So a test suite can build its own
repositories; a repository the shim cannot identify is treated as the guarded one. `guarded=None`, or a
directory that cannot be resolved, writes the guard-everything shim, byte-for-byte the text before the
guard existed.
THE EXEC-PATH MIRROR. git puts its exec-path first on PATH for every program it starts, so without help
a git that a hook, `rebase -x`, `bisect run`, a filter, diff.external, `--upload-pack`, a helper or
git's own child process runs would be the real git. A guarded shim written with a mirror (write_git_shim
always) therefore runs a call it passes through for another repository — and a call with no subcommand —
with GIT_EXEC_PATH=<shim dir>/exec-path: a copy of git's exec-path made of symlinks, whose `git` (and
git-upload-pack, git-receive-pack, git-upload-archive, which git runs by those names) is this shim, and
which has no other dashed builtin (`git-read-tree` is not found there). Every git such a call starts is
the shim again (NESTED: it arrives with GIT_EXEC_PATH = the mirror, which it takes off), scoped like any
call: another repository → passed, still mirrored; the guarded one → its policy. A call that gets the
policy, in the guarded repository or its worktrees, runs with git's own exec-path, exactly as before, so
`stash`, `fetch` and `worktree add` there keep their own git children. Nested calls only: a remote
helper (`remote-http`, `-https`, `-ftp`, `-ftps`), dumb-http `http-fetch` and a credential helper git
starts as `git credential-<name>` (`credential-osxkeychain`, Apple's system default) pass the builtin
gate; `upload-pack` / `upload-archive` are scoped by the repository they serve — the guarded one is
served with git's own exec-path (they only read), anything else with the mirror; `index-pack`,
`unpack-objects`, `pack-objects` and those helpers are scoped by their git dir, not by the work tree git
would assume (clone runs them with GIT_DIR=<new repository> in the caller's directory); and a
GIT_CONFIG_PARAMETERS in the form git writes for its children, with no key a -c may not set, is accepted
(and read by the probes).
The guard's own probes run with git's own exec-path: they start no program. The mirror records the real
git, its exec-path, `git --version` and both paths' inodes and mtimes; each mirrored call checks that
record (one `ls`, no git), rebuilds the mirror once on a mismatch, and when that fails gets the guarded
repository's policy (fail closed) with a line saying why. A caller's own GIT_EXEC_PATH (other than the
mirror) or --exec-path=<dir> stays refused by row 1.
THE DEPTH GUARD. Every guarded shim call counts itself in PILEAD_SHIM_DEPTH (DEPTH_VAR) before it runs
the real git, and one that finds DEPTH_LIMIT (8) calls already in its chain — or a value the shim never
writes — is refused (77) with a line naming the loop, before it starts anything: a `git` earlier on PATH
that runs this shim again would otherwise loop for ever. Its probes run with the variable at the limit, so
a probe that comes back to the shim ends at once (a loop costs one chain of calls, not a tree of them).
The guard-everything shim stays byte-for-byte the pre-guard text, so it has no depth guard.
LIMIT: a program that calls git by an ABSOLUTE path, or sets PATH or GIT_EXEC_PATH itself, is outside the
fence, like any non-git tool writing files: the shim is a git fence, not a sandbox. A hook, filter or
helper inside the guarded repository (which the executor would have to install there) runs with git's
own exec-path, as it did before the guard existed. Nested, a git-<name> that is neither a builtin nor one
of those helpers gets the policy, which refuses it. Commands run in another repository that write
files to an output path (`diff --output=`, `format-patch -o`, `archive --output`, `checkout-index
--prefix` and similar) are passed even when that path is in the guarded repository, and so is an output
path named by an environment variable or a config key (the GIT_TRACE* files, http.cookieFile): the shim
is a git fence, not a filesystem sandbox. For the same reason it does not look inside another
repository's git directory, so that repository's objects, index or config made a symlink into the guarded one are not
seen; nor does it know a file the guarded repository's config would include but that does not exist
yet. The shim is a guardrail against accidents, not a sandbox; the lead's review is the control.

The shim needs nothing but `sh`, `grep` and `tr` at call time (grep/tr only on the -c/config/env/deny
paths), plus the real git for a guarded shim's repository probe and `ls` for its mirror check (`ln`,
`mkdir`, `mv`, `rm` and `touch` only when it rebuilds the mirror): no python, no node. It finds the real
git by walking $PATH, skipping $PILEAD_SHIM_DIR, any non-absolute entry, and any `git` that is this very
file (`-ef`) — never a hardcoded /usr/bin/git.
"""
import os
import subprocess
from pathlib import Path

# ── policy: mirrors extensions/bash-fence.ts (tests/test_git_shim.py checks it) ─────────────────────
GIT_ALLOW = ("add rm mv status diff log show blame grep ls-files rev-parse branch checkout switch "
             "restore stash fetch remote config describe cat-file symbolic-ref worktree reset apply "
             "help version shortlog merge-base ls-tree for-each-ref rev-list check-ignore show-ref").split()
NAMED_DENY = "commit push merge rebase tag pull cherry-pick revert am".split()
REF_GUARD = {
    "branch": {"s": "dDfmMcCu", "l": ["--delete", "--force", "--move", "--copy", "--set-upstream-to",
                                      "--unset-upstream", "--edit-description"],
               "argL": ["--sort", "--format", "--points-at"]},
    "checkout": {"s": "B", "arg": "b", "l": ["--orphan"]},
    "switch": {"s": "C", "arg": "c", "l": ["--orphan", "--force-create"], "safe": ["--force"]},
    "stash": {"sub": ["clear"]},
    "remote": {"sub": ["set-url", "remove", "rm", "rename", "prune", "set-head"]},
    "worktree": {"s": "B", "arg": "b", "l": ["--orphan"], "sub": ["remove", "prune", "move", "lock", "unlock"]},
    "symbolic-ref": {"s": "d", "arg": "m", "l": ["--delete"], "maxPos": 1},
}
DANGEROUS_KEY = (r"^(alias|pager|filter|credential|difftool|mergetool|sequence|gpg|ssh|uploadpack|receivepack"
                 r"|include|includeif)\.|^core\.(pager|editor|sshcommand|fsmonitor|hookspath|askpass|gitproxy)$"
                 r"|\.(command|textconv|driver|program|helper|cmd)$|^diff\.external$|^init\.templatedir$")
CMD_VARS = ["GIT_PAGER", "PAGER", "GIT_EDITOR", "EDITOR", "VISUAL", "GIT_SEQUENCE_EDITOR", "GIT_SSH_COMMAND",
            "GIT_SSH", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_EXTERNAL_DIFF", "GIT_PROXY_COMMAND", "PROMPT_COMMAND"]
# bash-fence's GIT_CFG_VAR regex, spelled out; GIT_CONFIG_COUNT stands in for GIT_CONFIG_KEY_<n>.
CFG_VARS = ["GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_EXEC_PATH",
            "GIT_TEMPLATE_DIR", "GIT_CONFIG_COUNT"]
GIT_OPT_WITH_ARG = ["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"]
# `git` as a COMMAND WORD inside an env var's value: a word whose program basename is `git` or `git-*`
# (`git commit`, `/usr/bin/git push`, `sh -c "git x"`, `env git push`, `git-foo`) — never a directory
# named git on the way to another program (`/usr/local/git/bin/ssh`, `…/extensions/git/dist/askpass.sh`,
# `/opt/git-tools/vim`), nor `github`, `.git`, `id_git`. POSIX ERE (grep -E); no single quotes.
GIT_WORD = r"(^|[^A-Za-z0-9_.-])git(-[^/[:space:]]*)?([^A-Za-z0-9_./-]|$)"

REFUSAL = "pi-lead executor: stage your work and report; the lead commits."
EXIT_DENIED = 77
MARKER = "PILEAD_GIT_SHIM"
REF_REASON = "this git $pl_sub form moves or deletes a ref — executors may create a branch, never move, force-reset or delete one"
# Refused (and cleared at launch) by a shim that guards one repository: it moves where git reads and
# writes refs, so a call could look like another repository's and still write the guarded one's.
GUARD_VARS = ["GIT_COMMON_DIR"]
# The guarded shim's own reading of git's global options (git 2.50's handle_options), so that it finds
# the subcommand and every -C / --git-dir where git does: GIT_OPT_WITH_ARG plus the two options the text
# fence does not list that also take the NEXT word (--attr-source, --shallow-file); the `--x=value` forms
# it knows; and the options that take none. Any other option before the subcommand makes the call get
# the policy (fail closed: a newer git's option could take a word the shim would misread).
GUARD_OPT_WITH_ARG = GIT_OPT_WITH_ARG + ["--attr-source", "--shallow-file"]
GUARD_OPT_EQ = ["--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--attr-source",
                "--list-cmds", "--exec-path"]
GUARD_OPT_FLAGS = ("-p --paginate -P --no-pager --bare --no-replace-objects --no-lazy-fetch --no-optional-locks "
                   "--no-advice --literal-pathspecs --glob-pathspecs --noglob-pathspecs --icase-pathspecs "
                   "--exec-path --html-path --man-path --info-path -v --version -h --help").split()
# Environment variables naming a path git WRITES in whatever repository the call is on (the index, the
# object store, the shallow file, the graft file `replace --convert-graft-file` deletes): a call from
# another repository whose value lands in the guarded one gets the policy.
# GIT_ALTERNATE_OBJECT_DIRECTORIES is read as a colon-separated list.
GUARD_PATH_VARS = ["GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE"]
# Cleared (in a subshell) from the guard's own probes of a directory it asks "which repository is this?":
# the call's repository and path variables must not decide that, nor make the probe fail (fail open).
PROBE_CLEAR = ["GIT_DIR", "GIT_WORK_TREE"] + GUARD_PATH_VARS + ["GIT_ALTERNATE_OBJECT_DIRECTORIES"]
# Cleared from the environment of the git that resolves the guarded repository at write time.
RESOLVE_CLEAR = CFG_VARS + CMD_VARS + ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_SHALLOW_FILE",
                                     "GIT_GRAFT_FILE"]
# git 2.50's builtins (`git --list-cmds=builtins`, git 2.50.1): the only subcommands the guarded shim
# passes through for another repository. git runs a builtin before any alias or `git-<name>` program, so
# the name is what runs; any other name is an alias or an external command, and gets the policy. The
# installed git's own list, when it prints one, can only narrow this (a builtin a newer git dropped).
GUARD_BUILTINS = (
    "add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr "
    "check-ignore check-mailmap check-ref-format checkout checkout--worker checkout-index cherry "
    "cherry-pick clean clone column commit commit-graph commit-tree config count-objects credential "
    "credential-cache credential-cache--daemon credential-store describe diagnose diff diff-files "
    "diff-index diff-pairs diff-tree difftool fast-export fast-import fetch fetch-pack fmt-merge-msg "
    "for-each-ref for-each-repo format-patch fsck fsck-objects fsmonitor--daemon gc get-tar-commit-id "
    "grep hash-object help hook index-pack init init-db interpret-trailers log ls-files ls-remote ls-tree "
    "mailinfo mailsplit maintenance merge merge-base merge-file merge-index merge-ours merge-recursive "
    "merge-recursive-ours merge-recursive-theirs merge-subtree merge-tree mktag mktree multi-pack-index "
    "mv name-rev notes pack-objects pack-redundant pack-refs patch-id pickaxe prune prune-packed pull "
    "push range-diff read-tree rebase receive-pack reflog refs remote remote-ext remote-fd repack replace "
    "replay rerere reset restore rev-list rev-parse revert rm send-pack shortlog show show-branch "
    "show-index show-ref sparse-checkout stage stash status stripspace submodule--helper switch "
    "symbolic-ref tag unpack-file unpack-objects update-index update-ref update-server-info "
    "upload-archive upload-archive--writer upload-pack var verify-commit verify-pack verify-tag version "
    "whatchanged worktree write-tree ").split()
# ── the exec-path mirror (guarded shim only) ─────────────────────────────────────────────────────────
# git puts its exec-path first on PATH for every program it starts (hooks, `rebase -x`, `bisect run`,
# filters, diff.external, --upload-pack, helpers, its own git children), so a nested `git` would be the
# real git. A call the guarded shim passes through for ANOTHER repository therefore runs with
# GIT_EXEC_PATH=<shim dir>/exec-path: a mirror of the real exec-path whose `git` is this shim. A call
# that gets the guarded repository's policy runs with the real exec-path, as before the mirror.
MIRROR_DIR = "exec-path"
# In the mirror: the record (real git as found on PATH, its exec-path, `git --version`, and the inode
# line `ls -Lid <git> <exec-path>` prints, words joined by one space), and two empty files carrying the
# real git's and the exec-path's mtimes (`touch -r`).
MIRROR_RECORD = ".pilead-mirror"
MIRROR_GIT_MTIME = ".pilead-git"
MIRROR_XP_MTIME = ".pilead-xp"
# Dashed names that are the shim in the mirror: git runs them itself, by these names, through the shell
# (local transports). Every other dashed name that is the git binary (or a builtin's name) is left out.
MIRROR_SHIMMED = ["git-upload-pack", "git-receive-pack", "git-upload-archive"]
# Helpers git starts by name that are no builtins, which a NESTED call may run: the remote helpers
# (`git remote-<scheme>`) and dumb-http `git http-fetch` — and, by NESTED_CRED_PREFIX, any credential
# helper git starts as `git credential-<name>` (credential.helper=osxkeychain, Apple's system default).
NESTED_HELPERS = ["remote-http", "remote-https", "remote-ftp", "remote-ftps", "http-fetch"]
NESTED_CRED_PREFIX = "credential-"
# Nested children that touch no work tree (only the object store, or the network; a credential helper,
# NESTED_CRED_PREFIX, too): scoped by their git directory, not by the work tree git would assume —
# `clone` runs `index-pack` with GIT_DIR=<new repo> in the caller's directory, which may be the guarded
# worktree.
NESTED_NO_WORKTREE = ["index-pack", "unpack-objects", "pack-objects"] + NESTED_HELPERS
# The form of GIT_CONFIG_PARAMETERS git writes for its children (`'key'='value'` or `'key'`, the older
# `'key=value'`, single-quoted with `'\''`, one space apart): a nested call's value must match it
# before the shim reads its keys (so the eval that splits it expands nothing).
_CFGP_Q = r"'[^']*'(\\''[^']*')*"
CFGP_RE = rf"^{_CFGP_Q}(={_CFGP_Q})?( {_CFGP_Q}(={_CFGP_Q})?)*$"
# The shim's depth guard: every guarded shim call counts itself in PILEAD_SHIM_DEPTH before it runs the
# real git and refuses, starting nothing, when DEPTH_LIMIT calls are already in its chain — a loop (a
# `git` on PATH that runs this shim again). Its own probes see the limit, so a probe that comes back to
# the shim is refused at once: a loop costs one chain of calls, never a tree of them.
DEPTH_VAR = "PILEAD_SHIM_DEPTH"
DEPTH_LIMIT = 8
INIT_ARGS = ("b", "--template --separate-git-dir --object-format --ref-format --initial-branch")
CLONE_ARGS = ("obucj", "--template --reference --reference-if-able --origin --branch --revision --upload-pack "
              "--depth --shallow-since --shallow-exclude --separate-git-dir --config --server-option --jobs "
              "--filter --bundle-uri --ref-format")


def _guard_cases():
    """One `case` arm per REF_GUARD entry, setting the pl_g* variables pl_moves_ref reads."""
    arms = []
    for sub, g in REF_GUARD.items():
        vals = {"s": g.get("s", ""), "arg": g.get("arg", ""), "l": " ".join(g.get("l", [])),
                "safe": " ".join(g.get("safe", [])), "argl": " ".join(g.get("argL", [])),
                "sub": " ".join(g.get("sub", [])), "max": str(g.get("maxPos", ""))}
        sets = " ".join(f"pl_g{k}='{v}'" for k, v in vals.items())
        arms.append(f"\t\t{sub}) {sets} ;;")
    return "\n".join(arms)


def _q(words):
    return "'" + " ".join(words) + "'"


def _sq(text):
    """`text` as one single-quoted sh word."""
    return "'" + text.replace("'", "'\\''") + "'"


# rows 3–6 of the guarded shim (README "git shim"): pl_scope decides whether a call acts on the guarded
# repository (apply the policy) or on another one (pass it through untouched). Every function here
# fails CLOSED: a path or repository it cannot work out is treated as the guarded one.
_GUARD_FUNCS = r"""# ── the guarded repository: the policy above applies to PL_GUARD_* and its worktrees only ──
# pl_phys DIR: DIR's absolute physical path (cd -P && pwd -P); fails when DIR is no existing directory.
pl_phys() {
	case $1 in /*) pl_pp=$1 ;; *) pl_pp=./$1 ;; esac
	(CDPATH='' cd -P "$pl_pp" 2>/dev/null && pwd -P)
}

# pl_resolve PATH: pl_phys of PATH's nearest existing ancestor, the missing rest appended as given;
# fails when a missing component is `..` or a dangling symlink (where it lands is not worked out).
pl_resolve() {
	pl_rp=$1 pl_rt=
	while ! pl_rr=$(pl_phys "$pl_rp"); do
		[ -h "$pl_rp" ] && return 1
		pl_rl=${pl_rp##*/}
		case $pl_rl in ..) return 1 ;; '' | .) ;; *) pl_rt=/$pl_rl$pl_rt ;; esac
		pl_rn=${pl_rp%/*}
		[ -n "$pl_rn" ] || pl_rn=/
		[ "x$pl_rn" = "x$pl_rp" ] && return 1
		pl_rp=$pl_rn
	done
	[ "x$pl_rr" = x/ ] && pl_rr=
	printf '%s\n' "$pl_rr$pl_rt"
}

# pl_is_gc DIR: DIR (physical) is the guarded common directory.
pl_is_gc() {
	[ "x$1" = "x$PL_GC" ] || [ "$1" -ef "$PL_GC" ]
}

# pl_under PATH (physical): PATH is the guarded top level or inside it, or the common dir or inside it.
pl_under() {
	pl_up=$1
	while [ -n "$pl_up" ]; do
		case $pl_up in "$PL_GT" | "$PL_GC") return 0 ;; esac
		if [ "$pl_up" -ef "$PL_GT" ] || [ "$pl_up" -ef "$PL_GC" ]; then return 0; fi
		case $pl_up in */?*) pl_up=${pl_up%/*} ;; *) return 1 ;; esac
	done
	return 1
}

# pl_git WORDS ARGS…: the real git run with the global options of the call ARGS… that pick the
# repository, the work tree and config (-C, --git-dir, --work-tree, --bare, -c, --config-env, in
# order), then WORDS split on blanks (no WORD has one: fixed words, a remote or branch name, a config
# key); stdin closed, stderr discarded.
pl_git() {
	pl_gw=$1; shift
	pl_st=
	for pl_a do
		shift
		case $pl_st in
		keep) set -- "$@" "$pl_a"; pl_st=; continue ;;
		skip) pl_st=; continue ;;
		rest) continue ;;
		esac
		case $pl_a in
		-C | --git-dir | --work-tree | -c | --config-env) set -- "$@" "$pl_a"; pl_st=keep ;;
		--git-dir=* | --work-tree=* | --config-env=* | --bare) set -- "$@" "$pl_a" ;;
		-*) if pl_in "$pl_a" "$PL_GARG"; then pl_st=skip; fi ;;
		*) pl_st=rest ;;
		esac
	done
	# shellcheck disable=SC2086
	"$PL_REAL" "$@" $pl_gw </dev/null 2>/dev/null
}

# pl_probe WORDS ARGS…: the directory `pl_git WORDS ARGS…` prints, physically; fails when git fails,
# prints nothing, or names no existing directory.
pl_probe() {
	pl_pd=$(pl_git "$@") || return 1
	[ -n "$pl_pd" ] && pl_phys "$pl_pd"
}

# pl_common ARGS…: the common dir `git ARGS… rev-parse` names, physically.
pl_common() {
	pl_probe "rev-parse --path-format=absolute --git-common-dir" "$@"
}

# pl_lands DIR (relative to the call's -C directory): DIR resolves into the guarded repository — its
# top level, anything inside it, its common dir, or a directory of one of its worktrees — or cannot
# be worked out.
pl_lands() {
	case $1 in /*) pl_lp=$1 ;; *) pl_lp=$pl_base/$1 ;; esac
	pl_lp=$(pl_resolve "$pl_lp") || return 0
	pl_under "$pl_lp" && return 0
	pl_la=$pl_lp
	while [ ! -d "$pl_la" ]; do pl_la=${pl_la%/*}; [ -n "$pl_la" ] || pl_la=/; done
	pl_find_real || return 1
	pl_lc=$(unset $PL_PROBE_CLEAR; pl_common -C "$pl_la") || return 1
	pl_is_gc "$pl_lc"
}

# pl_wtlist: PL_WTS = the guarded top level, common dir and every worktree `worktree list` names
# (physical when they exist), one per line — worked out once per call; fails when git cannot list them.
pl_wtlist() {
	[ -n "$PL_WTS" ] && return 0
	pl_find_real || return 1
	pl_wl=$(unset $PL_PROBE_CLEAR;
		"$PL_REAL" --git-dir="$PL_GC" worktree list --porcelain </dev/null 2>/dev/null) || return 1
	PL_WTS=$PL_GT$pl_nl$PL_GC
	pl_ks=$IFS
	IFS=$pl_nl
	for pl_k in $pl_wl; do
		IFS=$pl_ks
		case $pl_k in worktree\ ?*)
			pl_k=${pl_k#worktree }
			pl_kk=$(pl_phys "$pl_k") || pl_kk=$pl_k
			PL_WTS=$PL_WTS$pl_nl$pl_kk ;;
		esac
	done
	IFS=$pl_ks
}

# pl_contains DIR (relative to the call's -C directory): DIR resolves to the guarded top level, its
# common dir or one of its worktrees, or to an ANCESTOR of one (a work tree there contains them), or
# that cannot be decided.
pl_contains() {
	case $1 in /*) pl_kp=$1 ;; *) pl_kp=$pl_base/$1 ;; esac
	pl_kp=$(pl_resolve "$pl_kp") || return 0
	[ -n "$pl_kp" ] || return 0  # the root directory contains everything
	pl_wtlist || return 0
	pl_ks=$IFS
	IFS=$pl_nl
	for pl_k in $PL_WTS; do
		IFS=$pl_ks
		pl_up=$pl_k
		while [ -n "$pl_up" ]; do
			if [ "x$pl_up" = "x$pl_kp" ] || [ "$pl_up" -ef "$pl_kp" ]; then IFS=$pl_ks; return 0; fi
			case $pl_up in */?*) pl_up=${pl_up%/*} ;; *) break ;; esac
		done
	done
	IFS=$pl_ks
	return 1
}

# pl_reach DIR: a work tree at DIR reaches the guarded repository — pl_lands or pl_contains.
pl_reach() {
	pl_lands "$1" || pl_contains "$1"
}

# pl_walk ARGS…: the global options, as git reads them — pl_base (the directory after every -C),
# pl_gd / pl_wt (the last --git-dir / --work-tree), pl_cwt (the last -c / --config-env core.worktree;
# pl_cwtx when one cannot be read), pl_shf (the last --shallow-file), pl_unk (an option this shim does
# not know), pl_ng (words before the subcommand), PL_SUB.
pl_walk() {
	pl_base=. pl_gd= pl_wt= pl_cwt= pl_cwtx= pl_shf= pl_unk= pl_ng=0 PL_SUB=
	while [ $# -gt 0 ]; do
		if pl_in "$1" "$PL_GARG"; then
			pl_o=$1 pl_v=${2-}
			shift; pl_ng=$((pl_ng + 1))
			if [ $# -gt 0 ]; then shift; pl_ng=$((pl_ng + 1)); fi
			case $pl_o in
			-C) case $pl_v in '') ;; /*) pl_base=$pl_v ;; *) pl_base=$pl_base/$pl_v ;; esac ;;
			--git-dir) pl_gd=$pl_v ;;
			--work-tree) pl_wt=$pl_v ;;
			-c) pl_cfgwt "$pl_v" ;;
			--config-env) pl_cfgwt "$pl_v" env ;;
			--shallow-file) pl_shf=$pl_v; [ -n "$pl_v" ] || pl_unk=1 ;;
			esac
			continue
		fi
		case $1 in
		--git-dir=*) pl_gd=${1#--git-dir=} ;;
		--work-tree=*) pl_wt=${1#--work-tree=} ;;
		--config-env=*) pl_cfgwt "${1#--config-env=}" env ;;
		--?*=*) pl_in "${1%%=*}" "$PL_GEQ" || pl_unk=1 ;;
		-*) pl_in "$1" "$PL_GFLAG" || pl_unk=1 ;;
		*) PL_SUB=$1; return 0 ;;
		esac
		shift; pl_ng=$((pl_ng + 1))
	done
}

# pl_cfgwt KEY[=VALUE] [env]: a -c setting (with `env`, a --config-env one, whose VALUE names the
# variable holding the value) of core.worktree (any case) sets pl_cwt; one that cannot be read, pl_cwtx.
pl_cfgwt() {
	case ${1%%=*} in [cC][oO][rR][eE].[wW][oO][rR][kK][tT][rR][eE][eE]) ;; *) return 0 ;; esac
	case $1 in *=*) ;; *) pl_cwtx=1; return 0 ;; esac
	pl_cwt=${1#*=}
	[ -n "${2-}" ] || return 0
	case $pl_cwt in '' | [0-9]* | *[!A-Za-z0-9_]*) pl_cwtx=1; return 0 ;; esac
	eval "pl_cwt=\${$pl_cwt-}"
}

# pl_pathvar VALUE [BASE…]: the path VALUE (an index, object directory or shallow file git will write)
# lands in the guarded repository — absolute, or relative to the -C directory or to any BASE (git reads
# it from the directory it has moved to: the top level, or the git dir). With no BASE a relative VALUE
# counts as landing (init/clone: where git will be when it reads it is not worked out).
pl_pathvar() {
	pl_pv=$1; shift
	pl_lands "$pl_pv" && return 0
	case $pl_pv in /*) return 1 ;; esac
	[ $# -gt 0 ] || return 0
	for pl_pb do
		pl_lands "$pl_pb/$pl_pv" && return 0
	done
	return 1
}

# pl_pathvars [BASE…]: 0 when GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_SHALLOW_FILE, GIT_GRAFT_FILE,
# --shallow-file or an entry of GIT_ALTERNATE_OBJECT_DIRECTORIES (colon-separated; a C-quoted entry,
# which may hide a colon, is not decoded and counts as landing) lands in the guarded repository.
pl_pathvars() {
	for pl_pn in $PL_GPATH_VARS; do
		eval "pl_pw=\${$pl_pn-}"
		if [ -n "$pl_pw" ] && pl_pathvar "$pl_pw" "$@"; then return 0; fi
	done
	if [ -n "$pl_shf" ] && pl_pathvar "$pl_shf" "$@"; then return 0; fi
	pl_pw=${GIT_ALTERNATE_OBJECT_DIRECTORIES-}
	[ -n "$pl_pw" ] || return 1
	case $pl_pw in \"* | *:\"*) return 0 ;; esac
	pl_ps=$IFS
	IFS=:
	for pl_pe in $pl_pw; do
		IFS=$pl_ps
		if [ -n "$pl_pe" ] && pl_pathvar "$pl_pe" "$@"; then return 0; fi
	done
	IFS=$pl_ps
	return 1
}

# pl_greads FILE: FILE (relative to the -C directory) is a config file the guarded repository reads —
# one `config --list --show-origin` names for it (a quoted origin, or a list git cannot produce, counts),
# or the global file git would read or create ($HOME/.gitconfig, $XDG_CONFIG_HOME/git/config).
pl_greads() {
	case $1 in /*) pl_fp=$1 ;; *) pl_fp=$pl_base/$1 ;; esac
	pl_fp=$(pl_resolve "$pl_fp") || return 0
	pl_find_real || return 0
	pl_fo=$(unset $PL_PROBE_CLEAR;
		"$PL_REAL" --git-dir="$PL_GC" config --list --show-origin --name-only </dev/null 2>/dev/null) || return 0
	pl_fl=
	if [ -n "${HOME-}" ]; then
		pl_fl=$HOME/.gitconfig$pl_nl${XDG_CONFIG_HOME:-$HOME/.config}/git/config
	elif [ -n "${XDG_CONFIG_HOME-}" ]; then
		pl_fl=$XDG_CONFIG_HOME/git/config
	fi
	pl_fs=$IFS
	IFS=$pl_nl
	for pl_fk in $pl_fo; do
		IFS=$pl_fs
		case $pl_fk in
		file:\"*) IFS=$pl_fs; return 0 ;;
		file:*) pl_fk=${pl_fk#file:}; pl_fl=$pl_fl$pl_nl${pl_fk%%"	"*} ;;
		esac
	done
	for pl_fk in $pl_fl; do
		IFS=$pl_fs
		pl_fr=$(pl_resolve "$pl_fk") || pl_fr=$pl_fk
		if [ "x$pl_fr" = "x$pl_fp" ] || [ "$pl_fk" -ef "$pl_fp" ]; then IFS=$pl_fs; return 0; fi
	done
	IFS=$pl_fs
	return 1
}

# pl_cfgpath FILE: the config file FILE lands in the guarded repository or is one it reads.
pl_cfgpath() {
	pl_lands "$1" || pl_greads "$1"
}

# pl_cfgfile ARGS… (the words after `config`): 0 when this config call names a file the guarded
# repository reads — --global, --system (or a unique prefix), or a --file / -f (any prefix of --file;
# `=VALUE`, the next word, or the rest of a short cluster) that lands in it (its config, config.worktree,
# a worktree's) or is one it reads (pl_greads). A -f with no word after it counts (git fails anyway).
pl_cfgfile() {
	pl_fnx=
	for pl_a do
		case $pl_fnx in
		file) pl_fnx=; pl_cfgpath "$pl_a" && return 0; continue ;;
		skip) pl_fnx=; continue ;;
		esac
		case $pl_a in
		--) break ;;
		--?*)
			pl_n=${pl_a%%=*}
			if [ ${#pl_n} -ge 4 ]; then
				case --global in "$pl_n"*) return 0 ;; esac
				case --system in "$pl_n"*) return 0 ;; esac
			fi
			case --file in "$pl_n"*)
				case $pl_a in *=*) pl_cfgpath "${pl_a#*=}" && return 0 ;; *) pl_fnx=file ;; esac ;;
			esac ;;
		-?*)
			pl_c=${pl_a#-}
			while [ -n "$pl_c" ]; do
				pl_ch=${pl_c%"${pl_c#?}"}
				pl_c=${pl_c#?}
				case $pl_ch in f | t)
					if [ -n "$pl_c" ]; then
						[ "x$pl_ch" = xf ] && pl_cfgpath "$pl_c" && return 0
					elif [ "x$pl_ch" = xf ]; then pl_fnx=file
					else pl_fnx=skip; fi
					break ;;
				esac
			done ;;
		esac
	done
	[ "x$pl_fnx" = xfile ] && return 0
	return 1
}

# pl_initclone SUB ARGS…: rows 3–4 — 0 when this init/clone creates or initialises nothing inside the
# guarded repository, nor a repository whose work tree would contain it or one of its worktrees. The
# directory is the last positional (init: else the -C directory; clone: a name git would derive is not
# worked out) and must not land in it or contain it (the -C directory is checked for containing only
# when no git dir is named: then it is only recorded as the work tree); every --separate-git-dir,
# --git-dir and $GIT_DIR must land outside it, and --work-tree / $GIT_WORK_TREE outside and not around it.
pl_initclone() {
	pl_isub=$1; shift
	if [ "x$pl_isub" = xinit ]; then pl_sarg=__INIT_S__ pl_larg='__INIT_L__'
	else pl_sarg=__CLONE_S__ pl_larg='__CLONE_L__'; fi
	pl_npos=0 pl_last=
	while [ $# -gt 0 ]; do
		pl_a=$1; shift
		case $pl_a in
		--)
			for pl_a in "$@"; do pl_npos=$((pl_npos + 1)); pl_last=$pl_a; done
			break ;;
		--?*=*)
			case --separate-git-dir in "${pl_a%%=*}"*) pl_lands "${pl_a#*=}" && return 1 ;; esac ;;
		--?*)
			for pl_l in $pl_larg; do
				case $pl_l in "$pl_a"*)
					case --separate-git-dir in "$pl_a"*) pl_lands "${1-}" && return 1 ;; esac
					if [ $# -gt 0 ]; then shift; fi
					break ;;
				esac
			done ;;
		-?*)
			pl_c=${pl_a#-}
			while [ -n "$pl_c" ]; do
				pl_ch=${pl_c%"${pl_c#?}"}
				pl_c=${pl_c#?}
				case $pl_sarg in *"$pl_ch"*)
					if [ -z "$pl_c" ] && [ $# -gt 0 ]; then shift; fi
					break ;;
				esac
			done ;;
		*) pl_npos=$((pl_npos + 1)); pl_last=$pl_a ;;
		esac
	done
	if [ "x$pl_isub" = xclone ] && [ "$pl_npos" -lt 2 ]; then return 1; fi
	if [ "$pl_npos" -gt 0 ]; then
		pl_reach "$pl_last" && return 1
	else
		pl_lands . && return 1
		# with no git dir named, the -C / current directory is where the repository is made
		if [ -z "$pl_gd" ] && [ -z "${GIT_DIR-}" ] && pl_contains .; then return 1; fi
	fi
	for pl_v in "$pl_gd" "${GIT_DIR-}"; do
		if [ -n "$pl_v" ] && pl_lands "$pl_v"; then return 1; fi
	done
	for pl_v in "$pl_wt" "${GIT_WORK_TREE-}"; do
		if [ -n "$pl_v" ] && pl_reach "$pl_v"; then return 1; fi
	done
	return 0
}

# pl_wtadd ARGS… (the words after `worktree`): 0 when this is `worktree add` and one of its words
# (the new worktree's path among them) lands in the guarded repository or contains it (pl_reach).
pl_wtadd() {
	[ "x${1-}" = xadd ] || return 1
	shift
	pl_dd=
	for pl_a do
		if [ -z "$pl_dd" ]; then
			case $pl_a in --) pl_dd=1; continue ;; -*) continue ;; esac
		fi
		pl_reach "$pl_a" && return 0
	done
	return 1
}

# pl_path PATH SHOWN: deny when the local push destination PATH (relative to the -C directory; `~`
# as git expands it) is, or leads git to, a repository in the guarded one — PATH, PATH/.git,
# PATH.git and PATH.git/.git, each also read as a git dir or gitfile, the way git enters it.
pl_path() {
	pl_p=$1
	case $pl_p in
	'~' | '~/'*) [ -n "${HOME-}" ] || pl_deny "cannot tell where push destination $2 leads (no \$HOME)"
		pl_p=$HOME${pl_p#'~'} ;;
	'~'*) pl_deny "cannot tell where push destination $2 leads (another user's ~)" ;;
	'') pl_deny "cannot tell where an empty push destination leads" ;;
	esac
	case $pl_p in /*) ;; *) pl_p=$pl_base/$pl_p ;; esac
	for pl_c in "$pl_p" "$pl_p/.git" "$pl_p.git" "$pl_p.git/.git"; do
		pl_lands "$pl_c" && pl_deny "$2 is the guarded repository: a $PL_SUB there writes its refs"
		if [ -e "$pl_c" ] && [ ! -d "$pl_c" ]; then
			pl_pc=$(unset $PL_PROBE_CLEAR; pl_common --git-dir="$pl_c") &&
				pl_is_gc "$pl_pc" && pl_deny "$2 is the guarded repository: a $PL_SUB there writes its refs"
		fi
	done
	return 0
}

# pl_url URL: deny when the push destination URL (after git's rewrites) is local and lands in the
# guarded repository, or looks local and cannot be resolved; a URL that is clearly remote passes.
pl_url() {
	pl_u=$1
	case $pl_u in
	ssh://* | git+ssh://* | ssh+git://* | git://* | http://* | https://* | ftp://* | ftps://*) return 0 ;;
	file://*)
		pl_u=${pl_u#file://}
		case $pl_u in
		*%*) pl_deny "cannot tell where push destination $1 leads (percent-encoded)" ;;
		/*) ;;
		*) pl_deny "cannot tell where push destination $1 leads" ;;
		esac ;;
	*::* | *://*) pl_deny "cannot tell where push destination $1 leads (a transport this shim does not know)" ;;
	*:*) case ${pl_u%%:*} in */*) ;; *) return 0 ;; esac ;;  # [user@]host:path, git's scp form: ssh
	esac
	pl_path "$pl_u" "$1"
}

# pl_lines TEXT FUNC: FUNC LINE for every line of TEXT.
pl_lines() {
	pl_ls=$IFS
	IFS=$pl_nl
	for pl_ln in $1; do IFS=$pl_ls; "$2" "$pl_ln"; done
	IFS=$pl_ls
}

# pl_rewrites URL: pl_url on URL as written and on URL under every url.<base>.insteadOf /
# pushInsteadOf rule (pl_rw, as `config --get-regexp` prints them) whose value starts it.
pl_rewrites() {
	pl_url "$1"
	pl_ws=$IFS
	IFS=$pl_nl
	for pl_w in $pl_rw; do
		IFS=$pl_ws
		case $pl_w in
		*.pushinsteadof\ *) pl_rb=${pl_w%%.pushinsteadof *} pl_rv=${pl_w#*.pushinsteadof } ;;
		*.insteadof\ *) pl_rb=${pl_w%%.insteadof *} pl_rv=${pl_w#*.insteadof } ;;
		*) continue ;;
		esac
		case $1 in "$pl_rv"*) pl_url "${pl_rb#url.}${1#"$pl_rv"}" ;; esac
	done
	IFS=$pl_ws
}

# pl_dest DEST ARGS…: deny when the push destination DEST reaches the guarded repository. As a
# remote's name: every push URL git reports for it (`remote get-url --push --all`: pushurl, else
# url, rewrites applied), every remote.DEST.pushurl and .url value `config --get-all` reads (which
# also sees -c), and its receivepack must be unset. As a URL or path: DEST itself. Each URL read from
# config, and DEST, is checked as written and under every insteadOf / pushInsteadOf rule matching it.
pl_dest() {
	pl_dn=$1; shift
	pl_rw=$(pl_git 'config --get-regexp ^url\..*\.(push)?insteadof$' "$@") || pl_rw=
	case $pl_dn in
	'' | *' '* | *'	'* | *"$pl_nl"*) ;;
	*)
		pl_ul=$(pl_git "remote get-url --push --all -- $pl_dn" "$@") || pl_ul=
		pl_lines "$pl_ul" pl_url
		for pl_k in pushurl url; do
			pl_ul=$(pl_git "config --get-all remote.$pl_dn.$pl_k" "$@") || pl_ul=
			pl_lines "$pl_ul" pl_rewrites
		done
		pl_ul=$(pl_git "config --get-all remote.$pl_dn.receivepack" "$@") || pl_ul=
		[ -n "$pl_ul" ] && pl_deny "remote $pl_dn sets receivepack: cannot tell where this $PL_SUB leads"
		;;
	esac
	pl_rewrites "$pl_dn"
}

# pl_push ARGS…: a push / send-pack / receive-pack on another repository is denied when its
# destination is in the guarded repository (or cannot be told): the first positional, every --repo,
# and, for a push with neither, the remote git pushes to by default. --receive-pack / --exec name a
# program that decides where the push goes, so they are denied.
pl_push() {
	pl_i=0 pl_dst= pl_has= pl_repo= pl_nx= pl_dd=
	for pl_a do
		pl_i=$((pl_i + 1))
		[ "$pl_i" -le $((pl_ng + 1)) ] && continue
		if [ -n "$pl_nx" ]; then
			if [ "x$pl_nx" = xrepo ]; then pl_repo="$pl_repo$pl_a$pl_nl"; fi
			pl_nx=; continue
		fi
		if [ -z "$pl_dd" ]; then
			case $pl_a in
			--) pl_dd=1; continue ;;
			--?*)
				pl_n=${pl_a%%=*}
				for pl_l in --receive-pack --exec; do
					case $pl_l in "$pl_n"*) pl_deny "git $PL_SUB $pl_n runs a program that decides where the push goes" ;; esac
				done
				case --repo in "$pl_n"*)
					case $pl_a in *=*) pl_repo="$pl_repo${pl_a#*=}$pl_nl" ;; *) pl_nx=repo ;; esac
					continue ;;
				esac
				case $pl_a in *=*) ;; *)
					for pl_l in --push-option --recurse-submodules --remote; do
						case $pl_l in "$pl_n"*) pl_nx=skip; break ;; esac
					done ;;
				esac
				continue ;;
			-?*)
				case $pl_a in -*o) pl_nx=skip ;; esac
				continue ;;
			esac
		fi
		if [ -z "$pl_has" ]; then pl_has=1 pl_dst=$pl_a; fi
	done
	if [ "x$PL_SUB" = xreceive-pack ]; then
		[ -n "$pl_has" ] && pl_path "$pl_dst" "$pl_dst"
		return 0
	fi
	[ -n "$pl_has" ] && pl_dest "$pl_dst" "$@"
	pl_sifs=$IFS
	IFS=$pl_nl
	for pl_l in $pl_repo; do IFS=$pl_sifs; pl_dest "$pl_l" "$@"; done
	IFS=$pl_sifs
	if [ -z "$pl_has" ] && [ -z "$pl_repo" ] && [ "x$PL_SUB" = xpush ]; then
		pl_br=$(pl_git "symbolic-ref -q --short HEAD" "$@") || pl_br=
		pl_dr=
		[ -n "$pl_br" ] && pl_dr=$(pl_git "config --get branch.$pl_br.pushremote" "$@")
		[ -n "$pl_dr" ] || pl_dr=$(pl_git "config --get remote.pushdefault" "$@")
		[ -n "$pl_dr" ] || { [ -n "$pl_br" ] && pl_dr=$(pl_git "config --get branch.$pl_br.remote" "$@"); }
		[ -n "$pl_dr" ] || pl_dr=origin
		pl_dest "$pl_dr" "$@"
	fi
	return 0
}

# pl_builtin NAME: NAME is a git builtin — one of git 2.50's (PL_GBUILTIN) and, when the real git prints
# a list for `--list-cmds=builtins` (no config read, no program run), one of the installed git's too. git
# runs a builtin before any `git-NAME` program or alias.NAME (an alias cannot override a builtin), so NAME
# is what runs; any other NAME is an alias (plain, `!shell`, or of a builtin) or an external `git-NAME`
# whose `git` is the real git (git puts its exec-path first on PATH), and gets the policy.
pl_builtin() {
	pl_in "$1" "$PL_GBUILTIN" || return 1
	pl_find_real || return 1
	pl_bl=$(unset $PL_PROBE_CLEAR; "$PL_REAL" --list-cmds=builtins </dev/null 2>/dev/null) || pl_bl=
	[ -n "$pl_bl" ] || return 0  # no list from the installed git: git 2.50's decides
	case $pl_nl$pl_bl$pl_nl in *"$pl_nl$1$pl_nl"*) return 0 ;; esac
	return 1
}

# pl_nhelp NAME: NAME is a helper a NESTED call may run although it is no builtin — a remote helper or
# `http-fetch` (PL_NHELP), or a credential helper `credential-<name>` (PL_NCRED; a name without `/`).
pl_nhelp() {
	case $1 in "$PL_NCRED"*/*) return 1 ;; "$PL_NCRED"?*) return 0 ;; esac
	pl_in "$1" "$PL_NHELP"
}

# pl_scope ARGS…: 0 = the call acts on another repository (rows 4–5: pass it through untouched),
# 1 = apply the policy (rows 3 and 6, and the no-subcommand pass-throughs, which the policy passes).
# A call is on the guarded repository when its git dir OR its work tree (--work-tree, $GIT_WORK_TREE,
# -c core.worktree, the top level git names) lands there or contains it or one of its worktrees, or a
# `worktree add` puts a worktree there. A subcommand that is not a git builtin (pl_builtin: an alias or
# an external `git-NAME`) gets the policy wherever it runs.
# A push, send-pack or receive-pack into the guarded repository from another one is denied here.
# A NESTED call (one a mirrored git started: PL_NESTED) may also be a helper (pl_nhelp: a remote or
# credential helper, http-fetch), which is no builtin; an upload-pack / upload-archive is scoped by the
# repository it serves (pl_serve), and an object-store-only child (PL_NNOWT, or a pl_nhelp helper) by its
# git dir, not by the work tree git would assume.
pl_scope() {
	PL_GC=$(pl_phys "$PL_GUARD_COMMON") || PL_GC=$PL_GUARD_COMMON
	PL_GT=$(pl_phys "$PL_GUARD_TOP") || PL_GT=$PL_GUARD_TOP
	pl_nl='
' PL_WTS=
	pl_walk "$@"
	[ -n "$PL_SUB" ] || return 1
	[ -n "$pl_unk" ] && return 1
	if ! pl_builtin "$PL_SUB"; then
		[ -n "$PL_NESTED" ] && pl_nhelp "$PL_SUB" || return 1
	fi
	case $PL_NESTED$PL_SUB in
	1upload-pack | 1upload-archive) shift $((pl_ng + 1)); pl_serve "$@"; return 0 ;;
	esac
	case $PL_SUB in
	init | clone) pl_pathvars && return 1; shift "$pl_ng"; pl_initclone "$@"; return ;;
	esac
	pl_find_real || return 1
	pl_sc=$(pl_common "$@") || return 1
	pl_is_gc "$pl_sc" && return 1
	[ -n "$pl_cwtx" ] && return 1
	for pl_v in "$pl_wt" "${GIT_WORK_TREE-}" "$pl_cwt"; do
		if [ -n "$pl_v" ] && pl_reach "$pl_v"; then return 1; fi
	done
	pl_nowt=
	if [ -n "$PL_NESTED" ] && { pl_in "$PL_SUB" "$PL_NNOWT" || pl_nhelp "$PL_SUB"; }; then pl_nowt=1; fi
	if pl_tl=$(pl_probe "rev-parse --path-format=absolute --show-toplevel" "$@"); then
		[ -z "$pl_nowt" ] && pl_reach "$pl_tl" && return 1
	else
		pl_tl=
	fi
	# the index, object store, alternates, shallow and graft file git will write (a relative one from the
	# -C directory, the top level or the git dir: git moves to one of them before it reads it)
	if [ -n "${GIT_INDEX_FILE-}${GIT_OBJECT_DIRECTORY-}${GIT_SHALLOW_FILE-}${GIT_GRAFT_FILE-}${GIT_ALTERNATE_OBJECT_DIRECTORIES-}$pl_shf" ]; then
		pl_gdir=$(pl_probe "rev-parse --absolute-git-dir" "$@") || pl_gdir=
		pl_pathvars ${pl_tl:+"$pl_tl"} ${pl_gdir:+"$pl_gdir"} && return 1
	fi
	case $PL_SUB in
	config) shift $((pl_ng + 1)); pl_cfgfile "$@" && return 1 ;;
	worktree) shift $((pl_ng + 1)); pl_wtadd "$@" && return 1 ;;
	push | send-pack | receive-pack) pl_push "$@" ;;
	esac
	return 0
}

"""

# The exec-path mirror, in sh (the shim builds and checks it itself: nothing but sh, ls, ln, mkdir, mv, rm
# and touch, and only when the mirror has to be rebuilt).
_MIRROR_FUNCS = r"""# ── the exec-path mirror: PL_MIRROR, git's exec-path with `git` (and git-upload-pack,
# git-receive-pack, git-upload-archive) this shim, and no other dashed builtin ──
# pl_mstamp XP: the real git and the exec-path XP by inode and path — `ls -Lid`, its words joined by one
# space; fails when either cannot be listed.
pl_mstamp() {
	pl_ms=$(ls -Lid "$PL_REAL" "$1" 2>/dev/null) || return 1
	set -- $pl_ms
	printf '%s\n' "$*"
}

# pl_mcheck: the mirror is current for PL_REAL — its record names PL_REAL, the exec-path it mirrors still
# exists, both still have the recorded mtimes (`-nt` / `-ot` against the files `touch -r` stamped) and
# inodes, and its `git` is this shim. Runs `ls` once; never runs git.
pl_mcheck() {
	pl_mr=$PL_MIRROR/__RECORD__
	[ -f "$pl_mr" ] && [ "$PL_MIRROR/git" -ef "${PL_MIRROR%/*}/git" ] || return 1
	{ IFS= read -r pl_mg && IFS= read -r pl_mx && IFS= read -r pl_mv && IFS= read -r pl_mt; } <"$pl_mr" || return 1
	[ "x$pl_mg" = "x$PL_REAL" ] && [ -d "$pl_mx" ] || return 1
	if [ "$PL_REAL" -nt "$PL_MIRROR/__GITM__" ] || [ "$PL_REAL" -ot "$PL_MIRROR/__GITM__" ]; then return 1; fi
	if [ "$pl_mx" -nt "$PL_MIRROR/__XPM__" ] || [ "$pl_mx" -ot "$PL_MIRROR/__XPM__" ]; then return 1; fi
	[ "x$(pl_mstamp "$pl_mx")" = "x$pl_mt" ]
}

# pl_mbuild: (re)build the mirror from PL_REAL's `--exec-path` (the git's own exec-path: GIT_EXEC_PATH
# is not set here) — in a new directory beside it, then moved into place. An entry named `git`, or a
# dashed name that is the git binary (-ef its exec-path's `git` or PL_REAL) or a builtin's name, is left
# out, except git-upload-pack / git-receive-pack / git-upload-archive (this shim) and git-remote-*;
# everything else (scripts, their libraries, mergetools/, other programs) is a symlink to the real entry.
pl_mbuild() {
	pl_xp=$("$PL_REAL" --exec-path </dev/null 2>/dev/null) || return 1
	pl_mv=$("$PL_REAL" --version </dev/null 2>/dev/null) || return 1
	case $pl_xp in /*) ;; *) return 1 ;; esac
	case $PL_REAL$pl_xp$pl_mv in *'
'*) return 1 ;; esac
	[ -d "$pl_xp" ] && [ -n "$pl_mv" ] || return 1
	pl_mt=$(pl_mstamp "$pl_xp") || return 1
	pl_mn=$PL_MIRROR.new.$$
	rm -rf "$pl_mn" && mkdir "$pl_mn" && ln -s ../git "$pl_mn/git" || { rm -rf "$pl_mn"; return 1; }
	set +f
	for pl_e in "$pl_xp"/*; do
		set -f
		[ -e "$pl_e" ] || [ -h "$pl_e" ] || continue
		pl_n=${pl_e##*/}
		case $pl_n in
		git) continue ;;
		git-upload-pack | git-receive-pack | git-upload-archive) pl_t=../git ;;
		git-remote-*) pl_t=$pl_e ;;
		git-*)
			if [ "$pl_e" -ef "$pl_xp/git" ] || [ "$pl_e" -ef "$PL_REAL" ] || pl_in "${pl_n#git-}" "$PL_GBUILTIN"; then
				continue
			fi
			pl_t=$pl_e ;;
		*) pl_t=$pl_e ;;
		esac
		ln -s "$pl_t" "$pl_mn/$pl_n" || { set -f; rm -rf "$pl_mn"; return 1; }
	done
	set -f
	{ printf '%s\n' "$PL_REAL" "$pl_xp" "$pl_mv" "$pl_mt" >"$pl_mn/__RECORD__" &&
		touch -r "$PL_REAL" "$pl_mn/__GITM__" && touch -r "$pl_xp" "$pl_mn/__XPM__"; } 2>/dev/null ||
		{ rm -rf "$pl_mn"; return 1; }
	pl_mo=$PL_MIRROR.old.$$
	rm -rf "$pl_mo"
	if [ -e "$PL_MIRROR" ] || [ -h "$PL_MIRROR" ]; then mv -f "$PL_MIRROR" "$pl_mo" 2>/dev/null; fi
	mv "$pl_mn" "$PL_MIRROR" 2>/dev/null
	# another shim may have moved its own build in first: then ours is not used (nor left inside it)
	rm -rf "$pl_mo" "$pl_mn" "$PL_MIRROR/${pl_mn##*/}"
	return 0
}

# pl_mirror: 0 when the mirror is current for the real git — as it is, or after one rebuild; else
# PL_MWHY says why.
pl_mirror() {
	pl_find_real || { PL_MWHY="no real git found on PATH"; return 1; }
	pl_mcheck && return 0
	pl_mbuild && pl_mcheck && return 0
	PL_MWHY="the exec-path mirror $PL_MIRROR is stale or missing and could not be rebuilt for $PL_REAL"
	return 1
}

# pl_serve ARGS… (the words after a NESTED upload-pack / upload-archive): the directory it serves (its
# last word, from the -C directory; `~/` as git expands it) is looked up the way git enters it — DIR/.git,
# DIR, DIR.git/.git, DIR.git, the first git dir or gitfile — and PL_SERVE=1 when that is the guarded
# repository (it then runs with git's own exec-path: it only reads, and its children must reach the
# guarded repository's objects). Anything else, or a directory that cannot be told, is served with the
# mirror, so its children still meet this shim.
pl_serve() {
	pl_sd=
	for pl_a do pl_sd=$pl_a; done
	case $pl_sd in
	'' | -*) return 0 ;;
	'~' | '~/'*) [ -n "${HOME-}" ] || return 0; pl_sd=$HOME${pl_sd#'~'} ;;
	'~'*) return 0 ;;
	esac
	case $pl_sd in /*) ;; *) pl_sd=$pl_base/$pl_sd ;; esac
	pl_find_real || return 0
	for pl_c in "$pl_sd/.git" "$pl_sd" "$pl_sd.git/.git" "$pl_sd.git"; do
		[ -f "$pl_c" ] || [ -d "$pl_c" ] || continue
		pl_sc=$(unset $PL_PROBE_CLEAR; pl_common --git-dir="$pl_c") || continue
		pl_is_gc "$pl_sc" && PL_SERVE=1
		return 0
	done
	return 0
}

# pl_cfgp_ok VALUE: a nested call's GIT_CONFIG_PARAMETERS is in the form git writes for its children
# (PL_CFGP_RE: single-quoted words only, so splitting it with eval expands nothing) and none of its keys
# is one a -c may not set (pl_risky). Otherwise the variable stays and row 1 refuses the call.
pl_cfgp_ok() {
	case $1 in *'
'*) return 1 ;; esac
	printf '%s\n' "$1" | grep -Eq -- "$PL_CFGP_RE" || return 1
	eval "set -- $1"
	for pl_w do
		pl_risky "${pl_w%%=*}" && return 1
	done
	return 0
}

"""


def mirror_path(shim_dir):
    """The exec-path mirror's path for a shim written in `shim_dir` (physical, as the script records it)."""
    return os.path.join(os.path.realpath(shim_dir), MIRROR_DIR)


def _guard_parts(guard, mirror=None):
    """The fragments script() splices in for a guarded shim; all empty for `guard=None`, so the
    guard-everything text is byte-for-byte the pre-guard shim's. `mirror` (the exec-path mirror's path)
    adds the mirror: without it a guarded shim runs every call with git's own exec-path."""
    if guard is None:
        return "", "", "", "", "", ""
    common, top = guard
    head = (f"PL_GUARD_COMMON={_sq(common)}\nPL_GUARD_TOP={_sq(top)}\nPL_NOPOLICY=\n"
            f"PL_GARG={_q(GUARD_OPT_WITH_ARG)}\nPL_GEQ={_q(GUARD_OPT_EQ)}\nPL_GFLAG={_q(GUARD_OPT_FLAGS)}\n"
            f"PL_GPATH_VARS={_q(GUARD_PATH_VARS)}\nPL_PROBE_CLEAR={_q(PROBE_CLEAR)}\n"
            f"PL_GBUILTIN={_q(GUARD_BUILTINS)}\n"
            f"PL_MIRROR={_sq(mirror or '')}\nPL_NHELP={_q(NESTED_HELPERS)}\nPL_NNOWT={_q(NESTED_NO_WORKTREE)}\n"
            f"PL_NCRED={_sq(NESTED_CRED_PREFIX)}\n"
            f"PL_CFGP_RE={_sq(CFGP_RE)}\nPL_NESTED= PL_CFGP= PL_SERVE= PL_XP=\n")
    stop = '\t[ -n "$PL_NOPOLICY" ] && return 0  # rows 1–2 checked; the policy is not wanted here\n'
    funcs = (_GUARD_FUNCS.replace("__INIT_S__", INIT_ARGS[0]).replace("__INIT_L__", INIT_ARGS[1])
             .replace("__CLONE_S__", CLONE_ARGS[0]).replace("__CLONE_L__", CLONE_ARGS[1]))
    main = "".join(f'[ -n "${{{v}-}}" ] && pl_deny "{v} moves where git reads and writes refs"\n'
                   for v in GUARD_VARS) + (
        "PL_NOPOLICY=1\n"
        'pl_check "$@"  # rows 1–2: the refused environment and -c / --config-env / --exec-path= checks\n'
        "PL_NOPOLICY=\n")
    # the depth guard, before anything runs: a call already DEPTH_LIMIT deep (or with a value the shim never
    # writes) is refused; the probes below run with the variable at the limit, the real git with depth + 1.
    depth = (f"case ${{{DEPTH_VAR}-}} in\n"
             "'') pl_dp=0 ;;\n"
             f"[0-9] | [1-9][0-9]) pl_dp=${DEPTH_VAR} ;;\n"
             f"*) pl_dp={DEPTH_LIMIT} ;;\n"
             "esac\n"
             f"if [ \"$pl_dp\" -ge {DEPTH_LIMIT} ]; then\n"
             f"\tprintf 'pi-lead git shim: {DEPTH_VAR}=%s — {DEPTH_LIMIT} git shim calls are already "
             "nested here: a loop (a `git` on PATH that runs this shim again?); refusing without starting anything\\n' "
             f"\"${{{DEPTH_VAR}-}}\" >&2\n"
             f"\texit {EXIT_DENIED}\n"
             "fi\n"
             f"{DEPTH_VAR}={DEPTH_LIMIT}\nexport {DEPTH_VAR}\n")
    deeper = f"{DEPTH_VAR}=$((pl_dp + 1))\n"
    if not mirror:
        return head, depth, "", stop, funcs, main + 'pl_scope "$@" && PL_NOPOLICY=1\n' + deeper
    funcs += (_MIRROR_FUNCS.replace("__RECORD__", MIRROR_RECORD).replace("__GITM__", MIRROR_GIT_MTIME)
              .replace("__XPM__", MIRROR_XP_MTIME))
    # before the command line is read: a call a mirrored git started (GIT_EXEC_PATH is the mirror) is
    # NESTED — the variable is taken off (row 1 refuses any other value), and so is a GIT_CONFIG_PARAMETERS
    # in git's own form whose keys a -c could set (it is put back for the probes and the real git); the
    # mirror's git-upload-pack / git-receive-pack / git-upload-archive are `git <name>`.
    pre = depth + ('if [ -n "$PL_MIRROR" ] && [ "x${GIT_EXEC_PATH-}" = "x$PL_MIRROR" ]; then\n'
           "\tPL_NESTED=1\n\tunset GIT_EXEC_PATH\n"
           '\tif [ -n "${GIT_CONFIG_PARAMETERS-}" ] && pl_cfgp_ok "$GIT_CONFIG_PARAMETERS"; then\n'
           "\t\tPL_CFGP=$GIT_CONFIG_PARAMETERS\n\t\tunset GIT_CONFIG_PARAMETERS\n\tfi\nfi\n"
           "pl_b=${0##*/}\n"
           'case $pl_b in git-upload-pack | git-receive-pack | git-upload-archive) set -- "${pl_b#git-}" "$@" ;; esac\n')
    fskip = '\t\t[ -n "$PL_MIRROR" ] && [ "x$pl_d" = "x$PL_MIRROR" ] && continue\n'
    # the exec-path: a pass-through call (another repository) and a call with no subcommand run with the
    # mirror, when it is current or can be rebuilt; a pass-through call whose mirror cannot be had gets the
    # policy (fail closed), with a line saying why; a policy call, and a nested upload-pack / upload-archive
    # serving the guarded repository, run with git's own exec-path.
    main += ('if [ -n "$PL_CFGP" ]; then GIT_CONFIG_PARAMETERS=$PL_CFGP; export GIT_CONFIG_PARAMETERS; fi\n'
             'if pl_scope "$@"; then\n'
             '\tif [ -n "$PL_SERVE" ]; then PL_NOPOLICY=1\n'
             '\telif pl_mirror; then PL_NOPOLICY=1 PL_XP=$PL_MIRROR\n'
             "\telse printf 'pi-lead git shim: %s; this call gets the guarded repository'\\''s policy\\n' \"$PL_MWHY\" >&2\n"
             "\tfi\n"
             'elif [ -z "$PL_SUB" ]; then\n'
             '\tif pl_mirror; then PL_XP=$PL_MIRROR\n'
             "\telse printf 'pi-lead git shim: %s; running with git'\\''s own exec-path\\n' \"$PL_MWHY\" >&2\n"
             "\tfi\n"
             "fi\n"
             'if [ -n "$PL_XP" ]; then GIT_EXEC_PATH=$PL_XP; export GIT_EXEC_PATH; fi\n' + deeper)
    return head, pre, fskip, stop, funcs, main


def script(guard=None, mirror=None):
    """The shim's full text (deterministic: same policy, guard and mirror → same bytes). `guard` is None
    (guard every repository — the text is exactly the pre-guard shim's) or (common dir, top level), two
    absolute physical paths: the policy then applies to that repository and its worktrees only. `mirror`
    (a guarded shim only) is the exec-path mirror's path (mirror_path): calls on other repositories then
    run with it as GIT_EXEC_PATH, so every git they start meets this shim again."""
    allowed = ", ".join(GIT_ALLOW)
    g_head, g_pre, g_fskip, g_stop, g_funcs, g_main = _guard_parts(guard, mirror)
    return f"""#!/bin/sh
# {MARKER} — pi-lead's process-level git fence, written per launch by `pilead spawn` (lib/pilead/shim.py).
# First on the executor's PATH: any program that runs `git` by name lands here. Policy = the text
# fence's (extensions/bash-fence.ts); allowed calls exec the real git with argv untouched, denied
# ones print one refusal line to stderr and exit {EXIT_DENIED}. POSIX sh only.
set -f
PL_ALLOW={_q(GIT_ALLOW)}
PL_NAMED={_q(NAMED_DENY)}
PL_WITH_ARG={_q(GIT_OPT_WITH_ARG)}
PL_CMD_VARS={_q(CMD_VARS)}
PL_CFG_VARS={_q(CFG_VARS)}
PL_KEY_RE='{DANGEROUS_KEY}'
PL_GIT_WORD='{GIT_WORD}'
{g_head}
pl_deny() {{
	pl_t=$(printf '%s' "$PL_CMD" | tr '\\n\\t' '  ')
	if [ ${{#pl_t}} -gt 200 ]; then pl_t="$(printf '%.199s' "$pl_t")…"; fi
	printf '%s (blocked by git shim: %s%s)\\n' '{REFUSAL}' "$pl_t" "${{1:+ — $1}}" >&2
	exit {EXIT_DENIED}
}}

# pl_in WORD LIST: WORD is one of LIST's space-separated words (exact match).
pl_in() {{
	for pl_x in $2; do [ "x$pl_x" = "x$1" ] && return 0; done
	return 1
}}

# A -c / --config-env key whose value git runs as a command or loads as config.
pl_risky() {{
	case $1 in *'$'* | *'`'*) return 0 ;; esac
	printf '%s\\n' "$1" | grep -Eiq -- "$PL_KEY_RE"
}}

pl_env_check() {{
	for pl_v in $PL_CFG_VARS; do
		eval "pl_val=\\${{$pl_v-}}"
		[ -n "$pl_val" ] && pl_deny "$pl_v can make git load other config or code"
	done
	for pl_v in $PL_CMD_VARS; do
		eval "pl_val=\\${{$pl_v-}}"
		[ -n "$pl_val" ] && printf '%s\\n' "$pl_val" | grep -Eq -- "$PL_GIT_WORD" &&
			pl_deny "$pl_v runs a command that calls git, which would bypass this shim"
	done
	return 0
}}

# pl_moves_ref ARGS…: bash-fence movesRef — short flags pl_gs in clusters (pl_garg letters take the
# value), long flags pl_gl by any unique prefix (pl_gsafe exempt, pl_gargl take the next word), first
# positional in pl_gsub, more than pl_gmax positionals.
pl_moves_ref() {{
	pl_npos=0 pl_pos0=
	while [ $# -gt 0 ]; do
		pl_a=$1; shift
		case $pl_a in
		--) break ;;
		--?*)
			pl_n=${{pl_a%%=*}}
			if ! pl_in "$pl_n" "$pl_gsafe"; then
				for pl_l in $pl_gl; do case $pl_l in "$pl_n"*) return 0 ;; esac; done
			fi
			if pl_in "$pl_a" "$pl_gargl" && [ $# -gt 0 ]; then shift; fi
			;;
		-?*)
			pl_c=${{pl_a#-}}
			while [ -n "$pl_c" ]; do
				pl_ch=${{pl_c%"${{pl_c#?}}"}}
				pl_c=${{pl_c#?}}
				case $pl_gs in *"$pl_ch"*) return 0 ;; esac
				case $pl_garg in *"$pl_ch"*)
					if [ -z "$pl_c" ] && [ $# -gt 0 ]; then shift; fi
					break ;;
				esac
			done
			;;
		*) [ $pl_npos -eq 0 ] && pl_pos0=$pl_a; pl_npos=$((pl_npos + 1)) ;;
		esac
	done
	for pl_a in "$@"; do [ $pl_npos -eq 0 ] && pl_pos0=$pl_a; pl_npos=$((pl_npos + 1)); done
	[ -n "$pl_gsub" ] && [ $pl_npos -gt 0 ] && pl_in "$pl_pos0" "$pl_gsub" && return 0
	[ -n "$pl_gmax" ] && [ $pl_npos -gt "$pl_gmax" ] && return 0
	return 1
}}

pl_check() {{
	while [ $# -gt 0 ]; do
		if pl_in "$1" "$PL_WITH_ARG"; then
			pl_o=$1; shift
			pl_key=${{1-}}; pl_key=${{pl_key%%=*}}
			[ $# -gt 0 ] && shift
			if [ "x$pl_o" = x-c ] || [ "x$pl_o" = x--config-env ]; then
				pl_risky "$pl_key" && pl_deny "git $pl_o $pl_key can run arbitrary commands"
			fi
			continue
		fi
		case $1 in
		--exec-path=*) pl_deny "git --exec-path= can make git load other config or code" ;;
		--config-env=*)
			pl_key=${{1#--config-env=}}
			pl_risky "${{pl_key%%=*}}" && pl_deny "git --config-env can run arbitrary commands" ;;
		-*) ;;
		*) break ;;
		esac
		shift
	done
	[ $# -eq 0 ] && return 0  # no subcommand: `git`, `git --version`, `git --help`, `git -h`
{g_stop}	pl_sub=$1; shift
	if ! pl_in "$pl_sub" "$PL_ALLOW"; then
		pl_in "$pl_sub" "$PL_NAMED" && pl_deny
		pl_deny "\\"$pl_sub\\" is not an allowed git subcommand (allowed: {allowed})"
	fi
	pl_gs= pl_garg= pl_gl= pl_gsafe= pl_gargl= pl_gsub= pl_gmax= pl_guarded=1
	case $pl_sub in
{_guard_cases()}
		*) pl_guarded= ;;
	esac
	if [ -n "$pl_guarded" ] && pl_moves_ref "$@"; then pl_deny "{REF_REASON}"; fi
	case $pl_sub in
	reset)  # git accepts unique prefixes of long options (--har, --me, --ke); after `--` come paths
		for pl_a in "$@"; do
			[ "x$pl_a" = x-- ] && break
			case $pl_a in --?*)
				for pl_o in --hard --merge --keep; do case $pl_o in "$pl_a"*) pl_deny ;; esac; done ;;
			esac
		done ;;
	config)
		pl_reads= pl_bad=
		for pl_a in "$@"; do
			case $pl_a in
			--get | --get-all | --get-regexp | -l | --list | get | list) pl_reads=1 ;;
			*) printf '%s\\n' "$pl_a" | grep -Eiq -- "$PL_KEY_RE" && pl_bad=1 ;;
			esac
		done
		[ -z "$pl_reads" ] && [ -n "$pl_bad" ] && pl_deny "that git config key can run arbitrary commands" ;;
	esac
	return 0
}}

{g_funcs}pl_find_real() {{
	pl_self=${{PILEAD_SHIM_DIR%/}}
	case $0 in */*) pl_me=$0 ;; *) pl_me= ;; esac
	pl_ifs=$IFS; IFS=:
	for pl_d in $PATH; do
		IFS=$pl_ifs
		case $pl_d in /*) ;; *) continue ;; esac  # absolute entries only: never a repo's ./git
		pl_d=${{pl_d%/}}
		[ -n "$pl_self" ] && [ "x$pl_d" = "x$pl_self" ] && continue
{g_fskip}		[ -f "$pl_d/git" ] && [ -x "$pl_d/git" ] || continue
		[ -n "$pl_me" ] && [ "$pl_d/git" -ef "$pl_me" ] && continue
		PL_REAL=$pl_d/git
		return 0
	done
	IFS=$pl_ifs
	return 1
}}

{g_pre}PL_CMD=git
for pl_a in "$@"; do PL_CMD="$PL_CMD $pl_a"; done
pl_env_check
{g_main}pl_check "$@"
if ! pl_find_real; then
	echo "pi-lead git shim: no real git found on PATH (skipping ${{PILEAD_SHIM_DIR:-its own dir}})" >&2
	exit 127
fi
exec "$PL_REAL" "$@"
"""


def launch_env_reset():
    """Bootstrap fragment (an `&&` chain link, like the rest of the launch line) that clears the
    variables the shim refuses on, so values the human's shell exported before spawn never reach the
    executor: every CFG_VARS and GUARD_VARS entry unconditionally (the shim refuses any value), and a CMD_VARS entry
    only when its value names git as a command word (the shim refuses exactly those; `EDITOR=vim`
    survives). A value set later, inside the executor's session, still meets the shim's refusal."""
    return (f"unset {' '.join(CFG_VARS + GUARD_VARS)} && "
            f"for pl_v in {' '.join(CMD_VARS)}; do "
            'eval "pl_val=\\${$pl_v-}"; '
            f"if printf '%s\\n' \"$pl_val\" | grep -Eq -- '{GIT_WORD}'; then unset \"$pl_v\"; fi; "
            "done && ")


def _real_git():
    """The first absolute `git` on PATH that is not a pi-lead shim (this process may itself run under
    an executor's PATH), or None."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "git")
        if not (os.path.isabs(d) and os.path.isfile(p) and os.access(p, os.X_OK)):
            continue
        try:
            with open(p, "rb") as f:
                if MARKER.encode() in f.read(4096):
                    continue
        except OSError:
            continue
        return p
    return None


def resolve_guard(guarded):
    """(common dir, top level) of the repository `guarded` is in — both absolute and physical — found
    with the real git in an environment cleared of RESOLVE_CLEAR; None when either cannot be resolved."""
    git = _real_git()
    if git is None or not guarded:
        return None
    env = {k: v for k, v in os.environ.items() if k not in RESOLVE_CLEAR}
    out = []
    for flag in ("--git-common-dir", "--show-toplevel"):
        try:
            r = subprocess.run(["git", "-C", str(guarded), "rev-parse", "--path-format=absolute", flag],
                               executable=git, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = r.stdout.splitlines()
        if r.returncode != 0 or len(lines) != 1 or not os.path.isabs(lines[0]) or not os.path.isdir(lines[0]):
            return None
        out.append(os.path.realpath(lines[0]))
    return out[0], out[1]


def write_git_shim(session_dir, guarded=None) -> Path:
    """Write `<session_dir>/shim/git` (mode 0755), overwriting any earlier copy; return its path.
    `guarded` is the directory whose repository the shim guards (the executor's worktree, the worktree a
    re-run tests); None — or a directory whose repository cannot be resolved — guards every repository."""
    d = Path(session_dir) / "shim"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "git"
    tmp = d / ".git.tmp"
    guard = resolve_guard(guarded) if guarded is not None else None
    mirror = mirror_path(d) if guard is not None else None
    tmp.write_text(script(guard, mirror), encoding="utf-8")
    tmp.chmod(0o755)
    tmp.replace(p)
    if mirror is not None:
        build_mirror(p)
    return p


def build_mirror(shim_path):
    """Build (or rebuild) the exec-path mirror beside a guarded shim by running the shim once as
    `git --exec-path`: the shim builds its own mirror (one implementation, in sh) and prints the path
    git will use. The real git is the one _real_git() finds; the environment is cleared of what the
    shim refuses. Returns True when the printed path is the mirror. A mirror that cannot be built now
    is retried by the shim at its next call, which fails closed meanwhile."""
    git = _real_git()
    if git is None:
        return False
    shim_path = Path(shim_path)
    env = {k: v for k, v in os.environ.items() if k not in RESOLVE_CLEAR}
    env["PILEAD_SHIM_DIR"] = str(shim_path.parent)
    env["PATH"] = os.pathsep.join([os.path.dirname(git), "/usr/bin", "/bin"])
    try:
        # the shim run as git is (argv[0] `git`, like resolve_guard's probes), by its path
        r = subprocess.run(["git", "--exec-path"], executable=str(shim_path), env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == mirror_path(shim_path.parent)


def mirror_state(shim_dir):
    """(True, "current") when the exec-path mirror in `shim_dir` is what the shim would accept without a
    rebuild — its `git` is the shim, and the git and exec-path its record names still have the recorded
    inodes and mtimes (whole seconds, as sh's -nt / -ot see them) — else (False, why). Reads files only;
    runs nothing."""
    shim_dir = Path(shim_dir)
    m = Path(mirror_path(shim_dir))
    if not m.is_dir():
        return False, "missing"
    try:
        lines = (m / MIRROR_RECORD).read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return False, "has no readable record"
    if len(lines) < 4 or not all(lines[:4]):
        return False, "has an incomplete record"
    git, xp, _version, stamp = lines[:4]
    try:
        if not os.path.samefile(m / "git", shim_dir / "git"):
            return False, "has a `git` that is not the shim"
        sg, sx = os.stat(git), os.stat(xp)
        for real, mark in ((sg, MIRROR_GIT_MTIME), (sx, MIRROR_XP_MTIME)):
            if int(real.st_mtime) != int(os.stat(m / mark).st_mtime):
                return False, f"is stale ({git} or {xp} changed since it was built)"
    except OSError:
        return False, f"is stale ({git} or {xp} is gone)"
    words = ([str(sg.st_ino), *git.split()], [str(sx.st_ino), *xp.split()])
    if stamp not in (" ".join(words[0] + words[1]), " ".join(words[1] + words[0])):  # `ls` sorts its operands
        return False, f"is stale ({git} or {xp} was replaced since it was built)"
    return True, "current"

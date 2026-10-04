// Adversarial table for extensions/bash-fence.ts — the executor's git fence. Written before the
// parser: every row is an attempt to reach a committing git subcommand (denied) or a legitimate
// command an executor runs every day (allowed). `null` = allowed; a string = the offending simple
// command the fence must return (whitespace-normalised); DENY = denied, exact text not pinned.
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { tsImport } from "tsx/esm/api";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

let fence: any, shim: any;
before(async () => {
	const mod: any = await tsImport("../../extensions/bash-fence.ts", { parentURL: import.meta.url } as any);
	fence = typeof mod.deniedGitCommand === "function" ? mod : mod.default;
	const two: any = await tsImport("../../extensions/shim-fence.ts", { parentURL: import.meta.url } as any);
	shim = typeof two.shimFenceVerdict === "function" ? two : two.default;
});

const DENY = Symbol("deny");
/** `shell`: the shell pi runs the command with (its `shellPath` setting); bash when omitted, as pi's default. */
type Row = [cmd: string, expected: string | null | typeof DENY, shell?: "zsh"];

const DENIED: Row[] = [
	// plain + compound
	["git commit -m x", "git commit -m x"],
	["git add -A && git commit -m x", "git commit -m x"],
	["git status; git merge feature", "git merge feature"],
	["true || git rebase main", "git rebase main"],
	["sleep 1 & git push", "git push"],
	["git diff | git tag v1", "git tag v1"],
	["cd x && git rebase main", "git rebase main"],
	["\ngit commit", "git commit"],
	["git\tcommit -m x", "git commit -m x"],
	["git  \t  commit", "git commit"],
	["git status; git \\\n  commit -m x", DENY], // line continuation
	["git commit # trailing comment", "git commit"],
	// subshells, groups, substitutions
	["(git commit)", "git commit"],
	["( cd sub && (git push) )", "git push"],
	["{ git commit -m x; }", "git commit -m x"],
	["$(git commit)", "git commit"],
	["echo \"$(git commit -m x)\"", "git commit -m x"],
	["echo `git push`", "git push"],
	["echo \"`git push origin`\"", "git push origin"],
	["x=$(git commit -m y)", "git commit -m y"],
	["echo $(echo $(git tag v2))", "git tag v2"],
	["diff <(git commit) x", "git commit"],
	["echo ${X:-$(git push)}", "git push"],
	// control flow / functions
	["if true; then git commit; fi", "git commit"],
	["for i in 1 2; do git push; done", "git push"],
	["while false; do :; done; until true; do git merge x; done", "git merge x"],
	["f() { git commit -m x; }; f", "git commit -m x"],
	["function f { git push; }", "git push"],
	["! git commit", DENY],
	["case $x in a) git commit;; esac", "git commit"],
	// shells / eval
	["bash -c \"git push\"", "git push"],
	["bash -c 'git commit -m t'", "git commit -m t"],
	["sh -lc 'cd x; git merge main'", "git merge main"],
	["zsh -o errexit -c \"git rebase main\"", "git rebase main"],
	["bash --norc -c 'true && git tag v1'", "git tag v1"],
	["/bin/bash -xc 'git push'", "git push"],
	["bash -c \"bash -c 'git commit'\"", "git commit"],
	["echo $(bash -c \"git push\")", "git push"],
	["bash -c $'git\\x20commit'", "git commit"],
	["bash -c \"echo \\$(git commit)\"", "git commit"],
	["eval \"git tag v1\"", "git tag v1"],
	["eval git tag v1", "git tag v1"],
	["eval 'git add . && git commit -m x'", "git commit -m x"],
	// stdin-fed shells
	["bash <<EOF\ngit commit -m x\nEOF", "git commit -m x"],
	["sh <<'EOF'\ngit push\nEOF", "git push"],
	["bash <<<'git push'", "git push"],
	["echo 'git commit' | bash", "bash"],
	["cat script | sh -s", "sh -s"],
	// heredocs: body is data, but not the command line around it
	["cat <<EOF\ngit commit\nEOF\n&& git commit", "git commit"],
	["cat <<EOF && git push\nhello\nEOF", "git push"],
	["cat <<EOF\n$(git commit -m x)\nEOF", "git commit -m x"],
	["cat <<EOF\nok\nEOF\ngit tag v1", "git tag v1"],
	// wrappers
	["GIT_DIR=x git commit", "GIT_DIR=x git commit"],
	["env GIT_DIR=x FOO=1 git commit", "env GIT_DIR=x FOO=1 git commit"],
	["env -u X -i git push", "env -u X -i git push"],
	["env -S 'git commit -m x'", "git commit -m x"],
	["command git commit", "command git commit"],
	["exec git commit", "exec git commit"],
	["exec -a foo git push", "exec -a foo git push"],
	["builtin command git push", DENY],
	["nice -n 5 nohup time -p git commit", "nice -n 5 nohup time -p git commit"],
	["sudo -u bob git push", "sudo -u bob git push"],
	["timeout -s KILL 10 git push", "timeout -s KILL 10 git push"],
	["stdbuf -o L git commit", DENY],
	["watch -n 1 git commit", "git commit"],
	// xargs / find
	["echo x | xargs git commit -m", "xargs git commit -m"],
	["echo commit | xargs git", "xargs git"],
	["echo commit | xargs -n1 git -C .", "xargs -n1 git -C ."],
	["xargs -I{} git {}", "xargs -I{} git {}"],
	["xargs -I % sh -c 'git %'", DENY],
	["echo \"'git commit'\" | xargs sh -c", "xargs sh -c"],
	["echo --hard | xargs git reset", "xargs git reset"],
	["echo git | xargs -I{} {} commit", DENY],
	["find . -name x -exec git commit -m y {} \\;", DENY],
	["find . -execdir git push \\;", DENY],
	// git global options, aliases, spellings
	["git -C /repo -c color.ui=false --git-dir=.git --no-pager commit", DENY],
	["git --work-tree x push", "git --work-tree x push"],
	["git -C /repo push origin main", "git -C /repo push origin main"],
	["git ci -m x", "git ci -m x"],
	["git pull", "git pull"],
	["git cherry-pick abc", "git cherry-pick abc"],
	["git am < p.patch", DENY],
	["git update-ref refs/heads/main HEAD", DENY],
	["git commit-tree HEAD^{tree}", DENY],
	["git COMMIT -m x", DENY],
	["\"git\" \"commit\"", "\"git\" \"commit\""],
	["\\git commit", "\\git commit"],
	["g''it co'mm'it", DENY],
	["/usr/bin/git commit", "/usr/bin/git commit"],
	["git-commit -m x", "git-commit -m x"],
	["/usr/libexec/git-core/git-push", DENY],
	["git {commit,}", DENY],
	["{git,commit}", DENY],
	["git c?mmit", DENY],
	["git $SUB", DENY],
	["$(echo git) commit", DENY],
	["g=git; $g commit", DENY],
	["\"$GIT\" status", DENY],
	["git -c alias.x='!git commit' x", DENY],
	["git -c core.pager='git commit -am x' log", DENY],
	["git -c core.editor=evil add -e", DENY],
	["git config alias.ci commit", DENY],
	["git config --global core.hooksPath /tmp/h", DENY],
	// reset
	["git reset --hard", "git reset --hard"],
	["git reset --hard HEAD~1", "git reset --hard HEAD~1"],
	["git reset HEAD --merge", "git reset HEAD --merge"],
	["git reset --keep HEAD~", DENY],
	["git reset --har", "git reset --har"],
	["git -C . reset --me x", DENY],
	// command-running env vars, aliases, traps, rebinding, config/hook writes
	["GIT_PAGER='git commit -am x' git log", "git commit -am x"],
	["export GIT_EDITOR=\"git push\"", "git push"],
	["EDITOR='git tag v1'; git add -e", "git tag v1"],
	["env GIT_SSH_COMMAND='git push' git fetch", "git push"],
	["GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0=x git log", DENY],
	["alias c='git commit'", "git commit"],
	["trap 'git push' EXIT", "git push"],
	["hash -p /usr/bin/git g; g commit", DENY],
	["git config --add include.path /tmp/evil.cfg", DENY],
	["echo '[alias] c = commit' >> .git/config", DENY],
	["cat hook > .git/hooks/post-checkout", DENY],
	["printf x | tee -a ~/.gitconfig", DENY],
	["cp evil .git/config", DENY],
	// scripts the parser can see into
	["source <(echo git commit)", DENY],
	["bash <(printf 'git commit')", DENY],
	["bash < <(echo git commit)", DENY],
	[". /dev/stdin <<< 'git commit'", "git commit"],
	["bash -c \"$(printf 'git commit')\"", DENY],
	["coproc git push", DENY],
	// stepping around the git shim (lib/pilead/shim.py): git named by path, PATH / PILEAD_SHIM_DIR changes
	["/usr/bin/git status", "/usr/bin/git status"],
	["./git commit -m x", "./git commit -m x"],
	["$HOME/bin/git log", "$HOME/bin/git log"],
	["$(which git) commit", DENY],
	["xcrun git commit -m x", "xcrun git commit -m x"],
	["xcrun -sdk macosx git status", DENY],
	["PATH=/usr/bin git status", "PATH=/usr/bin git status"],
	["PATH=/usr/bin:$PATH python3 x.py", "PATH=/usr/bin:$PATH python3 x.py"],
	["export PATH=\"$HOME/bin:$PATH\"", "export PATH=\"$HOME/bin:$PATH\""],
	["export PILEAD_SHIM_DIR=/tmp", DENY],
	["declare -x PATH=/bin", DENY],
	["PATH+=:/x; make", DENY],
	["env PATH=/bin make", DENY],
	["env -i python3 x.py", "env -i python3 x.py"],
	["env - node x.js", DENY],
	["env -u PATH node x.js", DENY],
	["env --unset=PILEAD_SHIM_DIR make", DENY],
	["unset PILEAD_SHIM_DIR", DENY],
	["read -r PATH < f", DENY],
	["printf -v PATH %s /bin", DENY],
	["bash -c 'PATH=/bin; git status'", DENY],
	["sudo PATH=/bin make", DENY],
	// un-exporting / clearing / looping over PATH or PILEAD_SHIM_DIR, and git --exec-path=<dir>
	["export -n PATH", "export -n PATH"],
	["export -n PILEAD_SHIM_DIR; git status", "export -n PILEAD_SHIM_DIR"],
	["declare +x PATH", "declare +x PATH"],
	["typeset +x PATH", "typeset +x PATH"],
	["typeset +x PILEAD_SHIM_DIR && make", "typeset +x PILEAD_SHIM_DIR"],
	["exec -c git status", "exec -c git status"],
	["exec -cl python3 x.py", "exec -cl python3 x.py"],
	["for PATH in /bin; do python3 x.py; done", "for PATH in /bin"],
	["for PILEAD_SHIM_DIR in /x; do make; done", "for PILEAD_SHIM_DIR in /x"],
	["git --exec-path=/tmp status", "git --exec-path=/tmp status"],
	// PATH as an array: under bash 3.2 any element assignment makes PATH an unexported array, so a child finds /usr/bin/git
	["PATH[0]=/usr/bin", "PATH[0]=/usr/bin"],
	["PATH[1]+=x", "PATH[1]+=x"],
	["PATH=(a b)", DENY],
	["PATH+=(a)", DENY],
	["PILEAD_SHIM_DIR[0]=/x; make", "PILEAD_SHIM_DIR[0]=/x"],
	["PATH[$i]=/usr/bin python3 x.py", DENY],
	["export PATH[0]=/usr/bin", "export PATH[0]=/usr/bin"],
	["export 'PATH[0]=/usr/bin'", DENY],
	["export PATH+=(a)", DENY],
	["declare PATH[1]=x", "declare PATH[1]=x"],
	["declare PATH=(a b)", DENY],
	["typeset -a PATH[0]=x", "typeset -a PATH[0]=x"],
	["readonly PATH[0]=x", "readonly PATH[0]=x"],
	["local PILEAD_SHIM_DIR[0]=x", DENY],
	["declare -a PATH", "declare -a PATH"],
	["readonly -a PATH", "readonly -a PATH"],
	["unset 'PATH[0]'", "unset 'PATH[0]'"],
	["unset PILEAD_SHIM_DIR[1]", DENY],
	["printf -v 'PATH[0]' %s /usr/bin", DENY],
	["env PATH[0]=x make", DENY],
	["let PATH[1]=0", "let PATH[1]=0"],
	["let 'x=1' 'PATH += 1'", DENY],
	["((PATH[1]=0))", "PATH[1]=0"],
	["(( PATH[1] = 0 ))", DENY],
	// scoping PATH: in a function `local`/`typeset`/`declare` (and zsh's `readonly`) make a fresh, unexported PATH
	["local PATH", "local PATH"],
	["typeset PATH", "typeset PATH"],
	["declare PATH", "declare PATH"],
	["readonly PATH", "readonly PATH"],
	["local -r PATH", "local -r PATH"],
	["typeset -gx PATH", "typeset -gx PATH"],
	["declare -x PILEAD_SHIM_DIR", "declare -x PILEAD_SHIM_DIR"],
	["local PILEAD_SHIM_DIR", "local PILEAD_SHIM_DIR"],
	["local x PATH=/bin", DENY],
	["f() { typeset PATH; python3 x.py; }; f", "typeset PATH"],
	["f() { local PATH; make; }", "local PATH"],
	["git -C . --exec-path= log", DENY],
	// THE RULE (bash-fence.ts SHIM_VARS): a shim name where the shell would change its value, type, scope or export state.
	// (b) `-p` is a query only alone: any other flag declares, wherever it sits
	["declare -a PATH -p", "declare -a PATH -p"],
	["typeset -a PATH -p", "typeset -a PATH -p"],
	["declare -a PILEAD_SHIM_DIR -p", DENY],
	["typeset -a PILEAD_SHIM_DIR -p", DENY],
	["declare -ap PATH", DENY],
	["declare -p PATH=x", DENY],
	["declare -n ref=PATH", "declare -n ref=PATH"],
	["declare -n ref=PILEAD_SHIM_DIR", DENY],
	["local -n r; r=PATH", DENY],
	["declare -n r; for r in PATH; do r=x; done", DENY],
	["read -aPATH", DENY],
	["print -v PATH x", DENY, "zsh"],
	["zparseopts a=path", DENY, "zsh"],
	["unset 'a[PATH=1]'", DENY],
	["coproc PATH { cat; }", DENY],
	["for a PATH in 1 2; do :; done", DENY],
	// (a) a subscript with blanks is still one assignment word to bash
	["PATH[ 0 ]=/usr/bin", "PATH[ 0 ]=/usr/bin"],
	["PILEAD_SHIM_DIR[ 0 ]=/x", DENY],
	["PATH[ 0 ]=/usr/bin python3 x.py", DENY],
	// (c) arithmetic, any assigning operator, quoted or not
	["let PATH[1]++", "let PATH[1]++"],
	["let 'PATH[1]++'", "let 'PATH[1]++'"],
	["let \"PATH[1]+=1\"", "let \"PATH[1]+=1\""],
	["let PILEAD_SHIM_DIR[1]++", DENY],
	["let 'PILEAD_SHIM_DIR[1]++'", DENY],
	["let \"PILEAD_SHIM_DIR[1]+=1\"", DENY],
	["let --PATH", DENY],
	["arr[PATH[1]=0]=x", "arr[PATH[1]=0]=x"],
	["arr[PILEAD_SHIM_DIR[1]=0]=x", DENY],
	["echo ${arr[PATH[1]=0]}", DENY],
	["echo ${arr[PILEAD_SHIM_DIR[1]=0]}", DENY],
	["[[ 1 -eq PATH[1]=0 ]]", "[[ 1 -eq PATH[1]=0 ]]"],
	["[[ 1 -eq PILEAD_SHIM_DIR[1]=0 ]]", DENY],
	["[[ ( 1 -eq PATH=0 ) ]]", DENY],
	["[[ -v a[PATH=1] ]]", DENY],
	["declare -i n; n=PATH[1]=0", "n=PATH[1]=0"],
	["declare -i n; n=PILEAD_SHIM_DIR[1]=0", DENY],
	["declare -i n=PATH=1", DENY],
	["integer n=PATH=1", DENY, "zsh"],
	["(( PATH++ ))", DENY],
	["for (( ; ; PATH++ )); do :; done", DENY],
	["echo $(( PATH += 1 ))", DENY],
	["x=$(( --PILEAD_SHIM_DIR ))", DENY],
	["echo $[PATH=1]", DENY],
	["echo ${x:PATH=1}", DENY],
	["cat <<EOF\n$((PATH=1))\nEOF", DENY],
	// (d) assigning expansions, subscript or not, quoted or not
	[": ${PATH[1]:=x}", DENY],
	["echo ${PATH[1]:=x}", DENY],
	[": ${PILEAD_SHIM_DIR[1]=x}", DENY],
	[": ${PATH[1]=x}", DENY],
	["echo ${PILEAD_SHIM_DIR[1]:=x}", DENY],
	["echo \"${PATH:=x}\"", DENY],
	["echo ${path::=x}", DENY, "zsh"],
	["echo ${(A)path=x}", DENY, "zsh"],
	// (e) behind a prefix word (`noglob`, `-`: zsh precommand modifiers)
	["noglob unset PATH[1]", "noglob unset PATH[1]", "zsh"],
	["noglob unset PILEAD_SHIM_DIR[1]", DENY, "zsh"],
	["builtin unset PATH", DENY],
	["command declare PATH", DENY],
	["eval -- unset PATH", DENY],
	["-- unset PATH", DENY],
	["- git commit", DENY, "zsh"],
	// zsh's `path` array is PATH — under zsh only (pi's shellPath); under bash `path` is an ordinary name (ALLOWED rows below)
	["path=(/usr/bin)", DENY, "zsh"],
	["path+=(/usr/bin)", DENY, "zsh"],
	["path[1]=/usr/bin", DENY, "zsh"],
	["local path", DENY, "zsh"],
	["typeset -U path", DENY, "zsh"],
	// an unclosed `[` is one word with blanks only where bash reads an assignment; elsewhere the next blank ends it (review 1–2)
	["env -u V[ /usr/bin/git commit -m x", DENY],
	["exec -a n[ git commit", DENY],
	["sudo -u u[ git commit", DENY],
	["doas -u u[ git commit", DENY],
	["nice -n n[ git commit", DENY],
	["time -f f[ git commit", DENY],
	["timeout -s s[ 5 git commit", DENY],
	["stdbuf -o L[ git commit", DENY],
	["xargs -I r[ git commit", DENY],
	["env -C d[ git commit", DENY],
	["find . -name x[ -exec git commit \\;", DENY],
	["env -u V[ bash -c 'git push'", "git push"],
	["x[ 1 ]=2 git commit", "x[ 1 ]=2 git commit"],
	["time PATH[ 0 ]=/usr/bin", DENY],
	["time -p PATH[ 0 ]=/usr/bin make", DENY],
	["x[ git commit", DENY], // bash: a syntax error; the fence cannot tell, so it fails closed
	// arithmetic bodies are checked for the two names even when they hold the word `git` (review 3, 6)
	["(( git, PATH[1]=0 ))", DENY],
	["echo $(( git, PATH[1]=0 ))", DENY],
	[": $(( (PATH)=1 ))", DENY],
	["(( ((PILEAD_SHIM_DIR)) += 1 ))", DENY],
	["((git, PATH++))", DENY],
	// a declaring builtin's compound assignment given as one quoted word is parsed at run time (review 4)
	["declare -a 'arr=([PATH[1]=0]=x)'", DENY],
	["typeset -a 'arr=([PATH[1]=0]=x)'", DENY],
	["local -a 'arr=([PATH[1]=0]=x)'", DENY],
	["readonly -a 'arr=([PATH[1]=0]=x)'", DENY],
	["export 'arr=([PATH[1]=0]=x)'", DENY],
	["declare 'arr=( [PILEAD_SHIM_DIR[1]=0]=x )'", DENY],
	["declare -a 'a=($(git commit))'", "git commit"],
	// `set -k` makes every NAME=value argument an assignment (review 5)
	["set -k; make PATH=/usr/bin", "make PATH=/usr/bin"],
	["set -o keyword; make PATH=/usr/bin", "make PATH=/usr/bin"],
	["set -ek; npm test PILEAD_SHIM_DIR=/x", DENY],
	["set -k; env X=1 python3 x.py PATH+=/usr/bin", DENY],
	["set -k; make PATH[0]=/usr/bin", DENY], // not an assignment even with -k (bash 3.2 passes it as an argument): fail closed
	["set -k; git log GIT_PAGER='git commit'", "git commit"],
	["for i in 1 2; do make PATH=/x; set -k; done", DENY], // the loop runs `make` again after `set -k`
	["bash -k -c 'make PATH=/usr/bin'", DENY],
	["bash -o keyword -c 'make PATH=/usr/bin'", DENY],
	["set -k; bash -c 'make PATH=/usr/bin'", DENY], // an exported SHELLOPTS would carry it: fail closed
	["env SHELLOPTS=keyword bash -c 'make PATH=/usr/bin'", DENY],
	// zsh-only forms (review 7–8): under zsh `path` is PATH, and these shapes change PATH
	["path=x", DENY, "zsh"],
	["local path=$1", DENY, "zsh"],
	["for path in src/*.ts; do echo \"$path\"; done", DENY, "zsh"],
	["while read -r path; do :; done", DENY, "zsh"],
	["repeat 1 unset PATH", DENY, "zsh"],
	["repeat 2 git commit", "repeat 2 git commit", "zsh"],
	["foreach PATH (a b) echo; end", DENY, "zsh"],
	["emulate sh -c 'PATH=/usr/bin'", DENY, "zsh"],
	["emulate -R zsh -c 'git push'", "git push", "zsh"],
	["set -A PATH a b", DENY, "zsh"],
	["set +A path a", DENY, "zsh"],
	["exec {PATH}>f", DENY, "zsh"],
	["exec {PATH}>f", DENY], // bash >= 4.1 names fds too
	["exec {PILEAD_SHIM_DIR}<f", DENY],
	["{fd}>f git commit", DENY],
	// a command that starts zsh has its text checked with the zsh rules, whatever the outer shell
	["zsh -c 'path=(/usr/bin)'", DENY],
	["zsh -fc 'for path in a; do :; done'", DENY],
	["zsh <<'EOF'\nlocal path\nEOF", DENY],
	["zsh -c \"emulate sh -c 'path=x'\"", DENY],
	// the shim file itself (review 10)
	["rm \"$PILEAD_SHIM_DIR/git\"", DENY],
	["ln -sf /usr/bin/git \"$PILEAD_SHIM_DIR/git\"", DENY],
	["echo x > \"$PILEAD_SHIM_DIR/git\"", DENY],
	["chmod -x \"$PILEAD_SHIM_DIR/git\"", DENY],
	["mv \"$PILEAD_SHIM_DIR\" /tmp/x", DENY],
	["rm -rf ${PILEAD_SHIM_DIR}", DENY],
	["cp /usr/bin/git \"$PILEAD_SHIM_DIR/git\"", DENY],
	["install -m 755 x -t \"$PILEAD_SHIM_DIR\"", DENY],
	["printf x | tee \"$PILEAD_SHIM_DIR/git\"", DENY],
	["cat x >> \"$PILEAD_SHIM_DIR/git\"", DENY],
	["find \"$PILEAD_SHIM_DIR\" -delete", DENY],
	["sed -i '' 1d \"$PILEAD_SHIM_DIR/git\"", DENY],
	["touch \"$(printenv PILEAD_SHIM_DIR)/git\"", DENY],
	["sudo chmod 000 \"$PILEAD_SHIM_DIR/git\"", DENY],
	// `(( ))` that is a subshell, not arithmetic, is still parsed as commands
	["((git commit))", "git commit"],
	["((git-commit))", DENY],
	["$((git commit))", "git commit"],
	["(( a[$(git push)] ))", "git push"],
	["echo $[ $(git push) ]", "git push"],
	["((a) ; git commit)", "git commit"],
	// earlier rounds allowed these; pass 1 (the committed guard, unchanged) denies them, so they are DENIED again: an array element
	// assignment is a program name holding a glob or an expansion to it, `(( ))`/`$(( ))` a subshell/substitution, printf's PATH a setter
	["MYPATH[0]=x", "MYPATH[0]=x"],
	["local -a files; files[0]=x", "files[0]=x"],
	["declare -A m; m[k]=v; echo ${m[k]}", "m[k]=v"],
	["arr[0]=x", "arr[0]=x"],
	["files[$i]=$f", "files[$i]=$f"],
	["printf '%s' PATH", "printf '%s' PATH"],
	["((arr[i]++))", "arr[i]++"],
	["echo $((arr[0]+1))", "arr[0]+1"],
	["echo $(( $(wc -l < f) + 1 ))", "$(wc -l < f) + 1"],
	// round-3 review: denied by the committed guard, and must stay so (an unclosed `[` before an assignment; a program name split by
	// quotes or expansions inside arithmetic parens)
	["export a[ PATH=/usr/bin:/bin", DENY],
	["declare a[ PATH=/usr/bin", DENY],
	["local a[ PATH=/usr/bin", DENY],
	["export a[0 PATH=/usr/bin", DENY],
	["export a[$i PATH=/usr/bin", DENY],
	["export x[ GIT_PAGER='git commit'", DENY],
	["export x[ GIT_CONFIG_GLOBAL=/tmp/x", DENY],
	["declare a[ PILEAD_SHIM_DIR=/tmp", DENY],
	["((/usr/bin/gi\"\"t${IFS}commit))", DENY],
	["((/usr/bin/g\\it${IFS}commit))", DENY],
	["(($g))", DENY],
	["((${g}${IFS}commit))", DENY],
	["dash -c '((/usr/bin/gi\"\"t${IFS}commit))'", DENY],
	// pass 2 alone: a shim name spelled with quotes or expansions is taken as the name it could spell
	["export P${x}ATH=/usr/bin", DENY],
	["declare ${p}ATH=/usr/bin", DENY],
	["unset P${x}ATH", DENY],
	["let P${x}ATH=1", DENY],
	["let 'P\"\"ATH=1'", DENY],
	[": $(( P${x}ATH = 1 ))", DENY],
	["export GIT_CONFIG_KEY_$n=core.pager", DENY],
	["export GIT_PAG${x}ER='git commit'", DENY],
	["unset PATH[ 1 ]", DENY],
	["declare a[ 1+PATH++ ]=x", DENY],
	["declare a[ PATH[0]=/usr/bin", DENY], // bash 3.2 reads `PATH[0]=…` as its own argument: an array PATH
	["a[ 1 ]=x npm test", DENY], // one reading makes `a[` the command name: fail closed
	// pass 2 checks the GIT_* config variables where pass 1 does not look: zsh `integer`/`float`, `set -k` arguments
	["integer a[ GIT_CONFIG_GLOBAL=/tmp/x", DENY, "zsh"],
	["float -x GIT_EXEC_PATH=/tmp", DENY, "zsh"],
	["set -k; git log GIT_CONFIG_GLOBAL=/tmp/x", DENY],
	// pass 2, change 1: a program given by path — any word that is a path to `git` or a `git-*` helper, whatever wrapper runs it
	["arch -arm64 /usr/bin/git commit -m x", DENY],
	["script -q /dev/null /usr/bin/git commit", DENY],
	["taskpolicy -b /usr/bin/git commit", DENY],
	["sandbox-exec -p '(version 1)(allow default)' /usr/bin/git commit", DENY],
	["arch -arm64 ./git commit", DENY],
	["arch -arm64 ../x/git commit", DENY],
	["arch -arm64 ~/bin/git commit", DENY],
	["arch -arm64 $HOME/bin/git commit", DENY],
	["arch -arm64 ${HOME}/bin/git commit", DENY],
	["arch -arm64 \"$HOME\"/bin/git commit", DENY], // only partly quoted: not a quoted plain argument
	["arch -arm64 /usr/bin/g\"\"it commit", DENY],
	["arch -arm64 /usr/bin/gi${x}t commit", DENY],
	["arch -arm64 /usr/libexec/git-core/git-commit -m x", DENY],
	["taskpolicy -b lib/../../usr/bin/git commit", DENY],
	["bash -c 'arch -arm64 /usr/bin/git commit'", DENY],
	["x=$(taskpolicy -b /usr/bin/git push)", DENY],
	// the path rule by position: every word of a command whose program is not a plain file tool, the program word itself, and what a
	// file tool runs (`find -exec`); `source`/`.` of a file named exactly `git`
	["/usr/bin/git commit -m x", DENY],
	["./git commit", DENY],
	["arch -arm64 /usr/bin/git commit", DENY],
	["xargs /usr/bin/git commit", DENY],
	["find . -exec /usr/bin/git commit \\;", DENY],
	["find . -name x -execdir ./git commit {} +", DENY],
	["cd /x && /usr/bin/git commit", DENY],
	["ls; /usr/bin/git commit", DENY],
	["echo $(/usr/bin/git commit)", DENY],
	["cat <(/usr/bin/git log)", DENY],
	["ln -s /usr/bin/git g", DENY],
	["cp /usr/bin/git ./g", DENY],
	["source ./git", DENY],
	[". /usr/bin/git", DENY],
	["sudo ls /usr/bin/git", DENY], // a wrapper before the file tool: its words stay in program position
	["rg --pre /usr/libexec/git-core/git-commit x", DENY], // rg runs its --pre program on each file
	// a string that defines functions or aliases can make a file tool's name run its arguments: no file-tool exemption there
	["cd() { \"$@\"; }; cd /usr/bin/git commit", DENY],
	["function echo { \"$@\"; }; echo /usr/bin/git commit", DENY],
	["bash -c $'ls\\x28\\x29 { \"$@\"; }; ls /usr/bin/git commit'", DENY],
	["function ls { \"$@\"; }; ls /usr/bin/git commit", DENY],
	["alias ls=env; ls /usr/bin/git commit", DENY],
	["ls () ( \"$@\" ); ls /usr/bin/git commit", DENY],
	["eval 'cat() { \"$@\"; }'; cat /usr/bin/git commit", DENY],
	["enable -f ./x.so ls; ls /usr/bin/git commit", DENY],
	["hash -p /usr/bin/git ls; ls commit", DENY],
	["ls () { \"$@\"; }; ls /usr/bin/git commit", DENY],
	["function ls() { \"$@\"; }; ls /usr/bin/git commit", DENY],
	["unalias ls; ls /usr/bin/git commit", DENY],
	["unset -f ls; ls /usr/bin/git commit", DENY],
	// a `find -exec` segment ends at `;`, or at `+` only right after `{}`; any other `+` is a word of the command it runs
	["find . -maxdepth 0 -exec env -u + /usr/bin/git commit -m x \\;", DENY],
	["find . -execdir env -u + /usr/bin/git commit \\;", DENY],
	["find . -ok env -u + /usr/bin/git commit \\;", DENY],
	["find . -exec nice -n 1 + /usr/bin/git commit \\;", DENY],
	["find . -exec arch -arm64 + /usr/bin/git commit \\;", DENY],
	["find . -exec timeout 5 + /usr/bin/git commit \\;", DENY],
	["find . -exec env -u + /usr/bin/git commit ';'", DENY],
	["find . -exec env -u + /usr/bin/git commit \";\"", DENY],
	["find . -okdir env -u + /usr/bin/git commit \\;", DENY],
	// the same segment end for the env, login-shell and PATH rules (checkWords): a stray `+` does not end what they see
	["find . -exec env -i + make \\;", DENY],
	["find . -exec env -u PATH + make \\;", DENY],
	["find . -exec bash -l + -c make \\;", DENY],
	["find . -execdir env -i + make \\;", DENY],
	// ...and where the offending part comes after the stray `+` (the rows above are denied before it)
	["find . -exec env -u + env -i make \\;", DENY],
	["find . -exec env -u + bash -l -c make \\;", DENY],
	["find . -exec nice -n + bash -lc make \\;", DENY],
	["find . -exec env -u + PATH=/usr/bin make \\;", DENY],
	["find . -execdir env -u + env -u PATH make \\;", DENY],
	["env -P /usr/bin git commit -m x", DENY],
	["env -P /usr/bin git commit", DENY],
	["env -P/usr/bin git status", DENY],
	// pass 2, change 2: shells that reset PATH (a login shell runs the system profile, which puts /usr/bin first)
	["bash -l", DENY],
	["bash -lc make", DENY],
	["bash -l -c 'npm test'", DENY],
	["bash -il -c 'npm test'", DENY],
	["bash --login", DENY],
	["bash --login -c make", DENY],
	["/bin/bash -l script.sh", DENY],
	["sh -l", DENY],
	["sh -l -c make", DENY],
	["zsh -l", DENY],
	["zsh -lc make", DENY],
	["zsh -o login -c make", DENY],
	["zsh --login -c make", DENY],
	["echo make | bash -l", DENY],
	["exec -l bash", DENY],
	["exec -l python3 x.py", DENY],
	["exec -a -bash bash", DENY], // a `-` at the start of argv[0] is what `exec -l` does
	["su -", DENY],
	["su - bob", DENY],
	["su -l bob", DENY],
	["su --login bob -c make", DENY],
	["sudo su -", DENY],
	["login", DENY],
	["login -f bob", DENY],
	["command -p make", DENY],
	["command -p git status", DENY],
	["builtin command -p make", DENY],
	// pass 2, change 3: `env` options that clear or replace the environment, in any order or bundling
	["env -i make", DENY],
	["env -iv make", DENY],
	["env -vi make", DENY],
	["env -0i make", DENY],
	["env - make", DENY],
	["env -u PATH make", DENY],
	["env -u PILEAD_SHIM_DIR make", DENY],
	["env -uPATH make", DENY],
	["env -vuPATH make", DENY],
	["env -v -u PATH make", DENY],
	["env -u FOO -u PATH make", DENY],
	["env -u 'P''ATH' make", DENY],
	["env --unset PATH make", DENY],
	["env --unset=PATH make", DENY],
	["env --uns=PILEAD_SHIM_DIR make", DENY],
	["env --ignore-environment make", DENY],
	["env --ignore make", DENY],
	["env FOO=1 --ignore-environment make", DENY],
	["env -P /usr/bin make", DENY],
	["env -vP/usr/bin make", DENY],
	["env -S '-i make'", DENY],
	["env -S '-iv make'", DENY],
	["env -S'-u PATH make'", DENY],
	["env -vS '-P /usr/bin make'", DENY],
	["env --split-string='- make'", DENY],
	["env -S 'FOO=1 --ignore-environment make'", DENY],
	["env -L bob make", DENY], // an option the guard does not know (FreeBSD: a login class's environment): cannot tell, so deny
	["bash -c 'env -iv make'", DENY],
	// fail closed on what cannot be parsed
	["echo \"unbalanced", DENY],
	["echo 'unbalanced", DENY],
	["echo $(unclosed", DENY],
	["echo `unclosed", DENY],
	["git add . && echo \"oops", DENY],
];

/** Ref-moving forms of allowlisted subcommands: denied, and the reason must say why ("moves or deletes a ref"). */
const REF_MOVES: string[] = [
	"git branch -d old", "git branch -D main", "git branch --delete old", "git branch --del old", "git branch -vD main",
	"git branch -f main HEAD~3", "git branch --force main x", "git branch -m old new", "git branch -M main", "git branch --move a b",
	"git branch -c a b", "git branch -C a main", "git branch --copy a b", "git branch --set-upstream-to=origin/x",
	"git branch -u origin/x", "git branch --unset-upstream", "git branch --edit-description", "git branch --sort=refname -D main",
	"git checkout -B main origin/x", "git checkout -fB main", "git checkout --orphan gh-pages", "git checkout --orph x",
	"git switch -C main", "git switch -Cmain", "git switch --force-create main", "git switch --orphan x",
	"git symbolic-ref HEAD refs/heads/x", "git symbolic-ref -m why HEAD refs/heads/x", "git symbolic-ref -d HEAD", "git symbolic-ref --delete HEAD",
	"git worktree remove ../x", "git worktree prune", "git worktree move ../a ../b", "git worktree lock ../x", "git worktree unlock ../x",
	"git worktree add -B main ../x", "git worktree add --orphan -b y ../x",
	"git stash clear", "git remote set-url origin x", "git remote remove origin", "git remote rm origin", "git remote rename origin up",
	"git remote prune origin", "git remote -v rename a b", "git remote set-head origin -d",
	"git -C . branch -D main", "git-branch -D main", "bash -c 'git branch -D main'", "echo $(git switch -C main)",
	"echo -D | xargs git branch", "echo clear | xargs git stash", "echo x | xargs git symbolic-ref HEAD --",
];
/** Never allowlisted (fail-closed default), whatever they do to refs. */
const NEVER: string[] = ["git update-ref refs/heads/main HEAD", "git update-ref -d refs/heads/x", "git replace a b", "git notes add -m x", "git am p.patch"];

const ALLOWED: Row[] = [
	// ref-safe siblings of the REF_MOVES forms
	["git branch", null],
	["git branch new", null],
	["git branch new origin/main", null],
	["git branch -vv --list 'feat*'", null],
	["git branch --sort -committerdate", null],
	["git branch --contains HEAD --merged main", null],
	["git checkout -b x", null],
	["git checkout -b feat origin/main", null],
	["git checkout -bBugfix", null],
	["git checkout main", null],
	["git switch -c x", null],
	["git switch --create x", null],
	["git switch --force main", null],
	["git symbolic-ref HEAD", null],
	["git symbolic-ref --short -q HEAD", null],
	["git worktree list", null],
	["git worktree add ../x", null],
	["git worktree add -b feat ../x", null],
	["git stash drop", null],
	["git stash show -p", null],
	["git stash push -m clear", null],
	["git remote -v", null],
	["git remote show origin", null],
	["git remote add up https://x", null],
	["git apply p.diff", null],
	["git apply --index --3way p.diff", null],
	["git ls-files | xargs git checkout --", null],

	["git add -A", null],
	["git add src/*.ts && git status", null],
	["git status --short", null],
	["git diff --cached", null],
	["git log --grep=commit", null],
	["git log --oneline -- commit.txt", null],
	["git log --format='%H commit %s'", null],
	["git show HEAD:commit", null],
	["git reset HEAD~ -- file", null],
	["git reset HEAD file", null],
	["git reset -- --hard", null], // a path named --hard
	["git reset --mixed", null],
	["git stash", null],
	["git stash list", null],
	["git stash push -m wip", null],
	["git stash pop", null],
	["git stash drop", null],
	["git checkout -- file.ts", null],
	["git switch -c feat", null],
	["git restore --staged x", null],
	["git branch -a", null],
	["git -C /repo status", null],
	["git -c color.ui=always diff", null],
	["git --no-pager log -1", null],
	["git rev-parse --show-toplevel", null],
	["git worktree list", null],
	["git fetch origin", null],
	["git config user.name", null],
	["git config --get alias.ci", null],
	["git grep -n commit", null],
	["git ls-files -z", null],
	["git blame -L 1,2 x", null],
	["git", null],
	["git --version", null],
	["git help commit", null],
	["echo git commit", null],
	["echo \"git commit\"", null],
	["printf 'git push\\n'", null],
	["grep -rn \"git commit\" .", null],
	["rg 'git (commit|push)' src", null],
	["# git commit", null],
	["cat <<EOF\ngit commit\nEOF", null],
	["cat <<'EOF'\n$(git commit)\nEOF", null],
	["cat <<-EOF\n\tgit push\n\tEOF\ngit status", null],
	["cat > notes.md <<'EOF'\nrun `git commit` later\nEOF", null],
	["bash -c 'git status && git diff'", null],
	["sh -c \"git add .\"", null],
	["bash scripts/check.sh", null],
	["eval \"git log -1\"", null],
	["find . -name '*.ts' -exec git add {} +", null],
	["xargs git add", null],
	["git ls-files | xargs -n1 git add", null],
	["env FOO=1 git status", null],
	["GIT_PAGER=cat git log -p", null],
	["nice git diff", null],
	["command -v git", null],
	["timeout 60 npm test", null],
	["[ -f x ] && git add x", null],
	["[[ -n $x ]] && git status", null],
	["npm test && git add -A && git status", null],
	["$HOME/bin/tool commit", null],
	["./node_modules/.bin/tsc --noEmit", null],
	["python3 -c \"print('git commit')\"", null],
	["for f in a b; do git add \"$f\"; done", null],
	["x=$(git rev-parse HEAD); echo $x", null],
	["echo ${HOME:-/tmp}", null],
	["ls 2>&1 | head -5 > out.txt", null],
	["git diff > /tmp/p.patch 2>/dev/null", null],
	["EDITOR=vim; export EDITOR", null],
	["alias ll='ls -l'", null],
	["trap 'rm -f /tmp/x' EXIT", null],
	["source ./env.sh && git status", null],
	["bash < script.sh", null],
	["echo hi > .github/workflows/x.yml", null],
	["git config --get include.path", null],
	["cp .gitignore /tmp/backup", null],
	["echo $PATH", null],
	["printf '%s\\n' \"$PATH\"", null],
	["which git && command -v git", null],
	["xcrun --find git", null],
	["xcrun clang -v", null],
	["env -u FOO npm test", null],
	["unset FOO", null],
	["read -r line < f", null],
	["export PATH", null],
	["export -n FOO", null],
	["declare +x FOO; declare -p PATH", null],
	["typeset -x FOO=1", null],
	["exec node x.js", null],
	["exec -a name python3 x.py", null],
	["for f in PATH b; do echo $f; done", null],
	["git --exec-path", null],
	// near misses of the PATH array / scoping forms
	["local MYPATH", null],
	["PATHS=(a b)", null],
	["echo \"local PATH\"", null],
	["grep -n 'PATH\\[0\\]' file", null],
	["declare -p PATH", null],
	["typeset -p PATH PILEAD_SHIM_DIR", null],
	["let i+=1", null],
	["let 'x = PATH == 1'", null],
	["f() { local x=1 y; echo $x; }", null],
	["./scripts/build.sh && GITHUB_TOKEN=x gh pr list", null],
	// THE RULE's near misses: reading a shim name, or quoted text given to an ordinary program
	["typeset -p PATH", null],
	["declare -p PILEAD_SHIM_DIR", null],
	["echo \"$PATH\"", null],
	["echo ${PATH}", null],
	["echo ${PATH:-x}", null],
	["echo ${#PATH}", null],
	["echo ${PILEAD_SHIM_DIR:-x}", null],
	["echo ${PATH: -3} ${x:1:2}", null],
	["let n++", null],
	["cd some/path", null],
	["ls path", null],
	["python3 -c \"import os; print(os.environ['PATH'])\"", null],
	["grep 'PATH=' .envrc", null],
	["echo \"PATH=/x\"", null],
	["read -p 'PATH: ' x", null],
	["grep -v PATH=1 f", null],
	["(( PATH == 1 ))", null],
	["for ((i=0;i<3;i++)); do echo $i; done", null],
	["x=$[1+2]", null],
	["[[ $x -eq 1 ]]", null],
	["echo a[ 1 ]", null],
	// near misses of the rows above
	["set -- a[ 1 ]", null],
	["ls a[ b", null],
	["declare -a 'arr=([0]=x [1]=y)'", null],
	["declare -A 'm=([PATH]=x)'", null],
	["(( (n)=1 ))", null],
	["set -e; make CC=gcc", null],
	["set -k; make CC=gcc", null],
	["make -k PATH=/usr/bin", null], // `-k` for make, not `set -k` (make's own reading of PATH=: see the report's Residual)
	["path=x", null],
	["local path=$1", null],
	["for path in src/*.ts; do echo \"$path\"; done", null],
	["while read -r path; do :; done", null],
	["bash -c 'path=x'", null],
	["emulate sh -c 'PATH=/usr/bin'", null], // bash has no `emulate`
	["set -A arr a b", null, "zsh"],
	["exec {fd}>f", null, "zsh"],
	["foreach f (a b) echo $f; end", null, "zsh"],
	["emulate sh -c 'echo hi'", null, "zsh"],
	["repeat 3 echo hi", null, "zsh"],
	["ls \"$PILEAD_SHIM_DIR\"", null],
	["cat \"$PILEAD_SHIM_DIR/git\"", null],
	["cp \"$PILEAD_SHIM_DIR/git\" /tmp/g", null],
	["ln -s \"$PILEAD_SHIM_DIR/git\" /tmp/g", null],
	["echo \"$PILEAD_SHIM_DIR\" > /tmp/where", null],
	// everyday commands (packet change 7)
	["npm test", null],
	["export NODE_ENV=test", null],
	["echo $((1+2))", null],
	["cd x && ls", null],
	["grep PATH f", null],
	["which git", null],
	["git status", null],
	["git add README.md", null],
	["export PREFIX_PATH=/x; local n=${#PATH}; (( n += 1 ))", null],
	// near misses of pass 2's changes 1–3: a path that is not git, one that does not start at /, ~, . or an expansion, a quoted plain
	// argument; a shell that is not a login shell; `env` options that keep the environment
	["ls .git", null],
	["cat .git/config", null],
	["cd src/git-tools", null],
	["grep -r foo lib/git", null],
	["grep -n '/usr/bin/git' notes.md", null],
	["echo \"$HOME/bin/git\"", null],
	["ls ~/.gitconfig /usr/bin/gitk", null],
	["curl -O https://example.com/git-2.0.tar.gz", null],
	// the path rule allows a git path as an argument of a plain file tool (a project folder named `git` or `git-…`)
	["cd /Users/x/dev/git-tools && npm test", null],
	["ls ~/code/git-hooks", null],
	["cd /Users/x/code/git && ls", null],
	["cat ./git-notes.md", null],
	["ls ./git-*.sh", null],
	["ls -l /usr/bin/git", null],
	["test -x /usr/bin/git", null],
	["[[ -x /usr/local/bin/git ]]", null],
	["file /usr/bin/git", null],
	["echo /usr/bin/git", null],
	["grep /usr/bin/git README.md", null],
	["source /usr/local/etc/bash_completion.d/git-completion.bash", null],
	["pushd ~/src/git && npm test; popd", null],
	["find ~/code/git-tools -name '*.ts' -exec grep -l foo {} +", null],
	["find ~/code/git-tools -name '*.ts' -exec grep -n foo {} +", null],
	["find . -name '*.md' -exec wc -l {} +", null],
	["find . -type f -exec ls -l {} \\;", null],
	["find . -name '*.ts' -exec wc -l {} +", null],
	["find . -type f -exec env NODE_ENV=test node {} \\;", null],
	// words that merely contain `function`, `alias`, `enable` or `()` as an argument, a path or inside quotes define nothing
	["cd /Users/x/dev/git-tools && grep -rn function src", null],
	["cd /Users/x/dev/git-tools && npm run enable-flags", null],
	["cd /Users/x/dev/git-tools && git add src/function.ts", null],
	["cd /Users/x/dev/git-tools && node -e \"console.log(f())\"", null],
	["ls ~/code/git-hooks; echo alias", null],
	["cd /Users/x/dev/git-tools && npm test", null, "zsh"],
	["bash -c 'npm test'", null],
	["sh -c 'ls'", null],
	["bash -x -c 'npm test'", null],
	["bash -o pipefail -c 'npm test | tee out'", null],
	["zsh -f -c 'echo hi'", null],
	["command ls", null],
	["command -pv git", null],
	["env", null],
	["env NODE_ENV=test npm test", null],
	["env -v npm test", null],
	["env -C sub npm test", null],
	["env -S 'NODE_ENV=test npm test'", null],
	["env --unset=FOO npm test", null],
	["", null],
];

function norm(r: string | null): string | null {
	return r === null ? null : r.replace(/\s+/g, " ").trim();
}

test(`table: ${DENIED.length} denied + ${ALLOWED.length} allowed cases`, () => {
	const failures: string[] = [];
	for (const [cmd, want, shell] of [...DENIED, ...ALLOWED]) {
		let got: string | null;
		try {
			got = fence.deniedGitCommand(cmd, { shell });
		} catch (e) {
			failures.push(`${JSON.stringify(cmd)} THREW ${String(e)}`);
			continue;
		}
		const ok = want === DENY ? typeof got === "string" && got.length > 0 : norm(got) === want;
		if (!ok) failures.push(`${JSON.stringify(cmd)}${shell ? ` (${shell})` : ""}: want ${want === DENY ? "DENY" : JSON.stringify(want)}, got ${JSON.stringify(got)}`);
	}
	assert.deepEqual(failures, [], `${failures.length} case(s) failed:\n${failures.join("\n")}`);
});

test(`ref moves: ${REF_MOVES.length} denied with a "moves or deletes a ref" reason; ${NEVER.length} never allowlisted`, () => {
	const failures: string[] = [];
	for (const cmd of REF_MOVES) {
		const v = fence.deniedGitVerdict(cmd);
		if (!v || !/moves or deletes a ref/.test(v.reason ?? "")) failures.push(`${JSON.stringify(cmd)}: got ${JSON.stringify(v)}`);
	}
	for (const cmd of NEVER) if (!fence.deniedGitCommand(cmd)) failures.push(`${JSON.stringify(cmd)}: allowed`);
	assert.deepEqual(failures, [], `${failures.length} case(s) failed:\n${failures.join("\n")}`);
	assert.equal(fence.deniedGitVerdict("git branch -D main").command, "git branch -D main");
	assert.match(fence.deniedGitVerdict("git update-ref -d refs/heads/x").reason, /not an allowed git subcommand/);
	assert.equal(fence.deniedGitVerdict("git am p.patch").reason, undefined);
});

test("verdict: unknown/alias subcommands name the allowlist; commit does not need a reason", () => {
	const v = fence.deniedGitVerdict("git ci -m x");
	assert.equal(v.command, "git ci -m x");
	assert.match(v.reason, /allowed:.*\badd\b.*\bstatus\b/);
	assert.equal(fence.deniedGitVerdict("git commit").reason, undefined);
	assert.match(fence.deniedGitVerdict("echo 'x").reason, /parse|unterminated/i);
});

test("shim bypasses name the git shim in the reason — pass 1's own reason where pass 1 denies", () => {
	const check = (cmd: string, shell?: string) => {
		const first = fence.gitFenceVerdict(cmd), got = fence.deniedGitVerdict(cmd, { shell });
		if (first) assert.deepEqual(got, first, `${cmd}: pass 1 denies, so its verdict and reason are the answer`);
		else assert.match(got?.reason ?? "", /bypasses the git shim/, cmd);
	};
	for (const cmd of ["/usr/bin/git status", "xcrun git status", "export PATH=/bin", "env -i make", "unset PATH", "export -n PATH",
		"declare +x PATH", "typeset +x PATH", "exec -c make", "for PATH in /bin; do make; done", "PATH[0]=/usr/bin", "declare -a PATH",
		"unset 'PATH[0]'", "let PATH[1]=0", "local PATH", "typeset PATH", ...LIVE_PATH_ROWS, "declare -n ref=PATH", "(( PATH++ ))",
		"rm \"$PILEAD_SHIM_DIR/git\"", "echo x > \"$PILEAD_SHIM_DIR/git\"", "set -k; make PATH=/usr/bin", "exec {PATH}>f",
		"arch -arm64 /usr/bin/git commit", "env -P /usr/bin git status", "bash -lc make", "exec -l bash", "su -", "login", "command -p make",
		"env -iv make", "env - make", "env -u PATH make", "env --ignore-environment make", "env -S '-i make'"]) check(cmd);
	for (const cmd of ["path=(/usr/bin)", "noglob unset PATH[1]", "set -A PATH a b", "repeat 1 unset PATH"]) check(cmd, "zsh");
	// git named by path: the reason says how to pass a path that is data
	for (const cmd of ["arch -arm64 /usr/bin/git commit", "ln -s /usr/bin/git g", "source ./git"]) {
		assert.match(fence.deniedGitVerdict(cmd)?.reason ?? "", /naming git by path .* if the path is data, not a program to run, put it in quotes/, cmd);
	}
	// the rows pass 1 allows get pass 2's reason
	for (const cmd of ["PATH[ 0 ]=/usr/bin", "let PATH[1]=0", "(( PATH++ ))", "declare -a PATH", "rm \"$PILEAD_SHIM_DIR/git\"", "exec {PATH}>f"]) {
		assert.equal(fence.gitFenceVerdict(cmd), null, cmd);
		assert.match(fence.deniedGitVerdict(cmd)?.reason ?? "", /bypasses the git shim/, cmd);
	}
});

test("zsh: `path` is PATH there, so the refusal tells the executor to rename the variable; bash allows it", () => {
	for (const cmd of ["path=x", "local path=$1", "for path in src/*.ts; do echo \"$path\"; done", "while read -r path; do :; done",
		"path=(/usr/bin)", "typeset -U path", "echo ${path::=x}", "zsh -c 'path=x'"]) {
		assert.match(fence.deniedGitVerdict(cmd, { shell: "zsh" })?.reason ?? "", /rename the variable/, cmd);
	}
	for (const cmd of ["path=x", "local path=$1", "for path in src/*.ts; do echo \"$path\"; done", "while read -r path; do :; done"]) {
		assert.equal(fence.deniedGitVerdict(cmd), null, cmd);
		assert.equal(fence.deniedGitVerdict(cmd, { shell: "bash" }), null, cmd);
		assert.equal(fence.deniedGitVerdict(cmd, { shell: "/bin/bash" }), null, cmd);
	}
	assert.match(fence.deniedGitVerdict("zsh -c 'path=x'")?.reason ?? "", /rename the variable/, "a command that starts zsh gets the zsh rules");
	assert.match(fence.deniedGitVerdict("path=x", { shell: "/opt/homebrew/bin/zsh" })?.reason ?? "", /rename the variable/, "a shellPath naming zsh");
});

/** Review items 1–5 (PATH forms): under /bin/bash each turns PATH into an array, which bash 3.2 stops exporting, or sets element 0. */
const LIVE_PATH_ROWS = [
	"PATH[0]=/usr/bin", "declare -a PATH -p", "typeset -a PATH -p", "PATH[ 0 ]=/usr/bin", "let PATH[1]++", "let 'PATH[1]++'",
	"let \"PATH[1]+=1\"", ": ${PATH[1]:=x}", "echo ${PATH[1]:=x}", "arr[PATH[1]=0]=x", "echo ${arr[PATH[1]=0]}", "[[ 1 -eq PATH[1]=0 ]]",
	"declare -i n; n=PATH[1]=0",
	// arithmetic bodies holding the word `git` (bash reads `git` there as a variable), and quoted compound assignments
	"(( git, PATH[1]=0 ))", "echo $(( git, PATH[1]=0 ))", "declare -a 'arr=([PATH[1]=0]=x)'", "typeset -a 'arr=([PATH[1]=0]=x)'",
	"readonly -a 'arr=([PATH[1]=0]=x)'", "f() { local -a 'arr=([PATH[1]=0]=x)'; }; f",
];

test("real /bin/bash: each live PATH row makes a child resolve git past a stub first on PATH, and the fence denies it", (t) => {
	if (!existsSync("/bin/bash")) return t.skip("no /bin/bash");
	const dir = mkdtempSync(join(tmpdir(), "fence-path-array-"));
	try {
		const stub = join(dir, "git");
		writeFileSync(stub, "#!/bin/sh\necho stub\n");
		chmodSync(stub, 0o755);
		// the child is a fresh /bin/sh by absolute path, as a python subprocess or make would start one; the row's own output is discarded
		const childGit = (pre: string) => spawnSync("/bin/bash", ["-c", `{ ${pre}; } >/dev/null 2>&1; /bin/sh -c 'command -v git'`], {
			env: { PATH: `${dir}:/usr/bin:/bin`, HOME: dir }, encoding: "utf8",
		}).stdout.trim();
		assert.equal(childGit(":"), stub, "control: the stub is the child's git");
		assert.equal(childGit("echo ${PATH:-x}; declare -p PATH; let n++"), stub, "control: reading PATH leaves the stub in place");
		const failures: string[] = [];
		for (const row of LIVE_PATH_ROWS) {
			if (childGit(row) === stub) failures.push(`${row}: the child still finds the stub (row not a live bypass)`);
			if (fence.deniedGitCommand(row) === null) failures.push(`${row}: fence allowed it`);
		}
		assert.deepEqual(failures, []);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test("real shells: a quoted compound assignment runs its `$(…)`; `(NAME)=1` assigns in zsh (bash 3.2 refuses it); the fence denies both", (t) => {
	if (!existsSync("/bin/bash")) return t.skip("no /bin/bash");
	const run = (shell: string, cmd: string, env: Record<string, string> = {}) =>
		spawnSync(shell, shell.endsWith("zsh") ? ["-f", "-c", cmd] : ["-c", cmd], { env: { PATH: "/usr/bin:/bin", ...env }, encoding: "utf8" }).stdout.trim();
	assert.equal(run("/bin/bash", "declare -a 'a=($(echo 7))'; echo ${a[0]}"), "7");
	const probe = ": $(( (PILEAD_SHIM_DIR)=1 )); echo $PILEAD_SHIM_DIR";
	t.diagnostic(`/bin/bash: ${JSON.stringify(probe)} with PILEAD_SHIM_DIR=5 prints ${JSON.stringify(run("/bin/bash", probe, { PILEAD_SHIM_DIR: "5" }))}`);
	if (existsSync("/bin/zsh")) assert.equal(run("/bin/zsh", probe, { PILEAD_SHIM_DIR: "5" }), "1", "zsh assigns through the parentheses");
	for (const row of [": $(( (PATH)=1 ))", ": $(( (PILEAD_SHIM_DIR)=1 ))", "declare -a 'a=($(git commit))'"]) {
		assert.notEqual(fence.deniedGitCommand(row), null, row);
		assert.notEqual(fence.deniedGitCommand(row, { shell: "zsh" }), null, row);
	}
});

test("real /bin/bash: rows that start the child themselves reach a git past the stub first on PATH, and the fence denies each", (t) => {
	if (!existsSync("/bin/bash")) return t.skip("no /bin/bash");
	const dir = mkdtempSync(join(tmpdir(), "fence-live-child-"));
	try {
		const stub = join(dir, "git"), sys = join(dir, "sys");
		writeFileSync(stub, "#!/bin/sh\necho stub\n");
		chmodSync(stub, 0o755);
		// a stand-in for the real git, reached only by path (running /usr/bin/git itself could open the developer-tools prompt)
		mkdirSync(sys);
		writeFileSync(join(sys, "git"), "#!/bin/sh\necho real\n");
		chmodSync(join(sys, "git"), 0o755);
		// HOME is the temp dir, so a login shell reads only the system profile, never the developer's own startup files
		const childGit = (row: string) => spawnSync("/bin/bash", ["-c", row], { env: { PATH: `${dir}:/usr/bin:/bin:/usr/sbin:/sbin`, HOME: dir }, encoding: "utf8" }).stdout.trim();
		const probe = "/bin/sh -c 'command -v git'";
		assert.equal(childGit(`${probe} PATH=/usr/bin:/bin`), stub, "control: without -k the argument is only an argument");
		assert.equal(childGit("bash -c 'command -v git'"), stub, "control: a shell that is not a login shell keeps PATH");
		assert.equal(childGit(`env -v ${probe}`), stub, "control: env -v keeps the environment");
		// set -k: the PATH=… argument reaches the command's environment
		const rows = [`set -k; ${probe} PATH=/usr/bin:/bin`, `set -o keyword; ${probe} PATH=/usr/bin:/bin`, `set -k; ${probe} PATH+=/usr/bin`];
		// pass 2 change 1: git by path behind a wrapper pass 1 does not know; change 3: `env -iv` empties the environment
		if (existsSync("/usr/sbin/taskpolicy")) rows.push(`taskpolicy -b ${sys}/git`);
		rows.push(`env -iv ${probe}`);
		// change 2: a login shell runs /etc/profile, whose path_helper (macOS) puts /usr/bin ahead of the stub
		if (existsSync("/usr/libexec/path_helper")) rows.push("bash -lc 'command -v git'");
		t.diagnostic(`live rows: ${rows.length}`);
		for (const row of rows) {
			assert.notEqual(childGit(row), stub, `${row}: the child still finds the stub`);
			assert.notEqual(fence.deniedGitCommand(row), null, `${row}: fence allowed it`);
		}
		for (const row of rows.slice(3)) assert.equal(fence.gitFenceVerdict(row), null, `${row}: pass 1 allows it, so pass 2 is what denies it`);
		assert.equal(fence.deniedGitCommand(`${probe} PATH=/usr/bin:/bin`), null, "without -k the fence allows it too");
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

// ---- regression net: the committed guard (fixed at BASELINE) vs this one ------------------------------------------------------

/** The commit whose guard is the baseline: the last one before the two-pass split. Fixed, not HEAD, so the baseline does not move when
 * later work on the guard is committed. */
const BASELINE = "0930011";

/** Commands DENIED by the guard at BASELINE that this guard ALLOWS on purpose. MUST STAY EMPTY: pass 1 is the committed guard and pass 2
 * can only deny, so nothing the committed guard denies is allowed now. The regression net fails while this list is non-empty. */
const RELAXATIONS: Record<string, string> = {};

/** Prefix words (pass 1's WRAP, plus zsh's precommand modifiers), each with the options that take an argument. */
const WRAPPERS: Record<string, string[]> = {
	command: [], builtin: [], "--": [], nohup: [], busybox: [], setsid: [], unbuffer: [], chronic: [], exec: ["-a"], doas: ["-u", "-C"],
	time: ["-f", "-o", "--format", "--output"], nice: ["-n", "--adjustment"], env: ["-u", "-C", "--unset", "--chdir"],
	sudo: ["-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--host", "--prompt", "--chdir", "--role", "--type", "--other-user"],
	timeout: ["-s", "-k", "--signal", "--kill-after"], stdbuf: ["-i", "-o", "-e"], caffeinate: ["-t", "-w"], ionice: ["-c", "-n", "-p", "-P", "-u"],
	xargs: ["-I", "-a", "-d", "-E", "-L", "-n", "-P", "-s"], noglob: [], nocorrect: [], "-": [], repeat: [],
};
/** The wrappers of review items 1–2, each with an unclosed opener `o` in its argument, around command `c`. */
const WRAPPED: ((o: string, c: string) => string)[] = [
	(o, c) => `env -u V${o} ${c}`, (o, c) => `exec -a n${o} ${c}`, (o, c) => `sudo -u u${o} ${c}`, (o, c) => `doas -u u${o} ${c}`,
	(o, c) => `nice -n n${o} ${c}`, (o, c) => `time -f f${o} ${c}`, (o, c) => `timeout -s s${o} 5 ${c}`, (o, c) => `stdbuf -o L${o} ${c}`,
	(o, c) => `xargs -I r${o} ${c}`, (o, c) => `env -C d${o} ${c}`, (o, c) => `find . -name x${o} -exec ${c} \\;`,
];
const DECLARING = ["declare", "typeset", "local", "readonly", "export", "integer", "float"];
const OPENERS = ["[", "(", "{", "'", '"'];
/** Assignments to either name or to a GIT_* variable that pass 1 checks. */
const TARGETS = ["PATH=/usr/bin:/bin", "PATH[0]=/usr/bin", "PATH+=:/x", "PILEAD_SHIM_DIR=/tmp", "GIT_PAGER='git commit'", "GIT_EDITOR='git push'",
	"GIT_SSH_COMMAND='git push'", "GIT_CONFIG_GLOBAL=/tmp/x", "GIT_CONFIG_KEY_0=core.pager", "GIT_EXEC_PATH=/tmp"];
/** The item `x` placed in each position among `fill` (0 to 2 of them). */
const positions = (fill: string[], x: string): string[][] => {
	const out: string[][] = [[x]];
	for (const f of fill) out.push([f, x], [x, f]);
	if (fill.length >= 2) out.push([fill[0], fill[1], x], [fill[0], x, fill[1]], [x, fill[0], fill[1]]);
	return out;
};
/** A single-quoted shell word holding `t`. */
const sq = (t: string) => `'${t.replace(/'/g, `'\\''`)}'`;

/** Pass 2's change 1: git named by path, and the wrappers (known to pass 1 or not) that run it. */
const GIT_PATHS = ["/usr/bin/git", "./git", "../x/git", "~/bin/git", "$HOME/bin/git", "${HOME}/bin/git", "\"$HOME\"/bin/git", "/usr/bin/g''it",
	"/usr/bin/gi${x}t", "/opt/homebrew/bin/git", "/usr/libexec/git-core/git-commit", "lib/../../usr/bin/git"];
const PATH_RUNNERS = ["", "arch -arm64", "script -q /dev/null", "taskpolicy -b", "sandbox-exec -p x", "caffeinate -i", "nice", "nohup", "env", "command",
	"xargs", "time", "env FOO=1", "sudo", "exec"];
/** Change 2: login shells, and forms that look a program up on a default PATH. */
const LOGIN_FORMS = ["bash -l", "bash -lc", "bash -l -c", "bash -il -c", "bash --login -c", "/bin/bash -l -c", "sh -l -c", "sh -lc", "dash -l -c",
	"zsh -l -c", "zsh -lc", "zsh -o login -c", "zsh --login -c", "ksh -l -c", "exec -l bash -c", "exec -a -sh sh -c", "su - bob -c", "su -l bob -c",
	"sudo su - -c", "login -f bob", "command -p", "builtin command -p", "command -p -- bash -c"];
/** Change 3: `env` options that clear or replace the environment, alone, bundled, attached, via `-S` and after an assignment. */
const ENV_OPTS = ["-i", "-iv", "-vi", "-0i", "-", "-u PATH", "-uPATH", "-vuPATH", "-u PILEAD_SHIM_DIR", "-u FOO -u PATH", "--unset PATH",
	"--unset=PATH", "--uns=PILEAD_SHIM_DIR", "--ignore-environment", "--ignore", "-P /usr/bin", "-P/usr/bin", "-vP/usr/bin", "-S '-i'",
	"-S'-iv'", "-S '-u PATH'", "-vS '-P /usr/bin'", "--split-string='-'", "FOO=1 -i", "FOO=1 --ignore-environment"];
/** Their near misses, which keep the environment. */
const ENV_KEEP = ["", "-v", "-u FOO", "-uFOO", "--unset=FOO", "-C sub", "-0", "NODE_ENV=test", "-S 'NODE_ENV=test'"];

/** The regression corpus: every table row, plus (a) an unclosed opener in each argument position of each declaring builtin and each
 * wrapper, before an assignment to either name or a GIT_* variable, (b) the wrappers of review items 1–2 with an unclosed opener around
 * each git row, (c) program names split by quotes, backslashes and expansions inside `(( ))`, `$(( ))` and `sh`/`dash`/`bash`/`zsh -c`,
 * (d) pass 2's changes 1–3: git by path behind each runner, login shells and `command -p`, and `env`'s environment options, each
 * around a git command and an ordinary one, bare and inside `bash -c`. */
function corpus(): Set<string> {
	const rows = [...DENIED, ...ALLOWED].map((r) => r[0]).concat(REF_MOVES, NEVER, LIVE_PATH_ROWS);
	const out = new Set(rows);
	for (const c of rows) if (/git/.test(c)) for (const wrap of WRAPPED) for (const o of OPENERS) out.add(wrap(o, c));
	for (const o of OPENERS) for (const t of TARGETS) {
		for (const b of DECLARING) for (const x of [o, `a${o}`]) for (const args of positions(["-x", "v=1"], x)) out.add(`${b} ${args.join(" ")} ${t}`);
		for (const [w, opts] of Object.entries(WRAPPERS)) {
			const lists = [...positions(["-x"], `a${o}`), [o], ...opts.map((op) => [op, `v${o}`]), ...opts.map((op) => [`${op}v${o}`])];
			for (const args of lists) for (const tail of ["make", "git status"]) out.add(`${w} ${args.join(" ")} ${t} ${tail}`);
		}
	}
	const gits = ["gi\"\"t", "g''it", "g\\it", "gi${x}t", "gi$x''t", "g$(echo)it", "g`echo`it", "${g}", "$g", "\"git\"", "\\git", "git"];
	for (const g of gits) for (const dir of ["", "/usr/bin/"]) for (const sep of [" ", "${IFS}", "$IFS"]) for (const sub of ["commit", "push", "co''mmit"]) {
		const p = `${dir}${g}${sep}${sub}`;
		for (const a of [`((${p}))`, `(( ${p} ))`, `$((${p}))`, `echo $((${p}))`, `: $(( ${p} ))`]) {
			out.add(a);
			for (const shell of ["sh", "dash", "bash", "zsh"]) out.add(`${shell} -c ${sq(a)}`);
		}
		for (const shell of ["sh", "dash", "bash", "zsh"]) out.add(`${shell} -c ${sq(p)}`);
	}
	const around = (c: string) => { out.add(c); out.add(`bash -c ${sq(c)}`); out.add(`x=$(${c})`); };
	for (const p of GIT_PATHS) for (const r of PATH_RUNNERS) for (const sub of ["commit -m x", "push", "status", ""]) around(`${r} ${p} ${sub}`.trim());
	for (const l of LOGIN_FORMS) for (const c of ["make", "'npm test'", "'git status'", "'git commit -m x'", "'command -v git'"]) around(`${l} ${c}`);
	for (const o of [...ENV_OPTS, ...ENV_KEEP]) for (const c of ["make", "npm test", "git status", "git commit -m x", "python3 x.py"]) around(`env ${o} ${c}`.replace(/ +/g, " "));
	return out;
}

/** The committed guard: every extensions/*fence*.ts at BASELINE, imported from a temp dir. */
async function committedGuard(t: any): Promise<{ mod: any; cleanup: () => void } | null> {
	const root = fileURLToPath(new URL("../..", import.meta.url));
	if (spawnSync("git", ["cat-file", "-e", `${BASELINE}^{commit}`], { cwd: root }).status !== 0) {
		t.skip(`commit ${BASELINE} is not in this clone (a shallow clone?), so this check did not run — fetch ${BASELINE} to run it`);
		return null;
	}
	const ls = spawnSync("git", ["ls-tree", "--name-only", BASELINE, "extensions/"], { cwd: root, encoding: "utf8" });
	const files = ls.status === 0 ? ls.stdout.split("\n").filter((f) => /fence[^/]*\.ts$/.test(f)) : [];
	if (!files.includes("extensions/bash-fence.ts")) { t.skip(`commit ${BASELINE} has no extensions/bash-fence.ts, so this check did not run`); return null; }
	const dir = mkdtempSync(join(tmpdir(), "fence-head-"));
	for (const f of files) writeFileSync(join(dir, f.slice("extensions/".length)), spawnSync("git", ["show", `${BASELINE}:${f}`], { cwd: root, encoding: "utf8" }).stdout);
	const mod: any = await tsImport(pathToFileURL(join(dir, "bash-fence.ts")).href, { parentURL: import.meta.url } as any);
	return { mod: typeof mod.deniedGitCommand === "function" ? mod : mod.default, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

test("pass 1 is the committed guard: for every corpus command its verdict and reason equal the committed guard's", async (t) => {
	const head = await committedGuard(t);
	if (!head) return;
	try {
		// before the two-pass split the committed guard IS pass 1 (`deniedGitVerdict`); after it, its `gitFenceVerdict`
		const committedPass1 = head.mod.gitFenceVerdict ?? head.mod.deniedGitVerdict;
		const diffs: string[] = [];
		for (const cmd of corpus()) {
			const want = committedPass1(cmd), got = fence.gitFenceVerdict(cmd);
			if (JSON.stringify(got) !== JSON.stringify(want)) diffs.push(`${JSON.stringify(cmd)}: committed ${JSON.stringify(want)}, pass 1 ${JSON.stringify(got)}`);
		}
		assert.deepEqual(diffs, [], `${diffs.length} command(s) where pass 1 differs from the committed guard:\n${diffs.slice(0, 50).join("\n")}`);
	} finally {
		head.cleanup();
	}
});

test("two passes: DENIED when either pass denies, pass 1's verdict first; pass 2 never changes a pass 1 denial", () => {
	const wrong: string[] = [];
	let byPass1 = 0, byPass2 = 0;
	for (const cmd of corpus()) for (const shell of ["bash", "zsh"]) {
		const first = fence.gitFenceVerdict(cmd), second = shim.shimFenceVerdict(cmd, { shell, gitCheck: fence.gitFenceVerdict });
		const got = fence.deniedGitVerdict(cmd, { shell }), want = first ?? second;
		if (JSON.stringify(got) !== JSON.stringify(want)) wrong.push(`${JSON.stringify(cmd)} (${shell}): got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
		if (first) byPass1++; else if (second) byPass2++;
	}
	assert.deepEqual(wrong, []);
	assert.ok(byPass1 > 1000 && byPass2 > 100, `pass 1 denies ${byPass1}, pass 2 adds ${byPass2}`);
});

test("regression net: nothing the committed guard denies is allowed now, and the list of relaxations is empty", async (t) => {
	assert.deepEqual(Object.keys(RELAXATIONS), [], "no relaxations: pass 1 is the committed guard and pass 2 can only deny");
	const head = await committedGuard(t);
	if (!head) return;
	try {
		const all = corpus(), relaxed: string[] = [];
		for (const cmd of all) for (const shell of ["bash", "zsh"]) {
			if (head.mod.deniedGitCommand(cmd, { shell }) === null) continue;
			if (fence.deniedGitCommand(cmd, { shell }) === null) relaxed.push(`${JSON.stringify(cmd)} (${shell})`);
		}
		assert.deepEqual(relaxed, [], `${relaxed.length} command(s) denied at ${BASELINE} are allowed now:\n${relaxed.join("\n")}`);
		assert.ok(all.size > 20000, `corpus: ${all.size} commands`);
		t.diagnostic(`corpus: ${all.size} commands, each checked under bash and zsh; 0 relaxations`);
	} finally {
		head.cleanup();
	}
});

/** Rows whose wrapper variants (WRAPPED × OPENERS) the guard ALLOWS — 105 commands the guard before the parse-based `defines` denied.
 * In each, `alias`/`enable`/`eval`/`function` is only an argument of an outside program, or the text is a syntax error, so nothing is
 * defined in the shell that then runs the file tool. `tool`: the line the row's file tool (or last program) prints when it runs; SYS is
 * the git stand-in's directory, ROOT the temp root (HOME, and where the project paths point). */
const DEFINER_ARG_ROWS: [row: string, tool: string][] = [
	["alias ls=env; ls /usr/bin/git commit", "ls SYS/git commit"],
	["eval 'cat() { \"$@\"; }'; cat /usr/bin/git commit", "cat SYS/git commit"],
	["enable -f ./x.so ls; ls /usr/bin/git commit", "ls SYS/git commit"],
	["cd /Users/x/dev/git-tools && grep -rn function src", "grep -rn function src"],
	["cd /Users/x/dev/git-tools && npm run enable-flags", "npm run enable-flags"],
	["cd /Users/x/dev/git-tools && git add src/function.ts", "git add src/function.ts"],
	["cd /Users/x/dev/git-tools && node -e \"console.log(f())\"", "node -e console.log(f())"],
	["ls ~/code/git-hooks; echo alias", "ls ROOT/code/git-hooks"],
];

test("real /bin/bash: the wrapper variants the guard allows define nothing — the stub file tool runs, never the git stand-in", (t) => {
	if (!existsSync("/bin/bash")) return t.skip("no /bin/bash");
	const root = mkdtempSync(join(tmpdir(), "fence-definer-arg-")), bin = join(root, "bin"), sys = join(root, "sys"), log = join(root, "log");
	try {
		// every outside program and file tool the commands name, as a stub that prints its own name and arguments; PATH is only this
		// directory, so nothing but bash builtins and the stubs can run. `alias`/`enable`/`eval` are the programs `exec` would start.
		mkdirSync(bin);
		for (const p of ["env", "sudo", "doas", "nice", "stdbuf", "xargs", "timeout", "find", "ls", "cat", "grep", "npm", "node", "git", "alias", "enable", "eval"]) {
			writeFileSync(join(bin, p), "#!/bin/sh\nprintf '%s\\n' \"${0##*/} $*\" >> \"$STUB_LOG\"\n");
			chmodSync(join(bin, p), 0o755);
		}
		// the stand-in for the git path, reached only by path (never /usr/bin/git itself)
		mkdirSync(sys);
		writeFileSync(join(sys, "git"), "#!/bin/sh\nprintf 'STAND-IN %s\\n' \"$*\" >> \"$STUB_LOG\"\n");
		chmodSync(join(sys, "git"), 0o755);
		mkdirSync(join(root, "dev", "git-tools", "src"), { recursive: true });
		mkdirSync(join(root, "code", "git-hooks"), { recursive: true });
		const local = (s: string) => s.replaceAll("/usr/bin/git", `${sys}/git`).replaceAll("/Users/x/dev/", `${root}/dev/`);
		const env = { PATH: bin, HOME: root, STUB_LOG: log };
		const run = (cmd: string) => {
			writeFileSync(log, "");
			const r = spawnSync("/bin/bash", ["-c", local(cmd)], { cwd: root, env, encoding: "utf8" });
			return { status: r.status, lines: spawnSync("/bin/cat", [log], { encoding: "utf8" }).stdout.split("\n").filter(Boolean) };
		};
		const syntaxError = (cmd: string) => spawnSync("/bin/bash", ["-n", "-c", local(cmd)], { env, encoding: "utf8" }).status !== 0;
		// controls: the harness sees a definition that does reach the stand-in, and a plain file tool reaching its stub
		for (const c of ["eval 'cat() { \"$@\"; }'; cat /usr/bin/git commit", "function ls { \"$@\"; }; ls /usr/bin/git commit"]) {
			assert.deepEqual(run(c).lines, ["STAND-IN commit"], `control: ${c}`);
		}
		assert.deepEqual(run("ls /usr/bin/git commit").lines, [`ls ${sys}/git commit`], "control: the stub file tool");
		assert.equal(syntaxError("env -u V( ls"), true, "control: an unquoted `(` after a word is a syntax error");
		const cmds: [string, string][] = [];
		for (const [row, tool] of DEFINER_ARG_ROWS) for (const wrap of WRAPPED) for (const o of OPENERS) {
			const c = wrap(o, row);
			if (fence.deniedGitCommand(c, { shell: "bash" }) === null && fence.deniedGitCommand(c, { shell: "zsh" }) === null) cmds.push([c, tool]);
		}
		assert.equal(cmds.length, 105, "the 105 wrapper variants the parse-based `defines` stopped denying");
		const failures: string[] = [], counts = { syntax: 0, tool: 0, exec: 0, find: 0 };
		for (const [c, tool] of cmds) {
			const { status, lines } = run(c), want = tool.replace("SYS", sys).replace("ROOT", root);
			const why = (m: string) => failures.push(`${c}: ${m} — exit ${status}, ran ${JSON.stringify(lines)}`);
			if (lines.some((l) => l.startsWith("STAND-IN"))) why("the git stand-in ran");
			else if (syntaxError(c)) { counts.syntax++; if (status === 0 || lines.length) why("a syntax error, yet it ran something or exited 0"); }
			// `exec PROG …` replaces the shell with the stub PROG (an outside program), so nothing after it runs
			else if (c.startsWith("exec ")) { counts.exec++; if (lines.length !== 1 || lines[0] === want || lines[0].startsWith(`${want} `)) why("`exec` did not hand the shell to one stub"); }
			// `find … -exec TOOL …`: the file tool is find's argument, which a real find runs as its own child
			else if (!lines.some((l) => l === want || l.startsWith(`${want} `)) && lines.some((l) => l.startsWith("find ") && l.endsWith(` -exec ${want}`))) counts.find++;
			else { counts.tool++; if (!lines.some((l) => l === want || l.startsWith(`${want} `))) why(`the stub file tool did not run as \`${want}\``); }
		}
		assert.deepEqual(failures, [], `${failures.length} command(s) failed:\n${failures.join("\n")}`);
		t.diagnostic(`ran ${cmds.length} commands under /bin/bash: ${counts.syntax} syntax errors (exit non-zero, nothing run), ${counts.tool} ran the stub file tool, ${counts.exec} exec'd a stub, ${counts.find} handed the file tool to the find stub; the git stand-in ran in none`);
	} finally {
		rmSync(root, { recursive: true, force: true });
	}
});

test("real /bin/bash: `: ${PILEAD_SHIM_DIR[1]=x}` un-exports PILEAD_SHIM_DIR from a child, and the fence denies it", (t) => {
	if (!existsSync("/bin/bash")) return t.skip("no /bin/bash");
	const childSees = (pre: string) => spawnSync("/bin/bash", ["-c", `{ ${pre}; } >/dev/null 2>&1; /bin/sh -c 'echo "\${PILEAD_SHIM_DIR-unset}"'`], {
		env: { PATH: "/usr/bin:/bin", PILEAD_SHIM_DIR: "/shim" }, encoding: "utf8",
	}).stdout.trim();
	assert.equal(childSees(":"), "/shim", "control");
	assert.equal(childSees(": ${PILEAD_SHIM_DIR[1]=x}"), "unset");
	assert.notEqual(fence.deniedGitCommand(": ${PILEAD_SHIM_DIR[1]=x}"), null);
});

test("never throws on hostile input; deep nesting fails closed", () => {
	const deep = "bash -c ".repeat(1) + "'" + "$(".repeat(40) + "git status" + ")".repeat(40) + "'";
	assert.notEqual(fence.deniedGitCommand(deep), null, "too deep → denied");
	for (const junk of ["\\", "$", "`", "$(", "${", "<<", "<<EOF", "|||", ")))", "\u0000git commit", "a".repeat(20000)]) {
		assert.doesNotThrow(() => fence.deniedGitCommand(junk), junk.slice(0, 20));
	}
	assert.equal(fence.deniedGitCommand(undefined as any), null);
});

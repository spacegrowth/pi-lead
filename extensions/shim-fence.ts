/**
 * shim-fence: PASS 2 of the executor's bash guard (bash-fence.ts). DENY-ONLY: `shimFenceVerdict` returns a hit or null, and
 * bash-fence.ts runs it only on commands pass 1 allows, so nothing here can allow what pass 1 denies. Pure, and self-contained: its own
 * scanner, no code shared with pass 1 (whose parser stays as committed). It denies what steps around `pilead spawn`'s git shim
 * (lib/pilead/shim.py), which pass 1 does not see:
 *   THE RULE — a command is denied when PATH or PILEAD_SHIM_DIR (and zsh's `path`, which is PATH as an array) sits where the shell
 *   would change its value, type, scope or export state: (a) an assignment's left side, subscript or not; (b) an argument of a builtin
 *   that declares, scopes or sets variables (DECLARERS, SETTERS; `-p` alone and a bare `export PATH` are queries); (c) arithmetic
 *   (`let`, `(( ))`, `$(( ))`, `$[ ]`, subscripts, `[[ -eq ]]` operands, integer values) next to an assigning operator; (d) an
 *   assigning `${NAME=…}`/`${NAME:=…}`; (e) any of those behind a prefix word (WRAP); (f) after `set -k`, any `NAME=value` argument.
 *   Quoted text given to an ordinary program is data, not such a position.
 * plus: removing, writing, renaming, linking over or changing the mode of anything under `$PILEAD_SHIM_DIR`; git named by path in
 * program position (the command name, and every word of a command whose program is not a plain file tool, whatever wrapper runs it:
 * `arch -arm64 /usr/bin/git …`, `env -P DIR git`; an argument written as one quoted string is data) — an argument of a plain file tool
 * (`cd`, `ls`, `cat`, `grep`, …) is data, so a project folder named `git` or `git-…` works; a shell that resets PATH (a login shell: `bash -l`, `--login`, `zsh -o login`, `exec -l`, `su -`, `login`; and
 * `command -p`); `env` options that clear or replace the environment (`-i`, `-`, `-u NAME`, `-P`, `-S STR` holding one, bundled or
 * not, and any option it does not know); and zsh's own forms
 * (precommand modifiers, `repeat`, `foreach`, `emulate -c`, `set -A`, `integer`, `print -v`, …) when pi's shell is zsh or the text is
 * handed to `zsh`. A zsh-only prefix hides the command after it from pass 1, so that command goes to `gitCheck` (pass 1) too.
 * WHERE THE READING IS UNCERTAIN, DENY: every string is scanned twice — once reading `NAME[ … ]` with blanks as one word where bash may
 * (bash >= 4 in declaring-builtin arguments), once splitting at every blank (bash 3.2) — and denied if either reading denies; `(( ))`
 * and `$(( ))` are checked both as arithmetic and as commands; a name spelled with quotes or expansions (`P""ATH`, `P${x}ATH`) is
 * taken as the shim name it could spell; an unclosed quote, `$(`, `${` or `NAME[` command word fails closed. SHORTCUT: a name
 * computed wholly at run time (`v=PATH; export "$v=…"`) is not visible in the text; the lead handles that class another way. */

/** `command`: the offending simple command, whitespace-normalised; `reason`: why. */
export interface ShimHit { command: string; reason?: string }
/** `shell`: the shell pi runs the command with (anything but zsh gets the bash rules). `gitCheck`: pass 1, for a command only zsh runs. */
export interface ShimOptions { shell?: string; gitCheck?: (cmd: string) => ShimHit | null }

/** Env vars whose value a program runs as a command: the value is scanned like a `sh -c` string. */
const CMD_VARS = new Set(["GIT_PAGER", "PAGER", "GIT_EDITOR", "EDITOR", "VISUAL", "GIT_SEQUENCE_EDITOR", "GIT_SSH_COMMAND", "GIT_SSH", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_EXTERNAL_DIFF", "GIT_PROXY_COMMAND", "PROMPT_COMMAND"]);
const BYPASS = "bypasses the git shim";
const SHIM_FILE = `changing the git shim in $PILEAD_SHIM_DIR ${BYPASS}`;
/** The refusal reason for changing shim name `name`; zsh's `path` is an everyday variable name elsewhere, so say what to do instead. */
const why = (name: string, what = "changing") =>
	name === "path" ? `in zsh \`path\` is PATH as an array, so changing it ${BYPASS} — rename the variable (e.g. \`p\` or \`file_path\`)` : `${what} ${name} ${BYPASS}`;

/** The shell a command string runs in. zsh ties its `path` array to PATH, so there `path` is a shim name too. */
interface Sh { zsh: boolean; names: readonly string[]; arg: RegExp }
function mkSh(zsh: boolean): Sh {
	const names = ["PATH", "PILEAD_SHIM_DIR", ...(zsh ? ["path"] : [])];
	// arg: a SETTERS argument naming a shim variable — `PATH`, `PATH[1]`, `-aPATH` (option with its value attached), zparseopts `a=PATH`
	return { zsh, names, arg: new RegExp(`(^(-[A-Za-z]*)?|=)(${names.join("|")})(\\[|$)`) };
}
const BASH = mkSh(false), ZSH = mkSh(true);

/** An expansion inside a name: `${…}`, `$(…)`, backticks, `$NAME`, `$1`. */
const EXP = /\$\{[^}]*\}?|\$\([^)]*\)?|`[^`]*`?|\$[A-Za-z_]\w*|\$[0-9@*#?$!-]/g;
/** The shim name that `name` is or could spell. A name with expansions in it (`P${x}ATH`, `${p}ATH`) is taken as any text they could
 * give (fail closed), when it holds at least one literal character; one wholly computed at run time (`$v`) is out of reach. */
function shimName(name: string, sh: Sh): string | null {
	return sh.names.includes(name) ? name : couldSpell(name, sh.names);
}
/** Which of `names` a name spelled with expansions could give; null for a literal name or one with no literal character. */
function couldSpell(name: string, names: readonly string[]): string | null {
	if (!/[$`]/.test(name) || !/\w/.test(name.replace(EXP, ""))) return null;
	let re = "", last = 0;
	for (const m of name.matchAll(EXP)) { re += esc(name.slice(last, m.index)) + "[\\s\\S]*"; last = m.index + m[0].length; }
	const rx = new RegExp(`^${re}${esc(name.slice(last))}$`);
	return names.find((n) => rx.test(n)) ?? null;
}
/** Env vars that point git at other config or code (as pass 1's GIT_CFG_VAR), and spelled out for names spelled with expansions. */
const GIT_CFG_VAR = /^GIT_(CONFIG(_PARAMETERS|_KEY_\d+|_GLOBAL|_SYSTEM)?|EXEC_PATH|TEMPLATE_DIR)$/;
const GIT_CFG_NAMES = ["GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_KEY_0", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR"];
const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** The variable a word names as an assignment or builtin argument: `PATH`, `PATH[0]`, `PATH[ 0 ]+=x` all name PATH. */
const nameOf = (a: string) => a.replace(/(\[|\+?=)[\s\S]*$/, "");
const isShimVar = (a: string, sh: Sh) => shimName(nameOf(a), sh);
/** Builtins whose arguments declare or scope variables. */
const DECLARERS = new Set(["declare", "typeset", "local", "readonly", "export"]);
/** zsh only: `integer`/`float` are `typeset -i`/`-F`. */
const ZSH_DECLARERS = new Set(["integer", "float"]);
const isDeclarer = (n: string, sh: Sh) => DECLARERS.has(n) || (sh.zsh && ZSH_DECLARERS.has(n));
/** Builtins that store into a variable named by an argument (`printf`/`print` only with `-v`). */
const SETTERS = new Set(["unset", "read", "mapfile", "readarray", "getopts", "wait", "printf"]);
/** zsh only: its own and its module builtins that do the same. */
const ZSH_SETTERS = new Set(["print", "vared", "zparseopts", "zstyle", "strftime", "zformat", "zstat", "sysread", "zselect"]);
/** A word naming the shim directory (`"$PILEAD_SHIM_DIR/git"`, `${PILEAD_SHIM_DIR}`, `$(printenv PILEAD_SHIM_DIR)`). */
const isShimPath = (a: string) => /PILEAD_SHIM_DIR/.test(a);
/** A path to git or a `git-*` helper: a word with a `/` whose last component is `git`, starts with `git-`, or is spelled with
 * expansions that could give `git` (`gi${x}t`). */
function gitPath(v: string): boolean {
	const k = v.lastIndexOf("/"), last = v.slice(k + 1);
	return k >= 0 && (last.startsWith("git-") || isGit(last));
}
/** A last path part that is `git`, or spelled with expansions that could give it. */
const isGit = (last: string) => last === "git" || couldSpell(last, ["git"]) !== null;
/** A path that can reach a git outside the project: it starts at `/`, `~`, `.` or an expansion, or climbs with `..`. An argument like
 * `lib/git` or `src/git-tools` stays inside the tree (a git put there is the renamed-copy class the lead handles another way). */
const ANCHORED = /^[/~.$`]|(^|\/)\.\.(\/|$)/;
/** A word written as one quoted string (`'…'`, `"…"`): as an argument it is data, even when it names git (`grep '/usr/bin/git' f`). */
function wholeQuoted(raw: string | undefined): boolean {
	if (!raw || (raw[0] !== "'" && raw[0] !== '"')) return false;
	try { return close(raw, 0) === raw.length - 1; } catch { return false; }
}
const LOGIN = `a login shell runs the system profile, which rebuilds PATH and ${BYPASS}`;
const EMPTY_ENV = (opt: string) => `\`env ${opt}\` starts the program with an empty environment (a default PATH), which ${BYPASS}`;
/** An assigning operator right after an arithmetic operand (`=`, `+=`, `<<=`, `**=`, `++`, `--`); `==`, `<=`, `!=` compare. */
const ARITH_SET_AFTER = /^(\+\+|--|(\*\*|<<|>>|&&|\|\||\^\^|[-+*\/%&|^])?=(?!=))/;
const ARITH_CMP = /^-(eq|ne|lt|le|gt|ge)$/;
/** An arithmetic operand: identifier characters and expansions glued together (`PATH`, `P${x}ATH`, `$v`). */
const OPERAND = /(?:\$\{[^}]*\}?|\$\([^)]*\)?|`[^`]*`?|\$[A-Za-z_]\w*|\$[0-9@*#?$!-]|\w)+/g;

/** Index of the `]` closing the `[` at `k` (nesting-aware), or -1. */
function bracketEnd(t: string, k: number): number {
	let d = 0;
	for (let j = k; j < t.length; j++) if (t[j] === "[") d++; else if (t[j] === "]" && --d === 0) return j;
	return -1;
}

/** `NAME=v`, `NAME+=v`, `NAME[sub]=v` (subscript bracket-balanced, whitespace allowed), or null. `dyn`: the name holds an expansion
 * (`P${x}ATH=v`), so the shell does not assign it here, but a declaring builtin or `eval` given it would. */
function splitAssign(v: string): { name: string; sub?: string; value: string; dyn: boolean } | null {
	const m = /^(?:[A-Za-z_]|\$\{[^}]*\}|\$[A-Za-z_]\w*)(?:\w|\$\{[^}]*\}|\$[A-Za-z_]\w*)*/.exec(v);
	if (!m) return null;
	let k = m[0].length, sub: string | undefined;
	if (v[k] === "[") {
		const e = bracketEnd(v, k);
		if (e < 0) return null;
		sub = v.slice(k + 1, e);
		k = e + 1;
	}
	if (v[k] === "+") k++;
	return v[k] === "=" ? { name: m[0], sub, value: v.slice(k + 1), dyn: /\$/.test(m[0]) } : null;
}
const isAssign = (v: string) => splitAssign(v) !== null;

/** The shim name arithmetic text assigns (`PATH=1`, `PATH[1]++`, `(PATH)=1`, `--path` in zsh, `arr[PATH[ 1 ]=0]`, `P""ATH=1`,
 * `P${x}ATH=1`), or null. Quotes and backslashes are dropped as `(( ))` drops them; `$PATH` is a value, not an assignable operand. */
function arithAssigns(text: string, sh: Sh): string | null {
	const t = text.replace(/["'\\]/g, "");
	for (const m of t.matchAll(OPERAND)) {
		const name = /^\d/.test(m[0]) ? null : shimName(m[0], sh);
		if (!name) continue;
		let k = m.index + m[0].length;
		while (/[\s)]/.test(t[k] ?? "")) k++; // `(PATH)=1`: a parenthesised operand is still the one assigned
		if (t[k] === "[") {
			const e = bracketEnd(t, k);
			if (e < 0) return name; // an unclosed subscript: cannot tell, so fail closed
			for (k = e + 1; /[\s)]/.test(t[k] ?? ""); k++);
		}
		if (ARITH_SET_AFTER.test(t.slice(k)) || /(\+\+|--)[\s(]*$/.test(t.slice(0, m.index))) return name;
	}
	return null;
}

/** The shim name assigned inside the subscript of `NAME[sub]…` (unset/read/`[[ -v ]]` evaluate it as arithmetic), or null. */
function subscriptAssigns(a: string, sh: Sh): string | null {
	const m = /^[A-Za-z_]\w*\[/.exec(a);
	if (!m) return null;
	const e = bracketEnd(a, m[0].length - 1);
	return arithAssigns(e < 0 ? a.slice(m[0].length) : a.slice(m[0].length, e), sh);
}

/** A `${…}` body: the shim name it assigns — `${PATH=x}`, `${PATH[1]:=x}`, zsh `${(A)path::=x}`, or arithmetic in its subscript or
 * `:offset:length` — or null. */
function paramAssigns(body: string, sh: Sh): string | null {
	const t = body.replace(/^\([^)]*\)/, "").replace(/^[#!^=~+]+/, "");
	const m = /^([A-Za-z_]\w*|[0-9@*#?$!-])/.exec(t);
	if (!m) return null;
	let k = m[0].length;
	if (t[k] === "[") {
		const e = bracketEnd(t, k);
		const hit = arithAssigns(t.slice(k + 1, e < 0 ? t.length : e), sh);
		if (hit || e < 0) return hit;
		k = e + 1;
	}
	const rest = t.slice(k);
	if (sh.names.includes(m[1]) && /^:{0,2}=/.test(rest)) return m[1];
	return /^:[^-=?+:]/.test(rest) ? arithAssigns(rest.slice(1), sh) : null;
}

const MAX_DEPTH = 8;
const KEYWORDS = new Set(["!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "coproc"]);
const SHELLS = new Set(["sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"]);
const STDIN_PATH = /^(-|\/dev\/stdin|\/dev\/fd\/0)$/;
const OPS = [";;&", "&>>", "<<<", "<<-", ";;", ";&", "&&", "&>", "||", "|&", "<<", "<>", "<&", ">>", ">|", ">&", ";", "&", "|", "(", ")", "<", ">"];
const SEPS = new Set([";;&", ";;", ";&", "&&", "||", "|&", ";", "&", "|", "(", ")"]);
const WRITE_OPS = new Set([">", ">>", ">|", "&>", "&>>", "<>"]);
const WORD_END = " \t\n;&|<>()";

/** Prefix words that run their operand: options (those taking an argument listed), then `pos` skips. A bare `--` counts as one. */
type WrapSpec = { arg?: string[]; pos?: number };
const WRAP: Record<string, WrapSpec> = {
	command: {}, builtin: {}, "--": {}, nohup: {}, busybox: {}, setsid: {}, unbuffer: {}, chronic: {}, exec: { arg: ["-a"] }, doas: { arg: ["-u", "-C"] },
	time: { arg: ["-f", "-o", "--format", "--output"] }, nice: { arg: ["-n", "--adjustment"] }, env: {}, // env's options: checkEnv
	sudo: { arg: ["-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--host", "--prompt", "--chdir", "--role", "--type", "--other-user"] },
	timeout: { arg: ["-s", "-k", "--signal", "--kill-after"], pos: 1 }, stdbuf: { arg: ["-i", "-o", "-e"] }, caffeinate: { arg: ["-t", "-w"] }, ionice: { arg: ["-c", "-n", "-p", "-P", "-u"] },
};
/** zsh only: precommand modifiers, and `repeat N cmd`. Pass 1 does not know them, so the command after them also goes to `gitCheck`. */
const ZSH_WRAP: Record<string, WrapSpec> = { noglob: {}, nocorrect: {}, "-": {}, repeat: { pos: 1 } };
const XARGS_WITH_ARG = new Set(["-a", "-d", "-E", "-L", "-n", "-P", "-s", "--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars"]);

class Unparseable extends Error {}

/** `v`: the dequoted value (expansions kept raw); `at`: where the word starts in its simple command's `src`; `joined`: blanks inside
 * `NAME[ … ]` were kept in the word (a joining reading); `raw`: the word as written. */
interface Word { v: string; quoted: boolean; at: number; procSub?: boolean; joined?: boolean; raw?: string }
/** `fds`: spans of `{NAME}>f` words, which pass 1 reads as the command name — the command after them is hidden from it. `defn`: an
 * unquoted `()` follows the command's words (`name() {`, `name () (`, `function name() {`): it defines a function. */
interface Simple { src: string; words: Word[]; start: number; end: number; heredoc?: string; herestring?: string; shim?: string; fds?: [number, number][]; defn?: boolean }
/** `sh`: the shell running the string; `join`: this reading keeps `NAME[ … ]` with blanks as one word; `keyword`: `set -k` is on;
 * `ints`/`refs`: names declared integer (values are arithmetic) or nameref (values name a variable) in this string; `redef`: this string
 * or one around it may define a function or alias (REDEFINES), so a file tool's name may run its arguments. */
interface Ctx { sh: Sh; join: boolean; keyword: boolean; gitCheck?: ShimOptions["gitCheck"]; ints?: Set<string>; refs?: Set<string>; redef: boolean }
type Deny = (reason?: string) => ShimHit;

class WordBuf {
	v = ""; quoted = false;
	lit(t: string, q: boolean): void { this.v += t; this.quoted ||= q; }
	exp(raw: string): void { this.v += raw; }
}

/** Index of the closer of the `(`, `{`, `"`, `'` or backtick at `i` (quote/escape/nesting-aware); throws when there is none. */
function close(src: string, i: number): number {
	const o = src[i];
	const shut = o === "(" ? ")" : o === "{" ? "}" : o;
	let depth = 0;
	for (let j = i; j < src.length; j++) {
		const c = src[j];
		if (o === "'") { if (j > i && c === "'") return j; }
		else if (c === "\\") j++;
		else if (j > i && c === shut && (shut === o || --depth === 0)) return j;
		else if (c === o && shut !== o) depth++;
		else if (j > i && (c === "`" || (o === "(" && (c === '"' || c === "'")))) j = close(src, j);
		else if (c === "$" && src[j + 1] === "(") j = close(src, j + 1);
	}
	throw new Unparseable(`unterminated ${o === "{" ? "${" : o} — could not parse the command`);
}

const C_ESC: Record<string, string> = { n: "\n", t: "\t", r: "\r", a: "\x07", b: "\b", e: "\x1b", E: "\x1b", f: "\f", v: "\v", "\\": "\\", "'": "'", '"': '"', "?": "?" };

/** Decode a `$'…'` body starting at `i`; returns [value, index after its quote]. */
function ansiC(src: string, i: number): [string, number] {
	let out = "";
	while (i < src.length && src[i] !== "'") {
		if (src[i] !== "\\") { out += src[i++]; continue; }
		const c = src[i + 1] ?? "";
		const m = /^(?:x([0-9a-fA-F]{1,2})|[uU]([0-9a-fA-F]{1,8})|([0-7]{1,3}))/.exec(src.slice(i + 1));
		if (m) out += String.fromCodePoint(Math.min(m[3] ? parseInt(m[3], 8) : parseInt(m[1] ?? m[2], 16), 0x10ffff));
		else out += C_ESC[c] ?? "\\" + c;
		i += 1 + (m ? m[0].length : 1);
	}
	if (i >= src.length) throw new Unparseable("unterminated $' quote — could not parse the command");
	return [out, i + 1];
}

/** A standalone hit (an assigning expansion, an arithmetic assignment) that `scan` reports like a denied simple command. */
const shimHit = (text: string, name: string): Simple => ({ src: text, words: [], start: 0, end: text.length, shim: why(name) });

/** A shim assignment in arithmetic text is a hit in `nested`. */
function arithShim(body: string, nested: Simple[], sh: Sh): void {
	const name = arithAssigns(body, sh);
	if (name) nested.push(shimHit(body, name));
}

/** A `$…` or backtick expansion at `i`: what it runs, and any shim assignment in it, go to `nested`; returns the next index. */
function expansion(src: string, i: number, q: boolean, depth: number, nested: Simple[], buf: WordBuf, ctx: Ctx): number {
	const nx = src[i + 1] ?? "";
	let end: number;
	if (src[i] === "`" || nx === "(") {
		const tick = src[i] === "`";
		end = close(src, tick ? i : i + 1);
		// `$(( … ))` is arithmetic, or a command substitution holding a subshell: check both readings
		if (!tick && src[i + 2] === "(") arithShim(src.slice(i + 3, end), nested, ctx.sh);
		nested.push(...parse(tick ? src.slice(i + 1, end).replace(/\\([\\`$"])/g, "$1") : src.slice(i + 2, end), depth + 1, ctx));
	} else if (nx === "[") { // bash's old `$[ … ]` arithmetic; unclosed, the rest of the text is checked as arithmetic
		end = bracketEnd(src, i + 1);
		if (end < 0) { arithShim(src.slice(i + 2), nested, ctx.sh); buf.lit("$", q); return i + 1; }
		arithShim(src.slice(i + 2, end), nested, ctx.sh);
		readDq(src.slice(i + 2, end), 0, null, depth, nested, new WordBuf(), ctx);
	} else if (nx === "{") {
		end = close(src, i + 1);
		const body = src.slice(i + 2, end), name = paramAssigns(body, ctx.sh);
		if (name) nested.push(shimHit(src.slice(i, end + 1), name));
		readDq(body, 0, null, depth, nested, new WordBuf(), ctx);
	} else {
		const m = /^([A-Za-z_]\w*|[0-9@*#?$!-])/.exec(src.slice(i + 1));
		if (!m) { buf.lit("$", q); return i + 1; }
		end = i + m[0].length;
	}
	buf.exp(src.slice(i, end + 1));
	return end + 1;
}

/** Double-quote body (term `"`) or an expanding heredoc/`${}` body (term null) from `i`. */
function readDq(src: string, i: number, term: '"' | null, depth: number, nested: Simple[], buf: WordBuf, ctx: Ctx): number {
	const escapable = term ? '$`"\\' : "$`\\";
	while (i < src.length) {
		const c = src[i];
		if (term && c === term) return i + 1;
		if (c === "\\" && src[i + 1] === "\n") i += 2;
		else if (c === "\\" && escapable.includes(src[i + 1] || "#")) { buf.lit(src[i + 1], true); i += 2; }
		else if (c === "$" || c === "`") i = expansion(src, i, true, depth, nested, buf, ctx);
		else { buf.lit(c, true); i++; }
	}
	if (term) throw new Unparseable("unterminated double quote — could not parse the command");
	return i;
}

/** One word from `i`. `assignOk` in a joining reading: blanks inside a subscript opened right after a bare name do not end it. */
function readWord(src: string, i: number, depth: number, nested: Simple[], ctx: Ctx, assignOk = false): [Word, number] {
	const buf = new WordBuf(), at = i;
	let br = 0, joined = false;
	while (i < src.length && (!WORD_END.includes(src[i]) || (br > 0 && (src[i] === " " || src[i] === "\t")))) {
		const c = src[i];
		let v: string;
		joined ||= c === " " || c === "\t"; // an unquoted blank reaches here only inside a joined `NAME[ … ]`
		if (c === "[" && (br > 0 || (ctx.join && assignOk && !buf.quoted && /^[A-Za-z_]\w*$/.test(buf.v)))) br++;
		else if (c === "]" && br > 0) br--;
		if (c === "\\") { if (src[i + 1] !== "\n" && i + 1 < src.length) buf.lit(src[i + 1], true); i += 2; }
		else if (c === "'") { const end = close(src, i); buf.lit(src.slice(i + 1, end), true); i = end + 1; }
		else if (c === "$" && src[i + 1] === "'") { [v, i] = ansiC(src, i + 2); buf.lit(v, true); }
		else if (c === '"' || (c === "$" && src[i + 1] === '"')) { buf.quoted = true; i = readDq(src, i + (c === "$" ? 2 : 1), '"', depth, nested, buf, ctx); }
		else if (c === "$" || c === "`") i = expansion(src, i, false, depth, nested, buf, ctx);
		else { buf.lit(c, false); i++; }
	}
	return [{ v: buf.v, quoted: buf.quoted, at, joined, raw: src.slice(at, i) }, i];
}

/** Whether the next word of a simple command whose words so far are `ws` may be read as an assignment: `ws` is only assignments,
 * after an optional leading `time`/`time -p`, or its command name is a declaring builtin. */
function assignPos(ws: Word[]): boolean {
	let k = 0;
	if (ws[0]?.v === "time" && !ws[0].quoted) k = ws[1]?.v === "-p" ? 2 : 1;
	while (k < ws.length && isAssign(ws[k].v)) k++;
	return k >= ws.length || (!ws[k].quoted && DECLARERS.has(ws[k].v));
}

/** `()` with only blanks between, matched where `lastIndex` points. */
const EMPTY_PARENS = /\(\s*\)/y;

/** Every simple command in `src` under the reading `ctx.join`, including those nested in substitutions (flattened). */
function parse(src: string, depth: number, ctx: Ctx): Simple[] {
	if (depth > MAX_DEPTH) throw new Unparseable("command nesting too deep to check");
	const out: Simple[] = [], fresh = (): Simple => ({ src, words: [], start: -1, end: -1 });
	let cur = fresh(), pending: { cmd: Simple; delim: string; strip: boolean; quoted: boolean }[] = [];
	const finish = () => { if (cur.start >= 0) { out.push(cur); cur = fresh(); } };
	const touch = (a: number, b: number) => { if (cur.start < 0) cur.start = a; cur.end = b; };
	let i = 0;
	while (i < src.length) {
		const c = src[i];
		if (c === " " || c === "\t") { i++; continue; }
		if (c === "\\" && src[i + 1] === "\n") { i += 2; continue; }
		if (c === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
		if (c === "\n") {
			finish();
			i++;
			for (const h of pending) { // heredoc bodies start on the line after their `<<`
				const body: string[] = [];
				while (i < src.length) {
					const nl = src.indexOf("\n", i);
					const line = src.slice(i, nl < 0 ? src.length : nl);
					i = nl < 0 ? src.length : nl + 1;
					if ((h.strip ? line.replace(/^\t+/, "") : line) === h.delim) break;
					body.push(line);
				}
				h.cmd.heredoc = body.join("\n");
				if (!h.quoted) readDq(h.cmd.heredoc, 0, null, depth, out, new WordBuf(), ctx); // `$(…)` still runs
			}
			pending = [];
			continue;
		}
		if ((c === "<" || c === ">") && src[i + 1] === "(") { // process substitution: a file name made by a command
			const end = close(src, i + 1);
			out.push(...parse(src.slice(i + 2, end), depth + 1, ctx));
			cur.words.push({ v: "/dev/fd/63", quoted: false, at: i, procSub: true });
			touch(i, end + 1);
			i = end + 1;
			continue;
		}
		// `(( … ))` / `for (( … ))` is arithmetic, or two nested subshells: check it as arithmetic here, then parse it as commands below
		if (c === "(" && src[i + 1] === "(") arithShim(src.slice(i + 2, close(src, i)), out, ctx.sh);
		// an unquoted `()` is a function definition (`name=()` is an empty array)
		if (c === "(" && src[i - 1] !== "=" && ((EMPTY_PARENS.lastIndex = i), EMPTY_PARENS.test(src))) { cur.defn = true; if (cur.start < 0) touch(i, i + 1); }
		const op = OPS.find((o) => src.startsWith(o, i));
		if (op && SEPS.has(op)) { finish(); i += op.length; continue; }
		if (op) { // a redirect: its target is not a word of the command
			touch(i, i + op.length);
			i += op.length;
			while (src[i] === " " || src[i] === "\t") i++;
			if (i >= src.length || WORD_END.includes(src[i])) continue;
			const [target, next] = readWord(src, i, depth, out, ctx);
			touch(i, next);
			i = next;
			if (op === "<<" || op === "<<-") pending.push({ cmd: cur, delim: target.v, strip: op === "<<-", quoted: target.quoted });
			else if (op === "<<<") cur.herestring = target.v;
			else if (WRITE_OPS.has(op) && isShimPath(target.v)) cur.shim = SHIM_FILE;
			continue;
		}
		const [w, next] = readWord(src, i, depth, out, ctx, assignPos(cur.words));
		const start = i;
		i = next;
		if (!w.quoted && (w.v === "{" || w.v === "}")) { finish(); continue; }
		// a reserved word opening a command is not part of the simple command; `coproc` stays, as its NAME is assigned
		if (!w.quoted && cur.start < 0 && KEYWORDS.has(w.v) && w.v !== "coproc") continue;
		if (!w.quoted && /^\d+$/.test(w.v) && (src[next] === "<" || src[next] === ">")) continue; // an fd
		// `{NAME}>f` (zsh, bash >= 4.1) stores the fd number it opens in NAME; bash 3.2 would run a program `{NAME}`
		if (!w.quoted && /^\{[A-Za-z_]\w*\}$/.test(w.v) && (src[next] === "<" || src[next] === ">")) {
			const name = shimName(w.v.slice(1, -1), ctx.sh);
			if (name) cur.shim = why(name);
			(cur.fds ??= []).push([start, next]);
			touch(start, next);
			continue;
		}
		cur.words.push(w);
		touch(start, next);
	}
	finish();
	return out;
}

const clip = (t: string) => ((t = t.replace(/\s+/g, " ").trim()).length > 200 ? t.slice(0, 199) + "…" : t);
const textOf = (s: Simple) => clip(s.src.slice(s.start, s.end));
const each = <T>(xs: T[], f: (x: T) => ShimHit | null): ShimHit | null => xs.reduce<ShimHit | null>((hit, x) => hit ?? f(x), null);

/** The verdict for one simple command (or a standalone hit). */
const verdict = (s: Simple, depth: number, ctx: Ctx): ShimHit | null =>
	s.shim ? { command: textOf(s), reason: s.shim } : gitByPath(s, ctx) ?? (s.fds ? gitBehind(withoutFds(s), s, ctx) : null) ?? checkWords(s.words, s, depth, ctx);
/** Git named by path in program position, so any wrapper that runs its arguments (`arch -arm64 /usr/bin/git commit`, `script`,
 * `taskpolicy`) reaches it past the shim. The command name counts whatever its spelling. Another word counts when it is anchored
 * (ANCHORED) and not written as one quoted string, unless the program is a plain file tool (FILE_TOOLS), whose arguments are data —
 * except what it runs (`find -exec PROG`, `rg --pre PROG`) and a script `source`/`.` reads that is named exactly `git`.
 * SHORTCUT: a quoted argument (`arch "/usr/bin/git" commit`) is exempt, as data to an ordinary program, because the guard cannot tell an
 * unknown wrapper from one — the ceiling is any wrapper not in WRAP; the upgrade is to exempt quoted paths only for a list of known
 * data-only programs (grep, echo, cat, …) and deny them everywhere else. */
function gitByPath(s: Simple, ctx: Ctx): ShimHit | null {
	const hit = gitPathWord(s.words, ctx.redef);
	return hit ? { command: textOf(s), reason: `naming git by path (${clip(hit.v)}) ${BYPASS} — if the path is data, not a program to run, put it in quotes` } : null;
}
/** Programs that only read, list, print or make their arguments: a git path given to one is data. `rg` and `find` run a program given
 * after `--pre` and `-exec`; `source`/`.` run a script. */
const FILE_TOOLS = new Set(["cd", "pushd", "ls", "cat", "head", "tail", "less", "wc", "stat", "file", "test", "[", "[[", "du", "find", "grep", "rg",
	"mkdir", "touch", "echo", "printf", "source", "."]);
/** REDEFINES: a string (or a string around it) that holds a DEFINITION, where a file tool's name may run its arguments, so FILE_TOOLS
 * gives no exemption. A definition is: a function definition (an unquoted `()`: `name() {`, `name () (`, `function name() {`); a
 * definer in program position (DEFINERS, `hash -p`, `unset -f`, also behind `builtin`/`command`/zsh's precommand modifiers, or spelled
 * with expansions that could give one); a write to zsh's `functions`/`aliases` tables; an exported `BASH_FUNC_…`; or, where pass 2
 * cannot tell, any definition-like text (DEFINE_TEXT) in a command that runs text in this shell (IN_SHELL: `eval '…'`, `trap`,
 * `source <(…)`). A word that is an argument, a path or quoted text (`grep function src`, `node -e "f()"`, `echo alias`) is not one.
 * SHORTCUT: a definition computed at run time (`eval "$v"`) or in a sourced file is not seen — the ceiling is text-visible definitions;
 * the upgrade is to also switch the exemption off wherever the string runs `eval` or `source` of text it cannot read. */
const DEFINERS = ["alias", "unalias", "enable", "function", "autoload", "functions"];
/** Prefix words that run a shell builtin (`builtin alias`, zsh `noglob alias`); options after them are skipped, and `repeat`'s count. */
const BUILTIN_PREFIX = new Set(["builtin", "command", "time", "--", "noglob", "nocorrect", "-", "repeat"]);
/** Commands that run text in this shell, so a definition in their text reaches the file tool after them. */
const IN_SHELL = new Set(["eval", "trap", "source", ".", "mapfile", "readarray", "emulate", "fc"]);
/** Definition-like text, for a command in IN_SHELL where pass 2 cannot tell (fail closed). */
const DEFINE_TEXT = /\(\s*\)|\b(function|alias|unalias|enable|autoload|functions|hash|unset|dis_functions|aliases|galiases|saliases|dis_aliases|dis_galiases|dis_saliases|BASH_FUNC_\w*)\b/;
/** zsh's function and alias tables written (`functions[ls]=…`, `aliases+=(…)`, `${aliases[ls]::=env}`), and an exported bash function. */
const DEFINE_TABLE = /\b(functions|dis_functions|aliases|galiases|saliases|dis_aliases|dis_galiases|dis_saliases)(\[|\+?=)|BASH_FUNC_/;
/** Whether `src`, parsed as `simples`, holds a definition (REDEFINES). */
function defines(src: string, simples: Simple[]): boolean {
	return DEFINE_TABLE.test(src) || simples.some((s) => {
		if (s.defn) return true;
		const ws = s.words;
		let k = 0;
		while (k < ws.length && isAssign(ws[k].v)) k++;
		while (k < ws.length && BUILTIN_PREFIX.has(ws[k].v)) {
			const count = ws[k++].v === "repeat";
			while (k < ws.length && /^-./.test(ws[k].v)) k++;
			if (count) k++;
		}
		const p = ws[k]?.v ?? "", args = ws.slice(k + 1).map((w) => w.v);
		if (DEFINERS.includes(p) || couldSpell(p, [...DEFINERS, "hash", "unset", ...IN_SHELL]) !== null) return true;
		if (p === "hash" && args.some((a) => /^-[A-Za-z]*p/.test(a))) return true;
		if (p === "unset" && args.some((a) => /^-[A-Za-z]*f/.test(a))) return true;
		return IN_SHELL.has(p) && DEFINE_TEXT.test(`${s.src.slice(s.start, s.end)}\n${s.heredoc ?? ""}\n${s.herestring ?? ""}`);
	});
}
/** The word of `ws` (one simple command, or what `find -exec` runs) that names git by path in program position, per gitByPath. */
function gitPathWord(ws: Word[], redef: boolean): Word | undefined {
	const cmd = ws.findIndex((w) => !isAssign(w.v));
	const arg = (w: Word) => !w.procSub && gitPath(w.v) && ANCHORED.test(w.v) && !wholeQuoted(w.raw);
	const tool = cmd >= 0 && !redef ? ws[cmd].v : "";
	if (!FILE_TOOLS.has(tool)) return ws.find((w, k) => !w.procSub && gitPath(w.v) && (k === cmd || arg(w)));
	const before = ws.slice(0, cmd).find(arg), rest = ws.slice(cmd + 1);
	if (before) return before;
	if (tool === "source" || tool === ".") return rest.find((w) => arg(w) && isGit(w.v.slice(w.v.lastIndexOf("/") + 1)));
	if (tool === "rg") return rest.find((w, k) => k > 0 && rest[k - 1].v === "--pre" && arg(w));
	if (tool !== "find") return undefined;
	for (let j = 0; j < rest.length; j++) {
		if (!/^-(exec|execdir|ok|okdir)$/.test(rest[j].v)) continue;
		let k = j + 1;
		while (k < rest.length && !execEnd(rest, k)) k++;
		const hit = gitPathWord(rest.slice(j + 1, k), redef);
		if (hit) return hit;
		j = k;
	}
	return undefined;
}
/** Whether word `k` of `ws` ends a `find -exec` segment: `;` (written `\;`, `';'`, `";"`), or `+` right after `{}` — as find reads it,
 * any other `+` is a word of the command the segment runs. */
const execEnd = (ws: Word[], k: number) => ws[k].v === ";" || (ws[k].v === "+" && ws[k - 1]?.v === "{}");
/** The simple command's text with its `{NAME}` fd-name words blanked, as zsh and bash >= 4.1 read it. */
const withoutFds = (s: Simple) => [...s.src.slice(s.start, s.end)].map((c, k) => (s.fds!.some(([a, b]) => k + s.start >= a && k + s.start < b) ? " " : c)).join("");

/** Scan `src` under both readings of `NAME[ … ]` with blanks (joined, then split); denied if either denies. `keyword`: `set -k` was
 * on where this string runs — passed down to every nested string, even a new shell's (fail closed: an exported SHELLOPTS carries it). */
function scan(src: string, depth: number, sh: Sh, keyword: boolean, gitCheck: ShimOptions["gitCheck"], redef = false): ShimHit | null {
	for (const join of [true, false]) {
		const base: Ctx = { sh, join, keyword, gitCheck, redef };
		const simples = parse(src, depth, base), ctx = attrsOf(simples, { ...base, redef: redef || defines(src, simples) });
		const hit = each(simples, (s) => verdict(s, depth, ctx));
		if (hit) return hit;
	}
	return null;
}

/** `set -k`/`set -o keyword` (or a SHELLOPTS naming keyword) anywhere in the string, and the names it declares integer (`declare -i n`,
 * zsh `integer n`) or nameref (`local -n r`), wherever the declaration sits: a later `make PATH=x`, `n=PATH[1]=0` or `r=PATH` then
 * changes PATH. Anywhere, not only before: a loop body runs again after a later `set -k`. SHORTCUT: one string only — pi starts a
 * fresh shell per command, so nothing carries from an earlier one. */
function attrsOf(simples: Simple[], base: Ctx): Ctx {
	const ints = new Set<string>(), refs = new Set<string>();
	let keyword = base.keyword;
	for (const s of simples) {
		const vals = s.words.map((w) => w.v), set = vals.indexOf("set");
		if (set >= 0 && vals.slice(set + 1).some((a, k, r) => /^-[A-Za-z]*k/.test(a) || (/^-[A-Za-z]*o$/.test(a) && r[k + 1] === "keyword"))) keyword = true;
		if (vals.some((a) => /^SHELLOPTS=.*(keyword|[$`])/.test(a))) keyword = true;
		const k = vals.findIndex((v) => isDeclarer(v, base.sh));
		if (k < 0) continue;
		const rest = vals.slice(k + 1), flags = rest.filter((a) => /^[-+]./.test(a)).join(" "), names = rest.filter((a) => !/^[-+]./.test(a)).map(nameOf);
		if (vals[k] === "integer" || vals[k] === "float" || /-[A-Za-z]*[iEF]/.test(flags)) for (const n of names) ints.add(n);
		if (vals[k] !== "export" && /-[A-Za-z]*n/.test(flags)) for (const n of names) refs.add(n);
	}
	return { ...base, keyword, ints, refs };
}

/** Check a command string that a nested shell will run (`-c`, eval, heredoc, a GIT_PAGER, …), by default in the same shell. */
function scanString(str: string, depth: number, ctx: Ctx, sh = ctx.sh, keyword = ctx.keyword): ShimHit | null {
	return scan(str, depth + 1, sh, keyword, ctx.gitCheck, ctx.redef);
}

/** The command a zsh-only prefix hides from pass 1 (`- git commit`, `repeat 2 git commit`, `emulate -c STR`): pass 1's verdict on it. */
function gitBehind(text: string, s: Simple | null, ctx: Ctx): ShimHit | null {
	const hit = ctx.gitCheck?.(text) ?? null;
	return hit && s ? { command: textOf(s), reason: hit.reason } : hit;
}

function checkAssign(v: string, depth: number, ctx: Ctx, deny: Deny): ShimHit | null {
	const m = splitAssign(v);
	if (!m) return null;
	const target = shimName(m.name, ctx.sh);
	if (target) return deny(target === "path" ? why(target) : `assigning ${target}${m.sub !== undefined ? " or an element of it" : ""} ${BYPASS}`);
	const inSub = m.sub !== undefined ? arithAssigns(m.sub, ctx.sh) : null; // an indexed array's subscript is arithmetic
	if (inSub) return deny(why(inSub));
	const inValue = ctx.ints?.has(m.name) ? arithAssigns(m.value, ctx.sh) : null; // so is an integer variable's value
	if (inValue) return deny(why(inValue));
	const ref = ctx.refs?.has(m.name) ? isShimVar(m.value, ctx.sh) : null;
	if (ref) return deny(why(ref, "pointing a nameref at"));
	// a value run as a command, where pass 1 may not see the assignment (after `set -k`, a name spelled with expansions): pass 1 checks it too
	if (CMD_VARS.has(m.name) || (m.dyn && couldSpell(m.name, [...CMD_VARS]))) return gitBehind(m.value, null, ctx) ?? scanString(m.value, depth, ctx);
	// env vars that point git at other config or code, in places pass 1 does not look (zsh `integer`/`float`, after `set -k`)
	if (GIT_CFG_VAR.test(m.name)) return deny(`${m.name} can make git load other config or code`);
	return m.dyn && couldSpell(m.name, GIT_CFG_NAMES) ? deny(`${m.name} could name a variable that makes git load other config or code`) : null;
}

/** A declaring builtin's `NAME=(…)` given as ONE (quoted) word: bash parses it at run time as a compound assignment, so each `[sub]=`
 * subscript is arithmetic and each `$(…)` in it runs (`declare -a 'arr=([PATH[1]=0]=x)'`). */
function checkCompound(value: string, depth: number, ctx: Ctx, deny: Deny): ShimHit | null {
	for (const m of value.matchAll(/(?:^\(|\s)\s*\[/g)) {
		const k = m.index + m[0].length - 1, e = bracketEnd(value, k);
		const hit = arithAssigns(value.slice(k + 1, e < 0 ? value.length : e), ctx.sh);
		if (hit) return deny(why(hit));
	}
	for (let j = 0; j < value.length; j++) { // pass 1 reads the quoted word as data: each `$(…)`/backtick in it goes to pass 1 too
		if (value[j] === "\\") j++;
		else if (value[j] === "'") j = close(value, j);
		else if (value[j] === "`" || (value[j] === "$" && value[j + 1] === "(")) {
			const e = close(value, value[j] === "`" ? j : j + 1), hit = gitBehind(value.slice(value[j] === "`" ? j + 1 : j + 2, e), null, ctx);
			if (hit) return hit;
			j = e;
		}
	}
	const nested: Simple[] = [];
	readDq(value, 0, null, depth + 1, nested, new WordBuf(), ctx);
	return each(nested, (x) => verdict(x, depth + 1, ctx));
}

/** `declare`/`typeset`/`local`/`readonly`/`export`/zsh `integer`: naming a shim variable with any flag or value changes it (in a
 * function `local`/`typeset`/`declare` make a fresh unexported one, `-a` an array, `-n`/`+x` un-export it). Read-only exceptions:
 * `-p` flags and nothing else (`declare -p PATH`), and a bare `export PATH`, which re-exports what is already exported. */
function checkDeclare(name: string, vals: string[], depth: number, ctx: Ctx, deny: Deny): ShimHit | null {
	const flags = vals.filter((a) => /^[-+]./.test(a)), names = vals.filter((a) => !/^[-+]./.test(a));
	const query = !names.some(isAssign) && (flags.length ? flags.every((f) => /^-p+$/.test(f)) : name === "export");
	const target = names.map((a) => isShimVar(a, ctx.sh)).find(Boolean);
	if (target && !query) return deny(why(target, "declaring"));
	if (name !== "export" && flags.some((f) => /^-[A-Za-z]*n/.test(f))) { // `declare -n ref=PATH`: ref now writes PATH
		const ref = names.map((a) => isShimVar(splitAssign(a)?.value ?? "", ctx.sh)).find(Boolean);
		if (ref) return deny(why(ref, "pointing a nameref at"));
	}
	const ints = name === "integer" || name === "float" || flags.some((f) => /^-[A-Za-z]*[iEF]/.test(f));
	const inValue = ints ? names.map((a) => arithAssigns(splitAssign(a)?.value ?? "", ctx.sh)).find(Boolean) : null;
	if (inValue) return deny(why(inValue));
	const compound = each(names, (a) => { const v = splitAssign(a)?.value ?? ""; return v.startsWith("(") ? checkCompound(v, depth, ctx, deny) : null; });
	return compound ?? each(names, (a) => checkAssign(a, depth, ctx, deny));
}

/** `unset`/`read`/`printf -v`/… store into the variable an argument names; a subscript there is evaluated as arithmetic. */
function checkSetter(name: string, vals: string[], sh: Sh, deny: Deny): ShimHit | null {
	if ((name === "printf" || name === "print") && !vals.some((a) => /^-v/.test(a))) return null;
	const target = vals.map((a) => sh.arg.exec(a)?.[3] ?? (a.startsWith("-") ? null : isShimVar(a, sh))).find(Boolean);
	if (target) return deny(why(target));
	const inSub = vals.map((a) => subscriptAssigns(a, sh)).find(Boolean);
	return inSub ? deny(why(inSub)) : null;
}

/** The file a cp/mv/ln/install-style command writes: its `-t DIR` / `--target-directory=DIR`, else its last argument. */
function destOf(vals: string[]): string {
	const t = vals.findIndex((a) => a === "-t" || a === "--target-directory");
	return t >= 0 ? vals[t + 1] ?? "" : vals.find((a) => a.startsWith("--target-directory="))?.slice(19) ?? vals[vals.length - 1] ?? "";
}

function checkWords(words: Word[], s: Simple, depth: number, ctx: Ctx): ShimHit | null {
	const deny: Deny = (reason) => ({ command: textOf(s), reason }), sh = ctx.sh;
	let i = 0;
	for (; i < words.length; i++) {
		const w = words[i];
		// loop variables (zsh takes several, and has `foreach NAME (…)`) are assigned; a nameref loop variable points at each word
		if (!w.quoted && (w.v === "for" || w.v === "select" || (sh.zsh && w.v === "foreach"))) {
			const rest = words.slice(i + 1).map((x) => x.v), n = rest.findIndex((x) => x === "in" || x === "do");
			const vars = n < 0 ? rest : rest.slice(0, n), target = vars.map((v) => isShimVar(v, sh)).find(Boolean);
			if (target) return deny(why(target));
			return vars.some((v) => ctx.refs?.has(v)) && rest.some((v) => isShimVar(v, sh)) ? deny(`pointing a nameref at PATH/PILEAD_SHIM_DIR ${BYPASS}`) : null;
		}
		if (!w.quoted && w.v === "coproc" && isShimVar(words[i + 1]?.v ?? "", sh)) return deny(why(nameOf(words[i + 1].v), "coproc makes an array of"));
		if (!w.quoted && w.v === "case") return null;
		if (!w.quoted && w.v === "function") { i++; continue; }
		if (!w.quoted && KEYWORDS.has(w.v)) continue;
		const a = splitAssign(w.v);
		if (!a) break;
		const hit = checkAssign(w.v, depth, ctx, deny);
		if (hit) return hit;
		if (a.dyn) break; // `P${x}ATH=v` is a command word to the shell: checked as an assignment above, then as the command
	}
	const w = words[i];
	if (!w) return null;
	if (words.slice(0, i).some((x) => x.joined)) { // pass 1 split `a[ 1 ]=x cmd` at its blanks, so it did not see `cmd` as the command
		const hit = gitBehind(s.src.slice(w.at, Math.max(s.end, w.at)), s, ctx);
		if (hit) return hit;
	}
	if (ctx.keyword) { // after `set -k` every `NAME=value` argument is an assignment in the command's environment
		const hit = each(words.slice(i + 1).filter((x) => isAssign(x.v)), (x) => checkAssign(x.v, depth, ctx, deny));
		if (hit) return hit;
	}
	// `[[ 1 -eq PATH[1]=0 ]]`: both operands of an arithmetic comparison are arithmetic, and so is the subscript `-v` tests
	// (checked on every simple command: a `(` inside `[[ … ]]` splits it before the parser sees `[[`)
	const all = words.slice(i).map((x) => x.v);
	const inTest = all.map((a, k) => (ARITH_CMP.test(a) ? arithAssigns(all[k - 1] ?? "", sh) ?? arithAssigns(all[k + 1] ?? "", sh) : a === "-v" ? subscriptAssigns(all[k + 1] ?? "", sh) : null)).find(Boolean);
	if (inTest) return deny(why(inTest));
	// `NAME[ …` unclosed as a command name: bash joins it with what follows only where an assignment stands, and there an unclosed
	// one is a syntax error — the reading is uncertain, so fail closed
	if (!w.quoted && /^[A-Za-z_]\w*\[[^\]]*$/.test(w.v)) return deny("an unclosed `[` in the command name — could not parse the command");
	const name = w.v.slice(w.v.lastIndexOf("/") + 1);
	const args = words.slice(i + 1);
	const vals = args.map((x) => x.v);
	if (isDeclarer(name, sh)) return checkDeclare(name, vals, depth, ctx, deny);
	if (SETTERS.has(name) || (sh.zsh && ZSH_SETTERS.has(name))) return checkSetter(name, vals, sh, deny);
	switch (name) {
		case "eval": return scanString((vals[0] === "--" ? vals.slice(1) : vals).join(" "), depth, ctx);
		case "xargs": return checkXargs(args, s, depth, ctx);
		case "source": case ".": return args.length ? checkScript(args[0], s, depth, ctx) : null;
		case "su": // `su -`, `-l`, `--login`: a login shell
			return vals.some((x) => x === "-" || x === "--login" || /^-[A-Za-z]*l/.test(x)) ? deny(LOGIN) : null;
		case "login": return deny(LOGIN);
		case "let": { // every argument is an arithmetic expression, quoted or not
			const hit = vals.map((x) => arithAssigns(x, sh)).find(Boolean);
			return hit ? deny(why(hit)) : null;
		}
		// the shim file itself: removing, writing, renaming, linking over it or changing its mode (reading it is fine)
		case "rm": case "rmdir": case "unlink": case "chmod": case "chown": case "chgrp": case "chflags": case "touch": case "truncate": case "shred": case "xattr": case "setfacl":
			return vals.some(isShimPath) ? deny(SHIM_FILE) : null;
		case "dd": return vals.some((x) => x.startsWith("of=") && isShimPath(x)) ? deny(SHIM_FILE) : null;
		case "sed": case "perl": return vals.some((x) => /^-[A-Za-z]*i/.test(x)) && vals.some(isShimPath) ? deny(SHIM_FILE) : null;
		case "rsync": case "ditto": return isShimPath(destOf(vals)) ? deny(SHIM_FILE) : null;
		case "tee": return vals.some(isShimPath) ? deny(SHIM_FILE) : null;
		case "cp": case "mv": case "ln": case "install": // mv also removes its sources
			return (name === "mv" ? vals : [destOf(vals)]).some(isShimPath) ? deny(SHIM_FILE) : null;
		case "set": { // zsh `set -A NAME …` / `+A` assigns an array
			const k = sh.zsh ? vals.findIndex((x) => /^[-+][A-Za-z]*A$/.test(x)) : -1;
			const target = k >= 0 ? isShimVar(vals[k + 1] ?? "", sh) : null;
			return target ? deny(why(target)) : null;
		}
		case "emulate": // zsh `emulate sh -c '…'` runs the string in this shell; pass 1 does not know `emulate`
			return sh.zsh ? each(vals.flatMap((x, k) => (/^-[A-Za-z]*c$/.test(x) ? [k + 1] : [])), (k) => gitBehind(vals[k] ?? "", null, ctx) ?? scanString(vals[k] ?? "", depth, ctx)) : null;
		case "alias": return each(vals, (x) => (x.includes("=") ? scanString(x.slice(x.indexOf("=") + 1), depth, ctx) : null));
		case "trap": return each(vals.filter((x) => !x.startsWith("-")).slice(0, 1), (action) => scanString(action, depth, ctx));
		case "watch": {
			let j = 0;
			while (j < vals.length && vals[j].startsWith("-")) j += vals[j] === "-n" || vals[j] === "--interval" ? 2 : 1;
			return scanString(vals.slice(j).join(" "), depth, ctx);
		}
		case "find": // -delete or -exec under the shim directory; -exec CMD… ; | {} + (segment end: execEnd, as in gitPathWord)
			if (vals.some(isShimPath) && vals.some((x) => /^-(delete|exec|execdir|ok|okdir)$/.test(x))) return deny(SHIM_FILE);
			return each(vals.flatMap((x, j) => (/^-(exec|execdir|ok|okdir)$/.test(x) ? [j + 1] : [])), (j) => {
				let k = j;
				while (k < args.length && !execEnd(args, k)) k++;
				return checkWords(args.slice(j, k), s, depth, ctx);
			});
	}
	if (SHELLS.has(name)) return checkShell(name, args, s, depth, ctx);
	if (name === "env") {
		const at = checkEnv(vals, depth, ctx, deny);
		return typeof at === "number" ? checkWords(args.slice(at), s, depth, ctx) : at;
	}
	const zshOnly = sh.zsh && !WRAP[name] ? ZSH_WRAP[name] : undefined, spec = WRAP[name] ?? zshOnly;
	if (!spec) return null;
	let j = 0;
	for (; j < args.length; j++) {
		const x = vals[j];
		if (x === "--") { j++; break; }
		if (name === "sudo" && isAssign(x)) {
			const hit = checkAssign(x, depth, ctx, deny);
			if (hit) return hit;
			continue;
		}
		if (name === "command" && (x === "-v" || x === "-V")) return null; // prints a path, never runs
		// `command -p` looks the program up on a default PATH that skips the shim (with `-v`/`-V` it only prints it)
		if (name === "command" && /^-[A-Za-z]*p/.test(x) && !optionsFrom(vals, j).some((y) => /[vV]/.test(y))) {
			return deny(`\`command -p\` looks the program up on a default PATH, which ${BYPASS}`);
		}
		// `exec -l`, or an argv[0] starting with `-` (`exec -a -bash bash`), makes the program a login shell
		if (name === "exec" && (/^-[A-Za-z]*l/.test(x) || (/^-[A-Za-z]*a$/.test(x) && vals[j + 1]?.startsWith("-")) || /^-[A-Za-z]*a-/.test(x))) return deny(LOGIN);
		if (!x.startsWith("-") || x === "-") break;
		if (spec.arg?.includes(x)) j++;
	}
	const rest = args.slice(j + (spec.pos ?? 0));
	if (zshOnly && rest.length) {
		const hit = gitBehind(s.src.slice(rest[0].at, Math.max(s.end, rest[0].at)), s, ctx);
		if (hit) return hit;
	}
	return checkWords(rest, s, depth, ctx);
}

/** The option words from `j` up to the first operand or `--`. */
function optionsFrom(vals: string[], j: number): string[] {
	const out: string[] = [];
	for (; j < vals.length && vals[j].startsWith("-") && vals[j] !== "-" && vals[j] !== "--"; j++) out.push(vals[j]);
	return out;
}

/** `env`'s long options (GNU, which also takes any unambiguous prefix); the value-taking ones name their value in ENV_VALUE. */
const ENV_LONG = ["--ignore-environment", "--unset", "--split-string", "--chdir", "--argv0", "--null", "--debug", "--help", "--version",
	"--block-signal", "--default-signal", "--ignore-signal", "--list-signal-handling"];
const ENV_VALUE: Record<string, string> = { "--unset": "u", "--split-string": "S", "--chdir": "C", "--argv0": "a" };

/** `env`'s options and assignments. Denied: clearing the environment (`-i`, `-`, `--ignore-environment`), unsetting a shim name
 * (`-u PATH`), `-P DIR` (the program is looked up on DIR instead of PATH) and any option it does not know; bundles and attached
 * values (`-iv`, `-uPATH`) are read as env reads them. `-S STR` splits STR into more options and arguments, as env does, so
 * `env STR REST` is checked. Returns the hit (or null, after `-S`), or the index of the command's first word. */
function checkEnv(vals: string[], depth: number, ctx: Ctx, deny: Deny): ShimHit | null | number {
	for (let j = 0; j < vals.length; j++) {
		const x = vals[j];
		if (x === "--") return j + 1;
		if (x === "-") return deny(EMPTY_ENV("-"));
		if (isAssign(x)) {
			const hit = checkAssign(x, depth, ctx, deny);
			if (hit) return hit;
			continue;
		}
		if (!x.startsWith("-")) return j;
		let flag = "", value: string | undefined;
		if (x.startsWith("--")) {
			const eq = x.indexOf("="), key = eq < 0 ? x : x.slice(0, eq), hits = ENV_LONG.filter((o) => o.startsWith(key));
			if (hits.includes("--ignore-environment")) return deny(EMPTY_ENV(x));
			if (!hits.length) return deny(`\`env ${x}\`: an option the guard does not know, so it cannot check the command`);
			flag = ENV_VALUE[hits.includes(key) ? key : hits[0]] ?? "";
			if (!flag) continue;
			value = eq < 0 ? vals[++j] ?? "" : x.slice(eq + 1);
		} else {
			for (let k = 1; k < x.length && !flag; k++) {
				const c = x[k];
				if (c === "v" || c === "0") continue;
				if (c === "i") return deny(EMPTY_ENV(x));
				if (c === "P") return deny(`\`env -P\` looks the program up on another PATH, which ${BYPASS}`);
				if (!"uSCa".includes(c)) return deny(`\`env ${x}\`: an option the guard does not know, so it cannot check the command`);
				flag = c;
				value = x.slice(k + 1) || (vals[++j] ?? "");
			}
			if (!flag) continue;
		}
		// SHORTCUT: the remaining words are joined dequoted, so one holding `;` or `|` may be misread as a separator (a false denial, or a
		// missed pass-2 form in an already-odd `-S` command; pass 1 still checks the original text). Upgrade: re-quote each word with `sq`.
		if (flag === "S") return scanString(["env", value ?? "", ...vals.slice(j + 1)].join(" "), depth, ctx);
		const unset = flag === "u" ? shimName(value ?? "", ctx.sh) : null;
		if (unset) return deny(why(unset, "unsetting"));
	}
	return vals.length;
}

/** A shell or `source` whose script is `script` (undefined: stdin): a heredoc or here-string is scanned. Script files are out of reach. */
function checkScript(script: Word | undefined, s: Simple, depth: number, ctx: Ctx, sh = ctx.sh, keyword = ctx.keyword): ShimHit | null {
	if (script && !STDIN_PATH.test(script.v)) return null;
	if (s.heredoc !== undefined) return scanString(s.heredoc, depth, ctx, sh, keyword);
	if (s.herestring !== undefined) return scanString(s.herestring, depth, ctx, sh, keyword);
	return null;
}

/** `sh`/`zsh -c STR`, or fed by stdin: STR is checked with that shell's rules (`zsh` → zsh), and with `set -k` on for `-k`/`-o keyword`. */
function checkShell(shell: string, args: Word[], s: Simple, depth: number, ctx: Ctx): ShimHit | null {
	const sh = shell === "zsh" ? ZSH : BASH;
	/** zsh reads option names ignoring case and underscores (`--LOG_IN`, `-o Login`). */
	const isLogin = (opt: string) => opt.toLowerCase().replace(/_/g, "") === "login";
	let [j, dashC, dashS, keyword] = [0, false, false, ctx.keyword];
	for (; j < args.length; j++) {
		const a = args[j].v;
		if (a === "--") { j++; break; }
		if (a === "--command") { dashC = true; continue; }
		if (a.startsWith("--command=")) return scanString(a.slice(10), depth, ctx, sh, keyword);
		if (a.startsWith("--") && isLogin(a.slice(2))) return { command: textOf(s), reason: LOGIN };
		if (a.startsWith("--")) { if (a === "--rcfile" || a === "--init-file") j++; continue; }
		if (!/^[-+][A-Za-z]+$/.test(a)) break;
		if (a[0] === "-" && a.includes("l")) return { command: textOf(s), reason: LOGIN };
		if (a[0] === "-") { dashC ||= a.includes("c"); dashS ||= a.includes("s"); keyword ||= a.includes("k"); }
		if (/[oO]/.test(a)) {
			const opt = args[++j]?.v ?? "";
			if (a[0] === "-" && isLogin(opt)) return { command: textOf(s), reason: LOGIN };
			keyword ||= opt === "keyword";
		}
	}
	const rest = args.slice(j);
	if (dashC) return rest.length ? scanString(rest[0].v, depth, ctx, sh, keyword) : null;
	return checkScript(dashS ? undefined : rest[0], s, depth, ctx, sh, keyword);
}

function checkXargs(args: Word[], s: Simple, depth: number, ctx: Ctx): ShimHit | null {
	let j = 0;
	for (; j < args.length; j++) {
		const a = args[j].v;
		if (a === "--") { j++; break; }
		if (!a.startsWith("-") || a === "-") break;
		if (a === "-I" || XARGS_WITH_ARG.has(a)) j++;
	}
	return j < args.length ? checkWords(args.slice(j), s, depth, ctx) : null;
}

/** Pass 2's verdict: the offending command + reason, or null when none of its rules applies. Never throws: what it cannot parse is
 * denied. It never allows anything by itself — bash-fence.ts runs it only after pass 1 allows. */
export function shimFenceVerdict(cmd: string, opts: ShimOptions = {}): ShimHit | null {
	const src = String(cmd ?? "");
	try { return scan(src, 0, /zsh/.test(String(opts?.shell ?? "")) ? ZSH : BASH, false, opts?.gitCheck); } catch (e) {
		return { command: clip(src) || "(unparseable command)", reason: e instanceof Unparseable ? e.message : "could not parse the command" };
	}
}

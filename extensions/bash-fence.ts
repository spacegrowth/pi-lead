/**
 * bash-fence: the executor's git fence. Pure — no pi imports, no fs, no child processes. `deniedGitCommand(cmd)` returns the offending
 * simple command when any command in the bash string would reach a git subcommand an executor may not run, else null. Ported from
 * claude-relay lib/bash_writes.py (heredocs split off → quote-aware tokens → simple commands split on `; & && || | |& ( ) { } \n` →
 * program word resolved through wrappers), plus recursion wherever a command string hides: `$(…)`, backticks, `<(…)`, `${…}`, unquoted
 * heredoc bodies, `sh -c`, `eval`, `env -S`, `watch`, `trap`, `alias`, GIT_PAGER=/EDITOR=-style vars, shells/`source` fed by stdin. Git is
 * ALLOWLISTED (aliases/unknown subcommands denied), minus the ref-moving forms of allowed subcommands (REF_GUARD), and the parser fails
 * CLOSED (unbalanced quotes, nesting past MAX_DEPTH, a run-time program name like `$g commit`, any throw → denied). SHORTCUT: string-level
 * only — blind to script FILES the executor writes then runs (`bash x.sh`, hooks), a renamed/symlinked git, `python -c`, ssh/su. The
 * OS-level guard behind it is `pilead spawn`'s git shim (lib/pilead/shim.py, same policy), first on the executor's PATH — so this fence
 * also denies what would step around the shim: git named by path (`/usr/bin/git`, `xcrun git`) and any change to PATH/PILEAD_SHIM_DIR.
 *
 * TWO PASSES. Pass 1 is everything above `gitFenceVerdict`: the guard as committed before the split, its parser and checks unchanged
 * (tests/ts/bash-fence.test.ts proves its verdicts and reasons equal the committed guard's). Pass 2 (shim-fence.ts) holds the rules
 * added since — where PATH/PILEAD_SHIM_DIR may not be named (assignment, declaring builtins, arithmetic, `${NAME=…}`, prefix words,
 * `set -k`), writes to the shim file, and zsh's rules — with its own scanner, and it can only deny. `deniedGitVerdict` is DENIED when
 * either pass denies, so no change to pass 2 can turn a pass 1 denial into an allow. */
import { shimFenceVerdict } from "./shim-fence.ts";

/** `command`: the offending simple command, whitespace-normalised; `reason`: when not self-evident. */
export interface FenceHit { command: string; reason?: string }

/** The packet's read/stage-safe subcommands, plus read-only extras from `help` on (cannot move a ref or write an object). */
export const GIT_ALLOW: ReadonlySet<string> = new Set(("add rm mv status diff log show blame grep ls-files rev-parse branch checkout switch " +
	"restore stash fetch remote config describe cat-file symbolic-ref worktree reset apply " +
	"help version shortlog merge-base ls-tree for-each-ref rev-list check-ignore show-ref").split(" "));
/** Ref-moving forms of allowlisted subcommands (a NEW branch — `branch x`, `-b`, `-c` — stays allowed): short flags `s` in clusters
 * (`arg` letters take the value), long flags `l` by any unique prefix (`safe` exempt; `argL` take the next word), first positional `sub`, > `maxPos` positionals. */
const REF_GUARD: Record<string, { s?: string; arg?: string; l?: string[]; safe?: string[]; argL?: string[]; sub?: string[]; maxPos?: number }> = {
	branch: { s: "dDfmMcCu", l: ["--delete", "--force", "--move", "--copy", "--set-upstream-to", "--unset-upstream", "--edit-description"], argL: ["--sort", "--format", "--points-at"] },
	checkout: { s: "B", arg: "b", l: ["--orphan"] }, switch: { s: "C", arg: "c", l: ["--orphan", "--force-create"], safe: ["--force"] },
	stash: { sub: ["clear"] }, remote: { sub: ["set-url", "remove", "rm", "rename", "prune", "set-head"] }, // update-ref, replace, notes, am: never allowlisted
	worktree: { s: "B", arg: "b", l: ["--orphan"], sub: ["remove", "prune", "move", "lock", "unlock"] }, "symbolic-ref": { s: "d", arg: "m", l: ["--delete"], maxPos: 1 },
};
/** Denied without an extra reason: the refusal sentence already says why. */
const NAMED_DENY = new Set("commit push merge rebase tag pull cherry-pick revert am".split(" "));
/** `git -c KEY=…` / `git config KEY …` keys whose value git runs as a command or loads as config. */
const DANGEROUS_KEY =
	/^(alias|pager|filter|credential|difftool|mergetool|sequence|gpg|ssh|uploadpack|receivepack|include|includeif)\.|^core\.(pager|editor|sshcommand|fsmonitor|hookspath|askpass|gitproxy)$|\.(command|textconv|driver|program|helper|cmd)$|^diff\.external$|^init\.templatedir$/i;
/** Env vars whose value is run as a command: the value is checked like a `sh -c` string. */
const CMD_VARS = new Set(["GIT_PAGER", "PAGER", "GIT_EDITOR", "EDITOR", "VISUAL", "GIT_SEQUENCE_EDITOR", "GIT_SSH_COMMAND", "GIT_SSH", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_EXTERNAL_DIFF", "GIT_PROXY_COMMAND", "PROMPT_COMMAND"]);
/** Env vars that point git at other config/code. */
const GIT_CFG_VAR = /^GIT_(CONFIG(_PARAMETERS|_KEY_\d+|_GLOBAL|_SYSTEM)?|EXEC_PATH|TEMPLATE_DIR)$/;
/** A write into git's own config/hooks: that is how aliases, pagers and hooks get planted. */
const GIT_PATH = /(^|\/)\.git(\/|$)|gitconfig$|(^|\/)\.config\/git\//;
const GIT_OPT_WITH_ARG = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"]);
/** The git shim's lookup: first `git` on PATH, skipping PILEAD_SHIM_DIR. Changing either (or naming git by path) steps around it. */
const SHIM_VARS = new Set(["PATH", "PILEAD_SHIM_DIR"]);
const BYPASS = "bypasses the git shim";
const isGit = (p: string) => /^git(-|$)/.test(p.slice(p.lastIndexOf("/") + 1));

const MAX_DEPTH = 8;
const ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*\+?=/;
const KEYWORDS = new Set(["!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "coproc"]);
const SHELLS = new Set(["sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"]);
const STDIN_PATH = /^(-|\/dev\/stdin|\/dev\/fd\/0)$/;
const OPS = [";;&", "&>>", "<<<", "<<-", ";;", ";&", "&&", "&>", "||", "|&", "<<", "<>", "<&", ">>", ">|", ">&", ";", "&", "|", "(", ")", "<", ">"];
const SEPS = new Set([";;&", ";;", ";&", "&&", "||", "|&", ";", "&", "|", "(", ")"]);
const WRITE_OPS = new Set([">", ">>", ">|", "&>", "&>>", "<>"]);
const WORD_END = " \t\n;&|<>()";
/** Unquoted glob / brace expansion in the program-name segment makes the program unknowable. */
const GLOB = /[*?]|\[[^\]]*\]|\{[^}]*(,|\.\.)[^}]*\}/;

/** Wrappers that run their operand: options (those taking an argument listed), then `pos` skips. */
const WRAP: Record<string, { arg?: string[]; pos?: number }> = {
	command: {}, builtin: {}, nohup: {}, busybox: {}, setsid: {}, unbuffer: {}, chronic: {}, exec: { arg: ["-a"] }, doas: { arg: ["-u", "-C"] },
	time: { arg: ["-f", "-o", "--format", "--output"] }, nice: { arg: ["-n", "--adjustment"] }, env: { arg: ["-u", "-C", "--unset", "--chdir"] },
	sudo: { arg: ["-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--host", "--prompt", "--chdir", "--role", "--type", "--other-user"] },
	timeout: { arg: ["-s", "-k", "--signal", "--kill-after"], pos: 1 }, stdbuf: { arg: ["-i", "-o", "-e"] }, caffeinate: { arg: ["-t", "-w"] }, ionice: { arg: ["-c", "-n", "-p", "-P", "-u"] },
};
const XARGS_WITH_ARG = new Set(["-a", "-d", "-E", "-L", "-n", "-P", "-s", "--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars"]);

class Unparseable extends Error {}

interface Word { v: string; quoted: boolean; progDyn: boolean; procSub?: boolean }
interface Simple { src: string; words: Word[]; start: number; end: number; piped: boolean; heredoc?: string; herestring?: string; gitWrite?: boolean }
interface Ctx { stdinArgs?: boolean; repl?: string }
type Deny = (reason?: string) => FenceHit;

/** One word: its dequoted value (expansions kept raw, as a nested shell would receive them) and
 * whether the segment after its last `/` is computed at run time. */
class WordBuf {
	v = ""; quoted = false; private dyn = false; private unq = "";
	lit(t: string, q: boolean): void {
		for (const ch of t) if (ch === "/") { this.dyn = false; this.unq = ""; } else if (!q) this.unq += ch;
		this.v += t;
		this.quoted ||= q;
	}
	exp(raw: string): void { this.v += raw; this.dyn = true; }
	word(): Word { return { v: this.v, quoted: this.quoted, progDyn: this.dyn || GLOB.test(this.unq) }; }
}

/** Index of the closer of the `(`, `{`, `"`, `'` or backtick at `i` (quote/escape/nesting-aware). */
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

/** Decode a `$'…'` body starting at `i` (\n \xHH \uHHHH \NNN …); returns [value, index after its quote]. */
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

/** A `$…` or backtick expansion at `i`: its commands go to `nested`; returns the next index. */
function expansion(src: string, i: number, q: boolean, depth: number, nested: Simple[], buf: WordBuf): number {
	const nx = src[i + 1] ?? "";
	let end: number;
	if (src[i] === "`" || nx === "(") {
		const tick = src[i] === "`";
		end = close(src, tick ? i : i + 1);
		nested.push(...parse(tick ? src.slice(i + 1, end).replace(/\\([\\`$"])/g, "$1") : src.slice(i + 2, end), depth + 1));
	} else if (nx === "{") {
		end = close(src, i + 1);
		readDq(src.slice(i + 2, end), 0, null, depth, nested, new WordBuf());
	} else {
		const m = /^([A-Za-z_]\w*|[0-9@*#?$!-])/.exec(src.slice(i + 1));
		if (!m) { buf.lit("$", q); return i + 1; }
		end = i + m[0].length;
	}
	buf.exp(src.slice(i, end + 1));
	return end + 1;
}

/** Double-quote body (term `"`) or an expanding heredoc/`${}` body (term null) from `i`. */
function readDq(src: string, i: number, term: '"' | null, depth: number, nested: Simple[], buf: WordBuf): number {
	const escapable = term ? '$`"\\' : "$`\\";
	while (i < src.length) {
		const c = src[i];
		if (term && c === term) return i + 1;
		if (c === "\\" && src[i + 1] === "\n") i += 2;
		else if (c === "\\" && escapable.includes(src[i + 1] || "#")) { buf.lit(src[i + 1], true); i += 2; }
		else if (c === "$" || c === "`") i = expansion(src, i, true, depth, nested, buf);
		else { buf.lit(c, true); i++; }
	}
	if (term) throw new Unparseable("unterminated double quote — could not parse the command");
	return i;
}

function readWord(src: string, i: number, depth: number, nested: Simple[]): [Word, number] {
	const buf = new WordBuf();
	while (i < src.length && !WORD_END.includes(src[i])) {
		const c = src[i];
		let v: string;
		if (c === "\\") { if (src[i + 1] !== "\n" && i + 1 < src.length) buf.lit(src[i + 1], true); i += 2; }
		else if (c === "'") { const end = close(src, i); buf.lit(src.slice(i + 1, end), true); i = end + 1; }
		else if (c === "$" && src[i + 1] === "'") { [v, i] = ansiC(src, i + 2); buf.lit(v, true); }
		else if (c === '"' || (c === "$" && src[i + 1] === '"')) { buf.quoted = true; i = readDq(src, i + (c === "$" ? 2 : 1), '"', depth, nested, buf); }
		else if (c === "$" || c === "`") i = expansion(src, i, false, depth, nested, buf);
		else { buf.lit(c, false); i++; }
	}
	return [buf.word(), i];
}

/** Every simple command in `src`, including those nested in substitutions (flattened). */
function parse(src: string, depth: number): Simple[] {
	if (depth > MAX_DEPTH) throw new Unparseable("command nesting too deep to check");
	const out: Simple[] = [], fresh = (piped = false): Simple => ({ src, words: [], start: -1, end: -1, piped });
	let cur = fresh(), pending: { cmd: Simple; delim: string; strip: boolean; quoted: boolean }[] = [];
	const finish = (pipedNext: boolean) => {
		if (cur.start >= 0) { out.push(cur); cur = fresh(pipedNext); } else if (pipedNext) cur.piped = true;
	};
	const touch = (a: number, b: number) => { if (cur.start < 0) cur.start = a; cur.end = b; };
	let i = 0;
	while (i < src.length) {
		const c = src[i];
		if (c === " " || c === "\t") { i++; continue; }
		if (c === "\\" && src[i + 1] === "\n") { i += 2; continue; }
		if (c === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
		if (c === "\n") {
			finish(false);
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
				if (!h.quoted) readDq(h.cmd.heredoc, 0, null, depth, out, new WordBuf()); // `$(…)` still runs
			}
			pending = [];
			continue;
		}
		if ((c === "<" || c === ">") && src[i + 1] === "(") { // process substitution: a file name made by a command
			const end = close(src, i + 1);
			out.push(...parse(src.slice(i + 2, end), depth + 1));
			cur.words.push({ v: "/dev/fd/63", quoted: false, progDyn: true, procSub: true });
			touch(i, end + 1);
			i = end + 1;
			continue;
		}
		const op = OPS.find((o) => src.startsWith(o, i));
		if (op && SEPS.has(op)) { finish(op === "|" || op === "|&"); i += op.length; continue; }
		if (op) { // a redirect: its target is not a word of the command
			touch(i, i + op.length);
			i += op.length;
			while (src[i] === " " || src[i] === "\t") i++;
			if (i >= src.length || WORD_END.includes(src[i])) continue;
			const [target, next] = readWord(src, i, depth, out);
			touch(i, next);
			i = next;
			if (op === "<<" || op === "<<-") pending.push({ cmd: cur, delim: target.v, strip: op === "<<-", quoted: target.quoted });
			else if (op === "<<<") cur.herestring = target.v;
			else if (WRITE_OPS.has(op) && GIT_PATH.test(target.v)) cur.gitWrite = true;
			continue;
		}
		const [w, next] = readWord(src, i, depth, out);
		const start = i;
		i = next;
		if (!w.quoted && (w.v === "{" || w.v === "}")) { finish(false); continue; }
		// a reserved word opening a command (`then git commit`) is not part of the simple command
		if (!w.quoted && cur.start < 0 && KEYWORDS.has(w.v)) continue;
		if (!w.quoted && /^\d+$/.test(w.v) && (src[next] === "<" || src[next] === ">")) continue; // an fd
		cur.words.push(w);
		touch(start, next);
	}
	finish(false);
	return out;
}

const clip = (t: string) => ((t = t.replace(/\s+/g, " ").trim()).length > 200 ? t.slice(0, 199) + "…" : t);
const textOf = (s: Simple) => clip(s.src.slice(s.start, s.end));
const GIT_WRITE = "writes into git's own config/hooks";
const each = <T>(xs: T[], f: (x: T) => FenceHit | null): FenceHit | null => xs.reduce<FenceHit | null>((hit, x) => hit ?? f(x), null);

const scan = (src: string, depth: number): FenceHit | null =>
	each(parse(src, depth), (s) => (s.gitWrite ? { command: textOf(s), reason: GIT_WRITE } : checkWords(s.words, s, depth, {})));

/** Check a command string that a nested shell will run (`-c`, eval, heredoc, a GIT_PAGER, …). */
function scanString(str: string, s: Simple, depth: number, ctx: Ctx): FenceHit | null {
	if (ctx.repl && str.includes(ctx.repl)) return { command: textOf(s), reason: "the command comes from xargs/find input" };
	return scan(str, depth + 1);
}

function checkAssign(v: string, s: Simple, depth: number, ctx: Ctx, deny: Deny): FenceHit | null {
	const m = /^([A-Za-z_]\w*)\+?=([\s\S]*)$/.exec(v);
	if (m && SHIM_VARS.has(m[1])) return deny(`assigning ${m[1]} ${BYPASS}`);
	if (m && CMD_VARS.has(m[1])) return scanString(m[2], s, depth, ctx);
	return m && GIT_CFG_VAR.test(m[1]) ? deny(`${m[1]} can make git load other config or code`) : null;
}

function checkWords(words: Word[], s: Simple, depth: number, ctx: Ctx): FenceHit | null {
	const deny: Deny = (reason) => ({ command: textOf(s), reason });
	let i = 0;
	for (; i < words.length; i++) {
		const w = words[i];
		if (!w.quoted && (w.v === "for" || w.v === "select")) return SHIM_VARS.has(words[i + 1]?.v ?? "") ? deny(`changing PATH/PILEAD_SHIM_DIR ${BYPASS}`) : null;
		if (!w.quoted && w.v === "case") return null;
		if (!w.quoted && w.v === "function") { i++; continue; }
		if (!w.quoted && KEYWORDS.has(w.v)) continue;
		if (!ASSIGN.test(w.v)) break;
		const hit = checkAssign(w.v, s, depth, ctx, deny);
		if (hit) return hit;
	}
	const w = words[i];
	if (!w) return ctx.stdinArgs ? deny("the program comes from xargs input") : null;
	if (w.progDyn) return deny("the program name is computed at run time");
	if (ctx.repl && w.v.includes(ctx.repl)) return deny("the program comes from xargs/find input");
	const name = w.v.slice(w.v.lastIndexOf("/") + 1);
	const args = words.slice(i + 1);
	const vals = args.map((a) => a.v);
	if (w.v.includes("/") && isGit(name)) return deny(`git named by path ${BYPASS}`);
	switch (name) {
		case "git": return checkGit(args, deny, ctx);
		case "eval": return scanString(vals.join(" "), s, depth, ctx);
		case "xargs": return checkXargs(args, s, depth);
		case "source": case ".": return args.length ? checkScript(args[0], s, depth, ctx, deny) : null;
		case "hash": return vals.includes("-p") ? deny("hash -p rebinds a command name") : null;
		case "xcrun": return vals.some(isGit) && !vals.some((a) => /^(-f|--find)$/.test(a)) ? deny(`xcrun git ${BYPASS}`) : null;
		case "unset": case "read": case "printf": case "mapfile": case "readarray": case "getopts":
			return vals.some((a) => SHIM_VARS.has(a)) ? deny(`changing PATH/PILEAD_SHIM_DIR ${BYPASS}`) : null;
		case "tee": return vals.some((a) => GIT_PATH.test(a)) ? deny(GIT_WRITE) : null;
		case "cp": case "mv": case "ln": case "install": return GIT_PATH.test(vals[vals.length - 1] ?? "") ? deny(GIT_WRITE) : null;
		case "alias": return each(vals, (a) => (a.includes("=") ? scanString(a.slice(a.indexOf("=") + 1), s, depth, ctx) : null));
		case "export": case "declare": case "typeset": case "local": case "readonly": // `export -n PATH`, `declare +x PATH`: un-exported
			if (vals.some((a) => /^(-[A-Za-z]*n|\+[A-Za-z]*x)/.test(a)) && vals.some((a) => SHIM_VARS.has(a))) return deny(`un-exporting PATH/PILEAD_SHIM_DIR ${BYPASS}`);
			return each(vals, (a) => checkAssign(a, s, depth, ctx, deny));
		case "trap": return each(vals.filter((a) => !a.startsWith("-")).slice(0, 1), (action) => scanString(action, s, depth, ctx));
		case "watch": {
			let j = 0;
			while (j < vals.length && vals[j].startsWith("-")) j += vals[j] === "-n" || vals[j] === "--interval" ? 2 : 1;
			return scanString(vals.slice(j).join(" "), s, depth, ctx);
		}
		case "find": // -exec CMD… ; | +   (`{}` becomes file names, like xargs -I{})
			return each(vals.flatMap((a, j) => (/^-(exec|execdir|ok|okdir)$/.test(a) ? [j + 1] : [])), (j) => {
				let k = j;
				while (k < vals.length && vals[k] !== ";" && vals[k] !== "+") k++;
				return checkWords(args.slice(j, k), s, depth, { repl: "{}" });
			});
	}
	if (name.startsWith("git-")) return checkGit([{ ...w, v: name.slice(4) }, ...args], deny, ctx);
	if (SHELLS.has(name)) return checkShell(args, s, depth, ctx, deny);
	const spec = WRAP[name];
	if (!spec) return null;
	let j = 0;
	for (; j < args.length; j++) {
		const a = vals[j];
		if (a === "--") { j++; break; }
		if ((name === "env" || name === "sudo") && ASSIGN.test(a)) {
			const hit = checkAssign(a, s, depth, ctx, deny);
			if (hit) return hit;
			continue;
		}
		if (name === "env" && /^(-S|--split-string)/.test(a)) {
			const head = a === "-S" || a === "--split-string" ? vals[++j] ?? "" : a.replace(/^(-S|--split-string=)/, "");
			return scanString([head, ...vals.slice(j + 1)].join(" "), s, depth, ctx);
		}
		if (name === "command" && (a === "-v" || a === "-V")) return null; // prints a path, never runs
		if (name === "exec" && /^-[A-Za-z]*c/.test(a)) return deny(`exec -c clearing PATH/PILEAD_SHIM_DIR ${BYPASS}`);
		if (name === "env" && (/^(-i|-|--ignore-environment)$/.test(a) || SHIM_VARS.has(/^(-u|--unset)$/.test(a) ? vals[j + 1] ?? "" : a.replace(/^(-u|--unset=)/, ""))))
			return deny(`env clearing PATH/PILEAD_SHIM_DIR ${BYPASS}`);
		if (!a.startsWith("-") || a === "-") break;
		if (spec.arg?.includes(a)) j++;
	}
	return checkWords(args.slice(j + (spec.pos ?? 0)), s, depth, ctx);
}

function checkGit(args: Word[], deny: Deny, ctx: Ctx): FenceHit | null {
	const risky = (key: string) => DANGEROUS_KEY.test(key) || /[$`]/.test(key);
	let j = 0;
	for (; j < args.length; j++) {
		const a = args[j].v;
		if (GIT_OPT_WITH_ARG.has(a)) {
			const key = (args[++j]?.v ?? "").split("=")[0];
			if ((a === "-c" || a === "--config-env") && risky(key)) return deny(`git ${a} ${key} can run arbitrary commands`);
			continue;
		}
		if (a.startsWith("--config-env=") && risky(a.slice(13).split("=")[0])) return deny("git --config-env can run arbitrary commands");
		if (a.startsWith("--exec-path=")) return deny("git --exec-path= can make git load other config or code"); // bare `--exec-path` only prints it
		if (!a.startsWith("-")) break;
	}
	const sub = args[j];
	if (!sub) return ctx.stdinArgs ? deny("the git subcommand comes from xargs input") : null;
	if (!GIT_ALLOW.has(sub.v)) return deny(NAMED_DENY.has(sub.v) ? undefined : `"${sub.v}" is not an allowed git subcommand (allowed: ${[...GIT_ALLOW].join(", ")})`);
	const rest = args.slice(j + 1).map((a) => a.v);
	if (ctx.stdinArgs && (sub.v === "reset" || sub.v === "config")) return deny(`xargs could append options to git ${sub.v}`);
	if (movesRef(sub.v, rest, !!ctx.stdinArgs)) return deny(`${ctx.stdinArgs ? "with xargs input, " : ""}this git ${sub.v} form moves or deletes a ref — executors may create a branch, never move, force-reset or delete one`);
	// git accepts unique prefixes of long options (--har, --me, --ke); after `--` come paths
	const opts = rest.includes("--") ? rest.slice(0, rest.indexOf("--")) : rest;
	if (sub.v === "reset" && opts.some((a) => a.length > 2 && a.startsWith("--") && ["--hard", "--merge", "--keep"].some((o) => o.startsWith(a)))) return deny();
	const reads = rest.some((a) => /^(--get(-all|-regexp)?|-l|--list|get|list)$/.test(a));
	return sub.v === "config" && !reads && rest.some((a) => DANGEROUS_KEY.test(a)) ? deny("that git config key can run arbitrary commands") : null;
}

/** Whether `git <sub> <rest…>` hits a REF_GUARD form; `more`: xargs may append words (options too, unless after `--`). */
function movesRef(sub: string, rest: string[], more: boolean): boolean {
	const g = REF_GUARD[sub], pos: string[] = [];
	let k = 0;
	for (; g && k < rest.length && rest[k] !== "--"; k++) {
		const a = rest[k], n = a.split("=")[0];
		if (/^--./.test(a)) {
			if (!g.safe?.includes(n) && g.l?.some((l) => l.startsWith(n))) return true;
			if (g.argL?.includes(a)) k++; // `--sort -refname`: the next word is the value
		} else if (a.length < 2 || a[0] !== "-") pos.push(a);
		else for (let c = 1; c < a.length; c++) {
			if (g.s?.includes(a[c])) return true;
			if (g.arg?.includes(a[c])) { k += c === a.length - 1 ? 1 : 0; break; } // `-bX` / `-b X`: the rest is the value
		}
	}
	pos.push(...rest.slice(k + 1), ...(more ? [""] : [])); // xargs input: at least one more positional (any options, without `--`)
	return !!g && ((more && (k >= rest.length || (!!g.sub && pos.length < 2))) || !!g.sub?.includes(pos[0]) || pos.length > (g.maxPos ?? Infinity));
}

/** A shell or `source` whose script is `script` (undefined: stdin). Script files are out of reach. */
function checkScript(script: Word | undefined, s: Simple, depth: number, ctx: Ctx, deny: Deny): FenceHit | null {
	if (script?.procSub) return deny("a script from a process substitution cannot be checked");
	if ((script && !STDIN_PATH.test(script.v)) || ctx.stdinArgs) return null;
	if (s.heredoc !== undefined) return scanString(s.heredoc, s, depth, ctx);
	if (s.herestring !== undefined) return scanString(s.herestring, s, depth, ctx);
	return s.piped ? deny("a shell reading its script from a pipe cannot be checked") : null;
}

function checkShell(args: Word[], s: Simple, depth: number, ctx: Ctx, deny: Deny): FenceHit | null {
	let [j, dashC, dashS] = [0, false, false];
	for (; j < args.length; j++) {
		const a = args[j].v;
		if (a === "--") { j++; break; }
		if (a === "--command") { dashC = true; continue; }
		if (a.startsWith("--command=")) return scanString(a.slice(10), s, depth, ctx);
		if (a.startsWith("--")) { if (a === "--rcfile" || a === "--init-file") j++; continue; }
		if (!/^[-+][A-Za-z]+$/.test(a)) break;
		if (a[0] === "-") { dashC ||= a.includes("c"); dashS ||= a.includes("s"); }
		if (/[oO]/.test(a)) j++;
	}
	const rest = args.slice(j);
	if (dashC) return rest.length ? scanString(rest[0].v, s, depth, ctx) : ctx.stdinArgs ? deny("the -c script comes from xargs input") : null;
	return checkScript(dashS ? undefined : rest[0], s, depth, ctx, deny);
}

function checkXargs(args: Word[], s: Simple, depth: number): FenceHit | null {
	let [repl, j]: [string | undefined, number] = [undefined, 0];
	for (; j < args.length; j++) {
		const a = args[j].v;
		if (a === "--") { j++; break; }
		if (!a.startsWith("-") || a === "-") break;
		if (a === "-I") repl = args[++j]?.v;
		else if (a.startsWith("-I")) repl = a.slice(2);
		else if (a === "-i" || a === "--replace") repl = "{}";
		else if (a.startsWith("-i")) repl = a.slice(2);
		else if (a.startsWith("--replace=")) repl = a.slice(10);
		else if (XARGS_WITH_ARG.has(a)) j++;
	}
	return j < args.length ? checkWords(args.slice(j), s, depth, { stdinArgs: true, repl: repl || undefined }) : null;
}

/** PASS 1 — the guard as committed before the two-pass split: the offending command + optional reason, or null. Never throws. */
export function gitFenceVerdict(cmd: string): FenceHit | null {
	const src = String(cmd ?? "");
	try { return scan(src, 0); } catch (e) {
		return { command: clip(src) || "(unparseable command)", reason: e instanceof Unparseable ? e.message : "could not parse the command" };
	}
}

/** `shell`: the shell pi runs the command with — its `shellPath` setting; anything but zsh gets the bash rules (pass 2 only). */
export interface FenceOptions { shell?: string }

/** The offending command + optional reason, or null when the command may run. Never throws. DENIED when pass 1 or pass 2 denies,
 * the reason from the pass that denied, pass 1 first; pass 2 runs only when pass 1 allows, so it can never change a pass 1 denial. */
export function deniedGitVerdict(cmd: string, opts: FenceOptions = {}): FenceHit | null {
	const first = gitFenceVerdict(cmd);
	if (first) return first;
	try { return shimFenceVerdict(String(cmd ?? ""), { shell: opts?.shell, gitCheck: gitFenceVerdict }); } catch {
		return { command: clip(String(cmd ?? "")) || "(unparseable command)", reason: "could not parse the command" };
	}
}

/** The offending simple command (for the refusal message), or null when the command may run. */
export function deniedGitCommand(cmd: string, opts: FenceOptions = {}): string | null {
	return deniedGitVerdict(cmd, opts)?.command ?? null;
}

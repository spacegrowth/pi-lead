/**
 * pi-lead: the lead/executor pattern from claude-relay, on pi.
 *
 * One extension, three roles, picked by PI_LEAD_ROLE at launch (`roleFrom`; see CONTRACT "Executor lifecycle" /
 * "Lead lifecycle"):
 *   - executor: works a packet, may stage but never commit/push, writes a report, takes follow-up
 *     packets through its inbox file, and publishes its status (idle|busy|reported|closed) plus a
 *     `reported <sid> <report>` line into its lead's inbox once the current packet's report exists.
 *     After each settle, when packets are queued for it (`sessions/<sid>/queue.json`, `pilead send
 *     --when-idle`), it runs `pilead queue <sid> --deliver --trigger settle` without waiting for it: the
 *     next packet is delivered by pilead, through the inbox, with no lead and no polling involved.
 *   - reviewer ("reviewer", exactly): a read-only second pi (`pilead review`). Its one tool_call handler lets
 *     `read`, `grep`, `find` and `ls` through and refuses every other tool with REVIEWER_REFUSAL; it reads,
 *     watches and writes nothing under home, sends nothing, registers no command and sets the marker
 *     (PI_LEAD_HELD_BY) without reading it.
 *   - lead (any other value, or none): plans, delegates, reviews; wakes (a triggerTurn custom message) when an
 *     executor reports; in a REGISTERED lead (leads/<sid>.json exists) large inline edit/write calls,
 *     and an edit/write of any of the four control files (config.json, ledger.jsonl, a leads/<id>.route,
 *     a leads/<id>.json) at any size, are gated so they get delegated (or a human's word asked for)
 *     instead; a bash command with a hint of a write (`bashGateAsks`) is put to `pilead gate` (the bash
 *     write gate, lib/pilead/bashgate.py) and refused only when it answers `block` with a reason.
 *
 * A pi process that is not the session's own does no lead work (in the executor role, no executor
 * work): one that finds PI_LEAD_HELD_BY holding another process's id (it was started from inside a
 * pi-lead session's shell), or a lead whose run mode is print or json. The factory sets PI_LEAD_HELD_BY
 * to its own pid, so every process started from the session's shell inherits it.
 *
 * State lives under $PI_LEAD_HOME (default ~/.pi-lead):
 *   leads/<lead_sid>.json            { inbox, ... }   written by `pilead lead-start`
 *   leads/<lead_sid>.route           route grace file (mtime < grace_seconds → gate open)
 *   sessions/<exec_sid>/packet-NNNN.md, report-NNNN.md, inbox.md, status
 *   config.json                      edit_line_threshold / block_on_new_file / grace_seconds /
 *                                    poll_interval, read once at session start (lib/pilead/config.py)
 *   ledger.jsonl                     append-only events; this file adds reported / retained / blocked,
 *                                    wake_delivered / wake_recovered / wake_putback_failed,
 *                                    packet_recovered / packet_putback_failed,
 *                                    turn_end_suppressed / turn_end_failed
 *   wake/<lead_sid>/health.json      a registered lead's wake health (lib/pilead/wakehealth.py reads it):
 *                                    written at session_start, after each drain that delivered, put back
 *                                    or failed, every HEALTH_BEAT_MS, and with `ended` at session_shutdown;
 *                                    its `auto_wake` field is config.json's switch, its `version` the
 *                                    pi-lead version this pi loaded (`pilead list` VER)
 *   wake/<lead_sid>/notified/        banner claims (lib/pilead/notify.py), made by the pilead CLI
 *
 * Desktop banners: in pi's terminal UI (`ctx.mode` "tui") the lead runs `pilead notify <lead> --executor <sid>
 * --packet <n>` after a wake was handed over (the verb decides whether to post; lib/pilead/notify.py). An
 * executor that settled with a report runs `pilead escalate <sid> --packet <n> --json` (in the terminal UI or
 * RPC mode): lib/pilead/escalation.py decides, once per packet, whether the report reached a listening lead,
 * hands it to the one live lead of its project when its owner is gone, rewrites a lost inbox line and posts the
 * banner for a report nobody will hear. Neither is awaited and an error is swallowed.
 *
 * A lead's registration can move to another session id (`pilead takeover`, lib/pilead/succession.py), which leaves a
 * forward note `handoffs/<old>.json`. A wake is put back into the inbox of the lead `resolveOwner`'s walk from this
 * session's own id ends on, chosen when it is written (its successor's once a note names one; else where it came
 * from). A lead session whose record is gone takes and sends nothing; once a note names its successor it is named
 * `[ex-Lead] <project>` with one notice. `/new` and `/fork` in a registered lead's tab run `pilead takeover
 * <previous> --as <new> --force`; `/resume` moves nothing and says so.
 *
 * Tab names: pi writes the terminal title from the session name, so the extension names its own tab and nothing
 * is typed into it. A registered lead calls setSessionName with its record's `label` at each drain and health
 * beat; an executor with its meta.json's `tab_title`, else `label`, at each drain (session_start, the inbox, a
 * poll tick). Only a changed name is set (recordTabName).
 *
 * When pi has settled, a registered lead runs `pilead turn-end --json` (lib/pilead/turnend.py: parks finished
 * executors, names the lead's own commits, executors approaching heavy, the lead's own weight) and sends what
 * it says as ONE message with triggerTurn, at most one per TURN_END_GAP_MS. config.json `auto_wake: false`
 * turns off claiming the inbox, the wakes and this message (read once, at session_start; the reports stay in
 * the inbox for `pilead list` and `pilead check`).
 *
 * pi's footer (both roles, a UI with setStatus only) shows the first line of `pilead status <sid>
 * --statusline`, refreshed at session_start, each settle, a handed-over wake, /pilead:route retain and
 * every beat; one child at a time, never delaying a wake.
 *
 * A lead's wake message for a report also carries one line from `pilead check <sid> --refs-only`:
 * whether the repository's refs moved since the packet's baseline (lib/pilead/refs.py) — detection
 * of a commit made by any route, after the fact. It is fetched asynchronously; the claim stays
 * synchronous, so a wake is sent exactly once, in inbox order, with or without its refs line.
 * A claimed wake counts as delivered only once `sendMessage` for it has returned without throwing
 * (ledgered `wake_delivered`): the failed sends of one batch are put back in the inbox together, in
 * their first claim's order (and the next drain waits `retryMs`). On session_shutdown the lead sends
 * NOTHING (pi's sendMessage does not throw there, and a message handed to a session being torn down
 * would be lost): it stops waiting for refs lines, kills the `pilead` children still running, and puts
 * every pending wake back in the inbox, in order, with synchronous file calls, for the next lead
 * session's first drain. A wake already handed to `sendMessage` is not put back. Nothing is sent or
 * written after the handler returns. A put-back that fails is ledgered (`wake_putback_failed`), warned
 * and retried: its claim files stay on disk.
 *
 * INBOX CONSUMPTION (both roles) is rename-based: the reader renames the inbox to a unique
 * `<inbox>.claim-<pid>-<ms>-<seq>` sibling (atomic on one filesystem) and recreates an empty inbox with
 * O_APPEND (never truncating). Anything a writer appends after the rename lands in the fresh inbox and
 * is picked up on the next drain, so nothing is read twice or half-read. The claim file is KEPT until
 * its content was delivered: the lead rewrites it as each wake is handed over or put back and deletes
 * it when none is left; the executor deletes it once pi records the packet as a user message
 * (message_end). A claim file this instance does not hold whose process is gone, that this very process
 * made (an earlier instance), or that is CLAIM_STALE_MS old is orphaned: the next drain of any session
 * on that inbox puts its content back ahead of the inbox (`wake_recovered` / `packet_recovered`) and
 * deletes it. So after a kill a wake or packet may arrive twice; it is never lost. Writer contract: append a whole line/packet in ONE write (open-write-close), or write
 * a temp file and rename it into place. The only residual window is a writer that holds an fd
 * open across our rename and writes after we have read the claim — a one-write writer never does.
 * Watching is fs.watch on the inbox's DIRECTORY (a file watch would be lost on rename) plus a
 * slow poll as a fallback for missed FSEvents.
 */

import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { deniedGitVerdict } from "./bash-fence.ts";

export type Role = "lead" | "executor" | "reviewer";
export type Status = "idle" | "busy" | "reported" | "closed";

/** A single edit/write changing MORE than this many lines is gated in a registered lead session
 * (fallback for config.json `edit_line_threshold`; relay's default, though relay gates AT it). */
export const ROUTE_LINE_THRESHOLD = 40;
/** `/pilead:route retain` opens the gate for this long (grace file mtime age; fallback for
 * config.json `grace_seconds`; relay's default). */
export const ROUTE_GRACE_MS = 120 * 1000;
/** Fallback for config.json `poll_interval` (seconds there, ms here). */
const DEFAULT_POLL_MS = 3000;

/** The config.json keys this extension reads (the file is shared with lib/pilead/config.py). */
export interface PiLeadConfig {
	edit_line_threshold: number;
	block_on_new_file: boolean;
	grace_seconds: number;
	poll_interval: number;
}

export const CONFIG_DEFAULTS: Readonly<PiLeadConfig> = Object.freeze({
	edit_line_threshold: ROUTE_LINE_THRESHOLD,
	block_on_new_file: true,
	grace_seconds: ROUTE_GRACE_MS / 1000,
	poll_interval: DEFAULT_POLL_MS / 1000,
});

/** The values each numeric key may take (lib/pilead/config.py RANGES, same rules). */
const CONFIG_RANGES: Partial<Record<keyof PiLeadConfig, (v: number) => boolean>> = {
	edit_line_threshold: (v) => Number.isInteger(v) && v >= 1, // a whole number of lines, ≥ 1
	grace_seconds: (v) => v >= 1, // ≥ 1 s
	poll_interval: (v) => v === 0 || v >= 1, // 0 = no poll, else ≥ 1 s
};

/**
 * `<home>/config.json` merged over CONFIG_DEFAULTS, by the rules of lib/pilead/config.py: unknown
 * keys ignored, a value whose JSON type differs from the default's — or a number outside its key's
 * range — ignored for that key, a missing or corrupt file means pure defaults. Never throws.
 */
export function loadConfig(home: string): PiLeadConfig {
	const cfg: PiLeadConfig = { ...CONFIG_DEFAULTS };
	try {
		const user = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf-8"));
		if (user && typeof user === "object" && !Array.isArray(user)) {
			for (const k of Object.keys(CONFIG_DEFAULTS) as (keyof PiLeadConfig)[]) {
				const v = user[k];
				if (typeof v !== typeof CONFIG_DEFAULTS[k] || v === null) continue;
				const inRange = CONFIG_RANGES[k];
				if (inRange && !(Number.isFinite(v) && inRange(v))) continue;
				(cfg as any)[k] = v;
			}
		}
	} catch {}
	return cfg;
}

/** UTC ISO to the second, the same stamp lib/pilead/state.py `now_iso` writes. */
function isoSeconds(ms: number): string {
	return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Append one `{ts, event, ...fields}` line to `<home>/ledger.jsonl` — the same file and shape as
 * lib/pilead/ledger.py. One appendFileSync per record; best-effort, never throws.
 */
export function appendLedger(home: string, event: string, fields: Record<string, unknown>, nowMs: number = Date.now()): void {
	try {
		fs.mkdirSync(home, { recursive: true });
		fs.appendFileSync(path.join(home, "ledger.jsonl"), `${JSON.stringify({ ts: isoSeconds(nowMs), event, ...fields })}\n`);
	} catch {}
}

/** "2 min" for whole minutes (the default), else seconds. */
function graceText(ms: number): string {
	return ms % 60000 === 0 ? `${ms / 60000} min` : `${ms / 1000} s`;
}

export interface PiLeadOptions {
	/** Environment to read PI_LEAD_* from (default process.env). */
	env?: NodeJS.ProcessEnv;
	/** Clock for the route grace window and the hold after a failed send (default Date.now). */
	now?: () => number;
	/** Start fs.watch watchers on session_start (default true). */
	watch?: boolean;
	/** Fallback inbox poll interval in ms; 0 disables (default: config.json `poll_interval`, else 3000). */
	pollMs?: number;
	/** Directory holding pilead-<name>/SKILL.md (default: this package's skills/; tests override). */
	skillsDir?: string;
	/** The one-line refs result for an executor's wake message (default: `wakeFacts`, which runs
	 *  `pilead check <sid> --refs-only --wake` and also reads the size of the staged diff and whether the work
	 *  landed). May return the line or a promise of it; an injected one gives no such facts. */
	refsCheck?: (home: string, sid: string, signal: AbortSignal) => string | Promise<string>;
	/** The `pilead` the default refsCheck runs (default: this package's bin/pilead; tests override). */
	pileadBin?: string;
	/** After a wake's send throws (outside shutdown) and it is put back in the inbox, drains wait this
	 *  long before claiming again, so a failing send never spins (default SEND_RETRY_MS). */
	retryMs?: number;
	/** This process's id, written into PI_LEAD_HELD_BY and into the names of claim files (default
	 *  process.pid; tests override). */
	pid?: number;
	/** The footer's status line for session `sid` (default: `statusLineText`, which runs `pilead status
	 *  <sid> --statusline --home <home> [args]`). `args` holds `--ctx-tokens N` / `--ctx-window N` when pi
	 *  gives them (lead only). May return the text or a promise of it; its first line is shown. */
	statusLine?: (home: string, sid: string, args: string[], signal: AbortSignal) => string | Promise<string>;
	/** Post the desktop banner for a report: gets the argument list of the `pilead` call (`notify <lead>
	 *  --executor <sid> --packet <n> --home <home>`; the lead only).
	 *  Default: runs `pilead` with it (`execFile`, no shell, BANNER_TIMEOUT_MS), not awaited, errors swallowed.
	 *  Asked only when the latest event's `ctx.mode` is "tui". */
	banner?: (args: string[]) => void | Promise<void>;
	/** What the lead is told when pi has settled: gets the argument list of the `pilead` call (`turn-end
	 *  --session <sid> --cwd <cwd> --json --home <home>`, `--banner` added in the terminal UI) and a signal that
	 *  session_shutdown aborts, and returns the child's stdout (one JSON object with `lines`). Default: runs
	 *  `pilead` with it (`execFile`, no shell, TURN_END_TIMEOUT_MS), only when `ctx.hasUI` and `ctx.mode` is
	 *  "tui" or "rpc". Given, it is used whatever `ctx.mode` is. */
	turnEnd?: (args: string[], signal: AbortSignal) => string | Promise<string>;
	/** The bash write gate's answer for a registered lead's bash command: gets the command's text and the cwd,
	 *  and returns the answer's text (one JSON line, as `pilead gate --json` prints it) or a promise of it.
	 *  Default: runs `pilead gate --session <sid> --cwd <cwd> --record --json --home <home>` (`execFile`, no
	 *  shell, the command on stdin, BASH_GATE_TIMEOUT_MS). Asked only for a command `bashGateAsks` says yes to. */
	bashGate?: (command: string, cwd: string) => string | Promise<string>;
	/** The executor's escalation of a settled report: gets the argument list of the `pilead` call (`escalate <sid>
	 *  --packet <n> --json --home <home>`). Default: runs `pilead` with it (`execFile`, no shell,
	 *  ESCALATE_TIMEOUT_MS, a signal that session_shutdown aborts), not awaited, errors swallowed, only when
	 *  `ctx.hasUI` and `ctx.mode` is "tui" or "rpc". Given, it is used whatever `ctx.mode` is. */
	escalate?: (args: string[]) => void | Promise<void>;
	/** How long the lead's `pilead takeover` child (after `/new` or `/fork`) may run (default TAKEOVER_TIMEOUT_MS;
	 *  tests shorten it). */
	takeoverTimeoutMs?: number;
	/** The package.json whose `version` the lead writes into its health file (default: the one two directories
	 *  above this file, this package's own; tests point it elsewhere). */
	packageJson?: string;
}

export interface PiLeadHandle {
	role: Role;
	home: string;
	/** Drain this role's inbox now (what the watchers call). Returns items delivered — for the lead,
	 *  the wakes claimed: each is sent once its refs line arrives (see `flush`). */
	drainInbox(): number;
	/** Resolves once every wake claimed so far has been sent or put back (lead; immediate for an
	 *  executor). */
	flush(): Promise<void>;
	/** Stop watchers/timers (idempotent; also run on session_shutdown). */
	stop(): void;
	/** True when this process does no pi-lead work (see `quietMode` / `heldByAnother`): in the lead
	 *  role no lead work, in the executor role no executor work. The mode is known from session_start on. */
	leadIsQuiet(): boolean;
}

// ---- small fs helpers --------------------------------------------------------------------------

function ensureFile(file: string): void {
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.closeSync(fs.openSync(file, "a")); // create if missing; never truncates a writer's content
}

function writeAtomic(file: string, text: string): void {
	fs.mkdirSync(path.dirname(file), { recursive: true });
	const tmp = `${file}.tmp-${process.pid}`;
	fs.writeFileSync(tmp, text);
	fs.renameSync(tmp, file);
}

let claimSeq = 0;

/** Atomically take the current contents of `file` (see INBOX CONSUMPTION above). "" if empty. */
export function claimFile(file: string): string {
	try {
		if (fs.statSync(file).size === 0) return "";
	} catch {
		return "";
	}
	const claim = `${file}.claim-${process.pid}-${Date.now()}-${claimSeq++}`;
	try {
		fs.renameSync(file, claim);
	} catch {
		return ""; // raced with another reader or the file vanished
	}
	ensureFile(file);
	const text = fs.readFileSync(claim, "utf-8");
	fs.unlinkSync(claim);
	return text;
}

/** A claim file whose name's `<ms>` is this long or more before now is orphaned, whoever made it
 * (lib/pilead/wakehealth.py CLAIM_STALE_SECONDS is the same, in seconds). */
export const CLAIM_STALE_MS = 60_000;

/** The `<pid>`, `<ms>` and `<seq>` of a claim file named `<inboxBase>.claim-<pid>-<ms>-<seq>`, or null for any other name. */
function parseClaimName(name: string, inboxBase: string): { pid: number; ms: number; seq: number } | null {
	const prefix = `${inboxBase}.claim-`;
	if (!name.startsWith(prefix)) return null;
	const m = /^(\d+)-(\d+)-(\d+)$/.exec(name.slice(prefix.length));
	if (!m) return null;
	const [pid, ms, seq] = [m[1], m[2], m[3]].map(Number);
	return [pid, ms, seq].every(Number.isSafeInteger) ? { pid, ms, seq } : null;
}

/**
 * Take the current contents of `file` into a claim file `<file>.claim-<pid>-<ms>-<seq>` that STAYS on disk
 * (the caller deletes it when it is done with it), and make a new empty `file`. null when there is nothing
 * to claim: no file, an empty one, or a path that is not a regular file. A rename that fails for another
 * reason than a vanished file, or a read that fails, throws (a claim that could not be read stays on disk).
 */
function takeClaim(file: string, pid: number, ms: number): { file: string; text: string } | null {
	let st: fs.Stats;
	try {
		st = fs.statSync(file);
	} catch {
		return null;
	}
	if (!st.isFile() || st.size === 0) return null;
	const claim = `${file}.claim-${pid}-${Math.max(0, Math.floor(ms))}-${claimSeq++}`;
	try {
		fs.renameSync(file, claim);
	} catch (e) {
		if ((e as NodeJS.ErrnoException)?.code === "ENOENT") return null; // raced with another reader
		throw e;
	}
	ensureFile(file);
	return { file: claim, text: fs.readFileSync(claim, "utf-8") };
}

/** True only when no process has `pid` (`process.kill(pid, 0)` fails with ESRCH). */
function pidGone(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return false;
	} catch (e) {
		return (e as NodeJS.ErrnoException)?.code === "ESRCH";
	}
}

/** The first line of an error's message, at most 200 characters; never empty. */
function errorLine(e: unknown): string {
	let text: string;
	try {
		text = e instanceof Error ? e.message : String(e);
	} catch {
		text = "";
	}
	return (text.split("\n")[0] || "error").slice(0, 200);
}

/** `report-0003.md` → 3: the last run of digits in the report's file name, or null when it has none. */
function reportPacket(report: string): number | null {
	const m = /(\d+)(?!.*\d)/.exec(path.basename(report));
	return m ? Number.parseInt(m[1], 10) : null;
}

/** The text of a message's content: a string as it is, else its text parts joined by newlines (pi's own rule). */
function messageText(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((b) => b && typeof b === "object" && (b as any).type === "text" && typeof (b as any).text === "string")
		.map((b) => (b as any).text as string)
		.join("\n");
}

function expandHome(p: string): string {
	if (p === "~") return os.homedir();
	if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
	return p;
}

/** Resolve symlinks through the nearest existing ancestor (the target itself may not exist). */
function realish(p: string): string {
	const tail: string[] = [];
	let cur = path.resolve(p);
	for (;;) {
		try {
			return path.join(fs.realpathSync(cur), ...tail.reverse());
		} catch {
			const parent = path.dirname(cur);
			if (parent === cur) return path.resolve(p);
			tail.push(path.basename(cur));
			cur = parent;
		}
	}
}

function isUnder(child: string, root: string): boolean {
	const rel = path.relative(root, child);
	return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

// ---- executor: packet/report/status ------------------------------------------------------------

export interface CurrentPacket {
	num: string;
	packet: string;
	report: string;
}

/** Highest-numbered packet-NNNN.md in a session dir, with its report-NNNN.md path. */
export function currentPacket(sessionDir: string): CurrentPacket | null {
	let best: { n: number; num: string } | null = null;
	let names: string[];
	try {
		names = fs.readdirSync(sessionDir);
	} catch {
		return null;
	}
	for (const name of names) {
		const m = /^packet-(\d+)\.md$/.exec(name);
		if (!m) continue;
		const n = Number.parseInt(m[1], 10);
		if (!best || n > best.n) best = { n, num: m[1] };
	}
	if (!best) return null;
	return {
		num: best.num,
		packet: path.join(sessionDir, `packet-${best.num}.md`),
		report: path.join(sessionDir, `report-${best.num}.md`),
	};
}

export function readStatus(sessionDir: string): string {
	try {
		return fs.readFileSync(path.join(sessionDir, "status"), "utf-8").trim();
	} catch {
		return "";
	}
}

export function writeStatus(sessionDir: string, status: Status): void {
	writeAtomic(path.join(sessionDir, "status"), `${status}\n`);
}

/** The longest usable session id (lib/pilead/state.py `SESSION_SID_MAX`). */
export const SESSION_SID_MAX = 128;
/** The longest usable lead id (lib/pilead/state.py `LEAD_SID_MAX`). */
export const LEAD_SID_MAX = 128;

/**
 * A session id (an executor's; also pi's own --session-id) names sessions/<sid>/, so it has one safe
 * form — the same rule, to the character, as `valid_session_sid` in lib/pilead/state.py
 * (tests/session-ids.json is the table both suites read): pi's own `assertValidSessionId` pattern, no
 * `..`, at most SESSION_SID_MAX characters. false for anything that is not a string. Never throws.
 */
export function validSessionSid(sid: unknown): boolean {
	try {
		return (
			typeof sid === "string" &&
			sid.length <= SESSION_SID_MAX &&
			/^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(sid) &&
			!sid.includes("..")
		);
	} catch {
		return false;
	}
}

/**
 * A lead id names files under leads/: a usable session id that is at most LEAD_SID_MAX characters and
 * does not end in `.usage` — `valid_lead_sid` in lib/pilead/state.py (tests/lead-ids.json). Never throws.
 */
export function validLeadSid(sid: unknown): boolean {
	try {
		return validSessionSid(sid) && (sid as string).length <= LEAD_SID_MAX && !(sid as string).endsWith(".usage");
	} catch {
		return false;
	}
}

/** The lead's inbox path from leads/<leadSid>.json, or null if it isn't registered (yet) or the id is not usable. */
export function leadInboxPath(home: string, leadSid: string | undefined): string | null {
	if (!validLeadSid(leadSid)) return null;
	const leadsDir = path.join(home, "leads");
	try {
		const meta = JSON.parse(fs.readFileSync(path.join(leadsDir, `${leadSid}.json`), "utf-8"));
		if (typeof meta?.inbox !== "string" || !meta.inbox) return null;
		return path.resolve(leadsDir, expandHome(meta.inbox));
	} catch {
		return null;
	}
}

function hasReport(report: string): boolean {
	try {
		return fs.statSync(report).size > 0;
	} catch {
		return false;
	}
}

/** Forward notes followed by `resolveOwner` at most (lib/pilead/ownership.py MAX_MOVES). */
export const OWNER_MAX_MOVES = 8;

/**
 * The lead that owns executor `sid` now: the start is `lead` of `<home>/sessions/<sid>/meta.json` when it parses
 * and names a usable lead id (`validLeadSid`), else `envLead` when usable, else there is none (null). From the
 * start, forward notes (`<home>/handoffs/<id>.json`, `{"to": <id>, …}`) are followed while no lead record
 * exists for the id, at most OWNER_MAX_MOVES moves, every id checked — `resolve` in lib/pilead/ownership.py, to
 * the step (tests/owner-cases.json). Returns the id the walk ended on (a registered lead's, or one no record and
 * no note lead on from), or null when the answer is unknown: a record that exists and does not read as a JSON
 * object, a note that cannot be read or names an unusable id, a loop, or a chain too long. Never throws, never
 * writes.
 */
export function resolveOwner(home: string, sid: string, envLead: string | undefined): string | null {
	try {
		let start: string | undefined;
		if (validSessionSid(sid)) {
			try {
				const meta = JSON.parse(fs.readFileSync(path.join(home, "sessions", sid, "meta.json"), "utf-8"));
				if (meta !== null && typeof meta === "object" && !Array.isArray(meta) && validLeadSid(meta.lead)) start = meta.lead;
			} catch {}
		}
		if (start === undefined && validLeadSid(envLead)) start = envLead;
		if (start === undefined) return null;
		const seen = new Set<string>();
		let cur: string = start;
		for (let moves = 0; ; ) {
			if (!validLeadSid(cur)) return null;
			seen.add(cur);
			const rec = path.join(home, "leads", `${cur}.json`);
			if (exists(rec)) {
				try {
					const d = JSON.parse(fs.readFileSync(rec, "utf-8"));
					return d !== null && typeof d === "object" && !Array.isArray(d) ? cur : null;
				} catch {
					return null;
				}
			}
			const note = path.join(home, "handoffs", `${cur}.json`);
			if (!exists(note)) return cur;
			let to: unknown;
			try {
				const d = JSON.parse(fs.readFileSync(note, "utf-8"));
				to = d !== null && typeof d === "object" && !Array.isArray(d) ? d.to : undefined;
			} catch {
				return null;
			}
			if (!validLeadSid(to) || seen.has(to as string) || moves >= OWNER_MAX_MOVES) return null;
			moves++;
			cur = to as string;
		}
	} catch {
		return null;
	}
}

/** Whether something is at `p` (a dangling link counts), without following it. */
function exists(p: string): boolean {
	try {
		fs.lstatSync(p);
		return true;
	} catch {
		return false;
	}
}

/**
 * Whether `.notified`'s text `last` says report `report` was announced as it is now. Two forms match: the
 * extension's own `<report> <mtimeMs>` (the whole text equals it), and `<report> ns:<digits>` written by
 * lib/pilead/ownership.py `adopt`, whose `<digits>` equal the report's `mtimeNs` (its path the same file). Anything
 * else is "not announced". Never throws.
 */
export function notifiedMatches(last: string, report: string): boolean {
	try {
		if (last === `${report} ${fs.statSync(report).mtimeMs}`) return true;
		const m = /^(.*) ns:(\d+)$/.exec(last);
		if (!m) return false;
		const same = m[1] === report || fs.realpathSync(m[1]) === fs.realpathSync(report);
		return same && BigInt(m[2]) === fs.statSync(report, { bigint: true }).mtimeNs;
	} catch {
		return false;
	}
}

/**
 * Append `reported <sid> <report>` to the lead inbox once per report version. The marker file
 * `.notified` holds "<report> <mtimeMs>" (or the `ns:` form `pilead adopt` writes, `notifiedMatches`), so a
 * settle that doesn't rewrite the report (the human pokes the tab after reporting) never re-wakes the lead,
 * while a rewritten report does. `leadSid` is the owner as `resolveOwner` finds it at this settle, so a session
 * adopted while it works wakes its new owner. Returns false when the lead inbox is unknown — the marker isn't
 * written, so a later settle retries.
 */
function notifyLead(home: string, sid: string, leadSid: string | null | undefined, sessionDir: string, report: string, packet: number): boolean {
	const inbox = leadInboxPath(home, leadSid ?? undefined);
	if (!inbox) return false;
	const stamp = `${report} ${fs.statSync(report).mtimeMs}`;
	const marker = path.join(sessionDir, ".notified");
	let last = "";
	try {
		last = fs.readFileSync(marker, "utf-8").trim();
	} catch {}
	if (notifiedMatches(last, report)) return true;
	fs.mkdirSync(path.dirname(inbox), { recursive: true });
	fs.appendFileSync(inbox, `reported ${sid} ${report}\n`); // one O_APPEND write: atomic line
	writeAtomic(marker, `${stamp}\n`);
	appendLedger(home, "reported", { session_id: sid, packet });
	return true;
}

// ---- lead: routing gate ------------------------------------------------------------------------

function countLines(s: unknown): number {
	if (typeof s !== "string" || s === "") return 0;
	const n = s.split("\n").length;
	return s.endsWith("\n") ? n - 1 : n;
}

/**
 * Lines a single edit/write call changes. write → lines of content (a full rewrite);
 * edit → Σ max(lines(oldText), lines(newText)) per replacement, so a big deletion counts as much
 * as a big insertion. Unknown shapes count 0 (fail-open: under-counting is the safe direction).
 */
export function changedLines(toolName: string, input: Record<string, unknown>): number {
	if (toolName === "write") return countLines(input.content);
	if (toolName !== "edit") return 0;
	const edits: unknown[] = Array.isArray(input.edits) ? [...input.edits] : [];
	if (typeof input.oldText === "string" || typeof input.newText === "string") {
		edits.push({ oldText: input.oldText, newText: input.newText }); // legacy single-edit shape
	}
	let total = 0;
	for (const e of edits) {
		if (!e || typeof e !== "object") continue;
		const { oldText, newText } = e as Record<string, unknown>;
		total += Math.max(countLines(oldText), countLines(newText));
	}
	return total;
}

/** Resolve a tool path the way pi's tools do: strip a leading "@", expand "~", relative to cwd. */
export function resolveToolPath(p: string, cwd: string): string {
	const stripped = p.startsWith("@") ? p.slice(1) : p;
	return path.resolve(cwd, expandHome(stripped));
}

/**
 * The kind of pi-lead control file `absPath` is, resolved through symlinks as `isRouteExempt` resolves:
 * `<home>/config.json` → "settings" (the gate's own threshold is in it), `<home>/ledger.jsonl` →
 * "ledger" (the record of what the gate did), `<home>/leads/<anything>.route` → "grace file" (its age
 * opens the gate), `<home>/leads/<anything>.json` → "lead record" (its existence turns the gate on).
 * null for any other path. tests/control-files.json is the table this and lib/pilead/bashgate.py
 * `control_kind` both read. Never throws.
 */
export function controlFileKind(absPath: string, home: string): string | null {
	try {
		// However the name is cased, as the bash write gate reads it (lib/pilead/bashgate.py): on a
		// case-insensitive disk CONFIG.JSON is config.json, and gating more can only refuse.
		const rel = path.relative(realish(home).toLowerCase(), realish(absPath).toLowerCase());
		if (rel === "config.json") return "settings";
		if (rel === "ledger.jsonl") return "ledger";
		if (/^leads\/[^/]*\.route$/.test(rel)) return "grace file";
		if (/^leads\/[^/]*\.json$/.test(rel)) return "lead record";
		return null;
	} catch {
		return null;
	}
}

/**
 * Whether `leads/<leadSid>.json` exists as a file under `home` — what `pilead lead-start` writes and
 * `pilead stop` removes. Only its existence counts: the record is never read (an unparsable one still
 * counts). false for an id `validLeadSid` refuses, and on any error. Never throws.
 */
export function isRegisteredLead(home: string, leadSid: string): boolean {
	try {
		if (!validLeadSid(leadSid)) return false;
		return fs.statSync(path.join(home, "leads", `${leadSid}.json`)).isFile();
	} catch {
		return false;
	}
}

/**
 * Paths the lead writes as its own job: ~/.relay-tasks/**, ~/.pi-lead/**, $PI_LEAD_HOME/**, *-packet.md
 * — except the four control files (`controlFileKind`), which are never exempt.
 */
export function isRouteExempt(absPath: string, home: string): boolean {
	if (controlFileKind(absPath, home) !== null) return false;
	if (path.basename(absPath).endsWith("-packet.md")) return true;
	const target = realish(absPath);
	const roots = [path.join(os.homedir(), ".relay-tasks"), path.join(os.homedir(), ".pi-lead"), home];
	return roots.some((r) => isUnder(target, realish(r)));
}

export function routeGraceFile(home: string, leadSid: string): string {
	return path.join(home, "leads", `${leadSid}.route`);
}

export function inRouteGrace(home: string, leadSid: string, now: number, graceMs: number = ROUTE_GRACE_MS): boolean {
	try {
		return now - fs.statSync(routeGraceFile(home, leadSid)).mtimeMs < graceMs;
	} catch {
		return false;
	}
}

export type RouteDecision =
	| { block: false }
	| { block: true; reason: string; filePath: string; lines: number; newFile: boolean; control?: string };

/** The lead's routing gate for one edit/write call. Any error → allow (fail-open). A session that is
 * not a registered lead (`isRegisteredLead`) is never gated. A control file (`controlFileKind`) is gated
 * at any size while the grace window is closed. The three knobs default to the exported constants;
 * the extension passes config.json's values. */
export function routeDecision(args: {
	toolName: string;
	input: Record<string, unknown>;
	cwd: string;
	home: string;
	leadSid: string;
	now: number;
	threshold?: number;
	graceMs?: number;
	blockOnNewFile?: boolean;
}): RouteDecision {
	try {
		const { toolName, input, cwd, home, leadSid, now } = args;
		const threshold = args.threshold ?? ROUTE_LINE_THRESHOLD;
		const graceMs = args.graceMs ?? ROUTE_GRACE_MS;
		const blockOnNewFile = args.blockOnNewFile ?? true;
		// Only a registered lead is gated, asked at every call: on after `pilead lead-start`, off after `pilead stop`.
		if (!isRegisteredLead(home, leadSid)) return { block: false };
		if (toolName !== "edit" && toolName !== "write") return { block: false };
		if (typeof input.path !== "string" || !input.path) return { block: false };
		const abs = resolveToolPath(input.path, cwd);
		if (isRouteExempt(abs, home)) return { block: false };
		if (inRouteGrace(home, leadSid, now, graceMs)) return { block: false };
		// Only write creates files; an edit of a missing path fails on its own, so don't gate it.
		const newFile = toolName === "write" && !fs.existsSync(abs);
		const lines = changedLines(toolName, input);
		const control = controlFileKind(abs, home);
		if (control !== null) {
			return {
				block: true,
				filePath: abs,
				lines,
				newFile,
				control,
				reason:
					`pi-lead lead gate: ${input.path} is a pi-lead control file (${control}) — a change to it needs a ` +
					`human's word; ask, or \`/pilead:route retain "<reason>"\` and retry within ${graceText(graceMs)}.`,
			};
		}
		if (!(newFile && blockOnNewFile) && lines <= threshold) return { block: false };
		const size = newFile ? `new file, ${lines} lines` : `${lines} changed lines`;
		return {
			block: true,
			filePath: abs,
			lines,
			newFile,
			reason:
				`pi-lead lead gate: this ${toolName} of ${input.path} (${size}) is implementation work — ` +
				`delegate this (pilead spawn/send) or \`/pilead:route retain "<reason>"\` and retry within ` +
				`${graceText(graceMs)}. (A write made through bash meets the same gate where its shape can be read.)`,
		};
	} catch {
		return { block: false };
	}
}

// ---- lead: the bash write gate ------------------------------------------------------------------

/**
 * The hint: a cheap test of a bash command's text that says whether asking `pilead gate` can matter. It is
 * true for a redirect (`>`) or a here-document (`<<`); for the programs whose writes the gate's parser reads
 * (`tee`, `sed`, `gsed`, `cp`, `mv`, `python` with any version); for the words of the gate's verb log (`npm`,
 * `yarn`, `pip`, `pip3`, `tsc`, `gcc`, `g++`, `clang`, `go`, `cargo`, `make`, `git clone`, `systemctl`,
 * `rsync`, `/etc/systemd/system/`); and — because the gate refuses a command that NAMES a control file, not
 * only one that writes it — for the control files' names and the home's markers as raw text spells them
 * (`config.json`, `ledger.jsonl`, `.route`, `leads`, `.pi-lead`, `pi_lead_home`), in any case, and for `awk`
 * (its own writes are read by the gate's raw-text rule). It may say yes too often; it must never say no for a
 * command the gate answers `log` or `block` (tests/bash-gate-commands.json is checked against it).
 */
export const BASH_GATE_HINT =
	/>|<<|\b(?:tee|g?sed|cp|mv|python[0-9.]*|npm|yarn|pip3?|tsc|gcc|clang|go|cargo|make|systemctl|rsync|[gm]?awk)\b|\bg\+\+|\bgit\s+clone\b|\/etc\/systemd\/system\/|config\.json|ledger\.jsonl|\.route|leads|\.pi-lead|pi_lead_home/i;

/** Whether a bash command's text holds a hint (`BASH_GATE_HINT`). false for anything that is not a text and for
 *  the empty text. Never throws. */
export function bashGateHint(command: unknown): boolean {
	try {
		return typeof command === "string" && command !== "" && BASH_GATE_HINT.test(command);
	} catch {
		return false;
	}
}

/**
 * Whether the lead's bash handler asks the gate about `command` run in `cwd`: the hint (`bashGateHint`), or —
 * what a test of the text alone cannot see — the command names the home's own path (as given or resolved,
 * in any case), or `cwd` lies inside home (where a bare `leads` or `..` is a control path). Never throws.
 */
export function bashGateAsks(command: unknown, cwd: unknown, home: string): boolean {
	try {
		if (bashGateHint(command)) return true;
		if (typeof command !== "string" || command === "") return false;
		const low = command.toLowerCase();
		const homes = [path.resolve(home), realish(home)].map((h) => h.toLowerCase()).filter((h) => h !== path.sep);
		if (homes.some((h) => low.includes(h))) return true;
		return typeof cwd === "string" && cwd !== "" && isUnder(realish(cwd).toLowerCase(), realish(home).toLowerCase());
	} catch {
		return false;
	}
}

/** How long the `pilead gate` child may run before it is killed and the command runs unchecked. */
export const BASH_GATE_TIMEOUT_MS = 5_000;

/** The answer of the `pilead gate` child: its stdout, or why there is none. */
export type BashGateReply = { text: string } | { why: "timed out" | "failed" };

/**
 * Ask `<bin> gate --session <sid> --cwd <cwd> --record --json --home <home>` about `command`, which goes to the
 * child's stdin (never onto its command line; no shell). Never throws and never rejects: a child that could not
 * be started or failed is `failed`, one killed at `timeoutMs` is `timed out`.
 */
export function bashGateReply(
	command: string,
	cwd: string,
	home: string,
	sid: string,
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = BASH_GATE_TIMEOUT_MS,
): Promise<BashGateReply> {
	return new Promise((resolve) => {
		try {
			const args = ["gate", "--session", sid, "--cwd", cwd, "--record", "--json", "--home", home];
			const child = execFile(bin, args, { encoding: "utf-8", timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 }, (err, stdout) => {
				if (err) return resolve({ why: (err as { killed?: boolean }).killed ? "timed out" : "failed" });
				resolve({ text: String(stdout ?? "") });
			});
			child.stdin?.on("error", () => {}); // a child gone before it read stdin: its own exit says so
			child.stdin?.end(command);
		} catch {
			resolve({ why: "failed" });
		}
	});
}

/**
 * What the lead's bash handler does with the gate's reply: `{ block, reason }` only for one JSON object whose
 * `verdict` is `block` and whose `reason` is a non-empty text; otherwise the command runs, and `warn` names why
 * it was not checked when it was not (`timed out`, `failed`, `unreadable answer` — not one JSON object, or an
 * object whose verdict is neither `allow`, `log` nor a `block` with a reason — or `not read`, `"parsed": false`).
 * A plain `allow` or `log` with `parsed` true gives neither. Never throws.
 */
export function bashGateVerdict(reply: BashGateReply): { block?: { block: true; reason: string }; warn?: string } {
	try {
		if ("why" in reply) return { warn: reply.why };
		const lines = reply.text.split("\n").filter((l) => l.trim() !== "");
		if (lines.length !== 1) return { warn: "unreadable answer" };
		const d = JSON.parse(lines[0]);
		if (d === null || typeof d !== "object" || Array.isArray(d)) return { warn: "unreadable answer" };
		if (d.verdict === "block" && typeof d.reason === "string" && d.reason !== "") return { block: { block: true, reason: d.reason } };
		if (d.verdict !== "allow" && d.verdict !== "log") return { warn: "unreadable answer" };
		return d.parsed === false ? { warn: "not read" } : {};
	} catch {
		return { warn: "unreadable answer" };
	}
}

// ---- watcher -----------------------------------------------------------------------------------

class InboxWatcher {
	private watchers = new Map<string, fs.FSWatcher>();
	private timer: ReturnType<typeof setInterval> | undefined;

	constructor(private readonly onEvent: () => void) {}

	/** Watch a directory (idempotent). Errors are swallowed: the poll still covers it. */
	watchDir(dir: string): void {
		if (this.watchers.has(dir)) return;
		try {
			fs.mkdirSync(dir, { recursive: true });
			const w = fs.watch(dir, () => this.onEvent());
			w.on("error", () => {});
			this.watchers.set(dir, w);
		} catch {}
	}

	poll(ms: number): void {
		if (ms <= 0 || this.timer) return;
		this.timer = setInterval(() => this.onEvent(), ms);
		this.timer.unref?.();
		this.pollMs = ms;
	}

	/** The poll interval running now, in ms (0 = none). */
	pollMs = 0;

	/** Whether a directory watcher is running. */
	watching(): boolean {
		return this.watchers.size > 0;
	}

	stop(): void {
		for (const w of this.watchers.values()) w.close();
		this.watchers.clear();
		if (this.timer) clearInterval(this.timer);
		this.timer = undefined;
		this.pollMs = 0;
	}
}

// ---- refs detection ----------------------------------------------------------------------------

/** How long the lead waits for one refs line before giving up on it (the wake is sent regardless). */
export const REFS_TIMEOUT_MS = 20_000;
/** After a wake's send throws and it is put back in the inbox, how long drains wait before retrying. */
export const SEND_RETRY_MS = 5_000;
/** A registered lead's health file (wake/<sid>/health.json) is rewritten at least this often; three of these
 * without a beat make it stale (lib/pilead/wakehealth.py STALE_SECONDS). The footer's status line is
 * refreshed at the same beat. */
export const HEALTH_BEAT_MS = 30_000;
/** How long the footer's `pilead status --statusline` child may run before it is killed. */
export const STATUS_LINE_TIMEOUT_MS = 5_000;
/** How long a `pilead notify` child (the desktop banner for a report) may run before it is killed. */
export const BANNER_TIMEOUT_MS = 10_000;
/** How long the executor's `pilead escalate` child may run before it is killed. */
export const ESCALATE_TIMEOUT_MS = 30_000;

/**
 * The footer text for session `sid`: the first stdout line of `<package>/bin/pilead status <sid> --statusline
 * --home <home> [args]`, run asynchronously as a child with no shell. Never throws and never rejects: a slow,
 * failing or missing pilead gives "" (which clears the footer text). Aborting `signal` kills the child.
 */
export function statusLineText(
	home: string,
	sid: string,
	args: string[] = [],
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = STATUS_LINE_TIMEOUT_MS,
	signal?: AbortSignal,
): Promise<string> {
	return new Promise((resolve) => {
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, ...(signal ? { signal } : {}) };
			execFile(bin, ["status", sid, "--statusline", "--home", home, ...args], o, (err, stdout) => {
				if (err) return resolve("");
				resolve(String(stdout ?? "").split("\n")[0].trim());
			});
		} catch {
			resolve("");
		}
	});
}

/**
 * The one-line refs result for executor `sid`: `refs: unchanged since packet NNNN`,
 * `REFS MOVED: <n> change(s) since packet NNNN`, `refs: not compared (…)` or `refs: not a git repository`
 * — the first stdout line of `<package>/bin/pilead check <sid> --refs-only --home <home>`, run
 * asynchronously so the lead's event loop never waits on it. Never throws and never rejects: a slow,
 * failing or missing pilead gives `refs: not checked (<why>)`, so the wake itself is never lost.
 * Aborting `signal` kills the child (SIGTERM) and resolves `refs: not checked (aborted)`.
 */
export function refsLine(
	home: string,
	sid: string,
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = REFS_TIMEOUT_MS,
	signal?: AbortSignal,
): Promise<string> {
	return new Promise((resolve) => {
		const notChecked = (why: string) => resolve(`refs: not checked (${why})`);
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, ...(signal ? { signal } : {}) };
			execFile(bin, ["check", sid, "--refs-only", "--home", home], o, (err, stdout) => {
				if (err) {
					const e = err as NodeJS.ErrnoException & { killed?: boolean };
					if (e.name === "AbortError" || signal?.aborted) return notChecked("aborted");
					if (e.killed) return notChecked(`timed out after ${timeoutMs} ms`);
					return notChecked(String(e.message ?? e).split("\n")[0]);
				}
				const line = String(stdout ?? "")
					.split("\n")
					.find((l) => l.trim())
					?.trim();
				if (line) resolve(line);
				else notChecked("no output from pilead check --refs-only");
			});
		} catch (e) {
			notChecked(e instanceof Error ? e.message.split("\n")[0] : String(e));
		}
	});
}

// ---- what a wake says besides its refs line -----------------------------------------------------

/** Failed sends of one inbox line after which this runtime stops sending it (it stays in the inbox). */
export const WAKE_RETRY_CAP = 3;
/** The most a report may hold for `reportBrief` to read it. */
export const BRIEF_MAX_BYTES = 1024 * 1024;
/** A brief is cut to this many characters (lib/pilead/notify.py BRIEF_MAX). */
export const BRIEF_MAX_CHARS = 200;

/** What one wake is told: the refs line, and the facts of lib/pilead/wakefacts.py (null / false = not read). */
export interface WakeFacts {
	refs: string;
	/** `(diff: <n> files +<a>/-<d>)`, or null. */
	diff: string | null;
	/** true / false as `pilead check --wake` says it, or null when it could not be told. */
	landed: boolean | null;
	/** true only when the ledger proves the landing. */
	proven: boolean;
}

/**
 * The refs line and the facts of one report: the first stdout line of
 * `<package>/bin/pilead check <sid> --refs-only --wake --home <home>` is what `refsLine` would have resolved from
 * the same output (every `refs: not checked (…)` case included); the first line that starts `wake: ` and whose
 * rest parses as a JSON object gives `diff` (a non-empty text, else null), `landed` (true, false, else null) and
 * `proven` (true only for the JSON value true). No such line, or one that does not parse: `diff` and `landed`
 * are null and `proven` is false. ONE child; never throws and never rejects.
 */
export function wakeFacts(
	home: string,
	sid: string,
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = REFS_TIMEOUT_MS,
	signal?: AbortSignal,
): Promise<WakeFacts> {
	return new Promise((resolve) => {
		const notChecked = (why: string) => resolve({ refs: `refs: not checked (${why})`, diff: null, landed: null, proven: false });
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, ...(signal ? { signal } : {}) };
			execFile(bin, ["check", sid, "--refs-only", "--wake", "--home", home], o, (err, stdout) => {
				try {
					if (err) {
						const e = err as NodeJS.ErrnoException & { killed?: boolean };
						if (e.name === "AbortError" || signal?.aborted) return notChecked("aborted");
						if (e.killed) return notChecked(`timed out after ${timeoutMs} ms`);
						return notChecked(String(e.message ?? e).split("\n")[0]);
					}
					const lines = String(stdout ?? "").split("\n");
					const refs = lines.find((l) => l.trim())?.trim();
					if (!refs) return notChecked("no output from pilead check --refs-only");
					const facts: WakeFacts = { refs, diff: null, landed: null, proven: false };
					const wake = lines.map((l) => l.replace(/\r$/, "")).find((l) => l.startsWith("wake: "));
					if (wake !== undefined) {
						try {
							const d = JSON.parse(wake.slice("wake: ".length));
							if (d !== null && typeof d === "object" && !Array.isArray(d)) {
								if (typeof d.diff === "string" && d.diff !== "") facts.diff = d.diff;
								if (d.landed === true || d.landed === false) facts.landed = d.landed;
								facts.proven = d.proven === true;
							}
						} catch {}
					}
					resolve(facts);
				} catch (e) {
					notChecked(e instanceof Error ? e.message.split("\n")[0] : String(e));
				}
			});
		} catch (e) {
			notChecked(e instanceof Error ? e.message.split("\n")[0] : String(e));
		}
	});
}

// Python's `str.isspace` set, so a brief matches lib/pilead/notify.py `brief` character for character.
const PY_SPACE = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_SPACE_RUN = new RegExp(`[${PY_SPACE}]+`);
const PY_BLANK = new RegExp(`^[${PY_SPACE}]*$`);
// Python's `str.splitlines` boundaries.
const PY_LINE_END = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;

/**
 * The first line of `text` that is not blank, leading `#` and spaces removed, runs of white space made one
 * space, cut to BRIEF_MAX_CHARS characters (`brief` in lib/pilead/notify.py; tests/briefs.json is the table
 * both suites read). "" when there is no such line. Never throws.
 */
function briefOf(text: string): string {
	try {
		for (const line of text.split(PY_LINE_END)) {
			if (PY_BLANK.test(line)) continue;
			const words = line.replace(/^[# ]+/, "").split(PY_SPACE_RUN).filter((w) => w !== "");
			return Array.from(words.join(" ")).slice(0, BRIEF_MAX_CHARS).join("");
		}
	} catch {}
	return "";
}

/**
 * The brief of a report file, read ONLY when `execSid` is a usable session id and `report`, made absolute, lies
 * directly inside `<home>/sessions/<execSid>/` (no `..` in it, no link that leaves that directory) and is a
 * regular file of at most 1 MB; otherwise, on any error and for an empty brief, null. The text is data: it is
 * never run and never used as a path. Never throws.
 */
export function reportBrief(home: string, execSid: string, report: string): string | null {
	try {
		if (!validSessionSid(execSid) || typeof report !== "string" || report === "") return null;
		if (report.split(/[\\/]/).includes("..")) return null;
		const dir = path.join(home, "sessions", execSid);
		const abs = path.resolve(report);
		if (path.dirname(abs) !== path.resolve(dir)) return null;
		const real = fs.realpathSync(abs);
		if (path.dirname(real) !== fs.realpathSync(dir)) return null;
		const st = fs.statSync(real);
		if (!st.isFile() || st.size > BRIEF_MAX_BYTES) return null;
		const brief = briefOf(fs.readFileSync(real).toString("utf-8"));
		return brief === "" ? null : brief;
	} catch {
		return null;
	}
}

// ---- the end of the lead's turn ------------------------------------------------------------------

/** How long the `pilead turn-end` child may run before it is killed. */
export const TURN_END_TIMEOUT_MS = 30_000;
/** After a turn-end message was sent, no other is sent for this long (the guard against a loop). */
export const TURN_END_GAP_MS = 60_000;
/** What follows the lines of a turn-end message under the manual posture. */
export const TURN_END_MANUAL =
	"Open your reply with '🚦 [pi-lead] — review needed:', say these to the human, and wait for their word. Pass on every line that starts with 🟠 exactly as it is.";

/**
 * `auto_wake` from `<home>/config.json`: false only when the file parses as an object whose `auto_wake` is the
 * JSON value `false`; everything else (no file, a file that is not JSON, any other value) is true. Read on its
 * own: `loadConfig` and `PiLeadConfig` do not carry it. Never throws.
 */
export function readAutoWake(home: string): boolean {
	try {
		const user = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf-8"));
		return !(user !== null && typeof user === "object" && !Array.isArray(user) && user.auto_wake === false);
	} catch {
		return true;
	}
}

/**
 * The answer of `pilead turn-end --json` as `{lines, kinds}`: one JSON object whose `lines` is a list of texts.
 * Anything else — not JSON, not an object, `lines` missing or not a list of texts — has no lines. `kinds` keeps
 * the texts of its list, else is empty. Never throws.
 */
export function parseTurnEnd(text: unknown): { lines: string[]; kinds: string[] } {
	const none = { lines: [] as string[], kinds: [] as string[] };
	try {
		const d = JSON.parse(String(text ?? ""));
		if (d === null || typeof d !== "object" || Array.isArray(d)) return none;
		if (!Array.isArray(d.lines) || !d.lines.every((l: unknown) => typeof l === "string")) return none;
		const kinds = Array.isArray(d.kinds) ? d.kinds.filter((k: unknown) => typeof k === "string") : [];
		return { lines: d.lines as string[], kinds };
	} catch {
		return none;
	}
}

/**
 * The stdout of `<bin> <args>`, run asynchronously as a child with no shell. Never throws and never rejects: a
 * slow (killed at `timeoutMs`), failing or missing pilead gives "". Aborting `signal` kills the child.
 */
export function pileadText(
	args: string[],
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = TURN_END_TIMEOUT_MS,
	signal?: AbortSignal,
): Promise<string> {
	return new Promise((resolve) => {
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, ...(signal ? { signal } : {}) };
			execFile(bin, args, o, (err, stdout) => resolve(err ? "" : String(stdout ?? "")));
		} catch {
			resolve("");
		}
	});
}

/** How long the lead's `pilead takeover` child (after `/new` or `/fork` in its tab) may run before it is killed. */
export const TAKEOVER_TIMEOUT_MS = 10_000;

/**
 * Run `<bin> <args>` (no shell, `timeoutMs`, `extraEnv` added to the environment) and say how it went: `ok` when it
 * exited 0, its stdout, and the first line of what went wrong (stderr's first line, else the error's; "timed out
 * after N s" when it was killed at the limit). Never throws and never rejects.
 */
export function pileadRun(
	args: string[],
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = TAKEOVER_TIMEOUT_MS,
	extraEnv: Record<string, string> = {},
): Promise<{ ok: boolean; stdout: string; error: string }> {
	return new Promise((resolve) => {
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, env: { ...process.env, ...extraEnv } };
			execFile(bin, args, o, (err, stdout, stderr) => {
				const out = String(stdout ?? "");
				if (!err) return resolve({ ok: true, stdout: out, error: "" });
				const e = err as { killed?: boolean; signal?: unknown; message?: unknown };
				const first = (t: unknown) => String(t ?? "").split("\n").map((l) => l.trim()).find((l) => l !== "") ?? "";
				const error = e.killed ? `timed out after ${timeoutMs / 1000} s` : first(stderr) || first(e.message) || "failed";
				resolve({ ok: false, stdout: out, error: error.slice(0, 200) });
			});
		} catch (e) {
			resolve({ ok: false, stdout: "", error: errorLine(e) });
		}
	});
}

/** The session id in a pi session log's name, `<timestamp>_<session id>.jsonl` (the part after the first `_`), or
 *  undefined when the name has not that form. Never throws. */
export function sessionIdFromFile(file: unknown): string | undefined {
	try {
		if (typeof file !== "string" || !file) return undefined;
		const base = path.basename(file);
		if (!base.endsWith(".jsonl")) return undefined;
		const i = base.indexOf("_");
		if (i < 0) return undefined;
		const id = base.slice(i + 1, -".jsonl".length);
		return id || undefined;
	} catch {
		return undefined;
	}
}

// ---- tab names ----------------------------------------------------------------------------------

/** The longest tab name the extension sets (lib/pilead/tabs.py cuts a lead's project to 60). */
export const TAB_NAME_MAX = 80;

/** A name pi-lead may give a tab: a non-empty string of at most TAB_NAME_MAX characters with no control
 *  character. */
export function usableTabName(v: unknown): v is string {
	return typeof v === "string" && v.length > 0 && v.length <= TAB_NAME_MAX && !/[\u0000-\u001f\u007f-\u009f]/.test(v);
}

/**
 * The name a record asks its tab to have, or undefined: a lead's record (`leads/<sid>.json`) by its `label`; an
 * executor's `meta.json` (`withTitle`) by its `tab_title` when that is usable (a rename by `pilead close
 * --keep-tab` or a revive), else by its `label`. pi writes the terminal title from the session name
 * (`π - <name> - <directory>`), so the name is what pi-lead sets; nothing is typed into the tab. Never throws.
 */
export function recordTabName(rec: unknown, withTitle: boolean): string | undefined {
	try {
		if (rec === null || typeof rec !== "object" || Array.isArray(rec)) return undefined;
		const r = rec as { label?: unknown; tab_title?: unknown };
		if (withTitle && usableTabName(r.tab_title)) return r.tab_title;
		return usableTabName(r.label) ? r.label : undefined;
	} catch {
		return undefined;
	}
}

/** The forward note `<home>/handoffs/<leadSid>.json` when it names a usable successor: `{to, project}` (`project`
 *  "" when it has none), else undefined. Never throws. */
export function forwardNote(home: string, leadSid: string | undefined): { to: string; project: string } | undefined {
	try {
		if (!validLeadSid(leadSid)) return undefined;
		const d = JSON.parse(fs.readFileSync(path.join(home, "handoffs", `${leadSid}.json`), "utf-8"));
		if (d === null || typeof d !== "object" || Array.isArray(d) || !validLeadSid(d.to)) return undefined;
		return { to: d.to, project: typeof d.project === "string" ? d.project : "" };
	} catch {
		return undefined;
	}
}

// ---- the autonomous posture in the wake --------------------------------------------------------

/**
 * The five cases in which a lead stops and asks under either posture. Byte-identical to STOP_LIST in
 * lib/pilead/posture.py (tests/test_posture.py compares them).
 */
export const AUTONOMOUS_STOP_LIST =
	"a report with a risk flag, failing tests, or an UNVERIFIED claim that bears on correctness; core logic, ledgers, parity or golden tests, migrations, deploys; an action that cannot be undone or that leaves this machine (a push, a delete, a publish, a message to someone); work that is not in the approved plan; real ambiguity the packet and the plan do not settle";

/** What a wake carries after its first line: nothing for a manual lead, the autonomous instruction otherwise. */
export function wakeInstruction(autonomous: boolean): string {
	if (!autonomous) return "";
	return (
		"\n\nYou are in AUTONOMOUS MODE: announce, act, and record. Open your reply with '🚦 [pi-lead] — review needed:', say what reported, then take the next step that is clearly inside the approved plan, and announce each autonomous action together with what you would have asked. Stop and ask anyway for any of these: " +
		AUTONOMOUS_STOP_LIST +
		". Say which one stopped you. Committing an executor's work is permitted without asking only when pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <path> prints AUTO-COMMIT: CLEARED; pass the two attestation flags only when they are true. The verifier clears the automation; it never replaces the review of the diff."
	);
}

/**
 * The lead's posture from its record `file` (leads/<sid>.json), read now: true only when the file parses
 * as an object whose `autonomous` is the JSON value `true` (`autonomous_state` in lib/pilead/posture.py).
 * A missing or unreadable record, or any other value, is false. Never throws.
 */
function leadAutonomous(file: string | undefined): boolean {
	if (!file) return false;
	try {
		const rec = JSON.parse(fs.readFileSync(file, "utf-8"));
		return rec !== null && typeof rec === "object" && !Array.isArray(rec) && rec.autonomous === true;
	} catch {
		return false;
	}
}

// ---- queued packets ----------------------------------------------------------------------------

/** How long an executor's settle lets `pilead queue <sid> --deliver --trigger settle` run before killing it. */
export const SETTLE_DELIVER_TIMEOUT_MS = 30_000;

/**
 * Ask pilead to deliver executor `sid`'s next queued packet (`pilead send --when-idle`):
 * `<package>/bin/pilead queue <sid> --deliver --trigger settle --home <home>`, as a child process with
 * no shell. pilead decides whether the session has finished its packet; a delivered packet reaches the
 * session through its inbox, like every packet. Resolves when the child ends; never throws and never
 * rejects — a slow, failing, silent or missing pilead changes nothing here. Aborting `signal` kills the
 * child (SIGTERM).
 */
export function deliverQueued(
	home: string,
	sid: string,
	bin: string = path.join(PACKAGE_DIR, "bin", "pilead"),
	timeoutMs: number = SETTLE_DELIVER_TIMEOUT_MS,
	signal?: AbortSignal,
): Promise<void> {
	return new Promise((resolve) => {
		try {
			const o = { encoding: "utf-8" as const, timeout: timeoutMs, ...(signal ? { signal } : {}) };
			execFile(bin, ["queue", sid, "--deliver", "--trigger", "settle", "--home", home], o, () => resolve());
		} catch {
			resolve();
		}
	});
}

// ---- skill commands ----------------------------------------------------------------------------

/** The 36 skill commands, each also reachable as the lead-only extension command `/pilead:<name>`: relay 0.5.7's
 *  nineteen (its twenty skills less `route`) followed by pi-lead's own seventeen verbs. `/pilead:route` and
 *  `/pilead:status` are registered directly below, so 38 `/pilead:` commands in all; `pilead-route` is a skill
 *  file only (see lib/pilead/doctor.py SKILL_FILES). */
export const SKILL_NAMES = [
	"mode", "spawn", "send", "check", "list", "review", "verify", "close", "diff", "board",
	"auto", "tier", "plan", "handoff", "restart", "resume", "retire", "focus", "stop",
	"adopt", "auto-close", "close-predecessor", "doctor", "gate", "keep", "lineup", "lint", "notify", "prune", "queue",
	"refs", "report", "stats", "takeover", "tidy", "whoami",
] as const;

/** Package root (the directory holding `skills/`), resolved from this file so `-e` works with no skills loaded. */
const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** The `version` string of a package.json (default: this package's own), or null when it cannot be read or
 *  has none. Never throws. */
export function packageVersion(file: string = path.join(PACKAGE_DIR, "package.json")): string | null {
	try {
		const v = (JSON.parse(fs.readFileSync(file, "utf8")) as { version?: unknown })?.version;
		return typeof v === "string" ? v : null;
	} catch {
		return null;
	}
}

/** Split a SKILL.md into its frontmatter `description` and body (frontmatter stripped, trimmed like pi does). */
export function parseSkillFile(text: string): { description: string; body: string } {
	const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
	if (!m) return { description: "", body: text.trim() };
	const d = /^description:\s*(.*)$/m.exec(m[1]);
	let description = (d?.[1] ?? "").trim();
	if (/^(["']).*\1$/.test(description)) description = description.slice(1, -1);
	return { description, body: m[2].trim() };
}

/** The message pi itself sends for `/skill:<name> <args>`. */
export function skillBlock(name: string, file: string, body: string, args: string): string {
	const block = `<skill name="${name}" location="${file}">\nReferences are relative to ${path.dirname(file)}.\n\n${body}\n</skill>`;
	return args ? `${block}\n\n${args}` : block;
}

// ---- a pi that is not the session's own: quiet --------------------------------------------------

/** The environment variable that marks a pi-lead session's process: the factory sets it to this
 * process's id, so every process started from the session's shell (a second pi included) inherits it. */
export const HELD_BY_VAR = "PI_LEAD_HELD_BY";

/**
 * Whether pi's run mode (`ctx.mode`) makes a lead quiet: true for exactly "print" (`pi -p`) and "json";
 * false for "tui", "rpc", any other value and a missing one (a mode that cannot be read means "as today").
 */
export function quietMode(mode: unknown): boolean {
	return mode === "print" || mode === "json";
}

/**
 * Whether the marker value found in the environment names ANOTHER process: true only for a text of
 * decimal digits naming a whole number > 0 that is not `ownPid`. A missing or empty value, `ownPid`
 * itself, and anything that is not such a number mean "as today" (false).
 */
export function heldByAnother(value: unknown, ownPid: number): boolean {
	if (typeof value !== "string" || !/^[0-9]+$/.test(value)) return false;
	const n = Number(value);
	return Number.isSafeInteger(n) && n > 0 && n !== ownPid;
}

// ---- the role ----------------------------------------------------------------------------------

/** What a reviewer's refused tool call is told, whatever the tool. */
export const REVIEWER_REFUSAL = "pi-lead reviewer: this session only reads — it runs no command and changes no file.";
/** The tools a reviewer may call: pi's read-only built-ins. Every other name is refused. */
export const REVIEWER_TOOLS: readonly string[] = Object.freeze(["read", "grep", "find", "ls"]);

/**
 * The role PI_LEAD_ROLE names: exactly "executor" → executor, exactly "reviewer" → reviewer; any other value,
 * none, or an environment that cannot be read → lead (as before the reviewer role existed). A role that could
 * not be read never means "reviewer". Never throws.
 */
export function roleFrom(env: unknown): Role {
	let v: unknown;
	try {
		v = (env as Record<string, unknown> | null | undefined)?.PI_LEAD_ROLE;
	} catch {
		return "lead";
	}
	return v === "executor" ? "executor" : v === "reviewer" ? "reviewer" : "lead";
}

/**
 * The reviewer role: the marker set (not read), one tool_call handler that can only refuse, and nothing else —
 * no state under home read, watched or written, no message, no footer, no command, no session_shutdown work.
 */
function startReviewer(pi: ExtensionAPI, env: NodeJS.ProcessEnv | Record<string, string | undefined>, ownPid: number, home: string): PiLeadHandle {
	try {
		env[HELD_BY_VAR] = String(ownPid);
	} catch {}
	pi.on("tool_call", async (event) => {
		let name: unknown;
		try {
			name = (event as { toolName?: unknown }).toolName;
		} catch {}
		if (typeof name === "string" && REVIEWER_TOOLS.includes(name)) return undefined;
		return { block: true, reason: REVIEWER_REFUSAL };
	});
	return { role: "reviewer", home, drainInbox: () => 0, flush: () => Promise.resolve(), stop: () => {}, leadIsQuiet: () => true };
}

// ---- the extension -----------------------------------------------------------------------------

export function createPiLead(pi: ExtensionAPI, opts: PiLeadOptions = {}): PiLeadHandle {
	const env = opts.env ?? process.env;
	const now = opts.now ?? Date.now;
	const doWatch = opts.watch ?? true;
	const skillsDir = opts.skillsDir ?? path.join(PACKAGE_DIR, "skills");
	const retryMs = opts.retryMs ?? SEND_RETRY_MS;
	/** The refs line and the facts for one wake: never rejects, whatever the child or `refsCheck` does. An
	 *  injected `refsCheck` gives the refs line only, so no fact is read (a fact not read adds nothing). */
	const factsFor = (execSid: string, signal: AbortSignal): Promise<WakeFacts> => {
		const bare = (refs: string): WakeFacts => ({ refs, diff: null, landed: null, proven: false });
		const failed = (e: unknown) => bare(`refs: not checked (${e instanceof Error ? e.message.split("\n")[0] : String(e)})`);
		const check = opts.refsCheck;
		if (check) {
			return Promise.resolve()
				.then(() => check(home, execSid, signal))
				.then((line) => bare(line), failed);
		}
		return wakeFacts(home, execSid, opts.pileadBin, REFS_TIMEOUT_MS, signal).catch(failed);
	};
	/** Failed sends per inbox line, for this runtime's life (WAKE_RETRY_CAP), and the lines that reached the cap:
	 *  this runtime sends those no more (they stay in the inbox). */
	const sendFailures = new Map<string, number>();
	const cappedLines = new Set<string>();
	const lineKey = (inbox: string, line: string): string => `${inbox}\n${line}`;
	// Wakes are sent in claim order: each drain's batch waits for the batch claimed before it.
	let deliveries: Promise<void> = Promise.resolve();
	/** A claim file this instance made and has not finished with ("held"): it holds the lines of `wakes`,
	 *  the claimed wakes that are neither handed over nor put back yet. `dirty` = the file could not be
	 *  rewritten to match (retried at the next drain; held meanwhile, so it is never recovered as an orphan). */
	type Claim = { file: string; wakes: Wake[]; dirty: boolean };
	/** Wakes claimed from the inbox and neither sent nor put back yet, in claim order. `num` orders put-back
	 *  lines (change 3): one rising counter per instance; a line put back and claimed again keeps its number. */
	type Wake = {
		inbox: string;
		line: string;
		execSid: string;
		report: string;
		facts: Promise<WakeFacts>;
		abort: AbortController;
		claim: Claim;
		num: number;
	};
	const pending: Wake[] = [];
	const held = new Set<Claim>();
	let lineNum = 0;
	let recoveredNum = 0; // recovered lines sort ahead of every line this instance claimed
	/** Lines this instance put back in an inbox and has not claimed again, with their numbers. */
	let putBackOut: { inbox: string; line: string; num: number }[] = [];
	/** Executor: a claimed packet handed to pi and not yet recorded as a user message of the session. */
	const execHeld: { file: string; text: string }[] = [];
	/** Set by session_shutdown: from then on nothing is claimed, sent or written. session_start clears it. */
	let closed = false;
	let holdUntil = 0; // no claim before this (ms since epoch): a send just failed
	let retryTimer: ReturnType<typeof setTimeout> | undefined;
	/** config.json `auto_wake`, read at session_start (lead): false = claim nothing, send no wake, no turn-end message. */
	let autoWake = true;
	const role: Role = roleFrom(env);
	const home = path.resolve(expandHome(env.PI_LEAD_HOME || path.join(os.homedir(), ".pi-lead")));
	if (role === "reviewer") return startReviewer(pi, env, opts.pid ?? process.pid, home);

	// The marker: read what the environment holds, then claim it for this process (either role), so a pi
	// started from this session's shell sees our id. A pi that finds ANOTHER process's id there was
	// started from inside a pi-lead session: it does no lead (or executor) work, for the runtime's life.
	// The run mode is read at session_start: a print or json run of a lead is quiet too.
	const ownPid = opts.pid ?? process.pid;
	const heldFound = env[HELD_BY_VAR];
	const heldBy = heldByAnother(heldFound, ownPid) ? String(heldFound) : undefined;
	try {
		env[HELD_BY_VAR] = String(ownPid);
	} catch {}
	let quietRun: "print" | "json" | undefined; // lead only, from ctx.mode at session_start
	/** True when this process does no pi-lead work: every piece of lead (or executor) work asks this first. */
	const isQuiet = (): boolean => heldBy !== undefined || (role === "lead" && quietRun !== undefined);
	/** The pi-lead version this pi loaded, read once here (lead work: a quiet lead reads nothing); null when
	 *  package.json cannot be read or has no string `version`. */
	const extVersion: string | null = role === "lead" && !isQuiet() ? packageVersion(opts.packageJson) : null;
	/** The one notice a quiet process shows at session_start, or undefined. */
	const quietNotice = (): string | undefined => {
		const work = role === "lead" ? "lead" : "executor";
		if (heldBy !== undefined)
			return `pi-lead: this pi was started from inside a pi-lead session (process ${heldBy}), so it does no ${work} work: the ${work}'s inbox is left to the ${work}'s own session.`;
		if (role === "lead" && quietRun !== undefined)
			return `pi-lead: this is a ${quietRun} run, so it does no lead work: the lead's inbox is left to the lead's own session.`;
		return undefined;
	};
	let quietNoticed = false;
	/** The `/pilead:status` line: `lead work: …` or `executor work: …`. */
	const workLine = (): string => {
		const work = role === "lead" ? "lead" : "executor";
		if (heldBy !== undefined) return `${work} work: off (started from inside a pi-lead session, process ${heldBy})`;
		if (role === "lead" && quietRun !== undefined) return `${work} work: off (${quietRun} run)`;
		return `${work} work: on`;
	};

	// config.json, read once at session_start (and lazily if a handler somehow runs before it).
	let cfg: PiLeadConfig | undefined;
	const conf = (): PiLeadConfig => (cfg ??= loadConfig(home));
	const graceMs = (): number => conf().grace_seconds * 1000;

	// Our sid: PI_LEAD_SID, else pi's own session id (executors launch with --session-id <sid>,
	// and pi exports PI_SESSION_ID to bash, so `pilead lead-start` can register the same id).
	// The sid names files (leads/<sid>.* for a lead, sessions/<sid>/ for an executor), so either one
	// counts only when it is usable for the role (validLeadSid / validSessionSid); with neither, there is no sid.
	const validSid = role === "lead" ? validLeadSid : validSessionSid;
	const usable = (s: string | undefined): string | undefined => (validSid(s) ? s : undefined);
	const badEnvSid = !!env.PI_LEAD_SID && !validSid(env.PI_LEAD_SID);
	let sid: string | undefined = usable(env.PI_LEAD_SID || undefined);
	const sidFrom = (ctx?: ExtensionContext): string | undefined => {
		if (!sid && ctx) {
			try {
				sid = usable(ctx.sessionManager.getSessionId() || undefined);
			} catch {}
		}
		return sid;
	};
	let warnedSid = false;
	/** What session_start says about an id it could not use (once per session), or undefined. */
	const sidWarning = (): string | undefined => {
		if (role === "executor")
			return badEnvSid ? "pi-lead: PI_LEAD_SID is not a usable session id; using this session's own id" : undefined;
		if (badEnvSid)
			return sid
				? "pi-lead: PI_LEAD_SID is not a usable lead id; using this session's own id"
				: "pi-lead: this session has no usable lead id (PI_LEAD_SID and the session's own id are both refused); the edit gate is off";
		return sid ? undefined : "pi-lead: this session's id is not a usable lead id; the edit gate is off";
	};

	const sessionDir = (): string | undefined => (sid ? path.join(home, "sessions", sid) : undefined);
	const executorInbox = (): string | undefined => env.PI_LEAD_INBOX || (sid ? path.join(sessionDir()!, "inbox.md") : undefined);
	const leadJson = (): string | undefined => (sid ? path.join(home, "leads", `${sid}.json`) : undefined);

	// ---- claim files: orphans, and putting lines back -------------------------------------------------

	/** Claim files beside `inbox` that nobody will finish (change 2): not in `skip`, and made by this very
	 *  process (an earlier instance), by a process that is gone, or CLAIM_STALE_MS or more ago. Oldest
	 *  first (`<ms>`, then `<seq>`); a name that does not parse is never one. */
	const orphansBeside = (inbox: string, skip: Set<string>): { file: string; name: string }[] => {
		let names: string[];
		try {
			names = fs.readdirSync(path.dirname(inbox));
		} catch {
			return [];
		}
		const base = path.basename(inbox);
		const t = now();
		const found: { file: string; name: string; ms: number; seq: number }[] = [];
		for (const name of names) {
			const p = parseClaimName(name, base);
			if (!p) continue;
			const file = path.join(path.dirname(inbox), name);
			if (skip.has(file)) continue;
			if (p.pid === ownPid || t - p.ms >= CLAIM_STALE_MS || pidGone(p.pid)) found.push({ file, name, ms: p.ms, seq: p.seq });
		}
		return found.sort((a, b) => a.ms - b.ms || a.seq - b.seq).map(({ file, name }) => ({ file, name }));
	};

	/** The latest event handler's ctx, for a warning and the footer; dropped once pi says it is stale. */
	let lastCtx: ExtensionContext | undefined;
	const seen = (ctx: ExtensionContext | undefined): void => {
		if (ctx) lastCtx = ctx;
	};
	/** One warning through the latest ctx; a ctx that throws is dropped without a message. Never throws. */
	const warn = (text: string): void => {
		const ctx = lastCtx;
		if (!ctx) return;
		try {
			ctx.ui.notify(text, "warning");
		} catch {
			if (lastCtx === ctx) lastCtx = undefined;
		}
	};

	/** The latest failed send or failed put-back, for the health file. */
	let lastError: string | undefined;
	let lastErrorAt: number | undefined;

	/**
	 * Put wake lines back in `inbox` (change 3): re-claim what the inbox holds now, set apart the lines this
	 * instance put back earlier and has not claimed again, and append in ONE append — those lines together
	 * with `entries`, sorted by their numbers, then the rest of what the inbox held. true when the append
	 * succeeded. When the re-claim or the append throws (change 4) nothing is deleted — the claim made of the
	 * newer inbox content stays on disk for a later drain's recovery — the failure is ledgered, and outside
	 * shutdown the hold starts and the latest ctx gets a warning. With `skipHeld` (the inbox is a successor's,
	 * not the one the wakes came from) an entry whose line that inbox holds already is not written again.
	 * Never throws.
	 */
	const putBackLines = (inbox: string, entries: { line: string; num: number }[], atShutdown: boolean, skipHeld = false): boolean => {
		try {
			const newer = takeClaim(inbox, ownPid, now());
			const outstanding = putBackOut.filter((e) => e.inbox === inbox);
			const mine: { line: string; num: number }[] = [];
			const rest: string[] = [];
			const newerLines = newer ? newer.text.split("\n") : [];
			if (newerLines.length && newerLines[newerLines.length - 1] === "") newerLines.pop();
			if (skipHeld) entries = entries.filter((e) => !newerLines.includes(e.line));
			for (const l of newerLines) {
				const i = outstanding.findIndex((e) => e.line === l);
				if (i >= 0) mine.push(...outstanding.splice(i, 1));
				else rest.push(l);
			}
			const sorted = [...mine, ...entries].sort((a, b) => a.num - b.num);
			fs.appendFileSync(inbox, [...sorted.map((e) => e.line), ...rest].map((l) => `${l}\n`).join(""));
			if (newer) {
				try {
					fs.rmSync(newer.file, { force: true });
				} catch {}
			}
			// what this instance has put back in this inbox and not claimed again is now exactly `sorted`
			putBackOut = putBackOut.filter((e) => e.inbox !== inbox).concat(sorted.map((e) => ({ inbox, line: e.line, num: e.num })));
			return true;
		} catch (e) {
			const error = errorLine(e);
			lastError = error;
			lastErrorAt = now();
			appendLedger(home, "wake_putback_failed", { session_id: sid, wakes: entries.length, error }, now());
			if (!atShutdown) {
				holdUntil = now() + retryMs;
				armRetry(retryMs);
				warn(`pi-lead: ${entries.length} wake(s) could not be put back in ${inbox} (${error}) — kept on disk, retried at the next drain`);
			}
			return false;
		}
	};

	/** Rewrite a held claim file to hold only its wakes' lines (tmp + rename), or delete it when none is left.
	 *  A claim that could not be synced stays held (and dirty) until a later sync succeeds. Never throws. */
	const syncClaim = (c: Claim): void => {
		try {
			if (c.wakes.length === 0) {
				fs.rmSync(c.file, { force: true });
				held.delete(c);
				c.dirty = false;
				return;
			}
			const tmp = `${c.file}.tmp`;
			fs.writeFileSync(tmp, c.wakes.map((w) => `${w.line}\n`).join(""));
			fs.renameSync(tmp, c.file);
			c.dirty = false;
		} catch {
			c.dirty = true;
		}
	};

	/**
	 * Put claimed wakes back (one put-back per inbox, keeping their order by number). On success they leave
	 * `pending` and their claim files lose their lines; on failure the claim files stay on disk untouched and
	 * this instance stops holding them (a later drain recovers them as orphans), and the wakes leave `pending`
	 * too — the claim files on disk hold them from then on, so they are neither lost nor held twice.
	 */
	const putBackWakes = (ws: Wake[], atShutdown: boolean): void => {
		// the inbox is chosen now, not when the wake was taken: a successor's once a forward note names one
		const target = new Map<string, string>();
		for (const w of ws) if (!target.has(w.inbox)) target.set(w.inbox, putBackTarget(w.inbox));
		for (const inbox of new Set(ws.map((w) => target.get(w.inbox) as string))) {
			const group = ws.filter((w) => target.get(w.inbox) === inbox);
			const moved = group.some((w) => w.inbox !== inbox);
			const ok = putBackLines(inbox, group.map((w) => ({ line: w.line, num: w.num })), atShutdown, moved);
			const claims = new Set(group.map((w) => w.claim));
			for (const w of group) {
				const i = pending.indexOf(w);
				if (i >= 0) pending.splice(i, 1);
				if (ok) w.claim.wakes.splice(w.claim.wakes.indexOf(w), 1);
			}
			for (const c of claims) {
				if (ok) syncClaim(c);
				else held.delete(c);
			}
		}
	};

	/**
	 * Where a wake taken from `from` goes back: the inbox of the lead `resolveOwner`'s walk ends on, started from this
	 * session's own sid — its own inbox while it is registered, its successor's once a forward note names one. When
	 * the walk ends on no registered lead, `from`. Synchronous (session_shutdown uses it). Never throws.
	 */
	const putBackTarget = (from: string): string => {
		try {
			if (role !== "lead" || !sid || !validLeadSid(sid)) return from;
			const end = resolveOwner(home, "", sid); // no executor id: the walk starts at this lead's own id
			if (end && isRegisteredLead(home, end)) return leadInboxPath(home, end) ?? from;
		} catch {}
		return from;
	};

	/** True when this lead session has a usable id and its record leads/<sid>.json does not exist. */
	const ownRecordGone = (): boolean => role === "lead" && !!sid && validLeadSid(sid) && !isRegisteredLead(home, sid);
	let exLeadNamed = false;
	/**
	 * The drain of a session that is no longer registered: it takes nothing from any inbox. The wakes it holds
	 * stop waiting for their refs lines and go back (`putBackWakes`: to the successor's inbox when a forward note
	 * names one). Once per runtime, when a forward note names a successor, the session is named `[ex-Lead]
	 * <project>` and the human gets one notice. Never throws.
	 */
	const stepAside = (): void => {
		try {
			const left = pending.slice();
			for (const w of left) w.abort.abort();
			if (left.length > 0) putBackWakes(left, false);
			if (exLeadNamed) return;
			const note = forwardNote(home, sid);
			if (!note) return;
			exLeadNamed = true;
			try {
				pi.setSessionName(`[ex-Lead] ${note.project || note.to}`);
			} catch {}
			const ctx = lastCtx;
			try {
				ctx?.ui.notify(`pi-lead: this session handed its lead role to ${note.to}; it is no longer a lead`, "info");
			} catch {}
		} catch {}
	};

	/** Change 2: before the lead's inbox is claimed, put the `reported` lines of orphaned claim files back in
	 *  it, ahead of what it holds; only after that append succeeded is each orphan deleted (with its `.tmp`)
	 *  and one `wake_recovered` ledgered. A claim file that cannot be read is left where it is. */
	const recoverLead = (inbox: string): void => {
		const orphans = orphansBeside(inbox, new Set([...held].map((c) => c.file)));
		const read: { file: string; name: string; lines: string[] }[] = [];
		for (const o of orphans) {
			let text: string;
			try {
				text = fs.readFileSync(o.file, "utf-8");
			} catch {
				continue;
			}
			const lines = text.split("\n").map((l) => l.trim()).filter((l) => /^reported\s+\S+\s+.+$/.test(l));
			read.push({ ...o, lines });
		}
		if (read.length === 0) return;
		const entries = read.flatMap((o) => o.lines.map((line) => ({ line, num: -1e12 + recoveredNum++ })));
		if (entries.length > 0 && !putBackLines(inbox, entries, false)) {
			writeHealth();
			return;
		}
		for (const o of read) {
			for (const f of [o.file, `${o.file}.tmp`]) {
				try {
					fs.rmSync(f, { force: true });
				} catch {}
			}
			appendLedger(home, "wake_recovered", { session_id: sid, claim: o.name, wakes: o.lines.length }, now());
		}
	};

	/** Change 7: the executor's orphaned claims, put back ahead of what its inbox holds in one append, then
	 *  deleted with one `packet_recovered` each; a failed put-back deletes nothing (`packet_putback_failed`). */
	const recoverExecutor = (inbox: string): void => {
		const orphans = orphansBeside(inbox, new Set(execHeld.map((h) => h.file)));
		const read: { file: string; name: string; text: string }[] = [];
		for (const o of orphans) {
			try {
				read.push({ ...o, text: fs.readFileSync(o.file, "utf-8") });
			} catch {}
		}
		if (read.length === 0) return;
		if (!putBackPackets(inbox, read.map((o) => o.text))) return;
		for (const o of read) {
			try {
				fs.rmSync(o.file, { force: true });
			} catch {}
			appendLedger(home, "packet_recovered", { session_id: sid, claim: o.name }, now());
		}
	};

	/** Executor packets back in `inbox`, ahead of what it holds, in ONE append; true when it succeeded, else
	 *  `packet_putback_failed` is ledgered and the claim of the newer inbox content stays on disk. Never throws. */
	const putBackPackets = (inbox: string, texts: string[]): boolean => {
		try {
			const newer = takeClaim(inbox, ownPid, now());
			fs.appendFileSync(inbox, texts.map((t) => (t.endsWith("\n") ? t : `${t}\n`)).join("") + (newer ? newer.text : ""));
			if (newer) {
				try {
					fs.rmSync(newer.file, { force: true });
				} catch {}
			}
			return true;
		} catch (e) {
			appendLedger(home, "packet_putback_failed", { session_id: sid, error: errorLine(e) }, now());
			return false;
		}
	};

	const drainExecutor = (): number => {
		if (closed) return 0;
		const inbox = executorInbox();
		if (!inbox) return 0;
		recoverExecutor(inbox);
		// The claim stays on disk until pi has recorded the packet as a user message (message_end below),
		// so a process killed in between leaves it for the next drain's recovery.
		const claim = takeClaim(inbox, ownPid, now());
		if (!claim) return 0;
		const packet = claim.text.trim();
		if (!packet) {
			try {
				fs.rmSync(claim.file, { force: true });
			} catch {}
			return 0;
		}
		const h = { file: claim.file, text: packet };
		execHeld.push(h);
		try {
			// followUp: if a run is still streaming, queue it after the current work instead of throwing
			// ("Agent is already processing") and losing an already-claimed packet.
			pi.sendUserMessage(packet, { deliverAs: "followUp" });
		} catch {
			execHeld.splice(execHeld.indexOf(h), 1); // not held any more: the next drain recovers it from disk
			return 0;
		}
		return 1;
	};

	/** True when the inbox holds lines and every one is a `reported` line this runtime gave up sending. Claiming
	 *  and putting them back would only make a watch event each time (a loop); reading them changes nothing. */
	const onlyCapped = (inbox: string): boolean => {
		try {
			const lines = fs
				.readFileSync(inbox, "utf-8")
				.split("\n")
				.map((l) => l.trim())
				.filter((l) => l !== "");
			return lines.length > 0 && lines.every((l) => /^reported\s+\S+\s+.+$/.test(l) && cappedLines.has(lineKey(inbox, l)));
		} catch {
			return false;
		}
	};

	const drainLead = (): number => {
		if (!closed && ownRecordGone()) {
			stepAside(); // a session that is not registered takes nothing and sends nothing
			return 0;
		}
		const inbox = leadInboxPath(home, sid);
		if (!inbox) return 0;
		if (doWatch) watcher.watchDir(path.dirname(inbox)); // inbox may live outside leads/
		// The claim is synchronous (a rename), so a watcher event or poll that fires while this batch
		// still waits for its refs lines finds the inbox empty: nothing is delivered twice. The lines
		// are then fetched concurrently and the wakes sent in inbox order. The claim file stays on disk
		// until each of its wakes was handed over or put back, so a process killed meanwhile leaves it for
		// the next drain's recovery (a wake may then arrive twice; it is never lost).
		if (closed || now() < holdUntil) return 0;
		for (const c of held) if (c.dirty) syncClaim(c);
		recoverLead(inbox);
		if (now() < holdUntil) return 0; // the recovery's put-back failed: the hold has started
		if (!autoWake) return 0; // auto_wake is off: the reports stay in the inbox, where list and check show them
		if (cappedLines.size > 0 && onlyCapped(inbox)) return 0; // nothing to send: leave the inbox exactly as it is
		const claim = takeClaim(inbox, ownPid, now());
		if (!claim) return 0;
		const c: Claim = { file: claim.file, wakes: [], dirty: false };
		const wakes: Wake[] = [];
		const capped: Wake[] = []; // lines this runtime gave up sending: put back untouched, no child, no hold
		for (const raw of claim.text.split("\n")) {
			// Only `reported <exec_sid> <report path>` lines are defined; anything else is consumed.
			const line = raw.trim();
			const m = /^reported\s+(\S+)\s+(.+)$/.exec(line);
			if (!m) continue;
			const [, execSid, report] = m;
			const abort = new AbortController();
			const i = putBackOut.findIndex((e) => e.inbox === inbox && e.line === line);
			const num = i >= 0 ? putBackOut.splice(i, 1)[0].num : ++lineNum;
			const isCapped = cappedLines.has(lineKey(inbox, line));
			const w: Wake = {
				inbox,
				line,
				execSid,
				report,
				facts: isCapped ? Promise.resolve({ refs: "", diff: null, landed: null, proven: false }) : factsFor(execSid, abort.signal),
				abort,
				claim: c,
				num,
			};
			(isCapped ? capped : wakes).push(w);
		}
		if (wakes.length === 0 && capped.length === 0) {
			try {
				fs.rmSync(claim.file, { force: true }); // nothing in it to deliver
			} catch {}
			return 0;
		}
		c.wakes.push(...wakes, ...capped);
		held.add(c);
		pending.push(...wakes, ...capped);
		if (capped.length > 0) putBackWakes(capped, false);
		if (wakes.length === 0) return 0;
		deliveries = deliveries.then(async () => {
			const failed: Wake[] = [];
			for (const w of wakes) {
				const facts = await w.facts;
				// session_shutdown has already put back every wake still pending (the failed ones included)
				if (closed) return;
				if (!pending.includes(w)) continue; // put back meanwhile: this session stopped being a lead
				if (ownRecordGone()) {
					stepAside(); // never sent by a session that is not registered
					continue;
				}
				if (facts.landed === true && facts.proven === true) {
					skippedLanded(w);
					continue;
				}
				const error = sendWake(w, facts);
				if (error === undefined) {
					sendFailures.delete(lineKey(w.inbox, w.line));
					handedOver(w);
					continue;
				}
				// one failed send never stops the wakes queued after it; the failed ones go back together
				failed.push(w);
				lastError = error;
				lastErrorAt = now();
				if (failed.length === 1) {
					holdUntil = now() + retryMs;
					armRetry(retryMs);
				}
				countFailure(w, error);
			}
			if (failed.length > 0) putBackWakes(failed, false);
			writeHealth();
		});
		return wakes.length;
	};

	/** A wake whose work is proven committed already: no message and no banner. The line leaves the inbox as a
	 *  delivered one does, after `wake_skipped_landed` is ledgered; `wake_delivered` is not written for it. */
	const skippedLanded = (w: Wake): void => {
		appendLedger(
			home,
			"wake_skipped_landed",
			{ session_id: sid, executor: w.execSid, packet: reportPacket(w.report), report: w.report, reason: "landed", proven: true },
			now(),
		);
		const i = pending.indexOf(w);
		if (i >= 0) pending.splice(i, 1);
		w.claim.wakes.splice(w.claim.wakes.indexOf(w), 1);
		syncClaim(w.claim);
	};

	/** One more failed send of `w`'s line. At WAKE_RETRY_CAP the line is capped for this runtime's life: the
	 *  ledger gets `wake_retry_capped` and the latest ctx one warning; the line itself stays in the inbox. */
	const countFailure = (w: Wake, error: string): void => {
		const key = lineKey(w.inbox, w.line);
		const attempts = (sendFailures.get(key) ?? 0) + 1;
		sendFailures.set(key, attempts);
		if (attempts < WAKE_RETRY_CAP || cappedLines.has(key)) return;
		cappedLines.add(key);
		appendLedger(home, "wake_retry_capped", { session_id: sid, executor: w.execSid, report: w.report, attempts, error }, now());
		warn(
			`pi-lead: the wake for ${w.execSid} could not be handed over after ${WAKE_RETRY_CAP} tries (${error}) — it stays in the inbox; pilead check ${w.execSid} shows the report`,
		);
	};

	/** A wake was handed over: count it, ledger `wake_delivered` BEFORE its claim file loses the line (a crash
	 *  in between can give a second wake, never a lost one), then refresh the footer. */
	const handedOver = (w: Wake): void => {
		delivered++;
		lastDelivered = now();
		appendLedger(home, "wake_delivered", { session_id: sid, executor: w.execSid, packet: reportPacket(w.report), report: w.report }, now());
		const i = pending.indexOf(w);
		if (i >= 0) pending.splice(i, 1);
		w.claim.wakes.splice(w.claim.wakes.indexOf(w), 1);
		syncClaim(w.claim);
		refreshStatus();
		const packet = reportPacket(w.report);
		if (sid && packet !== null) askBanner(["notify", sid, "--executor", w.execSid, "--packet", String(packet), "--home", home]);
	};

	/**
	 * One timer that drains once the hold after a failed send is over (only when watching: the watcher
	 * would otherwise see the put-back entry again only on the next inbox write or poll). A timer that
	 * fires before `holdUntil` — timers may fire a little early, and the clock is injectable — re-arms
	 * for the time left instead of draining nothing. Inert once session_shutdown has run.
	 */
	const armRetry = (delay: number): void => {
		clearTimeout(retryTimer);
		retryTimer = undefined;
		if (!doWatch || closed) return;
		retryTimer = setTimeout(() => {
			retryTimer = undefined;
			if (closed) return;
			const left = holdUntil - now();
			if (left > 0) armRetry(left);
			else drainInbox();
		}, Math.max(1, delay));
		retryTimer.unref?.();
	};

	/** Send one wake; undefined when `sendMessage` returned without throwing, else the error's first line. */
	const sendWake = (w: Wake, facts: WakeFacts): string | undefined => {
		try {
			const on = leadAutonomous(leadJson()); // the posture now, not when the wake was claimed
			let content = `🚦 [pi-lead] executor ${w.execSid} reported: ${w.report} — ${facts.refs}`;
			const details: Record<string, unknown> = { sid: w.execSid, report: w.report, refs: facts.refs };
			if (facts.diff !== null) {
				content += ` ${facts.diff}`;
				details.diff = facts.diff;
			}
			const brief = reportBrief(home, w.execSid, w.report);
			if (brief !== null) {
				content += `\n       ${brief}`;
				details.brief = brief;
			}
			if (facts.landed === true && facts.proven === false) {
				content += "\n       its claimed files are clean at HEAD — it may have been committed already";
				details.landed = "unproven";
			}
			if (on) details.autonomous = true;
			pi.sendMessage({ customType: "pi-lead", content: `${content}${wakeInstruction(on)}`, display: true, details }, { triggerTurn: true });
			return undefined;
		} catch (e) {
			return errorLine(e);
		}
	};

	// ---- the end of the lead's turn ------------------------------------------------------------------

	/** The running `pilead turn-end` child (at most one) and when the last turn-end message was sent. */
	let turnEndRun: AbortController | undefined;
	let turnEndPending: Promise<void> = Promise.resolve();
	let lastTurnEndAt: number | undefined;
	const turnEndChild =
		opts.turnEnd ?? ((args: string[], signal: AbortSignal) => pileadText(args, opts.pileadBin, TURN_END_TIMEOUT_MS, signal));

	/**
	 * When pi has settled: ask `pilead turn-end` what the lead should hear and send ONE message with it. Only for a
	 * registered lead, with `auto_wake` on, before session_shutdown. The child is the default only when `ctx.hasUI`
	 * and `ctx.mode` is "tui" or "rpc" (a `turnEnd` option is used whatever the mode), with `--banner` in "tui".
	 * A second message inside TURN_END_GAP_MS is ledgered (`turn_end_suppressed`), not sent; a send that throws is
	 * ledgered (`turn_end_failed`). Not awaited by the handler; `flush()` waits for it. Never throws.
	 */
	const askTurnEnd = (ctx: ExtensionContext): void => {
		try {
			// SHORTCUT: a settle that comes while a turn-end child runs is skipped, not queued (nothing is stamped by a
			// skipped run, so the next settle still reports); fine while the child takes ~1 s; queue one re-run like
			// `statusAgain` if children ever run long enough for settles to overlap in practice.
			if (closed || isQuiet() || !autoWake || turnEndRun) return;
			const leadSid = sid;
			if (role !== "lead" || !leadSid || !validLeadSid(leadSid) || !isRegisteredLead(home, leadSid)) return;
			let mode: unknown;
			let hasUI: unknown;
			try {
				mode = (ctx as { mode?: unknown }).mode;
				hasUI = ctx.hasUI;
			} catch {}
			if (!opts.turnEnd && !(hasUI === true && (mode === "tui" || mode === "rpc"))) return;
			const args = ["turn-end", "--session", leadSid];
			if (typeof ctx.cwd === "string" && ctx.cwd) args.push("--cwd", ctx.cwd);
			args.push("--json", "--home", home);
			if (mode === "tui") args.push("--banner");
			const abort = new AbortController();
			turnEndRun = abort;
			turnEndPending = Promise.resolve()
				.then(() => turnEndChild(args, abort.signal))
				.catch(() => "")
				.then((answer) => {
					if (turnEndRun === abort) turnEndRun = undefined;
					if (closed || abort.signal.aborted) return;
					const { lines, kinds } = parseTurnEnd(answer);
					if (lines.length === 0) return;
					const t = now();
					if (lastTurnEndAt !== undefined && t - lastTurnEndAt < TURN_END_GAP_MS) {
						appendLedger(home, "turn_end_suppressed", { session_id: leadSid, lines }, t);
						return;
					}
					const tail = leadAutonomous(leadJson()) ? wakeInstruction(true) : `\n\n${TURN_END_MANUAL}`;
					try {
						pi.sendMessage(
							{
								customType: "pi-lead",
								content: `🚦 [pi-lead] — review needed:\n${lines.join("\n")}${tail}`,
								display: true,
								details: { kind: "turn-end", kinds, lines },
							},
							{ triggerTurn: true },
						);
						lastTurnEndAt = t;
					} catch (e) {
						appendLedger(home, "turn_end_failed", { session_id: leadSid, lines, error: errorLine(e) }, t);
					}
				});
		} catch {
			turnEndRun = undefined;
		}
	};

	/**
	 * session_shutdown (lead): send nothing. Stop waiting for refs lines, kill the children still
	 * fetching them, and put every pending wake (claimed, not yet handed to `sendMessage`) back in its
	 * inbox, ordered by number (change 3). Synchronous file calls only, so it is complete when the handler
	 * returns whether or not pi awaits it; afterwards a late refs answer, the retry timer and the poll are
	 * inert. A failed put-back is ledgered and leaves the claim files on disk; it shows nothing.
	 */
	const shutdownWakes = (): void => {
		closed = true;
		clearTimeout(retryTimer);
		retryTimer = undefined;
		turnEndRun?.abort();
		const left = pending.slice();
		for (const w of left) w.abort.abort();
		if (left.length > 0) putBackWakes(left, true);
		for (const c of held) if (c.dirty) syncClaim(c);
		held.clear();
	};

	/** session_shutdown (executor): a held packet that pi has not recorded yet goes back ahead of the inbox
	 *  (synchronous calls), then its claim is deleted; a failed put-back leaves it on disk for recovery. */
	const shutdownPackets = (): void => {
		const left = execHeld.splice(0);
		const inbox = executorInbox();
		if (left.length === 0 || !inbox) return;
		if (!putBackPackets(inbox, left.map((h) => h.text))) return;
		for (const h of left) {
			try {
				fs.rmSync(h.file, { force: true });
			} catch {}
		}
	};

	// ---- the health file (registered lead) and the footer's status line (both roles) --------------------

	let startedAt: number | undefined;
	let delivered = 0;
	let lastDelivered: number | undefined;
	let beatTimer: ReturnType<typeof setTimeout> | undefined;

	/**
	 * wake/<sid>/health.json for a registered lead whose id is usable (lib/pilead/wakehealth.py reads it):
	 * written atomically; `ended` set only by the last write at session_shutdown. A write that fails is
	 * ignored. Never throws, never delays a wake.
	 */
	const writeHealth = (ended?: number): void => {
		try {
			if (role !== "lead" || isQuiet() || !sid || !validLeadSid(sid) || !isRegisteredLead(home, sid)) return;
			const at = (ms: number | undefined) => (ms === undefined ? null : isoSeconds(ms));
			const health = {
				sid,
				pid: ownPid,
				started: at(startedAt),
				beat: at(now()),
				watching: watcher.watching(),
				poll_seconds: watcher.pollMs / 1000,
				delivered,
				last_delivered: at(lastDelivered),
				pending: pending.length,
				last_error: lastError ?? null,
				last_error_at: at(lastErrorAt),
				ended: at(ended),
				auto_wake: autoWake,
				version: extVersion,
			};
			writeAtomic(path.join(home, "wake", sid, "health.json"), `${JSON.stringify(health)}\n`);
		} catch {}
	};

	/** The beat: one timer, re-armed after each firing, that never keeps the process alive. Started with the
	 *  watchers; cleared by stop() and at session_shutdown. It rewrites the health file and the footer. */
	const armBeat = (): void => {
		clearTimeout(beatTimer);
		beatTimer = setTimeout(() => {
			beatTimer = undefined;
			if (closed) return;
			writeHealth();
			syncTabName();
			refreshStatus();
			armBeat();
		}, HEALTH_BEAT_MS);
		beatTimer.unref?.();
	};
	const stopBeat = (): void => {
		clearTimeout(beatTimer);
		beatTimer = undefined;
	};

	const statusLine =
		opts.statusLine ??
		((h: string, s: string, args: string[], signal: AbortSignal) => statusLineText(h, s, args, opts.pileadBin, STATUS_LINE_TIMEOUT_MS, signal));
	/** The footer text set last (undefined = none), the running child, and whether a refresh waits for it. */
	let statusShown: string | undefined;
	let statusRun: AbortController | undefined;
	let statusAgain = false;

	/**
	 * Refresh the footer's status line from `pilead status <sid> --statusline`, through the latest event's
	 * ctx, only when that ctx has a UI with `setStatus`. One child at a time: a refresh asked while one runs
	 * runs once more when it has ended. The text is set only when it changed; an empty answer clears it. A
	 * ctx that throws is dropped. Nothing is started once session_shutdown has run. Never throws.
	 */
	const refreshStatus = (): void => {
		try {
			if (closed || isQuiet() || !sid) return;
			const ctx = lastCtx;
			if (!ctx) return;
			let args: string[] = [];
			try {
				if (!ctx.hasUI || typeof ctx.ui?.setStatus !== "function") return;
				if (role === "lead") {
					const u = typeof ctx.getContextUsage === "function" ? ctx.getContextUsage() : undefined;
					if (u && Number.isSafeInteger(u.tokens) && (u.tokens as number) >= 0) args.push("--ctx-tokens", String(u.tokens));
					if (u && Number.isSafeInteger(u.contextWindow) && u.contextWindow > 0) args.push("--ctx-window", String(u.contextWindow));
				}
			} catch {
				if (lastCtx === ctx) lastCtx = undefined;
				return;
			}
			if (statusRun) {
				statusAgain = true;
				return;
			}
			const run = new AbortController();
			statusRun = run;
			const forSid = sid;
			void Promise.resolve()
				.then(() => statusLine(home, forSid, args, run.signal))
				.catch(() => "")
				.then((answer) => {
					if (statusRun === run) statusRun = undefined;
					if (closed || run.signal.aborted) return;
					showStatus(String(answer ?? "").split("\n")[0].trim() || undefined);
					if (statusAgain) {
						statusAgain = false;
						refreshStatus();
					}
				});
		} catch {}
	};
	/** Set the footer text through the latest ctx when it changed; a ctx that throws is dropped. */
	const showStatus = (text: string | undefined): void => {
		if (text === statusShown) return;
		const ctx = lastCtx;
		if (!ctx) return;
		try {
			ctx.ui.setStatus("pi-lead", text);
			statusShown = text;
		} catch {
			if (lastCtx === ctx) lastCtx = undefined;
		}
	};

	const bannerBin = opts.pileadBin ?? path.join(PACKAGE_DIR, "bin", "pilead");
	/**
	 * Post the desktop banner for a report by running `pilead <args>`, only when the latest event's ctx is the
	 * terminal UI (`ctx.mode` "tui"): a banner in a headless run is noise. Not awaited; an error is swallowed.
	 * `opts.banner` replaces the child in tests. Never throws.
	 */
	const askBanner = (args: string[]): void => {
		try {
			let mode: unknown;
			try {
				mode = (lastCtx as { mode?: unknown } | undefined)?.mode;
			} catch {}
			if (mode !== "tui") return;
			if (opts.banner) {
				void Promise.resolve(opts.banner(args)).catch(() => {});
				return;
			}
			execFile(bannerBin, args, { timeout: BANNER_TIMEOUT_MS }, () => {});
		} catch {}
	};

	/** The name this runtime set last with setSessionName (undefined = none yet). */
	let tabNamed: string | undefined;
	/**
	 * Name this session's tab from its own record (recordTabName): a registered lead from leads/<sid>.json, an
	 * executor from sessions/<sid>/meta.json. setSessionName is called only when the name differs from the one
	 * set last; a call that throws is tried again next time. A quiet process, a lead that is not registered (its
	 * `[ex-Lead]` name is stepAside's), or a record that cannot be read sets nothing. Runs at every drain (an
	 * executor's poll tick is a drain) and, for a lead, at every health beat. Never throws, never starts a turn.
	 */
	const syncTabName = (): void => {
		try {
			if (closed || isQuiet() || !sid || !validSid(sid)) return;
			let file: string;
			if (role === "lead") {
				if (!isRegisteredLead(home, sid)) return;
				file = path.join(home, "leads", `${sid}.json`);
			} else file = path.join(home, "sessions", sid, "meta.json");
			let rec: unknown;
			try {
				rec = JSON.parse(fs.readFileSync(file, "utf-8"));
			} catch {
				return;
			}
			if (role === "lead" && (rec as { no_rename?: unknown } | null)?.no_rename === true) return; // lead-start --no-rename
			const name = recordTabName(rec, role === "executor");
			if (name === undefined || name === tabNamed) return;
			try {
				pi.setSessionName(name);
				tabNamed = name;
			} catch {}
		} catch {}
	};

	const drainInbox = (): number => {
		try {
			if (isQuiet()) return 0; // a quiet process claims nothing and watches nothing
			syncTabName();
			return role === "executor" ? drainExecutor() : drainLead();
		} catch {
			return 0;
		}
	};

	const watcher = new InboxWatcher(drainInbox);

	/** The running `pilead takeover` child after `/new` or `/fork` (flush() waits for it). */
	let takeoverPending: Promise<void> = Promise.resolve();
	/**
	 * session_start in the lead role with `reason` `new` or `fork` (pi replaced the session inside this process, and
	 * the tab now has a new session id): when the previous session (from `previousSessionFile`) is a usable lead id
	 * with a record and this one has none, run `pilead takeover <previous> --as <new> --force` (`--force`: pi itself
	 * named the previous session of this process; TAKEOVER_TIMEOUT_MS), show one notice with the result, then drain.
	 * With `reason` `resume` nothing is moved: one notice when the session left is registered and this one is not.
	 * Nothing when `$PI_LEAD_SID` is set (the sid does not change), for a quiet lead, or after session_shutdown.
	 * Never throws.
	 */
	const leadSessionSwitch = (event: unknown, ctx: ExtensionContext): void => {
		try {
			if (role !== "lead" || isQuiet() || closed || env.PI_LEAD_SID) return;
			const e = (event ?? {}) as { reason?: unknown; previousSessionFile?: unknown };
			const reason = e.reason;
			if (reason !== "new" && reason !== "fork" && reason !== "resume") return;
			const prev = sessionIdFromFile(e.previousSessionFile);
			if (!prev || !validLeadSid(prev)) return;
			const cur = sidFrom(ctx);
			if (!cur || cur === prev || !isRegisteredLead(home, prev) || isRegisteredLead(home, cur)) return;
			const notice = (text: string, type: "info" | "warning"): void => {
				try {
					ctx.ui.notify(text, type);
				} catch {}
			};
			if (reason === "resume") {
				notice(`pi-lead: the session you left (${prev}) is still the registered lead; pilead takeover ${prev} makes this one the lead`, "info");
				return;
			}
			const args = ["takeover", prev, "--as", cur, "--force"];
			takeoverPending = pileadRun(args, opts.pileadBin, opts.takeoverTimeoutMs ?? TAKEOVER_TIMEOUT_MS, { PI_LEAD_HOME: home }).then((r) => {
				if (closed) return;
				if (r.ok) {
					const n = r.stdout.split("\n").filter((l) => l.startsWith("adopted ")).length;
					notice(`pi-lead: lead role moved from ${prev} to this session (${n} executor(s))`, "info");
					drainInbox();
				} else {
					notice(`pi-lead: lead role NOT moved: ${r.error}`, "warning");
				}
			});
		} catch {}
	};

	pi.on("session_start", async (event, ctx) => {
		cfg = loadConfig(home);
		autoWake = readAutoWake(home);
		sidFrom(ctx);
		const warning = sidWarning();
		if (warning && !warnedSid) {
			warnedSid = true;
			ctx.ui.notify(warning, "warning");
		}
		if (role === "lead" && quietRun === undefined) {
			let mode: unknown;
			try {
				mode = ctx.mode;
			} catch {}
			if (quietMode(mode)) quietRun = mode as "print" | "json";
		}
		const notice = quietNotice();
		if (notice && !quietNoticed) {
			quietNoticed = true;
			ctx.ui.notify(notice, "info");
		}
		if (isQuiet()) return; // no inbox created, watched, polled or drained
		// An instance that was shut down and gets session_start again (change 6) claims and delivers again.
		closed = false;
		holdUntil = 0;
		sendFailures.clear(); // a new session tries every line again
		cappedLines.clear();
		clearTimeout(retryTimer);
		retryTimer = undefined;
		seen(ctx);
		startedAt = now();
		if (role === "executor") {
			const inbox = executorInbox();
			if (inbox) {
				ensureFile(inbox);
				if (doWatch) watcher.watchDir(path.dirname(inbox));
			}
		} else if (sid && doWatch) {
			watcher.watchDir(path.join(home, "leads")); // leads/<sid>.json may appear later
		}
		if (doWatch) {
			watcher.poll(opts.pollMs ?? conf().poll_interval * 1000);
			armBeat();
		}
		drainInbox(); // anything queued before we started
		writeHealth();
		refreshStatus();
		leadSessionSwitch(event, ctx); // /new or /fork in a lead's tab: the lead role follows the tab
	});

	pi.on("session_shutdown", async (event, ctx) => {
		watcher.stop();
		stopBeat();
		if (isQuiet()) return; // nothing was claimed, so nothing is put back or written
		if (role === "lead") shutdownWakes();
		if (role === "executor") {
			closed = true;
			shutdownPackets();
			stopQueuedDelivery();
			stopEscalating();
		}
		writeHealth(now()); // the last write: `ended` set
		// the footer: the child stopped, the text cleared, nothing started afterwards (closed)
		statusRun?.abort();
		statusRun = undefined;
		statusAgain = false;
		if (statusShown !== undefined) {
			try {
				ctx.ui.setStatus("pi-lead", undefined);
			} catch {}
			statusShown = undefined;
		}
		// Only "quit" ends the executor; reload/new/resume/fork rebuild the runtime in the same tab.
		if (role !== "executor" || event.reason !== "quit") return;
		sidFrom(ctx);
		const dir = sessionDir();
		if (dir && readStatus(dir) === "idle") writeStatus(dir, "closed");
	});

	/** The executor's running `pilead queue --deliver` child (at most one), and whether shutdown has begun. */
	let delivering: AbortController | undefined;
	let deliveryStopped = false;
	/** After a settle: when sessions/<sid>/queue.json exists, ask pilead to deliver the next queued
	 *  packet. Not awaited; one child at a time; none once session_shutdown has begun. */
	const askForQueued = (dir: string, execSid: string): void => {
		if (deliveryStopped || delivering) return;
		try {
			fs.lstatSync(path.join(dir, "queue.json"));
		} catch {
			return; // no queue: nothing is started
		}
		const abort = new AbortController();
		delivering = abort;
		void deliverQueued(home, execSid, opts.pileadBin, SETTLE_DELIVER_TIMEOUT_MS, abort.signal).then(() => {
			if (delivering === abort) delivering = undefined;
		});
	};
	/** session_shutdown (executor): stop a running delivery child; start none afterwards. */
	const stopQueuedDelivery = (): void => {
		deliveryStopped = true;
		delivering?.abort();
	};

	/** The executor's running `pilead escalate` children; session_shutdown aborts them. */
	const escalating = new Set<AbortController>();
	/**
	 * After a settle with a report: ONE `pilead escalate <sid> --packet <n> --json --home <home>` child (lib/pilead/
	 * escalation.py decides, once per packet). Not awaited, errors swallowed; none once session_shutdown has run.
	 * The default child only when `ctx.hasUI` and `ctx.mode` is "tui" or "rpc"; the `escalate` option is used
	 * whatever the mode. Never throws.
	 */
	const askEscalate = (ctx: ExtensionContext, execSid: string, packet: number): void => {
		try {
			if (closed || !Number.isInteger(packet) || packet < 1) return;
			const args = ["escalate", execSid, "--packet", String(packet), "--json", "--home", home];
			if (opts.escalate) {
				const ask = opts.escalate;
				void Promise.resolve()
					.then(() => ask(args))
					.catch(() => {});
				return;
			}
			let mode: unknown;
			let hasUI: unknown;
			try {
				mode = (ctx as { mode?: unknown }).mode;
				hasUI = ctx.hasUI;
			} catch {}
			if (!(hasUI === true && (mode === "tui" || mode === "rpc"))) return;
			const abort = new AbortController();
			escalating.add(abort);
			void pileadText(args, opts.pileadBin, ESCALATE_TIMEOUT_MS, abort.signal).then(() => {
				escalating.delete(abort);
			});
		} catch {}
	};
	/** session_shutdown (executor): kill the running escalate children. */
	const stopEscalating = (): void => {
		for (const a of escalating) a.abort();
		escalating.clear();
	};

	if (role === "executor") {
		pi.on("tool_call", async (event, ctx) => {
			if (event.toolName !== "bash") return undefined;
			// pi runs the command with its `shellPath` setting (project .pi/settings.json over the global one; default /bin/bash).
			// Either file naming zsh turns on the fence's zsh rules — fail closed: an untrusted project file, or one that cannot be read.
			const settings = [path.join(expandHome(env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent")), "settings.json"),
				path.join(ctx?.cwd ?? process.cwd(), ".pi", "settings.json")];
			const zsh = settings.some((f) => {
				try { return /zsh/.test(path.basename(String(JSON.parse(fs.readFileSync(f, "utf8"))?.shellPath ?? ""))); }
				catch (e: any) { return e?.code !== "ENOENT"; }
			});
			// The fence parses the command's structure (compounds, subshells, sh -c/eval strings,
			// wrappers, git aliases) and never throws; if it somehow did, pi blocks the tool anyway.
			const hit = deniedGitVerdict(String(event.input.command ?? ""), { shell: zsh ? "zsh" : "bash" });
			if (hit) {
				return {
					block: true,
					reason: "pi-lead executor: stage your work and report; the lead commits." +
						` (blocked: ${hit.command}${hit.reason ? ` — ${hit.reason}` : ""})`,
				};
			}
			return undefined;
		});

		pi.on("before_agent_start", async (_event, ctx) => {
			if (isQuiet()) return;
			seen(ctx);
			sidFrom(ctx);
			const dir = sessionDir();
			if (dir) writeStatus(dir, "busy");
		});

		// agent_settled, not agent_end: pi may still retry/compact/continue after agent_end.
		pi.on("agent_settled", async (_event, ctx) => {
			if (isQuiet()) return;
			seen(ctx);
			sidFrom(ctx);
			const dir = sessionDir();
			if (!dir || !sid) return;
			const cur = currentPacket(dir);
			if (cur && hasReport(cur.report)) {
				writeStatus(dir, "reported");
				try {
					notifyLead(home, sid, resolveOwner(home, sid, env.PI_LEAD_LEAD), dir, cur.report, Number.parseInt(cur.num, 10));
				} finally {
					// whether the report reached a listening lead; the verb decides (once per packet)
					askEscalate(ctx, sid, Number.parseInt(cur.num, 10));
				}
			} else {
				writeStatus(dir, "idle");
			}
			// Last: the status is written and the lead notified. A queued packet then arrives via the inbox.
			askForQueued(dir, sid);
			refreshStatus(); // the footer, after everything else
		});

		// pi has recorded a user message (pi 0.87.1 dist/core/agent-session.js: extensions get message_end at
		// line 579, and the session file gets the message at line 595, in the same handler): the held claim of
		// the packet with that text is done with. A message that matches no held claim changes nothing.
		pi.on("message_end", async (event, ctx) => {
			if (isQuiet()) return undefined;
			seen(ctx);
			const m = event.message as { role?: unknown; content?: unknown } | undefined;
			if (m?.role !== "user") return undefined;
			const text = messageText(m.content).trim();
			const i = execHeld.findIndex((h) => h.text === text);
			if (i < 0) return undefined;
			const [h] = execHeld.splice(i, 1);
			try {
				fs.rmSync(h.file, { force: true });
			} catch {}
			return undefined;
		});
	} else {
		// The lead's footer is refreshed after each settle, and what the lead should hear is asked for.
		pi.on("agent_settled", async (_event, ctx) => {
			if (isQuiet()) return;
			seen(ctx);
			refreshStatus();
			sidFrom(ctx);
			askTurnEnd(ctx);
		});

		pi.on("tool_call", async (event, ctx) => {
			if (event.toolName === "bash") return leadBash(event, ctx);
			if (event.toolName !== "edit" && event.toolName !== "write") return undefined;
			const leadSid = sidFrom(ctx);
			if (!leadSid) return undefined;
			const c = conf();
			const t = now();
			const d = routeDecision({
				toolName: event.toolName,
				input: event.input as Record<string, unknown>,
				cwd: ctx.cwd,
				home,
				leadSid,
				now: t,
				threshold: c.edit_line_threshold,
				graceMs: c.grace_seconds * 1000,
				blockOnNewFile: c.block_on_new_file,
			});
			if (!d.block) return undefined;
			const fields: Record<string, unknown> = { session_id: leadSid, file_path: d.filePath, lines: d.lines, new_file: d.newFile };
			if (d.control !== undefined) fields.control = d.control;
			appendLedger(home, "blocked", fields, t);
			return { block: true, reason: d.reason };
		});

		/**
		 * A registered lead's bash command (p20g change 2): no usable id or not registered → runs, no child; no
		 * hint (`bashGateAsks`) → runs, no child; the gate's answer `block` with a reason → refused with that reason;
		 * anything else runs, with one warning when it was not checked (`bashGateVerdict`). The gate itself
		 * (`pilead gate --record`) writes the ledger event. The executor's git fence is not asked. Never throws.
		 */
		const leadBash = async (event: { input?: unknown }, ctx: ExtensionContext): Promise<{ block: true; reason: string } | undefined> => {
			try {
				const leadSid = sidFrom(ctx);
				if (!leadSid || !isRegisteredLead(home, leadSid)) return undefined;
				const command = (event.input as { command?: unknown } | undefined)?.command;
				const cwd = typeof ctx.cwd === "string" && ctx.cwd ? ctx.cwd : process.cwd();
				if (typeof command !== "string" || !bashGateAsks(command, cwd, home)) return undefined;
				const ask = opts.bashGate;
				const reply: BashGateReply = ask
					? await Promise.resolve()
							.then(() => ask(command, cwd))
							.then((text) => ({ text: String(text ?? "") }), () => ({ why: "failed" as const }))
					: await bashGateReply(command, cwd, home, leadSid, opts.pileadBin, BASH_GATE_TIMEOUT_MS);
				const v = bashGateVerdict(reply);
				if (v.block) return v.block;
				if (v.warn) {
					try {
						ctx.ui.notify(`pilead: the bash gate could not check this command (${v.warn})`, "warning");
					} catch {}
				}
				return undefined;
			} catch {
				return undefined;
			}
		};

		pi.registerCommand("pilead:route", {
			// built now, before session_start: the grace this home's config.json names at registration
			description: `Open the lead routing gate for ${graceText(loadConfig(home).grace_seconds * 1000)}: /pilead:route retain "<reason>"`,
			handler: async (args, ctx) => {
				const m = /^retain\s+(.+)$/s.exec(args.trim());
				const reason = m?.[1].trim().replace(/^(["'])(.*)\1$/s, "$2").trim();
				const leadSid = sidFrom(ctx);
				if (!reason || !leadSid) {
					ctx.ui.notify('usage: /pilead:route retain "<why this edit is lead work>"', "warning");
					return;
				}
				if (!isRegisteredLead(home, leadSid)) {
					ctx.ui.notify("pi-lead: this session is not a registered lead — the gate is off; run pilead lead-start to register", "warning");
					return;
				}
				const file = routeGraceFile(home, leadSid);
				fs.mkdirSync(path.dirname(file), { recursive: true });
				const t = now();
				fs.writeFileSync(file, `${new Date(t).toISOString()} ${reason}\n`);
				fs.utimesSync(file, t / 1000, t / 1000); // mtime = opened-at, on the same clock the gate reads
				appendLedger(home, "retained", { session_id: leadSid, reason }, t);
				ctx.ui.notify(`pi-lead: routing gate open for ${graceText(graceMs())} (${reason})`, "info");
				if (!isQuiet()) {
					seen(ctx);
					refreshStatus();
				}
			},
		});

		for (const short of SKILL_NAMES) {
			const skill = `pilead-${short}`;
			const file = path.join(skillsDir, skill, "SKILL.md");
			let description = `Load the ${skill} skill`;
			try {
				description = parseSkillFile(fs.readFileSync(file, "utf-8")).description || description;
			} catch {}
			pi.registerCommand(`pilead:${short}`, {
				description,
				handler: async (args, ctx) => {
					let text: string;
					try {
						text = fs.readFileSync(file, "utf-8");
					} catch (e) {
						ctx.ui.notify(`pi-lead: cannot read skill ${skill} at ${file}: ${(e as Error).message}`, "warning");
						return;
					}
					// followUp: a command handler runs even mid-turn, and a plain send would throw and drop it.
					pi.sendUserMessage(skillBlock(skill, file, parseSkillFile(text).body, args.trim()), { deliverAs: "followUp" });
				},
			});
		}
	}

	pi.registerCommand("pilead:status", {
		description: "Show pi-lead role, session id and watched paths",
		handler: async (_args, ctx) => {
			sidFrom(ctx);
			const lines = [`pi-lead role: ${role}`, `sid: ${sid ?? "(unknown)"}`, `home: ${home}`];
			if (role === "executor") {
				const dir = sessionDir();
				lines.push(`inbox: ${executorInbox() ?? "(none)"}`);
				lines.push(`status: ${dir ? `${readStatus(dir) || "(unset)"} (${path.join(dir, "status")})` : "(none)"}`);
				// the owner a report would wake now (resolveOwner): meta.json's lead, forward notes followed
				const owner = sid ? resolveOwner(home, sid, env.PI_LEAD_LEAD) : validLeadSid(env.PI_LEAD_LEAD) ? env.PI_LEAD_LEAD! : null;
				lines.push(`lead: ${owner ?? "(none or unknown)"} → inbox ${leadInboxPath(home, owner ?? undefined) ?? "(unregistered)"}`);
				lines.push(workLine());
			} else {
				lines.push(`lead registration: ${leadJson() ?? "(none)"}`);
				lines.push(`inbox: ${leadInboxPath(home, sid) ?? "(unregistered — run pilead lead-start)"}`);
				lines.push(`route grace: ${sid && inRouteGrace(home, sid, now(), graceMs()) ? "open" : "closed"} (${sid ? routeGraceFile(home, sid) : "-"})`);
				lines.push(`gate: ${sid && isRegisteredLead(home, sid) ? "on (registered)" : "off (not registered)"}`);
				lines.push(workLine());
				lines.push("bash gate: asked for commands that write or install (mode is config bash_write_gate)");
			}
			ctx.ui.notify(lines.join("\n"), "info");
		},
	});

	const stop = (): void => {
		watcher.stop();
		stopBeat();
	};

	return { role, home, drainInbox, flush: () => deliveries.then(() => turnEndPending).then(() => takeoverPending).then(() => deliveries), stop, leadIsQuiet: isQuiet };
}

/**
 * Prepend this package's `bin/` to PATH so `pilead` resolves in every bash call of a pi session that
 * loads the extension (pi rebuilds the shell env from process.env.PATH on each call). Idempotent;
 * returns the dir it added, or null (already present, missing dir, or any failure).
 */
export function ensurePileadOnPath(env: NodeJS.ProcessEnv = process.env): string | null {
	try {
		const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "bin");
		if (!fs.existsSync(dir)) return null;
		const cur = env.PATH ?? "";
		if (cur.split(path.delimiter).includes(dir)) return null;
		env.PATH = cur ? dir + path.delimiter + cur : dir;
		return dir;
	} catch {
		return null;
	}
}
ensurePileadOnPath();

export default function (pi: ExtensionAPI) {
	createPiLead(pi);
}

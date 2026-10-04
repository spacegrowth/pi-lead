// Unit tests for extensions/pi-lead.ts against a stub `pi` (no live pi, no network).
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { tsImport } from "tsx/esm/api";

// HOME is sandboxed for the whole file (node --test runs each file in its own process), so the
// gate's ~/.relay-tasks and ~/.pi-lead exemptions resolve inside the tmp dir.
const SANDBOX = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "pilead-ts-")));
process.env.HOME = SANDBOX;
after(() => fs.rmSync(SANDBOX, { recursive: true, force: true }));

// package.json has no "type": tsx loads files as CJS (no top-level await), and the module's named
// exports may sit under .default.
let ext: any;
before(async () => {
	const mod: any = await tsImport("../../extensions/pi-lead.ts", { parentURL: import.meta.url } as any);
	ext = typeof mod.createPiLead === "function" ? mod : mod.default;
});

type Handler = (event: any, ctx: any) => Promise<any>;

function stubPi() {
	const handlers = new Map<string, Handler[]>();
	const commands = new Map<string, { handler: (args: string, ctx: any) => Promise<void> }>();
	const sent: { message: any; options: any }[] = [];
	const userSent: { content: any; options: any }[] = [];
	const state = { streaming: false };
	const pi = {
		on: (name: string, h: Handler) => {
			handlers.set(name, [...(handlers.get(name) ?? []), h]);
			return () => {};
		},
		registerCommand: (name: string, options: any) => void commands.set(name, options),
		sendMessage: (message: any, options: any) => void sent.push({ message, options }),
		sendUserMessage: (content: any, options: any) => {
			// pi throws for a plain send while the agent is streaming; only deliverAs queues it.
			if (state.streaming && !options?.deliverAs) throw new Error("Agent is already processing");
			userSent.push({ content, options });
		},
	};
	const fire = async (name: string, event: any, ctx: any) => {
		let result: any;
		for (const h of handlers.get(name) ?? []) result = (await h({ type: name, ...event }, ctx)) ?? result;
		return result;
	};
	return { pi: pi as any, handlers, commands, sent, userSent, fire, state };
}

let seq = 0;
function setup(role: "lead" | "executor", extraEnv: Record<string, string> = {}, opts: any = {}) {
	const home = path.join(SANDBOX, `home-${seq++}`);
	const sid = role === "executor" ? `topic-10000${seq}` : `lead-${seq}`;
	const leadSid = `lead-of-${seq}`;
	const cwd = path.join(SANDBOX, `repo-${seq}`);
	fs.mkdirSync(cwd, { recursive: true });
	const env: Record<string, string> = {
		PI_LEAD_ROLE: role,
		PI_LEAD_HOME: home,
		PI_LEAD_SID: sid,
		...(role === "executor" ? { PI_LEAD_LEAD: leadSid } : {}),
		...extraEnv,
	};
	const stub = stubPi();
	const notes: { msg: string; type: string }[] = [];
	const ctx = {
		cwd,
		hasUI: true,
		sessionManager: { getSessionId: () => sid },
		ui: { notify: (msg: string, type: string) => void notes.push({ msg, type }) },
	};
	const handle = ext.createPiLead(stub.pi, { env, watch: false, pollMs: 0, ...opts });
	const sessDir = path.join(home, "sessions", sid);
	return { ...stub, handle, home, sid, leadSid, cwd, ctx, notes, sessDir };
}

/** What `pilead lead-start` writes: leads/<sid>.json pointing at an inbox. */
function registerLead(home: string, leadSid: string): string {
	const inbox = path.join(home, "leads", `${leadSid}.inbox`);
	fs.mkdirSync(path.dirname(inbox), { recursive: true });
	fs.writeFileSync(path.join(home, "leads", `${leadSid}.json`), JSON.stringify({ sid: leadSid, inbox }));
	fs.writeFileSync(inbox, "");
	return inbox;
}

const status = (dir: string) => fs.readFileSync(path.join(dir, "status"), "utf-8").trim();
const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i}`).join("\n") + "\n";

// ---- executor lifecycle ------------------------------------------------------------------------

test("executor: before_agent_start → busy; agent_settled without report → idle", async () => {
	const t = setup("executor");
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "do it");
	await t.fire("before_agent_start", { prompt: "do it" }, t.ctx);
	assert.equal(status(t.sessDir), "busy");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(t.sessDir), "idle");
});

test("executor: report for the highest packet → reported + one line in the lead inbox", async () => {
	const t = setup("executor");
	const inbox = registerLead(t.home, t.leadSid);
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "Done.\nStatus: clean");
	fs.writeFileSync(path.join(t.sessDir, "packet-0002.md"), "p2");
	// packet 2 is current and has no report yet: report-0001 must not count.
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(t.sessDir), "idle");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");

	const report2 = path.join(t.sessDir, "report-0002.md");
	fs.writeFileSync(report2, "Done 2.\nStatus: clean");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(t.sessDir), "reported");
	assert.equal(fs.readFileSync(inbox, "utf-8"), `reported ${t.sid} ${report2}\n`);
});

test("executor: an empty report file is not a report", async () => {
	const t = setup("executor");
	registerLead(t.home, t.leadSid);
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(t.sessDir), "idle");
});

test("executor: re-settling on an unchanged report does not re-wake; a rewritten report does", async () => {
	const t = setup("executor");
	const inbox = registerLead(t.home, t.leadSid);
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	const report = path.join(t.sessDir, "report-0001.md");
	fs.writeFileSync(report, "v1");
	await t.fire("agent_settled", {}, t.ctx);
	await t.fire("before_agent_start", {}, t.ctx); // human pokes the tab after reporting
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8").split("\n").filter(Boolean).length, 1);
	assert.equal(status(t.sessDir), "reported");

	fs.writeFileSync(report, "v2");
	const later = new Date(Date.now() + 5000);
	fs.utimesSync(report, later, later);
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8").split("\n").filter(Boolean).length, 2);
});

test("executor: lead not registered yet → reported, and the next settle delivers the line", async () => {
	const t = setup("executor");
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "done");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(t.sessDir), "reported");
	const inbox = registerLead(t.home, t.leadSid);
	await t.fire("agent_settled", {}, t.ctx);
	assert.match(fs.readFileSync(inbox, "utf-8"), /^reported topic-\S+ .*report-0001\.md\n$/);
});

test("executor: session_shutdown(quit) closes an idle session only", async () => {
	const idle = setup("executor");
	fs.mkdirSync(idle.sessDir, { recursive: true });
	await idle.fire("agent_settled", {}, idle.ctx); // no packet at all → idle
	await idle.fire("session_shutdown", { reason: "reload" }, idle.ctx);
	assert.equal(status(idle.sessDir), "idle", "reload keeps the executor alive");
	await idle.fire("session_shutdown", { reason: "quit" }, idle.ctx);
	assert.equal(status(idle.sessDir), "closed");

	const rep = setup("executor");
	fs.mkdirSync(rep.sessDir, { recursive: true });
	fs.writeFileSync(path.join(rep.sessDir, "packet-0001.md"), "p");
	fs.writeFileSync(path.join(rep.sessDir, "report-0001.md"), "r");
	await rep.fire("agent_settled", {}, rep.ctx);
	await rep.fire("session_shutdown", { reason: "quit" }, rep.ctx);
	assert.equal(status(rep.sessDir), "reported");
});

test("executor: inbox packet → sendUserMessage(followUp), inbox left empty, one claim file until pi records the message", async () => {
	const t = setup("executor");
	await t.fire("session_start", { reason: "startup" }, t.ctx);
	const inbox = path.join(t.sessDir, "inbox.md");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "", "session_start creates the inbox");
	fs.appendFileSync(inbox, "Follow-up packet\nline 2\n");
	assert.equal(t.handle.drainInbox(), 1);
	assert.deepEqual(t.userSent, [{ content: "Follow-up packet\nline 2", options: { deliverAs: "followUp" } }]);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
	assert.equal(fs.readdirSync(t.sessDir).filter((n) => n.includes(".claim-")).length, 1, "the claim is kept until pi records the packet");
	await t.fire("message_end", { message: { role: "user", content: [{ type: "text", text: "Follow-up packet\nline 2" }] } }, t.ctx);
	assert.deepEqual(fs.readdirSync(t.sessDir).filter((n) => n.includes(".claim-")), []);
	assert.equal(t.handle.drainInbox(), 0, "a drained inbox delivers nothing twice");
});

test("executor: PI_LEAD_INBOX overrides the default inbox path; sid falls back to pi's session id", async () => {
	const custom = path.join(SANDBOX, "custom-inbox.md");
	const t = setup("executor", { PI_LEAD_INBOX: custom, PI_LEAD_SID: "" });
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(custom, "hello");
	assert.equal(t.handle.drainInbox(), 1);
	await t.fire("before_agent_start", {}, t.ctx);
	assert.equal(status(t.sessDir), "busy", "status written under sessions/<pi session id>/");
});

test("executor: git commit/push/… blocked, git add/status allowed", async () => {
	const t = setup("executor");
	const call = (command: string) => t.fire("tool_call", { toolName: "bash", input: { command } }, t.ctx);
	for (const c of ["git commit -m x", "git -C /repo push origin main", "cd x && git rebase main", "git reset --hard HEAD", "git tag v1"]) {
		assert.equal((await call(c))?.block, true, c);
	}
	for (const c of ["git add -A", "git status", "git diff --cached", "git reset HEAD file", "echo commit"]) {
		assert.equal(await call(c), undefined, c);
	}
	// the refusal names the offending simple command, even through a `bash -c` wrapper
	assert.deepEqual(await call("git add -A && bash -c 'git commit -m t'"), {
		block: true,
		reason: "pi-lead executor: stage your work and report; the lead commits. (blocked: git commit -m t)",
	});
	const alias = await call("git ci -m t");
	assert.match(alias.reason, /^pi-lead executor: stage your work and report; the lead commits\. \(blocked: git ci -m t — "ci" is not an allowed git subcommand \(allowed: add, /);
});

test("executor: the fence's zsh rules follow pi's shellPath setting (global or project), bash by default", async () => {
	const agentDir = path.join(SANDBOX, `agent-${seq}`);
	fs.mkdirSync(agentDir, { recursive: true });
	const t = setup("executor", { PI_CODING_AGENT_DIR: agentDir });
	const call = (command: string) => t.fire("tool_call", { toolName: "bash", input: { command } }, t.ctx);
	assert.equal(await call("path=x"), undefined, "no settings: bash, where `path` is an ordinary name");
	fs.writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ shellPath: "/bin/bash" }));
	assert.equal(await call("for path in a b; do echo $path; done"), undefined, "shellPath bash");
	fs.writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ shellPath: "/bin/zsh" }));
	assert.match((await call("path=x"))?.reason ?? "", /rename the variable/, "global shellPath zsh");
	fs.rmSync(path.join(agentDir, "settings.json"));
	fs.mkdirSync(path.join(t.cwd, ".pi"), { recursive: true });
	fs.writeFileSync(path.join(t.cwd, ".pi", "settings.json"), JSON.stringify({ shellPath: "/opt/homebrew/bin/zsh" }));
	assert.equal((await call("local path=$1"))?.block, true, "project shellPath zsh");
	fs.writeFileSync(path.join(t.cwd, ".pi", "settings.json"), "{ not json");
	assert.equal((await call("path=x"))?.block, true, "an unreadable settings file fails closed");
	fs.rmSync(path.join(t.cwd, ".pi"), { recursive: true });
	assert.equal(await call("path=x"), undefined);
	assert.equal((await call("git commit -m x"))?.block, true);
});

test("roles: executor has no routing gate/route command; lead has no status lifecycle", async () => {
	const ex = setup("executor");
	assert.ok(ex.handlers.has("before_agent_start") && ex.handlers.has("agent_settled"));
	assert.ok(!ex.commands.has("pilead:route") && ex.commands.has("pilead:status"));
	const w = await ex.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, ex.ctx);
	assert.equal(w, undefined, "executors are never route-gated");

	const lead = setup("lead");
	assert.ok(!lead.handlers.has("before_agent_start") && lead.handlers.has("agent_settled"));
	assert.ok(lead.commands.has("pilead:route") && lead.commands.has("pilead:status"));
	const b = await lead.fire("tool_call", { toolName: "bash", input: { command: "git commit -m x" } }, lead.ctx);
	assert.equal(b, undefined, "the lead commits");
});

// ---- lead lifecycle ----------------------------------------------------------------------------

test("lead: each reported line → triggerTurn pi-lead message; other lines consumed", async () => {
	const t = setup("lead");
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	fs.appendFileSync(inbox, "garbage line\n");
	fs.appendFileSync(inbox, "reported exec-b /h/sessions/exec-b/report-0003.md\n");
	assert.equal(t.handle.drainInbox(), 2);
	await t.handle.flush(); // wakes are sent once their refs lines arrive
	assert.deepEqual(t.sent[0], {
		message: {
			customType: "pi-lead",
			content: "🚦 [pi-lead] executor exec-a reported: /h/sessions/exec-a/report-0001.md — refs: not compared (unknown session exec-a)",
			display: true,
			details: {
				sid: "exec-a",
				report: "/h/sessions/exec-a/report-0001.md",
				refs: "refs: not compared (unknown session exec-a)",
			},
		},
		options: { triggerTurn: true },
	});
	assert.equal(
		t.sent[1].message.content,
		"🚦 [pi-lead] executor exec-b reported: /h/sessions/exec-b/report-0003.md — refs: not compared (unknown session exec-b)",
	);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
	assert.equal(t.handle.drainInbox(), 0);
});

test("lead: unregistered inbox is a no-op; executor → lead round trip wakes the lead", async () => {
	const lead = setup("lead");
	await lead.fire("session_start", {}, lead.ctx);
	assert.equal(lead.handle.drainInbox(), 0);

	registerLead(lead.home, lead.sid);
	const exStub = stubPi();
	const ex = ext.createPiLead(exStub.pi, {
		env: { PI_LEAD_ROLE: "executor", PI_LEAD_HOME: lead.home, PI_LEAD_SID: "exec-rt-1", PI_LEAD_LEAD: lead.sid },
		watch: false,
		pollMs: 0,
	});
	assert.equal(ex.role, "executor");
	const dir = path.join(lead.home, "sessions", "exec-rt-1");
	fs.mkdirSync(dir, { recursive: true });
	fs.writeFileSync(path.join(dir, "packet-0001.md"), "p");
	fs.writeFileSync(path.join(dir, "report-0001.md"), "r");
	await exStub.fire("agent_settled", {}, { sessionManager: { getSessionId: () => "exec-rt-1" } });
	assert.equal(lead.handle.drainInbox(), 1);
	await lead.handle.flush();
	// no meta.json for exec-rt-1: the refs line says so, and the wake still arrives
	assert.equal(
		lead.sent[0].message.content,
		`🚦 [pi-lead] executor exec-rt-1 reported: ${path.join(dir, "report-0001.md")} — refs: not compared (unknown session exec-rt-1)\n       r`,
	);
});

// ---- refs detection in the wake message ---------------------------------------------------------

function git(cwd: string, ...args: string[]): string {
	return execFileSync("git", ["-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", ...args], { encoding: "utf-8" });
}

/** A real repo + a session whose packet-0001 refs baseline `pilead` itself stored (lib/pilead/refs.py). */
function sessionWithBaseline(home: string, execSid: string, leadSid: string): string {
	const repo = path.join(SANDBOX, `refs-repo-${execSid}`);
	fs.mkdirSync(repo, { recursive: true });
	git(repo, "init", "-q");
	git(repo, "commit", "-q", "--allow-empty", "-m", "seed");
	const dir = path.join(home, "sessions", execSid);
	fs.mkdirSync(dir, { recursive: true });
	const meta = { sid: execSid, lead: leadSid, worktree: repo, topic: "t", model: "m", status: "reported", created: "-", packets: 1, label: "x" };
	fs.writeFileSync(path.join(dir, "meta.json"), JSON.stringify(meta));
	fs.writeFileSync(path.join(dir, "packet-0001.md"), "p");
	fs.writeFileSync(path.join(dir, "report-0001.md"), "r");
	execFileSync("python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(path.join(REPO, "lib"))}); from pilead import refs; refs.record_baseline(${JSON.stringify(home)}, ${JSON.stringify(execSid)}, 1, ${JSON.stringify(repo)})`]);
	assert.ok(fs.existsSync(path.join(dir, "refs-0001.json")));
	return repo;
}

test("lead: the wake message carries the refs line — unchanged, then REFS MOVED after a commit", async () => {
	const t = setup("lead");
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	const repo = sessionWithBaseline(t.home, "exec-refs", t.sid);
	const report = path.join(t.home, "sessions", "exec-refs", "report-0001.md");

	fs.appendFileSync(inbox, `reported exec-refs ${report}\n`);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.sent[0].message.content, `🚦 [pi-lead] executor exec-refs reported: ${report} — refs: unchanged since packet 0001\n       r`);
	assert.equal(t.sent[0].message.details.refs, "refs: unchanged since packet 0001");

	git(repo, "commit", "-q", "--allow-empty", "-m", "a commit the fence never saw");
	fs.appendFileSync(inbox, `reported exec-refs ${report}\n`);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.sent[1].message.content, `🚦 [pi-lead] executor exec-refs reported: ${report} — REFS MOVED: 2 change(s) since packet 0001\n       r`);
});

test("lead: the wake message says `refs: not compared (no baseline stored)` when the baseline is missing", async () => {
	const t = setup("lead");
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	sessionWithBaseline(t.home, "exec-nobase", t.sid);
	fs.rmSync(path.join(t.home, "sessions", "exec-nobase", "refs-0001.json"));
	fs.appendFileSync(inbox, "reported exec-nobase /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.sent[0].message.content, "🚦 [pi-lead] executor exec-nobase reported: /r/report-0001.md — refs: not compared (no baseline stored)");
});

test("refsLine: a missing pilead gives `refs: not checked (…)` and never rejects", async () => {
	const pending = ext.refsLine(SANDBOX, "exec-x", path.join(SANDBOX, "no-such-pilead"));
	assert.ok(pending instanceof Promise);
	assert.match(await pending, /^refs: not checked \(/);
});

/** A stub `pilead` (sh): `check <sid> --refs-only …` sleeps per sid (`slow-*` 0.6 s, `hang-*` 30 s), then
 *  prints `refs: stub for <sid>`; `fail-*` exits 2 with nothing on stdout. Every call is logged; `times`
 *  gets `start <sid> <epoch s>` / `end <sid> <epoch s>` (ms resolution) around the sleep,
 *  `<dir>/pid-<sid>` holds the stub's own pid and `<dir>/child-<sid>` its sleep's pid. The sleep runs in
 *  the background under `wait`, and SIGTERM (a timeout, an abort) kills it with the stub: nothing lingers. */
function stubPilead(): { bin: string; log: string; times: string; dir: string } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "stub-pilead-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	const times = path.join(dir, "times.log");
	const stamp = `perl -MTime::HiRes=time -e 'printf "%.3f", time'`;
	fs.writeFileSync(
		bin,
		`#!/bin/sh\ntrap 'kill $! 2>/dev/null; exit 143' TERM\n` +
			`echo "$2" >> ${JSON.stringify(log)}\necho $$ > ${JSON.stringify(dir)}/pid-"$2"\n` +
			`echo "start $2 $(${stamp})" >> ${JSON.stringify(times)}\n` +
			`nap() { sleep "$1" & echo $! > ${JSON.stringify(dir)}/child-"$2"; wait $!; }\n` +
			`case "$2" in slow-*) nap 0.6 "$2" ;; hang-*) nap 30 "$2" ;; fail-*) exit 2 ;; esac\n` +
			`echo "end $2 $(${stamp})" >> ${JSON.stringify(times)}\necho "refs: stub for $2"\n`,
	);
	fs.chmodSync(bin, 0o755);
	return { bin, log, times, dir };
}

/** [start, end] (epoch s) of each stub call, by sid, from the stub's times log. */
function callIntervals(times: string): Map<string, { start: number; end?: number }> {
	const out = new Map<string, { start: number; end?: number }>();
	for (const l of fs.readFileSync(times, "utf-8").split("\n")) {
		const [what, sid, t] = l.split(" ");
		if (what === "start") out.set(sid, { start: Number(t) });
		else if (what === "end") out.get(sid)!.end = Number(t);
	}
	return out;
}

test("refsLine: a slow pilead times out as `refs: not checked (timed out …)`; a failing one is `not checked` too", async () => {
	const { bin } = stubPilead();
	assert.equal(await ext.refsLine(SANDBOX, "hang-1", bin, 200), "refs: not checked (timed out after 200 ms)");
	assert.match(await ext.refsLine(SANDBOX, "fail-1", bin), /^refs: not checked \(Command failed: /);
	assert.equal(await ext.refsLine(SANDBOX, "ok-1", bin), "refs: stub for ok-1");
});

test("lead: a poll while a refs line is still being fetched neither re-delivers nor loses the wake", async () => {
	const { bin, log } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported slow-1 /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	// the watcher fires again (a poll, another fs event) before the late answer arrives
	assert.equal(t.handle.drainInbox(), 0);
	assert.equal(t.handle.drainInbox(), 0);
	assert.equal(t.sent.length, 0, "nothing sent before the refs line arrives");
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.equal(t.sent[0].message.content, "🚦 [pi-lead] executor slow-1 reported: /r/report-0001.md — refs: stub for slow-1");
	assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.equal(t.sent.length, 1, "delivered exactly once");
	assert.equal(fs.readFileSync(log, "utf-8"), "slow-1\n", "asked exactly once");
});

test("lead: sessions reporting in one drain are asked concurrently and woken in inbox order", async () => {
	const { bin, times } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported slow-a /r/a.md\nreported slow-b /r/b.md\nreported fast-c /r/c.md\n");
	assert.equal(t.handle.drainInbox(), 3);
	await t.handle.flush();
	// concurrency is proven by overlap, not speed: each call's [start, end] as the stub itself recorded
	// it; asked one after the other, slow-b would start only after slow-a ended
	const iv = callIntervals(times);
	const a = iv.get("slow-a")!;
	const b = iv.get("slow-b")!;
	assert.ok(a.end !== undefined && b.end !== undefined, `both calls ran to the end: ${JSON.stringify([...iv])}`);
	assert.ok(a.start < b.end! && b.start < a.end!, `the two 0.6 s calls overlap: slow-a ${JSON.stringify(a)}, slow-b ${JSON.stringify(b)}`);
	assert.deepEqual(
		t.sent.map((x) => x.message.details.sid),
		["slow-a", "slow-b", "fast-c"],
	);
	// a later drain's wake is sent after every earlier one, even when its own refs line is faster
	fs.appendFileSync(inbox, "reported slow-d /r/d.md\n");
	t.handle.drainInbox();
	fs.appendFileSync(inbox, "reported fast-e /r/e.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	assert.deepEqual(
		t.sent.map((x) => x.message.details.sid),
		["slow-a", "slow-b", "fast-c", "slow-d", "fast-e"],
	);
});

// ---- a wake is never lost at shutdown -----------------------------------------------------------
// pi's sendMessage inside session_shutdown does not throw (it is fire-and-forget into the session being
// torn down), so these stubs never throw either: shutdown must send nothing and put everything back.

/** A second lead extension instance on the same home and sid: what pi builds after a reload / new session. */
function nextLeadSession(t: ReturnType<typeof setup>, opts: any = {}) {
	const stub = stubPi();
	const handle = ext.createPiLead(stub.pi, {
		env: { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: t.home, PI_LEAD_SID: t.sid },
		watch: false,
		pollMs: 0,
		...opts,
	});
	return { ...stub, handle };
}

const claimLeftovers = (inbox: string) => fs.readdirSync(path.dirname(inbox)).filter((f) => f.includes(".claim-"));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function pidGone(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return false;
	} catch (e: any) {
		return e?.code === "ESRCH";
	}
}

/** Resolves once `cond()` is true; fails after `ms`. */
async function until(cond: () => boolean, what: string, ms = 3000): Promise<void> {
	for (const t0 = Date.now(); !cond(); await sleep(20)) if (Date.now() - t0 > ms) assert.fail(`timed out waiting for ${what}`);
}

/** The pid a stub pilead wrote to `<dir>/<kind>-<sid>` (waits for the file). */
async function stubPid(dir: string, kind: "pid" | "child", sid: string): Promise<number> {
	const f = path.join(dir, `${kind}-${sid}`);
	await until(() => fs.existsSync(f) && fs.readFileSync(f, "utf-8").trim() !== "", `${f}`);
	return Number(fs.readFileSync(f, "utf-8").trim());
}

test("lead: shutdown with a wake pending sends nothing and puts it back; the next session delivers it once", async () => {
	const { bin, dir } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported slow-1 /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "", "claimed");
	await stubPid(dir, "child", "slow-1"); // the fetch is under way

	// not awaited: the put-back is complete when the handler returns, whether or not pi awaits it
	void t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	assert.equal(t.sent.length, 0, "sendMessage was not called for the pending wake");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "reported slow-1 /r/report-0001.md\n", "back in the inbox under its own name");
	assert.deepEqual(claimLeftovers(inbox), []);

	const next = nextLeadSession(t, { pileadBin: bin });
	await next.fire("session_start", {}, t.ctx); // its first drain
	await next.handle.flush();
	assert.equal(next.sent.length, 1);
	assert.equal(next.sent[0].message.content, "🚦 [pi-lead] executor slow-1 reported: /r/report-0001.md — refs: stub for slow-1");
	assert.equal(next.handle.drainInbox(), 0);
	await next.handle.flush();
	assert.equal(next.sent.length, 1, "delivered exactly once");
	assert.equal(t.sent.length, 0, "and never by the session that shut down");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
});

test("lead: two wakes pending at shutdown go back in their order, ahead of a newer entry", async () => {
	const { bin } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported slow-a /r/a.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	fs.appendFileSync(inbox, "reported slow-b /r/b.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	fs.appendFileSync(inbox, "reported fast-c /r/c.md\n"); // written after both claims, never claimed
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.equal(t.sent.length, 0);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "reported slow-a /r/a.md\nreported slow-b /r/b.md\nreported fast-c /r/c.md\n");

	const next = nextLeadSession(t, { pileadBin: bin });
	await next.fire("session_start", {}, t.ctx);
	await next.handle.flush();
	assert.deepEqual(
		next.sent.map((x) => [x.message.details.sid, x.message.details.refs]),
		[
			["slow-a", "refs: stub for slow-a"],
			["slow-b", "refs: stub for slow-b"],
			["fast-c", "refs: stub for fast-c"],
		],
	);
});

test("lead: a wake already handed to sendMessage before shutdown is not put back; the pending one is", async () => {
	const { bin } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported fast-a /r/a.md\nreported slow-b /r/b.md\n");
	assert.equal(t.handle.drainInbox(), 2);
	await until(() => t.sent.length === 1, "fast-a to be sent");
	assert.equal(t.sent[0].message.details.sid, "fast-a");
	await t.fire("session_shutdown", { reason: "new" }, t.ctx);
	assert.equal(t.sent.length, 1, "slow-b was not sent");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "reported slow-b /r/b.md\n", "only the pending wake is put back");

	const next = nextLeadSession(t, { pileadBin: bin });
	await next.fire("session_start", {}, t.ctx);
	await next.handle.flush();
	assert.deepEqual(next.sent.map((x) => x.message.details.sid), ["slow-b"], "fast-a does not arrive twice");
});

test("lead: after the shutdown handler returns, a late refs answer, the retry timer and the poll are inert", async () => {
	const { bin, dir } = stubPilead();
	const t = setup("lead", {}, { pileadBin: bin, watch: true, pollMs: 50, retryMs: 100 });
	try {
		const inbox = registerLead(t.home, t.sid);
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(inbox, "reported slow-1 /r/report-0001.md\n");
		await until(() => fs.existsSync(path.join(dir, "child-slow-1")), "the poll to claim slow-1 and start its fetch");
		const pid = await stubPid(dir, "pid", "slow-1");
		const child = await stubPid(dir, "child", "slow-1");
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		assert.equal(t.sent.length, 0);
		const inboxAfter = fs.readFileSync(inbox, "utf-8");
		assert.equal(inboxAfter, "reported slow-1 /r/report-0001.md\n");
		const mtimeAfter = fs.statSync(inbox).mtimeMs;

		// longer than the late stub (0.6 s), many poll intervals (50 ms) and retryMs (100 ms)
		await sleep(1000);
		await t.handle.flush();
		assert.equal(t.sent.length, 0, "no sendMessage after the handler returned");
		assert.equal(fs.readFileSync(inbox, "utf-8"), inboxAfter, "no write to the inbox");
		assert.equal(fs.statSync(inbox).mtimeMs, mtimeAfter);
		assert.deepEqual(claimLeftovers(inbox), [], "no claim either");
		assert.ok(pidGone(pid), `the stub pilead (pid ${pid}) was killed`);
		assert.ok(pidGone(child), `its child (pid ${child}) is not left running`);
		assert.equal(fs.existsSync(path.join(dir, "times.log")) && /^end slow-1 /m.test(fs.readFileSync(path.join(dir, "times.log"), "utf-8")), false, "the fetch never ran to its end");
	} finally {
		t.handle.stop(); // a failed assertion never leaves a watcher holding the runner open
	}
});

test("lead: shutdown during the hold after a failed send leaves the retry timer inert", async () => {
	const refsCheck = (_h: string, s: string) => `refs: stub for ${s}`;
	const t = setup("lead", {}, { refsCheck, watch: true, pollMs: 0, retryMs: 300 });
	try {
		const inbox = registerLead(t.home, t.sid);
		let calls = 0;
		t.pi.sendMessage = () => {
			calls++;
			throw new Error("transient"); // a running session's send failing: put back, hold, retry timer armed
		};
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(inbox, "reported x-1 /r/x.md\n");
		t.handle.drainInbox();
		await t.handle.flush();
		assert.equal(calls, 1);
		assert.equal(fs.readFileSync(inbox, "utf-8"), "reported x-1 /r/x.md\n");
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		await sleep(700);
		assert.equal(calls, 1, "the retry timer did not drain after shutdown");
		assert.equal(fs.readFileSync(inbox, "utf-8"), "reported x-1 /r/x.md\n");
	} finally {
		t.handle.stop(); // a failed assertion never leaves a watcher holding the runner open
	}
});

test("lead: with poll_interval 0, a retry timer that fires before the hold ends re-arms and delivers without another inbox write", async () => {
	let clock = 1_000_000; // the injected clock: frozen, so every timer firing is "early" until it moves
	const refsCheck = (_h: string, s: string) => `refs: stub for ${s}`;
	const t = setup("lead", {}, { refsCheck, watch: true, pollMs: undefined, retryMs: 100, now: () => clock });
	try {
		fs.mkdirSync(t.home, { recursive: true });
		fs.writeFileSync(path.join(t.home, "config.json"), JSON.stringify({ poll_interval: 0 }));
		const inbox = registerLead(t.home, t.sid);
		const send = t.pi.sendMessage;
		let failures = 0;
		t.pi.sendMessage = (m: any, o: any) => {
			if (failures++ === 0) throw new Error("transient");
			send(m, o);
		};
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(inbox, "reported x-1 /r/x.md\n");
		assert.equal(t.handle.drainInbox(), 1);
		await t.handle.flush();
		assert.equal(t.sent.length, 0);
		assert.equal(fs.readFileSync(inbox, "utf-8"), "reported x-1 /r/x.md\n", "put back");

		await sleep(350); // the timer has fired ~3 times, each before the (frozen) hold's end
		assert.equal(t.sent.length, 0, "no claim during the hold");
		assert.equal(fs.readFileSync(inbox, "utf-8"), "reported x-1 /r/x.md\n");

		clock += 100; // the hold is over; nobody writes the inbox again
		await until(() => t.sent.length === 1, "the re-armed retry timer to deliver the wake");
		await t.handle.flush();
		assert.equal(t.sent[0].message.content, "🚦 [pi-lead] executor x-1 reported: /r/x.md — refs: stub for x-1");
		assert.equal(fs.readFileSync(inbox, "utf-8"), "");
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	} finally {
		t.handle.stop(); // a failed assertion never leaves a watcher holding the runner open
	}
});

test("lead: a wake whose send throws outside shutdown is put back and delivered by a later drain", async () => {
	const t = setup("lead", {}, { refsCheck: (_h: string, s: string) => `refs: stub for ${s}`, retryMs: 50 });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	const send = t.pi.sendMessage;
	t.pi.sendMessage = (m: any) => {
		if (m.details.sid === "x-1") throw new Error("transient");
		send(m, { triggerTurn: true });
	};
	fs.appendFileSync(inbox, "reported x-1 /r/x.md\nreported y-1 /r/y.md\n");
	assert.equal(t.handle.drainInbox(), 2);
	await t.handle.flush();
	assert.deepEqual(t.sent.map((x) => x.message.details.sid), ["y-1"], "one failed send does not stop the next");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "reported x-1 /r/x.md\n", "the failed one is back in the inbox");
	assert.equal(t.handle.drainInbox(), 0, "no retry before retryMs");
	t.pi.sendMessage = send;
	await new Promise((r) => setTimeout(r, 80));
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.deepEqual(t.sent.map((x) => x.message.details.sid), ["y-1", "x-1"]);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
});

test("lead: a refsCheck that throws or rejects still delivers the wake", async () => {
	for (const refsCheck of [
		() => {
			throw new Error("boom");
		},
		() => Promise.reject(new Error("late boom")),
	]) {
		const t = setup("lead", {}, { refsCheck });
		const inbox = registerLead(t.home, t.sid);
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(inbox, "reported x-1 /r/x.md\n");
		assert.equal(t.handle.drainInbox(), 1);
		await t.handle.flush();
		assert.match(t.sent[0].message.content, /— refs: not checked \((late )?boom\)$/);
	}
});

test("lead: real watcher picks up a registration made after start and a later report line", async () => {
	const t = setup("lead", {}, { watch: true, pollMs: 50 });
	await t.fire("session_start", {}, t.ctx);
	const inbox = registerLead(t.home, t.sid); // `pilead lead-start` run from the lead's bash
	fs.appendFileSync(inbox, "reported exec-w /r/report-0001.md\n");
	const deadline = Date.now() + 3000;
	while (t.sent.length === 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20));
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx); // stops watchers + poll
	assert.equal(t.sent.length, 1);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
});

// ---- routing gate ------------------------------------------------------------------------------

test("gate matrix: new file / threshold / exempt paths", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	const call = (toolName: string, input: any) => t.fire("tool_call", { toolName, input }, t.ctx);
	const existing = path.join(t.cwd, "existing.ts");
	fs.writeFileSync(existing, lines(10));

	// small edits to existing files: allowed
	assert.equal(await call("edit", { path: existing, edits: [{ oldText: "line 1", newText: "LINE 1" }] }), undefined);
	assert.equal(await call("write", { path: "existing.ts", content: lines(40) }), undefined, "40 = at threshold");
	// over threshold
	assert.equal((await call("write", { path: "existing.ts", content: lines(41) }))?.block, true);
	assert.equal((await call("edit", { path: existing, edits: [{ oldText: "a", newText: lines(80) }, { oldText: "b", newText: lines(41) }] }))?.block, true);
	assert.equal((await call("edit", { path: existing, oldText: lines(200), newText: "" }))?.block, true, "legacy shape, big deletion");
	// new file, even tiny
	const r = await call("write", { path: "src/new.ts", content: "x\n" });
	assert.equal(r?.block, true);
	assert.match(r.reason, /delegate this \(pilead spawn\/send\) or `\/pilead:route retain/);
	// read-only tools untouched
	assert.equal(await call("read", { path: "src/new.ts" }), undefined);

	// exempt: *-packet.md anywhere, ~/.relay-tasks/**, ~/.pi-lead/**, $PI_LEAD_HOME/**
	assert.equal(await call("write", { path: "drafts/007-packet.md", content: lines(300) }), undefined);
	assert.equal(await call("write", { path: "~/.relay-tasks/x/notes.md", content: lines(300) }), undefined);
	assert.equal(await call("write", { path: path.join(SANDBOX, ".pi-lead", "a.md"), content: "x" }), undefined);
	assert.equal(await call("write", { path: `@${path.join(t.home, "sessions", "e", "packet-0002.md")}`, content: lines(300) }), undefined);
	// a lookalike outside the roots is not exempt
	assert.equal((await call("write", { path: path.join(SANDBOX, ".relay-tasks-evil", "a.md"), content: "x" }))?.block, true);
});

test("gate grace: /pilead:route retain opens the grace window; stale grace or bad usage does not", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	const write = () => t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, t.ctx);
	assert.equal((await write())?.block, true);

	await t.commands.get("pilead:route")!.handler("", t.ctx);
	assert.equal(t.notes.at(-1)?.type, "warning");
	assert.equal((await write())?.block, true, "usage error opens nothing");

	await t.commands.get("pilead:route")!.handler('retain "schema bump the lead owns"', t.ctx);
	const grace = path.join(t.home, "leads", `${t.sid}.route`);
	assert.match(fs.readFileSync(grace, "utf-8"), /schema bump the lead owns/);
	assert.equal(await write(), undefined);

	const stale = new Date(Date.now() - 11 * 60 * 1000);
	fs.utimesSync(grace, stale, stale);
	assert.equal((await write())?.block, true);
});

test("gate grace uses the injected clock", async () => {
	let now = Date.now();
	const t = setup("lead", {}, { now: () => now });
	registerLead(t.home, t.sid);
	await t.commands.get("pilead:route")!.handler("retain quick fix", t.ctx);
	const write = () => t.fire("tool_call", { toolName: "write", input: { path: "n.ts", content: "x" } }, t.ctx);
	assert.equal(await write(), undefined);
	now += 10 * 60 * 1000 + 1;
	assert.equal((await write())?.block, true);
});

test("changedLines + resolveToolPath helpers", () => {
	assert.equal(ext.changedLines("write", { content: "" }), 0);
	assert.equal(ext.changedLines("write", { content: "a\nb" }), 2);
	assert.equal(ext.changedLines("write", { content: "a\nb\n" }), 2);
	assert.equal(ext.changedLines("edit", { edits: [{ oldText: "a\nb\nc", newText: "x" }, { oldText: "y", newText: "1\n2" }] }), 5);
	assert.equal(ext.changedLines("edit", { edits: "nonsense" }), 0);
	assert.equal(ext.changedLines("bash", { command: "x" }), 0);
	assert.equal(ext.resolveToolPath("@src/a.ts", "/r"), "/r/src/a.ts");
	assert.equal(ext.resolveToolPath("~/x", "/r"), path.join(SANDBOX, "x"));
});

test("/pilead:status prints role, sid and watched paths", async () => {
	const lead = setup("lead");
	registerLead(lead.home, lead.sid);
	await lead.commands.get("pilead:status")!.handler("", lead.ctx);
	const msg = lead.notes.at(-1)!.msg;
	assert.match(msg, /role: lead/);
	assert.match(msg, new RegExp(`sid: ${lead.sid}`));
	assert.ok(msg.includes(path.join(lead.home, "leads", `${lead.sid}.inbox`)), msg);
	assert.match(msg, /route grace: closed/);

	const ex = setup("executor");
	await ex.commands.get("pilead:status")!.handler("", ex.ctx);
	const emsg = ex.notes.at(-1)!.msg;
	assert.match(emsg, /role: executor/);
	assert.ok(emsg.includes(path.join(ex.sessDir, "inbox.md")), emsg);
	assert.match(emsg, /unregistered/);
});

test("ensurePileadOnPath prepends the package bin once and is idempotent", () => {
	const bin = path.resolve(import.meta.dirname ?? path.dirname(fileURLToPath(import.meta.url)), "../../bin");
	const env: any = { PATH: "/usr/bin:/bin" };
	assert.equal(ext.ensurePileadOnPath(env), bin);
	assert.equal(env.PATH, `${bin}${path.delimiter}/usr/bin:/bin`);
	assert.equal(ext.ensurePileadOnPath(env), null);
	assert.equal(env.PATH.split(path.delimiter).filter((p: string) => p === bin).length, 1);
});

test("ensurePileadOnPath leaves an env that already has the dir byte-identical", () => {
	const bin = path.resolve(import.meta.dirname ?? path.dirname(fileURLToPath(import.meta.url)), "../../bin");
	const before = `/x${path.delimiter}${bin}${path.delimiter}/y`;
	const env: any = { PATH: before };
	assert.equal(ext.ensurePileadOnPath(env), null);
	assert.equal(env.PATH, before);
});

// ---- /pilead:<name> skill commands -------------------------------------------------------------

// relay 0.5.7's nineteen command skills, then pi-lead's own seventeen verbs (`route` is a command of its own, below)
const SKILLS = [
	"mode", "spawn", "send", "check", "list", "review", "verify", "close", "diff", "board",
	"auto", "tier", "plan", "handoff", "restart", "resume", "retire", "focus", "stop",
	"adopt", "auto-close", "close-predecessor", "doctor", "gate", "keep", "lineup", "lint", "notify", "prune", "queue",
	"refs", "report", "stats", "takeover", "tidy", "whoami",
];
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

test("lead registers all 38 /pilead: commands; executor only pilead:status", () => {
	const lead = setup("lead");
	assert.deepEqual([...lead.commands.keys()].sort(), ["pilead:route", "pilead:status", ...SKILLS.map((n) => `pilead:${n}`)].sort());
	const ex = setup("executor");
	assert.deepEqual([...ex.commands.keys()], ["pilead:status"]);
});

test("skill command description is the SKILL.md frontmatter description", () => {
	const lead = setup("lead");
	const text = fs.readFileSync(path.join(REPO, "skills", "pilead-mode", "SKILL.md"), "utf-8");
	const want = /^description: (.*)$/m.exec(text)![1];
	assert.equal((lead.commands.get("pilead:mode") as any).description, want);
});

test("skill command sends pi's <skill> block, with and without args", async () => {
	const lead = setup("lead");
	const file = path.join(REPO, "skills", "pilead-spawn", "SKILL.md");
	const body = fs.readFileSync(file, "utf-8").replace(/^---\n[\s\S]*?\n---\n/, "").trim();
	const block = `<skill name="pilead-spawn" location="${file}">\nReferences are relative to ${path.dirname(file)}.\n\n${body}\n</skill>`;
	await lead.commands.get("pilead:spawn")!.handler("", lead.ctx);
	await lead.commands.get("pilead:spawn")!.handler("  fix the bug  ", lead.ctx);
	assert.equal(lead.userSent.length, 2);
	assert.equal(lead.userSent[0].content, block);
	assert.equal(lead.userSent[1].content, `${block}\n\nfix the bug`);
	assert.equal(lead.notes.length, 0);
});

test("missing skill file warns with its path and does not throw", async () => {
	const missing = path.join(SANDBOX, "no-skills");
	const lead = setup("lead", {}, { skillsDir: missing });
	await lead.commands.get("pilead:mode")!.handler("x", lead.ctx);
	assert.equal(lead.userSent.length, 0);
	assert.equal(lead.notes.length, 1);
	assert.equal(lead.notes[0].type, "warning");
	assert.ok(lead.notes[0].msg.includes(path.join(missing, "pilead-mode", "SKILL.md")));
});

test("skill command typed while the agent streams is queued as a followUp, not lost", async () => {
	const lead = setup("lead");
	lead.state.streaming = true;
	await lead.commands.get("pilead:list")!.handler("now", lead.ctx);
	assert.equal(lead.userSent.length, 1);
	assert.equal(lead.userSent[0].options?.deliverAs, "followUp");
	assert.ok(lead.userSent[0].content.endsWith("\n\nnow"));
});

test("skill command still sends when the agent is idle", async () => {
	const lead = setup("lead");
	await lead.commands.get("pilead:list")!.handler("", lead.ctx);
	assert.equal(lead.userSent.length, 1);
	assert.match(lead.userSent[0].content, /^<skill name="pilead-list"/);
});

// ---- config.json + ledger.jsonl ----------------------------------------------------------------

const writeConfig = (home: string, cfg: unknown) => {
	fs.mkdirSync(home, { recursive: true });
	fs.writeFileSync(path.join(home, "config.json"), typeof cfg === "string" ? cfg : JSON.stringify(cfg));
};
const readLedger = (home: string) =>
	fs.existsSync(path.join(home, "ledger.jsonl"))
		? fs.readFileSync(path.join(home, "ledger.jsonl"), "utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l))
		: [];
const fieldsOf = (rec: any) => {
	const { ts, event, ...rest } = rec;
	assert.match(ts, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
	return rest;
};

test("loadConfig: defaults are the constants; bad files and wrong types fall back per key", () => {
	const home = path.join(SANDBOX, "cfg-unit");
	const want = { edit_line_threshold: 40, block_on_new_file: true, grace_seconds: 120, poll_interval: 3 };
	assert.deepEqual(ext.loadConfig(home), want, "no file");
	assert.deepEqual({ ...ext.CONFIG_DEFAULTS }, want);
	assert.equal(ext.CONFIG_DEFAULTS.edit_line_threshold, ext.ROUTE_LINE_THRESHOLD);
	assert.equal(ext.CONFIG_DEFAULTS.grace_seconds * 1000, ext.ROUTE_GRACE_MS);
	for (const bad of ["{nope", "", "[1]", "null", '"x"']) {
		writeConfig(home, bad);
		assert.deepEqual(ext.loadConfig(home), want, `corrupt: ${bad}`);
	}
	writeConfig(home, { edit_line_threshold: "40", block_on_new_file: 0, grace_seconds: null, poll_interval: 1.5, other: 1 });
	assert.deepEqual(ext.loadConfig(home), { ...want, poll_interval: 1.5 });
	writeConfig(home, { edit_line_threshold: 40, block_on_new_file: false, grace_seconds: 90 });
	assert.deepEqual(ext.loadConfig(home), { edit_line_threshold: 40, block_on_new_file: false, grace_seconds: 90, poll_interval: 3 });
});

test("gate honours edit_line_threshold from config.json (read at session start)", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	writeConfig(t.home, { edit_line_threshold: 10 });
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(path.join(t.cwd, "existing.ts"), lines(3));
	const write = (n: number) => t.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(n) } }, t.ctx);
	assert.equal(await write(10), undefined, "10 = at the configured threshold");
	assert.equal((await write(11))?.block, true);
	writeConfig(t.home, { edit_line_threshold: 500 }); // read once: a later edit of the file changes nothing
	assert.equal((await write(11))?.block, true);
});

test("gate without a config file falls back to the exported constants", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(path.join(t.cwd, "existing.ts"), lines(3));
	const write = (n: number) => t.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(n) } }, t.ctx);
	assert.equal(await write(ext.ROUTE_LINE_THRESHOLD), undefined);
	assert.equal((await write(ext.ROUTE_LINE_THRESHOLD + 1))?.block, true);
	const bad = setup("lead");
	registerLead(bad.home, bad.sid);
	writeConfig(bad.home, "{corrupt");
	await bad.fire("session_start", {}, bad.ctx);
	fs.writeFileSync(path.join(bad.cwd, "existing.ts"), lines(3));
	const w = (n: number) => bad.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(n) } }, bad.ctx);
	assert.equal(await w(40), undefined, "a corrupt file is the defaults");
	assert.equal((await w(41))?.block, true);
});

test("gate honours grace_seconds from config.json; default grace is ROUTE_GRACE_MS", async () => {
	let now = Date.now();
	const t = setup("lead", {}, { now: () => now });
	registerLead(t.home, t.sid);
	writeConfig(t.home, { grace_seconds: 90 });
	await t.fire("session_start", {}, t.ctx);
	await t.commands.get("pilead:route")!.handler("retain short window", t.ctx);
	assert.match(t.notes.at(-1)!.msg, /routing gate open for 90 s \(short window\)/);
	const write = () => t.fire("tool_call", { toolName: "write", input: { path: "n.ts", content: "x" } }, t.ctx);
	now += 89 * 1000;
	assert.equal(await write(), undefined);
	now += 2 * 1000;
	const r = await write();
	assert.equal(r?.block, true);
	assert.match(r.reason, /retry within 90 s\./);

	let now2 = Date.now();
	const d = setup("lead", {}, { now: () => now2 });
	registerLead(d.home, d.sid);
	await d.fire("session_start", {}, d.ctx);
	await d.commands.get("pilead:route")!.handler("retain default window", d.ctx);
	assert.match(d.notes.at(-1)!.msg, /routing gate open for 2 min/);
	const w = () => d.fire("tool_call", { toolName: "write", input: { path: "n.ts", content: "x" } }, d.ctx);
	now2 += ext.ROUTE_GRACE_MS - 1;
	assert.equal(await w(), undefined);
	now2 += 2;
	assert.equal((await w())?.block, true);
});

test("block_on_new_file: false lets a small new file through; the line threshold still applies", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	writeConfig(t.home, { block_on_new_file: false });
	await t.fire("session_start", {}, t.ctx);
	const write = (p: string, n: number) => t.fire("tool_call", { toolName: "write", input: { path: p, content: lines(n) } }, t.ctx);
	assert.equal(await write("small.ts", 5), undefined);
	const r = await write("big.ts", 121);
	assert.equal(r?.block, true);
	assert.match(r.reason, /new file, 121 lines/);
});

test("ledger: blocked {session_id, file_path, lines, new_file} per denied edit/write", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(path.join(t.cwd, "existing.ts"), lines(3));
	await t.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(5) } }, t.ctx); // allowed
	await t.fire("tool_call", { toolName: "write", input: { path: "src/new.ts", content: lines(2) } }, t.ctx);
	await t.fire("tool_call", { toolName: "edit", input: { path: "existing.ts", edits: [{ oldText: "a", newText: lines(130) }] } }, t.ctx);
	const recs = readLedger(t.home);
	assert.deepEqual(recs.map((r: any) => r.event), ["blocked", "blocked"]);
	assert.deepEqual(fieldsOf(recs[0]), { session_id: t.sid, file_path: path.join(t.cwd, "src", "new.ts"), lines: 2, new_file: true });
	assert.deepEqual(fieldsOf(recs[1]), { session_id: t.sid, file_path: path.join(t.cwd, "existing.ts"), lines: 130, new_file: false });
});

test("ledger: retained {session_id, reason} when /pilead:route opens the gate; bad usage logs nothing", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	await t.commands.get("pilead:route")!.handler("", t.ctx);
	assert.deepEqual(readLedger(t.home), []);
	await t.commands.get("pilead:route")!.handler('retain "schema bump"', t.ctx);
	const recs = readLedger(t.home);
	assert.equal(recs.length, 1);
	assert.equal(recs[0].event, "retained");
	assert.deepEqual(fieldsOf(recs[0]), { session_id: t.sid, reason: "schema bump" });
});

test("ledger: reported {session_id, packet} once per report version, only when the lead is notified", async () => {
	const t = setup("executor");
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "packet-0002.md"), "p2");
	const report = path.join(t.sessDir, "report-0002.md");
	fs.writeFileSync(report, "v1");
	await t.fire("agent_settled", {}, t.ctx); // lead not registered yet: nobody notified, nothing logged
	assert.deepEqual(readLedger(t.home), []);
	registerLead(t.home, t.leadSid);
	await t.fire("agent_settled", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx); // unchanged report: no second record
	let recs = readLedger(t.home);
	assert.equal(recs.length, 1);
	assert.equal(recs[0].event, "reported");
	assert.deepEqual(fieldsOf(recs[0]), { session_id: t.sid, packet: 2 });
	fs.writeFileSync(report, "v2");
	const later = new Date(Date.now() + 5000);
	fs.utimesSync(report, later, later);
	await t.fire("agent_settled", {}, t.ctx);
	recs = readLedger(t.home);
	assert.deepEqual(recs.map((r: any) => fieldsOf(r)), [{ session_id: t.sid, packet: 2 }, { session_id: t.sid, packet: 2 }]);
});

test("appendLedger is best-effort: an unwritable ledger never throws and the gate still decides", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	fs.mkdirSync(path.join(t.home, "ledger.jsonl"), { recursive: true }); // a directory where the file belongs
	assert.doesNotThrow(() => ext.appendLedger(t.home, "x", { session_id: "s" }));
	await t.fire("session_start", {}, t.ctx);
	const r = await t.fire("tool_call", { toolName: "write", input: { path: "new.ts", content: "x" } }, t.ctx);
	assert.equal(r?.block, true);
	await t.commands.get("pilead:route")!.handler("retain ok", t.ctx);
	assert.equal(t.notes.at(-1)?.type, "info");
});

test("loadConfig: out-of-range numbers fall back per key; in-range values and poll_interval 0 are kept", () => {
	const home = path.join(SANDBOX, "cfg-ranges");
	const def = { ...ext.CONFIG_DEFAULTS };
	const bad: [string, number][] = [
		["edit_line_threshold", 0], ["edit_line_threshold", -5], ["edit_line_threshold", 12.5],
		["grace_seconds", 0.5], ["grace_seconds", 0], ["grace_seconds", -60],
		["poll_interval", 0.5], ["poll_interval", -3],
	];
	for (const [k, v] of bad) {
		writeConfig(home, { [k]: v, block_on_new_file: false });
		const cfg = ext.loadConfig(home);
		assert.equal(cfg[k], def[k], `${k}=${v} → default`);
		assert.equal(cfg.block_on_new_file, false, "the rest of the file still applies");
	}
	const good: [string, number][] = [
		["edit_line_threshold", 1], ["edit_line_threshold", 40], ["grace_seconds", 1], ["grace_seconds", 90.5],
		["poll_interval", 0], ["poll_interval", 1], ["poll_interval", 2.5],
	];
	for (const [k, v] of good) {
		writeConfig(home, { [k]: v });
		assert.equal(ext.loadConfig(home)[k], v, `${k}=${v} is used`);
	}
});

test("gate: an out-of-range edit_line_threshold or grace_seconds is ignored (the constants apply)", async () => {
	let now = Date.now();
	const t = setup("lead", {}, { now: () => now });
	registerLead(t.home, t.sid);
	writeConfig(t.home, { edit_line_threshold: 0, grace_seconds: -1 });
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(path.join(t.cwd, "existing.ts"), lines(3));
	const write = (n: number) => t.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(n) } }, t.ctx);
	assert.equal(await write(40), undefined);
	assert.equal((await write(41))?.block, true);
	await t.commands.get("pilead:route")!.handler("retain r", t.ctx);
	assert.match(t.notes.at(-1)!.msg, /open for 2 min/);
	now += ext.ROUTE_GRACE_MS - 1;
	assert.equal(await write(500), undefined, "still inside the default 2 min grace");
});

test("poll_interval from config.json drives the fallback poll; 0 disables it", async () => {
	const realSetInterval = globalThis.setInterval;
	const calls: number[] = [];
	(globalThis as any).setInterval = (fn: any, ms: number, ...rest: any[]) => {
		calls.push(ms);
		return realSetInterval(fn, ms, ...rest);
	};
	try {
		const run = async (cfg: unknown) => {
			calls.length = 0;
			const t = setup("lead", {}, { watch: true, pollMs: undefined });
			if (cfg !== undefined) writeConfig(t.home, cfg);
			await t.fire("session_start", {}, t.ctx);
			t.handle.stop();
			return [...calls];
		};
		assert.deepEqual(await run(undefined), [3000], "no file → the 3 s fallback");
		assert.deepEqual(await run({ poll_interval: 2 }), [2000]);
		assert.deepEqual(await run({ poll_interval: 0 }), [], "0 disables the poll");
		assert.deepEqual(await run({ poll_interval: 0.5 }), [3000], "out of range → the default");
	} finally {
		(globalThis as any).setInterval = realSetInterval;
	}
});

test("loadConfig: an oversized integer costs its own key only, as in lib/pilead/config.py", () => {
	const home = path.join(SANDBOX, "cfg-huge");
	const huge = "9".repeat(400); // JSON.parse → Infinity; Python's loader ignores the same key
	for (const key of ["edit_line_threshold", "poll_interval"]) {
		writeConfig(home, `{"${key}": ${huge}, "grace_seconds": 90, "block_on_new_file": false, "executor_layout": "pane"}`);
		assert.deepEqual(ext.loadConfig(home), { edit_line_threshold: 40, block_on_new_file: false, grace_seconds: 90, poll_interval: 3 }, key);
	}
});

// ---- lead ids: one safe form (tests/lead-ids.json, shared with tests/test_lead_ids.py) ------------

const LEAD_IDS: { usable: string[]; not_usable: string[] } = JSON.parse(
	fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "lead-ids.json"), "utf-8"),
);

/** Every file and directory under root, with each file's content and mtime. */
function treeOf(root: string, skip?: string): Map<string, string> {
	const out = new Map<string, string>();
	const walk = (dir: string) => {
		for (const name of fs.readdirSync(dir)) {
			const p = path.join(dir, name);
			if (skip && (p === skip || p.startsWith(skip + path.sep))) continue;
			const st = fs.lstatSync(p);
			if (st.isDirectory()) {
				out.set(p, "dir");
				walk(p);
			} else out.set(p, `${st.mtimeMs} ${fs.readFileSync(p, "base64")}`);
		}
	};
	walk(root);
	return out;
}

/** A home two directories deep in its own dir, so `leads/../../x` still lands inside `root`. */
function deepHome(): { root: string; home: string } {
	const root = path.join(SANDBOX, `lead-ids-${seq++}`);
	const home = path.join(root, "outer", "home");
	fs.mkdirSync(path.join(home, "leads"), { recursive: true });
	return { root, home };
}

const SID_WARNING = "pi-lead: PI_LEAD_SID is not a usable lead id; using this session's own id";

test("validLeadSid agrees with tests/lead-ids.json; non-strings are not usable", () => {
	assert.equal(ext.LEAD_SID_MAX, 128);
	assert.ok(LEAD_IDS.usable.length >= 11 && LEAD_IDS.not_usable.length >= 25);
	assert.ok(LEAD_IDS.usable.includes("a".repeat(128)) && LEAD_IDS.not_usable.includes("a".repeat(129)));
	assert.ok(LEAD_IDS.not_usable.includes("a\u0000b") && LEAD_IDS.not_usable.includes("a\n"));
	for (const s of LEAD_IDS.usable) assert.equal(ext.validLeadSid(s), true, JSON.stringify(s));
	for (const s of LEAD_IDS.not_usable) assert.equal(ext.validLeadSid(s), false, JSON.stringify(s));
	for (const v of [undefined, null, 5, ["a"], { a: 1 }, new String("lead-1")]) assert.equal(ext.validLeadSid(v), false, String(v));
});

test("lead: an unusable PI_LEAD_SID falls back to pi's own id, warns once, and the gate stays on", async () => {
	const { root, home } = deepHome();
	const t = setup("lead", { PI_LEAD_SID: "../../x", PI_LEAD_HOME: home });
	registerLead(home, "lead-7"); // the gate and the route act only for a registered lead
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "lead-7" } };
	const before = treeOf(root, home);
	await t.fire("session_start", {}, ctx);
	await t.fire("session_start", {}, ctx);
	assert.equal(t.notes.filter((n) => n.msg === SID_WARNING && n.type === "warning").length, 1);

	const write = () => t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, ctx);
	assert.equal((await write())?.block, true, "an edit over the threshold is still blocked");

	await t.commands.get("pilead:route")!.handler('retain "r"', ctx);
	assert.match(fs.readFileSync(path.join(home, "leads", "lead-7.route"), "utf-8"), / r\n$/);
	assert.equal(await write(), undefined, "the grace opened for lead-7");
	assert.deepEqual(treeOf(root, home), before, "nothing written outside home");
	assert.ok(!fs.existsSync(path.join(root, "outer", "x.route")));
	assert.equal(t.notes.filter((n) => n.msg === SID_WARNING).length, 1);
});

test("lead: a usable PI_LEAD_SID is the id as today, with no warning", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid); // the route opens a window only for a registered lead
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "lead-own" } };
	await t.fire("session_start", {}, ctx);
	assert.equal(t.notes.filter((n) => n.type === "warning").length, 0);
	await t.commands.get("pilead:route")!.handler("retain r", ctx);
	assert.ok(fs.existsSync(path.join(t.home, "leads", `${t.sid}.route`)));
	assert.ok(!fs.existsSync(path.join(t.home, "leads", "lead-own.route")));
});

test("executor: an unusable PI_LEAD_LEAD reads no record — reported, nothing appended, no marker", async () => {
	const { root, home } = deepHome();
	const t = setup("executor", { PI_LEAD_LEAD: "../../x", PI_LEAD_HOME: home });
	const inbox = path.join(root, "outer", "x.inbox");
	fs.writeFileSync(inbox, "");
	// the record where leads/../../x.json points: a read of it would succeed
	fs.writeFileSync(path.join(home, "leads", "..", "..", "x.json"), JSON.stringify({ sid: "x", inbox }));
	assert.ok(fs.existsSync(path.join(root, "outer", "x.json")), "outside home, inside this test's dir");
	assert.equal(ext.leadInboxPath(home, "../../x"), null);

	const sessDir = path.join(home, "sessions", t.sid);
	fs.mkdirSync(sessDir, { recursive: true });
	fs.writeFileSync(path.join(sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(sessDir, "report-0001.md"), "Done.\nStatus: clean");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(sessDir), "reported");
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
	assert.ok(!fs.existsSync(path.join(sessDir, ".notified")));
});

// ---- session ids: one safe form (tests/session-ids.json, shared with tests/test_session_ids.py) ---

const SESSION_IDS: { usable: string[]; not_usable: string[] } = JSON.parse(
	fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "session-ids.json"), "utf-8"),
);
const EXEC_SID_WARNING = "pi-lead: PI_LEAD_SID is not a usable session id; using this session's own id";
const NO_LEAD_ID_WARNING =
	"pi-lead: this session has no usable lead id (PI_LEAD_SID and the session's own id are both refused); the edit gate is off";
const OWN_ID_WARNING = "pi-lead: this session's id is not a usable lead id; the edit gate is off";

test("validSessionSid agrees with tests/session-ids.json; non-strings are not usable; the lead rule did not move", () => {
	assert.equal(ext.SESSION_SID_MAX, 128);
	assert.ok(SESSION_IDS.usable.includes("a".repeat(128)) && SESSION_IDS.not_usable.includes("a".repeat(129)));
	assert.ok(SESSION_IDS.usable.includes("exec-1.usage") && SESSION_IDS.not_usable.includes("a\u0000b"));
	for (const s of SESSION_IDS.usable) assert.equal(ext.validSessionSid(s), true, JSON.stringify(s));
	for (const s of SESSION_IDS.not_usable) assert.equal(ext.validSessionSid(s), false, JSON.stringify(s));
	for (const v of [undefined, null, 5, ["a"], { a: 1 }, new String("s1")]) assert.equal(ext.validSessionSid(v), false, String(v));
	for (const s of LEAD_IDS.usable) assert.equal(ext.validLeadSid(s), true, JSON.stringify(s));
	for (const s of LEAD_IDS.not_usable) assert.equal(ext.validLeadSid(s), false, JSON.stringify(s));
	assert.equal(ext.validLeadSid("exec-1.usage"), false);
	assert.equal(ext.validSessionSid("exec-1.usage"), true);
});

test("executor: an unusable PI_LEAD_SID falls back to pi's own id, warns once, and writes only inside home", async () => {
	const { root, home } = deepHome();
	const t = setup("executor", { PI_LEAD_SID: "../../x", PI_LEAD_HOME: home });
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "exec-7" } };
	const sessDir = path.join(home, "sessions", "exec-7");
	const outside = treeOf(root, home);
	await t.fire("session_start", {}, ctx);
	await t.fire("session_start", {}, ctx);
	assert.equal(t.notes.filter((n) => n.msg === EXEC_SID_WARNING && n.type === "warning").length, 1);
	assert.equal(t.notes.length, 1);

	await t.fire("before_agent_start", { prompt: "go" }, ctx);
	assert.equal(status(sessDir), "busy");
	assert.deepEqual(treeOf(root, home), outside, "no file outside home");
	assert.ok(!fs.existsSync(path.join(root, "outer", "x")));

	fs.writeFileSync(path.join(sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(sessDir, "report-0001.md"), "Done.\nStatus: clean");
	await t.fire("agent_settled", {}, ctx);
	assert.equal(status(sessDir), "reported");
	assert.deepEqual(treeOf(root, home), outside, "still no file outside home");
});

test("executor: with PI_LEAD_SID and pi's own id both unusable there is no id — nothing written, the fence still on", async () => {
	const { root, home } = deepHome();
	const t = setup("executor", { PI_LEAD_SID: "../../x", PI_LEAD_HOME: home });
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "a..b" } };
	const before = treeOf(root);
	await t.fire("session_start", {}, ctx);
	await t.fire("before_agent_start", { prompt: "go" }, ctx);
	fs.mkdirSync(path.join(root, "outer", "x"), { recursive: true }); // where the id points: still nothing written there
	const withX = treeOf(root);
	await t.fire("agent_settled", {}, ctx);
	await t.fire("session_shutdown", { reason: "quit" }, ctx);
	assert.deepEqual(treeOf(root), withX, "no file written anywhere");
	withX.delete(path.join(root, "outer", "x"));
	assert.deepEqual(withX, before);
	assert.equal(t.userSent.length, 0, "nothing drained");

	const r = await t.fire("tool_call", { toolName: "bash", input: { command: "git commit -m x" } }, ctx);
	assert.equal(r?.block, true, "the git fence blocks as before");
	assert.match(r.reason, /blocked: git commit -m x/);
});

test("executor: a usable PI_LEAD_SID is the id as today, with no warning", async () => {
	const t = setup("executor");
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "pi-own-1" } };
	await t.fire("session_start", {}, ctx);
	await t.fire("before_agent_start", { prompt: "go" }, ctx);
	assert.equal(t.notes.filter((n) => n.type === "warning").length, 0);
	assert.equal(status(t.sessDir), "busy");
	assert.equal(t.sessDir, path.join(t.home, "sessions", t.sid));
	assert.ok(!fs.existsSync(path.join(t.home, "sessions", "pi-own-1")));
});

test("lead: PI_LEAD_SID and pi's own id both unusable — one true warning, and no lead id (the gate is off, as before)", async () => {
	const { root, home } = deepHome();
	const t = setup("lead", { PI_LEAD_SID: "../../x", PI_LEAD_HOME: home });
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "a..b" } };
	const before = treeOf(root);
	await t.fire("session_start", {}, ctx);
	await t.fire("session_start", {}, ctx);
	assert.deepEqual(t.notes, [{ msg: NO_LEAD_ID_WARNING, type: "warning" }]);
	assert.equal(t.notes.filter((n) => n.msg === SID_WARNING).length, 0, "the untrue text is gone");
	const r = await t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, ctx);
	assert.equal(r, undefined, "no lead id: the gate does what it did at 5ecd5cd");
	assert.deepEqual(treeOf(root), before);
});

test("lead: PI_LEAD_SID not set and pi's own id unusable — one warning, and no lead id (the gate is off, as before)", async () => {
	const { root, home } = deepHome();
	const t = setup("lead", { PI_LEAD_SID: "", PI_LEAD_HOME: home });
	const ctx = { ...t.ctx, sessionManager: { getSessionId: () => "a..b" } };
	const before = treeOf(root);
	await t.fire("session_start", {}, ctx);
	await t.fire("session_start", {}, ctx);
	assert.deepEqual(t.notes, [{ msg: OWN_ID_WARNING, type: "warning" }]);
	const r = await t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, ctx);
	assert.equal(r, undefined, "no lead id: the gate does what it did at 5ecd5cd");
	assert.deepEqual(treeOf(root), before);
});

// ---- executor: the next queued packet after a settle ---------------------------------------------

/** A stub `pilead` (sh) for `queue <sid> --deliver --trigger settle --home <home>`: each call appends
 *  `args: …`, the session's status file and the lead inbox (both as they are the moment it starts) to
 *  `log`, and `<dir>/pid` holds its own pid. `mode`: `ok` prints one line and exits 0, `fail` exits 2,
 *  `silent` prints nothing, `hang` sleeps 30 s (in the background under `wait`; SIGTERM kills both). */
function stubQueuePilead(mode: "ok" | "fail" | "silent" | "hang", leadInbox = "/nonexistent"): { bin: string; log: string; dir: string } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "stub-queue-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	const q = JSON.stringify;
	fs.writeFileSync(
		bin,
		`#!/bin/sh\ntrap 'kill $! 2>/dev/null; exit 143' TERM\n` +
			`echo $$ > ${q(dir)}/pid\n` +
			`{ printf 'args:'; for a in "$@"; do printf ' %s' "$a"; done; printf '\\n'; ` +
			`printf 'status: %s\\n' "$(cat "$7/sessions/$2/status" 2>/dev/null)"; ` +
			`printf 'inbox: %s\\n' "$(cat ${q(leadInbox)} 2>/dev/null)"; } >> ${q(log)}\n` +
			`case ${mode} in hang) sleep 30 & echo $! > ${q(dir)}/child; wait $! ;; fail) exit 2 ;; silent) exit 0 ;; esac\n` +
			`echo "delivered queued packet #1"\n`,
	);
	fs.chmodSync(bin, 0o755);
	return { bin, log, dir };
}

const callLog = (log: string): string[] => (fs.existsSync(log) ? fs.readFileSync(log, "utf-8").split("\n").filter(Boolean) : []);
const argLines = (log: string): string[] => callLog(log).filter((l) => l.startsWith("args:"));

/** An executor whose current packet (0001) exists, with sessions/<sid>/queue.json present unless `queue` is false. */
function queuedExecutor(bin: string, { queue = true, report = true } = {}) {
	const t = setup("executor", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.leadSid);
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	if (report) fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "Done.\nStatus: clean");
	if (queue) fs.writeFileSync(path.join(t.sessDir, "queue.json"), JSON.stringify({ next_id: 2, items: [] }));
	return { ...t, inbox };
}

test("executor: with queue.json, a settle runs pilead queue --deliver once — after the status and the lead's line", async () => {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "q-inbox-"));
	// the lead inbox path is known only after setup; the stub reads it through a symlink made below
	const link = path.join(dir, "lead-inbox");
	const { bin, log } = stubQueuePilead("ok", link);
	const t = queuedExecutor(bin);
	fs.symlinkSync(t.inbox, link);
	await t.fire("agent_settled", {}, t.ctx);
	await until(() => callLog(log).length >= 3, "the stub's call");
	await sleep(200);
	const report = path.join(t.sessDir, "report-0001.md");
	assert.deepEqual(callLog(log), [
		`args: queue ${t.sid} --deliver --trigger settle --home ${t.home}`,
		"status: reported",
		`inbox: reported ${t.sid} ${report}`,
	]);
	assert.equal(status(t.sessDir), "reported");
	assert.deepEqual([t.sent.length, t.userSent.length], [0, 0], "the extension sends nothing about a delivery");
});

test("executor: a settle with no report runs it too — the status is idle when it starts", async () => {
	const { bin, log } = stubQueuePilead("ok");
	const t = queuedExecutor(bin, { report: false });
	await t.fire("agent_settled", {}, t.ctx);
	await until(() => callLog(log).length >= 2, "the stub's call");
	assert.deepEqual(callLog(log).slice(0, 2), [`args: queue ${t.sid} --deliver --trigger settle --home ${t.home}`, "status: idle"]);
	assert.equal(status(t.sessDir), "idle");
});

test("executor: without queue.json a settle starts no pilead", async () => {
	const { bin, log } = stubQueuePilead("ok");
	const t = queuedExecutor(bin, { queue: false });
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(400);
	assert.equal(fs.existsSync(log), false);
	assert.equal(status(t.sessDir), "reported");
});

for (const mode of ["fail", "silent", "missing"] as const) {
	test(`executor: a pilead that ${mode === "fail" ? "exits 2" : mode === "silent" ? "prints nothing" : "does not exist"} changes nothing after a settle`, async () => {
		const stub = stubQueuePilead(mode === "missing" ? "ok" : mode);
		const bin = mode === "missing" ? path.join(SANDBOX, "no-such-pilead") : stub.bin;
		const t = queuedExecutor(bin);
		await t.fire("agent_settled", {}, t.ctx); // resolves, nothing thrown
		if (mode !== "missing") await until(() => argLines(stub.log).length === 1, "the stub's call");
		await sleep(300); // the child has ended and its result has been seen
		assert.equal(status(t.sessDir), "reported");
		assert.deepEqual([t.sent.length, t.userSent.length, t.notes.length], [0, 0, 0]);
		// and the next settle may start one again (the failed child is not held on to)
		await t.fire("agent_settled", {}, t.ctx);
		if (mode !== "missing") await until(() => argLines(stub.log).length === 2, "a second call");
		assert.equal(status(t.sessDir), "reported");
	});
}

test("executor: one delivery child at a time; session_shutdown stops it and no later settle starts one", async () => {
	const { bin, log, dir } = stubQueuePilead("hang");
	const t = queuedExecutor(bin);
	await t.fire("agent_settled", {}, t.ctx);
	await until(() => fs.existsSync(path.join(dir, "child")) && fs.readFileSync(path.join(dir, "child"), "utf-8").trim() !== "", "the stub's sleep");
	const stubPidN = Number(fs.readFileSync(path.join(dir, "pid"), "utf-8").trim());
	const childN = Number(fs.readFileSync(path.join(dir, "child"), "utf-8").trim());
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(300);
	assert.equal(argLines(log).length, 1, "a settle while one runs starts none");
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	await until(() => pidGone(stubPidN) && pidGone(childN), "the stub and its sleep to be gone");
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(300);
	assert.equal(argLines(log).length, 1, "none after shutdown");
	assert.equal(status(t.sessDir), "reported");
});

test("lead: agent_settled runs no pilead", async () => {
	const { bin, log } = stubQueuePilead("ok");
	const t = setup("lead", {}, { pileadBin: bin });
	fs.mkdirSync(path.join(t.home, "sessions", t.sid), { recursive: true });
	fs.writeFileSync(path.join(t.home, "sessions", t.sid, "queue.json"), "{}");
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(300);
	assert.equal(fs.existsSync(log), false);
});

test("executor: the REAL bin/pilead delivers a queued packet after the settle that reported", async () => {
	const t = setup("executor"); // pileadBin unset: this package's bin/pilead
	const wt = path.join(SANDBOX, `wt-${t.sid}`);
	fs.mkdirSync(wt, { recursive: true });
	fs.mkdirSync(t.sessDir, { recursive: true });
	const meta = { sid: t.sid, lead: t.leadSid, worktree: wt, topic: "t", model: "m", created: "-", packets: 1, label: t.sid };
	fs.writeFileSync(path.join(t.sessDir, "meta.json"), JSON.stringify(meta));
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "status"), "busy\n");
	const queued = path.join(SANDBOX, `queued-${t.sid}.md`);
	fs.writeFileSync(queued, "GOAL: the queued packet's own text\n");
	const agentDir = path.join(SANDBOX, "pi-agent-real");
	fs.mkdirSync(agentDir, { recursive: true });
	const envBefore = { agent: process.env.PI_CODING_AGENT_DIR, lint: process.env.PILEAD_NO_LINT };
	process.env.PI_CODING_AGENT_DIR = agentDir; // no real pi session log is read
	process.env.PILEAD_NO_LINT = "1";
	try {
		execFileSync("python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(path.join(REPO, "lib"))}); from pilead import queue; queue.enqueue(${JSON.stringify(t.home)}, ${JSON.stringify(t.sid)}, open(${JSON.stringify(queued)}).read(), ${JSON.stringify(queued)})`]);
		fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "Done.\nStatus: clean");
		await t.fire("agent_settled", {}, t.ctx);
		const ledger = path.join(t.home, "ledger.jsonl");
		const delivered = () =>
			fs.existsSync(ledger) && fs.readFileSync(ledger, "utf-8").split("\n").filter(Boolean).map((l) => JSON.parse(l)).some((r) => r.event === "queue_delivered" && r.trigger === "settle");
		await until(delivered, "queue_delivered with the trigger settle", 10_000);
		assert.equal(JSON.parse(fs.readFileSync(path.join(t.sessDir, "meta.json"), "utf-8")).packets, 2);
		assert.match(fs.readFileSync(path.join(t.sessDir, "inbox.md"), "utf-8"), /GOAL: the queued packet's own text/);
		assert.equal(JSON.parse(fs.readFileSync(path.join(t.sessDir, "queue.json"), "utf-8")).items.length, 0);
	} finally {
		for (const [k, v] of [["PI_CODING_AGENT_DIR", envBefore.agent], ["PILEAD_NO_LINT", envBefore.lint]] as const) {
			if (v === undefined) delete process.env[k];
			else process.env[k] = v;
		}
	}
});

// ---- the autonomous posture in the wake message -------------------------------------------------

const STOP_LIST_TEXT =
	"a report with a risk flag, failing tests, or an UNVERIFIED claim that bears on correctness; core logic, ledgers, parity or golden tests, migrations, deploys; an action that cannot be undone or that leaves this machine (a push, a delete, a publish, a message to someone); work that is not in the approved plan; real ambiguity the packet and the plan do not settle";
const AUTONOMOUS_TEXT =
	"You are in AUTONOMOUS MODE: announce, act, and record. Open your reply with '🚦 [pi-lead] — review needed:', say what reported, then take the next step that is clearly inside the approved plan, and announce each autonomous action together with what you would have asked. Stop and ask anyway for any of these: " +
	STOP_LIST_TEXT +
	". Say which one stopped you. Committing an executor's work is permitted without asking only when pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <path> prints AUTO-COMMIT: CLEARED; pass the two attestation flags only when they are true. The verifier clears the automation; it never replaces the review of the diff.";

/** Today's wake for exec-a (no posture): the message compared whole. */
const plainWake = (refs: string) => ({
	message: {
		customType: "pi-lead",
		content: `🚦 [pi-lead] executor exec-a reported: /h/sessions/exec-a/report-0001.md — ${refs}`,
		display: true,
		details: { sid: "exec-a", report: "/h/sessions/exec-a/report-0001.md", refs },
	},
	options: { triggerTurn: true },
});

/** Rewrite the lead's record: `fields` merged into what registerLead wrote, or raw text. */
function setLeadRecord(home: string, leadSid: string, fields: Record<string, unknown> | string | null): void {
	const file = path.join(home, "leads", `${leadSid}.json`);
	if (fields === null) return void fs.rmSync(file, { force: true });
	if (typeof fields === "string") return void fs.writeFileSync(file, fields);
	const rec = JSON.parse(fs.readFileSync(file, "utf-8"));
	fs.writeFileSync(file, JSON.stringify({ ...rec, ...fields }));
}

test("wakeInstruction: empty for false, the autonomous text after two newlines for true", () => {
	assert.equal(ext.wakeInstruction(false), "");
	assert.equal(ext.wakeInstruction(true), "\n\n" + AUTONOMOUS_TEXT);
	assert.equal(ext.AUTONOMOUS_STOP_LIST, STOP_LIST_TEXT);
});

test("wake: a lead whose record has autonomous true gets the instruction and details.autonomous", async () => {
	const t = setup("lead", {}, { refsCheck: () => "refs: stub" });
	const inbox = registerLead(t.home, t.sid);
	setLeadRecord(t.home, t.sid, { autonomous: true, autonomous_source: "command" });
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	const want = plainWake("refs: stub");
	assert.deepEqual(t.sent, [
		{
			message: {
				...want.message,
				content: want.message.content + "\n\n" + AUTONOMOUS_TEXT,
				details: { ...want.message.details, autonomous: true },
			},
			options: { triggerTurn: true },
		},
	]);
});

for (const [name, record] of [
	["autonomous false", { autonomous: false }],
	["no autonomous field", {}],
	['autonomous "yes"', { autonomous: "yes" }],
	["autonomous 1", { autonomous: 1 }],
	["autonomous null", { autonomous: null }],
	["a record that is not valid JSON", "{not json"],
	["a record that is a JSON array", "[true]"],
	["no record", null],
] as [string, Record<string, unknown> | string | null][]) {
	test(`wake: ${name} → the whole message is today's, with no autonomous key`, async () => {
		const t = setup("lead", {}, {
			// the record is changed after the claim (which needs a readable record to find the inbox) and
			// before the send: what the send reads is what counts
			refsCheck: () => {
				setLeadRecord(t.home, t.sid, record);
				return "refs: stub";
			},
		});
		const inbox = registerLead(t.home, t.sid);
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
		assert.equal(t.handle.drainInbox(), 1);
		await t.handle.flush();
		if (record === null) {
			// p22b: a wake is never sent by a session that is not registered; with no forward note it goes back
			assert.deepEqual(t.sent, []);
			assert.equal(fs.readFileSync(inbox, "utf-8"), "reported exec-a /h/sessions/exec-a/report-0001.md\n");
			return;
		}
		assert.deepEqual(t.sent, [plainWake("refs: stub")]);
		assert.equal("autonomous" in t.sent[0].message.details, false);
	});
}

test("wake: the posture is read when the wake is sent — a record changed between two wakes changes the second only", async () => {
	let next: Record<string, unknown> | undefined;
	const t = setup("lead", {}, {
		refsCheck: () => {
			if (next) setLeadRecord(t.home, t.sid, next);
			return "refs: stub";
		},
	});
	const inbox = registerLead(t.home, t.sid);
	setLeadRecord(t.home, t.sid, { autonomous: true });
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	setLeadRecord(t.home, t.sid, { autonomous: false });
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	next = { autonomous: true }; // turned on after the claim, before the send
	setLeadRecord(t.home, t.sid, { autonomous: false });
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	assert.equal(t.sent.length, 3);
	assert.equal(t.sent[0].message.content, plainWake("refs: stub").message.content + "\n\n" + AUTONOMOUS_TEXT);
	assert.equal(t.sent[0].message.details.autonomous, true);
	assert.deepEqual(t.sent[1], plainWake("refs: stub"));
	assert.equal(t.sent[2].message.details.autonomous, true);
});

// ---- the gate: only a registered lead, the three control files, the route description ------------

const NOT_REGISTERED = "pi-lead: this session is not a registered lead — the gate is off; run pilead lead-start to register";
const controlReason = (p: string, kind: string, grace = "2 min") =>
	`pi-lead lead gate: ${p} is a pi-lead control file (${kind}) — a change to it needs a human's word; ask, or \`/pilead:route retain "<reason>"\` and retry within ${grace}.`;

test("registration: an unregistered session is not gated; lead-start turns the gate on, removing the record turns it off", async () => {
	const t = setup("lead");
	fs.writeFileSync(path.join(t.cwd, "existing.ts"), lines(3));
	const newFile = () => t.fire("tool_call", { toolName: "write", input: { path: "src/new.ts", content: "x\n" } }, t.ctx);
	const big = () => t.fire("tool_call", { toolName: "write", input: { path: "existing.ts", content: lines(500) } }, t.ctx);
	assert.equal(await newFile(), undefined);
	assert.equal(await big(), undefined);
	assert.deepEqual(readLedger(t.home), [], "nothing is ledgered for a session that is not registered");

	registerLead(t.home, t.sid);
	assert.equal((await newFile())?.block, true);
	assert.equal((await big())?.block, true);
	assert.equal(readLedger(t.home).length, 2);

	const record = path.join(t.home, "leads", `${t.sid}.json`);
	fs.rmSync(record);
	assert.equal(await newFile(), undefined, "pilead stop: off at the next call");
	assert.equal(await big(), undefined);
	assert.equal(readLedger(t.home).length, 2);

	fs.writeFileSync(record, "{broken");
	assert.equal((await big())?.block, true, "a record that cannot be parsed still gates");
	assert.equal(ext.isRegisteredLead(t.home, t.sid), true);

	assert.equal(ext.isRegisteredLead(path.join(SANDBOX, "no-such-home", "deeper"), t.sid), false);
	assert.equal(ext.isRegisteredLead(t.home, "../x"), false, "an unusable id names no file");
	fs.mkdirSync(path.join(t.home, "leads", "lead-dir.json"), { recursive: true });
	assert.equal(ext.isRegisteredLead(t.home, "lead-dir"), false, "a directory is not a record");
});

test("/pilead:route retain in a session that is not registered: a warning, no grace file, no ledger event", async () => {
	const t = setup("lead");
	await t.commands.get("pilead:route")!.handler("retain x", t.ctx);
	assert.deepEqual(t.notes, [{ msg: NOT_REGISTERED, type: "warning" }]);
	assert.ok(!fs.existsSync(path.join(t.home, "leads", `${t.sid}.route`)));
	assert.deepEqual(readLedger(t.home), []);
	await t.commands.get("pilead:route")!.handler("", t.ctx);
	assert.equal(t.notes.at(-1)!.msg, 'usage: /pilead:route retain "<why this edit is lead work>"', "the usage message comes first");
});

test("/pilead:status prints gate: on (registered) and gate: off (not registered)", async () => {
	const t = setup("lead");
	const status = async () => {
		await t.commands.get("pilead:status")!.handler("", t.ctx);
		return t.notes.at(-1)!.msg.split("\n");
	};
	let msg = await status();
	assert.equal(msg[msg.findIndex((l: string) => l.startsWith("route grace:")) + 1], "gate: off (not registered)");
	registerLead(t.home, t.sid);
	msg = await status();
	assert.equal(msg[msg.findIndex((l: string) => l.startsWith("route grace:")) + 1], "gate: on (registered)");
});

test("control files: config.json, ledger.jsonl and leads/*.route are gated at any size for a registered lead", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	writeConfig(t.home, { poll_interval: 3 });
	const call = (toolName: string, input: any) => t.fire("tool_call", { toolName, input }, t.ctx);
	const route = path.join(t.home, "leads", `${t.sid}.route`);
	const link = path.join(SANDBOX, `home-link-${seq++}`);
	fs.symlinkSync(t.home, link);
	const tilde = `~/${path.relative(SANDBOX, t.home)}`;
	const cases: [string, string, any, string][] = [];
	for (const base of [t.home, link, tilde]) {
		cases.push(
			[`${base}/config.json`, "edit", { edits: [{ oldText: '"poll_interval": 3', newText: '"poll_interval": 3' }] }, "settings"],
			[`${base}/ledger.jsonl`, "write", { content: "{}\n" }, "ledger"],
			[`${base}/leads/${t.sid}.route`, "write", { content: "now\n" }, "grace file"],
		);
	}
	for (const [p, tool, input, kind] of cases) {
		const before = readLedger(t.home).length;
		const r = await call(tool, { path: p, ...input });
		assert.equal(r?.block, true, p);
		assert.equal(r.reason, controlReason(p, kind));
		const recs = readLedger(t.home);
		assert.equal(recs.length, before + 1);
		const abs = ext.resolveToolPath(p, t.cwd);
		assert.deepEqual(fieldsOf(recs.at(-1)), {
			session_id: t.sid, file_path: abs, lines: 1, new_file: tool === "write" && !fs.existsSync(abs), control: kind,
		});
		assert.equal(recs.at(-1).event, "blocked");
	}
	assert.equal(ext.controlFileKind(path.join(t.home, "leads", "anyone.route"), t.home), "grace file");
	assert.equal(ext.controlFileKind(path.join(t.home, "leads", "x.inbox.md"), t.home), null);
	assert.equal(ext.controlFileKind(path.join(t.home, "leads", "sub", "x.route"), t.home), null);
	assert.equal(ext.controlFileKind(path.join(t.home, "sessions", "config.json"), t.home), null);
	assert.equal(ext.controlFileKind(path.join(t.home, "CONFIG.JSON"), t.home), "settings", "however the name is cased");
	assert.equal(ext.controlFileKind(path.join(t.home, "LEADS", "X.ROUTE"), t.home), "grace file");
	const upper = await call("write", { path: path.join(t.home, "Ledger.JSONL"), content: "x\n" });
	assert.equal(upper?.reason, controlReason(path.join(t.home, "Ledger.JSONL"), "ledger"));
	// the rest of home stays exempt
	for (const p of [path.join(t.home, "sessions", "e", "packet-0002.md"), path.join(t.home, "leads", "x.inbox.md"), path.join(t.home, "notes.md")]) {
		assert.equal(await call("write", { path: p, content: lines(300) }), undefined, p);
	}
	// an open grace window lets the three through
	await t.commands.get("pilead:route")!.handler('retain "fix the config"', t.ctx);
	assert.ok(fs.existsSync(route));
	for (const [p, tool, input] of cases) assert.equal(await call(tool, { path: p, ...input }), undefined, p);
	// a session that is not registered is not blocked
	const u = setup("lead");
	writeConfig(u.home, { poll_interval: 3 });
	for (const p of [path.join(u.home, "config.json"), path.join(u.home, "ledger.jsonl"), path.join(u.home, "leads", `${u.sid}.route`)]) {
		assert.equal(await u.fire("tool_call", { toolName: "write", input: { path: p, content: "x\n" } }, u.ctx), undefined, p);
	}
});

test("the /pilead:route description names the grace config.json gives when the command is registered", () => {
	const describe = (cfg?: unknown) => {
		const home = path.join(SANDBOX, `desc-home-${seq++}`);
		if (cfg !== undefined) writeConfig(home, cfg);
		const stub = stubPi();
		ext.createPiLead(stub.pi, { env: { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: "lead-desc" }, watch: false, pollMs: 0 });
		return (stub.commands.get("pilead:route") as any).description;
	};
	assert.equal(describe(), 'Open the lead routing gate for 2 min: /pilead:route retain "<reason>"');
	assert.equal(describe({ grace_seconds: 90 }), 'Open the lead routing gate for 90 s: /pilead:route retain "<reason>"');
	assert.equal(describe("{not json"), 'Open the lead routing gate for 2 min: /pilead:route retain "<reason>"');
});

// ---- a pi that is not the session's own does no lead (or executor) work ---------------------------

test("quietMode: only print and json are quiet", () => {
	for (const m of ["print", "json"]) assert.equal(ext.quietMode(m), true, m);
	for (const m of ["tui", "rpc", "PRINT", undefined, null, 7]) assert.equal(ext.quietMode(m), false, String(m));
});

test("heldByAnother: only a decimal text naming another process id > 0", () => {
	const own = 4242;
	assert.equal(ext.heldByAnother(undefined, own), false);
	assert.equal(ext.heldByAnother("", own), false);
	assert.equal(ext.heldByAnother(String(own), own), false);
	assert.equal(ext.heldByAnother("999", own), true);
	for (const v of ["0", "-5", "12x", " 12", "1e3", 999, null]) assert.equal(ext.heldByAnother(v, own), false, String(v));
});

const REPORTED_LINE = "reported exec-q /h/sessions/exec-q/report-0001.md\n";
const HELD_LEAD_NOTICE = (pid: string) =>
	`pi-lead: this pi was started from inside a pi-lead session (process ${pid}), so it does no lead work: the lead's inbox is left to the lead's own session.`;
const HELD_EXEC_NOTICE = (pid: string) =>
	`pi-lead: this pi was started from inside a pi-lead session (process ${pid}), so it does no executor work: the executor's inbox is left to the executor's own session.`;
const RUN_NOTICE = (mode: string) =>
	`pi-lead: this is a ${mode} run, so it does no lead work: the lead's inbox is left to the lead's own session.`;

/** One extension instance over an environment object the test owns (so it can read the marker back). */
function withEnv(env: Record<string, string | undefined>, opts: any = {}, mode?: unknown) {
	const stub = stubPi();
	const notes: { msg: string; type: string }[] = [];
	const cwd = fs.mkdtempSync(path.join(SANDBOX, "cwd-"));
	const ctx: any = {
		cwd,
		hasUI: true,
		sessionManager: { getSessionId: () => env.PI_LEAD_SID },
		ui: { notify: (msg: string, type: string) => void notes.push({ msg, type }) },
	};
	if (mode !== undefined) ctx.mode = mode;
	const handle = ext.createPiLead(stub.pi, { env, watch: false, pollMs: 0, refsCheck: () => "refs: stub", ...opts });
	return { ...stub, handle, ctx, notes };
}

/** A registered lead's home with one reported line waiting in its inbox. */
function waitingLead() {
	const home = path.join(SANDBOX, `quiet-home-${seq++}`);
	const sid = `lead-q${seq}`;
	const inbox = registerLead(home, sid);
	fs.writeFileSync(inbox, REPORTED_LINE);
	return { home, sid, inbox };
}

/** Run a lead through session_start, a poll and session_shutdown; assert it did no lead work. */
async function assertQuietLead(t: ReturnType<typeof withEnv>, home: string, inbox: string, notice: string) {
	const before = treeOf(home);
	await t.fire("session_start", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE, "after session_start");
	assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE, "after a poll");
	await t.fire("session_start", {}, t.ctx);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE, "after session_shutdown");
	assert.deepEqual(t.sent, [], "no message sent");
	assert.deepEqual(t.userSent, []);
	assert.deepEqual(treeOf(home), before, "home byte-identical: no claim file, no ledger line");
	assert.deepEqual(t.notes, [{ msg: notice, type: "info" }], "the notice, once");
	assert.equal(t.handle.leadIsQuiet(), true);
}

test("a registered lead in a print or json run does no lead work and says so once", async () => {
	for (const mode of ["print", "json"]) {
		const { home, sid, inbox } = waitingLead();
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid };
		const t = withEnv(env, { pid: 4242 }, mode);
		assert.equal(t.handle.leadIsQuiet(), false, "the mode is known from session_start on");
		await assertQuietLead(t, home, inbox, RUN_NOTICE(mode));
		assert.equal(env.PI_LEAD_HELD_BY, "4242", "the marker is set in either case");
	}
});

test("a lead whose marker names another process does no lead work, in tui mode and with no mode", async () => {
	for (const mode of ["tui", undefined]) {
		const { home, sid, inbox } = waitingLead();
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" };
		const t = withEnv(env, { pid: 4242 }, mode);
		assert.equal(t.handle.leadIsQuiet(), true, "known when the factory runs");
		await assertQuietLead(t, home, inbox, HELD_LEAD_NOTICE("999"));
		assert.equal(env.PI_LEAD_HELD_BY, "4242", "afterwards the environment holds this process's id");
	}
	// both conditions: the marker's notice is the one shown
	const { home, sid, inbox } = waitingLead();
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242 }, "print");
	await assertQuietLead(t, home, inbox, HELD_LEAD_NOTICE("999"));
});

test("after quiet runs, the lead's own session claims the line and wakes once — in every mode, with any marker that is not another's", async () => {
	const { home, sid, inbox } = waitingLead();
	await assertQuietLead(withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid }, { pid: 4242 }, "print"), home, inbox, RUN_NOTICE("print"));
	await assertQuietLead(
		withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242 }),
		home, inbox, HELD_LEAD_NOTICE("999"),
	);
	const variants: [unknown, string | undefined][] = [
		["tui", undefined], ["rpc", undefined], [undefined, undefined], ["tui", "4242"], ["tui", ""], [undefined, "12x"],
	];
	for (const [mode, marker] of variants) {
		if (fs.readFileSync(inbox, "utf-8") === "") fs.writeFileSync(inbox, REPORTED_LINE);
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid };
		if (marker !== undefined) env.PI_LEAD_HELD_BY = marker;
		const t = withEnv(env, { pid: 4242 }, mode);
		await t.fire("session_start", {}, t.ctx);
		await t.handle.flush();
		const what = `mode ${String(mode)}, marker ${JSON.stringify(marker)}`;
		assert.equal(t.handle.leadIsQuiet(), false, what);
		assert.equal(t.sent.length, 1, what);
		assert.equal(t.sent[0].message.content, "🚦 [pi-lead] executor exec-q reported: /h/sessions/exec-q/report-0001.md — refs: stub", what);
		assert.equal(fs.readFileSync(inbox, "utf-8"), "", what);
		assert.deepEqual(t.notes, [], what);
		assert.equal(env.PI_LEAD_HELD_BY, "4242", what);
	}
	// a second run of the factory in the same process with the same environment object (pi's reload) is not quiet
	fs.writeFileSync(inbox, REPORTED_LINE);
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid };
	withEnv(env, { pid: 4242 }, "tui");
	const again = withEnv(env, { pid: 4242 }, "tui");
	await again.fire("session_start", {}, again.ctx);
	await again.handle.flush();
	assert.equal(again.handle.leadIsQuiet(), false);
	assert.equal(again.sent.length, 1);
	// while a pi started from its shell (a copy of that environment, another pid) is quiet
	fs.writeFileSync(inbox, REPORTED_LINE);
	const child = withEnv({ ...env }, { pid: 5151 }, "tui");
	assert.equal(child.handle.leadIsQuiet(), true);
	await child.fire("session_start", {}, child.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
	assert.deepEqual(child.notes, [{ msg: HELD_LEAD_NOTICE("4242"), type: "info" }]);
});

test("a quiet registered lead still refuses a large edit with today's reason; /pilead:status prints each lead work line", async () => {
	const statusLines = async (t: ReturnType<typeof withEnv>) => {
		await t.commands.get("pilead:status")!.handler("", t.ctx);
		return t.notes.at(-1)!.msg.split("\n");
	};
	const cases: [unknown, string | undefined, string][] = [
		["tui", undefined, "lead work: on"],
		["print", undefined, "lead work: off (print run)"],
		["json", undefined, "lead work: off (json run)"],
		["tui", "999", "lead work: off (started from inside a pi-lead session, process 999)"],
	];
	for (const [mode, marker, want] of cases) {
		const { home, sid } = waitingLead();
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid };
		if (marker !== undefined) env.PI_LEAD_HELD_BY = marker;
		const t = withEnv(env, { pid: 4242 }, mode);
		await t.fire("session_start", {}, t.ctx);
		const got = await statusLines(t);
		assert.equal(got[got.indexOf("gate: on (registered)") + 1], want);
		assert.equal(got.indexOf("gate: on (registered)"), got.findIndex((l: string) => l.startsWith("route grace:")) + 1);
		const r = await t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, t.ctx);
		assert.equal(r?.block, true, want);
		assert.equal(
			r.reason,
			"pi-lead lead gate: this write of big.ts (new file, 500 lines) is implementation work — delegate this (pilead spawn/send) or " +
				'`/pilead:route retain "<reason>"` and retry within 2 min. (A write made through bash meets the same gate where its shape can be read.)',
		);
	}
});

/** An executor's home: a packet waiting in its inbox, a current packet with a report, its lead registered. */
function waitingExecutor() {
	const home = path.join(SANDBOX, `quiet-exec-${seq++}`);
	const sid = `topic-q${seq}`;
	const leadSid = `lead-of-q${seq}`;
	const leadInbox = registerLead(home, leadSid);
	const dir = path.join(home, "sessions", sid);
	fs.mkdirSync(dir, { recursive: true });
	fs.writeFileSync(path.join(dir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(dir, "report-0001.md"), "Done.\nStatus: clean");
	const inbox = path.join(dir, "inbox.md");
	fs.writeFileSync(inbox, "Follow-up packet\n");
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "executor", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_LEAD: leadSid };
	return { home, sid, dir, inbox, leadInbox, env };
}

test("an executor in a print run is an executor as today, and sets the marker", async () => {
	const x = waitingExecutor();
	const t = withEnv(x.env, { pid: 4242 }, "print");
	await t.fire("session_start", {}, t.ctx);
	assert.deepEqual(t.userSent, [{ content: "Follow-up packet", options: { deliverAs: "followUp" } }], "its packet is claimed");
	await t.fire("before_agent_start", { prompt: "go" }, t.ctx);
	assert.equal(status(x.dir), "busy");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(status(x.dir), "reported");
	assert.equal(fs.readFileSync(x.leadInbox, "utf-8"), `reported ${x.sid} ${path.join(x.dir, "report-0001.md")}\n`);
	assert.equal(x.env.PI_LEAD_HELD_BY, "4242");
	assert.equal(t.handle.leadIsQuiet(), false);
	assert.deepEqual(t.notes, []);
});

test("an executor whose marker names another process does no executor work; with any other marker it works as today", async () => {
	const x = waitingExecutor();
	x.env.PI_LEAD_HELD_BY = "999";
	const t = withEnv(x.env, { pid: 4242 }, "tui");
	const before = treeOf(x.home);
	await t.fire("session_start", {}, t.ctx);
	assert.equal(t.handle.drainInbox(), 0);
	await t.fire("before_agent_start", { prompt: "go" }, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.fire("session_start", {}, t.ctx);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.equal(fs.readFileSync(x.inbox, "utf-8"), "Follow-up packet\n", "the executor's inbox, byte for byte");
	assert.deepEqual(t.userSent, []);
	assert.deepEqual(treeOf(x.home), before, "no status, claim file, ledger line or lead-inbox line");
	assert.deepEqual(t.notes, [{ msg: HELD_EXEC_NOTICE("999"), type: "info" }]);
	assert.equal(x.env.PI_LEAD_HELD_BY, "4242");
	assert.equal(t.handle.leadIsQuiet(), true);
	const r = await t.fire("tool_call", { toolName: "bash", input: { command: "git commit -m x" } }, t.ctx);
	assert.equal(r?.block, true, "the git fence is a gate: it stays on");

	for (const marker of [undefined, "4242", "", "12x"]) {
		const y = waitingExecutor();
		if (marker !== undefined) y.env.PI_LEAD_HELD_BY = marker;
		const u = withEnv(y.env, { pid: 4242 });
		await u.fire("session_start", {}, u.ctx);
		assert.deepEqual(u.userSent, [{ content: "Follow-up packet", options: { deliverAs: "followUp" } }], String(marker));
		await u.fire("before_agent_start", { prompt: "go" }, u.ctx);
		assert.equal(status(y.dir), "busy", String(marker));
		assert.deepEqual(u.notes, []);
	}
});

test("/pilead:status in the executor role prints each executor work line", async () => {
	for (const [marker, want] of [
		[undefined, "executor work: on"],
		["999", "executor work: off (started from inside a pi-lead session, process 999)"],
	] as const) {
		const x = waitingExecutor();
		if (marker !== undefined) x.env.PI_LEAD_HELD_BY = marker;
		const t = withEnv(x.env, { pid: 4242 });
		await t.commands.get("pilead:status")!.handler("", t.ctx);
		assert.ok(t.notes.at(-1)!.msg.split("\n").includes(want), t.notes.at(-1)!.msg);
	}
});

// ---- wake delivery: claim files kept, orphans recovered, put-back order, failures reported -----------

/** A promise and its resolver. */
function deferred<T>() {
	let resolve!: (v: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

/** The pid of a process that has ended. */
function endedPid(): number {
	const pid = spawnSync("true").pid!;
	assert.ok(pidGone(pid), `pid ${pid} has ended`);
	return pid;
}

/** The claim files (not `.tmp`) beside an inbox, with their text. */
function claimsOf(inbox: string): Map<string, string> {
	const out = new Map<string, string>();
	for (const n of fs.readdirSync(path.dirname(inbox))) {
		if (n.startsWith(`${path.basename(inbox)}.claim-`) && !n.endsWith(".tmp")) out.set(n, fs.readFileSync(path.join(path.dirname(inbox), n), "utf-8"));
	}
	return out;
}
const tmpLeftovers = (inbox: string) => fs.readdirSync(path.dirname(inbox)).filter((n) => n.endsWith(".tmp") || n.includes(".tmp-"));
const events = (home: string, event: string) => readLedger(home).filter((r: any) => r.event === event).map(fieldsOf);

/** A registered lead whose refs lines come from `refs` (sid → line or promise). */
function leadWith(refs: (s: string) => string | Promise<string>, opts: any = {}) {
	const t = setup("lead", {}, { refsCheck: (_h: string, s: string) => refs(s), ...opts });
	const inbox = registerLead(t.home, t.sid);
	return { ...t, inbox };
}
const stubRefs = (s: string) => `refs: stub for ${s}`;
const sids = (t: { sent: { message: any }[] }) => t.sent.map((x) => x.message.details.sid);

test("wake: while a wake waits for its refs line one claim file holds it; once handed over no claim or .tmp is left", async () => {
	const d = deferred<string>();
	const t = leadWith(() => d.promise);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
	assert.deepEqual([...claimsOf(t.inbox).values()], ["reported exec-a /r/report-0001.md\n"]);
	assert.match([...claimsOf(t.inbox).keys()][0], new RegExp(`^${t.sid}\\.inbox\\.claim-${process.pid}-\\d+-\\d+$`));
	d.resolve("refs: late");
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.deepEqual(claimLeftovers(t.inbox), []);
	assert.deepEqual(tmpLeftovers(t.inbox), []);
});

test("wake: a batch of three, the second still waiting — after the first was handed over the claim holds lines two and three", async () => {
	const d = deferred<string>();
	const t = leadWith((s) => (s === "exec-b" ? d.promise : stubRefs(s)));
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/a.md\nreported exec-b /r/b.md\nreported exec-c /r/c.md\n");
	assert.equal(t.handle.drainInbox(), 3);
	await until(() => t.sent.length === 1, "exec-a to be handed over");
	await sleep(20);
	assert.deepEqual([...claimsOf(t.inbox).values()], ["reported exec-b /r/b.md\nreported exec-c /r/c.md\n"]);
	d.resolve("refs: b");
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-a", "exec-b", "exec-c"]);
	assert.deepEqual(claimLeftovers(t.inbox), []);
});

test("wake: an orphan of an ended process is put back ahead of the inbox and delivered in the same drain", async () => {
	const t = leadWith(stubRefs);
	await t.fire("session_start", {}, t.ctx);
	const pid = endedPid();
	const orphan = `${t.inbox}.claim-${pid}-${Date.now()}-0`;
	fs.writeFileSync(orphan, "reported exec-o1 /r/o1.md\ngarbage\nreported exec-o2 /r/o2.md\n");
	fs.writeFileSync(`${orphan}.tmp`, "half-written\n");
	fs.appendFileSync(t.inbox, "reported exec-i /r/i.md\n");
	assert.equal(t.handle.drainInbox(), 3);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-o1", "exec-o2", "exec-i"]);
	assert.ok(!fs.existsSync(orphan) && !fs.existsSync(`${orphan}.tmp`), "the orphan and its .tmp are gone");
	assert.deepEqual(events(t.home, "wake_recovered"), [{ session_id: t.sid, claim: path.basename(orphan), wakes: 2 }]);
	assert.deepEqual(claimLeftovers(t.inbox), []);
});

test("wake: orphans are taken by <ms> then <seq>, compared as numbers", async () => {
	const t = leadWith(stubRefs);
	await t.fire("session_start", {}, t.ctx);
	const pid = endedPid();
	fs.writeFileSync(`${t.inbox}.claim-${pid}-10-0`, "reported exec-ms10 /r/a.md\n");
	fs.writeFileSync(`${t.inbox}.claim-${pid}-9-5`, "reported exec-ms9 /r/b.md\n");
	fs.writeFileSync(`${t.inbox}.claim-${pid}-20-10`, "reported exec-seq10 /r/c.md\n");
	fs.writeFileSync(`${t.inbox}.claim-${pid}-20-9`, "reported exec-seq9 /r/d.md\n");
	assert.equal(t.handle.drainInbox(), 4);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-ms9", "exec-ms10", "exec-seq9", "exec-seq10"]);
	assert.deepEqual(events(t.home, "wake_recovered").map((e: any) => e.claim), [
		`${t.sid}.inbox.claim-${pid}-9-5`, `${t.sid}.inbox.claim-${pid}-10-0`, `${t.sid}.inbox.claim-${pid}-20-9`, `${t.sid}.inbox.claim-${pid}-20-10`,
	]);
});

test("wake: a live process's claim 59 s old by the injected clock is left alone; at 60 s it is recovered", async () => {
	let clock = 5_000_000;
	const t = leadWith(stubRefs, { now: () => clock });
	await t.fire("session_start", {}, t.ctx);
	assert.ok(!pidGone(process.ppid) && process.ppid !== process.pid);
	const claim = `${t.inbox}.claim-${process.ppid}-${clock - 59_000}-0`;
	fs.writeFileSync(claim, "reported exec-live /r/l.md\n");
	assert.equal(ext.CLAIM_STALE_MS, 60_000);
	assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.equal(t.sent.length, 0);
	assert.equal(fs.readFileSync(claim, "utf-8"), "reported exec-live /r/l.md\n");
	clock += 1000;
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-live"]);
	assert.ok(!fs.existsSync(claim));
});

test("wake: a file beside the inbox whose name does not parse is left alone, byte-identical", async () => {
	const t = leadWith(stubRefs, { now: () => 9e12 });
	await t.fire("session_start", {}, t.ctx);
	const odd = [`${t.inbox}.claim-abc`, `${t.inbox}.claim-1-2`, `${t.inbox}.claim-1-2-3-4`, `${t.inbox}.claim-${process.pid}-1-2.tmp`, `${t.inbox}.claim--1-2-3`];
	for (const f of odd) fs.writeFileSync(f, "reported exec-odd /r/x.md\n");
	const before = treeOf(path.dirname(t.inbox));
	fs.appendFileSync(t.inbox, "reported exec-i /r/i.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-i"]);
	const after = treeOf(path.dirname(t.inbox));
	for (const f of odd) assert.equal(after.get(f), before.get(f), f);
});

test("wake: nothing is recovered after session_shutdown", async () => {
	const t = leadWith(stubRefs);
	await t.fire("session_start", {}, t.ctx);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	const orphan = `${t.inbox}.claim-${endedPid()}-1-0`;
	fs.writeFileSync(orphan, "reported exec-o /r/o.md\n");
	const before = treeOf(path.dirname(t.inbox));
	assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.deepEqual(treeOf(path.dirname(t.inbox)), before);
	assert.equal(t.sent.length, 0);
});

test("wake order, case 1: two wakes of one batch whose sends throw go back as a then b", async () => {
	const t = leadWith(stubRefs, { retryMs: 60_000 });
	t.pi.sendMessage = () => {
		throw new Error("transient");
	};
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/a.md\nreported exec-b /r/b.md\n");
	assert.equal(t.handle.drainInbox(), 2);
	await t.handle.flush();
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "reported exec-a /r/a.md\nreported exec-b /r/b.md\n");
	assert.deepEqual(claimLeftovers(t.inbox), []);
});

test("wake order, case 2: a put back earlier and b pending at shutdown go back as a then b; the next instance delivers a, b", async () => {
	const da = deferred<string>();
	const db = deferred<string>();
	const t = leadWith((s) => (s === "exec-a" ? da.promise : db.promise), { retryMs: 60_000 });
	const send = t.pi.sendMessage;
	t.pi.sendMessage = (m: any, o: any) => {
		if (m.details.sid === "exec-a") throw new Error("transient");
		send(m, o);
	};
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/a.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	fs.appendFileSync(t.inbox, "reported exec-b /r/b.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	da.resolve("refs: a");
	await until(() => fs.readFileSync(t.inbox, "utf-8") === "reported exec-a /r/a.md\n", "exec-a to be put back");
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "reported exec-a /r/a.md\nreported exec-b /r/b.md\n");
	assert.deepEqual(claimLeftovers(t.inbox), []);
	assert.equal(t.sent.length, 0);
	const next = nextLeadSession(t, { refsCheck: (_h: string, s: string) => stubRefs(s) });
	await next.fire("session_start", {}, t.ctx);
	await next.handle.flush();
	assert.deepEqual(sids(next), ["exec-a", "exec-b"]);
});

test("wake: a put-back whose append fails throws nothing, keeps the claim, is ledgered and warned, and is delivered after the hold", async () => {
	let clock = 7_000_000;
	const t = leadWith(stubRefs, { now: () => clock, retryMs: 1000 });
	await t.fire("session_start", {}, t.ctx);
	const send = t.pi.sendMessage;
	t.pi.sendMessage = () => {
		// the inbox path becomes a directory at that moment: the put-back's append must fail
		fs.rmSync(t.inbox);
		fs.mkdirSync(t.inbox);
		throw new Error("transient");
	};
	fs.appendFileSync(t.inbox, "reported exec-x /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush(); // resolves: nothing thrown
	assert.deepEqual([...claimsOf(t.inbox).values()], ["reported exec-x /r/report-0001.md\n"], "the wake's claim file is still there with its line");
	const [ev] = events(t.home, "wake_putback_failed");
	assert.deepEqual(Object.keys(ev), ["session_id", "wakes", "error"]);
	assert.equal(ev.session_id, t.sid);
	assert.equal(ev.wakes, 1);
	assert.ok(ev.error.length > 0 && ev.error.length <= 200 && !ev.error.includes("\n"), ev.error);
	assert.deepEqual(t.notes.filter((n) => n.type === "warning"), [
		{ msg: `pi-lead: 1 wake(s) could not be put back in ${t.inbox} (${ev.error}) — kept on disk, retried at the next drain`, type: "warning" },
	]);
	assert.deepEqual(events(t.home, "wake_delivered"), []);
	// the cause is removed; during the hold nothing is claimed; after it the drain recovers and delivers
	fs.rmdirSync(t.inbox);
	fs.writeFileSync(t.inbox, "");
	t.pi.sendMessage = send;
	assert.equal(t.handle.drainInbox(), 0, "the hold");
	clock += 1000;
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-x"]);
	assert.deepEqual(claimLeftovers(t.inbox), []);
	assert.equal(events(t.home, "wake_recovered").length, 1);
});

test("wake: a warning through a ctx that throws is dropped without a message and never throws", async () => {
	const t = leadWith(stubRefs, { retryMs: 60_000 });
	const ctx = { ...t.ctx, ui: { notify: () => { throw new Error("stale ctx"); } } };
	await t.fire("session_start", {}, ctx);
	t.pi.sendMessage = () => {
		fs.rmSync(t.inbox);
		fs.mkdirSync(t.inbox);
		throw new Error("transient");
	};
	fs.appendFileSync(t.inbox, "reported exec-x /r/x.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(events(t.home, "wake_putback_failed").length, 1);
	assert.equal(claimsOf(t.inbox).size, 1);
});

test("wake_delivered: once per wake handed over, with packet from the report's name; none for a failed send or a shutdown put-back", async () => {
	const d = deferred<string>();
	const t = leadWith((s) => (s === "exec-slow" ? d.promise : stubRefs(s)), { retryMs: 60_000 });
	const send = t.pi.sendMessage;
	let failedSends = 0;
	t.pi.sendMessage = (m: any, o: any) => {
		if (m.details.sid === "exec-fail") {
			failedSends++;
			throw new Error("transient");
		}
		send(m, o);
	};
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(
		t.inbox,
		"reported exec-a /h/sessions/exec-a/report-0003.md\nreported exec-b /r/x.md\nreported exec-fail /r/f.md\nreported exec-slow /r/s.md\n",
	);
	assert.equal(t.handle.drainInbox(), 4);
	await until(() => failedSends === 1, "exec-fail's send to throw");
	assert.deepEqual(events(t.home, "wake_delivered"), [
		{ session_id: t.sid, executor: "exec-a", packet: 3, report: "/h/sessions/exec-a/report-0003.md" },
		{ session_id: t.sid, executor: "exec-b", packet: null, report: "/r/x.md" },
	]);
	assert.deepEqual(Object.keys(events(t.home, "wake_delivered")[0]), ["session_id", "executor", "packet", "report"]);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx); // exec-fail and exec-slow go back
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "reported exec-fail /r/f.md\nreported exec-slow /r/s.md\n");
	d.resolve("refs: late");
	await t.handle.flush();
	assert.equal(events(t.home, "wake_delivered").length, 2, "nothing for the failed send or the put-back at shutdown");
});

test("wake: an instance that got session_shutdown and then session_start again delivers a line appended afterwards", async () => {
	const t = leadWith(stubRefs, { retryMs: 60_000 });
	t.pi.sendMessage = () => {
		throw new Error("transient"); // leaves a hold behind, which session_start clears
	};
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/a.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	t.pi.sendMessage = (m: any, o: any) => void t.sent.push({ message: m, options: o });
	fs.appendFileSync(t.inbox, "reported exec-b /r/b.md\n");
	await t.fire("session_start", {}, t.ctx);
	await t.handle.flush();
	assert.deepEqual(sids(t), ["exec-a", "exec-b"]);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
});

// ---- executor: a claimed packet survives a kill ------------------------------------------------------

function executorWith(opts: any = {}, extraEnv: Record<string, string> = {}) {
	const t = setup("executor", extraEnv, opts);
	const inbox = path.join(t.sessDir, "inbox.md");
	return { ...t, inbox };
}
const userMsg = (text: string) => ({ message: { role: "user", content: [{ type: "text", text }] } });

test("executor claim: one claim file holds the handed-over packet; a message_end with other text keeps it, with its text deletes it", async () => {
	const t = executorWith();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "  The packet\nline 2\n\n");
	assert.equal(t.handle.drainInbox(), 1);
	assert.deepEqual([...claimsOf(t.inbox).values()], ["  The packet\nline 2\n\n"]);
	await t.fire("message_end", userMsg("something else"), t.ctx);
	await t.fire("message_end", { message: { role: "assistant", content: [{ type: "text", text: "The packet\nline 2" }] } }, t.ctx);
	assert.equal(claimsOf(t.inbox).size, 1);
	await t.fire("message_end", { message: { role: "user", content: "The packet\nline 2" } }, t.ctx);
	assert.equal(claimsOf(t.inbox).size, 0);
});

test("executor claim: an orphan of an ended process goes back ahead of the inbox and is delivered first; one packet_recovered", async () => {
	const t = executorWith();
	fs.mkdirSync(t.sessDir, { recursive: true });
	const orphan = `${t.inbox}.claim-${endedPid()}-${Date.now()}-0`;
	fs.writeFileSync(orphan, "Orphan packet\n");
	fs.writeFileSync(t.inbox, "Second packet\n");
	await t.fire("session_start", {}, t.ctx);
	assert.deepEqual(t.userSent.map((u) => u.content), ["Orphan packet\nSecond packet"]);
	assert.ok(!fs.existsSync(orphan));
	assert.deepEqual(events(t.home, "packet_recovered"), [{ session_id: t.sid, claim: path.basename(orphan) }]);
	assert.equal(claimsOf(t.inbox).size, 1, "the new claim is held until pi records it");
});

test("executor claim: a live pid's claim 59 s old is left alone and recovered at 60 s; an odd name is left alone; nothing after shutdown", async () => {
	let clock = 3_000_000;
	const t = executorWith({ now: () => clock });
	await t.fire("session_start", {}, t.ctx);
	const live = `${t.inbox}.claim-${process.ppid}-${clock - 59_000}-0`;
	const odd = `${t.inbox}.claim-x-1-2`;
	fs.writeFileSync(live, "Live packet\n");
	fs.writeFileSync(odd, "Odd packet\n");
	const oddBefore = treeOf(t.sessDir).get(odd);
	assert.equal(t.handle.drainInbox(), 0);
	assert.ok(fs.existsSync(live));
	clock += 1000;
	assert.equal(t.handle.drainInbox(), 1);
	assert.deepEqual(t.userSent.map((u) => u.content), ["Live packet"]);
	assert.ok(!fs.existsSync(live));
	assert.equal(treeOf(t.sessDir).get(odd), oddBefore);
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	const later = `${t.inbox}.claim-${endedPid()}-1-0`;
	fs.writeFileSync(later, "Later packet\n");
	assert.equal(t.handle.drainInbox(), 0);
	assert.ok(fs.existsSync(later));
	assert.equal(t.userSent.length, 1);
});

test("executor claim: session_shutdown with a held, unrecorded claim puts the packet back in the inbox and leaves no claim file", async () => {
	const t = executorWith();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "Held packet\n");
	assert.equal(t.handle.drainInbox(), 1);
	fs.appendFileSync(t.inbox, "Newer packet\n");
	void t.fire("session_shutdown", { reason: "reload" }, t.ctx); // synchronous: complete when the handler returns
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "Held packet\nNewer packet\n");
	assert.equal(claimsOf(t.inbox).size, 0);
});

test("executor claim: a failed put-back throws nothing, keeps the claim, is ledgered, and a later drain recovers it", async () => {
	const t = executorWith();
	fs.mkdirSync(t.inbox, { recursive: true }); // the inbox path is a directory
	const orphan = `${t.inbox}.claim-${endedPid()}-1-0`;
	fs.writeFileSync(orphan, "Kept packet\n");
	assert.equal(t.handle.drainInbox(), 0);
	assert.equal(fs.readFileSync(orphan, "utf-8"), "Kept packet\n");
	const [ev] = events(t.home, "packet_putback_failed");
	assert.deepEqual(Object.keys(ev), ["session_id", "error"]);
	assert.equal(ev.session_id, t.sid);
	assert.ok(ev.error.length > 0 && ev.error.length <= 200);
	fs.rmdirSync(t.inbox);
	assert.equal(t.handle.drainInbox(), 1);
	assert.deepEqual(t.userSent.map((u) => u.content), ["Kept packet"]);
	assert.ok(!fs.existsSync(orphan));
});

test("executor claim: an executor whose marker is held by another process leaves an orphan and the inbox byte-identical", async () => {
	const t = executorWith({ pid: 4242 }, { PI_LEAD_HELD_BY: "999" });
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(`${t.inbox}.claim-${endedPid()}-1-0`, "Orphan packet\n");
	fs.writeFileSync(t.inbox, "Inbox packet\n");
	const before = treeOf(t.home);
	await t.fire("session_start", {}, t.ctx);
	assert.equal(t.handle.drainInbox(), 0);
	await t.fire("message_end", userMsg("Inbox packet"), t.ctx);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.deepEqual(treeOf(t.home), before);
	assert.deepEqual(t.userSent, []);
});

// ---- the guard: a quiet lead does none of the new lead work --------------------------------------------

test("quiet lead: an orphan beside the inbox is not recovered — inbox and orphan byte-identical", async () => {
	const { home, sid, inbox } = waitingLead();
	fs.writeFileSync(`${inbox}.claim-${endedPid()}-1-0`, "reported exec-o /r/o.md\n");
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242 });
	const before = treeOf(home);
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	await t.handle.flush();
	assert.deepEqual(treeOf(home), before);
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
});

test("quiet lead: no health file is written, at session_start, a drain or shutdown", async () => {
	const { home, sid, inbox } = waitingLead();
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid }, { pid: 4242 }, "print");
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.ok(!fs.existsSync(path.join(home, "wake")));
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
});

test("quiet lead: the status line is never asked", async () => {
	const { home, sid, inbox } = waitingLead();
	let asked = 0;
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242, statusLine: () => (asked++, "x") });
	t.ctx.ui.setStatus = () => {};
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(20);
	assert.equal(asked, 0);
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
});

// ---- the health file ----------------------------------------------------------------------------------

const HEALTH_KEYS = ["sid", "pid", "started", "beat", "watching", "poll_seconds", "delivered", "last_delivered", "pending", "last_error", "last_error_at", "ended", "auto_wake", "version"];
const healthOf = (home: string, sid: string) => JSON.parse(fs.readFileSync(path.join(home, "wake", sid, "health.json"), "utf-8"));
const isoAt = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const PACKAGE_VERSION = JSON.parse(fs.readFileSync(path.join(REPO, "package.json"), "utf-8")).version;

test("health: `version` is package.json's version, written with every health file", async () => {
	assert.equal(typeof PACKAGE_VERSION, "string");
	const t = leadWith(stubRefs);
	await t.fire("session_start", {}, t.ctx);
	assert.equal(healthOf(t.home, t.sid).version, PACKAGE_VERSION);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.equal(healthOf(t.home, t.sid).version, PACKAGE_VERSION);
	assert.equal(ext.packageVersion(), PACKAGE_VERSION);
});

test("health: an unreadable package.json gives `version` null", async () => {
	const missing = path.join(SANDBOX, "no-such-dir", "package.json");
	const t = leadWith(stubRefs, { packageJson: missing });
	await t.fire("session_start", {}, t.ctx);
	const h = healthOf(t.home, t.sid);
	assert.ok(Object.keys(h).includes("version"));
	assert.equal(h.version, null);
	const bad = path.join(SANDBOX, `bad-package-${seq++}.json`);
	fs.writeFileSync(bad, "{not json");
	assert.equal(ext.packageVersion(bad), null);
	fs.writeFileSync(bad, JSON.stringify({ version: 3 }));
	assert.equal(ext.packageVersion(bad), null);
});

test("quiet lead: no version is written — no health file, the inbox byte-identical", async () => {
	const { home, sid, inbox } = waitingLead();
	const pkg = path.join(SANDBOX, `watched-package-${seq++}.json`);
	fs.writeFileSync(pkg, JSON.stringify({ version: "9.9.9" }));
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242, packageJson: pkg });
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.ok(!fs.existsSync(path.join(home, "wake")));
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
});

test("health: the file's fields at session_start, after a delivered wake, after a failed send, and at shutdown", async () => {
	let clock = Date.UTC(2026, 8, 30, 12, 0, 0);
	const t0 = clock;
	const t = leadWith(stubRefs, { now: () => clock, retryMs: 60_000 });
	await t.fire("session_start", {}, t.ctx);
	let h = healthOf(t.home, t.sid);
	assert.deepEqual(Object.keys(h), HEALTH_KEYS);
	assert.deepEqual(h, {
		sid: t.sid, pid: process.pid, started: isoAt(t0), beat: isoAt(t0), watching: false, poll_seconds: 0, delivered: 0,
		last_delivered: null, pending: 0, last_error: null, last_error_at: null, ended: null, auto_wake: true,
		version: PACKAGE_VERSION,
	});
	assert.deepEqual(fs.readdirSync(path.join(t.home, "wake", t.sid)), ["health.json"]);

	clock += 5000;
	fs.appendFileSync(t.inbox, "reported exec-a /r/a.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	h = healthOf(t.home, t.sid);
	assert.equal(h.delivered, 1);
	assert.equal(h.last_delivered, isoAt(t0 + 5000));
	assert.equal(h.beat, isoAt(t0 + 5000));
	assert.equal(h.last_error, null);

	clock += 5000;
	t.pi.sendMessage = () => {
		throw new Error("send broke\nsecond line");
	};
	fs.appendFileSync(t.inbox, "reported exec-b /r/b.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	h = healthOf(t.home, t.sid);
	assert.equal(h.last_error, "send broke");
	assert.equal(h.last_error_at, isoAt(t0 + 10_000));
	assert.equal(h.delivered, 1);
	assert.equal(h.pending, 0);

	clock += 5000;
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	h = healthOf(t.home, t.sid);
	assert.equal(h.ended, isoAt(t0 + 15_000));
	assert.equal(h.started, isoAt(t0));
});

test("health: a session that is not registered gets no health file; one that registers later gets it at the next drain", async () => {
	const t = setup("lead", {}, { refsCheck: () => "refs: stub" });
	await t.fire("session_start", {}, t.ctx);
	assert.equal(t.handle.drainInbox(), 0);
	assert.ok(!fs.existsSync(path.join(t.home, "wake")));
	const inbox = registerLead(t.home, t.sid);
	fs.appendFileSync(inbox, "reported exec-a /r/a.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	assert.equal(healthOf(t.home, t.sid).delivered, 1);
});

test("health: one beat timer of HEALTH_BEAT_MS, started with the watchers, rewrites the file; stop() clears it", async () => {
	assert.equal(ext.HEALTH_BEAT_MS, 30_000);
	const realSet = globalThis.setTimeout;
	const realClear = globalThis.clearTimeout;
	const beats: { fn: () => void; handle: any }[] = [];
	const cleared = new Set<any>();
	(globalThis as any).setTimeout = (fn: any, ms: number, ...rest: any[]) => {
		const handle = realSet(() => {}, 0);
		if (ms === ext.HEALTH_BEAT_MS) beats.push({ fn, handle });
		else return realSet(fn, ms, ...rest);
		return handle;
	};
	(globalThis as any).clearTimeout = (h: any) => {
		cleared.add(h);
		realClear(h);
	};
	let clock = Date.UTC(2026, 8, 30, 13, 0, 0);
	try {
		const t = leadWith(stubRefs, { watch: true, pollMs: 0, now: () => clock });
		await t.fire("session_start", {}, t.ctx);
		assert.equal(beats.length, 1, "one beat timer");
		assert.equal(healthOf(t.home, t.sid).watching, true);
		clock += 30_000;
		beats[0].fn(); // the beat fires: the file is rewritten and the timer re-armed
		assert.equal(healthOf(t.home, t.sid).beat, isoAt(clock));
		assert.equal(beats.length, 2);
		t.handle.stop();
		assert.ok(cleared.has(beats[1].handle), "stop() clears the beat timer");
	} finally {
		(globalThis as any).setTimeout = realSet;
		(globalThis as any).clearTimeout = realClear;
	}
});

// ---- the footer's status line -------------------------------------------------------------------------

/** A ctx with a recording `setStatus`, and a statusLine stub answering from `answers` (a value or a deferred). */
function withFooter(role: "lead" | "executor", answer: () => string | Promise<string>) {
	const asks: { sid: string; args: string[] }[] = [];
	const t = setup(role, {}, { refsCheck: () => "refs: stub", statusLine: (_h: string, s: string, args: string[]) => (asks.push({ sid: s, args }), answer()) });
	const set: [string, string | undefined][] = [];
	const ctx: any = { ...t.ctx, ui: { ...t.ctx.ui, setStatus: (k: string, v: string | undefined) => void set.push([k, v]) } };
	return { ...t, asks, set, ctx };
}

test("status line: set from the injected statusLine, not set again for the same text, cleared for an empty answer", async () => {
	let text = "🚦 proj";
	const t = withFooter("lead", () => text);
	registerLead(t.home, t.sid);
	const ctx = { ...t.ctx, getContextUsage: () => ({ tokens: 123_456, contextWindow: 200_000, percent: 61 }) };
	await t.fire("session_start", {}, ctx);
	await until(() => t.set.length === 1, "the first footer text");
	assert.deepEqual(t.set, [["pi-lead", "🚦 proj"]]);
	assert.deepEqual(t.asks[0], { sid: t.sid, args: ["--ctx-tokens", "123456", "--ctx-window", "200000"] });
	await t.fire("agent_settled", {}, ctx);
	await until(() => t.asks.length === 2, "the second ask");
	await sleep(20);
	assert.equal(t.set.length, 1, "the same text is not set again");
	text = "";
	await t.fire("agent_settled", {}, ctx);
	await until(() => t.set.length === 2, "the clear");
	assert.deepEqual(t.set[1], ["pi-lead", undefined]);
	await t.fire("session_shutdown", { reason: "quit" }, ctx);
	assert.equal(t.set.length, 2, "nothing to clear at shutdown");
});

test("status line: a refresh asked during a run is run once more when it has ended; shutdown clears the text", async () => {
	let d = deferred<string>();
	const t = withFooter("executor", () => d.promise);
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(t.asks.length, 1, "one child at a time");
	assert.deepEqual(t.asks[0], { sid: t.sid, args: [] });
	const first = d;
	d = deferred<string>();
	first.resolve("pkt 0001 busy");
	await until(() => t.asks.length === 2, "the one more run");
	d.resolve("pkt 0001 reported");
	await until(() => t.set.length === 2, "both texts");
	await sleep(20);
	assert.equal(t.asks.length, 2, "asked exactly once more");
	assert.deepEqual(t.set, [["pi-lead", "pkt 0001 busy"], ["pi-lead", "pkt 0001 reported"]]);
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	assert.deepEqual(t.set.at(-1), ["pi-lead", undefined]);
	await t.fire("agent_settled", {}, t.ctx);
	await sleep(20);
	assert.equal(t.asks.length, 2, "nothing started after shutdown");
});

test("status line: never asked when ctx.ui has no setStatus or hasUI is false", async () => {
	for (const make of [(c: any) => c, (c: any) => ({ ...c, hasUI: false, ui: { ...c.ui, setStatus: () => {} } })]) {
		let asked = 0;
		const t = setup("lead", {}, { refsCheck: () => "refs: stub", statusLine: () => (asked++, "x") });
		registerLead(t.home, t.sid);
		const ctx = make(t.ctx);
		await t.fire("session_start", {}, ctx);
		await t.fire("agent_settled", {}, ctx);
		await sleep(20);
		assert.equal(asked, 0);
	}
});

test("status line: a ctx whose setStatus throws does not stop a wake", async () => {
	const t = withFooter("lead", () => "🚦 proj");
	const inbox = registerLead(t.home, t.sid);
	const ctx = { ...t.ctx, ui: { ...t.ctx.ui, setStatus: () => { throw new Error("stale"); } } };
	await t.fire("session_start", {}, ctx);
	fs.appendFileSync(inbox, "reported exec-a /r/a.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	await sleep(20);
	assert.deepEqual(t.sent.map((x) => x.message.details.sid), ["exec-a"]);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "");
});

test("statusLineText: a missing pilead gives an empty answer and never rejects", async () => {
	assert.equal(await ext.statusLineText(SANDBOX, "lead-x", [], path.join(SANDBOX, "no-such-pilead")), "");
});

// ---- desktop banners for a report (p20d) --------------------------------------------------------------

const tui = (ctx: any) => ({ ...ctx, mode: "tui" });

/** A registered lead in a tui ctx, with the `banner` option recording the argument lists it is asked for. */
function bannerLead(refs: (s: string) => string | Promise<string> = stubRefs) {
	const calls: string[][] = [];
	const t = leadWith(refs, { banner: (a: string[]) => void calls.push(a) });
	return { ...t, calls, ctx: tui(t.ctx) };
}

test("banner: one call per wake handed over, with the whole argument list", async () => {
	const t = bannerLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\nreported exec-b /r/report-0003.md\n");
	assert.equal(t.handle.drainInbox(), 2);
	await t.handle.flush();
	assert.deepEqual(t.calls, [
		["notify", t.sid, "--executor", "exec-a", "--packet", "1", "--home", t.home],
		["notify", t.sid, "--executor", "exec-b", "--packet", "3", "--home", t.home],
	]);
	assert.equal(t.sent.length, 2, "the wakes themselves are as before");
});

test("banner: none for a wake whose send threw, one put back at shutdown, or a report path with no number", async () => {
	// a send that throws: the wake is put back, nobody is told
	const a = bannerLead();
	await a.fire("session_start", {}, a.ctx);
	a.pi.sendMessage = () => {
		throw new Error("send failed");
	};
	fs.appendFileSync(a.inbox, "reported exec-a /r/report-0001.md\n");
	a.handle.drainInbox();
	await a.handle.flush();
	assert.deepEqual(a.calls, []);
	assert.equal(fs.readFileSync(a.inbox, "utf-8"), "reported exec-a /r/report-0001.md\n", "put back");
	// a wake still waiting for its refs line when the session shuts down
	const d = deferred<string>();
	const b = bannerLead(() => d.promise);
	await b.fire("session_start", {}, b.ctx);
	fs.appendFileSync(b.inbox, "reported exec-b /r/report-0001.md\n");
	assert.equal(b.handle.drainInbox(), 1);
	await b.fire("session_shutdown", { reason: "quit" }, b.ctx);
	d.resolve("refs: late");
	await b.handle.flush();
	assert.deepEqual([b.calls, b.sent], [[], []]);
	// a report path with no number: the wake is sent, no banner is asked for
	const c = bannerLead();
	await c.fire("session_start", {}, c.ctx);
	fs.appendFileSync(c.inbox, "reported exec-c /r/report.md\n");
	c.handle.drainInbox();
	await c.handle.flush();
	assert.equal(c.sent.length, 1);
	assert.deepEqual(c.calls, []);
});

test("banner: none when ctx.mode is not tui (rpc, print, json, none), the wake still goes", async () => {
	for (const mode of ["rpc", "print", "json", undefined]) {
		const calls: string[][] = [];
		const t = leadWith(stubRefs, { banner: (a: string[]) => void calls.push(a) });
		const ctx = mode === undefined ? t.ctx : { ...t.ctx, mode };
		await t.fire("session_start", {}, ctx);
		fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
		t.handle.drainInbox();
		await t.handle.flush();
		if (mode === "print" || mode === "json") {
			assert.deepEqual([t.sent.length, calls], [0, []], String(mode)); // quiet: nothing at all
		} else {
			assert.deepEqual([t.sent.length, calls], [1, []], String(mode));
		}
	}
});

test("banner: a banner that throws or rejects never changes the wake", async () => {
	for (const banner of [
		() => {
			throw new Error("sync");
		},
		() => Promise.reject(new Error("async")),
	]) {
		const t = leadWith(stubRefs, { banner });
		const ctx = tui(t.ctx);
		await t.fire("session_start", {}, ctx);
		fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
		t.handle.drainInbox();
		await t.handle.flush();
		await sleep(10);
		assert.deepEqual(sids(t), ["exec-a"]);
		assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
		assert.equal(events(t.home, "wake_delivered").length, 1);
	}
});

test("banner: without the option the lead runs pilead notify itself (execFile, the given bin), in a tui ctx only", async () => {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "banner-bin-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	fs.writeFileSync(bin, `#!/bin/sh\nprintf '%s\\n' "$*" >> ${JSON.stringify(log)}\nif [ "$1" = check ]; then echo "refs: stub"; fi\n`);
	fs.chmodSync(bin, 0o755);
	const t = setup("lead", {}, { pileadBin: bin });
	const inbox = registerLead(t.home, t.sid);
	const ctx = tui(t.ctx);
	await t.fire("session_start", {}, ctx);
	fs.appendFileSync(inbox, "reported exec-a /r/report-0002.md\n");
	t.handle.drainInbox();
	await t.handle.flush();
	await until(() => fs.existsSync(log) && fs.readFileSync(log, "utf-8").includes("notify"), "the notify child");
	const seenCalls = fs.readFileSync(log, "utf-8").split("\n").filter(Boolean);
	assert.ok(seenCalls.includes(`notify ${t.sid} --executor exec-a --packet 2 --home ${t.home}`), seenCalls.join("|"));
});

test("banner: a quiet lead asks for none and leaves its inbox byte-identical", async () => {
	const { home, sid, inbox } = waitingLead();
	const calls: string[][] = [];
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" };
	const t = withEnv(env, { pid: 4242, banner: (a: string[]) => void calls.push(a) }, "tui");
	await t.fire("session_start", {}, t.ctx);
	assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
	assert.deepEqual([calls, t.sent], [[], []]);
});

test("banner: the executor asks with --no-lead once per settled report, whether or not a lead can be told", async () => {
	for (const registered of [true, false]) {
		const calls: string[][] = [];
		const t = setup("executor", {}, { escalate: (a: string[]) => void calls.push(a) });
		if (registered) registerLead(t.home, t.leadSid);
		fs.mkdirSync(t.sessDir, { recursive: true });
		fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
		const ctx = tui(t.ctx);
		await t.fire("session_start", {}, ctx);
		await t.fire("before_agent_start", { prompt: "go" }, ctx);
		await t.fire("agent_settled", {}, ctx);
		assert.equal(status(t.sessDir), "idle");
		assert.deepEqual(calls, [], "no report yet: no banner");
		fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "Done.");
		await t.fire("agent_settled", {}, ctx);
		assert.equal(status(t.sessDir), "reported");
		assert.deepEqual(calls, [["escalate", t.sid, "--packet", "1", "--json", "--home", t.home]], String(registered));
	}
});

test("banner: an executor asks for none outside a tui ctx, and a quiet executor asks for none", async () => {
	const calls: string[][] = [];
	const record = (a: string[]) => void calls.push(a);
	const t = setup("executor", {}, { banner: record });
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	fs.writeFileSync(path.join(t.sessDir, "report-0001.md"), "Done.");
	await t.fire("agent_settled", {}, { ...t.ctx, mode: "rpc" });
	assert.equal(status(t.sessDir), "reported");
	assert.deepEqual(calls, []);
	const x = waitingExecutor();
	x.env.PI_LEAD_HELD_BY = "999";
	const q = withEnv(x.env, { pid: 4242, banner: record }, "tui");
	await q.fire("session_start", {}, q.ctx);
	await q.fire("agent_settled", {}, q.ctx);
	assert.deepEqual(calls, []);
	assert.equal(fs.readFileSync(x.inbox, "utf-8"), "Follow-up packet\n");
});

// ---- the executor's escalation (p20i) -------------------------------------------------------------------

/** An executor with packet 1 written; `report` writes its report. */
function escalatingExecutor(opts: any = {}) {
	const t = setup("executor", {}, opts);
	const inbox = registerLead(t.home, t.leadSid);
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	const report = path.join(t.sessDir, "report-0001.md");
	return { ...t, inbox, report, writeReport: () => fs.writeFileSync(report, "Done.\nStatus: clean") };
}

test("escalate: an executor's settle with a report asks once, with the whole argument list", async () => {
	const calls: string[][] = [];
	const t = escalatingExecutor({ escalate: (a: string[]) => void calls.push(a) });
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	assert.deepEqual(calls, [], "no report: no ask");
	t.writeReport();
	await t.fire("agent_settled", {}, t.ctx); // t.ctx has no mode: the injected option is used whatever the mode
	assert.deepEqual(calls, [["escalate", t.sid, "--packet", "1", "--json", "--home", t.home]]);
	assert.equal(status(t.sessDir), "reported");
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
});

test("escalate: none after session_shutdown", async () => {
	const calls: string[][] = [];
	const t = escalatingExecutor({ escalate: (a: string[]) => void calls.push(a) });
	await t.fire("session_start", {}, t.ctx);
	await t.fire("session_shutdown", { reason: "reload" }, t.ctx);
	t.writeReport();
	await t.fire("agent_settled", {}, t.ctx);
	assert.deepEqual(calls, []);
});

test("escalate: one that throws or rejects stops nothing; the status and the inbox line are as without it", async () => {
	for (const escalate of [
		() => {
			throw new Error("boom");
		},
		() => Promise.reject(new Error("boom")),
	]) {
		const t = escalatingExecutor({ escalate });
		await t.fire("session_start", {}, t.ctx);
		t.writeReport();
		await t.fire("agent_settled", {}, t.ctx);
		await new Promise((r) => setImmediate(r));
		assert.equal(status(t.sessDir), "reported");
		assert.equal(fs.readFileSync(t.inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
		assert.match(fs.readFileSync(path.join(t.sessDir, ".notified"), "utf-8"), /report-0001\.md /);
	}
});

test("escalate: without the option, no child for a ctx with no mode, or print/json, or no UI; one in tui and rpc", async () => {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "esc-bin-"));
	const log = path.join(dir, "calls.log");
	const bin = path.join(dir, "pilead");
	fs.writeFileSync(bin, `#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\n`);
	fs.chmodSync(bin, 0o755);
	const cases: [any, boolean][] = [
		[{}, false],
		[{ mode: "print" }, false],
		[{ mode: "json" }, false],
		[{ mode: "tui", hasUI: false }, false],
		[{ mode: "tui" }, true],
		[{ mode: "rpc" }, true],
	];
	for (const [extra, started] of cases) {
		try {
			fs.rmSync(log);
		} catch {}
		const t = escalatingExecutor({ pileadBin: bin });
		const ctx = { ...t.ctx, ...extra };
		await t.fire("session_start", {}, ctx);
		t.writeReport();
		await t.fire("agent_settled", {}, ctx);
		const deadline = Date.now() + 3000;
		while (started && !fs.existsSync(log) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20));
		if (!started) await new Promise((r) => setTimeout(r, 100));
		const got = fs.existsSync(log) ? fs.readFileSync(log, "utf-8") : "";
		const want = started ? `escalate ${t.sid} --packet 1 --json --home ${t.home}\n` : "";
		const lines = got.split("\n").filter((l) => l.startsWith("escalate"));
		assert.equal(lines.length ? `${lines.join("\n")}\n` : "", want, JSON.stringify(extra));
		await t.fire("session_shutdown", { reason: "quit" }, ctx);
	}
});

test("escalate: with executor_escalation false the inbox line is still written", async () => {
	const calls: string[][] = [];
	const t = escalatingExecutor({ escalate: (a: string[]) => void calls.push(a) });
	fs.mkdirSync(t.home, { recursive: true });
	fs.writeFileSync(path.join(t.home, "config.json"), JSON.stringify({ executor_escalation: false }));
	await t.fire("session_start", {}, t.ctx);
	t.writeReport();
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
	assert.equal(calls.length, 1, "the verb itself answers `off`");
});

// ---- the end of the lead's turn, and auto_wake (p20e) ----------------------------------------------------

const TURN_LINES = ["  📝 you made 1 commit(s) this turn in /repo:", "       abc1234 add thing", "  🟠 exec-a is approaching heavy (125k ctx)"];
const TURN_JSON = (lines: unknown = TURN_LINES, kinds: unknown = ["commits", "ctx-warn"]) => JSON.stringify({ lines, kinds, parked: [] });
const HEAD = "🚦 [pi-lead] — review needed:\n";

/** A registered lead whose `turnEnd` option answers with the next of `answers` (the last one repeats) and records its calls. */
function turnEndLead(answers: (string | Error)[], opts: any = {}) {
	const calls: { args: string[]; aborted: () => boolean }[] = [];
	let i = 0;
	const t = leadWith(stubRefs, {
		turnEnd: (args: string[], signal: AbortSignal) => {
			calls.push({ args, aborted: () => signal.aborted });
			const a = answers[Math.min(i++, answers.length - 1)];
			if (a instanceof Error) throw a;
			return a;
		},
		...opts,
	});
	return { ...t, calls };
}

test("turn-end: agent_settled of a registered lead asks once and sends one message (manual posture)", async () => {
	const t = turnEndLead([TURN_JSON()]);
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.deepEqual(t.calls.map((c) => c.args), [["turn-end", "--session", t.sid, "--cwd", t.cwd, "--json", "--home", t.home]]);
	assert.deepEqual(t.sent, [
		{
			message: {
				customType: "pi-lead",
				content: `${HEAD}${TURN_LINES.join("\n")}\n\n${ext.TURN_END_MANUAL}`,
				display: true,
				details: { kind: "turn-end", kinds: ["commits", "ctx-warn"], lines: TURN_LINES },
			},
			options: { triggerTurn: true },
		},
	]);
	assert.equal(ext.TURN_END_MANUAL, "Open your reply with '🚦 [pi-lead] — review needed:', say these to the human, and wait for their word. Pass on every line that starts with 🟠 exactly as it is.");
});

test("turn-end: under the autonomous posture the message carries the autonomous instruction; `--banner` in the terminal UI", async () => {
	const t = turnEndLead([TURN_JSON()]);
	setLeadRecord(t.home, t.sid, { autonomous: true });
	const ctx = tui(t.ctx);
	await t.fire("session_start", {}, ctx);
	await t.fire("agent_settled", {}, ctx);
	await t.handle.flush();
	assert.deepEqual(t.calls.map((c) => c.args), [["turn-end", "--session", t.sid, "--cwd", t.cwd, "--json", "--home", t.home, "--banner"]]);
	assert.deepEqual(t.sent, [
		{
			message: {
				customType: "pi-lead",
				content: `${HEAD}${TURN_LINES.join("\n")}${ext.wakeInstruction(true)}`,
				display: true,
				details: { kind: "turn-end", kinds: ["commits", "ctx-warn"], lines: TURN_LINES },
			},
			options: { triggerTurn: true },
		},
	]);
});

test("turn-end: no lines, an answer that is not JSON, and lines that are not a list of texts send nothing", async () => {
	for (const answer of [TURN_JSON([]), "not json", "", "[]", "null", "3", JSON.stringify({ kinds: [] }), JSON.stringify({ lines: "x" }),
		JSON.stringify({ lines: ["ok", 3] }), JSON.stringify({ lines: { 0: "x" } }), new Error("child failed")]) {
		const t = turnEndLead([answer]);
		await t.fire("session_start", {}, t.ctx);
		await t.fire("agent_settled", {}, t.ctx);
		await t.handle.flush();
		assert.equal(t.calls.length, 1, String(answer));
		assert.deepEqual(t.sent, [], String(answer));
		assert.deepEqual(events(t.home, "turn_end_failed"), []);
	}
});

test("turn-end: a session that is not registered, one after session_shutdown, and an executor do not ask", async () => {
	const a = turnEndLead([TURN_JSON()]);
	setLeadRecord(a.home, a.sid, null);
	await a.fire("session_start", {}, a.ctx);
	await a.fire("agent_settled", {}, a.ctx);
	await a.handle.flush();
	assert.deepEqual([a.calls, a.sent], [[], []]);

	const b = turnEndLead([TURN_JSON()]);
	await b.fire("session_start", {}, b.ctx);
	await b.fire("session_shutdown", { reason: "quit" }, b.ctx);
	await b.fire("agent_settled", {}, b.ctx);
	await b.handle.flush();
	assert.deepEqual([b.calls, b.sent], [[], []]);

	const calls: string[][] = [];
	const e = setup("executor", {}, { turnEnd: (args: string[]) => (calls.push(args), TURN_JSON()) });
	fs.mkdirSync(e.sessDir, { recursive: true });
	fs.writeFileSync(path.join(e.sessDir, "packet-0001.md"), "p");
	await e.fire("agent_settled", {}, e.ctx);
	await e.handle.flush();
	assert.deepEqual([calls, e.sent], [[], []]);
});

test("turn-end: a session that shuts down while the child runs sends nothing and aborts the child", async () => {
	const d = deferred<string>();
	const seenSignals: AbortSignal[] = [];
	const u = leadWith(stubRefs, { turnEnd: (_a: string[], s: AbortSignal) => (seenSignals.push(s), d.promise) });
	await u.fire("session_start", {}, u.ctx);
	await u.fire("agent_settled", {}, u.ctx);
	await u.fire("agent_settled", {}, u.ctx); // one child at a time
	assert.equal(seenSignals.length, 1);
	await u.fire("session_shutdown", { reason: "quit" }, u.ctx);
	assert.equal(seenSignals[0].aborted, true);
	d.resolve(TURN_JSON());
	await u.handle.flush();
	assert.deepEqual(u.sent, []);
});

test("turn-end: a second answer with lines inside 60 seconds is ledgered, not sent; at 60 seconds it is sent", async () => {
	assert.equal(ext.TURN_END_GAP_MS, 60_000);
	let clock = Date.UTC(2026, 8, 30, 12, 0, 0);
	const t = turnEndLead([TURN_JSON(["  📝 one"]), TURN_JSON(["  📝 two"]), TURN_JSON(["  📝 three"])], { now: () => clock });
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	clock += 59_999;
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.deepEqual(events(t.home, "turn_end_suppressed"), [{ session_id: t.sid, lines: ["  📝 two"] }]);
	clock += 1;
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 2);
	assert.equal(t.sent[1].message.content.startsWith(`${HEAD}  📝 three`), true);
});

test("turn-end: an empty answer inside the 60 seconds is neither sent nor ledgered", async () => {
	let clock = 1_800_000_000_000;
	const t = turnEndLead([TURN_JSON(["  📝 one"]), TURN_JSON([])], { now: () => clock });
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.deepEqual(events(t.home, "turn_end_suppressed"), []);
});

test("turn-end: a send that throws is swallowed and ledgered, and does not start the 60 seconds", async () => {
	const t = turnEndLead([TURN_JSON(["  📝 one"])]);
	await t.fire("session_start", {}, t.ctx);
	let boom = true;
	const sent: any[] = [];
	t.pi.sendMessage = (m: any, o: any) => {
		if (boom) throw new Error("send broke\nsecond line");
		sent.push({ m, o });
	};
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.deepEqual(events(t.home, "turn_end_failed"), [{ session_id: t.sid, lines: ["  📝 one"], error: "send broke" }]);
	boom = false;
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(sent.length, 1, "a failed send did not count as a message sent");
});

test("turn-end: the report wake is as it was — a wake and a turn-end message are separate messages", async () => {
	const t = turnEndLead([TURN_JSON(["  📝 one"])]);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
	t.handle.drainInbox();
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 2);
	assert.equal(t.sent[0].message.content, "🚦 [pi-lead] executor exec-a reported: /r/report-0001.md — refs: stub for exec-a");
	assert.deepEqual(t.sent[0].message.details, { sid: "exec-a", report: "/r/report-0001.md", refs: "refs: stub for exec-a" });
	assert.equal(t.sent[1].message.details.kind, "turn-end");
});

test("turn-end: without the option no child is started unless hasUI and ctx.mode is tui or rpc; the child gets --banner in tui only", async () => {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "turn-end-bin-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	fs.writeFileSync(bin, `#!/bin/sh\nprintf '%s\\n' "$*" >> ${JSON.stringify(log)}\necho '${TURN_JSON(["  📝 from the child"], ["commits"])}'\n`);
	fs.chmodSync(bin, 0o755);
	const cases: [string, any, boolean][] = [
		["no mode", (c: any) => c, false],
		["print", (c: any) => ({ ...c, mode: "print" }), false],
		["json", (c: any) => ({ ...c, mode: "json" }), false],
		["tui without a UI", (c: any) => ({ ...c, mode: "tui", hasUI: false }), false],
		["rpc", (c: any) => ({ ...c, mode: "rpc" }), true],
		["tui", (c: any) => ({ ...c, mode: "tui" }), true],
	];
	for (const [name, make, runs] of cases) {
		fs.rmSync(log, { force: true });
		const t = setup("lead", {}, { pileadBin: bin, refsCheck: () => "refs: stub" });
		registerLead(t.home, t.sid);
		const ctx = make(t.ctx);
		// print and json are quiet runs: they do not even start; everything else registers as a lead
		await t.fire("session_start", {}, ctx);
		await t.fire("agent_settled", {}, ctx);
		await t.handle.flush();
		const called = fs.existsSync(log) ? fs.readFileSync(log, "utf-8").split("\n").filter(Boolean) : [];
		if (!runs) {
			assert.deepEqual([called, t.sent], [[], []], name);
			continue;
		}
		const banner = name === "tui" ? " --banner" : "";
		assert.deepEqual(called.filter((l) => l.startsWith("turn-end")), [`turn-end --session ${t.sid} --cwd ${t.cwd} --json --home ${t.home}${banner}`], name);
		assert.equal(t.sent.length, 1, name);
		assert.equal(t.sent[0].message.content.startsWith(`${HEAD}  📝 from the child`), true);
		assert.deepEqual(t.sent[0].message.details.kinds, ["commits"]);
	}
});

test("turn-end: pileadText gives the child's stdout, and \"\" for a failing, missing or aborted one", async () => {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "text-bin-"));
	const ok = path.join(dir, "ok");
	const bad = path.join(dir, "bad");
	fs.writeFileSync(ok, "#!/bin/sh\necho \"$*\"\n");
	fs.writeFileSync(bad, "#!/bin/sh\necho partial\nexit 3\n");
	fs.chmodSync(ok, 0o755);
	fs.chmodSync(bad, 0o755);
	assert.equal(await ext.pileadText(["a", "b"], ok), "a b\n");
	assert.equal(await ext.pileadText([], bad), "");
	assert.equal(await ext.pileadText([], path.join(dir, "missing")), "");
	const ac = new AbortController();
	ac.abort();
	assert.equal(await ext.pileadText([], ok, 1000, ac.signal), "");
});

test("turn-end: parseTurnEnd", () => {
	assert.deepEqual(ext.parseTurnEnd(TURN_JSON()), { lines: TURN_LINES, kinds: ["commits", "ctx-warn"] });
	assert.deepEqual(ext.parseTurnEnd(JSON.stringify({ lines: ["a"] })), { lines: ["a"], kinds: [] });
	assert.deepEqual(ext.parseTurnEnd(JSON.stringify({ lines: ["a"], kinds: ["k", 3, null] })), { lines: ["a"], kinds: ["k"] });
	for (const bad of [undefined, null, 5, "", "{", "[]", '"x"', "{}", '{"lines":[1]}']) assert.deepEqual(ext.parseTurnEnd(bad), { lines: [], kinds: [] });
});

test("turn-end: a quiet lead (marker names another process) asks for nothing and its inbox is byte-identical", async () => {
	const { home, sid, inbox } = waitingLead();
	const calls: string[][] = [];
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" };
	const t = withEnv(env, { pid: 4242, turnEnd: (a: string[]) => (calls.push(a), TURN_JSON()) }, "tui");
	const before = treeOf(home);
	await t.fire("session_start", {}, t.ctx);
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
	assert.deepEqual([calls, t.sent, treeOf(home)], [[], [], before]);
});

// ---- auto_wake

const writeCfg = (home: string, text: string) => {
	fs.mkdirSync(home, { recursive: true });
	fs.writeFileSync(path.join(home, "config.json"), text);
};

test("auto_wake false: a reported line stays in the inbox, nothing is sent, the health file says so", async () => {
	const t = turnEndLead([TURN_JSON()]);
	writeCfg(t.home, JSON.stringify({ auto_wake: false }));
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 0);
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "reported exec-a /r/report-0001.md\n");
	assert.deepEqual([t.sent, t.calls, claimLeftovers(t.inbox)], [[], [], []]);
	assert.equal(healthOf(t.home, t.sid).auto_wake, false);
	assert.deepEqual(events(t.home, "wake_delivered"), []);
});

test("auto_wake false: an orphaned claim file is still put back in the inbox", async () => {
	const t = turnEndLead([TURN_JSON()]);
	writeCfg(t.home, JSON.stringify({ auto_wake: false }));
	await t.fire("session_start", {}, t.ctx);
	const orphan = `${t.inbox}.claim-${endedPid()}-${Date.now()}-0`;
	fs.writeFileSync(orphan, "reported exec-o /r/o.md\n");
	assert.equal(t.handle.drainInbox(), 0);
	assert.ok(!fs.existsSync(orphan));
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "reported exec-o /r/o.md\n");
	assert.equal(t.sent.length, 0);
	assert.equal(events(t.home, "wake_recovered").length, 1);
});

for (const [name, text] of [
	['"no"', JSON.stringify({ auto_wake: "no" })],
	["0", JSON.stringify({ auto_wake: 0 })],
	["null", JSON.stringify({ auto_wake: null })],
	["true", JSON.stringify({ auto_wake: true })],
	["not valid JSON", "{ auto_wake: false"],
	["an array", "[false]"],
	["a file without the key", JSON.stringify({ poll_interval: 3 })],
] as const) {
	test(`auto_wake ${name}: wakes are delivered, and the health file says true`, async () => {
		const t = turnEndLead([TURN_JSON()]);
		writeCfg(t.home, text);
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
		assert.equal(t.handle.drainInbox(), 1);
		await t.fire("agent_settled", {}, t.ctx);
		await t.handle.flush();
		assert.equal(t.sent.length, 2, "the wake and the turn-end message");
		assert.equal(healthOf(t.home, t.sid).auto_wake, true);
	});
}

test("auto_wake: readAutoWake, and loadConfig still has the four keys it had", () => {
	const home = fs.mkdtempSync(path.join(SANDBOX, "aw-"));
	assert.equal(ext.readAutoWake(home), true, "no file");
	writeCfg(home, JSON.stringify({ auto_wake: false, edit_line_threshold: 7 }));
	assert.equal(ext.readAutoWake(home), false);
	assert.deepEqual(ext.loadConfig(home), { edit_line_threshold: 7, block_on_new_file: true, grace_seconds: 120, poll_interval: 3 });
	assert.deepEqual(Object.keys(ext.CONFIG_DEFAULTS), ["edit_line_threshold", "block_on_new_file", "grace_seconds", "poll_interval"]);
});

test("auto_wake false on a quiet lead: home is byte-identical, orphans included", async () => {
	const { home, sid, inbox } = waitingLead();
	writeCfg(home, JSON.stringify({ auto_wake: false }));
	const orphan = `${inbox}.claim-${endedPid()}-${Date.now()}-0`;
	fs.writeFileSync(orphan, REPORTED_LINE);
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" };
	const t = withEnv(env, { pid: 4242 }, "tui");
	const before = treeOf(home);
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	assert.deepEqual(treeOf(home), before);
});

// ---- the wake's facts: the report's first line, the size of the diff, a landing, three tries ----------

/** A stub `pilead` (sh) for `check <sid> --refs-only --wake …`: logs the sid, prints `refs: stub for <sid>`, then
 *  what `<dir>/second` holds (the wake line, when a test writes it). */
function factsPilead(second?: string): { bin: string; log: string; dir: string; setSecond: (t: string | null) => void } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "facts-pilead-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	fs.writeFileSync(
		bin,
		`#!/bin/sh\necho "$2" >> ${JSON.stringify(log)}\necho "refs: stub for $2"\n[ -f ${JSON.stringify(dir)}/second ] && cat ${JSON.stringify(dir)}/second\nexit 0\n`,
	);
	fs.chmodSync(bin, 0o755);
	const setSecond = (t: string | null) => (t === null ? fs.rmSync(path.join(dir, "second"), { force: true }) : fs.writeFileSync(path.join(dir, "second"), t));
	if (second !== undefined) setSecond(second);
	return { bin, log, dir, setSecond };
}
const stubCalls = (log: string): string[] => (fs.existsSync(log) ? fs.readFileSync(log, "utf-8").split("\n").filter(Boolean) : []);
const wakeLine = (o: Record<string, unknown>) => `wake: ${JSON.stringify({ packet: 1, diff: null, diff_state: "empty", landed: null, proven: false, ...o })}\n`;
const DIFF3 = "(diff: 3 files +10/-2)";
const LANDED_NOTE = "\n       its claimed files are clean at HEAD — it may have been committed already";

/** A registered lead whose wake facts come from a stub pilead, and executor `exec-f` with a report on disk. */
function factsLead(second: string | undefined, report: string | null = "# Split-pane works\n\nStatus: clean\n", opts: any = {}) {
	const st = factsPilead(second);
	const t = setup("lead", {}, { pileadBin: st.bin, ...opts });
	const inbox = registerLead(t.home, t.sid);
	const execSid = "exec-f";
	const dir = path.join(t.home, "sessions", execSid);
	fs.mkdirSync(dir, { recursive: true });
	const reportPath = path.join(dir, "report-0001.md");
	if (report !== null) fs.writeFileSync(reportPath, report);
	return { ...t, st, inbox, execSid, dir, reportPath, line: `reported ${execSid} ${reportPath}\n` };
}
const base = (t: { execSid: string; reportPath: string }) => `🚦 [pi-lead] executor ${t.execSid} reported: ${t.reportPath} — refs: stub for ${t.execSid}`;

async function wakeOnce(t: ReturnType<typeof factsLead>) {
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
}

test("wake facts: all three — the message and details compared whole, manual and autonomous", async () => {
	const second = wakeLine({ diff: DIFF3, diff_state: "staged", landed: true, proven: false });
	for (const autonomous of [false, true]) {
		const t = factsLead(second);
		if (autonomous) setLeadRecord(t.home, t.sid, { autonomous: true });
		await wakeOnce(t);
		const content = `${base(t)} ${DIFF3}\n       Split-pane works${LANDED_NOTE}`;
		const details: Record<string, unknown> = { sid: t.execSid, report: t.reportPath, refs: `refs: stub for ${t.execSid}`, diff: DIFF3, brief: "Split-pane works", landed: "unproven" };
		assert.deepEqual(t.sent, [
			{
				message: {
					customType: "pi-lead",
					content: autonomous ? content + "\n\n" + AUTONOMOUS_TEXT : content,
					display: true,
					details: autonomous ? { ...details, autonomous: true } : details,
				},
				options: { triggerTurn: true },
			},
		]);
		assert.deepEqual(stubCalls(t.st.log), [t.execSid], "the child is asked once for the wake");
	}
});

test("wake facts: each fact alone adds only its own text", async () => {
	const cases: [string, string | undefined, string | null, string, Record<string, unknown>][] = [
		["diff", wakeLine({ diff: DIFF3, diff_state: "staged" }), null, ` ${DIFF3}`, { diff: DIFF3 }],
		["brief", undefined, "First line here\nmore\n", "\n       First line here", { brief: "First line here" }],
		["landed, unproven", wakeLine({ landed: true, proven: false }), null, LANDED_NOTE, { landed: "unproven" }],
	];
	for (const [name, second, report, add, extra] of cases) {
		const t = factsLead(second, report);
		await wakeOnce(t);
		assert.equal(t.sent[0].message.content, base(t) + add, name);
		assert.deepEqual(t.sent[0].message.details, { sid: t.execSid, report: t.reportPath, refs: `refs: stub for ${t.execSid}`, ...extra }, name);
	}
});

test("wake facts: a wake: line that is not JSON, one whose diff is a number, and none at all give today's message", async () => {
	for (const second of ["wake: {not json\n", "wake: \n", 'wake: {"diff": 5, "landed": "yes", "proven": "true"}\n', 'wake: [1,2]\n', "wake:{}\n", undefined]) {
		const t = factsLead(second, null);
		await wakeOnce(t);
		assert.equal(t.sent[0].message.content, base(t), String(second));
		assert.deepEqual(t.sent[0].message.details, { sid: t.execSid, report: t.reportPath, refs: `refs: stub for ${t.execSid}` }, String(second));
	}
});

test("wake facts: a landed that is not `true` and a proven that is not `true` say nothing", async () => {
	for (const o of [{ landed: false }, { landed: null }, { landed: "true" }, { landed: 1 }]) {
		const t = factsLead(wakeLine(o), null);
		await wakeOnce(t);
		assert.equal(t.sent[0].message.content, base(t), JSON.stringify(o));
	}
	// landed true with a proven that is not the JSON value true: not proven, so the note is there and it is sent
	const t = factsLead(wakeLine({ landed: true, proven: "true" }), null);
	await wakeOnce(t);
	assert.equal(t.sent[0].message.content, base(t) + LANDED_NOTE);
});

test("wake facts: an injected refsCheck gives the refs line only — no child is run and no fact is read", async () => {
	const st = factsPilead(wakeLine({ diff: DIFF3, landed: true, proven: true }));
	const t = setup("lead", {}, { pileadBin: st.bin, refsCheck: () => "refs: injected" });
	const inbox = registerLead(t.home, t.sid);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(inbox, "reported exec-a /h/sessions/exec-a/report-0001.md\n");
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.deepEqual(t.sent, [plainWake("refs: injected")]);
	assert.deepEqual(stubCalls(st.log), []);
});

test("wakeFacts: the refs line is refsLine's, the facts come from the wake: line, and it never rejects", async () => {
	const st = factsPilead(wakeLine({ diff: DIFF3, diff_state: "staged", landed: false, proven: true }));
	assert.deepEqual(await ext.wakeFacts(SANDBOX, "exec-x", st.bin), { refs: "refs: stub for exec-x", diff: DIFF3, landed: false, proven: true });
	assert.equal((await ext.wakeFacts(SANDBOX, "exec-x", st.bin)).refs, await ext.refsLine(SANDBOX, "exec-x", st.bin));
	// the wake line first: it is the first non-empty line, as refsLine would take it
	st.setSecond(null);
	const other = stubPilead();
	for (const sid of ["ok-1", "fail-1", "hang-1"]) {
		const a = await ext.wakeFacts(SANDBOX, sid, other.bin, 300);
		// (a failed child's message names its command line, which differs by the one option)
		assert.equal(a.refs.replace(" --wake", ""), await ext.refsLine(SANDBOX, sid, other.bin, 300), sid);
		assert.deepEqual([a.diff, a.landed, a.proven], [null, null, false], sid);
	}
	const missing = await ext.wakeFacts(SANDBOX, "exec-x", path.join(SANDBOX, "no-such-pilead"));
	assert.match(missing.refs, /^refs: not checked \(/);
	assert.deepEqual([missing.diff, missing.landed, missing.proven], [null, null, false]);
	const ac = new AbortController();
	const pending = ext.wakeFacts(SANDBOX, "hang-2", other.bin, 30_000, ac.signal);
	ac.abort();
	assert.equal((await pending).refs, "refs: not checked (aborted)");
	// an empty answer
	const silent = path.join(st.dir, "silent");
	fs.writeFileSync(silent, "#!/bin/sh\nexit 0\n");
	fs.chmodSync(silent, 0o755);
	assert.equal((await ext.wakeFacts(SANDBOX, "exec-x", silent)).refs, "refs: not checked (no output from pilead check --refs-only)");
});

// -- reportBrief: the shared table, Python's own brief, and the paths it will read

const BRIEFS_PATH = path.join(REPO, "tests", "briefs.json");
const BRIEFS: { name: string; report: string; brief: string }[] = JSON.parse(fs.readFileSync(BRIEFS_PATH, "utf-8"));

test("reportBrief agrees with tests/briefs.json and with Python's brief on every row", () => {
	assert.ok(BRIEFS.length >= 15);
	const py: (string | null)[] = JSON.parse(
		execFileSync(
			"python3",
			[
				"-c",
				`import json, sys; sys.path.insert(0, ${JSON.stringify(path.join(REPO, "lib"))}); from pilead import notify; rows = json.load(open(${JSON.stringify(BRIEFS_PATH)}, encoding='utf-8')); print(json.dumps([notify.brief(r['report']) for r in rows]))`,
			],
			{ encoding: "utf-8" },
		),
	);
	const home = path.join(SANDBOX, `briefs-${seq++}`);
	const dir = path.join(home, "sessions", "exec-b");
	fs.mkdirSync(dir, { recursive: true });
	BRIEFS.forEach((row, i) => {
		const file = path.join(dir, `report-${String(i + 1).padStart(4, "0")}.md`);
		fs.writeFileSync(file, Buffer.from(row.report, "utf-8"));
		assert.equal(ext.reportBrief(home, "exec-b", file), row.brief === "" ? null : row.brief, row.name);
		assert.equal(py[i], row.brief, `python: ${row.name}`);
	});
});

test("reportBrief reads only a regular file of at most 1 MB directly inside the executor's own session directory", () => {
	const home = path.join(SANDBOX, `brief-paths-${seq++}`);
	const dir = path.join(home, "sessions", "exec-b");
	const other = path.join(home, "sessions", "exec-c");
	fs.mkdirSync(dir, { recursive: true });
	fs.mkdirSync(other, { recursive: true });
	const own = path.join(dir, "report-0001.md");
	fs.writeFileSync(own, "# Mine\n");
	fs.writeFileSync(path.join(other, "report-0001.md"), "# Theirs\n");
	const outside = path.join(SANDBOX, `brief-outside-${seq}.md`);
	fs.writeFileSync(outside, "# Outside\n");
	assert.equal(ext.reportBrief(home, "exec-b", own), "Mine");
	assert.equal(ext.reportBrief(home, "exec-b", path.join(other, "report-0001.md")), null, "another session's report");
	assert.equal(ext.reportBrief(home, "exec-b", outside), null, "a file outside home");
	assert.equal(ext.reportBrief(home, "exec-b", `${dir}/../exec-c/report-0001.md`), null, "a path with ..");
	assert.equal(ext.reportBrief(home, "exec-b", `${dir}/../exec-b/report-0001.md`), null, "a path with .. that comes back");
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "sub", "report-0001.md")), null, "not directly inside");
	fs.symlinkSync(outside, path.join(dir, "link.md"));
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "link.md")), null, "a link that leaves the directory");
	fs.symlinkSync(path.join(other, "report-0001.md"), path.join(dir, "link2.md"));
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "link2.md")), null, "a link into another session");
	fs.mkdirSync(path.join(dir, "report-0002.md"));
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "report-0002.md")), null, "a directory");
	fs.writeFileSync(path.join(dir, "report-0003.md"), Buffer.alloc(1024 * 1024 + 1, "a"));
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "report-0003.md")), null, "over 1 MB");
	fs.writeFileSync(path.join(dir, "report-0004.md"), Buffer.alloc(1024 * 1024, "a"));
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "report-0004.md")), "a".repeat(200), "exactly 1 MB is read");
	assert.equal(ext.reportBrief(home, "exec-b", path.join(dir, "missing.md")), null, "no such file");
	assert.equal(ext.reportBrief(home, "exec-b", ""), null);
	for (const bad of ["", "../exec-b", "exec-b/..", "a/b", "exec b"]) assert.equal(ext.reportBrief(home, bad, own), null, JSON.stringify(bad));
	assert.equal(ext.reportBrief(home, undefined as any, own), null);
	assert.equal(ext.reportBrief(home, "exec-b", undefined as any), null);
});

// -- a wake that is not sent: the work is proven committed

test("wake facts: proven landed sends nothing, empties the inbox, ledgers wake_skipped_landed and no wake_delivered, asks for no banner", async () => {
	const banners: string[][] = [];
	const t = factsLead(wakeLine({ diff: DIFF3, diff_state: "staged", landed: true, proven: true }), "# Landed\n", { banner: (a: string[]) => void banners.push(a) });
	(t.ctx as any).mode = "tui";
	await wakeOnce(t);
	assert.deepEqual(t.sent, []);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
	assert.deepEqual([...claimsOf(t.inbox).keys()], [], "no claim file is left");
	assert.deepEqual(events(t.home, "wake_skipped_landed"), [
		{ session_id: t.sid, executor: t.execSid, packet: 1, report: t.reportPath, reason: "landed", proven: true },
	]);
	assert.deepEqual(events(t.home, "wake_delivered"), []);
	assert.deepEqual(banners, []);
	// the next report of another executor in the same inbox is an ordinary wake
	fs.appendFileSync(t.inbox, `reported exec-g ${t.home}/sessions/exec-g/report-0002.md\n`);
	t.st.setSecond(null);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.equal(events(t.home, "wake_delivered").length, 1);
});

test("wake facts: landed and not proven is sent with the third line and details.landed", async () => {
	const t = factsLead(wakeLine({ landed: true, proven: false }), null);
	await wakeOnce(t);
	assert.equal(t.sent.length, 1);
	assert.equal(t.sent[0].message.details.landed, "unproven");
	assert.deepEqual(events(t.home, "wake_skipped_landed"), []);
});

// -- three tries

/** The wake of `exec-f` whose send always throws `boom`, under a clock the test moves. */
function failingLead(extra: any = {}) {
	const clock = { ms: 1_000_000 };
	const t = factsLead(undefined, null, { now: () => clock.ms, retryMs: 100, ...extra });
	const calls: string[] = [];
	const send = t.pi.sendMessage;
	let failing = true;
	t.pi.sendMessage = (m: any, o: any) => {
		calls.push(m.details.sid);
		if (failing && m.details.sid === "exec-f") throw new Error("boom\nsecond line");
		send(m, o);
	};
	const tick = async () => {
		clock.ms += 200;
		const n = t.handle.drainInbox();
		await t.handle.flush();
		return n;
	};
	return { ...t, clock, calls, tick, stop: () => (failing = false) };
}

test("wake retries: WAKE_RETRY_CAP is 3", () => assert.equal(ext.WAKE_RETRY_CAP, 3));

test("wake retries: a send that always throws gives three calls, one wake_retry_capped, one notice, the line kept, and no fourth call", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.calls.length, 1);
	assert.equal(await t.tick(), 1);
	assert.equal(t.calls.length, 2);
	assert.deepEqual(events(t.home, "wake_retry_capped"), [], "not yet");
	assert.deepEqual(t.notes, []);
	assert.equal(await t.tick(), 1);
	assert.equal(t.calls.length, 3);
	assert.deepEqual(events(t.home, "wake_retry_capped"), [
		{ session_id: t.sid, executor: t.execSid, report: t.reportPath, attempts: 3, error: "boom" },
	]);
	assert.deepEqual(t.notes, [
		{
			msg: "pi-lead: the wake for exec-f could not be handed over after 3 tries (boom) — it stays in the inbox; pilead check exec-f shows the report",
			type: "warning",
		},
	]);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), t.line, "the line is back in the inbox");
	const askedBefore = stubCalls(t.st.log).length;
	for (let i = 0; i < 6; i++) assert.equal(await t.tick(), 0);
	assert.equal(t.calls.length, 3, "no fourth call however often the drain runs and the clock moves");
	assert.equal(stubCalls(t.st.log).length, askedBefore, "and no child is asked for it");
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), t.line);
	assert.equal(events(t.home, "wake_retry_capped").length, 1, "nothing more is written");
	assert.equal(t.notes.length, 1);
	assert.deepEqual([...claimsOf(t.inbox).keys()], []);
});

test("wake retries: a second line in the same inbox is still delivered; the capped one stays exactly once", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	for (let i = 0; i < 3; i++) {
		await t.tick();
	}
	assert.equal(t.calls.length, 3);
	const g = `reported exec-g ${t.home}/sessions/exec-g/report-0001.md\n`;
	fs.appendFileSync(t.inbox, g);
	assert.equal(await t.tick(), 1, "only the new line is claimed for sending");
	assert.deepEqual(t.calls, ["exec-f", "exec-f", "exec-f", "exec-g"]);
	assert.deepEqual(t.sent.map((x) => x.message.details.sid), ["exec-g"]);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), t.line);
	assert.deepEqual(stubCalls(t.st.log).filter((s) => s === "exec-f").length, 3, "exec-f was never asked about after its third try");
	for (let i = 0; i < 3; i++) assert.equal(await t.tick(), 0);
	assert.equal(t.calls.length, 4);
});

test("wake retries: a new runtime on the same home sends the first line", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	for (let i = 0; i < 3; i++) await t.tick();
	assert.equal(t.calls.length, 3);
	const stub2 = stubPi();
	const h2 = ext.createPiLead(stub2.pi, { env: { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: t.home, PI_LEAD_SID: t.sid }, watch: false, pollMs: 0, pileadBin: t.st.bin });
	await stub2.fire("session_start", {}, { cwd: t.cwd, hasUI: true, sessionManager: { getSessionId: () => t.sid }, ui: { notify() {} } });
	await h2.flush();
	assert.deepEqual(stub2.sent.map((x) => x.message.details.sid), ["exec-f"], "the new runtime starts its count at 0");
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
	assert.equal(events(t.home, "wake_delivered").length, 1);
});

test("wake retries: the same runtime after session_shutdown and session_start tries the line again", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	for (let i = 0; i < 3; i++) await t.tick();
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	t.stop();
	await t.fire("session_start", {}, t.ctx);
	await t.handle.flush();
	assert.equal(t.sent.length, 1, "delivered after the restart");
});

test("wake retries: two failures and then a success write no event and no notice", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, t.line);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	await t.tick();
	assert.equal(t.calls.length, 2);
	t.stop();
	await t.tick();
	assert.equal(t.calls.length, 3);
	assert.equal(t.sent.length, 1);
	assert.deepEqual(events(t.home, "wake_retry_capped"), []);
	assert.deepEqual(t.notes, []);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
	// the count of that line was cleared by the success: a later report of the same line fails afresh
	t.pi.sendMessage = () => {
		throw new Error("again");
	};
	fs.appendFileSync(t.inbox, t.line);
	for (let i = 0; i < 3; i++) await t.tick();
	assert.equal(events(t.home, "wake_retry_capped").length, 1);
});

test("wake retries: with no ctx at hand the cap is ledgered and nothing is shown", async () => {
	const t = failingLead();
	await t.fire("session_start", {}, t.ctx);
	(t.ctx as any).ui.notify = () => {
		throw new Error("stale ctx");
	};
	fs.appendFileSync(t.inbox, t.line);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	await t.tick();
	await t.tick();
	assert.equal(events(t.home, "wake_retry_capped").length, 1);
	assert.equal(t.calls.length, 3);
});

// -- the guard against a second pi: a quiet lead does none of this

test("wake facts: a quiet lead runs no child, sends nothing, skips and caps nothing, and its inbox is byte-identical", async () => {
	for (const second of [wakeLine({ landed: true, proven: true }), wakeLine({ diff: DIFF3, landed: true, proven: false })]) {
		const st = factsPilead(second);
		const { home, sid, inbox } = waitingLead();
		const report = path.join(home, "sessions", "exec-q", "report-0001.md");
		fs.mkdirSync(path.dirname(report), { recursive: true });
		fs.writeFileSync(report, "# Quiet\n");
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" };
		const t = withEnv(env, { pid: 4242, pileadBin: st.bin, refsCheck: undefined, retryMs: 10 }, "tui");
		const before = fs.readFileSync(inbox, "utf-8");
		const tree0 = treeOf(home);
		await t.fire("session_start", {}, t.ctx);
		for (let i = 0; i < 4; i++) {
			assert.equal(t.handle.drainInbox(), 0);
			await t.handle.flush();
		}
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		assert.equal(fs.readFileSync(inbox, "utf-8"), before);
		assert.deepEqual([t.sent, stubCalls(st.log)], [[], []]);
		assert.deepEqual(treeOf(home), tree0, "no ledger line, no claim file");
	}
});


// ---- p20g: a registered lead's bash commands are put to the bash write gate ------------------------

const BASH_TABLE: { command: string; verdict: string; mode: string }[] = JSON.parse(
	fs.readFileSync(path.join(REPO, "tests", "bash-gate-commands.json"), "utf-8"),
);
const CONTROL_FILES: { path: string; kind: string | null }[] = JSON.parse(fs.readFileSync(path.join(REPO, "tests", "control-files.json"), "utf-8"));
const NOT_CHECKED = (why: string) => `pilead: the bash gate could not check this command (${why})`;
const heredoc41 = (target: string) => `cat > ${target} <<EOF\n${lines(41)}EOF`;

/** A lead whose bash gate is the injected `answer` (every call recorded); registered unless told otherwise. */
function bashLead(answer: (command: string, cwd: string) => unknown, { register = true, extraEnv = {} as Record<string, string>, opts = {} as any } = {}) {
	const calls: { command: string; cwd: string }[] = [];
	const t = setup("lead", extraEnv, {
		bashGate: (command: string, cwd: string) => {
			calls.push({ command, cwd });
			return answer(command, cwd);
		},
		...opts,
	});
	if (register) registerLead(t.home, t.sid);
	const bash = (command: unknown) => t.fire("tool_call", { toolName: "bash", input: { command } }, t.ctx);
	return { ...t, calls, bash };
}
const BLOCK_ANSWER = JSON.stringify({ verdict: "block", mode: "deny", reason: "R: refused", targets: [], rule: "bash-write", parsed: true });
const answerOf = (verdict: string, parsed: unknown = true) => JSON.stringify({ verdict, mode: "log", reason: null, targets: [], rule: null, parsed });

test("bash gate, row 1: a session with no usable id, or not registered, is never asked, whatever the command", async () => {
	const unregistered = bashLead(() => BLOCK_ANSWER, { register: false });
	for (const c of [heredoc41("x.py"), "echo 1 > ~/.pi-lead/config.json", "npm install", "ls"]) {
		assert.equal(await unregistered.bash(c), undefined, c);
	}
	assert.deepEqual(unregistered.calls, []);
	const noId = bashLead(() => BLOCK_ANSWER, { extraEnv: { PI_LEAD_SID: "../bad" } });
	noId.ctx.sessionManager.getSessionId = () => "../also-bad";
	registerLead(noId.home, "..%2fbad");
	assert.equal(await noId.bash("echo x > a.py"), undefined);
	assert.deepEqual(noId.calls, []);
	assert.deepEqual(unregistered.notes.concat(noId.notes).filter((n) => n.msg.startsWith("pilead: the bash gate")), []);
});

test("bash gate, row 2: a command with no hint runs at once and is never asked", async () => {
	const t = bashLead(() => BLOCK_ANSWER);
	for (const c of ["pilead list", "ls -la", "cat README.md", "git commit -m x", "git status", "pytest -q", "", "   "]) {
		assert.equal(await t.bash(c), undefined, c);
	}
	for (const c of [undefined, null, 42, ["echo x > a"], { command: "x > y" }]) assert.equal(await t.bash(c), undefined, String(c));
	assert.deepEqual(t.calls, []);
	assert.deepEqual(t.notes, []);
});

test("bash gate, row 3: a block answer with a reason refuses the command with the reason unchanged", async () => {
	const t = bashLead(() => BLOCK_ANSWER);
	const r = await t.bash(heredoc41("tracked.py"));
	assert.deepEqual(r, { block: true, reason: "R: refused" });
	assert.deepEqual(t.calls, [{ command: heredoc41("tracked.py"), cwd: t.cwd }]);
	const p = bashLead(() => Promise.resolve(JSON.stringify({ verdict: "block", reason: "  odd  reason\u2014kept ", parsed: false })));
	assert.deepEqual(await p.bash("echo x > y"), { block: true, reason: "  odd  reason\u2014kept " });
	assert.deepEqual(p.notes, [], "a refusal shows no warning");
	assert.deepEqual(readLedger(t.home), [], "the extension writes no ledger event of its own for a bash command");
});

test("bash gate, row 4: anything but a block with a reason runs — allow and log quietly, the rest with one warning", async () => {
	const cases: [string, () => unknown, string | null][] = [
		["allow", () => answerOf("allow"), null],
		["log", () => answerOf("log"), null],
		["allow with a newline", () => `${answerOf("allow")}\n`, null],
		["not json", () => "not json", "unreadable answer"],
		["[1]", () => "[1]", "unreadable answer"],
		["{}", () => "{}", "unreadable answer"],
		["block with no reason", () => '{"verdict":"block"}', "unreadable answer"],
		["block with an empty reason", () => '{"verdict":"block","reason":""}', "unreadable answer"],
		["block with a reason that is not a text", () => '{"verdict":"block","reason":7}', "unreadable answer"],
		["two JSON lines", () => `${answerOf("allow")}\n${BLOCK_ANSWER}`, "unreadable answer"],
		["an empty answer", () => "", "unreadable answer"],
		["allow, not read", () => answerOf("allow", false), "not read"],
		["log, not read", () => answerOf("log", false), "not read"],
		["a function that throws", () => {
			throw new Error("boom");
		}, "failed"],
		["a promise that rejects", () => Promise.reject(new Error("no")), "failed"],
	];
	for (const [name, answer, warn] of cases) {
		const t = bashLead(answer);
		assert.equal(await t.bash("echo x > lib/new.py"), undefined, name);
		assert.equal(t.calls.length, 1, name);
		assert.deepEqual(t.notes, warn === null ? [] : [{ msg: NOT_CHECKED(warn), type: "warning" }], name);
	}
});

test("bash gate: a ctx whose notify throws still lets the command run; the handler never throws", async () => {
	const t = bashLead(() => "not json");
	t.ctx.ui.notify = () => {
		throw new Error("ui gone");
	};
	assert.equal(await t.bash("echo x > a"), undefined);
	const u = bashLead(() => answerOf("allow"));
	u.ctx.cwd = undefined as any;
	assert.equal(await u.bash("echo x > a"), undefined);
	assert.equal(u.calls[0].cwd, process.cwd(), "no cwd from pi: the process's own");
});

test("bash gate: the executor's bash is the git fence exactly as before, and bashGate is never called", async () => {
	const calls: string[] = [];
	const t = setup("executor", {}, { bashGate: (c: string) => (calls.push(c), BLOCK_ANSWER) });
	registerLead(t.home, t.sid);
	const call = (command: string) => t.fire("tool_call", { toolName: "bash", input: { command } }, t.ctx);
	assert.deepEqual(await call("git add -A && bash -c 'git commit -m t'"), {
		block: true,
		reason: "pi-lead executor: stage your work and report; the lead commits. (blocked: git commit -m t)",
	});
	for (const c of ["git add -A", "echo x > lib/new.py", heredoc41("tracked.py"), "echo 1 > ~/.pi-lead/config.json", "npm install"]) {
		assert.equal(await call(c), undefined, c);
	}
	assert.deepEqual(calls, []);
});

test("bash gate: the edit and write handler gives the gate matrix's results, and never asks the bash gate", async () => {
	const t = bashLead(() => BLOCK_ANSWER);
	const call = (toolName: string, input: any) => t.fire("tool_call", { toolName, input }, t.ctx);
	const existing = path.join(t.cwd, "existing.ts");
	fs.writeFileSync(existing, lines(10));
	assert.equal(await call("edit", { path: existing, edits: [{ oldText: "line 1", newText: "LINE 1" }] }), undefined);
	assert.equal(await call("write", { path: "existing.ts", content: lines(40) }), undefined);
	assert.equal((await call("write", { path: "existing.ts", content: lines(41) }))?.block, true);
	const r = await call("write", { path: "src/new.ts", content: "x\n" });
	assert.equal(r?.block, true);
	assert.match(r.reason, /\(A write made through bash meets the same gate where its shape can be read\.\)$/);
	assert.equal(await call("write", { path: "drafts/007-packet.md", content: lines(300) }), undefined);
	assert.equal(await call("read", { path: "src/new.ts" }), undefined);
	assert.deepEqual(t.calls, []);
});

test("bashGateHint: true for every command the gate answers log or block (tests/bash-gate-commands.json)", () => {
	const gated = BASH_TABLE.filter((r) => r.verdict === "log" || r.verdict === "block");
	assert.ok(gated.length >= 60, String(gated.length));
	for (const row of gated) assert.equal(ext.bashGateHint(row.command), true, row.command);
	for (const c of ["pilead list", "ls -la", "cat README.md", "git commit -m x"]) assert.equal(ext.bashGateHint(c), false, c);
	for (const v of [undefined, null, 1, {}, [], ""]) assert.equal(ext.bashGateHint(v), false, String(v));
	assert.ok(ext.BASH_GATE_HINT instanceof RegExp);
	for (const c of ["a > b", "cat <<X", "tee f", "gsed -i x f", "cp a b", "mv a b", "python3.11 x", "python x", "yarn add x", "pip3 install x",
		"tsc -p .", "g++ a.c", "clang x", "go build", "cargo build", "make", "git  clone u", "systemctl enable x", "rsync a b",
		"cat /etc/systemd/system/x.service", "awk 1 f", "touch ~/.PI-LEAD/leads/x.route", "rm $PI_LEAD_HOME/Config.JSON", "rm ledger.jsonl"]) {
		assert.equal(ext.bashGateHint(c), true, c);
	}
});

test("bashGateAsks: the hint, a command naming home's own path, or a cwd inside home", () => {
	const home = path.join(SANDBOX, `asks-home-${seq++}`);
	fs.mkdirSync(path.join(home, "leads"), { recursive: true });
	const link = path.join(SANDBOX, `asks-link-${seq++}`);
	fs.symlinkSync(home, link);
	const repo = path.join(SANDBOX, `asks-repo-${seq++}`);
	fs.mkdirSync(repo);
	assert.equal(ext.bashGateAsks("ls -la", repo, home), false);
	assert.equal(ext.bashGateAsks("echo x > a", repo, home), true);
	assert.equal(ext.bashGateAsks(`rm -rf ${home}`, repo, home), true, "home's path, as given");
	assert.equal(ext.bashGateAsks(`rm -rf ${home.toUpperCase()}`, repo, home), true, "in any case");
	assert.equal(ext.bashGateAsks("find . -delete", home, home), true, "cwd is home");
	assert.equal(ext.bashGateAsks("rm -rf ..", path.join(home, "leads"), home), true, "cwd inside home");
	assert.equal(ext.bashGateAsks("rm -rf ..", path.join(link, "leads"), home), true, "through a link");
	assert.equal(ext.bashGateAsks("", home, home), false);
	assert.equal(ext.bashGateAsks(7, home, home), false);
});

test("control files: controlFileKind agrees with tests/control-files.json (lib/pilead/bashgate.py reads it too)", () => {
	const home = path.join(SANDBOX, `cf-home-${seq++}`, ".pi-lead");
	fs.mkdirSync(path.join(home, "leads"), { recursive: true });
	assert.ok(CONTROL_FILES.length >= 12);
	for (const row of CONTROL_FILES) {
		const abs = row.path.startsWith("/") ? row.path : path.join(home, row.path);
		assert.equal(ext.controlFileKind(abs, home), row.kind, row.path);
	}
});

test("control files: the lead's record is gated at any size, like the other three", async () => {
	const t = setup("lead");
	registerLead(t.home, t.sid);
	const rec = path.join(t.home, "leads", `${t.sid}.json`);
	const r = await t.fire("tool_call", { toolName: "edit", input: { path: rec, edits: [{ oldText: "a", newText: "b" }] } }, t.ctx);
	assert.equal(r?.block, true);
	assert.equal(r.reason, controlReason(rec, "lead record"));
	assert.equal(fieldsOf(readLedger(t.home).at(-1)).control, "lead record");
	assert.equal(await t.fire("tool_call", { toolName: "write", input: { path: path.join(t.home, "leads", "x.inbox.md"), content: "x\n" } }, t.ctx), undefined);
});

test("/pilead:status: the lead's lines end with the bash gate line; the executor's have none", async () => {
	const t = setup("lead");
	await t.commands.get("pilead:status")!.handler("", t.ctx);
	assert.equal(t.notes.at(-1)!.msg.split("\n").at(-1), "bash gate: asked for commands that write or install (mode is config bash_write_gate)");
	const ex = setup("executor");
	await ex.commands.get("pilead:status")!.handler("", ex.ctx);
	assert.ok(!ex.notes.at(-1)!.msg.includes("bash gate"));
});

/** A stub pilead that logs each argument on its own line and its stdin, then prints `answer` (or sleeps). */
function gateStub(answer: string, sleepS = 0): { bin: string; argv: string; stdin: string } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "gate-stub-"));
	const bin = path.join(dir, "pilead");
	const argv = path.join(dir, "argv");
	const stdin = path.join(dir, "stdin");
	fs.writeFileSync(
		bin,
		`#!/bin/sh\ntrap 'kill $! 2>/dev/null; exit 143' TERM\nfor a in "$@"; do printf '%s\\n' "$a" >> ${JSON.stringify(argv)}; done\ncat > ${JSON.stringify(stdin)}\n` +
			(sleepS ? `sleep ${sleepS} & wait $!\n` : "") +
			`printf '%s\\n' ${JSON.stringify(answer).replace(/\$/g, "\\$")}\n`,
	);
	fs.chmodSync(bin, 0o755);
	return { bin, argv, stdin };
}

test("bash gate: the default child gets the command on stdin, never on its command line, and no shell", async () => {
	const st = gateStub(BLOCK_ANSWER);
	const t = setup("lead", {}, { pileadBin: st.bin });
	registerLead(t.home, t.sid);
	const cmd = "echo \"$(rm -rf /nope)\" > `x`; touch '; exit 1' && cat <<EOF\n$HOME\nEOF";
	assert.deepEqual(await t.fire("tool_call", { toolName: "bash", input: { command: cmd } }, t.ctx), { block: true, reason: "R: refused" });
	assert.deepEqual(fs.readFileSync(st.argv, "utf-8").split("\n").slice(0, -1), [
		"gate", "--session", t.sid, "--cwd", t.cwd, "--record", "--json", "--home", t.home,
	]);
	assert.equal(fs.readFileSync(st.stdin, "utf-8"), cmd);
	assert.equal(ext.BASH_GATE_TIMEOUT_MS, 5000);
});

test("bashGateReply: timed out, failed, and a missing pilead never reject", async () => {
	const slow = gateStub(answerOf("allow"), 5);
	const t0 = Date.now();
	assert.deepEqual(await ext.bashGateReply("echo x > a", SANDBOX, SANDBOX, "lead-x", slow.bin, 300), { why: "timed out" });
	assert.ok(Date.now() - t0 < 3000);
	assert.deepEqual(await ext.bashGateReply("echo x > a", SANDBOX, SANDBOX, "lead-x", path.join(SANDBOX, "no-such-pilead")), { why: "failed" });
	const dir = fs.mkdtempSync(path.join(SANDBOX, "gate-fail-"));
	const fail = path.join(dir, "pilead");
	fs.writeFileSync(fail, "#!/bin/sh\nexit 2\n");
	fs.chmodSync(fail, 0o755);
	assert.deepEqual(await ext.bashGateReply("x > y", SANDBOX, SANDBOX, "lead-x", fail), { why: "failed" });
	const big = "echo " + "x".repeat(2_000_000) + " > a";
	const quick = path.join(dir, "quick");
	fs.writeFileSync(quick, `#!/bin/sh\nprintf '%s\\n' '${answerOf("allow")}'\n`);
	fs.chmodSync(quick, 0o755);
	assert.deepEqual(await ext.bashGateReply(big, SANDBOX, SANDBOX, "lead-x", quick), { text: `${answerOf("allow")}\n` }, "a child that never reads stdin");
});

test("bash gate: the REAL pilead gate — deny blocks a 41-line heredoc into a tracked file, log runs it, a missing pilead runs it", async () => {
	for (const mode of ["deny", "log", "missing"] as const) {
		const t = setup("lead", {}, mode === "missing" ? { pileadBin: path.join(SANDBOX, "no-such-pilead") } : {});
		registerLead(t.home, t.sid);
		writeConfig(t.home, { bash_write_gate: mode === "missing" ? "deny" : mode, edit_line_threshold: 40 });
		git(t.cwd, "init", "-q");
		fs.writeFileSync(path.join(t.cwd, "tracked.py"), "x\n");
		git(t.cwd, "add", "tracked.py");
		git(t.cwd, "commit", "-q", "-m", "seed");
		const before = fs.existsSync(path.join(t.home, "ledger.jsonl"));
		const r = await t.fire("tool_call", { toolName: "bash", input: { command: heredoc41("tracked.py") } }, t.ctx);
		const recs = readLedger(t.home);
		if (mode === "deny") {
			assert.equal(r?.block, true);
			assert.match(r.reason, /^pi-lead lead gate: this bash command writes .*tracked\.py \(41 lines\) — delegate this/);
			assert.deepEqual(recs.map((e: any) => [e.event, e.vector, e.lines]), [["blocked", "bash", 41]]);
			assert.deepEqual(t.notes, []);
		} else if (mode === "log") {
			assert.equal(r, undefined);
			assert.deepEqual(recs.map((e: any) => [e.event, e.vector, e.lines]), [["would_have_blocked", "bash", 41]]);
			assert.deepEqual(t.notes, []);
		} else {
			assert.equal(r, undefined);
			assert.equal(before, false);
			assert.deepEqual(recs, [], "nothing written");
			assert.deepEqual(t.notes, [{ msg: NOT_CHECKED("failed"), type: "warning" }]);
		}
		assert.equal(fs.readFileSync(path.join(t.cwd, "tracked.py"), "utf-8"), "x\n", "the command itself is never run by the extension");
	}
});

// ---- p22: the executor finds its owner each time it reports ------------------------------------------

const OWNER_CASES: any[] = JSON.parse(fs.readFileSync(path.join(REPO, "tests", "owner-cases.json"), "utf-8"));

function putSpec(file: string, spec: any): void {
	fs.mkdirSync(path.dirname(file), { recursive: true });
	if (spec.dir) fs.mkdirSync(file);
	else fs.writeFileSync(file, "raw" in spec ? spec.raw : JSON.stringify(spec.json));
}

test("resolveOwner agrees with lib/pilead/ownership.py on tests/owner-cases.json", () => {
	assert.ok(OWNER_CASES.length >= 12);
	for (const c of OWNER_CASES) {
		const home = path.join(SANDBOX, `owner-home-${seq++}`);
		for (const [id, spec] of Object.entries(c.records)) putSpec(path.join(home, "leads", `${id}.json`), spec);
		for (const [id, spec] of Object.entries(c.notes)) putSpec(path.join(home, "handoffs", `${id}.json`), spec);
		const meta: any = { sid: "exec-1", topic: "t", packets: 1 };
		if ("lead" in c) meta.lead = c.lead;
		fs.mkdirSync(path.join(home, "sessions", "exec-1"), { recursive: true });
		fs.writeFileSync(path.join(home, "sessions", "exec-1", "meta.json"), JSON.stringify(meta));
		const before = treeOf(home);
		assert.equal(ext.resolveOwner(home, "exec-1", undefined), c.resolved, c.name);
		assert.deepEqual(treeOf(home), before, `${c.name}: nothing written`);
	}
	assert.equal(ext.OWNER_MAX_MOVES, 8);
});

test("resolveOwner: with no meta.json, or one naming no usable lead, the environment's lead is the start", () => {
	const home = path.join(SANDBOX, `owner-env-${seq++}`);
	registerLead(home, "lead-env");
	assert.equal(ext.resolveOwner(home, "exec-x", "lead-env"), "lead-env");
	assert.equal(ext.resolveOwner(home, "exec-x", undefined), null);
	assert.equal(ext.resolveOwner(home, "exec-x", "../bad"), null);
	assert.equal(ext.resolveOwner(home, "../bad-sid", "lead-env"), "lead-env", "an unusable sid reads no meta.json");
	fs.mkdirSync(path.join(home, "sessions", "exec-y"), { recursive: true });
	fs.writeFileSync(path.join(home, "sessions", "exec-y", "meta.json"), JSON.stringify({ lead: "../../x" }));
	assert.equal(ext.resolveOwner(home, "exec-y", "lead-env"), "lead-env");
	fs.writeFileSync(path.join(home, "sessions", "exec-y", "meta.json"), "{broken");
	assert.equal(ext.resolveOwner(home, "exec-y", "lead-env"), "lead-env");
});

/** An executor whose meta.json names `metaLead` (none when undefined), with packet 1 reported. */
function ownedExecutor(metaLead: string | undefined) {
	const t = setup("executor");
	fs.mkdirSync(t.sessDir, { recursive: true });
	fs.writeFileSync(path.join(t.sessDir, "packet-0001.md"), "p1");
	const report = path.join(t.sessDir, "report-0001.md");
	fs.writeFileSync(report, "Done.\nStatus: clean");
	const setLead = (lead: string | undefined) =>
		fs.writeFileSync(path.join(t.sessDir, "meta.json"), JSON.stringify({ sid: t.sid, topic: "t", packets: 1, ...(lead ? { lead } : {}) }));
	if (metaLead !== undefined) setLead(metaLead);
	return { ...t, report, setLead };
}

test("owner: a settle after meta.json's lead changed wakes the NEW owner, and nothing reaches the old one", async () => {
	const t = ownedExecutor(undefined);
	const oldInbox = registerLead(t.home, t.leadSid);
	const newInbox = registerLead(t.home, "lead-new");
	t.setLead(t.leadSid);
	fs.writeFileSync(t.report, "v1");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(oldInbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
	t.setLead("lead-new"); // adopted while it works
	fs.writeFileSync(t.report, "v2, rewritten");
	fs.utimesSync(t.report, new Date(), new Date(Date.now() + 5000));
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(newInbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
	assert.equal(fs.readFileSync(oldInbox, "utf-8"), `reported ${t.sid} ${t.report}\n`, "the old owner got nothing more");
});

test("owner: a forward note from the environment's lead leads to its successor's inbox", async () => {
	const t = ownedExecutor(undefined);
	const newInbox = registerLead(t.home, "lead-succ");
	fs.mkdirSync(path.join(t.home, "handoffs"), { recursive: true });
	fs.writeFileSync(path.join(t.home, "handoffs", `${t.leadSid}.json`), JSON.stringify({ from: t.leadSid, to: "lead-succ" }));
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(newInbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
});

test("owner: with no meta.json the environment's lead is woken as today; an unknown owner wakes nobody and retries", async () => {
	const t = ownedExecutor(undefined);
	const inbox = registerLead(t.home, t.leadSid);
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
	const u = ownedExecutor("lead-broken");
	fs.mkdirSync(path.join(u.home, "leads"), { recursive: true });
	fs.writeFileSync(path.join(u.home, "leads", "lead-broken.json"), "{not json");
	const envInbox = registerLead(u.home, u.leadSid);
	await u.fire("agent_settled", {}, u.ctx);
	assert.equal(fs.readFileSync(envInbox, "utf-8"), "", "an unknown owner is never replaced by the environment's lead");
	assert.ok(!fs.existsSync(path.join(u.sessDir, ".notified")), "no marker: a later settle tries again");
	assert.equal(status(u.sessDir), "reported");
});

test("owner: a .notified marker in the ns: form that matches suppresses the line; one whose digits differ does not", async () => {
	const t = ownedExecutor(undefined);
	const inbox = registerLead(t.home, t.leadSid);
	const ns = fs.statSync(t.report, { bigint: true }).mtimeNs;
	const marker = path.join(t.sessDir, ".notified");
	fs.writeFileSync(marker, `${t.report} ns:${ns}\n`);
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), "", "announced already (by pilead adopt)");
	fs.writeFileSync(marker, `${t.report} ns:${ns + 1n}\n`);
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`);
	assert.equal(fs.readFileSync(marker, "utf-8"), `${t.report} ${fs.statSync(t.report).mtimeMs}\n`, "the extension's own form");
	await t.fire("agent_settled", {}, t.ctx);
	assert.equal(fs.readFileSync(inbox, "utf-8"), `reported ${t.sid} ${t.report}\n`, "once");
	assert.equal(ext.notifiedMatches(`${t.report} ns:x1`, t.report), false);
	assert.equal(ext.notifiedMatches(`${path.join(t.sessDir, "report-0002.md")} ns:${ns}`, t.report), false, "another report");
	assert.equal(ext.notifiedMatches("", t.report), false);
});

test("owner: /pilead:status names the resolved owner and its inbox", async () => {
	const t = ownedExecutor("lead-new");
	const inbox = registerLead(t.home, "lead-new");
	await t.commands.get("pilead:status")!.handler("", t.ctx);
	assert.ok(t.notes.at(-1)!.msg.split("\n").includes(`lead: lead-new → inbox ${inbox}`), t.notes.at(-1)!.msg);
	t.setLead("lead-gone");
	await t.commands.get("pilead:status")!.handler("", t.ctx);
	assert.ok(t.notes.at(-1)!.msg.split("\n").includes("lead: lead-gone → inbox (unregistered)"), t.notes.at(-1)!.msg);
});

// ---- the lead's session changes (p22b) -------------------------------------------------------------------

const LINE_A = "reported exec-a /r/report-0001.md";

/** A successor lead: registered in `home`, and a forward note from `from` to it. */
function handOver(home: string, from: string, to: string, project = "proj"): string {
	const inbox = registerLead(home, to);
	fs.mkdirSync(path.join(home, "handoffs"), { recursive: true });
	fs.writeFileSync(path.join(home, "handoffs", `${from}.json`), JSON.stringify({ from, to, project, at: "-", reason: "takeover" }));
	return inbox;
}

/** Every file under `dir` with its bytes (the set of files and their contents). */
function snapshot(dir: string): Record<string, string> {
	const out: Record<string, string> = {};
	const walk = (d: string) => {
		for (const n of fs.readdirSync(d)) {
			const p = path.join(d, n);
			if (fs.statSync(p).isDirectory()) walk(p);
			else out[path.relative(dir, p)] = fs.readFileSync(p, "utf-8");
		}
	};
	if (fs.existsSync(dir)) walk(dir);
	return out;
}

test("lead move: a wake held when the record goes and a note names a successor is put in the successor's inbox once, sent by nobody", async () => {
	for (const already of [false, true]) {
		const d = deferred<string>();
		const t = leadWith(() => d.promise);
		t.pi.setSessionName = () => {};
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(t.inbox, `${LINE_A}\n`);
		assert.equal(t.handle.drainInbox(), 1);
		fs.rmSync(path.join(t.home, "leads", `${t.sid}.json`));
		const succ = handOver(t.home, t.sid, `succ-${t.sid}`);
		if (already) fs.writeFileSync(succ, `${LINE_A}\n`); // the takeover's adoption announced it already
		assert.equal(t.handle.drainInbox(), 0);
		d.resolve("refs: late");
		await t.handle.flush();
		assert.deepEqual(t.sent, []);
		assert.equal(fs.readFileSync(succ, "utf-8"), `${LINE_A}\n`, String(already));
		assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
		assert.deepEqual(claimLeftovers(t.inbox), []);
	}
});

test("lead move: with no forward note the held wake goes back where it came from, and nothing is sent", async () => {
	const d = deferred<string>();
	const t = leadWith(() => d.promise);
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, `${LINE_A}\n`);
	assert.equal(t.handle.drainInbox(), 1);
	fs.rmSync(path.join(t.home, "leads", `${t.sid}.json`));
	d.resolve("refs: late"); // the send itself finds the record gone
	await t.handle.flush();
	assert.deepEqual(t.sent, []);
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), `${LINE_A}\n`);
	assert.deepEqual(claimLeftovers(t.inbox), []);
});

test("lead move: at session_shutdown a held wake goes to the successor's inbox once; with no note, back where it came from", async () => {
	for (const note of [true, false]) {
		const d = deferred<string>();
		const t = leadWith(() => d.promise);
		await t.fire("session_start", {}, t.ctx);
		fs.appendFileSync(t.inbox, `${LINE_A}\n`);
		assert.equal(t.handle.drainInbox(), 1);
		fs.rmSync(path.join(t.home, "leads", `${t.sid}.json`));
		const succ = note ? handOver(t.home, t.sid, `succ-${t.sid}`) : undefined;
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		d.resolve("refs: late");
		await t.handle.flush();
		assert.deepEqual(t.sent, []);
		if (succ) {
			assert.equal(fs.readFileSync(succ, "utf-8"), `${LINE_A}\n`);
			assert.equal(fs.readFileSync(t.inbox, "utf-8"), "");
		} else {
			assert.equal(fs.readFileSync(t.inbox, "utf-8"), `${LINE_A}\n`);
		}
		assert.deepEqual(claimLeftovers(t.inbox), []);
	}
});

test("lead move: a registered session puts a failed wake back in its own inbox, as before", async () => {
	const t = leadWith(stubRefs);
	t.pi.sendMessage = () => {
		throw new Error("boom");
	};
	await t.fire("session_start", {}, t.ctx);
	fs.appendFileSync(t.inbox, `${LINE_A}\n`);
	t.handle.drainInbox();
	await t.handle.flush();
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), `${LINE_A}\n`);
});

test("lead move: an unregistered session's drain takes nothing; it is named [ex-Lead] once; registered again, it drains", async () => {
	const t = leadWith(stubRefs);
	const names: string[] = [];
	t.pi.setSessionName = (n: string) => void names.push(n);
	await t.fire("session_start", {}, t.ctx);
	fs.rmSync(path.join(t.home, "leads", `${t.sid}.json`));
	const succ = handOver(t.home, t.sid, `succ-${t.sid}`, "demo");
	fs.writeFileSync(t.inbox, `${LINE_A}\n`);
	for (let i = 0; i < 3; i++) assert.equal(t.handle.drainInbox(), 0);
	await t.handle.flush();
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), `${LINE_A}\n`);
	assert.equal(fs.readFileSync(succ, "utf-8"), "");
	assert.deepEqual(t.sent, []);
	assert.deepEqual(names, ["[ex-Lead] demo"]);
	assert.deepEqual(
		t.notes.filter((n) => n.msg.includes("handed its lead role")).map((n) => n.msg),
		[`pi-lead: this session handed its lead role to succ-${t.sid}; it is no longer a lead`],
	);
	registerLead(t.home, t.sid);
	fs.writeFileSync(path.join(t.home, "leads", `${t.sid}.inbox`), `${LINE_A}\n`);
	assert.equal(t.handle.drainInbox(), 1);
	await t.handle.flush();
	assert.equal(t.sent.length, 1);
	assert.deepEqual(names, ["[ex-Lead] demo"]);
});

test("lead move: a quiet lead whose record is gone does nothing (no name, no put-back), its inbox byte-identical", async () => {
	const w = waitingLead();
	fs.rmSync(path.join(w.home, "leads", `${w.sid}.json`));
	handOver(w.home, w.sid, `succ-${w.sid}`);
	const names: string[] = [];
	const q = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: w.home, PI_LEAD_SID: w.sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242 }, "tui");
	q.pi.setSessionName = (n: string) => void names.push(n);
	await q.fire("session_start", {}, q.ctx);
	q.handle.drainInbox();
	await q.fire("session_shutdown", { reason: "quit" }, q.ctx);
	await q.handle.flush();
	assert.equal(fs.readFileSync(w.inbox, "utf-8"), REPORTED_LINE);
	assert.equal(fs.readFileSync(path.join(w.home, "leads", `succ-${w.sid}.inbox`), "utf-8"), "");
	assert.deepEqual([names, q.sent], [[], []]);
});

/** A stub `pilead` for the takeover child: logs its arguments; `mode` "ok" registers the new lead with one wake line
 *  waiting and prints two adopted lines, "fail" exits 2 with a line on stderr, "slow" sleeps past the limit. */
function takeoverBin(mode: "ok" | "fail" | "slow"): { bin: string; log: string } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "takeover-bin-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	const body = {
		ok: [
			'mkdir -p "$PI_LEAD_HOME/leads"',
			`printf '{"sid":"%s","inbox":"%s"}' "$4" "$PI_LEAD_HOME/leads/$4.inbox" > "$PI_LEAD_HOME/leads/$4.json"`,
			`printf '%s\\n' '${LINE_A}' >> "$PI_LEAD_HOME/leads/$4.inbox"`,
			'echo "lead $2 → $4 (proj)"; echo "adopted exec-a from $2 (forced)"; echo "adopted exec-b from $2 (forced)"',
		],
		fail: ['echo "pilead: takeover: boom happened" >&2', "exit 2"],
		slow: ["sleep 5"],
	}[mode];
	fs.writeFileSync(bin, ["#!/bin/sh", `printf '%s\\n' "$*" >> '${log}'`, ...body].join("\n") + "\n");
	fs.chmodSync(bin, 0o755);
	return { bin, log };
}

const PREV = "lead-prev";
const NEWSID = "lead-newsid";
const prevFile = (id: string) => `/s/--repo--/2026-10-03T10-00-00-000Z_${id}.jsonl`;

/** A lead process whose sid is pi's own (no PI_LEAD_SID): `PREV` registered, the new session `NEWSID` not. */
function switchingLead(mode: "ok" | "fail" | "slow", extraEnv: Record<string, string> = {}, opts: any = {}) {
	const home = path.join(SANDBOX, `switch-home-${seq++}`);
	const prevInbox = registerLead(home, PREV);
	const stub = takeoverBin(mode);
	const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, ...extraEnv }, { pileadBin: stub.bin, takeoverTimeoutMs: 300, ...opts });
	t.ctx.sessionManager = { getSessionId: () => NEWSID };
	const calls = () => (fs.existsSync(stub.log) ? fs.readFileSync(stub.log, "utf-8").split("\n").filter((l) => l !== "") : []);
	return { ...t, home, prevInbox, calls };
}

test("lead move: /new and /fork run the takeover once with exactly its arguments, then drain", async () => {
	for (const reason of ["new", "fork"]) {
		const t = switchingLead("ok");
		await t.fire("session_start", { reason, previousSessionFile: prevFile(PREV) }, t.ctx);
		await t.handle.flush();
		assert.deepEqual(t.calls(), [`takeover ${PREV} --as ${NEWSID} --force`], reason);
		assert.deepEqual(
			t.notes.filter((n) => n.msg.includes("lead role")).map((n) => n.msg),
			[`pi-lead: lead role moved from ${PREV} to this session (2 executor(s))`],
		);
		assert.equal(t.sent.length, 1, "drained: the waiting wake was sent");
		assert.equal(t.sent[0].message.details.sid, "exec-a");
	}
});

test("lead move: resume, startup and reload run nothing; resume shows the notice", async () => {
	for (const reason of ["resume", "startup", "reload"]) {
		const t = switchingLead("ok");
		const before = snapshot(t.home);
		await t.fire("session_start", { reason, previousSessionFile: prevFile(PREV) }, t.ctx);
		await t.handle.flush();
		assert.deepEqual(t.calls(), [], reason);
		assert.deepEqual(snapshot(t.home), before);
		const said = t.notes.filter((n) => n.msg.includes(PREV)).map((n) => n.msg);
		assert.deepEqual(
			said,
			reason === "resume" ? [`pi-lead: the session you left (${PREV}) is still the registered lead; pilead takeover ${PREV} makes this one the lead`] : [],
		);
	}
});

test("lead move: an unusable previous id, an unregistered previous session, or PI_LEAD_SID set runs nothing", async () => {
	const cases: [string, Record<string, string>, unknown][] = [
		["unusable id", {}, prevFile("../x")],
		["no underscore", {}, `/s/${PREV}.jsonl`],
		["not registered", {}, prevFile("lead-nobody")],
		["PI_LEAD_SID", { PI_LEAD_SID: NEWSID }, prevFile(PREV)],
		["no file", {}, undefined],
	];
	for (const [name, env, file] of cases) {
		const t = switchingLead("ok", env);
		const before = snapshot(t.home);
		await t.fire("session_start", { reason: "new", previousSessionFile: file }, t.ctx);
		await t.handle.flush();
		assert.deepEqual(t.calls(), [], name);
		assert.deepEqual(snapshot(t.home), before, name);
	}
});

test("lead move: a takeover that fails or times out shows the NOT-moved notice and leaves every file as it was", async () => {
	for (const [mode, why] of [
		["fail", "pilead: takeover: boom happened"],
		["slow", "timed out after 0.3 s"],
	] as const) {
		const t = switchingLead(mode);
		const before = snapshot(t.home);
		await t.fire("session_start", { reason: "new", previousSessionFile: prevFile(PREV) }, t.ctx);
		await t.handle.flush();
		assert.equal(t.calls().length, 1, mode);
		assert.deepEqual(
			t.notes.filter((n) => n.msg.includes("lead role")).map((n) => n.msg),
			[`pi-lead: lead role NOT moved: ${why}`],
		);
		assert.deepEqual(snapshot(t.home), before, mode);
		assert.deepEqual(t.sent, []);
	}
});

test("lead move: a quiet lead runs no takeover after /new, its inbox byte-identical", async () => {
	const t = switchingLead("ok", { PI_LEAD_HELD_BY: "999" }, { pid: 4242 });
	fs.writeFileSync(t.prevInbox, REPORTED_LINE);
	const before = snapshot(t.home);
	await t.fire("session_start", { reason: "new", previousSessionFile: prevFile(PREV) }, t.ctx);
	await t.handle.flush();
	assert.deepEqual(t.calls(), []);
	assert.deepEqual(snapshot(t.home), before);
});

// ---- tab names: the extension names its own tab from its record (nothing is typed) ---------------------

/** Capture the health beat's timers (as the health test does); run `body` with a `beat()` that fires the last one. */
async function withBeats(body: (beat: () => void, beats: (() => void)[]) => Promise<void>): Promise<void> {
	const realSet = globalThis.setTimeout;
	const beats: (() => void)[] = [];
	(globalThis as any).setTimeout = (fn: any, ms: number, ...rest: any[]) => {
		if (ms === ext.HEALTH_BEAT_MS) {
			beats.push(fn);
			return realSet(() => {}, 0);
		}
		return realSet(fn, ms, ...rest);
	};
	try {
		await body(() => beats[beats.length - 1](), beats);
	} finally {
		(globalThis as any).setTimeout = realSet;
	}
}

/** A registered lead whose record holds `fields` besides `sid` and `inbox`. */
function labelLead(t: { home: string; sid: string; inbox: string }, fields: Record<string, unknown>): void {
	fs.writeFileSync(path.join(t.home, "leads", `${t.sid}.json`), JSON.stringify({ sid: t.sid, inbox: t.inbox, ...fields }));
}

function recordNames(t: { pi: any }): string[] {
	const names: string[] = [];
	t.pi.setSessionName = (n: string) => void names.push(n);
	return names;
}

test("tab names: a registered lead is named from its label once, however often the drain and the beat run; a changed label once more", async () => {
	await withBeats(async (beat) => {
		const t = leadWith(stubRefs, { watch: true, pollMs: 0 });
		labelLead(t, { label: "[Lead] proj" });
		const names = recordNames(t);
		await t.fire("session_start", {}, t.ctx);
		for (let i = 0; i < 3; i++) t.handle.drainInbox();
		beat();
		beat();
		assert.deepEqual(names, ["[Lead] proj"]);
		labelLead(t, { label: "[Lead] proj ·ab12" });
		beat(); // the beat alone picks the change up
		beat();
		t.handle.drainInbox();
		assert.deepEqual(names, ["[Lead] proj", "[Lead] proj ·ab12"]);
		labelLead(t, { label: "[Lead] other" });
		t.handle.drainInbox(); // and so does a drain
		assert.deepEqual(names, ["[Lead] proj", "[Lead] proj ·ab12", "[Lead] other"]);
		t.handle.stop();
	});
});

test("tab names: no label, a label of 81 characters, one with a line break or a control character, a non-string: no name", async () => {
	for (const fields of [{}, { label: "x".repeat(81) }, { label: "[Lead] a\nb" }, { label: "[Lead] \u001b[31m" }, { label: "" }, { label: 7 }]) {
		const t = leadWith(stubRefs);
		labelLead(t, fields);
		const names = recordNames(t);
		await t.fire("session_start", {}, t.ctx);
		t.handle.drainInbox();
		assert.deepEqual(names, [], JSON.stringify(fields));
	}
	const t = leadWith(stubRefs);
	labelLead(t, { label: "x".repeat(80) });
	const names = recordNames(t);
	await t.fire("session_start", {}, t.ctx);
	assert.deepEqual(names, ["x".repeat(80)], "80 characters is allowed");
});

test("tab names: a lead that is not registered, or whose record is not JSON, gets no name", async () => {
	const t = setup("lead", {}, { refsCheck: () => "refs: stub" });
	const names = recordNames(t);
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	assert.deepEqual(names, []);
	const u = leadWith(stubRefs);
	fs.writeFileSync(path.join(u.home, "leads", `${u.sid}.json`), "{not json");
	const unames = recordNames(u);
	await u.fire("session_start", {}, u.ctx);
	u.handle.drainInbox();
	assert.deepEqual(unames, []);
});

test("tab names: a setSessionName that throws stops nothing and is called again at the next beat", async () => {
	await withBeats(async (beat) => {
		const t = leadWith(stubRefs, { watch: true, pollMs: 0 });
		labelLead(t, { label: "[Lead] proj" });
		let calls = 0;
		t.pi.setSessionName = () => {
			calls++;
			throw new Error("no");
		};
		await t.fire("session_start", {}, t.ctx);
		assert.equal(calls, 1);
		fs.appendFileSync(t.inbox, "reported exec-a /r/report-0001.md\n");
		assert.equal(t.handle.drainInbox(), 1, "the wake is still claimed");
		await t.handle.flush();
		assert.equal(t.sent.length, 1, "and still sent");
		assert.equal(calls, 2, "the drain tried again");
		beat();
		assert.equal(calls, 3, "the beat tries again");
		const names: string[] = [];
		t.pi.setSessionName = (n: string) => void names.push(n);
		beat();
		beat();
		assert.deepEqual(names, ["[Lead] proj"]);
		t.handle.stop();
	});
});

test("tab names: a quiet lead (held by another process) names nothing and its inbox is byte-identical", async () => {
	const { home, sid, inbox } = waitingLead();
	fs.writeFileSync(path.join(home, "leads", `${sid}.json`), JSON.stringify({ sid, inbox, label: "[Lead] quiet" }));
	await withBeats(async (_beat, beats) => {
		const t = withEnv({ PI_LEAD_ROLE: "lead", PI_LEAD_HOME: home, PI_LEAD_SID: sid, PI_LEAD_HELD_BY: "999" }, { pid: 4242, watch: true });
		const names = recordNames(t);
		const before = treeOf(home);
		await t.fire("session_start", {}, t.ctx);
		t.handle.drainInbox();
		for (const b of beats.slice()) b(); // a quiet lead arms no beat; any that exists runs and names nothing
		await t.handle.flush();
		assert.deepEqual(names, []);
		assert.equal(fs.readFileSync(inbox, "utf-8"), REPORTED_LINE);
		assert.deepEqual(treeOf(home), before);
		t.handle.stop();
	});
});

/** An executor whose sessions/<sid>/meta.json is `meta` (a string is written as it is). */
function metaExecutor(meta: unknown) {
	const t = executorWith();
	fs.mkdirSync(t.sessDir, { recursive: true });
	if (meta !== undefined) fs.writeFileSync(path.join(t.sessDir, "meta.json"), typeof meta === "string" ? meta : JSON.stringify(meta));
	return t;
}

test("tab names: an executor is named from its meta.json label once, however often its inbox is drained", async () => {
	const t = metaExecutor({ sid: "s-1", label: "[Exec] s-1" });
	const names = recordNames(t);
	await t.fire("session_start", {}, t.ctx);
	for (let i = 0; i < 4; i++) t.handle.drainInbox();
	assert.deepEqual(names, ["[Exec] s-1"]);
	fs.appendFileSync(t.inbox, "The packet\n");
	assert.equal(t.handle.drainInbox(), 1, "the packet is still delivered");
	assert.deepEqual(names, ["[Exec] s-1"]);
});

test("tab names: an executor's tab_title is used over its label, at a drain (a poll tick drains), once per change", async () => {
	const t = metaExecutor({ sid: "s-1", label: "[Exec] s-1" });
	const names = recordNames(t);
	await t.fire("session_start", {}, t.ctx);
	fs.writeFileSync(path.join(t.sessDir, "meta.json"), JSON.stringify({ sid: "s-1", label: "[Exec] s-1", tab_title: "[closed] s-1" }));
	t.handle.drainInbox();
	t.handle.drainInbox();
	assert.deepEqual(names, ["[Exec] s-1", "[closed] s-1"]);
	fs.writeFileSync(path.join(t.sessDir, "meta.json"), JSON.stringify({ sid: "s-1", label: "[Exec] s-1", tab_title: "x".repeat(81) }));
	t.handle.drainInbox();
	assert.deepEqual(names, ["[Exec] s-1", "[closed] s-1", "[Exec] s-1"], "an unusable tab_title falls back to the label");
	const u = metaExecutor({ sid: "s-2", label: "[Exec] s-2", tab_title: "[closed] s-2" });
	const unames = recordNames(u);
	await u.fire("session_start", {}, u.ctx);
	assert.deepEqual(unames, ["[closed] s-2"], "at session_start");
});

test("tab names: an executor whose meta.json is missing, not JSON, or has a label of 81 characters gets no name", async () => {
	for (const meta of [undefined, "{not json", { label: "x".repeat(81) }, { label: "[Exec] a\tb" }]) {
		const t = metaExecutor(meta);
		const names = recordNames(t);
		await t.fire("session_start", {}, t.ctx);
		t.handle.drainInbox();
		assert.deepEqual(names, [], JSON.stringify(meta));
	}
});

test("tab names: recordTabName and usableTabName", () => {
	assert.equal(ext.TAB_NAME_MAX, 80);
	assert.equal(ext.recordTabName({ label: "[Lead] p" }, false), "[Lead] p");
	assert.equal(ext.recordTabName({ label: "[Lead] p", tab_title: "t" }, false), "[Lead] p", "a lead's tab_title is not read");
	assert.equal(ext.recordTabName({ label: "l", tab_title: "t" }, true), "t");
	assert.equal(ext.recordTabName({ label: "l", tab_title: "" }, true), "l");
	for (const r of [null, [], "x", 3, {}]) assert.equal(ext.recordTabName(r, true), undefined);
	assert.equal(ext.usableTabName("a\u0085b"), false);
});

// ── p23d: the route contract shared with `pilead route retain`, and lead-start --no-rename ────────────────

/** tests/route-cases.json: the cases tests/test_route.py runs against lib/pilead/route.py. */
const ROUTE_CASES: {
	name: string;
	registered: boolean;
	sid: string;
	reason: string;
	written: boolean;
	event: boolean;
	file_text: string | null;
}[] = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "route-cases.json"), "utf-8"));
const ROUTE_NOW = 1767225600123; // 2026-01-01T00:00:00.123Z, tests/test_route.py's NOW

test("route contract: /pilead:route retain agrees with tests/route-cases.json at the injected moment", async () => {
	assert.ok(ROUTE_CASES.length >= 6);
	for (const c of ROUTE_CASES) {
		const { home } = deepHome();
		const t = setup("lead", { PI_LEAD_HOME: home, PI_LEAD_SID: c.sid }, { now: () => ROUTE_NOW });
		t.ctx.sessionManager = { getSessionId: () => c.sid };
		if (c.registered) {
			const rec = path.join(home, "leads", `${c.sid}.json`); // for an unusable id: where that id would point
			fs.mkdirSync(path.dirname(rec), { recursive: true });
			fs.writeFileSync(rec, JSON.stringify({ sid: c.sid }));
		}
		await t.commands.get("pilead:route")!.handler(`retain ${c.reason}`, t.ctx);
		const grace = path.join(home, "leads", `${c.sid}.route`);
		assert.equal(fs.existsSync(grace), c.written, c.name);
		if (c.written) {
			assert.equal(fs.readFileSync(grace, "utf-8"), c.file_text, c.name);
			assert.ok(Math.abs(fs.statSync(grace).mtimeMs - ROUTE_NOW) < 1, c.name);
		} else assert.equal(c.file_text, null, c.name);
		const retained = readLedger(home).filter((r) => r.event === "retained");
		if (c.event) {
			assert.equal(retained.length, 1, c.name);
			assert.deepEqual(fieldsOf(retained[0]), { session_id: c.sid, reason: c.file_text!.split(" ").slice(1).join(" ").replace(/\n$/, "") }, c.name);
		} else assert.deepEqual(retained, [], c.name);
	}
});

test("route contract: a grace file written by bin/pilead route retain opens the gate until the grace time is over", async () => {
	let now = Date.now();
	const t = setup("lead", {}, { now: () => now });
	registerLead(t.home, t.sid);
	const write = () => t.fire("tool_call", { toolName: "write", input: { path: "big.ts", content: lines(500) } }, t.ctx);
	assert.equal((await write())?.block, true, "closed before");
	const env: NodeJS.ProcessEnv = { ...process.env };
	for (const k of ["PI_LEAD_ROLE", "PI_LEAD_SID", "PI_SESSION_ID", "PI_LEAD_HOME"]) delete env[k];
	const out = execFileSync(path.join(REPO, "bin", "pilead"), ["route", "retain", "verb opened it", "--home", t.home, "--session", t.sid], {
		env,
		encoding: "utf-8",
	});
	assert.equal(out, "retain window open for 120s — reason: verb opened it\n");
	assert.match(fs.readFileSync(path.join(t.home, "leads", `${t.sid}.route`), "utf-8"), /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z verb opened it\n$/);
	now = Date.now();
	assert.equal(await write(), undefined, "open after the verb");
	now += 120 * 1000 + 1000;
	assert.equal((await write())?.block, true, "closed after the grace time");
});

test("tab names: a lead record with no_rename exactly true is never named; false, no field or the text \"true\" is named once", async () => {
	for (const [fields, want] of [
		[{ no_rename: true }, []],
		[{ no_rename: false }, ["[Lead] proj"]],
		[{}, ["[Lead] proj"]],
		[{ no_rename: "true" }, ["[Lead] proj"]],
	] as [Record<string, unknown>, string[]][]) {
		await withBeats(async (beat) => {
			const t = leadWith(stubRefs, { watch: true, pollMs: 0 });
			try {
				labelLead(t, { label: "[Lead] proj", ...fields });
				const names = recordNames(t);
				await t.fire("session_start", {}, t.ctx);
				for (let i = 0; i < 3; i++) t.handle.drainInbox();
				beat();
				beat();
				t.handle.drainInbox();
				assert.deepEqual(names, want, JSON.stringify(fields));
			} finally {
				t.handle.stop(); // a failed assertion must not leave the watcher running
			}
		});
	}
});

// ---- p26c: the reviewer role; a quiet lead does none of the lead work added since the rule ----------------

test("roleFrom: exactly executor or reviewer; any other value, none, or an unreadable env is a lead", () => {
	assert.equal(ext.roleFrom({ PI_LEAD_ROLE: "executor" }), "executor");
	assert.equal(ext.roleFrom({ PI_LEAD_ROLE: "reviewer" }), "reviewer");
	for (const v of ["lead", "Reviewer", "REVIEWER", " reviewer", "", undefined]) assert.equal(ext.roleFrom({ PI_LEAD_ROLE: v }), "lead", String(v));
	assert.equal(ext.roleFrom({}), "lead");
	for (const env of [undefined, null, 7]) assert.equal(ext.roleFrom(env), "lead", String(env));
	const unreadable = Object.defineProperty({}, "PI_LEAD_ROLE", { get: () => { throw new Error("no"); } });
	assert.equal(ext.roleFrom(unreadable), "lead");
});

/** A registered lead (label `[Lead] work`) with a reported line in its inbox and an orphaned claim file of an
 *  ended process beside it. */
function leadWorkHome() {
	const w = waitingLead();
	fs.writeFileSync(path.join(w.home, "leads", `${w.sid}.json`), JSON.stringify({ sid: w.sid, inbox: w.inbox, label: "[Lead] work" }));
	const orphan = `${w.inbox}.claim-${endedPid()}-${Date.now() - 600_000}-0`;
	fs.writeFileSync(orphan, "reported exec-o /h/sessions/exec-o/report-0001.md\n");
	return { ...w, orphan };
}

/** A stub `pilead` that only logs its arguments: any child the extension starts shows in `calls()`. */
function loggingBin(): { bin: string; calls: () => string[] } {
	const dir = fs.mkdtempSync(path.join(SANDBOX, "log-bin-"));
	const bin = path.join(dir, "pilead");
	const log = path.join(dir, "calls.log");
	fs.writeFileSync(bin, `#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\n`);
	fs.chmodSync(bin, 0o755);
	return { bin, calls: () => (fs.existsSync(log) ? fs.readFileSync(log, "utf-8").split("\n").filter(Boolean) : []) };
}

/** One lead process over `w`'s home, every piece of lead work observable: banners, turn-end asks, footer texts
 *  asked and set, tab names, and any child started (`pileadBin`). */
function observedLead(w: { home: string; sid: string }, extraEnv: Record<string, string>, mode: string) {
	const seen = { banners: [] as string[][], turnEnds: [] as string[][], statusAsked: 0, statusSet: [] as unknown[], names: [] as string[] };
	const stub = loggingBin();
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: w.home, PI_LEAD_SID: w.sid, ...extraEnv };
	const t = withEnv(env, {
		pid: 4242,
		watch: true,
		pileadBin: stub.bin,
		banner: (a: string[]) => void seen.banners.push(a),
		turnEnd: (a: string[]) => (seen.turnEnds.push(a), '{"lines":[]}'),
		statusLine: () => (seen.statusAsked++, "pi-lead: ok"),
	}, mode);
	t.ctx.ui.setStatus = (_k: string, v: unknown) => void seen.statusSet.push(v);
	t.pi.setSessionName = (n: string) => void seen.names.push(n);
	return { ...t, env, seen, calls: stub.calls };
}

/** session_start, a poll, a health beat, agent_settled and session_shutdown. */
async function leadWorkRun(t: ReturnType<typeof observedLead>, beats: (() => void)[]) {
	await t.fire("session_start", {}, t.ctx);
	t.handle.drainInbox();
	for (const b of beats.slice()) b();
	await t.handle.flush();
	await t.fire("agent_settled", {}, t.ctx);
	await t.handle.flush();
	await sleep(30);
	await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
	t.handle.stop();
}

const QUIET_KINDS: { what: string; env: Record<string, string>; mode: string }[] = [
	{ what: "print run", env: {}, mode: "print" },
	{ what: "marker held by another process", env: { PI_LEAD_HELD_BY: "999" }, mode: "tui" },
];

test("a quiet lead does none of the lead work: claim files, wakes, health beat, footer, tab name, banners, turn-end", async () => {
	for (const k of QUIET_KINDS) {
		const w = leadWorkHome();
		const orphanText = fs.readFileSync(w.orphan, "utf-8");
		await withBeats(async (_beat, beats) => {
			const t = observedLead(w, k.env, k.mode);
			const before = treeOf(w.home);
			await leadWorkRun(t, beats);
			assert.equal(t.handle.leadIsQuiet(), true, k.what);
			assert.equal(fs.readFileSync(w.inbox, "utf-8"), REPORTED_LINE, `${k.what}: the inbox line`);
			assert.equal(fs.readFileSync(w.orphan, "utf-8"), orphanText, `${k.what}: the orphaned claim`);
			assert.deepEqual([t.sent, t.userSent], [[], []], `${k.what}: no message`);
			assert.deepEqual(t.seen, { banners: [], turnEnds: [], statusAsked: 0, statusSet: [], names: [] }, k.what);
			assert.deepEqual(t.calls(), [], `${k.what}: no process started`);
			assert.ok(!fs.existsSync(path.join(w.home, "wake", w.sid, "health.json")), `${k.what}: no health file`);
			assert.ok(!fs.existsSync(path.join(w.home, "ledger.jsonl")), `${k.what}: no ledger line`);
			assert.ok(fs.existsSync(path.join(w.home, "leads", `${w.sid}.json`)), `${k.what}: the record is where it was`);
			assert.deepEqual(treeOf(w.home), before, `${k.what}: home byte-identical`);
		});
	}
});

test("a quiet lead does not move the lead role at /new: no takeover child, the record and home byte-identical", async () => {
	for (const k of QUIET_KINDS) {
		const t = switchingLead("ok", k.env, { pid: 4242 });
		t.ctx.mode = k.mode;
		fs.writeFileSync(t.prevInbox, REPORTED_LINE);
		const before = treeOf(t.home);
		const names = recordNames(t);
		await t.fire("session_start", { reason: "new", previousSessionFile: prevFile(PREV) }, t.ctx);
		await t.handle.flush();
		assert.deepEqual([t.calls(), names, t.sent], [[], [], []], k.what);
		assert.deepEqual(treeOf(t.home), before, k.what);
	}
});

test("the same home and a lead that is not quiet: each piece of lead work happens", async () => {
	const w = leadWorkHome();
	await withBeats(async (_beat, beats) => {
		const t = observedLead(w, {}, "tui");
		assert.equal(t.handle.leadIsQuiet(), false);
		await leadWorkRun(t, beats);
		assert.equal(fs.readFileSync(w.inbox, "utf-8"), "", "the inbox line was claimed");
		assert.ok(!fs.existsSync(w.orphan), "the orphaned claim was recovered");
		assert.deepEqual(sids(t), ["exec-o", "exec-q"], "both wakes sent, the recovered one first");
		assert.deepEqual(events(w.home, "wake_recovered").length, 1);
		assert.ok(fs.existsSync(path.join(w.home, "wake", w.sid, "health.json")), "the health file");
		assert.ok(healthOf(w.home, w.sid).ended, "the last health write at shutdown");
		assert.ok(beats.length >= 1, "a health beat was armed");
		assert.ok(t.seen.statusAsked >= 1 && t.seen.statusSet.includes("pi-lead: ok"), "the footer");
		assert.deepEqual(t.seen.names, ["[Lead] work"], "the tab's name");
		assert.equal(t.seen.banners.length, 2, "a banner per report");
		assert.equal(t.seen.turnEnds.length, 1, "the turn-end call at agent_settled");
	});
	const m = switchingLead("ok", {}, { pid: 4242 });
	m.ctx.mode = "tui";
	await m.fire("session_start", { reason: "new", previousSessionFile: prevFile(PREV) }, m.ctx);
	await m.handle.flush();
	assert.deepEqual(m.calls(), [`takeover ${PREV} --as ${NEWSID} --force`], "the lead role moves");
});

test("a quiet lead still refuses a large edit and a bash command that writes a control file, with today's reasons", async () => {
	for (const k of QUIET_KINDS) {
		const w = leadWorkHome();
		const t = observedLead(w, k.env, k.mode);
		await t.fire("session_start", {}, t.ctx);
		assert.equal(t.handle.leadIsQuiet(), true, k.what);
		const big = await t.fire("tool_call", { toolName: "write", input: { path: path.join(t.ctx.cwd, "big.ts"), content: lines(200) } }, t.ctx);
		assert.equal(big?.block, true, `${k.what}: the large edit`);
		const loud = observedLead(leadWorkHome(), {}, "tui");
		await loud.fire("session_start", {}, loud.ctx);
		const want = await loud.fire("tool_call", { toolName: "write", input: { path: path.join(loud.ctx.cwd, "big.ts"), content: lines(200) } }, loud.ctx);
		assert.equal(big.reason, want.reason.split(loud.ctx.cwd).join(t.ctx.cwd), `${k.what}: today's reason`);
		t.handle.stop();
		loud.handle.stop();
	}
	for (const k of QUIET_KINDS) {
		const w = leadWorkHome();
		const asked: string[] = [];
		const reason = "pilead gate: control file";
		const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "lead", PI_LEAD_HOME: w.home, PI_LEAD_SID: w.sid, ...k.env };
		const t = withEnv(env, { pid: 4242, bashGate: (c: string) => (asked.push(c), JSON.stringify({ verdict: "block", reason })) }, k.mode);
		await t.fire("session_start", {}, t.ctx);
		const cmd = `echo x > ${path.join(w.home, "config.json")}`;
		const r = await t.fire("tool_call", { toolName: "bash", input: { command: cmd } }, t.ctx);
		assert.deepEqual(r, { block: true, reason }, k.what);
		assert.deepEqual(asked, [cmd], k.what);
	}
});

/** A reviewer process over a home with a registered lead and a reported line in its inbox. */
function reviewerOver(extraEnv: Record<string, string> = {}) {
	const w = waitingLead();
	const env: Record<string, string | undefined> = { PI_LEAD_ROLE: "reviewer", PI_LEAD_HOME: w.home, PI_LEAD_SID: w.sid, ...extraEnv };
	const t = withEnv(env, { pid: 4242, watch: true, banner: () => assert.fail("banner"), turnEnd: () => assert.fail("turn-end"), statusLine: () => assert.fail("status line") }, "tui");
	t.ctx.ui.setStatus = () => assert.fail("setStatus");
	t.pi.setSessionName = () => assert.fail("setSessionName");
	return { ...t, ...w, env };
}

test("reviewer: every tool but read, grep, find and ls is refused with REVIEWER_REFUSAL", async () => {
	assert.equal(ext.REVIEWER_REFUSAL, "pi-lead reviewer: this session only reads — it runs no command and changes no file.");
	const t = reviewerOver();
	assert.equal(t.handle.role, "reviewer");
	await t.fire("session_start", {}, t.ctx);
	for (const [toolName, input] of [["bash", { command: "ls" }], ["edit", { path: "a", edits: [] }], ["write", { path: "a", content: "x" }],
		["mcp_x", {}], ["Read", {}], ["", {}]] as [string, unknown][]) {
		assert.deepEqual(await t.fire("tool_call", { toolName, input }, t.ctx), { block: true, reason: ext.REVIEWER_REFUSAL }, toolName);
	}
	for (const toolName of ["read", "grep", "find", "ls"]) assert.equal(await t.fire("tool_call", { toolName, input: {} }, t.ctx), undefined, toolName);
	assert.equal(t.handlers.get("tool_call")?.length, 1, "one tool_call handler");
	t.handle.stop();
});

test("reviewer: with a registered lead's PI_LEAD_SID it touches nothing — inbox, messages, commands, shutdown", async () => {
	const t = reviewerOver();
	const before = treeOf(t.home);
	await withBeats(async (_beat, beats) => {
		await t.fire("session_start", {}, t.ctx);
		assert.equal(t.handle.drainInbox(), 0);
		for (const b of beats.slice()) b();
		await t.fire("before_agent_start", { prompt: "x" }, t.ctx);
		await t.fire("agent_settled", {}, t.ctx);
		await t.fire("message_end", { message: { role: "user", content: "x" } }, t.ctx);
		await t.handle.flush();
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		assert.deepEqual(beats, [], "no beat armed");
	});
	t.handle.stop();
	assert.equal(fs.readFileSync(t.inbox, "utf-8"), REPORTED_LINE);
	assert.deepEqual([t.sent, t.userSent, t.notes], [[], [], []]);
	assert.deepEqual([...t.commands.keys()], [], "no command registered");
	assert.deepEqual([...t.handlers.keys()], ["tool_call"], "no other handler");
	assert.deepEqual(treeOf(t.home), before, "home byte-identical");
	assert.equal(t.handle.leadIsQuiet(), true);
});

test("reviewer: a marker held by another process changes nothing, and the reviewer sets the marker", async () => {
	for (const marker of ["999", undefined, "4242", "junk"]) {
		const t = reviewerOver(marker === undefined ? {} : { PI_LEAD_HELD_BY: marker });
		const before = treeOf(t.home);
		await t.fire("session_start", {}, t.ctx);
		assert.deepEqual(await t.fire("tool_call", { toolName: "bash", input: { command: "true" } }, t.ctx), { block: true, reason: ext.REVIEWER_REFUSAL });
		assert.equal(await t.fire("tool_call", { toolName: "read", input: {} }, t.ctx), undefined);
		await t.fire("session_shutdown", { reason: "quit" }, t.ctx);
		assert.equal(t.env.PI_LEAD_HELD_BY, "4242", String(marker));
		assert.deepEqual([t.notes, t.sent], [[], []], String(marker));
		assert.deepEqual(treeOf(t.home), before, String(marker));
		t.handle.stop();
	}
});

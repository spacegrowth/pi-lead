// Real `pi --mode rpc`: the extension alone (no --skill) must list every /pilead: command.
// Sends only get_commands, so no model is called. Skips when pi is not on PATH.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
// The probe gets a home of its own too: even `pi --version` writes under $HOME/.pi/agent.
const probeHome = fs.mkdtempSync(path.join(os.tmpdir(), "pilead-rpc-probe-"));
const hasPi = spawnSync("pi", ["--version"], {
	stdio: "ignore",
	env: { ...process.env, HOME: probeHome, PI_CODING_AGENT_DIR: path.join(probeHome, "agent") },
}).status === 0;

const WANT = [
	"mode", "spawn", "send", "check", "list", "review", "verify", "close", "diff", "board",
	"auto", "tier", "plan", "handoff", "restart", "resume", "retire", "focus", "stop",
	"adopt", "auto-close", "close-predecessor", "doctor", "gate", "keep", "lineup", "lint", "notify", "prune", "queue",
	"refs", "report", "stats", "takeover", "tidy", "whoami",
	"status", "route",
];

test("pi rpc lists the 38 /pilead: commands with only -e", { skip: !hasPi && "pi not on PATH" }, async () => {
	const home = fs.mkdtempSync(path.join(os.tmpdir(), "pilead-rpc-"));
	// pi writes auth.json and models-store.json under $HOME/.pi/agent when it starts: give it a home of its
	// own, so the test never touches the caller's own ~/.pi.
	const piHome = fs.mkdtempSync(path.join(os.tmpdir(), "pilead-rpc-home-"));
	const child = spawn("pi", ["--mode", "rpc", "--no-session", "-e", path.join(REPO, "extensions", "pi-lead.ts")], {
		cwd: REPO,
		env: { ...process.env, PI_LEAD_HOME: home, HOME: piHome, PI_CODING_AGENT_DIR: path.join(piHome, "agent") },
		stdio: ["pipe", "pipe", "ignore"],
	});
	try {
		const reply = await new Promise<any>((resolve, reject) => {
			const timer = setTimeout(() => reject(new Error("timeout waiting for get_commands")), 30000);
			let buf = "";
			child.stdout.on("data", (d) => {
				buf += d;
				let i: number;
				while ((i = buf.indexOf("\n")) >= 0) {
					const line = buf.slice(0, i);
					buf = buf.slice(i + 1);
					try {
						const m = JSON.parse(line);
						if (m.type === "response" && m.command === "get_commands") {
							clearTimeout(timer);
							resolve(m);
						}
					} catch {}
				}
			});
			child.on("error", reject);
			child.stdin.write(JSON.stringify({ id: "1", type: "get_commands" }) + "\n");
		});
		const names = (reply.data?.commands ?? []).map((c: any) => c.name).filter((n: string) => n.startsWith("pilead:"));
		assert.deepEqual(names.sort(), WANT.map((n) => `pilead:${n}`).sort());
	} finally {
		const exited = child.exitCode !== null || child.signalCode !== null ? Promise.resolve()
			: new Promise<void>((resolve) => { child.once("exit", () => resolve()); setTimeout(resolve, 5000).unref(); });
		child.kill();
		await exited; // pi may still be writing under piHome until it has exited
		fs.rmSync(home, { recursive: true, force: true });
		fs.rmSync(piHome, { recursive: true, force: true });
	}
});

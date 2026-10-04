import { test } from "node:test";
import assert from "node:assert/strict";
import { tsImport } from "tsx/esm/api";

type Call = { kind: "on" | "registerCommand"; name: string };

function stubPi(calls: Call[]) {
	return {
		on: (name: string) => void calls.push({ kind: "on", name }),
		registerCommand: (name: string) => void calls.push({ kind: "registerCommand", name }),
		sendUserMessage: () => {},
		sendMessage: () => {},
	} as any;
}

// ROLE is read at module load, so each role gets a fresh load: tsImport's per-call namespace
// keeps module instances apart (a plain `import(...?query)` also loses the default export).
async function load(role: string, tag: string) {
	process.env.PI_LEAD_ROLE = role;
	const mod: any = await tsImport("../../extensions/pi-lead.ts", {
		parentURL: import.meta.url,
		namespace: tag,
	} as any);
	// package.json has no "type": tsx loads the .ts as CJS, so the default export is nested.
	const init = typeof mod.default === "function" ? mod.default : mod.default.default;
	const calls: Call[] = [];
	init(stubPi(calls));
	return calls;
}

test("executor role registers tool_call + session_start", async () => {
	const calls = await load("executor", "exec");
	const names = calls.filter((c) => c.kind === "on").map((c) => c.name);
	assert.ok(names.includes("tool_call"), `got ${names}`);
	assert.ok(names.includes("session_start"), `got ${names}`);
});

test("lead role registers the routing gate, inbox watcher and its commands", async () => {
	const calls = await load("lead", "lead");
	const names = calls.map((c) => `${c.kind}:${c.name}`);
	for (const n of ["on:tool_call", "on:session_start", "registerCommand:pilead:route", "registerCommand:pilead:status"]) {
		assert.ok(names.includes(n), `missing ${n} in ${names}`);
	}
});

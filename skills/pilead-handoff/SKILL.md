---
name: pilead-handoff
description: Hand this lead's role to a fresh successor lead from a memo; use only when the human says "hand off", "start a successor lead" or "hand this off to a new session" (a lead never hands off by itself).
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Use this when the lead session has got heavy (`pilead list` flags it, and the turn-end line reads `🔁 …getting heavy…`)
and a fresh context is the healthy move — and the human has said so.

**First write the memo**, a file under `~/.relay-tasks/` (gate-exempt): what is in flight (each executor, its packet,
where it stands), what is reviewed and committed, open questions, next steps. Write it as a memo to your successor, not
a transcript. Give it a real `# ` heading naming the work. The successor's project is `--project`, else yours.
Carry forward every `[marker]` — a word in square brackets, lower-case and
`-` — your own predecessor's memo had, word for word (`pilead handoff` warns on stderr if one is missing). On every
queue item name WHO does it (the successor, an executor, the human): never phrase box/deploy/ops work as the lead's own.

**Then run:**

```
pilead handoff <memo.md> [--project NAME] [--model MODEL] [--effort LEVEL]
```

It registers a new lead (`leads/<uuid>.json`, before its first turn), opens its tab next to yours titled
`[Lead] <project> ·xxxx`, moves your executors, plan and pending wakes to it, and **steps this session down**. Its first
line says `successor model: <model> (from --model | from this lead's session log)`; with neither, it is refused (exit 2).
**No posture is handed on**: the successor is manual with tier `auto`.

**Aftercare, for the successor.** It reads the memo copy's SUCCESSOR AFTERCARE section, checks `pilead lead-start`, runs
`/pilead:list`, and asks the human whether to close the predecessor's tab. Only on a yes:

```
pilead close-predecessor
```

It closes the tab recorded at handoff by its handle, never by a title. See README, Handoff.

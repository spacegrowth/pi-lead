---
name: pilead-route
description: The retain escape hatch for lead mode — when the routing gate blocks an inline edit that is genuinely lead work, declare a reason and open a short grace window; use when a gate block tells you to, or the user says "route retain".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Use this ONLY when the edit gate has blocked an `edit`/`write` and the edit is genuinely lead work you should not delegate
(committing an executor's staged diff that needs a small hand-adjustment; a change that truly cannot be packaged). It is
not a way out of delegating real implementation work — that is what the gate keeps.

**Two ways in, the same effect** — a window of `grace_seconds` (120 by default) in which inline edits pass ungated, and one
`retained` ledger event with your reason:

- `/pilead:route retain "<one-line reason>"` — the **extension command**, typed in the lead's pi session. It is the
  extension's own command, not a skill; this skill only explains it.
- from a shell, a script or your own bash tool, where that cannot be typed:

```
pilead route retain "<one-line reason>" [--session SID]
```

  It prints `retain window open for 120s — reason: …`. `--session` defaults to `$PI_LEAD_SID`, else `$PI_SESSION_ID`.

Make the edit within the window; once it expires the gate re-engages and you run this again. Prefer `/pilead:spawn` /
`/pilead:send` whenever the work fits a packet.

Limits: only a registered lead's gate is opened. The verb refuses an executor by \`PI_LEAD_ROLE\` only (an honour-level
check), and with \`--session\` any shell can open a registered lead's window. The four control files under home
(\`config.json\`, \`ledger.jsonl\`, \`leads/*.route\`, \`leads/*.json\`) are blocked at any size while the window is closed:
changing them needs the human's word or an open window. See README, Lead gate.

---
name: pilead-retire
description: Retire a heavy executor — close it AND write a successor seed (an index of its packets and reports), so a fresh session over the same territory starts cheaply; use when asked to "retire that session", "this exec is getting heavy" or "rotate this executor".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead retire <sid> [--force] [--keep-tab]
```

That closes the session (as superseded by `successor-seed`, tab and all, as `/pilead:close` does) and writes
`sessions/<sid>/successor-seed.md`: every packet it was sent, each report's outcome line, `Status`, `Risk flags` and
`UNVERIFIED`, the worktree and topic it owned. The seed is an INDEX, not a transcript: the detail stays in the reports.

**Then start the successor from the seed**, as the output tells you:

```
pilead spawn <worktree> <topic> <packet> --seed <sid> --lead "$PI_SESSION_ID"
```

(or `pilead send <sid> <packet> --rotate` does both). The seed is appended to the packet as inherited context.

**Retire rather than `/pilead:send`** when a session is heavy, has finished a phase, or is dead or stalled with useful
history. Heaviness is not degradation: it is a judgement call, not a rule. `pilead list` and `check` show the heavy mark.

**Refusals:** *still busy on packet N and has not reported* — retiring kills that work unreported; wait
(`pilead check`), or `--force` (that packet is seeded as NO REPORT). A session whose status is already stalled, paused or dead is not refused.
*already retired* — the seed exists; spawn with it. A pinned session (`pilead keep`) is refused. `--keep-tab` retires but
leaves the process and tab running, retitled `[closed] <sid>`. Auto-close retires a heavy finished executor by itself.
See README, Lifecycle.

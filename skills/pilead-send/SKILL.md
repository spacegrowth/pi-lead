---
name: pilead-send
description: Send a follow-up packet (fix-list or related task) into an existing executor session; use to reuse an idle session instead of spawning fresh.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Prefer this over `spawn` whenever an idle or reported session already owns the worktree/topic —
it keeps the executor's context. Check with `pilead list` first.

Write the follow-up as a packet file (a GOAL line, what to change, acceptance; not the standing
rules), then:

```
pilead send <sid> <packet>
```

`pilead` appends the report-path footer and drops the packet into the executor's inbox; the
running tab picks it up as a new user message. It prints `sent <sid> packet NNNN`. If the tab is
closed, `send` relaunches the executor.

A plain send is refused (exit 2, nothing changed) when the session hasn't finished its current
packet — `busy`, `stalled` or `paused` — or its status can't be read at all; there's no override, so
wait for the report (`pilead check <sid>`) or hand it to a successor with `--rotate` instead. Or
queue it: `pilead send <sid> <packet> --when-idle` stores it, and `pilead queue <sid> --deliver` sends
the oldest queued packet once the report is in (`pilead queue <sid>` shows them, `--cancel ID|all`
removes them). Queue only a packet that does not depend on your review of the current one.

`send` first reads the session's live context from pi's session log. A send prints
`  ctx: <live>, hit-rate <n>%` before `sent …` when it can read it. A **heavy** session (live context
at or above `context_nudge_tokens`, 150k by default) is refused with exit 2 and nothing is sent. That
is not a verdict on the executor's work; every turn re-sends the whole context. Two ways forward:
`pilead send <sid> <packet> --rotate` (usually right: see below), or
`pilead send <sid> <packet> --heavy-override "<reason>"`. Never pass `--heavy-override` without a real
reason (e.g. a one-line fix in files the session already holds); the reason goes to the ledger.

A fix-list is a packet like any other: name each finding, the file, and the acceptance check. Two
fix rounds that haven't landed it is the signal to move to a stronger model: from the third packet on,
`send` says so on stderr. Executors may not commit, so never ask one to.

**Rotate or upgrade** instead of reusing a heavy or stuck session:

```
pilead send <sid> <packet> --rotate [--model <m>] [--name <new-sid>]
pilead send <sid> <packet> --upgrade
```

Both retire the session (`pilead retire`: it writes a successor seed indexing its packets and
reports, then closes it) and spawn a successor `<sid>-r<N>` on the same worktree, topic, scope and
effort, with the seed and THIS packet as its packet 1. `--upgrade` goes one tier up (sonnet → opus);
for a model with no tier use `--rotate --model`. They need no `--heavy-override`. Refused, with
nothing changed, for a pinned session or one still busy with no report. The last line is
`successor: <sid>`: wait for that session's wake.

A `lint[…]` line on stderr is advice about the packet you just wrote; read it, and fix the packet
before the next send when it is right.

Announce `🚦 [pi-lead] — in flight: <sid> fix round` and wait for the wake.

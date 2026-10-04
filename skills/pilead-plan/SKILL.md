---
name: pilead-plan
description: Show or edit this lead's ordered plan — every planned packet with its target, model and derived state (queued / in-flight / reported / done) — plus add, rm, done, bind and the command for the next queued item; use when asked "what's left", "show the queue", "add this packet to the plan" or "what's next".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Run the one that matches what the user asked for (default to the bare table). This works only in a lead session
(`/pilead:mode` first); `--session` defaults to `$PI_LEAD_SID`, else `$PI_SESSION_ID`.

```
pilead plan
pilead plan add <packet.md> [--target fresh|<sid>] [--model M] [--note "…"] [--before N]
pilead plan rm N
pilead plan done N [--note "…"]
pilead plan bind N <executor sid> [--packet K]
pilead plan next
```

- **State is derived, never typed in**: `queued` = not yet picked up by a spawn/send; `in-flight` = a spawn/send of that
  packet by one of your executors was seen in the ledger (bound automatically); `reported` = its report exists;
  `done` = `pilead refs accept` ran for it, or you ran `plan done N`. `closed`, `superseded`, `dead` and `unknown` can
  also show. `plan bind` covers what the automatic match cannot see (a packet sent from a different path).
- **`plan next` never sends.** It prints the exact `pilead spawn …` / `pilead send …` command for the first queued item
  (exit 1, no output when none). Running it is an ordinary spawn/send decision: announce it and wait for the human's go
  (or proceed under `/pilead:auto` if it is routine and in-plan).
- Binding takes no lock, as in relay 0.5.4: when the plan is read, what binds is written back, so two commands on
  one plan at the same time are last-writer-wins. Run plan commands one at a time.
- An executor can read a plan but not change it (exit 2). A plan file that cannot be read is never written over.

**Then answer with one `🚦 [pi-lead]` line per queued item** — `#N packet → target (model): note` — and one line with
the counts (queued · in flight · reported · done). If nothing is queued, say so. See README, Plan.

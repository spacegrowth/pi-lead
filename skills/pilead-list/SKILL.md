---
name: pilead-list
description: Show every executor session with status and model; use first before any spawn, and to reconstruct what is in flight after a restart.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead list
pilead list --closed          # also closed / dead sessions (15 most recent)
pilead list --lead <sid>      # this lead's executors (and those with no lead)
pilead list --all-leads       # also ghost leads of other projects
pilead list --json            # everything, unfiltered, for scripting
```

Two tables. `LEADS`: `PROJECT SESSION MODEL TERM LIVE CTX MB LAST ACTIVE` — LIVE is `live` (its tab
exists), `unreachable` (no tab, active in the last 15 min — check on it), `ghost` (no tab, long quiet)
or `broken`; the last column, `WAKE`, says whether that lead's wake works — `ok`, `off` (no pi with the
extension watches its inbox), `STALE` (its pi stopped: reports wait; `pilead resume <sid>`), `STUCK` (a
claimed wake was not delivered) or `?` (unreadable, never ok). `EXECUTORS`: `SESSION STATUS TOPIC PROJECT MODEL PKT CTX TOKENS REPORTED`, one row per
session that is not `closed`/`dead`. STATUS is worked out from evidence, not just stored:
`busy | idle | reported | stalled | paused (limit) | broken` (`dead` sessions are hidden and counted).
CTX is live context / window, TOKENS prompt/output billed plus the cache hit rate; `-` is unknown,
never 0. VER is the pi-lead version each lead's pi loaded; `⚠ old extension` means run /reload in that pi.

Read the footnotes: `⚠ heavy` (prefer `pilead close` + a fresh `pilead spawn` for the next packet),
`⚠ approaching heavy`, `⚠ heavy lead`, `⚠ LIVE=ghost`, `⚠ broken` (a record to inspect), a CTX
ending in ` !` (the window is wrong), `(+N closed/dead hidden)`, and, per session with a queue, one
of `queue unreadable`, `queue stuck` (heavy — `send ... --rotate` carries the head on), `queue
failing` (`last_error` on the head) or `queued` (just waiting its turn). `list` never delivers.

- `🚦 <k> report(s) not yet handed to their lead: <sid> (packet 0001) (diff: 3 files +10/-2), …`: those reports
  are waiting and the wake is tried again; the size of each diff is there, so read it before you open a diff
  inline, and read the report with `pilead check <sid>`. Do not wait for the wake.
- `stalled`: pi is gone but its tab is open, or it has been busy a long time with a quiet session log —
  look at the tab. `paused (limit)`: a provider usage limit; wait or tell the user.
- `dead`: pi and its tab are gone; `pilead send` relaunches it with its context.
- A last line `auto-closed <sid> (<reason>)` / `auto-retired …`: run by you, `list` parked a finished
  executor whose report you had seen (landed or idle); `pilead send <sid> <packet>` reopens it.

Always run it **before** `spawn` (reuse an idle session on the same worktree/topic via `pilead-send`)
and right after a crash or restart — the state on disk is the crash-recovery surface, so
externalize what matters there and in the docs. For the full picture across leads use
`pilead board`; for one session with its TL;DR use `pilead check <sid>`. To bring a session's or a lead's tab to the front, `pilead focus <sid>`.

Summarize for the user in a line: what is in flight, what is reported and waiting on review.

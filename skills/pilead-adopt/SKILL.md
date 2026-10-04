---
name: pilead-adopt
description: Make this lead the owner of executor sessions, so their reports wake it; use when a session is an orphan or unowned (after a handoff, a crash, a lead that was closed), or when `pilead list` says `⚠ orphan`.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead adopt <sid> [<sid> …] [--force]
pilead adopt --from <old lead sid>
```

The calling lead (`$PI_LEAD_SID`, else `$PI_SESSION_ID`, registered) becomes the owner, so each report wakes it. `--from`
takes every session that names that old lead, closed and dead ones included. It prints one line per session
(`adopted <sid> from <old> (…)`), and an adopted report never announced before is announced to you once.

Adopted without `--force`: an orphan, an unowned session, a session of a ghost lead. Refused (exit 1, the others are still
adopted): one owned by a lead that is live, unreachable or broken, or whose owner is unknown, until `--force`. Exit 2 and
nothing changed: no calling lead, run from an executor, ids and `--from` both or neither, `--from` naming yourself.
`send`, `resume` and `restart` from a lead claim a session the same way. Concurrent adopts are last-writer-wins on
`meta.json` (worst case one duplicate wake). README: Ownership.

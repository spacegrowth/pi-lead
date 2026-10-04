---
name: pilead-takeover
description: Move a lead's registration, executors, wakes and plan to another session id; use after `/new` or `/fork` in a lead's tab, or when a new pi replaces a lead whose tab closed.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead takeover <old_lead_sid> [--as NEW_SID] [--project NAME] [--force]
```

The new id is `--as`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`. In order: a record for the new id; every executor of the old lead
(`adopted`, reason `takeover`); the plan; a forward note so a report that still names the old id reaches you; the old files go
(as `pilead stop` removes them); the old inbox's pending wakes are appended to the new one. The posture never moves: the new record
is manual with tier `auto`. It ends with the `pilead lead-start <new> --project '<project>'` to run in the new session. Each step is
safe to repeat.

A lead that is `live`, `unreachable` or `broken` is taken over only with `--force` or from its own tab. The extension runs it for
`/new` and `/fork`; `/resume` moves nothing. Limit: after `/new` in an unsaved pi session nothing moves. README: When the lead's session changes.

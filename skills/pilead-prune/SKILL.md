---
name: pilead-prune
description: Delete the state of sessions that ended long ago, and ghost lead records; use to tidy `~/.pi-lead` after work is committed.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead prune --dry-run [--days N] [--lead SID] [--all]
pilead prune [--days N] [--lead SID] [--all]
```

Run with `--dry-run` first: it lists what would go and changes nothing. A session's directory is removed only when its
STORED status is `closed` or `dead`, it is not pinned, its newest file is older than `--days` (default 7) and it has no queued
packet. A lead record goes only when it is a ghost, not the calling lead, older than N days, with no pending wake and no open plan
item. Everything else is listed as kept, with the reason.

Scope: `--lead SID`, else the calling lead, or `--all`; with none of them it exits 2. Never removed: anything outside home,
pi's own session logs, the ledger (only `pruned` / `lead_pruned` are appended), `config.json`, `models.json`.
Deleted state cannot be resumed. README: Lifecycle.

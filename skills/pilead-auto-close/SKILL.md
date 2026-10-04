---
name: pilead-auto-close
description: Park this lead's finished executors now — close them (retire when heavy) — instead of waiting for the sweep `list` and `check` run; use after you have seen reports and committed, or with `--dry-run` to see what would go.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead auto-close [--lead SID] [--dry-run]
```

A session is parked only when it is `reported`, not pinned, has a lead, its lead has **seen** the report (`verify`, `check`,
`diff`, `close` or `retire` ran), its inbox holds no waiting packet, and either its work provably landed (`landed`) or
the report has sat `auto_close_idle_minutes` (60; `0` turns that off). It prints one line per park —
`auto-closed <sid> (<reason>) — pilead send <sid> <packet> reopens it with its conversation`, or `auto-retired …` with the
`pilead spawn … --seed <sid>` command — or `nothing to park`. `--dry-run` says `would auto-close …` and writes nothing.

Refused: with no `--lead` and no calling lead (exit 2). A lead only parks its own executors. Pin one to keep it open
(`/pilead:keep`); `"auto_close": false` in `config.json` turns it off. Nothing is deleted. README: Auto-close.

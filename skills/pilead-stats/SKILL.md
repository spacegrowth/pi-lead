---
name: pilead-stats
description: Per-packet rounds, verdicts, report status, tokens and cost, by model; use to see which models earn their cost.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead stats [--lead SID] [--since DAYS] [--json]
```

One row per packet ever sent (closed and dead sessions too): SESSION, PKT, MODEL, EFFORT, ROUNDS (later packets sent before
the work was committed), VERDICT (the last `verify`), STATUS (the report's), TOKENS, COST; a trailer per session; and a SUMMARY
by model (packets, mean rounds, % COUNTS-MATCH, % exactly `clean`, tokens per packet, cost). A `~` before a token count means
an estimate. `--lead` and `--since` narrow it (the trailer then says `N of M packet(s) shown`). Read-only. README: `pilead stats`.

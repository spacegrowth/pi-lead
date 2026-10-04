---
name: pilead-keep
description: Pin an executor so nothing parks or deletes it; use when a follow-up for a finished session is imminent.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead keep <sid>
pilead keep <sid> --off
```

Pinned: `pilead close` and `retire` refuse it, `auto-close` never parks it, `prune` keeps it, `restart` refuses it. `--off`
releases the pin. `pilead spawn --keep` pins at spawn. `pilead handoff` names the pinned sessions the successor inherits;
the successor owns them until someone runs `pilead keep <sid> --off`. README: Lifecycle.

---
name: pilead-stop
description: Step down from lead mode for this session; use when asked to "unarm", "stop pilead", "step down from lead" or "turn off the gate".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

First run `pilead list` and say what is still in flight, so the next lead can pick it up. Then:

```
pilead stop "$PI_SESSION_ID"
```

This removes the lead's record, its route and its usage cache, and its inbox unless a wake is pending; its executors'
reports then wake nobody (adopt them elsewhere with `pilead adopt`). It is the same as `pilead close --self <lead_sid>`.
Stopping twice is not an error. The extension's edit gate stays on until this pi session ends.

Stepping down is reversible: run `/pilead:mode` to register as a lead again. See README, Lifecycle.

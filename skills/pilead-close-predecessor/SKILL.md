---
name: pilead-close-predecessor
description: After a handoff, close the outgoing lead's tab — only after the human has said yes; run by the successor lead.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead close-predecessor
```

It closes the tab recorded as `predecessor` in this lead's record, **by its handle only**, never by a title and never this
session's own tab. The `pi` on that tab gets SIGTERM, 0.3 s later the tab is closed, and it prints
`closed the outgoing lead's tab (<sid>)`. With no `predecessor` it prints `no predecessor recorded — nothing to close`.

Refused (exit 1, nothing closed): the predecessor is still a registered lead (run `pilead stop <sid>` first); no handle is
recorded; the handle is this session's own tab; the handle does not resolve (`close it by hand`). **Always ask the human
first** — never run it unasked. See `/pilead:handoff`, README: Handoff.

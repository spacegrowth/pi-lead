---
name: pilead-notify
description: Post a desktop banner — mostly run by the extension itself; use `--test` to check that banners work for a lead.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead notify <lead_sid> --test
pilead notify <lead_sid> --executor <sid> --packet N
pilead notify --no-lead --executor <sid> --packet N
```

`--test` posts a test banner whatever `notify_on_wake` says. The other forms are what the extension runs when a report's
wake was delivered (`<sid> reported`), or when a report finds no listener (`--no-lead`: it posts only when the executor's
lead is not listening). Tier 1 is iTerm2's own notification (click focuses the lead's tab); tier 2 is `osascript`, or
`notify-send` on Linux when installed (else no banner). A banner never changes a verb's output, exit code or a wake.
`lead_sid` is not allowed with `--no-lead`. README: Notifications.

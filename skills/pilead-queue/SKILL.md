---
name: pilead-queue
description: Show, cancel or deliver the packets queued with `pilead send --when-idle`; use to hand a busy executor its next packet.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead send <sid> <packet> --when-idle
pilead queue <sid> [--json]
pilead queue <sid> --deliver
pilead queue <sid> --cancel <ID|all>
```

`send --when-idle` copies the packet into `sessions/<sid>/queue/` while the session works (nothing is numbered or put in its
inbox) and prints `queued <sid> #<id> …`. `queue <sid>` lists them; `--deliver` sends the oldest once the session has reported
(a closed or dead one is reopened); `--cancel` removes one or all (one being delivered is kept). The extension delivers on its own
at a settle. Queue only a packet that does not depend on your review of the current one.

Refused: `--json` together with `--cancel` or `--deliver` (exit 2). Limits: a death after a packet is numbered but before
`inbox.md` is written counts as delivered and prints a `pilead check` hint; a death while a queued packet is being moved into the inbox can deliver it twice, never lose it.
README: Queueing a packet for a busy executor.

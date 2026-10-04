---
name: pilead-restart
description: Run a closed, dead or stuck executor's current packet again as a NEW conversation (its old context is lost); use when asked to "restart X", "re-run X's packet" or "start X over" — prefer /pilead:resume to keep the context.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead restart <sid> [--name NAME] [--model MODEL] [--effort LEVEL] [--model-override REASON] [--force]
```

This opens a fresh tab and runs the session's **current packet** again as a brand-new pi conversation. The old
conversation is NOT carried over — `/pilead:resume` does that. A pi session id *is* the conversation, so the successor is
a new session: `--name`, else `<sid>-r<N>` (`foo` → `foo-r2` → `foo-r3`). It keeps the worktree and its staged work,
topic, scope, lead, layout and effort (unless `--effort`), so the packet is re-done on top of whatever is already there:
use it when the previous attempt was botched and you want a clean redo.

- The model is `--model`, else config `executor_fallback_model` when the old session is `paused`, else its own; the
  ceiling rule of `spawn` applies (`--model-override REASON` launches above it; the reason is ledgered).
- Refused (exit 2, nothing changed): a lead's sid, a pinned session (`pilead keep <sid> --off` first), a session that is
  alive and not `stalled` (unless `--force`; a stalled one is exactly what restart is for), a missing packet file, a
  `--name` that exists, a model above the ceiling.
- The old session is closed and marked superseded by the successor. If the successor cannot launch, the old one stays
  closed but not superseded, no successor is left behind and the exit code is 1.
- From a lead, a restart claims the executor: an orphan or unowned session is adopted (`adopted …` printed first).

Relay's `--mcp` and `--context` have no equivalent: pi has no MCP, and the model list carries the context window.
See README, Lifecycle.

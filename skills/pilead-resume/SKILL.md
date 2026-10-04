---
name: pilead-resume
description: Bring back a closed or dead executor tab — or a crashed lead — WITH its full conversation, staged work intact; use when asked to "bring back X", "reopen X", "X's tab closed, restore it" or "restore the crashed lead".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead resume <sid> [--effort LEVEL] [--force]
```

This opens a new tab on the SAME pi session (`pi --session-id <sid>`), so the executor comes back with its entire
conversation and the worktree's staged work, and picks its packet up where it left off. Prefer it over
`/pilead:restart` whenever you want to keep what the executor already did.

It is refused (exit 2, nothing changed) unless `--force`: when the session is alive (its pi runs, or its tab exists);
when its pi is gone but its tab is still open (`pilead close <sid>` closes the tab); or when pi has no session log for it
(it would start an EMPTY conversation: use `pilead restart <sid>`). A superseded session can be resumed. Nothing is removed.

**Ownership follows the resume.** Resuming an executor from a lead also **adopts** it (re-points its wake to you), which
matters after a handoff. A session another live lead owns is NOT taken: `pilead adopt <sid> --force` takes it explicitly.

**Restoring a crashed lead.** Pass the lead's session id (see LEADS in `pilead list`): its tab reopens as `[Lead] <project>`
in its recorded cwd, and the first prompt is `pilead lead-start "$PI_SESSION_ID" --project '<project>'`, which re-arms it.
It is refused when the lead is `live` or pi has no log for it, unless `--force`, and always when its cwd is gone. The restored
lead runs `/pilead:list` to see what is in flight. See README, Lifecycle.

---
name: pilead-focus
description: Jump to a session's tab (iTerm2) or window (tmux) — an executor's or a lead's; use when asked to "go to X's tab", "show me session X", "focus X" or "take me to that executor".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead focus <sid | project | unique id prefix>
```

It brings the session's tab to the front and prints `focused <sid> (tab '<label>')` for an executor or
`focused lead <project> (<sid>)` for a lead. **Works for executors and leads**: pass either kind of id, or a lead's project
name. The handle recorded for the session names its backend, so a tmux pane (`tmux:%N`) is selected through tmux and
anything else through iTerm2; the two never need a flag.

It never opens a tab. When none has the recorded handle it exits 1 on stderr:
- an executor → `pilead resume <sid>` reopens it WITH its conversation (`/pilead:resume`); `pilead restart <sid>` runs its
  packet again as a new conversation (`/pilead:restart`);
- a lead → run `pilead lead-start` again in that lead's tab so it records the tab it is in now.

It also fails (exit 1) when iTerm2 is not running, or when no tmux server answers for a tmux handle. A session that
was not launched in iTerm2 or tmux is refused (`… is not in an iTerm2 tab`). Nothing is typed into any pane.
See README, Telling tabs apart, and tmux and Linux.

---
name: pilead-diff
description: Render an executor's staged changes to a self-contained HTML review page and open it; use when asked to show or review what a session changed.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead diff <sid> --open
pilead diff <sid> --all
```

Writes `$PI_LEAD_HOME/sessions/<sid>/diff-NNNN.html` from the executor worktree's **staged** diff
(what you'd commit), limited to the files the report names. `--all` renders the whole staged
diff — use it to catch staged files the report doesn't mention (scope creep). `--open` opens the
page in the browser.

Read it against the packet's boundaries and acceptance, not for its own sake — `pilead-review` says
what to look for. If several executors share a worktree, the staged diff contains all of theirs;
use the report's file list to tell them apart.

Nothing is committed by rendering. When satisfied, commit yourself.

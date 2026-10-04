---
name: pilead-tidy
description: Put every lead's iTerm2 tabs in order — the lead's tab, then its executors — and paint each group in its lead's colour; use when tabs have drifted.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead tidy [--dry-run] [--quiet] [--lead SID]
```

The order is `[Lead] [Exec 1] [Exec 2] … [Lead 2] [Exec 2.1] …`: each lead with a recorded tab (oldest first), followed by the
executors it owns that are not closed or dead. `--dry-run` prints the order and how many tabs would move, and changes nothing.
Each window is ordered on its own: no tab moves to another window, a tab pi-lead does not manage is never moved, nothing is closed,
opened or renamed. A tidy that could not be applied exits 1 with the reason.

It needs iTerm2's Python API (`pip3 install iterm2`, and Settings → General → Magic → Enable Python API). **Under tmux it does
nothing** (there is no tab tidy). The spawning verbs tidy by themselves; `tidy_tabs: false` in `config.json` turns that off.
README: Telling tabs apart.

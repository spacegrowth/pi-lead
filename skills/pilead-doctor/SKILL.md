---
name: pilead-doctor
description: Check that this machine and state dir are fit to run pi-lead; use first whenever something does not work, and after an update.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead doctor [--offline] [--quick] [--strict] [--json]
```

Read-only: it repairs and installs nothing and never calls a model. It prints one line per check — python, package files,
verbs, `pilead` on PATH, git, the terminal backend (iTerm2 or tmux), home, config, models, the ledger, sessions, git shims,
and four live checks that start `pi` (version, `pi commands`, `pi skills`, one copy) — each PASS, WARN, FAIL, SKIP or
NOT CHECKED, and a summary with all five counts.

`--offline` skips the live checks; `--quick` skips only the slower three. Exit 1 when any check FAILs, else 3 when any is
NOT CHECKED, else 0 (`--strict` counts a WARN as FAIL; 130 on Ctrl-C). `--json` prints the results as a list. Give it
`--home <tmpdir>` to test a scratch state dir. It refuses nothing. README: Doctor.

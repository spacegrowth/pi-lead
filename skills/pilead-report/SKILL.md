---
name: pilead-report
description: Print an executor's whole report; use only when the TL;DR from `/pilead:check` is not enough.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead report <sid> [--packet N]
```

It prints the whole report of the current packet (or packet N) in a green frame, then one dim line naming
`pilead diff <sid> --open` when the worktree has staged changes. It writes nothing. `/pilead:check` prints only the TL;DR.

Review **findings, not transcripts**: prefer `/pilead:review` and `/pilead:verify` to reading a whole report. Use this when
a TL;DR field cannot be judged without the detail. README: Lifecycle.

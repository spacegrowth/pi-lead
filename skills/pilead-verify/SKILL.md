---
name: pilead-verify
description: Machine-check an executor's report against its staged diff before you commit; use whenever a session has reported, to see whether its claims hold.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead verify <sid>
pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <path>
```

Checks that the report's TL;DR block is well formed, that the files it claims are actually staged,
and that declared counts match the index. Verdicts:

- `COUNTS-MATCH` — claims agree with what is staged.
- `MISMATCH` — a claimed file isn't staged (or the reverse), or counts differ. An empty index
  with claims over it is `MISMATCH`.
- `MALFORMED` — the TL;DR is missing a field (`UNVERIFIED:` is mandatory even when none).
- `INCONCLUSIVE` — cannot tell (e.g. nothing staged); never clears auto-commit.

A `REFS MOVED` block (a commit or ref change since the packet was sent; verdict `MISMATCH`,
`NOT-CLEARED-BECAUSE-refs-moved`) stops the commit and goes to the user; so does
`refs: not compared (<why>)` (`NOT-CLEARED-BECAUSE-refs-not-compared`) — nothing was checked.

Anything but `COUNTS-MATCH` stops: send a fix-list, don't commit. `--packet N` selects a packet
number. `--for-autocommit` adds the auto-commit gate (`--in-plan` and `--diff-reviewed` are
attestations — pass them only if true; `--findings` is where your `pilead-review` findings are saved).
A review file written by `pilead review` is checked by the gate against the staged tree.
It prints `AUTO-COMMIT: CLEARED` or `NOT-CLEARED-BECAUSE-<reason>`, exit 0 only when cleared.

`COUNTS-MATCH` says the report matches the index, not that it is true. `--rerun` re-runs the
report's declared pytest commands and exactly `npm test`, `npm run check` or `node --test` in its
worktree (argv only, the git shim in force) and compares the counts; it executes the worktree's code.

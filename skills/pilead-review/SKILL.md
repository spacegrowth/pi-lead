---
name: pilead-review
description: Review a reported executor's work with `pilead review` — a separate read-only pi that checks the report and reads the staged diff against the packet — then read its findings and put the review file through the commit gate; use when a session reports, and `--why` for any question about a session's state.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Review **findings, not transcripts**. Never `cat` a report, a session log or the ledger.

```
pilead check <sid>
pilead review <sid>
pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <the review file>
pilead review <sid> --why
```

1. `pilead check <sid>` — confirm `reported` and read the TL;DR. A `REFS MOVED` block stops the
   commit and goes to the user.
2. `pilead review <sid>` — a separate headless pi on read-only tools runs `verify` with the declared
   tests re-run, reads the report, the packet and the full staged diff, and answers in a fixed shape.
   It prints the model and that it costs tokens before it starts. `--fresh` leaves your context out;
   `--dry-run` shows what would start. It saves `review-NNNN.md` beside the session.
3. Read the FINDINGS, not the diff. Open a hunk yourself only when a finding names a sign-off-gated
   path (core logic, ledgers, parity/golden tests, migrations, deploys) and the change there is more
   than a guard or a rename. An outcome `INVALID` or `INCOMPLETE` is no review — run it again.
4. `pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <the review file>` —
   give `--in-plan` and `--diff-reviewed` only when they are true. The gate checks the review file
   against the staged tree: a review of another tree, not `VALID`, with a `blocker` or `should-fix`
   finding, or not recommending commit does not clear.

Then a recommendation: **commit**, **fix-list** (`pilead send`), or **escalate**. Sign-off-gated
paths need the user's explicit approval whatever the verdict. Executors never commit — you do, after
the user's go unless they've delegated it. Then `pilead close <sid>`.

**Why is it stalled?** Every question about a session's state — why it is stalled, what it is doing,
whether the wake fired — goes to `pilead review <sid> --why`: a separate pi, never with your context,
reads its status, newest report, the end of its log and its ledger lines, and answers in at most eight
lines.

**Commit identity.** Commit an executor's staged work as the repository's configured git identity (`git config user.name/user.email`, or the user's global config): never pass `-c user.name=…`/`-c user.email=…`, never `--author`, never invent a name or address. If no identity is configured, STOP and ask the human rather than inventing one.

Say `🚦 [pi-lead] — review needed: <sid> …` with the findings and your recommendation.

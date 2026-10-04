---
name: pilead-check
description: Poll one executor session for its status and, once reported, its TL;DR; use when asked "is it done" or when a wake arrives.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead check <sid>
pilead check --all            # every session not closed/dead, each under `== <sid> ==`
pilead check --all --json     # only JSON on stdout, one object per session
```

Before checking a session, `check` delivers it one queued packet (`pilead send ... --when-idle`) if it
has finished its current one — never for `--refs-only`, never reopening a `closed`/`dead` session.
Prints the session's status, worked out from evidence (pid, tab, report file, session log) — one of
`idle`, `busy`, `reported`, `stalled`, `paused`, `dead`, `closed` — and, when it is
`reported`, the report path plus its TL;DR block (outcome line, Status, Risk flags, UNVERIFIED,
Changed). Right after the status line: `queued: N waiting` when packets are still queued for it,
`queue: cannot be read (...)` for a queue file that is broken, or `queue: #id could not be delivered —
...` when the head keeps failing; `--json` objects gain `queued`/`queue_error` for the same.

A `reported` session also shows the size of its staged diff, `diff: 3 files +10/-2`, right after the report's
path (`diff: not read (<why>)` when git could not be read; nothing when nothing is staged): read that size
before you open a diff inline, and use `pilead diff` when it is large.

When pi's session log for it is found, one more line follows the TL;DR:
`tokens 1.2M/34k over 57 requests · ctx 146k/1M · approaching heavy` — prompt/output tokens billed so
far, then live context / the model's window (what the next turn starts from). `· heavy` means the next
`pilead send` is refused without `--heavy-override`: prefer a fresh `pilead spawn`. `-` is unknown (never 0).
No log found (or found but unreadable): no tokens line, one stderr line says which.

- `busy`: working; do nothing. `idle`: waiting for a packet, no report yet for the current one.
- `reported`: go to `pilead-review`. Do not `cat` the whole report — read the TL;DR, then use `pilead-verify`
  and `pilead-diff`.
- `stalled`: pi's tab is open but pi is gone, or it has been busy a long time with a quiet log — look at it.
- `paused`: the provider's usage limit was hit; wait, or tell the user.
- `closed` / `dead`: the tab is gone; `pilead send` relaunches it if you need it back.

For a status-bar line that writes nothing, `pilead status <sid>`; for per-packet history, `pilead stats`.
When the TL;DR is not enough, `pilead report <sid> [--packet N]` prints the whole report and writes nothing.

For everything at once use `pilead list`.

Report to the user as `🚦 [pi-lead] — review needed: <what reported>` (reported) or a plain
status line. A risk flag, non-`clean` Status or an UNVERIFIED claim always goes to the user.

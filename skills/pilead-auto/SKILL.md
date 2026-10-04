---
name: pilead-auto
description: Flip this lead's autonomous posture — proceed by default on routine in-plan steps instead of asking, or go back to waiting; use when asked to "go autonomous", "stop asking me every time", "just proceed", "turn auto off" or "what posture am I in".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Run ONE of these, matching what the user asked for (default to `status` if they only asked what posture you are in):

```
pilead auto on     --session "$PI_SESSION_ID"
pilead auto off    --session "$PI_SESSION_ID"
pilead auto status --session "$PI_SESSION_ID"
```

`--session` defaults to `$PI_LEAD_SID`, else `$PI_SESSION_ID`. This works only in a lead session (`/pilead:mode` first); an
executor cannot change a lead's posture. `status` prints one line: ON or OFF, and whether config or `pilead auto` set it.

**Then tell the user, in one line, which posture you now hold and what that changes** — the posture governs *your*
behaviour from here on. Use the `🚦 [pi-lead]` marker.

- **on** → your default inverts from *wait* to *proceed* on routine, in-plan beats: sending the obvious next packet,
  spawning an executor the approved plan already calls for, reviewing a clean report. Follow the stop-list and the
  rule that **every autonomous action is announced along with what you would have asked** (README, Autonomous mode).
- **off** → back to announce-and-wait on every approval beat. The default.
- **status** → the posture and where it came from.

**Say plainly whenever you turn it ON:**
- **Committing executor work is gated separately.** It is allowed without asking only when
  `pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <path>` prints `AUTO-COMMIT: CLEARED`
  (`COUNTS-MATCH`, a `clean` TL;DR with no risk flags and nothing UNVERIFIED, the packet in the approved plan, nothing
  sign-off-gated touched, refs unmoved, the diff reviewed by `/pilead:review`). `--in-plan` and `--diff-reviewed` are
  your attestations: pass them only when true. Anything not cleared → stop and ask, naming the condition.
- The posture is scoped to this session: `pilead lead-start` (every `/pilead:mode`) resets it to config `autonomous_mode`.

Do NOT turn it on by yourself. The user opts in; you may *suggest* `/pilead:auto on` if the round-trips get tedious, then
wait. See README, Autonomous mode.

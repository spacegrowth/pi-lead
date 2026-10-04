---
name: pilead-tier
description: Flip this lead's model-tier posture — who decides which model an executor runs on: auto (the lead, by rubric; default), manual (the human at EVERY spawn/rotate/upgrade) or lead (executors mirror the lead's own class); use when asked "who picks the executor's model", "make me choose every model" or "what tier am I in".
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

Run ONE of these, matching what the user asked for (default to `status`):

```
pilead tier auto|manual|lead|status --session "$PI_SESSION_ID"
pilead tier ask --session "$PI_SESSION_ID" --packet <packet> --json
```

This works only in a lead session (`/pilead:mode` first); an executor cannot change it. **Then tell the user, in one
line, which posture you hold and what it changes** (`🚦 [pi-lead]` marker).

- **auto** (default) → you choose each executor's model per packet by the `/pilead:spawn` rubric.
- **manual** → the human decides at EVERY `spawn`, `send --rotate` and `send --upgrade`. **The one posture that ADDS A
  STOP — autonomous mode never relaxes it.** Those three with no `--model` refuse (exit 2) and point at `tier ask`.
- **lead** → with no `--model`, `spawn` and `send --rotate` launch on YOUR OWN class (`model: tier lead — …` printed).
  The ceiling and fallback still apply; `--model` wins; `send --upgrade` still goes one tier up.

**Manual protocol**, with no `--model` in hand: (1) run `pilead tier ask --session … --packet <packet> --json`; (2) put its
options UNCHANGED to the human, recommended first, never re-ordered, re-worded or dropped; (3) pass the answer as `--model`.
If `ask` exits non-zero (no model available: run `pilead doctor`), that is the answer; do not ask a question with no options.

Layering: config `executor_default_model` / `executor_model_ceiling` < `pilead tier` (this session; reset to `auto` by every
`pilead lead-start`) < `--model` on one spawn. See README, Model check and model tier.

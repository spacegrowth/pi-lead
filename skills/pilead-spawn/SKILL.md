---
name: pilead-spawn
description: Open a new executor in its own iTerm tab seeded with a work packet; use to delegate genuinely new work when no idle session already owns the territory.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

**First run `pilead list`** — if an idle session already owns this worktree/topic, use `pilead-send`
instead (cheaper, keeps context). Spawn only for new work, a dead session, or a stronger model
(a session's model is fixed at launch). Your **first spawn of a session needs the user's go**
(see `pilead-mode`).

**Model per packet, by judgment demanded and cost of a wrong-but-plausible result:**
- `dsflash` / `haiku` — mechanical, fully specified, verifiable by command.
- `sonnet` — the workhorse: bounded features, bugfixes with a repro, tests, one-module refactors.
- `opus` / `dspro` — unknown-root-cause debugging, cross-cutting or core-logic work, anything
  where a plausible wrong result would survive review. `fable` likewise where available.
- Choose per packet by the judgment the work needs — never by how many files it touches.
- Effort is a quality lever on top of the right model, never a way to save cost; the cheaper class is.
- Two fix rounds that did not land: move one class up (`pilead send <sid> <packet> --upgrade`).
  Acceptance a script could check: a lower class is enough.
- Pass a **tier alias** (`haiku|sonnet|opus|fable|dsflash|dspro`) or an explicit `provider/id`
  (e.g. `deepseek/deepseek-flash`). Aliases resolve via a cache of `pi --list-models`; never type
  version ids from memory. Without `--model`, config `executor_default_model` (default `sonnet`).
- **Tier posture** (`pilead tier status`): under `manual` a spawn with no `--model` refuses until the human
  has answered `pilead tier ask --session "$PI_SESSION_ID" --packet <packet>`; under `lead` it runs on your
  own class. `--model` always wins. Only classes `pilead lineup` lists as available exist here.
- **Ceiling:** a model above config `executor_model_ceiling` (default `opus`, so `fable`) is refused
  (exit 2). Pass `--model-override "<reason>"` only when the packet truly needs that model and you can
  say why in one line; the reason is ledgered. Otherwise pick a model at or below the ceiling.
- **Effort** (`--effort off|minimal|low|medium|high|xhigh|max`): pi's thinking level. Without it, the
  packet's `EFFORT: <level>` line, else config `executor_default_effort` (default `high`). Lower it for
  mechanical packets, raise it for hard reasoning.
- `--scope "<what it covers>"` shows in `pilead list` (default: the topic); `--keep` pins the session.
- `--seed <retired-sid or seed file>` — taking over a retired session's territory (`pilead retire`):
  its successor seed (an index of that session's packets and reports) is appended to the packet.
  `pilead send <sid> <packet> --rotate` does retire + seeded spawn in one step.

Write only the task-specific packet (GOAL / ROLE / REQUIRED READING / WORK PACKET / ACCEPTANCE /
BOUNDARIES) to a file; the standing rules ride in the executor's system prompt and `pilead`
appends the report path, the review-page command and the closing line. Then:

```
pilead spawn <worktree> <topic> <packet> --model <alias|provider/id> [--effort <level>] --lead "$PI_SESSION_ID"
```

`<worktree>` is the absolute path of the shared project dir; parallel executors on one project
pass the same one. `<topic>` becomes the session id `<topic>-<HHMMSS>`. Add `--name "<label>"`
for the tab title. Needs a prior `pilead lead-start` (else exit 2). It prints
`spawned <sid> model=… tab=…` then `effort=… scope=…`; note the sid. A `model: … fallback` line
means the alias you asked for is not available here. The executor stages, never commits, writes a
report, and stays idle; `git commit/push/merge/rebase/reset --hard/tag` are blocked in its tab.
A `lint[…]` line on stderr is advice about the packet you just wrote; read it, and fix the packet
before the next send when it is right.

Then tell the user: `🚦 [pi-lead] — in flight: <what you delegated>`, and wait for the wake.

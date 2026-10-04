# 🚦 pi-lead — a lead/executor pattern for pi

[claude-relay](https://github.com/spacegrowth/claude-relay)'s lead/executor pattern, for the
[pi](https://pi.dev) coding agent. One pi session (the lead) delegates work to executor sessions in
their own terminal tabs, each seeded with a work packet. The lead plans, delegates and reviews;
executors **stage their work (never commit)**, write a report, and stay idle for reuse; the lead
reviews the staged diff and commits. You stay in the loop at every gate: the lead proposes a split and
waits for your go, and wakes itself when an executor finishes.

## Why

- **Per-executor model choice.** The lead picks `--model` at spawn time. A strong model does the
  thinking; cheaper ones do the bounded implementation.
- **Bounded packets.** Each executor gets a small work order with acceptance criteria, so there is
  less room to wander off-spec, and the staged-diff review catches the rest.
- **Context isolation.** Each executor burns its own context window, so the lead stays light.
- **Real parallelism.** Independent packets run at once, each in its own tab.
- **Human gates.** Nothing spawns without your go, nothing lands without your review.

## Requirements

- macOS with iTerm2, or tmux 3.2+ on macOS or Linux
- pi 0.87 or newer
- Python 3.9+ and git

## Install

```bash
pi install npm:pi-lead          # or: pi install /path/to/pi-lead
```

## Quick start

1. Start pi in your project and describe the work (or point it at a brief).
2. Type `/pilead:mode`. The session becomes the lead, checks its model is strong enough, and turns
   the routing gate on.
3. The lead proposes a split, one line per executor, and **waits for your go**.
4. On your go, executors open in their own tabs. `pilead list` shows what is in flight; the lead
   wakes when each one reports.
5. Review each report and its staged diff (`pilead check`, `pilead verify`, `pilead diff <sid> --open`),
   then commit, or send a fix-list with `pilead send`.

## Mental model

The flow, in five beats: **design** the work, **arm** the lead with `/pilead:mode`, **approve** the
split, **spawn** the executors, then **review and commit**.

- A **lead** plans, delegates and reviews. An **executor** is a session in its own tab; it stays
  alive across packets, like one engineer you keep assigning related work to.
- A **packet** is a work order (a `.md` file, GOAL line first). The executor rules (stage, don't
  commit, one deliverable, report format) are injected for you.
- A **report** is what the executor writes back. Its work is staged, not committed.

## Commands

Each is also a slash command (`/pilead:<verb>`).

| Command | What it does |
|---|---|
| `pilead lead-start` | Register this session as a lead (`/pilead:mode` does it for you) |
| `pilead spawn` | Start an executor in a new tab from a packet |
| `pilead send` | Send an executor a follow-up packet |
| `pilead check` | Show an executor's report and status |
| `pilead verify` | Check a report against what is actually staged |
| `pilead diff` | Show the staged diff as a page (`--open` opens it) |
| `pilead list` | List leads and executors, and their real state |
| `pilead board` | One page for everything (`--open`, `--live`) |
| `pilead handoff` | Pass the lead role to a fresh session |
| `pilead resume` / `restart` | Bring back a dead executor, with or without its conversation |
| `pilead close` / `stop` | Close an executor / step down as lead |

Every verb, flag and config key is in [docs/REFERENCE.md](docs/REFERENCE.md).

## The gate

Once a session is a registered lead, a hook **blocks its large inline edits** (a new file, or over
about 40 changed lines) and tells it to delegate, or to run `/pilead:route retain "<reason>"` for
genuinely lead-appropriate work (a two-minute grace window). Packet files are exempt. It is friction,
not trust: it only acts in registered leads, and it only sees the write shapes it can parse.

## Verifying a report

`pilead verify <sid>` checks a report against the staged reality in its worktree: the TL;DR block is
well formed, the files it claims are really staged, the declared test counts match. It stamps
`COUNTS-MATCH`, `MISMATCH`, `MALFORMED` or `INCONCLUSIVE`. `COUNTS-MATCH` means the checkable numbers
agree and **nothing more**; it cannot tell you the report is true. It prints the report's risk flags
and UNVERIFIED lines as they are, and grading those is your job.

## Autonomous mode

By default the lead announces each routine step and waits for you. `pilead auto on` (only when you
ask) lets it proceed on routine steps inside the approved plan, announcing each action and what it
would have asked. The gates and fences are the same, and `pilead auto off` puts it back.

## tmux and Linux

Inside tmux (on a Mac, or on a Linux box over SSH) executors open as tmux windows next to the lead's,
or as split panes with `pilead spawn … --pane`. Banners are status-line messages, or `notify-send`
where it exists. `--open` uses `xdg-open`, and prints the address when there is none.

## Troubleshooting

- **First, `pilead doctor`.** It checks the install, the terminal backend and that the extension loads.
- **`pilead list` / `pilead check --all` show the real state** (busy, reported, stalled, dead). Trust
  them over how a tab looks.
- **Tab died mid-build?** `pilead resume <sid>` reopens it with its conversation;
  `pilead restart <sid>` re-runs the packet fresh.
- **Executor finished but the lead never woke?** Look at the WAKE column of `pilead list`;
  `pilead report <sid>` pulls a report by hand.
- **After updating pi-lead,** `pilead list` flags a lead on an older extension: run `/reload` in it.
- **Model too weak?** If `/pilead:mode` prints `STOP:`, switch the lead to a stronger model with `/model`.

## More

- [docs/REFERENCE.md](docs/REFERENCE.md) — everything, in full
- [docs/smoke-check.md](docs/smoke-check.md) — thirteen live checks before a release
- [docs/manual-checklist.md](docs/manual-checklist.md) — all 76 live checks
- [CHANGELOG.md](CHANGELOG.md), [LICENSE](LICENSE)

Full reference. The short version is the [README](../README.md).

# pi-lead

The lead/executor pattern from [claude-relay](https://github.com/spacegrowth/claude-relay), for
[pi](https://pi.dev) on macOS + iTerm2, or inside tmux on macOS and Linux.

A lead session plans, delegates and reviews. Executors each run in their own iTerm tab, seeded with
a work packet; they **stage, never commit**, write a report, and stay idle for reuse. The lead
reviews the staged diff (through `pilead verify` and `pilead diff`) and commits.

## Why

- **Per-executor model choice.** The lead picks `--model` at spawn time (and again per rotation with `send --rotate`) — a
  strong model does the thinking (decomposition, review, integration), cheaper models do the bounded implementation. Net
  effect: your highest-cost tokens go to judgment calls, not to grinding out diffs a smaller model can produce just as well.
- **Bounded packets narrow the failure surface.** Each executor gets a small, self-contained work order with explicit
  acceptance criteria — narrow scope leaves less room to wander off-spec, and the report + staged-diff review is where
  mistakes that happen anyway get caught, before they land.
- **Context isolation.** Each executor burns its own context window, not the lead's — the lead stays light across a long
  session. When it doesn't, `pilead list` flags it, the lead is told once when its turn ends, and `/pilead:handoff` exists for
  exactly that.
- **Real parallelism.** Independent packets run concurrently, each in its own tab (or tmux window or pane) — no serializing
  unrelated work through one context.
- **Fenced executors.** An executor cannot `git commit` or `push` (a git shim and the extension refuse them, not just the
  instructions), its model is pinned at spawn, and it parks itself in an idle session when done. The lead carries the
  decisions; executors carry the diff.
- **Human gates throughout.** Nothing spawns without your go, nothing lands without your review, and executors stage, never
  commit.
- **Hallucination risk is contained, not eliminated.** An executor can be confidently wrong — invent an API, misread the spec,
  report tests green that it never ran. pi-lead's answer is structural, not trust: acceptance criteria live in the packet, the
  report has a required format, and the lead independently verifies the staged diff (`pilead verify --rerun` re-runs the
  declared tests itself) before anything is committed. Wrong output still costs a review cycle — it just doesn't land.

## Mental model

The flow, in five beats:

1. **Design** — tell the session what to build, or point it at a brief.
2. **`/pilead:mode`** — arm it as the lead. The order is flexible: arm first and then describe the work, or design first and
   arm after — both work. Armed with no task yet? Don't invent one: say you are ready and wait to be told what to build, then
   propose the split as usual. The lead names itself from context — 2–4 words on what it is here to do, not the folder it is in
   — and passes that as `--project`; only a genuinely fresh, brief-less session falls back to the directory name, and says so.
3. **Approve the split** — the lead proposes executors and packet files and **waits for your go** before its first spawn.
4. **Spawn** — executors build in parallel, each in its own tab, each on the model the lead picked for it; the lead is woken
   (`🚦 [pi-lead] executor <sid> reported: <path>`) as each one reports. That wake is the lead's inbox being read by its own
   extension: an idle lead hears about a report without anyone typing.
5. **Review → commit** — the lead checks the report, reviews the staged diff, and commits; an executor never does. Finished
   executors then **park themselves** (auto-close once their work has landed or they have idled out; `pilead send` brings one
   back with its conversation) — or take follow-up packets.

And the three nouns:

- A **lead** is the pi session that plans, delegates and reviews, registered with `pilead lead-start`. An **executor** is a
  session in its own tab, working one worktree and topic. It stays alive across packets — one engineer you keep assigning
  related work to, not a disposable one-shot.
- A **packet** is a work order (a `.md` file, GOAL line first). The rules every executor follows (stage, don't commit; one
  deliverable per packet; the report format) live in `skills/executor-rules.md`, injected into the executor's system prompt;
  `pilead spawn` and `pilead send` append only the per-packet report path and closing steps — you never write those.
- A **report** is what the executor writes back when done, at the path the footer names. Its work is **staged, not
  committed**: the lead reads the TL;DR (`/pilead:check`), checks the claims against the staged diff (`/pilead:verify`,
  `/pilead:review`) and commits itself.

## Requirements

- macOS with **iTerm2** (Terminal.app only opens a window; see Non-goals), or **tmux 3.2+** on macOS or Linux (see
  [tmux and Linux](#tmux-and-linux))
- **pi ≥ 0.87** installed
- **Python 3.9 or newer** as `python3`, standard library only
- **git 2.31 or newer** (refs detection uses `git rev-parse --path-format=absolute`); Node only for running the test suite.
  Doctor's git-shim probe sets `GIT_CONFIG_GLOBAL`, which git reads from 2.32 on; on git 2.31 the probe
  is still safe, because `GIT_DIR` already points at a repository that does not exist.
- Developers: the test suite also needs `perl` (the TypeScript tests' pi stub uses it); tests give their session ids to pi with
  `--session-id`.

## Install

```bash
pi install /abs/path/to/pi-lead     # or `pi install npm:pi-lead` once published
pi list                     # confirm the package is installed
```

Inside any pi session that loads the extension, `pilead` is on PATH (the extension prepends its own
`bin/`); outside pi, run it by path or `npm install -g pi-lead`.

The package ships one extension (`extensions/pi-lead.ts`), 37 skill files (`skills/`, one per `/pilead:<name>` command, plus `pilead-route`) and the CLI. The extension picks its role from
`PI_LEAD_ROLE` (`roleFrom`):

| `PI_LEAD_ROLE` | Role |
|---|---|
| `executor` | executor |
| `reviewer` | reviewer — the read-only second pi of `pilead review` |
| any other value, or none | lead |

Only those exact words count: `Reviewer`, an empty value or one that cannot be read is a lead, as before. A
reviewer lets the tools `read`, `grep`, `find` and `ls` through and refuses every other tool (`bash`, `edit`,
`write`, any extension's tool) with `pi-lead reviewer: this session only reads — it runs no command and changes
no file.` It reads, watches and writes nothing under the pi-lead home — no inbox, claim file, status, health
file, ledger line or session directory, even with a lead's `PI_LEAD_SID` in its environment — sends no
message, shows no banner or footer, registers no `/pilead:` command and writes nothing at its end. It sets the
marker `PI_LEAD_HELD_BY` like the other roles and never reads it.

A pi process that is not the session's own does no lead work — it claims, watches and sends nothing, puts
nothing back and writes nothing at its end — when either of these holds:

| Condition | Read at | Notice (once, at session start) |
|---|---|---|
| `PI_LEAD_HELD_BY` holds another process's id (the pi was started from inside a pi-lead session's shell) | when the extension loads | `pi-lead: this pi was started from inside a pi-lead session (process <pid>), so it does no lead work: …` |
| pi's run mode is `print` (`pi -p`) or `json` | session start | `pi-lead: this is a <print \| json> run, so it does no lead work: …` |

A second pi started from a lead's shell inherits the lead's environment, its `PI_LEAD_SID` included, and
would otherwise claim the lines in the lead's inbox, so the real lead would never be woken. The extension
sets the marker `PI_LEAD_HELD_BY` to its own process id when it loads, so every process started from the
session's shell carries it. The executor role reads the marker too (a pi started from an executor's shell
does no executor work); its run mode changes nothing. The gates stay on, `/pilead:` commands work as
before, and `/pilead:status` says `lead work: on` or `off (<why>)` (`executor work:` for an executor).
Every piece of lead work asks this rule before it reads, writes, watches or sends anything: the inbox and its
claim files (an orphaned claim is not recovered), the wakes, the health file and its beat, the footer, the tab's
name, the banners, the turn-end call and the move of the lead role at `/new` or `/fork`.

## Quick start

1. Start pi as the lead and check the extension is live:

   ```bash
   pi                                  # after `pi install`
   pi -e ./extensions/pi-lead.ts       # or, from a checkout, the extension alone
   /pilead:status
   ```

   `-e` loads the extension only: every `/pilead:` command works, but the model cannot load a skill
   on its own until the skills are loaded too (`pi install`, or `--skill ./skills`).

2. Adopt the role: run `/pilead:mode`. It registers you (`pilead lead-start "$PI_SESSION_ID"
   --project "<name>"`), then plans and asks for the user's go before the **first** spawn.
3. Write a packet (a markdown file; GOAL line first), then spawn one executor on a cheap model:

   ```bash
   pilead spawn /abs/path/to/project my-topic /abs/path/to/packet.md \
     --model deepseek/deepseek-flash --lead "$PI_SESSION_ID"
   ```

4. The lead is woken when the executor reports (`🚦 [pi-lead] executor <sid> reported: <path>`).
   `pilead spawn` and `pilead send` end every packet with a footer naming the report path, one
   command and the closing line; the executor runs that `pilead diff` itself after writing its
   report, so the review page already exists when the lead is woken:

   ```text
   ---
   (pi-lead — do not remove or reword this section)

   REPORT: write your full report (the outcome in one plain sentence first, then the TL;DR block) to
     <home>/sessions/<sid>/report-NNNN.md
   AFTER writing it, run:
     pilead diff <sid> --home <home>
   (it makes the review page of your staged changes; do not open it and do not attach it anywhere; run it once)
   Then end your final turn with EXACTLY this one line, nothing after it:
         ✅ [pi-lead] — staged + report written — diff: file://<home>/sessions/<sid>/diff-NNNN.html — idle, awaiting the lead's review.
   ```

   Review it:

   ```bash
   pilead check <sid>
   pilead verify <sid>
   pilead diff <sid> --open
   ```

   `pilead verify <sid> --rerun` also re-runs the report's declared test commands in the
   worktree (pytest, or exactly `npm test`, `npm run check` or `node --test`; argv only, never a
   shell) and compares the counts; a declared command it will not run, such as `yarn test`, is named
   as NOT RE-RUN; INCONCLUSIVE only when nothing declared could be re-run. See
   [Re-running a report's tests](#re-running-a-reports-tests).

5. Commit the staged work yourself (executors never commit), or `pilead send <sid> <fix-list>`.
   Then `pilead close <sid>`. `pilead list` and `pilead board --open` show what is in flight.

## Commands

Every verb a human types has its own `/pilead:<name>` command, backed by one skill of the same name, so the 38
commands below are all there is to remember. The 36 skill commands are registered in the lead role only; `status`
is in both roles and `route` is lead only (`/pilead:route` is the extension's own command; `skills/pilead-route`
is only the skill file that explains it). Each command sends its skill's text to the model, which runs the verb.
`pilead <verb> --help` is the truth about a verb's flags.

| Command | What it does |
|---|---|
| `/pilead:mode` | Adopt the lead role: plan, delegate, review, never implement large work |
| `/pilead:spawn` | Open a new executor in its own tab, seeded with a packet |
| `/pilead:send` | Send a follow-up packet to an existing executor (`--when-idle` queues it for a busy one) |
| `/pilead:check` | Poll one executor for status and its TL;DR |
| `/pilead:list` | Every lead and executor session with status and model |
| `/pilead:review` | Review a reported executor's work with a separate read-only pi and return findings (`--why` answers a question about a session) |
| `/pilead:verify` | Machine-check a report against its staged diff |
| `/pilead:close` | Close an executor's tab and mark it closed (`--supersede`, `--keep-tab`) |
| `/pilead:diff` | Render an executor's staged changes as an HTML page (the executor already runs it once after its report) |
| `/pilead:board` | One HTML page of every lead and executor (`--live` keeps it current) |
| `/pilead:auto` | Flip the lead's autonomous posture: proceed on routine in-plan steps, or wait (`on`, `off`, `status`) |
| `/pilead:tier` | Flip who chooses an executor's model: `auto`, `manual`, `lead`, `status`, `ask` |
| `/pilead:plan` | Show or edit the lead's ordered plan: `add`, `rm`, `done`, `bind`, `next` |
| `/pilead:handoff` | Hand the lead's role to a fresh successor lead from a memo (only when the human says so) |
| `/pilead:restart` | Run an executor's current packet again as a NEW conversation |
| `/pilead:resume` | Bring back a closed or dead executor, or a crashed lead, WITH its conversation |
| `/pilead:retire` | Close a heavy executor and write its successor seed |
| `/pilead:focus` | Bring an executor's or a lead's tab (iTerm2) or window (tmux) to the front |
| `/pilead:stop` | Step down from lead mode |
| `/pilead:adopt` | Make this lead the owner of orphaned or unowned executors, so their reports wake it |
| `/pilead:auto-close` | Park this lead's finished executors now (`--dry-run` shows which) |
| `/pilead:close-predecessor` | After a handoff, close the outgoing lead's tab, once the human has said yes |
| `/pilead:doctor` | Check that this machine and state dir are fit to run pi-lead (read-only) |
| `/pilead:gate` | Say what the lead's bash write gate makes of the command on stdin (read-only) |
| `/pilead:keep` | Pin an executor so nothing parks or deletes it (`--off` releases it) |
| `/pilead:lineup` | Say which model classes this pi offers and where the lead sits: the `Model check:` line |
| `/pilead:lint` | Check a packet file before sending it (advisory; `--strict` exits 1 on warnings) |
| `/pilead:notify` | Post a desktop banner; `--test` checks that banners work for a lead |
| `/pilead:prune` | Delete the state of sessions that ended long ago, and ghost lead records (`--dry-run` first) |
| `/pilead:queue` | Show, cancel or deliver the packets queued with `send --when-idle` |
| `/pilead:refs` | `accept`: make the refs as they are now the packet's baseline, after you commit |
| `/pilead:report` | Print an executor's whole report (`check` prints only its TL;DR) |
| `/pilead:stats` | Per-packet rounds, verdicts, report status and tokens, by model |
| `/pilead:takeover` | Move a lead's registration, executors, wakes and plan to another session id |
| `/pilead:tidy` | Put every lead's iTerm2 tabs in order: the lead's tab, then its executors (nothing under tmux) |
| `/pilead:whoami` | Say who a session is: a lead and its executors, or an executor and its lead |
| `/pilead:status` | Show role, session id and watched paths (both roles) |
| `/pilead:route` | `retain "<reason>"` opens the lead routing gate for `grace_seconds` (2 min by default; a registered lead only); `pilead route retain` does the same from a shell |

Four verbs have no skill and no command, on purpose: `pilead lead-start` (`/pilead:mode` runs it), `pilead turn-end` and
`pilead escalate` (the extension runs them, see [When the lead's turn ends](#when-the-leads-turn-ends) and
[When a report finds no listener](#when-a-report-finds-no-listener)) and `pilead status` (the verb; `/pilead:status` is the
extension's role command).

When the skills are loaded (`pi install` or `--skill ./skills`), the same skills also appear as `/skill:pilead-<name>`,
because pi lists every skill that way.

## Names

Wherever a `pilead` verb takes an id, you can type something shorter. `lib/pilead/names.py` reads what you typed and decides what it means. The first match wins:

1. **An executor's id**, when `sessions/<it>/meta.json` exists.
2. **A lead's id**, when `leads/<it>.json` exists.
3. **A lead's project**, when exactly one lead record has that `project`. It is compared as typed: same case, nothing trimmed.
4. **A unique prefix** of 6 characters or more of one executor's or lead's id.

Anything else goes to the verb as typed, and the verb answers as it always has (`unknown session …`, `lead id … is not usable: …`). If a project or a prefix fits more than one session, the verb does nothing. It exits 2 and lists every candidate (newest first, with its project or topic and its age) so you can pick one:

```
pilead: ambiguous session '4c1d0e' — matches multiple sessions:
  4c1d0e2b-5a49-4382-8170-6f5e4d3c2b1a  (lead 'other', v0.1.0, 2h)
  4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d  (lead 'proj', v0.1.0, 3d)
pass the session id (or a unique prefix of it) instead
```

An exact id always wins, even over a project of the same text or a longer id that starts with it. The resolver only reads files under home, and it never builds a path from a name that is not a usable id. An id taken from the environment (`$PI_LEAD_SID`, `$PI_SESSION_ID`, and the default of `pilead status` with no argument) is used as it is and never resolved, so `pilead status --statusline` lists no directory.

Three arguments are never resolved, because each names something that does not exist yet:

- the id given to `pilead lead-start` (the command creates that lead);
- `takeover --as` (the registration it is about to make);
- the new session's name in `spawn --name`, `send --name` and `restart --name`.

`pilead handoff <memo>` takes a path, and `plan add --target` takes `fresh` or an exact id.

`pilead focus` keeps its own wider project match: trimmed and in any case. It also takes a unique id prefix. Everywhere else a project is compared exactly as typed.

`pilead whoami [token] [--json]` says who a session is, reading state and writing nothing. The token is an id, a project or a prefix; with none it uses `$PI_LEAD_SID`, else `$PI_SESSION_ID`. For a lead it prints the project, terminal, tab label and its executors with their stored status. For an executor it prints its status, current packet and the report path, and whether that report exists yet. It also names the owning lead and that lead's tab.

## Lifecycle

| Step | Who | What happens | Status |
|---|---|---|---|
| `lead-start` | lead | registers `leads/<sid>.json` and its inbox | – |
| `spawn` | lead | opens a tab, launches `pi` as executor with the packet | `busy` |
| work | executor | edits, stages; `git commit/push/merge/rebase/reset --hard/tag` blocked | `busy` |
| report | executor | writes `report-NNNN.md`; extension flips status, appends to lead's inbox | `reported` |
| wake | lead | extension turns the inbox line into a new turn | – |
| review | lead | `check`, `report`, `verify`, `diff`; then commit or `send` a fix-list | – |
| `send` | lead | follow-up packet lands in the running tab's inbox — refused unless the session is `reported` or `idle` (`closed` / `dead`: relaunched instead; see [Sending to a session that is still working](#sending-to-a-session-that-is-still-working)) | `busy` |
| `send <sid> <packet> --when-idle` | lead | the packet is queued (not numbered, not in the inbox) while the session works; `pilead queue <sid> --deliver` delivers the oldest one after the report (see [Queueing a packet for a busy executor](#queueing-a-packet-for-a-busy-executor)) | unchanged; `busy` once delivered |
| idle | executor | waiting for the next packet | `idle` |
| `close` | lead | closes the tab, keeps the files | `closed` |
| `resume <sid>` | lead | reopens a closed or dead executor — or a crashed lead — in a new tab on the SAME pi session: its conversation and the worktree's staged work are where they were | `busy` (`reported` when its report exists) |
| `restart <sid>` | lead | runs an executor's current packet again as a NEW conversation, in a successor session `<sid>-r<N>`; the old one is closed and superseded by it | successor `busy`; old shown `superseded` |
| `close --supersede BY` | lead | as `close`, and records what took over its work (`BY`: a successor's sid, or a word such as `successor-seed`); `send` refuses it from then on | shown `superseded` (stored `closed`) |
| `close --keep-tab` | lead | marks it closed but signals no process and closes no tab; the tab is retitled `[closed] <sid>`. A `send` while its process still runs goes through the inbox, with no relaunch, and retitles the tab back | `closed` |
| `retire <sid>` | lead | writes the session's successor seed, then closes it as `close --supersede successor-seed` does | shown `superseded` (stored `closed`) |
| `spawn … --seed <sid or path>` | lead | as `spawn`, with a retired session's seed appended to the packet as inherited context | `busy` |
| `send <sid> <packet> --rotate` / `--upgrade` | lead | `retire`, then `spawn --seed` of a successor `<sid>-r<N>` with this packet as its packet 1 (`--upgrade`: one tier up) | successor `busy`; old shown `superseded` |
| `stop <lead_sid>` / `close --self <lead_sid>` | lead | the lead steps down: removes its record, route and usage cache, and its inbox unless a wake is pending | – |
| `keep <sid>` / `keep <sid> --off` | lead | pins (or releases) a session: `close` refuses a pinned one, `prune` never removes it and auto-close never parks it | – |
| `auto-close [--lead SID] [--dry-run]` | lead (also run by `list` and `check`) | parks the lead's finished executors: closes each one whose report the lead has seen and whose work has landed or has sat reported too long; retires it instead when it is heavy (see [Auto-close](#auto-close)) | `closed` (retired: shown `superseded`) |
| `prune` | lead | deletes the state of sessions that ended long ago, and of ghost lead records | – |

Reviewing: `pilead check <sid>` prints the report's TL;DR block; `pilead report <sid> [--packet N]` prints the whole report in a green frame. It adds one dim line naming `pilead diff <sid> --open` when the session's worktree has staged changes, and it writes nothing.

`close` removes no file, whichever option is given: a closed session's packets, reports and `meta.json` stay
under `sessions/<sid>/` until `prune` removes them. A plain `close` of a session that is still `busy` on a
packet with no report says so on stderr (`pilead: <sid> was busy on packet NNNN; no report was written`).
`close` refuses a pinned session (`pilead keep <sid> --off` releases it). Use `--supersede` when another
session (or a successor seed) has taken the work over: `list`, `check`, `status` and the board then show
`superseded`, and a `send` to it is refused, so a follow-up cannot land in the wrong session.

`resume <sid> [--effort LEVEL] [--force]` launches pi again with the session's own `--session-id`, so pi
reopens the same conversation: nothing it said or read is lost, and nothing in the worktree changes. An
executor is launched with no model and no packet (a reopened pi session keeps its model and thinking level;
`--effort` replaces the stored effort and is passed as `--thinking`). When the current packet has no report,
a nudge naming the packet and its report path is added to the executor's inbox, after anything already
waiting there, and the status becomes `busy`; when the report exists, nothing is added and the status is
`reported`. No refs baseline is written (overwriting it would hide a move made before the resume), and a
superseded session can be resumed (one more line says what superseded it; the record stays). Refused with
exit code 2, and nothing changed, unless `--force` is given: a session that is alive (its pi runs, or, with no
pid file, its tab exists) — two live copies of one conversation would work against each other; a session
whose pi is gone but whose tab is still open — a pi started by hand in that tab would be a second copy
(`pilead close <sid>` closes the tab); and a session pi has no session log for — pi would start an EMPTY
conversation under that id (`pilead restart <sid>` is the way to run its packet again). A sid that is not an
executor but a lead (`leads/<sid>.json`) is a crashed lead: it is reopened in a new tab titled
`[Lead] <project>` in its record's `cwd`, with `pilead lead-start "$PI_SESSION_ID" --project '<project>'` as its
first prompt; the launch's own files live in `leads/<sid>.launch/`, and the record gains `resumed`. A lead is
refused when it is `live` (its tab exists) or pi has no log for it in its `cwd`, unless `--force`; and always
when its `cwd` is not a directory.

`restart <sid> [--effort LEVEL] [--model MODEL] [--model-override REASON] [--name NAME] [--force]` is for an
executor whose conversation should NOT be kept: it starts over on the current packet. A pi session id is the
conversation, so a new conversation needs a new session: the successor is `--name`, else `<sid>-r<N>` (a
trailing `-r<digits>` is dropped first, and N is the smallest number from 2 that names no session, so `foo`
gives `foo-r2` and `foo-r2` gives `foo-r3`). It keeps the worktree (and its staged work), topic, scope, lead,
layout and effort (unless `--effort`), and runs the same packet text with its own footer and report path;
it loses the old conversation. The model is `--model`, else the fallback (config `executor_fallback_model`)
when the old session is `paused`, else its own model — the ceiling rule of `spawn` applies. Everything is
checked first, and a refusal (exit code 2) changes nothing: a lead's sid, a pinned session, a session that is
alive and not `stalled` (unless `--force`), a missing packet file, a `--name` that exists or is not a usable
id, a model above the ceiling without `--model-override`. Then the old session is closed as `close` closes it,
the successor is spawned, and the old one is marked superseded by it. If the successor cannot be launched, the
old session stays closed but not superseded, no successor is left behind, and the exit code is 1. A restart or rotation whose successor launch fails leaves `spawned` and
`launch_options` ledger lines for a directory that was removed; a tab that did open keeps its pi. Neither
`resume` nor `restart` removes a file.

`retire <sid> [--force] [--keep-tab]` makes replacing a heavy or stuck executor cheap. It writes the
session's successor seed, `sessions/<sid>/successor-seed.md`, and then closes the session through `close`'s
own code as superseded by `successor-seed` (`--keep-tab` as for `close`); stdout is `close`'s line, then
where the seed is and the `pilead spawn … --seed <sid>` command that starts its successor. The seed is an
INDEX, built only from files already on disk: the territory (worktree, topic, scope, model, effort, context
window, how many packets were reported, where the files are; a value that is not recorded reads
`(unrecorded)`, and a session that ran heavy gets one hint line), then one entry per packet — its first line,
and its report's outcome, `Status:`, `Risk flags:` and `UNVERIFIED:`, or `NO REPORT` for a packet that was
never reported — and a closing "inherit with care". It is not a transcript and not instructions: every line
in it is the retired session's own account of its own work, and a successor verifies what it relies on.
Refused with exit code 2, and nothing changed: a session already retired (the line names its seed and the
spawn command), a pinned one, and one that is `busy` with no report for its current packet unless `--force`
(that packet is then seeded as `NO REPORT`). `retire` removes no file.

`spawn … --seed PATH_OR_SID` hands a seed to a new session: a readable file at that path, else
`sessions/<value>/successor-seed.md` (a file wins over a session of the same name); neither is exit code 2,
decided before anything is written. The packet the executor reads is the lead's text, then an
`INHERITED CONTEXT` section holding the seed's path and text, then the usual footer, last. `meta.json` records
`seed`; stdout gains `seed=<path>` directly before `placement=`.

`send <sid> <packet> --rotate` does both in one step: it retires the session and spawns its successor
(`--name`, else `<sid>-r<N>` as for `restart`) on the same worktree, topic, scope, lead, layout and effort, with
the seed inherited and THIS packet as the successor's packet 1; the last stdout line is
`successor: <sid>`. The model is `--model`, else the fallback (config `executor_fallback_model`) when the
session is `paused`, else its own. `--upgrade` is the same one tier up (haiku → sonnet → opus → fable, a tier
whose alias matches no model skipped); it is refused for a model with no tier (use `--rotate --model`), at the
top tier, and when no tier above resolves. The ceiling rule of `spawn` applies (`--model-override`). The
heaviness gate does not apply: rotating is the way out of a heavy session. Everything is checked first, and a
refusal changes nothing — including retire's own refusals; a busy session with no report is refused naming
`pilead retire <sid> --force` and then `pilead spawn … --seed <sid>`. If the successor cannot be launched the
session stays retired with its seed, no successor is left behind, the exit code is 1, and the line names the
spawn command to run by hand.

A plain `send` that makes the packet number 3 or more prints one note on stderr (`this is packet <n> into a
<tier> session — if the last two were fixes for the same work and it still is not done, stop sending fixes:
pilead send <sid> <packet> --upgrade …`; for a model with no tier it names `--rotate --model <model>`). Its
stdout and exit code do not change.

`stop` (or `close --self`) takes the lead's id, refuses an id that is not a usable lead id before it reads
anything, and prints each file it removed and each it kept (with `leads/<sid>.launch/`, the directory a
`resume` of the lead launched from), then `lead <sid> stepped down — wakes are off`.
A pending wake line (in `leads/<sid>.inbox.md` or a `leads/<sid>.inbox.md.claim-*` file) keeps the inbox and
its claim files. It signals no process and touches no tab and no executor's files; when the lead still has
executors that are not closed or dead, it names them on stderr, since their reports will wake nobody until a
lead registers under that id again. The edit gate of the running pi session stays on until that session
ends: `stop` removes the registration, not the gate. The lead's plan (`plans/<sid>.json`, see [Plan](#plan))
is removed too and named like the other files when it has no open item; a plan with open items
(`kept <path> — <n> open item(s)`) or one that cannot be read (`kept <path> — cannot be read`) is kept, and a
lead with no plan file prints nothing about one. `stop` reads the stored items only: it binds nothing and
writes no plan.

`prune [--days N] [--dry-run] [--lead SID] [--all]` removes a session's directory only when all of these
hold: it is in scope (`--lead SID`'s sessions; with no option, the calling lead's — `$PI_LEAD_SID`, else
`$PI_SESSION_ID`, when a lead record exists for it; `--all` for every session); its STORED status is `closed`
or `dead` (prune never recomputes a status, so a `busy`, `idle`, `reported`, `stalled` or `paused` session
is never removed); it is not pinned; its age — now minus the newest modification time among the files
directly inside `sessions/<sid>/` — is known and more than N days (default 7); and `sessions/<sid>` resolves
to a directory directly inside `<home>/sessions`. A lead record is removed (the same files `stop` removes)
only when it is in scope (`--lead`'s project; `--all` for every project), its LIVE value is `ghost`, it is
not the calling lead, its last activity is known and older than N days, no wake line is pending, and its plan
has no open item or does not exist (the plan file is removed with the record, and named under it as
`removed plan <path>`; with `--dry-run`, `would remove plan <path>`); its
files are named by the record file's own name, never by the `sid` inside it. Everything else is listed as
kept, with the reason (`pinned`, `newer than N days`, `age unknown`, `outside home`, `pending wake lines`,
`plan has open items`, `plan cannot be read`, `unusable id`, `unreadable`). With no `--lead`, no calling lead and no `--all`, prune exits 2.

What `prune` never removes or changes: anything outside `<home>`; pi's own session logs (under `~/.pi/`);
`ledger.jsonl` (except for the `pruned` / `lead_pruned` events it appends), `config.json` and `models.json`;
any session or lead it does not remove, which is left byte-identical. `--dry-run` writes nothing at all —
no status, no usage cache, no ledger line — and prints what a real run would remove.

A lead id names files under `leads/`, so `lead-start` accepts it in one form only: ASCII letters, digits,
`.`, `_` and `-`, first and last a letter or digit, no `..`, not ending in `.usage`, at most 128 characters.
The ids pi makes by itself always have that form; an id given to pi with `--session-id` may not. An id in
any other form (an empty `$PI_SESSION_ID`, `../x`) is refused with exit code 2 and nothing is written; `spawn --lead` refuses it the same way, and `status` looks
up no lead record for it. A file that an older build wrote outside home for such an id is not removed by
pi-lead; remove it by hand.

An executor's session id names its directory, `sessions/<sid>/`, and is also pi's own `--session-id`, so it
has one form too: the lead id's, except that it may end in `.usage` (letters, digits, `.`, `_` and `-`, first
and last a letter or digit, no `..`, at most 128 characters). Every id `spawn` makes has that form (`spawn`
refuses a topic that would not give one, and keeps its ids to 120 characters), and `send`, `close`, `check`,
`verify`, `diff` and `refs accept` refuse an id in any other form with exit code 2 and write nothing. A
session's id is the name of its directory: the `sid` field inside `meta.json` never builds a path, and a
directory under `sessions/` whose name is not a usable id shows as broken and is never written to.

State lives under `$PI_LEAD_HOME` (default `~/.pi-lead/`): `leads/`, `sessions/<sid>/`
(`meta.json`, `inbox.md`, `packet-NNNN.md`, `report-NNNN.md`, `refs-NNNN.json`, `usage.json`, `status`,
`escalation.json` once a report was escalated,
`successor-seed.md` once retired, `queue.json` and `queue/` once a packet was queued),
`usage/leads/<sid>.json` (a lead's usage cache), `wake/<sid>/health.json` (a lead's wake health, written by
the extension; see [`pilead list`](#pilead-list)), `wake/<sid>/notified/<executor>.<word>` (the empty files that
make a banner announced once; see [Notifications](#notifications)), `wake/<sid>/head` and `wake/<sid>/handoff-nudged`
(see [When the lead's turn ends](#when-the-leads-turn-ends)), `models.json`,
`config.json` (see [Configuration](#configuration)) and `ledger.jsonl` (see [Ledger](#ledger)).

### Queueing a packet for a busy executor

A plain `send` to a session that has not finished its current packet (`busy`, `stalled` or `paused`)
is refused (see [Sending to a session that is still working](#sending-to-a-session-that-is-still-working)):
a send raises the packet number at once, so the report the executor is writing would no longer be the
report of the current packet. `--when-idle` is the way to hand such a session its next packet now:

```
pilead send <sid> <packet> --when-idle
```

- When the session has not finished its current packet, or packets are already queued for it, the
  packet is **queued**: its text is copied to `sessions/<sid>/queue/NNNN-queued.md` as it is at this
  moment (later edits to your file do not change it) and listed in `sessions/<sid>/queue.json`. Nothing
  else changes: no packet number, no footer, no refs baseline, nothing in the inbox, no status change.
  It prints `queued <sid> #<id> — delivers when the session has finished its current packet (<n> queued)`.
  The heaviness gate is not asked now; a `--heavy-override` reason is kept with the packet and used at
  delivery.
- Otherwise (the queue is empty and the session is `reported`, `idle`, `closed` or `dead`) it is a
  plain `send`, output and all.

Queued packets are delivered **oldest first, one per finished packet**: a packet is never sent ahead of
one queued before it, and after a delivery the session is `busy` again, so the next one waits for the
next report. What delivers: first the executor's own session — after each settle, when
`sessions/<sid>/queue.json` exists, the extension runs `pilead queue <sid> --deliver --trigger settle`
itself (no lead involved, no polling; it does not wait for it, and the packet arrives through the inbox
like any other); then `pilead check` (one item per session it checks, before printing that session's
lines); then `pilead queue <sid> --deliver` by hand. A delivery interrupted after its packet was
numbered is recorded (`packet_sent` with `via: "recovered"`), never sent a second time.

**Limits.** A death after the packet is numbered but before `inbox.md` is written counts as delivered, and the verb prints a
`pilead check` hint (a drained inbox cannot be told from an unwritten one). The small window that remains in `queue move` (a
`--rotate`, `--upgrade` or `restart`) can deliver an item twice, never lose it. `pilead queue <sid> --json` together with
`--cancel` or `--deliver` is refused (exit 2).
Only when that packet holds the queued text: one another send numbered meanwhile leaves the queued packet to be delivered; a queue move that died midway, run again, moves each item once (`moved_from`).
A delivery is a plain `send` of the queued text: the packet is numbered then, and gets its footer and
refs baseline then; its `packet_sent` event names the file you queued and the queue id. A delivery that
is refused (the session became heavy, was superseded, …) keeps the packet at the head of the queue with
the reason, shown by `pilead queue <sid>` as `last delivery attempt failed: …`; `check` never opens a
tab for a `closed` or `dead` session (nor does `--deliver --trigger settle`), while `pilead queue <sid>
--deliver` (trigger `manual`) relaunches it as a plain `send` does.

Queue only a packet that does not depend on your review of the one before it: it is delivered after the
report is written, whether or not you have read that report.

`check`, `list` and `status` all show what is queued, right from what's already on disk — none of them
write to a queue except `check`'s own delivery above:
- `check <sid>` prints, right after the status line: `queued: <n> waiting — pilead queue <sid>` when
  something is still queued; `queue: cannot be read (<path>)` for a queue file that cannot be read; and
  `queue: #<id> could not be delivered — <last_error>` when the head keeps failing. `check --json`
  objects gain `queued` (the count, `null` when unreadable) and `queue_error` (the head's `last_error`,
  the unreadable text, or `null`).
- `list` adds, after its other footnotes, at most one line per session, the first of: `queue
  unreadable: <sid> — …` (the file cannot be read — nothing is delivered until it is repaired or
  removed), `queue stuck: <sid> — … the session is heavy — pilead send <sid> <body_path> --rotate
  carries the head to a successor`, `queue failing: <sid> — … could not be delivered — <last_error>`,
  or `queued: <sid> — <n> waiting for the session to finish its current packet — pilead queue <sid>`.
  `list --json` executor rows gain `queued` (the count, `null` when unreadable).
- `status <sid>` gains the segment `queued <n>` right after the packet segment (`queued ?` when the
  queue cannot be read); nothing when the queue is empty or missing. It stays read-only.

`pilead queue <sid>` lists what is queued (`--json` for a script); `pilead queue <sid> --cancel <id>`
or `--cancel all` removes queued packets (their text stays on disk under `queue/`). A packet being
delivered at that moment is not cancelled. Queue ids are never reused within a session. A `queue.json`
that cannot be read is never shown as empty and never changed: `pilead queue` says so and exits 1, and
`send --when-idle` refuses with exit code 2 and names the file.

**What happens to a queue when a session ends.**
- `send --rotate` / `--upgrade` and `restart`: the queue moves to the successor once it has launched,
  oldest first, with new ids (`moved <n> queued packet(s) from <sid> to <successor>`); a queued packet
  that is the one being sent as the successor's packet 1 is dropped, not queued twice. A queue that cannot
  be read, or whose head is being delivered, refuses the verb before anything changes; a successor that
  fails to launch leaves the queue where it was.
- `close`: the queue stays; one stderr line says none will be delivered while the session is closed
  (`pilead queue <sid> --deliver` reopens it, `--cancel all` empties it).
- `retire`: the queue stays; one stderr line says a retired session receives no packet and names where
  the bodies are, to send to the successor.
- `prune`: never removes a session whose queue has items (`kept <sid>: queued packets`) or cannot be read
  (`kept <sid>: queue unreadable`).
- Auto-close: a waiting queue keeps a session open, and a queue that cannot be read is never parked; a
  queue whose head is stuck on heaviness does not keep it open, and is emptied when the session is
  parked (one `queue_cancelled_on_park` per packet, bodies kept, one line after the park line).

A reviewed packet parks its executor: `pilead verify <sid> --diff-reviewed` closes the session right
after `report_reviewed` (`auto_closed: reviewed`) — its staged work stays in the worktree, and `pilead
send <sid> <packet>` resumes it — unless it is pinned, already closed, dead or superseded, the packet
reviewed is not its current one, or its queue has items or cannot be read.

### Auto-close

Executors finish, report, and then sit in open tabs for hours. Closing one loses nothing — the report is
on disk, staged work stays in the worktree, and `pilead send <sid> <packet>` to a closed session reopens the
same conversation — so pi-lead parks finished executors by itself. A session is parked only when ALL of
these hold:

- its status is `reported` (never `busy`, `idle`, `stalled`, `paused`, `dead` or `closed`);
- it is not pinned (`pilead keep <sid>`), it has a lead, and nothing superseded it;
- its lead has **seen** the report: the ledger holds `report_verify` or `report_reviewed` (from
  `pilead verify`), a `usage_snapshot` at `"reported"` (from the first `pilead check` that found the
  report) or a `report_looked_at` (from `pilead check`, `diff`, `close` or `retire`) for this session and its
  current packet, written by the lead or by a caller that cannot be named (no `$PI_SESSION_ID`: a human at a
  plain terminal, a script). An executor's own `check`, `diff` or `verify` (its `$PI_SESSION_ID` is its sid —
  the packet footer tells it to run `pilead diff <own sid>`) never counts as the lead having looked: those
  records carry `caller: "self"`, and the first such look leaves one `surfaced_stamp_declined`. Records written
  before `caller` existed still count. A report nobody looked at is never parked;
- its inbox (`sessions/<sid>/inbox.md`) holds no packet waiting to be delivered;
- and one of the two reasons applies:
  - `landed` — the report is at least two minutes old and its claimed work provably reached a commit
    (claude-relay 0.5.1's `claims_landed`): each path it claims (under "What changed" / `Changed:`) is
    resolved to one tracked file (`git ls-files`) — itself, or else the ONE tracked path it is a `/`-suffix
    of, so a report that says `resume.py` means `lib/pilead/verbs/resume.py` — every resolved file is clean
    in the worktree (nothing staged, modified or untracked for it), and HEAD's commit is at least as new as
    the report (whole seconds). A claim that resolves to nothing or to more than one file, a HEAD older than
    the report, or no claim at all makes it unknown, and unknown never lands (the idle reason still
    applies). Or the report is a `Status: clean` report that says it changed nothing and the worktree is
    clean. Git is the real git (never the `git` first on `PATH`); when it cannot be run there (not a
    repository, no worktree) what it would tell is unknown;
  - `idle <n>m` — the report has been there for `auto_close_idle_minutes` (60 by default; `0` turns this
    reason off and leaves `landed` on).

To park is to close the session through `close`'s own code (the ledger's `closed` has
`reason: "auto-close (<reason>)"`), or, when the session is heavy (see [Accounting](#accounting)), to retire
it through `retire`'s own code, so its successor seed is written. `meta.json` gains
`auto_closed: "<reason>"`, and `pilead list --closed` footnotes it (`  auto-closed: <sid> (<reason>)`). A park
deletes no file.

`pilead auto-close [--lead SID] [--dry-run]` sweeps now: the lead is `--lead`, else the calling lead
(`$PI_LEAD_SID`, else `$PI_SESSION_ID`, when a lead record exists for it); with neither it exits 2. A lead
only ever parks its own executors. It prints one line per park —
`auto-closed <sid> (<reason>) — pilead send <sid> <packet> reopens it with its conversation` or
`auto-retired <sid> (<reason>) — pilead spawn <worktree> <topic> <packet> --seed <sid> starts its successor`
— or `nothing to park`. `--dry-run` prints `would auto-close …` / `would auto-retire …` and writes nothing
at all (no status, `meta.json`, ledger line, usage cache or seed). The same sweep runs by itself when a lead
runs `pilead list` or `pilead check --all` (its executors) or `pilead check <sid>` (that session, when it is
the lead's): after the command's own output, so the table shows the state the command found, with the park
lines printed last (on stderr under `--json`, so stdout stays JSON). With no calling lead these commands
sweep nothing. `pilead check <sid> --refs-only` and `pilead status` never sweep.

To keep a finished session open, pin it: `pilead keep <sid>` (`--off` releases it). To turn auto-close off,
set `"auto_close": false` in `config.json` (then `pilead auto-close` prints `auto-close is off (config
auto_close)`). To bring a parked session back, `pilead send <sid> <packet>` (or `pilead resume <sid>`)
reopens its conversation; a retired one is continued by `pilead spawn <worktree> <topic> <packet> --seed <sid>`.

## Ownership

`lead` in an executor's `sessions/<sid>/meta.json` names the lead that owns it: the lead whose inbox its
reports wake. `spawn` writes it; `pilead adopt` and a claim (below) change it. A lead that comes back under a
new session id leaves a forward note, `<home>/handoffs/<old sid>.json` (`{"from", "to", "project", "at",
"reason"}`), and the owner is found by following those notes from the id `meta.json` names while no lead
record exists for it (at most 8 moves, every id checked). A session is in one of four owner states:

| State | When |
|---|---|
| `owned` | the lead that walk ends on has a record (`leads/<sid>.json`) that reads as a JSON object |
| `orphan` | `meta.json` names a usable lead id, and neither a record nor a forward note leads to one — its reports wake nobody |
| `unowned` | `meta.json` has no `lead`, or an empty one |
| `unknown` | the answer could not be read: the lead's record exists and cannot be read, the id is not a usable lead id, a forward note cannot be read, or the notes loop or run past 8 moves — never shown as owned and never as an orphan |

`pilead list` names the orphans and the unknown owners among the sessions it lists (`⚠ orphan: …`,
`⚠ owner unknown: …`), `list --json` and `check --json` carry `orphan` (`true`, `false`, or `null` for
unknown), `pilead check` says it on stderr, and the board marks an orphan with a chip and a warning.

**`pilead adopt <sid> [<sid> …] [--force]`** (or `--from LEAD_SID` for every session that names that lead,
closed and dead ones included) makes the calling lead (`$PI_LEAD_SID`, else `$PI_SESSION_ID`, registered)
the owner. An orphan, an unowned session and a session of a lead whose LIVE is `ghost` are adopted
(`adopted <sid> from <old> (no longer a registered lead | unowned | ghost lead)`); a session of a lead whose
LIVE is `live`, `unreachable` or `broken`, or whose owner is `unknown`, is refused (`refused <sid>: owned by
… — pilead adopt <sid> --force takes it`) unless `--force`. Exit code 0 when every session was adopted or was
the caller's already, 1 when one was refused or unknown (the others are adopted anyway), 2 when the command
itself is refused (no calling lead, run from an executor, ids and `--from` both or neither, an id that is not
usable, `--from` naming the caller) — then nothing changes. An adoption writes `lead` in `meta.json`; when
the old owner has no record any more, its inbox lines for that session move to the new owner's inbox; a report
that was never announced is announced to the new owner once (`.notified` then holds `<report> ns:<mtime in
ns>`, a form the extension accepts); and the ledger gets `adopted`. Two `pilead adopt` commands at the same moment are last-writer-wins on `meta.json`; the worst case is
one duplicate wake. A ghost lead that is resumed later may get one duplicate wake for an executor that was adopted away from it
(never a lost one).

**Adoption on claim.** Sending to a session (`pilead send`, plain, `--when-idle`, `--rotate`, `--upgrade`),
resuming an executor (`pilead resume <executor>`) and restarting it (`pilead restart`) is a claim of it. With
a calling lead, once the verb's own refusals have passed, an orphan, unowned or ghost-owned session is
adopted and the verb prints the `adopted` line first; the successor of a restart or a rotation names the
caller. A session another lead owns (LIVE `live`, `unreachable`, `broken`) or whose owner is unknown is NOT
adopted: the verb says `pilead: <sid> is owned by <lead> (<project>) — its report will wake that lead, not
you; pilead adopt <sid> --force takes it` on stderr and goes on. Nothing is adopted when there is no calling
lead, when the verb refuses, or by a delivery from the queue (`pilead queue --deliver`, `check`, the
executor's settle).

**The executor finds its owner each time it reports.** The extension reads `meta.json` (and follows forward
notes) at every settle that announces a report, instead of the `PI_LEAD_LEAD` it was launched with (used only
when `meta.json` names no usable lead): a session adopted while it works wakes its new owner, and never the old
one. An owner that cannot be read wakes nobody, and the next settle tries again. `/pilead:status` in an
executor names the owner as it resolves now.

## When the lead's session changes

A lead is the pi session whose id has a record, `leads/<sid>.json`. When the human goes on in another session —
`/new` or `/fork` in the lead's tab, or a new pi after the lead's tab was closed — the role moves with
**`pilead takeover <old_lead> [--as NEW_SID] [--project NAME] [--force]`** (lib/pilead/succession.py). The new id is
`--as`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`; it need not be registered. What moves, in this order:

1. a record for the new id (the old one's `project`, `cwd` and `terminal`, `lineage_started`, `moved_from`; its tab
   handle only when the command runs in the new session), unless the new id has one, which is kept and only gains
   `lineage_started` and `moved_from`. A posture never moves: the new record is `autonomous` false and `tier`
   `auto`, as `pilead lead-start` leaves them;
2. every executor whose `meta.json` names the old lead, closed and dead ones included (`adopted`, reason
   `takeover`);
3. the plan, `plans/<old>.json` → `plans/<new>.json`, unless the new lead has a plan (then both stay, and the
   output names them);
4. a forward note, `handoffs/<old>.json` (`{"from", "to", "project", "at", "reason"}`), so whatever still names the
   old id — an executor's report, a wake put back by the old session — reaches the new lead;
5. the old record, route file, launch directory, usage cache and wake directory go (as `pilead stop` removes them);
6. the old inbox's pending wake lines, appended to the new inbox in one write (a line it holds already is not
   written twice). A claim file of the old lead — wakes its process had taken and not handed over — moves too only
   when the old lead's LIVE was `ghost` or the move is in the same tab; otherwise it is left, and named, because a
   pi still running there may deliver it;
7. ledger `lead_moved`.

Each step is safe to repeat: run again after a move, it finishes what was left (`already moved to <new>; finished
what was left`). A lead whose LIVE is `live`, `unreachable` or `broken` (or could not be read) is taken over only
with `--force` or from its own tab: a lead that may still be running would lose its executors. The output ends
with the `pilead lead-start <new> --project '<project>'` to run in the new session, which refreshes its tab and
posture; a record that came from a move keeps its project whatever `--project` says. Nothing removes a forward
note except `pilead lead-start` of that same id: a session that registers again is a lead again.

In a lead's tab (`$PI_LEAD_SID` not set, so the lead's id is pi's own):

- **`/new` and `/fork`** give the tab a new session id. The extension runs `pilead takeover <previous> --as <new>
  --force` (the previous session is the one pi itself named; 10-second limit), shows `pi-lead: lead role moved
  from <previous> to this session (<n> executor(s))` or `pi-lead: lead role NOT moved: <why>`, and drains the new
  inbox.
- After `/new` in an unsaved pi session (pi names no previous session file) nothing moves: the executors keep waking the old id,
  and `pilead takeover <old>` run by hand moves them.
- **`/resume`** to another session moves nothing: `pi-lead: the session you left (<previous>) is still the
  registered lead; pilead takeover <previous> makes this one the lead`.
- **A session that is no longer registered** takes nothing from any inbox and sends nothing; a wake it held goes
  back into its successor's inbox (or, with no forward note, where it came from), and once a note names a
  successor the session is named `[ex-Lead] <project>` with one notice.

## Handoff

A lead session grows heavy: `pilead list` flags it, and when its turn ends the lead is told once (`🔁 this lead
session is getting heavy (…). Consider handing off to a fresh lead session: write a handoff note, then run pilead
handoff <note.md>.`). The human decides; a lead never hands off by itself. The remedy is a fresh session that
continues the work from a memo.

**The memo** is a file the outgoing lead writes: what is in flight (each executor, its packet and where it
stands), what is reviewed and committed, open questions, and next steps — every queue item naming who does it
(the successor, an executor, the human). A rule the lead itself inherited as a marker — a word in square brackets
made of lower-case letters and `-`, such as `[ops-not-lead-work]` — is carried forward word for word: when the memo
the lead was started from holds a marker the new memo does not, `pilead handoff` names it on stderr (`if they still
hold, carry them forward word for word`) and goes on.

**`pilead handoff <memo.md> [--project NAME] [--model MODEL] [--effort LEVEL]`**, run by the lead (`$PI_LEAD_SID`,
else `$PI_SESSION_ID`, must name a registered lead). Everything is checked before anything changes; each refusal is
exit code 2 with one line on stderr: no calling lead (it names `pilead lead-start`); `$PI_LEAD_ROLE` is
`executor`; the memo is missing, not a file, or blank; the lead's recorded cwd is not a directory; no model; an
`--effort` that is not a thinking level. **The model** is `--model` (resolved as `pilead spawn` resolves one),
else the model of the lead's LAST answer in its own session log — so a lead that switched model mid-session hands
off the model it is on now. A model recorded at a launch (`launch_model`) is history, never a source, and pi's
default model is never used: with no `--model` and no answer in the log, the handoff is refused (claude-relay
0.5.3's rule). The first output line says which: `successor model: <model> (from --model)` or `(from this lead's
session log)`. The project is `--project`, else the lead's.

Then, in order:

1. the successor's record, `leads/<successor>.json` — a new random UUID, registered BEFORE its first turn — and its
   empty inbox: the lead's `cwd` and `terminal`, `lineage_started`, `handoff_memo` (the memo copy's path),
   `launch_model`, `predecessor` (`{"sid", "project", "iterm_handle", "terminal"}` of the outgoing lead; the handle
   is `$ITERM_SESSION_ID` when set), and the configured posture: `autonomous` false and `tier` `auto`. **No posture
   is handed on**; only the human grants another;
2. the memo copy, `handoffs/<successor>.md`: the memo's text followed by a `SUCCESSOR AFTERCARE` section (check the
   registration with `pilead lead-start`, or `pilead takeover <successor>` when its session id differs; close the
   outgoing lead's tab only after the human has said yes; how many executors are its own now; review every report
   with the pilead-review skill; the outgoing posture, which is not handed on; the pinned sessions it inherits). The
   memo itself is never changed;
3. the successor's tab, opened next to the lead's with the launch `pilead resume` of a lead uses
   (`PI_LEAD_HOME=<home> exec pi --session-id <successor> --model <model> [--thinking <level>] -e <extension>
   <message>`) and titled `[Lead] <project> ·<first four characters of its sid>`, so the two tabs never share a
   title. Its first prompt is one sentence: `You are the successor lead for project '<project>', registered by
   pilead handoff. Read <memo copy> and follow its SUCCESSOR AFTERCARE section first.` The record gains the new
   tab's handle;
4. the move, only now that the tab is open: `pilead takeover`'s `move_lead` gives the successor the lead's
   executors, plan and pending wake lines and leaves the forward note `handoffs/<lead>.json`; the lead's record
   goes (its claim files stay with it: its pi is still running);
5. ledger `lead_handoff`.

When the memo copy or the tab cannot be made, what steps 1-3 wrote is removed, exit code 1, and the line says this
session is still the lead. The output after the model line: the placement of the new tab, what moved, one line
naming the pinned sessions when there are any, and `handoff complete — successor <sid> (<project>) is the lead;
this session has stepped down`. The outgoing lead's tab is then renamed `[ex-Lead] <project>` by its own extension.

What the successor finds: its record, its inbox (with the pending wakes), its executors, the plan, and the memo
copy its first prompt names. What does not move: the posture, and the outgoing lead's tab, which stays open until
the human closes it. `pilead stop <successor>` removes the memo copy with the lead's other files.

**`pilead close-predecessor`**, run by the successor after the human said yes, closes the tab recorded as
`predecessor` in its record — by that handle only, never by a title, and never the caller's own tab. Refused with
exit code 1, nothing closed or signalled and `predecessor` kept: the predecessor is still a registered lead (it
names `pilead stop <sid>`); no handle is recorded; the handle names the caller's own tab (its recorded handle or
`$ITERM_SESSION_ID`; checked before any call to the terminal); the handle does not resolve (`the tab was not found;
it is not looked for by title — close it by hand`). Otherwise the `pi` on that tab's terminal line gets SIGTERM (so
iTerm asks nothing), and 0.3 s later the tab is closed by its id: `closed the outgoing lead's tab (<sid>)`, and
`predecessor` leaves the record. When the close does not take: `the tab did not close — close it by hand`, exit 1.
With no `predecessor`: `no predecessor recorded — nothing to close`. Ledger `predecessor_closed`.

## Plan

`pilead plan [--session SID] [status|add|rm|done|next|bind]` is a lead's written plan (claude-relay 0.5.4's
`relay plan`): an ordered list of packets, each with the state worked out from the ledger and the sessions.
Nothing in it sends, spawns or queues anything by itself. With no sub-verb it is `status`. The plan is the
lead's own: `--session`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`, must be a usable lead id with a lead
record (`plan: <sid> is not a registered lead — run pilead lead-start first`, exit 2); an executor
(`PI_LEAD_ROLE=executor`) can read a plan but `add`, `rm`, `done` and `bind` are refused
(`plan: an executor cannot change a lead's plan`, exit 2).

| Sub-verb | What it does | Ledger |
|---|---|---|
| `status [--json]` | the table `#  STATE  PACKET  TARGET  MODEL  NOTE` and a counts line (`  <q> queued · <f> in flight · <r> reported · <d> done`; `closed`, `superseded`, `dead` and `unknown` items count as in flight); `--json` prints `{"lead": <sid>, "items": [...]}` | – |
| `add <packet> [--target fresh\|SID] [--model M] [--note TEXT] [--before N]` | appends an item (or puts it before item N). The packet must be a file: looked for from the lead's `cwd` first (stored as given), then from the current directory (stored as an absolute path). `--target` is `fresh` (a new executor, the default) or a session that exists | `plan_add` |
| `rm N` | removes item N | `plan_rm` |
| `done N [--note TEXT]` | marks item N done by hand | `plan_done` |
| `bind N SID [--packet K]` | binds item N to a session and to its packet K (default: the session's current packet) | `plan_bind` |
| `next` | prints the command for the first `queued` item and nothing else: `pilead spawn <lead's cwd> <topic> <packet> [--model M] --lead <sid>` for a `fresh` target (the topic is the packet's file name without `-packet.md`), `pilead send <target> <packet>` for a session, every part quoted for a shell. It sends nothing: the lead or the human runs the command. With no queued item: exit 1, nothing printed | – |

**Binding.** An item that is not bound and not done is bound when the plan is READ (`status`, `next`,
`list --plan`): the earliest `spawned` (packet 1) or `packet_sent` ledger event at or after the item's `added`
time, on a session whose `meta.json` names this lead, whose `source` is the item's packet file (a relative
packet is taken from the lead's `cwd`), and that no other item holds, gives the item its `executor` and
`packet_n`, and the plan is written back. A session whose `meta.json` cannot be read binds nothing. Binding is **unlocked**, as in claude-relay 0.5.4: the plan is
read, bound and written back with no lock, so two commands changing one plan at the same moment (or a `plan bind` while a
`list --plan` binds) are last-writer-wins. In practice: run plan commands one at a time, from one session.

**State.** The first that applies: `done` (the item was marked, or the ledger has `refs_accepted` for its
`(executor, packet)` — so a packet the lead commits is `done` once `pilead refs accept` runs; an `auto_closed`
event does not count); `queued` (not bound); `reported` (that packet's report file exists); `closed`,
`superseded` or `dead` (the session's shown status); `unknown` (its record or status cannot be read — never
shown as a state that was read); `in-flight`.

**Where it lives.** `<home>/plans/<lead sid>.json`, `{"version": 1, "items": [...]}`, items numbered 1..N in
order, each `{n, packet, target, model, note, executor, packet_n, added, done}`. The directory is made on the
first write; nothing is put in `leads/`. A plan that cannot be read (not JSON, not an object, `items` not a
list, an item that is not an object) is never taken for an empty one and is never written over: every verb that
would write exits 1 with `plan: <path> cannot be read — inspect that file; nothing was changed`, and the file
is byte-identical. `pilead list --plan` adds a PLAN block (the `status` table of `--lead`, else the calling
lead) after the executors and their footnotes, before any auto-close park line; `list --json` is unchanged.
`stop` and `prune` decide what to do with a plan from its stored items alone (see [Lifecycle](#lifecycle)).

## Autonomous mode

A lead has a posture. **Manual** (the default): on every routine step — sending the next packet of the
approved plan, reviewing a report, committing — the lead announces what it would do and waits for you.
**Autonomous**: on the routine steps that are clearly inside the approved plan it proceeds by default, and
announces each action together with what it would have asked. The posture changes that default and nothing
else: the lead is not allowed anything more, the gates and fences are the same, and executors never see it.

Only a human's request turns it on. The lead never turns autonomous mode on by itself; it runs
`pilead auto on` when you ask for it, and `pilead auto off` when you ask for that:

```
pilead auto on            # this lead (its sid from $PI_LEAD_SID, else $PI_SESSION_ID)
pilead auto off --session <lead sid>
pilead auto status        # one line: ON or OFF, and whether config or `pilead auto` set it; changes nothing
```

`on` and `off` each write one ledger event (`auto_mode_on` / `auto_mode_off`), also when the posture did
not change. An executor (`PI_LEAD_ROLE=executor`) cannot change a lead's posture; `status` works anywhere. That refusal reads the environment
only — an honour-level check, not a proof.
The posture lives in the lead's own record (`leads/<sid>.json`: `autonomous`, `autonomous_source`) and is on
only when that record holds the JSON value `true`; anything else, or a record that cannot be read, is off.

**The stop-list.** Under either posture the lead stops and asks for: a report with a risk flag, failing
tests, or an UNVERIFIED claim that bears on correctness; core logic, ledgers, parity or golden tests,
migrations, deploys; an action that cannot be undone or that leaves this machine (a push, a delete, a
publish, a message to someone); work that is not in the approved plan; real ambiguity the packet and the
plan do not settle. It says which one stopped it.

**The commit gate.** Committing an executor's work without asking is permitted only when
`pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <path>` prints
`AUTO-COMMIT: CLEARED`. Its conditions: (0) the refs did not move while the executor worked; (1) `verify`
says `COUNTS-MATCH`; (2) the report's TL;DR is `Status: clean`, `Risk flags: none`, `UNVERIFIED: none`;
(3) the packet was in the approved plan; (4) nothing sign-off-gated is touched — the built-in markers
(`extensions/`, `lib/pilead/state.py`, `migration`, `parity`, `golden`, `schema`, `deploy`) plus every
non-empty string in config `signoff_paths`, which only ever adds to them; (5) the diff was reviewed. (3)
and (5) are the lead's own attestations, `--in-plan` and `--diff-reviewed`: it passes them only when they
are true. Anything but `CLEARED` means stop and ask, naming the condition. The verifier clears the
automation; it never replaces the review of the diff.

**Reset and visibility.** Every `pilead lead-start` resets the posture to config `autonomous_mode`
(default `false`), whatever it was: a posture never outlives the session it was turned on in. When that
makes it on, `lead-start` prints a second line saying so. `pilead list` shows an `AUTO` column (the last
one of LEADS) and a `⚡ AUTO:` footnote naming every autonomous lead, and the wake message of an autonomous
lead carries the instruction above after its first line (`details.autonomous` is then `true`); a manual
lead's wake is unchanged.

## Lead gate

The extension's routing gate blocks a registered lead's `edit` or `write` call that creates a new file (when
`block_on_new_file` is true) or changes more than `edit_line_threshold` lines, outside the exempt paths
(`*-packet.md`, `~/.relay-tasks/**`, `~/.pi-lead/**`, home), unless `/pilead:route retain "<reason>"` opened
the grace window (`leads/<sid>.route`, younger than `grace_seconds`). It is a help against slips, not a lock.
pi-lead gates a change of MORE lines than the threshold, where claude-relay gates one of the threshold and
over: at the default 40, a 40-line change passes here.

Only a registered lead is gated: the gate asks at every call whether `leads/<sid>.json` exists (it never
reads it), so it turns on at the next call after `pilead lead-start` and off at the next call after
`pilead stop`, with no restart. A pi session that never registered is not gated, and `/pilead:route` in it
opens nothing and says so; `/pilead:status` prints `gate: on (registered)` or `gate: off (not registered)`.

A lead can also open the window without typing a slash command: `pilead route retain "<reason>" [--session SID]`
works from its own bash tool, a script or a human's shell. The lead is `--session` (an id, a project or a unique
prefix), else `$PI_LEAD_SID`, else `$PI_SESSION_ID`. The verb follows the same contract as `/pilead:route
retain`: one line in `leads/<sid>.route` with the time and the reason, the file's time set to now, the ledger
event `retained`. It prints `retain window open for <n>s — reason: <reason>`. A session whose `PI_LEAD_ROLE` is
`executor` or `reviewer` is refused (`route: an executor cannot open a lead's gate`, exit 2): an executor cannot
change what gates its lead. That refusal reads the environment (`PI_LEAD_ROLE`) only — an honour-level check, not a proof: with
`--session`, any other shell can open a registered lead's window. A lead that is not registered, or an empty reason, is refused and nothing is written.

Four files under home are never exempt, and an `edit` or `write` of one is blocked at any size while the
grace window is closed: `<home>/config.json` (settings — the gate's own threshold is in it),
`<home>/ledger.jsonl` (ledger — the record of what the gate did), `<home>/leads/*.route` (grace file — its
age opens the gate) and `<home>/leads/*.json` (lead record — its existence turns the gate on). Otherwise a
lead could raise its own threshold, open its own grace window or unregister itself with one small write that
nothing records; a change to one of them needs a human's word or an open grace window. Its `blocked` event
carries `control` (`"settings"`, `"ledger"`, `"grace file"` or `"lead record"`); a name cased otherwise
(`CONFIG.JSON`) and a path through `~` or a symlinked home count too. The edit gate and the bash write gate
read one table for this (`tests/control-files.json`).

### Writes made through bash

The routing gate does not see a file written through bash. `lib/pilead/bashgate.py` closes that gap the way
claude-relay does: it reads the TEXT of a lead's bash command — it never runs it and never starts a shell —
for the shapes that provably write a file (the parser is claude-relay's `bash_writes.py`, vendored unchanged):

- redirects: `>`, `>>`, `>|`, `&>`, `&>>`, with the line count of a heredoc that feeds them;
- `tee [-a] FILE…`, also at the end of a pipe a heredoc feeds;
- `sed -i` (GNU and BSD spellings);
- the destination of `cp` and `mv` (one file per source when the destination is a directory);
- a Python body (`python - <<EOF`, `python -c`) that opens a literal path for writing (`open(…, "w"|"a"|"x")`,
  `Path(…).write_text`).

A target is a **hit** when it is a control file, or when all of these hold: it is inside the session's cwd
(after symlinks are resolved); it is not a `*-packet.md`, not under `~/.relay-tasks`, `~/.pi-lead` or home, and
no part of its path under cwd is `_staging`; git tracks it, or it is new and git tracks a file in its
directory; and it is new (with `block_on_new_file`) or its line count is known and MORE than
`edit_line_threshold`. A write of unknown size (`echo x > f`, `sed -i`, `cp`) into a file that exists is never
a hit, unless it is a control file. The four control files are `<home>/config.json` (settings),
`<home>/ledger.jsonl` (ledger), `<home>/leads/*.route` (grace file) and `<home>/leads/*.json` (a lead's
record: removing it unregisters the lead, which turns its gate off): a hit at any size, from any cwd, however
the path is spelled (relative, `..`, `~`, a symlinked home, a symlink or hard link to the file) and however
its name is cased (`CONFIG.JSON`, `Ledger.jsonl` and `LEADS/<sid>.ROUTE` are control files too, on every
disk). The home directory itself and `<home>/leads` are judged the same way.

For the control files, home and `leads/` the gate does not rely on the parser's shapes. It looks at:

- the parser's targets, as for any other path;
- every word of the command (split as the shell would, quotes removed; the part after a word's first `=` too,
  as in `of=<path>`; a relative word also from the directory a literal `cd`/`pushd` earlier in the command
  went to): a command in which a word is a control file, home or `<home>/leads` is judged as a write to it,
  whatever the command is — `touch`, `rm -rf`, `truncate`, `install`, `ln -sf`, `mv`, `find … -delete`
  alike (a `cd` or `pushd` is a plain read for this check alone: `cd ~/.pi-lead && ls` passes);
- the raw text of every part it cannot read literally — a part with a word holding `$`, a backquote, `*`, `?`
  or `[`; whose command word is `eval`, `source`, `.`, `bash`, `sh`, `zsh`, `dash`, `node`, `python`,
  `python3`, `perl`, `ruby`, `xargs`, `env`, `awk`, `gawk`, `mawk` or `sed`; that has a here-document; or that
  comes after a `cd` or `pushd`. When that text names a control file (`config.json`, `ledger.jsonl`, `.route` or `leads/`, in any
  case) together with a home marker (the home's path, as typed or resolved, `.pi-lead` or `PI_LEAD_HOME`; an
  earlier `cd` to such a place or to a path the gate cannot read counts as one), the part is judged as a write
  to that control file. So `echo x > $HOME/.pi-lead/config.json`, `touch ~/.pi-lead/leads/*.route`,
  `bash -c '…'`, `eval '…'`, `node -e …`, `awk 'BEGIN{print "x" > "<home>/config.json"}'`, `bash <<EOF` and
  `cd ~/.pi-lead && echo x > config.json` are all refused (the `cd` target is a home marker for the parts
  after it).

The one exception is a plain read with no redirect onto the file: `cat`, `head`, `tail`, `less`, `more`, `wc`,
`grep`, `ls`, `stat`, `file`, `jq`, `diff`, `cmp`, `test`, `[`, `echo`, `realpath`, `readlink`, `basename`,
`dirname`, and a `sed` whose script writes nothing (no `w`/`W` or `e` command or flag, no `-i`/`--in-place`,
no `-f` script file: `sed -n 1,5p <home>/config.json` reads) — not one holding a backquote; for a plain read
the gate cannot read literally, only the text of its redirects is looked at. A pipeline or a list is judged part by part: `cat <home>/config.json | jq .`,
`ls ~/.pi-lead` and `grep x $HOME/.pi-lead/ledger.jsonl` read; `cat a && touch <home>/leads/s.route` does not.
A control file the parser found as a target is refused with the text below (`writes <path>`); one found among
the words or by the raw text with `pilead: this command names <path>, which controls the lead gate, and the
gate cannot confirm it only reads it — use a plain read (cat, head, grep, jq, …) or leave the change to the
human`.

`bash_write_gate` picks the mode; any value other than these three reads as `log`:

| Mode | a control file | another hit | no hit, a verb rule matches | nothing |
|---|---|---|---|---|
| `deny` | block | block | log | allow |
| `log` (default) | block | log | log | allow |
| `off` | allow | allow | log | allow |

The verb rules are claude-relay's: a custody verb (`git commit/push`, `systemctl restart/status`,
`clickhouse-client`, a test suite) is never logged; an implementation verb (`npm install`, `npm run build`,
`pip install`, a compiler or `make`, `git clone`, a service file, `sed -i`, a heredoc, `tee`, `rsync`) is
logged as `would_have_blocked` when `bash_gate_logging` is true. Logging never blocks. With the grace window
open no target is looked for, so only the verb log applies.

`pilead gate [--session SID] [--cwd DIR] [--json]` says what the gate makes of the command on stdin: `allow`,
`log: <rule>` (with ` — <path> (<size>)` for a hit), or `block: <reason>`; `--json` prints the whole decision.
A command the gate could not read — an unterminated quote, a NUL, anything else that makes the parser or the
word split give up — gets a second line, `parsed: no` (`"parsed": false` in `--json`, and `parsed: false` in
the ledger event `--record` writes for it; when such a command is allowed, `--record` writes `gate_unread`,
so every unread command leaves a trace): the check could not be made, so its `allow` is not a pass. Such a
command is judged as the parser left it (no targets; the verb log still applies), except that its whole raw
text goes through the raw-text rule above.
The session is `--session`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`; a session that is not a registered
lead is `allow`. It writes nothing; `--record` (for the extension) appends the ledger event.

```
echo 'cat > lib/new.py <<EOF
print(1)
EOF' | pilead gate --session "$PI_LEAD_SID"
```

Limits, in plain words: for paths other than the control files, home and `leads/`, a path with a variable or
a glob (`> $OUT`, `sed -i … *.py`), `bash -c '…'`, `dd`, `perl -i`, `touch`, `truncate`, `install`, `ln -sf`,
a script that writes a file, and git's own writes are not seen; a shape the parser does not know always
passes. For the control files, home and `leads/` every word and the raw text of every part not read literally
are looked at (above), so those shapes are seen there — except a spelling whose text names neither a control
file nor a home marker: a glob that spells no name (`~/.pi-lead/conf*.json`), a variable that holds the whole
path (`> $CFG`), a glued option (`-o<path>`), text built at run time, a script file run by path
(`./fix.sh`), or a parent of home (`rm -rf ~`).

More shapes the bash gate does not see, by design: `perl -i`, `node -e`, `ruby -e`, `dd of=`, `install`, `truncate`,
`ln -sf`, `curl -o`, `patch`, `git apply`, `git checkout --`, `npx prettier --write`; a `cp` or redirect of unknown size; and a
control path built from variables (`H=~/.pi; rm ${H}-lead/config.json`). Two more limits: the gate covers only a **registered**
lead, so an unregistered pi may edit home's control files; and a nested `pi -p "…"` started from a lead's bash is **not gated** —
it has its own, unregistered session id, so it passes both gates, exactly as claude-relay's `claude -p` does. Closing that would
take a nested-agent rule in `bashgate.py`; it is the human's to decide.

**In a live lead.** The extension puts a registered lead's bash commands to the gate (the executor's bash
goes to its git fence instead, and a command the human types with `!` is never put to it):

| # | Check | Result |
|---|---|---|
| 1 | the session has no usable id, or is not a registered lead | the command runs; no child |
| 2 | the command has no hint | the command runs; no child |
| 3 | the gate answers `block` with a reason | the command is refused with that reason |
| 4 | anything else: `allow`, `log`, an answer that is not one JSON object, a child that failed, timed out or could not be started | the command runs |

The hint is a cheap test of the command's text (`BASH_GATE_HINT` in `extensions/pi-lead.ts`): a `>` or `<<`,
a program whose writes the gate reads (`tee`, `sed`, `gsed`, `cp`, `mv`, `python…`, `awk`), a word of the verb
log (`npm`, `yarn`, `pip`, `make`, a compiler, `git clone`, `systemctl`, `rsync`, …), or a control file's name
or a home marker (`config.json`, `ledger.jsonl`, `.route`, `leads`, `.pi-lead`, `PI_LEAD_HOME`, in any case);
a command that names home's own path, or that runs with its cwd inside home, is asked too. A command without
a hint is never put to the gate: `ls`, `git status`, `pilead list` start at once, with no child process. The
child is `pilead gate --session <sid> --cwd <cwd> --record --json --home <home>`, with the command on its stdin
(never on a command line; no shell), and it writes the ledger event itself. It has 5 seconds
(`BASH_GATE_TIMEOUT_MS`). A gate that is slow, missing or gives an answer the extension cannot read lets the
command run — row 4 is "not checked", not a pass, and the lead sees one line: `pilead: the bash gate could not
check this command (timed out | failed | unreadable answer | not read)` (`not read` is an answer with
`"parsed": false`). `/pilead:status` names the bash gate in its last line.

## Fence

An executor cannot commit, push or move a ref. Two layers enforce one policy: an allowlist of
read/stage subcommands (`add`, `status`, `diff`, `log`, `stash`, `checkout -b`, …) minus their
ref-moving forms (`branch -D`, `checkout -B`, `reset --hard`, `stash clear`, …); unknown subcommands
and aliases are refused. Both refuse with `pi-lead executor: stage your work and report; the lead commits.`

1. **Text fence** (`extensions/bash-fence.ts`) parses every bash command the agent runs: compounds,
   subshells, `sh -c`/`eval` strings, heredocs, wrappers (`env`, `xargs`, `find -exec`),
   command-running env vars (`GIT_PAGER=…`), `git -c alias.*`, writes into `.git/`. It also refuses
   what would step around layer 2: git named by path (`/usr/bin/git`, `./git`, `xcrun git`,
   `$(which git)`, and an unquoted path to git in any word, so behind any wrapper:
   `arch -arm64 /usr/bin/git`; an argument of a plain file tool such as `cd`, `ls`, `cat` or `grep`
   is data, so a project folder named `git` or `git-…` works), a shell that resets `PATH` (`bash -l`, `zsh -lc`, `exec -l`, `su -`,
   `login`, `command -p`), `env` options that clear or replace the environment (`-i`, `-`, `-u PATH`,
   `-P`, alone, bundled or inside `-S`), `exec -c`, and any command that names `PATH` or
   `PILEAD_SHIM_DIR` where the shell would change its value, type, scope or export state. That is the
   left side of an assignment, an argument to a builtin that declares or sets variables (`declare`,
   `local`, `export`, `unset`, `read`, `printf -v`, …, including a quoted `declare -a 'a=(…)'`), an
   assigning operator in any arithmetic context (`let`, `(( ))`, `$(( ))`, a subscript, `[[ -eq ]]`),
   an assigning `${PATH:=…}`, a `NAME=value` argument after `set -k`, or `exec {PATH}>f`, also behind
   `builtin`, `command` or `eval`. Reading the variable is allowed (`echo "$PATH"`, `declare -p PATH`,
   a bare `export PATH`), and so is its name as quoted text given to an ordinary program. It also
   refuses a command that removes, writes, renames, links over or changes the mode of anything under
   `$PILEAD_SHIM_DIR` (`rm`, `mv`, `ln`, `cp`, `chmod`, `>`, `tee`, `find -delete`, …); `ls` and
   `cat` there are fine.

   zsh adds rules of its own. There `path` is `PATH` as an array, so `path=…`, `local path`,
   `for path in …` and `read path` are refused (rename the variable), and so are `repeat N …`,
   `foreach PATH (…)`, `emulate … -c '…'`, `set -A`, `noglob`/`nocorrect`/`-` prefixes, `print -v`
   and `integer`. The fence applies them when pi's `shellPath` setting (`~/.pi/agent/settings.json`,
   or the project's `.pi/settings.json`) names zsh, and to the text a command hands to `zsh -c` or
   `zsh <<EOF`. pi's default shell is `/bin/bash`, where `path` is an ordinary variable name.

   The text fence runs in two passes. The first is the git check as committed before the `PATH` rules
   were added, and it is never changed to make room for them. The second (`extensions/shim-fence.ts`)
   holds the `PATH`/`PILEAD_SHIM_DIR`, shim-file, git-by-path, login-shell, `env`-option and zsh
   rules, with its own parser, and it can only refuse. Where it cannot tell how the shell will read
   the text (an unclosed quote or `[`, a name spelled with quotes or `$x`), it refuses. A command
   runs only when both passes allow it; when the first pass refuses, its reason is the one shown.
2. **git shim** (`lib/pilead/shim.py`). `pilead spawn` writes `sessions/<sid>/shim/git`, a POSIX `sh`
   script, and puts its directory first on the executor's `PATH` before `exec pi`. Any program that
   runs `git` by name lands in it: a python `subprocess`, node, `make`, a script file. The shim checks
   the real argv against the same policy, then either `exec`s the real git (the next `git` on `PATH`)
   untouched, or prints the refusal and exits 77. It also refuses git options and env vars that make
   git run code: risky `-c` keys, `--exec-path=<dir>`, `GIT_CONFIG_*`, `GIT_EXEC_PATH`, or a
   `GIT_PAGER`/`GIT_EDITOR`/`GIT_SSH_COMMAND`/… whose command is git (`git …`, `/usr/bin/git …`; a
   `…/git/bin/ssh` path is fine). Those must be refused because git puts its own directory first
   on `PATH` for everything it spawns, so a nested `git commit` would not pass through the shim.

The shim guards one repository: the one the executor's worktree belongs to (for a `verify --rerun`,
the one the verified worktree belongs to), resolved when the shim is written and recorded in it. The
policy applies to that repository and every worktree of it (a worktree shares its common git
directory). A call on another repository is passed to the real git untouched, so a test suite can
build its own repositories (`git init`, `git commit`, `git tag` in a temporary directory). For each
call the shim asks the real git which repository the call's own `-C`, `--git-dir`, `--work-tree`,
`--bare`, `-c` and `--config-env` name (`git rev-parse --git-common-dir`, compared physically, so a
symlink to the guarded repository is the guarded repository). A repository the shim cannot identify —
git fails, answers nothing, or names a directory that does not exist — is treated as the guarded one.
A call whose git directory is another repository's but whose work tree lands in the guarded repository
or one of its worktrees (`--work-tree`, `$GIT_WORK_TREE`, `-c core.worktree=`, or the top level
`git rev-parse --show-toplevel` names — so `git --git-dir=<other>/.git …` run inside the guarded
repository, which makes the current directory the work tree, counts) gets the guarded repository's
policy, as does a `git worktree add` whose new worktree would land there. So does a call whose work tree
CONTAINS the guarded repository or one of its worktrees — an ancestor directory, such as a repository
whose top level is a parent of the worktree, where `git clean -ffdx` or `git reset --hard` could remove
or overwrite them — and `git init` / `git clone` into such a directory are refused; when the shim
cannot decide, it counts as containing. An unrelated sibling directory is not a container. A `git push`, `git send-pack`
or `git receive-pack` from another repository is refused when its destination lands in the guarded
repository or one of its worktrees, whatever refs it names: a local path (as git enters it, including
the `.git` suffixes, a gitfile and `~`), a `file://` URL, or a remote name whose push URLs
(`git remote get-url --push --all`, and the `remote.<name>.pushurl` / `.url` values git's config holds,
`-c` included) do — each also rewritten by any matching `url.<base>.insteadOf` / `pushInsteadOf`. A
destination that is clearly not local (`ssh://`, `git://`, `http(s)://`, `ftp(s)://`, `host:path`)
passes; one that looks local but cannot be resolved (a relative `file://` URL, `~user`, a
`<transport>::` address or an unknown scheme) is refused, and so are `--receive-pack`, `--exec` and
`remote.<name>.receivepack`, since that program decides where the push goes. `git init` and
`git clone` stay refused when the directory they would create or initialise (their last directory
argument, else the `-C` or current directory; for `clone` with no directory argument, always) is the
guarded repository, inside it, its git directory, or a directory of one of its worktrees; the same goes
for `--separate-git-dir`, `--git-dir`, `--work-tree`, `$GIT_DIR` and `$GIT_WORK_TREE`. The refused
environment and `-c` checks apply to every call, in any repository. `GIT_COMMON_DIR` is refused too:
it moves where git reads and writes refs. A call from another repository also gets the guarded
repository's policy when a path git would write there lands in the guarded repository or one of its
worktrees: `GIT_INDEX_FILE` (so `read-tree --empty` cannot empty its index, nor a `clone` overwrite it),
`GIT_OBJECT_DIRECTORY` or an entry of `GIT_ALTERNATE_OBJECT_DIRECTORIES` (so `gc --prune=now` or
`prune` cannot delete its staged blobs), `GIT_SHALLOW_FILE` / `--shallow-file`, and `GIT_GRAFT_FILE`
(which `git replace --convert-graft-file` deletes). A relative value is checked from the `-C`
directory, the top level and the git directory, since git reads it after moving to one of them; for
`init` / `clone` a relative value, and a C-quoted alternates entry, count as landing. The same goes for
`git config --file` / `-f` (any spelling git accepts) naming the guarded repository's config or a
worktree's `config.worktree`, or a config file the guarded repository reads (what
`git config --list --show-origin` names for it, `~/.gitconfig`, `$XDG_CONFIG_HOME/git/config`), and for
`git config --global` / `--system`: a read passes, and a key the policy refuses (a pager, `core.fsmonitor`,
an alias…) is refused, as in the worktree. The shim reads git's global options the way git 2.50 does:
`--attr-source` and `--shallow-file` take the next word, so `git --attr-source HEAD -C <guarded> …` is
the guarded repository's call; an option it does not know gets the policy. A call from another
repository passes only when its subcommand is a git builtin — one of git 2.50's (`git --list-cmds=builtins`),
and one of the installed git's when that git prints its own list. git runs a builtin before any alias or
`git-<name>` program of the same name, so that is what runs; any other subcommand — an alias (plain,
`!shell`, or of a builtin, such as `alias.st = status`), or an external `git-<name>` on `PATH` or in git's
exec-path, including git's own scripts such as `git submodule` and add-ons such as `git lfs` — gets the
policy, which refuses it. An alias's inner `git` would be the real git (git puts its exec-path first on
`PATH`), and a plain alias would skip the push, `config` and `worktree add` checks above. The cost: a test
suite that sets a harmless alias in its own temporary repository (`alias.co = checkout`, then `git co`) is
refused there, exit 77; spelling the command out passes. When the worktree's repository cannot be resolved at spawn
(git fails, or the directory is not in a repository), the shim guards every repository, as before.

Every git that git itself starts meets the shim too. git puts its exec-path first on `PATH` for every
program it runs, so a call the shim passes through for another repository (and a call with no
subcommand) runs with `GIT_EXEC_PATH` set to a mirror of git's exec-path beside the shim,
`<session>/shim/exec-path`: symlinks to git's own entries, except that its `git` — and `git-upload-pack`,
`git-receive-pack` and `git-upload-archive`, which git runs by those names — is the shim, and no other
dashed builtin is there (`git-read-tree` is simply not found). A hook, `git rebase -x`, `git bisect run`,
a clean/smudge filter, `diff.external`, `--upload-pack`, a helper, or one of git's own child processes
that runs `git -C <guarded> …` therefore reaches the shim and gets the guarded repository's policy (exit
77); one that runs git on its own repository passes, still mirrored. A call that gets the policy — in the
guarded repository or its worktrees — runs with git's own exec-path, as before, so `git stash`,
`git fetch` and `git worktree add` there keep working. For git's own children the shim also lets through
the remote helpers `git remote-http(s)` / `remote-ftp(s)` (so an https clone in another repository works),
dumb-http `git http-fetch` and any credential helper git starts as `git credential-<name>` (so the
keychain's `credential-osxkeychain`, which Apple's system gitconfig sets, still answers a private https
remote there; typed at the top level, `git credential-<name>` and `-c credential.helper=…` stay refused),
`upload-pack` / `upload-archive` serving the guarded repository (a clone, fetch or `archive --remote` from
it), `index-pack` / `unpack-objects` / `pack-objects` and those helpers scoped by their git directory (a
clone run from inside the worktree into another directory), and the `GIT_CONFIG_PARAMETERS` git hands its children after
`git -c …` (checked like `-c`). `git --exec-path` prints the mirror. The mirror records the real git and
its exec-path (path, inode, mtime, `--version`); a call that finds them changed rebuilds it once, and when
it cannot, gets the guarded repository's policy with a line saying why. `pilead doctor` warns about a stale
or missing mirror. The cost: each call that runs git from another repository pays the shim's checks once
more per git child (a commit whose hook runs git, a fetch's `index-pack`). Every guarded shim call counts
itself in `PILEAD_SHIM_DEPTH` before it runs the real git, and a call that finds 8 already in its chain
is refused (exit 77) with a line naming the loop, before it starts anything — a `git` earlier on `PATH`
that runs the shim again would otherwise loop for ever; the shim's own probes run at the limit, so one
that comes back to the shim ends at once. A hook → git → hook → git chain uses 2.

Limit: a program that calls git by an absolute path, or sets `PATH` or `GIT_EXEC_PATH` itself, is outside
the fence, like any non-git tool writing files. So is a hook, filter or helper installed inside the
guarded repository itself: calls there run with git's own exec-path. Commands run in another repository that
write files to an output path (`git diff --output=`, `git format-patch -o`, `git archive --output`,
`git checkout-index --prefix` and similar) are passed even when that path is inside the guarded
repository, and so is an output path named by an environment variable or a config key (the
`GIT_TRACE*` files, `http.cookieFile`): the shim is a git fence, not a filesystem sandbox. For the same
reason it does not look inside another repository's git directory: that repository's objects, index or
config made a symlink into the guarded repository is not seen, and neither is a file the guarded
repository's config would include but that does not exist yet. The shim is a guardrail against
accidents, not a sandbox; the lead's review of the staged diff (and refs detection) is the control.

The shim refuses on inherited environment too, so an executor would be stuck if the human's shell
exported one of those variables. `pilead spawn` (and a send-relaunch) therefore clears them just
before `exec pi`, in the same bootstrap step that puts the shim first on `PATH`. It always unsets
`GIT_CONFIG`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_EXEC_PATH`,
`GIT_TEMPLATE_DIR`, `GIT_CONFIG_COUNT` and `GIT_COMMON_DIR`. It unsets `GIT_PAGER`, `PAGER`, `GIT_EDITOR`, `EDITOR`,
`VISUAL`, `GIT_SEQUENCE_EDITOR`, `GIT_SSH_COMMAND`, `GIT_SSH`, `GIT_ASKPASS`, `SSH_ASKPASS`,
`GIT_EXTERNAL_DIFF`, `GIT_PROXY_COMMAND` and `PROMPT_COMMAND` only when the value runs git, so
`EDITOR=vim` stays. The executor's git therefore reads your normal `~/.gitconfig` and
`/etc/gitconfig`, not a file named by `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`. If the executor sets
one of these variables itself, the shim still refuses.

The shim reads a value as running git when `git` appears in it as a word, so a value with `git` as a
plain argument, such as `GIT_SSH_COMMAND="ssh -l git"`, is refused in-session and cleared at spawn.
A variable that the launching shell has marked read-only makes the clearing step fail, and pi does
not launch. This happens with the Terminal.app backend, whose launch line runs in your login shell,
when a startup file such as `~/.zshrc` runs `readonly GIT_CONFIG_GLOBAL=…`: remove the `readonly`
(or unset the variable) there. The iTerm backend runs the launch in a fresh `sh`, which does not
inherit the read-only mark.

What the text fence cannot see in the text (one of these that runs `git` by name still meets the
shim, unless it names git by path, changes `PATH`, or is run by git itself, as a hook is):
- a name or program computed at run time (`v=PATH; export "$v=…"`, `$(which git)` handed to a
  program the fence does not know);
- script files (`bash x.sh`, `make`, a hook written into `.git/hooks` with a non-bash tool);
- other languages (`python -c`, `node -e`, a compiled program that embeds libgit2 or writes `.git/`);
- the shim file reached by its literal path or through another variable;
- `sudo` and `ssh`, which start the program with an environment of their own.

What needs zsh or bash ≥ 4 (pi's default `/bin/bash` on macOS is 3.2):
- bash ≥ 4 forms the fence refuses from reading bash's rules alone, never tried against a bash ≥ 4:
  namerefs (`declare -n`), `{NAME}>f` fd names, `mapfile`/`readarray`, a subscript with blanks
  inside `declare`;
- zsh forms it still allows: `private PATH=…`, a name computed at run time (`${(P)v::=…}`), and any
  zsh form when `shellPath` names a zsh whose file name does not contain `zsh` (it gets the bash
  rules).

The fence stops an executor committing by habit or by accident. It is not a sandbox against an
executor that sets out to commit. The text fence still refuses `git init` and `git commit` typed in a
bash command, in any repository; a test suite whose own code builds temporary repositories runs,
because those calls reach the shim, which passes calls on other repositories through.

## Detection

The fence tries to stop a commit; detection reports one after the fact, by whatever route it was
made — a script, another language's git library, `sudo`, a name computed at run time. It detects;
it prevents nothing, and it undoes nothing: the lead and the user decide.

What is compared: a refs snapshot of the executor's worktree — `HEAD`, the current branch, every
ref with its object id (`git for-each-ref`), the stash, and the linked worktrees with their `HEAD`s.
The index and the working tree are not part of it, so staging, unstaging and editing files never
register. Every snapshot is read with the real git by absolute path (the first `git` on `PATH` that
is not an executor's shim and answers `git --version`), never through the shim.

When: `pilead spawn` and every `pilead send` (relaunch included) store the packet's baseline in
`sessions/<sid>/refs-NNNN.json` before the executor can see the packet. `pilead check <sid>`,
`pilead verify <sid>`, the board and the lead's wake message compare a fresh snapshot against it.

What the lead sees:
- the wake message ends with the one-line result — `refs: unchanged since packet NNNN`,
  `REFS MOVED: <n> change(s) since packet NNNN`, `refs: not compared (<why>)` or
  `refs: not a git repository` — from `pilead check <sid> --refs-only`, which prints only that line
  and always exits 0. The lead asks for it without blocking; if `pilead` is slow (20 s) or fails,
  the wake still arrives, with `refs: not checked (<why>)`. When the lead's session ends (quit,
  reload, a new session) while a wake is still waiting for its refs line, the fetch is stopped and
  the wake is put back in the inbox, in order, not sent; the next lead session delivers it. A wake
  that was claimed and not yet delivered is kept in a claim file beside the inbox
  (`<inbox>.claim-<pid>-<ms>-<seq>`) until it was handed to pi or put back, and every wake handed to pi
  is ledgered as `wake_delivered`. A claim file whose process is gone (killed outright), or that is 60 s
  old, is recovered by the next drain of any lead session on that inbox: its wakes go back ahead of what
  the inbox holds and `wake_recovered` is ledgered. So after a kill a wake may arrive twice; it is never
  lost. A put-back that fails is in the ledger (`wake_putback_failed`) and warned in the lead's session;
  its claim files stay on disk for that recovery. An executor's claimed packet is kept the same way, in
  a claim file beside its inbox, until pi records it as a user message of the session; after a kill it
  may arrive twice, never lost (`packet_recovered`, `packet_putback_failed`). Wake delivery is near-always-once, not exactly-once: the executor's claim
  is deleted at pi's `message_end`, before pi persists the message (`agent-session.js` around lines 579 and 595), so a kill in
  that gap can deliver a wake twice. Known limit: an entry put
  back can arrive after one written at the same moment (the order, never the entry);
- the wake says what happened and how big the change is. The same one child, `pilead check <sid> --refs-only
  --wake`, prints the refs line and then a second line, `wake: ` and a JSON object (`packet`, `diff`,
  `diff_state`, `landed`, `proven`; it writes nothing and exits 0 whatever happens), so the message becomes

  ```
  🚦 [pi-lead] executor <sid> reported: <report> — <refs line> (diff: 3 files +10/-2)
         <the first line of the report>
         its claimed files are clean at HEAD — it may have been committed already
  ```

  The first line is the report's first line that is not empty, with leading `#` and spaces removed, runs of
  white space made one space and at most 200 characters; the extension reads it only from a regular file of at
  most 1 MB that lies directly inside `<home>/sessions/<executor>/` (a link out of it, a `..`, another
  directory: nothing). The size is `git diff --cached --numstat` (`<n>` files, the sums of the two number
  columns; a binary file counts as a file and adds nothing), and is shown only when something is staged. The
  third line appears when every path the report claims is a tracked file that is clean at HEAD and HEAD is not
  older than the report, but nothing in the ledger proves it (`details.landed` is `"unproven"`; the lead should
  look, not assume). A fact that could not be read adds nothing to the message and is `null` in `details`; it is
  never shown as 0. Each line goes into `details` too (`diff`, `brief`, `landed`), and a wake whose facts were
  not read is byte-identical to the message without them (an injected `refsCheck` reads none);
- a wake is not sent for work that is provably committed already: when every claimed path is clean at HEAD
  **and** the ledger holds, for that session and packet, a `refs_accepted` event, or an `auto_commit` with
  `cleared` true, or an `auto_closed` with reason `landed` written after the session's last `packet_sent` (after
  its `spawned` for packet 1 — an `auto_closed` names no packet, so an earlier one proves nothing), the line is
  taken from the inbox as a delivered one is, no message and no banner follow, and `wake_skipped_landed` is
  ledgered (`wake_delivered` is not). Landed but not proven is only the third line above;
- three tries: a wake whose send throws is put back and tried again after the hold, at most 3 times by one lead
  runtime. At the third failed send `wake_retry_capped` is ledgered, the lead's session shows one warning
  (`pi-lead: the wake for <executor> could not be handed over after 3 tries (<error>) — it stays in the inbox;
  pilead check <executor> shows the report`), and that runtime sends the line no more: it is put back untouched
  and nothing else is written for it. The line stays in the inbox, so it is never dropped; a new lead runtime
  (a new session, a reload) starts its count at 0 and tries again;
- `pilead check` prints the size of the staged diff directly after the report's path, `diff: <n> files
  +<a>/-<d>`, or `diff: not read (<why>)` when git could not be run or did not answer in 10 seconds; nothing when
  nothing is staged, the worktree is not a repository or none is recorded. `check --json` gains `diff` (`state`
  — `staged`, `empty`, `not-a-repo`, `no-worktree` or `not-read` —, `files`, `insertions`, `deletions`, `why`),
  `null` for a session whose current packet has no report. `pilead list` puts one footnote under the executors
  for the reported ones that have a lead, whose landing is not proven and that no wake was delivered for:
  `🚦 <k> report(s) not yet handed to their lead: <sid> (packet <NNNN>) (diff: 3 files +10/-2), … — read with pilead
  check <sid>; the wake is tried again, do not wait for it.`; `list --json` gives each reported executor row `diff`
  (the size text, else `null`);
- `pilead check` prints a `REFS MOVED` block above the status line: each change (HEAD moved, branch
  changed, ref added / moved / removed, stash changed, worktree added / removed), each new commit
  with author, date and subject, and the other sessions whose worktree is in the same repository,
  whose work moves the same refs. Unchanged: one `refs: unchanged since packet NNNN` line at the end;
- `pilead verify` gives `MISMATCH` and exits non-zero; `--for-autocommit` prints
  `NOT-CLEARED-BECAUSE-refs-moved`;
- the board shows a warning for the session.

A `REFS MOVED` block stops the commit and goes to the user. The lead's own commit moves the refs
too; after committing an executor's work, make the refs as they are now the packet's baseline:

```
pilead refs accept <sid> --reason "lead committed packet 0001"
```

A comparison that could not be made is never read as "unchanged". When the worktree is a git
repository but the packet's baseline is missing, unreadable or corrupt (not JSON, or any field
missing or of the wrong type), or git fails now, the result is `refs: not compared (<why>)` —
`no baseline stored`, `baseline unreadable` or `git failed: <first line>` (an older git than 2.31
lands here). When a baseline was stored for the packet (readable or not) and the worktree is no
longer a git repository — its `.git` was removed, or the directory was deleted — the result is
`refs: not compared (repository missing)`. `check` prints that line first, `verify` makes a
`COUNTS-MATCH` verdict `INCONCLUSIVE` (a worse verdict stays as it is),
`--for-autocommit` prints `NOT-CLEARED-BECAUSE-refs-not-compared`, and the board shows a warning.
`pilead refs accept <sid>` stores a baseline where none is readable. A snapshot that fails at spawn
or send prints one stderr line and writes `refs_snapshot_failed`; the spawn or send still happens.
A worktree outside git with no baseline stored for the packet has nothing to detect: the result
is `refs: not a git repository`, `check` prints nothing about refs, and verdicts are untouched. A
closed session is not compared on the board. A moved remote-tracking ref (after a `git fetch`) is
listed as a change, but its commits are not listed as new.

## Re-running a report's tests

`pilead verify <sid> --rerun` is the one place where text an executor wrote becomes a process, so what
it runs is a fixed list. A command between backquotes in the report is re-run only when it is one of:

- `pytest …` or `python[3] -m pytest …` (the program is exactly pytest, no option that writes outside
  the run such as `--basetemp` or `-p`, no argument outside the worktree);
- exactly `npm test`;
- exactly `npm run check`;
- exactly `node --test`.

"Exactly" means the whole command, after runs of spaces are folded: nothing added, nothing in front.
Anything else is not run, and is named as `NOT RE-RUN` with the reason:

| Not run | Why |
|---|---|
| `npm test -- -k x`, `npm run check -- -k x`, `node --test tests/ts/x.test.ts`, `node --test --import tsx`, `node --test=x` | an argument or option added |
| `npm run test`, `npm run build`, `npm t`, `node test`, `NPM TEST` | not one of the three strings |
| `npm --prefix x test`, `/usr/local/bin/npm test`, `./node_modules/.bin/npm test`, `npx npm test` | something in front |
| `FOO=1 npm test`, `env npm test`, `cd x && npm test` | an assignment, a wrapper, a shell character |
| `yarn test`, `pnpm test` | another tool |

Each command runs from an argument list written in pi-lead, never through a shell, with stdin closed,
in its own process group, for at most 600 seconds (then the whole group is killed), in the worktree
recorded for the session. `npm` and `node` are looked up on `PATH` and must lie outside the worktree;
the two npm commands also need a `package.json` in the worktree, and without one they are named as
`NOT RE-RUN` (`no package.json in the worktree`) and nothing starts.

A re-run executes code the executor wrote, with the rights of whoever runs `pilead verify`. `npm test`
and `npm run check` run the scripts in the worktree's own `package.json`, and npm starts those with a
shell of its own; `node --test` runs the test files node finds in the worktree; a pytest re-run loads
the worktree's `conftest.py`. Read the staged diff before you re-run it.

What stays in force during a re-run:

- the git shim: `sessions/<sid>/shim/git` is written and put first on `PATH` (with `PILEAD_SHIM_DIR`),
  so a `git commit`, `git push` or any other refused call from inside the run is refused with exit 77;
- the executor's git environment: the variables the shim refuses on are cleared, as at launch, and
  the pi-lead / pi session variables (`PI_LEAD_*`, `PI_SESSION_ID`) are removed;
- refs detection: `verify` compares the refs after the re-run, so a commit made during it by any other
  route shows as `REFS MOVED`.

The bash fence does not apply: it reads the bash calls of a pi session, and a re-run is not one.

Counts are read for pytest (its summary line, `808 passed, 1 skipped in 12.0s`) and for node's own test
runner (`# pass 71` / `ℹ pass 71`, with `fail` and `cancelled`). `npm test` whose output holds both is
compared twice, once per runner. For node's count to be compared, the report states it as
`node: 71 passed` or `node 71/71`, and keeps a backquoted command from standing right before a pytest
count on the same line (`` `node --test`: 808 passed `` reads as node's count). A plain `12 passed` is
compared with node's count only when the run printed no pytest summary. Any other runner behind
`npm test` (jest, vitest, mocha, …) gives no count and the verdict is `INCONCLUSIVE`, never a pass.
Every count the report declares must be compared by some re-run of its own runner: a declared node count
when only pytest re-ran, or a declared pytest count when only node re-ran, makes the verdict
`INCONCLUSIVE` (`rerun-declared-not-compared`), never a match.

The git shim is on `PATH` for every re-run, pytest included, and it guards the verified worktree's
repository only, so a suite whose tests create git repositories of their own (`git init`, `git commit`
in a temporary directory outside the worktree) runs as it would anywhere else.

## Review by a second pi

`pilead review <sid>` hands the reading of an executor's staged diff to a second pi process, so the lead
does not spend its own context on it (claude-relay does this with a "fork" of the lead's session). It
starts `pi -p` — one prompt, one answer, then the process exits — on the lead's own model, and by
default with a copy of the lead's session log (`--fork`), so the reviewer knows what the lead knows. pi
only reads the lead's log to make the copy; the copy is written under
`sessions/<sid>/review-NNNN/session/`. Nothing of the lead's own files is written: not its log, its
record, its inbox or its usage cache.

```
pilead review <sid> [--packet N] [--lead SID] [--model SPEC] [--fresh] [--no-rerun]
                    [--timeout SECONDS] [--large] [--dry-run] [--json]
pilead review <sid> --why [--lead SID] [--model SPEC] [--timeout SECONDS] [--dry-run] [--json]
```

| Option | Meaning |
|---|---|
| `--packet N` | the packet to review; default the newest report, as `verify` chooses it |
| `--lead SID` | the calling lead, when `$PI_LEAD_SID` / `$PI_SESSION_ID` do not name it |
| `--model SPEC` | the reviewer's model, an alias (`dsflash`, `sonnet`, …) or `provider/id`; default the lead's own model, read from its session log. With neither, the verb refuses: no default model is ever used |
| `--fresh` | start the reviewer with no copy of the lead's context |
| `--no-rerun` | do not re-run the report's declared test commands |
| `--timeout SECONDS` | 60 to 7200, default 1800; then SIGTERM to the reviewer's process group, 5 seconds, SIGKILL |
| `--large` | allow a staged diff of more than 1,500,000 bytes |
| `--dry-run` | print the start line and the argument list; start nothing, write nothing |
| `--json` | print the result as one JSON object |

The reviewer has read-only tools only: `pi --tools read,grep,find,ls`, with no extension, no skill, no
prompt template and no context file. It has no bash, so it cannot run anything: the verb runs the
declared test commands for it, through `pilead verify <sid> --rerun` and its fixed list (see
[Re-running a report's tests](#re-running-a-reports-tests)), and gives the reviewer that output as
`verify.txt`, with the report, the packet and the full staged diff, in `sessions/<sid>/review-NNNN/`.
Its environment has no `PI_LEAD_SID`, `PI_LEAD_LEAD`, `PI_LEAD_INBOX`, `PI_SESSION_ID` or
`PI_CODING_AGENT_SESSION_DIR`, and `PI_LEAD_ROLE` is `reviewer`: it is never taken for the lead.

Before starting anything the verb refuses (one line, exit 2 or 3, nothing written) when there is no
report or worktree, the lead's model or log cannot be read, tracked files have unstaged changes, nothing
is staged, the diff is over the size limit, or the lead's live context plus a quarter of the diff's bytes
does not fit the model's window.

**The measurement.** Before the reviewer starts and after it ends, the verb measures the worktree with
the real git (never the shim, never a shell): `git write-tree`, the hash of the staged diff, `HEAD`,
`for-each-ref`, `stash list`, `worktree list --porcelain` and `status` (tracked files only). The review
file's first line is its outcome:

- `VALID` — the answer is in the fixed shape and nothing moved. Only a VALID review writes
  `report_reviewed`.
- `INVALID (<facts> changed during the review)` — something in the index, the refs or the tracked files
  moved while the reviewer ran (or could not be measured afterwards). It never counts, whatever the
  reviewer said.
- `INCOMPLETE (<why>)` — the reviewer timed out, was interrupted (exit 130), pi exited non-zero, or its
  answer is not in the fixed shape (`Verify:`, `TL;DR:`, `Tests:`, `Existing lines edited:`,
  `Findings:` with `<n>. <where> — blocker|should-fix|note — <text>` lines or `No findings.`, and
  `Recommendation: commit | fix-list | send back`).

Exit codes: 0 VALID, 1 INVALID, 3 INCOMPLETE, 130 interrupted.

The review file `sessions/<sid>/review-NNNN.md` has the verb's own lines above a `---` (outcome,
reviewer, the start and end tree, whether the refs moved, the diff's hash, verify's verdict and exit
code, whether the re-run matched, and the review's own cost) and the reviewer's answer, unchanged, below
it. The lines above the `---` are measured by the verb; the words below it are a model's reading of the
diff — useful, and wrong at times. A second review of the same packet moves the earlier file into the
work directory as `previous-<k>.md`.

**Cost.** A review costs tokens, on the lead's model. A same-model fork review on claude-relay has cost
from 120k to 360k tokens, and the figure grows with the lead's own context, because the copy of the lead's
session is sent with every request. `--fresh` leaves the lead's context out; `--model dsflash` uses a
cheap model. The model, the context size and the diff size are printed on stderr before anything starts,
and `--dry-run` prints them without starting anything. pi logged in to Anthropic bills this as extra
usage. The `Cost:` line counts only what the review itself spent (the copied entries were billed to the
lead), or says `not known`.

### The review file and the commit gate

`pilead verify <sid> --for-autocommit --in-plan --diff-reviewed --findings <file>` reads a review file
written by `pilead review` (its first line is `# Review of <sid> packet <NNNN> — pilead review`) as data:
its outcome, its end tree, its findings' levels and its recommendation, nothing else. After the five
conditions and the refs condition, it makes these checks in order; the first that fails makes condition 5
not ok with its own detail, and gives the reason unless an earlier condition already gave one:

| # | Check | `NOT-CLEARED-BECAUSE-…` |
|---|---|---|
| a | the file is readable as a review (every header line, the `---`, an answer in the fixed shape) | `review-unreadable` |
| b | its session and packet are the ones being verified | `review-is-of-another-packet` |
| c | its outcome is `VALID` | `review-not-valid` |
| d | the tree of the index now could be read (`git write-tree`, with the real git) | `review-tree-not-compared` |
| e | the review's end tree equals it | `review-is-of-another-tree` |
| f | no finding has the level `blocker` or `should-fix` | `review-has-open-findings` |
| g | its recommendation is `commit` | `review-does-not-recommend-commit` |

These checks only ever add a reason not to clear: none of them turns a condition from not ok to ok. When
all seven hold, condition 5 reads `the diff was reviewed by pilead review, the review is of the staged
tree, and the lead has read it` (detail `<file>: VALID, tree <12 characters>, <n> note(s)`), and it is
still ok only with `--diff-reviewed`: that flag is the lead's word that it has read the findings. Staging
one more file after the review makes it a review of another tree. A review file that already is
`sessions/<sid>/review-NNNN.md` is not copied onto itself. Without `--for-autocommit`, `verify` prints one
line above its block: `review: <outcome>, of tree <12 characters> — the staged tree` (or `— the staged
tree is now <12 characters>`, or `— the staged tree could not be read`). Any other file given to
`--findings` (hand-written findings) is handled exactly as before. A review whose `Review:` line says
`VALID` but whose `Hash:` line says the trees `differ` is not VALID (check c). A review file whose first
line is damaged is read as hand-written findings, so none of the seven checks runs and condition 5
falls back to the lead's own `--diff-reviewed` attestation.

### `--why`: a question about a session's state

`pilead review <sid> --why` asks a separate headless pi why a session is stalled, what it is doing, or
whether the lead's wake was sent — so the lead never reads a transcript itself. It may be combined with
`--lead`, `--model`, `--timeout` (default 600), `--dry-run` and `--json`; with `--packet`, `--fresh`,
`--no-rerun` or `--large` it is refused (exit 2). The model is chosen as for a review. The process never
gets the lead's context (no `--fork`), has the same read-only tools and environment, and is measured
before and after in the same way (a session with no worktree on disk is not measured). It reads, from
`sessions/<sid>/why-<YYYYMMDD-HHMMSS>/`, `check.json` (what `pilead check <sid> --json` would print, read
without running `check`: no auto-close sweep, no status stored, no report marked seen, no refs move
logged: apart from its own `why-*` files and its `session_diagnosed` line, `--why` changes nothing
about the session), `report.md` (the
newest report), `log-tail.jsonl` (the last 50 entries of the session's own log, each cut to 2000
characters) and `ledger.jsonl` (its last 200 ledger lines), and answers in at most eight lines. The
answer is saved unchanged as `sessions/<sid>/why-<stamp>.md` under `Outcome:`, `Reviewer:` and `Cost:`
lines: `ANSWERED` (with ` (longer than the 8 lines asked for)` when it is), `INVALID (…)` or
`INCOMPLETE (…)`; exit 0, 1 or 3. It writes `session_diagnosed`.

## Accounting

`pilead check` and `pilead send` read each executor's token usage and live context from pi's own
session log — the JSONL file pi writes for the session, found by its id (pi-lead launches every
executor with `--session-id <sid>`). pi keeps it in `<agent dir>/sessions/--<cwd>--/`, or in the one
directory `$PI_CODING_AGENT_SESSION_DIR` or a `sessionDir` setting names (`<agent dir>` is
`$PI_CODING_AGENT_DIR`, else `~/.pi/agent`). Nothing is estimated: every number comes from the usage
pi recorded for each request, and a parse is cached until the log changes — an executor's in
`sessions/<sid>/usage.json`, a lead's in `usage/leads/<sid>.json` (outside `leads/`, so a cache is never
read as a lead record; a lead id with `/`, `\`, `..` or a NUL in it is read uncached). `pilead status`
writes no cache. pi-lead never runs `pi` for this; the model's context window comes from `models.json` as it is.

- **Tokens**: `<prompt>/<output>` billed over the whole session — every usage in the log, including
  tool calls that used a model and compactions. Prompt tokens are input + cache read + cache write.
- **Live context**: what the last successful request sent, after the latest compaction — the context
  the next turn starts from. A request that errored or was aborted does not count. Right after a
  compaction it is unknown until the next request.
- **Heavy**: live context at or above `context_nudge_tokens` (150k). When the live context is unknown,
  a session log at or above `handoff_nudge_mb` (5 MB) counts as heavy instead. **Approaching heavy**:
  at or above `context_warn_tokens` (120k) and not heavy.
- **`-`** means unknown: a reading that could not be made. It is never shown as `0`.

A cache keeps no more than 120 characters of a provider's error message: the last assistant message's
`stopReason`, the first 120 characters of its `errorMessage`, and `limit` — whether `usage_limit_pattern`
matched the whole message as the log holds it (up to 1000 characters) — with the pattern it was read with.
A cache written with another pattern, or by an older build (no pattern), is parsed again, and a writing
command then replaces it.

`pilead check <sid>` prints, after the status and the TL;DR and before the refs line:

```
tokens 1.2M/34k over 57 requests · ctx 146k/1M · approaching heavy
```

`ctx` is live context / the model's window (the live figure alone when the window is unknown; a
trailing ` !` means a request was larger than that window, so the model list's window is wrong for
this session). When no session log is found, stdout is unchanged and one stderr line says
`pilead: no session log found for <sid>; tokens and context are unknown`.

`pilead send` refuses a heavy session before it changes anything (exit 2). The message gives the
reading and the threshold. It is not a verdict on the executor's work: a long session re-sends its
whole context on every turn, and a fresh one starts empty. The two ways forward:

```
pilead close <sid>
pilead spawn <worktree> <topic> <packet> --model <alias> --lead <lead>
pilead send <sid> <packet> --heavy-override "<reason>"
```

that is, close it and spawn a fresh executor for the next packet, or send anyway with a reason (written
to the ledger as `heavy_override`; an empty reason is refused). Every send to a session whose log it
can read prints `  ctx: <live>, hit-rate <n>%` (the share of prompt tokens served from the provider's
cache) before `sent …`. The gate cannot see a session whose log it cannot find: such a session is
never refused, and `send` prints what it always did — `pilead check` is where the unknown is reported.

When a log is found but cannot be read, the stderr line says
`pilead: session log for <sid> could not be read; tokens and context are unknown` instead. A pi
session log is a tree (pi branches when you navigate back and continue from an earlier point): live
context and the model come from the **active branch** — the path from the log's last entry back
through its parent links, as pi builds it — while the billed tokens count every entry in the file,
because a branch you left was still billed.

### Sending to a session that is still working

A plain `send` (neither `--rotate` nor `--upgrade`) is refused before anything is written when the
session's status (see [Status](#status)) is `busy`, `stalled` or `paused` — none of these has finished
its current packet — or when the status cannot be read at all:

| Status | Outcome of a plain send |
|---|---|
| `reported` or `idle` | sent, as always |
| `closed` or `dead` | relaunched (or, for a `close --keep-tab` session whose process still runs, delivered through the inbox), as always |
| `busy`, `stalled` or `paused` | refused: the session has not finished its current packet |
| anything else, or no status at all | refused: it is not known whether the packet is finished |

The reason: the session is still writing the report for the packet it has now, under that packet's
number; a send at this moment would raise the packet number before that report exists, so the report
lands under the wrong number and `pilead check` never announces it. There is no override for this
refusal — unlike the heaviness gate above, sending anyway is never the right answer here. Three ways
forward instead: queue the packet with `pilead send <sid> <packet> --when-idle` (see [Queueing a packet
for a busy executor](#queueing-a-packet-for-a-busy-executor)), wait for the report (`pilead check <sid>`),
or hand the packet to a successor with `pilead send <sid> <packet> --rotate`, which is unaffected by this
refusal. The refusal's line names all three.

### Status

`pilead list`, `pilead check` and the board work out each executor's status from evidence, and store
it when it changed (logged once to the ledger as `stalled`, `paused` or `dead`):

| Stored status | Evidence | Status now |
|---|---|---|
| `closed` or `dead` | anything | unchanged — only `pilead send` leaves it (a relaunch; or, for a `close --keep-tab` session whose process still runs, the inbox) |
| any other | the current packet's report file exists | `reported` |
| any other | no pid file, or it cannot be read | unchanged — nothing is claimed without evidence |
| `busy`, `idle`, `stalled`, `paused` | pi is running; the last assistant message on its session log's active branch is a usage-limit error | `paused` |
| `busy`, `stalled`, `paused` | pi is running; busy longer than `stall_threshold_seconds` and its session log untouched for as long (no log found: the first alone) | `stalled` |
| `busy`, `stalled`, `paused` | pi is running; otherwise | `busy` |
| `idle` | pi is running | `idle` |
| any other | pi is gone; its tab is still open | `stalled` — look at the tab |
| any other | pi is gone; its tab is gone, or the terminal cannot be asked | `dead` |

"pi is running" means the process in `sessions/<sid>/pid` exists and did not start after that file
was written (a later process is another one that got the same number; its start is read as its age,
`ps -o etime`, so no clock or time-zone text is involved). A relaunch removes the previous run's pid
file before it opens the new tab, so the session reads `busy` until the new pi writes its own. "Busy longer than" counts from
when the current packet was written. A usage-limit error is an assistant message with
`stopReason: "error"` whose `errorMessage` matches `usage_limit_pattern`; `list` shows it as
`paused (limit)` and the message's first 120 characters are kept as `pause_text` in `meta.json`.
`pilead send` to a `dead` session relaunches it, exactly as for a `closed` one. A `closed` session whose
process is still alive (`close --keep-tab`) is not relaunched: the packet goes through its inbox. A
superseded session is refused. A `closed` session with `superseded_by` in `meta.json` is shown as
`superseded`; its stored status stays `closed`.

### `pilead list`

`--lead SID` shows only that lead's executors (and those with no lead); add `--all` to show every executor
whatever lead owns it while `SID` stays the reference for the LEADS section and `--all-leads`. Without
`--lead`, `--all` changes nothing.

Two tables. **LEADS**, one row per `leads/*.json`:

| Column | Meaning |
|---|---|
| PROJECT, SESSION | the lead's project and session id |
| MODEL | the model of the last assistant message in the lead's session log (`-` when unknown) |
| TERM | the terminal recorded at `lead-start` (`iterm`), `?` when none was |
| LIVE | `live`: its tab exists (looked up by the recorded iTerm handle only). `unreachable`: no tab, active in the last 15 minutes. `ghost`: no tab, not active since. `broken`: the record cannot be read |
| CTX | live context / window of the lead's own session |
| MB | the size of the lead's session log |
| LAST ACTIVE | when the lead's session log was last written, else when it was registered |
| AUTO | `AUTO` when the lead's posture is autonomous, else `-` (see [Autonomous mode](#autonomous-mode)) |
| TIER | the lead's model-tier posture: `auto`, `manual` or `lead`; `?` when it could not be read, `-` for a record that cannot be read (see [Model check and model tier](#model-check-and-model-tier)) |
| WAKE | whether the lead's wake is working, read from `wake/<sid>/health.json` (below); `?` for a record that cannot be read |
| VER | the pi-lead version the lead's running pi loaded (the health file's `version`), else the version it was registered with (the record's `version`, written by `lead-start`), else `?` |

The WAKE cell. The lead's own pi process — the extension, loaded in the lead's session — watches the inbox
and turns each `reported` line into a turn; while it runs it rewrites `wake/<sid>/health.json` at least every
30 seconds, and once more with `ended` set when the session ends. The first row that applies wins:

| Cell | When |
|---|---|
| `?` | the health file exists and cannot be read, or is not a JSON object, or has no usable `beat` — never shown as `ok` |
| `off` | there is no health file, or its `ended` is set |
| `STALE` | no process has its `pid`, or its beat is 90 seconds old or more |
| `STUCK` | a claim file beside the inbox is 60 seconds old or more, or the last failed send or put-back is later than the last delivered wake |
| `ok` | none of the above |

One footnote per cell that occurs, naming the leads (project, else sid) in table order:

```
  ⚠ WAKE=STUCK: <names> — a wake was claimed and not delivered (<detail>); the next drain retries it. If it stays, restart pi in that lead's tab.
  ⚠ WAKE=STALE: <names> — the lead's pi session stopped (<detail>); <n> report(s) wait in its inbox until pi runs there again: pilead resume <sid>
  ⚠ WAKE=off: <names> — registered, but no pi session with the pi-lead extension is watching the inbox
  ⚠ WAKE=?: <names> — the health file could not be read (<detail>)
```

`<n>` counts the `reported` lines in the inbox and in the claim files beside it (`reports may wait` when the
inbox cannot be read). With several leads in one footnote, each detail is prefixed by its lead's name, `<n>`
is their sum and the command reads `pilead resume <sid>`. `list --json` gives each lead `wake` (`ok`, `off`,
`stale`, `stuck` or `unknown`) and `waiting` (the count, or `null`).

The VER cell. The lead's extension reads the `version` of its own `package.json` once, when pi loads it, and
writes it into every health file. After pi-lead is upgraded, a lead whose pi is still running (WAKE `ok` or
`STUCK`) on the older extension gets one footnote, after the other LEADS footnotes, naming those leads in
table order and the oldest such version:

```
  ⚠ old extension: <names> — its pi runs pi-lead <that version>, <installed> is installed; run /reload in that pi (it loads the extension again)
```

Versions are compared part by part as whole numbers (`0.10.0` is newer than `0.9.0`); a version with a part
that is not a whole number, or one that is unknown on either side, is never compared and gives no footnote.
`list --json` gives each lead `version` (`null` for `?`).

**EXECUTORS**, one row per session that is not `closed` or `dead`:

| Column | Meaning |
|---|---|
| SESSION, STATUS, TOPIC | the executor, its status (above) and topic |
| SCOPE | what it covers: `spawn --scope`, else its topic; `-` for a session spawned before scope was recorded |
| PROJECT | its lead's project |
| MODEL | the model id, without the provider |
| PKT | the current packet |
| CTX | live context / window, as in `check` |
| TOKENS | prompt/output billed so far, then the share of prompt tokens served from cache |
| EFFORT | its thinking level (see [Model and effort](#model-and-effort)); `-` when its record has none |
| REPORTED | whether the current packet's report exists |

When the table has at least one row, one dim legend line follows its last row, before any footnote:

```
  CTX = live/window (live: the context of the last completed request on the conversation's current branch; window from pi's model list, ! = a request was larger than that window); TOKENS = prompt/output billed so far, then the cache hit rate
```

A value longer than its column is cut with `…`. Footnotes under each table say what needs a look:
heavy and approaching-heavy executors, a heavy lead (live context at or above `lead_nudge_tokens`,
or `context_nudge_tokens` for a lead on a 200k window), ghost leads, records that cannot be read, a
request larger than the model's window, how many `closed`/`dead` sessions are hidden, (`⚡ AUTO:`)
which listed leads are autonomous, and (`⚠ WAKE=…`) whose wake is not working.

```
pilead list --closed          # also closed and dead sessions (the 15 most recently created)
pilead list --lead <sid>      # that lead's executors and those with no lead
pilead list --all-leads       # also ghost leads of other projects
pilead list --json            # every lead and session, unfiltered
pilead list --plan            # after the executors, the plan of --lead (else the calling lead; see Plan)
```

Without `--all-leads`, ghost leads of another project are left out and counted in one line. "Another
project" is measured against `--lead`'s project, else the project of the lead in `$PI_LEAD_SID` /
`$PI_SESSION_ID`, else the current directory's name when a lead has that project; with none of these,
every lead is listed. Colour is used only on a terminal with `NO_COLOR` unset; piped output and
`--json` never contain escape codes.

### `pilead check --all`, `--json`

```
pilead check --all            # every session that is not closed or dead, each under `== <sid> ==`
pilead check --all --json     # only JSON on stdout: one object per session
pilead check <sid> --json
```

A JSON object has `sid`, `status`, `packet`, `reported`, `report`, `tldr`, `refs` (`state`, `line`),
`usage`, `ctx_window`, `heavy`, `approaching`, `diff` (the size of the staged diff of a session whose current
packet has a report, else `null`; see [Detection](#detection)), or `sid` and `error` for a session that cannot be read
(the others are still checked). Everything else `check` prints goes to stderr in this mode.

### `pilead status`

```
pilead status [sid]
pilead status [sid] --statusline [--ctx-tokens N] [--ctx-window N]
```

One line, for a status bar. It writes nothing — no status, no ledger, no cache — and shows the stored
status (only an existing report file upgrades it to `reported`). The sid defaults to `$PI_LEAD_SID`,
then `$PI_SESSION_ID`; for anything else it prints nothing.

```
lead proj · busy: api-120000 · reported: ui-130000 · ctx 146k/1M
pkt 0002 busy · for proj · ctx 88k/1M
```

`--statusline` prints the shorter line pi's footer shows (see [Status line](#status-line)). It reads the
records, `meta.json`, the status files, the lead's health file and whether report files exist — never a
session log — and writes nothing. A lead: `🚦 <project, else lead>`, then ` · busy: <names>` and
` · ✅ <names>` (reported; each at most three names, then `+<n>`), then ` · WAKE <cell>` when the wake is
not `ok`, then the weight segment: with `--ctx-tokens N` (a whole number ≥ 0) against the lead's line
(`lead_nudge_tokens`, or `context_nudge_tokens` when `--ctx-window` is 200000), ` · <tokens> ctx` at 60% of
the line or more and ` · <tokens> ctx → hand off` at the line or more. An executor: `pkt <NNNN> <status>`
and ` · for <project>` (the line above without `ctx`). Anything else prints nothing; the exit code is 0.

```
🚦 proj · busy: api-120000 · ✅ ui-130000 · WAKE STALE · 190k ctx
pkt 0002 busy · for proj
```

### Status line

pi's footer shows one pi-lead line in every session that loads the extension (the terminal UI and RPC
mode; print and JSON runs show none): the first line of `pilead status <sid> --statusline --home <home>`,
with the lead's `--ctx-tokens` / `--ctx-window` from pi's own context reading. It is refreshed at
`session_start`, after each settle, after a wake was handed over, after `/pilead:route retain` and every 30
seconds, one `pilead` child at a time (5 s timeout); it is set only when it changed, an empty answer
clears it, and it is cleared when the session ends. A failure there never delays or stops a wake. pi's own
footer already shows the session's context; the weight segment only says when the lead is getting heavy.

### `pilead stats`

```
pilead stats [--lead <sid>] [--since <days>] [--json]
```

One row per packet ever sent, closed and dead sessions included: SESSION, PKT, MODEL, EFFORT (the
session's thinking level; `-` when its record has none, `null` as `effort` in `--json`), ROUNDS, VERDICT
(the last `pilead verify` verdict), STATUS (the report's `Status:`), TOKENS and COST; a trailer line
per session (packets, tokens, tokens per packet, pi's recorded cost — with `--since` or `--lead` it
reads `N of M packet(s) shown`) and a SUMMARY by model (packets,
mean rounds, % COUNTS-MATCH, % exactly `clean`, tokens per packet, cost).

- **ROUNDS**: how many later packets went to the same session before the work was committed —
  "committed" is the first `pilead refs accept`, or cleared `verify --for-autocommit`, after the packet
  was sent. `-` when no such event follows.
- **TOKENS**: prompt/output used while the packet was in flight — the usage at its report minus the
  usage when it was sent. When either reading is missing, or the difference is negative, the value is
  an **estimate**: the session's total over its packet count, prefixed `~`. The count is all of the
  session's packets, whatever `--since` keeps. `-` when nothing is known.

## Board

`pilead board [--open] [--out PATH] [--lead SID] [--json] [--live [on|off]]` writes `<home>/board.html`: one
self-contained page (nothing is loaded from the network) of every lead and executor. The board and `pilead list` read through the same functions
(`read_leads` and `read_sessions` in `lib/pilead/verbs/list.py`), so a lead's LIVE, WAKE, posture, heaviness and
an executor's status, tokens, context and queue are the same on both. Run by a lead (`$PI_LEAD_SID`, else
`$PI_SESSION_ID`, naming a registered lead), `pilead board` first parks that lead's finished executors as
`pilead list` does (see [Auto-close](#auto-close)) and prints one line per park after the page's path. It
prints the path, then the page's `file://` address, then the park lines; with `--open` the page is opened last.

| Option | What it does |
|---|---|
| `--open` | open the page afterwards (`open` on macOS, `xdg-open` on Linux; with no opener, one line gives the address) |
| `--out PATH` | write the page to PATH (`~` is expanded, its directory is made) instead of `<home>/board.html` |
| `--lead SID` | only that lead's executors, and those with no lead; takes a lead's project or a unique id prefix like every other id; an unusable id is refused (exit 2) before anything is written |
| `--json` | print the data the page is drawn from, as JSON, and write no page and no data file; the park lines go to stderr |
| `--live [on\|off]` | `on` (also with no value): render a live page and write its data file beside it; `off`: remove that data file |

`--json` is the same data the page is drawn from: a live page's data file is that document, as the page was
last drawn. `--json` does not go with `--open`, `--out` or `--live`, and `--live off` does not go with `--open`,
`--json` or `--lead` (exit 2, nothing written). A page that cannot be written is exit 1 with one line naming
the path; every file is written through a temporary file and one rename, so a reader never sees half a page.

### Live mode

A live page reloads itself every `board_refresh_seconds` (10) and carries a badge, `updated HH:MM:SS`. Nothing
runs in the background — no server, no socket, no watcher. Instead pilead rewrites the page and its data file
(`board.json`, beside the page: `board.html` → `board.json`) whenever a command changes or shows state, so an
open page is never more than one reload behind.

- Live mode is on when config `board_live` is `true`, or when `<home>/board.json` exists — so one
  `pilead board --live` is enough, and `pilead board` with `board_live` on is live too.
- `pilead board --live off` removes the data file (the one beside `--out`, with it) and live mode is off. When
  config `board_live` is `true` that key keeps it on: the command says so, and the key is set to `false` by hand.
- These verbs rewrite the default page, last, before the tidy of the tabs where they have one: `list` and
  `check` (also `--all` and `--json`, never `--refs-only`), `send`, `spawn` and `close` after a success, and
  `turn-end` (never `--dry-run`). `status`, `stats`, `focus`, `tidy`, `doctor`, `lint` and every `--dry-run`
  never do. The rewrite parks nothing, stores no status and writes no usage cache or ledger event — of all of
  home it changes `board.html` and `board.json` — and it never changes what the verb prints or its exit code.
- The badge turns red (`upd stale`) when the page is older than three times `board_refresh_seconds`: nothing
  has rewritten it, because no verb has run. Run any of the verbs above, or `pilead board`, to bring it back.
- A page written elsewhere with `--out … --live` is not rewritten by the verbs; it reloads itself and shows the
  red badge until `pilead board --out … --live` is run again.

In the rail, beside a lead's name:

| Chip | Meaning |
|---|---|
| `STUCK` / `STALE` (red) | the wake is stuck or the lead's pi stopped (see `pilead list`'s WAKE); the title says why |
| `off` | registered, but no pi with the extension is watching the inbox |
| `?` | the health file could not be read; titled `posture not readable`, the posture could not be read |
| `heavy` | the lead's live context is past `lead_nudge_tokens`; the title gives the reading |
| `auto`, `tier manual`, `tier lead` | the lead's posture (`pilead auto`, `pilead tier`) |

A lead's name is red when its LIVE is `unreachable`, `ghost` or `broken`; a lead record that cannot be read
is its id and the word `broken`, and its executors show under "Unowned / orphaned".

An executor's page has chips for its model, `effort`, context, tokens, cache hit rate, size, `heavy`,
`approaching heavy` (with the live context), `pinned`, `queued` (`queue ?` when the queue file cannot be
read), `auto:` (why it was auto-closed; its status then reads `closed (auto)`), `report not delivered` (a
reported session's lead has no `wake_delivered` for its packet) and `orphan`. Its actions copy a command:
`Verify report` and `Staged diff` (reported), `Send follow-up`, `Close`, `Resume` (closed, dead or
superseded), `Focus tab` (any session still running) and `Retire` (a running session that is heavy).

The overview's warnings, in this order: refs moved / not compared; orphans; reports not delivered to their
lead yet; heavy and approaching-heavy executors; heavy leads; wakes `STUCK` or `STALE`; wakes not known;
queues that cannot be read; records that cannot be read; sessions this run parked; a ledger that cannot be
read. The sub-header names the installed pi-lead version.

Three kinds of "not known" appear on the page, and none is ever shown as `0`, `false` or fine: `-` for a
reading that could not be made (tokens, context, size, last active), `?` for a wake, posture or version that
could not be read, and `queue ?` for a queue file that cannot be read.

## Model aliases

`--model` takes `provider/id` verbatim, or a tier alias resolved against a cache of
`pi --list-models`: `haiku`, `sonnet`, `opus`, `fable` (latest Anthropic model of that family),
`dsflash`, `dspro` (DeepSeek flash / pro). Never type version ids from memory.

## Model and effort

Every executor is launched with an explicit model and thinking effort chosen by pi-lead, never left to
the human's personal pi settings. Both are decided before `pilead spawn` writes anything; a refusal
leaves the state dir as it was (bar a refresh of the `models.json` cache).

**Model**, in this order: `--model`, else config `executor_default_model` (default `sonnet`).

- **Fallback.** When the model is a known alias that matches no model in `models.json` and config
  `executor_fallback_model` (an alias or `provider/id`) is set, the fallback is used; spawn prints
  `model: <asked> is not available here; using the fallback <model>` and ledgers `model_fallback`. An
  unknown alias is still an error, and so is a fallback that does not resolve (with the original error).
  The fallback acts when a session is **launched**, never in the middle of one: pi has no fallback model
  of its own — it retries a transient provider error a few times and then ends the turn with the error.
- **Ceiling.** Config `executor_model_ceiling` (default `opus`; one of `haiku` < `sonnet` < `opus` <
  `fable`). An `anthropic` model's tier is the first of those words its id contains. A model whose tier
  ranks above the ceiling, or an `anthropic` model whose id has no tier word, is refused (exit 2) unless
  `--model-override "<reason>"` gives a reason that is not blank; the launch then prints
  `model: <model> is above the ceiling <ceiling>; override recorded` and ledgers
  `model_ceiling_override`. A model of any other provider (DeepSeek) has no tier and is never refused. A
  ceiling that is not one of the four words puts every `anthropic` model above it. A cold `models.json` cache is written before a
  ceiling refusal (the refusal still holds).

**Effort** (pi's `--thinking` level: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; pi clamps it
to what the model supports), in this order: `--effort` (anything else is refused, exit 2); else the
packet's first `EFFORT: <level>` line (a value that is not a level is ignored with one line on stderr, and
packet lint warns `effort-unparsable`); else config `executor_default_effort` (default `high`; a value
that is not a level means `high`). The level is stored in `meta.json` as `effort` and typed as
`--thinking <level>` on every launch of the session, the relaunch by `send` included.

`--scope TEXT` records what the executor covers (`pilead list`'s SCOPE; default: the topic). `--keep`
pins the session at birth, as `pilead keep` would, to the spawning lead. Spawn prints, between its
`spawned …` first line and `placement=…` last line, `effort=<level> scope=<scope>`, then the fallback
and override lines when they apply, then `pinned` for `--keep`; it ledgers `launch_options`.

## Model check and model tier

**The model check.** `pilead lineup [--session SID] [--json]` reads what this machine's pi really offers:
`pi --list-models` (cached in `<home>/models.json`, refreshed when older than a day) lists the models whose
provider has credentials here, with no model call. A class (`haiku`, `sonnet`, `opus`, `fable`, in that
rank order) is **available** when the list holds a model of it, **not available** when the list was read and
holds none, and **unknown** when the list could not be read; unknown is never shown as either of the others.
The unranked aliases `dsflash` and `dspro` are listed the same way. The lead's own model is the `provider/id`
of the last answer in its own pi session log, so a model switched with `/model` is seen after the lead's
next answer, not before it. Nothing here names a class this machine's pi does not list, and no model id is
written from memory: every model named comes from the list or from a session log.

The first line printed is the `Model check:` line the lead says as the first line of its answer: where its
class sits among the available ones, the stronger classes it could switch to, and the executors it can
delegate to (the available classes not above config `executor_model_ceiling`, then the available unranked
aliases). When the lead runs the weakest class available, the line asks for a `/model` switch and `STOP:`
follows it; the exit code is 0 either way. A lead on an unranked model, or whose log holds no answer yet, or a
model list that could not be read, is said so and never guessed at. `lineup` writes nothing but what
`models.load` writes to `models.json`.

**The tier posture** decides who chooses an executor's model when `--model` is not given:

```
pilead tier auto          # the lead chooses by the rubric (the default)
pilead tier manual        # the human chooses at every spawn, rotate and upgrade
pilead tier lead          # an executor with no --model runs on the lead's own class
pilead tier status        # one line; changes nothing
pilead tier ask --session <lead sid> --packet <packet> [--json]   # the question a human answers under manual
```

- **`auto`**: nothing changes (`--model`, else config `executor_default_model`).
- **`manual`**: `pilead spawn`, `pilead send --rotate` and `pilead send --upgrade` with no `--model` refuse
  (exit 2) before anything is written, and point at `pilead tier ask`. The autonomous posture never relaxes
  it: it is the one posture that adds a stop. `ask` prints the options this machine offers (the recommended
  class first, chosen from the packet's lint hints: `opus` for `shape-opus`, `haiku` for `shape-haiku`, else
  `sonnet`), the lead's own class tagged, the classes above the ceiling named as not offered; the human's
  answer is passed as `--model`. With no option available `ask` exits 1 and says to run `pilead doctor`.
- **`lead`**: `spawn` with no `--model` and `send --rotate` with no `--model` launch on the lead's own class
  (an unranked lead model gives that `provider/id`), printing `model: tier lead — this lead runs <model>;
  using <chosen>` and ledgering `model_from_tier`. The fallback and the ceiling still apply to the chosen
  model, a paused session's rotation still takes the fallback, and `--model` always wins. A lead whose model
  cannot be read is refused. `send --upgrade` still goes one tier up from the session's own model.

A plain `send`, a queue delivery, `resume` and `restart`, and every verb given `--model`, are not affected.
A posture that could not be read (a `tier` that is none of the three words) refuses a spawn, rotate or
upgrade with no `--model` and is never taken for `auto`. The posture is per lead session, in the lead's own
record (`leads/<sid>.json`: `tier`, `tier_source`); `pilead tier` writes `tier_set`, and every
`pilead lead-start` resets it to `auto`. `pilead list` shows it as the `TIER` column and, for an autonomous
lead on `manual`, in the `⚡ AUTO:` footnote. An executor cannot change it.

## Skills

One skill per command, named `pilead-<name>`: relay 0.5.7's twenty (`auto`, `board`, `check`, `close`, `diff`, `focus`, `handoff`,
`list`, `mode`, `plan`, `restart`, `resume`, `retire`, `review`, `route`, `send`, `spawn`, `stop`, `tier`, `verify`) and pi-lead's
own seventeen verbs (see [Commands](#commands)); `pilead-route` has no command of its own, because `/pilead:route` is the
extension's. The executor's standing rules and report format are `skills/executor-rules.md`, injected into each
executor's system prompt.

The executor rules also carry CONTEXT HYGIENE rules (read by line range, filter shell output, never loop on
screenshots, don't re-read). Measured on claude-relay 2026-10-03: executors' median peak context was 144k and the
max 846k, almost all of it raw tool output rather than reasoning.

## Packet lint

```
pilead lint <packet> [--worktree DIR] [--model MODEL] [--strict] [--json]
```

A zero-token, read-only sanity pass over a packet file before it is sent — ported from claude-relay's
`lint_packet`. It reads the packet and (sizes only) the files it names; it writes nothing, anywhere,
and never runs `pi`. `--worktree` resolves the packet's relative file references (for the reading-size
rule below); `--model` is used as text only — lint never resolves an alias or touches `models.json`.

| Finding | Level | Fires when |
|---|---|---|
| `short-packet` | warn | the packet body is shorter than 80 characters |
| `no-preconditions` | warn | no `## Preconditions` heading |
| `line-ignored` | warn | the packet has an `MCP:` or `CONTEXT:` line — pi-lead does not read either (see below); one finding per kind present |
| `effort-unparsable` | warn | the packet's first `EFFORT:` line has a value that is not one of `off/minimal/low/medium/high/xhigh/max` (any letter case) — `pilead spawn` ignores it |
| `reading-front-loaded` | warn | the files the packet refers to total 1.2MB or more — every relaunch re-reads that prefix |
| `asks-to-commit` | warn | the packet tells the executor to commit or push |
| `asks-to-ask` | warn | the packet tells the executor to ask someone instead of stopping and reporting |
| `shape-haiku` | info | the packet names its files and has an acceptance command, and the model isn't already haiku/dsflash — a cheap model could do this |
| `shape-opus` | info | the packet asks the executor to investigate/diagnose/figure out why, with no repro, and the model isn't already opus/fable |

Left out of relay's rule set, and why: the three `mcp-*` rules (pi has no MCP support); `context-*`
(pi's model list carries the context window — pi-lead has no `CONTEXT:` declaration to get wrong).

With no findings, `pilead lint` prints `✓ packet lint: no findings` and exits 0. `--strict` exits 1
when any finding is a warn (info-only findings never fail `--strict`). `--json` prints the findings as
a JSON list of `{"level", "code", "message"}` and nothing else. A packet path that is not a readable
file is an error, exit 2.

`pilead spawn` and `pilead send` each lint the outgoing packet automatically and print the same
findings, in the same wording, on stderr — `  ⚠ lint[<code>]: <message>` for a warn, `  ℹ lint[<code>]:
<message>` for an info. This is advice only: it never changes the exit code, stdout or what gets sent,
and a lint finding never blocks a spawn or a send. Set `PILEAD_NO_LINT=1` to turn it off.

## Doctor

```
pilead doctor [--offline] [--quick] [--strict] [--json]
```

Checks that this machine and state dir are fit to run pi-lead. Read-only: it never repairs, installs
or deletes anything, and never calls a model. It runs twenty-one checks and prints one line per check:

- **PASS** — checked and fine.
- **WARN** — checked; pi-lead works, with a limit worth knowing about.
- **FAIL** — checked; something pi-lead needs is missing or wrong.
- **SKIP** — deliberately not run (`--offline`, or nothing to check — home doesn't exist yet, or there
  are no open sessions).
- **NOT CHECKED** — the check was attempted and could not be made (a program wouldn't start, its
  output couldn't be parsed, a probe timed out). Never counted or worded as a pass.

Checks, in order: the Python interpreter (3.9+); the package's own files (the extension, the executor
rules, the 37 skill files); every verb module loads and registers; `pilead` resolves on PATH to this package;
git (2.31+, via the same resolver refs detection uses); iTerm2 and its Python API; `osascript`; the
home directory's existence and permissions; `config.json`'s shape (a bad key names why it's ignored;
a bare `NaN`, `Infinity` or `-Infinity` anywhere in the file, at any depth, is a `FAIL` that names
where it sits — the pi extension ignores the whole file because of it, though the Python CLI does not);
`models.json`'s freshness; every model alias resolves; the ledger's shape; every lead/session file
parses; at least one of pi's session-log directories exists and can be listed; every open session's
own log can be found there; every open session's git shim (present, executable, matches the current
version, and actually refuses a `commit` — probed where git can find no repository and reads no system
or global config, so a shim that fails to refuse still cannot commit anywhere); and four **live**
checks — `pi`'s own version, then three that share ONE
`pi --mode rpc --no-session` process (in a throwaway, scrubbed environment: never the real home,
never a real session id), reading its reply for whether it lists all 38 `/pilead:` commands, all
37 skills, and loads pi-lead from this package (not another copy on PATH).

A `git --version` or `pi --version` that exits non-zero is a `FAIL` giving the exit code and the first
line of its stderr, even when it printed a version. The `pi --mode rpc` probe has one 20 s deadline from
start to answer; when it runs out, the three checks read `NOT CHECKED` and pi is stopped (terminated,
then killed).

An open session, for every check that counts one (`state`, `executor logs`, `git shim`), is one whose
stored status is neither `closed` nor `dead`.

`--offline` skips the four live checks (`SKIP`, and no `pi` process starts). `--quick` skips only the
slower three, `pi commands`, `pi skills` and `one copy` (`SKIP` with the detail `--quick`; pi is asked for its
version and never started in RPC mode); with `--offline` as well, the four say `--offline`. Exit code: 1 when any
check is `FAIL`; else 3 when any check is `NOT CHECKED`; else 0; 130 when interrupted with Ctrl-C
(one line, `pilead: doctor interrupted`, on stderr; the throwaway directory and any pi probe are
cleaned up). `--strict` makes a `WARN` also count
as a `FAIL` for the exit code (the printed statuses don't change). `--json` prints the results as a
JSON list of `{"check", "status", "detail", "info"}` and nothing else. The summary line always gives
the five counts — it never claims everything passed.

## Tab placement

`pilead spawn` opens the executor's tab immediately to the right of the lead's own tab. That needs
iTerm2's Python API: `pip3 install iterm2` for the `python3` you run pilead with, and iTerm → Settings →
General → Magic → **Enable Python API**. The lead's tab is the `$ITERM_SESSION_ID` that
`pilead lead-start` records (and a spawn typed in the lead's own bash uses its live one, which
survives an iTerm restart). Without the API — module missing, API disabled, a 2 s timeout, or the
lead's tab gone — the spawn still succeeds: AppleScript appends the tab to the end of the lead's
window (or the front window when the lead can't be found). The last line of `spawn` says which:
`placement=adjacent` or `placement=end-of-bar (<reason>)`. In iTerm and tmux, `--pane` splits the lead's own tab
instead (`placement=pane`); `--tab` is the default. Under tmux the pane is a side-by-side split of the lead's pane.

## tmux and Linux

**tmux** is the backend for Linux / SSH leads and for anyone running pi inside tmux; it needs tmux 3.2 or newer.

**Quick start inside tmux.** The same five steps as the [Quick start](#quick-start), in a tmux session:

```bash
tmux new -s lead                      # or attach to one
pi -e ./extensions/pi-lead.ts         # or `pi` after `pi install`
/pilead:mode                          # in pi: registers this lead and renames its window
```

Executors then open as **windows** next to the lead's own, or as split panes of the lead's window with
`pilead spawn … --pane` (a side-by-side split of the lead's pane; `--tab` is the default). `/pilead:focus <sid>` selects an
executor's window, `/pilead:resume` reopens a dead one.

**What differs from iTerm2.**

- Sessions are addressed by pane id, so tab handles look like `tmux:%N`; the handle names its backend, so `focus`, `close`
  and `resume` need no flag.
- A lead's colour is applied through `window-status-style` instead of a tab colour.
- The lead's window name is set when `pilead lead-start` runs (`rename-window`; nothing is typed into a running pi), unless
  `--no-rename`. An executor's window is named at spawn the same way, never with `/rename`.
- There is no tab tidy under tmux: `pilead tidy` orders iTerm2 tabs only, and the automatic one skips silently.
- Banners: a tmux lead gets a status-line message on its own pane; the fallback is `osascript` on a Mac and `notify-send` on
  Linux when it is installed (else nothing). See [Notifications](#notifications).
- A tmux lead is live only while a `pi` runs on its pane's terminal line (a pane outlives its program).

**Selecting it.** tmux is used when `PILEAD_TERMINAL=tmux` (or claude-relay's `RELAY_TERMINAL=tmux`), when `terminal_app: tmux`
is in `config.json`, or automatically when `$TMUX` is set. `PILEAD_TERMINAL` forces `"iterm"` or `"tmux"` and beats
`terminal_app`; `RELAY_TERMINAL` is read after it. `PILEAD_TMUX_SOCKET=<name>` (else `RELAY_TMUX_SOCKET`) points pi-lead at a
named tmux server (`tmux -L <name>`). `pilead doctor` shows the selected backend and the tmux version.

**On Linux** the optional helpers are `xdg-open` (for `--open`), `xclip` or `wl-copy` (clipboard) and `notify-send` (banners);
none is required — without an opener `--open` prints the page's `file://` address. `pilead doctor` has a `linux helpers` row
saying which are on PATH. Outside tmux on Linux pi-lead has no terminal to drive: run it inside tmux or set
`PILEAD_TERMINAL=tmux`.

**Nothing of this has run on a real Linux box yet** (see `docs/manual-checklist.md`, which carries the live checks); until
that checklist says otherwise, treat Linux as untested.

## Telling tabs apart

**Names.** A lead's tab is named `[Lead] <project>` (white space and control characters folded to one space,
cut to 60 characters; `[Lead] <sid>` when the project is empty). `pilead lead-start` writes that `label` into the
lead's record; when another live lead already holds exactly that label, the new one gets ` ·<first four
characters of its sid>` on the end, as a handoff's successor does. pi writes the terminal title itself, from the
session name, so the lead's own extension names the tab (`pi.setSessionName`, at every drain and health beat)
and the tab bar reads `π - [Lead] <project> - <directory>`. An executor's tab is named the same way from its
`meta.json`: `label` (`[Exec] <sid>`), or `tab_title` when `pilead close --keep-tab` or a revive renamed it.
Nothing is ever typed into a tab where pi runs: the only text pi-lead types is the launch line into a new
shell. `pilead lead-start … --no-rename` registers the lead and leaves its own tab as it is. The record still
gets its `label` and `color` (its executors wear that colour), with `"no_rename": true`, and the extension
names no tab and nothing is painted. A later `lead-start` without the option writes `false`, and the tab is
named and painted again.

**Colours.** Each lead has one colour from a palette of six, kept in its record (`color`), picked so that no two
registered leads share one while six or fewer exist; its own tab is painted at `lead-start` and every executor
it owns wears the same colour — printed into the new tab's launch file before `exec pi`, so a spawn, a relaunch,
a `resume`, a `restart` and a rotation all wear the CURRENT owner's colour. The colour follows the role: a
handoff's successor takes the caller's colour (and its suffixed label), a takeover's new record carries the old
record's colour and label, and an executor that changes owner (`pilead adopt`, a takeover) is repainted with its
new owner's colour. `tab_colors: false` in `config.json` turns colours off: no launch file prints one and no
terminal line is written. Painting is cosmetic — a colour that could not be applied never changes an exit code,
a line of output or a ledger event. (`PILEAD_NO_PAINT`, set and not empty, stops every write of a colour to a
terminal line; the test suite sets it.)

**Focus.** `pilead focus <name>` brings a tab to the front: `<name>` is an executor's sid, a lead's sid, or the
project of exactly one registered lead (compared trimmed and in any case; a project two leads hold is refused
with both sids). It writes nothing and asks nothing when iTerm2 is not running. pi-lead finds a tab by its
HANDLE (`sessions/<sid>/iterm-id`, a lead's `iterm_handle`), never by its title. A handle goes stale when iTerm2
is restarted: `pilead focus` then says it could not focus the tab — `pilead lead-start` run again in the lead's
tab records the tab it is in now, and `pilead resume <sid>` reopens an executor. A lead that runs in neither iTerm2 nor tmux (no
handle recorded) gets the same `could not focus` answer. Under tmux the handle is a pane (`tmux:%N`) and `focus` selects its window.

**Order.** `pilead tidy` puts the tab bar back into `[Lead] [Exec 1] [Exec 2] … [Lead 2] [Exec 2.1] …`: every lead
with a recorded iTerm2 tab (oldest lineage first — a handoff's successor sorts where its predecessor did), each
followed by the executors it owns that are not closed or dead, in the order they were spawned — and paints each
group in its lead's colour (unless `tab_colors` is false). Each window is ordered on its own: no tab is ever moved
to another window (an executor whose tab is in another window than its lead's is named and left where it is), a
tab pi-lead does not manage is never moved, and nothing is closed, opened, renamed or signalled. A handle made
stale by an iTerm2 restart is found again by the terminal line of the pi behind it. `pilead tidy --dry-run`
prints the order and how many tabs would move, and changes nothing; a tidy that could not be applied exits 1
with the reason. The verbs that open a tab or change an owner — `spawn` (and a rotation or upgrade), a `send`
that relaunched or adopted a session, `resume`, `restart`, `handoff`, `close-predecessor`, `adopt` and
`takeover` — tidy afterwards by themselves, in a new process (`pilead tidy --quiet`, at most 8 s); a tidy that
could not be done prints ONE line, `· tab tidy skipped (<reason>) — pilead tidy --dry-run shows the order it
wanted`, and never changes the verb's exit code, its output or its files; `pilead list` names a lead whose last
tidy was skipped. `tidy_tabs: false` in `config.json` turns the automatic tidy off (`pilead tidy` still works);
`RELAY_NO_TIDY`, set to anything, turns every tidy off (the test suite sets it). Moving tabs needs iTerm2's
Python API — iTerm → Settings → General → Magic → **Enable Python API**, and `pip3 install iterm2` for the
`python3` you run pilead with; without them every tidy is skipped with that one line and everything else works.

## When the lead's turn ends

When pi has settled (the `agent_settled` event), a registered lead's extension runs `pilead turn-end --session <sid>
--cwd <cwd> --json` (with `--banner` in pi's terminal UI, and only in the terminal UI or an RPC run) and sends what
it says as ONE message with `triggerTurn`: `🚦 [pi-lead] — review needed:`, the lines, and then the posture's
instruction (the autonomous one, or under the manual posture "say these to the human and wait for their word; pass on
every line that starts with 🟠 exactly as it is"). The steps run in this order; an error in one is swallowed and the
next still runs:

| # | Step | Needs config | Line it adds |
|---|---|---|---|
| 1 | the sweep: park the lead's finished executors (see [Auto-close](#auto-close)) | `auto_close` | none (`pilead turn-end --json` lists them under `parked`) |
| 2 | the lead's own commits | `surface_commits` | `📝 you made <n> commit(s) this turn in <cwd>:` and one line per commit (at most 20) |
| 3 | executors of this lead approaching heavy | `ctx_warn_wake` | `🟠 <executor> is approaching heavy (<live> ctx, warn line …, rotate line …) — plan a rotate: pilead retire <executor> and a fresh spawn for its next packet` |
| 4 | the lead's own session is heavy | `handoff_nudge` | `🔁 this lead session is getting heavy (<reading>). Consider handing off to a fresh lead session: write a handoff note, then run pilead handoff <note.md>.` |

Each is said once. The commits are found by comparing `wake/<sid>/head` (`<top level of the repository>` TAB `<commit
id>`) with the repository `--cwd` is in; the file is written on the first run, when the repository changes, and
whenever the commit moved, so a commit is named only in the turn after it was made. A 🟠 line is claimed once per
executor (`wake/<sid>/notified/<executor>.ctx-warn-wake`), the 🔁 line once per lead (the stamp
`wake/<sid>/handoff-nudged`; the lead is heavy when its live context is at or above `lead_nudge_tokens` — capped at
`context_nudge_tokens` on a 200k window — or its log is `handoff_nudge_mb` or larger). A reading that could not be
made adds no line and uses up no claim; a claim or stamp that could not be written adds no line. With `--banner` and
at least one line that is not a 🟠 line, one desktop banner says `review needed` (see [Notifications](#notifications);
it needs `notify_on_wake`). The lead is never told to hand off, retire or commit: each line states what was seen.

At most one turn-end message is sent per 60 seconds; a message that comes sooner is written to the ledger as
`turn_end_suppressed` and not sent, and a send that fails as `turn_end_failed`. A suppressed line is never sent later, so a commit
or heavy line that falls in that window never reaches the lead; and a settle that comes while a turn-end child is still running is
skipped, not queued. `pilead turn-end --dry-run` (as the
lead, or with `--session`) prints the lines a run would give, with `dry run — nothing was written` first, and writes
nothing anywhere — no head file, claim, stamp, ledger event or banner, and the sweep is a dry run. `--json` prints
`{"lines": […], "kinds": […], "parked": […]}` as one line.

`auto_wake: false` in `config.json` (read once, at session start) turns the lead's wake off: the extension claims
nothing from the inbox, sends no wake and no turn-end message, and reports stay in the inbox, where `pilead list` and
`pilead check` show them; orphaned claim files are still put back. The health file gains `auto_wake: false`, and the
lead's wake reads `off`.

## Notifications

pi-lead posts a desktop banner for the four events below, each announced once. There are two tiers, as in
claude-relay:

| Tier | How | Click |
|---|---|---|
| 1 | iTerm's own escape sequence (OSC 777), written to the lead's tab by its tty device | focuses the lead's tab |
| 2 | `osascript -e 'display notification …'` | none |

Tier 1 needs macOS to allow iTerm to post notifications (System Settings → Notifications → iTerm). Its
title is set by iTerm; with `notify_via: "osascript"` the banner has pi-lead's own title and no click. The tty
is recorded once, by `pilead lead-start` (the record's `tty`, found through iTerm with one AppleScript call
from `$ITERM_SESSION_ID`; `null` when that failed, and then found again at each banner from the record's
`iterm_handle`). When tier 1 was tried and did not post, tier 2 does, and one `banner_fallback` event is
ledgered with the reason. A banner never changes what a verb prints, its exit code, or whether a wake is
delivered, and every error in one is swallowed.

| # | Event | Who posts it | Banner |
|---|---|---|---|
| 1 | a report's wake was handed over | the lead's extension, after `wake_delivered`, by running `pilead notify <lead> --executor S --packet N` | `pi-lead · <project>` / `S reported` / the first line of the report |
| 2 | an executor settled with a report for its current packet | the executor's extension, by running `pilead escalate S --packet N --json` (see [When a report finds no listener](#when-a-report-finds-no-listener)); `pilead notify --no-lead --executor S --packet N` posts the same banner by hand | `pi-lead — report ready` / `S reported` / why the lead is not told (below) |
| 3 | an executor is approaching heavy and has a lead with a record | `pilead check` and `pilead list`, after their own output | `pi-lead · <project>` / `S is approaching heavy` / `<live> ctx, warn line <warn>, rotate line <nudge> — plan a rotate: pilead retire S` |
| 4 | `pilead send` printed the third-packet note | `pilead send`, after the note | `pi-lead · <project>` / `S: packet <n>` / the note |

The extension runs row 1 only in pi's terminal UI (a headless run posts nothing) and row 2's `pilead escalate`
in the terminal UI and in RPC mode, without a shell, with a 10-second (row 1) or 30-second (row 2) limit, and
does not wait for the result. Row 2 posts only when the executor's lead is not listening:
`pilead notify --no-lead` looks at the executor's lead and the lead's wake word (see [`pilead list`](#pilead-list)) and
posts when there is no lead or no record for it (`no lead is registered for it — pilead check S`) or the word is
`off`, `STALE` or `?` (`its lead is not listening (<word>) — the report waits in the inbox`); for `ok` and `STUCK` it
posts nothing, because the lead's own session announces the report. It needs `executor_escalation` too.
`pilead escalate` posts these two and two more, once per packet (the table in
[When a report finds no listener](#when-a-report-finds-no-listener)).

## When a report finds no listener

When an executor settles with a report, its extension appends `reported <sid> <report>` to its owner's inbox
(once per report, whatever `executor_escalation` is) and then runs `pilead escalate <sid> --packet <n> --json`
(one child, in pi's terminal UI or RPC mode, 30-second limit, not awaited; `session_shutdown` kills it). The verb
(`lib/pilead/escalation.py`) decides once per packet whether that report reached a lead that will act on it — the
port of claude-relay's executor escalation. pi-lead never types into a running pi session: relay's typed line is
the inbox line, which the lead's own extension turns into a wake; when no extension is listening, a banner tells
the human and the line waits.

The first row that applies wins:

| # | When | Outcome | What is done | Stored |
|---|---|---|---|---|
| 1 | config `executor_escalation` is false | `off` | nothing | — |
| 2 | the packet has no report file | `no-report` | nothing | — |
| 3 | an entry exists for the packet, confirmed or with a status other than `sent` | `handled` | nothing | unchanged |
| 4 | the ledger holds `wake_delivered` or `wake_skipped_landed` for this executor and packet, or the lead looked at the report (`report_verify`, `report_reviewed`, `report_looked_at`, or `usage_snapshot` with `at` `"reported"`, as in [Auto-close](#auto-close): the executor's own `check`, `diff` or `verify` never counts) | `resolved` | an entry `sent` becomes confirmed (no event); with no entry, `escalation_resolved` | `resolved`, or `sent` confirmed |
| 5 | the owner state is `unknown` (see [Ownership](#ownership)) | `owner-unknown` | banner A | `notified` |
| 6 | `orphan` or `unowned`, or owned by a lead whose LIVE is `ghost`, and a fall-back lead exists | `fallback` | `adopted` (reason `fallback`) to that lead, `owner_fallback`, then row 8 or 9 for it | that of row 8 or 9 |
| 7 | as row 6, and no fall-back lead exists | `no-lead` | banner B | `notified` |
| 8 | owned, and the owner's wake word is `ok` or `stuck` | `send` | the inbox line, when it is not waiting already; banner C | `sent`, unconfirmed |
| 9 | owned, and the owner's wake word is `stale`, `off` or `unknown` | `not-listening` | the inbox line, when it is not waiting already; banner D | `sent`, unconfirmed |

"Waiting" means the exact line is in the owner's inbox or in a claim file beside it, so a lost line is written
again and a waiting one never twice. An append that fails is ledgered `escalation_push_failed` and stored as
`failed`; the row's banner is still asked for. An unconfirmed `sent` is decided again at the next settle and is
confirmed once the lead has the report. A state that could not be read is never `resolved` and never "no lead". Two limits: two settles close together can append a
duplicate escalation inbox line (benign), and a push that failed (`failed`) is not retried, although the extension still writes the
inbox line itself.

The fall-back lead is the ONE lead, other than the owner, whose record can be read, whose `project` equals the
session's project exactly, whose LIVE is `live` and whose wake word is `ok` or `stuck`. The session's project is
`owner_project` in `meta.json` (written by `spawn` and by every adoption), else the owner's record's, else the
forward note's. No project, no such lead, or two of them: there is none — pi-lead never chooses between two.

| Banner | Title | Subtitle | Message | Claimed as |
|---|---|---|---|---|
| A | `pi-lead — report ready` | `<sid> reported` | `its owner could not be read — pilead check <sid>` | `_no-lead` / `<sid>.no-lead-<n>` |
| B | `pi-lead — report ready` | `<sid> reported` | `no lead is registered for it — pilead check <sid>` | `_no-lead` / `<sid>.no-lead-<n>` |
| C | `pi-lead · <project>` | `<sid> reported` | the report's first line, else `report ready` | `<owner>` / `<sid>.report-<n>`, the key of the lead's own wake banner, so only one of the two shows |
| D | `pi-lead — report ready` | `<sid> reported` | `its lead is not listening (<word>) — the report waits in the inbox` | `<owner>` / `<sid>.no-lead-<n>` |

Each banner needs `notify_on_wake`, and none is posted (or claimed) under the kill switch. What was decided is kept
in `sessions/<sid>/escalation.json`, one entry per packet (`{"status", "confirmed", "at"}`).

`pilead escalate <sid> [--packet N] [--json] [--dry-run]` prints `escalation: <outcome> — <detail>` (for `fallback`
a second line, `then: <outcome> — <detail>`, for the new owner), or the decision as one JSON line with `--json`;
exit code 0 for every outcome, 2 for an unusable or unknown session. `--dry-run` prints `dry run — nothing was
written` first and writes nothing at all — no entry, inbox line, `meta.json`, ledger event, claim or banner.

Each thing is announced once: the banner is claimed first by creating the empty file
`wake/<lead>/notified/<executor>.<word>` (`report-N`, `no-lead-N`, `ctx-warn`; `_no-lead` stands for a session with
no lead) with an exclusive create, so two processes racing for it post one banner between them. A banner that is
switched off (the kill switch, `notify_on_wake`, `executor_escalation`) never uses up its claim. The third-packet
note prints every time and so is not claimed.

`pilead notify` prints one line and exits 0: `banner posted via <tier>` or `banner not posted (<why>)`, `<why>` being
`kill switch`, `notify_on_wake is off`, `executor_escalation is off`, `already announced`, `lead is listening` or
`osascript failed`. `pilead notify <lead> --test` posts a test banner (it needs neither `notify_on_wake` nor a claim)
and is how to check that banners work: run it in the lead's own tab and click the banner.

Kill switch: set `PILEAD_NO_NOTIFY` (or claude-relay's `RELAY_NO_NOTIFY`) to any non-empty value and nothing is
ever posted; the test suite sets it for every test.

## Configuration

`<home>/config.json` (home is `--home`, else `$PI_LEAD_HOME`, else `~/.pi-lead`). Every key is
optional; the defaults are the behaviour without a file. Thirty-three keys are read today:

| Key | Default | Read by | Meaning |
|---|---|---|---|
| `edit_line_threshold` | `40` | extension | a registered lead's edit/write changing more lines than this is gated |
| `block_on_new_file` | `true` | extension | a lead write that creates a file is gated at any size |
| `grace_seconds` | `120` | extension | how long `/pilead:route retain` opens the gate |
| `poll_interval` | `3` | extension | fallback inbox poll, seconds (`0` turns it off) |
| `executor_layout` | `"tab"` | `pilead spawn` | `"tab"` or `"pane"` when neither `--tab` nor `--pane` is given; `"pane"` = iTerm and tmux |
| `terminal_app` | `"auto"` | every verb that opens or asks a tab, `pilead doctor` | `"iterm"`, `"tmux"` or `"auto"` — auto-detect: tmux when `$TMUX` is set, else via `$TERM_PROGRAM`; iTerm default (see [tmux and Linux](#tmux-and-linux)); `PILEAD_TERMINAL` beats it; any other value reads as `"auto"` |
| `executor_default_model` | `"sonnet"` | `pilead spawn` | the model when no `--model` is given: a tier alias or `provider/id` (see [Model and effort](#model-and-effort)) |
| `executor_fallback_model` | `null` | `pilead spawn` | an alias or `provider/id` used when the chosen alias matches no model; `null` = no fallback |
| `executor_model_ceiling` | `"opus"` | `pilead spawn` | `haiku`, `sonnet`, `opus` or `fable`: a model above it needs `--model-override`; any other text puts every `anthropic` model above it |
| `executor_default_effort` | `"high"` | `pilead spawn` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max` when neither `--effort` nor a packet `EFFORT:` line gives one; any other value means `high` |
| `context_warn_tokens` | `120000` | `pilead check` | live context at or above this is "approaching heavy" (see [Accounting](#accounting)) |
| `context_nudge_tokens` | `150000` | `pilead check`, `pilead send` | live context at or above this is "heavy"; `send` refuses without `--heavy-override` |
| `handoff_nudge_mb` | `5` | `pilead check`, `pilead send` | when live context is unknown, a session log this many MB or more is heavy |
| `stall_threshold_seconds` | `2700` | `pilead list`, `pilead check`, the board | a running executor busy this long, whose session log is as old, is `stalled` (see [Status](#status)) |
| `lead_nudge_tokens` | `300000` | `pilead list` | a lead's live context at or above this is heavy (`context_nudge_tokens` for a lead on a 200k window) |
| `usage_limit_pattern` | built-in | `pilead list`, `pilead check`, the board | a regular expression, searched case-insensitively in an error message, that marks a session `paused`; `null` means the built-in pattern `out of extra usage\|monthly usage limit reached\|insufficient_quota\|quota exceeded` |
| `bash_write_gate` | `"log"` | the bash write gate (`pilead gate`) | `"deny"`, `"log"` or `"off"` (see [Writes made through bash](#writes-made-through-bash)); any other value reads as `"log"` |
| `bash_gate_logging` | `true` | the bash write gate (`pilead gate`) | log a command an implementation verb rule names as `would_have_blocked` |
| `auto_close` | `true` | `pilead auto-close`, `pilead list`, `pilead check` | park finished executors (see [Auto-close](#auto-close)); `false` turns every sweep off |
| `auto_close_idle_minutes` | `60` | `pilead auto-close`, `pilead list`, `pilead check` | a seen report this many minutes old is parked as `idle <n>m`; `0` turns that reason off (`landed` still applies) |
| `autonomous_mode` | `false` | `pilead lead-start` | the posture every `lead-start` resets the lead to (see [Autonomous mode](#autonomous-mode)); only `true` turns it on |
| `auto_wake` | `true` | extension | `false` (the JSON value, read once at session start) turns off claiming the lead's inbox, the wakes and the turn-end message: reports stay in the inbox, `pilead list` and `pilead check` show them, and the lead's wake reads `off` (see [When the lead's turn ends](#when-the-leads-turn-ends)) |
| `surface_commits` | `false` | `pilead turn-end` | name the commits the lead made in a turn (the head file is kept up to date either way) |
| `ctx_warn_wake` | `true` | `pilead turn-end` | tell the lead when an executor of it is approaching heavy |
| `handoff_nudge` | `true` | `pilead turn-end` | tell the lead once when its own session is heavy |
| `notify_on_wake` | `true` | `pilead notify`, `pilead check`, `pilead list`, `pilead send` | post a desktop banner for a report, an executor approaching heavy and the third-packet note (see [Notifications](#notifications)); `pilead notify <lead> --test` ignores it |
| `notify_via` | `"auto"` | `pilead notify` and every other banner | `"osascript"` skips the click-to-focus tier and posts pi-lead's own title (`"terminal-notifier"` reads as `"osascript"`); any other value reads as `"auto"` |
| `executor_escalation` | `true` | `pilead escalate`, `pilead notify --no-lead` | decide what happens to a report that finds no listener, and post its banner (see [When a report finds no listener](#when-a-report-finds-no-listener)); the inbox line is written whatever it is |
| `tab_colors` | `true` | `pilead lead-start`, every tab launch, `pilead adopt`, `pilead takeover`, `pilead handoff`, `pilead tidy` | one colour per lead that its executors' tabs wear (see [Telling tabs apart](#telling-tabs-apart)); `false` paints nothing |
| `tidy_tabs` | `true` | `spawn`, `send`, `resume`, `restart`, `handoff`, `close-predecessor`, `adopt`, `takeover` | put the tabs in order after a verb that opened a tab or changed an owner (see [Telling tabs apart](#telling-tabs-apart)); `false` turns that off, `pilead tidy` still works |
| `board_live` | `false` | `pilead board`, and the verbs that keep a live board fresh | `true` keeps a live board on (see [Board](#board)): `pilead board` renders a live page and its data file, and the verbs rewrite them |
| `board_refresh_seconds` | `10` | `pilead board`, and the verbs that keep a live board fresh | how often a live page reloads itself, seconds; a whole number ≥ 1 |
| `signoff_paths` | `[]` | `pilead verify --for-autocommit` | path markers (substrings of a staged path) that add to the built-in sign-off markers of condition 4; entries that are not non-empty strings are dropped, and a value that is not a list means none |

Ranges — a number outside its key's range is ignored for that key, and the default applies:

- `edit_line_threshold`: a whole number ≥ 1.
- `grace_seconds`: a number ≥ 1.
- `poll_interval`: exactly `0` (no poll), or a number ≥ 1.
- `context_warn_tokens`: a whole number ≥ 1.
- `context_nudge_tokens`: a whole number ≥ 1.
- `handoff_nudge_mb`: a number > 0.
- `stall_threshold_seconds`: a number ≥ 60.
- `lead_nudge_tokens`: a whole number ≥ 1.
- `auto_close_idle_minutes`: a number ≥ 0.
- `board_refresh_seconds`: a whole number ≥ 1.
- `usage_limit_pattern`: a string that is a valid regular expression, or `null`; anything else means the
  built-in pattern.

When `context_warn_tokens` is at or above `context_nudge_tokens`, nothing is ever "approaching" (not an
error).

Precedence, highest first: a CLI flag, an environment variable that exists today, `config.json`,
the default. The extension reads its four keys once, at session start, and `auto_wake` on its own at the same
moment; the other twenty-eight are read by the CLI only (the bash write gate also reads `edit_line_threshold`, `block_on_new_file` and `grace_seconds`). A missing or corrupt file
means the defaults; an unknown key is ignored; a value whose JSON type differs from the default's is
ignored for that key.

## Ledger

`<home>/ledger.jsonl` is an append-only event log, one JSON object per line:
`{"ts": "2026-09-26T12:00:00Z", "event": "<name>", ...fields}`. Writing it is best-effort: a
ledger that cannot be written never changes what a verb prints or its exit code.

| Event | Written by | Fields |
|---|---|---|
| `lead_started` | `pilead lead-start` | `session_id`, `project` |
| `auto_mode_on` | `pilead auto on`, `pilead lead-start` | `session_id`, `project`, `was` (the posture before: `true` or `false`), `source` (`"command"` from `pilead auto`, `"config"` from `lead-start` with `autonomous_mode: true`) — written by `pilead auto on` also when it was already on |
| `auto_mode_off` | `pilead auto off`, `pilead lead-start` | `session_id`, `project`, `was`, `source` (`"command"` from `pilead auto`; `"arm"` when `lead-start` reset an autonomous lead to off) |
| `tier_set` | `pilead tier auto\|manual\|lead`, `pilead lead-start` | `session_id`, `project`, `tier` (the posture set), `was` (the posture before: `auto`, `manual` or `lead`, `null` when it could not be read), `source` (`"command"` from `pilead tier`, also when nothing changed; `"arm"` when `lead-start` reset a `manual` or `lead` posture to `auto`) |
| `spawned` | `pilead spawn`, `pilead restart`, `pilead send --rotate`/`--upgrade` | `session_id`, `lead`, `worktree`, `topic`, `model`, `tab_label`, `source` |
| `launch_options` | `pilead spawn`, `pilead restart`, `pilead send --rotate`/`--upgrade` | `session_id`, `effort`, `effort_source` (`"flag"`, `"packet"`, `"config"`, or `"inherited"` for a restart's or rotation's successor that took the old session's stored effort — `"config"` when it had none), `scope`, `keep` |
| `model_fallback` | `pilead spawn` | `session_id`, `asked` (the alias that matched no model), `model` (the fallback launched) |
| `model_ceiling_override` | `pilead spawn --model-override` | `session_id`, `model`, `ceiling`, `reason` — a model above the ceiling was launched anyway |
| `model_from_tier` | `pilead spawn`, `pilead send --rotate` | `session_id` (the new session), `lead`, `lead_model` (the lead's own `provider/id`), `model` (what launched) — only when the `lead` tier posture chose the model |
| `packet_sent` | `pilead send`, `pilead queue --deliver` | `session_id`, `packet`, `source`, `via` (`"inbox"` or `"relaunch"`); `queue_id` too when the packet was a queued one (`source` is then the file that was queued); `via: "recovered"` when a delivery died after numbering the packet and the next one recorded it instead of sending it again |
| `packet_queued` | `pilead send --when-idle`; a queue move | `session_id`, `queue_id`, `source`, and `moved_from` (`{session_id, queue_id}`) when the item was moved here from another session's queue — the packet was queued, not sent |
| `queue_delivered` | `pilead queue --deliver` | `session_id`, `queue_id`, `packet` (the number it was sent as), `trigger` (`"manual"`, `"check"` or `"settle"`), `remaining` (how many are still queued), `recovered` (`true` when an interrupted delivery's send was found in the ledger, or had numbered its packet, and was not repeated) — after its `packet_sent` |
| `queue_delivery_failed` | `pilead queue --deliver` | `session_id`, `queue_id`, `trigger`, `error` (one line, at most 300 characters) — the head stays queued; written once per distinct error |
| `queue_cancelled` | `pilead queue --cancel` | `session_id`, `queue_id`, `source` — one per packet removed |
| `queue_moved` | `pilead send --rotate`/`--upgrade`, `pilead restart` | `session_id` (the old session), `successor`, `queue_id` (its id there), `new_queue_id` (its id in the successor's queue, whose own `packet_queued` precedes it) — one per packet moved |
| `queue_deduped` | `pilead send --rotate`/`--upgrade`, `pilead restart` | `session_id`, `successor`, `queue_id`, `source` — a queued packet that was the packet being sent, dropped instead of moved |
| `queue_cancelled_on_park` | auto-close | `session_id`, `queue_id`, `source`, `body_path`, `action`, `reason`, `trigger` — one per packet of a queue stuck on heaviness, emptied as its session was parked (the body stays) |
| `closed` | `pilead close`, `pilead restart`, auto-close | `session_id`, `reason` (`"manual close"`; `"restart"` for the session `restart` replaces; `"auto-close (<reason>)"` for a park) |
| `resumed` | `pilead resume` | `session_id`, `packet` (the current packet), `effort` (the stored effort, or `null`) |
| `lead_resumed` | `pilead resume` of a lead | `session_id`, `project` |
| `restarted` | `pilead restart` | `session_id`, `successor`, `packet` (the packet run again), `model` (the successor's) |
| `superseded` | `pilead close --supersede`, `pilead retire` | `session_id`, `superseded_by` — written in place of `closed` |
| `retired` | `pilead retire`, `pilead send --rotate`/`--upgrade` | `session_id`, `seed` (the seed's path), `packets` (how many it indexes), `forced` — after close's `superseded` |
| `seed_inherited` | `pilead spawn --seed`, `pilead send --rotate`/`--upgrade` | `session_id` (the new session), `seed` (the seed's path) |
| `adopted` | `pilead adopt`; `pilead send`, `pilead resume` (an executor) and `pilead restart` from a lead (a claim) | `session_id`, `from_lead` (what `meta.json` named, or `null`), `to_lead`, `forced`, `reason` (`"adopt"`, `"claim"`, `"handoff"`, `"takeover"` or `"fallback"`) |
| `rotated` | `pilead send --rotate`/`--upgrade` | `session_id`, `successor`, `model` (the successor's), `upgrade`, `from_tier` (the old model's tier, or `null`) |
| `lead_stepped_down` | `pilead stop`, `pilead close --self` | `session_id`, `project` |
| `keep` | `pilead keep` | `session_id`, `keep` (`true` or `false`), `keep_by` (the calling lead, or `null`) |
| `pruned` | `pilead prune` | `session_id`, `status` (the stored status, `closed` or `dead`) — one per session removed |
| `lead_pruned` | `pilead prune` | `session_id`, `project` — one per lead record removed |
| `plan_add` | `pilead plan add` | `session_id` (the lead), `n`, `packet` (as stored), `target`, `model` |
| `plan_rm` | `pilead plan rm` | `session_id`, `n`, `packet` |
| `plan_done` | `pilead plan done` | `session_id`, `n`, `packet` |
| `plan_bind` | `pilead plan bind` | `session_id`, `n`, `executor`, `packet` (the packet number) |
| `auto_closed` | `pilead auto-close`, `pilead list`, `pilead check`, `pilead verify --diff-reviewed` | `session_id`, `action` (`"close"` or `"retire"`), `reason` (`"landed"`, `"idle <n>m"`, or `"reviewed"` for the park after a review), `trigger` (`"manual"`, `"list"`, `"check"` or `"verify"`) — one per park, after close's `closed` (or retire's `superseded` and `retired`) |
| `auto_close_error` | `pilead auto-close`, `pilead list`, `pilead check`, `pilead verify --diff-reviewed` | `session_id`, `error` (at most 200 characters), `trigger` — handling that session failed; it was left as it was and the sweep went on |
| `report_verify` | `pilead verify` | `session_id`, `packet`, `verdict`, `rerun`, `mismatches`, `caller` (`"lead"`, `"self"`, `"other"` or `"unknown"`: who ran it, from `$PI_SESSION_ID`; see [Auto-close](#auto-close)) |
| `report_looked_at` | `pilead check`, `diff`, `close`, `retire` (not `check --refs-only`) | `session_id`, `packet`, `caller` (`"lead"` or `"unknown"`), `via` (the verb) — once, when the lead looked at the current packet's report and nothing in the ledger counted as a look yet |
| `surfaced_stamp_declined` | `pilead check`, `diff`, `close`, `retire`, run by the executor itself | `session_id`, `packet`, `owner_lead`, `caller` (`"self"`), `via` — once per session and packet: the executor's own look was not counted as the lead's |
| `report_reviewed` | `pilead verify --diff-reviewed`, `pilead review` | `session_id`, `packet`, `findings` (the stored review file, or `"inline"`), from `pilead verify` also `caller` (as `report_verify`); from `pilead review` (a VALID review only) also `reviewer: "headless"`, `model`, `tree` (the staged tree reviewed), `recommendation` (`"commit"`, `"fix-list"` or `"send back"`), `blockers`, `should_fix`, `notes` (counts) |
| `review_failed` | `pilead review` | `session_id`, `packet`, `outcome` (the INVALID or INCOMPLETE line), `model`, `tree` (the staged tree at the start) — the review does not count |
| `auto_commit` | `pilead verify --for-autocommit` | `session_id`, `packet`, `cleared`, `reason` (`null` when cleared, else the slug: a condition's, `refs-moved` / `refs-not-compared`, or for a review file from `pilead review` `review-unreadable`, `review-is-of-another-packet`, `review-not-valid`, `review-tree-not-compared`, `review-is-of-another-tree`, `review-has-open-findings`, `review-does-not-recommend-commit`) |
| `session_diagnosed` | `pilead review --why` | `session_id`, `model`, `outcome` (`ANSWERED…`, `INVALID (…)` or `INCOMPLETE (…)`) |
| `refs_snapshot` | `pilead spawn`, `pilead send` | `session_id`, `packet`, `head`, `branch`, `refs` (how many) |
| `refs_snapshot_failed` | `pilead spawn`, `pilead send` | `session_id`, `packet`, `reason` — the snapshot could not be taken; the spawn/send still happened |
| `refs_moved` | `pilead check`, `pilead verify`, the extension's wake | `session_id`, `packet`, `changes` (the list `check` prints) — once per distinct move; a move seen after `refs_accepted` is logged again |
| `refs_accepted` | `pilead refs accept` | `session_id`, `packet`, `reason`, `head` |
| `reported` | extension (executor) | `session_id`, `packet` — once per report version, when the lead is notified |
| `retained` | extension (lead) | `session_id`, `reason` — `/pilead:route retain` |
| `banner_fallback` | `pilead notify` and every other banner | `session_id` (the lead), `reason` (`no-tty`, `tty-lookup-failed` or `write-failed`) — the click-to-focus tier was tried and did not post, so the banner went out through `osascript` |
| `handoff_nudged` | `pilead turn-end` | `session_id` (the lead), `mb` (its log's size to one decimal, or `null`), `tokens` (its live context, or `null`) — the lead was told once that its session is heavy |
| `turn_end_suppressed` | extension (lead) | `session_id`, `lines` — a turn-end message that came less than 60 seconds after the last one, not sent |
| `turn_end_failed` | extension (lead) | `session_id`, `lines`, `error` (first line, at most 200 characters) — sending a turn-end message threw |
| `wake_delivered` | extension (lead) | `session_id`, `executor`, `packet` (the number in the report's file name, `null` when it has none), `report` — a wake was handed to pi; written before its claim file loses the line |
| `wake_skipped_landed` | extension (lead) | `session_id` (the lead), `executor`, `packet`, `report`, `reason` (`"landed"`), `proven` (`true`) — the report's work is provably committed already, so no wake was sent; the line left the inbox, `wake_delivered` was not written |
| `escalation_resolved` | `pilead escalate` (the executor's extension) | `session_id`, `packet`, `owner_lead` — the lead had the report when its escalation was first decided |
| `escalation_push_failed` | `pilead escalate` | `session_id`, `packet`, `owner_lead`, `error` (the first line, at most 200 characters) — the report's inbox line could not be appended |
| `owner_fallback` | `pilead escalate` | `session_id`, `packet`, `old_owner`, `new_owner`, `project` — a session whose owner is gone was handed to the one live lead of its project (after `adopted` with reason `"fallback"`) |
| `lead_handoff` | `pilead handoff` | `from_lead`, `to_lead` (the successor), `project`, `memo` (the memo's path), `model` (the successor's) |
| `predecessor_closed` | `pilead close-predecessor` | `session_id` (the successor), `predecessor`, `tab_closed` — `false` for a refusal over the caller's own tab or a handle that does not resolve, and for a close that did not take |
| `tidy` | `pilead tidy` (also run after a verb, see [Telling tabs apart](#telling-tabs-apart)) | `session_id` (`--lead`, else the calling lead, else `null`), `attempts` (1, or 2 when a connection failure was asked again), `reason`, `moved`, `windows`, `missing` (the numbers of the reason, 0 when it names none) — the tabs were put in order |
| `tidy_skipped` | `pilead tidy` | `session_id`, `attempts`, `reason` — the tidy reached the terminal and could not be applied; none for a dry run, with `RELAY_NO_TIDY` set, or with no lead to order |
| `lead_moved` | `pilead takeover` (also run by the lead's extension after `/new` or `/fork`) | `from_lead`, `to_lead`, `project`, `reason` (`"takeover"`), `forced`, `sessions` (how many were adopted), `wake_lines` (how many pending wake lines moved) |
| `wake_retry_capped` | extension (lead) | `session_id`, `executor`, `report`, `attempts` (3), `error` (first line, at most 200 characters) — the third send of one inbox line failed; this lead runtime sends it no more, and it stays in the inbox |
| `wake_recovered` | extension (lead) | `session_id`, `claim` (the orphaned claim file's name), `wakes` (how many `reported` lines it held) — put back in the inbox by a drain, then the claim file deleted |
| `wake_putback_failed` | extension (lead) | `session_id`, `wakes`, `error` (first line, at most 200 characters) — nothing was deleted; the claim files stay on disk and a later drain recovers them |
| `packet_recovered` | extension (executor) | `session_id`, `claim` — an orphaned claim of a packet was put back ahead of the executor's inbox, then deleted |
| `packet_putback_failed` | extension (executor) | `session_id`, `error` (first line, at most 200 characters) — nothing was deleted; the next drain tries again |
| `blocked` | extension (lead), `pilead gate --record` | `session_id`, `file_path`, `lines`, `new_file` — the routing gate denied an edit/write; for a control file also `control` (`"settings"`, `"ledger"` or `"grace file"`); from the bash write gate also `vector: "bash"` (`lines` is `null` when unknown) and, for a control file, `control` (`"settings"`, `"ledger"`, `"grace file"`, `"lead record"`, `"home"` or `"leads"`); `parsed: false` for a command the gate could not read |
| `gate_unread` | `pilead gate --record` | `session_id`, `command_sha` (sha256 of the command), `parsed: false` — the bash write gate could not read a command and allowed it |
| `would_have_blocked` | `pilead gate --record` | `session_id`, `command` (at most 1000 characters), `rule`; for a hit the gate only logged also `vector: "bash"`, `file_path`, `lines` (or `null` when unknown), `new_file` (`rule` is then the verb rule, else `"bash-write"`) |
| `heavy_override` | `pilead send --heavy-override` | `session_id`, `packet`, `tokens` (live context, or `null`), `mb` (log size, or `null`), `reason` — a heavy session was sent to anyway |
| `usage_snapshot` | `pilead spawn`, `pilead send`, `pilead check` | `session_id`, `packet`, `at` (`"sent"` or `"reported"`), `usage` (`{prompt, output, requests}` billed so far, or `null` when unknown) — `spawn` writes zeros; `check` writes `"reported"` once per packet, when it first finds that packet's report, with `caller` (as `report_verify`) |
| `stalled` | `pilead list`, `pilead check`, the board | `session_id`, `packet`, `reason` — the status became `stalled` |
| `paused` | `pilead list`, `pilead check`, the board | `session_id`, `packet`, `reason` (the usage-limit message) — the status became `paused` |
| `dead` | `pilead list`, `pilead check`, the board | `session_id`, `packet`, `reason` — the status became `dead` |

`source` is the absolute path of the packet file named on the command line; `packet` is a number.

## Troubleshooting

- **First, `pilead doctor`.** It checks the Python and git versions, the package's files, every verb, `pilead` on PATH, the terminal
  backend, the state dir, the config, the model list, the ledger, every open session's git shim, and — by starting one
  throwaway `pi` — that the extension, its 38 commands and its skills load. `--offline` skips the checks that start pi, `--quick`
  skips the slow ones; run it after every update. See [Doctor](#doctor).
- **`pilead check --all` and `pilead list` tell you the real state** (busy, reported, stalled, dead) — trust them over how a tab
  looks. `stalled` means go look at that tab: an executor stuck on a prompt it cannot answer keeps its process alive but stops
  writing its session log, so it reads `stalled` once `stall_threshold_seconds` passes, and `pilead restart` accepts it without
  `--force`.
- **Tab died mid-build?** `pilead resume <sid>` reopens the same conversation with its context and staged work intact;
  `pilead restart <sid>` runs its packet again as a new conversation. To send it more work, `pilead send <sid> <packet>` revives
  a closed or dead session by itself.
- **An executor finished but the lead never woke?** Look at the WAKE column of `pilead list`. `STALE`: the lead's pi stopped, and
  its reports wait in the inbox until pi runs there again (`pilead resume <sid>`). `STUCK`: a wake was claimed and not delivered;
  the next drain retries it, and if it stays, restart pi in that lead's tab. `off`: no pi with the extension is watching the inbox
  (`auto_wake: false`, or the extension is not loaded). `pilead report <sid>` pulls a report by hand. After a handoff or a
  takeover, an executor still owned by a retired lead wakes nobody: `pilead list` names it (`⚠ orphan`) and `pilead adopt <sid>`
  re-points it.
- **After updating pi-lead, check that running leads picked it up.** A running pi keeps the extension it loaded. `pilead list`
  shows each lead's VER and, for a lead on an older one, an `⚠ old extension` footnote: run `/reload` in that pi.
- **A lead tab under tmux whose pi exited still has its pane** (a pane outlives its program). pi-lead reads it as not live: LIVE
  shows `unreachable` (active in the last 15 minutes) or `ghost`, never `live`, and `pilead resume <sid>` reopens the lead in a new
  window with its conversation. Close the old pane by hand.
- **`pilead doctor` on Linux outside tmux** still passes its `terminal backend` row, but the row's detail says `on Linux pi-lead
  needs tmux: run it inside tmux or set PILEAD_TERMINAL=tmux`. Without a terminal to drive, no verb can open a tab.
- **No `xdg-open` on Linux:** `--open` (on `diff`, `board`) prints the page's `file://` address instead of opening it.
- **No `notify-send` on Linux:** there is no desktop banner. A tmux lead still gets its status-line message.
- **`pilead tidy` does nothing under tmux:** there is no tab order to keep there (`pilead tidy` orders iTerm2 tabs only).
- **A stale row in the LEADS table with an old LAST ACTIVE** is a dead lead (tab closed or crashed without `pilead stop`);
  `pilead prune` clears it once it is older than `--days`. A lead you are actively using is never pruned.
- **Model note:** `/pilead:mode` starts by running `pilead lineup` and repeating its `Model check:` line; if it says `STOP:`,
  switch the lead to a stronger model with `/model` first.

## Non-goals / not yet

- Terminal.app only "opens a window" — no parity with the iTerm backend.
- Tab colours, Linear and autonomous mode are not ported.
- No live model calls in the tests, ever.

## Relation to claude-relay

pi-lead is a port. `vendor/relay/` holds files copied from claude-relay (same author, MIT); five
of them were adapted for pi. What was copied, what changed and why is in [VENDOR.md](../VENDOR.md).
Pi-specific edits stay here and do not flow back.

Manual live check: [docs/manual-checklist.md](manual-checklist.md). Tests: `npm test`.
Every test runs with a recording `osascript` and the fake `pi` first on PATH, set in `tests/conftest.py`, so no
test can open a real tab or start the real pi; a test file needs a fixture of its own only to change what the
stub answers.

## Releasing

1. Bump `version` in package.json (required — an unchanged version makes `pi update` a silent no-op).
2. `npm run check` (tests + typecheck), then `npm pack --dry-run` to inspect the file list.
3. `npm publish --access public`; then `pi update pi-lead` on a second machine and confirm the installed version.

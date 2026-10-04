# Manual live check

The one checklist a human runs live before a release: the loop in six steps, then one block per area. Everything runs in iTerm2 on a Mac, except the tmux block (tmux on a Mac) and the Ubuntu block (a Linux box over SSH, in tmux).
Tests cannot run any of it: each live check below was left unverified by the batch that built it.
Uses a scratch git repo so nothing real is touched. Replace `<sid>` with the id `spawn` prints.

Every fenced `pilead …` command below that mentions `$PI_SESSION_ID` is meant to be pasted to the
**lead as a prompt** (`Run: pilead …`) — pi sets that variable only inside the lead's bash tool, so
it is unset in your own shell.

Setup:

```bash
mkdir -p /tmp/pl-demo && cd /tmp/pl-demo && git init -q
cat > /tmp/pl-demo-packet.md <<'EOF'
GOAL: add hello.txt containing hi, stage it, write the report.
Attempt `git commit -m test` once to observe the block, and paste what it prints.
Then attempt `python3 -c "import subprocess; subprocess.run(['git','commit','-m','t'])"` once and paste what it prints.
EOF
pi -e <your pi-lead checkout>/extensions/pi-lead.ts     # this tab is the lead
```

In the lead tab, run `/pilead:status`, then have the lead run
`pilead lead-start "$PI_SESSION_ID" --project demo`.

1. **Spawn opens a tab next to the lead's.** A new `[Exec] <sid>` tab opens immediately to the
   right of the lead's tab and spawn's last line says `placement=adjacent` (`end-of-bar (<reason>)`
   means the iTerm Python API was unavailable — see README "Tab placement").
   ```bash
   pilead spawn /tmp/pl-demo demo /tmp/pl-demo-packet.md --model deepseek/deepseek-flash --lead "$PI_SESSION_ID"
   ```
2. **`git commit` is blocked, by both layers.** The demo packet told the executor to attempt
   `git commit -m test` once to observe the block. Its report (and the tab) must show the refusal line
   `pi-lead executor: stage your work and report; the lead commits.` The packet's second attempt, the
   `python3 -c "…subprocess.run(['git','commit','-m','t'])"` one-liner, passes the text fence (it is
   just `python3`). The report must show the git shim's refusal for it:
   `pi-lead executor: stage your work and report; the lead commits. (blocked by git shim: git commit -m t)`.
   `git -C /tmp/pl-demo log` must still show no commits. Then, from the lead:
   ```bash
   pilead check <sid>
   ```
3. **Send lands in the running tab.**
   ```bash
   printf 'GOAL: also add world.txt containing there, stage it, write the report.\n' > /tmp/pl-demo-packet2.md
   pilead send <sid> /tmp/pl-demo-packet2.md
   ```
   The text appears as a new user message in the executor's tab without you touching it.
4. **Report flips status.** When the executor writes `report-NNNN.md`:
   ```bash
   pilead list
   ```
   The row shows `reported` (it was `busy` while working).
5. **Lead wakes.** The lead tab shows `🚦 [pi-lead] executor <sid> reported: <path>` and starts a
   turn on its own. Then:
   ```bash
   pilead verify <sid>
   ```
   After the lead commits the staged work, `git log -1 --format='%an <%ae>'` in `/tmp/pl-demo` must
   show the repo's own identity (`git config user.name/user.email`), not `pi-lead`.
6. **Close shuts the tab.**
   ```bash
   pilead close <sid>
   ```
   The executor tab closes and `pilead check <sid>` prints `closed`.


## The wake (two live checks; tests cannot run these)

7. **A report's wake shows its first line and the size of its diff.** With a lead running and an executor that has
   staged something, let the executor report. The wake shows `🚦 [pi-lead] executor <sid> reported: <path> — <refs line>
   (diff: <n> files +<added>/-<removed>)`, then the report's first line (leading `#` removed) on the line under it.
8. **A late line for a landed packet wakes nobody.** After the lead commits that packet's staged work and runs
   `pilead refs accept <sid>`, append a copy of that report's `🚦 …` line to the lead's inbox
   (`<home>/leads/<lead sid>.inbox.md`). The lead is not woken, the line leaves the inbox, and the ledger has
   `wake_skipped_landed` for it.

## Resume (three live checks; tests cannot run these)

9. **A closed executor resumes with its conversation.** After step 6, from the lead:
   ```bash
   pilead resume <sid>
   ```
   A new `[Exec] <sid>` tab opens and shows the executor's EARLIER conversation (the messages of steps
   1–5), not an empty one. If its last packet had no report, its first new message is the resume nudge
   naming that packet and its report path. `pilead check <sid>` prints `busy` (or `reported`).
10. **A lead resumes after its tab closes.** Close the lead's tab (quit pi there and close the tab), then
   from any other shell:
   ```bash
   pilead resume <lead_sid>
   ```
   A new `[Lead] demo` tab opens in the lead's cwd with the lead's earlier conversation, and the message
   `You are the resumed lead for project 'demo'. First run: pilead lead-start "$PI_SESSION_ID" …` arrives
   as its first prompt; the lead runs `pilead lead-start` and `pilead list`.
11. **A resumed lead is woken once per report.** With pi-lead installed as a package (`pi install`), the
   resumed lead's launch line adds `-e <extension>` to a pi that may already load the package. Send the
   executor a packet and let it report: the resumed lead must show ONE `🚦 [pi-lead] executor <sid>
   reported: …` line and start ONE turn, not two.

## Queue (two live checks; tests cannot run these)

12. **An executor starts a queued packet by itself.** Send the executor a packet that takes a while, and
    while it works queue a second one:
    ```bash
    pilead send <sid> /tmp/pl-demo-packet2.md --when-idle
    ```
    It prints `queued <sid> #1 — …`. Do nothing else. When the executor writes its report, the lead is
    woken ONCE (`🚦 [pi-lead] executor <sid> reported: …`), and the executor then starts the queued
    packet by itself (`pilead check <sid>` shows the next packet number and `busy`; the ledger has
    `queue_delivered` with `"trigger": "settle"`). When that one is reported, the lead is woken once more.
13. **Two queued packets go one per finished packet, in order.** As step 10, with two packets queued
    (`--when-idle` twice). After the running packet's report the executor starts the first queued one only;
    after that one's report it starts the second. `pilead queue <sid>` shows one, then none waiting.

## Ownership (two live checks; tests cannot run these)

14. **A running executor adopted by a second lead wakes only the new lead.** With an executor working a
    packet for lead A, open a second pi session, register it (`pilead lead-start "$PI_SESSION_ID" --project
    demo2`) and, from its bash tool, run `pilead adopt <sid> --force` (A is live, so `--force` is needed). It
    prints `adopted <sid> from <A> (forced)`. When the executor writes its report, the SECOND lead shows ONE
    `🚦 [pi-lead] executor <sid> reported: …` line and starts one turn; lead A shows nothing for it.
15. **A send to an orphan adopts it and wakes the sender.** Stop lead A (`pilead stop <A>` from any shell),
    then from the second lead: `pilead send <sid> /tmp/pl-demo-packet2.md` (after the current packet
    reported). The first line is `adopted <sid> from <A> (no longer a registered lead)`, then `sent <sid>
    packet …`; when that packet's report lands, the second lead is woken once.

## Escalation (three live checks; tests cannot run these)

Each with an executor spawned `--model dsflash`, so the run is cheap.

16. **A report whose lead is not listening waits, and wakes the lead once it is back.** Quit pi in the lead's
    tab (the tab stays open). When the executor writes its report, a banner says `its lead is not listening
    (off) — the report waits in the inbox`. Then `pilead resume <lead>`: the resumed lead is woken for that
    report exactly once.
17. **A report whose owner is gone wakes the one live lead of its project.** With one other live lead of the
    same project registered, remove the owner's record (`pilead stop <owner>`). When the executor writes its
    report, the other lead is woken for it, and `pilead list` shows the session under that lead.
18. **Two live leads of the project: nobody is chosen.** As 15, with two live leads of that project. No lead is
    woken; a banner says `no lead is registered for it — pilead check <sid>`.

## The lead's session changes (three live checks; tests cannot run these)

19. **`/new` in a lead's tab moves the role.** In a registered lead's tab with an executor working, type `/new` (and, again, `/fork`).
    The notice `pi-lead: lead role moved from <old> to this session (1 executor(s))` shows. When the executor
    reports, the NEW session is woken once; nothing else shows the report.
20. **`/resume` moves nothing.** In a registered lead's tab, `/resume` another, unregistered session. The notice
    `pi-lead: the session you left (<old>) is still the registered lead; …` shows, and `pilead list` still shows
    the old lead with its executors.
21. **A closed lead is taken over from a new session.** Close a lead's tab (its executors keep working), open a
    new pi session and ask it to `Run: pilead takeover <old>` (once the old lead reads `ghost` in `pilead list`;
    before that `--force` is needed). Then `Run: pilead lead-start "$PI_SESSION_ID" --project demo`. The executors'
    next reports wake the new session.

## Handoff (five live checks; tests cannot run these)

Use `--model dsflash` for the successor. In a registered lead's tab with one executor working, write a short memo
(`/tmp/pl-demo-memo.md`) and ask the lead to `Run: pilead handoff /tmp/pl-demo-memo.md --model dsflash`.

22. **The successor's tab opens next to the lead's, on the model named, and its first prompt is the message.** A new
    tab titled `[Lead] demo ·<4 characters>` opens directly after the lead's tab; its pi runs on the dsflash model
    (pi's footer names it), and its first turn answers `You are the successor lead for project 'demo', registered by
    pilead handoff. Read … and follow its SUCCESSOR AFTERCARE section first.`
23. **Its `$PI_SESSION_ID` is the registered id.** In the successor ask `Run: echo "$PI_SESSION_ID"`: it is the sid
    the handoff printed (`handoff complete — successor <sid> …`), and `pilead list` shows that lead.
24. **The next executor report wakes the successor once and the outgoing lead not at all.** When the executor
    reports, the successor gets one wake; the outgoing lead's tab shows nothing.
25. **The outgoing lead's tab is retitled `[ex-Lead] …`.** Right after the handoff, the old tab's title reads
    `[ex-Lead] demo`.
26. **`pilead close-predecessor` closes that tab and no other, with no dialog.** In the successor, after saying yes,
    `Run: pilead close-predecessor`: `closed the outgoing lead's tab (<sid>)`; only the `[ex-Lead]` tab closes, and
    iTerm asks no "close a running process?" question.

## The edit gate and the bash gate (five live checks; tests cannot run these)

27. **A large write passes in a pi that is not a registered lead.** In a scratch directory run
    `pi -e <path to extensions/pi-lead.ts>` (no `pilead lead-start`) and ask it to write a new file of about 80 lines.
    The write goes through.
28. **The same write is blocked once the session is a registered lead.** In that pi, have it run
    `pilead lead-start "$PI_SESSION_ID" --project demo`, then ask again for the 80-line write. It is refused with
    `pi-lead lead gate: this write of <file> (new file, <n> lines) is implementation work — delegate this …`.
29. **`/pilead:route` shows `2 min`.** In pi's command list (type `/pilead:`) the `route` entry reads
    `Open the lead routing gate for 2 min: /pilead:route retain "<reason>"`.
30. **`pilead route retain` lets the lead's next large write through.** From the lead's bash tool run
    ```bash
    pilead route retain "demo edit"
    ```
    then `/pilead:status` shows `route grace: open`, and the 80-line write from 28 now goes through; after two minutes
    `/pilead:status` shows `route grace: closed` and the same write is refused again.
31. **A bash command that writes a control file is refused.** In the registered lead's bash tool run
    `touch "$HOME/.pi-lead/leads/x.route"` (use your `PI_LEAD_HOME` if set): the call is refused, naming the control
    file, and `cat "$HOME/.pi-lead/config.json"` (a plain read) still runs.

## A second pi and the reviewer role (three live checks; tests cannot run these)

Use `--model dsflash`. In a registered lead's tab, with its health file beating (`pilead list` shows `WAKE ok`) and
an executor's report waiting in the lead's inbox (`pilead list` shows the 🚦 footnote):

32. **A print run from the lead's own bash tool leaves the lead's work to the lead.** Ask the lead to
    `Run: pi --model dsflash -p "say hi"`. It prints `this is a print run, so it does no lead work` (or the
    `started from inside a pi-lead session` notice); the inbox line and `wake/<lead sid>/health.json` are not touched by
    it, and the lead is woken by the report once — not zero times, not twice. Run the same from another shell with
    `PI_LEAD_SID=<the lead's id>` set: the same result.
33. **A reviewer refuses a command.** In a scratch directory, from your own shell:
    `PI_LEAD_ROLE=reviewer pi --model dsflash -e <path to extensions/pi-lead.ts> "Run: ls"` — the bash call is
    refused with `pi-lead reviewer: this session only reads — it runs no command and changes no file.`, and a
    `read` of a file in that directory works.
34. **A reviewer refuses a write.** In the same reviewer pi ask for `Write hello.txt containing hi`: the write is
    refused with the same `pi-lead reviewer: …` line and no `hello.txt` exists.

## Banners and the lead's turn (six live checks; tests cannot run these)

In a registered lead's tab on a Mac:

35. **A test banner appears, and a click focuses the lead's tab.** `Run: pilead notify "$PI_SESSION_ID" --test` shows a
    macOS banner; clicking it brings that iTerm tab to the front.
36. **A live report's wake shows one banner.** When an executor reports, exactly one banner shows for it.
37. **`notify_via: "osascript"` gives pi-lead's own title.** With that value in `<home>/config.json` and pi restarted,
    the banner of 35 carries pi-lead's title and a click does nothing.
38. **A turn-end message starts one turn and no second one.** After the lead's turn ends with an executor reported
    and not yet looked at, the lead is told once and starts one turn; no further turn begins by itself.
39. **`surface_commits` names the lead's own commits.** With `"surface_commits": true`, after a turn in which the
    lead made a commit, the lead sees `📝 you made 1 commit(s) this turn in <cwd>:` and the commit's line.
40. **`auto_wake: false` leaves a report waiting.** With `"auto_wake": false` and pi restarted, a report does not
    wake the lead; `pilead list` shows it waiting.

## A kill loses nothing (four live checks; tests cannot run these)

41. **A killed lead gets its wake back.** `kill -9` the lead's pi while an executor's report is being delivered, then
    start pi again in that tab with the same session id (`pilead resume <lead sid>`): the wake arrives (it may arrive
    twice).
42. **A killed executor still gets its packet.** `kill -9` an executor's pi right after `pilead send` to it, then
    `pilead resume <sid>`: the packet arrives in the resumed tab.
43. **The footers show the roles.** A live lead's footer shows `🚦 <project>` and its busy and reported executors; a
    live executor's footer shows its packet and status.
44. **A stopped lead shows `STALE` within two minutes.** Stop the lead's pi process; `pilead list` shows `STALE` for
    it within two minutes (90 seconds after the last beat).

## `list`, names and registering (six live checks; tests cannot run these)

45. **`pilead list` shows a VER column for each lead.** In a live lead `Run: pilead list`: the LEADS rows end in the
    pi-lead version each runs.
46. **The old-extension footnote appears after an upgrade and clears after `/reload`.** Install a newer pi-lead while a
    lead's pi is open: `pilead list` shows `⚠ old extension: <names> — … run /reload in that pi`; run `/reload` there
    and the footnote is gone.
47. **An executor's own look does not unblock auto-close.** After an executor reports, have the executor run
    `pilead check <its sid>` and `pilead diff <its sid>`; the lead's `pilead auto-close --dry-run` still does not list
    it as seen. After the lead runs `pilead verify <sid>` it does.
48. **A project name or an 8-character id prefix works as a name.** In a live lead `Run: pilead check <first 8
    characters of an executor's sid>` and `Run: pilead list --lead demo`: the first prints that executor's check, the
    second lists only that lead's executors (`pilead list --lead demo --all` lists the rest too).
49. **`pilead report` prints a report in colour.** In your own terminal `pilead report <sid>` prints the executor's
    whole report, coloured.
50. **`--no-rename` leaves the tab as it was.** In a tab you titled by hand run
    `pilead lead-start "$PI_SESSION_ID" --project demo --no-rename`: the tab's title and colour do not change, and
    `pilead list` shows the lead.

## Tab names, colours, focus and tidy (nine live checks; tests cannot run these)

51. **A lead's tab is named `π - [Lead] <project> - <directory>`.** Right after `pilead lead-start`, the iTerm tab title
    reads so.
52. **An executor's tab is named `π - [Exec] <sid> - <directory>` and keeps it while it works.** After a spawn, the
    tab reads so, and still does after the executor has worked for a minute.
53. **The lead's tab and its executors' tabs share one colour, and pi does not reset it.** The tab colours match and
    stay so after the executor's first turn.
54. **`pilead focus` brings the right tab to the front, in a second window too.** With a lead and an executor in a
    second iTerm window, `pilead focus <executor sid>` and `pilead focus demo` each raise the right tab and its window.
55. **A handoff's successor has the predecessor's colour.** After `pilead handoff`, the successor's tab shows the
    colour the outgoing lead's tab had.
56. **`pilead tidy --dry-run` names what would move, and `pilead tidy` moves it, in two windows at once.** With
    iTerm2's Python API on, tabs out of order in two windows: `pilead tidy --dry-run` names the tabs that would move and
    moves nothing; `pilead tidy` then puts each lead's tab first, its executors after it, in both windows.
57. **A spawn is followed by a tidy that prints no line.** `pilead spawn …` (as in step 1) prints no `tab tidy` line.
58. **With the API off, a spawn prints one `· tab tidy skipped (…)` line and still succeeds.** Turn the iTerm2 Python API
    off, spawn: the one line shows, the executor's tab opens.
59. **After iTerm2 restarts with its windows restored, `pilead tidy` still finds the tabs.** Quit iTerm2 and reopen it
    with windows restored: `pilead tidy --dry-run` lists the leads' and executors' tabs by name.

## The board (five live checks; tests cannot run these)

60. **`pilead board --open` shows every lead's colour, its wake, and each executor's tokens and context.** With two
    real leads and a working executor, run `pilead board --open`: the page shows each lead in its own colour with its
    wake state, and the executor with its tokens and context.
61. **A copied `Focus tab`, `Resume` or `Retire` command does what it says.** Paste each into the matching session's
    shell (`Retire` only for a heavy one): the tab is focused, the closed session comes back, the heavy one is retired.
62. **A live board reloads itself.** `pilead board --live --open`, then let an executor report: the open page shows the
    change within one refresh, with nothing run by hand.
63. **The badge turns red when the lead is left alone.** With the page open, stop every verb for more than three
    refreshes: the badge reads `upd stale` in red.
64. **`pilead board --live off` stops the rewriting.** After it, the page's data file is gone and a report does not
    change the open page.

## tmux (five live checks; tests cannot run these)

Tests drive only a private, detached tmux server (`tests/test_e2e_tmux.py`); these need a human attached to a real one.
Inside a real tmux session (`tmux new -s demo`), start a lead with `pi`, register it, and use `--model dsflash`:

65. **An executor opens in a new window next to its lead's.** Ask the lead to `Run: pilead spawn …` (any small
    packet): a new window appears right after the lead's in the status bar, `placement=adjacent` is printed, and
    pi starts in it with nothing typed after the launch line (no `/rename` in the pane).
66. **The window is named and coloured.** The status bar shows the executor's label (`[Exec] <sid>`), and its entry
    has the lead's colour as its background (`window-status-style`); the lead's own window is named `[Lead] <project>`
    in the same colour.
67. **`pilead focus` switches to it.** From the lead, `Run: pilead focus <sid>`: the client shows the executor's
    window and the line `focused <sid> (tab '[Exec] <sid>')` is printed.
68. **`pilead close` kills the pane.** `Run: pilead close <sid>`: the executor's window disappears.
69. **A banner is a status-line message.** When the executor reports, the lead's client shows a status-line message
    (`<title> — <body>`) for about four seconds; no tty escape appears in the lead's pane.

## Ubuntu (over SSH, in tmux) (seven live checks; tests cannot run these)

Nothing here has run on a real Linux box. On the box, inside `tmux new -s demo`, start pi, register a lead, use
`--model dsflash`:

70. **The same as 65–69 on a Linux box reached over SSH.** Repeat 65–69 in tmux on a Linux box (`pilead doctor` shows
    `terminal backend … selected: tmux`).
71. **`pilead doctor` has a `linux helpers` row.** `pilead doctor` shows the row naming which of `xdg-open`, `xclip` /
    `wl-copy` and `notify-send` are found.
72. **A real pi is found by its process name.** With a real executor working, `pilead list` shows it `busy` and the
    lead's WAKE `ok`: neither reads `dead` (liveness reads `ps -eo`, whose columns differ from a Mac's).
73. **A banner is a `notify-send` banner on a desktop session, and only a status-line message without one.** On a box
    with `notify-send` and a desktop, `pilead notify "$PI_SESSION_ID" --test` shows a banner; on a headless box the
    tmux client shows the status-line message and nothing else.
74. **`--open` opens the page with `xdg-open`.** On a box with a desktop and `xdg-open`, `Run: pilead board --open`
    opens the page in the browser.
75. **`--open` without an opener prints the address.** On a headless box with no `xdg-open`,
    `Run: pilead board --open` writes the page and prints
    `(no opener available — open it yourself: file://…/board.html)`, exit 0.
76. **There is no tab tidy under tmux.** `Run: pilead tidy`: no window moves; a `pilead spawn` prints no `tab tidy` line
    (the automatic tidy skips silently, since `tidy` orders iTerm2 tabs only).

Cleanup: `tmux kill-session -t demo` (and `pilead close` any executor left open).

Cleanup: `rm -rf /tmp/pl-demo /tmp/pl-demo-packet*.md /tmp/pl-demo-memo.md` (and `pilead close` any tab left open).

# Smoke check

Thirteen live checks, the ones that matter most before a release. Tests cannot run them: they need a real
pi, a real terminal and a real tab. Each item is one action and one expected result; "(full: item N)"
points at the same item in [manual-checklist.md](manual-checklist.md), which has all 76.

Setup (a scratch repo, so nothing real is touched):

```bash
mkdir -p /tmp/pl-demo && cd /tmp/pl-demo && git init -q
cat > /tmp/pl-demo-packet.md <<'EOF'
GOAL: add hello.txt containing hi, stage it, write the report.
Attempt `git commit -m test` once to observe the block, and paste what it prints.
EOF
pi -e <your pi-lead checkout>/extensions/pi-lead.ts     # this tab is the lead
```

In the lead tab run `/pilead:status`, then ask the lead to run `pilead lead-start "$PI_SESSION_ID" --project demo`.
Replace `<sid>` with the id `pilead spawn` prints. Ask the lead to run any command that mentions
`$PI_SESSION_ID` (it is set only in the lead's bash tool).

## On a Mac, in iTerm2

1. **Spawn opens a tab.** `pilead spawn /tmp/pl-demo demo /tmp/pl-demo-packet.md --model <model> --lead "$PI_SESSION_ID"`
   opens a `[Exec] <sid>` tab right next to the lead's, and prints `placement=adjacent`. (full: item 1)
2. **The executor cannot commit.** Its report shows `pi-lead executor: stage your work and report; the lead commits.`
   and `git -C /tmp/pl-demo log` shows no commits. (full: item 2)
3. **Send reaches the running tab.** `pilead send <sid> <packet>` makes the text appear as a new message in the
   executor's tab without you touching it. (full: item 3)
4. **`list` shows the executor.** `pilead list` shows the row `busy` while it works and `reported` after its
   report. (full: item 4)
5. **A report wakes the lead.** The lead tab shows `🚦 [pi-lead] executor <sid> reported: <path>` and starts a
   turn on its own. (full: item 5)
6. **The wake carries the first line.** The wake text shows the report's first line and the size of its diff.
   (full: item 7)
7. **`verify` and `diff --open` work.** `pilead verify <sid>` prints a verdict, and `pilead diff <sid> --open`
   opens the staged diff as a page. (full: item 5)
8. **The lead's large write is blocked.** In a registered lead, ask for a new file of about 80 lines. It is refused
   with `pi-lead lead gate: this write of <file> … is implementation work — delegate this …`. (full: item 28)
9. **`/pilead:route` lets it through.** `pilead route retain "demo edit"`, then ask for the same write again. It
   goes through, and `/pilead:status` shows `route grace: open`. (full: item 30)
10. **Close shuts the tab.** `pilead close <sid>` closes the executor's tab, and `pilead check <sid>` prints
    `closed`. (full: item 6)
11. **The board opens.** `pilead board --open` shows the lead's colour, its wake, and the executor's tokens and
    context. (full: item 60)

## Under tmux, on a Mac

12. **An executor opens in a new window next to its lead's.** Ask the lead to `Run: pilead spawn …` (any small
    packet). A new tmux window appears right after the lead's, `placement=adjacent` is printed, and pi starts in
    it. (full: item 65)

## On Ubuntu, over SSH, in tmux

13. **Doctor finds the Linux helpers.** `pilead doctor` shows a `linux helpers` row naming which of `xdg-open`,
    `xclip` / `wl-copy` and `notify-send` are found. (full: item 71)

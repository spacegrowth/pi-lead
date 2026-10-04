---
name: pilead-board
description: Render the board — one HTML page of every lead and executor with status, models and timelines — and open it; use when asked for an overview of everything in flight.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead board --open
```

Writes `$PI_LEAD_HOME/board.html` from every lead and session on disk and (with `--open`) opens
it (via macOS `open`). Use it for "what's going on across everything"; use `pilead list` for the terminal-sized view
and `pilead check <sid>` for one session.

It is a snapshot at render time; re-run it to refresh, or turn on live mode (below). Nothing is spawned. Run by a
lead, it first parks that lead's finished executors, as `pilead list` does, and prints one line per park.

Options:
- `--open` open the page; `--out PATH` write it there instead (directories are made)
- `--lead SID` only that lead's executors and the unowned ones (a project or unique id prefix works)
- `--json` print the data the page is drawn from; no page, no data file; not with `--open`, `--out`, `--live`
- `--live [on|off]` `on`: a page that reloads itself (`updated HH:MM:SS` badge, red when stale) and a
  `board.json` beside it; `off`: remove that file. No server: `list`, `check`, `send`, `spawn`, `close` and
  `turn-end` rewrite the default page while live mode is on (config `board_live` true, or `board.json` there)

It reads what `pilead list` reads. Chips to know:
- lead: `STUCK` / `STALE` (red), `off`, `?` (not readable), `heavy`, `auto`, `tier manual|lead`;
  a red name is unreachable, ghost or broken
- executor: `effort`, `approaching heavy · <ctx>`, `queue ?` (queue unreadable), `auto:` (auto-closed),
  `report not delivered`, `orphan`; actions `Resume`, `Focus tab`, `Retire`
- `-` and `?` mean "not known" — never zero, never fine

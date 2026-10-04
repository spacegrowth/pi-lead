---
name: pilead-close
description: Close an executor's tab and mark it closed, or step down from the lead role; use once its work is committed or discarded.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead close <sid>
pilead close <sid> --supersede <successor-sid>
pilead close <sid> --keep-tab
pilead retire <sid>
pilead stop <lead_sid>
```

`pilead close <sid>` closes the executor's iTerm tab and marks the session `closed`. It removes no
file: packets, reports and meta stay under `$PI_LEAD_HOME/sessions/<sid>/`, so `pilead send <sid>
<packet>` can bring it back. It prints `closed <sid>`; on a `busy` session with no report it also
says on stderr that the packet's work had no report.

- `--supersede BY` — use it when another session (or a successor seed, `BY` = `successor-seed`) has
  taken this one's work over. It is shown as `superseded` and `pilead send` refuses it, so a
  follow-up cannot land in the wrong session. `BY` must not be empty.
- `--keep-tab` — mark it closed but leave its process and tab alone (retitled `[closed] <sid>`).
  A later `send` while its process still runs goes through the inbox, with no relaunch.
- A pinned session (`pilead keep <sid>`) is refused; `pilead keep <sid> --off` releases it.
- `pilead retire <sid>` — close it AND keep what it learned: it writes `successor-seed.md` (an index
  of its packets and reported outcomes) and closes it as `--supersede successor-seed`. Start the
  successor with `pilead spawn … --seed <sid>`, or do both with `pilead send <sid> <packet> --rotate`.
  A session still busy with no report needs `--force` (that packet is seeded as NO REPORT).

Close when the work is committed or discarded. **Do not close** an executor whose follow-up is
imminent — a live idle session is the cheap way to reuse its context. Never close a session that
is still `busy` unless you mean to kill its work. Executors never close sessions (theirs or
others'); this is the lead's tool.

**Stepping down from lead mode:** run `pilead list` and say what is still in flight so the next
lead can pick it up, then `pilead stop <lead_sid>` (the same as `pilead close --self <lead_sid>`).
It removes the lead's record, route and usage cache, and its inbox unless a wake is pending; its
executors' reports then wake nobody. The edit gate stays on until this pi session ends.

Finished executors are also parked for you: once you have seen a report (`pilead check`/`verify`) and
its work has landed or sat an hour, `pilead list`/`check` close it (retire it when heavy) and say so on
the last line; `pilead auto-close [--dry-run]` does it now, and `pilead keep <sid>` keeps one open.

`pilead prune` later deletes the state of sessions that ended more than `--days` ago (default 7);
see the README.

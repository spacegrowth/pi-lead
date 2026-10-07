# Vendored code

`vendor/relay/` is copied from [claude-relay](../claude-relay), same author, MIT: `tmux_backend.py` and
`platform_cmds.py` from commit `d1cc0a7` (0.5.7, unchanged at 0.5.8 `0479828`), `backend.py` and the tmux/Linux
parts of `iterm.py` (`bootstrap_file_content`'s `sid_expr`, `_tty_name`, `_args_match`, `pids_on_tty`) from
`d1cc0a7` too, everything else from commit `25936bf` (0.5.4). Pi-specific edits are made here and do not flow back.
Only `platform_cmds.py`, `iterm_pyapi.py` and `bash_writes.py` are byte-identical to claude-relay; the others are
adapted (see below).

| File | From | Role here |
|---|---|---|
| `backend.py` | `scripts/backend.py` (adapted) | picks iTerm2, tmux or Terminal.app; the one reader of the live handle |
| `tmux_backend.py` | `scripts/tmux_backend.py` (adapted, d1cc0a7) | tmux: windows, splits, colours, focus, close, banners |
| `platform_cmds.py` | `scripts/platform_cmds.py` (identical, d1cc0a7) | the one platform seam: `open`, Linux helpers |
| `iterm.py` | `scripts/iterm.py` | tabs, splits, colors, focus, close |
| `iterm_pyapi.py` | `scripts/iterm_pyapi.py` | iTerm Python API: tab placement next to the lead |
| `terminal_app.py` | `scripts/terminal_app.py` | Terminal.app fallback |
| `report_verify.py` | `lib/report_verify.py` | checks a report against the staged diff |
| `diff_render.py` | `lib/diff_render.py` | staged diff → HTML review page |
| `board_render.py` | `lib/board_render.py` | the board page |
| `bash_writes.py` | `lib/bash_writes.py` | the file-writing shapes of a bash command |

## Adapted files

- `report_verify.py` — an empty index is INCONCLUSIVE, not COUNTS-MATCH (nothing to compare);
  sign-off markers are pi-lead's paths (`extensions/`, `lib/pilead/state.py`); the ledger-format
  proxy is dropped (pi-lead has no ledger).
- `report_verify.py` `--rerun` port — adds a timeout, failed-count (`rerun-red`) and named
  timeout / non-zero-exit / could-not-start findings; a refused declared command (`yarn test`) is a
  NOT RE-RUN note, and under `--rerun` auto-commit condition 1 also needs the rerun to match. The
  pytest allowlist is stricter than relay's — it also rejects: argv[0] not exactly `pytest` /
  `python[3] -m pytest` (`./pytest.sh`, `pytest-watch`), `--basetemp`/`--junitxml`/`--pastebin`/
  `--debug`/`--log-file`/`--rootdir`/`--confcutdir`/`--override-ini`/`--config-file`/… and
  `-p`/`-o`/`-c`, and any argument that is absolute, `~` or has `..`. Unlike relay (pytest only),
  it also accepts exactly `npm test`, `npm run check` and `node --test` as whole strings
  (`RERUN_EXACT`; `rerun_runner` names each command's runner) — lib/pilead/review.py starts them from
  an argument list of its own, with the git shim first on PATH. `node_counts` reads node's test-runner
  lines, `runner_counts` a run's counts per runner, `declared_for` the report's declared count per
  runner; `rerun-declared-not-compared` names every declared pytest or node count no re-run of that
  runner produced, whichever runner did re-run (a plain count a node row took counts), and a command
  refused for a reason only the worktree shows (`reality["rerun_refused_here"]`: no `package.json`)
  is named like one refused from the text.
- `diff_render.py` — `REPO_ROOT` is one level deeper (`vendor/relay/`) and page titles say `pilead`.
- `board_render.py` — `pilead` branding; data comes from `lib/pilead/review.py`; drops the MCP chip
  and the Resume/Focus actions; adds a "Staged diff" action; live mode is used (`pilead board --live`). Since the board-data packet (p24):
  - the overview's sub-header reads `generated <time> · pilead <version, else ?> · click an executor to drill in`;
  - a lead's wake chip is shown for every `wake` but `ok`, with the text `STUCK`, `STALE`, `off` or `?`
    (`_WAKE_TEXT`), class `bad` for `stuck` and `stale`, titled with `wake_detail`;
  - a lead's name has class `lv-bad` for liveness `broken` too (beside `unreachable` and `ghost`);
  - a lead's posture: a chip `auto` when `auto` is true, `tier <word>` when `tier` is `manual` or `lead`, and
    `?` titled `posture not readable` when either key is present and null;
  - an executor's chips: `effort<b>…</b>` when `effort` is set; the `approaching` chip reads the live context
    from `usage["live"]`; `queue ?` when the row has `queued` and it is null;
  - an executor's actions add `Resume` (`pilead resume <sid>`) for a closed, dead or superseded session,
    `Focus tab` (`pilead focus <sid>`) for every other, and `Retire` (`pilead retire <sid>`) for a live heavy one;
  - a broken lead row is its sid with `lv-bad` and the word `broken`, and has no executors block of its own
    (its executors show under "Unowned / orphaned").
- `iterm.py`, `terminal_app.py` — launch `pi` (via `build_pi_cmd`) instead of `claude`; the iTerm
  spawn AppleScript returns a 4th `lead` verdict line and `spawn` reports its placement
  (`adjacent | pane | end-of-bar (<reason>)`).
  `spawn` takes `type_name=True` (b7): pi-lead passes False everywhere, so no `/name` is typed into a running pi;
  with True the script is byte-identical to before.
  `build_pi_cmd` takes an optional `thinking=` keyword: given, `--thinking <level>` follows `--session-id`
  (and `--model`), for a fresh launch and a resume alike; left out, the line is unchanged.
  It also takes the keywords `role=` (default `"executor"`) and `message=` (default None): `role="lead"` gives a lead's
  reopen line (`PI_LEAD_HOME=<home> exec pi --session-id <sid> [--thinking <level>] -e <extension>`, no executor
  env, model, rules or packet), a `message` is appended as the last argument, and with neither the line is unchanged.
  It also takes the keyword `lead_model=` (default None; `pilead handoff`): with `role="lead"` and a non-empty string,
  `--model <lead_model>` follows `--session-id <sid>`; every other call's line is unchanged.
  Production passes `spawn`'s `env_prefix` parameter (documented in the file as a test-only hook):
  `lib/pilead/cli.py` uses it to clear the inherited git env and put the git shim first on `PATH`.
- `iterm.py` (b12, from d1cc0a7) — `bootstrap_file_content`/`write_bootstrap_file` take `sid_expr` (default
  `${ITERM_SESSION_ID:-$TERM_SESSION_ID}`, the text otherwise relay 0.5.6's); `pids_on_tty` matches the `args=`
  column through `_args_match` on BOTH platforms (`ps -axo pid=,tty=,args=` on macOS, `ps -eo …` on Linux) — relay
  matches `comm=` on macOS and `args=` with "ends with" on Linux; pi-lead wants an EXACT basename (`api` is not
  `pi`) and reads args on macOS too, because pi is a node program whose `comm` may be `node`. A node shim counts
  when its script path contains `pi-coding-agent` (relay: `claude-code`).
- `iterm.py`, `terminal_app.py` (from relay cf43d14) — `spawn`'s AppleScript no longer starts with `activate`, so a
  new executor opens behind whatever you're in instead of taking focus; `focus` still activates.
- `backend.py` (b12) — `select(env=None, configured=None)` takes its inputs (relay reads `os.environ` and loads its
  config through `lead_guard`, which pi-lead does not have) and reads `PILEAD_TERMINAL` before `RELAY_TERMINAL`;
  `live_handle_from_env(bk=None, env=None)` reads `$TMUX_PANE` / `$ITERM_SESSION_ID` only (never
  `$TERM_SESSION_ID`); `for_handle(handle)` is pi-lead's (the handle names its backend); `tab_id_from_env` is not
  ported. `for_handle` and `live_handle_from_env` accept any `w#t#p#:<id>` as an iTerm handle (`ITERM_HANDLE_RE`);
  `is_live_handle` keeps relay's strict UUID shape.
- `tmux_backend.py` (b12, from d1cc0a7) — `spawn` has the vendored iTerm `spawn`'s signature and types NOTHING
  after the launch line (relay types `/rename <label>`); `rename_by_id` renames the window only and types nothing
  (relay also types `/rename`); the spawn result carries `placement`; new `window_is_own(handle)`; the socket is
  `$PILEAD_TMUX_SOCKET`, else `$RELAY_TMUX_SOCKET`.
- `iterm_pyapi.py` — `create_adjacent_tab` returns `(handle, reason)` for every degrade path
  (missing module, timeout, stale handle, API refused); `try_create_adjacent_tab` wraps it.

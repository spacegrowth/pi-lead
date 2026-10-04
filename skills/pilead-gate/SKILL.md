---
name: pilead-gate
description: See what the lead's bash write gate makes of a command, without running it; use to test a command before the gate blocks it.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
echo 'sed -i s/a/b/ file.py' | pilead gate [--session SID] [--cwd DIR] [--json] [--record]
```

The command comes on **stdin**. It prints `allow`, `log: <rule>` (with ` — <path> (<size>)` for a hit) or `block: <reason>`;
a command it could not parse gets a second line `parsed: no` — its `allow` is then not a pass. `--json` prints the whole
decision. The session is `--session`, else `$PI_LEAD_SID`, else `$PI_SESSION_ID`; a session that is not a registered lead
is `allow`. Read-only (`--record` is for the extension: it appends the ledger event).

It judges a bash command that writes a new tracked file or more than the threshold of lines, or writes a control file.
Config `bash_write_gate` (`log`, the default; `deny`; `off`) decides whether a hit is logged or blocked; a control-file
write is blocked in both modes. Known limits: `perl -i`, `node -e`, `dd of=`, `curl -o`, `git apply` and variable-built
control paths are not caught. README: Lead gate.

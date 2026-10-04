---
name: pilead-lint
description: Check a packet file before sending it; use on every packet you write, before `/pilead:spawn` or `/pilead:send` (both also lint it).
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead lint <packet.md> [--worktree DIR] [--model TEXT] [--strict] [--json]
```

Zero tokens, read-only, advisory. Warns: a packet shorter than 80 characters, no `## Preconditions`, an `MCP:` or `CONTEXT:`
line (pi-lead reads neither), an unparsable `EFFORT:`, referenced files over 1.2MB, a packet that tells the executor to
commit or push, or to ask someone instead of stopping and reporting. Info: `shape-haiku` (a cheap model could do this),
`shape-opus` (investigation with no repro). With no findings it prints `✓ packet lint: no findings`, exit 0.

`--strict` exits 1 when any finding is a warn; `--json` prints a list of `{level, code, message}`. A path that is not a readable
file is exit 2. `PILEAD_NO_LINT=1` turns off the lint `spawn` and `send` do themselves. README: Packet lint.

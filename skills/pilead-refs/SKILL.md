---
name: pilead-refs
description: Make the git refs as they are now an executor packet's baseline, after you commit its work; use right after the commit, so the next `check` does not say `REFS MOVED`.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead refs accept <sid> --reason "lead committed packet 0001"
```

`check` and `verify` compare the refs with the baseline taken when the packet was sent; a move shows as
a `REFS MOVED` block that stops the commit and goes to the human. Your own commit moves the refs too: `refs accept` stores
the refs as they are now as the packet's baseline, ledgers `refs_accepted`, and marks that plan item `done`.

It also stores a baseline where none is readable. A comparison that could not be made is never read as "unchanged".
Run it after committing, not before. README: Detection.

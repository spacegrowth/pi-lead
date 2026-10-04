---
name: pilead-lineup
description: Say which model classes this machine's pi offers and where the lead sits among them; run it first in lead mode, and repeat its `Model check:` line.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead lineup --session "$PI_SESSION_ID" [--json]
```

It reads `pi --list-models` (cached in `<home>/models.json`, refreshed when older than a day; no model call). A class
(`haiku`, `sonnet`, `opus`, `fable`) is **available**, **not available** (the list was read and holds none) or **unknown**
(the list could not be read) — unknown is never shown as either. The lead's own model is the last answer in its pi session log.

The first line printed is the `Model check:` line: say it as the first line of your answer. When the lead runs the weakest
class available the line asks for a `/model` switch and `STOP:` follows: stop until the human has switched. The exit code is 0
either way. It writes only `models.json`. See `/pilead:tier`; README: Model check and model tier.

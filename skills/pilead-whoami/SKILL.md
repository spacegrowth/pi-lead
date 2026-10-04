---
name: pilead-whoami
description: Say who a session is — a lead and its executors, or an executor and its lead; use when you lose track of which session or tab you are in.
---

Inside a pi session that loads the pi-lead extension, `pilead` resolves directly (the extension prepends its own `bin/`), so call it as `pilead …`.

```
pilead whoami [<id | project | unique id prefix>] [--json]
```

With no token it uses `$PI_LEAD_SID`, else `$PI_SESSION_ID`. For a lead it prints the project, terminal, tab label and its
executors with their stored status. For an executor it prints its status, current packet, the report path and whether that report
exists, and names the owning lead and that lead's tab. It reads state and writes nothing. README: Names.

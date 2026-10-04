"""<home>/ledger.jsonl — an append-only JSON-lines event log (claude-relay's `append_ledger`).

Every record is `{"ts": <UTC ISO>, "event": <name>, **fields}` on one line. Writing is best-effort:
a ledger that cannot be written never changes what a verb prints or its exit code. The pi extension
(extensions/pi-lead.ts `appendLedger`) appends to the same file in the same shape."""
import json
from pathlib import Path

from . import state

NAME = "ledger.jsonl"


def path(home):
    return Path(home) / NAME


def append(home, event, **fields):
    """Append one record (one `write` call in append mode, so concurrent writers never interleave
    within a line). Any failure is swallowed — never raised, never printed."""
    try:
        p = path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": state.now_iso(), "event": event, **fields}) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read(home, session_id=None, event=None):
    """Every record, oldest first, optionally filtered by `session_id` and/or `event`. Lines that are
    not a valid JSON object are skipped (bytes that are not UTF-8 are replaced, so such a line fails
    to parse or keeps U+FFFD); a missing or unreadable ledger is []. Never raises."""
    try:
        text = path(home).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if session_id is not None and rec.get("session_id") != session_id:
            continue
        if event is not None and rec.get("event") != event:
            continue
        out.append(rec)
    return out

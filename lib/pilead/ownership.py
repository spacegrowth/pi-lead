"""Who owns an executor session, and making another lead its owner.

`sessions/<sid>/meta.json`'s `lead` names the lead that owns the session: the lead whose inbox its reports
wake. A lead that comes back under a new session id leaves a forward note, `<home>/handoffs/<old sid>.json`
= `{"from", "to", "project", "at", "reason"}` (written by the lead-move packet; only READ here). The owner is
found by following those notes from the id `meta.json` names, while no lead record exists for the id: at most
`MAX_MOVES` moves, every id on the way a usable lead id (`state.valid_lead_sid`). The same walk, to the step, is
`resolveOwner` in extensions/pi-lead.ts; tests/owner-cases.json is the table both suites read.

The four owner states:
- `owned`   — the walk ends on a lead whose record (`leads/<sid>.json`) exists and reads as a JSON object;
- `orphan`  — `meta.json` names a usable lead id and neither a record nor a forward note leads to one;
- `unowned` — `meta.json` has no `lead`, or an empty one;
- `unknown` — the answer could not be read: the lead's record exists and cannot be read, the named id is not
  a usable lead id, a forward note cannot be read (or names an id that is not usable), or the notes form a
  loop or a chain of more than `MAX_MOVES`. It is never shown as `owned` and never as `orphan`.

`resolve` and `owner` never raise and never write. `adopt` writes: `meta.json`, the two inboxes, `.notified`
and the ledger event `adopted`."""
import json
import os
from pathlib import Path

from . import ledger, state
from .state import PileadError

MAX_MOVES = 8
REASONS = ("adopt", "claim", "handoff", "takeover", "fallback")


def _walk(home, lead_sid):
    """(the id the walk ended on, state, what could not be read or None). Never raises."""
    try:
        home = Path(home)
        cur, seen, moves = lead_sid, set(), 0
        while True:
            if not state.valid_lead_sid(cur):
                return cur, "unknown", f"lead id {cur!r} is not a usable lead id"
            seen.add(cur)
            rec = home / "leads" / f"{cur}.json"
            if os.path.lexists(rec):
                try:
                    ok = isinstance(json.loads(rec.read_text()), dict)
                except Exception:  # noqa: BLE001 — a record that cannot be read
                    ok = False
                if ok:
                    return cur, "owned", None
                return cur, "unknown", f"lead record {rec} cannot be read"
            note = home / "handoffs" / f"{cur}.json"
            if not os.path.lexists(note):
                return cur, "orphan", None
            try:
                d = json.loads(note.read_text())
                to = d.get("to") if isinstance(d, dict) else None
            except Exception:  # noqa: BLE001 — a note that cannot be read
                return cur, "unknown", f"forward note {note} cannot be read"
            if not state.valid_lead_sid(to):
                return cur, "unknown", f"forward note {note} names {to!r}, not a usable lead id"
            if to in seen:
                return cur, "unknown", f"forward notes loop back to {to} at {note}"
            if moves >= MAX_MOVES:
                return cur, "unknown", f"forward notes from {lead_sid} run past {MAX_MOVES} moves at {note}"
            moves += 1
            cur = to
    except Exception as e:  # noqa: BLE001
        return lead_sid, "unknown", f"the owner could not be worked out ({type(e).__name__})"


def resolve(home, lead_sid):
    """`(sid, state)`: from `lead_sid`, following forward notes while no record exists for the current id (at
    most MAX_MOVES moves). `state` is `owned`, `orphan` or `unknown`; `sid` is the id the walk ended on. Never
    raises, never writes."""
    sid, st, _why = _walk(home, lead_sid)
    return sid, st


def owner(home, meta):
    """`{"lead", "state", "why"}` of a session's `meta.json` record: `state` one of the four words; `lead` the id
    the walk ended on when `owned`, else the id `meta.json` names (None for `unowned`); `why` what could not be
    read when `unknown`, else None. Never raises, never writes."""
    try:
        named = meta.get("lead") if isinstance(meta, dict) else None
    except Exception:  # noqa: BLE001
        named = None
    if named is None or named == "":
        return {"lead": None, "state": "unowned", "why": None}
    sid, st, why = _walk(home, named)
    return {"lead": sid if st == "owned" else named, "state": st, "why": why}


def _inbox(home, lead_sid):
    """The inbox a lead's record names (`inbox`, `~` expanded, relative to leads/), else leads/<sid>.inbox.md."""
    leads = Path(home) / "leads"
    try:
        rec = json.loads((leads / f"{lead_sid}.json").read_text())
        name = rec.get("inbox") if isinstance(rec, dict) else None
        if isinstance(name, str) and name:
            return leads / os.path.expanduser(name)
    except Exception:  # noqa: BLE001 — no record, or one that cannot be read: the default name
        pass
    return leads / f"{lead_sid}.inbox.md"


def _record_project(home, lead_sid):
    """The non-empty `project` of a lead's record, else None (no record, unreadable, no project). Never raises."""
    try:
        rec = json.loads((Path(home) / "leads" / f"{lead_sid}.json").read_text())
        proj = rec.get("project") if isinstance(rec, dict) else None
        return proj if isinstance(proj, str) and proj else None
    except Exception:  # noqa: BLE001
        return None


def _same(a, b):
    try:
        return os.path.realpath(str(a)) == os.path.realpath(str(b))
    except Exception:  # noqa: BLE001
        return False


def _append(path, text):
    """One O_APPEND write of `text` (whole lines), as the extension appends to an inbox."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _pending(home, old, new_lead, sid, report):
    """True when leads/<old>.inbox.md or a leads/<old>.inbox.md.claim-* file holds `reported <sid> <report>`."""
    if not state.valid_lead_sid(old) or old == new_lead:
        return False
    leads = Path(home) / "leads"
    files = [leads / f"{old}.inbox.md"] + (sorted(leads.glob(f"{old}.inbox.md.claim-*")) if leads.is_dir() else [])
    for f in files:
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            parts = ln.split(None, 2)
            if len(parts) == 3 and parts[:2] == ["reported", sid] and _same(parts[2].strip(), report):
                return True
    return False


def _repaint(home, meta):
    """The executor's tab wears its new owner's colour (lib/pilead/tabs.py); cosmetic, never raises."""
    try:
        from . import config, tabs
        tabs.repaint_executor(home, meta, config.load(home))
    except Exception:  # noqa: BLE001
        pass


def adopt(home, sid, new_lead, reason, forced=False):
    """Make `new_lead` the owner of session `sid`; return the old owner's sid (what `meta.json` named, or None).

    1. `meta.json` gets `lead` = `new_lead` and `owner_project` = the project of `new_lead`'s record (left as it
       is when that record has none) (`state.save_meta`), every other field kept.
    2. When the old owner has NO lead record and its inbox file (leads/<old>.inbox.md) still exists, its lines
       that announce this session (`reported <sid> …`) are appended to the new owner's inbox in one write, then
       removed from the old file (`state.atomic_write`); the other lines stay, in order. An old owner WITH a
       record keeps its inbox untouched (its own pi session may be reading it).
    3. When the current packet's report exists (and is not empty), step 2 moved no line for it, and either
       `.notified` does not name it or the old owner's inbox (leads/<old>.inbox.md) or one of its claim files
       (leads/<old>.inbox.md.claim-*) still holds a `reported <sid> <report>` line (announced, never surfaced:
       the ghost or orphan will not wake anyone for it): `reported <sid> <report>` is appended to the new
       owner's inbox once, in one write, and `.notified` becomes `<report> ns:<st_mtime_ns of the report>`
       (the extension accepts that form). The old owner's inbox and claim files are left as they are.
    4. Ledger: `adopted` {session_id, from_lead, to_lead, forced, reason}.

    After step 1 the executor's tab is repainted with the new owner's colour (tabs.repaint_executor; cosmetic).

    A `meta.json` that cannot be read raises PileadError before anything is written."""
    home = Path(home)
    state.check_session_sid(sid)
    state.check_lead_sid(new_lead)
    if reason not in REASONS:
        raise PileadError(f"adopt: reason {reason!r} is not one of {', '.join(REASONS)}")
    mp = state.session_dir(home, sid) / "meta.json"
    try:
        meta = json.loads(mp.read_text())
        if not isinstance(meta, dict):
            raise ValueError("not an object")
    except Exception as e:  # noqa: BLE001
        raise PileadError(f"{mp} cannot be read ({type(e).__name__}); nothing was adopted") from None
    old = meta.get("lead")
    meta["sid"] = sid  # the directory's name, whatever the field said
    meta["lead"] = new_lead
    project = _record_project(home, new_lead)
    if project is not None:
        meta["owner_project"] = project
    state.save_meta(home, meta)
    _repaint(home, meta)

    new_inbox = _inbox(home, new_lead)
    moved = []
    if state.valid_lead_sid(old) and old != new_lead and not os.path.lexists(home / "leads" / f"{old}.json"):
        old_inbox = home / "leads" / f"{old}.inbox.md"
        if old_inbox.is_file():
            lines = old_inbox.read_text().splitlines(keepends=True)
            mine = [ln for ln in lines if ln.split()[:2] == ["reported", sid]]
            if mine:
                _append(new_inbox, "".join(ln if ln.endswith("\n") else ln + "\n" for ln in mine))
                state.atomic_write(old_inbox, "".join(ln for ln in lines if ln not in mine))
                moved = [ln.split(None, 2)[2].strip() for ln in mine if len(ln.split(None, 2)) == 3]

    try:
        n = int(meta.get("packets") or 1)
    except (TypeError, ValueError):
        n = None
    report = state.report_path(home, sid, n) if n is not None else None
    if report is not None and report.is_file() and report.stat().st_size > 0:
        marker = state.session_dir(home, sid) / ".notified"
        try:
            last = marker.read_text().strip()
        except OSError:
            last = ""
        named = last.rsplit(" ", 1)[0] if " " in last else last
        if not any(_same(p, report) for p in moved) and (not (named and _same(named, report))
                                                          or _pending(home, old, new_lead, sid, report)):
            path = os.path.abspath(str(report))
            _append(new_inbox, f"reported {sid} {path}\n")
            state.atomic_write(marker, f"{path} ns:{report.stat().st_mtime_ns}\n")

    ledger.append(home, "adopted", session_id=sid, from_lead=old, to_lead=new_lead, forced=bool(forced),
                  reason=reason)
    return old

"""What happens to a report that finds no listener: claude-relay's executor escalation (hooks/executor_escalation.py,
lib/lead_guard.py `escalation_decision`), with pi-lead's state layout. The executor's extension runs
`pilead escalate <sid> --packet <n> --json` when pi has settled with a report (extensions/pi-lead.ts).

pi-lead never types into a running pi session. relay's typed line is the inbox line `reported <sid> <report>`,
which the lead's own extension turns into a wake; when the lead's extension is not running, a banner tells the
human and the line waits.

`decide(home, sid, packet=None, dry_run=False)` takes the first row that applies:

  1  config `executor_escalation` is false                                   off            nothing
  2  the packet has no report file                                           no-report      nothing
  3  an entry exists for the packet, confirmed or its status is not `sent`   handled        nothing
  4  the lead has the report: `wake_delivered` / `wake_skipped_landed` for   resolved       entry `sent` → confirmed;
     this executor and packet, or the report was looked at                                  else `escalation_resolved`
  5  the owner state is `unknown`                                            owner-unknown  banner A
  6  orphan / unowned / owned by a `ghost`, and ONE fall-back lead exists     fallback       adopt, `owner_fallback`,
                                                                                            then row 8 or 9
  7  as 6, and no fall-back lead                                             no-lead        banner B
  8  owned, wake word `ok` or `stuck`                                        send           line if not waiting; C
  9  owned, wake word `stale`, `off` or `unknown`                            not-listening  line if not waiting; D

"looked at" = `seen.looked_at`: the ledger holds, for this session and packet, `report_verify`, `report_reviewed`,
`report_looked_at` or `usage_snapshot` with `at` "reported", written by the lead or an unnamed caller — never by
the executor itself. "waiting" = the line `reported <sid> <report>` is in the owner's inbox or a
claim file beside it. A state that could not be read is never `resolved` and never "no lead".

What is remembered: `sessions/<sid>/escalation.json`, one entry per packet number, `{"status", "confirmed", "at"}`.
A file that cannot be read counts as empty and is written over. With `dry_run` nothing is written anywhere."""
import json
import os
from pathlib import Path

from . import config, ledger, notify, ownership, seen, state, wakehealth

FILE = "escalation.json"
WAKE_EVENTS = ("wake_delivered", "wake_skipped_landed")
LISTENING = ("ok", "stuck")
TITLE = "pi-lead — report ready"


def _text(v):
    return v if isinstance(v, str) and v else None


def _read_json(path):
    """A JSON object from `path`, else None. Never raises."""
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def project_of(home, meta):
    """The project a session belongs to: `owner_project` when it is a non-empty text; else the project of the
    owner's record when it can be read; else the `project` of the forward note for the named lead; else None.
    Never raises."""
    try:
        if not isinstance(meta, dict):
            return None
        proj = _text(meta.get("owner_project"))
        if proj:
            return proj
        named = meta.get("lead")
        if not state.valid_lead_sid(named):
            return None
        lead, st = ownership.resolve(home, named)
        if st == "owned":
            proj = _text((_read_json(state.lead_path(Path(home), lead)) or {}).get("project"))
            if proj:
                return proj
        note = _read_json(Path(home) / "handoffs" / f"{named}.json")
        return _text((note or {}).get("project"))
    except Exception:  # noqa: BLE001
        return None


def _load_entries(sdir):
    d = _read_json(sdir / FILE)
    return d if d is not None else {}


def _store(sdir, entries, packet, status, confirmed=False):
    """Write the packet's entry; False when it could not be written (never raises)."""
    try:
        entries = dict(entries)
        entries[str(packet)] = {"status": status, "confirmed": bool(confirmed), "at": state.now_iso()}
        state.atomic_write(sdir / FILE, json.dumps(entries, indent=2, sort_keys=True) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def _packet_of(rec):
    try:
        return int(rec.get("packet"))
    except (TypeError, ValueError):
        return None


def _resolved(records, sid, n):
    """True when the lead has the report: a wake event for this executor and packet, or it was looked at."""
    for r in records:
        if r.get("event") in WAKE_EVENTS and r.get("executor") == sid and _packet_of(r) == n:
            return True
    return seen.looked_at(records, sid, n)  # the lead looked (lib/pilead/seen.py), never the executor itself


def _line_matches(line, sid, report):
    parts = line.split(None, 2)
    return len(parts) == 3 and parts[:2] == ["reported", sid] and ownership._same(parts[2].strip(), report)


def _waiting(inbox, sid, report):
    """True when `reported <sid> <report>` is in the inbox or a claim file beside it."""
    inbox = Path(inbox)
    files = [inbox]
    try:
        files += sorted(inbox.parent.glob(f"{inbox.name}.claim-*"))
    except Exception:  # noqa: BLE001
        pass
    for f in files:
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        if any(_line_matches(ln, sid, report) for ln in lines):
            return True
    return False


def _appendable(inbox):
    """What a dry run expects of an append: the inbox's directory is (or can be made) a directory, and the inbox
    is writable when it exists."""
    inbox = Path(inbox)
    p = inbox.parent
    while not os.path.lexists(p):
        if p.parent == p:
            return False
        p = p.parent
    if not p.is_dir():
        return False
    if os.path.lexists(inbox):
        return inbox.is_file() and os.access(inbox, os.W_OK)
    return os.access(p, os.W_OK)


def _first_line(e):
    text = str(e) or type(e).__name__
    return (text.splitlines() or [type(e).__name__])[0][:200]


def _lead_rows(home, cfg):
    """{sid: read_leads row}; None when the leads could not be read (the terminal is asked only here)."""
    try:
        from .verbs.list import read_leads  # noqa: WPS433 — list.py imports a lot; only when needed
        return {r["sid"]: r for r in read_leads(home, cfg, write_cache=False)}
    except Exception:  # noqa: BLE001
        return None


def _fallback(home, rows, owner_sids, project):
    """The ONE lead, other than the owner, whose record can be read, whose project equals `project`, whose LIVE
    value is `live` and whose wake word is `ok` or `stuck`; else None (never a choice between two)."""
    if not project or rows is None:
        return None
    found = []
    for lead, r in rows.items():
        if lead in owner_sids or r.get("broken") or not state.valid_lead_sid(lead):
            continue
        rec = r.get("rec") or {}
        if rec.get("project") != project or r.get("live") != "live":
            continue
        word, _d = wakehealth.state(home, lead)
        if word in LISTENING:
            found.append(lead)
    return found[0] if len(found) == 1 else None


class _Ctx:
    """What one call knows: the inputs, and whether it may write."""

    def __init__(self, home, cfg, sid, n, report, dry_run):
        self.home, self.cfg, self.sid, self.n, self.report, self.dry_run = home, cfg, sid, n, report, dry_run

    def banner(self, owner_key, key, title, message, lead_rec=None, lead_sid=None):
        """Ask for one banner: never in a dry run, never under the kill switch or with `notify_on_wake` off (and
        then no claim is used up), then only when the claim is the first. Never raises."""
        if self.dry_run or notify.kill_switch() or self.cfg.get("notify_on_wake") is not True:
            return
        if not notify.claim(self.home, owner_key, key):
            return
        notify.banner(self.home, self.cfg, title, f"{self.sid} reported", message, lead_rec=lead_rec,
                      lead_sid=lead_sid)


def _deliver(c, owner, word):
    """Rows 8 and 9 for `owner` (a lead with a record): (outcome, banner letter, stored status, detail)."""
    rec = notify.lead_record(c.home, owner)
    inbox = ownership._inbox(c.home, owner)
    listening = word in LISTENING
    outcome = "send" if listening else "not-listening"
    status = "sent"
    if not _waiting(inbox, c.sid, c.report):
        if c.dry_run:
            if not _appendable(inbox):
                status = "failed"
        else:
            try:
                ownership._append(inbox, f"reported {c.sid} {os.path.abspath(str(c.report))}\n")
            except Exception as e:  # noqa: BLE001
                status = "failed"
                ledger.append(c.home, "escalation_push_failed", session_id=c.sid, packet=c.n, owner_lead=owner,
                              error=_first_line(e))
    if listening:
        detail = f"the report is in {owner}'s inbox; its lead is listening ({word})"
        letter = "C"
        c.banner(owner, f"{c.sid}.report-{c.n}", notify.project_title(rec, owner),
                 notify.report_brief(c.home, c.sid, c.n) or "report ready", lead_rec=rec, lead_sid=owner)
    else:
        detail = f"the report waits in {owner}'s inbox; its lead is not listening ({word})"
        letter = "D"
        c.banner(owner, f"{c.sid}.no-lead-{c.n}", TITLE,
                 f"its lead is not listening ({word}) — the report waits in the inbox", lead_rec=rec, lead_sid=owner)
    if status == "failed":
        detail = f"the report could not be put in {owner}'s inbox ({inbox}); its lead's wake word is {word}"
    return outcome, letter, status, detail


def _result(sid, packet, outcome, owner=None, fallback=None, banner=None, stored=None, detail=""):
    return {"sid": sid, "packet": packet, "outcome": outcome, "owner": owner, "fallback": fallback,
            "banner": banner, "stored": stored, "detail": detail}


def _current_packet(home, sid, meta):
    """The session's current packet: `packets` of meta.json, else the number of its latest report file."""
    if isinstance(meta, dict):
        try:
            n = int(meta.get("packets") or 1)
            if n >= 1:
                return n
        except (TypeError, ValueError):
            pass
    try:
        rep = state.latest_report(Path(home), sid)
        return int(rep.stem.split("-", 1)[1]) if rep is not None else None
    except Exception:  # noqa: BLE001
        return None


def decide(home, sid, packet=None, dry_run=False):
    """The escalation of session `sid`'s report for `packet` (default: the current one). Returns
    {"sid", "packet", "outcome", "owner", "fallback", "banner", "stored", "detail"} — and, for `fallback`,
    "then": the outcome for the new owner. Never raises (see the module docstring)."""
    try:
        return _decide(Path(home), sid, packet, dry_run)
    except Exception as e:  # noqa: BLE001 — a decision that could not be made is reported, never raised
        return _result(sid, packet, "error", detail=f"the escalation could not be decided ({_first_line(e)})")


def _decide(home, sid, packet, dry_run):
    state.check_session_sid(sid)
    sdir = state.session_dir(home, sid)
    cfg = config.load(home)
    if cfg.get("executor_escalation") is not True:
        return _result(sid, packet, "off", detail="executor_escalation is off")
    try:
        meta = json.loads((sdir / "meta.json").read_text())
        if not isinstance(meta, dict):
            meta = None
    except Exception:  # noqa: BLE001
        meta = None
    n = packet if packet is not None else _current_packet(home, sid, meta)
    report = state.report_path(home, sid, n) if isinstance(n, int) and n >= 1 else None
    if report is None or not report.is_file():
        return _result(sid, n, "no-report", detail=f"packet {n} has no report" if n else "no packet has a report")
    entries = _load_entries(sdir)
    entry = entries.get(str(n))
    if isinstance(entry, dict) and (entry.get("confirmed") is True or entry.get("status") != "sent"):
        return _result(sid, n, "handled", stored=entry.get("status"),
                       detail=f"packet {n} was handled already ({entry.get('status')})")
    if not isinstance(entry, dict):
        entry = None
    records = ledger.read(home)
    c = _Ctx(home, cfg, sid, n, report, dry_run)
    named = meta.get("lead") if meta is not None else None

    # 4 — the lead has the report
    if _resolved(records, sid, n):
        if entry is not None:
            if not dry_run:
                _store(sdir, entries, n, "sent", confirmed=True)
            return _result(sid, n, "resolved", owner=named, stored="sent",
                           detail="the lead has the report; the earlier push is confirmed")
        if not dry_run:
            _store(sdir, entries, n, "resolved")
            ledger.append(home, "escalation_resolved", session_id=sid, packet=n, owner_lead=named)
        return _result(sid, n, "resolved", owner=named, stored="resolved", detail="the lead has the report")

    # 5 — the owner could not be read (a meta.json that is not a JSON object included)
    if meta is None:
        own = {"lead": None, "state": "unknown", "why": f"{sdir / 'meta.json'} cannot be read"}
    else:
        own = ownership.owner(home, meta)
    if own["state"] == "unknown":
        if not dry_run:
            _store(sdir, entries, n, "notified")
        c.banner(notify.NO_LEAD, f"{sid}.no-lead-{n}", TITLE, f"its owner could not be read — pilead check {sid}")
        return _result(sid, n, "owner-unknown", owner=own["lead"], banner="A", stored="notified",
                       detail=f"its owner could not be read ({own['why']})")

    rows = _lead_rows(home, cfg)
    lead = own["lead"]
    live = None
    if own["state"] == "owned":
        live = (rows or {}).get(lead, {}).get("live") if rows is not None else None
    gone = own["state"] in ("orphan", "unowned") or (own["state"] == "owned" and live == "ghost")
    if gone:
        project = project_of(home, meta)
        fb = _fallback(home, rows, {x for x in (lead, named) if x}, project)
        if fb is None:
            # 7 — nobody to hand it to
            if not dry_run:
                _store(sdir, entries, n, "notified")
            c.banner(notify.NO_LEAD, f"{sid}.no-lead-{n}", TITLE, f"no lead is registered for it — pilead check {sid}")
            why = {"orphan": f"its lead {named} has no record", "unowned": "it names no lead"}.get(
                own["state"], f"its lead {lead} is a ghost")
            return _result(sid, n, "no-lead", owner=lead, banner="B", stored="notified",
                           detail=f"{why}, and no single live lead of project {project or '(none)'} can take it")
        # 6 — hand it to the one live lead of the same project
        if not dry_run:
            old = ownership.adopt(home, sid, fb, "fallback")
            ledger.append(home, "owner_fallback", session_id=sid, packet=n, old_owner=old, new_owner=fb,
                          project=project)
        word, _d = wakehealth.state(home, fb)
        outcome, letter, status, detail = _deliver(c, fb, word)
        if not dry_run:
            _store(sdir, entries, n, status)
        res = _result(sid, n, "fallback", owner=lead, fallback=fb, banner=letter, stored=status,
                      detail=f"{lead or 'no lead'} cannot take it; {fb} of project {project} is its owner now")
        res["then"] = {"outcome": outcome, "owner": fb, "banner": letter, "stored": status, "detail": detail}
        return res

    # 8, 9 — owned, and not a ghost
    word, _d = wakehealth.state(home, lead)
    outcome, letter, status, detail = _deliver(c, lead, word)
    if not dry_run:
        _store(sdir, entries, n, status)
    return _result(sid, n, outcome, owner=lead, banner=letter, stored=status, detail=detail)

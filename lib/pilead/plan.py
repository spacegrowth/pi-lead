"""A lead's written plan (claude-relay 0.5.4's `relay plan`): an ordered list of packets whose state is worked
out from the ledger and the sessions, never stored. One plan per lead, at `<home>/plans/<lead sid>.json`:
`{"version": 1, "items": [{n, packet, target, model, note, executor, packet_n, added, done}, …]}`. Not a verb
module (the verbs are `pilead plan` and `pilead list --plan`), so one broken verb never takes the other down.

Nothing here sends, spawns or queues. `rows` binds an item to the session and packet that were made from its
packet file (the plan is written back when anything bound), then gives every item a state:
`done`, `queued`, `reported`, `closed` / `superseded` / `dead`, `unknown` or `in-flight`. A plan that cannot be
read is never taken for an empty one and is never written over; a reading that cannot be made is `unknown`."""
import json
import os
from pathlib import Path

from . import ledger, state
from .views import c, table

VERSION = 1
STATES = ("queued", "in-flight", "reported", "done")  # the four the counts line names
STATE_STYLE = {"queued": ("yellow",), "in-flight": (), "reported": ("green", "bold"), "done": ("dim",),
               "closed": ("dim",), "superseded": ("dim",), "dead": ("dim", "red"), "unknown": ("red",)}
EMPTY_LINE = "  (plan is empty — pilead plan add <packet>)"


def path(home, sid):
    """`<home>/plans/<sid>.json`. The id is checked first (PileadError, code 2): the file's name comes from
    a usable lead id and from nothing else."""
    return Path(home) / "plans" / f"{state.check_lead_sid(sid)}.json"


def unreadable_line(p):
    return f"plan: {p} cannot be read — inspect that file; nothing was changed"


def read(home, sid):
    """(plan, "ok") for a plan file; (empty plan, "none") when there is none; (None, "unreadable") for
    anything else (not JSON, not an object, `items` not a list, an item that is not an object, a file that
    cannot be opened). Never raises for a usable id; an unusable id raises PileadError as `path` does."""
    p = path(home, sid)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        if os.path.lexists(p):  # a link to nothing is not "no plan"
            return None, "unreadable"
        return {"version": VERSION, "items": []}, "none"
    except Exception:  # noqa: BLE001 — a plan that cannot be opened is unreadable, never empty
        return None, "unreadable"
    try:
        d = json.loads(text)
    except Exception:  # noqa: BLE001
        return None, "unreadable"
    if not isinstance(d, dict) or not isinstance(d.get("items"), list) \
            or not all(isinstance(it, dict) for it in d["items"]):
        return None, "unreadable"
    d.setdefault("version", VERSION)
    return d, "ok"


def write(home, sid, plan):
    """Number the items 1..N in list order and write atomically."""
    for i, it in enumerate(plan["items"], 1):
        it["n"] = i
    state.atomic_write(path(home, sid), json.dumps(plan, indent=2) + "\n")


def open_items(plan):
    """How many stored items are not marked done (the stored items only: nothing is bound or derived)."""
    return sum(1 for it in plan["items"] if not it.get("done"))


# ── the lead's cwd, an item's packet as an absolute path ───────────────────────────────────────
def lead_cwd(home, sid):
    """The `cwd` of leads/<sid>.json, else the current directory (a record that cannot say)."""
    try:
        rec = json.loads((Path(home) / "leads" / f"{state.check_lead_sid(sid)}.json").read_text())
        cwd = rec.get("cwd") if isinstance(rec, dict) else None
        if isinstance(cwd, str) and cwd:
            return cwd
    except state.PileadError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return os.getcwd()


def packet_abs(item, cwd):
    """An item's packet as an absolute path; a relative one is taken from the lead's `cwd`."""
    raw = item.get("packet")
    p = Path(raw if isinstance(raw, str) else "").expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return str(p.resolve())
    except Exception:  # noqa: BLE001
        return str(p)


# ── binding ──────────────────────────────────────────────────────────────────────────────────────
def _bound(it):
    return isinstance(it.get("executor"), str) and bool(it["executor"])


def _owned_by(home, sid, lead, cache):
    """True when sessions/<sid>/meta.json can be read and names `lead` as its lead (cached)."""
    if sid not in cache:
        ok = False
        try:
            if state.valid_session_sid(sid):
                meta = json.loads((state.session_dir(Path(home), sid) / "meta.json").read_text())
                ok = isinstance(meta, dict) and meta.get("lead") == lead
        except Exception:  # noqa: BLE001 — a record that cannot be read binds nothing
            ok = False
        cache[sid] = ok
    return cache[sid]


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _bind(home, lead, items, cwd, events):
    """Stamp `executor` / `packet_n` on each item that is not bound and not done: the earliest `spawned`
    (packet 1) or `packet_sent` event at or after the item's `added`, on a session of this lead, whose
    `source` is the item's packet, and that no other item holds. True when anything was bound."""
    todo = [it for it in items if not _bound(it) and not it.get("done")]
    if not todo:
        return False
    owned = {}
    sends = []
    for i, e in enumerate(events):
        ev, sid, src = e.get("event"), e.get("session_id"), e.get("source")
        if ev not in ("spawned", "packet_sent") or not isinstance(src, str) or not isinstance(sid, str):
            continue
        n = 1 if ev == "spawned" else _int(e.get("packet"))
        if n is None or not _owned_by(home, sid, lead, owned):
            continue
        sends.append((str(e.get("ts") or ""), i, sid, n, src))
    sends.sort()
    taken = {(it["executor"], it.get("packet_n")) for it in items if _bound(it)}
    changed = False
    for it in todo:
        want = packet_abs(it, cwd)
        added = it.get("added") if isinstance(it.get("added"), str) else ""
        for ts, _i, sid, n, src in sends:
            if src == want and ts >= added and (sid, n) not in taken:
                it["executor"], it["packet_n"] = sid, n
                taken.add((sid, n))
                changed = True
                break
    return changed


# ── state ────────────────────────────────────────────────────────────────────────────────────────
def _session_word(home, sid):
    """The status word shown for session `sid` (status.display of the stored status); None when the
    record or the status cannot be read."""
    from . import status as status_mod
    try:
        if not state.valid_session_sid(sid):
            return None
        meta = json.loads((state.session_dir(Path(home), sid) / "meta.json").read_text())
        if not isinstance(meta, dict):
            return None
        word = state.read_status(Path(home), sid, meta)
        if not isinstance(word, str) or not word or word == "unknown":
            return None
        return status_mod.display(meta, word)
    except Exception:  # noqa: BLE001
        return None


def _item_state(home, it, accepted):
    sid, n = it.get("executor"), it.get("packet_n")
    if it.get("done"):
        return "done"
    if _bound(it) and (sid, n) in accepted:
        return "done"
    if not _bound(it):
        return "queued"
    if _int(n) is None or not state.valid_session_sid(sid):
        return "unknown"
    try:
        if state.report_path(Path(home), sid, n).is_file():
            return "reported"
    except Exception:  # noqa: BLE001
        return "unknown"
    word = _session_word(home, sid)
    if word is None:
        return "unknown"
    return word if word in ("closed", "superseded", "dead") else "in-flight"


def rows(home, sid):
    """Bind what can be bound (writing the plan back when anything was), then every item, in order, with
    `n`, `state` and `packet_path`. None for a plan that cannot be read. Reads the ledger once."""
    plan, st = read(home, sid)
    if plan is None:
        return None
    events = ledger.read(home)
    cwd = lead_cwd(home, sid)
    if _bind(home, sid, plan["items"], cwd, events):
        write(home, sid, plan)
    accepted = {(e.get("session_id"), e.get("packet")) for e in events if e.get("event") == "refs_accepted"}
    out = []
    for i, it in enumerate(plan["items"], 1):
        out.append({"n": i, "packet": it.get("packet"), "target": it.get("target"), "model": it.get("model"),
                    "note": it.get("note"), "executor": it.get("executor"), "packet_n": it.get("packet_n"),
                    "added": it.get("added"), "done": it.get("done"), "state": _item_state(home, it, accepted),
                    "packet_path": packet_abs(it, cwd)})
    return out


# ── the table ────────────────────────────────────────────────────────────────────────────────────
def _text(v):
    return v if isinstance(v, str) and v.strip() else "-"


def table_lines(rows_):
    """The lines of `pilead plan status` (and of the PLAN block of `pilead list --plan`)."""
    if not rows_:
        return [c(EMPTY_LINE, "dim")]
    body = []
    for r in rows_:
        packet = r.get("packet")
        name = os.path.basename(packet) if isinstance(packet, str) and packet else "-"
        target = r.get("executor") if _bound(r) else None
        body.append([(str(r["n"]), ()), (r["state"], STATE_STYLE.get(r["state"], ())), (name, ()),
                     (_text(target or r.get("target") or "fresh"), ()), (_text(r.get("model")), ()),
                     (_text(r.get("note")), ())])
    lines = table([("#", 3), ("STATE", 10), ("PACKET", 40), ("TARGET", 36), ("MODEL", 24), ("NOTE", 50)], body)
    counts = {k: 0 for k in STATES}
    for r in rows_:
        counts[r["state"] if r["state"] in counts else "in-flight"] += 1  # closed, superseded, dead, unknown
    lines.append(c(f"  {counts['queued']} queued · {counts['in-flight']} in flight · "
                   f"{counts['reported']} reported · {counts['done']} done", "dim"))
    return lines

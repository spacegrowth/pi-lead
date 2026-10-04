"""Moving a lead's registration to another session id: claude-relay's `migrate_lead` / `safe_migrate_by_tab`
(lib/lead_guard.py) and `_reparent_executors` (bin/relay), with pi-lead's state layout. `pilead takeover` and,
through it, the lead's extension (`/new` and `/fork` in a lead's tab) call `move_lead`.

`move_lead(home, old, new, reason, project=None, forced=False)` moves everything that belongs to lead `old` to lead
`new`, in this order, each step safe to repeat:

  1. the new record (written when missing, with the old record's `color` and `label`; an existing one only gains
     `lineage_started` and `moved_from`), and the new inbox; a posture is never carried;
  2. every session whose `meta.json` names `old` (terminal ones included) is adopted by `new` — `ownership.adopt`
     repaints each one's tab with the new owner's colour;
  3. `plans/<old>.json` moves to `plans/<new>.json` when `new` has no plan;
  4. the forward note `handoffs/<old>.json` = {"from", "to", "project", "at", "reason"};
  5. the old record and the old lead's other files go (lib/pilead/lifecycle.py `remove_lead`, what `pilead stop`
     uses), EXCEPT its inbox, its claim files and its plan;
  6. the old inbox's pending wake lines are appended to the new inbox in one write (a line the new inbox holds
     already is not written twice); a claim file of the old lead moves the same way only when the old lead's
     LIVE was `ghost` before step 5 or the move is in the same tab, and is otherwise left and named;
  7. ledger `lead_moved`.

When `old` has no record and its forward note names `new`, the move was made before: steps 2, 3 and 6 are done
again for whatever is left and no second `lead_moved` is written. Forward notes are never removed here."""
import json
import os
from pathlib import Path

from . import ledger, lifecycle, ownership, plan as plan_mod, state
from .state import PileadError


def same_tab(a, b):
    """True when two tab handles (`w<d>t<d>p<d>:<UUID>`) name the same tab: their UUID parts are equal."""
    if not (isinstance(a, str) and isinstance(b, str) and a.strip() and b.strip()):
        return False
    ua, ub = a.strip().split(":")[-1], b.strip().split(":")[-1]
    return bool(ua) and ua == ub


def in_same_tab(rec, env=None, home=None):
    """True when the old lead's record has an `iterm_handle`, the caller has a live handle (lib/pilead/launch.py
    `live_handle`: $ITERM_SESSION_ID, or $TMUX_PANE under tmux), and both name one tab."""
    env = os.environ if env is None else env
    from .launch import live_handle
    return same_tab((rec or {}).get("iterm_handle"), live_handle(home, env))


def old_live(home):
    """{lead sid: LIVE value} of every lead record (lib/pilead/verbs/list.py `read_leads`, no cache written);
    None when the leads could not be read."""
    try:
        from . import config
        from .verbs.list import read_leads
        return {r["sid"]: r.get("live") for r in read_leads(Path(home), config.load(home), write_cache=False)}
    except Exception:  # noqa: BLE001 — a reading that could not be made is unknown, never "ghost"
        return None


def read_note(home, old):
    """The forward note of `old` as a dict, or None (none, or not readable as an object)."""
    try:
        d = json.loads((Path(home) / "handoffs" / f"{state.check_lead_sid(old)}.json").read_text())
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _lines(path):
    try:
        return [ln if ln.endswith("\n") else ln + "\n" for ln in Path(path).read_text(errors="replace").splitlines()
                if ln.strip()]
    except OSError:
        return None


def _live(home, env):
    from .launch import live_handle
    return live_handle(home, env)


def _new_record(home, old_rec, old, new, project, env):
    """Step 1. Returns True when the record was written new."""
    p = state.lead_path(home, new)
    inbox = home / "leads" / f"{new}.inbox.md"
    created = False
    if os.path.lexists(p):
        try:
            rec = json.loads(p.read_text())
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except Exception as e:  # noqa: BLE001
            raise PileadError(f"{p} cannot be read ({type(e).__name__}); nothing was moved") from None
        changed = False
        if "lineage_started" not in rec:
            rec["lineage_started"] = old_rec.get("lineage_started") or old_rec.get("started")
            changed = True
        if "moved_from" not in rec:
            rec["moved_from"] = old
            changed = True
        if changed:
            state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
    else:
        mine = new in (env.get("PI_SESSION_ID"), env.get("PI_LEAD_SID"))
        rec = {"sid": new, "project": project, "cwd": old_rec.get("cwd"), "inbox": str(inbox),
               "iterm_handle": _live(home, env) if mine else None,
               "terminal": old_rec.get("terminal"), "started": state.now_iso(),
               "lineage_started": old_rec.get("lineage_started") or old_rec.get("started"), "moved_from": old,
               "autonomous": False, "autonomous_source": "config", "tier": "auto", "tier_source": "config"}
        for k in ("color", "label"):  # the lead's tab colour and label go with its registration
            if k in old_rec:
                rec[k] = old_rec[k]
        state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
        created = True
    target = ownership._inbox(home, new)
    if not os.path.lexists(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    return created


def _sessions(home, old, new, reason, forced, suffix):
    """Step 2: [(sid, line)] for every session whose meta.json names `old`, adopted by `new`."""
    out = []
    d = home / "sessions"
    if not d.is_dir():
        return out
    for sdir in sorted(d.iterdir()):
        sid = sdir.name
        if not state.valid_session_sid(sid):
            continue
        try:
            meta = json.loads((sdir / "meta.json").read_text())
        except Exception:  # noqa: BLE001 — a session that cannot be read names nobody we can see
            continue
        if not isinstance(meta, dict) or meta.get("lead") != old:
            continue
        ownership.adopt(home, sid, new, reason, forced)
        out.append((sid, f"adopted {sid} from {old}{suffix}"))
    return out


def _plan(home, old, new):
    """Step 3: "moved", "none", or ("kept", old path, new path)."""
    src, dst = plan_mod.path(home, old), plan_mod.path(home, new)
    if not os.path.lexists(src):
        return "none"
    if os.path.lexists(dst):
        return ("kept", src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return "moved"


def _wake_lines(home, old, new, move_claims):
    """Step 6: (lines moved, [(claim file left, its line count)])."""
    leads = home / "leads"
    inbox = leads / f"{old}.inbox.md"
    sources = sorted(leads.glob(f"{old}.inbox.md.moving-*")) if leads.is_dir() else []  # an interrupted move's
    if os.path.lexists(inbox):
        moving = leads / f"{old}.inbox.md.moving-{os.getpid()}"
        os.replace(inbox, moving)
        if moving not in sources:
            sources.append(moving)
    left = []
    claims = sorted(leads.glob(f"{old}.inbox.md.claim-*")) if leads.is_dir() else []
    for c in claims:
        if move_claims:
            sources.append(c)
        else:
            got = _lines(c)
            left.append((c, len(got) if got is not None else None))
    target = ownership._inbox(home, new)
    have = set(_lines(target) or [])
    lines = []
    readable = []
    for src in sources:
        got = _lines(src)
        if got is None:
            left.append((src, None))
            continue
        readable.append(src)
        for ln in got:
            if ln not in have:
                have.add(ln)
                lines.append(ln)
    if lines:
        ownership._append(target, "".join(lines))
    for src in readable:
        try:
            src.unlink()
        except OSError:
            pass
    return len(lines), left


def move_lead(home, old, new, reason, project=None, forced=False, live=None, env=None):
    """Move lead `old`'s registration to `new` (see the module docstring). `live` is the old lead's LIVE value as
    the caller read it (None: read here). Returns a dict for the verb to print: {"from", "to", "project", "again",
    "created", "sessions" [(sid, line)], "wake_lines", "claims_left" [(path, lines)], "kept" [(path, why)],
    "removed" [path], "plan"}. Raises PileadError for an unusable id, a record of `old` that cannot be read (then
    nothing is changed), or an `old` with neither a record nor a forward note naming `new`."""
    state.check_lead_sid(old)
    state.check_lead_sid(new)
    if old == new:
        raise PileadError(f"{old} is already the lead it would move to")
    env = os.environ if env is None else env
    home = Path(home)
    rec_p = state.lead_path(home, old)
    again = False
    old_rec = None
    if os.path.lexists(rec_p):
        try:
            old_rec = json.loads(rec_p.read_text())
            if not isinstance(old_rec, dict):
                raise ValueError("not an object")
        except Exception as e:  # noqa: BLE001
            raise PileadError(f"{rec_p} cannot be read ({type(e).__name__}); nothing was moved") from None
    else:
        note = read_note(home, old)
        if not note or note.get("to") != new:
            raise PileadError(f"{old} is not a registered lead")
        again = True
    if again:
        proj = project or (read_note(home, old) or {}).get("project")
        sessions = _sessions(home, old, new, reason, forced, " (no longer a registered lead)")
        plan_res = _plan(home, old, new)
        n, left = _wake_lines(home, old, new, move_claims=False)
        return {"from": old, "to": new, "project": proj, "again": True, "created": False, "sessions": sessions,
                "wake_lines": n, "claims_left": left, "kept": [], "removed": [], "plan": plan_res}

    proj = project or old_rec.get("project")
    if live is None:
        live = (old_live(home) or {}).get(old)
    tab = in_same_tab(old_rec, env, home)
    move_claims = live == "ghost" or tab
    suffix = " (forced)" if forced else " (ghost lead)" if live == "ghost" else " (same tab)" if tab else ""
    created = _new_record(home, old_rec, old, new, proj, env)                       # 1
    sessions = _sessions(home, old, new, reason, forced, suffix)                     # 2
    plan_res = _plan(home, old, new)                                                 # 3
    state.atomic_write(home / "handoffs" / f"{old}.json",                            # 4
                       json.dumps({"from": old, "to": new, "project": proj, "at": state.now_iso(),
                                   "reason": reason}, indent=2) + "\n")
    removed, kept = lifecycle.remove_lead(home, old, None)                           # 5
    mine = f"{old}.inbox.md"
    kept = [(p, why) for p, why in kept if not Path(p).name.startswith(mine)]
    n, left = _wake_lines(home, old, new, move_claims)                               # 6
    ledger.append(home, "lead_moved", from_lead=old, to_lead=new, project=proj, reason=reason,  # 7
                  forced=bool(forced), sessions=len(sessions), wake_lines=n)
    return {"from": old, "to": new, "project": proj, "again": False, "created": created, "sessions": sessions,
            "wake_lines": n, "claims_left": left, "kept": kept, "removed": removed, "plan": plan_res}

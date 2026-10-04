"""pilead plan [status|add|rm|done|next|bind] — a lead's written plan (claude-relay 0.5.4's `relay plan`;
lib/pilead/plan.py): an ordered list of packets whose state is worked out from the ledger and the sessions.

`status` (the default) shows the table; `add` / `rm` / `done` / `bind` change the plan and each write one
ledger event; `next` prints the command for the first queued item and SENDS NOTHING — the lead or the human
runs it. An item is bound to the session and packet made from its packet file when the plan is read. A plan
that cannot be read is never written over. An executor (PI_LEAD_ROLE=executor) cannot change a lead's plan."""
import argparse
import json
import os
import re
import shlex
from pathlib import Path

from .. import ledger, lifecycle, plan as plan_mod, state
from ..state import PileadError

ORDER = 14
RESOLVE = ("session", "session_sid")

_WHOLE = re.compile(r"[0-9]+")
_CHANGES = ("add", "rm", "done", "bind")


def _sid(a, home):
    """--session, else the caller's (lifecycle.caller); PileadError (2) with neither. The id is checked
    before anything is read."""
    if a.session is not None:
        return state.check_lead_sid(a.session)
    sid = lifecycle.caller(home)
    if sid is None:
        raise PileadError("plan: no lead session id — pass --session, or run it inside the lead's own pi session", 2)
    return sid


def _index(text, n_items):
    """N (a whole number 1..len) as a 0-based index; PileadError (2) otherwise."""
    if not isinstance(text, str) or not _WHOLE.fullmatch(text) or not 1 <= int(text) <= n_items:
        raise PileadError(f"plan: no item #{text} (the plan has {n_items})", 2)
    return int(text) - 1


def _load(home, sid):
    """(plan, path): refuses (exit 1, nothing changed) a plan that cannot be read."""
    plan, _st = plan_mod.read(home, sid)
    p = plan_mod.path(home, sid)
    if plan is None:
        raise PileadError(plan_mod.unreadable_line(p), 1)
    return plan, p


def _name(packet):
    return os.path.basename(packet) if isinstance(packet, str) and packet else "-"


def _session_meta(home, sid):
    """The meta.json record of session `sid`: PileadError (2) when the session does not exist."""
    if not state.valid_session_sid(sid) or not (state.session_dir(home, sid) / "meta.json").is_file():
        raise PileadError(f"plan: session {sid} does not exist", 2)
    return sid


def _status(a, home, sid):
    rows = plan_mod.rows(home, sid)
    if rows is None:
        raise PileadError(plan_mod.unreadable_line(plan_mod.path(home, sid)), 1)
    if a.json:
        print(json.dumps({"lead": sid, "items": rows}, indent=2))
    else:
        for line in plan_mod.table_lines(rows):
            print(line)
    return 0


def _add(a, home, sid):
    plan, _p = _load(home, sid)
    raw = a.packet
    cwd = plan_mod.lead_cwd(home, sid)
    given = Path(raw).expanduser()
    from_lead = given if given.is_absolute() else Path(cwd) / given
    if from_lead.is_file():
        stored = raw
    elif given.resolve().is_file():
        stored = str(given.resolve())  # found from HERE, not from the lead's cwd
    else:
        raise PileadError(f"plan: packet {raw!r} is not a file — looked from the lead's cwd {cwd} and from the "
                          f"current directory {os.getcwd()}", 2)
    target = a.target or "fresh"
    if target != "fresh":
        _session_meta(home, target)
    idx = _index(a.before, len(plan["items"])) if a.before is not None else None
    item = {"n": 0, "packet": stored, "target": target, "model": a.model, "note": a.note, "executor": None,
            "packet_n": None, "added": state.now_iso(), "done": None}
    if idx is None:
        plan["items"].append(item)
    else:
        plan["items"].insert(idx, item)
    plan_mod.write(home, sid, plan)
    ledger.append(home, "plan_add", session_id=sid, n=item["n"], packet=stored, target=target, model=a.model)
    print(f"plan: added #{item['n']} {_name(stored)} → {target}")
    return 0


def _rm(a, home, sid):
    plan, _p = _load(home, sid)
    idx = _index(a.n, len(plan["items"]))
    gone = plan["items"].pop(idx)
    plan_mod.write(home, sid, plan)
    ledger.append(home, "plan_rm", session_id=sid, n=idx + 1, packet=gone.get("packet"))
    print(f"plan: removed #{idx + 1} {_name(gone.get('packet'))}")
    return 0


def _done(a, home, sid):
    plan, _p = _load(home, sid)
    idx = _index(a.n, len(plan["items"]))
    it = plan["items"][idx]
    it["done"] = state.now_iso()
    if a.note:
        it["note"] = a.note
    plan_mod.write(home, sid, plan)
    ledger.append(home, "plan_done", session_id=sid, n=idx + 1, packet=it.get("packet"))
    print(f"plan: marked #{idx + 1} {_name(it.get('packet'))} done")
    return 0


def _bind(a, home, sid):
    plan, _p = _load(home, sid)
    idx = _index(a.n, len(plan["items"]))
    ex = _session_meta(home, a.session_sid)
    if a.packet is not None:
        if not _WHOLE.fullmatch(a.packet) or int(a.packet) < 1:
            raise PileadError(f"plan: --packet {a.packet!r} is not a packet number (1 or more)", 2)
        n = int(a.packet)
    else:
        try:
            meta = json.loads((state.session_dir(home, ex) / "meta.json").read_text())
            n = int(meta.get("packets") or 1)
        except Exception:  # noqa: BLE001
            raise PileadError(f"plan: the record of session {ex} cannot be read — pass --packet", 2)
    it = plan["items"][idx]
    it["executor"], it["packet_n"] = ex, n
    plan_mod.write(home, sid, plan)
    ledger.append(home, "plan_bind", session_id=sid, n=idx + 1, executor=ex, packet=n)
    print(f"plan: bound #{idx + 1} to {ex} packet {n}")
    return 0


def _next(a, home, sid):
    rows = plan_mod.rows(home, sid)
    if rows is None:
        raise PileadError(plan_mod.unreadable_line(plan_mod.path(home, sid)), 1)
    nxt = next((r for r in rows if r["state"] == "queued"), None)
    if nxt is None:
        return 1
    target = str(nxt.get("target") or "fresh")
    q = shlex.quote
    if target == "fresh":
        name = _name(nxt["packet_path"])
        topic = name[:-len("-packet.md")] if name.endswith("-packet.md") else Path(name).stem
        model = f" --model {q(nxt['model'])}" if isinstance(nxt.get("model"), str) and nxt["model"] else ""
        print(f"pilead spawn {q(plan_mod.lead_cwd(home, sid))} {q(topic)} {q(nxt['packet_path'])}{model} "
              f"--lead {q(sid)}")
    else:
        print(f"pilead send {q(target)} {q(nxt['packet_path'])}")
    return 0


_VERBS = {"status": _status, "add": _add, "rm": _rm, "done": _done, "bind": _bind, "next": _next}


def cmd_plan(a):
    home = state.home_dir(a.home)
    sid = _sid(a, home)  # first: an unusable id reads and writes nothing
    if not (home / "leads" / f"{sid}.json").is_file():
        raise PileadError(f"plan: {sid} is not a registered lead — run pilead lead-start first", 2)
    sub = a.sub or "status"
    if sub in _CHANGES and os.environ.get("PI_LEAD_ROLE") == "executor":
        raise PileadError("plan: an executor cannot change a lead's plan", 2)
    return _VERBS[sub](a, home, sid)


def register(verb):
    p = verb("plan", cmd_plan, "a lead's written plan: an ordered list of packets and where each stands")
    p.add_argument("--session", metavar="SID", help="the lead's session id (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.set_defaults(sub=None, json=False)
    sub = p.add_subparsers(dest="sub", metavar="[status|add|rm|done|next|bind]")

    def leaf(name, help_):
        q = sub.add_parser(name, help=help_, description=help_)
        # also accepted after the sub-verb; unset here so it never overwrites what came before it
        q.add_argument("--session", metavar="SID", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        q.add_argument("--home", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return q

    s = leaf("status", "show the plan (the default)")
    s.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the plan as JSON")
    s = leaf("add", "add a packet file to the plan")
    s.add_argument("packet", help="the packet file (looked for from the lead's cwd, then from here)")
    s.add_argument("--target", metavar="fresh|SID", help="a new executor (default) or an existing session")
    s.add_argument("--model", metavar="M", help="the model `plan next` puts on the spawn command")
    s.add_argument("--note", metavar="TEXT")
    s.add_argument("--before", metavar="N", help="put it before item N (default: at the end)")
    s = leaf("rm", "remove item N")
    s.add_argument("n", metavar="N")
    s = leaf("done", "mark item N done")
    s.add_argument("n", metavar="N")
    s.add_argument("--note", metavar="TEXT")
    s = leaf("bind", "bind item N to an executor's packet by hand")
    s.add_argument("n", metavar="N")
    s.add_argument("session_sid", metavar="SID", help="the executor session")
    s.add_argument("--packet", metavar="K", help="its packet number (default: its current packet)")
    leaf("next", "print the command for the first queued item (sends nothing)")

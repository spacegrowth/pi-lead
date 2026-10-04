"""pilead whoami — who a session is: a lead and its executors, or an executor and its lead (claude-relay
0.5.4's `cmd_whoami`). The token is the argument (an id, a lead's project or a unique id prefix, through
`names.resolve`), else $PI_LEAD_SID, else $PI_SESSION_ID. It reads state and writes nothing; a status is the
STORED one, never refreshed."""
import json
import os
from pathlib import Path

from .. import state
from ..state import PileadError

ORDER = 37
RESOLVE = ("token",)


def _read(path, token):
    try:
        rec = json.loads(Path(path).read_text())
        if not isinstance(rec, dict):
            raise ValueError("not an object")
        return rec
    except Exception:  # noqa: BLE001
        raise PileadError(f"whoami: the record of '{token}' cannot be read", 1) from None


def _text(v):
    return v if isinstance(v, str) and v else None


def _stored_status(home, sid, meta):
    try:
        return state.read_status(home, sid, meta)
    except Exception:  # noqa: BLE001 — a status file that cannot be read: the record's own
        return (meta or {}).get("status", "unknown")


def _execs_of(home, lead_sid):
    try:
        names = sorted(os.listdir(home / "sessions"))
    except OSError:
        return []
    out = []
    for sid in names:
        if not state.valid_session_sid(sid):
            continue
        mp = state.session_dir(home, sid) / "meta.json"
        try:
            meta = json.loads(mp.read_text()) if mp.is_file() else None
        except Exception:  # noqa: BLE001 — an unreadable executor is not listed
            meta = None
        if isinstance(meta, dict) and meta.get("lead") == lead_sid:
            out.append({"name": sid, "status": _stored_status(home, sid, meta)})
    return out


def _lead_data(home, sid, rec):
    return {"name": _text(rec.get("project")) or sid, "session": sid, "role": "lead",
            "backend": _text(rec.get("terminal")), "tab_label": _text(rec.get("label")),
            "iterm_session": _text(rec.get("iterm_handle")), "execs": _execs_of(home, sid)}


def _exec_data(home, sid, meta):
    n = meta.get("packets")
    n = n if isinstance(n, int) and not isinstance(n, bool) else None
    rp = None
    if n is not None:
        try:
            rp = state.report_path(home, sid, n)
        except Exception:  # noqa: BLE001 — a number the path cannot be made from
            n = None
    owner = _text(meta.get("lead"))
    orec = None
    if owner is not None and state.valid_lead_sid(owner):
        try:
            p = state.lead_path(home, owner)
            orec = json.loads(p.read_text()) if p.is_file() else None
        except Exception:  # noqa: BLE001
            orec = None
        if not isinstance(orec, dict):
            orec = None
    o = orec or {}
    data = {"name": sid, "session": sid, "role": "executor", "status": _stored_status(home, sid, meta),
            "owner_lead": owner, "current_packet": n, "report_path": str(rp) if rp is not None else None,
            "report_exists": rp.is_file() if rp is not None else None,
            "lead_backend": _text(o.get("terminal")), "lead_tab_label": _text(o.get("label")),
            "lead_iterm_session": _text(o.get("iterm_handle"))}
    return data, o


def cmd_whoami(a):
    token = a.token or os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID")
    if not token:
        raise PileadError("whoami: no token given, and neither $PI_LEAD_SID nor $PI_SESSION_ID is set", 2)
    home = state.home_dir(a.home)
    if state.valid_lead_sid(token) and state.lead_path(home, token).is_file():
        data = _lead_data(home, token, _read(state.lead_path(home, token), token))
        if a.json:
            print(json.dumps(data, indent=2))
            return 0
        execs = ", ".join(f"{e['name']} ({e['status']})" for e in data["execs"]) or "-"
        print(f"name       : {data['name']}")
        print(f"session    : {data['session']}")
        print("role       : lead")
        print(f"backend    : {data['backend'] or '-'}             tab '{data['tab_label'] or '-'}'")
        print(f"execs      : {execs}")
        return 0
    if state.valid_session_sid(token) and (state.session_dir(home, token) / "meta.json").is_file():
        meta = _read(state.session_dir(home, token) / "meta.json", token)
        data, orec = _exec_data(home, token, meta)
        if a.json:
            print(json.dumps(data, indent=2))
            return 0
        n = data["current_packet"]
        print(f"name       : {data['name']}")
        print(f"session    : {data['session']}")
        print("role       : executor")
        print(f"status     : {data['status']}          (packet {f'{n:04d}' if n is not None else '-'})")
        if data["owner_lead"] is not None:
            print(f"lead       : {data['owner_lead']}  {_text(orec.get('project')) or '-'}  "
                  f"[{data['lead_backend'] or '?'}]  tab '{data['lead_tab_label'] or '-'}'")
        else:
            print("lead       : -  (unowned)")
        return 0
    raise PileadError(f"whoami: could not resolve '{token}' to a known executor or lead", 1)


def register(verb):
    p = verb("whoami", cmd_whoami, "say who a session is: a lead and its executors, or an executor and its lead")
    p.add_argument("token", nargs="?",
                   help="an id, a lead's project or a unique id prefix (default $PI_LEAD_SID, else $PI_SESSION_ID)")
    p.add_argument("--json", action="store_true")

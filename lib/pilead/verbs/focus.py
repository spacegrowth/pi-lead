"""pilead focus — bring an executor's or a lead's iTerm2 tab to the front (claude-relay 0.5.4's `cmd_focus`).

`<name>` is, first match wins: an executor's sid (sessions/<name>/meta.json exists), a lead's sid (its record
exists), or the project of exactly one registered lead (compared trimmed, in lower case). A tab is found by its
HANDLE (sessions/<sid>/iterm-id, else meta.json `tab`; a lead's `iterm_handle`): pi writes its own terminal title,
`π - <session name> - <directory>`, which the vendored title matchers do not match. A handle goes stale when iTerm2
is restarted; `pilead lead-start` run again in a lead's tab records the new one.

It reads state and asks the terminal; it writes nothing and ledgers nothing. It never opens, closes, renames or
colours a tab, and never signals a process. iTerm2 not running: nothing is asked (asking would start it)."""
import json
import sys
from pathlib import Path

from .. import lifecycle, names, state, tabs
from ..state import PileadError

ORDER = 35
RESOLVE = ()


def _read(path, name):
    try:
        rec = json.loads(Path(path).read_text())
        if not isinstance(rec, dict):
            raise ValueError("not an object")
        return rec
    except Exception:  # noqa: BLE001
        raise PileadError(f"the record of '{name}' cannot be read", 1) from None


def _leads_by_project(home, name):
    want = name.strip().lower()
    out = []
    for sid, p in tabs.lead_record_paths(home):
        if not state.valid_lead_sid(sid):
            continue
        rec = tabs.read_json(p)
        proj = rec.get("project") if rec is not None else None
        if isinstance(proj, str) and proj.strip().lower() == want:
            out.append((sid, rec))
    return out


def _resolve(home, name):
    """("executor", sid, meta) or ("lead", sid, record); PileadError for the refusals."""
    if state.valid_session_sid(name):
        mp = state.session_dir(home, name) / "meta.json"
        if mp.exists():
            meta = _read(mp, name)
            meta["sid"] = name
            return "executor", name, meta
    if state.valid_lead_sid(name):
        lp = state.lead_path(home, name)
        if lp.exists():
            return "lead", name, _read(lp, name)
    try:
        hits = _leads_by_project(home, name)
    except OSError:
        hits = []
    if len(hits) == 1:
        return "lead", hits[0][0], hits[0][1]
    if len(hits) > 1:
        raise PileadError(f"'{name}' names {len(hits)} leads: {', '.join(sid for sid, _ in hits)} — give the sid", 2)
    resolved = names.resolve(home, name, projects=False)  # a unique id prefix; an ambiguous one is refused
    if resolved != name:
        return _resolve(home, resolved)
    raise PileadError(f"no session or lead named '{name}'", 1)


def cmd_focus(a):
    home = state.home_dir(a.home)
    kind, sid, rec = _resolve(home, a.name)
    from ..launch import backend
    if kind == "executor":
        if (rec.get("backend") or "iterm") not in ("iterm", "tmux"):
            raise PileadError(f"{sid} is not in an iTerm2 tab", 1)
        handle = lifecycle.tab_handle(rec, state.session_dir(home, sid)).strip()
        label = rec.get("label") if isinstance(rec.get("label"), str) else f"[Exec] {sid}"
    else:
        h = rec.get("iterm_handle")
        handle = h.strip() if isinstance(h, str) else ""
        label = rec.get("label") if isinstance(rec.get("label"), str) and rec.get("label") \
            else tabs.lead_label(rec.get("project"), sid)
    # the handle names its backend: a tmux pane (`tmux:%N`) is focused through tmux, anything else through iTerm
    be = backend.for_handle(handle)
    it = be if be is not None and be.NAME == "tmux" else backend.by_name("iterm")
    if not it.running():
        raise PileadError("no tmux server answers" if it.NAME == "tmux" else "iTerm2 is not running", 1)
    if handle and it.focus(label, handle, None) is True:
        if kind == "executor":
            print(f"focused {sid} (tab '{label}')")
        else:
            print(f"focused lead {rec.get('project') or sid} ({sid})")
        return 0
    if kind == "executor":
        print(f"pilead: could not focus {sid} — no open tab has its handle. pilead resume {sid} reopens it with its "
              f"conversation; pilead restart {sid} runs its packet again.", file=sys.stderr)
    else:
        print(f"pilead: could not focus lead {sid} — no open tab has its handle. Run pilead lead-start again in that "
              "lead's tab to record the tab it is in now.", file=sys.stderr)
    return 1


def register(verb):
    p = verb("focus", cmd_focus, "bring an executor's or a lead's iTerm2 tab to the front")
    p.add_argument("name", help="an executor's sid, a lead's sid, or a lead's project")

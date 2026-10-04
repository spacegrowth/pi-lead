"""pilead handoff — hand this lead's role to a fresh successor lead in a new tab (claude-relay 0.5.4's `cmd_handoff`).

The outgoing lead writes a memo for its successor (what is in flight, what is reviewed and committed, open
questions, next steps) and runs `pilead handoff <memo.md>`. Everything is checked before anything changes; a
refusal is exit code 2 with one line on stderr and changes nothing. Then, in this order:

  1. the successor's record `leads/<successor>.json` is written (registered BEFORE its first turn) with its empty
     inbox: the caller's cwd and terminal, `lineage_started`, `handoff_memo`, `launch_model`, `predecessor` (the
     caller's sid, project, tab handle and terminal) and the configured posture (no posture is handed on);
  2. the memo copy `handoffs/<successor>.md` is written (lib/pilead/handoff.py): the memo plus the SUCCESSOR
     AFTERCARE section. The memo itself is never changed;
  3. the successor's tab is opened next to the caller's with the launch `pilead resume <lead>` uses, on the
     model given (`--model`, else the model of the caller's LAST answer, from its session log — never a model
     recorded at a launch, never pi's default: no model, no handoff), its first prompt the one short message;
  4. `succession.move_lead(caller, successor, "handoff")` moves the executors, the plan and the pending wakes;
  5. ledger `lead_handoff`.

When step 2 or 3 fails, what steps 1-3 wrote is removed and this session is still the lead (exit code 1). Nothing
here types into a running pi."""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from .. import config, handoff as handoff_mod, ledger, lifecycle, models, posture, state, succession, tabs, usage
from ..launch import _backend_for
from ..state import PileadError

ORDER = 16
RESOLVE = ()



def _refuse(msg):
    raise PileadError(f"handoff: {msg}", 2)


def message(project, copy_path):
    """The successor's first prompt: the last argument of its launch line."""
    return (f"You are the successor lead for project '{project}', registered by pilead handoff. Read {copy_path} "
            "and follow its SUCCESSOR AFTERCARE section first.")


def _read_record(home, sid):
    p = state.lead_path(home, sid)
    try:
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # noqa: BLE001
        _refuse(f"this lead's record {p} cannot be read ({type(e).__name__}); nothing was handed off")
    return rec


def _read_memo(path):
    p = Path(path).expanduser()
    what = ("a memo holds what is in flight, what is reviewed and committed, open questions and next steps — write "
            "one and pass its path")
    if not p.exists():
        _refuse(f"the memo {p} does not exist; {what}")
    if not p.is_file():
        _refuse(f"the memo {p} is not a file; {what}")
    try:
        text = p.read_text()
    except (OSError, UnicodeDecodeError) as e:
        _refuse(f"the memo {p} cannot be read ({type(e).__name__}); {what}")
    if not text.strip():
        _refuse(f"the memo {p} holds nothing but white space; {what}")
    return p, text


def _posture(rec):
    """(autonomous True/False/None, tier word or None) of the caller; None = could not be read."""
    a = rec.get("autonomous", False)
    autonomous = a if isinstance(a, bool) else None
    tier, _src, readable = posture.tier_state(rec)
    return autonomous, (tier if readable else None)


def _owned(home, lead):
    """(every session whose meta.json names `lead` — what move_lead adopts —, the pinned ones among those that are
    not closed or dead), in sid order."""
    owned, pins = [], []
    d = Path(home) / "sessions"
    for sdir in sorted(d.iterdir()) if d.is_dir() else []:
        sid = sdir.name
        if not state.valid_session_sid(sid):
            continue
        try:
            meta = json.loads((sdir / "meta.json").read_text())
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(meta, dict) or meta.get("lead") != lead:
            continue
        owned.append(sid)
        if meta.get("keep") and state.read_status(home, sid, meta) not in ("closed", "dead"):
            pins.append(sid)
    return owned, pins


def _model(home, a, caller, cwd):
    """(model, where it came from) — `--model` resolved as `pilead spawn` resolves one, else the model of the
    caller's last answer in its session log; refused when neither is known."""
    if a.model is not None:
        return models.resolve(home, a.model), "from --model"
    try:
        u, _mb = usage.lead_reading(home, caller, cwd, write_cache=False)
    except Exception:  # noqa: BLE001
        u = None
    m = (u or {}).get("model")
    if not (isinstance(m, str) and m.strip()):
        _refuse("this lead's own model could not be read from its session log, and the successor must not start on "
                "pi's default model — pass --model <provider/id or alias>")
    return m, "from this lead's session log"


def _move_lines(res):
    """The lines of what move_lead did (the executors adopted, the wakes and the plan moved, what was kept)."""
    old, new = res["from"], res["to"]
    out = [f"lead {old} → {new} ({res['project'] or '-'})"]
    out += [line for _sid, line in res["sessions"]]
    out.append(f"moved {res['wake_lines']} pending wake line(s)")
    for p, n in res["claims_left"]:
        count = f"{n} line(s)" if n is not None else "lines unknown (unreadable)"
        out.append(f"kept {p} — a claim file of {old}, {count}: its process may still be delivering it")
    out += [f"kept {p} — {why}" for p, why in res["kept"]]
    plan = res["plan"]
    if plan == "moved":
        out.append("plan moved")
    elif isinstance(plan, tuple):
        out.append(f"plan not moved: {new} has its own plan {plan[2]}; {plan[1]} stays where it is")
    return out


def _undo(paths, ldir, made_dir):
    """Remove what steps 1-3 wrote; `made_dir` (handoffs/, when this run created it) goes too when it is empty."""
    for p in paths:
        try:
            if os.path.lexists(p):
                os.unlink(p)
        except OSError:
            pass
    shutil.rmtree(ldir, ignore_errors=True)
    if made_dir is not None:
        try:
            made_dir.rmdir()
        except OSError:
            pass


def _successor_color(home, caller, rec, successor):
    """The successor's `color`: the caller's usable colour (it is handed on, not shared), else a palette colour
    no other lead holds with the caller not counted as a holder; None when `tab_colors` is false."""
    if not config.load(home).get("tab_colors"):
        return None
    if tabs.usable_color(rec.get("color")):
        return list(rec["color"])
    return tabs.pick_lead_color(home, successor, exclude=(caller,))


def cmd_handoff(a):
    home = state.home_dir(a.home)
    caller = lifecycle.caller(home)
    if caller is None:
        _refuse("no calling lead — run it inside a registered lead session (pilead lead-start \"$PI_SESSION_ID\" "
                "--project <name> registers one)")
    if os.environ.get("PI_LEAD_ROLE") == "executor":
        _refuse("an executor cannot hand off a lead")
    state.check_lead_sid(caller)
    rec = _read_record(home, caller)
    memo_path, memo_text = _read_memo(a.memo)
    cwd = rec.get("cwd")
    if not (isinstance(cwd, str) and cwd and Path(cwd).is_dir()):
        _refuse(f"this lead's recorded cwd {cwd!r} is not a directory; the successor cannot start there")
    effort = None
    if a.effort is not None:
        from .spawn import _check_level
        effort = _check_level("--effort", a.effort)
    model, source = _model(home, a, caller, cwd)
    project = a.project or rec.get("project") or ""
    successor = state.check_lead_sid(str(uuid.uuid4()))
    from ..launch import live_handle
    handle = live_handle(home) or rec.get("iterm_handle") or None
    owned, pins = _owned(home, caller)
    inherited = Path(home) / "handoffs" / f"{caller}.md"
    if inherited.is_file():
        try:
            dropped = handoff_mod.dropped_markers(inherited.read_text(), memo_text)
        except (OSError, UnicodeDecodeError):
            dropped = []
        if dropped:
            print(f"pilead: handoff: the memo this lead was started from carried {', '.join(dropped)}, which this "
                  "memo does not; if they still hold, carry them forward word for word", file=sys.stderr)

    # ── 1. the successor's record and inbox
    rec_p = state.lead_path(home, successor)
    inbox = home / "leads" / f"{successor}.inbox.md"
    copy_p = home / "handoffs" / f"{successor}.md"
    ldir = lifecycle.launch_dir(home, successor)
    made_dir = None if os.path.lexists(copy_p.parent) else copy_p.parent
    new = {"sid": successor, "project": project, "cwd": cwd, "inbox": str(inbox), "terminal": rec.get("terminal"),
           "started": state.now_iso(), "lineage_started": rec.get("lineage_started") or rec.get("started"),
           "handoff_memo": str(copy_p), "launch_model": model,
           "predecessor": {"sid": caller, "project": rec.get("project"), "iterm_handle": handle,
                           "terminal": rec.get("terminal")},
           "autonomous": False, "autonomous_source": "config", "tier": "auto", "tier_source": "config",
           "label": tabs.lead_label(project, successor, suffix=True), "color": _successor_color(home, caller, rec, successor)}
    state.atomic_write(rec_p, json.dumps(new, indent=2) + "\n")
    inbox.write_text("")
    written = [rec_p, inbox]
    try:
        # ── 2. the memo copy
        written.append(copy_p)
        state.atomic_write(copy_p, handoff_mod.build_copy(memo_text, successor, project, len(owned), pins,
                                                         _posture(rec)))
        # ── 3. the tab
        ldir.mkdir(parents=True, exist_ok=True)
        be = _backend_for(rec, home)
        pi_args = dict(session_id=successor, model=None, extension=str(state.EXTENSION), rules=None, home=str(home),
                       lead=None, inbox=None, resume=False, thinking=effort, role="lead",
                       message=message(project, copy_p), lead_model=model)
        r = be.spawn(cwd, "", new["label"], str(ldir / "pid"), pi_args,
                     iterm_id_file=str(ldir / "iterm-id"), lead_handle=handle, layout="tab",
                     tab_color=tabs.owner_color(home, successor, config.load(home)), type_name=False)
        if not r.get("ok"):
            raise RuntimeError(f"tab launch failed ({r.get('reason')})")
    except Exception as e:  # noqa: BLE001 — undo steps 1-3: the caller is still the lead
        _undo(written, ldir, made_dir)
        why = " ".join(str(e).split()) or type(e).__name__
        print(f"pilead: handoff: {why}; nothing was handed off — this session is still the lead", file=sys.stderr)
        return 1
    new["iterm_handle"] = r.get("session_id") or None
    state.atomic_write(rec_p, json.dumps(new, indent=2) + "\n")
    placement = r.get("placement") or f"end-of-bar ({be.NAME} backend has no tab placement)"

    # ── 4. the move, only now that the tab is open; 5. the ledger
    # the caller is running this (LIVE `live`) and the successor is in another tab: its claim files stay with it
    # (relay 0.5.6 `_headless_env` also drops $TMUX_PANE: a child never claims the lead's pane)
    from ..launch import backend
    move_env = {k: v for k, v in os.environ.items() if k not in backend.LIVE_HANDLE_VARS}
    res = succession.move_lead(home, caller, successor, "handoff", project=project, live="live", env=move_env)
    ledger.append(home, "lead_handoff", from_lead=caller, to_lead=successor, project=project, memo=str(memo_path),
                  model=model)
    print(f"successor model: {model} ({source})")
    print(f"placement={placement}")
    for line in _move_lines(res):
        print(line)
    if pins:
        print(f"pinned: {', '.join(pins)} — still pinned under the successor; nothing closes them by itself")
    print(f"handoff complete — successor {successor} ({project}) is the lead; this session has stepped down")
    tabs.tidy_after(home, "handoff")  # the successor's tab was opened; cosmetic, last
    return 0


def register(verb):
    p = verb("handoff", cmd_handoff, "hand this lead's role to a fresh successor lead in a new tab, from a memo")
    p.add_argument("memo", help="the memo for the successor: in flight, reviewed and committed, open questions, next steps")
    p.add_argument("--project", metavar="NAME", help="the successor's project (default this lead's)")
    p.add_argument("--model", metavar="MODEL",
                   help="the successor's model, an alias or provider/id (default the model of this lead's last answer)")
    p.add_argument("--effort", metavar="LEVEL", help="pi thinking level for the successor")

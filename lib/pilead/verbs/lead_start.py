"""pilead lead-start — register a lead session and create its inbox.

Every run also resets the lead's autonomous posture (lib/pilead/posture.py) to config `autonomous_mode`,
source "config", whatever the record held before, and its model-tier posture to `auto` (source "config"): a
posture never silently outlives the session it was turned on in.

Registering `<sid>` removes a forward note `handoffs/<sid>.json` (a session that registers again is a lead again;
a note for it would send its executors' reports elsewhere). A record that came from a move (`moved_from`) or a
handoff (`predecessor`) keeps its `project`, whatever `--project` says, and its `lineage_started`, `moved_from`
and `predecessor`; a record written by `pilead handoff` also keeps `handoff_memo` and `launch_model`."""
import json
import os
import sys
from pathlib import Path

from .. import config, ledger, posture, state, tabs

ORDER = 10
RESOLVE = ()

AUTO_ON_LINE = ("AUTONOMOUS MODE ON (config autonomous_mode: true) — this lead proceeds by default on routine "
                "in-plan steps instead of waiting. pilead auto off reverts it for this session.")


def _tty(handle):
    """The terminal line of the tab `handle` names, asked through the backend the handle names: a tmux pane
    (`tmux:%N`) → `#{pane_tty}`; otherwise the iTerm tab (/dev/ttysNNN, one AppleScript call). None when unknown."""
    try:
        from ..launch import backend
        be = backend.for_handle(handle)
        if be is not None and be.NAME == "tmux":
            return be.tty_by_id(handle) or None
        return tabs.tty_by_id(handle) or None
    except Exception:  # noqa: BLE001 — no tty just means a banner falls back to osascript
        return None


def _label(home, sid, rec, project):
    """The lead's tab label: a record that came from a move or a handoff keeps a label it has; otherwise
    `[Lead] <project>`, with the ` ·<sid[:4]>` ending when another LIVE registered lead has exactly that label.
    Only leads holding that label are asked about (their tab's liveness costs one AppleScript call each)."""
    kept = rec.get("label")
    if (rec.get("predecessor") or rec.get("moved_from")) and isinstance(kept, str) and kept:
        return kept
    label = tabs.lead_label(project, sid)
    try:
        others = []
        for other, p in tabs.lead_record_paths(home):
            if other == sid:
                continue
            orec = tabs.read_json(p)
            if orec is not None and orec.get("label") == label:
                others.append(other)
        if others:
            from . import list as list_verb
            live = {ld["sid"]: ld["live"] for ld in list_verb.read_leads(home, config.load(home), write_cache=False)}
            if any(live.get(o) == "live" for o in others):
                return tabs.lead_label(project, sid, suffix=True)
    except Exception:  # noqa: BLE001 — a label is cosmetic
        pass
    return label


def _name_and_paint(rec):
    """The lead's own tab wears its colour. Under tmux (a `tmux:%N` handle) the lead's WINDOW is also renamed to its
    `label` (`tmux_backend.rename_by_id`: window only, nothing typed, a shared window left alone) and painted
    through `tabs.paint_tab`; an iTerm lead is painted through its terminal line, as before. Never raises."""
    try:
        handle = rec.get("iterm_handle")
        from ..launch import backend
        be = backend.for_handle(handle)
        if be is not None and be.NAME == "tmux":
            if isinstance(rec.get("label"), str) and rec["label"]:
                be.rename_by_id(handle, rec["label"])
            if tabs.usable_color(rec.get("color")):
                tabs.paint_tab(handle, rec.get("tty"), rec["color"])
            return
        if isinstance(rec.get("tty"), str) and rec["tty"] and tabs.usable_color(rec.get("color")):
            tabs.paint_tab(handle, rec["tty"], rec["color"])
    except Exception:  # noqa: BLE001 — a name or a colour is cosmetic
        pass


def cmd_lead_start(a):
    state.check_lead_sid(a.lead_sid)  # first: an unusable id reads, creates and writes nothing
    home = state.home_dir(a.home)
    p = state.lead_path(home, a.lead_sid)
    inbox = home / "leads" / f"{a.lead_sid}.inbox.md"
    rec = json.loads(p.read_text()) if p.is_file() else {"started": state.now_iso()}
    was_on = posture.autonomous_state(rec)[0]
    was_tier, _src, was_tier_readable = posture.tier_state(rec)
    on = config.load(home)["autonomous_mode"] is True
    project = a.project
    kept = rec.get("project") if isinstance(rec.get("project"), str) and rec.get("project") else None
    if (rec.get("moved_from") or rec.get("predecessor")) and kept is not None and kept != project:
        print(f"pilead: {a.lead_sid} took its role over from "
              f"{rec.get('moved_from') or rec.get('predecessor')} and keeps its project {kept!r}, not {project!r}",
              file=sys.stderr)
        project = kept
    note = home / "handoffs" / f"{a.lead_sid}.json"
    if os.path.lexists(note):
        os.unlink(note)
        print(f"pilead: removed the forward note {note} — {a.lead_sid} is a lead again, and its executors' "
              f"reports wake it", file=sys.stderr)
    from ..launch import live_handle, selected
    rec.update({"sid": a.lead_sid, "project": project, "cwd": str(Path(a.cwd or os.getcwd()).resolve()),
                "inbox": str(inbox),
                # the lead's own tab (iTerm `w#t#p#:UUID`, tmux `tmux:%N`), for placing executor tabs next to
                # it; refreshed on every re-run
                "iterm_handle": live_handle(home),
                "terminal": os.environ.get("TERM_PROGRAM") or None,
                "backend": selected(home).NAME,
                # the posture is reset on every run (lib/pilead/posture.py)
                "autonomous": on, "autonomous_source": "config", "tier": "auto", "tier_source": "config",
                # the pi-lead it was registered with (`pilead list` VER falls back to it)
                "version": state.version()})
    handle = rec["iterm_handle"]
    if handle:  # the tab's tty device, found once here: a banner writes to it by path (lib/pilead/notify.py)
        rec["tty"] = _tty(handle)
    else:
        rec.pop("tty", None)
    cfg = config.load(home)
    rec["label"] = _label(home, a.lead_sid, rec, project)
    rec["color"] = tabs.pick_lead_color(home, a.lead_sid) if cfg.get("tab_colors") else None
    rec["no_rename"] = bool(a.no_rename)  # true: the extension names no tab and the lead's tab is not painted
    state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
    if not a.no_rename:
        _name_and_paint(rec)  # cosmetic: the answer is not printed and changes nothing
    if not inbox.exists():
        inbox.write_text("")
    ledger.append(home, "lead_started", session_id=a.lead_sid, project=project)
    if on:
        ledger.append(home, "auto_mode_on", session_id=a.lead_sid, project=project, was=was_on, source="config")
    elif was_on:
        ledger.append(home, "auto_mode_off", session_id=a.lead_sid, project=project, was=True, source="arm")
    if was_tier_readable and was_tier in ("manual", "lead"):
        ledger.append(home, "tier_set", session_id=a.lead_sid, project=project, tier="auto", was=was_tier,
                      source="arm")
    print(f"lead {a.lead_sid} ready inbox={inbox}")
    if on:
        print(AUTO_ON_LINE)


def register(verb):
    p = verb("lead-start", cmd_lead_start, "register a lead session and create its inbox (idempotent)")
    p.add_argument("lead_sid"); p.add_argument("--project", required=True); p.add_argument("--cwd")
    p.add_argument("--no-rename", action="store_true", help="do not rename or colour the lead's own tab")

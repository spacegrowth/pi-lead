"""pilead status — one read-only line for a lead or an executor (claude-relay's `cmd_status`). It writes
NOTHING: no status file, no meta.json, no ledger, no usage cache. It shows the stored status; only an
existing report file upgrades it to `reported`. `--statusline` prints the shorter line pi's footer shows
(extensions/pi-lead.ts): it reads records, meta.json, status files, the lead's health file and whether
report files exist — never a session log."""
import json
import os
import re
from pathlib import Path

from .. import config, queue as queue_mod, state, status as status_mod, usage, wakehealth

ORDER = 55
RESOLVE = ("sid",)


def _read_json(p):
    try:
        d = json.loads(Path(p).read_text())
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _shown_status(home, sid, meta):
    """The stored status, upgraded to `reported` when the current packet's report file exists; a
    superseded session is shown as `superseded` (status.display), report or not."""
    stored = state.read_status(home, sid, meta)
    if status_mod.display(meta, stored) == "superseded":
        return "superseded"
    try:
        n = int(meta.get("packets") or 1)
        if state.report_path(home, sid, n).is_file():
            return "reported"
    except Exception:
        pass
    return stored


def _lead_executors(home, sid):
    """(busy, reported) sids of lead `sid`'s executors that are not closed or dead, in sid order."""
    busy, reported = [], []
    d = home / "sessions"
    for mp in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        esid = mp.parent.name  # a session's id is its directory's name, not the record's `sid` field
        if not state.valid_session_sid(esid):
            continue  # a directory named otherwise holds no session
        meta = _read_json(mp)
        if not meta or meta.get("lead") != sid:
            continue
        meta["sid"] = esid
        if state.read_status(home, esid, meta) in ("closed", "dead"):
            continue
        st = _shown_status(home, esid, meta)
        if st == "reported":
            reported.append(esid)
        elif st == "busy":
            busy.append(esid)
    return busy, reported


def lead_line(home, sid, rec):
    busy, reported = _lead_executors(home, sid)
    u = usage.for_lead(home, sid, rec.get("cwd") or None, write_cache=False)
    segs = [f"lead {rec.get('project') or '-'}"]
    if busy:
        segs.append("busy: " + ",".join(busy))
    if reported:
        segs.append("reported: " + ",".join(reported))
    segs.append("ctx " + usage.ctx_cell(u, usage.window(home, (u or {}).get("model"))))
    return " · ".join(segs)


def _whole(text, least):
    """`text` as a whole number >= `least`, else None (`abc`, `-1`, `1.5` are not)."""
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+", text):
        return None
    n = int(text)
    return n if n >= least else None


def _names(sids):
    """At most three names, then `+<n>` for the rest."""
    return ",".join(sids[:3]) + (f" +{len(sids) - 3}" if len(sids) > 3 else "")


def lead_statusline(home, sid, rec, ctx_tokens=None, ctx_window=None):
    """`🚦 <project, else lead>`, the busy and reported executors, the wake's cell when it is not ok, and the
    weight segment when `ctx_tokens` is a whole number >= 0 (against `lead_threshold` of pilead list)."""
    busy, reported = _lead_executors(home, sid)
    segs = [f"🚦 {rec.get('project') or 'lead'}"]
    if busy:
        segs.append("busy: " + _names(busy))
    if reported:
        segs.append("✅ " + _names(reported))
    word, _detail = wakehealth.state(home, sid)
    if word != "ok":
        segs.append(f"WAKE {wakehealth.CELL[word]}")
    tokens = _whole(ctx_tokens, 0)
    if tokens is not None:
        from . import list as list_verb  # lead_threshold: the lead's line, as pilead list draws it
        line = list_verb.lead_threshold(config.load(home), _whole(ctx_window, 1))
        if tokens >= line:
            segs.append(f"{usage.human_tokens(tokens)} ctx → hand off")
        elif tokens >= 0.6 * line:
            segs.append(f"{usage.human_tokens(tokens)} ctx")
    return " · ".join(segs)


def executor_segments(home, sid, meta):
    """`executor_line` without its ctx segment."""
    n = int(meta.get("packets") or 1)
    segs = [f"pkt {n:04d} {_shown_status(home, sid, meta)}"]
    qcount = queue_mod.count(home, sid)
    if qcount:
        segs.append(f"queued {qcount}")
    elif qcount is None:
        segs.append("queued ?")
    lead = meta.get("lead")
    rec = _read_json(state.lead_path(home, lead)) if state.valid_lead_sid(lead) else None
    if rec is not None:
        segs.append(f"for {rec.get('project') or '-'}")
    return segs


def executor_line(home, sid, meta):
    segs = executor_segments(home, sid, meta)
    u = usage.for_session(home, sid, meta.get("worktree") or None, write_cache=False)
    segs.append("ctx " + usage.ctx_cell(u, usage.window(home, meta.get("model"))))
    return " · ".join(segs)


def cmd_status(a):
    sid = a.sid or os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID")
    # the lead record is looked up only for a usable lead id, the session record only for a usable
    # session id; with neither, nothing is read and nothing is printed
    as_lead, as_session = state.valid_lead_sid(sid), state.valid_session_sid(sid)
    if not (as_lead or as_session):
        return 0
    home = state.home_dir(a.home)
    try:
        rec = _read_json(state.lead_path(home, sid)) if as_lead else None
        if rec is not None:
            if a.statusline:
                print(lead_statusline(home, sid, rec, a.ctx_tokens, a.ctx_window))
            else:
                print(lead_line(home, sid, rec))
            return 0
        meta = _read_json(state.session_dir(home, sid) / "meta.json") if as_session else None
        if meta is not None:
            if a.statusline:
                print(" · ".join(executor_segments(home, sid, meta)))
            else:
                print(executor_line(home, sid, meta))
    except Exception:  # noqa: BLE001 — a status line never fails loudly
        pass
    return 0


def register(verb):
    p = verb("status", cmd_status, "print one read-only status line for a lead or an executor")
    p.add_argument("sid", nargs="?", help="default $PI_LEAD_SID, else $PI_SESSION_ID")
    p.add_argument("--statusline", action="store_true",
                   help="the shorter line pi's footer shows (reads no session log)")
    p.add_argument("--ctx-tokens", metavar="N", help="with --statusline: the lead's context now, in tokens")
    p.add_argument("--ctx-window", metavar="N", help="with --statusline: the lead's context window, in tokens")

"""pilead list — every lead (LEADS) and every live executor (EXECUTORS), with an honest status
(lib/pilead/status.py), context and tokens (lib/pilead/usage.py) and footnotes for what needs a look.
claude-relay's `cmd_list`, without the columns that belong to later packets. Run by a registered lead, it
then parks that lead's finished executors (lib/pilead/autoclose.py) and prints one line per park last."""
import json
import os
import time
from pathlib import Path

from .. import (autoclose, config, ledger, lifecycle, notify, ownership, plan as plan_mod, posture, queue as queue_mod,
                review, state, status as status_mod, usage, wakefacts, wakehealth)
from ..views import c, model_id, relative_age, table

ORDER = 40
RESOLVE = ("lead",)
LIVE_WINDOW_SECONDS = 900  # a lead with no tab, active this recently, is `unreachable`; else `ghost`
CLOSED_CAP = 15

STATUS_STYLE = {"reported": ("green", "bold"), "busy": ("yellow",), "idle": (), "stalled": ("red",),
                "paused": ("yellow",), "dead": ("dim", "red"), "closed": ("dim",), "broken": ("red", "bold")}
LIVE_STYLE = {"live": ("green",), "unreachable": ("red", "bold"), "ghost": ("dim", "red"), "broken": ("red", "bold")}
AUTO_CELL = {True: ("AUTO", ("yellow", "bold")), False: ("-", ("dim",))}  # loud when a lead proceeds without asking
TIER_STYLE = {"auto": (), "manual": ("yellow", "bold"), "lead": ("yellow",), "?": ("red",), "-": ("dim",)}
WAKE_STYLE = {"ok": ("green",), "off": ("dim",), "stale": ("red", "bold"), "stuck": ("red", "bold"), "unknown": ("red",)}


def _wake(home, ld):
    """(word, detail) of a lead's wake (lib/pilead/wakehealth.py); a record that cannot be read is unknown."""
    if ld["broken"]:
        return "unknown", "the lead record cannot be read"
    return wakehealth.state(home, ld["sid"])


def _ver_cell(v):
    return (v, ()) if v is not None else ("?", ("dim",))


def _wake_cell(word):
    return (wakehealth.CELL[word], WAKE_STYLE[word])


def _wake_footnotes(home, leads):
    """One footnote per wake word that is not ok, naming the leads (project, else sid) in table order. With
    one lead the detail, count and sid are its own; with several, each detail is prefixed by its lead's name,
    the count is their sum (unknown when any is unknown) and the resume command names `<sid>`."""
    by_word = {}
    for ld in leads:
        word, detail = ld["wake"]
        if word != "ok":
            by_word.setdefault(word, []).append((ld, detail))
    out = []

    def names(items):
        return ", ".join(ld["rec"].get("project") or ld["sid"] for ld, _ in items)

    def details(items):
        if len(items) == 1:
            return items[0][1]
        return "; ".join(f"{ld['rec'].get('project') or ld['sid']}: {d}" for ld, d in items)

    if "stuck" in by_word:
        items = by_word["stuck"]
        out.append((f"  ⚠ WAKE=STUCK: {names(items)} — a wake was claimed and not delivered ({details(items)}); the next "
                    "drain retries it. If it stays, restart pi in that lead's tab.", "red"))
    if "stale" in by_word:
        items = by_word["stale"]
        counts = [wakehealth.waiting(home, ld["sid"]) for ld, _ in items]
        wait = "reports may wait" if any(n is None for n in counts) else f"{sum(counts)} report(s) wait"
        sid = items[0][0]["sid"] if len(items) == 1 else "<sid>"
        out.append((f"  ⚠ WAKE=STALE: {names(items)} — the lead's pi session stopped ({details(items)}); {wait} in its "
                    f"inbox until pi runs there again: pilead resume {sid}", "red"))
    if "off" in by_word:
        out.append((f"  ⚠ WAKE=off: {names(by_word['off'])} — registered, but no pi session with the pi-lead extension "
                    "is watching the inbox", "dim"))
    if "unknown" in by_word:
        items = by_word["unknown"]
        out.append((f"  ⚠ WAKE=?: {names(items)} — the health file could not be read ({details(items)})", "red"))
    return out


def _health_version(home, sid):
    """The `version` the lead's extension wrote into its health file, or None (no file, unreadable, no string)."""
    try:
        h = json.loads(wakehealth.health_path(home, sid).read_text())
        v = h.get("version") if isinstance(h, dict) else None
    except Exception:  # noqa: BLE001
        return None
    return v if isinstance(v, str) else None


def _version(home, ld):
    """The VER of a lead: its health file's `version` (what its running pi loaded), else its record's (what
    it registered with), else None (`?`)."""
    hv = _health_version(home, ld["sid"])
    if hv is not None:
        return hv
    rv = None if ld["broken"] else ld["rec"].get("version")
    return rv if isinstance(rv, str) else None


def _version_parts(v):
    """A dotted version as whole numbers, or None when it is not one (never a guess)."""
    if not isinstance(v, str) or not v:
        return None
    parts = v.split(".")
    if not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _older(a, b):
    """True when dotted version tuple `a` is older than `b` (missing parts count as 0)."""
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) < b + (0,) * (n - len(b))


def _old_extension_footnote(home, leads):
    """The leads whose pi is running (WAKE ok or stuck) an extension older than the installed pi-lead, as one
    footnote naming them in table order and the oldest such version; None when there is none or either side
    cannot be compared."""
    installed = state.version()
    inst = _version_parts(installed)
    if inst is None:
        return None
    old = []
    for ld in leads:
        if ld["wake"][0] not in ("ok", "stuck"):
            continue
        hv = _health_version(home, ld["sid"])
        parts = _version_parts(hv)
        if parts is not None and _older(parts, inst):
            old.append((ld, hv, parts))
    if not old:
        return None
    names = ", ".join(ld["rec"].get("project") or ld["sid"] for ld, _, _ in old)
    oldest = min(old, key=lambda t: t[2] + (0,) * (16 - len(t[2])))[1]
    return (f"  ⚠ old extension: {names} — its pi runs pi-lead {oldest}, {installed} is installed; run /reload in "
            "that pi (it loads the extension again)")


def _tidy_footnotes(home, leads):
    """One footnote per lead (table order) whose LATEST `tidy` / `tidy_skipped` ledger event, by `session_id`, is
    `tidy_skipped`."""
    last = {}
    for e in ledger.read(home):
        if e.get("event") in ("tidy", "tidy_skipped") and isinstance(e.get("session_id"), str):
            last[e["session_id"]] = e
    out = []
    for ld in leads:
        e = last.get(ld["sid"])
        if e is None or e.get("event") != "tidy_skipped":
            continue
        out.append(f"  · {ld['rec'].get('project') or ld['sid']}: the last tab tidy was skipped after "
                   f"{e.get('attempts', '?')} attempt(s) ({e.get('reason') or '-'}) — pilead tidy tries again")
    return out


LEGEND = ("  CTX = live/window (live: the context of the last completed request on the conversation's current "
          "branch; window from pi's model list, ! = a request was larger than that window); TOKENS = prompt/output "
          "billed so far, then the cache hit rate")


def _tier_cell(rec):
    """The TIER cell of a lead's row: its model-tier posture, `?` when it could not be read, `-` for a record
    that cannot be read at all (`rec` None)."""
    if rec is None:
        return ("-", TIER_STYLE["-"])
    tier, _source, readable = posture.tier_state(rec)
    word = tier if readable else "?"
    return (word, TIER_STYLE[word])


def _public_usage(u):
    return usage.public(u)


# ── leads ─────────────────────────────────────────────────────────────────────────────────────
def lead_files(home):
    d = Path(home) / "leads"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if usage.is_lead_record(p))


def lead_threshold(cfg, win):
    """lead_nudge_tokens, capped to context_nudge_tokens when the lead's window is exactly 200000."""
    t = cfg["lead_nudge_tokens"]
    return min(t, cfg["context_nudge_tokens"]) if win == 200000 else t


def read_leads(home, cfg, write_cache=True):
    """One dict per lead record: {"sid", "file", "broken", "rec", "usage", "mb", "window", "model",
    "last_active" (epoch or None), "live", "heavy", "threshold"}. `write_cache=False` (prune) reads
    each lead's usage without writing its cache."""
    now = time.time()
    out = []
    for p in lead_files(home):
        try:
            rec = json.loads(p.read_text())
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except Exception:
            out.append({"sid": p.stem, "file": p, "broken": True, "live": "broken"})
            continue
        sid = p.stem  # a lead's id is its record's file name, not the record's `sid` field (as state.load_meta)
        rec["sid"] = sid
        cwd = rec.get("cwd") or None
        u, mb = usage.lead_reading(home, sid, cwd, write_cache=write_cache)
        log = Path(u["path"]) if u and u.get("path") else (usage.locate(sid, cwd) if mb is not None else None)
        last = None
        try:
            last = log.stat().st_mtime if log is not None else None
        except OSError:
            last = None
        if last is None:
            age = state.age_seconds(rec.get("started"))
            last = now - age if age is not None else None
        model = (u or {}).get("model")
        win = usage.window(home, model)
        if status_mod.lead_tab_exists(rec):
            live = "live"
        elif last is not None and now - last < LIVE_WINDOW_SECONDS:
            live = "unreachable"
        else:
            live = "ghost"
        threshold = lead_threshold(cfg, win)
        out.append({"sid": sid, "file": p, "broken": False, "rec": rec, "usage": u, "mb": mb, "window": win,
                    "model": model, "last_active": last, "live": live, "threshold": threshold,
                    "heavy": usage.is_heavy(u, mb, threshold, cfg["handoff_nudge_mb"])})
    return out


def _proj_key(p):
    return str(p or "").strip().lower()


def reference_project(leads, lead_sid):
    """--lead's project; else that of the lead whose sid is $PI_LEAD_SID or $PI_SESSION_ID; else the
    current directory's name when a lead record has that project; else None."""
    by_sid = {ld["sid"]: ld for ld in leads if not ld["broken"]}
    for sid in (lead_sid, os.environ.get("PI_LEAD_SID"), os.environ.get("PI_SESSION_ID")):
        if sid and sid in by_sid:
            return _proj_key(by_sid[sid]["rec"].get("project"))
        if sid and sid == lead_sid:
            return None  # --lead names no readable lead: nothing to scope by
    here = _proj_key(os.path.basename(os.getcwd()))
    if here and here in {_proj_key(ld["rec"].get("project")) for ld in by_sid.values()}:
        return here
    return None


# ── executors ─────────────────────────────────────────────────────────────────────────────────
def read_sessions(home, cfg, write_cache=True, records=None, store=True):
    """One dict per sessions/*/meta.json, sid order: {"sid", "broken", "meta", "status", "reported",
    "usage", "mb", "window", "heavy", "approaching"}. The status comes from status.refresh; `store=False`
    (the board's read-only reading) passes it on, so a changed status is worked out and not stored."""
    home = Path(home)
    sdir = home / "sessions"
    out = []
    if not sdir.is_dir():
        return out
    warn, nudge, mb_th = cfg["context_warn_tokens"], cfg["context_nudge_tokens"], cfg["handoff_nudge_mb"]
    for mp in sorted(sdir.glob("*/meta.json")):
        sid = mp.parent.name  # a session's id is its directory's name, not the record's `sid` field
        if not state.valid_session_sid(sid):  # a directory named otherwise holds no session: a broken row
            out.append({"sid": sid, "broken": True, "file": mp, "unusable_name": True})
            continue
        try:
            meta = json.loads(mp.read_text())
            if not isinstance(meta, dict):
                raise ValueError("not an object")
            meta["sid"] = sid
            n = int(meta.get("packets") or 1)
        except Exception:
            out.append({"sid": sid, "broken": True, "file": mp})
            continue
        # Read first and hand it to the status engine: the session's log is parsed once per command.
        u, mb = usage.session_reading(home, meta["sid"], meta.get("worktree") or None, write_cache=write_cache)
        st = status_mod.refresh(home, meta, records, reading=(u, mb), store=store)
        win = usage.window(home, meta.get("model"))
        try:
            q_items, q_unreadable = queue_mod.read(home, meta["sid"])[0], None
        except queue_mod.QueueUnreadable as e:
            q_items, q_unreadable = None, e.path
        out.append({"sid": meta["sid"], "broken": False, "meta": meta, "status": st, "packet": n,
                    "reported": state.report_path(home, meta["sid"], n).is_file(), "usage": u, "mb": mb,
                    "window": win, "heavy": usage.is_heavy(u, mb, nudge, mb_th),
                    "approaching": usage.is_approaching(u, warn, nudge),
                    "queue_items": q_items, "queue_unreadable_path": q_unreadable})
    return out


def orphan_value(home, meta, status):
    """The `orphan` of an executor row (lib/pilead/ownership.py): true for owner state `orphan`, false for
    `owned` and `unowned`, None for `unknown`; false for a terminal session (closed or dead)."""
    if status in ("closed", "dead"):
        return False
    st = ownership.owner(home, meta)["state"]
    return True if st == "orphan" else None if st == "unknown" else False


def _owner_footnotes(home, rows):
    """The listed sessions that are not terminal whose owner is gone (one line naming them) or could not be
    read (one line each)."""
    orphans, unknown = [], []
    for s in rows:
        if s["broken"] or s["status"] in ("closed", "dead"):
            continue
        o = ownership.owner(home, s["meta"])
        if o["state"] == "orphan":
            orphans.append(s["sid"])
        elif o["state"] == "unknown":
            unknown.append((s["sid"], o["why"]))
    out = []
    if orphans:
        target = orphans[0] if len(orphans) == 1 else "<sid>"
        out.append((f"  ⚠ orphan: {', '.join(orphans)} — owned by a lead that is no longer registered; their reports "
                    f"wake nobody — pilead adopt {target}", "red"))
    for sid, why in unknown:
        out.append((f"  ⚠ owner unknown: {sid} ({why}) — inspect that file", "red"))
    return out


def _json_payload(home, leads, sessions):
    lrows = []
    for ld in leads:
        if ld["broken"]:
            lrows.append({"sid": ld["sid"], "broken": True})
            continue
        lrows.append({**ld["rec"], "live": ld["live"], "model": ld["model"], "usage": _public_usage(ld["usage"]),
                      "ctx_window": ld["window"], "heavy": ld["heavy"],
                      "last_active": status_mod.iso_utc(ld["last_active"]) if ld["last_active"] is not None else None,
                      "wake": ld["wake"][0], "waiting": ld["waiting"], "version": ld["ver"]})
    erows = []
    lead_project = {ld["sid"]: ld["rec"].get("project") for ld in leads if not ld["broken"]}
    for s in sessions:
        if s["broken"]:
            erows.append({"sid": s["sid"], "broken": True})
            continue
        erows.append({**s["meta"], "status": s["status"], "reported": s["reported"],
                      "project": lead_project.get(s["meta"].get("lead")), "usage": _public_usage(s["usage"]),
                      "ctx_window": s["window"], "heavy": s["heavy"], "approaching": s["approaching"],
                      "queued": len(s["queue_items"]) if s["queue_items"] is not None else None,
                      # the size of the staged diff, read only for a reported row (lib/pilead/wakefacts.py)
                      "diff": wakefacts.diff_size(s["meta"].get("worktree") or None)["text"]
                      if s["status"] == "reported" else None,
                      "orphan": orphan_value(home, s["meta"], s["status"])})
    return {"leads": lrows, "executors": erows}


def _unhanded_footnote(home, rows):
    """One footnote naming the reported executors whose report the lead has not been handed: a session in
    `rows` with status `reported`, a lead, a current-packet report on disk, no proof that its work landed
    (wakefacts) and no `wake_delivered` event in the ledger. The ledger is read once, and only when a row
    qualifies. None when there is no such row."""
    cands = [s for s in rows if not s["broken"] and s["status"] == "reported" and s["meta"].get("lead")
             and s["reported"]]
    if not cands:
        return None
    records = ledger.read(home)
    delivered = {(r.get("executor"), r.get("packet")) for r in records if r.get("event") == "wake_delivered"}
    items = []
    for s in cands:
        if (s["sid"], s["packet"]) in delivered:
            continue
        if wakefacts.landed(home, s["sid"], s["packet"], records)[1]:
            continue
        text = wakefacts.diff_size(s["meta"].get("worktree") or None)["text"]
        items.append(f"{s['sid']} (packet {s['packet']:04d})" + (f" {text}" if text else ""))
    if not items:
        return None
    return (f"  🚦 {len(items)} report(s) not yet handed to their lead: {', '.join(items)} — read with "
            "pilead check <sid>; the wake is tried again, do not wait for it.")


def _text_cell(v):
    """A record's text field as a table cell: `-` when it is missing, not text, or blank."""
    return v if isinstance(v, str) and v.strip() else "-"


# ── the verb ──────────────────────────────────────────────────────────────────────────────────
def _announce_approaching(home, sessions):
    """After the output: a banner, once each, for an executor that is approaching heavy (lib/pilead/notify.py)."""
    for s in sessions:
        if not s["broken"] and s["approaching"]:
            notify.ctx_warn(home, s["sid"], s["meta"], s["usage"], s["status"])


def cmd_list(a):
    home = state.home_dir(a.home)
    cfg = config.load(home)
    leads = read_leads(home, cfg)
    for ld in leads:
        ld["wake"] = _wake(home, ld)
        ld["waiting"] = None if ld["broken"] else wakehealth.waiting(home, ld["sid"])
        ld["ver"] = _version(home, ld)
    sessions = read_sessions(home, cfg)
    if a.json:
        print(json.dumps(_json_payload(home, leads, sessions), indent=2))
        _announce_approaching(home, sessions)
        autoclose.after_command(home, "list", to_stderr=True)
        review.refresh_after(home)  # last: a live board shows what list found
        return
    lead_project = {ld["sid"]: ld["rec"].get("project") for ld in leads if not ld["broken"]}

    # ── LEADS
    shown, hidden = leads, []
    if not a.all_leads:
        ref = reference_project(leads, a.lead)
        if ref is not None:
            shown = [ld for ld in leads if not (ld["live"] == "ghost" and _proj_key(ld["rec"].get("project")) != ref)]
            hidden = [ld for ld in leads if ld not in shown]
    print(c("LEADS", "bold"))
    if shown:
        now = time.time()
        rows = []
        for ld in shown:
            if ld["broken"]:
                rows.append([("-", ()), (ld["sid"], ()), ("-", ()), ("-", ()), ("broken", LIVE_STYLE["broken"]),
                             ("-", ()), ("-", ()), ("-", ()), AUTO_CELL[False], _tier_cell(None),
                             _wake_cell(ld["wake"][0]), _ver_cell(ld["ver"])])
                continue
            rec = ld["rec"]
            term = rec.get("terminal")
            term = "iterm" if term == "iTerm.app" else (term or "?")
            h = rec.get("iterm_handle")
            if rec.get("backend") == "tmux" or (isinstance(h, str) and h.startswith("tmux:%")):
                term = "tmux"  # a lead inside tmux (on a Mac $TERM_PROGRAM may still say the outer terminal)
            rows.append([(rec.get("project") or "-", ()), (ld["sid"], ()), (model_id(ld["model"]) or "-", ()),
                         (term, ("dim",) if term == "?" else ()), (ld["live"], LIVE_STYLE[ld["live"]]),
                         (usage.ctx_cell(ld["usage"], ld["window"]), ()),
                         (f"{ld['mb']:.1f}" if ld["mb"] is not None else "-", ()),
                         (relative_age(now - ld["last_active"]) if ld["last_active"] is not None else "-", ()),
                         AUTO_CELL[posture.autonomous_state(rec)[0]], _tier_cell(rec), _wake_cell(ld["wake"][0]),
                         _ver_cell(ld["ver"])])
        for line in table([("PROJECT", 22), ("SESSION", 36), ("MODEL", 24), ("TERM", 8), ("LIVE", 11),
                           ("CTX", 14), ("MB", 6), ("LAST ACTIVE", 12), ("AUTO", 4), ("TIER", 6), ("WAKE", 5),
                           ("VER", 8)], rows):
            print(line)
    elif not hidden:
        print("(no leads)")
    ok = [ld for ld in shown if not ld["broken"]]
    heavy = [ld for ld in ok if ld["heavy"]]
    if heavy:
        parts = ", ".join(f"{ld['rec'].get('project') or ld['sid']} ({usage.heavy_reading_text(ld['usage'], ld['mb'])}"
                          f", lead line {usage.human_tokens(ld['threshold'])})" for ld in heavy)
        print(c(f"  ⚠ heavy lead: {parts} — no verdict on the work, just a heads up: a lead this heavy "
                f"re-sends its whole context on every turn; a fresh lead session starts empty.", "yellow"))
    for ld in ok:
        if ld["usage"] is not None and usage.ctx_cell(ld["usage"], ld["window"]).endswith(" !"):
            print(c(f"  ⚠ {ld['sid']}: a request of {usage.human_tokens(ld['usage'].get('max_context') or 0)} "
                    f"was larger than the {usage.human_tokens(ld['window'])} window models.json gives its "
                    f"model — that window is wrong for this session", "red"))
    ghosts = [ld for ld in ok if ld["live"] == "ghost"]
    if ghosts:
        names = ", ".join(f"{ld['rec'].get('project') or '-'} ({ld['sid']})" for ld in ghosts)
        print(c(f"  ⚠ LIVE=ghost: {names} — no tab and not active in the last {LIVE_WINDOW_SECONDS // 60} min. "
                f"If that lead is still running, `pilead lead-start <sid> --project <name>` in its own tab "
                f"re-registers it.", "dim"))
    for ld in shown:
        if ld["broken"]:
            print(c(f"  ⚠ broken: lead record {ld['file']} cannot be read — inspect that file", "red"))
    if hidden:
        print(c(f"  {len(hidden)} ghost lead(s) of other projects hidden — `pilead list --all-leads` shows them",
                "dim"))
    auto = [ld for ld in ok if posture.autonomous_state(ld["rec"])[0]]
    if auto:
        names = ", ".join(ld["rec"].get("project") or ld["sid"] for ld in auto)
        manual = [ld["rec"].get("project") or ld["sid"] for ld in auto
                  if posture.tier_state(ld["rec"])[::2] == ("manual", True)]
        tier_note = (f"tier: manual for {', '.join(manual)} — every spawn stops for a model choice, under the "
                     "autonomous posture too. " if manual else "")
        print(c(f"  ⚡ AUTO: {names} — proceeding without asking on routine in-plan steps. Committing an executor's "
                f"work still needs every condition of pilead verify <sid> --for-autocommit. {tier_note}pilead auto off "
                f"--session <sid> reverts.", "yellow"))
    for text, colour in _wake_footnotes(home, ok):
        print(c(text, colour))
    old_ext = _old_extension_footnote(home, ok)
    if old_ext:
        print(c(old_ext, "yellow"))
    for text in _tidy_footnotes(home, ok):
        print(c(text, "dim"))
    print()

    # ── EXECUTORS
    print(c("EXECUTORS", "bold"))
    if a.lead and not a.all:  # --all: every executor; --lead still names the LEADS section's reference
        sessions = [s for s in sessions if s["broken"] or s["meta"].get("lead") in (None, "", a.lead)]
    live = [s for s in sessions if s["broken"] or s["status"] not in ("closed", "dead")]
    ended = [s for s in sessions if not s["broken"] and s["status"] in ("closed", "dead")]
    if a.closed:
        ended.sort(key=lambda s: str(s["meta"].get("created") or ""), reverse=True)
        rows_src, n_hidden = live + ended[:CLOSED_CAP], max(0, len(ended) - CLOSED_CAP)
    else:
        rows_src, n_hidden = live, len(ended)
    if rows_src:
        rows = []
        for s in rows_src:
            if s["broken"]:
                rows.append([(s["sid"], ()), ("broken", STATUS_STYLE["broken"])] + [("-", ())] * 9)
                continue
            m, st = s["meta"], s["status"]
            u = s["usage"]
            tok = usage.usage_cell(u)
            if u is not None and u.get("cache_hit_rate") is not None:
                tok += f" {round(u['cache_hit_rate'] * 100)}%"
            shown = "paused (limit)" if st == "paused" else status_mod.display(m, st)
            rows.append([(s["sid"], ()), (shown, STATUS_STYLE.get(st, ())),
                         (m.get("topic") or "-", ()), (_text_cell(m.get("scope")), ()),
                         (lead_project.get(m.get("lead")) or "-", ()),
                         (model_id(m.get("model")) or "-", ()), (f"{s['packet']:04d}", ()),
                         (usage.ctx_cell(u, s["window"]), ()), (tok, ()), (_text_cell(m.get("effort")), ()),
                         ("yes" if s["reported"] else "no", ("green", "bold") if s["reported"] else ("dim",))])
        for line in table([("SESSION", 36), ("STATUS", 14), ("TOPIC", 40), ("SCOPE", 30), ("PROJECT", 14),
                           ("MODEL", 24), ("PKT", 4), ("CTX", 14), ("TOKENS", 18), ("EFFORT", 7),
                           ("REPORTED", 8)], rows):
            print(line)
        print(c(LEGEND, "dim"))
    else:
        print("(no executor sessions)")
    ok = [s for s in rows_src if not s["broken"] and s["status"] not in ("closed", "dead")]
    heavy = [s for s in ok if s["heavy"]]
    if heavy:
        parts = ", ".join(f"{s['sid']} ({s['packet']} pkts / {usage.heavy_reading_text(s['usage'], s['mb'])})"
                          for s in heavy)
        print(c(f"  ⚠ heavy: {parts} — no verdict on the work, just a heads up: `pilead close <sid>` and a "
                f"fresh `pilead spawn` keep the next packet's context cheap; `pilead send <sid> <packet> "
                f"--heavy-override \"<reason>\"` keeps going in this one.", "yellow"))
    approaching = [s for s in ok if not s["heavy"] and s["approaching"]]
    if approaching:
        parts = ", ".join(f"{s['sid']} ({s['packet']} pkts / {usage.heavy_reading_text(s['usage'], s['mb'])})"
                          for s in approaching)
        print(c(f"  ⚠ approaching heavy: {parts} — past the {usage.human_tokens(cfg['context_warn_tokens'])} warn "
                f"line, not yet the {usage.human_tokens(cfg['context_nudge_tokens'])} heavy line; a fresh session "
                f"for the next packet is worth planning.", "yellow"))
    for s in rows_src:
        if not s["broken"] and s["usage"] is not None and usage.ctx_cell(s["usage"], s["window"]).endswith(" !"):
            print(c(f"  ⚠ {s['sid']}: a request of {usage.human_tokens(s['usage'].get('max_context') or 0)} was "
                    f"larger than the {usage.human_tokens(s['window'])} window models.json gives its model — "
                    f"that window is wrong for this session", "red"))
    for s in rows_src:
        if s["broken"] and s.get("unusable_name"):
            print(c(f"  ⚠ broken: {s['sid']} — the name of the directory {s['file'].parent} is not a usable "
                    "session id", "red"))
        elif s["broken"]:
            print(c(f"  ⚠ broken: {s['sid']} — {s['file']} cannot be read; inspect that file", "red"))
    for s in rows_src:
        if not s["broken"] and s["meta"].get("keep"):
            m = s["meta"]
            by = m.get("keep_by")
            who = (lead_project.get(by) or by) if isinstance(by, str) and by else None
            age = state.age_seconds(m.get("keep_at"))
            ago = relative_age(age) if age is not None else "- ago"  # unknown is `-`, never 0s
            print(c(f"  pinned: {s['sid']} by {who or '-'} {ago} — "
                    f"pilead keep {s['sid']} --off releases it", "dim"))
    if a.closed:
        for s in rows_src:
            if not s["broken"] and s["status"] == "closed" and s["meta"].get("auto_closed"):
                print(c(f"  auto-closed: {s['sid']} ({s['meta']['auto_closed']})", "dim"))
    for text, colour in _owner_footnotes(home, rows_src):
        print(c(text, colour))
    unhanded = _unhanded_footnote(home, rows_src)
    if unhanded:
        print(c(unhanded, "yellow"))
    if n_hidden:
        print(c(f"  (+{n_hidden} closed/dead hidden — pilead list --closed)", "dim"))
    for s in rows_src:
        if s["broken"]:
            continue
        sid = s["sid"]
        q_items, q_path = s["queue_items"], s["queue_unreadable_path"]
        if q_path is not None:
            print(c(f"  queue unreadable: {sid} — {q_path} cannot be read; nothing is delivered until it is "
                    "repaired or removed", "red"))
        elif q_items:
            head = q_items[0]
            if queue_mod.stuck_on_heaviness(head):
                print(c(f"  queue stuck: {sid} — {len(q_items)} packet(s) cannot be delivered because the "
                        f"session is heavy — pilead send {sid} {head.get('body_path')} --rotate carries the "
                        "head to a successor", "yellow"))
            elif head.get("last_error"):
                print(c(f"  queue failing: {sid} — {len(q_items)} queued packet(s) could not be delivered — "
                        f"{head['last_error']}", "red"))
            else:
                print(c(f"  queued: {sid} — {len(q_items)} waiting for the session to finish its current "
                        f"packet — pilead queue {sid}", "dim"))
    if a.plan:
        _print_plan(home, a.lead)
    _announce_approaching(home, sessions)
    autoclose.after_command(home, "list")  # last: the table above shows what the command found
    review.refresh_after(home)  # and a live board is rewritten


def _print_plan(home, lead):
    """`--plan`: an empty line, PLAN and `pilead plan status`'s table for the lead named by --lead, else the
    caller; nothing at all when there is no such lead. An unreadable plan prints its line (list still exits 0)."""
    sid = lead if lead is not None else lifecycle.caller(home)
    if not state.valid_lead_sid(sid) or not (Path(home) / "leads" / f"{sid}.json").is_file():
        return
    print()
    print(c("PLAN", "bold"))
    rows = plan_mod.rows(home, sid)
    if rows is None:
        print(plan_mod.unreadable_line(plan_mod.path(home, sid)))
        return
    for line in plan_mod.table_lines(rows):
        print(line)


def register(verb):
    p = verb("list", cmd_list, "list leads and executor sessions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--closed", action="store_true",
                   help=f"also list closed and dead sessions (the {CLOSED_CAP} most recently created)")
    p.add_argument("--lead", metavar="SID", help="only this lead's executors (and those with no lead)")
    p.add_argument("--all", action="store_true",
                   help="show every executor regardless of owner (the default without --lead)")
    p.add_argument("--all-leads", action="store_true",
                   help="also list ghost leads of other projects")
    p.add_argument("--plan", action="store_true",
                   help="after the executors, the plan of --lead (default: the calling lead)")

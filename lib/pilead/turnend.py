"""What is said to a lead when its turn ends (`pilead turn-end`, run by the lead's extension at `agent_settled`).

`run(home, lead_sid, cwd, dry_run=False, banner=False)` returns `{"lines": [...], "kinds": [...], "parked": [...]}`
and never raises. Four steps, in this order; an error in one is swallowed and the next still runs:

  1. the sweep: `autoclose.sweep(home, "turn-end", lead=lead_sid)` parks the lead's finished executors
     (`parked` is what it parked: `[sid, action, reason]` each; it adds no line)
  2. `commits`   the lead's own commits since the last turn         needs `surface_commits` for the lines
  3. `ctx-warn`  executors of this lead that are approaching heavy   needs `ctx_warn_wake`
  4. `handoff`   the lead's own session is heavy                      needs `handoff_nudge`

Each thing is said once: a claim (`wake/<lead>/notified/<executor>.ctx-warn-wake`) or a stamp
(`wake/<lead>/handoff-nudged`), made with an exclusive create BEFORE the line is added. A reading that could not
be made adds no line and uses up no claim or stamp; a claim or stamp that could not be written adds no line.
`kinds` names, in order, the steps that added lines.

With `banner`, and at least one line that is not a 🟠 line, one desktop banner is posted (`notify.banner`, needs
`notify_on_wake`). With `dry_run` nothing is written anywhere — no head file, stamp, claim, ledger event or
banner, and the sweep is a dry run — and the lines are those a real run would give.

The lead is never told to hand off, retire or commit: each line states what was seen."""
import os
import re
from pathlib import Path

from . import autoclose, config, ledger, notify, refs, state, status as status_mod, usage, wakehealth

MAX_COMMITS = 20
_COMMIT_ID = re.compile(r"[0-9a-f]{7,64}")
LIVE_STATUSES = ("busy", "idle", "stalled", "reported")
WARN_MARK = "  🟠"


def _wake_dir(home, lead_sid):
    return wakehealth.wake_dir(home, lead_sid)  # checks the id first


def _text_number(x):
    """A threshold as text: 5 → '5', 2.5 → '2.5'."""
    return f"{x:g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


# ── 1. the lead's own commits ────────────────────────────────────────────────────────────────────
def _read_head(path):
    """(top level, commit id) from the head file, or None when it is missing or not in the stored form."""
    try:
        top, tab, commit = Path(path).read_text().rstrip("\n").partition("\t")
        return (top, commit) if tab and top and commit and "\n" not in commit else None
    except Exception:  # noqa: BLE001
        return None


def _commit_lines(home, lead_sid, cwd, cfg, dry_run):
    """The `commits` lines. The head file `wake/<sid>/head` holds `<top level>` TAB `<commit id>`; it is
    written when it is missing, names another repository, or the commit moved (whatever `surface_commits` is)."""
    if not cwd:
        return []
    _, top = refs._git(cwd, "rev-parse", "--show-toplevel")
    _, head = refs._git(cwd, "rev-parse", "HEAD")
    top, head = top.strip(), head.strip()
    if not top or not head:
        return []
    path = _wake_dir(home, lead_sid) / "head"
    stored = _read_head(path)

    def write():
        if not dry_run:
            state.atomic_write(path, f"{top}\t{head}\n")

    if stored is None or stored[0] != top:
        write()
        return []
    if stored[1] == head:
        return []
    listed = []
    if _COMMIT_ID.fullmatch(stored[1]):
        try:
            _, out = refs._git(cwd, "rev-list", f"--max-count={MAX_COMMITS}", "--oneline", f"{stored[1]}..{head}")
            listed = [ln for ln in out.splitlines() if ln.strip()]
        except refs.RefsError:
            listed = []  # the stored commit is gone
    write()
    if not cfg["surface_commits"] or not listed:
        return []
    return [f"  📝 you made {len(listed)} commit(s) this turn in {cwd}:"] + [f"       {ln}" for ln in listed]


# ── 2. executors approaching heavy ───────────────────────────────────────────────────────────────
def _ctx_warn_lines(home, lead_sid, cfg, dry_run):
    warn, nudge = cfg["context_warn_tokens"], cfg["context_nudge_tokens"]
    if not warn < nudge:
        return []
    out = []
    sdir = Path(home) / "sessions"
    for mp in sorted(sdir.glob("*/meta.json")) if sdir.is_dir() else []:
        sid = mp.parent.name
        try:
            if not state.valid_session_sid(sid):
                continue
            meta = state.load_meta(home, sid)
            if not isinstance(meta, dict) or meta.get("lead") != lead_sid:
                continue
            u, mb = usage.session_reading(home, sid, meta.get("worktree") or None, write_cache=not dry_run)
            st = status_mod.refresh(home, meta, None, reading=(u, mb), store=not dry_run)
            if st not in LIVE_STATUSES or not usage.is_approaching(u, warn, nudge):
                continue
            key = f"{sid}.ctx-warn-wake"
            if dry_run:
                if (Path(home) / "wake" / lead_sid / "notified" / key).exists():
                    continue
            elif not notify.claim(home, lead_sid, key):  # last: nothing is used up before this
                continue
            out.append(f"  🟠 {sid} is approaching heavy ({usage.human_tokens(u['live'])} ctx, warn line "
                       f"{usage.human_tokens(warn)}, rotate line {usage.human_tokens(nudge)}) — plan a rotate: "
                       f"pilead retire {sid} and a fresh spawn for its next packet")
        except Exception:  # noqa: BLE001 — one executor never stops the others
            continue
    return out


# ── 3. the lead's own weight ─────────────────────────────────────────────────────────────────────
def _handoff_lines(home, lead_sid, cwd, cfg, dry_run):
    from .verbs.list import lead_threshold  # the lead's line, capped for a 200000 window
    u, mb = usage.lead_reading(home, lead_sid, cwd, write_cache=not dry_run)
    tokens = (u or {}).get("live")
    tokens = tokens if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else None
    win = usage.window(home, (u or {}).get("model"))
    line = lead_threshold(cfg, win)
    mb_line = cfg["handoff_nudge_mb"]
    over_tokens = tokens is not None and tokens >= line
    over_mb = mb is not None and mb >= mb_line
    if not (over_tokens or over_mb):
        return []
    stamp = _wake_dir(home, lead_sid) / "handoff-nudged"
    if dry_run:
        if stamp.exists():
            return []
    else:
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(stamp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))  # FIRST
        except OSError:
            return []
        ledger.append(home, "handoff_nudged", session_id=lead_sid,
                      mb=round(mb, 1) if mb is not None else None,
                      tokens=int(tokens) if tokens is not None else None)
    reading = (f"{usage.human_tokens(tokens)} live, line {usage.human_tokens(line)} on a "
               f"{usage._fmt_window(win) if win else '?'} window" if over_tokens
               else f"~{mb:.1f}MB log, past {_text_number(mb_line)}MB")
    return [f"  🔁 this lead session is getting heavy ({reading}). Consider handing off to a fresh lead session: "
            "write a handoff note, then run pilead handoff <note.md>."]


# ── the run ──────────────────────────────────────────────────────────────────────────────────────
def _message(line):
    """A line without its leading spaces and its sign (`  📝 you made …` → `you made …`)."""
    parts = line.strip().split(" ", 1)
    return parts[1].strip() if len(parts) == 2 else parts[0]


def run(home, lead_sid, cwd, dry_run=False, banner=False):
    result = {"lines": [], "kinds": [], "parked": []}
    try:
        home = Path(home)
        state.check_lead_sid(lead_sid)
        cfg = config.load(home)
        try:
            result["parked"] = [[sid, action, reason] for sid, action, reason
                                in autoclose.sweep(home, "turn-end", lead=lead_sid, dry_run=dry_run)]
        except Exception:  # noqa: BLE001
            pass
        steps = (("commits", True, lambda: _commit_lines(home, lead_sid, cwd, cfg, dry_run)),
                 ("ctx-warn", cfg["ctx_warn_wake"] is True, lambda: _ctx_warn_lines(home, lead_sid, cfg, dry_run)),
                 ("handoff", cfg["handoff_nudge"] is True, lambda: _handoff_lines(home, lead_sid, cwd, cfg, dry_run)))
        for kind, enabled, step in steps:
            if not enabled:
                continue
            try:
                lines = step()
            except Exception:  # noqa: BLE001 — an error in one step never stops the next
                continue
            if lines:
                result["lines"].extend(lines)
                result["kinds"].append(kind)
        lines = result["lines"]
        first = next((ln for ln in lines if not ln.startswith(WARN_MARK)), None)
        if banner and not dry_run and first is not None and cfg["notify_on_wake"] is True:
            try:
                rec = notify.lead_record(home, lead_sid)
                notify.banner(home, cfg, notify.project_title(rec, lead_sid), "review needed", _message(first),
                              lead_rec=rec, lead_sid=lead_sid)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return result

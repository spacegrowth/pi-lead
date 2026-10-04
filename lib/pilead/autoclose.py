"""Auto-close: park finished executors (claude-relay 0.5.4's `auto_close_decision` / `auto_close_sweep`,
rebuilt on pi-lead's state).

Executors finish, report, and then sit in open tabs for hours because nobody closes them. Closing loses
nothing: the report is on disk, staged work stays in the worktree, and `pilead send` to a closed session
reopens the same conversation. So a session is parked (closed, or retired when heavy, so its successor
seed is written) when ALL of these hold:

  - its status is `reported`, it is not pinned (`pilead keep`), it has a lead and nothing superseded it;
  - its lead has SEEN the report: `seen.looked_at` — the ledger holds `report_verify`, `report_reviewed`,
    `report_looked_at` or a `usage_snapshot` at "reported" for this session and its current packet, written
    by the lead or an unnamed caller, never by the executor itself (a report nobody looked at is never
    parked);
  - its inbox holds no packet waiting to be delivered, and its queue (lib/pilead/queue.py) holds none
    either — unless the queue's head is stuck on heaviness (its delivery refused by the heaviness gate,
    a deadlock, not work in flight): then the queue is emptied as the session is parked, one
    `queue_cancelled_on_park` event per item, body files kept. A queue that cannot be read is never parked;
  - and either the work LANDED (the report is at least GRACE_SECONDS old and `claims_landed` is True —
    or it is a clean report that says it changed nothing and the worktree is clean), or it has been
    reported for `auto_close_idle_minutes` (0 turns that rule off).

`claims_landed` is claude-relay 0.5.1's rule (backlog row 93): each claimed path is resolved to ONE
tracked file (itself, else the unique tracked path it is a `/`-suffix of — a report written relative
to a package says `resume.py` for `lib/pilead/verbs/resume.py`); a claim that resolves to nothing or
to more than one file makes the whole answer UNKNOWN, and so does a HEAD commit older than the report
(nothing was committed since it was written). Only a provable True lands; unknown falls to the idle
rule. Git is the real git (refs.git_path), never the `git` first on PATH; when it cannot be run in the
worktree everything it would tell is UNKNOWN, and unknown never lands. A park goes through `pilead close`'s
and `pilead retire`'s own functions; it deletes no file and writes only under home. `decide` is pure;
`sweep` never raises."""
import contextlib
import io
import json
import shlex
import time
from pathlib import Path

from . import config, ledger, lifecycle, queue as queue_mod, refs, seen as seen_mod, state, status as status_mod, usage

GRACE_SECONDS = 120  # a report written seconds ago is not judged mid-stage
ERROR_LEN = 200


# ── the decision (pure) ───────────────────────────────────────────────────────────────────────
def decide(meta, status, *, report_age, seen, inbox_pending, claimed, dirty, landed, heavy, no_change,
           idle_minutes, grace=GRACE_SECONDS, queued=0, queue_stuck_heavy=False):
    """(action, reason) — action `close`, or `retire` when `heavy`; reason `landed` or `idle <n>m` — or
    None. `report_age` seconds since the current report was written (None: unknown); `claimed` the
    paths the report claims; `dirty` the worktree's dirty paths (None: unknown, never clean); `landed`
    `claims_landed`'s tri-state (only True lands; False and None fall to the idle rule); `no_change`
    the report is a clean one that says it changed nothing; `queued` the number of queued packets (None:
    the queue cannot be read — never parked); `queue_stuck_heavy` the queue's head is stuck on heaviness,
    so the queue does not keep the session open."""
    meta = meta if isinstance(meta, dict) else {}
    if status != "reported" or meta.get("keep") or not meta.get("lead") or meta.get("superseded_by"):
        return None
    if report_age is None or not seen or inbox_pending:
        return None
    if queued is None or (queued > 0 and not queue_stuck_heavy):
        return None
    claimed = list(claimed or ())
    reason = None
    if report_age >= grace and dirty is not None and (
            (claimed and landed is True) or (no_change and not claimed and not dirty)):
        reason = "landed"
    elif idle_minutes and idle_minutes > 0 and report_age >= idle_minutes * 60:
        reason = f"idle {int(report_age // 60)}m"
    if reason is None:
        return None
    return ("retire" if heavy else "close"), reason


# ── what the sweep reads ──────────────────────────────────────────────────────────────────────
def dirty_paths(worktree):
    """Every path `git status --porcelain` lists in `worktree` (staged, modified, untracked; both
    sides of a rename), relative to the repository's top; None when git cannot be run there or fails
    (not a repository, no worktree, no real git)."""
    if not worktree or not Path(worktree).is_dir():
        return None
    try:
        # --no-optional-locks: status must not refresh the repository's index (nothing written there)
        r = refs._run(Path(worktree), "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all")
    except Exception:  # noqa: BLE001 — refs.RefsError (no real git, could not run): unknown
        return None
    if r.returncode != 0:
        return None
    out, parts, i = set(), r.stdout.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        out.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:  # -z: the rename's source is the next field
            if i < len(parts) and parts[i]:
                out.add(parts[i])
            i += 1
    return out


def repo_files(worktree):
    """Every path git tracks in `worktree` (`git ls-files`: the index, so a staged new file too), relative
    to the repository's top — the files a claimed path can resolve to; None when git cannot be run there
    or fails."""
    if not worktree or not Path(worktree).is_dir():
        return None
    try:
        r = refs._run(Path(worktree), "--no-optional-locks", "ls-files", "-z")
    except Exception:  # noqa: BLE001 — refs.RefsError: unknown
        return None
    if r.returncode != 0:
        return None
    return {p for p in r.stdout.split("\0") if p}


def head_time(worktree):
    """HEAD's commit time (unix seconds) in `worktree`; None with no repository, no commit or a git failure."""
    if not worktree or not Path(worktree).is_dir():
        return None
    try:
        r = refs._run(Path(worktree), "--no-optional-locks", "log", "-1", "--format=%ct")
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except ValueError:
        return None


def resolve_claims(claimed, files):
    """The tracked file each claimed path names — itself when tracked, else the ONE tracked path it is a
    `/`-suffix of (`report_verify.resolve_claim_suffixes`) — in claim order; None the moment any claim
    resolves to nothing or to more than one file (relay's `resolve_landed_claims`)."""
    from .review import report_verify  # vendored (review sets up its import path)
    files = set(files or ())
    claimed = list(claimed or ())
    rest = [p for p in claimed if p not in files]
    by_suffix, _ambiguous = report_verify.resolve_claim_suffixes(rest, sorted(files))
    out = []
    for p in claimed:
        if not p:
            return None
        if p in files:
            out.append(p)
        elif p in by_suffix:
            out.append(by_suffix[p])
        else:
            return None
    return out


def claims_landed(claimed, files, dirty, head, report_mtime):
    """Tri-state, claude-relay 0.5.1's `claims_landed` (pure): True — at least one claim, every claim
    resolved to a tracked file, none of them dirty, and HEAD's commit (whole seconds) at least as new as
    the report; False — a resolved path is still dirty; None — cannot prove it: no claims, tracked files,
    dirty paths, HEAD time or report time known, a claim that resolves to nothing or ambiguously, or HEAD
    older than the report. None must be read as "keep it open"."""
    if not claimed or files is None or report_mtime is None:
        return None
    resolved = resolve_claims(claimed, files)
    if not resolved or dirty is None:
        return None
    if head is None or head < int(report_mtime):
        return None  # nothing has been committed since the report was written
    return not (set(resolved) & set(dirty))


def _norm(path):
    p = str(path).strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def inbox_pending(home, sid):
    """True when sessions/<sid>/inbox.md holds anything but blank lines, or cannot be read."""
    p = state.session_dir(Path(home), sid) / "inbox.md"
    try:
        if not p.exists():
            return False
        return bool(p.read_text(errors="replace").strip())
    except OSError:
        return True


def _report_mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _candidates(home, sids, lead):
    """`sids`, else every session directory holding a meta.json and named by a usable session id (sid
    order); with `lead`, only those whose meta.json names it (one that cannot be read here belongs to nobody known and is left out)."""
    if sids is None:
        d = Path(home) / "sessions"
        sids = [mp.parent.name for mp in sorted(d.glob("*/meta.json"))
                if state.valid_session_sid(mp.parent.name)] if d.is_dir() else []
    out = []
    for sid in sids:
        if lead is None:
            out.append(sid)
            continue
        try:
            m = json.loads((state.session_dir(Path(home), sid) / "meta.json").read_text())
            if isinstance(m, dict) and m.get("lead") == lead:
                out.append(sid)
        except Exception:  # noqa: BLE001
            continue
    return out


# ── the sweep ─────────────────────────────────────────────────────────────────────────────────
def _consider(home, sid, lead, cfg, records, dry_run):
    """The decision for one session, its status brought up to date first; None when it stays."""
    meta = state.load_meta(home, sid)
    if not isinstance(meta, dict):
        raise ValueError(f"{state.session_dir(home, sid) / 'meta.json'} is not a JSON object")
    if lead is not None and meta.get("lead") != lead:
        return None, meta
    status = status_mod.refresh(home, meta, None if dry_run else records, store=not dry_run)
    if status != "reported" or meta.get("keep") or not meta.get("lead") or meta.get("superseded_by"):
        return None, meta
    n = int(meta.get("packets") or 1)
    rp = state.report_path(home, sid, n)
    mtime = _report_mtime(rp)
    age = None if mtime is None else max(0.0, time.time() - mtime)
    is_seen = seen_mod.looked_at(records, sid, n)  # the lead looked (lib/pilead/seen.py), never the executor
    pending = inbox_pending(home, sid)
    if age is None or not is_seen or pending:
        return None, meta
    from .review import report_verify  # vendored (review sets up its import path); loaded only when needed
    text = rp.read_text(errors="replace")
    claimed = [_norm(p) for p in report_verify.claimed_paths(text)[0]]
    no_change = report_verify.clean_no_change_report(text)
    wt = meta.get("worktree") or None
    dirty = dirty_paths(wt)
    landed = claims_landed(claimed, repo_files(wt), dirty, head_time(wt), mtime) if claimed else None
    u, mb = usage.session_reading(home, sid, wt, write_cache=False)
    heavy = usage.is_heavy(u, mb, cfg["context_nudge_tokens"], cfg["handoff_nudge_mb"])
    queued, stuck = queue_state(home, sid)
    return decide(meta, status, report_age=age, seen=is_seen, inbox_pending=pending, claimed=claimed,
                  dirty=dirty, landed=landed, heavy=heavy, no_change=no_change, idle_minutes=cfg["auto_close_idle_minutes"],
                  queued=queued, queue_stuck_heavy=stuck), meta


def queue_state(home, sid):
    """(queued, stuck on heaviness): the number of items in `sid`'s queue (None when it cannot be read)
    and whether its head is stuck on heaviness (`queue.stuck_on_heaviness`) and not being delivered."""
    try:
        items, _ = queue_mod.read(home, sid)
    except Exception:  # noqa: BLE001 — QueueUnreadable, or anything else: unknown, never empty
        return None, False
    return len(items), _is_stuck(items)


def _is_stuck(items):
    return bool(items) and queue_mod.stuck_on_heaviness(items[0]) and not items[0].get("claimed_at")


class Parked(tuple):
    """One entry of what `sweep` returns — (sid, action, reason), compared as that tuple — carrying the
    queued items the park cancelled (`cancelled`, oldest first; with `dry_run`, those it would cancel)."""
    cancelled = ()

    def __new__(cls, sid, action, reason, cancelled=()):
        t = super().__new__(cls, (sid, action, reason))
        t.cancelled = tuple(cancelled)
        return t


def _stuck_queue(home, sid):
    """The items of `sid`'s queue when its head is stuck on heaviness (and not claimed); else ()."""
    try:
        items, _ = queue_mod.read(home, sid)
    except Exception:  # noqa: BLE001
        return ()
    return tuple(items) if _is_stuck(items) else ()


def _cancel_stuck_queue(home, sid, action, reason, trigger):
    """Empty `sid`'s queue, stuck on heaviness, before it is parked: the items leave `queue.json`
    (`next_id` and the body files stay) and each gets a `queue_cancelled_on_park` event. Re-read under
    the queue's lock; a queue that is no longer stuck raises, so the park does not happen. Returns the
    items cancelled."""
    with queue_mod._held(home, sid):
        items, next_id = queue_mod.read(home, sid)
        if not _is_stuck(items):
            raise state.PileadError(f"the queue of {sid} changed while it was being parked; nothing was parked")
        queue_mod._write(home, sid, [], next_id)
    for it in items:
        ledger.append(home, "queue_cancelled_on_park", session_id=sid, queue_id=it["id"], source=it.get("source"),
                      body_path=it.get("body_path"), action=action, reason=reason, trigger=trigger)
    return tuple(items)


def _park(home, sid, meta, action, reason):
    """Close or retire through the verbs' own functions, their output captured and dropped."""
    from .verbs import retire as retire_verb
    captured = []
    sink = captured.append
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if action == "retire":
            retire_verb.check(home, sid, meta)
            retire_verb.retire(home, sid, meta, out=sink, err=sink)
        else:
            lifecycle.close_executor(home, sid, meta, reason=f"auto-close ({reason})", out=sink, err=sink)
    m = state.load_meta(home, sid)
    m["auto_closed"] = reason
    state.save_meta(home, m)


def sweep(home, trigger, sids=None, lead=None, dry_run=False):
    """Park what `decide` says to, among `sids` (else every session), only `lead`'s executors when it is
    given. Returns [(sid, action, reason)] parked — or, with `dry_run`, that would be parked (nothing is
    written then: no status, meta.json, ledger, usage cache or seed). Ledger `auto_closed` per park and
    `auto_close_error` per session that failed (left as it was; the sweep goes on). Never raises."""
    acted = []
    try:
        home = Path(home)
        cfg = config.load(home)
        if not cfg["auto_close"]:
            return acted
        records = ledger.read(home)  # once per sweep
        for sid in _candidates(home, sids, lead):
            try:
                decision, meta = _consider(home, sid, lead, cfg, records, dry_run)
                if decision is None:
                    continue
                action, reason = decision
                cancelled = _stuck_queue(home, sid)
                if not dry_run:
                    if cancelled:  # a queue stuck on heaviness is not left behind on a parked session
                        cancelled = _cancel_stuck_queue(home, sid, action, reason, trigger)
                    _park(home, sid, meta, action, reason)
                    ledger.append(home, "auto_closed", session_id=sid, action=action, reason=reason,
                                  trigger=trigger)
                acted.append(Parked(sid, action, reason, cancelled))
            except Exception as e:  # noqa: BLE001 — one session's failure never stops the sweep
                if not dry_run:
                    msg = " ".join(str(e).split()) or type(e).__name__
                    ledger.append(home, "auto_close_error", session_id=sid, error=msg[:ERROR_LEN],
                                  trigger=trigger)
    except Exception:  # noqa: BLE001
        pass
    return acted


# ── what is printed ───────────────────────────────────────────────────────────────────────────
def lines(home, acted, dry_run=False):
    """One line per park (`pilead auto-close`'s, and what list/check print after their own output)."""
    out = []
    for entry in acted:
        sid, action, reason = entry
        try:
            meta = state.load_meta(Path(home), sid)
        except Exception:  # noqa: BLE001
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        if action == "retire":
            wt, topic = meta.get("worktree"), meta.get("topic")
            head = "would auto-retire" if dry_run else "auto-retired"
            out.append(f"{head} {sid} ({reason}) — pilead spawn {shlex.quote(wt) if wt else '<worktree>'} "
                       f"{shlex.quote(topic) if topic else '<topic>'} <packet> --seed {sid} starts its successor")
        else:
            head = "would auto-close" if dry_run else "auto-closed"
            out.append(f"{head} {sid} ({reason}) — pilead send {sid} <packet> reopens it with its conversation")
        cancelled = getattr(entry, "cancelled", ())
        if cancelled:
            bodies = ", ".join(str(it.get("body_path")) for it in cancelled)
            if dry_run:
                out.append(f"  would cancel {len(cancelled)} queued packet(s) that were not delivered — bodies: {bodies}")
            else:
                out.append(f"  {len(cancelled)} queued packet(s) were not delivered and are no longer queued — "
                           f"bodies: {bodies}")
    return out


def after_command(home, trigger, sids=None, to_stderr=False):
    """`pilead list` / `pilead check`'s sweep, run after the command's own output: the calling lead's
    executors (among `sids` when given) are swept and one line per park is printed — on stderr when
    `to_stderr` (`--json`: stdout stays JSON only). No calling lead: nothing is swept or printed."""
    import sys
    try:
        lead = lifecycle.caller(home)
        if lead is None:
            return []
        acted = sweep(home, trigger, sids=sids, lead=lead)
        for line in lines(home, acted):
            print(line, file=sys.stderr if to_stderr else sys.stdout)
        return acted
    except Exception:  # noqa: BLE001 — a sweep never breaks the command it follows
        return []

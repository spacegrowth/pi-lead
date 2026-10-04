"""pilead prune — delete the state of sessions that ended long ago, and of ghost lead records.

A session is removed only when ALL hold: it is in scope; its STORED status is `closed` or `dead` (nothing
is recomputed); it is not pinned; its queue is empty or absent (a queued packet, or a queue that cannot be
read, keeps it); its age — now minus the newest modification time among the entries
directly inside sessions/<sid>/ — is known and greater than --days; and sessions/<sid> resolves to a
directory directly inside <home>/sessions. A lead record is removed only when it is in scope, its LIVE
value is `ghost`, it is not the caller, its last activity is known and older than --days, it has no
pending wake lines, and its plan has no open item or does not exist (the plan file goes with the record; a
plan with open items, or one that cannot be read, keeps it); the id naming its files is the record FILE's
name. Nothing outside <home> is deleted; ledger.jsonl gains only `pruned` / `lead_pruned`; `--dry-run` writes nothing at all."""
import json
import os
import re
import shutil
import sys
import time

from .. import config, ledger, lifecycle, queue as queue_mod, state, status as status_mod, wakehealth
from ..state import PileadError

ORDER = 97
RESOLVE = ("lead",)
DEFAULT_DAYS = 7
_DAYS = re.compile(r"[0-9]+")


def _days(text):
    if text is None:
        return DEFAULT_DAYS
    if not isinstance(text, str) or not _DAYS.fullmatch(text):
        raise PileadError(f"--days {text!r} is not a whole number of days (0 or more). Nothing was pruned.")
    return int(text)


def _newest_mtime(d):
    """The newest modification time (not following links) among the entries directly inside `d`; None
    when there are none or they cannot be read."""
    try:
        times = [e.stat(follow_symlinks=False).st_mtime for e in os.scandir(d)]
    except OSError:
        return None
    return max(times) if times else None


def _read_meta(sdir):
    meta = json.loads((sdir / "meta.json").read_text())
    if not isinstance(meta, dict):
        raise ValueError("not an object")
    return meta


def _session_candidates(home, scope_lead, cutoff_secs, now):
    """(to remove [(sid, path, stored, shown, age)], kept [(sid, reason)])."""
    sroot = home / "sessions"
    remove, kept = [], []
    if not sroot.is_dir():
        return remove, kept
    try:
        sreal = sroot.resolve()
        sessions_inside = sreal == home.resolve() / "sessions"
    except Exception:
        sreal, sessions_inside = None, False
    for p in sorted(sroot.iterdir()):
        sid = p.name
        if not state.valid_session_sid(sid) or not p.is_dir():
            continue  # not a session (list shows such a directory as broken)
        try:
            meta = _read_meta(p)
        except Exception:
            kept.append((sid, "unreadable"))
            continue
        if scope_lead is not None and meta.get("lead") != scope_lead:
            continue
        stored = state.read_status(home, sid, meta)
        if stored not in status_mod.TERMINAL:
            continue
        meta["sid"] = sid
        if meta.get("keep"):
            kept.append((sid, "pinned"))
            continue
        queued = queue_mod.count(home, sid)
        if queued != 0:
            kept.append((sid, "queue unreadable" if queued is None else "queued packets"))
            continue
        newest = _newest_mtime(p)
        if newest is None:
            kept.append((sid, "age unknown"))
            continue
        age = now - newest
        if age <= cutoff_secs:
            kept.append((sid, f"newer than {cutoff_secs // 86400} days"))
            continue
        try:
            inside = sessions_inside and p.resolve().parent == sreal and p.resolve().is_dir()
        except Exception:
            inside = False
        if not inside:
            kept.append((sid, "outside home"))
            continue
        remove.append((sid, p, stored, status_mod.display(meta, stored), age))
    return remove, kept


def _list_verb():
    """pilead.verbs.list (its read_leads is the one LIVE computation), imported when prune RUNS: a list
    module that fails to import never takes prune out of `--help` with it."""
    from . import list as list_verb
    return list_verb


def _lead_candidates(home, cfg, scope_project, all_, caller, cutoff_secs, now):
    """(to remove [(sid, project, age, plan file to remove with the record, or None)], kept [(sid, project,
    reason)])."""
    remove, kept = [], []
    lv = _list_verb()
    for ld in lv.read_leads(home, cfg, write_cache=False):
        if ld["broken"] or ld["live"] != "ghost":
            continue
        sid, project = ld["sid"], ld["rec"].get("project")
        if not all_ and (scope_project is None or lv._proj_key(project) != scope_project):
            continue
        if not state.valid_lead_sid(sid):
            kept.append((sid, project, "unusable id"))
            continue
        if sid == caller:
            kept.append((sid, project, "the calling lead"))
            continue
        last = ld["last_active"]
        if last is None:
            kept.append((sid, project, "age unknown"))
            continue
        if now - last <= cutoff_secs:
            kept.append((sid, project, f"newer than {cutoff_secs // 86400} days"))
            continue
        pending = lifecycle.pending_wake_lines(home, sid)
        if pending != 0:
            kept.append((sid, project, "pending wake lines"))
            continue
        plan_file, plan_why = lifecycle.plan_holds(home, sid)  # stored items only: nothing is bound or written
        if plan_why is not None:
            kept.append((sid, project, "plan cannot be read" if plan_why == "cannot be read"
                         else "plan has open items"))
            continue
        if not all(lifecycle.inside_home(home, f) for f in lifecycle.lead_files(home, sid)) \
                or (plan_file is not None and not lifecycle.inside_home(home, plan_file)):
            kept.append((sid, project, "outside home"))
            continue
        remove.append((sid, project, now - last, plan_file))
    return remove, kept


def cmd_prune(a):
    if a.lead is not None:
        state.check_lead_sid(a.lead)  # first: nothing is read, listed or removed for an unusable id
    days = _days(a.days)
    if a.lead is not None and a.all:
        print("pilead: prune takes --lead <sid> or --all, not both", file=sys.stderr)
        return 2
    home = state.home_dir(a.home)
    caller = lifecycle.caller(home)
    scope_lead = a.lead if a.lead is not None else caller
    if scope_lead is None and not a.all:
        print("pilead: prune needs a scope: --lead <sid> for that lead's sessions, or --all for every "
              "lead's (no calling lead was found)", file=sys.stderr)
        return 2
    if a.all:
        scope_lead = None
    scope_project = (_list_verb()._proj_key(lifecycle.lead_project(home, scope_lead)) or None) if scope_lead else None
    cfg = config.load(home)
    now = time.time()
    cutoff = days * 86400
    s_remove, s_kept = _session_candidates(home, scope_lead, cutoff, now)
    l_remove, l_kept = _lead_candidates(home, cfg, scope_project, a.all, caller, cutoff, now)

    done_s, done_l = [], []
    for sid, p, stored, shown, age in s_remove:
        if a.dry_run:
            done_s.append((sid, shown, age))
            continue
        try:
            if p.is_symlink():
                p.unlink()  # the link only: never what it points to
            else:
                shutil.rmtree(p)
        except OSError as e:
            s_kept.append((sid, f"could not be removed: {e.strerror or e}"))
            continue
        ledger.append(home, "pruned", session_id=sid, status=stored)
        done_s.append((sid, shown, age))
    for sid, project, age, plan_file in l_remove:
        plan_gone = plan_file is not None
        wake_dir = wakehealth.wake_dir(home, sid)
        wake_gone = os.path.lexists(wake_dir)  # a dry run names it when it exists
        if not a.dry_run:
            removed, left = lifecycle.remove_lead(home, sid, 0)
            wake_gone = wake_dir in removed
            if left:
                l_kept.append((sid, project, "; ".join(f"{p.name}: {why}" for p, why in left)))
                if not removed:
                    continue
            plan_gone = False
            if plan_file is not None:
                if os.path.lexists(lifecycle.lead_files(home, sid)[0]):
                    l_kept.append((sid, project, f"{plan_file.name}: kept with the record that stayed"))
                else:
                    why = lifecycle.remove_plan(home, sid)
                    plan_gone = why is None
                    if why is not None:
                        l_kept.append((sid, project, f"{plan_file.name}: {why}"))
            ledger.append(home, "lead_pruned", session_id=sid, project=project)
        done_l.append((sid, project, age, plan_file if plan_gone else None, wake_dir if wake_gone else None))

    if not (done_s or done_l or s_kept or l_kept):
        print("nothing to prune")
        return 0
    print(f"{'would prune' if a.dry_run else 'pruned'} {len(done_s)} session(s) and {len(done_l)} lead record(s):")
    for sid, shown, age in done_s:
        print(f"  {sid} ({shown}, {state.fmt_age(int(age))})")
    for sid, project, age, plan_file, wake_dir in done_l:
        print(f"  [lead] {project or '-'} ({sid}, {state.fmt_age(int(age))})")
        if plan_file is not None:
            print(f"    {'would remove' if a.dry_run else 'removed'} plan {plan_file}")
        if wake_dir is not None:
            print(f"    {'would remove' if a.dry_run else 'removed'} {wake_dir}")
    for sid, why in s_kept:
        print(f"  kept {sid}: {why}")
    for sid, project, why in l_kept:
        print(f"  kept [lead] {project or '-'} ({sid}): {why}")
    return 0


def register(verb):
    p = verb("prune", cmd_prune, "delete the state of sessions that ended long ago (and ghost lead records)")
    p.add_argument("--days", metavar="N", help=f"only what ended more than N days ago (default {DEFAULT_DAYS})")
    p.add_argument("--dry-run", action="store_true", help="print what would be removed; change nothing")
    p.add_argument("--lead", metavar="SID", help="that lead's sessions, and the lead records of its project "
                                                 "(default: the calling lead)")
    p.add_argument("--all", action="store_true", help="every session and lead record under home")

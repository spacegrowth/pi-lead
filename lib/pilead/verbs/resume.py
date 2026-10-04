"""pilead resume — reopen a closed or dead executor, or a crashed lead, WITH its conversation: pi is
launched again on the same `--session-id`, so the session's log (and the worktree's staged work) is
where it left off.

An executor is refused (exit code 2, nothing changed) unless `--force` is given when it is alive (two live
copies of one conversation would work against each other), when its tab was left open (its pi is gone
but the tab exists: a pi started by hand there would be a second copy), or when pi has no session log for
it (resuming would start an EMPTY conversation: `pilead restart` runs its packet again instead). A lead is
refused likewise when its LIVE value is `live` or pi has no log for it in its record's cwd, and always
when that cwd is not a directory. No refs baseline is written and no file is removed. Resuming an executor
is a claim of it: with a calling lead, once every refusal has passed, verbs/adopt.py `claim` adopts it when
its owner is gone, missing or a ghost."""
import json
import os
from pathlib import Path

from .. import config as config_mod, ledger, lifecycle, state, tabs, usage
from ..launch import _backend_for, _launch
from ..state import PileadError

ORDER = 32
RESOLVE = ("sid",)


def nudge(n, report):
    """The line an executor resumed with no report for its current packet finds in its inbox."""
    return (f"You were resumed after your tab closed. Your staged work in this worktree is intact. Pick up "
            f"packet {n:04d} where you left off, finish it, and write your report to {report}.")


def lead_message(project, sid):
    """The first prompt of a resumed lead (the last argument of its launch line)."""
    return (f"You are the resumed lead for project '{project}'. First run: pilead lead-start \"$PI_SESSION_ID\" "
            f"--project '{project}'. If your session id is not {sid}, also run: pilead stop {sid}. Then run "
            f"pilead list and continue where you left off.")


def _effort(value):
    from .spawn import _check_level
    return None if value is None else _check_level("--effort", value)


# ── an executor ───────────────────────────────────────────────────────────────────────────────
def _resume_executor(home, sid, a):
    meta = state.load_meta(home, sid)
    effort = _effort(a.effort)
    sdir = state.session_dir(home, sid)
    if not a.force:
        live = lifecycle.executor_liveness(meta, sdir)
        if live == "alive":
            raise PileadError(f"session {sid} is still alive; two live copies of one conversation would work "
                              "against each other. Close it first, or pass --force. Nothing was resumed.")
        if live == "tab-open":
            raise PileadError(f"session {sid}'s pi is gone but its tab is still open; a pi started by hand in "
                              f"that tab would be a second copy of this conversation. `pilead close {sid}` "
                              "closes the tab; or pass --force. Nothing was resumed.")
        if usage.locate(sid, meta.get("worktree") or None) is None:
            raise PileadError(f"pi has no session log for {sid}; resuming would start an EMPTY conversation. "
                              f"`pilead restart {sid}` runs its packet again as a new conversation; or pass "
                              "--force. Nothing was resumed.")
    from .adopt import claim  # at run time: an adopt module that fails to load never takes resume down
    claim(home, sid, meta)
    n = int(meta.get("packets") or 1)
    if effort is not None:
        meta["effort"] = effort
    label_open = meta.get("label_open") if meta.get("kept_tab") else None
    meta.pop("kept_tab", None)
    if label_open:
        meta["label"] = label_open  # a relaunched tab takes its open title, not `[closed] <sid>`
    state.save_meta(home, meta)
    tab, placement = _launch(home, meta, state.packet_path(home, sid, n), resume=True)
    report = state.report_path(home, sid, n)
    if report.is_file():
        state.write_status(home, sid, "reported")
    else:
        inbox = sdir / "inbox.md"
        pending = inbox.read_text().rstrip() if inbox.is_file() else ""
        state.atomic_write(inbox, (pending + "\n\n" if pending else "") + nudge(n, report) + "\n")
        state.write_status(home, sid, "busy")
    ledger.append(home, "resumed", session_id=sid, packet=n, effort=meta.get("effort"))
    print(f"resumed {sid} tab={tab}")
    if meta.get("superseded_by"):
        print(f"note: {sid} was superseded by {meta['superseded_by']}; that is still recorded")
    print(f"placement={placement}")
    tabs.tidy_after(home, "resume")  # a tab was opened; cosmetic, last
    return 0


# ── a lead ────────────────────────────────────────────────────────────────────────────────────
def _lead_log(sid, cwd):
    """pi's session log for `sid` whose header's cwd is `cwd` (where `pi --session-id` looks), or None."""
    p = usage.locate(sid, cwd)
    h = usage.header(p) if p is not None else None
    try:
        if h and isinstance(h.get("cwd"), str) and os.path.realpath(h["cwd"]) == os.path.realpath(cwd):
            return p
    except Exception:
        pass
    return None


def _resume_lead(home, sid, a):
    rec_path = state.lead_path(home, sid)
    try:
        rec = json.loads(rec_path.read_text())
    except (OSError, ValueError) as e:
        raise PileadError(f"lead record {rec_path} cannot be read ({e}). Nothing was resumed.")
    if not isinstance(rec, dict):
        raise PileadError(f"lead record {rec_path} is not an object. Nothing was resumed.")
    effort = _effort(a.effort)
    project = rec.get("project")
    cwd = rec.get("cwd")
    if not (isinstance(cwd, str) and cwd and Path(cwd).is_dir()):
        raise PileadError(f"lead {sid}'s cwd {cwd!r} is not a directory; its conversation cannot be reopened "
                          "there. Nothing was resumed.")
    if not a.force:
        from . import list as list_verb
        from .. import config
        live = next((ld["live"] for ld in list_verb.read_leads(home, config.load(home), write_cache=False)
                     if ld["sid"] == sid), None)
        if live == "live":
            raise PileadError(f"lead {sid} is live (its tab exists); two live copies of one conversation would "
                              "work against each other. Close its tab first, or pass --force. Nothing was resumed.")
        if _lead_log(sid, cwd) is None:
            raise PileadError(f"pi has no session log for lead {sid} in {cwd}; resuming would start an EMPTY "
                              "conversation. Pass --force to open it anyway. Nothing was resumed.")
    ldir = lifecycle.launch_dir(home, sid)
    ldir.mkdir(parents=True, exist_ok=True)
    (ldir / "pid").unlink(missing_ok=True)  # a previous launch's pid is not this one's
    be = _backend_for(rec, home)
    pi_args = dict(session_id=sid, model=None, extension=str(state.EXTENSION), rules=None, home=str(home),
                   lead=None, inbox=None, resume=True, thinking=effort, role="lead",
                   message=lead_message(project, sid))
    r = be.spawn(cwd, "", tabs.lead_label(project, sid), str(ldir / "pid"), pi_args,
                 iterm_id_file=str(ldir / "iterm-id"), lead_handle=None, layout="tab",
                 tab_color=tabs.owner_color(home, sid, config_mod.load(home)), type_name=False)
    if not r.get("ok"):
        raise PileadError(f"tab launch failed ({r.get('reason')}); nothing was started", 1)
    tab = r.get("session_id") or ""
    placement = r.get("placement") or f"end-of-bar ({be.NAME} backend has no tab placement)"
    rec["resumed"] = state.now_iso()
    state.atomic_write(rec_path, json.dumps(rec, indent=2) + "\n")
    ledger.append(home, "lead_resumed", session_id=sid, project=project)
    print(f"resumed lead {sid} ({project}) tab={tab}")
    print(f"placement={placement}")
    tabs.tidy_after(home, "resume")  # a tab was opened; cosmetic, last
    return 0


def cmd_resume(a):
    sid = state.check_session_sid(a.sid)
    home = state.home_dir(a.home)
    if state.session_dir(home, sid).is_dir() or not state.valid_lead_sid(sid) \
            or not state.lead_path(home, sid).is_file():
        return _resume_executor(home, sid, a)  # an unknown sid: load_meta's `unknown session <sid>`
    return _resume_lead(home, sid, a)


def register(verb):
    p = verb("resume", cmd_resume, "reopen a closed or dead executor, or a crashed lead, with its conversation")
    p.add_argument("sid", help="the executor's (or the lead's) session id")
    p.add_argument("--effort", metavar="LEVEL",
                   help="pi thinking level for the reopened session (an executor stores it)")
    p.add_argument("--force", action="store_true",
                   help="resume even when it looks alive, its tab is still open, or pi has no session log for it")

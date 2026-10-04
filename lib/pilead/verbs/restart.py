"""pilead restart — run an executor's CURRENT packet again as a NEW conversation.

A pi session id IS the conversation, so the new conversation is a successor session (`--name`, else
`<sid>-r<N>`, lifecycle.successor_name): same worktree, topic, scope, lead and layout, not pinned, the
old session's effort unless `--effort`, its packet number 1. Everything is checked before anything
changes, and a refusal leaves home as it was. Then the old session is closed as `pilead close` closes it,
the successor is created and launched by spawn's own function, and the old session is marked superseded
by it. If the launch fails, the old session stays closed and not superseded and no successor directory
is left. No file of the old session is removed. A queue that cannot be read, or whose head is being
delivered, is refused before anything changes; once the successor has launched, the packets queued for
the old session move to it (lib/pilead/queue.py `move`). Restarting is a claim of the session: with a
calling lead, once every refusal has passed, verbs/adopt.py `claim` adopts it when its owner is gone, missing
or a ghost, so the successor names the calling lead."""
import shutil
import sys

from .. import config, ledger, lifecycle, queue as queue_mod, state, status as status_mod, tabs
from ..state import PileadError

ORDER = 33
RESOLVE = ("sid",)


def packet_text(home, sid, n):
    """The lead's text of packet `n`: the file's content without the footer spawn/send appended, when it
    ends with exactly that footer; else the whole content."""
    text = state.packet_path(home, sid, n).read_text()
    foot = state.footer(home, sid, n)
    return text[:-len(foot)] if text.endswith(foot) else text


def _successor(home, name, sid):
    if name is None:
        return lifecycle.successor_name(home, sid)
    try:
        usable = state.safe_topic(name) == name and state.valid_session_sid(name)
    except PileadError:
        usable = False
    if not usable:
        raise PileadError(f"--name {name!r} is not usable as a session id: letters, digits, '.', '_' and '-' "
                          "only, first and last a letter or digit, no '..'. Nothing was restarted.")
    if state.session_dir(home, name).exists():
        raise PileadError(f"--name {name}: a session of that name already exists. Nothing was restarted.")
    return name


def cmd_restart(a):
    from . import spawn  # at run time: a spawn module that fails to load never takes restart out of --help
    sid = state.check_session_sid(a.sid)
    home = state.home_dir(a.home)
    sdir = state.session_dir(home, sid)
    if not sdir.is_dir() and state.valid_lead_sid(sid) and state.lead_path(home, sid).is_file():
        raise PileadError(f"{sid} is a lead; restart runs an executor's packet again (`pilead resume {sid}` "
                          "reopens a lead). Nothing was restarted.")
    meta = state.load_meta(home, sid)
    if meta.get("keep"):
        raise PileadError(f"session {sid} is pinned; `pilead keep {sid} --off` releases it. Nothing was restarted.")
    stalled = False
    if not a.force and lifecycle.executor_liveness(meta, sdir) == "alive":
        # what status.refresh would make of it, worked out without storing anything (a refusal writes nothing)
        now = status_mod._decide(home, meta, state.read_status(home, sid, meta))[0]
        if now != "stalled":
            raise PileadError(f"session {sid} is still alive ({now}); close it first, or pass --force. "
                              "Nothing was restarted.")
        stalled = True
    n = int(meta.get("packets") or 1)
    if not state.packet_path(home, sid, n).is_file():
        raise PileadError(f"packet {n:04d} of {sid} is missing ({state.packet_path(home, sid, n)}). "
                          "Nothing was restarted.")
    queue_mod.movable(home, sid, what="restarted")
    successor = _successor(home, a.name, sid)
    effort, effort_source = ((spawn._check_level("--effort", a.effort), "flag") if a.effort is not None
                             else spawn.inherited_effort(home, meta))
    effort = spawn._check_level("effort", effort)
    fallback_note = None
    if a.model is not None:
        model = spawn.choose_model(home, a.model, a.model_override)
    else:
        fallback = config.executor_models(home)[1]
        if state.read_status(home, sid, meta) == "paused" and fallback is not None:
            model = spawn.choose_model(home, fallback, a.model_override)
            fallback_note = (f"model: {sid} was paused at its usage limit on {meta.get('model')}; "
                             f"restarting on the fallback {model['model']}")
        else:
            model = spawn.choose_model(home, meta.get("model"), a.model_override)
    body = packet_text(home, sid, n)

    # every check passed: from here on things change
    from .adopt import claim  # at run time, as spawn above
    claim(home, sid, meta)  # the successor names the lead this session names after it
    if stalled:
        status_mod.refresh(home, meta)  # stores and logs `stalled`, as list or check would
        meta = state.load_meta(home, sid)
    lifecycle.close_executor(home, sid, meta, reason="restart", out=lambda _l: None, err=lambda _l: None)
    try:
        new, placement = spawn.spawn_session(
            home, lead=meta.get("lead"), worktree=meta.get("worktree"), topic=meta.get("topic") or sid, body=body,
            model=model, effort=effort, effort_source=effort_source, scope=meta.get("scope"), keep=False,
            layout=meta.get("layout"), source=str(state.packet_path(home, sid, n)), sid=successor)
    except Exception as e:  # noqa: BLE001 — whatever failed, no successor is left behind
        d = state.session_dir(home, successor)
        if d.exists():
            shutil.rmtree(d)
        why = str(e) if isinstance(e, PileadError) else f"{type(e).__name__}: {e}"
        print(f"pilead: {sid} was closed, but the restart did not happen: {why}", file=sys.stderr)
        return 1
    meta = state.load_meta(home, sid)
    meta["superseded_by"] = successor
    state.save_meta(home, meta)
    ledger.append(home, "restarted", session_id=sid, successor=successor, packet=n, model=model["model"])
    for line in spawn.spawned_lines(new, model, placement):
        print(line)
    if fallback_note:
        print(fallback_note)
    from .send import move_queue  # at run time, as spawn above
    code = move_queue(home, sid, successor, state.packet_path(home, sid, n))
    print(f"restarted {sid} as {successor} — packet {n:04d} runs again as a new conversation")
    tabs.tidy_after(home, "restart")  # a tab was opened; cosmetic, last
    return code


def register(verb):
    p = verb("restart", cmd_restart, "run an executor's current packet again as a new conversation (a successor)")
    p.add_argument("sid", help="the executor to restart")
    p.add_argument("--effort", metavar="LEVEL", help="pi thinking level (default: the session's own)")
    p.add_argument("--model", help="tier alias or provider/id (default: the session's own; a paused session's "
                                   "config executor_fallback_model when set)")
    p.add_argument("--model-override", metavar="REASON",
                   help="launch a model above config executor_model_ceiling; the reason is ledgered")
    p.add_argument("--name", help="the successor's session id (default: <sid>-r<N>)")
    p.add_argument("--force", action="store_true", help="restart even when the session is alive and not stalled")

"""pilead spawn — open an executor tab seeded with a packet.

The launch is decided in full before anything is written (README "Model and effort"): the model
(`choose_model`: `--model`, else the owning lead's tier posture when it is `manual` (refused) or `lead`
(the lead's own class; lib/pilead/posture.py `model_choice`), else config `executor_default_model`; the
fallback; the ceiling and its override) and the thinking effort (`choose_effort`: `--effort`, else the packet's `EFFORT:` line, else
config `executor_default_effort`). `spawn_session` then writes the session and launches it; other verbs
call it with choices of their own. `cmd_spawn` is the thin CLI caller. `--seed` appends a retired
session's successor seed (lib/pilead/seed.py) to the packet as inherited context."""
import json
import sys
import time
from pathlib import Path

from .. import config, ledger, lint, models, posture, refs, review, seed as seed_mod, state, tabs
from ..launch import _launch
from ..state import PileadError

ORDER = 20
RESOLVE = ("lead",)
# where a session's effort came from: "inherited" is `pilead restart`'s (the restarted session's own)
EFFORT_SOURCES = ("flag", "packet", "config", "inherited")
TIME_SUFFIX = "-%H%M%S"  # what spawn adds to the topic to make the session id
# The longest topic (after state.safe_topic) whose id is still at most state.SPAWN_SID_MAX characters.
TOPIC_MAX = state.SPAWN_SID_MAX - len(time.strftime(TIME_SUFFIX, time.gmtime(0)))


def _usable_spawn_sid(topic, sid):
    """`sid` when it is a usable session id of at most state.SPAWN_SID_MAX characters (room for a later
    `-r<N>`), else PileadError naming the topic and the id it gives."""
    if not (state.valid_session_sid(sid) and len(sid) <= state.SPAWN_SID_MAX):
        raise PileadError(f"topic {topic!r} gives the session id {sid!r}, which is not usable: use letters, "
                          "digits, '.', '_' and '-', first character a letter or digit, no '..', "
                          f"at most {TOPIC_MAX} characters")
    return sid


def _base_sid(topic):
    """(safe topic, the first session id to try) for a raw topic; PileadError when either is unusable.
    Reads nothing, so an unusable topic fails before `pi --list-models` can run."""
    safe = state.safe_topic(topic)
    return safe, _usable_spawn_sid(topic, f"{safe}{time.strftime(TIME_SUFFIX)}")


def choose_model(home, asked=None, override=None):
    """The model a new session launches on, as a dict {"asked", "model", "fallback", "ceiling",
    "override"}. `asked` (an alias or provider/id; None → config `executor_default_model`) is resolved
    (lib/pilead/models.py); when it is a known alias that matches no model and config
    `executor_fallback_model` is set, the fallback is resolved instead (`fallback` True) — a fallback
    that does not resolve gives the original error. A model above config `executor_model_ceiling`
    (`models.above_ceiling`) is refused with exit code 2 unless `override` is a reason that is not
    blank; `override` in the result is that reason when it was needed, else None. Writes nothing but
    models.json (the `pi --list-models` cache)."""
    default, fallback, ceiling = config.executor_models(home)
    asked = asked if asked is not None else default
    used_fallback = False
    try:
        model = models.resolve(home, asked)
    except models.UnmatchedAlias as err:
        if fallback is None:
            raise
        try:
            model = models.resolve(home, fallback)
        except PileadError:
            raise err
        used_fallback = True
    reason = None
    if models.above_ceiling(model, ceiling):
        if not (isinstance(override, str) and override.strip()):
            raise PileadError(f"model {model} is above the ceiling {ceiling}; to launch it anyway pass "
                              f'--model-override "<reason>"', 2)
        reason = override
    return {"asked": asked, "model": model, "fallback": used_fallback, "ceiling": ceiling, "override": reason}


def _check_level(name, value):
    """`value` as a thinking level, else PileadError (exit code 2) naming `name` and the levels."""
    level = lint.effort_level(value)
    if level is None:
        raise PileadError(f"{name} {value!r} is not a thinking level; use one of {', '.join(lint.EFFORT_LEVELS)}", 2)
    return level


def choose_effort(home, flag=None, body=""):
    """(level, source, note): the thinking level a new session launches with and where it came from —
    `flag` ("flag"; a value that is not a level is refused with exit code 2), else the packet's first
    `EFFORT:` line ("packet"), else config `executor_default_effort` ("config"). `note` is the one line to
    print on stderr when the packet's line holds no level (it is then ignored), else None."""
    if flag is not None:
        return _check_level("--effort", flag), "flag", None
    note = None
    raw = lint.packet_effort(body)
    if raw is not None:
        level = lint.effort_level(raw)
        if level is not None:
            return level, "packet", None
        note = (f"effort: the packet's EFFORT: line {raw!r} is not one of {'/'.join(lint.EFFORT_LEVELS)} "
                "— ignored")
    return config.executor_default_effort(home), "config", note


def inherited_effort(home, meta):
    """(level, source) a successor of the session `meta` launches with (`restart`, `send --rotate` and
    `--upgrade`): its stored `effort` ("inherited"), else config `executor_default_effort` ("config")."""
    effort = (meta or {}).get("effort")
    if effort:
        return effort, "inherited"
    return config.executor_default_effort(home), "config"


def _lead_project(home, lead):
    """The `project` of the lead's record at this moment (a non-empty text), else None. Never raises."""
    try:
        rec = json.loads(state.lead_path(Path(home), lead).read_text())
        proj = rec.get("project") if isinstance(rec, dict) else None
        return proj if isinstance(proj, str) and proj else None
    except Exception:  # noqa: BLE001
        return None


def spawn_session(home, *, lead, worktree, topic, body, model, effort, effort_source, scope=None, keep=False,
                  name=None, layout=None, source=None, sid=None, seed=None):
    """Create an executor session and launch it; returns (its meta.json record, the placement line).

    `body` is the packet TEXT; `model` is `choose_model`'s result; `effort` a level of lint.EFFORT_LEVELS
    and `effort_source` "flag" | "packet" | "config" | "inherited"; `scope` (default: the topic); `keep` pins the
    session at birth to `lead`. `sid` is used in place of a generated id (it must be a usable session id
    that names no session yet). `seed` is (seed text, its path) from seed.resolve: the text is appended to
    `body` as inherited context and the path stored as meta.json's `seed`. Ledgers `spawned`,
    `launch_options`, and `model_fallback` / `model_ceiling_override` / `seed_inherited` when they apply."""
    worktree = Path(worktree)
    if _check_level("effort", effort) != effort:
        raise PileadError(f"effort {effort!r} is not lower-case", 2)
    if effort_source not in EFFORT_SOURCES:
        raise PileadError(f"effort source {effort_source!r} is not {', '.join(EFFORT_SOURCES[:-1])} or "
                          f"{EFFORT_SOURCES[-1]}", 2)
    safe = state.safe_topic(topic)
    if sid is not None:
        if not state.valid_session_sid(sid):
            raise PileadError(f"{sid!r} is not a usable session id")
        if state.session_dir(home, sid).exists():
            raise PileadError(f"session {sid} already exists")
    else:
        base = _base_sid(topic)[1]
        sid, i = base, 1
        while state.session_dir(home, sid).exists():
            i += 1
            sid = _usable_spawn_sid(topic, f"{base}-{i}")
    scope = scope if isinstance(scope, str) and scope.strip() else safe
    if seed is not None:
        body = seed_mod.seeded_body(body, seed[0], seed[1])
    sdir = state.session_dir(home, sid)
    sdir.mkdir(parents=True)
    (sdir / "inbox.md").write_text("")
    packet = state.write_packet(home, sid, 1, body)
    meta = {"sid": sid, "lead": lead, "worktree": str(worktree), "topic": safe, "model": model["model"],
            "status": "busy", "created": state.now_iso(), "packets": 1, "label": name or f"[Exec] {sid}",
            "layout": config.executor_layout(home, layout), "effort": effort, "scope": scope,
            "owner_project": _lead_project(home, lead)}
    if keep:
        meta.update(keep=True, keep_by=lead, keep_at=state.now_iso())
    if seed is not None:
        meta["seed"] = seed[1]
    state.save_meta(home, meta)
    state.write_status(home, sid, "busy")
    refs.record_baseline(home, sid, 1, worktree)  # before the executor exists, so nothing it does is in the baseline
    try:
        tab, placement = _launch(home, meta, packet, resume=False)
    except PileadError:
        state.write_status(home, sid, "closed")
        raise
    ledger.append(home, "spawned", session_id=sid, lead=lead, worktree=str(worktree), topic=safe,
                  model=model["model"], tab_label=meta["label"], source=source)
    ledger.append(home, "launch_options", session_id=sid, effort=effort, effort_source=effort_source,
                  scope=scope, keep=bool(keep))
    if model["fallback"]:
        ledger.append(home, "model_fallback", session_id=sid, asked=model["asked"], model=model["model"])
    if model["override"] is not None:
        ledger.append(home, "model_ceiling_override", session_id=sid, model=model["model"],
                      ceiling=model["ceiling"], reason=model["override"])
    if seed is not None:
        ledger.append(home, "seed_inherited", session_id=sid, seed=seed[1])
    # a new session has used nothing: the zero snapshot, not a reading (lib/pilead/usage.py `snapshot`)
    ledger.append(home, "usage_snapshot", session_id=sid, packet=1, at="sent",
                  usage={"prompt": 0, "output": 0, "requests": 0})
    return meta, placement


def cmd_spawn(a):
    state.check_lead_sid(a.lead)  # first: an unusable --lead reads and creates nothing
    home = state.home_dir(a.home)
    state.load_lead(home, a.lead)
    worktree = Path(a.worktree).expanduser().resolve()
    if not worktree.is_dir():
        raise PileadError(f"worktree {a.worktree} is not a directory")
    src = Path(a.packet).expanduser()
    if not src.is_file():
        raise PileadError(f"packet {a.packet} not found")
    _base_sid(a.topic)  # before models.resolve, which may run `pi --list-models`
    body = src.read_text()
    inherited = seed_mod.resolve(home, a.seed) if a.seed is not None else None  # before anything is written
    effort, effort_source, note = choose_effort(home, a.effort, body)
    from_tier = posture.model_choice(home, a.lead, a.packet) if a.model is None else None  # the tier posture
    model = choose_model(home, a.model if a.model is not None else from_tier, a.model_override)
    if note:
        print(note, file=sys.stderr)
    lint.advise(body, str(worktree), model["model"])
    meta, placement = spawn_session(home, lead=a.lead, worktree=worktree, topic=a.topic, body=body, model=model,
                                    effort=effort, effort_source=effort_source, scope=a.scope, keep=a.keep,
                                    name=a.name, layout=a.layout, source=str(src.resolve()), seed=inherited)
    tier_line = tier_lead_line(from_tier, model) if from_tier is not None else None
    for line in spawned_lines(meta, model, placement, tier_line):
        print(line)
    if from_tier is not None:
        ledger.append(home, "model_from_tier", session_id=meta["sid"], lead=a.lead, lead_model=from_tier.lead_model,
                      model=model["model"])
    review.refresh_after(home)  # a live board is rewritten, before the cosmetic tidy
    tabs.tidy_after(home, "spawn")  # a tab was opened; cosmetic, after everything else


def tier_lead_line(chosen, model):
    """The line printed when the `lead` tier posture chose the model (`chosen` is posture.model_choice's
    result, `model` choose_model's)."""
    return f"model: tier lead — this lead runs {chosen.lead_model}; using {model['model']}"


def spawned_lines(meta, model, placement, tier_line=None):
    """What `pilead spawn` prints for a session spawn_session made (`restart` prints the same for its
    successor): `spawned …` first, `placement=…` last. `tier_line`, when the `lead` tier posture chose the
    model, comes right after the effort line."""
    out = [f"spawned {meta['sid']} model={meta['model']} tab={meta['tab']}",
           f"effort={meta['effort']} scope={meta['scope']}"]
    if tier_line:
        out.append(tier_line)
    if model["fallback"]:
        out.append(f"model: {model['asked']} is not available here; using the fallback {model['model']}")
    if model["override"] is not None:
        out.append(f"model: {model['model']} is above the ceiling {model['ceiling']}; override recorded")
    if meta.get("keep"):
        out.append("pinned")
    if meta.get("seed"):
        out.append(f"seed={meta['seed']}")
    out.append(f"placement={placement}")
    return out


def register(verb):
    p = verb("spawn", cmd_spawn, "open an executor tab seeded with a packet")
    p.add_argument("worktree"); p.add_argument("topic"); p.add_argument("packet")
    p.add_argument("--model", help="tier alias (haiku|sonnet|opus|fable|dsflash|dspro) or provider/id "
                                   "(default: config executor_default_model, else sonnet)")
    p.add_argument("--model-override", metavar="REASON",
                   help="launch a model above config executor_model_ceiling (default opus); the reason is ledgered")
    p.add_argument("--effort", metavar="LEVEL",
                   help=f"pi thinking level: {'|'.join(lint.EFFORT_LEVELS)} (default: the packet's EFFORT: "
                        "line, else config executor_default_effort, else high)")
    p.add_argument("--scope", help="what this executor covers, shown by `pilead list` (default: the topic)")
    p.add_argument("--keep", action="store_true", help="pin the session at birth (as `pilead keep`)")
    p.add_argument("--seed", metavar="PATH_OR_SID",
                   help="inherit a retired session's successor seed: a seed file, or the retired session's id "
                        "(`pilead retire`)")
    p.add_argument("--lead", required=True); p.add_argument("--name", help="tab label (default '[Exec] <sid>')")
    lay = p.add_mutually_exclusive_group()
    lay.add_argument("--pane", dest="layout", action="store_const", const="pane",
                     help="iTerm and tmux: split a pane in the lead's own window (a tab if the lead can't be found)")
    lay.add_argument("--tab", dest="layout", action="store_const", const="tab",
                     help="a new tab right of the lead's tab (default)")
    p.set_defaults(layout=None)  # neither flag: config.json `executor_layout`, else "tab"

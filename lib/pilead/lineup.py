"""What this machine's pi offers, and where a lead sits in it (claude-relay 0.5.4's `lineup_data` /
`_class_availability` / `tier_ask_data`, on pi's own model list).

`pi --list-models` (cached in `<home>/models.json`, lib/pilead/models.py) lists the models whose provider
has credentials here. A class (haiku, sonnet, opus, fable) is AVAILABLE when the list holds a model of it,
NOT AVAILABLE when the list was read and holds none, and UNKNOWN when the list could not be read; unknown
is never shown as either of the others. No model is named from memory: every model this module names comes
from the list or from the lead's own session log. Nothing here writes a file except what `models.load`
writes to `models.json`."""
import json
import os
from pathlib import Path

from . import lint, models, state, usage
from .state import PileadError

AVAILABLE, NOT_AVAILABLE, UNKNOWN = "available", "not available", "unknown"
RANKED = models.FAMILIES
UNRANKED = tuple(a for a in models.ALIASES if a not in models.FAMILIES)

DESCRIPTIONS = {
    "haiku": "mechanical",
    "sonnet": "workhorse default",
    "opus": "judgment where a wrong-but-plausible result survives review",
    "fable": "maximum judgment",
    "dsflash": "unranked; the ceiling does not apply",
    "dspro": "unranked; the ceiling does not apply",
}

STOP_LINE = "STOP: wait for the user to switch models before continuing as lead."


class ModelListUnknown(PileadError):
    """The model list could not be read; `why` is the first line of the reason. Exit code 1."""

    def __init__(self, why):
        super().__init__(why, 1)
        self.why = why


def _first_line(err):
    text = str(err).strip().splitlines()
    return text[0].strip() if text and text[0].strip() else type(err).__name__


def availability(home):
    """One entry per alias — the four classes in rank order, then dsflash and dspro:
    {"alias", "ranked", "state", "model" (available) or "why" (unknown)}. The model list is loaded once;
    when that raises, every entry is `unknown` with the error's first line as `why`."""
    home = Path(home)
    try:
        listed = models.load(home)
    except Exception as e:  # noqa: BLE001 — whatever went wrong, the list is unknown
        why = _first_line(e)
        return [{"alias": a, "ranked": a in RANKED, "state": UNKNOWN, "why": why} for a in models.ALIASES]
    out = []
    for a in models.ALIASES:
        try:
            out.append({"alias": a, "ranked": a in RANKED, "state": AVAILABLE, "model": models.pick(listed, a)})
        except models.UnmatchedAlias:
            out.append({"alias": a, "ranked": a in RANKED, "state": NOT_AVAILABLE})
    return out


def lead_model(home, sid):
    """The `provider/id` of the last answer in lead `sid`'s own pi session log (the active branch), or None
    when there is no usable id, no log or no answer yet. Reads only; writes no usage cache."""
    if not state.valid_lead_sid(sid):
        return None
    cwd = None
    try:
        rec = json.loads(state.lead_path(Path(home), sid).read_text())
        if isinstance(rec, dict) and isinstance(rec.get("cwd"), str) and rec["cwd"]:
            cwd = rec["cwd"]
    except Exception:  # noqa: BLE001 — no record, or one that cannot be read: look for the log by id alone
        cwd = None
    u, _mb = usage.lead_reading(Path(home), sid, cwd, write_cache=False)
    model = (u or {}).get("model")
    return model if isinstance(model, str) and model.strip() else None


def data(home, sid, ceiling):
    """The lineup payload: {session, model, model_source, class, classes, available, above, below,
    executor_ceiling, executors, model_check, stop}. See README "Model check and model tier"."""
    home = Path(home)
    classes = availability(home)
    model = lead_model(home, sid) if sid else None
    own_class = models.tier(model) if model else None
    listed_unknown = next((c for c in classes if c["state"] == UNKNOWN), None)
    available = [c["alias"] for c in classes if c["ranked"] and c["state"] == AVAILABLE]
    if own_class is None:
        above, below = [], []
    else:
        i = RANKED.index(own_class)
        above = [a for a in available if RANKED.index(a) > i]
        below = [a for a in available if RANKED.index(a) < i]
    resolved = {c["alias"]: c["model"] for c in classes if c["state"] == AVAILABLE}
    executors = ([a for a in available if not models.above_ceiling(resolved[a], ceiling)]
                 + [c["alias"] for c in classes if not c["ranked"] and c["state"] == AVAILABLE])
    ex = ", ".join(executors) or "none"
    label = model or "unknown model"
    stop = False
    if listed_unknown is not None:
        line = (f"Model check: {label} — pi's model list could not be read ({listed_unknown['why']}), so nothing "
                "can be said about the classes on this machine.")
    elif model is None:
        line = ("Model check: unknown model — this session's log holds no answer yet, so its model could not be "
                f"read. Executors available here: {ex}.")
    elif own_class is None:
        line = (f"Model check: {model} — not one of the ranked classes, so it cannot be placed among them. "
                f"Executors available here: {ex}.")
    elif not available:
        line = f"Model check: {model} — no ranked class is available on this machine. Executors available here: {ex}."
    elif not below:
        stop = True
        line = (f"Model check: {model} — the weakest class available on this machine; nothing to delegate down "
                f"to. Please switch this session to {available[-1]} with /model first.")
    elif not above:
        line = (f"Model check: {model} — the strongest class available on this machine. Proceeding as lead; "
                f"executors available here: {ex}.")
    elif len(above) == 1:
        line = (f"Model check: {model} — one stronger class ({above[0]}) is available here if you want it; this "
                f"class suits lead work. Executors available here: {ex}.")
    else:
        line = (f"Model check: {model} — {len(above)} stronger classes ({', '.join(above)}) are available here. "
                f"Recommend /model {above[-1]} now; otherwise proceeding as it is. Executors available here: {ex}.")
    return {"session": sid, "model": model, "model_source": "session log" if model else None, "class": own_class,
            "classes": classes, "available": available, "above": above, "below": below,
            "executor_ceiling": ceiling, "executors": executors, "model_check": line, "stop": stop}


def text_lines(d, home):
    """What `pilead lineup` prints for `data`'s result."""
    out = [d["model_check"]]
    if d["stop"]:
        out.append(STOP_LINE)
    src = f" (from the {d['model_source']})" if d["model_source"] else ""
    out.append(f"  this session: {d['model'] or '-'}{src}, class {d['class'] or '?'}")
    for c in d["classes"]:
        if c["state"] == AVAILABLE:
            cap = ""
            if c["ranked"] and c["alias"] not in d["executors"]:
                cap = f"  [above executor ceiling {d['executor_ceiling']}]"
            out.append(f"  {c['alias']}  available → {c['model']}{cap}")
        elif c["state"] == NOT_AVAILABLE:
            out.append(f"  {c['alias']}  NOT available — pi's model list has no such model")
        else:
            out.append(f"  {c['alias']}  unknown — {c['why']}")
    out.append(f"  source: pi --list-models, cached in {Path(home) / 'models.json'}")
    return out


# ── `pilead tier ask` ────────────────────────────────────────────────────────────────────────────
def recommendation(packet_path):
    """The rubric's class for one packet from packet lint's shape hints: `opus` for `shape-opus`, `haiku` for
    `shape-haiku`, else `sonnet` (also with no packet, or one that cannot be read)."""
    if not packet_path:
        return "sonnet"
    try:
        body = Path(packet_path).expanduser().read_text()
    except (OSError, ValueError):
        return "sonnet"
    codes = {code for _level, code, _msg in lint.lint_packet(body)}
    return "opus" if "shape-opus" in codes else "haiku" if "shape-haiku" in codes else "sonnet"


def ask_data(home, packet_path, lead_class, ceiling):
    """The question `pilead tier ask` prints, as {header, options, omitted_above_ceiling, provenance}, or None
    when no option is available at all; ModelListUnknown when the model list could not be read. Options: the
    recommended class first, then the other available classes not above the ceiling in rank order, then the
    available unranked aliases."""
    classes = availability(home)
    unknown = next((c for c in classes if c["state"] == UNKNOWN), None)
    if unknown is not None:
        raise ModelListUnknown(f"pi's model list could not be read ({unknown['why']}), so no executor model can "
                               "be offered")
    by = {c["alias"]: c["model"] for c in classes if c["state"] == AVAILABLE}
    omitted = [a for a in RANKED if a in by and models.above_ceiling(by[a], ceiling)]
    order = [a for a in RANKED if a in by and a not in omitted] + [a for a in UNRANKED if a in by]
    if not order:
        return None
    rec = recommendation(packet_path)
    if rec not in order:
        rec = order[0]
    order = [rec] + [a for a in order if a != rec]
    name = Path(packet_path).name if packet_path else "this spawn"
    options = [{"alias": a, "resolved": by[a], "description": DESCRIPTIONS[a], "recommended": a == rec,
                "lead_tier": a == lead_class} for a in order]
    return {"header": f"Executor model for {name}?", "options": options, "omitted_above_ceiling": omitted,
            "provenance": {"available": len(options), "ceiling": ceiling}}


def ask_lines(d):
    """The lines `pilead tier ask` prints for `ask_data`'s result."""
    out = [d["header"], ""]
    for o in d["options"]:
        tags = [t for t, on in (("Recommended", o["recommended"]), ("lead's tier", o["lead_tier"])) if on]
        out.append(f"  {o['alias']} → {o['resolved']} — {o['description']}"
                   + (f" ({', '.join(tags)})" if tags else ""))
    for a in d["omitted_above_ceiling"]:
        out.append(f"  {a} — above ceiling ({d['provenance']['ceiling']}); not offered")
    out.append("")
    out.append(f"resolved from pi --list-models, {d['provenance']['available']} option(s) available, ceiling "
               f"{d['provenance']['ceiling']}")
    return out


NO_MODEL_LINE = "no executor model is available on this machine — run pilead doctor"


def caller_sid(home):
    """The calling lead's id: $PI_LEAD_SID, else $PI_SESSION_ID, when that is a usable lead id and a lead
    record exists for it; else None."""
    sid = os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID")
    if sid and state.valid_lead_sid(sid) and state.lead_path(Path(home), sid).is_file():
        return sid
    return None

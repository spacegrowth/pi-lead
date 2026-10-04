"""A lead's posture, kept in its own record `leads/<sid>.json` (claude-relay's `lib/lead_guard.py`
`autonomous_state` / `set_autonomous`).

The autonomous posture: `autonomous` (a boolean) and `autonomous_source` (`"config"` when `pilead
lead-start` set it from config `autonomous_mode`, `"command"` when `pilead auto on|off` set it). It is on
only when the record holds the JSON value `true`; a missing field, any other value, or a record that is
not an object is off. It changes the lead's DEFAULT on routine in-plan steps, never the stop-list below,
and never the commit gate (`pilead verify <sid> --for-autocommit`).

The model-tier posture: `tier` (`auto`, `manual` or `lead`) and `tier_source` (`"config"` when `pilead
lead-start` set it, `"command"` when `pilead tier` did). It decides who chooses an executor's model when
`--model` is not given (`model_choice`); a value that is none of the three words is a posture that could
not be read, and is never taken for `auto`."""
import json
from pathlib import Path

from . import config, lineup, models, state
from .state import PileadError

# The five cases in which a lead stops and asks under either posture. Byte-identical to
# AUTONOMOUS_STOP_LIST in extensions/pi-lead.ts (tests/test_posture.py compares them).
STOP_LIST = ("a report with a risk flag, failing tests, or an UNVERIFIED claim that bears on correctness; core "
             "logic, ledgers, parity or golden tests, migrations, deploys; an action that cannot be undone or "
             "that leaves this machine (a push, a delete, a publish, a message to someone); work that is not "
             "in the approved plan; real ambiguity the packet and the plan do not settle")

TIERS = ("auto", "manual", "lead")


def autonomous_state(rec):
    """(on, source) for a lead record. `on` is True only for a dict whose `autonomous` is JSON `true`;
    `source` is "command" when `autonomous_source` is "command", else "config". Never raises."""
    if not isinstance(rec, dict):
        return False, "config"
    on = rec.get("autonomous") is True
    return on, ("command" if rec.get("autonomous_source") == "command" else "config")


def set_autonomous(home, sid, on):
    """Set lead `sid`'s posture to `on` (source "command"), every other field kept as it is; returns the
    (on, source) from before the write. A record that does not exist or cannot be read raises
    PileadError and nothing is written. An unusable `sid` is refused by state.lead_path first."""
    p = state.lead_path(home, sid)
    if not p.is_file():
        raise PileadError(f"{sid} is not a registered lead — run pilead lead-start first", 2)
    try:
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # noqa: BLE001 — any unreadable record: say which file, write nothing
        raise PileadError(f"lead record {p} cannot be read ({type(e).__name__})", 1)
    was = autonomous_state(rec)
    rec["autonomous"] = bool(on)
    rec["autonomous_source"] = "command"
    state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
    return was


def tier_state(rec):
    """(tier, source, readable) for a lead record. No `tier` field: ("auto", "config", True). One of TIERS:
    that word. Any other value (or a record that is not an object): ("auto", "config", False) — the posture
    could not be read, which the caller must not take for `auto`. `source` is "command" when `tier_source`
    is "command", else "config". Never raises."""
    if not isinstance(rec, dict):
        return "auto", "config", False
    source = "command" if rec.get("tier_source") == "command" else "config"
    if "tier" not in rec:
        return "auto", "config", True
    t = rec["tier"]
    if isinstance(t, str) and t in TIERS:
        return t, source, True
    return "auto", "config", False


def set_tier(home, sid, tier):
    """Set lead `sid`'s model-tier posture to `tier` (source "command"), every other field kept as it is;
    returns the (tier, source, readable) from before the write. A record that does not exist or cannot be
    read, or a `tier` that is not in TIERS, raises PileadError and nothing is written. An unusable `sid` is
    refused by state.lead_path first."""
    p = state.lead_path(home, sid)
    if tier not in TIERS:
        raise PileadError(f"tier {tier!r} is not one of {', '.join(TIERS)}", 2)
    if not p.is_file():
        raise PileadError(f"{sid} is not a registered lead — run pilead lead-start first", 2)
    try:
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # noqa: BLE001 — any unreadable record: say which file, write nothing
        raise PileadError(f"lead record {p} cannot be read ({type(e).__name__})", 1)
    was = tier_state(rec)
    rec["tier"] = tier
    rec["tier_source"] = "command"
    state.atomic_write(p, json.dumps(rec, indent=2) + "\n")
    return was


class Chosen(str):
    """A model `model_choice` picked under the `lead` posture: the alias or `provider/id` to use, carrying the
    lead's own `provider/id` as `.lead_model`."""
    lead_model = None


def _lead_record(home, sid):
    """The lead's record as a dict; None when there is no usable id or no record (an unowned session);
    False when the record exists and cannot be read."""
    if not (isinstance(sid, str) and state.valid_lead_sid(sid)):
        return None
    p = state.lead_path(home, sid)
    if not p.is_file():
        return None
    try:
        rec = json.loads(p.read_text())
        return rec if isinstance(rec, dict) else False
    except Exception:  # noqa: BLE001
        return False


def model_choice(home, lead_sid, packet_path, lead_applies=True):
    """What the owning lead's model-tier posture makes of a spawn, rotate or upgrade given NO `--model`:
    None (the verb's own rule applies), a model to use (a `Chosen`: a class word, or an unranked
    `provider/id`), or PileadError (exit code 2). Reads only; call it before anything is written.
    `lead_applies=False` (an upgrade, or a rotate that takes the paused-session fallback) makes the `lead`
    posture change nothing; `manual` and an unreadable posture refuse either way. A lead with no usable id
    or no record owns no posture: None."""
    home = Path(home)
    rec = _lead_record(home, lead_sid)
    if rec is None:
        return None
    tier, _source, readable = tier_state(rec)
    if not readable:
        raise PileadError(f"tier posture of lead {lead_sid} could not be read — run pilead tier auto, or pass "
                          "--model", 2)
    if tier == "auto":
        return None
    if tier == "manual":
        try:
            ask = lineup.ask_data(home, packet_path, None, config.executor_models(home)[2])
        except lineup.ModelListUnknown as e:
            raise PileadError(e.why, 2)
        if ask is None:
            raise PileadError(lineup.NO_MODEL_LINE, 2)
        raise PileadError(f"tier is manual — no model chosen. Run: pilead tier ask --session {lead_sid} "
                          f"--packet {packet_path}, then pass the answer as --model.", 2)
    if not lead_applies:
        return None
    model = lineup.lead_model(home, lead_sid)
    if model is None:
        raise PileadError(f"tier is lead — but the model of lead {lead_sid} could not be read (its session log "
                          "holds no answer yet). Pass --model.", 2)
    chosen = Chosen(models.tier(model) or model)
    chosen.lead_model = model
    return chosen

"""<home>/config.json — pi-lead's settings (claude-relay's `LEAD_DEFAULTS` + `load_config`).

`load(home)` is DEFAULTS merged with the recognised keys of the file. Unknown keys are ignored; a key
whose JSON type differs from its default's (a string where a number belongs, a number where a
boolean belongs, …) is ignored for that key, and so is a number outside its key's RANGES/CLI_RANGES; a missing,
unreadable or corrupt file means pure defaults. It never throws. extensions/pi-lead.ts `loadConfig`
applies the same rules to the keys it reads.

Precedence where a value is read, highest first: a CLI flag · an environment variable that exists
today · config.json · the default. The pi extension reads `edit_line_threshold`,
`block_on_new_file`, `grace_seconds` and `poll_interval` from the same file at session start;
`pilead check` and `pilead send` read `context_warn_tokens`, `context_nudge_tokens` and
`handoff_nudge_mb`; `pilead list` / `pilead check` / the board (lib/pilead/status.py) read
`stall_threshold_seconds` and `usage_limit_pattern`; `pilead list` reads `lead_nudge_tokens`;
`pilead spawn` reads `executor_default_model`, `executor_fallback_model`, `executor_model_ceiling` and
`executor_default_effort` (README "Model and effort"); the auto-close sweep (lib/pilead/autoclose.py:
`pilead auto-close`, `list`, `check`) reads `auto_close` and `auto_close_idle_minutes`; `pilead board` and
the verbs that keep a live board fresh (lib/pilead/review.py) read `board_live` and `board_refresh_seconds`; the lead's
bash write gate (lib/pilead/bashgate.py, `pilead gate`) reads `bash_write_gate` and `bash_gate_logging`,
with `edit_line_threshold`, `block_on_new_file` and `grace_seconds`; `pilead lead-start` reads
`autonomous_mode` (the posture every run resets the lead to, lib/pilead/posture.py); `pilead verify
--for-autocommit` reads `signoff_paths` (paths that add to condition 4's built-in sign-off markers); and every
verb that picks a terminal backend (lib/pilead/launch.py, `pilead doctor`) reads `terminal_app`:
"iterm" | "tmux" | "auto" ($TMUX, then $TERM_PROGRAM; iTerm default). $PILEAD_TERMINAL / $RELAY_TERMINAL
beat it; any other value reads as "auto".

Relay keys deliberately not carried over: `executor_skip_permissions` (pi has no permission
prompts), `executor_default_context` (pi's model list carries the window), `cache_ttl_minutes`
(Claude Code's prompt cache), `transport_v2` (a relay prototype), `poll_seconds` (the lifetime of
relay's Stop-hook watcher; the pi extension polls in-process)."""
import json
import math
import re
from pathlib import Path

from . import lint

# The built-in `usage_limit_pattern` (searched case-insensitively in the errorMessage of an assistant
# message whose stopReason is "error"; lib/pilead/status.py). Where each wording comes from:
# - "out of extra usage": Anthropic's error text when an account runs out of usage ("You're out of extra
#   usage …"), one confirmed instance observed by the lead (packet p16b).
# - "monthly usage limit reached", "insufficient_quota", "quota exceeded": pi's own
#   NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN (the errors pi does not retry because a provider limit
#   was hit), in @earendil-works/pi-coding-agent 0.87.1 dist/bundle/chunks/chunk-OJP47DM6.js (pi-ai).
USAGE_LIMIT_PATTERN = r"out of extra usage|monthly usage limit reached|insufficient_quota|quota exceeded"

DEFAULTS = {
    # ── wired: read by existing code ──────────────────────────────────────────
    "edit_line_threshold": 40,   # extension: a registered lead's edit/write changing MORE lines than this is gated
    "block_on_new_file": True,   # extension: a lead write creating a new file is gated at any size
    "grace_seconds": 120,        # extension: `/pilead:route retain` opens the gate for this long
    "poll_interval": 3,          # extension: fallback inbox poll, seconds (0 disables it)
    "executor_layout": "tab",    # spawn: "tab" | "pane" when neither --tab nor --pane is given ("pane" = iTerm and tmux)
    "terminal_app": "auto",      # backend: "iterm" | "tmux" | "auto" ($TMUX, then $TERM_PROGRAM; iTerm default)
    # ── defined: read by later packets, nothing reads them yet ────────────────
    "auto_wake": True,
    "surface_commits": False,
    "notify_on_wake": True,
    "notify_via": "auto",
    "tab_colors": True,
    "tidy_tabs": True,
    "handoff_nudge": True,
    # ── wired: read by `pilead check` / `pilead send` (lib/pilead/usage.py) ──────
    "handoff_nudge_mb": 5,           # a session whose live context is unknown is heavy at this log size, MB
    "context_warn_tokens": 120000,   # live context at or above this is "approaching heavy"
    "context_nudge_tokens": 150000,  # live context at or above this is "heavy": `send` refuses without an override
    # ── wired: read by `pilead list` / `pilead check` / the board (lib/pilead/status.py) ──
    "lead_nudge_tokens": 300000,       # list: a lead whose live context is at or above this is heavy
    "usage_limit_pattern": USAGE_LIMIT_PATTERN,  # a matching error pauses a session; null = this default
    "stall_threshold_seconds": 2700,   # busy this long with a log this old → stalled
    # ── wired: read by `pilead spawn` (README "Model and effort") ─────────────
    "executor_default_model": "sonnet",   # a tier alias or provider/id, when no --model is given
    "executor_default_effort": "high",    # a pi thinking level (lint.EFFORT_LEVELS); anything else → "high"
    "executor_model_ceiling": "opus",     # a tier word; a model above it needs --model-override "<reason>"
    "executor_fallback_model": None,      # an alias or provider/id used when the chosen alias matches no model
    # ── defined: read by later packets, nothing reads them yet ────────────────
    "ctx_warn_wake": True,
    "signoff_paths": [],             # wired: `pilead verify --for-autocommit` adds these to condition 4's built-in markers
    "executor_escalation": True,
    "autonomous_mode": False,        # wired: `pilead lead-start` resets the lead's posture to it (lib/pilead/posture.py)
    "auto_close": True,              # wired: `pilead auto-close`, list and check park finished executors
    "auto_close_idle_minutes": 60,   # wired: reported this long (and seen) → parked; 0 = the idle rule is off
    "board_live": False,             # wired: `pilead board` renders a live page; verbs keep it fresh (review.refresh_live_board)
    "board_refresh_seconds": 10,     # wired: a live page reloads this often, seconds (whole, ≥ 1)
    # ── wired: read by the lead's bash write gate (lib/pilead/bashgate.py, `pilead gate`) ──
    "bash_write_gate": "log",    # "deny" | "log" | "off"; any other value reads as "log"
    "bash_gate_logging": True,   # the verb log: log a command an implementation rule names
}

# Numeric keys that code reads, with the values they may take; anything else is ignored for that key
# (the default applies), exactly like a wrong-typed value. Two dicts, one rule (`_accepts`):
# RANGES = keys the extension reads too, kept identical to CONFIG_RANGES in extensions/pi-lead.ts;
# CLI_RANGES = keys only the Python CLI reads (the extension never sees them).
RANGES = {
    "edit_line_threshold": lambda v: float(v).is_integer() and v >= 1,  # a whole number of lines, ≥ 1
    "grace_seconds": lambda v: v >= 1,                                  # ≥ 1 s
    "poll_interval": lambda v: v == 0 or v >= 1,                        # 0 = no poll, else ≥ 1 s
}
CLI_RANGES = {
    "context_warn_tokens": lambda v: float(v).is_integer() and v >= 1,   # a whole number of tokens, ≥ 1
    "context_nudge_tokens": lambda v: float(v).is_integer() and v >= 1,  # a whole number of tokens, ≥ 1
    "handoff_nudge_mb": lambda v: v > 0,                                 # MB, > 0
    "stall_threshold_seconds": lambda v: v >= 60,                        # seconds, ≥ 60
    "lead_nudge_tokens": lambda v: float(v).is_integer() and v >= 1,     # a whole number of tokens, ≥ 1
    "auto_close_idle_minutes": lambda v: v >= 0,                         # minutes, ≥ 0 (0 = idle rule off)
    "board_refresh_seconds": lambda v: float(v).is_integer() and v >= 1,  # a whole number of seconds, ≥ 1
}
# Keys that also take JSON null, meaning "the default" where the value is read (the only one:
# `usage_limit_pattern`, whose default is a string). A string for it must compile as a regex.
NULLABLE = {"usage_limit_pattern"}
# String keys that take only these values (letter case ignored); any other string is ignored for that
# key, exactly like a wrong-typed value, so the default applies.
CHOICES = {"terminal_app": ("auto", "iterm", "tmux")}

LAYOUTS = ("tab", "pane")


def path(home):
    return Path(home) / "config.json"


def _json_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):  # before int: bool is an int subclass in Python, not in JSON
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _accepts(key, default, value):
    """Same JSON type as the default (a null default — an optional name/pattern — also takes a
    string; a NULLABLE key also takes null; a `usage_limit_pattern` string must compile), a finite
    number where a number belongs, and inside RANGES[key] / CLI_RANGES[key] when the key has one.
    A value that cannot be evaluated (an integer too large for a float makes math.isfinite raise
    OverflowError) is rejected for this key only — the extension's loader reads it as Infinity and
    ignores it the same way. Never raises."""
    try:
        want = _json_type(default)
        got = _json_type(value)
        if key in NULLABLE and got == "null":
            return True
        if key == "usage_limit_pattern" and got == "string":
            re.compile(value)  # an invalid pattern raises → rejected → the default applies
        if not (got == want or (want == "null" and got == "string")):
            return False
        if got == "number" and not math.isfinite(value):  # Python's json accepts NaN/Infinity; JSON does not
            return False
        if key in CHOICES and value.lower() not in CHOICES[key]:
            return False
        rng = RANGES.get(key) or CLI_RANGES.get(key)
        return rng is None or bool(rng(value))
    except Exception:
        return False


def _parse_int(text):
    """json's parse_int: an integer too long for int() (Python 3.11+'s digit limit) becomes ±inf, so
    it costs its own key, as in the extension, instead of failing the whole file."""
    try:
        return int(text)
    except ValueError:
        return float("-inf") if text.startswith("-") else float("inf")


def load(home):
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    try:
        user = json.loads(path(home).read_text(), parse_int=_parse_int)
        if isinstance(user, dict):
            for k, default in DEFAULTS.items():
                if k in user and _accepts(k, default, user[k]):
                    cfg[k] = user[k]
    except Exception:
        pass
    return cfg


def executor_layout(home, flag=None):
    """Flag (`--tab`/`--pane`) over config.json over the default. A config value that is neither
    "tab" nor "pane" falls back to the default."""
    if flag:
        return flag
    v = load(home)["executor_layout"]
    return v if v in LAYOUTS else DEFAULTS["executor_layout"]


def terminal_app(home):
    """config.json's `terminal_app`, lower-cased: "iterm" | "tmux" | "auto" (anything else reads as "auto").
    Passed to the vendored backend.select(configured=…)."""
    v = str(load(home).get("terminal_app") or "").lower()
    return v if v in CHOICES["terminal_app"] else "auto"


def thresholds(home):
    """(context_warn_tokens, context_nudge_tokens, handoff_nudge_mb) as config.json sets them."""
    cfg = load(home)
    return cfg["context_warn_tokens"], cfg["context_nudge_tokens"], cfg["handoff_nudge_mb"]


def executor_models(home):
    """(executor_default_model, executor_fallback_model, executor_model_ceiling) as config.json sets them.
    The fallback is None when unset; a ceiling that is not a tier word is returned as it is (spawn then
    treats every `anthropic` model as above it)."""
    cfg = load(home)
    return cfg["executor_default_model"], cfg["executor_fallback_model"], cfg["executor_model_ceiling"]


def executor_default_effort(home):
    """config.json's `executor_default_effort` when it is one of pi's thinking levels (lint.EFFORT_LEVELS,
    letter case and surrounding spaces ignored), else the default."""
    return lint.effort_level(load(home)["executor_default_effort"]) or DEFAULTS["executor_default_effort"]

"""
Backend selector: which terminal pi-lead drives — iTerm2 (iterm.py), Terminal.app (terminal_app.py)
or tmux (tmux_backend.py). All expose the same operations; iTerm addresses tabs by session id (title
as a legacy fallback), Terminal by captured window id, tmux by pane id (see each module's docstring).

Adapted from claude-relay d1cc0a7 `scripts/backend.py`: `select` and `live_handle_from_env` take
their inputs explicitly (pi-lead has no `lead_guard`; tests pass dicts), the `PILEAD_` env names are
read before relay's, `for_handle` is pi-lead's, and relay's `tab_id_from_env` is not ported (pi-lead
never keyed anything off $TERM_SESSION_ID).

Selection order:
1. $PILEAD_TERMINAL, else $RELAY_TERMINAL ("iterm" | "terminal" | "tmux") — explicit
   per-invocation override, also what tests use to pin behavior regardless of where the suite runs.
2. `terminal_app` in pi-lead's config, passed in as `configured` ("iterm" | "tmux"; "auto" falls
   through).
3. Inside tmux ($TMUX set, or $TERM_PROGRAM == "tmux") → tmux. Checked BEFORE $TERM_PROGRAM's
   Terminal.app test: a tmux pane on a Mac inherits the outer terminal's identity variables, and
   the pane — not the outer tab — is what pi-lead can address. This is the Linux/SSH backend.
4. $TERM_PROGRAM auto-detect: "Apple_Terminal" → Terminal.app; anything else → iTerm (the default,
   and the richer backend: tab colors, title-based focus for leads).

Sessions record their backend at spawn ("backend" in meta.json); by_name() resolves it so a session
spawned under one terminal keeps being addressed there even if pi-lead is later invoked from the
other. A stored HANDLE names its own backend too (`for_handle`): a handle is always addressed through
the backend that minted it, whatever this invocation selected.

This module is also the ONE place that knows what a LIVE handle looks like — the current process's
own tab/pane id, read from the environment (`live_handle_from_env`, `is_live_handle`). Every site in
pi-lead that used to read $ITERM_SESSION_ID directly goes through these.
"""
import os
import re

import iterm
import terminal_app
import tmux_backend

_BY_NAME = {"iterm": iterm, "terminal": terminal_app, "tmux": tmux_backend}

# A real iTerm session handle ("w#t#p#:UUID") — the shape trusted for a live $ITERM_SESSION_ID.
LIVE_ITERM_SESSION_RE = re.compile(r"^w\d+t\d+p\d+:[0-9A-Fa-f-]{36}$")
# A tmux pane handle as pi-lead stores it ("tmux:%N").
LIVE_TMUX_HANDLE_RE = re.compile(r"^tmux:%\d+$")
# pi-lead: the variables that carry "the tab I am in" — a child process that must never claim its parent's
# tab or pane is started without them (relay 0.5.6 `_headless_env` drops the same).
LIVE_HANDLE_VARS = ("ITERM_SESSION_ID", "TMUX_PANE")
# pi-lead: anything SHAPED like an iTerm handle ("w#t#p#:<id>", any non-blank id). `for_handle` and
# `live_handle_from_env` use this looser shape so every handle pi-lead stored before b12 (any
# `$ITERM_SESSION_ID` lead-start saw) keeps being addressed through iTerm; `is_live_handle` keeps
# relay's strict UUID shape above.
ITERM_HANDLE_RE = re.compile(r"^w\d+t\d+p\d+:\S+$")


def by_name(name):
    """The backend module registered under `name`, or None (caller falls back to the selected
    default — covers pre-backend session records)."""
    return _BY_NAME.get(str(name or "").lower())


def _in_tmux(env):
    return bool(env.get("TMUX")) or env.get("TERM_PROGRAM") == "tmux"


def select(env=None, configured=None):
    """The backend module for this invocation (see the module docstring's selection order). `env`
    defaults to os.environ; `configured` is the config's `terminal_app` value (None/"auto" → detect)."""
    env = os.environ if env is None else env
    for var in ("PILEAD_TERMINAL", "RELAY_TERMINAL"):
        name = str(env.get(var) or "").lower()
        if name in _BY_NAME:
            return _BY_NAME[name]
    name = str(configured or "").lower()
    if name in _BY_NAME:
        return _BY_NAME[name]
    if _in_tmux(env):
        return tmux_backend
    if env.get("TERM_PROGRAM") == "Apple_Terminal":
        return terminal_app
    return iterm


def for_handle(handle):
    """The backend a stored handle belongs to: "tmux:%N" → tmux_backend, an iTerm "w#t#p#:UUID" →
    iterm, anything else (None, "", a Terminal "twid:N", junk) → None."""
    h = str(handle or "")
    if LIVE_TMUX_HANDLE_RE.match(h):
        return tmux_backend
    if ITERM_HANDLE_RE.match(h):
        return iterm
    return None


def is_live_handle(value):
    """True for a value shaped like a real live handle of either addressable kind: an iTerm session
    id ("w#t#p#:UUID") or a tmux pane handle ("tmux:%N")."""
    v = str(value or "")
    return bool(LIVE_ITERM_SESSION_RE.match(v) or LIVE_TMUX_HANDLE_RE.match(v))


def live_handle_from_env(bk=None, env=None):
    """The CURRENT process's own tab/pane handle for backend `bk` (default: `select(env)`), or None —
    only a value shaped like that backend's handle.
      - tmux:  $TMUX_PANE as "tmux:%N";
      - iTerm: $ITERM_SESSION_ID, only when shaped like an iTerm handle ("w#t#p#:<id>"; a bare UUID
        is not one);
      - anything else (Terminal.app): None — it has no live handle in the environment."""
    env = os.environ if env is None else env
    name = getattr(bk if bk is not None else select(env), "NAME", None)
    if name == "tmux":
        pane = str(env.get("TMUX_PANE") or "")
        return tmux_backend.handle_for(pane) if re.match(r"^%\d+$", pane) else None
    if name == "iterm":
        v = env.get("ITERM_SESSION_ID")
        return v if v and ITERM_HANDLE_RE.match(v) else None
    return None

"""An honest status for an executor session (claude-relay's `_check_one`, rebuilt on pi-lead's state).

`refresh(home, meta)` returns the session's status now and stores it when it changed. Rules, in order:

  stored `closed` / `dead`                         → unchanged (only `pilead send`'s relaunch leaves them)
  the current packet's report file exists          → `reported`
  no pid file, or it cannot be read                → unchanged (liveness unknown: nothing is claimed)
  process alive, stored busy/idle/stalled/paused, the last assistant message on the log's active
      branch is a usage-limit error               → `paused` (`pause_text` in meta.json)
  process alive, stored busy/stalled/paused        → `stalled` when busy longer than
      `stall_threshold_seconds` (from packet-NNNN.md's mtime) AND the session log was last written
      longer ago than that (log not found: the first condition alone); else `busy`
  process alive, stored idle                       → `idle`
  process gone, the tab still exists               → `stalled`
  process gone, the tab is gone or the terminal cannot be asked → `dead`

A change is stored (state.write_status) and logged once (`stalled` / `paused` / `dead` ledger events,
{session_id, packet, reason}); no change writes nothing. `refresh` never raises.

"Process alive" = the pid in sessions/<sid>/pid exists AND did not start after the pid file was last
written (a later process is another one given the same number). Its start is `now - elapsed` from
`ps -o etime=` — epoch arithmetic, no time zone or daylight-saving text involved. `pilead send`'s
relaunch removes the pid file before it opens the new tab, so until the new pi writes its own pid the
status is unchanged rather than read from the previous run's process. The tab is asked through the
vendored terminal backend (launch._backend_for) — `ps` and `osascript`, never the iterm2 Python package."""
import datetime
import os
import re
import subprocess
import time
from pathlib import Path

from . import config, ledger, state, usage

TERMINAL = ("closed", "dead")
ALIVE_KEEPS = ("busy", "idle", "stalled", "paused")
BUSY_LIKE = ("busy", "stalled", "paused")
PAUSE_TEXT_LEN = 120
# `etime` has one-second resolution: a start computed up to this many seconds after the pid file's
# modification time is not "after" it.
START_SLACK_SECONDS = 2


# ── the process ───────────────────────────────────────────────────────────────────────────────
_ETIME = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def parse_etime(text):
    """`ps -o etime` (`[[dd-]hh:]mm:ss`) → seconds, or None when it is not that shape."""
    m = _ETIME.match(" ".join(str(text).split()))
    if not m:
        return None
    d, h, mi, se = (int(g) if g else 0 for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def _elapsed(pid):
    """The process' age in whole seconds from `ps -o etime=`, or None (ps missing, failing, timing out
    or printing something else)."""
    try:
        r = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True,
                           timeout=5, env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        if r.returncode != 0:
            return None
        return parse_etime(r.stdout)
    except Exception:
        return None


def pid_alive(pidfile):
    """True / False from a readable pid file, None when there is none or it cannot be read.
    False: no such process, or one that started more than START_SLACK_SECONDS after the file was last
    written (pid reuse). When the age cannot be read, the process' existence decides (unknown is
    alive, never gone)."""
    try:
        pidfile = Path(pidfile)
        pid = int(pidfile.read_text().strip())
        written = pidfile.stat().st_mtime
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else
    except OSError:
        return False
    elapsed = _elapsed(pid)
    if elapsed is not None and time.time() - elapsed > written + START_SLACK_SECONDS:
        return False
    return True


# ── the tab ───────────────────────────────────────────────────────────────────────────────────
def executor_tab_exists(meta, sdir):
    """The executor's tab, asked through its backend (handle from sessions/<sid>/iterm-id, else the
    tab recorded at launch; the label as the backend's title fallback). False when the terminal
    cannot be asked (not running, osascript missing / failing / timing out) — never raises."""
    try:
        from .launch import _backend_for
        be = _backend_for(meta)
        running = getattr(be, "running", None)
        if callable(running) and not running():
            return False  # never `tell` an app that is not running (that would launch it)
        handle = ""
        try:
            handle = (Path(sdir) / "iterm-id").read_text().strip()
        except OSError:
            pass
        pid = None
        try:
            pid = int((Path(sdir) / "pid").read_text().strip())
        except (OSError, ValueError):
            pass
        return bool(be.is_alive(meta.get("label") or "", handle or meta.get("tab") or None, pid))
    except Exception:
        return False


def lead_tab_exists(rec):
    """True only when the lead's recorded `iterm_handle` resolves through the backend that handle names
    (vendor/relay/backend.py `for_handle`). An iTerm handle: it resolves to an iTerm session. A tmux
    handle (`tmux:%N`): a tmux server answers, the pane exists AND a `pi` runs on the pane's tty — a pane
    outlives its program (the shell stays), so the pane alone is not a live lead (claude-relay 0.5.6
    `_lead_alive`). A lead with no handle, or a handle of no known shape, has no tab and the terminal is not
    asked; never by title. False also when the terminal cannot be asked ("unknown" reads as not-live: LIVE
    is then unreachable or ghost). Never raises."""
    try:
        handle = (rec or {}).get("iterm_handle")
        if not isinstance(handle, str) or not handle.strip():
            return False
        handle = handle.strip()
        from .launch import backend
        be = backend.for_handle(handle)
        if be is None:
            return False
        if be.NAME == "tmux":
            if not be.running() or be.exists_by_id(handle) is not True:
                return False
            return bool(be.pids_on_tty(be.tty_by_id(handle), "pi"))
        if not be.running():
            return False
        return be.exists_by_id(handle) is True
    except Exception:
        return False


# ── the session log ───────────────────────────────────────────────────────────────────────────
def usage_limit_regex(home):
    """config.json's `usage_limit_pattern`, compiled case-insensitive; the built-in pattern
    (config.USAGE_LIMIT_PATTERN) when it is null — or anything else that is not a valid regex."""
    try:
        v = config.load(home).get("usage_limit_pattern")
        if isinstance(v, str):
            return re.compile(v, re.I)
    except Exception:
        pass
    return re.compile(config.USAGE_LIMIT_PATTERN, re.I)


def pause_text(home, reading):
    """The first PAUSE_TEXT_LEN characters of the errorMessage of the last assistant message on the
    log's active branch (usage.read's `last_stop`) when it is a usage-limit error (stopReason "error",
    errorMessage matching usage_limit_pattern), else None. A `last_stop` from a usage cache carries a
    boolean `limit` (usage._cached: whether the whole message matched the pattern it was read with), and
    then that decides: paused exactly when stopReason is "error" and `limit` is true."""
    m = (reading or {}).get("last_stop")
    if not isinstance(m, dict) or m.get("stopReason") != "error":
        return None
    msg = m.get("errorMessage")
    if isinstance(m.get("limit"), bool):
        return msg[:PAUSE_TEXT_LEN] if m["limit"] and isinstance(msg, str) else None
    if not isinstance(msg, str) or not usage_limit_regex(home).search(msg):
        return None
    return msg[:PAUSE_TEXT_LEN]


def _age(path):
    try:
        return max(0.0, time.time() - Path(path).stat().st_mtime)
    except (OSError, TypeError):
        return None


# ── refresh ───────────────────────────────────────────────────────────────────────────────────
def _decide(home, meta, stored, reading=None):
    """(new status, ledger reason or None, pause text or None). `reading` is the session's
    (usage, mb) when the caller already has it; else it is read here, only when needed, and not cached."""
    sid = meta["sid"]
    sdir = state.session_dir(Path(home), sid)
    n = int(meta.get("packets") or 1)
    if state.report_path(Path(home), sid, n).is_file():
        return "reported", None, None
    alive = pid_alive(sdir / "pid")
    if alive is None:
        return stored, None, None
    if alive:
        if stored not in ALIVE_KEEPS:
            return stored, None, None
        wt = meta.get("worktree") or None
        u, mb = reading if reading is not None else usage.session_reading(home, sid, wt, write_cache=False)
        log = Path(u["path"]) if u and u.get("path") else (usage.locate(sid, wt) if mb is not None else None)
        pt = pause_text(home, u)
        if pt:
            return "paused", pt, pt
        if stored == "idle":
            return "idle", None, None
        threshold = config.load(home)["stall_threshold_seconds"]
        busy_for = _age(state.packet_path(Path(home), sid, n))
        log_age = _age(log) if log is not None else None
        if busy_for is not None and busy_for > threshold and (log_age is None or log_age > threshold):
            why = f"busy {int(busy_for // 60)}min, no report"
            why += f"; session log last written {int(log_age // 60)}min ago" if log_age is not None \
                else "; session log not found"
            return "stalled", why, None
        return "busy", None, None
    if executor_tab_exists(meta, sdir):
        return "stalled", "process gone, no report; tab still open", None
    return "dead", "process gone and tab gone (or the terminal could not be asked)", None


def refresh(home, meta, records=None, reading=None, store=True):
    """The session's status now (see the module docstring); stored and logged only when it changed.
    `records`, when a list (the caller's already-read ledger), gets the logged event appended so a
    later reader in the same command sees it. `reading`, the (usage, mb) pair usage.session_reading
    returned, spares a second parse of the session log when the caller has already read it.
    `store=False` returns the status that would be stored and writes nothing: no `status` file, no
    meta.json, no ledger event, nothing appended to `records`, `meta` unchanged. Never raises: on any
    failure the stored status is returned unchanged."""
    home = Path(home)
    try:
        sid = meta["sid"]
        stored = state.read_status(home, sid, meta)
    except Exception:
        return (meta or {}).get("status", "unknown") if isinstance(meta, dict) else "unknown"
    if stored in TERMINAL:
        return stored
    try:
        new, reason, pt = _decide(home, meta, stored, reading)
        if new == stored or not store:
            return new
        state.write_status(home, sid, new)
        try:
            m = state.load_meta(home, sid)
            if pt:
                m["pause_text"] = pt
            else:
                m.pop("pause_text", None)
            state.save_meta(home, m)
            meta.update({"status": new})
            if pt:
                meta["pause_text"] = pt
            else:
                meta.pop("pause_text", None)
        except Exception:
            pass
        if new in ("stalled", "paused", "dead"):
            n = int(meta.get("packets") or 1)
            ledger.append(home, new, session_id=sid, packet=n, reason=reason)
            if isinstance(records, list):
                records.append({"ts": state.now_iso(), "event": new, "session_id": sid, "packet": n,
                                "reason": reason})
        return new
    except Exception:
        return stored


def display(meta, status):
    """The status word to SHOW: `superseded` when `status` is `closed` and `meta` has a non-empty
    `superseded_by` (`pilead close --supersede`); otherwise `status` unchanged. Pure: the stored status
    stays `closed`, and every rule that treats `closed` as terminal is unchanged."""
    try:
        by = meta.get("superseded_by") if isinstance(meta, dict) else None
        if status == "closed" and isinstance(by, str) and by.strip():
            return "superseded"
    except Exception:
        pass
    return status


def iso_utc(epoch):
    """`YYYY-MM-DDTHH:MM:SSZ` for an epoch, or None."""
    try:
        return datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None

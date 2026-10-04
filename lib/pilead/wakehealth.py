"""Is a lead's wake working? Read-only: the lead's health file `wake/<sid>/health.json` (written by the
pi-lead extension in the lead's own pi process: extensions/pi-lead.ts `writeHealth`), the lead's record (for
its inbox) and the names of the claim files beside the inbox. Nothing here writes, and nothing raises.

`state(home, sid)` gives one of five words, the first row that applies:

  unknown  the health file exists and cannot be read, is not a JSON object, or has no usable `beat`  (`?`)
  off      there is no health file, or its `ended` is set, or its `auto_wake` is false (detail
           `auto_wake is off`)                                                                    (`off`)
  stale    no process has its `pid`, or its beat is STALE_SECONDS old or more                    (`STALE`)
  stuck    a claim file beside the inbox is CLAIM_STALE_SECONDS old or more, or `last_error_at` is later
           than `last_delivered` (or `last_delivered` is null)                                   (`STUCK`)
  ok       none of the above                                                                     (`ok`)

A reading that could not be made is `unknown`, never `ok`."""
import calendar
import json
import os
import re
import time
from pathlib import Path

from . import state as _state  # `state` is this module's own reader below

# The extension's CLAIM_STALE_MS / 1000 and 3 × HEALTH_BEAT_MS / 1000 (tests/test_wakehealth.py compares).
CLAIM_STALE_SECONDS = 60
STALE_SECONDS = 90

CELL = {"unknown": "?", "off": "off", "stale": "STALE", "stuck": "STUCK", "ok": "ok"}

_CLAIM = re.compile(r"\.claim-(\d+)-(\d+)-(\d+)$")
_REPORTED = re.compile(r"^reported\s+\S+\s+.+$")


def wake_dir(home, sid):
    """wake/<sid>/ under home; the id goes through _state.lead_path first (PileadError for an unusable one)."""
    _state.lead_path(Path(home), sid)
    return Path(home) / "wake" / sid


def health_path(home, sid):
    return wake_dir(home, sid) / "health.json"


def _epoch(iso):
    """UTC ISO to the second (`2026-09-30T12:00:00Z`) → epoch seconds; None for anything else."""
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return None


def _age_text(secs):
    return _state.fmt_age(max(0, int(secs)))


def _pid_gone(pid):
    """True only when no process has `pid`; a pid that is not a whole number > 0 is gone too."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError, OverflowError):
        return False
    return False


def inbox_path(home, sid):
    """The lead's inbox: its record's `inbox` (relative to leads/, `~` expanded), else leads/<sid>.inbox.md;
    None when the record cannot be read."""
    try:
        rec = json.loads(_state.lead_path(Path(home), sid).read_text())
        if not isinstance(rec, dict):
            return None
    except Exception:  # noqa: BLE001 — an unreadable record names no inbox
        return None
    leads = Path(home) / "leads"
    inbox = rec.get("inbox")
    if isinstance(inbox, str) and inbox:
        return (leads / Path(inbox).expanduser()).resolve()
    return leads / f"{sid}.inbox.md"


def _claims(inbox):
    """[(path, ms)] of the claim files beside `inbox`; raises OSError when the directory cannot be listed."""
    out = []
    prefix = inbox.name + ".claim-"
    for e in os.scandir(inbox.parent):
        if not e.name.startswith(prefix):
            continue
        m = _CLAIM.search(e.name)
        if m and e.name == f"{prefix}{m.group(1)}-{m.group(2)}-{m.group(3)}":
            out.append((Path(e.path), int(m.group(2))))
    return out


def _reported_lines(path):
    return sum(1 for ln in Path(path).read_text(errors="replace").splitlines() if _REPORTED.match(ln.strip()))


def waiting(home, sid):
    """The `reported` lines in the lead's inbox plus those in the claim files beside it; None when the inbox
    cannot be read (a missing inbox holds nothing). Never raises."""
    try:
        inbox = inbox_path(home, sid)
        if inbox is None:
            return None
        n = 0
        try:
            n += _reported_lines(inbox)
        except FileNotFoundError:
            pass
        try:
            claims = _claims(inbox)
        except FileNotFoundError:
            claims = []
        for p, _ms in claims:
            n += _reported_lines(p)
        return n
    except Exception:  # noqa: BLE001 — an unknown count is None, never 0
        return None


def state(home, sid, now=None):
    """(word, detail) — see the module docstring. `detail` is short text for a footnote: the reason for
    `unknown`, the beat's age for `stale`, the oldest claim's age or the error for `stuck`, "" otherwise."""
    now = time.time() if now is None else now
    try:
        p = health_path(home, sid)
    except Exception:  # noqa: BLE001 — an unusable id has no health file to read
        return "unknown", "not a usable lead id"
    try:
        text = p.read_text()
    except FileNotFoundError:
        return "off", ""
    except Exception as e:  # noqa: BLE001
        return "unknown", f"cannot be read: {getattr(e, 'strerror', None) or e}"
    try:
        h = json.loads(text)
    except ValueError:
        return "unknown", "not JSON"
    if not isinstance(h, dict):
        return "unknown", "not a JSON object"
    beat = _epoch(h.get("beat"))
    if beat is None:
        return "unknown", "no usable beat"
    if h.get("ended") is not None:
        return "off", ""
    if h.get("auto_wake") is False:  # config.json `auto_wake: false`: the extension claims nothing, whatever else is true
        return "off", "auto_wake is off"
    if _pid_gone(h.get("pid")):
        return "stale", f"process {h.get('pid')} is gone, last beat {_age_text(now - beat)} ago"
    if now - beat >= STALE_SECONDS:
        return "stale", f"last beat {_age_text(now - beat)} ago"
    oldest = None
    try:
        inbox = inbox_path(home, sid)
        if inbox is not None:
            ages = [now - ms / 1000 for _p, ms in _claims(inbox)]
            oldest = max(ages) if ages else None
    except Exception:  # noqa: BLE001 — claims that cannot be listed say nothing either way
        oldest = None
    if oldest is not None and oldest >= CLAIM_STALE_SECONDS:
        return "stuck", f"oldest claim {_age_text(oldest)} old"
    err_at = _epoch(h.get("last_error_at"))
    delivered_at = _epoch(h.get("last_delivered"))
    if err_at is not None and (delivered_at is None or err_at > delivered_at):
        return "stuck", str(h.get("last_error") or "an error")[:200]
    return "ok", ""


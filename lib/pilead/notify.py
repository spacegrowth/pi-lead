"""Desktop banners, and "announced once". Claude-relay's `notify_banner` and `claim_notification`, with pi-lead's
state layout.

`banner(home, cfg, title, subtitle, message, lead_rec=None, lead_sid=None)` posts through two tiers and never
raises:

  1. By the lead's handle (claude-relay 0.5.6/0.5.7 `notify_banner`):
     - a tmux lead (`iterm_handle` is `tmux:%N`): a status-line message on the lead's own pane
       (`tmux_backend.notify`; tmux swallows OSC 777, so the tty is never written). Not posted → ledger
       `banner_fallback {reason: "write-failed"}`, then tier 2.
     - any other lead: iTerm's OSC 777 sequence written to the lead's tty device by path
       (`iterm.notify_via_tty`); a click on the banner focuses the lead's tab. The tty is the lead record's
       `tty` (recorded by `pilead lead-start`), else found through iTerm by the record's `iterm_handle`
       (`iterm.tty_by_id`, one AppleScript call).
  2. On Linux (`platform_cmds.is_linux()`): `notify-send <title> <body>` when it is on PATH, else silence —
     osascript is never tried there. Elsewhere `osascript -e 'display notification …'`: no click, and the only
     tier without iTerm.

`claim(home, owner, key)` is True the first time it is asked for an owner and key, and False every time after,
across processes: it creates the empty file `wake/<owner>/notified/<key>` with an exclusive create. A caller
asks it AFTER it has seen that the kill switch is off and the config allows the banner, so a banner that is
switched off never uses up its claim.

Kill switch: the environment variable `PILEAD_NO_NOTIFY` or `RELAY_NO_NOTIFY`, set and not empty. The test
suite sets `RELAY_NO_NOTIFY` for every test."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, ledger, state, usage

NO_LEAD = "_no-lead"  # the owner of a banner for a session that has no lead
BODY_MAX = 180        # characters of the message in the body of a banner
BRIEF_MAX = 200       # characters of a report's brief
OSASCRIPT_TIMEOUT = 5

_WORD = re.compile(r"[A-Za-z0-9-]+")


def kill_switch():
    """True when PILEAD_NO_NOTIFY or RELAY_NO_NOTIFY is set and not empty."""
    return bool(os.environ.get("PILEAD_NO_NOTIFY") or os.environ.get("RELAY_NO_NOTIFY"))


def iterm_module():
    """The vendored relay `iterm` module (imported when first needed, so this module never fails to load)."""
    vendor = str(state.ROOT / "vendor" / "relay")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import iterm  # noqa: WPS433
    return iterm


def tmux_module():
    """The vendored relay `tmux_backend` module (imported when first needed)."""
    iterm_module()  # puts vendor/relay on sys.path
    import tmux_backend  # noqa: WPS433
    return tmux_backend


def _is_linux():
    try:
        iterm_module()
        import platform_cmds  # noqa: WPS433
        return platform_cmds.is_linux()
    except Exception:  # noqa: BLE001
        return False


def _via(cfg):
    """`notify_via` as a tier choice: "osascript" (also for the legacy "terminal-notifier"), else "auto"."""
    v = (cfg or {}).get("notify_via") if isinstance(cfg, dict) else None
    return "osascript" if v in ("osascript", "terminal-notifier") else "auto"


def _quote(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def banner(home, cfg, title, subtitle, message, lead_rec=None, lead_sid=None):
    """Post one banner; returns the tier used (`"tmux"`, `"tty"`, `"notify-send"` or `"osascript"`), or None when
    nothing was posted (the kill switch, `osascript` could not run, or on Linux no `notify-send` posted). Never
    raises. Each time tier 1 was tried and did not post, one ledger event `banner_fallback {session_id, reason}`,
    `reason` one of `no-tty`, `tty-lookup-failed`, `write-failed`; none when tier 1 was not tried."""
    try:
        if kill_switch():
            return None
        body = f"{subtitle} — {(message or '')[:BODY_MAX]}"
        rec = lead_rec if isinstance(lead_rec, dict) else {}
        tty = rec.get("tty")
        handle = rec.get("iterm_handle")
        has_tty = isinstance(tty, str) and bool(tty)
        has_handle = isinstance(handle, str) and bool(handle)
        if has_handle and handle.startswith("tmux:") and _via(cfg) == "auto":
            try:
                posted = tmux_module().notify(handle, title, body) is True
            except Exception:  # noqa: BLE001
                posted = False
            if posted:
                return "tmux"
            ledger.append(home, "banner_fallback", session_id=lead_sid, reason="write-failed")
        elif _via(cfg) == "auto":
            reason = None
            resolved = None
            if has_tty:
                resolved = tty
            elif has_handle:
                try:
                    resolved = iterm_module().tty_by_id(handle)
                except Exception:  # noqa: BLE001
                    reason = "tty-lookup-failed"
                if reason is None and not resolved:
                    reason = "no-tty"
            else:
                reason = "no-tty"
            if resolved:
                try:
                    posted = iterm_module().notify_via_tty(resolved, title, body)
                except Exception:  # noqa: BLE001
                    posted = False
                if posted:
                    return "tty"
                reason = "write-failed"
            if has_tty or has_handle:  # tier 1 was expected to work; a record with neither never tried it
                ledger.append(home, "banner_fallback", session_id=lead_sid, reason=reason)
        if _is_linux():
            return _notify_send(title, body)
        script = f'display notification "{_quote(body)}" with title "{_quote(title)}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=OSASCRIPT_TIMEOUT)
        return "osascript"
    except Exception:  # noqa: BLE001 — a banner never breaks what asked for it
        return None


def _notify_send(title, body):
    """Tier 2 on Linux: `notify-send <title> <body>` when it is on PATH (5 s, output captured); "notify-send" when it
    exited 0, else None (silence). Never raises."""
    try:
        if not shutil.which("notify-send"):
            return None
        r = subprocess.run(["notify-send", title, body], capture_output=True, timeout=5)
        return "notify-send" if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def claim(home, owner, key):
    """True the first time this is called for `owner` and `key`, False every time after (across processes).
    `owner` is a lead id (`state.check_lead_sid`) or `_no-lead`; `key` is an executor's session id
    (`state.check_session_sid`), a `.` and a word (letters, digits and `-`). Any error gives False: when it
    cannot be recorded that something was announced, it is not announced."""
    try:
        if owner != NO_LEAD:
            state.check_lead_sid(owner)
        sid, dot, word = str(key).rpartition(".")
        if not dot or not _WORD.fullmatch(word):
            return False
        state.check_session_sid(sid)
        d = Path(home) / "wake" / owner / "notified"
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(d / f"{sid}.{word}", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        return True
    except Exception:  # noqa: BLE001 — FileExistsError is the "already announced" answer too
        return False


def brief(text):
    """The first line of a report that is not empty, leading `#` and spaces removed and runs of whitespace made
    one space, cut to BRIEF_MAX characters; "" for None or a report without such a line."""
    try:
        for line in (text or "").splitlines():
            if line.strip():
                return " ".join(line.lstrip("# ").split())[:BRIEF_MAX]
    except Exception:  # noqa: BLE001
        pass
    return ""


def report_brief(home, sid, n):
    """`brief` of report-NNNN.md; "" when it cannot be read."""
    try:
        return brief(state.report_path(Path(home), sid, n).read_text(errors="replace"))
    except Exception:  # noqa: BLE001
        return ""


def lead_record(home, lead_sid):
    """The lead's record as a dict, or None (no usable id, no record, not an object). Never raises."""
    try:
        rec = json.loads(state.lead_path(Path(home), lead_sid).read_text())
        return rec if isinstance(rec, dict) else None
    except Exception:  # noqa: BLE001
        return None


def project_title(rec, lead_sid):
    """`pi-lead · <project>` — the project of a lead's record, else its id."""
    proj = (rec or {}).get("project")
    return f"pi-lead · {proj if isinstance(proj, str) and proj else lead_sid}"


def announce(home, lead_sid, subtitle, message, claim_key=None, needs=("notify_on_wake",)):
    """The banner of a lead's own event (`pilead check`, `list`, `send`): nothing when the kill switch is on, the
    lead has no record, or a config key in `needs` is off; then, when `claim_key` is given, only if the claim is
    the first. Returns the tier or None. Never raises."""
    try:
        if kill_switch():
            return None
        rec = lead_record(home, lead_sid)
        if rec is None:
            return None
        cfg = config.load(home)
        if any(cfg.get(k) is not True for k in needs):
            return None
        if claim_key is not None and not claim(home, lead_sid, claim_key):
            return None
        return banner(home, cfg, project_title(rec, lead_sid), subtitle, message, lead_rec=rec, lead_sid=lead_sid)
    except Exception:  # noqa: BLE001
        return None


def ctx_warn(home, sid, meta, u, status=None):
    """`pilead check` / `pilead list` after their own output: an executor that is approaching heavy and has a
    lead with a record is announced once (`<sid>.ctx-warn`). A closed or dead session is not. Never raises."""
    try:
        if status in ("closed", "dead"):
            return None
        lead = (meta or {}).get("lead")
        if not state.valid_lead_sid(lead):
            return None
        cfg = config.load(home)
        warn, nudge = cfg["context_warn_tokens"], cfg["context_nudge_tokens"]
        if not usage.is_approaching(u, warn, nudge):
            return None
        message = (f"{usage.human_tokens(u['live'])} ctx, warn line {usage.human_tokens(warn)}, rotate line "
                   f"{usage.human_tokens(nudge)} — plan a rotate: pilead retire {sid}")
        return announce(home, lead, f"{sid} is approaching heavy", message, claim_key=f"{sid}.ctx-warn")
    except Exception:  # noqa: BLE001
        return None

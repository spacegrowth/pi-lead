"""On-disk state under $PI_LEAD_HOME (default ~/.pi-lead). Layout: see CONTRACT.md."""
import calendar
import json
import os
import re
import shlex
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root: lib/pilead/state.py -> ../..
EXTENSION = ROOT / "extensions" / "pi-lead.ts"
RULES = ROOT / "skills" / "executor-rules.md"


def version():
    """The `version` string of `package.json` in ROOT, read on every call; None when the file cannot be read
    or has no string `version`."""
    try:
        v = json.loads((ROOT / "package.json").read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001
        return None
    return v if isinstance(v, str) else None


class PileadError(Exception):
    """A user-facing failure: printed as one line, exit code `code`."""

    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def home_dir(override=None):
    """The state dir: `--home`, else $PI_LEAD_HOME, else ~/.pi-lead — always an ABSOLUTE path (`~` expanded, a relative
    one made absolute against the current directory), so the report path and `--home` in a packet footer name the
    same place from any directory. Symlinks are not resolved."""
    return Path(os.path.abspath(os.path.expanduser(str(override or os.environ.get("PI_LEAD_HOME") or Path.home() / ".pi-lead"))))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def age_seconds(iso):
    try:
        return max(0, int(time.time() - calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))
    except (TypeError, ValueError):
        return None


def fmt_age(secs):
    if secs is None:
        return "-"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def atomic_write(path, text):
    """tmp + rename, so a watcher never reads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def safe_topic(topic):
    t = re.sub(r"[^A-Za-z0-9._-]+", "-", topic).strip("-.")
    if not t:
        raise PileadError(f"topic {topic!r} has no usable characters (letters, digits, . _ -)")
    return t


# ── ids ──────────────────────────────────────────────────────────────────────
# A session id (an executor's; also pi's own --session-id) names sessions/<sid>/, so it has one safe
# form: pi's own `assertValidSessionId` pattern (every id pi makes itself passes), no `..`, at most
# SESSION_SID_MAX characters. A lead id names leads/<sid>.json (and usage/leads/), so it is a usable
# session id that also does not end in `.usage`. The same rules, to the character, are
# `validSessionSid` / `validLeadSid` in extensions/pi-lead.ts; tests/session-ids.json and
# tests/lead-ids.json are the tables both test suites read.
SESSION_SID_MAX = 128
LEAD_SID_MAX = 128
SPAWN_SID_MAX = 120  # what spawn makes: room left for a successor's `-r<N>`
_SID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SESSION_SID_RULE = ("letters, digits, '.', '_' and '-' only, first and last a letter or digit, no '..', "
                     f"at most {SESSION_SID_MAX} characters")
_LEAD_SID_RULE = ("letters, digits, '.', '_' and '-' only, first and last a letter or digit, no '..', "
                  f"not ending in '.usage', at most {LEAD_SID_MAX} characters")


def valid_session_sid(sid):
    """True when `sid` is a usable session id; False for anything else, whatever its type. Never raises."""
    try:
        return (isinstance(sid, str) and len(sid) <= SESSION_SID_MAX and _SID.fullmatch(sid) is not None
                and ".." not in sid)
    except Exception:  # noqa: BLE001 — an id that cannot be judged is not usable
        return False


def check_session_sid(sid):
    """`sid` when it is a usable session id, else PileadError (code 2) naming it and the rule."""
    if not valid_session_sid(sid):
        raise PileadError(f"session id {sid!r} is not usable: {_SESSION_SID_RULE}")
    return sid


# ── leads ────────────────────────────────────────────────────────────────────
def valid_lead_sid(sid):
    """True when `sid` is a usable lead id; False for anything else, whatever its type. Never raises."""
    try:
        return valid_session_sid(sid) and len(sid) <= LEAD_SID_MAX and not sid.endswith(".usage")
    except Exception:  # noqa: BLE001 — an id that cannot be judged is not usable
        return False


def check_lead_sid(sid):
    """`sid` when it is a usable lead id, else PileadError (code 2) naming it and the rule."""
    if not valid_lead_sid(sid):
        raise PileadError(f"lead id {sid!r} is not usable: {_LEAD_SID_RULE}")
    return sid


def lead_path(home, sid):
    return home / "leads" / f"{check_lead_sid(sid)}.json"


def load_lead(home, sid):
    p = lead_path(home, sid)
    if not p.is_file():
        raise PileadError(f"unknown lead {sid} (run `pilead lead-start {sid} --project <name>`)")
    return json.loads(p.read_text())


# ── executor sessions ────────────────────────────────────────────────────────
# A session's id is the NAME OF ITS DIRECTORY: the `sid` field inside meta.json never builds a path.
def session_dir(home, sid):
    return home / "sessions" / check_session_sid(sid)


def load_meta(home, sid):
    """The record in sessions/<sid>/meta.json, its `sid` set to the id it was asked for."""
    p = session_dir(home, sid) / "meta.json"
    if not p.is_file():
        raise PileadError(f"unknown session {sid}")
    meta = json.loads(p.read_text())
    if isinstance(meta, dict):
        meta["sid"] = sid
    return meta


def save_meta(home, meta):
    atomic_write(session_dir(home, meta["sid"]) / "meta.json", json.dumps(meta, indent=2) + "\n")


def read_status(home, sid, meta=None):
    """The `status` file is authoritative (the extension writes it); meta.json is the fallback."""
    p = session_dir(home, sid) / "status"
    if p.is_file():
        s = p.read_text().strip()
        if s:
            return s
    return (meta or {}).get("status", "unknown")


def write_status(home, sid, status):
    meta_p = session_dir(home, sid) / "meta.json"
    atomic_write(session_dir(home, sid) / "status", status + "\n")
    if meta_p.is_file():
        meta = json.loads(meta_p.read_text())
        meta["status"] = status
        meta["sid"] = sid  # the directory's name, whatever the field said
        save_meta(home, meta)


def report_path(home, sid, n):
    return session_dir(home, sid) / f"report-{n:04d}.md"


def packet_path(home, sid, n):
    return session_dir(home, sid) / f"packet-{n:04d}.md"


def latest_report(home, sid):
    reps = sorted(session_dir(home, sid).glob("report-*.md"))
    return reps[-1] if reps else None


def footer(home, sid, n):
    """The section spawn/send append to packet `n`: the report path, the one command to run after the
    report is written (`pilead diff`, a read that writes one page) and the exact closing line, whose
    address is the page that command writes."""
    report = report_path(home, sid, n)
    address = (session_dir(home, sid) / f"diff-{n:04d}.html").resolve().as_uri()
    return ("\n\n---\n"
            "(pi-lead — do not remove or reword this section)\n"
            "\n"
            "REPORT: write your full report (the outcome in one plain sentence first, then the TL;DR block) to\n"
            f"  {report}\n"
            "AFTER writing it, run:\n"
            f"  pilead diff {sid} --home {shlex.quote(str(home))}\n"
            "(it makes the review page of your staged changes; do not open it and do not attach it anywhere; "
            "run it once)\n"
            "Then end your final turn with EXACTLY this one line, nothing after it:\n"
            f"      ✅ [pi-lead] — staged + report written — diff: {address} — idle, awaiting the lead's review.\n")


def write_packet(home, sid, n, src_text):
    """packet-NNNN.md = the lead's text + the CONTRACT footer naming report-NNNN.md."""
    p = packet_path(home, sid, n)
    atomic_write(p, src_text.rstrip("\n") + footer(home, sid, n))
    return p

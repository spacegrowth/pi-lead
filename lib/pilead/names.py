"""Names: what a human may type where a pilead verb wants an id (claude-relay 0.5.4's `resolve_sid`).

`resolve(home, token)` turns a token into an id, or gives the token back unchanged, or refuses with the
candidates message when the token names more than one session. First match wins:

  a) an executor's id: a usable session id whose sessions/<token>/meta.json is a file;
  b) a lead's id: a usable lead id whose record leads/<token>.json is a file;
  c) the project of exactly ONE lead record, compared as typed (same case, nothing trimmed);
     more than one: refused (c2);
  d) a prefix of 6 characters or more of exactly ONE known executor or lead id; more than one: refused (d2);
  e) anything else: the token, unchanged, so the verb says what it says today for a name it does not know.

It only reads, and only under home. Rows a and b are the only places the token becomes part of a path, and
only after `state.valid_session_sid` / `state.valid_lead_sid` accepted it; rows c and d compare texts. A
name that is not a usable id is never returned and never a candidate. A read that fails is skipped; a
resolver fault is never the reason an exact id stops working. The refusal of c2 and d2 is the only thing
`resolve` raises."""
import calendar
import json
import os
import time
from pathlib import Path

from . import state
from .state import PileadError

PREFIX_MIN = 6


def _is_file(path):
    try:
        return Path(path).is_file()
    except Exception:  # noqa: BLE001 — a path that cannot be asked about is not there
        return False


def _lead_ids(home):
    """The usable ids of the lead records under leads/ (a name `usage.is_lead_record` accepts)."""
    try:
        names = os.listdir(Path(home) / "leads")
    except Exception:  # noqa: BLE001 — missing or unreadable: no leads to go on
        return set()
    out = set()
    for name in names:
        if not name.endswith(".json") or name.endswith(".usage.json"):
            continue
        sid = name[:-len(".json")]
        if state.valid_lead_sid(sid):
            out.add(sid)
    return out


def _executor_ids(home):
    """The usable ids of the directories under sessions/ that hold a meta.json."""
    try:
        names = os.listdir(Path(home) / "sessions")
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for sid in names:
        if state.valid_session_sid(sid) and _is_file(Path(home) / "sessions" / sid / "meta.json"):
            out.add(sid)
    return out


def known_ids(home):
    """(known executor ids, known lead ids): two sets of usable ids; a read that fails gives fewer."""
    return _executor_ids(home), _lead_ids(home)


def _read_obj(path):
    """The JSON object in `path`, or None (missing, unreadable, not JSON, not an object)."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


def _lead_record(home, sid):
    if not state.valid_lead_sid(sid):
        return None
    try:
        return _read_obj(state.lead_path(home, sid))
    except Exception:  # noqa: BLE001
        return None


def _meta(home, sid):
    if not state.valid_session_sid(sid):
        return None
    try:
        return _read_obj(state.session_dir(home, sid) / "meta.json")
    except Exception:  # noqa: BLE001
        return None


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001 — not a text, or not that form
        return None


def _text_or_dash(v):
    return v if isinstance(v, str) and v else "-"


def candidates_message(home, token, sids, lead_ids=None):
    """The refusal text for a token that names more than one session: one line per candidate, newest
    first (lead `started`, executor `created`), unreadable times last, equal times by id."""
    if lead_ids is None:
        lead_ids = _lead_ids(home)
    rows = []
    for sid in sids:
        if sid in lead_ids:
            rec = _lead_record(home, sid) or {}
            t = rec.get("started")
            ver = rec.get("version")
            ver_part = f"v{ver}, " if isinstance(ver, str) else ""
            desc = f"lead '{_text_or_dash(rec.get('project'))}', {ver_part}"
        else:
            meta = _meta(home, sid) or {}
            t = meta.get("created")
            desc = f"executor '{_text_or_dash(meta.get('topic'))}', "
        desc += state.fmt_age(state.age_seconds(t))
        rows.append((_epoch(t), sid, desc))
    rows.sort(key=lambda r: (r[0] is None, -(r[0] or 0), r[1]))
    lines = "\n".join(f"  {sid}  ({desc})" for _, sid, desc in rows)
    return (f"ambiguous session '{token}' — matches multiple sessions:\n{lines}\n"
            "pass the session id (or a unique prefix of it) instead")


def resolve(home, token, projects=True):
    """An id, or `token` unchanged, or PileadError (code 2) with the candidates message. Writes nothing."""
    if not isinstance(token, str) or not token:
        return token
    try:
        home = Path(home)
    except Exception:  # noqa: BLE001
        return token
    # a / b: an exact id, after at most two file checks; nothing is listed
    if state.valid_session_sid(token) and _is_file(home / "sessions" / token / "meta.json"):
        return token
    if state.valid_lead_sid(token):
        try:
            if _is_file(state.lead_path(home, token)):
                return token
        except Exception:  # noqa: BLE001
            pass
    try:
        lead_ids = _lead_ids(home)
        # c / c2: a lead's project, as typed
        if projects:
            hits = []
            for sid in sorted(lead_ids):
                rec = _lead_record(home, sid)
                if rec is not None and rec.get("project") == token:
                    hits.append(sid)
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise PileadError(candidates_message(home, token, hits, lead_ids), 2)
        # d / d2: a unique prefix of a known id
        if len(token) >= PREFIX_MIN:
            ids = _executor_ids(home) | lead_ids
            hits = sorted(sid for sid in ids if sid.startswith(token))
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise PileadError(candidates_message(home, token, hits, lead_ids), 2)
    except PileadError:
        raise
    except Exception:  # noqa: BLE001 — a resolver fault never stops a verb; the verb answers as today
        return token
    return token

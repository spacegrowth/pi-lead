"""Token usage and live context of a pi session, read from pi's own session log (claude-relay's
`transcript_usage` / `usage_cell` / `ctx_window_cell` / `is_heavy`, rebuilt on pi's log format).

Where the log is: `<session dir>/<timestamp>_<session id>.jsonl`. `<session dir>` is
`$PI_CODING_AGENT_SESSION_DIR`, else the `sessionDir` setting (`<worktree>/.pi/settings.json` over
`<agent dir>/settings.json`; a relative value resolves from the worktree), both ONE flat directory
for every project; else pi's default, `<agent dir>/sessions/--<cwd with / \\ : as ->--/`, where
`<agent dir>` is `$PI_CODING_AGENT_DIR` or `~/.pi/agent`. Every directory directly under
`<agent dir>/sessions/` is searched then, so a session found by id is found whatever its cwd was.
pi-lead launches an executor with `--session-id <sid>`, so its pi session id IS its sid.

What is read: line 1 is the header (`type == "session"`, `id`, `cwd`). Every usage object in the file
counts in the billed totals (assistant messages, tool results, `usage` entries, `compaction` and
`branch_summary` entries). A "good request" is an assistant message whose `stopReason` is neither
`error` nor `aborted` and whose context figure (pi's `calculateContextTokens`: `totalTokens`, else
input + output + cacheRead + cacheWrite) is above 0. The live context is that figure for the last good
request after the latest `compaction`, both on the ACTIVE BRANCH of the log's entry tree (`read`,
`_active_path`); unknown (None) when there is none — as pi's `getContextUsage`. `last_stop` is the
`stopReason` and `errorMessage` of the last assistant message on that same branch (the status engine's
usage-limit pause reads it, so the log is parsed once per command).

A reading that could not be made is None here, `-` in text, `null` in JSON — never 0. Nothing here
raises or runs `pi`; the only directory it creates is `usage/leads/` under home, when a lead's usage
is cached (`lead_reading`)."""
import datetime
import json
import math
import os
import re
from pathlib import Path

from . import config, state

BAD_STOPS = ("error", "aborted")
MB = 1024 * 1024
# The keys `read` returns; a cached usage missing any of them (an older shape) is re-parsed.
KEYS = ("requests", "input", "output", "cache_read", "cache_write", "prompt", "cost", "live", "max_context",
        "last_ts", "model", "models", "cache_hit_rate", "compactions", "mb", "path", "last_stop")
# Kept in the reading for the status engine only (lib/pilead/status.py); `public` leaves them out of
# what the verbs print as JSON.
PRIVATE = ("path", "last_stop")
# How much of the last assistant message's errorMessage `last_stop` keeps: enough for
# usage_limit_pattern to match on (status.py cuts the pause text it shows to 120 characters).
ERROR_TEXT_LEN = 1000
# How much of it a usage cache keeps on disk (`_cached`): what `status.pause_text` shows, no more. Whether
# the whole message matched the usage-limit pattern is kept beside it as `limit`.
CACHED_ERROR_TEXT_LEN = 120


# ── where the log is ──────────────────────────────────────────────────────────────────────────
def agent_dir():
    v = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(v).expanduser() if v else Path.home() / ".pi" / "agent"


def _from(worktree, value):
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = Path(worktree or os.getcwd()) / p
    return Path(os.path.normpath(str(p)))


def _setting_session_dir(worktree):
    """`sessionDir` from project settings, else agent settings, as a Path; None when neither sets it."""
    files = []
    if worktree:
        files.append(Path(worktree) / ".pi" / "settings.json")
    files.append(agent_dir() / "settings.json")
    for f in files:  # project first: it wins
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        v = d.get("sessionDir") if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            return _from(worktree, v.strip())
    return None


def session_dirs(worktree=None):
    """The directories to search, in order. Read-only; never creates one."""
    env = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if env:
        return [_from(worktree, env)]
    s = _setting_session_dir(worktree)
    if s is not None:
        return [s]
    try:
        return sorted(d for d in (agent_dir() / "sessions").iterdir() if d.is_dir())
    except OSError:
        return []


def _obj(line):
    try:
        d = json.loads(line)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def header(path):
    """The log's header dict (line 1, `type == "session"`), or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            h = _obj(f.readline())
    except Exception:
        return None
    return h if h is not None and h.get("type") == "session" else None


def _mtime(p):
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return -1


def locate(sid, worktree=None):
    """The session log of pi session `sid`, or None. A candidate's name ends with `_<sid>.jsonl` and
    its header's `id` is `sid`. Of several: those whose header `cwd` is the worktree, then the most
    recently modified (with no candidate in the worktree, the most recently modified of all)."""
    try:
        if not sid:
            return None
        suffix = f"_{sid}.jsonl"
        cands = []
        for d in session_dirs(worktree):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                p = Path(d) / n
                if not n.endswith(suffix) or not p.is_file():
                    continue
                h = header(p)
                if h is not None and h.get("id") == sid:
                    cands.append((p, h))
        if not cands:
            return None
        if worktree:
            wt = os.path.realpath(str(worktree))
            same = [c for c in cands if isinstance(c[1].get("cwd"), str) and os.path.realpath(c[1]["cwd"]) == wt]
            cands = same or cands
        return max(cands, key=lambda c: _mtime(c[0]))[0]
    except Exception:
        return None


# ── reading it ────────────────────────────────────────────────────────────────────────────────
def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return v if math.isfinite(v) else 0


def _tokens(u):
    """(input, output, cacheRead, cacheWrite) of a usage object; 0 for a missing/non-numeric field.
    `reasoning` (inside output) and `cacheWrite1h` (inside cacheWrite) are never added."""
    return tuple(int(_num(u.get(k))) for k in ("input", "output", "cacheRead", "cacheWrite"))


def context_figure(u):
    """pi's calculateContextTokens: totalTokens, or the four fields' sum when it is 0 or absent."""
    if not isinstance(u, dict):
        return 0
    total = int(_num(u.get("totalTokens")))
    return total if total > 0 else sum(_tokens(u))


def _ts(raw):
    try:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return raw / 1000 if raw > 1e12 else float(raw)
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _model(m):
    provider, model = m.get("provider"), m.get("model")
    if isinstance(model, str) and model:
        return f"{provider}/{model}" if isinstance(provider, str) and provider else model
    return None


def _active_path(nodes, order):
    """The ids on the active branch, or None when the log must be read in file order instead.

    As pi does it (@earendil-works/pi-coding-agent 0.87.1): on load `SessionManager._buildIndex()`
    (dist/core/session-manager.js) makes the LAST entry in file order (the header excluded) the leaf,
    and `buildSessionPath()` walks `parentId` from that leaf to the root (an entry whose `parentId` is
    null); `getContextUsage()` (dist/core/agent-session.js) takes the latest compaction on that branch
    only. `nodes` is {id: parentId}, `order` every entry id in file order. None — file order — when no
    entry names a parent (a log without links), or the walk meets a parent that names no entry or a cycle."""
    if not order or not any(isinstance(par, str) for par in nodes.values()):
        return None
    path, cur = set(), order[-1]
    while cur is not None:
        if cur in path or cur not in nodes:
            return None  # a cycle, or a parent link that names no entry
        path.add(cur)
        par = nodes[cur]
        cur = par if isinstance(par, str) else None
    return path


def read(path):
    """The usage of one session log (keys: KEYS), or None when it cannot be read or has no valid
    header. Streams the file line by line; skips what it cannot parse; never raises.

    Billed totals (input, output, cache_read, cache_write, prompt, cost, requests) sum EVERY entry in
    the file — tokens spent on a branch the user later left were still billed — and `max_context` is
    the largest good request anywhere. `live`, `last_ts` and `model` follow the ACTIVE BRANCH (see
    `_active_path`): the last good request on it after the last compaction on it, and the model of
    the last assistant message on it. `last_stop` is {"stopReason", "errorMessage" (its first
    ERROR_TEXT_LEN characters, or None)} of that last assistant message, None when there is none.
    A log with no parent links is read in file order."""
    try:
        p = Path(path)
        size = p.stat().st_size
        agg = {"requests": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "prompt": 0,
               "cost": None, "live": None, "max_context": 0, "last_ts": None, "model": None, "models": {},
               "cache_hit_rate": None, "compactions": 0, "mb": size / MB, "path": str(p), "last_stop": None}
        nodes, order = {}, []  # id → parentId, and ids in file order (every entry, for the active leaf)
        # What decides live/last_ts/model/last_stop, in file order: ("a", id, model, stop) an assistant message,
        # ("g", id, figure, ts) a good request, ("c", id) a compaction.
        events = []

        def bill(u):
            if not isinstance(u, dict):
                return
            i, o, cr, cw = _tokens(u)
            agg["input"] += i
            agg["output"] += o
            agg["cache_read"] += cr
            agg["cache_write"] += cw
            agg["prompt"] += i + cr + cw
            c = u.get("cost")
            if isinstance(c, dict) and not isinstance(c.get("total"), bool) and isinstance(c.get("total"), (int, float)) \
                    and math.isfinite(c["total"]):
                agg["cost"] = (agg["cost"] or 0.0) + float(c["total"])

        with open(p, "r", encoding="utf-8", errors="replace") as f:
            h = _obj(f.readline())
            if h is None or h.get("type") != "session":
                return None
            for line in f:
                if '"usage"' not in line and '"compaction"' not in line and '"assistant"' not in line:
                    link = _LINK.match(line)  # cheap: only the entry's id and parent are needed
                    if link:
                        eid = link.group(1)
                        nodes[eid] = link.group(3) if link.group(2) != "null" else None
                        order.append(eid)
                        continue
                e = _obj(line)
                if e is None:
                    continue
                eid = e.get("id")
                if isinstance(eid, str):
                    par = e.get("parentId")
                    nodes[eid] = par if isinstance(par, str) else None
                    order.append(eid)
                t = e.get("type")
                if t == "message":
                    m = e.get("message")
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role")
                    if role == "toolResult":
                        bill(m.get("usage"))
                    elif role == "assistant":
                        u = m.get("usage")
                        bill(u)
                        model = _model(m)
                        err = m.get("errorMessage")
                        stop = {"stopReason": m.get("stopReason") if isinstance(m.get("stopReason"), str) else None,
                                "errorMessage": err[:ERROR_TEXT_LEN] if isinstance(err, str) else None}
                        events.append(("a", eid, model, stop))
                        fig = context_figure(u)
                        if m.get("stopReason") in BAD_STOPS or fig <= 0:
                            continue
                        agg["requests"] += 1
                        agg["max_context"] = max(agg["max_context"], fig)
                        events.append(("g", eid, fig, _ts(e.get("timestamp"))))
                        if model:
                            agg["models"][model] = agg["models"].get(model, 0) + 1
                elif t == "usage":
                    bill(e.get("usage"))
                elif t in ("compaction", "branch_summary"):
                    bill(e.get("usage"))
                    if t == "compaction":
                        agg["compactions"] += 1
                        events.append(("c", eid))
        on = _active_path(nodes, order)
        for ev in events:
            if on is not None and ev[1] not in on:
                continue  # an entry of a branch the conversation left
            if ev[0] == "a":
                agg["model"], agg["last_stop"] = ev[2], ev[3]
            elif ev[0] == "g":
                agg["live"], agg["last_ts"] = ev[2], ev[3]
            else:
                agg["live"] = None  # unknown until the next good request
        if agg["prompt"] > 0:
            agg["cache_hit_rate"] = agg["cache_read"] / agg["prompt"]
        return agg
    except Exception:
        return None


# `{"type":"…","id":"…","parentId":null|"…"` at the start of a line, as pi writes every entry; a line
# of another shape is parsed in full instead.
_LINK = re.compile(r'^\{"type":"[^"\\]*","id":"([^"\\]*)","parentId":(null|"([^"\\]*)")')


# ── cached per session ────────────────────────────────────────────────────────────────────────
def limit_pattern(home):
    """The text of the usage-limit pattern in force: config.json's `usage_limit_pattern`, the built-in
    (config.USAGE_LIMIT_PATTERN) when it is null or not text."""
    try:
        v = config.load(home).get("usage_limit_pattern")
    except Exception:
        v = None
    return v if isinstance(v, str) else config.USAGE_LIMIT_PATTERN


def _limit_regex(pattern):
    """`pattern` compiled case-insensitive; the built-in one when it is not text or does not compile."""
    try:
        if isinstance(pattern, str):
            return re.compile(pattern, re.I)
    except re.error:
        pass
    return re.compile(config.USAGE_LIMIT_PATTERN, re.I)


def _for_cache(u, pattern):
    """`u` (a `read` result) as `_cached` writes and returns it: `last_stop`, when it is not None, becomes
    {"stopReason", "errorMessage" (its first CACHED_ERROR_TEXT_LEN characters), "limit" (whether `pattern`
    is found in the message as `read` returned it, all of it)}."""
    ls = u.get("last_stop")
    if not isinstance(ls, dict):
        return u
    msg = ls.get("errorMessage")
    full = msg if isinstance(msg, str) else None
    return {**u, "last_stop": {"stopReason": ls.get("stopReason"),
                               "errorMessage": full[:CACHED_ERROR_TEXT_LEN] if full is not None else None,
                               "limit": bool(full is not None and _limit_regex(pattern).search(full))}}


def _cached(path, cache, write_cache, make_dir=False, pattern=None):
    """read(path), cached in `cache` as {"key": [path, size, mtime_ns], "pattern": <pattern>, "usage": {…}}.
    A cache whose key or pattern differs, that cannot be read, or whose usage lacks a key is re-parsed. The
    cache is written only when `write_cache` and its directory already exists (or `make_dir`: it is created
    then); a failed write is ignored. `cache` None: no cache is read or written. What is written and what is
    returned are the same dict (`_for_cache`): no more than CACHED_ERROR_TEXT_LEN characters of an error
    message ever reach the disk. `pattern` None is the built-in usage-limit pattern."""
    if pattern is None:
        pattern = config.USAGE_LIMIT_PATTERN
    try:
        st = Path(path).stat()
        key = [str(path), st.st_size, st.st_mtime_ns]
    except OSError:
        return None
    if cache is None:
        u = read(path)
        return _for_cache(u, pattern) if u is not None else None
    try:
        d = json.loads(Path(cache).read_text())
        u = d.get("usage") if isinstance(d, dict) else None
        if d.get("key") == key and d.get("pattern") == pattern and isinstance(u, dict) and all(k in u for k in KEYS):
            return u
    except Exception:
        pass
    u = read(path)
    if u is None:
        return None
    u = _for_cache(u, pattern)
    if write_cache:
        try:
            if make_dir:
                Path(cache).parent.mkdir(parents=True, exist_ok=True)
            if Path(cache).parent.is_dir():
                state.atomic_write(cache, json.dumps({"key": key, "pattern": pattern, "usage": u}) + "\n")
        except Exception:
            pass
    return u


def _reading(sid, worktree, cache, write_cache, make_dir=False, pattern=None):
    """(usage, mb): usage as for_session returns it; mb the log's size in MB even when its usage
    cannot be read (None when there is no log)."""
    try:
        path = locate(sid, worktree)
        if path is None:
            return None, None
        u = _cached(path, cache, write_cache, make_dir, pattern=pattern)
        if u is not None:
            return u, u.get("mb")
        try:
            return None, path.stat().st_size / MB
        except OSError:
            return None, None
    except Exception:
        return None, None


def session_reading(home, sid, worktree, write_cache=True):
    """(usage, mb) as `_reading`, cached in sessions/<sid>/usage.json; (None, None) for an id that is not
    a usable session id (no log read, no cache written). Never raises."""
    if not state.valid_session_sid(sid):
        return None, None
    return _reading(sid, worktree, state.session_dir(Path(home), sid) / "usage.json", write_cache,
                    pattern=limit_pattern(home))


def for_session(home, sid, worktree, write_cache=True):
    """An executor's usage, cached in sessions/<sid>/usage.json; None when there is no readable log."""
    return session_reading(home, sid, worktree, write_cache)[0]


def lead_cache_path(home, lead_sid):
    """usage/leads/<lead_sid>.json under home — outside leads/, where lead records are listed — or None
    when `lead_sid` is not a usable lead id (`state.valid_lead_sid`, the one rule `pilead lead-start` and
    every verb taking `--lead` check where a lead id enters): no path is ever built from such an id, so
    none can lead out of that directory."""
    if not state.valid_lead_sid(lead_sid):
        return None
    return Path(home) / "usage" / "leads" / f"{lead_sid}.json"


def lead_reading(home, lead_sid, cwd, write_cache=True):
    """(usage, mb) as session_reading, cached at lead_cache_path (its directory created on write); a
    sid with no safe cache path is read uncached."""
    return _reading(lead_sid, cwd, lead_cache_path(home, lead_sid), write_cache, make_dir=True,
                    pattern=limit_pattern(home))


def is_lead_record(path):
    """True for a file under leads/ that is a lead record: `<sid>.json` whose name does not end in
    `.usage.json` (a lead usage cache an older build wrote there). Lead ids are not restricted — a
    dot is allowed in one — so the rule is by that suffix, not by "no further dot"."""
    name = Path(path).name
    return name.endswith(".json") and not name.endswith(".usage.json")


def for_lead(home, lead_sid, cwd, write_cache=True):
    """A lead's usage, cached in usage/leads/<lead_sid>.json; None when there is no readable log."""
    return lead_reading(home, lead_sid, cwd, write_cache)[0]


def public(usage):
    """The reading as the verbs print it in JSON: without PRIVATE's keys; None stays None."""
    return None if usage is None else {k: v for k, v in usage.items() if k not in PRIVATE}


def snapshot(usage):
    """The ledger's usage snapshot: {prompt, output, requests} from the billed totals, or None."""
    if not usage:
        return None
    return {"prompt": int(usage.get("prompt") or 0), "output": int(usage.get("output") or 0),
            "requests": int(usage.get("requests") or 0)}


# ── the model's window ────────────────────────────────────────────────────────────────────────
def parse_window(text):
    """`1M` → 1000000, `200K` → 200000, `128000` → 128000; None when it is not such a figure."""
    try:
        s = str(text).strip().upper()
        mult = 1
        if s.endswith("M"):
            mult, s = 1_000_000, s[:-1]
        elif s.endswith("K"):
            mult, s = 1_000, s[:-1]
        v = float(s) * mult
        return int(v) if math.isfinite(v) and v > 0 else None
    except Exception:
        return None


def window(home, model):
    """The context window of `model` (`provider/id`) from models.json as it is on disk, whatever its
    age, or None. Never runs `pi` (models.load / models.refresh are not called)."""
    try:
        if not model or "/" not in model:
            return None
        provider, mid = model.split("/", 1)
        d = json.loads((Path(home) / "models.json").read_text())
        for m in d.get("models") or []:
            if isinstance(m, dict) and m.get("provider") == provider and m.get("id") == mid:
                return parse_window(m.get("context"))
    except Exception:
        pass
    return None


# ── cells and the heaviness rule ──────────────────────────────────────────────────────────────
def human_tokens(n):
    """1234 → '1.2k', 1_234_567 → '1.2M', 0 → '0' (relay's)."""
    try:
        n = int(n)
    except Exception:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k" if n >= 10_000 else f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_window(n):
    """1_000_000 → '1M', 200_000 → '200k' — without human_tokens' decimal ('1.0M')."""
    if n % 1_000_000 == 0:
        return f"{n // 1_000_000}M"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def usage_cell(usage):
    """'<prompt>/<output>' such as '1.2M/34k'; '-' when unknown."""
    if not usage:
        return "-"
    return f"{human_tokens(usage.get('prompt') or 0)}/{human_tokens(usage.get('output') or 0)}"


def ctx_cell(usage, window_):
    """'<live>/<window>' such as '146k/1M'; the live figure alone when the window is unknown; '-' when
    the live context is unknown. ' !' is appended when a request was larger than the window (the model
    list's window is wrong for this session)."""
    live = (usage or {}).get("live")
    if live is None:
        return "-"
    if not window_:
        return human_tokens(live)
    cell = f"{human_tokens(live)}/{_fmt_window(window_)}"
    return cell + " !" if ((usage or {}).get("max_context") or 0) > window_ else cell


def is_heavy(usage, mb, token_threshold, mb_threshold):
    """Heavy by the live context when it is known; else by the log's size in MB; neither → not heavy."""
    if usage is not None and usage.get("live") is not None:
        return usage["live"] >= token_threshold
    return mb is not None and mb >= mb_threshold


def is_approaching(usage, warn_tokens, nudge_tokens):
    """At or above the warn threshold and below the nudge one, by the live context only (so nothing is
    ever approaching when warn >= nudge)."""
    live = (usage or {}).get("live")
    return live is not None and warn_tokens <= live < nudge_tokens


def heavy_reading_text(usage, mb):
    """How heavy, in words: the live context when known, else the MB proxy, else 'unknown'."""
    if usage is not None and usage.get("live") is not None:
        return f"{human_tokens(usage['live'])} ctx"
    if mb is not None:
        why = "live context unknown" if usage is not None else "session log unreadable for usage"
        return f"{mb:.1f}MB ({why}; MB proxy)"
    return "unknown"

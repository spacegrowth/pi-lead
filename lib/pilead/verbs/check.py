"""pilead check — print an executor's status (and its TL;DR when reported), its tokens and live
context from pi's session log (lib/pilead/usage.py), and whether the repository's refs moved since the
packet's baseline (lib/pilead/refs.py). Run by a registered lead, it then parks that lead's finished
executors among those it checked (lib/pilead/autoclose.py; `--all`: all of them) and prints one line per
park last; `--refs-only` never does. A reported packet also shows the size of its staged diff
(lib/pilead/wakefacts.py); `--refs-only --wake` adds the facts a wake carries as a second line."""
import json
import sys

from .. import (autoclose, config, ledger, notify, ownership, queue as queue_mod, refs, review, seen, state, status as status_mod,
                usage, wakefacts)

ORDER = 50
RESOLVE = ("sid",)


def _tldr(text):
    """The report's leading TL;DR block: outcome line through the `Changed:` field (max 30 lines)."""
    out, seen_status = [], False
    for line in text.splitlines():
        if not line.strip():
            if out and seen_status:
                break
            continue
        if line.startswith("#") and out:
            break
        seen_status = seen_status or line.startswith("Status:")
        out.append(line)
        if len(out) >= 30:
            break
    return "\n".join(out)


def _refs(home, sid, meta, records=None):
    return refs.check(home, sid, int(meta.get("packets") or 1), meta.get("worktree") or "", records=records)


class _Ledger:
    """The ledger's records, read on first use and at most once per command (refs' move de-dupe and
    the reported snapshot share it)."""

    def __init__(self, home):
        self.home, self._recs = home, None

    def __iter__(self):
        if self._recs is None:
            self._recs = ledger.read(self.home)
        return iter(self._recs)

    @property
    def cache(self):
        """The records already read (a list status.refresh appends its event to), or None."""
        return self._recs


def tokens_line(home, meta, u, mb):
    """`tokens <prompt>/<output> over <n> requests · ctx <live>/<window>` [· heavy | · approaching heavy]."""
    warn, nudge, mb_th = config.thresholds(home)
    line = (f"tokens {usage.usage_cell(u)} over {u.get('requests') or 0} requests"
            f" · ctx {usage.ctx_cell(u, usage.window(home, meta.get('model')))}")
    if usage.is_heavy(u, mb, nudge, mb_th):
        line += " · heavy"
    elif usage.is_approaching(u, warn, nudge):
        line += " · approaching heavy"
    return line


def _note_reported(home, sid, meta, status, u, records):
    """`usage_snapshot` at "reported", the first time `check` finds the current packet's report —
    once per (session, packet). Best-effort: never breaks a check."""
    try:
        n = int(meta.get("packets") or 1)
        if status != "reported" or not state.report_path(home, sid, n).is_file():
            return
        if any(r.get("event") == "usage_snapshot" and r.get("session_id") == sid and r.get("packet") == n
               and r.get("at") == "reported" for r in records):
            return
        rec = {"event": "usage_snapshot", "session_id": sid, "packet": n, "at": "reported",
               "usage": usage.snapshot(u), "caller": seen.caller(home, meta)}
        ledger.append(home, rec.pop("event"), **rec)
        cache = getattr(records, "cache", None)
        if isinstance(cache, list):  # seen.note, right after, reads the same records
            cache.append({"event": "usage_snapshot", **rec})
    except Exception:  # noqa: BLE001
        pass


def cmd_refs_only(a):
    """`--refs-only`: exactly one line (refs.one_line) and exit 0, whatever happens — the extension
    puts this line in the lead's wake message and must never lose the wake over it. With `--wake` a
    second line follows, `wake: ` and the facts of lib/pilead/wakefacts.py as one line of JSON; when they
    cannot be made that line is left out. Nothing is written either way."""
    try:
        home = state.home_dir(a.home)
        meta = state.load_meta(home, a.sid)
        print(refs.one_line(_refs(home, a.sid, meta)))
    except Exception as e:  # noqa: BLE001
        print(f"refs: not compared ({' '.join(str(e).split())})")
    if a.wake:
        try:
            line = "wake: " + json.dumps(wakefacts.facts(state.home_dir(a.home), a.sid))
        except Exception:  # noqa: BLE001
            line = None
        if line:
            print(line)
    return 0


def _queue_lines(home, sid, out):
    """Right after the status line, only when there is something to say: `queued: <n> waiting`, then
    `queue: cannot be read (<path>)` for an unreadable queue, then `queue: #<id> could not be
    delivered — <last_error>` when the head has one. Returns (queued, queue_error) for `--json`:
    `queued` is the item count, or None when unreadable; `queue_error` is the head's `last_error`,
    the unreadable text, or None. A session with no `queue.json` reads nothing beyond that (`n` is 0,
    no line, no extra field beyond `queued: 0`/`queue_error: None`)."""
    try:
        items, _ = queue_mod.read(home, sid)
    except queue_mod.QueueUnreadable as e:
        out(f"queue: cannot be read ({e.path})")
        return None, str(e)
    n = len(items)
    if n:
        out(f"queued: {n} waiting — pilead queue {sid}")
    head_error = items[0].get("last_error") if items else None
    if head_error:
        out(f"queue: #{items[0]['id']} could not be delivered — {head_error}")
    return n, head_error


def _stderr_unknown(sid, mb):
    """The one stderr line for a session whose usage is unknown: a log found but unreadable, or none."""
    if mb is not None:
        return f"pilead: session log for {sid} could not be read; tokens and context are unknown"
    return f"pilead: no session log found for {sid}; tokens and context are unknown"


def check_one(home, sid, records, out=print, err=None):
    """One session's check, printed through `out` (stdout text by default). Returns the JSON object
    `--json` prints for it. Raises PileadError for an unknown session (the caller decides)."""
    err = err or (lambda line: print(line, file=sys.stderr))
    meta = state.load_meta(home, sid)
    res = _refs(home, sid, meta, records)
    # Above the status line, so it is the first thing read: a move, and a comparison that could not
    # be made (it must never read as "unchanged").
    if res["state"] == "moved":
        out("\n".join(refs.block(res, sid)))
        out("")
    elif res["state"] == "not-compared":
        out(refs.one_line(res))
    # Read first and hand it to the status engine: the session's log is parsed once per check.
    u, mb = usage.session_reading(home, sid, meta.get("worktree") or None)
    status = status_mod.refresh(home, meta, getattr(records, "cache", None), reading=(u, mb))
    out(status_mod.display(meta, status))  # `superseded` shown; the stored word stays `closed`
    queued, queue_error = _queue_lines(home, sid, out)
    n = int(meta.get("packets") or 1)
    has_report = state.report_path(home, sid, n).is_file()
    dsize = wakefacts.diff_size(meta.get("worktree") or None) if has_report else None  # read only for a report
    rep, tldr = None, None
    if status == "reported":
        rep = state.latest_report(home, sid)
        if rep:
            tldr = _tldr(rep.read_text())
            out(str(rep))
            dline = wakefacts.diff_line(dsize) if dsize is not None else None
            if dline:
                out(dline)
            out(tldr)
    if u is not None:
        out(tokens_line(home, meta, u, mb))
    else:  # stderr: stdout stays exactly what it was before accounting existed
        err(_stderr_unknown(sid, mb))
    _note_reported(home, sid, meta, status, u, records)
    seen.note(home, sid, meta, "check", records=records)  # who looked (lib/pilead/seen.py)
    if res["state"] == "unchanged":
        out(refs.one_line(res))
    # "not-a-repo" prints nothing here (a worktree outside git with no baseline stored has nothing to
    # detect); `--refs-only` and `verify` do say it. A worktree gone AFTER a baseline was stored is
    # "not-compared" (`repository missing`), printed first above.
    warn, nudge, mb_th = config.thresholds(home)
    res_json = {"sid": sid, "status": status, "packet": n,
                "reported": has_report,
                "report": str(rep) if rep else None, "tldr": tldr,
                "refs": {"state": res["state"], "line": refs.one_line(res)},
                "usage": usage.public(u),
                "ctx_window": usage.window(home, meta.get("model")),
                "heavy": usage.is_heavy(u, mb, nudge, mb_th), "approaching": usage.is_approaching(u, warn, nudge),
                "queued": queued, "queue_error": queue_error,
                "diff": wakefacts.diff_json(dsize)}
    if meta.get("superseded_by"):  # carried only when set: the stored status stays `closed`
        res_json["superseded_by"] = meta["superseded_by"]
    # who wakes on this session's report (lib/pilead/ownership.py): after every line check printed before
    o = ownership.owner(home, meta) if status not in ("closed", "dead") else {"state": "owned"}
    if o["state"] == "orphan":
        err(f"pilead: {sid} is an orphan — its lead {o['lead']} is no longer registered; pilead adopt {sid}")
    elif o["state"] == "unknown":
        err(f"pilead: {sid}: owner unknown ({o['why']})")
    res_json["orphan"] = True if o["state"] == "orphan" else None if o["state"] == "unknown" else False
    notify.ctx_warn(home, sid, meta, u, status)  # after all the output: a banner changes nothing that is printed
    return res_json


def _live_sids(home):
    """Every session that is not closed or dead (by its stored status), in sid order; a session whose
    record cannot be read is included (its check reports the error)."""
    d = home / "sessions"
    out = []
    for mp in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        sid = mp.parent.name
        try:
            meta = json.loads(mp.read_text())
            if state.read_status(home, sid, meta) in ("closed", "dead"):
                continue
        except Exception:
            pass
        out.append(sid)
    return out


def cmd_check(a):
    if a.wake and not a.refs_only:
        print("pilead: check --wake needs --refs-only", file=sys.stderr)
        return 2
    # `a.sid is None`, not `not a.sid`: an empty id is an id given, refused by the session-id rule
    if a.refs_only:
        if a.sid is None:
            print("pilead: check --refs-only needs a session id", file=sys.stderr)
            return 2
        return cmd_refs_only(a)
    if a.sid is None and not a.all:
        print("pilead: check needs a session id, or --all", file=sys.stderr)
        return 2
    home = state.home_dir(a.home)
    records = _Ledger(home)
    if not a.all and not a.json:
        queue_mod.deliver(home, a.sid, "check", out=print)  # before the session is checked
        check_one(home, a.sid, records)
        autoclose.after_command(home, "check", sids=[a.sid])  # last: the lines above show what check found
        review.refresh_after(home)  # and a live board is rewritten
        return 0
    sids = _live_sids(home) if a.all else [a.sid]
    results = []
    to_err = lambda line: print(line, file=sys.stderr)  # noqa: E731
    for sid in sids:
        if not a.json:
            print(f"== {sid} ==")
        queue_mod.deliver(home, sid, "check", out=to_err if a.json else print)  # before the session is checked
        try:
            results.append(check_one(home, sid, records, out=to_err if a.json else print))
        except Exception as e:  # noqa: BLE001 — one unreadable session never stops the others
            msg = " ".join(str(e).split()) or type(e).__name__
            results.append({"sid": sid, "error": msg})
            if not a.json:
                print(f"error: {msg}")
    if a.json:
        print(json.dumps(results, indent=2))
    # last, after the command's own output; `--json`: the park lines go to stderr
    autoclose.after_command(home, "check", sids=None if a.all else [a.sid], to_stderr=a.json)
    review.refresh_after(home)  # and a live board is rewritten
    return 0


def register(verb):
    p = verb("check", cmd_check, "print an executor's status (and its TL;DR when reported)")
    p.add_argument("sid", nargs="?")
    p.add_argument("--all", action="store_true",
                   help="check every session that is not closed or dead, in sid order")
    p.add_argument("--json", action="store_true", help="print only JSON: one object per session checked")
    p.add_argument("--refs-only", action="store_true",
                   help="print only the one-line refs result and exit 0")
    p.add_argument("--wake", action="store_true",
                   help="with --refs-only: a second line of JSON facts (diff size, landing)")

"""How a session ends, shared by `close`, `stop`, `keep`, `prune`, `resume` and `restart` (not a verb
module, so one broken verb never takes another down with it): the calling lead, a lead's pending wake
lines, the removal of a lead's files when it steps down or is pruned, closing an executor, whether an
executor is still alive, and the name of a successor session."""
import json
import os
import re
import sys
from pathlib import Path

from . import ledger, plan as plan_mod, state, usage, wakehealth


def caller(home):
    """The calling lead's sid: $PI_LEAD_SID, else $PI_SESSION_ID, when that is a usable lead id AND a lead
    record exists for it; else None. Nothing is read for an id that is not usable."""
    sid = os.environ.get("PI_LEAD_SID") or os.environ.get("PI_SESSION_ID")
    if not state.valid_lead_sid(sid):
        return None
    return sid if (Path(home) / "leads" / f"{sid}.json").is_file() else None


def lead_project(home, sid):
    """The `project` of leads/<sid>.json, or None (no usable id, no record, unreadable)."""
    if not state.valid_lead_sid(sid):
        return None
    try:
        rec = json.loads((Path(home) / "leads" / f"{sid}.json").read_text())
        return rec.get("project") if isinstance(rec, dict) else None
    except Exception:
        return None


def _count_lines(path):
    """Non-blank lines of `path`; None when it cannot be read."""
    try:
        return sum(1 for ln in Path(path).read_text(errors="replace").splitlines() if ln.strip())
    except OSError:
        return None


def wake_files(home, sid):
    """[(path, pending lines or None)] for leads/<sid>.inbox.md (when it exists) and every
    leads/<sid>.inbox.md.claim-* file, in name order."""
    d = Path(home) / "leads"
    inbox = d / f"{sid}.inbox.md"
    files = [inbox] if os.path.lexists(inbox) else []
    if d.is_dir():
        files += sorted(d.glob(f"{sid}.inbox.md.claim-*"))
    return [(p, _count_lines(p)) for p in files]


def pending_wake_lines(home, sid):
    """The lead's pending wake lines (inbox plus claim files); None when a file could not be read (an
    unknown count is never "none")."""
    total = 0
    for _, n in wake_files(home, sid):
        if n is None:
            return None
        total += n
    return total


def inside_home(home, path):
    """True when `path`'s directory (symbolic links followed) is inside `home` (likewise resolved)."""
    try:
        root = Path(home).resolve()
        parent = Path(path).parent.resolve()
        return parent == root or root in parent.parents
    except Exception:
        return False


def lead_files(home, sid):
    """The files a lead's step-down removes, whether or not they exist: its record, route, usage cache,
    an older build's leads/<sid>.usage.json, and handoffs/<sid>.md (the memo copy `pilead handoff` started it
    from). `sid` must already be a usable lead id."""
    d = Path(home) / "leads"
    out = [d / f"{sid}.json", d / f"{sid}.route"]
    cache = usage.lead_cache_path(home, sid)
    if cache is not None:
        out.append(cache)
    out.append(d / f"{sid}.usage.json")
    out.append(Path(home) / "handoffs" / f"{sid}.md")
    return out


def launch_dir(home, sid):
    """leads/<sid>.launch/: where `pilead resume` of a lead keeps its launch's own files (`pid`,
    `iterm-id`, `bootstrap.sh`). `sid` must already be a usable lead id."""
    return Path(home) / "leads" / f"{sid}.launch"


def _unlink(path):
    """Remove one file (a symbolic link itself, never its target); True when something was removed."""
    p = Path(path)
    if not os.path.lexists(p) or (p.is_dir() and not p.is_symlink()):
        return False
    p.unlink()
    return True


def remove_lead(home, sid, pending):
    """Remove a lead's files (lead_files, and its inbox when `pending` == 0). Returns (removed paths,
    kept [(path, why)]). A file whose directory is outside home is kept, never removed."""
    removed, kept = [], []
    for p in lead_files(home, sid):
        if not os.path.lexists(p):
            continue
        if not inside_home(home, p):
            kept.append((p, "outside home"))
            continue
        try:
            if _unlink(p):
                removed.append(p)
        except OSError as e:
            kept.append((p, f"could not be removed: {e.strerror or e}"))
    _remove_launch_dir(home, sid, removed, kept)
    _remove_wake_dir(home, sid, removed, kept)
    for p, n in wake_files(home, sid):
        is_inbox = p.name == f"{sid}.inbox.md"
        if pending == 0:
            if not is_inbox:
                continue  # an empty claim file holds nothing pending; it is not one of the lead's files
            if not inside_home(home, p):
                kept.append((p, "outside home"))
                continue
            try:
                if _unlink(p):
                    removed.append(p)
            except OSError as e:
                kept.append((p, f"could not be removed: {e.strerror or e}"))
            continue
        what = "inbox" if is_inbox else "claimed inbox"
        count = f"{n} pending wake line(s)" if n is not None else "pending wake lines unknown (unreadable)"
        kept.append((p, f"{what}, {count}"))
    return removed, kept


def _remove_launch_dir(home, sid, removed, kept):
    """Remove leads/<sid>.launch/ with the files directly inside it (`_remove_dir`)."""
    _remove_dir(home, launch_dir(home, sid), removed, kept)


def _remove_wake_dir(home, sid, removed, kept):
    """Remove wake/<sid>/ (the lead's health file, lib/pilead/wakehealth.py) the same way (`_remove_dir`)."""
    _remove_dir(home, wakehealth.wake_dir(home, sid), removed, kept)


def _remove_dir(home, d, removed, kept):
    """Remove directory `d` with the files directly inside it (a link inside is removed itself, never its
    target), then the directory; appended to `removed` or, with why, to `kept`. Nothing when it does not
    exist. A link in its place is removed like a file; a directory outside home, or one holding a
    directory, is kept."""
    if not os.path.lexists(d):
        return
    if not inside_home(home, d):
        kept.append((d, "outside home"))
        return
    try:
        if d.is_symlink() or not d.is_dir():
            if _unlink(d):
                removed.append(d)
            return
        for e in sorted(d.iterdir()):
            if e.is_dir() and not e.is_symlink():
                kept.append((d, f"holds a directory ({e.name}); not removed"))
                return
        for e in sorted(d.iterdir()):
            _unlink(e)
        d.rmdir()
        removed.append(d)
    except OSError as e:
        kept.append((d, f"could not be removed: {e.strerror or e}"))


def tab_handle(meta, sdir):
    """An executor tab's handle: sessions/<sid>/iterm-id, else the tab recorded at launch; "" when neither."""
    try:
        h = (Path(sdir) / "iterm-id").read_text().strip()
    except OSError:
        h = ""
    return h or (meta or {}).get("tab") or ""


def rename_tab(meta, handle, title, home=None):
    """Give an executor's tab a new name WITHOUT typing into it: `tab_title` = `title` goes into `meta` and into
    sessions/<sid>/meta.json (a `.tmp`, then a rename), and the executor's extension names its tab from it
    (pi's title is written from the session name). True when that write succeeded; any failure: False. No
    backend is asked — except under tmux (meta `backend` "tmux"), where the window is also renamed
    (`tmux_backend.rename_by_id`, window only, nothing typed). A session whose pi is not running gets the name
    at its next start. `home` defaults to state.home_dir(). No `handle` (the session has no known tab): False,
    nothing written."""
    if not handle:  # no known tab: nothing to name (the label stays what it was)
        return False
    try:
        sid = (meta or {}).get("sid")
        p = state.session_dir(Path(home) if home is not None else state.home_dir(), sid) / "meta.json"
        rec = json.loads(p.read_text())
        if not isinstance(rec, dict):
            return False
        rec["tab_title"] = title
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(rec, indent=2) + "\n")
        os.replace(tmp, p)
        meta["tab_title"] = title
    except Exception:  # noqa: BLE001 — a rename is best-effort
        return False
    if str(meta.get("backend") or "").lower() == "tmux":
        # tmux: the status-bar WINDOW name is pi-lead's to set (rename-window, nothing typed); a split
        # pane is left alone. Cosmetic: the return value stays the meta.json write's.
        try:
            from .launch import backend
            tm = backend.by_name("tmux")
            tm.rename_by_id(handle, title)
        except Exception:  # noqa: BLE001
            pass
    return True


def open_executors(home, sid):
    """The sids of `sid`'s executors whose stored status is not closed or dead, in sid order."""
    d = Path(home) / "sessions"
    out = []
    for mp in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        esid = mp.parent.name
        if not state.valid_session_sid(esid):
            continue
        try:
            meta = json.loads(mp.read_text())
            if not isinstance(meta, dict) or meta.get("lead") != sid:
                continue
            if state.read_status(Path(home), esid, meta) in ("closed", "dead"):
                continue
        except Exception:
            continue
        out.append(esid)
    return out


def plan_holds(home, sid):
    """(plan path, why it must stay) for a lead's plan file, from the stored items only (nothing is bound,
    nothing is written): why is None for a plan with no open item (it may go with the lead), else
    `<n> open item(s)` or `cannot be read`. (None, None) when the lead has no plan file."""
    p = plan_mod.path(home, sid)
    plan, st = plan_mod.read(home, sid)
    if st == "none":
        return None, None
    if plan is None:
        return p, "cannot be read"
    n = plan_mod.open_items(plan)
    return p, (f"{n} open item(s)" if n else None)


def remove_plan(home, sid):
    """Remove a lead's plan file (a link itself, never its target). None when it went, else why it stayed."""
    p = plan_mod.path(home, sid)
    if not inside_home(home, p):
        return "outside home"
    try:
        _unlink(p)
    except OSError as e:
        return f"could not be removed: {e.strerror or e}"
    return None


def _step_down_plan(home, sid, out):
    """`stop`'s plan lines: a plan with no open item is removed and named; one with open items, or one that
    cannot be read, is kept and named with the reason; no plan file, no line."""
    p, why = plan_holds(home, sid)
    if p is None:
        return
    if why is None:
        why = remove_plan(home, sid)
        if why is None:
            out(f"removed {p}")
            return
    out(f"kept {p} — {why}")


def step_down(home, sid, out=print, err=None):
    """`pilead stop <sid>` / `pilead close --self <sid>`: the lead steps down. The id is checked FIRST
    (state.check_lead_sid raises before anything is read); a sid with no record changes nothing. Removes
    lead_files and, with no pending wake lines, the inbox; a plan with no open item is removed too, one with
    open items or one that cannot be read is kept (`plan_holds`); signals no process, touches no tab and no
    executor's files. Returns the exit code."""
    err = err or (lambda line: print(line, file=sys.stderr))
    state.check_lead_sid(sid)
    home = Path(home)
    if not (home / "leads" / f"{sid}.json").is_file():
        out(f"{sid} is not a registered lead — nothing to stop")
        return 0
    project = lead_project(home, sid)
    open_execs = open_executors(home, sid)
    removed, kept = remove_lead(home, sid, pending_wake_lines(home, sid))
    for p in removed:
        out(f"removed {p}")
    for p, why in kept:
        out(f"kept {p} — {why}")
    _step_down_plan(home, sid, out)
    ledger.append(home, "lead_stepped_down", session_id=sid, project=project)
    out(f"lead {sid} stepped down — wakes are off")
    if open_execs:
        err(f"pilead: lead {sid} still has executor(s) not closed or dead: {', '.join(open_execs)} — their "
            f"reports will wake nobody until a lead registers under {sid} again")
    return 0


# ── an executor: close it, is it alive, its successor's name ────────────────────────────────────
def close_executor(home, sid, meta, by=None, keep_tab=False, reason="manual close", out=print, err=None):
    """Close executor `sid` (`pilead close`'s executor path; `restart` calls it too): a pinned session is
    refused (PileadError) before anything changes. Unless `keep_tab`, its pi is signalled and its tab
    closed; with `keep_tab` the tab is retitled `[closed] <sid>` and left running. `by` records what
    superseded it. Stores `closed` and ledgers `superseded` (when `by`) or `closed` with `reason`. Prints
    `closed <sid>…` through `out`, and through `err` the note that a busy packet had no report."""
    from .launch import _backend_for, _kill_pi
    err = err or (lambda line: print(line, file=sys.stderr))
    if meta.get("keep"):
        raise state.PileadError(f"session {sid} is pinned; `pilead keep {sid} --off` releases it. Nothing was closed.")
    sdir = state.session_dir(home, sid)
    stored = state.read_status(home, sid, meta)  # as stored: no status.refresh first
    n = int(meta.get("packets") or 1)
    unreported_busy = stored == "busy" and not state.report_path(home, sid, n).is_file()
    handle = tab_handle(meta, sdir)
    if keep_tab:
        # the process is not signalled and the tab stays; it says what it is now
        title = f"[closed] {sid}"
        if not (meta.get("kept_tab") and meta.get("label_open")):
            meta["label_open"] = meta.get("label")
        meta["kept_tab"] = True
        if rename_tab(meta, handle, title, home=home):
            meta["label"] = title
    else:
        _kill_pi(sdir / "pid")
        _backend_for(meta, home).close(meta["label"], handle or None)
        meta.pop("kept_tab", None)
    if by is not None:
        meta["superseded_by"] = by
    state.save_meta(home, meta)
    state.write_status(home, sid, "closed")
    if by is not None:
        ledger.append(home, "superseded", session_id=sid, superseded_by=by)
    else:
        ledger.append(home, "closed", session_id=sid, reason=reason)
    out(f"closed {sid}" + (f" (superseded by {by})" if by is not None else "")
        + (" (tab kept)" if keep_tab else ""))
    if unreported_busy:
        err(f"pilead: {sid} was busy on packet {n:04d}; no report was written")


def executor_liveness(meta, sdir):
    """"alive" (its pid is alive; or, with no readable pid file, its tab exists), "tab-open" (its pid is
    gone but its tab exists: a pi started there by hand would be a second copy of the conversation) or
    "gone". Asks the process first and the terminal only when that did not settle it."""
    from . import status
    pid = status.pid_alive(Path(sdir) / "pid")
    if pid is True:
        return "alive"
    tab = status.executor_tab_exists(meta, sdir)
    if pid is None:
        return "alive" if tab else "gone"
    return "tab-open" if tab else "gone"


_ROTATION = re.compile(r"-r\d+$")


def successor_name(home, sid):
    """The session id of the session that takes over `sid`'s work: `sid` with a trailing `-r<digits>`
    removed, then `-r<N>` with N the smallest number >= 2 for which no session directory exists
    (`foo` -> `foo-r2`, `foo-r2` -> `foo-r3`, never `foo-r2-r2`). PileadError when that is not a usable
    session id."""
    base = _ROTATION.sub("", sid) or sid
    n = 2
    while state.session_dir(Path(home), f"{base}-r{n}").exists():
        n += 1
    return state.check_session_sid(f"{base}-r{n}")

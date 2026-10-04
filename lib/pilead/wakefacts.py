"""What a lead is told about a report besides its refs line: how big the staged change is, and whether the
work was committed already. `pilead check <sid> --refs-only --wake` prints these as the second line the lead's
extension reads (extensions/pi-lead.ts `wakeFacts`); `pilead check` and `pilead list` show the size.

Nothing here writes anything, and nothing here raises: a fact that could not be read is None (or the state
`not-read`), never 0, never `empty`, never "landed". Every git call goes to the real git (`refs.git_path()`) as
an argument list with no shell and a timeout.

Terms (packet p20h):
  diff state  `staged` (git answered, at least one file is staged), `empty` (git answered, nothing staged),
              `not-a-repo` (the directory exists and is in no repository), `no-worktree` (none recorded, or the
              directory is gone), `not-read` (git could not be run, failed, or was silent for 10 seconds).
  landed      `True` (the report claims at least one tracked file, every claim is clean in the worktree and HEAD
              is not older than the report), `False` (a claimed path is still dirty), `None` (cannot be told).
              The rule is the auto-close sweep's (`autoclose.claims_landed`); this module only reads its inputs.
  proven      landed is True AND the ledger holds for this session and packet a `refs_accepted`, or an
              `auto_commit` with `cleared` true, or an `auto_closed` with reason `landed` written after the
              session's last `packet_sent` (packet 1: after its `spawned`) — `auto_closed` names no packet, so
              one written for an earlier packet proves nothing about this one."""
import os
import subprocess
import time
from pathlib import Path

from . import autoclose, ledger, refs, state

GIT_TIMEOUT_SECONDS = 10


def _first_line(text):
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")[:200]


def _result(st, files=None, insertions=None, deletions=None, text=None, why=None):
    return {"state": st, "files": files, "insertions": insertions, "deletions": deletions, "text": text,
            "why": why}


class _NotRead(Exception):
    pass


def _git(worktree, argv, deadline):
    """(returncode, stdout, stderr) of the real git in `worktree`; _NotRead when it cannot be run or does not
    answer before `deadline` (a time.monotonic() value shared by every call of one reading)."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise _NotRead(f"git did not answer in {GIT_TIMEOUT_SECONDS} seconds")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    try:
        r = subprocess.run(["git", "-C", str(worktree), *argv], executable=refs.git_path(), capture_output=True,
                           text=True, errors="replace", env=env, stdin=subprocess.DEVNULL, timeout=left)
    except subprocess.TimeoutExpired:
        raise _NotRead(f"git did not answer in {GIT_TIMEOUT_SECONDS} seconds") from None
    except (OSError, subprocess.SubprocessError, refs.RefsError) as e:
        raise _NotRead(_first_line(str(e)) or type(e).__name__) from None
    return r.returncode, r.stdout, r.stderr


def diff_size(worktree):
    """`{"state", "files", "insertions", "deletions", "text", "why"}` of what is staged in `worktree`. The
    numbers and `text` are None unless the state is `staged` or `empty` (`empty`: numbers 0, `text` None);
    `text` is `(diff: <n> files +<a>/-<d>)`; `why` is a short reason for `not-read`, else None. Never raises."""
    try:
        if not worktree or not Path(worktree).is_dir():
            return _result("no-worktree")
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        rc, out, err = _git(worktree, ["rev-parse", "--is-inside-work-tree"], deadline)
        if rc != 0:
            if "not a git repository" in (err or ""):
                return _result("not-a-repo")
            return _result("not-read", why=f"git failed: {_first_line(err) or f'git rev-parse exited {rc}'}")
        if out.strip() != "true":
            return _result("not-read", why="not inside a work tree")
        rc, out, err = _git(worktree, ["--no-optional-locks", "diff", "--cached", "--numstat"], deadline)
        if rc != 0:
            return _result("not-read", why=f"git failed: {_first_line(err) or f'git diff exited {rc}'}")
        files = ins = dels = 0
        for line in out.splitlines():
            if not line.strip():
                continue
            files += 1
            cols = line.split("\t", 2)
            if len(cols) >= 2:
                if cols[0].isdigit():
                    ins += int(cols[0])
                if cols[1].isdigit():
                    dels += int(cols[1])
        if files == 0:
            return _result("empty", 0, 0, 0)
        return _result("staged", files, ins, dels, f"(diff: {files} files +{ins}/-{dels})")
    except _NotRead as e:
        return _result("not-read", why=str(e))
    except Exception as e:  # noqa: BLE001 — a fact that cannot be read is `not-read`, never an error
        return _result("not-read", why=_first_line(str(e)) or type(e).__name__)


def diff_json(d):
    """The `diff` key of `check --json`: a `diff_size` dict without its `text`, None for None (no report)."""
    return {k: v for k, v in d.items() if k != "text"} if d is not None else None


def diff_line(d):
    """The line `pilead check` prints for a `diff_size` dict, or None when there is nothing to say."""
    if d.get("state") == "staged":
        return f"diff: {d['files']} files +{d['insertions']}/-{d['deletions']}"
    if d.get("state") == "not-read":
        return f"diff: not read ({d.get('why') or 'unknown'})"
    return None


def _proven(records, sid, n):
    """The ledger's proof that packet `n` of `sid` was committed (see the module's `proven`)."""
    sent = spawned = None
    for i, r in enumerate(records):
        if r.get("session_id") != sid:
            continue
        ev = r.get("event")
        if ev == "refs_accepted" and r.get("packet") == n:
            return True
        if ev == "auto_commit" and r.get("packet") == n and r.get("cleared") is True:
            return True
        if ev == "packet_sent":
            sent = i
        elif ev == "spawned":
            spawned = i
    anchor = sent if sent is not None else (spawned if n == 1 else None)
    if anchor is None:
        return False
    return any(r.get("session_id") == sid and r.get("event") == "auto_closed" and r.get("reason") == "landed"
               for r in records[anchor + 1:])


def landed(home, sid, n, records=None):
    """`(value, proven)` for packet `n` of `sid`: value True / False / None as the module says, proven True only
    when value is True and the ledger proves it. `records` is the ledger already read (default: read it here).
    Never raises: an error gives (None, False)."""
    try:
        home = Path(home)
        meta = state.load_meta(home, sid)
        wt = meta.get("worktree") or None
        rp = state.report_path(home, sid, n)
        if not rp.is_file() or not wt:
            return None, False
        mtime = rp.stat().st_mtime
        from .review import report_verify  # vendored (review sets up its import path)
        claimed = [autoclose._norm(p) for p in report_verify.claimed_paths(rp.read_text(errors="replace"))[0]]
        if not claimed:
            return None, False
        value = autoclose.claims_landed(claimed, autoclose.repo_files(wt), autoclose.dirty_paths(wt),
                                        autoclose.head_time(wt), mtime)
        if value is not True:
            return value, False
        recs = ledger.read(home) if records is None else records
        return True, bool(_proven(recs, sid, n))
    except Exception:  # noqa: BLE001
        return None, False


def facts(home, sid, records=None):
    """`{"packet", "diff", "diff_state", "landed", "proven"}` for the session's current packet; `diff` is the
    diff text or None. A session that cannot be read: every value None, `diff_state` `no-worktree`, `proven`
    False. Never raises."""
    unreadable = {"packet": None, "diff": None, "diff_state": "no-worktree", "landed": None, "proven": False}
    try:
        home = Path(home)
        meta = state.load_meta(home, sid)
        n = int(meta.get("packets") or 1)
        d = diff_size(meta.get("worktree") or None)
        value, proven = landed(home, sid, n, records)
        return {"packet": n, "diff": d["text"], "diff_state": d["state"], "landed": value, "proven": bool(proven)}
    except Exception:  # noqa: BLE001
        return unreadable

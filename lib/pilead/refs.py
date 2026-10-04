"""Refs snapshots: DETECT, after the fact, that a repository's history or refs moved while an executor
worked — whatever route made the move (a script, another language's git library, `sudo`, a computed
program name: everything the text fence and the git shim cannot see). It detects; it prevents nothing.

A snapshot of an executor's worktree is `git rev-parse HEAD`, the current branch, every ref with its
object id (`for-each-ref`), the stash ref, and the linked worktrees (`worktree list --porcelain`).
`pilead spawn` and `pilead send` store one per packet at `sessions/<sid>/refs-NNNN.json`;
`pilead check` / `verify` take a fresh one and `compare` the two; `pilead refs accept` replaces the
stored baseline after the lead's own commit. The index and the working tree are NOT part of a
snapshot, so staging, unstaging and editing files never register.

A comparison has exactly one of four outcomes (`check`), and one that could not be made is never
silent: `unchanged`, `moved`, `not-compared` (the worktree is a repository but the packet's baseline
is missing, unreadable or not the shape `snapshot` writes, or git fails now; or a baseline WAS stored
and the worktree is no longer a repository — `repository missing`) and `not-a-repo` (no baseline was
stored and the worktree is not a repository: nothing to detect).

Every git call here goes to the REAL git by absolute path (`git_path`), never to whatever `git` comes
first on PATH: an executor's shim dir, or any `git` that does not answer `--version` as git, is
skipped."""
import json
import os
import subprocess
import sys
from pathlib import Path

from . import ledger, state

MAX_COMMITS = 50  # new commits listed per comparison; the rest are counted, not listed
SHIM_MARKER = b"PILEAD_GIT_SHIM"  # lib/pilead/shim.py MARKER, as bytes (the shim's second line carries it)
FALLBACK_GITS = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
_SEP = "\x1f"


class RefsError(Exception):
    """A snapshot could not be taken (no git, a git call failed). The message is the few words a
    `refs: not compared (<why>)` line carries, e.g. `git failed: fatal: …`."""


class NotARepo(RefsError):
    """The worktree is not inside a git repository (or does not exist): there is nothing to snapshot."""


# ── the real git ──────────────────────────────────────────────────────────────────────────────
_resolved = {}


def _is_shim(p):
    try:
        with open(p, "rb") as f:
            return SHIM_MARKER in f.read(512)
    except OSError:
        return True


def _answers_as_git(p):
    try:
        r = subprocess.run(["git", "--version"], executable=p, capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.startswith("git version")


def git_path(env_path=None):
    """Absolute path of the real git: the first `git` on PATH (then the usual install locations) that
    is an absolute executable file, is not a pi-lead shim (`$PILEAD_SHIM_DIR` or the shim marker), and
    answers `--version` with `git version …`. Cached per PATH. Raises RefsError when there is none."""
    env_path = os.environ.get("PATH", "") if env_path is None else env_path
    if env_path in _resolved:
        return _resolved[env_path]
    shim_dir = os.environ.get("PILEAD_SHIM_DIR") or ""
    seen = set()
    cands = [os.path.join(d, "git") for d in env_path.split(os.pathsep) if os.path.isabs(d)
             and not (shim_dir and os.path.realpath(d) == os.path.realpath(shim_dir))]
    for c in cands + list(FALLBACK_GITS):
        real = os.path.realpath(c)
        if real in seen:
            continue
        seen.add(real)
        if os.path.isfile(c) and os.access(c, os.X_OK) and not _is_shim(c) and _answers_as_git(c):
            _resolved[env_path] = c
            return c
    raise RefsError("git failed: no real git found on PATH")


def _run(worktree, *argv):
    """The real git in `worktree` → CompletedProcess. The program run is `git_path()` (passed as
    `executable`, so no PATH lookup happens); argv[0] stays the conventional `git`. The environment
    drops every GIT_* variable (a GIT_DIR/GIT_WORK_TREE inherited from the caller would point the
    snapshot at another repository) and pins LC_ALL=C."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    try:
        return subprocess.run(["git", "-C", str(worktree), *argv], executable=git_path(), capture_output=True,
                              text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise RefsError(f"git failed: {_first_line(str(e))}") from e


def _first_line(text):
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")[:200]


def _git(worktree, *argv, ok_codes=(0,)):
    """(returncode, stdout) of the real git in `worktree`; RefsError(`git failed: <first stderr
    line>`) on a code outside ok_codes."""
    r = _run(worktree, *argv)
    if r.returncode not in ok_codes:
        raise RefsError(f"git failed: {_first_line(r.stderr) or f'git {argv[0]} exited {r.returncode}'}")
    return r.returncode, r.stdout


# ── snapshot ──────────────────────────────────────────────────────────────────────────────────
def _worktrees(porcelain):
    out, cur = [], None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "head": None, "branch": None}
            out.append(cur)
        elif cur is not None and line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif cur is not None and line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
    return out


def snapshot(worktree):
    """The refs snapshot of `worktree` as a JSON-able dict. Raises NotARepo when the worktree is not
    in a git repository or does not exist (judged with the real git), RefsError when git cannot be run
    or fails."""
    if not worktree:
        raise RefsError("no worktree recorded")
    wt = Path(worktree)
    if not wt.is_dir():
        raise NotARepo(f"worktree {worktree} does not exist")
    r = _run(wt, "rev-parse", "--show-toplevel")
    if r.returncode != 0 and "not a git repository" in (r.stderr or ""):
        raise NotARepo(f"{worktree} is not in a git repository")
    _, top = _git(wt, "rev-parse", "--show-toplevel")
    _, common = _git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
    rc, head = _git(wt, "rev-parse", "-q", "--verify", "HEAD^{commit}", ok_codes=(0, 1))
    rc_b, branch = _git(wt, "symbolic-ref", "-q", "--short", "HEAD", ok_codes=(0, 1))
    _, refs_out = _git(wt, "for-each-ref", "--format=%(refname) %(objectname)")
    refs = {}
    for line in refs_out.splitlines():
        name, _, oid = line.rpartition(" ")
        if name:
            refs[name] = oid
    stash = refs.pop("refs/stash", None)
    _, wl = _git(wt, "worktree", "list", "--porcelain")
    return {"worktree": str(worktree), "toplevel": top.strip(),
            "common_dir": os.path.realpath(common.strip()), "taken": state.now_iso(),
            "head": head.strip() if rc == 0 and head.strip() else None,
            "branch": branch.strip() if rc_b == 0 and branch.strip() else None,
            "refs": refs, "stash": stash, "worktrees": _worktrees(wl)}


# ── compare ───────────────────────────────────────────────────────────────────────────────────
def _real(p):
    return os.path.realpath(p) if p else p


def compare(before, after):
    """The changes from snapshot `before` to snapshot `after`, in a stable order. Each is a dict with
    `kind` one of: head_moved {from, to} (plus `worktree` for another linked worktree's HEAD),
    branch_changed {from, to}, ref_added {ref, to}, ref_moved {ref, from, to}, ref_removed {ref, from},
    stash_changed {from, to}, worktree_added {path}, worktree_removed {path}. [] = nothing moved."""
    ch = []
    if before.get("head") != after.get("head"):
        ch.append({"kind": "head_moved", "from": before.get("head"), "to": after.get("head")})
    if before.get("branch") != after.get("branch"):
        ch.append({"kind": "branch_changed", "from": before.get("branch"), "to": after.get("branch")})
    b, a = before.get("refs") or {}, after.get("refs") or {}
    for ref in sorted(set(b) | set(a)):
        if ref not in b:
            ch.append({"kind": "ref_added", "ref": ref, "to": a[ref]})
        elif ref not in a:
            ch.append({"kind": "ref_removed", "ref": ref, "from": b[ref]})
        elif b[ref] != a[ref]:
            ch.append({"kind": "ref_moved", "ref": ref, "from": b[ref], "to": a[ref]})
    if before.get("stash") != after.get("stash"):
        ch.append({"kind": "stash_changed", "from": before.get("stash"), "to": after.get("stash")})
    own = {_real(before.get("toplevel")), _real(after.get("toplevel"))}
    bw = {_real(w["path"]): w for w in before.get("worktrees") or []}
    aw = {_real(w["path"]): w for w in after.get("worktrees") or []}
    for p in sorted(set(bw) | set(aw)):
        if p not in bw:
            ch.append({"kind": "worktree_added", "path": aw[p]["path"]})
        elif p not in aw:
            ch.append({"kind": "worktree_removed", "path": bw[p]["path"]})
        elif p not in own and bw[p].get("head") != aw[p].get("head"):
            # a commit made in ANOTHER linked worktree on a detached HEAD moves no ref at all
            ch.append({"kind": "head_moved", "worktree": aw[p]["path"],
                       "from": bw[p].get("head"), "to": aw[p].get("head")})
    return ch


def commits_between(worktree, from_, to, limit=MAX_COMMITS):
    """The commits reachable from `to` and not from `from_` (each a revision or a list of them; None
    or empty = nothing), newest first, as [{sha, author, date, subject}] — at most `limit`. A `from_`
    object git no longer has is dropped rather than failing the listing. Returns (commits, total)."""
    tips = [t for t in ([to] if isinstance(to, str) else list(to or [])) if t]
    if not tips:
        return [], 0
    base = [f for f in ([from_] if isinstance(from_, str) else list(from_ or [])) if f]
    base = [f for f in base if _git(worktree, "cat-file", "-e", f"{f}^{{commit}}", ok_codes=(0, 1, 128))[0] == 0]
    rng = [*tips, "--not", *base] if base else tips
    _, n = _git(worktree, "rev-list", "--count", *rng)
    _, out = _git(worktree, "log", f"-n{limit}", f"--format=%H{_SEP}%an <%ae>{_SEP}%aI{_SEP}%s", *rng)
    commits = []
    for line in out.splitlines():
        parts = line.split(_SEP, 3)
        if len(parts) == 4:
            commits.append(dict(zip(("sha", "author", "date", "subject"), parts)))
    return commits, int(n.strip() or 0)


def new_commits(worktree, before, after, changes):
    """The commits the changes made newly reachable: from every moved/added tip (HEAD, local refs,
    tags; never remote-tracking refs, whose new commits are someone else's, nor the stash's WIP
    commits) excluding everything reachable from the baseline. Returns (commits, total)."""
    tips = set()
    for c in changes:
        if c["kind"] in ("head_moved", "ref_added", "ref_moved") and c.get("to"):
            if c.get("ref", "").startswith("refs/remotes/"):
                continue
            tips.add(c["to"])
    if not tips:
        return [], 0
    base = {before.get("head")} | set((before.get("refs") or {}).values())
    base |= {w.get("head") for w in before.get("worktrees") or []}
    return commits_between(worktree, sorted(b for b in base if b), sorted(tips))


# ── stored baselines ──────────────────────────────────────────────────────────────────────────
def baseline_path(home, sid, n):
    return state.session_dir(Path(home), sid) / f"refs-{n:04d}.json"


def store(home, sid, n, snap):
    """Write packet n's baseline (tmp + rename). An empty directory in its place is removed first; a
    non-empty one is left alone and raises RefsError."""
    p = baseline_path(home, sid, n)
    if p.is_dir() and not p.is_symlink():
        try:
            p.rmdir()
        except OSError as e:
            raise RefsError(f"{p} is a directory and could not be replaced: {e.strerror}") from e
    state.atomic_write(p, json.dumps(snap, indent=2) + "\n")


_STR = (str,)
_OPT = (str, type(None))
# Every field `snapshot` writes, with the JSON types it writes (`refs` and `worktrees` are checked
# element by element in `_well_formed`). Extra keys (`accepted`, from `pilead refs accept`) are allowed.
SHAPE = {"worktree": _STR, "toplevel": _STR, "common_dir": _STR, "taken": _STR, "head": _OPT,
         "branch": _OPT, "refs": (dict,), "stash": _OPT, "worktrees": (list,)}
_WORKTREE_SHAPE = {"path": _STR, "head": _OPT, "branch": _OPT}


def _typed(d, shape):
    return isinstance(d, dict) and all(k in d and isinstance(d[k], t) for k, t in shape.items())


def _well_formed(d):
    """True when `d` has every field `snapshot` writes, each with the type `snapshot` writes."""
    return (_typed(d, SHAPE)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in d["refs"].items())
            and all(_typed(w, _WORKTREE_SHAPE) for w in d["worktrees"]))


def load(home, sid, n):
    """(baseline, None) for packet n, or (None, why): `no baseline stored` when there is no file,
    `baseline unreadable` when it cannot be read (a directory, no permission) or is not a snapshot
    (not JSON, or not the shape `snapshot` writes: a field missing or of another type — see SHAPE)."""
    p = baseline_path(home, sid, n)
    try:
        text = p.read_text()
    except FileNotFoundError:
        return None, "no baseline stored"
    except (OSError, UnicodeDecodeError):
        return None, "baseline unreadable"
    try:
        d = json.loads(text)
    except ValueError:
        return None, "baseline unreadable"
    if not _well_formed(d):
        return None, "baseline unreadable"
    return d, None


def take_and_store(home, sid, n, worktree):
    """Snapshot at spawn/send: store refs-NNNN.json and write `refs_snapshot`. Returns (snapshot, None);
    (None, None) for a worktree outside any git repository (nothing to detect, nothing said); or
    (None, reason) when git failed — nothing is stored then, and the caller prints the reason."""
    try:
        snap = snapshot(worktree)
    except NotARepo:
        return None, None
    except RefsError as e:
        return None, str(e)
    store(home, sid, n, snap)
    ledger.append(home, "refs_snapshot", session_id=sid, packet=n, head=snap["head"],
                  branch=snap["branch"], refs=len(snap["refs"]))
    return snap, None


def record_baseline(home, sid, n, worktree):
    """What `spawn` (packet 1) and `send` (packet n, relaunch or not) call before the packet can reach
    the executor. A worktree outside git gets no baseline, silently. A failure is one stderr line plus
    the `refs_snapshot_failed` ledger event, and later comparisons say `refs: not compared (no
    baseline stored)`. Never raises: a snapshot never stops a spawn or a send."""
    try:
        _, err = take_and_store(home, sid, n, worktree)
    except Exception as e:  # noqa: BLE001 — detection must never break delegation
        err = f"{type(e).__name__}: {e}"
    if err:
        print(f"pilead: refs snapshot for packet {n:04d} not taken: {err}", file=sys.stderr)
        ledger.append(home, "refs_snapshot_failed", session_id=sid, packet=n, reason=err)


# ── the check used by `check`, `verify` and the board ─────────────────────────────────────────
def _short(oid):
    return oid[:12] if oid else "(none)"


def describe(c):
    k = c["kind"]
    if k == "head_moved":
        where = f" in worktree {c['worktree']}" if c.get("worktree") else ""
        return f"HEAD moved{where}: {_short(c['from'])} -> {_short(c['to'])}"
    if k == "branch_changed":
        return f"branch changed: {c['from'] or '(detached)'} -> {c['to'] or '(detached)'}"
    if k == "ref_added":
        return f"ref added: {c['ref']} at {_short(c['to'])}"
    if k == "ref_moved":
        return f"ref moved: {c['ref']} {_short(c['from'])} -> {_short(c['to'])}"
    if k == "ref_removed":
        return f"ref removed: {c['ref']} (was {_short(c['from'])})"
    if k == "stash_changed":
        return f"stash changed: {_short(c['from'])} -> {_short(c['to'])}"
    if k == "worktree_added":
        return f"worktree added: {c['path']}"
    if k == "worktree_removed":
        return f"worktree removed: {c['path']}"
    return json.dumps(c)


def _repo_of(home, sid, meta):
    """The common git dir of a session's worktree: from its newest stored snapshot, else git."""
    sdir = state.session_dir(Path(home), sid)
    for p in sorted(sdir.glob("refs-*.json"), reverse=True):
        try:
            cd = json.loads(p.read_text()).get("common_dir")
            if cd and isinstance(cd, str):
                return cd
        except (OSError, ValueError, AttributeError):
            continue
    try:
        _, common = _git(meta.get("worktree") or "", "rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.realpath(common.strip())
    except RefsError:
        return None


def same_repo_sessions(home, sid, common_dir):
    """[(sid, status)] of every OTHER session whose worktree is in the same repository."""
    out = []
    sroot = Path(home) / "sessions"
    if not common_dir or not sroot.is_dir():
        return out
    for mp in sorted(sroot.glob("*/meta.json")):
        other = mp.parent.name  # a session's id is its directory's name, not the record's `sid` field
        if not state.valid_session_sid(other):
            continue  # a directory named otherwise holds no session
        try:
            meta = json.loads(mp.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            meta["sid"] = other
        if other == sid:
            continue
        if _repo_of(home, other, meta) == common_dir:
            out.append((other, state.read_status(Path(home), other, meta)))
    return out


def _log_move(home, sid, n, changes, records=None):
    """Append `refs_moved` unless the most recent `refs_moved` / `refs_accepted` event for this session
    and packet is a `refs_moved` with the same changes: one move is logged once, and a move seen after
    an accept is logged again. `records`: the ledger already read by the caller (every record, any
    session), so the command reads it once; None reads it here."""
    last = None
    recs = ledger.read(home, session_id=sid) if records is None else records
    for rec in recs:
        if rec.get("session_id") == sid and rec.get("packet") == n and rec.get("event") in ("refs_moved", "refs_accepted"):
            last = rec
    if last is not None and last.get("event") == "refs_moved" and last.get("changes") == changes:
        return
    ledger.append(home, "refs_moved", session_id=sid, packet=n, changes=changes)


def check(home, sid, n, worktree, log=True, records=None):
    """Compare packet n's stored baseline with a fresh snapshot. Returns a dict:
    {state, packet, why, changes, commits, total_commits, others, note} where state is one of
    - "not-a-repo": the worktree is not in a git repository (or does not exist) and no baseline was
      stored for the packet — nothing to detect;
    - "not-compared": it is, but git fails now or the baseline is missing / unreadable (`why` says);
      or a baseline was stored (readable or not) and the worktree is no longer a repository or is
      gone (`repository missing`);
    - "unchanged" / "moved": compared. A move writes `refs_moved` (when `log`, once per distinct move).
    `records`: ledger records the caller already read (an iterable of every record, read on demand);
    None reads the ledger here, as before. Never raises RefsError."""
    res = {"state": None, "packet": n, "why": None, "changes": [], "commits": [], "total_commits": 0,
           "others": [], "note": None}
    try:
        after = snapshot(worktree)
    except NotARepo:
        if load(home, sid, n)[1] == "no baseline stored":
            res["state"] = "not-a-repo"
        else:  # there was a repository to compare: its disappearance is not "nothing to detect"
            res.update(state="not-compared", why="repository missing")
        return res
    except RefsError as e:
        res.update(state="not-compared", why=str(e))
        return res
    before, why = load(home, sid, n)
    if before is None:
        res.update(state="not-compared", why=why)
        return res
    changes = compare(before, after)
    if not changes:
        res["state"] = "unchanged"
        return res
    res.update(state="moved", changes=changes)
    try:
        res["commits"], res["total_commits"] = new_commits(worktree, before, after, changes)
    except RefsError as e:
        res["note"] = f"new commits not listed: {e}"
    res["others"] = same_repo_sessions(home, sid, after.get("common_dir"))
    if log:
        _log_move(home, sid, n, changes, records)
    return res


def one_line(res):
    """The single line `check --refs-only` prints and the lead's wake message carries."""
    n = res["packet"]
    st = res["state"]
    if st == "unchanged":
        return f"refs: unchanged since packet {n:04d}"
    if st == "moved":
        return f"REFS MOVED: {len(res['changes'])} change(s) since packet {n:04d}"
    if st == "not-a-repo":
        return "refs: not a git repository"
    return f"refs: not compared ({res['why']})"


def block(res, sid):
    """The `REFS MOVED` block (lines), for a result whose state is "moved"."""
    n = res["packet"]
    L = [f"REFS MOVED since packet {n:04d} of {sid} — a commit or ref change happened outside the "
         "lead's hand (or the lead's own, not yet accepted)"]
    for c in res["changes"]:
        L.append(f"  - {describe(c)}")
    if res["commits"]:
        L.append(f"  new commits ({res['total_commits']}):")
        for c in res["commits"]:
            L.append(f"    {c['sha'][:12]} {c['date']} {c['author']}  {c['subject']}")
        if res["total_commits"] > len(res["commits"]):
            L.append(f"    … {res['total_commits'] - len(res['commits'])} more")
    elif res.get("note"):
        L.append(f"  ({res['note']})")
    if res["others"]:
        L.append("  other sessions in this repository (their work moves these refs too): "
                 + ", ".join(f"{s} ({st})" for s, st in res["others"]))
    else:
        L.append("  no other pi-lead session works in this repository")
    L.append(f"  Stop: do not commit; take this to the user. After a commit of your own: "
             f"pilead refs accept {sid} --reason \"...\"")
    return L

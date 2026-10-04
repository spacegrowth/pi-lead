"""pilead verify | diff | board — the vendored relay report checker / diff page / board, over pi-lead's
state dir (sessions/<sid>/{meta.json,status,packet-NNNN.md,report-NNNN.md})."""
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import config, ledger, ownership, refs, seen, shim, state, status as status_mod, usage
from .state import PileadError

sys.path.insert(0, str(state.ROOT / "vendor" / "relay"))
import board_render  # noqa: E402
import diff_render  # noqa: E402
import platform_cmds  # noqa: E402
import report_verify  # noqa: E402

_REPORT_RE = re.compile(r"^report-(\d+)\.md$")


def _git_lines(worktree, *argv):
    """`git -C worktree <argv>` as non-empty lines; [] on any failure (a missing worktree degrades the
    verify to fewer facts, it never crashes it)."""
    try:
        r = subprocess.run(["git", "-C", worktree, *argv], capture_output=True, text=True)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
    except OSError:
        return []


RERUN_TIMEOUT = 600  # seconds per declared command


def _run_group(argv, cwd, timeout, env=None, executable=None):
    """Run `argv` (no shell) as the leader of a new process group; on timeout SIGKILL the whole
    group, then reap. Returns (returncode, stdout+stderr, timed_out)."""
    p = subprocess.Popen(argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True, env=env,
                         executable=executable)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, (out or "") + (err or ""), False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:  # reap; a descendant that escaped the group (its own setsid) may hold the pipes open
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            out, err = "", ""
        return None, (out or "") + (err or ""), True


# The argument lists the three exact non-pytest commands are started from — written here, never taken
# from the report (report_verify.RERUN_EXACT names the same three strings).
_EXACT_ARGV = {"npm test": ["npm", "test"], "npm run check": ["npm", "run", "check"],
               "node --test": ["node", "--test"]}
_SESSION_VARS = ("PI_LEAD_ROLE", "PI_LEAD_SID", "PI_LEAD_LEAD", "PI_LEAD_INBOX", "PI_SESSION_ID")
# shim.GIT_WORD is POSIX ERE, matched per line by grep -E; the same pattern for Python's re.
_GIT_WORD_RE = re.compile(shim.GIT_WORD.replace("[:space:]", r"\s"), re.MULTILINE)


def _rerun_env(shim_dir):
    """The re-run's environment: the caller's, with what an executor's launch line clears cleared (every
    shim.CFG_VARS and shim.GUARD_VARS name; a shim.CMD_VARS name whose value calls git), the git shim
    first on PATH, no pi-lead / pi session identity, and no colour, prompts or npm chatter."""
    env = dict(os.environ)
    for v in shim.CFG_VARS + shim.GUARD_VARS:
        env.pop(v, None)
    for v in shim.CMD_VARS:
        if v in env and _GIT_WORD_RE.search(env[v]):
            del env[v]
    for v in _SESSION_VARS:
        env.pop(v, None)
    env["PILEAD_SHIM_DIR"] = str(shim_dir)
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    env.update(NO_COLOR="1", CI="1", npm_config_update_notifier="false", npm_config_fund="false",
               npm_config_audit="false")
    return env


def _inside(path, *dirs):
    return any(path == d or d in path.parents for d in dirs)


def _program(name, env, worktree, shim_dir):
    """The absolute path of `name` on the re-run's PATH, or None when it is missing, not absolute, or
    inside the worktree or the shim directory (as found, or after symlinks are resolved)."""
    found = shutil.which(name, path=env.get("PATH", ""))
    if not found or not os.path.isabs(found):
        return None
    fences = (Path(worktree).resolve(), Path(shim_dir).resolve(), Path(os.path.abspath(worktree)),
              Path(os.path.abspath(shim_dir)))
    if _inside(Path(os.path.abspath(found)), *fences) or _inside(Path(found).resolve(), *fences):
        return None
    return found


def _has_package_json(worktree):
    try:
        return stat.S_ISREG(os.lstat(os.path.join(worktree, "package.json")).st_mode)
    except OSError:
        return False


def _row_declared(runner, declared, has_pytest_entry):
    if runner == "pytest":
        return declared.get("pytest")
    if runner == "node":
        n = declared.get("node")
        return declared.get("pytest") if n is None and not has_pytest_entry else n
    return None


def _rerun_declared(worktree, commands, declared, timeout=None, *, home, sid):
    """Re-run the report's declared test commands in its worktree and read back each run's counts.
    Only reached under --rerun. `commands` is already allowlisted by report_verify.rerunnable
    (re-checked here anyway — this is the one place report text becomes a process), and each runs
    WITHOUT a shell: argv only, stdin closed, in its own session (process group), `timeout`
    seconds, then the WHOLE group is killed — a suite's own children (xdist workers, servers) too.
    Every run has the git shim first on PATH (`<session dir>/shim`, written once, before the first
    command that starts, guarding `worktree`'s repository) and an executor's cleared git environment. `declared` is
    report_verify.declared_for(text). Returns (rows, refused_here): one row per runner_counts entry,
    and [(cmd, reason)] for commands refused for a reason only the worktree shows."""
    timeout = RERUN_TIMEOUT if timeout is None else timeout
    shim_dir = state.session_dir(home, sid) / "shim"
    env = _rerun_env(shim_dir)
    results, refused_here, shim_written = [], [], False
    for cmd in commands:
        runner = report_verify.rerun_runner(cmd)
        base = {"cmd": cmd, "passed": None, "failed": None, "returncode": None, "timed_out": False,
                "error": None, "timeout": timeout, "runner": runner,
                "declared": _row_declared(runner, declared, False)}
        if runner is None:
            results.append(dict(base, error="refused by the allowlist"))
            continue
        norm = " ".join(cmd.split())
        if runner == "npm" and not _has_package_json(worktree):
            refused_here.append((cmd, "no package.json in the worktree"))
            continue
        executable = None
        if runner == "pytest":
            argv = shlex.split(cmd)
        else:
            argv = list(_EXACT_ARGV[norm])
            executable = _program(argv[0], env, worktree, shim_dir)
            if executable is None:
                results.append(dict(base, error=f"{argv[0]} was not found outside the worktree"))
                continue
        print(f"pilead verify: re-running `{cmd}` (timeout {timeout}s)…", file=sys.stderr, flush=True)
        try:
            if not shim_written:
                shim.write_git_shim(shim_dir.parent, guarded=worktree)  # guards the verified worktree
                shim_written = True
            rc, out, timed_out = _run_group(argv, worktree, timeout, env=env, executable=executable)
        except (OSError, ValueError) as e:
            results.append(dict(base, error=f"{type(e).__name__}: {e}"))
            continue
        if timed_out:
            results.append(dict(base, timed_out=True))
            continue
        entries = report_verify.runner_counts(runner, out)
        has_py = any(name == "pytest" for name, _, _ in entries)
        for name, passed, failed in entries:
            results.append(dict(base, runner=name, passed=passed, failed=failed, returncode=rc,
                                declared=_row_declared(name, declared, has_py)))
    return results, refused_here


def _packet_n(home, sid, meta, wanted):
    if wanted:
        return wanted
    reps = [int(m.group(1)) for p in state.session_dir(home, sid).glob("report-*.md")
            if (m := _REPORT_RE.match(p.name))]
    return max(reps) if reps else int(meta.get("packets") or 1)


def _out(lines):
    for text, _styles in lines:
        print(text)


def cmd_verify(a):
    home = state.home_dir(a.home)
    meta = state.load_meta(home, a.sid)
    n = _packet_n(home, a.sid, meta, a.packet)
    rp = state.report_path(home, a.sid, n)
    if not rp.is_file():
        raise PileadError(f"no report yet for {a.sid} packet {n:04d} — nothing to verify")
    worktree = meta.get("worktree")
    if not worktree:
        raise PileadError(f"session {a.sid} has no recorded worktree")
    text = rp.read_text()
    reality = {
        # --no-renames: a rename lists its old path too, so moving a file out of a sign-off path is still seen
        "staged": _git_lines(worktree, "diff", "--cached", "--name-only", "--no-renames"),
        "modified": _git_lines(worktree, "diff", "--name-only"),
        "untracked": _git_lines(worktree, "ls-files", "--others", "--exclude-standard"),
        "repo_entries": set(_git_lines(worktree, "ls-tree", "--name-only", "HEAD")),
        "repo_files": set(_git_lines(worktree, "ls-files")),
        "commits_since": 0, "rerun": None,
    }
    if a.rerun:
        reality["rerun"], reality["rerun_refused_here"] = _rerun_declared(
            worktree, report_verify.declared_commands(text), report_verify.declared_for(text),
            home=home, sid=a.sid)
    result = report_verify.verify(text, reality)
    refs_state = _apply_refs(home, a.sid, n, worktree, result)
    # A review file written by `pilead review` (a "measured review"): None for hand-written findings,
    # which are handled exactly as before. Read as data — never run, never shown as an instruction.
    measured = _read_measured(a.findings)
    if measured is not None and not a.for_autocommit:
        print(_review_line(measured, worktree))
    _out(report_verify.render(result, a.sid, n))
    ledger.append(home, "report_verify", session_id=a.sid, packet=n, verdict=result["verdict"],
                  rerun=bool(a.rerun), mismatches=sum(1 for f in result["findings"] if f["level"] == "mismatch"),
                  caller=seen.caller(home, meta))

    if a.diff_reviewed and a.findings:
        fp = Path(a.findings).expanduser()
        if not fp.is_file():
            raise PileadError(f"--findings file {a.findings} doesn't exist")
        rev = state.session_dir(home, a.sid) / f"review-{n:04d}.md"
        if not (measured is not None and _same_file(fp, rev)):  # a measured review is never copied onto itself
            state.atomic_write(rev, fp.read_text())
        ledger.append(home, "report_reviewed", session_id=a.sid, packet=n, findings=str(rev),
                      caller=seen.caller(home, meta))
    elif a.diff_reviewed:
        ledger.append(home, "report_reviewed", session_id=a.sid, packet=n, findings="inline",
                      caller=seen.caller(home, meta))
    parked = park_after_review(home, a.sid, n) if a.diff_reviewed else None

    if not a.for_autocommit:
        _print_parked(parked)
        return report_verify.EXIT_CODES[result["verdict"]]
    staged_diff = subprocess.run(["git", "-C", worktree, "diff", "--cached"],
                                 capture_output=True, text=True).stdout
    clr = report_verify.clearance(result, staged_diff, in_plan=a.in_plan, diff_reviewed=a.diff_reviewed,
                                  signoff_paths=configured_signoff_paths(home))
    refs_block = _REFS_BLOCKS.get(refs_state)
    if refs_block:  # outranks every other reason: the headline names it
        slug, detail = refs_block
        clr["conditions"].insert(0, {"n": 0, "name": f"refs compared and unchanged since packet {n:04d}'s baseline",
                                     "checkable": True, "ok": False, "slug": slug, "detail": detail})
        clr.update(cleared=False, reason=slug)
    if measured is not None:
        _apply_review(clr, measured, str(Path(a.findings).expanduser()), a.sid, n, worktree)
    _out(report_verify.render_clearance(clr))
    ledger.append(home, "auto_commit", session_id=a.sid, packet=n, cleared=clr["cleared"], reason=clr["reason"])
    _print_parked(parked)
    return 0 if clr["cleared"] else (report_verify.EXIT_CODES[result["verdict"]] or 1)


def configured_signoff_paths(home):
    """Config `signoff_paths` for condition 4 (claude-relay's `_configured_signoff_paths`): the list's
    non-empty strings, in order; [] for any value that is not a list. A configured marker only adds to the
    built-in ones (report_verify.signoff_hits), so a bad value can never switch a built-in off."""
    raw = config.load(home).get("signoff_paths")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and p]


def _print_parked(line):
    if line:
        print(line)


def park_after_review(home, sid, n):
    """claude-relay 0.5.2's `_park_after_review` (row 95): once packet `n`'s report has been REVIEWED
    (`report_reviewed` just appended), the executor has nothing left to do, so it is parked at once — its
    staged work stays in the worktree for the lead to commit, and a fix-list `pilead send` resumes the
    closed session with its conversation. Skipped, writing nothing, when the session is pinned, its stored
    status is `closed` or `dead`, something superseded it, its current packet is not `n`, or its queue has
    items or cannot be read. The park is `pilead close`'s own function (its output captured), then
    `auto_closed` = `reviewed` in meta.json and the sweep's `auto_closed` event with trigger `verify`.
    Returns the line to print after verify's own output, or None. Best effort: any failure is ledgered as
    `auto_close_error` and never changes verify's exit code or its other output."""
    try:
        from . import lifecycle, queue as queue_mod
        meta = state.load_meta(home, sid)
        if not isinstance(meta, dict) or meta.get("keep") or meta.get("superseded_by"):
            return None
        if state.read_status(home, sid, meta) in status_mod.TERMINAL:
            return None
        if int(meta.get("packets") or 1) != int(n):
            return None
        if queue_mod.count(home, sid) != 0:  # a packet is waiting for it, or the queue cannot be read
            return None
        sink = []
        lifecycle.close_executor(home, sid, meta, reason="auto-close (reviewed)", out=sink.append, err=sink.append)
        m = state.load_meta(home, sid)
        m["auto_closed"] = "reviewed"
        state.save_meta(home, m)
        ledger.append(home, "auto_closed", session_id=sid, action="close", reason="reviewed", trigger="verify")
        return (f"  parked {sid} (reviewed) — its staged work stays in the worktree; pilead send {sid} <packet> "
                "resumes it")
    except Exception as e:  # noqa: BLE001 — a park never fails the verify that asked for it
        try:
            msg = " ".join(str(e).split()) or type(e).__name__
            ledger.append(home, "auto_close_error", session_id=sid, error=msg[:200], trigger="verify")
        except Exception:  # noqa: BLE001
            pass
        return None


# ── a measured review given to --findings ──────────────────────────────────────────────────────
REVIEWED_NAME = "the diff was reviewed by pilead review, the review is of the staged tree, and the lead has read it"
_SHOWN = 80  # characters of a value read from a review file that a detail shows


def _read_measured(findings):
    """reviewer.read_review of the --findings file, or None (no --findings, or hand-written findings)."""
    if not findings:
        return None
    from . import reviewer  # here: reviewer imports this module
    return reviewer.read_review(Path(findings).expanduser())


def _same_file(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):
        return False


def _staged_tree(worktree):
    from . import reviewer
    return reviewer.staged_tree(worktree)


def _review_line(rv, worktree):
    """The one line `verify` prints above its block for a measured review given without --for-autocommit."""
    if not rv.get("readable"):
        return f"review: could not be read ({rv.get('why')})"
    tree = rv.get("tree_end")
    now = _staged_tree(worktree)
    shown = tree[:12] if tree else "not measured"
    where = ("the staged tree could not be read" if now is None else
             "the staged tree" if tree == now else f"the staged tree is now {now[:12]}")
    return f"review: {rv['outcome'][:_SHOWN]}, of tree {shown} — {where}"


def _review_failure(rv, file, sid, n, worktree):
    """(reason, detail) of the first of the seven checks a measured review fails, or (None, detail) when
    it passes all seven; `detail` is condition 5's detail either way."""
    if not rv.get("readable"):
        return "review-unreadable", f"{file}: not readable as a review ({rv.get('why')})"
    if rv["sid"] != sid or rv["packet"] != n:
        return ("review-is-of-another-packet",
                f"{file}: a review of {rv['sid'][:_SHOWN]} packet {rv['packet']:04d}, not of {sid} packet {n:04d}")
    if not rv["valid"]:
        if rv["outcome"] == "VALID":  # read_review: only a Hash line that says the trees differ does this
            return "review-not-valid", f"{file}: the review says VALID, but its Hash line says the trees differ"
        return "review-not-valid", f"{file}: the review is {rv['outcome'][:_SHOWN]}, not VALID"
    now = _staged_tree(worktree)
    if now is None:
        return "review-tree-not-compared", f"{file}: the staged tree could not be read — the review was not compared"
    if rv["tree_end"] != now:
        shown = rv["tree_end"][:12] if rv["tree_end"] else "not measured"
        return "review-is-of-another-tree", f"{file}: the review is of tree {shown}, the staged tree is {now[:12]}"
    open_ = [f for f in rv["findings"] if f["level"] in ("blocker", "should-fix")]
    if open_:
        b = sum(1 for f in open_ if f["level"] == "blocker")
        return "review-has-open-findings", f"{file}: {b} blocker(s), {len(open_) - b} should-fix"
    if rv["recommendation"] != "commit":
        return "review-does-not-recommend-commit", f"{file}: the review recommends {rv['recommendation']}"
    notes = sum(1 for f in rv["findings"] if f["level"] == "note")
    return None, f"{file}: VALID, tree {now[:12]}, {notes} note(s)"


def _apply_review(clr, rv, file, sid, n, worktree):
    """Condition 5 for a measured review. Only ever makes the result stricter: a failed check sets
    condition 5 not ok (and the reason, unless an earlier condition already gave one); a review that
    passes all seven renames condition 5, whose ok stays the lead's own --diff-reviewed."""
    c5 = next(c for c in clr["conditions"] if c["n"] == 5)
    reason, detail = _review_failure(rv, file, sid, n, worktree)
    c5["detail"] = detail
    if reason is None:
        c5["name"] = REVIEWED_NAME
        return
    c5.update(ok=False, slug=reason)
    earlier = next((c for c in clr["conditions"] if c["n"] < 5 and not c["ok"]), None)
    clr["cleared"] = False
    if earlier is None:
        clr["reason"] = reason


# refs.check state → (NOT-CLEARED slug, condition detail) for --for-autocommit
_REFS_BLOCKS = {"moved": ("refs-moved", "REFS MOVED — see the block at the top"),
                "not-compared": ("refs-not-compared", "refs were not compared — see the line at the top")}


def _apply_refs(home, sid, n, worktree, result):
    """Compare packet n's refs baseline with the repository now (lib/pilead/refs.py), print the outcome
    ABOVE the verify block, and make the verdict stricter, never better:
    - moved: a `refs-moved` mismatch finding; the verdict becomes MISMATCH (MALFORMED stays MALFORMED —
      it is not a report at all);
    - not-compared (a repository whose baseline is missing / unreadable, or git fails now): a
      `refs-not-compared` inconclusive finding; COUNTS-MATCH becomes INCONCLUSIVE, anything else stays;
    - unchanged / not-a-repo: the line only. Returns the refs state."""
    res = refs.check(home, sid, n, worktree)
    st = res["state"]
    if st == "moved":
        print("\n".join(refs.block(res, sid)))
        result["findings"].append({"level": "mismatch", "code": "refs-moved",
                                   "text": f"refs moved since packet {n:04d}: {len(res['changes'])} change(s) "
                                           "— a commit or ref change the report does not account for"})
        if result["verdict"] != report_verify.MALFORMED:
            result["verdict"] = report_verify.MISMATCH
        return st
    print(refs.one_line(res))
    if st == "not-compared":
        result["findings"].append({"level": "inconclusive", "code": "refs-not-compared",
                                   "text": f"refs for packet {n:04d} were not compared: {res['why']}"})
        if result["verdict"] == report_verify.COUNTS_MATCH:
            result["verdict"] = report_verify.INCONCLUSIVE
    return st


def cmd_diff(a):
    home = state.home_dir(a.home)
    meta = state.load_meta(home, a.sid)
    worktree = meta.get("worktree")
    if not worktree:
        raise PileadError(f"session {a.sid} has no recorded worktree")
    n = _packet_n(home, a.sid, meta, None)
    rp = state.report_path(home, a.sid, n)
    report_text = rp.read_text() if rp.is_file() else None
    staged = _git_lines(worktree, "diff", "--staged", "--name-only")

    scope_note, scope_files = "", None
    if not a.all:
        if report_text is not None:
            mentioned = set(diff_render.parse_report_mentions(report_text))
            scope_files = [f for f in staged if f in mentioned]
        if not scope_files:
            scope_note = "unfiltered — report not found/parsable"
    cmd = ["git", "-C", worktree, "diff", "--staged"] + (["--", *scope_files] if scope_files else [])
    diff_text = subprocess.run(cmd, capture_output=True, text=True).stdout

    page_meta = {"session_id": a.sid, "packet": n, "scope_note": scope_note}
    if report_text:
        tldr = report_verify.parse_tldr(report_text)
        if tldr.get("status"):
            page_meta["status"] = tldr["status"]
            if tldr.get("outcome") and not any(p.startswith("first line") for p in tldr.get("problems", [])):
                page_meta["gist"] = tldr["outcome"]
            rf = tldr.get("risk_flags")
            if rf and not report_verify.is_none_value(rf):
                page_meta["risk"] = rf
    out = state.session_dir(home, a.sid) / f"diff-{n:04d}.html"
    state.atomic_write(out, diff_render.generate_page(diff_text, page_meta))
    print(out)
    print(out.resolve().as_uri())
    if a.open:
        _open_or_print(out)


def _open_or_print(path):
    """`--open` (claude-relay 0.5.7 `_open_or_print`): hand `path` to the platform's opener
    (platform_cmds.open_path: `open` on macOS, `xdg-open` on Linux). No opener (a headless Linux box) or it
    failed → one line on stdout naming the file:// address instead; never an error, the page is already
    written."""
    if not platform_cmds.open_path(str(path)):
        print(f"(no opener available — open it yourself: {Path(path).resolve().as_uri()})")


def _read_capped(path, cap):
    try:
        text = path.read_text()
    except OSError:
        return None
    return {"text": text[:cap], "truncated": len(text) > cap, "path": str(path)}


LEDGER_TAIL = 40
LEDGER_TAIL_TEXT = 160


def _read_ledger(home):
    """(records, readable): every ledger record, readable False when the ledger exists and cannot be read
    (ledger.read gives [] for both; the board must tell them apart). A missing ledger is ([], True)."""
    try:
        with open(ledger.path(home), "rb"):
            pass
    except FileNotFoundError:
        return [], True
    except Exception:  # noqa: BLE001
        return [], False
    return ledger.read(home), True


def _ledger_tail(records):
    out = []
    for r in records[-LEDGER_TAIL:]:
        rest = {k: v for k, v in r.items() if k not in ("ts", "event")}
        try:
            fields = json.dumps(rest, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            fields = "{}"
        out.append(f"{r.get('ts') or '-'} {r.get('event') or '-'} {fields[:LEDGER_TAIL_TEXT]}")
    return out


def _packets(home, sid, cur):
    sdir = state.session_dir(home, sid)
    packets = []
    for pf in sorted(sdir.glob("packet-*.md")):
        n = pf.stem.split("-")[1]
        rf = sdir / f"report-{n}.md"
        tl = None
        if rf.is_file():
            t = report_verify.parse_tldr(rf.read_text())
            tl = {"outcome": t.get("outcome"), "status": t.get("status"),
                  "risk": t.get("risk_flags"), "unverified": t.get("unverified")}
        dp = sdir / f"diff-{n}.html"
        first = next((ln.strip() for ln in pf.read_text().splitlines() if ln.strip()), "")
        packets.append({"n": n, "gist": first[:200], "tldr": tl, "packet_path": str(pf),
                        "packet_body": _read_capped(pf, 6000),
                        "report_path": str(rf) if rf.is_file() else None,
                        "report_body": _read_capped(rf, 8000) if rf.is_file() else None,
                        "diff_path": str(dp) if dp.is_file() else None,
                        "diff_url": dp.resolve().as_uri() if dp.is_file() else None,
                        "current": int(n) == cur})
    return packets


def _refs_row(home, sid, cur, meta, status, warnings):
    """(refs_moved, refs_not_compared), each warning appended as at HEAD. A closed session is not compared
    (its tab is gone; the lead has usually committed its work)."""
    if status == "closed":
        return False, False
    try:
        rr = refs.check(home, sid, cur, meta.get("worktree") or "", log=False)
    except Exception as e:  # noqa: BLE001 — one bad session never takes the board down
        rr = {"state": "not-compared", "why": f"{type(e).__name__}: {' '.join(str(e).split())}"}
    if rr["state"] == "moved":
        warnings.append({"level": "bad", "text": f"REFS MOVED in {sid} since packet {cur:04d}: "
                         f"{len(rr['changes'])} change(s) — run `pilead check {sid}`; do not commit, "
                         "take it to the user"})
        return True, False
    if rr["state"] == "not-compared":
        warnings.append({"level": "warn", "text": f"refs of {sid} NOT COMPARED for packet {cur:04d} "
                         f"({rr['why']}) — a commit would not be detected; `pilead refs accept {sid}` "
                         "stores a baseline"})
        return False, True
    return False, False


def _mb_text(mb):
    return f"{mb:.1f}" if mb is not None else "-"


def _lead_row(home, ld, now):
    from . import posture, tabs, wakehealth
    from .views import relative_age
    if ld["broken"]:
        return {"session_id": ld["sid"], "broken": True, "liveness": "broken"}
    rec, u, mb = ld["rec"], ld["usage"], ld["mb"]
    wake, detail = wakehealth.state(home, ld["sid"])
    tier, _src, readable = posture.tier_state(rec)
    known = u is not None or mb is not None
    last = ld["last_active"]
    return {"session_id": ld["sid"], "project": rec.get("project"), "label": rec.get("label"),
            "model": ld["model"], "color": rec.get("color") if tabs.usable_color(rec.get("color")) else None,
            "liveness": ld["live"], "wake": wake, "wake_detail": detail,
            "waiting": wakehealth.waiting(home, ld["sid"]),
            "auto": posture.autonomous_state(rec)[0] if readable else None, "tier": tier if readable else None,
            "last_active": status_mod.iso_utc(last) if last is not None else None,
            "last_active_age": relative_age(now - last) if last is not None else "-",
            "ctx": usage.ctx_cell(u, ld["window"]), "mb": _mb_text(mb),
            "heavy": ld["heavy"] if known else None,
            "heavy_reading": (f"{usage.heavy_reading_text(u, mb)}, lead line {usage.human_tokens(ld['threshold'])}"
                              if known else None)}


def _unannounced(s, owner_state, delivered):
    """True: reported, the current packet's report exists, the owner is `owned`, and no `wake_delivered`
    names this executor and packet. None when the owner state is unknown or the ledger could not be read
    (`delivered` None); false otherwise."""
    if s["status"] in status_mod.TERMINAL or s["status"] != "reported":
        return False
    if owner_state == "unknown" or delivered is None:
        return None
    return bool(s["reported"] and owner_state == "owned" and (s["sid"], s["packet"]) not in delivered)


def _exec_row(home, s, lead_project, delivered):
    from .verbs import list as list_verb
    meta, st, u, mb = s["meta"], s["status"], s["usage"], s["mb"]
    sid, cur = s["sid"], s["packet"]
    own = ownership.owner(home, meta)
    shown = "paused (limit)" if st == "paused" else status_mod.display(meta, st)
    if st in status_mod.TERMINAL and meta.get("auto_closed"):
        shown += " (auto)"
    row_warnings = []
    refs_moved, refs_not_compared = _refs_row(home, sid, cur, meta, st, row_warnings)
    ctx = usage.ctx_cell(u, s["window"])
    rate = (u or {}).get("cache_hit_rate")
    heavy = bool(s["heavy"])
    approaching = bool(s["approaching"]) and not heavy
    owner_lead = own["lead"]
    return {"session_id": sid, "owner_lead": owner_lead,
            "owner_project": lead_project.get(owner_lead) if owner_lead is not None else None,
            "status": st, "rendered_status": shown, "topic": meta.get("topic"), "model": meta.get("model"),
            "scope": meta.get("scope"), "effort": meta.get("effort"),
            "worktree": meta.get("worktree"), "pkt": f"{cur:04d}", "packets": _packets(home, sid, cur),
            "refs_moved": refs_moved, "refs_not_compared": refs_not_compared,
            "orphan": list_verb.orphan_value(home, meta, st),
            "tokens": usage.usage_cell(u), "hit_rate": round(rate * 100) if rate is not None else None,
            "ctx_cell": ctx, "ctx_contradiction": ctx.endswith(" !"), "mb": _mb_text(mb),
            "usage": usage.public(u), "heavy": heavy, "approaching": approaching,
            "heavy_reading": usage.heavy_reading_text(u, mb) if heavy else None,
            "approaching_reading": usage.heavy_reading_text(u, mb) if approaching else None,
            "keep": meta.get("keep") is True,
            "queued": len(s["queue_items"]) if s.get("queue_items") is not None else None,
            "auto_closed": meta.get("auto_closed") or None,
            "unannounced": _unannounced(s, own["state"], delivered),
            "reported": bool(s["reported"]), "_owner_state": own["state"], "_warnings": row_warnings}


def _names(items):
    return ", ".join(items)


def board_data(home, lead=None, sweep=True, write=True, parked=None):
    """What the board shows, read through the functions `pilead list` reads through (verbs/list.py
    `read_leads` / `read_sessions`): one lead row per lead record, one executor row per session, the page's
    warnings, the version and the ledger's tail. `lead`: only that lead's executors and the unowned ones
    (refused with PileadError when it is not a usable lead id). With `sweep` and `write` and a calling lead
    (lifecycle.caller), that lead's finished executors are parked first (autoclose.sweep) — `parked`, a
    list, gets each (sid, action, reason). `write=False` writes nothing under home: no usage cache, no
    stored status, no sweep. A row that cannot be built is a broken row; the rest is built."""
    from . import autoclose, lifecycle
    from .verbs import list as list_verb
    home = Path(home)
    if lead is not None:
        state.check_lead_sid(lead)
    cfg = config.load(home)
    warnings, sweep_warnings, acted = [], [], []
    caller = lifecycle.caller(home) if (sweep and write) else None
    if caller is not None:
        acted = autoclose.sweep(home, "board", lead=caller)
        for sid, action, reason in acted:
            sweep_warnings.append({"level": "info", "text": f"{'auto-retired' if action == 'retire' else 'auto-closed'} "
                                   f"{sid} ({reason}) just now"})
        if parked is not None:
            parked.extend(acted)
    records, ledger_ok = _read_ledger(home)
    now = time.time()

    try:
        lds = list_verb.read_leads(home, cfg, write_cache=write)
    except Exception:  # noqa: BLE001 — every record is then one that could not be read
        lds = [{"sid": p.stem, "broken": True} for p in list_verb.lead_files(home)]
    leads = []
    for ld in lds:
        try:
            leads.append(_lead_row(home, ld, now))
        except Exception:  # noqa: BLE001
            leads.append({"session_id": ld.get("sid"), "broken": True, "liveness": "broken"})
    lead_project = {r["session_id"]: r.get("project") for r in leads if not r.get("broken")}

    try:
        sessions = list_verb.read_sessions(home, cfg, write_cache=write, records=records, store=write)
    except Exception:  # noqa: BLE001
        sd = home / "sessions"
        sessions = [{"sid": mp.parent.name, "broken": True}
                    for mp in (sorted(sd.glob("*/meta.json")) if sd.is_dir() else [])]
    delivered = ({(r.get("executor"), r.get("packet")) for r in records if r.get("event") == "wake_delivered"}
                 if ledger_ok else None)
    execs, queue_bad = [], []
    for s in sessions:
        if s.get("broken"):
            execs.append({"session_id": s["sid"], "broken": True})
            continue
        try:
            row = _exec_row(home, s, lead_project, delivered)
        except Exception:  # noqa: BLE001
            execs.append({"session_id": s["sid"], "broken": True})
            continue
        if s.get("queue_unreadable_path") is not None:
            queue_bad.append(s["sid"])
        execs.append(row)
    if lead is not None:
        execs = [e for e in execs if e.get("broken") or e["_owner_state"] == "unowned" or e["owner_lead"] == lead]
        queue_bad = [sid for sid in queue_bad if any(e["session_id"] == sid for e in execs)]
    for e in execs:  # the refs warnings of the rows shown, in row order
        e.pop("_owner_state", None)
        warnings.extend(e.pop("_warnings", ()))

    ok = [e for e in execs if not e.get("broken")]
    live = [e for e in ok if e["status"] not in status_mod.TERMINAL]
    orphans = [e["session_id"] for e in ok if e.get("orphan")]
    if orphans:
        warnings.append({"level": "bad", "text": f"⚠ {len(orphans)} executor(s) owned by a lead that is no longer "
                         f"registered: {', '.join(orphans)} — their reports wake nobody until adopted "
                         "(pilead adopt <sid>, or a send, resume or restart from a lead)"})
    unann = [e["session_id"] for e in ok if e.get("unannounced")]
    if unann:
        warnings.append({"level": "warn", "text": f"{len(unann)} report(s) not delivered to their lead yet: "
                         f"{_names(unann)} — pilead check <sid>"})
    heavy = [e["session_id"] for e in live if e["heavy"]]
    if heavy:
        warnings.append({"level": "info", "text": f"heavy: {_names(heavy)} — pilead send <sid> <packet> --rotate "
                         "retires the session and starts a seeded one"})
    appr = [e["session_id"] for e in live if e["approaching"]]
    if appr:
        warnings.append({"level": "info", "text": f"approaching heavy: {_names(appr)} — plan a fresh session for "
                         "the next packet"})
    okl = [r for r in leads if not r.get("broken")]

    def lname(r):
        return r.get("project") or r["session_id"]

    hl = [lname(r) for r in okl if r.get("heavy")]
    if hl:
        warnings.append({"level": "info", "text": f"heavy lead: {_names(hl)} — pilead handoff <note.md> starts a "
                         "fresh lead session"})

    def wake_detail(items):
        if len(items) == 1:
            return items[0].get("wake_detail") or ""
        return "; ".join(f"{lname(r)}: {r.get('wake_detail') or ''}" for r in items)

    for word in ("stuck", "stale"):
        items = [r for r in okl if r.get("wake") == word]
        if items:
            warnings.append({"level": "bad", "text": f"wake {word.upper()}: {_names(lname(r) for r in items)} "
                             f"({wake_detail(items)}) — see pilead list"})
    unk = [r for r in okl if r.get("wake") == "unknown"]
    if unk:
        warnings.append({"level": "warn", "text": f"wake not known for: {_names(lname(r) for r in unk)} "
                         f"({wake_detail(unk)})"})
    if queue_bad:
        warnings.append({"level": "warn", "text": f"queue of {_names(queue_bad)} cannot be read"})
    broken = [r["session_id"] for r in leads if r.get("broken")] + [e["session_id"] for e in execs if e.get("broken")]
    if broken:
        warnings.append({"level": "bad", "text": f"{len(broken)} record(s) cannot be read: {_names(broken)} — "
                         "see pilead list"})
    warnings.extend(sweep_warnings)
    if not ledger_ok:
        warnings.append({"level": "warn", "text": f"the ledger {ledger.path(home)} cannot be read — the board "
                         "shows no ledger tail, and whether a report was delivered is not known"})
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "version": state.version(), "relay_bin": "pilead",
            "home": str(home), "leads": leads, "executors": execs, "warnings": warnings,
            "ledger_tail": _ledger_tail(records) if ledger_ok else []}


def _data_path(page):
    """The data file of a page: beside it, same name, ending `.json` (`board.html` → `board.json`)."""
    return Path(page).with_suffix(".json")


def _write_file(path, text):
    """`text` to `path` through a temporary file beside it and one rename, so a reader never sees half a file.
    The directory is made. No temporary file is left behind, also after a failure; the error is the OSError."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _json_text(data):
    return json.dumps(data, indent=2, default=str) + "\n"


def live_active(home, cfg=None):
    """Live mode is on: config `board_live` is true, or `<home>/board.json` exists. Never raises (false)."""
    try:
        cfg = cfg if cfg is not None else config.load(home)
        return cfg["board_live"] is True or _data_path(Path(home) / "board.html").exists()
    except Exception:  # noqa: BLE001
        return False


def _write_live(page, page_text, data_text):
    """The page, then its data file. When the data file cannot be written, the page is put back as it was
    (or removed, when there was none) and the error is raised."""
    page = Path(page)
    try:
        before = page.read_bytes()
    except OSError:
        before = None
    _write_file(page, page_text)
    try:
        _write_file(_data_path(page), data_text)
    except BaseException:
        try:
            if before is None:
                page.unlink()
            else:
                tmp = page.with_name(f".{page.name}.{os.getpid()}.tmp")
                try:
                    tmp.write_bytes(before)
                    os.replace(tmp, page)
                finally:
                    if tmp.exists():
                        tmp.unlink()
        except OSError:
            pass
        raise


def refresh_live_board(home):
    """Keep a live board fresh (claude-relay `refresh_live_board`): with live mode on, rewrite `<home>/board.html`
    (live) and `<home>/board.json` from `board_data(sweep=False, write=False)` — no sweep, no stored status, no
    usage cache, no ledger event: of all of home, those two files only. Never raises, prints nothing; a failure
    leaves both files as they were. A page written elsewhere (`--out`) is not touched."""
    try:
        home = Path(home)
        cfg = config.load(home)
        if not live_active(home, cfg):
            return
        data = board_data(home, sweep=False, write=False)
        page = board_render.render(data, live=True, refresh_seconds=int(cfg["board_refresh_seconds"]))
        _write_live(home / "board.html", page, _json_text(data))
    except Exception:  # noqa: BLE001 — the board never breaks the verb it follows
        pass


def refresh_after(home):
    """A verb's call of `refresh_live_board`, guarded: whatever it does or raises, the verb's exit code, stdout
    and files stay as they are."""
    try:
        refresh_live_board(home)
    except Exception:  # noqa: BLE001
        pass


def _board_refusal(a):
    """The one-line reason these options cannot go together, or None."""
    live = getattr(a, "live", None)
    if a.json:
        for flag, given in (("--open", a.open), ("--out", a.out is not None), ("--live", live is not None)):
            if given:
                return f"board: --json prints the data and writes no page, so it does not go with {flag}"
    if live == "off":
        for flag, given in (("--open", a.open), ("--json", a.json), ("--lead", a.lead is not None)):
            if given:
                return f"board: --live off removes the data file and renders nothing, so it does not go with {flag}"
    return None


def _live_off(home, out):
    """`--live off`: remove the data file of the page and say so in one line."""
    cfg = config.load(home)
    target = _data_path(out)
    removed = False
    try:
        target.unlink()
        removed = True
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as e:
        raise PileadError(f"cannot remove {target}: {e.strerror or e}", 1) from None
    if cfg["board_live"] is True:
        print(("data file removed, but " if removed else "") + "config board_live is true and keeps live mode on — "
              "set it to false to turn live mode off")
    elif removed:
        print(f"live mode off ({target} removed)")
    else:
        print(f"live mode was already off (no {target})")


def cmd_board(a):
    """`pilead board [--open] [--out PATH] [--lead SID] [--json] [--live [on|off]]`."""
    from . import autoclose
    why = _board_refusal(a)
    if why is not None:
        raise PileadError(why, 2)
    home = state.home_dir(a.home)
    out = Path(a.out).expanduser() if a.out is not None else home / "board.html"
    live_arg = getattr(a, "live", None)
    if live_arg == "off":
        _live_off(home, out)
        return
    if a.lead is not None:
        state.check_lead_sid(a.lead)  # first: an unusable --lead reads and writes nothing
    cfg = config.load(home)
    live = live_arg == "on" or cfg["board_live"] is True
    if live and _data_path(out) == out:
        raise PileadError(f"board: {out} ends in .json, so the page and its data file would be one file — "
                          "name the page something.html", 2)
    parked = []
    data = board_data(home, lead=a.lead, parked=parked)
    if a.json:
        print(json.dumps(data, indent=2, default=str))
        for line in autoclose.lines(home, parked):
            print(line, file=sys.stderr)
        return
    page = board_render.render(data, live=live, refresh_seconds=int(cfg["board_refresh_seconds"]))
    try:
        if live:
            _write_live(out, page, _json_text(data))
        else:
            _write_file(out, page)
    except OSError as e:
        raise PileadError(f"cannot write {out}: {e.strerror or e}", 1) from None
    print(out)
    print(Path(out).resolve().as_uri())
    for line in autoclose.lines(home, parked):
        print(line)
    if a.open:
        _open_or_print(out)

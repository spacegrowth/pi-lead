"""lib/pilead/turnend.py and `pilead turn-end`: what a lead is told when its turn ends. Homes under the test's
temp directory (the suite's PI_LEAD_HOME), repositories made here with the real git, session logs written by
tests/test_usage.py's writers. A banner is seen only through the stub `osascript`; the tty is a plain file."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from test_autoclose import H, git, mark_seen, repo, session as auto_session, set_age
from test_notify import osc, script
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger, notify, turnend, usage  # noqa: E402

LEAD = "lead-1"


def lead(tmp_path, cwd=None, project="proj", **fields):
    hm = H(tmp_path)
    (hm / "leads").mkdir(parents=True, exist_ok=True)
    (hm / "leads" / f"{LEAD}.json").write_text(json.dumps({
        "sid": LEAD, "project": project, "cwd": str(cwd) if cwd else None,
        "inbox": str(hm / "leads" / f"{LEAD}.inbox.md"), **fields}))
    return hm


def config(tmp_path, **cfg):
    (H(tmp_path)).mkdir(parents=True, exist_ok=True)
    (H(tmp_path) / "config.json").write_text(json.dumps(cfg))


def commit(wt, name):
    (wt / name).write_text(name)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"add {name}")
    return git(wt, "rev-parse", "--short", "HEAD").stdout.strip()


def head_file(tmp_path):
    return H(tmp_path) / "wake" / LEAD / "head"


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
            for p in sorted(root.rglob("*"))}


def run(tmp_path, cwd, **kw):
    return turnend.run(H(tmp_path), LEAD, str(cwd), **kw)


# ── the lead's own commits ────────────────────────────────────────────────────────────────────────
def test_first_run_writes_the_head_file_and_says_nothing_then_the_new_commits_are_named(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    top = git(wt, "rev-parse", "--show-toplevel").stdout.strip()
    first = git(wt, "rev-parse", "HEAD").stdout.strip()
    assert run(tmp_path, wt) == {"lines": [], "kinds": [], "parked": []}
    assert head_file(tmp_path).read_text() == f"{top}\t{first}\n"
    a, b = commit(wt, "a.txt"), commit(wt, "b.txt")
    res = run(tmp_path, wt)
    assert res["lines"] == [f"  📝 you made 2 commit(s) this turn in {wt}:", f"       {b} add b.txt", f"       {a} add a.txt"]
    assert (res["kinds"], res["parked"]) == (["commits"], [])
    assert head_file(tmp_path).read_text().strip().split("\t") == [top, git(wt, "rev-parse", "HEAD").stdout.strip()]
    assert run(tmp_path, wt) == {"lines": [], "kinds": [], "parked": []}, "no new commit gives nothing"


def test_surface_commits_false_says_nothing_but_the_file_holds_the_new_id(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    run(tmp_path, wt)
    commit(wt, "a.txt")
    assert run(tmp_path, wt)["lines"] == []
    assert head_file(tmp_path).read_text().split("\t")[1].strip() == git(wt, "rev-parse", "HEAD").stdout.strip()
    config(tmp_path, surface_commits=True)
    assert run(tmp_path, wt)["lines"] == [], "already written: not said later"


def test_twenty_five_new_commits_list_twenty(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    run(tmp_path, wt)
    for i in range(25):
        commit(wt, f"f{i}.txt")
    lines = run(tmp_path, wt)["lines"]
    assert lines[0] == f"  📝 you made 20 commit(s) this turn in {wt}:" and len(lines) == 21
    assert lines[1].endswith(" add f24.txt") and lines[-1].endswith(" add f5.txt")


def test_a_stored_commit_that_does_not_exist_gives_no_line_and_the_file_is_rewritten(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    top = git(wt, "rev-parse", "--show-toplevel").stdout.strip()
    head_file(tmp_path).parent.mkdir(parents=True)
    head_file(tmp_path).write_text(f"{top}\t{'0' * 40}\n")
    assert run(tmp_path, wt)["lines"] == []
    assert head_file(tmp_path).read_text() == f"{top}\t{git(wt, 'rev-parse', 'HEAD').stdout.strip()}\n"
    # a stored id that is not an id at all is never handed to git as an argument
    head_file(tmp_path).write_text(f"{top}\t--output=/tmp/pwned\n")
    assert run(tmp_path, wt)["lines"] == []
    assert head_file(tmp_path).read_text().endswith(git(wt, "rev-parse", "HEAD").stdout.strip() + "\n")


def test_a_cwd_outside_any_repository_gives_no_line_and_writes_no_file(tmp_path):
    lead(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    config(tmp_path, surface_commits=True)
    assert run(tmp_path, plain) == {"lines": [], "kinds": [], "parked": []}
    assert not head_file(tmp_path).exists()
    assert run(tmp_path, tmp_path / "does-not-exist")["lines"] == []
    assert not (H(tmp_path) / "wake").exists()


def test_another_repository_in_cwd_rewrites_the_file_without_a_line(tmp_path):
    one, two = repo(tmp_path, "one"), repo(tmp_path, "two")
    lead(tmp_path, one)
    config(tmp_path, surface_commits=True)
    run(tmp_path, one)
    commit(one, "x.txt")
    assert run(tmp_path, two)["lines"] == []
    assert head_file(tmp_path).read_text() == (
        f"{git(two, 'rev-parse', '--show-toplevel').stdout.strip()}\t{git(two, 'rev-parse', 'HEAD').stdout.strip()}\n")


def test_a_repository_without_a_commit_says_nothing_and_writes_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    lead(tmp_path, empty)
    assert run(tmp_path, empty)["lines"] == [] and not head_file(tmp_path).exists()


def test_git_is_the_real_git_by_absolute_path(tmp_path, monkeypatch):
    """A `git` first on PATH that is not git changes nothing: every call goes through refs.git_path()."""
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    run(tmp_path, wt)
    a = commit(wt, "a.txt")
    fake = tmp_path / "fakegit"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\necho boom >&2\nexit 9\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    assert run(tmp_path, wt)["lines"][1:] == [f"       {a} add a.txt"]


# ── executors approaching heavy ───────────────────────────────────────────────────────────────────
def warm_session(tmp_path, sid, live=125_000, status="busy", **kw):
    d = auto_session(tmp_path, sid, status=status, report=None, seen=None, **kw)
    wt = json.loads((d / "meta.json").read_text())["worktree"]
    if live is not None:
        write_log(sid, wt, [assistant(u(3, 40, live, 100))])
    return d


def warn_line(sid, live="125k"):
    return (f"  🟠 {sid} is approaching heavy ({live} ctx, warn line 120k, rotate line 150k) — plan a rotate: "
            f"pilead retire {sid} and a fresh spawn for its next packet")


def test_one_line_per_executor_of_this_lead_and_each_is_said_once(tmp_path):
    lead(tmp_path)
    warm_session(tmp_path, "s-a")
    warm_session(tmp_path, "s-b", live=140_000)
    res = run(tmp_path, tmp_path)
    assert res["lines"] == [warn_line("s-a"), warn_line("s-b", "140k")] and res["kinds"] == ["ctx-warn"]
    assert (H(tmp_path) / "wake" / LEAD / "notified" / "s-a.ctx-warn-wake").is_file()
    assert run(tmp_path, tmp_path)["lines"] == []


def test_others_closed_heavy_and_unknown_readings_give_none_and_the_unknown_one_speaks_later(tmp_path):
    lead(tmp_path)
    warm_session(tmp_path, "s-other", lead="lead-2")
    warm_session(tmp_path, "s-closed", status="closed")
    (H(tmp_path) / "sessions" / "s-closed" / "status").write_text("closed\n")
    warm_session(tmp_path, "s-heavy", live=155_000)
    warm_session(tmp_path, "s-cool", live=20_000)
    later = warm_session(tmp_path, "s-later", live=None)
    assert run(tmp_path, tmp_path)["lines"] == []
    assert not (H(tmp_path) / "wake").exists(), "nothing used up for a reading that is unknown, or for any of these"
    wt = json.loads((later / "meta.json").read_text())["worktree"]
    write_log("s-later", wt, [assistant(u(3, 40, 125_000, 100))])
    assert run(tmp_path, tmp_path)["lines"] == [warn_line("s-later")]


def test_ctx_warn_wake_false_gives_none_and_uses_up_nothing(tmp_path):
    lead(tmp_path)
    warm_session(tmp_path, "s-a")
    config(tmp_path, ctx_warn_wake=False)
    assert run(tmp_path, tmp_path)["lines"] == [] and not (H(tmp_path) / "wake").exists()
    config(tmp_path, ctx_warn_wake=True)
    assert run(tmp_path, tmp_path)["lines"] == [warn_line("s-a")]


@pytest.mark.parametrize("warn,nudge", [(150_000, 150_000), (160_000, 150_000)])
def test_warn_at_or_above_nudge_gives_none(tmp_path, warn, nudge):
    lead(tmp_path)
    warm_session(tmp_path, "s-a")
    config(tmp_path, context_warn_tokens=warn, context_nudge_tokens=nudge)
    assert run(tmp_path, tmp_path)["lines"] == []


def test_a_claim_that_cannot_be_made_gives_no_line(tmp_path):
    lead(tmp_path)
    warm_session(tmp_path, "s-a")
    (H(tmp_path) / "wake").mkdir()
    (H(tmp_path) / "wake" / LEAD).write_text("a file where the directory belongs")
    assert run(tmp_path, tmp_path)["lines"] == []


# ── the lead's own weight ─────────────────────────────────────────────────────────────────────────
def heavy_lead(tmp_path, live=320_000, model="claude-sonnet-5"):
    cwd = tmp_path / "leadcwd"
    cwd.mkdir(exist_ok=True)
    lead(tmp_path, cwd)
    write_log(LEAD, cwd, [assistant(u(10, 5, live, 0), model=model)])
    return cwd


HANDOFF = ("  🔁 this lead session is getting heavy ({reading}). Consider handing off to a fresh lead session: "
           "write a handoff note, then run pilead handoff <note.md>.")


def test_over_by_tokens_says_so_once_and_the_ledger_event_has_its_fields(tmp_path):
    cwd = heavy_lead(tmp_path)
    res = run(tmp_path, cwd)
    assert res["lines"] == [HANDOFF.format(reading="320k live, line 300k on a ? window")] and res["kinds"] == ["handoff"]
    assert (H(tmp_path) / "wake" / LEAD / "handoff-nudged").read_bytes() == b""
    u_, mb = usage.lead_reading(H(tmp_path), LEAD, str(cwd))
    (ev,) = ledger.read(H(tmp_path), event="handoff_nudged")
    assert {k: v for k, v in ev.items() if k != "ts"} == {"event": "handoff_nudged", "session_id": LEAD,
                                                          "mb": round(mb, 1), "tokens": u_["live"]}
    assert isinstance(ev["tokens"], int)
    assert run(tmp_path, cwd)["lines"] == []


def test_over_by_log_size(tmp_path):
    cwd = tmp_path / "leadcwd"
    cwd.mkdir()
    lead(tmp_path, cwd)
    write_log(LEAD, cwd, [assistant(u(10, 5, 1000, 0)), {"type": "custom", "data": "x" * 1_200_000}])
    config(tmp_path, handoff_nudge_mb=1)
    res = run(tmp_path, cwd)
    assert res["lines"] == [HANDOFF.format(reading="~1.1MB log, past 1MB")]
    (ev,) = ledger.read(H(tmp_path), event="handoff_nudged")
    assert ev["mb"] == 1.1 and ev["tokens"] == usage.lead_reading(H(tmp_path), LEAD, str(cwd))[0]["live"]
    assert run(tmp_path, cwd)["lines"] == []


def test_a_200k_window_uses_the_capped_line(tmp_path):
    cwd = heavy_lead(tmp_path, live=155_000, model="claude-haiku-4-5")
    (H(tmp_path) / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": "anthropic", "id": "claude-haiku-4-5", "context": "200K"}]}))
    assert run(tmp_path, cwd)["lines"] == [HANDOFF.format(reading="155k live, line 150k on a 200k window")]


def test_an_unknown_reading_and_a_switch_that_is_off_give_none_and_make_no_stamp(tmp_path):
    lead(tmp_path, tmp_path / "nolog")
    assert run(tmp_path, tmp_path)["lines"] == []
    cwd = heavy_lead(tmp_path)
    config(tmp_path, handoff_nudge=False)
    assert run(tmp_path, cwd)["lines"] == []
    assert not (H(tmp_path) / "wake").exists() and ledger.read(H(tmp_path), event="handoff_nudged") == []
    config(tmp_path, handoff_nudge=True)
    assert len(run(tmp_path, cwd)["lines"]) == 1


def test_a_stamp_that_cannot_be_made_gives_no_line(tmp_path):
    cwd = heavy_lead(tmp_path)
    (H(tmp_path) / "wake").mkdir()
    (H(tmp_path) / "wake" / LEAD).write_text("a file")
    assert run(tmp_path, cwd)["lines"] == [] and ledger.read(H(tmp_path), event="handoff_nudged") == []
    (H(tmp_path) / "wake" / LEAD).unlink()
    (H(tmp_path) / "wake" / LEAD).mkdir()
    (H(tmp_path) / "wake" / LEAD / "handoff-nudged").mkdir()  # the stamp's path is taken by a directory: it exists
    assert run(tmp_path, cwd)["lines"] == []


# ── the order of the lines, the sweep, errors ────────────────────────────────────────────────────
def test_all_steps_in_order_and_the_sweep_parks_first(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    write_log(LEAD, wt, [assistant(u(10, 5, 320_000, 0))])
    config(tmp_path, surface_commits=True)
    warm_session(tmp_path, "s-warm")
    auto_session(tmp_path, "s-done", age=3700, seen="report_verify")
    run(tmp_path, wt, dry_run=True)
    assert (head_file(tmp_path)).exists() is False
    res = run(tmp_path, wt)  # first run: head written, no commit line
    assert res["parked"] == [["s-done", "close", "landed"]]
    assert res["kinds"] == ["ctx-warn", "handoff"] and len(res["lines"]) == 2
    commit(wt, "z.txt")
    (H(tmp_path) / "wake" / LEAD / "handoff-nudged").unlink()
    (H(tmp_path) / "wake" / LEAD / "notified" / "s-warm.ctx-warn-wake").unlink()
    res = run(tmp_path, wt)
    assert res["kinds"] == ["commits", "ctx-warn", "handoff"]
    assert res["lines"][0].startswith("  📝 you made 1 commit(s)") and res["lines"][2].startswith("  🟠 s-warm")
    assert res["lines"][3].startswith("  🔁 ") and res["parked"] == []


def test_an_error_in_one_step_does_not_stop_the_others(tmp_path, monkeypatch):
    cwd = heavy_lead(tmp_path)
    warm_session(tmp_path, "s-a")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(turnend, "_commit_lines", boom)
    assert run(tmp_path, cwd)["kinds"] == ["ctx-warn", "handoff"]
    monkeypatch.setattr(turnend.autoclose, "sweep", boom)
    (H(tmp_path) / "wake" / LEAD / "handoff-nudged").unlink()
    assert run(tmp_path, cwd)["kinds"] == ["handoff"]


def test_wake_lead_being_a_file_stops_no_step_and_raises_nothing(tmp_path):
    cwd = heavy_lead(tmp_path)
    auto_session(tmp_path, "s-done", age=3700, seen="report_verify")
    (H(tmp_path) / "wake").mkdir()
    (H(tmp_path) / "wake" / LEAD).write_text("in the way")
    res = run(tmp_path, cwd)
    assert res["lines"] == [] and res["kinds"] == [] and res["parked"] == [["s-done", "close", "landed"]]


def test_run_never_raises(tmp_path):
    assert turnend.run(tmp_path / "no" / "home", LEAD, str(tmp_path)) == {"lines": [], "kinds": [], "parked": []}
    assert turnend.run(tmp_path / "no" / "home", "../../x", None) == {"lines": [], "kinds": [], "parked": []}
    assert turnend.run(None, None, None, dry_run=True, banner=True) == {"lines": [], "kinds": [], "parked": []}


# ── dry run ───────────────────────────────────────────────────────────────────────────────────────
def test_dry_run_changes_nothing_and_says_what_the_real_run_says(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    write_log(LEAD, wt, [assistant(u(10, 5, 320_000, 0))])
    run(tmp_path, wt)  # the head file
    (H(tmp_path) / "wake" / LEAD / "handoff-nudged").unlink()
    commit(wt, "a.txt")
    warm_session(tmp_path, "s-warm")
    auto_session(tmp_path, "s-done", age=3700, seen="report_verify")
    before = tree(tmp_path)
    dry = run(tmp_path, wt, dry_run=True, banner=True)
    assert tree(tmp_path) == before, "every file's content and modification time, and the set of files"
    assert dry["kinds"] == ["commits", "ctx-warn", "handoff"] and dry["parked"] == [["s-done", "close", "landed"]]
    real = run(tmp_path, wt)
    assert real == dry
    assert tree(tmp_path) != before


def test_dry_run_of_a_repeat_says_nothing_new(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    warm_session(tmp_path, "s-warm")
    run(tmp_path, wt)
    assert run(tmp_path, wt, dry_run=True)["lines"] == []


# ── the banner ────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def osa(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_NO_NOTIFY", raising=False)
    monkeypatch.delenv("PILEAD_NO_NOTIFY", raising=False)
    log = tmp_path / "stub.log"
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    (bindir / "osascript").write_text("#!/bin/sh\n{ printf '%s\\n' \"$@\"; echo =====; } >> \"$STUB_LOG\"\n")
    (bindir / "osascript").chmod(0o755)
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def calls():
        return [c.strip("\n").split("\n") for c in log.read_text().split("=====\n") if c.strip()] if log.exists() else []

    return calls


def two_commits(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    run(tmp_path, wt)
    commit(wt, "a.txt")
    commit(wt, "b.txt")
    return wt


def test_banner_only_when_asked_and_only_for_a_line_other_than_a_warn_line(tmp_path, osa):
    wt = two_commits(tmp_path)
    run(tmp_path, wt)
    assert osa() == [], "not asked for"
    commit(wt, "c.txt")
    res = run(tmp_path, wt, banner=True)
    assert osa() == [["-e", script("pi-lead · proj", f"review needed — you made 1 commit(s) this turn in {wt}:")]]
    assert len(res["lines"]) == 2
    # only a 🟠 line: no banner
    warm_session(tmp_path, "s-a")
    assert run(tmp_path, wt, banner=True)["kinds"] == ["ctx-warn"]
    assert len(osa()) == 1
    # a 🟠 line first and then a 🔁 line: the message is the line that is not a 🟠 line
    (H(tmp_path) / "wake" / LEAD / "notified" / "s-a.ctx-warn-wake").unlink()
    write_log(LEAD, wt, [assistant(u(10, 5, 320_000, 0))])
    run(tmp_path, wt, banner=True)
    assert osa()[1][1].startswith('display notification "review needed — this lead session is getting heavy (320k live')


def test_banner_uses_the_leads_tty_when_the_record_has_one(tmp_path, osa):
    wt = repo(tmp_path, "r")
    tty = tmp_path / "tty"
    lead(tmp_path, wt, tty=str(tty))
    config(tmp_path, surface_commits=True)
    run(tmp_path, wt)
    commit(wt, "a.txt")
    run(tmp_path, wt, banner=True)
    assert tty.read_text() == osc("pi-lead · proj", f"review needed — you made 1 commit(s) this turn in {wt}:")
    assert osa() == []


@pytest.mark.parametrize("var", ["PILEAD_NO_NOTIFY", "RELAY_NO_NOTIFY"])
def test_no_banner_under_the_kill_switch(tmp_path, osa, monkeypatch, var):
    wt = two_commits(tmp_path)
    monkeypatch.setenv(var, "1")
    assert run(tmp_path, wt, banner=True)["lines"] != []
    assert osa() == []


def test_no_banner_when_notify_on_wake_is_false(tmp_path, osa):
    wt = two_commits(tmp_path)
    config(tmp_path, surface_commits=True, notify_on_wake=False)
    assert run(tmp_path, wt, banner=True)["lines"] != [] and osa() == []


# ── the verb ──────────────────────────────────────────────────────────────────────────────────────
def verb(*args, env=None, home=True):
    e = {**os.environ, **(env or {})}
    return subprocess.run([str(PILEAD), "turn-end", *args] + (["--home", str(e["PI_LEAD_HOME"])] if home else []),
                          capture_output=True, text=True, env=e)


def test_the_sid_comes_from_session_then_pi_lead_sid_then_pi_session_id(tmp_path):
    lead(tmp_path)
    for env, args in (({}, ["--session", LEAD]), ({"PI_LEAD_SID": LEAD}, []), ({"PI_SESSION_ID": LEAD}, []),
                      ({"PI_LEAD_SID": LEAD, "PI_SESSION_ID": "other"}, []),
                      ({"PI_LEAD_SID": "other", "PI_SESSION_ID": "other"}, ["--session", LEAD])):
        r = verb(*args, env=env)
        assert (r.returncode, r.stdout, r.stderr) == (0, "nothing to say\n", ""), (env, args, r)


def test_no_session_at_all_exits_2(tmp_path):
    lead(tmp_path)
    r = verb()
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == "pilead: turn-end: no lead session id — pass --session, or run it inside the lead's own pi session\n"


def test_an_unusable_sid_exits_2_and_leaves_the_directory_byte_identical(tmp_path):
    deep = tmp_path / "a" / "b" / "home"
    deep.mkdir(parents=True)
    before = tree(tmp_path)
    r = verb("--session", "../../x", env={"PI_LEAD_HOME": str(deep)})
    assert r.returncode == 2 and r.stdout == "" and "is not usable" in r.stderr
    assert tree(tmp_path) == before


def test_an_unregistered_sid_exits_2_and_writes_nothing(tmp_path):
    hm = H(tmp_path)
    hm.mkdir()
    before = tree(tmp_path)
    r = verb("--session", "lead-9")
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: turn-end: lead-9 is not a registered lead\n")
    assert tree(tmp_path) == before


def test_json_prints_one_line_with_the_three_keys(tmp_path):
    heavy_lead(tmp_path)
    r = verb("--session", LEAD, "--json")
    assert r.returncode == 0 and r.stdout.count("\n") == 1
    d = json.loads(r.stdout)
    assert set(d) == {"lines", "kinds", "parked"} and d["kinds"] == ["handoff"] and d["parked"] == []
    assert d["lines"] == [HANDOFF.format(reading="320k live, line 300k on a ? window")]
    empty = verb("--session", LEAD, "--json")
    assert json.loads(empty.stdout) == {"lines": [], "kinds": [], "parked": []}


def test_text_output_is_the_lines_or_nothing_to_say_and_dry_run_says_so_first(tmp_path):
    heavy_lead(tmp_path)
    dry = verb("--session", LEAD, "--dry-run")
    assert dry.stdout == "dry run — nothing was written\n" + HANDOFF.format(reading="320k live, line 300k on a ? window") + "\n"
    assert not (H(tmp_path) / "wake").exists()
    real = verb("--session", LEAD)
    assert real.stdout == HANDOFF.format(reading="320k live, line 300k on a ? window") + "\n"
    assert verb("--session", LEAD).stdout == "nothing to say\n"
    assert verb("--session", LEAD, "--dry-run").stdout == "dry run — nothing was written\nnothing to say\n"


def test_cwd_defaults_to_the_records_and_can_be_given(tmp_path):
    wt = repo(tmp_path, "r")
    lead(tmp_path, wt)
    config(tmp_path, surface_commits=True)
    verb("--session", LEAD)
    commit(wt, "a.txt")
    other = tmp_path / "plain"
    other.mkdir()
    assert verb("--session", LEAD, "--cwd", str(other)).stdout == "nothing to say\n"
    assert verb("--session", LEAD).stdout.startswith(f"  📝 you made 1 commit(s) this turn in {wt}:")


def test_banner_flag(tmp_path, osa):
    wt = two_commits(tmp_path)
    r = verb("--session", LEAD, "--banner")
    assert r.returncode == 0 and len(osa()) == 1

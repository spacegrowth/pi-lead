"""`pilead resume` for an executor and for a lead, driven through bin/pilead with the fake `pi`
(tests/fake_pi), a recording `osascript` stub and a `ps` stub first on PATH (the terminal seam: whether
iTerm "runs" and whether a tab "exists" come from the test's environment, never from this machine). A
live process is a child this file starts and stops. Session logs are small synthetic files
(tests/test_usage.py's writers). No real tab is opened or closed and the real pi never runs."""
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from test_close_variants import tree
from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"

# The osascript stub: a spawn answers OK (or NOTARGET when $FAKE_SPAWN is "fail"); every other question
# (does a session with this id exist? close it) is answered with $FAKE_TAB ("false" unless a test sets it).
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*)
    if [ "$FAKE_SPAWN" = fail ]; then printf 'NOTARGET\\n\\nfront\\n'; else printf 'OK\\nFAKE-UUID\\nfront\\n'; fi ;;
  *) echo "${FAKE_TAB:-false}" ;;
esac
"""
# `ps -axo comm=` (is iTerm running?) answers from $FAKE_ITERM_RUNNING; every other ps call is the real one.
PS = """#!/bin/sh
if [ "$1" = "-axo" ]; then
  [ -n "$FAKE_ITERM_RUNNING" ] && echo /Applications/iTerm.app/Contents/MacOS/iTerm2
  exit 0
fi
exec /bin/ps "$@"
"""


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "false")
    monkeypatch.delenv("FAKE_SPAWN", raising=False)


def pilead(*args, check=True, env=None):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stderr
    return r


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def sdir(tmp_path, sid):
    return H(tmp_path) / "sessions" / sid


def meta(tmp_path, sid):
    return json.loads((sdir(tmp_path, sid) / "meta.json").read_text())


def events(tmp_path, name=None):
    p = H(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if name is None or r["event"] == name]


def fake_pi_calls(tmp_path):
    p = tmp_path / "fake-pi.log"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


def run_bootstrap(boot):
    """Run a launch's bootstrap as the new tab's shell would, so fake_pi records the launch line."""
    subprocess.run(["sh", str(boot), "FAKE-UUID"], check=True,
                   env={**os.environ, "ITERM_SESSION_ID": "w0t0p0:FAKE-UUID"}, capture_output=True)


@pytest.fixture
def env(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    pilead("lead-start", "lead-1", "--project", "proj")
    return tmp_path


@pytest.fixture
def child():
    procs = []

    def start():
        p = subprocess.Popen(["sleep", "120"])
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()


def spawn(env, log=True, effort=None):
    extra = ["--effort", effort] if effort else []
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1",
               *extra)
    sid = r.stdout.split()[1]
    if log:
        write_log(sid, (env / "wt").resolve(), [assistant(u(10, 5))])
    return sid


def ended_pid(child):
    p = child()
    p.kill()
    p.wait()
    return p.pid


def mark_reported(env, sid):
    """The current packet reported (report file written, `reported` stored) — what a test does before a
    plain `pilead send`."""
    n = meta(env, sid)["packets"]
    (sdir(env, sid) / f"report-{n:04d}.md").write_text("Done.\nStatus: clean\n")
    (sdir(env, sid) / "status").write_text("reported\n")


def nudge(env, sid, n=1):
    return (f"You were resumed after your tab closed. Your staged work in this worktree is intact. Pick up "
            f"packet {n:04d} where you left off, finish it, and write your report to "
            f"{sdir(env, sid) / f'report-{n:04d}.md'}.")


def refused(r, *words):
    assert r.returncode == 2 and r.stdout == "", (r.stdout, r.stderr)
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith("pilead: "), r.stderr
    for w in words:
        assert w in r.stderr, (w, r.stderr)


# ── executor: the refusals ───────────────────────────────────────────────────────────────────────
def test_an_alive_session_is_refused_unchanged_and_force_resumes_it(env, child):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "pid").write_text(f"{child().pid}\n")
    before = tree(H(env))
    r = pilead("resume", sid, check=False)
    refused(r, "two live copies of one conversation would work against each other", "--force")
    assert tree(H(env)) == before
    r = pilead("resume", sid, "--force")
    assert r.stdout.splitlines()[0] == f"resumed {sid} tab=FAKE-UUID"


def test_an_alive_session_by_its_tab_alone_is_refused(env):
    """No pid file: the tab decides (it exists → alive)."""
    sid = spawn(env)
    pilead("close", sid)
    assert not (sdir(env, sid) / "pid").exists()
    before = tree(H(env))
    refused(pilead("resume", sid, check=False, env={"FAKE_TAB": "true"}), "work against each other")
    assert tree(H(env)) == before


def test_a_tab_left_open_is_refused_naming_close_and_force_resumes_it(env, child):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    before = tree(H(env))
    r = pilead("resume", sid, check=False, env={"FAKE_TAB": "true"})
    refused(r, "still open", "second copy of this conversation", f"pilead close {sid}", "--force")
    assert tree(H(env)) == before
    r = pilead("resume", sid, "--force", env={"FAKE_TAB": "true"})
    assert r.stdout.splitlines()[0] == f"resumed {sid} tab=FAKE-UUID"


def test_the_same_session_with_no_tab_resumes_without_force(env, child):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    r = pilead("resume", sid)
    assert r.stdout.splitlines() == [f"resumed {sid} tab=FAKE-UUID", r.stdout.splitlines()[-1]]
    assert r.stdout.splitlines()[-1].startswith("placement=")
    assert not (sdir(env, sid) / "pid").exists()  # the old run's pid file is gone before the tab opens


def test_no_session_log_is_refused_naming_restart_and_force_resumes(env):
    sid = spawn(env, log=False)
    pilead("close", sid)
    before = tree(H(env))
    r = pilead("resume", sid, check=False)
    refused(r, "EMPTY conversation", f"pilead restart {sid}", "--force")
    assert tree(H(env)) == before
    assert pilead("resume", sid, "--force").stdout.startswith(f"resumed {sid} ")


def test_an_unknown_sid_gives_the_existing_error(env):
    before = tree(H(env))
    r = pilead("resume", "nobody-here", check=False)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session nobody-here\n")
    assert tree(H(env)) == before


def test_an_effort_that_is_no_level_is_refused(env):
    sid = spawn(env)
    pilead("close", sid)
    before = tree(H(env))
    refused(pilead("resume", sid, "--effort", "extreme", check=False), "not a thinking level")
    assert tree(H(env)) == before


# ── executor: what a resume does ─────────────────────────────────────────────────────────────────
def test_the_launch_line_reopens_the_same_session(env):
    sid = spawn(env, effort="medium")
    pilead("close", sid)
    pilead("resume", sid)
    run_bootstrap(sdir(env, sid) / "bootstrap.sh")
    argv = fake_pi_calls(env)[-1]["argv"]
    assert argv[argv.index("--session-id") + 1] == sid
    assert "--model" not in argv and not any(x.startswith("@") for x in argv)
    assert argv[argv.index("--thinking") + 1] == "medium"
    assert fake_pi_calls(env)[-1]["env"]["PI_LEAD_ROLE"] == "executor"


def test_effort_changes_the_stored_effort_and_the_line(env):
    sid = spawn(env)
    assert meta(env, sid)["effort"] == "high"
    pilead("close", sid)
    pilead("resume", sid, "--effort", "LOW")
    assert meta(env, sid)["effort"] == "low"
    run_bootstrap(sdir(env, sid) / "bootstrap.sh")
    argv = fake_pi_calls(env)[-1]["argv"]
    assert argv[argv.index("--thinking") + 1] == "low" and argv.count("--thinking") == 1
    (ev,) = events(env, "resumed")
    assert ev == {"ts": ev["ts"], "event": "resumed", "session_id": sid, "packet": 1, "effort": "low"}


def test_no_report_puts_the_nudge_after_what_was_waiting_and_is_busy(env):
    sid = spawn(env)
    pilead("close", sid)
    inbox = sdir(env, sid) / "inbox.md"
    inbox.write_text("an earlier line still waiting\n")
    pilead("resume", sid)
    text = inbox.read_text()
    assert text.startswith("an earlier line still waiting\n")
    assert text.rstrip("\n").endswith(nudge(env, sid))
    assert text.count("You were resumed") == 1
    assert (sdir(env, sid) / "status").read_text() == "busy\n" and meta(env, sid)["status"] == "busy"


def test_no_report_and_an_empty_inbox_is_just_the_nudge(env):
    sid = spawn(env)
    pilead("close", sid)
    pilead("resume", sid)
    assert (sdir(env, sid) / "inbox.md").read_text() == nudge(env, sid) + "\n"


def test_a_report_leaves_the_inbox_alone_and_is_reported(env):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "report-0001.md").write_text("Done.\n")
    inbox = sdir(env, sid) / "inbox.md"
    inbox.write_text("x\n")
    pilead("resume", sid)
    assert inbox.read_text() == "x\n"
    assert (sdir(env, sid) / "status").read_text() == "reported\n"


def test_the_nudge_names_the_current_packet_after_a_send(env):
    sid = spawn(env)
    mark_reported(env, sid)
    (env / "p2.md").write_text("GOAL: the second thing\n")
    pilead("send", sid, str(env / "p2.md"))
    pilead("close", sid)
    (sdir(env, sid) / "inbox.md").write_text("")
    pilead("resume", sid)
    assert (sdir(env, sid) / "inbox.md").read_text() == nudge(env, sid, 2) + "\n"


def git_repo(path):
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (Path(path) / "f.txt").write_text("x\n")
    run("add", "f.txt")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "one")


def test_refs_baselines_are_left_as_they_are(env):
    git_repo(env / "wt")
    sid = spawn(env)
    mark_reported(env, sid)
    (env / "p2.md").write_text("GOAL: the second thing\n")
    pilead("send", sid, str(env / "p2.md"))
    pilead("close", sid)
    d = sdir(env, sid)
    refs = lambda: {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in d.glob("refs-*.json")}  # noqa: E731
    before = refs()
    assert set(before) == {"refs-0001.json", "refs-0002.json"}
    pilead("resume", sid)
    assert refs() == before
    # the current packet's baseline missing: it stays missing
    (d / "refs-0002.json").unlink()
    before = refs()
    pilead("resume", sid)
    assert refs() == before and not (d / "refs-0002.json").exists()
    assert not events(env, "refs_snapshot_failed")


def test_a_superseded_session_resumes_with_one_more_line(env):
    sid = spawn(env)
    pilead("close", sid, "--supersede", "succ-1")
    r = pilead("resume", sid)
    lines = r.stdout.splitlines()
    assert lines[0] == f"resumed {sid} tab=FAKE-UUID" and lines[-1].startswith("placement=")
    assert len(lines) == 3 and "superseded by succ-1" in lines[1]
    assert meta(env, sid)["superseded_by"] == "succ-1"


def test_other_sessions_and_leads_are_untouched(env):
    a = spawn(env)
    b = spawn(env)
    pilead("close", a)
    other = {k: v for k, v in tree(H(env)).items() if k.startswith(f"sessions/{b}") or k.startswith("leads")}
    pilead("resume", a)
    assert {k: v for k, v in tree(H(env)).items()
            if k.startswith(f"sessions/{b}") or k.startswith("leads")} == other


# ── a lead ───────────────────────────────────────────────────────────────────────────────────────
LEAD = "lead-crashed"


@pytest.fixture
def lead(tmp_path):
    """A lead record with a cwd that exists, a session log for it there, and no tab handle."""
    cwd = (tmp_path / "lead-cwd")
    cwd.mkdir()
    cwd = cwd.resolve()
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": LEAD, "project": "my proj", "cwd": str(cwd), "inbox": str(d / f"{LEAD}.inbox.md"),
           "iterm_handle": None, "terminal": "iTerm.app", "started": "2026-09-27T09:00:00Z"}
    (d / f"{LEAD}.json").write_text(json.dumps(rec, indent=2) + "\n")
    (d / f"{LEAD}.inbox.md").write_text("")
    write_log(LEAD, cwd, [assistant(u(10, 5))])
    return rec


def lead_message(project="my proj", sid=LEAD):
    return (f"You are the resumed lead for project '{project}'. First run: pilead lead-start \"$PI_SESSION_ID\" "
            f"--project '{project}'. If your session id is not {sid}, also run: pilead stop {sid}. Then run "
            f"pilead list and continue where you left off.")


def test_a_live_lead_is_refused_unchanged(tmp_path, lead):
    rec_file = H(tmp_path) / "leads" / f"{LEAD}.json"
    rec = json.loads(rec_file.read_text())
    rec["iterm_handle"] = "w0t0p0:11111111-2222-3333-4444-555555555555"
    rec_file.write_text(json.dumps(rec))
    before = tree(H(tmp_path))
    r = pilead("resume", LEAD, check=False, env={"FAKE_TAB": "true"})
    refused(r, "is live", "--force")
    assert tree(H(tmp_path)) == before
    assert pilead("resume", LEAD, "--force", env={"FAKE_TAB": "true"}).stdout.startswith(f"resumed lead {LEAD} ")


def test_a_lead_with_no_session_log_is_refused(tmp_path, lead):
    for p in (Path(os.environ["PI_CODING_AGENT_DIR"]) / "sessions").glob("*/*.jsonl"):
        p.unlink()
    before = tree(H(tmp_path))
    refused(pilead("resume", LEAD, check=False), "no session log", "EMPTY conversation", "--force")
    assert tree(H(tmp_path)) == before


def test_a_lead_log_in_another_cwd_is_not_its_log(tmp_path, lead):
    for p in (Path(os.environ["PI_CODING_AGENT_DIR"]) / "sessions").glob("*/*.jsonl"):
        p.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_log(LEAD, elsewhere.resolve(), [assistant(u(1, 1))])
    refused(pilead("resume", LEAD, check=False), "no session log")


def test_a_lead_whose_cwd_is_gone_is_refused_even_with_force(tmp_path, lead):
    Path(lead["cwd"]).rmdir()
    assert not Path(lead["cwd"]).exists()
    before = tree(H(tmp_path))
    for argv in (["resume", LEAD], ["resume", LEAD, "--force"]):
        refused(pilead(*argv, check=False), "not a directory")
    assert tree(H(tmp_path)) == before


def test_a_lead_resumes_with_its_own_launch_line(tmp_path, lead):
    r = pilead("resume", LEAD, "--effort", "xhigh")
    assert r.stdout.splitlines()[0] == f"resumed lead {LEAD} (my proj) tab=FAKE-UUID"
    assert r.stdout.splitlines()[1].startswith("placement=") and len(r.stdout.splitlines()) == 2
    ldir = H(tmp_path) / "leads" / f"{LEAD}.launch"
    boot = ldir / "bootstrap.sh"
    assert boot.is_file()
    text = boot.read_text()
    assert "PI_LEAD_ROLE" not in text and "PILEAD_SHIM_DIR" not in text and "--append-system-prompt" not in text
    assert f"cd {shlex.quote(lead['cwd'])} && " in text
    run_bootstrap(boot)
    assert (ldir / "pid").is_file() and (ldir / "iterm-id").is_file()
    assert sorted(p.name for p in ldir.iterdir()) == ["bootstrap.sh", "iterm-id", "pid"]
    rec = fake_pi_calls(tmp_path)[-1]
    argv = rec["argv"]
    assert argv[:2] == ["--session-id", LEAD]
    assert argv[argv.index("--thinking") + 1] == "xhigh"
    assert argv[argv.index("-e") + 1] == str(ROOT / "extensions" / "pi-lead.ts")
    assert "--model" not in argv and argv[-1] == lead_message()
    assert rec["env"] == {"PI_LEAD_HOME": str(H(tmp_path))}
    # the osascript spawn titled the tab `[Lead] <project>`
    assert "/name" not in (tmp_path / "osa.log").read_text()


def test_a_lead_record_keeps_every_field_and_gains_resumed(tmp_path, lead):
    before = json.loads((H(tmp_path) / "leads" / f"{LEAD}.json").read_text())
    pilead("resume", LEAD)
    after = json.loads((H(tmp_path) / "leads" / f"{LEAD}.json").read_text())
    assert set(after) == set(before) | {"resumed"}
    assert {k: after[k] for k in before} == before
    assert len(after["resumed"]) == 20 and after["resumed"].endswith("Z")
    (ev,) = events(tmp_path, "lead_resumed")
    assert ev == {"ts": ev["ts"], "event": "lead_resumed", "session_id": LEAD, "project": "my proj"}


def test_a_lead_resume_without_effort_has_no_thinking(tmp_path, lead):
    pilead("resume", LEAD)
    run_bootstrap(H(tmp_path) / "leads" / f"{LEAD}.launch" / "bootstrap.sh")
    assert "--thinking" not in fake_pi_calls(tmp_path)[-1]["argv"]


def test_a_lead_resume_touches_no_other_lead_or_session(tmp_path, lead, env):
    sid = spawn(env)
    mine = lambda k: LEAD in k or k in ("leads", "ledger.jsonl")  # noqa: E731  (its dir gains its files)
    other = {k: v for k, v in tree(H(tmp_path)).items() if not mine(k)}
    pilead("resume", LEAD)
    assert {k: v for k, v in tree(H(tmp_path)).items() if not mine(k)} == other
    assert sid in {k.split("/")[1] for k in other if k.startswith("sessions/")}


def test_stop_afterwards_removes_the_launch_directory_and_names_it(tmp_path, lead):
    pilead("resume", LEAD)
    ldir = H(tmp_path) / "leads" / f"{LEAD}.launch"
    run_bootstrap(ldir / "bootstrap.sh")
    r = pilead("stop", LEAD)
    assert f"removed {ldir}" in r.stdout.splitlines()
    assert not os.path.lexists(ldir)


# ── p22: resuming an executor from a lead is a claim of it ─────────────────────────────────────────
def test_a_resume_from_a_lead_adopts_an_orphan_first(env, child):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    (H(env) / "leads" / "lead-1.json").unlink()
    pilead("lead-start", "lead-2", "--project", "other")
    r = pilead("resume", sid, env={"PI_LEAD_SID": "lead-2"})
    lines = r.stdout.splitlines()
    assert lines[0] == f"adopted {sid} from lead-1 (no longer a registered lead)"
    assert lines[1] == f"resumed {sid} tab=FAKE-UUID"
    assert meta(env, sid)["lead"] == "lead-2"
    assert [e["reason"] for e in events(env, "adopted")] == ["claim"]


def test_a_refused_resume_adopts_nothing(env):
    sid = spawn(env, log=False)
    pilead("close", sid)
    (H(env) / "leads" / "lead-1.json").unlink()
    pilead("lead-start", "lead-2", "--project", "other")
    before = tree(H(env))
    refused(pilead("resume", sid, check=False, env={"PI_LEAD_SID": "lead-2"}), "EMPTY conversation")
    assert tree(H(env)) == before


def test_a_resume_with_no_caller_adopts_nothing(env, child):
    sid = spawn(env)
    pilead("close", sid)
    (sdir(env, sid) / "pid").write_text(f"{ended_pid(child)}\n")
    (H(env) / "leads" / "lead-1.json").unlink()
    r = pilead("resume", sid)
    assert r.stdout.splitlines()[0] == f"resumed {sid} tab=FAKE-UUID"
    assert meta(env, sid)["lead"] == "lead-1" and events(env, "adopted") == []

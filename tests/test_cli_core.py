"""bin/pilead driven through subprocess against fake_pi and a stub `osascript` (no real iTerm)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"

from conftest import OSA_STUB  # noqa: E402  (the one stub text; conftest puts it on PATH for every test)


@pytest.fixture(autouse=True)
def stub_osascript(tmp_path, monkeypatch):
    """A recording `osascript` first on PATH: no test can reach the real iTerm."""
    stub = tmp_path / "fakebin" / "osascript"
    stub.write_text(OSA_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))


@pytest.fixture
def env(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    return tmp_path


def pilead(*args, check=True):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def home(tmp_path):
    return tmp_path / "pi-lead-home"


def start_lead(sid="lead-1"):
    pilead("lead-start", sid, "--project", "proj")


def spawn(env, model="sonnet", topic="topic"):
    start_lead()
    r = pilead("spawn", str(env / "wt"), topic, str(env / "packet.md"), "--model", model, "--lead", "lead-1")
    return r.stdout.split()[1]  # "spawned <sid> ..."


def make_reported(env, sid, n=1, home=None):
    """The session has finished packet n: its report exists and its stored status is `reported`."""
    home_dir = home if home is not None else globals()["home"](env)
    sdir = home_dir / "sessions" / sid
    (sdir / f"report-{n:04d}.md").write_text("Done.\n\nStatus: clean\nRisk flags: none\n")
    (sdir / "status").write_text("reported\n")


def run_bootstrap(env, sid):
    """Run the typed-line's payload for real (as the tab's shell would) so fake_pi logs its argv."""
    boot = home(env) / "sessions" / sid / "bootstrap.sh"
    subprocess.run(["sh", str(boot), "FAKE-UUID"], check=True,
                   env={**os.environ, "ITERM_SESSION_ID": "w0t0p0:FAKE-UUID"})
    return [json.loads(l) for l in (env / "fake-pi.log").read_text().splitlines()]


def test_help_lists_all_verbs():
    out = pilead("--help").stdout
    for v in ("lead-start", "spawn", "send", "list", "check", "close"):
        assert v in out


def test_lead_start_idempotent(env):
    start_lead()
    p = home(env) / "leads" / "lead-1.json"
    first = json.loads(p.read_text())
    start_lead()
    again = json.loads(p.read_text())
    assert again["started"] == first["started"] and again["project"] == "proj"
    assert Path(again["inbox"]).is_file() and Path(again["inbox"]).read_text() == ""


def test_lead_start_removes_its_own_forward_note_with_one_stderr_line(env):
    notes = home(env) / "handoffs"
    notes.mkdir(parents=True)
    (notes / "lead-1.json").write_text(json.dumps({"from": "lead-1", "to": "lead-9", "project": "proj",
                                                   "at": "-", "reason": "takeover"}))
    (notes / "lead-2.json").write_text("{}")
    r = pilead("lead-start", "lead-1", "--project", "proj")
    assert r.stdout == f"lead lead-1 ready inbox={home(env) / 'leads' / 'lead-1.inbox.md'}\n"
    assert r.stderr == (f"pilead: removed the forward note {notes / 'lead-1.json'} — lead-1 is a lead again, and its "
                        f"executors' reports wake it\n")
    assert not (notes / "lead-1.json").exists() and (notes / "lead-2.json").exists()
    assert pilead("lead-start", "lead-1", "--project", "proj").stderr == ""


def test_lead_start_of_a_moved_record_keeps_its_project_and_lineage(env):
    leads = home(env) / "leads"
    leads.mkdir(parents=True)
    rec = {"sid": "lead-n", "project": "orig", "inbox": str(leads / "lead-n.inbox.md"), "started": "2026-01-01T00:00:00Z",
           "lineage_started": "2025-12-01T00:00:00Z", "moved_from": "lead-o", "predecessor": "lead-p"}
    (leads / "lead-n.json").write_text(json.dumps(rec))
    r = pilead("lead-start", "lead-n", "--project", "other")
    assert r.stdout == f"lead lead-n ready inbox={leads / 'lead-n.inbox.md'}\n"
    assert r.stderr == "pilead: lead-n took its role over from lead-o and keeps its project 'orig', not 'other'\n"
    got = json.loads((leads / "lead-n.json").read_text())
    assert (got["project"], got["lineage_started"], got["moved_from"], got["predecessor"]) == (
        "orig", "2025-12-01T00:00:00Z", "lead-o", "lead-p")
    ev = [json.loads(l) for l in (home(env) / "ledger.jsonl").read_text().splitlines()]
    assert [(e["event"], e["session_id"], e["project"]) for e in ev] == [("lead_started", "lead-n", "orig")]
    r = pilead("lead-start", "lead-n", "--project", "orig")
    assert r.stderr == "" and json.loads((leads / "lead-n.json").read_text())["project"] == "orig"


def test_spawn_writes_the_leads_project_as_owner_project(env):
    sid = spawn(env)
    meta = json.loads((home(env) / "sessions" / sid / "meta.json").read_text())
    assert meta["owner_project"] == "proj"


def test_spawn_under_a_lead_with_no_project_writes_owner_project_null(env):
    leads = home(env) / "leads"
    leads.mkdir(parents=True)
    (leads / "lead-np.json").write_text(json.dumps({"sid": "lead-np", "inbox": str(leads / "lead-np.inbox.md")}))
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-np")
    sid = r.stdout.split()[1]
    meta = json.loads((home(env) / "sessions" / sid / "meta.json").read_text())
    assert "owner_project" in meta and meta["owner_project"] is None


def test_spawn_creates_tree_footer_and_busy(env):
    r = spawn(env)
    sid = r
    sdir = home(env) / "sessions" / sid
    assert sid.startswith("topic-")
    assert (sdir / "status").read_text().strip() == "busy"
    assert (sdir / "inbox.md").read_text() == ""
    pkt = (sdir / "packet-0001.md").read_text()
    assert pkt.startswith("GOAL: do the thing")
    assert f"\nREPORT: write your full report (the outcome in one plain sentence first, then the TL;DR block) to\n  {sdir}/report-0001.md\n" in pkt
    assert f"\n  pilead diff {sid} --home {home(env)}\n" in pkt
    assert pkt.endswith(f" — diff: {(sdir / 'diff-0001.html').resolve().as_uri()} — idle, awaiting the lead's review.\n")
    meta = json.loads((sdir / "meta.json").read_text())
    assert meta["model"] == "anthropic/claude-sonnet-5" and meta["packets"] == 1 and meta["lead"] == "lead-1"
    assert json.loads((home(env) / "models.json").read_text())["models"]


def test_spawn_prints_line(env):
    start_lead()
    out = pilead("spawn", str(env / "wt"), "t", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1").stdout
    assert out.startswith("spawned t-") and "model=anthropic/claude-sonnet-5 tab=FAKE-UUID" in out


def test_spawn_launch_line_reaches_fake_pi(env):
    sid = spawn(env)
    (rec,) = run_bootstrap(env, sid)
    sdir = home(env) / "sessions" / sid
    argv = rec["argv"]
    assert argv[argv.index("--session-id") + 1] == sid
    assert argv[argv.index("--model") + 1] == "anthropic/claude-sonnet-5"
    assert argv[argv.index("-e") + 1] == str(ROOT / "extensions" / "pi-lead.ts")
    assert argv[-1] == f"@{sdir}/packet-0001.md"
    assert rec["env"]["PI_LEAD_ROLE"] == "executor" and rec["env"]["PI_LEAD_SID"] == sid
    assert rec["env"]["PI_LEAD_INBOX"] == str(sdir / "inbox.md") and rec["env"]["PI_LEAD_LEAD"] == "lead-1"
    assert "do the thing" in rec["files"][f"{sdir}/packet-0001.md"]


def test_spawn_installs_git_shim_first_on_pi_path(env):
    import shutil
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    git = sdir / "shim" / "git"
    assert git.is_file() and os.access(git, os.X_OK) and "PILEAD_GIT_SHIM" in git.read_text()
    assert json.loads((sdir / "meta.json").read_text())["shim"] is True
    boot = (sdir / "bootstrap.sh").read_text()
    assert f"export PILEAD_SHIM_DIR={sdir / 'shim'}" in boot
    assert boot.index('export PATH="$PILEAD_SHIM_DIR:$PATH"') < boot.index("exec pi")
    (rec,) = run_bootstrap(env, sid)
    assert rec["env"]["PILEAD_SHIM_DIR"] == str(sdir / "shim")
    assert rec["path"].split(os.pathsep)[0] == str(sdir / "shim")
    assert shutil.which("git", path=rec["path"]) == str(git)  # what pi and every child of it resolves
    pilead("close", sid)
    assert git.is_file()  # close keeps the session's files


def test_spawn_shim_guards_the_session_worktree(env):
    """_launch writes a shim naming the session's worktree's repository; a worktree git cannot resolve
    (the plain dir the other tests spawn in) gets the guard-everything shim."""
    sys.path.insert(0, str(ROOT / "lib"))
    from pilead import shim
    real = next(str(Path(d, "git")) for d in os.environ["PATH"].split(os.pathsep) if d.startswith("/")
                and Path(d, "git").is_file() and shim.MARKER.encode() not in Path(d, "git").read_bytes()[:4096])
    subprocess.run([real, "init", "-q", str(env / "wt")], check=True, capture_output=True)
    sid = spawn(env)
    text = (home(env) / "sessions" / sid / "shim" / "git").read_text()
    top = os.path.realpath(env / "wt")
    assert f"PL_GUARD_TOP='{top}'\n" in text and f"PL_GUARD_COMMON='{top}/.git'\n" in text
    (env / "plain").mkdir()
    r = pilead("spawn", str(env / "plain"), "t2", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid2 = r.stdout.split()[1]
    assert (home(env) / "sessions" / sid2 / "shim" / "git").read_text() == shim.script()


def test_send_relaunch_rewrites_the_shim(env):
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    pilead("close", sid)
    (sdir / "shim" / "git").unlink()
    (env / "p2.md").write_text("again\n")
    pilead("send", sid, str(env / "p2.md"))
    assert "PILEAD_GIT_SHIM" in (sdir / "shim" / "git").read_text()
    assert run_bootstrap(env, sid)[-1]["path"].split(os.pathsep)[0] == str(sdir / "shim")


# exported by the human's shell before spawn; each one would make the shim refuse every git call
INHERITED_REFUSED = {
    "GIT_CONFIG_GLOBAL": "/nonexistent/g.cfg", "GIT_CONFIG_SYSTEM": "/nonexistent/s.cfg", "GIT_CONFIG": "/nonexistent/c",
    "GIT_TEMPLATE_DIR": "/nonexistent/tpl", "GIT_EXEC_PATH": "/nonexistent/exec", "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_PARAMETERS": "'color.ui'='auto'", "GIT_EDITOR": "git var GIT_EDITOR", "PAGER": "/usr/bin/git -p log",
    "GIT_SSH_COMMAND": 'sh -c "git fetch"',
}
# harmless values the shim lets through: left exactly as the human set them
INHERITED_KEPT = {"EDITOR": "vim", "VISUAL": "code --wait", "GIT_ASKPASS": "/x/extensions/git/dist/askpass.sh",
                  "GIT_SSH": "/usr/local/git/bin/ssh", "GIT_SEQUENCE_EDITOR": "/opt/git/tools/vim"}


def test_spawn_clears_inherited_git_env_the_shim_refuses_on(env, monkeypatch):
    for k, v in {**INHERITED_REFUSED, **INHERITED_KEPT}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FAKE_PI_GIT", "1")  # fake_pi runs `git --version` through its PATH (the shim)
    sid = spawn(env)
    sdir = home(env) / "sessions" / sid
    (rec,) = run_bootstrap(env, sid)
    assert sorted(set(INHERITED_REFUSED) & set(rec["git_env"])) == []
    assert {k: rec["git_env"].get(k) for k in INHERITED_KEPT} == INHERITED_KEPT
    assert rec["git"] == [0, ""]  # not bricked: the shim passed the call on to the real git
    # a value set later, inside the executor's session, still meets the shim
    base = {k: v for k, v in os.environ.items() if k not in INHERITED_REFUSED}
    base.update(PATH=rec["path"], PILEAD_SHIM_DIR=str(sdir / "shim"))
    ok = subprocess.run(["git", "--version"], env=base, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    for k, v in (("GIT_EXEC_PATH", "/nonexistent"), ("GIT_EDITOR", "git commit")):
        r = subprocess.run(["git", "--version"], env={**base, k: v}, capture_output=True, text=True)
        assert r.returncode == 77 and k in r.stderr, (k, r.stderr)


def test_send_relaunch_clears_inherited_git_env_the_shim_refuses_on(env, monkeypatch):
    sid = spawn(env)
    pilead("close", sid)
    for k, v in {**INHERITED_REFUSED, **INHERITED_KEPT}.items():  # exported by the time the lead relaunches it
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FAKE_PI_GIT", "1")
    (env / "p2.md").write_text("again\n")
    pilead("send", sid, str(env / "p2.md"))
    rec = run_bootstrap(env, sid)[-1]  # bootstrap.sh was rewritten by the relaunch
    assert "--session-id" in rec["argv"] and "--model" not in rec["argv"]  # the relaunch line, not the spawn's
    assert sorted(set(INHERITED_REFUSED) & set(rec["git_env"])) == []
    assert {k: rec["git_env"].get(k) for k in INHERITED_KEPT} == INHERITED_KEPT
    assert rec["git"] == [0, ""]
    assert rec["path"].split(os.pathsep)[0] == str(home(env) / "sessions" / sid / "shim")


def test_provider_id_model_passes_verbatim(env):
    sid = spawn(env, model="deepseek/deepseek-v4-pro")
    assert json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["model"] == "deepseek/deepseek-v4-pro"
    assert not (home(env) / "models.json").exists()  # no lookup needed


def test_aliases_resolve_latest_of_family(env):
    for alias, want in (("sonnet", "anthropic/claude-sonnet-5"), ("haiku", "anthropic/claude-haiku-4-5"),
                        ("dsflash", "deepseek/deepseek-flash"), ("dspro", "deepseek/deepseek-v4-pro")):
        sid = spawn(env, model=alias, topic=alias)
        assert json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["model"] == want


def test_unknown_alias_exits_2_naming_aliases(env):
    start_lead()
    r = pilead("spawn", str(env / "wt"), "t", str(env / "packet.md"), "--model", "gpt", "--lead", "lead-1", check=False)
    assert r.returncode == 2
    assert "haiku" in r.stderr and "dspro" in r.stderr and len(r.stderr.strip().splitlines()) == 1


def test_unknown_sid_exits_2_one_line(env):
    for verb in (("check", "nope"), ("close", "nope"), ("send", "nope", str(env / "packet.md"))):
        r = pilead(*verb, check=False)
        assert r.returncode == 2 and r.stderr.strip() == "pilead: unknown session nope"


def test_send_numbers_packets_and_fills_inbox(env):
    sid = spawn(env)
    (env / "p2.md").write_text("second\n")
    (env / "p3.md").write_text("third\n")
    make_reported(env, sid)
    assert pilead("send", sid, str(env / "p2.md")).stdout.strip() == f"sent {sid} packet 0002"
    sdir = home(env) / "sessions" / sid
    inbox = (sdir / "inbox.md").read_text()
    assert inbox.startswith("second") and f"{sdir}/report-0002.md" in inbox
    make_reported(env, sid, 2)
    assert pilead("send", sid, str(env / "p3.md")).stdout.strip() == f"sent {sid} packet 0003"
    assert (sdir / "packet-0003.md").is_file()
    assert "second" in (sdir / "inbox.md").read_text() and "third" in (sdir / "inbox.md").read_text()
    assert json.loads((sdir / "meta.json").read_text())["packets"] == 3


def test_send_to_closed_relaunches_resume(env):
    sid = spawn(env)
    pilead("close", sid)
    (env / "p2.md").write_text("again\n")
    pilead("send", sid, str(env / "p2.md"))
    sdir = home(env) / "sessions" / sid
    assert (sdir / "status").read_text().strip() == "busy"
    assert "again" in (sdir / "inbox.md").read_text()
    recs = run_bootstrap(env, sid)  # bootstrap.sh was rewritten by the relaunch
    argv = recs[-1]["argv"]
    assert "--session-id" in argv and "--model" not in argv and not any(a.startswith("@") for a in argv)
    assert recs[-1]["env"]["PI_LEAD_ROLE"] == "executor"


def test_list_check_close_round_trip(env):
    sid = spawn(env)
    out = pilead("list").stdout.splitlines()
    assert out[out.index("EXECUTORS") + 1].split() == ["SESSION", "STATUS", "TOPIC", "SCOPE", "PROJECT", "MODEL", "PKT", "CTX", "TOKENS", "EFFORT", "REPORTED"]
    assert out[out.index("EXECUTORS") + 2].split()[:3] == [sid, "busy", "topic"] and out[out.index("EXECUTORS") + 2].split()[5:7] == ["claude-sonnet-5", "0001"]
    assert json.loads(pilead("list", "--json").stdout)["executors"][0]["sid"] == sid
    assert pilead("check", sid).stdout.strip() == "busy"
    sdir = home(env) / "sessions" / sid
    (sdir / "report-0001.md").write_text("Did it.\n\nStatus: clean\nRisk flags: none\n\n## More\nx\n")
    (sdir / "status").write_text("reported\n")
    chk = pilead("check", sid).stdout.splitlines()
    assert chk[0] == "reported" and chk[1] == str(sdir / "report-0001.md")
    assert chk[2:] == ["Did it.", "Status: clean", "Risk flags: none"]
    assert [(r.split()[1], r.split()[-1]) for r in pilead("list").stdout.splitlines() if r.startswith(sid)] == [("reported", "yes")]
    assert pilead("close", sid).stdout.strip() == f"closed {sid}"
    assert pilead("check", sid).stdout.strip() == "closed"
    assert (sdir / "packet-0001.md").is_file()
    assert "tell s to close" in (env / "osa.log").read_text()


def test_home_flag_overrides_env(env, tmp_path):
    other = tmp_path / "other-home"
    pilead("lead-start", "l2", "--project", "p", "--home", str(other))
    assert (other / "leads" / "l2.json").is_file()
    assert not (home(env) / "leads" / "l2.json").exists()


def test_spawn_unknown_lead_exits_2(env):
    r = pilead("spawn", str(env / "wt"), "t", str(env / "packet.md"), "--model", "sonnet", "--lead", "ghost", check=False)
    assert r.returncode == 2 and "unknown lead ghost" in r.stderr


def test_close_terminates_recorded_pid(env):
    import signal
    sid = spawn(env)
    child = subprocess.Popen(["sleep", "60"])  # throwaway stand-in; never a real pi
    try:
        (home(env) / "sessions" / sid / "pid").write_text(f"{child.pid}\n")
        pilead("close", sid)
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
    (home(env) / "sessions" / sid / "pid").write_text(f"{child.pid}\n")  # now dead: close is a no-op
    assert pilead("close", sid).stdout.strip() == f"closed {sid}"


# ── send: the heaviness gate and the advisory line (lib/pilead/usage.py) ─────────────────────────
def _git(wt, *args):
    subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def spawn_with_log(env, *entries, git=False):
    """spawn a session (in a git worktree when `git`, so it has a refs baseline) and give it a pi session
    log with `entries` (test_usage helpers) at pi's default place for its worktree."""
    from test_usage import write_log
    if git:
        _git(env / "wt", "init", "-q")
        _git(env / "wt", "commit", "-q", "--allow-empty", "-m", "seed")
    sid = spawn(env)
    make_reported(env, sid)
    if entries:
        write_log(sid, (env / "wt").resolve(), list(entries))
    (env / "p2.md").write_text("second\n")
    return sid


def tree_bytes(root):
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def heavy_entries(live=164_000):
    from test_usage import assistant, u
    return [assistant(u(4, 1000, 12000, 3000)), assistant(u(5, 2000, live - 2005, 0))]


def test_send_refuses_a_heavy_session_and_changes_nothing(env):
    sid = spawn_with_log(env, *heavy_entries(), git=True)
    sdir = home(env) / "sessions" / sid
    assert (sdir / "refs-0001.json").is_file()
    before, ledger_before = tree_bytes(sdir), (home(env) / "ledger.jsonl").read_bytes()
    r = pilead("send", sid, str(env / "p2.md"), check=False)
    assert r.returncode == 2 and r.stdout == ""
    assert len(r.stderr.splitlines()) == 1
    msg = r.stderr
    assert msg.startswith(f"pilead: session {sid} is heavy: 164k ctx, at or above the 150k-token "
                          "context_nudge_tokens threshold.")
    assert "not a verdict on the executor's work" in msg
    assert f"`pilead close {sid}`" in msg and "`pilead spawn`" in msg and '--heavy-override "<reason>"' in msg
    # meta.json, inbox.md, the packet files, the refs baselines, status: every byte as before; no usage.json
    assert tree_bytes(sdir) == before
    assert (home(env) / "ledger.jsonl").read_bytes() == ledger_before


def test_send_a_heavy_session_with_a_reason(env):
    sid = spawn_with_log(env, *heavy_entries())
    r = pilead("send", sid, str(env / "p2.md"), "--heavy-override", "one more fix, same files")
    assert r.stdout.splitlines() == ["  ctx: 164k, hit-rate 98%", f"sent {sid} packet 0002"]
    assert r.stderr == ""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "lib"))
    from pilead import ledger
    (rec,) = ledger.read(home(env), event="heavy_override")
    assert {k: v for k, v in rec.items() if k not in ("ts", "event")} == {
        "session_id": sid, "packet": 2, "tokens": 164_000, "mb": 0.0, "reason": "one more fix, same files"}
    assert json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["packets"] == 2


@pytest.mark.parametrize("reason", ["", "   ", "\t\n"])
@pytest.mark.parametrize("heavy", [True, False])
def test_send_refuses_an_empty_override_reason(env, reason, heavy):
    from test_usage import assistant, u
    sid = spawn_with_log(env, *(heavy_entries() if heavy else [assistant(u(10, 5))]))
    sdir = home(env) / "sessions" / sid
    before, ledger_before = tree_bytes(sdir), (home(env) / "ledger.jsonl").read_bytes()
    r = pilead("send", sid, str(env / "p2.md"), "--heavy-override", reason, check=False)
    assert r.returncode == 2 and r.stdout == "" and "--heavy-override needs a reason" in r.stderr
    assert tree_bytes(sdir) == before and (home(env) / "ledger.jsonl").read_bytes() == ledger_before


def test_send_not_heavy_prints_the_advisory_before_sent(env):
    from test_usage import assistant, u
    sid = spawn_with_log(env, assistant(u(10, 500, 30000, 9990)))
    r = pilead("send", sid, str(env / "p2.md"))
    assert r.stdout.splitlines() == ["  ctx: 40k, hit-rate 75%", f"sent {sid} packet 0002"]
    assert r.stderr == ""


def test_send_advisory_says_dash_for_what_is_unknown(env):
    from test_usage import assistant, compaction, u
    sid = spawn_with_log(env, assistant(u()), compaction())  # no good request, and no prompt tokens
    r = pilead("send", sid, str(env / "p2.md"))
    assert r.stdout.splitlines() == ["  ctx: -, hit-rate -", f"sent {sid} packet 0002"]


def test_send_with_no_session_log_prints_exactly_what_it_did(env):
    sid = spawn_with_log(env)
    r = pilead("send", sid, str(env / "p2.md"))
    assert (r.stdout, r.stderr) == (f"sent {sid} packet 0002\n", "")


def test_send_heavy_by_log_size_when_live_is_unknown(env):
    """After a compaction with no later request the live context is unknown: the log's size decides."""
    from test_usage import assistant, compaction, u
    sid = spawn_with_log(env, assistant(u(10, 5)), compaction())
    (home(env) / "config.json").write_text(json.dumps({"handoff_nudge_mb": 0.0001}))
    r = pilead("send", sid, str(env / "p2.md"), check=False)
    assert r.returncode == 2
    assert "(live context unknown; MB proxy), at or above the 0.0001MB handoff_nudge_mb threshold" in r.stderr


def test_send_threshold_comes_from_config(env):
    from test_usage import assistant, u
    sid = spawn_with_log(env, assistant(u(10, 500, 30000, 9990)))  # 40k live
    (home(env) / "config.json").write_text(json.dumps({"context_nudge_tokens": 40_000}))
    r = pilead("send", sid, str(env / "p2.md"), check=False)
    assert r.returncode == 2 and "at or above the 40k-token context_nudge_tokens threshold" in r.stderr


# ── the third-packet note (a plain send making packet 3 or more) ─────────────────────────────────
def reported(env, sid, n):
    """The current packet reported, as a test does directly before a plain send."""
    sdir = home(env) / "sessions" / sid
    (sdir / f"report-{n:04d}.md").write_text("Done.\nStatus: clean\n")
    (sdir / "status").write_text("reported\n")


def note_line(sid, n, what, how):
    return (f"pilead: this is packet {n} into a {what} session — if the last two were fixes for the same work and "
            f"it still is not done, stop sending fixes: pilead send {sid} <packet> {how}\n")


def test_send_packets_2_and_3_and_the_note(env):
    sid = spawn(env)
    (env / "p2.md").write_text("second\n")
    (env / "p3.md").write_text("third\n")
    reported(env, sid, 1)
    r = pilead("send", sid, str(env / "p2.md"))
    assert (r.stdout, r.stderr) == (f"sent {sid} packet 0002\n", "")
    reported(env, sid, 2)
    r = pilead("send", sid, str(env / "p3.md"))
    assert r.returncode == 0 and r.stdout == f"sent {sid} packet 0003\n"
    assert r.stderr == note_line(sid, 3, "sonnet", "--upgrade moves it one tier up with a seeded successor")


def test_the_note_for_a_model_with_no_tier_names_rotate_model(env):
    sid = spawn(env, model="dspro")
    (env / "p2.md").write_text("second\n")
    (env / "p3.md").write_text("third\n")
    reported(env, sid, 1)
    assert pilead("send", sid, str(env / "p2.md")).stderr == ""
    reported(env, sid, 2)
    r = pilead("send", sid, str(env / "p3.md"))
    assert r.stdout == f"sent {sid} packet 0003\n"
    assert r.stderr == note_line(sid, 3, "deepseek/deepseek-v4-pro",
                                 "--rotate --model <model> moves it to the model you name with a seeded successor")
    assert "--upgrade" not in r.stderr

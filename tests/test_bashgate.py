"""lib/pilead/bashgate.py — the lead's bash write gate: `decide(home, lead_sid, command, cwd, record)`.

Every test writes the five keys the gate reads (`edit_line_threshold`, `grace_seconds`, `block_on_new_file`,
`bash_write_gate`, `bash_gate_logging`) into its own config.json; none depends on a default. Repositories are
made here with the real git under tmp_path. The fixture (also the one tests/bash-gate-commands.json is judged
against):

    tmp/user/                  $HOME
    tmp/user/.pi-lead/         pi-lead home (two directories deep), leads/lead-1.json registered
    tmp/repo/                  cwd, a git repository:
        lib/tracked.py  bin/tool  _staging/keep.md        tracked
        lib/scratch.py                                    exists, untracked
        untracked_dir/                                    no tracked file in it
    config: threshold 40, grace 600, block_on_new_file true, bash_gate_logging true, mode per test
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import bashgate, config, ledger, refs  # noqa: E402

SID = "lead-1"
TABLE = Path(__file__).resolve().parent / "bash-gate-commands.json"


def body(n):
    return "\n".join("line %d" % i for i in range(n))


def heredoc(target, n, how="cat"):
    if how == "cat":
        return "cat > %s <<EOF\n%s\nEOF" % (target, body(n))
    if how == "tee":
        return "tee %s <<EOF\n%s\nEOF" % (target, body(n))
    return "cat <<EOF | sort | tee %s\n%s\nEOF" % (target, body(n))  # through a pipe


def write_cfg(home, mode="deny", threshold=40, grace=600, new_file=True, logging=True):
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.json").write_text(json.dumps({
        "edit_line_threshold": threshold, "grace_seconds": grace, "block_on_new_file": new_file,
        "bash_write_gate": mode, "bash_gate_logging": logging}) + "\n")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(r):
    (r / "lib").mkdir(parents=True)
    (r / "lib" / "tracked.py").write_text("x\n")
    (r / "bin").mkdir()
    (r / "bin" / "tool").write_text("x\n")
    (r / "_staging").mkdir()
    (r / "_staging" / "keep.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(r)], check=True, capture_output=True)
    git(r, "add", "-A")
    (r / "lib" / "scratch.py").write_text("untracked\n")
    (r / "untracked_dir").mkdir()
    return r


def register(home, sid=SID):
    (Path(home) / "leads").mkdir(parents=True, exist_ok=True)
    (Path(home) / "leads" / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": "p"}) + "\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    user = tmp_path / "user"
    home = user / ".pi-lead"
    register(home)
    monkeypatch.setenv("HOME", str(user))
    repo = make_repo(tmp_path / "repo")
    write_cfg(home)
    return SimpleNamespace(tmp=tmp_path, user=user, home=home, repo=repo,
                           real_repo=Path(os.path.realpath(repo)), real_home=Path(os.path.realpath(home)))


def decide(e, cmd, cwd=None, record=False, sid=SID, home=None):
    return bashgate.decide(home or e.home, sid, cmd, str(cwd or e.repo), record)


def events(e):
    return ledger.read(e.home)


def tree(root):
    """{relative path: bytes (or 'dir')} of everything under root — a byte-identical check."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        out[str(p.relative_to(root))] = "dir" if p.is_dir() else p.read_bytes()
    return out


def hit_of(res):
    return next((t for t in res["targets"] if t["hit"]), None)


def set_grace(e, age_seconds, sid=SID):
    p = e.home / "leads" / f"{sid}.route"
    p.write_text("retain: test\n")
    t = time.time() - age_seconds
    os.utime(p, (t, t))


# ── 2a: the steps ────────────────────────────────────────────────────────────────────────────────
def test_2a_row1_unregistered_session_allows_and_looks_at_nothing(env):
    before = tree(env.tmp)
    for sid in ("lead-2", "../../x", "", None, 7):
        res = decide(env, "echo 1 > ~/.pi-lead/config.json", sid=sid, record=True)
        assert res == {"verdict": "allow", "mode": None, "reason": None, "targets": [], "rule": None, "parsed": None}
    assert tree(env.tmp) == before
    assert not ledger.path(env.home).exists()


@pytest.mark.parametrize("value,mode", [("deny", "deny"), ("log", "log"), ("off", "off"),
                                        ("Deny", "log"), (7, "log"), (None, "log"), ("", "log")])
def test_2a_row2_mode(env, value, mode):
    write_cfg(env.home, mode=value)
    assert decide(env, "ls")["mode"] == mode


def test_2a_row3_grace_open_skips_targets(env):
    set_grace(env, 5)
    res = decide(env, "echo x > lib/new.py")
    assert res["verdict"] == "allow" and res["targets"] == []


def test_2a_row4_first_hit_decides(env):
    # first target is not a hit (tracked, unknown size), the second is (new beside tracked)
    res = decide(env, "echo a > lib/tracked.py; echo b > lib/new.py; echo c > bin/new.sh")
    assert [t["hit"] for t in res["targets"]] == [False, True, None]
    assert res["reason"].split(" writes ")[1].startswith(str(env.real_repo / "lib" / "new.py"))


def test_2a_row4_mode_off_looks_for_no_target(env):
    write_cfg(env.home, mode="off")
    for cmd in ("echo x > lib/new.py", "echo 1 > ~/.pi-lead/config.json"):
        res = decide(env, cmd)
        assert res["verdict"] == "allow" and res["targets"] == []


def test_2a_row5_no_hit_logs_the_verb_rule(env):
    assert decide(env, "npm install")["rule"] == "npm-install"
    write_cfg(env.home, logging=False)
    assert decide(env, "npm install")["rule"] is None


# ── 2b: targets ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["$OUT", "a$b.py", "`x`.py", "lib/*.py", "lib/?.py", "lib/[ab].py",
                                  "lib/a].py", "lib/{a,b}.py", "lib/a}.py", "'lib/a\x01b.py'", "/dev/null",
                                  "/dev/stdout"])
def test_2b_unresolvable_or_dev_is_dropped(env, path):
    assert decide(env, "echo x > %s" % path)["targets"] == []


def test_2b_tilde_is_expanded(env):
    res = decide(env, "echo x > ~/notes.txt")
    assert [t["path"] for t in res["targets"]] == [str(Path(os.path.realpath(env.user)) / "notes.txt")]


def test_2b_relative_path_from_cwd(env):
    res = decide(env, "echo x > new.py", cwd=env.repo / "lib")
    assert res["targets"][0]["path"] == str(env.real_repo / "lib" / "new.py")


def test_2b_directory_part_resolved_through_symlinks(env):
    (env.repo / "link").symlink_to(env.repo / "lib")
    res = decide(env, "echo x > link/new.py")
    assert res["targets"][0]["path"] == str(env.real_repo / "lib" / "new.py")
    assert res["verdict"] == "block"


def test_2b_cp_mv_into_a_directory_one_target_per_source(env):
    (env.repo / "a.py").write_text("x\n")
    (env.repo / "b.py").write_text("x\n")
    for verb in ("cp", "mv"):
        res = decide(env, f"{verb} a.py b.py lib/")
        assert [t["path"] for t in res["targets"]] == [str(env.real_repo / "lib" / "a.py"),
                                                       str(env.real_repo / "lib" / "b.py")]


def test_2b_a_directory_target_is_dropped(env):
    assert decide(env, "echo x > lib")["targets"] == []
    assert decide(env, "cp a.py lib/")["targets"][0]["path"] == str(env.real_repo / "lib" / "a.py")


def test_2b_new_file_and_lines(env):
    res = decide(env, "echo x > lib/tracked.py; " + heredoc("lib/other.py", 3))
    assert [(t["new_file"], t["lines"]) for t in res["targets"]] == [(False, None), (True, 3)]


def test_2b_resolver_never_raises():
    assert isinstance(bashgate.resolve_targets("echo x > y", None), list)
    assert bashgate.resolve_targets(None, "/") == []
    assert bashgate.resolve_targets(5, "/") == []


# ── 2c: hits ─────────────────────────────────────────────────────────────────────────────────────
def test_2c_control_file_is_a_hit(env):
    res = decide(env, "echo 1 > ~/.pi-lead/config.json")
    assert hit_of(res)["control"] == "settings" and res["verdict"] == "block"


def test_2c_1_outside_cwd_is_not_a_hit(env):
    # inside the repository, new beside a tracked file — but not inside cwd
    res = decide(env, "echo x > ../bin/new.sh", cwd=env.repo / "lib")
    assert res["targets"] and not hit_of(res) and res["verdict"] == "allow"
    assert decide(env, "echo x > bin/new.sh")["verdict"] == "block"  # the same file from the repo root


def test_2c_2_packet_file_is_exempt(env):
    assert decide(env, "echo x > lib/002-packet.md")["verdict"] == "allow"


def test_2c_2_staging_is_exempt(env):
    assert decide(env, "echo x > _staging/new.md")["verdict"] == "allow"
    assert decide(env, heredoc("_staging/keep.md", 80))["verdict"] == "log"  # heredoc rule; not a hit
    assert not hit_of(decide(env, heredoc("_staging/keep.md", 80)))


@pytest.mark.parametrize("root", [".relay-tasks", ".pi-lead"])
def test_2c_2_tilde_state_roots_are_exempt(env, monkeypatch, root):
    fake = env.repo / "fakehome"
    (fake / root / "x").mkdir(parents=True)
    (fake / root / "x" / "tracked.md").write_text("x\n")
    git(env.repo, "add", "-A")
    monkeypatch.setenv("HOME", str(fake))
    assert decide(env, f"echo x > fakehome/{root}/x/new.md")["verdict"] == "allow"
    monkeypatch.setenv("HOME", str(env.user))
    assert decide(env, f"echo x > fakehome/{root}/x/new.md")["verdict"] == "block"


def test_2c_2_pi_lead_home_is_exempt(env):
    home = env.repo / "state"
    register(home)
    write_cfg(home)
    (home / "notes").mkdir()
    (home / "notes" / "tracked.md").write_text("x\n")
    git(env.repo, "add", "-A")
    assert decide(env, "echo x > state/notes/new.md", home=home)["verdict"] == "allow"
    assert decide(env, "echo x > state/notes/new.md")["verdict"] == "block"  # another home: not exempt


def test_2c_3_tracked_or_new_beside_tracked(env):
    assert decide(env, heredoc("lib/tracked.py", 80))["verdict"] == "block"   # tracked
    assert decide(env, heredoc("lib/scratch.py", 80))["verdict"] != "block"   # exists, untracked
    assert decide(env, "echo x > lib/new.py")["verdict"] == "block"           # new beside tracked
    assert decide(env, "echo x > untracked_dir/new.py")["verdict"] == "allow"  # new, no tracked file there


def test_2c_3_no_repository_is_not_a_hit(env):
    plain = env.tmp / "plain"
    plain.mkdir()
    (plain / "f.py").write_text("x\n")
    assert decide(env, "echo x > new.py", cwd=plain)["verdict"] == "allow"
    assert not hit_of(decide(env, heredoc("f.py", 80), cwd=plain))


LYING_GIT = ("#!/bin/sh\ncase \"$1\" in --version) echo 'git version 2.99.0 (stub)' ;;\n"
             "*) echo 'fatal: simulated git failure' >&2; exit 128 ;;\nesac\n")


def test_2c_3_git_failing_is_not_a_hit(env, monkeypatch):
    d = env.tmp / "lying-bin"
    d.mkdir()
    (d / "git").write_text(LYING_GIT)
    (d / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    refs._resolved.clear()
    try:
        assert refs.git_path() == str(d / "git")  # the resolver takes it; every real call then fails
        assert decide(env, "echo x > lib/new.py")["verdict"] == "allow"
    finally:
        refs._resolved.clear()


def test_2c_4_new_file_rule_and_size(env):
    write_cfg(env.home, new_file=False, logging=False)
    assert decide(env, "echo x > lib/new.py")["verdict"] == "allow"            # new, rule off, unknown size
    assert decide(env, heredoc("lib/new.py", 41))["verdict"] == "block"        # new, rule off, over
    assert decide(env, heredoc("lib/tracked.py", 41))["verdict"] == "block"    # known, over
    assert decide(env, heredoc("lib/tracked.py", 40))["verdict"] == "allow"    # known, not over
    assert decide(env, "sed -i s/x/y/ lib/tracked.py")["verdict"] == "allow"   # unknown, exists


# ── the threshold edge and the three heredoc routes ──────────────────────────────────────────────
@pytest.mark.parametrize("how", ["cat", "tee", "pipe"])
def test_threshold_edge(env, how):
    write_cfg(env.home, threshold=40, logging=False)
    at = decide(env, heredoc("lib/tracked.py", 40, how))
    assert at["verdict"] == "allow" and at["targets"][0]["lines"] == 40
    over = decide(env, heredoc("lib/tracked.py", 41, how))
    assert over["verdict"] == "block" and hit_of(over)["lines"] == 41


def test_unknown_size_scenarios(env):
    write_cfg(env.home, logging=False)
    assert decide(env, "echo x > lib/tracked.py")["verdict"] == "allow"
    assert decide(env, "echo x > lib/new.py")["verdict"] == "block"
    assert decide(env, "echo x > untracked_dir/new.py")["verdict"] == "allow"
    assert decide(env, "echo x > ../outside.py")["verdict"] == "allow"
    plain = env.tmp / "plain"
    plain.mkdir()
    assert decide(env, "echo x > new.py", cwd=plain)["verdict"] == "allow"


# ── control files ────────────────────────────────────────────────────────────────────────────────
CONTROL = [("config.json", "settings"), ("ledger.jsonl", "ledger"), ("leads/lead-1.route", "grace file"),
           ("leads/other.route", "grace file")]


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("rel,kind", CONTROL)
def test_control_files_are_hits_anywhere(env, mode, rel, kind):
    write_cfg(env.home, mode=mode)
    elsewhere = env.tmp / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    link = env.tmp / "homelink"
    if not link.exists():
        link.symlink_to(env.home)
    commands = [
        f"echo x > {env.home / rel}",                       # absolute, unknown size
        heredoc(str(env.home / rel), 1),                    # known, tiny
        f"echo x >> ~/.pi-lead/{rel}",                      # through ~
        f"echo x > {link / rel}",                           # through a symlink to home
    ]
    for cwd in (env.repo, elsewhere, Path("/")):
        for cmd in commands:
            res = decide(env, cmd, cwd=cwd)
            assert res["verdict"] == "block", (cmd, cwd)
            assert hit_of(res)["control"] == kind
            assert hit_of(res)["path"] == str(env.real_home / rel)


def test_control_file_through_a_symlinked_file(env):
    (env.repo / "cfg").symlink_to(env.home / "config.json")
    res = decide(env, "echo x > cfg")
    assert res["verdict"] == "block" and hit_of(res)["control"] == "settings"


@pytest.mark.parametrize("rel", ["leads/x.inbox.md", "sessions/s/packet-0002.md"])
def test_other_home_files_are_not_control_files(env, rel):
    (env.home / rel).parent.mkdir(parents=True, exist_ok=True)
    for cwd in (env.repo, env.home):
        res = decide(env, f"echo x > {env.home / rel}", cwd=cwd)
        assert res["verdict"] == "allow" and res["targets"][0]["control"] is None


# ── 2e: the verdict table ────────────────────────────────────────────────────────────────────────
CELLS = {  # column → command, judged in the fixture (logging on)
    "control": "echo 1 > ~/.pi-lead/config.json",
    "hit": "echo x > lib/new.py",
    "rule": "npm install",
    "nothing": "ls -la",
}
TABLE_2E = {
    "deny": {"control": "block", "hit": "block", "rule": "log", "nothing": "allow"},
    "log": {"control": "block", "hit": "log", "rule": "log", "nothing": "allow"},
    "off": {"control": "allow", "hit": "allow", "rule": "log", "nothing": "allow"},
}


@pytest.mark.parametrize("mode", list(TABLE_2E))
@pytest.mark.parametrize("column", list(CELLS))
def test_2e_verdict(env, mode, column):
    write_cfg(env.home, mode=mode)
    res = decide(env, CELLS[column])
    assert res["verdict"] == TABLE_2E[mode][column]
    assert (res["reason"] is not None) == (res["verdict"] == "block")


def test_log_from_a_hit_carries_the_verb_rule_or_bash_write(env):
    write_cfg(env.home, mode="log")
    assert decide(env, "echo x > lib/new.py")["rule"] == "bash-write"
    assert decide(env, "echo x | tee lib/new.py")["rule"] == "tee-mutation"


# ── the grace window ─────────────────────────────────────────────────────────────────────────────
def test_grace_open_allows_or_logs_a_verb_rule(env):
    set_grace(env, 10)
    assert decide(env, "echo x > lib/new.py")["verdict"] == "allow"
    assert decide(env, "echo 1 > ~/.pi-lead/config.json")["verdict"] == "allow"
    res = decide(env, heredoc("lib/new.py", 80))
    assert (res["verdict"], res["rule"], res["targets"]) == ("log", "heredoc", [])


@pytest.mark.parametrize("grace", [600, 90])
def test_grace_one_second_past_blocks_again(env, grace):
    write_cfg(env.home, grace=grace)
    set_grace(env, grace - 2)
    assert decide(env, "echo x > lib/new.py")["verdict"] == "allow"
    set_grace(env, grace + 1)
    assert decide(env, "echo x > lib/new.py")["verdict"] == "block"


def test_another_leads_grace_file_does_not_open_this_gate(env):
    set_grace(env, 5, sid="lead-9")
    assert decide(env, "echo x > lib/new.py")["verdict"] == "block"


# ── 2d: the verb log ─────────────────────────────────────────────────────────────────────────────
RULE_COMMANDS = [
    ("git commit -m wip", None), ("systemctl restart api", None), ("clickhouse-client -q 'select 1'", None),
    ("python3 -m pytest tests -q", None),
    ("npm install", "npm-install"), ("npm run build", "npm-run-build"), ("pip3 install requests", "package-install"),
    ("make all", "compiler"), ("git clone https://example.invalid/x.git", "git-clone"),
    ("systemctl daemon-reload", "service-file-write"), ("sed -i s/a/b/ notes.txt", "sed-inplace"),
    ("cat <<EOF\nhello\nEOF", "heredoc"), ("echo x | tee out.txt", "tee-mutation"),
    ("rsync -a src/ dst/", "rsync"),
]


def test_rule_tables_are_relays_in_order():
    assert [r["name"] for r in bashgate.CUSTODY_RULES] == [
        "git-commit-push", "systemctl-restart-status", "ssh-clickhouse-sql", "test-suite"]
    assert [r["name"] for r in bashgate.IMPLEMENTATION_RULES] == [
        "npm-install", "npm-run-build", "package-install", "compiler", "git-clone", "service-file-write",
        "sed-inplace", "heredoc", "tee-mutation", "rsync"]
    names = {n for _, n in RULE_COMMANDS if n}
    assert names == {r["name"] for r in bashgate.IMPLEMENTATION_RULES}
    assert len([c for c, n in RULE_COMMANDS if n is None]) == len(bashgate.CUSTODY_RULES)


@pytest.mark.parametrize("cmd,rule", RULE_COMMANDS, ids=[c.split()[0] + "-" + str(n) for c, n in RULE_COMMANDS])
def test_verb_log_rule(env, cmd, rule):
    plain = env.tmp / "plain"
    plain.mkdir(exist_ok=True)
    assert bashgate.classify(cmd) == rule
    res = decide(env, cmd, cwd=plain)
    assert (res["verdict"], res["rule"]) == (("log", rule) if rule else ("allow", None))


def test_custody_wins_over_implementation(env):
    assert bashgate.classify("pytest -q && npm install") is None
    assert decide(env, "pytest -q && npm install")["verdict"] == "allow"


def test_bash_gate_logging_false_allows(env):
    write_cfg(env.home, logging=False)
    res = decide(env, "npm install", record=True)
    assert (res["verdict"], res["rule"]) == ("allow", None)
    assert not ledger.path(env.home).exists()


# ── 2f: reasons ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("grace,text", [(120, "2 min"), (90, "90 s")])
def test_reasons_whole(env, grace, text):
    write_cfg(env.home, grace=grace)
    p = env.real_repo / "lib" / "new.py"
    assert decide(env, "echo x > lib/new.py")["reason"] == (
        f"pi-lead lead gate: this bash command writes {p} (new file) — delegate this (pilead spawn/send) or "
        f"/pilead:route retain \"<reason>\" and retry within {text}.")
    assert decide(env, heredoc("lib/new.py", 41))["reason"] == (
        f"pi-lead lead gate: this bash command writes {p} (new file, 41 lines) — delegate this "
        f"(pilead spawn/send) or /pilead:route retain \"<reason>\" and retry within {text}.")
    t = env.real_repo / "lib" / "tracked.py"
    assert decide(env, heredoc("lib/tracked.py", 41))["reason"] == (
        f"pi-lead lead gate: this bash command writes {t} (41 lines) — delegate this (pilead spawn/send) or "
        f"/pilead:route retain \"<reason>\" and retry within {text}.")
    c = env.real_home / "ledger.jsonl"
    assert decide(env, "echo x >> ~/.pi-lead/ledger.jsonl")["reason"] == (
        f"pi-lead lead gate: this bash command writes {c}, a pi-lead control file (ledger) — a change to it "
        f"needs a human's word; ask, or /pilead:route retain \"<reason>\" and retry within {text}.")


def test_size_and_grace_text():
    assert [bashgate.size_text(*a) for a in ((5, True), (5, False), (None, True), (None, False))] == [
        "new file, 5 lines", "5 lines", "new file", "unknown size"]
    assert [bashgate.grace_text(s) for s in (600, 60, 90, 1, 90.5, 120.0)] == [
        "10 min", "1 min", "90 s", "1 s", "90.5 s", "2 min"]


# ── 2g: the ledger ───────────────────────────────────────────────────────────────────────────────
def only_event(e):
    evs = events(e)
    assert len(evs) == 1, evs
    ev = dict(evs[0])
    assert isinstance(ev.pop("ts"), str)
    return ev


def test_ledger_blocked(env):
    decide(env, "echo x > lib/new.py", record=True)
    assert only_event(env) == {"event": "blocked", "session_id": SID, "file_path": str(env.real_repo / "lib" / "new.py"),
                               "lines": None, "new_file": True, "vector": "bash"}


def test_ledger_blocked_known_lines(env):
    decide(env, heredoc("lib/tracked.py", 41), record=True)
    assert only_event(env) == {"event": "blocked", "session_id": SID,
                               "file_path": str(env.real_repo / "lib" / "tracked.py"),
                               "lines": 41, "new_file": False, "vector": "bash"}


def test_ledger_blocked_control(env):
    write_cfg(env.home, mode="log")
    decide(env, "echo 1 > ~/.pi-lead/config.json", record=True)
    assert only_event(env) == {"event": "blocked", "session_id": SID, "file_path": str(env.real_home / "config.json"),
                               "lines": None, "new_file": False, "vector": "bash", "control": "settings"}


def test_ledger_would_have_blocked_from_a_hit(env):
    write_cfg(env.home, mode="log")
    cmd = "echo x > lib/new.py"
    decide(env, cmd, record=True)
    assert only_event(env) == {"event": "would_have_blocked", "session_id": SID, "command": cmd, "rule": "bash-write",
                               "vector": "bash", "file_path": str(env.real_repo / "lib" / "new.py"), "lines": None,
                               "new_file": True}


def test_ledger_would_have_blocked_from_a_hit_with_lines_and_rule(env):
    write_cfg(env.home, mode="log")
    cmd = heredoc("lib/tracked.py", 41)
    decide(env, cmd, record=True)
    assert only_event(env) == {"event": "would_have_blocked", "session_id": SID, "command": cmd, "rule": "heredoc",
                               "vector": "bash", "file_path": str(env.real_repo / "lib" / "tracked.py"), "lines": 41,
                               "new_file": False}


def test_ledger_would_have_blocked_from_the_verb_log(env):
    decide(env, "npm install", record=True)
    assert only_event(env) == {"event": "would_have_blocked", "session_id": SID, "command": "npm install",
                               "rule": "npm-install"}


def test_ledger_allow_writes_nothing(env):
    decide(env, "ls -la", record=True)
    assert not ledger.path(env.home).exists()


def test_ledger_command_is_cut_to_1000(env):
    cmd = "npm install " + "x" * 5000
    decide(env, cmd, record=True)
    assert only_event(env)["command"] == cmd[:1000]
    ledger.path(env.home).unlink()
    write_cfg(env.home, mode="log")
    cmd = "echo " + "y" * 5000 + " > lib/new.py"
    decide(env, cmd, record=True)
    ev = only_event(env)
    assert ev["command"] == cmd[:1000] and len(ev["command"]) == 1000


def test_without_record_nothing_is_written(env):
    for mode in ("deny", "log", "off"):
        write_cfg(env.home, mode=mode)
        before = tree(env.home)
        for cmd in list(CELLS.values()) + [heredoc("lib/tracked.py", 41)]:
            decide(env, cmd)
        assert tree(env.home) == before
    assert not ledger.path(env.home).exists()


# ── fail-open ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sid", ["lead-2", "../../x", ""])
def test_bad_or_unregistered_session_writes_nothing(env, sid):
    before = tree(env.tmp)
    res = decide(env, "echo 1 > ~/.pi-lead/config.json", sid=sid, record=True)
    assert res["verdict"] == "allow"
    assert tree(env.tmp) == before


ODD = [None, 5, 3.5, b"echo x > lib/new.py", "a" * (1 << 20), "echo x > lib/n\x00ew.py", "\x00" * 10,
       "cat <<EOF > lib/new.py\nunterminated"]


@pytest.mark.parametrize("cmd", ODD, ids=["none", "int", "float", "bytes", "1mb", "nul-path", "nuls", "unterminated"])
def test_decide_never_raises(env, cmd):
    res = decide(env, cmd, record=True)
    assert set(res) == {"verdict", "mode", "reason", "targets", "rule", "parsed"}
    assert res["verdict"] in ("allow", "log", "block")
    if not isinstance(cmd, str) or "\x00" in cmd or cmd.startswith("a"):
        assert res["verdict"] == "allow"


def test_decide_never_raises_on_bad_home_or_cwd(env):
    assert bashgate.decide(None, SID, "echo x > y", "/")["verdict"] == "allow"
    assert bashgate.decide(env.home, SID, "echo x > y", None)["verdict"] in ("allow", "block", "log")
    assert bashgate.decide(env.home, SID, "echo x > y", str(env.tmp / "missing"))["verdict"] == "allow"
    assert bashgate.decide(env.tmp / "no-home", SID, "echo x > y", str(env.repo))["verdict"] == "allow"


def test_git_is_the_real_one_from_refs_git_path(env, monkeypatch):
    """A `git` first on PATH that logs every call and writes a marker for any call but `--version`, which
    it fails (so refs.git_path() rejects it when it vets candidates): the gate's `ls-files` calls go to the
    real git, so no marker appears and the only call the fake ever saw is the resolver's `--version`."""
    d = env.tmp / "marker-bin"
    d.mkdir()
    log, marker = env.tmp / "fake-git.log", env.tmp / "fake-git-ran"
    (d / "git").write_text(f"#!/bin/sh\necho \"$*\" >> '{log}'\n"
                           f"[ \"$1\" = --version ] && exit 1\ntouch '{marker}'\nexit 0\n")
    (d / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    refs._resolved.clear()
    try:
        assert decide(env, "echo x > lib/new.py")["verdict"] == "block"  # real git said: new beside tracked
        assert decide(env, heredoc("lib/tracked.py", 41))["verdict"] == "block"
    finally:
        refs._resolved.clear()
    assert not marker.exists()
    assert set(log.read_text().splitlines()) <= {"--version"}


# ── the shared command table (read by the TypeScript tests in the next packet) ───────────────────
def test_command_table(env):
    table = json.loads(TABLE.read_text())
    assert isinstance(table, list) and len(table) >= 30
    assert sum(1 for row in table if row["verdict"] == "allow") >= 10
    for row in table:
        assert set(row) == {"command", "verdict", "mode"}
        write_cfg(env.home, mode=row["mode"])
        res = decide(env, row["command"])
        assert res["verdict"] == row["verdict"], row


# ══ second round: the control files however cased, every word for them, and commands that could not be read ══
FIRST_ROUND_ROWS = 36  # tests/bash-gate-commands.json's first 36 rows are the first round's, untouched
FIRST_ROUND_SHA256 = "316e3eadbc0cfab49959c5b465e2c2c0e7a1c111b1ffbb3088ec05aa1d8778bc"
CONTROL_NAMES = ["config.json", "ledger.jsonl", "leads/lead-1.route"]
KIND = {"config.json": "settings", "ledger.jsonl": "ledger", "leads/lead-1.route": "grace file"}


def sha(cmd):
    import hashlib
    return hashlib.sha256(cmd.encode("utf-8", errors="replace")).hexdigest()


def cased(rel, how):
    return {"lower": rel.lower(), "upper": rel.upper(), "mixed": "".join(
        c.upper() if i % 2 else c.lower() for i, c in enumerate(rel))}[how]


TARGET_REASON = ("pi-lead lead gate: this bash command writes {path}, a pi-lead control file ({kind}) — a change to "
                 "it needs a human's word; ask, or /pilead:route retain \"<reason>\" and retry within {grace}.")
NAMES_REASON = ("pilead: this command names {path}, which controls the lead gate, and the gate cannot confirm it only "
                "reads it — use a plain read (cat, head, grep, jq, …) or leave the change to the human")


def grace_min(seconds):
    """The block reason's grace, for a whole number of minutes (write_cfg's 600 → `10 min`)."""
    assert seconds % 60 == 0, seconds
    return f"{seconds // 60} min"


def names_control(cmd):
    low = cmd.lower()
    return any(n in low for n in ("config.json", "ledger.jsonl", ".route"))


def control_hit(res):
    return next((t for t in res["targets"] if t["hit"] and t["control"]), None)


def blocks_control(res, kind, grace=600):
    """`grace` is the grace_seconds the gate read: write_cfg's 600 unless the test says otherwise."""
    assert res["verdict"] == "block", res
    assert control_hit(res)["control"] == kind, res
    path = control_hit(res)["path"]
    assert res["reason"] in (TARGET_REASON.format(path=path, kind=kind, grace=grace_min(grace)),
                             NAMES_REASON.format(path=path)), res
    return res


@pytest.fixture
def cenv(env):
    """env, plus a symlink to home (tmp/homelink) and the other control files present."""
    (env.tmp / "homelink").symlink_to(env.home)
    (env.home / "ledger.jsonl").write_text("")
    return env


# ── the Context table's rows ─────────────────────────────────────────────────────────────────────
def test_context_row1_config_in_upper_case_with_config_absent(env):
    (env.home / "config.json").unlink()   # the row as found: no config.json, so the mode is the default (`log`)
    assert bashgate.control_kind(str(env.real_home / "CONFIG.JSON"), env.home) == "settings"
    res = blocks_control(decide(env, "echo x > ~/.pi-lead/CONFIG.JSON"), "settings", config.DEFAULTS["grace_seconds"])
    assert control_hit(res)["path"] == str(env.real_home / "CONFIG.JSON") and control_hit(res)["new_file"] is True


def test_context_row2_touch_opens_no_grace_window(env):
    for mode in ("deny", "log"):
        write_cfg(env.home, mode=mode)
        blocks_control(decide(env, f"touch ~/.pi-lead/leads/{SID}.route"), "grace file")
    assert not (env.home / "leads" / f"{SID}.route").exists()


@pytest.mark.parametrize("cmd", ["echo 'x > ~/.pi-lead/config.json", "echo x > ~/.pi-lead/config.json\x00",
                                 "echo 'x > notes.txt", "echo x > lib/new\x00.py"])
def test_context_row3_an_unread_command_says_so(env, cmd):
    res = decide(env, cmd)
    assert res["parsed"] is False
    assert res["verdict"] == ("block" if names_control(cmd) else "allow")


def test_context_row4_readme_names_the_unseen_shapes_and_parsed_no():
    text = (ROOT / "README.md").read_text()
    limits = text[text.index("Limits, in plain words"):]
    limits = limits[:limits.index("\n\n")]
    for shape in ("`truncate`", "`install`", "`ln -sf`", "`touch`", "`bash -c"):
        assert shape in limits, shape
    assert "every word" in limits
    assert "`parsed: no`" in text[text.index("## Lead gate"):text.index("## Fence")]


# ── change 1: however the name is cased ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("how", ["lower", "upper", "mixed"])
@pytest.mark.parametrize("rel", CONTROL_NAMES)
def test_control_file_in_any_case(cenv, rel, how):
    e, name = cenv, cased(rel, how)
    assert bashgate.control_kind(str(e.real_home / name), e.home) == KIND[rel]
    for cmd, cwd in [(f"echo x > {e.home / name}", e.repo),              # absolute
                     (f"echo x >> ~/.pi-lead/{name}", e.repo),           # through ~
                     (f"echo x > {e.tmp / 'homelink' / name}", e.repo),  # through a symlinked home
                     (f"echo x > {name}", e.home),                       # relative
                     (f"echo x > ../{name}", e.home / "leads"),          # with ..
                     (f"touch ~/.pi-lead/{name}", Path("/"))]:           # a word, not a parser target
        blocks_control(decide(e, cmd, cwd=cwd), KIND[rel])


@pytest.mark.parametrize("how", ["lower", "upper", "mixed"])
def test_control_file_through_links_in_any_case(cenv, how):
    e = cenv
    (e.repo / "cfg").symlink_to(e.home / cased("config.json", how))   # a symlinked file, cased
    blocks_control(decide(e, "echo x > cfg"), "settings")
    blocks_control(decide(e, "truncate -s 0 cfg"), "settings")
    os.link(e.home / "ledger.jsonl", e.tmp / cased("hard.jsonl", how))  # a hard link, any name
    blocks_control(decide(e, f"echo x >> {e.tmp / cased('hard.jsonl', how)}"), "ledger")
    blocks_control(decide(e, f"truncate -s 0 {e.tmp / cased('hard.jsonl', how)}"), "ledger")


@pytest.mark.parametrize("rel", ["leads/x.inbox.md", "sessions/s/packet-0002.md", "CONFIG.JSON.bak",
                                 "leads/x.route.md", "notes/config.json", "Leads.jsonl", "leads/sub/x.route"])
def test_near_names_are_not_control_files(cenv, rel):
    assert bashgate.control_kind(str(cenv.real_home / rel), cenv.home) is None
    assert decide(cenv, f"touch {cenv.home / rel}")["verdict"] == "allow"


# ── change 2: every word, for the control files ──────────────────────────────────────────────────
VERBS = {  # the shapes the parser does not see as writes (cp and mv it does), aimed at the control file
    "touch": "touch {p}", "truncate": "truncate -s 0 {p}", "install": "install -m 644 lib/tracked.py {p}",
    "ln-sf": "ln -sf /tmp/elsewhere {p}", "rm": "rm -f {p}", "mv": "mv lib/scratch.py {p}",
    "cp": "cp lib/scratch.py {p}"}


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("verb", list(VERBS))
@pytest.mark.parametrize("rel", CONTROL_NAMES)
def test_every_verb_aimed_at_a_control_file(cenv, verb, rel, mode):
    e = cenv
    write_cfg(e.home, mode=mode)
    before = tree(e.home)
    spellings = [
        (str(e.home / rel), e.repo),                                      # absolute
        (os.path.relpath(e.home / rel, e.repo), e.repo),                  # relative (../user/.pi-lead/…)
        (str(e.tmp / "homelink" / rel), e.repo),                          # through a symlinked home
        (f"~/.pi-lead/{rel}", e.repo),                                    # through ~
    ]
    for path, cwd in spellings:
        cmd = VERBS[verb].format(p=path).replace("lib/", f"{e.repo}/lib/")
        res = blocks_control(decide(e, cmd, cwd=cwd, record=False), KIND[rel])
        assert control_hit(res)["path"].lower() == str(e.real_home / rel).lower(), (cmd, res)
    assert tree(e.home) == before


def test_every_verb_in_mode_off_is_allowed(cenv):
    write_cfg(cenv.home, mode="off")
    for verb in VERBS.values():
        res = decide(cenv, verb.format(p="~/.pi-lead/config.json"))
        assert res["verdict"] == "allow" and res["targets"] == []


def test_grace_open_skips_the_word_check(cenv):
    set_grace(cenv, 5)
    assert decide(cenv, "rm ~/.pi-lead/ledger.jsonl")["verdict"] == "allow"


READS = {"cat": "cat {p}", "head": "head -n 3 {p}", "tail": "tail -f {p}", "less": "less {p}", "more": "more {p}",
         "wc": "wc -l {p}", "grep": "grep -n mode {p}", "ls": "ls -la {p}", "stat": "stat {p}", "file": "file {p}",
         "jq": "jq . {p}", "diff": "diff {p} lib/tracked.py", "cmp": "cmp {p} lib/tracked.py"}


def test_the_plain_reads_are_one_constant():
    assert bashgate.PLAIN_READS == frozenset(READS) | {"test", "[", "echo", "realpath", "readlink", "basename", "dirname"}


@pytest.mark.parametrize("prog", list(READS))
@pytest.mark.parametrize("rel", CONTROL_NAMES)
def test_a_plain_read_of_a_control_file(cenv, prog, rel):
    e = cenv
    (e.home / rel).touch()
    t = time.time() - 3600
    os.utime(e.home / rel, (t, t))  # an old grace file: the window stays closed
    for p in (f"~/.pi-lead/{rel}", str(e.tmp / "homelink" / cased(rel, "upper"))):
        cmd = READS[prog].format(p=p)
        assert decide(e, cmd)["verdict"] == "allow", cmd                                 # alone
        assert decide(e, "sudo " + cmd)["verdict"] == "allow", cmd                       # after a prefix
        assert decide(e, cmd + " < " + p)["verdict"] == "allow", cmd                     # input redirect: a read
        assert decide(e, cmd + " > /tmp/copy.txt")["verdict"] == "allow", cmd            # onto another file
        for op in (">", ">>", ">|", "&>", "<>", ">&"):                                   # onto the control file
            blocks_control(decide(e, f"{cmd} {op} {p}"), KIND[rel])
        blocks_control(decide(e, f"{cmd} 1<>{p}"), KIND[rel])


@pytest.mark.parametrize("cmd,verdict", [
    ("cat ~/.pi-lead/config.json | tee /tmp/x.txt", "log"),      # reads, then tee writes elsewhere (verb rule)
    ("cat ~/.pi-lead/config.json | jq .mode", "allow"),
    ("grep -c . ~/.pi-lead/ledger.jsonl; ls ~/.pi-lead/leads", "allow"),
    ("cat a && touch ~/.pi-lead/leads/s.route", "block"),
    ("cat ~/.pi-lead/config.json && rm ~/.pi-lead/config.json", "block"),
    ("ls ~/.pi-lead/leads/lead-1.route || touch ~/.pi-lead/leads/lead-1.route", "block"),
    ("cat ~/.pi-lead/config.json | sponge ~/.pi-lead/config.json", "block"),
    ("(cd /tmp; touch ~/.pi-lead/ledger.jsonl)", "block"),
    ("echo $(touch ~/.pi-lead/leads/s.route)", "block"),
    ("cat ~/.pi-lead/config.json > /tmp/c && vi /tmp/c", "allow"),
    ("echo ~/.pi-lead/config.json", "allow"),                    # third round: `echo` is a plain read
    ("dd if=/dev/zero of=~/.pi-lead/config.json", "block"),       # a word's `=` part
    ("[ -f ~/.pi-lead/config.json ] && echo yes", "allow"),      # third round: `[` is a plain read
])
def test_two_part_commands_are_judged_part_by_part(cenv, cmd, verdict):
    assert decide(cenv, cmd)["verdict"] == verdict, cmd


def test_a_control_word_decides_over_an_earlier_other_hit(cenv):
    write_cfg(cenv.home, mode="log")
    res = decide(cenv, "echo x > lib/new.py; touch ~/.pi-lead/leads/lead-1.route", record=True)
    blocks_control(res, "grace file")
    assert [(t["control"], t["hit"]) for t in res["targets"]] == [(None, True), ("grace file", True)]
    ev = only_event(cenv)
    assert ev == {"event": "blocked", "session_id": SID, "file_path": str(cenv.real_home / "leads" / f"{SID}.route"),
                  "lines": None, "new_file": True, "vector": "bash", "control": "grace file"}


def test_a_parser_target_that_is_a_control_file_is_not_listed_twice(cenv):
    write_cfg(cenv.home, mode="log")
    res = decide(cenv, "echo x > lib/new.py; echo y > ~/.pi-lead/config.json")
    assert [t["path"] for t in res["targets"]] == [str(cenv.real_repo / "lib" / "new.py"),
                                                   str(cenv.real_home / "config.json")]
    blocks_control(res, "settings")


def test_other_paths_are_judged_as_in_the_first_round(cenv):
    # the word check touches control files only: other files a command names are not looked at
    for cmd in ("touch lib/new.py", "rm lib/tracked.py", "truncate -s 0 lib/tracked.py", "ln -sf x lib/new.py",
                "install -m 644 a lib/new.py", "echo lib/new.py", f"touch {cenv.home / 'leads' / 'x.inbox.md'}"):
        res = decide(cenv, cmd)
        assert (res["verdict"], res["targets"], res["parsed"]) == ("allow", [], True), cmd


def test_split_parts():
    assert bashgate.split_parts("cat 'a b' | tee -a \"c\" > d; x=1 sudo -u r e 2>&1 <<EOF\nbody words\nEOF") == [
        ("cat", ["cat", "a b"], []), ("tee", ["tee", "-a", "c", "d"], ["d"]), ("r", ["x=1", "sudo", "-u", "r", "e", "2", "1"], ["1"])]
    assert bashgate.split_parts("a <> f; b < g") == [("a", ["a", "f"], ["f"]), ("b", ["b", "g"], [])]
    for bad in ("echo 'x", 'echo "x', "echo x\x00", None, 5, b"ls"):
        assert bashgate.split_parts(bad) is None
    assert bashgate.split_parts("") == [] and bashgate.split_parts("   ") == []


# ── change 3: a command that could not be read says so ───────────────────────────────────────────
UNREAD_CONTROL = [
    ("echo 'x > ~/.pi-lead/config.json", "settings", "config.json"),
    ("printf \"%s y >> ~/.pi-lead/LEDGER.JSONL", "ledger", "LEDGER.JSONL"),
    ("touch ~/.pi-lead/leads/abc.route 'oops", "grace file", "leads/abc.route"),
    ("echo x > ~/.pi-lead/Config.Json\x00", "settings", "Config.Json"),
]


@pytest.mark.parametrize("cmd,kind,rel", UNREAD_CONTROL)
def test_unread_command_naming_a_control_file_is_the_control_row(cenv, cmd, kind, rel):
    for mode in ("deny", "log"):
        write_cfg(cenv.home, mode=mode)
        res = blocks_control(decide(cenv, cmd), kind)
        assert res["parsed"] is False and control_hit(res)["path"] == str(cenv.real_home / rel)
    write_cfg(cenv.home, mode="off")
    res = decide(cenv, cmd)
    assert (res["verdict"], res["parsed"]) == ("allow", False)


def test_unread_command_naming_the_home_path_not_pi_lead(tmp_path, monkeypatch):
    user, home = tmp_path / "u", tmp_path / "state-home"
    register(home)
    write_cfg(home, mode="deny")
    monkeypatch.setenv("HOME", str(user))
    res = bashgate.decide(home, SID, f"echo 'x > {home}/Ledger.jsonl", str(tmp_path), False)
    blocks_control(res, "ledger")
    # the name alone, with neither the home's path nor `.pi-lead`, is not enough
    res = bashgate.decide(home, SID, "echo 'x > ledger.jsonl", str(tmp_path), False)
    assert (res["verdict"], res["parsed"]) == ("allow", False)


@pytest.mark.parametrize("cmd", ["echo 'x > notes.txt", "echo x > lib/new\x00.py",
                                 "echo 'config.json and ledger.jsonl'x \"", "\x00"])
def test_unread_command_naming_no_control_file_is_as_before(cenv, cmd):
    res = decide(cenv, cmd, record=True)
    assert (res["verdict"], res["targets"], res["parsed"]) == ("allow", [], False), cmd
    assert only_event(cenv) == {"event": "gate_unread", "session_id": SID, "command_sha": sha(cmd), "parsed": False}


def test_unread_command_is_recorded_with_parsed_false(cenv):
    decide(cenv, "echo 'x > ~/.pi-lead/config.json", record=True)
    assert only_event(cenv) == {"event": "blocked", "session_id": SID, "file_path": str(cenv.real_home / "config.json"),
                                "lines": None, "new_file": False, "vector": "bash", "control": "settings",
                                "parsed": False}
    ledger.path(cenv.home).write_text("")
    decide(cenv, "echo x | tee 'out.txt", record=True)                 # the verb log, from raw text
    assert only_event(cenv) == {"event": "would_have_blocked", "session_id": SID,
                                "command": "echo x | tee 'out.txt", "rule": "tee-mutation", "parsed": False}


def test_parsed_is_true_for_a_command_that_was_read(cenv):
    for cmd in ("ls", "", "echo x > lib/new.py", "cat <<EOF > lib/new.py\nunterminated"):
        assert decide(cenv, cmd)["parsed"] is True, cmd
    set_grace(cenv, 5)
    assert decide(cenv, "ls")["parsed"] is True and decide(cenv, "echo 'x")["parsed"] is False
    write_cfg(cenv.home, mode="off")
    assert decide(cenv, "ls")["parsed"] is True and decide(cenv, "echo 'x")["parsed"] is False


def test_parsed_is_none_for_a_session_that_is_not_a_lead_and_false_on_an_error(cenv, monkeypatch):
    assert decide(cenv, "echo 'x", sid="lead-2")["parsed"] is None
    monkeypatch.setattr(bashgate, "_decide", lambda *a: 1 / 0)
    assert decide(cenv, "ls") == {"verdict": "allow", "mode": None, "reason": None, "targets": [], "rule": None,
                                  "parsed": False}


# ── the command table: the first round's rows are untouched and judged as before ─────────────────
def test_first_round_rows_are_untouched_and_keep_their_verdicts(env):
    import hashlib
    table = json.loads(TABLE.read_text())
    first = table[:FIRST_ROUND_ROWS]
    blob = json.dumps(first, sort_keys=True, ensure_ascii=True).encode()
    assert hashlib.sha256(blob).hexdigest() == FIRST_ROUND_SHA256
    plain = [row for row in first if not names_control(row["command"])]
    assert len(plain) == 31
    for row in plain:
        write_cfg(env.home, mode=row["mode"])
        res = decide(env, row["command"])
        assert (res["verdict"], res["parsed"]) == (row["verdict"], True), row


def test_command_table_second_round_rows(env):
    """Judged in the same fixture as the first round's rows (test_command_table runs them all as well)."""
    table = json.loads(TABLE.read_text())[FIRST_ROUND_ROWS:]
    assert len(table) >= 20
    for row in table:
        write_cfg(env.home, mode=row["mode"])
        res = decide(env, row["command"])
        assert res["verdict"] == row["verdict"], row
        assert res["parsed"] is not ("'" in row["command"] or "\x00" in row["command"]), row


# ══ third round: what the gate cannot read literally, home, `leads/` and the lead's record, the refusal's
#    wording, `gate_unread`, `lines: null` ══════════════════════════════════════════════════════════════
# The Context table's rows a–h: each wrote a control file (or removed home, `leads/` or the lead's record) with
# the verdict `allow` before this round. (row, command, path under home the refusal names, kind)
CONTEXT_AH = [
    ("a", "echo x > $HOME/.pi-lead/config.json", "config.json", "settings"),
    ("a", "echo x > ${HOME}/.pi-lead/config.json", "config.json", "settings"),
    ("b", "echo x > $PI_LEAD_HOME/config.json", "config.json", "settings"),
    # p20g 4b: a `cd` is a plain read for the word check, so the file named after it decides (was `home` / `leads`)
    ("c", "cd ~/.pi-lead && echo x > config.json", "config.json", "settings"),
    ("c", "cd ~/.pi-lead/leads && touch s.route", "leads/s.route", "grace file"),
    ("d", "touch ~/.pi-lead/leads/*.route", "leads/*.route", "grace file"),
    ("e", "eval 'echo x > ~/.pi-lead/config.json'", "config.json", "settings"),
    ("e", "bash -c 'echo x > ~/.pi-lead/config.json'", "config.json", "settings"),
    ("e", "sh -c \"echo x > ~/.pi-lead/config.json\"", "config.json", "settings"),
    ("e", "bash <<EOF\necho x > ~/.pi-lead/config.json\nEOF", "config.json", "settings"),
    ("f", "node -e \"require('fs').writeFileSync('/Users/x/.pi-lead/config.json','x')\"", "config.json", "settings"),
    ("f", "python3 -c \"open('/Users/x/.pi-lead/config.json','w').write('x')\"", "config.json", "settings"),
    ("g", "find ~/.pi-lead -name '*.route' -delete", "", "home"),
    ("g", "rm -rf ~/.pi-lead/leads", "leads", "leads"),
    ("g", "rm -rf ~/.pi-lead", "", "home"),
    ("h", f"rm ~/.pi-lead/leads/{SID}.json", f"leads/{SID}.json", "lead record"),
]
NEW_READS = {"test": "test -f {p}", "[": "[ -f {p} ]", "echo": "echo {p}", "realpath": "realpath {p}",
             "readlink": "readlink {p}", "basename": "basename {p}", "dirname": "dirname {p}"}
FOUR_CONTROLS = CONTROL_NAMES + [f"leads/{SID}.json"]
KIND4 = dict(KIND, **{f"leads/{SID}.json": "lead record"})


def names_blocks(res, path, kind):
    """Blocked as a control file found among the words or by the raw text: the third round's refusal, whole."""
    assert res["verdict"] == "block", res
    hit = control_hit(res)
    assert (hit["path"], hit["control"]) == (str(path), kind), res
    assert res["reason"] == NAMES_REASON.format(path=path), res
    return res


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("row,cmd,rel,kind", CONTEXT_AH, ids=[f"{r[0]}{i}" for i, r in enumerate(CONTEXT_AH)])
def test_context_rows_a_to_h_are_blocked(cenv, row, cmd, rel, kind, mode):
    e = cenv
    write_cfg(e.home, mode=mode)
    before = tree(e.home)
    res = names_blocks(decide(e, cmd), os.path.join(e.real_home, rel) if rel else e.real_home, kind)
    assert res["parsed"] is True and res["mode"] == mode
    assert tree(e.home) == before


@pytest.mark.parametrize("row,cmd,rel,kind", CONTEXT_AH, ids=[f"{r[0]}{i}" for i, r in enumerate(CONTEXT_AH)])
def test_context_rows_a_to_h_record_blocked_with_lines_null(cenv, row, cmd, rel, kind):
    e = cenv
    write_cfg(e.home, mode="log")
    decide(e, cmd, record=True)
    path = os.path.join(e.real_home, rel) if rel else str(e.real_home)
    ev = only_event(e)
    assert ev == {"event": "blocked", "session_id": SID, "file_path": path, "lines": None,
                  "new_file": not os.path.exists(path), "vector": "bash", "control": kind}


@pytest.mark.parametrize("row,cmd,rel,kind", CONTEXT_AH, ids=[f"{r[0]}{i}" for i, r in enumerate(CONTEXT_AH)])
def test_context_rows_a_to_h_in_mode_off_and_in_grace_are_as_before(cenv, row, cmd, rel, kind):
    # the decision table is unchanged: mode `off` and an open grace window look for no target at all
    write_cfg(cenv.home, mode="off")
    assert decide(cenv, cmd)["verdict"] in ("allow", "log")
    write_cfg(cenv.home, mode="deny")
    set_grace(cenv, 5)
    assert decide(cenv, cmd)["verdict"] in ("allow", "log")


# ── change 1: a part the gate cannot read literally is judged by its raw text ────────────────────────
@pytest.mark.parametrize("prog", sorted(bashgate.INTERPRETERS) + ["python3.11", "/bin/bash", "sudo bash", "env -i bash",
                                                                  "X=1 bash"])
def test_a_command_word_that_runs_text_is_not_literal(cenv, prog):
    # the quoted text is one word, which resolves to no path: only the raw-text rule sees the control file
    cmd = f'{prog} "echo x > ~/.pi-lead/config.json"'
    names_blocks(decide(cenv, cmd), cenv.real_home / "config.json", "settings")


@pytest.mark.parametrize("cmd", ['printf "echo x > ~/.pi-lead/config.json"',
                                 'git commit -m "fix ~/.pi-lead/config.json handling"',
                                 'grep -r "~/.pi-lead/config.json" lib'])
def test_a_literal_part_is_not_judged_by_its_raw_text(cenv, cmd):
    # every word is literal and none resolves to a control file: the raw text is not looked at
    assert decide(cenv, cmd)["verdict"] == "allow", cmd


@pytest.mark.parametrize("cmd,rel,kind", [
    ("touch ~/.pi-lead/leads/?.route", "leads/?.route", "grace file"),
    ("rm ~/.pi-lead/leads/[a-z]*.json", "leads/[a-z]*.json", "lead record"),
    ("rm ~/.pi-lead/leads/*", "leads/*", "leads"),
    ("cp x `echo ~/.pi-lead`/ledger.jsonl", "", "home"),          # the word `~/.pi-lead`, in a `cp` part
    ("truncate -s 0 $(echo ~/.pi-lead)/LEDGER.JSONL", "LEDGER.JSONL", "ledger"),
    ("echo x | tee $PI_LEAD_HOME/leads/lead-1.route", "leads/lead-1.route", "grace file"),
    ("cat $(echo ~/.pi-lead/leads)/x.route > /dev/null && touch $(dirname $PI_LEAD_HOME/x)/Leads/y.route",
     "leads/y.route", "grace file"),
    ("sed -i s/a/b/ ~/.pi-lead/conf*.json", None, None),   # a glob that spells no control-file name: not seen
])
def test_variables_globs_and_substitutions(cenv, cmd, rel, kind):
    res = decide(cenv, cmd)
    if rel is None:
        assert (res["verdict"], res["rule"]) == ("log", "sed-inplace"), res   # the verb log only (README Limits)
    else:
        names_blocks(res, os.path.join(cenv.real_home, rel) if rel else cenv.real_home, kind)


@pytest.mark.parametrize("cmd,verdict", [
    ("cd /tmp && echo x > config.json", "allow"),                  # the cd target names no home
    ("cd /tmp; ls; echo hi", "allow"),
    ("cd \"$D\" && echo x > config.json", "block"),                # a cd the gate cannot read counts as home
    ("cd `pwd` && touch s.route", "block"),
    ("cd $PI_LEAD_HOME && rm ledger.jsonl", "block"),
    ("pushd ~/.pi-lead/leads && touch x.route", "block"),
    (f"cd ~/.pi-lead/leads && rm {SID}.json", "block"),
    ("cd $REPO && npm run lint", "allow"),                           # nothing after it names a control file
    ("cd \"$D\" && cat config.json", "allow"),                      # a plain read, no redirect
    ("cd \"$D\" && cat a > config.json", "block"),                  # … redirected onto it
])
def test_cd_and_pushd(cenv, cmd, verdict):
    # cwd outside any repository, so the first round's rule has no hit to find
    assert decide(cenv, cmd, cwd=cenv.tmp)["verdict"] == verdict, cmd


def test_a_literal_cd_target_is_a_base_for_the_words_after_it(cenv):
    (cenv.tmp / "elsewhere").mkdir()
    res = decide(cenv, f"cd {cenv.tmp / 'homelink' / 'leads'} && rm -f {SID}.json", cwd=cenv.tmp / "elsewhere")
    names_blocks(res, cenv.real_home / "leads" / f"{SID}.json", "lead record")  # p20g 4b: the file after the cd
    write_cfg(cenv.home, mode="deny")
    res = decide(cenv, f"cd {cenv.tmp} && rm -f homelink/leads/{SID}.json")
    names_blocks(res, cenv.real_home / "leads" / f"{SID}.json", "lead record")


@pytest.mark.parametrize("cmd,verdict", [
    ("bash <<'EOF'\nrm ~/.pi-lead/ledger.jsonl\nEOF", "block"),
    ("python3 - <<EOF\nopen('/u/.pi-lead/ledger.jsonl', 'a').write('x')\nEOF", "block"),
    ("tee notes.md <<EOF\nsee ~/.pi-lead/config.json\nEOF", "block"),   # strict: the body is text to the gate
    ("cat > notes.md <<EOF\nsee ~/.pi-lead/config.json\nEOF", "log"),    # a plain read redirected elsewhere: verb log
    ("cat <<EOF > ~/.pi-lead/config.json\n{}\nEOF", "block"),
    ("bash <<EOF\necho hi\nEOF", "log"),                                  # names no control file: the verb log
])
def test_here_documents(cenv, cmd, verdict):
    # cwd outside any repository, so the first round's rule has no hit to find
    assert decide(cenv, cmd, cwd=cenv.tmp)["verdict"] == verdict, cmd


def test_a_plain_read_holding_a_backquote_is_not_a_plain_read(cenv):
    names_blocks(decide(cenv, "echo `touch ~/.pi-lead/leads/x.route`"), cenv.real_home / "leads" / "x.route",
                 "grace file")
    names_blocks(decide(cenv, 'cat "`rm ~/.pi-lead/config.json`"'), cenv.real_home / "config.json", "settings")


def test_other_paths_that_are_not_literal_keep_their_verdicts(cenv):
    write_cfg(cenv.home, mode="deny", logging=False)  # the verb log off: only targets could change a verdict
    for cmd in ("echo x > $OUT", "sed -i s/a/b/ *.py", 'bash -c "echo x > lib/new.py"', "eval $CMD",
                "for f in *.py; do wc -l $f; done", "xargs rm < list.txt", "echo $HOME",
                "python3 -c 'print(1)'", "cd lib && touch new.py"):
        assert decide(cenv, cmd)["verdict"] == "allow", cmd


def test_raw_control_is_the_one_rule(cenv):
    h = cenv.home
    real = str(cenv.real_home)
    rc = bashgate.raw_control
    assert rc("echo x > $HOME/.pi-lead/CONFIG.json", h) == (real + "/CONFIG.json", "settings")
    assert rc("x ledger.jsonl y config.json .pi-lead", h) == (real + "/ledger.jsonl", "ledger")   # the earliest
    assert rc("rm $PI_LEAD_HOME/leads/abc.JSON", h) == (real + "/leads/abc.JSON", "lead record")
    assert rc("rm -r $pi_lead_home/LEADS/", h) == (real + "/leads", "leads")                      # any case
    assert rc("touch ~/.pi-lead/leads/abc.route", h) == (real + "/leads/abc.route", "grace file")  # file over `leads/`
    assert rc(f"echo x > {h}/config.json", h) == (real + "/config.json", "settings")               # home as typed
    assert rc(f"echo x > {real}/config.json", h) == (real + "/config.json", "settings")            # … resolved
    assert rc("echo x > config.json", h) is None                                                   # no home marker
    assert rc("echo x > config.json", h, cd_home=True) == (real + "/config.json", "settings")       # … but a cd's
    assert rc("ls ~/.pi-lead", h) is None and rc("", h) is None                                    # no name
    assert rc("rm leads.txt .pi-lead", h) is None


def test_not_literal_parts():
    parts = bashgate._split("ls a; ls $a; ls *.py; ls a?; ls [ab]; bash x; env ls; X=1 sudo python3.9 x; "
                            "cat <<EOF\nb\nEOF\necho ok")
    assert [bashgate._not_literal(p) for p in parts] == [False, True, True, True, True, True, True, True, True,
                                                         False]
    assert parts[8].segment.bodies == ["b"] and parts[8].heredoc and not parts[9].heredoc
    assert parts[9].segment.bodies == [] and parts[9].segment is not parts[8].segment
    # a substitution does not end a segment: the part before it keeps the text around it
    parts = bashgate._split("truncate -s 0 $(echo ~/.pi-lead)/LEDGER.JSONL; ls")
    assert [p.program for p in parts] == ["truncate", "echo", "LEDGER.JSONL", "ls"]  # a basename
    assert parts[0].segment is parts[1].segment is parts[2].segment is not parts[3].segment
    assert parts[0].segment.text() == "truncate -s 0 $ ( echo ~/.pi-lead ) /LEDGER.JSONL"


# ── change 2: the lead's record, home and `leads/` ─────────────────────────────────────────────────
@pytest.mark.parametrize("how", ["lower", "upper", "mixed"])
def test_the_lead_record_is_a_control_file(cenv, how):
    e, name = cenv, cased(f"leads/{SID}.json", how)
    assert bashgate.control_kind(str(e.real_home / name), e.home) == "lead record"
    for cmd, cwd in [(f"rm {e.home / name}", e.repo), (f"rm ~/.pi-lead/{name}", e.repo),
                     (f"rm {e.tmp / 'homelink' / name}", e.repo), (f"rm {name}", e.home),
                     (f"mv ../{name} /tmp/x", e.home / "leads"), (f"echo x > ~/.pi-lead/{name}", e.repo)]:
        blocks_control(decide(e, cmd, cwd=cwd), "lead record")
    assert not (e.home / "leads" / f"{SID}.json").read_text() == ""


def test_the_lead_record_through_a_hard_link(cenv):
    os.link(cenv.home / "leads" / f"{SID}.json", cenv.tmp / "rec")
    blocks_control(decide(cenv, f"truncate -s 0 {cenv.tmp / 'rec'}"), "lead record")


@pytest.mark.parametrize("rel", ["leads/x.inbox.md", "leads/sub/x.json", "leads.json", "notes/lead-1.json",
                                 "leads/x.json.bak"])
def test_near_names_of_the_lead_record(cenv, rel):
    assert bashgate.control_kind(str(cenv.real_home / rel), cenv.home) is None
    assert decide(cenv, f"touch {cenv.home / rel}")["verdict"] == "allow"


@pytest.mark.parametrize("cmd,cwd,kind", [
    ("rm -rf ~/.pi-lead", None, "home"), ("rm -rf ~/.PI-LEAD/", None, "home"), ("rm -rf {tmp}/homelink", None, "home"),
    ("mv ~/.pi-lead /tmp/x", None, "home"), ("chmod 000 ~/.pi-lead/leads", None, "leads"),
    ("rm -rf LEADS", "home", "leads"), ("find . -delete", "home", "home"), ("rm -rf ..", "leads", "home"),
    ("rm -rf ~/.pi-lead/leads/", None, "leads"), ("cp -r /tmp/x ~/.pi-lead/leads", None, "leads"),
])
def test_home_and_leads_directories(cenv, cmd, cwd, kind):
    cwd = {"home": cenv.home, "leads": cenv.home / "leads", None: cenv.repo}[cwd]
    res = decide(cenv, cmd.format(tmp=cenv.tmp), cwd=cwd)
    assert res["verdict"] == "block" and res["reason"] == NAMES_REASON.format(path=control_hit(res)["path"]), res
    assert control_hit(res)["control"] == kind, res
    assert os.path.realpath(control_hit(res)["path"]).lower() == \
        str(cenv.real_home if kind == "home" else cenv.real_home / "leads").lower(), res
    assert bashgate._Controls(cenv.home).dir_kind(str(cenv.real_home).upper()) == "home"


@pytest.mark.parametrize("cmd", ["ls ~/.pi-lead", "ls -la ~/.pi-lead/leads", "stat ~/.pi-lead", "file ~/.pi-lead/leads",
                                 "ls ~/.pi-lead | wc -l", "ls {tmp}/homelink", "ls ~/.pi-lead/sessions",
                                 "rm -rf ~/.pi-lead/sessions/x", "touch ~/.pi-lead/leads/x.inbox.md"])
def test_reading_home_and_leads_and_other_paths_under_home(cenv, cmd):
    assert decide(cenv, cmd.format(tmp=cenv.tmp))["verdict"] == "allow", cmd


# ── change 3: the plain reads, and the refusal's wording ───────────────────────────────────────────
@pytest.mark.parametrize("prog", list(NEW_READS))
@pytest.mark.parametrize("rel", FOUR_CONTROLS)
def test_the_new_plain_reads_of_a_control_file(cenv, prog, rel):
    e = cenv
    (e.home / rel).touch()
    t = time.time() - 3600
    os.utime(e.home / rel, (t, t))
    p = f"~/.pi-lead/{rel}"
    cmd = NEW_READS[prog].format(p=p)
    assert decide(e, cmd)["verdict"] == "allow", cmd                           # alone
    assert decide(e, cmd + " > /tmp/copy.txt")["verdict"] == "allow", cmd      # onto another file
    for op in (">", ">>", ">|"):                                               # onto the control file
        res = blocks_control(decide(e, f"{cmd} {op} {p}"), KIND4[rel])
        assert res["reason"] == TARGET_REASON.format(path=control_hit(res)["path"], kind=KIND4[rel], grace=grace_min(600))  # a parser target
    names_blocks(decide(e, f"{cmd} <> {p}"), e.real_home / rel, KIND4[rel])  # a word the parser does not see


@pytest.mark.parametrize("cmd", ["ls ~/.pi-lead", "cat ~/.pi-lead/ledger.jsonl | sort", "grep x $HOME/.pi-lead/ledger.jsonl",
                                 "grep x $HOME/.pi-lead/ledger.jsonl 2>/dev/null | wc -l",
                                 "grep -c . ${PI_LEAD_HOME}/ledger.jsonl > /tmp/n.txt",
                                 "test -f $HOME/.pi-lead/leads/lead-1.route && echo open"])
def test_the_acceptance_reads_are_allowed(cenv, cmd):
    res = decide(cenv, cmd)
    assert (res["verdict"], res["parsed"]) == ("allow", True), cmd


def test_a_plain_read_redirected_onto_a_control_file_it_cannot_resolve(cenv):
    names_blocks(decide(cenv, "grep x $HOME/.pi-lead/ledger.jsonl >> $HOME/.pi-lead/ledger.jsonl"),
                 cenv.real_home / "ledger.jsonl", "ledger")


def test_the_refusal_texts_whole(cenv):
    rh = cenv.real_home
    want = ("pilead: this command names %s, which controls the lead gate, and the gate cannot confirm it only reads "
            "it — use a plain read (cat, head, grep, jq, …) or leave the change to the human")
    # a word, the raw text, the raw text of a command that could not be read: the third round's text
    assert decide(cenv, "touch ~/.pi-lead/config.json")["reason"] == want % (rh / "config.json")
    assert decide(cenv, "echo x > $HOME/.pi-lead/config.json")["reason"] == want % (rh / "config.json")
    assert decide(cenv, "echo 'x > ~/.pi-lead/config.json")["reason"] == want % (rh / "config.json")
    assert decide(cenv, "rm -rf ~/.pi-lead")["reason"] == want % rh
    assert bashgate.names_reason("/x/y") == want % "/x/y"
    # a parser target: the first round's text, unchanged
    assert decide(cenv, "echo x > ~/.pi-lead/config.json")["reason"] == (
        f"pi-lead lead gate: this bash command writes {rh / 'config.json'}, a pi-lead control file (settings) — a "
        f"change to it needs a human's word; ask, or /pilead:route retain \"<reason>\" and retry within 10 min.")


# ── change 4: the ledger ─────────────────────────────────────────────────────────────────────────
def test_gate_unread_under_record(cenv):
    cmd = "echo 'x > notes.txt"
    res = decide(cenv, cmd, record=True)
    assert (res["verdict"], res["parsed"]) == ("allow", False)
    assert only_event(cenv) == {"event": "gate_unread", "session_id": SID, "command_sha": sha(cmd), "parsed": False}
    assert bashgate.command_sha(cmd) == sha(cmd) and len(sha(cmd)) == 64


@pytest.mark.parametrize("mode", ["deny", "log", "off"])
def test_gate_unread_in_every_mode_and_with_grace_open(cenv, mode):
    write_cfg(cenv.home, mode=mode)
    decide(cenv, "ls 'x", record=True)
    assert only_event(cenv)["event"] == "gate_unread"
    ledger.path(cenv.home).write_text("")
    set_grace(cenv, 5)
    decide(cenv, "ls \x00", record=True)
    assert only_event(cenv) == {"event": "gate_unread", "session_id": SID, "command_sha": sha("ls \x00"),
                                "parsed": False}


def test_no_gate_unread_without_record_or_for_a_command_that_was_read(cenv):
    before = tree(cenv.home)
    decide(cenv, "echo 'x > notes.txt")                        # without record: nothing written
    assert tree(cenv.home) == before
    decide(cenv, "ls -la", record=True)                        # read and allowed: no event, as before
    assert tree(cenv.home) == before
    decide(cenv, "echo 'x > notes.txt", sid="lead-2", record=True)   # not a lead: nothing looked at
    assert tree(cenv.home) == before


def test_an_unread_command_that_blocks_or_logs_writes_its_own_event_not_gate_unread(cenv):
    decide(cenv, "echo 'x > ~/.pi-lead/config.json", record=True)
    assert only_event(cenv)["event"] == "blocked"
    ledger.path(cenv.home).write_text("")
    decide(cenv, "npm install 'x", record=True)
    assert only_event(cenv) == {"event": "would_have_blocked", "session_id": SID, "command": "npm install 'x",
                                "rule": "npm-install", "parsed": False}


def test_lines_is_null_not_0_when_unknown(cenv):
    decide(cenv, "touch ~/.pi-lead/leads/lead-1.route", record=True)
    raw = ledger.path(cenv.home).read_text()
    assert '"lines": null' in raw and '"lines": 0' not in raw
    ledger.path(cenv.home).write_text("")
    decide(cenv, "echo x > lib/new.py", record=True)             # a parser target of unknown size
    assert '"lines": null' in ledger.path(cenv.home).read_text()
    ledger.path(cenv.home).write_text("")
    decide(cenv, heredoc("lib/tracked.py", 41), record=True)       # a known size is still the number
    assert only_event(cenv)["lines"] == 41


def test_an_unread_command_naming_leads_is_now_the_control_row(cenv):
    # moved from test_unread_command_naming_no_control_file_is_as_before: `leads/` is a control-file name now
    res = decide(cenv, 'cat "~/.pi-lead/leads/x.inbox.md', record=True)
    names_blocks(res, cenv.real_home / "leads" / "x.inbox.md", "leads")
    assert res["parsed"] is False and only_event(cenv)["event"] == "blocked"


# ── the command table: this round's rows ─────────────────────────────────────────────────────────
SECOND_ROUND_ROWS = 60
THIRD_ROUND_ROWS = 105


def test_command_table_third_round_rows(env):
    table = json.loads(TABLE.read_text())
    assert len(table[:SECOND_ROUND_ROWS]) == 60
    rows = table[SECOND_ROUND_ROWS:THIRD_ROUND_ROWS]  # p20g: the rows after these are p20g's (below)
    assert len(rows) == 45
    for row in rows:
        write_cfg(env.home, mode=row["mode"])
        res = decide(env, row["command"])
        assert (res["verdict"], res["parsed"]) == (row["verdict"], True), row
        if res["verdict"] == "block":
            assert control_hit(res) is not None, row


# ══ p20g: the control files in one table both languages read (tests/control-files.json), and two follow-ups in
#    the decision (4b): awk and sed are judged by their raw text, and `cd`/`pushd` are plain reads for the word check ══
CONTROL_FILES = Path(__file__).resolve().parent / "control-files.json"


def test_control_files_table_agrees_with_the_python_rule(env):
    table = json.loads(CONTROL_FILES.read_text())
    assert len(table) >= 12
    rels = {row["path"] for row in table}
    assert {"config.json", "ledger.jsonl", "leads/a.b.route", "leads/x.json", "leads/x.inbox.md", "config.json.bak",
            "sessions/s/config.json", "ledger.jsonl.1"} <= rels
    assert any(r.startswith("/") for r in rels) and any(row["kind"] == "grace file" for row in table)
    for row in table:
        assert set(row) == {"path", "kind"}
        assert row["kind"] in ("settings", "ledger", "grace file", "lead record", None), row
        p = row["path"] if row["path"].startswith("/") else str(env.home / row["path"])
        assert bashgate.control_kind(p, env.home) == row["kind"], row


def test_awk_joins_the_commands_that_run_text():
    assert {"awk", "gawk", "mawk", "sed"} <= bashgate.INTERPRETERS


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("prog", ["awk", "gawk", "mawk", "/usr/bin/awk", "sudo awk"])
def test_awk_writing_a_control_file_blocks(cenv, prog, mode):
    write_cfg(cenv.home, mode=mode)
    cmd = prog + " 'BEGIN{print \"x\" > \"~/.pi-lead/config.json\"}'"
    names_blocks(decide(cenv, cmd), cenv.real_home / "config.json", "settings")


def test_awk_with_no_control_file_is_as_before(cenv):
    write_cfg(cenv.home, mode="deny", logging=False)
    for cmd in ("awk '{print $1}' lib/tracked.py", "awk 'BEGIN{print 1 > \"/tmp/out.txt\"}'", "ls | awk '{print}'"):
        assert decide(cenv, cmd)["verdict"] == "allow", cmd


@pytest.mark.parametrize("cmd", ["sed -n 1,5p ~/.pi-lead/config.json", "sed -n 1p $HOME/.pi-lead/config.json",
                                 "sed -n '/warn/p' ~/.pi-lead/ledger.jsonl", "sed -ne 's/a/b/p' ~/.pi-lead/config.json",
                                 "sed --expression=1p ~/.pi-lead/leads/lead-1.route", "sed -E -n '$p' ~/.pi-lead/config.json",
                                 "sed -n 1p ~/.pi-lead/config.json > /tmp/copy.txt", "sed 'y/abc/xyz/' ~/.pi-lead/config.json",
                                 "sed -n 'b end; p; :end' ~/.pi-lead/config.json"])
def test_a_sed_that_writes_nothing_is_a_read(cenv, cmd):
    res = decide(cenv, cmd)
    assert (res["verdict"], res["parsed"]) == ("allow", True), cmd


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("cmd", ["sed 's/a/b/w ~/.pi-lead/config.json' lib/tracked.py",
                                 "sed -n '1W ~/.pi-lead/leads/lead-1.route' lib/tracked.py",
                                 "sed -n '/x/w ~/.pi-lead/ledger.jsonl' lib/tracked.py",
                                 "sed -e 1p -e 'w ~/.pi-lead/config.json' lib/tracked.py",
                                 "sed 'b end; w ~/.pi-lead/config.json' lib/tracked.py",
                                 "sed 's/a/b/e' ~/.pi-lead/config.json", "sed '1e rm x' ~/.pi-lead/config.json",
                                 "sed -f script.sed ~/.pi-lead/config.json", "sed --file=s.sed ~/.pi-lead/config.json",
                                 "sed -n 1p ~/.pi-lead/config.json >> ~/.pi-lead/config.json"])
def test_a_sed_that_writes_or_runs_blocks(cenv, cmd, mode):
    write_cfg(cenv.home, mode=mode)
    res = decide(cenv, cmd)
    assert res["verdict"] == "block" and control_hit(res) is not None, cmd


@pytest.mark.parametrize("cmd", ["sed -i s/a/b/ ~/.pi-lead/config.json", "sed -i.bak s/a/b/ ~/.pi-lead/config.json",
                                 "sed -Ei s/a/b/ ~/.pi-lead/config.json", "sed --in-place s/a/b/ ~/.pi-lead/config.json",
                                 "sed --in-place=.b s/a/b/ ~/.pi-lead/config.json",
                                 "sed -i -e s/a/b/ $HOME/.pi-lead/config.json"])
def test_an_in_place_sed_on_a_control_file_blocks(cenv, cmd):
    for mode in ("deny", "log"):
        write_cfg(cenv.home, mode=mode)
        res = decide(cenv, cmd)
        assert res["verdict"] == "block" and control_hit(res)["control"] == "settings", (cmd, mode)


@pytest.mark.parametrize("script,writes", [
    ("1,5p", False), ("/warn/p", False), ("s/a/b/g", False), ("s/w/W/2p", False), ("s|/w|/e|", False),
    ("y/we/ew/", False), ("$!N;P;D", False), ("/x/{p;n}", False), ("b end; p; :end", False), ("# w here\np", False),
    ("a write me", False), ("\\,w,p", False), ("0~3d", False), ("l 5", False), ("=", False),
    ("w out", True), ("1W out", True), ("s/a/b/w out", True), ("s/a/b/gw out", True), ("s/a/b/e", True),
    ("e ls", True), ("/x/ w out", True), ("p;w out", True), ("b end; w out", True), ("s/a", True), ("/unclosed", True),
    ("{w out\n}", True),
])
def test_sed_script_writes(script, writes):
    assert bashgate._sed_script_writes(script) is writes


@pytest.mark.parametrize("cmd,writes", [
    ("sed -n 1p f", False), ("sed -n -e 1p -e 2p f", False), ("sed -e1p f", False), ("sed -l 5 -n 1p f", False),
    ("sed -- 1p f", False), ("sed --expression 1p f", False), ("sed -nE 1p f", False),
    ("sed -i 1p f", True), ("sed -ni 1p f", True), ("sed -i.bak 1p f", True), ("sed --in-place 1p f", True),
    ("sed -f s f", True), ("sed --file s f", True), ("sed", True), ("sed -n", True), ("sed -e", True),
    ("sed -e 1p -e 'w x' f", True), ("sudo sed -n 1p f", False), ("X=1 sed -i 1p f", True),
])
def test_sed_writes(cmd, writes):
    part = bashgate._split(cmd)[0]
    assert bashgate._sed_writes(part) is writes, cmd


@pytest.mark.parametrize("cmd", ["cd ~/.pi-lead && ls", "cd ~/.pi-lead", "pushd ~/.pi-lead/leads && ls -la", "cd ~/.PI-LEAD/",
                                 "cd $HOME/.pi-lead && cat config.json", "cd ~/.pi-lead && sed -n 1p config.json",
                                 "cd ~/.pi-lead/leads; ls; cd -"])
def test_cd_and_pushd_are_plain_reads_for_the_word_check(cenv, cmd):
    for mode in ("deny", "log"):
        write_cfg(cenv.home, mode=mode)
        res = decide(cenv, cmd)
        assert (res["verdict"], res["parsed"]) == ("allow", True), (cmd, mode)


@pytest.mark.parametrize("cmd,rel,kind", [
    ("cd ~/.pi-lead && echo x > config.json", "config.json", "settings"),
    ("cd ~/.pi-lead && touch ledger.jsonl", "ledger.jsonl", "ledger"),
    ("cd ~/.pi-lead/leads && touch s.route", "leads/s.route", "grace file"),
    (f"pushd ~/.pi-lead/leads && rm -f {SID}.json", f"leads/{SID}.json", "lead record"),
    ("cd ~/.pi-lead && rm -rf leads", "leads", "leads"),
    ("cd ~/.pi-lead > ~/.pi-lead/config.json", "config.json", "settings"),
    ("cd ~/.pi-lead && sed -i s/a/b/ config.json", "config.json", "settings"),
])
def test_a_cd_target_still_marks_what_follows(cenv, cmd, rel, kind):
    for mode in ("deny", "log"):
        write_cfg(cenv.home, mode=mode)
        res = decide(cenv, cmd)
        assert res["verdict"] == "block", (cmd, mode)
        hit = control_hit(res)
        assert (hit["path"], hit["control"]) == (str(cenv.real_home / rel), kind), (cmd, res)


def test_command_table_p20g_rows(env):
    table = json.loads(TABLE.read_text())
    rows = table[THIRD_ROUND_ROWS:]
    assert len(rows) == 20
    for row in rows:
        assert set(row) == {"command", "verdict", "mode"}
        write_cfg(env.home, mode=row["mode"])
        res = decide(env, row["command"])
        assert (res["verdict"], res["parsed"]) == (row["verdict"], True), row
        if res["verdict"] == "block":
            assert control_hit(res) is not None, row

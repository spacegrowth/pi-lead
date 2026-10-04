"""`pilead gate` — bin/pilead driven with the command on stdin: each verdict's line and JSON, where the session
id comes from, refusals (exit 2), and that nothing is written without `--record`. Fixture as in
tests/test_bashgate.py: $HOME = tmp/user, home = tmp/user/.pi-lead (lead-1 registered), cwd = tmp/repo.
bin/pilead runs with PYTHONDONTWRITEBYTECODE=1 (see the fixture)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_bashgate import SID, grace_min, heredoc, make_repo, register, tree, write_cfg
from pilead import config  # noqa: E402 — lib/ is on sys.path once test_bashgate is imported

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"


@pytest.fixture
def g(tmp_path, monkeypatch):
    user = tmp_path / "user"
    home = user / ".pi-lead"
    register(home)
    write_cfg(home, mode="deny")
    repo = make_repo(tmp_path / "repo")
    for var in ("PI_LEAD_SID", "PI_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PI_LEAD_HOME", str(home))
    # the system Python caches stdlib bytecode under $HOME/Library/Caches when it starts; that is the
    # interpreter, not pilead, and would break the byte-identical checks of tmp
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    class G:
        pass
    o = G()
    o.tmp, o.home, o.repo = tmp_path, home, repo
    o.real_repo, o.real_home = Path(os.path.realpath(repo)), Path(os.path.realpath(home))

    def run(stdin, *args, env=None, session=SID):
        argv = [sys.executable, str(PILEAD), "gate", *(["--session", session] if session is not None else []),
                "--cwd", str(repo), *args]
        return subprocess.run(argv, input=stdin.encode(), capture_output=True, env={**os.environ, **(env or {})})
    o.run = run
    return o


def out(r):
    assert r.returncode == 0, r.stderr
    lines = r.stdout.decode().splitlines()
    assert len(lines) == 1, r.stdout
    return lines[0]


def test_allow_line_and_json(g):
    assert out(g.run("ls -la")) == "allow"
    assert json.loads(out(g.run("ls -la", "--json"))) == {
        "verdict": "allow", "mode": "deny", "reason": None, "targets": [], "rule": None, "parsed": True}


def test_log_verb_rule_line_and_json(g):
    assert out(g.run("npm install")) == "log: npm-install"
    d = json.loads(out(g.run("npm install", "--json")))
    assert (d["verdict"], d["rule"], d["targets"]) == ("log", "npm-install", [])


def test_log_hit_line_and_json(g):
    write_cfg(g.home, mode="log")
    p = g.real_repo / "lib" / "new.py"
    assert out(g.run("echo x > lib/new.py")) == f"log: bash-write — {p} (new file)"
    assert out(g.run(heredoc("lib/tracked.py", 41))) == f"log: heredoc — {g.real_repo / 'lib' / 'tracked.py'} (41 lines)"
    d = json.loads(out(g.run("echo x > lib/new.py", "--json")))
    assert d == {"verdict": "log", "mode": "log", "reason": None, "rule": "bash-write", "parsed": True,
                 "targets": [{"path": str(p), "lines": None, "new_file": True, "control": None, "hit": True}]}


def test_block_line_and_json(g):
    p = g.real_repo / "lib" / "new.py"
    reason = (f"pi-lead lead gate: this bash command writes {p} (new file) — delegate this (pilead spawn/send) or "
              f"/pilead:route retain \"<reason>\" and retry within 10 min.")
    assert out(g.run("echo x > lib/new.py")) == f"block: {reason}"
    d = json.loads(out(g.run("echo x > lib/new.py", "--json")))
    assert (d["verdict"], d["reason"]) == ("block", reason)


def test_block_control_file(g):
    r = out(g.run("echo 1 > ~/.pi-lead/config.json"))
    assert r.startswith(f"block: pi-lead lead gate: this bash command writes {g.real_home / 'config.json'}, "
                        "a pi-lead control file (settings)")


@pytest.mark.parametrize("how", ["flag", "PI_LEAD_SID", "PI_SESSION_ID"])
def test_session_id_sources(g, how):
    env = {} if how == "flag" else {how: SID}
    assert out(g.run("echo x > lib/new.py", env=env, session=SID if how == "flag" else None)).startswith("block: ")
    # an unregistered id from the same source is simply not a lead: allow
    env = {} if how == "flag" else {how: "lead-2"}
    assert out(g.run("echo x > lib/new.py", env=env, session="lead-2" if how == "flag" else None)) == "allow"


def test_flag_wins_over_env(g):
    assert out(g.run("echo x > lib/new.py", env={"PI_LEAD_SID": "lead-2"})).startswith("block: ")
    assert out(g.run("echo x > lib/new.py", env={"PI_LEAD_SID": SID, "PI_SESSION_ID": "lead-2"},
                     session=None)).startswith("block: ")


def test_no_session_id_exits_2(g):
    before = tree(g.tmp)
    for env in ({}, {"PI_LEAD_SID": "", "PI_SESSION_ID": ""}):
        r = g.run("echo x > lib/new.py", env=env, session=None)
        assert r.returncode == 2 and r.stdout == b""
        assert len(r.stderr.decode().splitlines()) == 1
    assert tree(g.tmp) == before


@pytest.mark.parametrize("sid", ["../../x", "", "a/b", "x.usage"])
def test_unusable_session_exits_2_and_writes_nothing(g, sid):
    before = tree(g.tmp)
    r = g.run("echo 1 > ~/.pi-lead/config.json", "--record", session=sid)
    assert r.returncode == 2 and r.stdout == b""
    assert r.stderr.decode().startswith(f"pilead: lead id {sid!r} is not usable")
    assert tree(g.tmp) == before


def test_without_record_home_is_byte_identical(g):
    write_cfg(g.home, mode="log")
    before = tree(g.home)
    for cmd in ("echo x > lib/new.py", "npm install", "echo 1 > ~/.pi-lead/config.json", "ls"):
        g.run(cmd)
        g.run(cmd, "--json")
    assert tree(g.home) == before


def test_record_writes_the_one_event(g):
    write_cfg(g.home, mode="log")
    out(g.run("echo x > lib/new.py", "--record"))
    evs = [json.loads(ln) for ln in (g.home / "ledger.jsonl").read_text().splitlines()]
    assert len(evs) == 1
    ev = evs[0]
    ev.pop("ts")
    assert ev == {"event": "would_have_blocked", "session_id": SID, "command": "echo x > lib/new.py",
                  "rule": "bash-write", "vector": "bash", "file_path": str(g.real_repo / "lib" / "new.py"),
                  "lines": None, "new_file": True}


def test_empty_stdin_is_allow(g):
    assert out(g.run("")) == "allow"
    assert json.loads(out(g.run("", "--json")))["verdict"] == "allow"


def test_cwd_defaults_to_the_current_directory(g):
    r = subprocess.run([sys.executable, str(PILEAD), "gate", "--session", SID], input=b"echo x > lib/new.py",
                       capture_output=True, cwd=str(g.repo))
    assert out(r).startswith("block: ")


# ══ second round: case, every word for the control files, `parsed` ══════════════════════════════════
def lines_of(r):
    assert r.returncode == 0, r.stderr
    return r.stdout.decode().splitlines()


def names_block(g, rel):
    return (f"block: pilead: this command names {g.real_home / rel}, which controls the lead gate, and the gate "
            f"cannot confirm it only reads it — use a plain read (cat, head, grep, jq, …) or leave the change to the "
            f"human")


def control_block(g, rel, kind, grace=600):
    """`grace` is the grace_seconds the gate read: write_cfg's 600 unless the test says otherwise."""
    return (f"block: pi-lead lead gate: this bash command writes {g.real_home / rel}, a pi-lead control file "
            f"({kind}) — a change to it needs a human's word; ask, or /pilead:route retain \"<reason>\" and retry "
            f"within {grace_min(grace)}.")


def test_context_row1_upper_case_config_with_config_absent(g):
    (g.home / "config.json").unlink()  # as the review found it: no config.json (mode is then the default, log)
    assert lines_of(g.run("echo x > ~/.pi-lead/CONFIG.JSON")) == [control_block(g, "CONFIG.JSON", "settings",
                                                                                   config.DEFAULTS["grace_seconds"])]
    assert not (g.home / "config.json").exists() and not (g.home / "CONFIG.JSON").exists()


@pytest.mark.parametrize("cmd,rel,kind", [
    (f"touch ~/.pi-lead/leads/{SID}.route", f"leads/{SID}.route", "grace file"),
    ("truncate -s 0 ~/.pi-lead/Ledger.jsonl", "Ledger.jsonl", "ledger"),
    ("install -m 644 lib/tracked.py ~/.pi-lead/config.json", "config.json", "settings"),
    ("ln -sf /tmp/elsewhere ~/.pi-lead/LEADS/x.ROUTE", "LEADS/x.ROUTE", "grace file"),
    ("rm ~/.pi-lead/config.json", "config.json", "settings"),
    ("cat a && touch ~/.pi-lead/leads/s.route", "leads/s.route", "grace file"),
])
def test_context_row2_word_shapes_aimed_at_a_control_file(g, cmd, rel, kind):
    assert lines_of(g.run(cmd)) == [names_block(g, rel)]
    d = json.loads(out(g.run(cmd, "--json")))
    assert (d["verdict"], d["parsed"]) == ("block", True)


def test_plain_reads_of_a_control_file_are_allowed(g):
    for cmd in ("cat ~/.pi-lead/config.json", "jq . ~/.pi-lead/config.json | head -n 2",
                "grep -c . ~/.pi-lead/config.json", "cat ~/.pi-lead/config.json | tee /tmp/x.txt"):
        assert lines_of(g.run(cmd))[0] in ("allow", "log: tee-mutation"), cmd
    assert lines_of(g.run("cat ~/.pi-lead/config.json >> ~/.pi-lead/config.json")) == [
        control_block(g, "config.json", "settings")]


def test_context_row3_unread_command_says_parsed_no(g):
    assert lines_of(g.run("echo 'x > notes.txt")) == ["allow", "parsed: no"]
    assert lines_of(g.run("echo x > lib/new\x00.py")) == ["allow", "parsed: no"]
    assert lines_of(g.run("echo 'x > ~/.pi-lead/config.json")) == [names_block(g, "config.json"), "parsed: no"]
    d = json.loads(out(g.run("echo 'x > notes.txt", "--json")))
    assert d == {"verdict": "allow", "mode": "deny", "reason": None, "targets": [], "rule": None, "parsed": False}
    d = json.loads(out(g.run("echo x >> ~/.pi-lead/LEDGER.JSONL\x00", "--json")))
    assert (d["verdict"], d["parsed"], d["targets"][0]["control"]) == ("block", False, "ledger")


def test_read_command_text_is_one_line_and_json_says_parsed_true(g):
    for cmd in ("ls -la", "npm install", "echo x > lib/new.py", ""):
        assert len(lines_of(g.run(cmd))) == 1
        assert json.loads(out(g.run(cmd, "--json")))["parsed"] is True


def test_unregistered_session_json_parsed_is_null(g):
    assert json.loads(out(g.run("echo 'x", "--json", session="lead-2")))["parsed"] is None
    assert lines_of(g.run("echo 'x", session="lead-2")) == ["allow"]


def test_record_unread_command_is_recorded_with_parsed_false(g):
    write_cfg(g.home, mode="log")
    lines_of(g.run("echo 'x > ~/.pi-lead/config.json", "--record"))
    lines_of(g.run("echo x | tee 'out.txt", "--record"))
    lines_of(g.run("echo 'x > notes.txt", "--record"))  # allow: third round, `gate_unread`
    evs = [json.loads(ln) for ln in (g.home / "ledger.jsonl").read_text().splitlines()]
    for ev in evs:
        ev.pop("ts")
    assert evs == [
        {"event": "blocked", "session_id": SID, "file_path": str(g.real_home / "config.json"), "lines": None,
         "new_file": False, "vector": "bash", "control": "settings", "parsed": False},
        {"event": "would_have_blocked", "session_id": SID, "command": "echo x | tee 'out.txt",
         "rule": "tee-mutation", "parsed": False},
        {"event": "gate_unread", "session_id": SID,
         "command_sha": hashlib.sha256(b"echo 'x > notes.txt").hexdigest(), "parsed": False}]


SECOND_ROUND = ["echo x > ~/.pi-lead/CONFIG.JSON", f"touch ~/.pi-lead/leads/{SID}.route",
                "truncate -s 0 ~/.pi-lead/ledger.jsonl", "rm ~/.pi-lead/config.json", "cat ~/.pi-lead/config.json",
                "echo 'x > ~/.pi-lead/config.json", "echo 'x > notes.txt", "echo x > f\x00"]


def test_still_read_only_without_record(g):
    for mode in ("deny", "log", "off"):
        write_cfg(g.home, mode=mode)
        before = tree(g.tmp)
        for cmd in SECOND_ROUND:
            g.run(cmd)
            g.run(cmd, "--json")
        assert tree(g.tmp) == before


def test_a_missing_home_is_not_created(g):
    missing = g.tmp / "no-such-home"
    before = tree(g.tmp)
    for cmd in SECOND_ROUND:
        r = g.run(cmd, env={"PI_LEAD_HOME": str(missing)})
        assert lines_of(r) in (["allow"],)  # not a registered lead there: the command is not looked at
    assert not missing.exists() and tree(g.tmp) == before


# ══ third round: rows a–h, the refusal's wording, `gate_unread`, `lines: null` ══════════════════════
CONTEXT_AH = [  # (command, path under home the refusal names, kind) — the Context table of the third round
    ("echo x > $HOME/.pi-lead/config.json", "config.json", "settings"),
    ("echo x > ${HOME}/.pi-lead/config.json", "config.json", "settings"),
    ("echo x > $PI_LEAD_HOME/config.json", "config.json", "settings"),
    ("cd ~/.pi-lead && echo x > config.json", "config.json", "settings"),   # p20g 4b: was "", "home"
    ("cd ~/.pi-lead/leads && touch s.route", "leads/s.route", "grace file"),  # p20g 4b: was "leads", "leads"
    ("touch ~/.pi-lead/leads/*.route", "leads/*.route", "grace file"),
    ("eval 'echo x > ~/.pi-lead/config.json'", "config.json", "settings"),
    ("bash -c 'echo x > ~/.pi-lead/config.json'", "config.json", "settings"),
    ("sh -c \"echo x > ~/.pi-lead/config.json\"", "config.json", "settings"),
    ("bash <<EOF\necho x > ~/.pi-lead/config.json\nEOF", "config.json", "settings"),
    ("node -e \"require('fs').writeFileSync('/Users/x/.pi-lead/config.json','x')\"", "config.json", "settings"),
    ("python3 -c \"open('/Users/x/.pi-lead/config.json','w').write('x')\"", "config.json", "settings"),
    ("find ~/.pi-lead -name '*.route' -delete", "", "home"),
    ("rm -rf ~/.pi-lead/leads", "leads", "leads"),
    ("rm -rf ~/.pi-lead", "", "home"),
    (f"rm ~/.pi-lead/leads/{SID}.json", f"leads/{SID}.json", "lead record"),
]


def path_of(g, rel):
    return os.path.join(g.real_home, rel) if rel else str(g.real_home)


@pytest.mark.parametrize("mode", ["deny", "log"])
@pytest.mark.parametrize("cmd,rel,kind", CONTEXT_AH, ids=[str(i) for i in range(len(CONTEXT_AH))])
def test_context_rows_a_to_h(g, cmd, rel, kind, mode):
    write_cfg(g.home, mode=mode)
    before = tree(g.tmp)
    want = ("block: pilead: this command names %s, which controls the lead gate, and the gate cannot confirm it only "
            "reads it — use a plain read (cat, head, grep, jq, …) or leave the change to the human") % path_of(g, rel)
    assert lines_of(g.run(cmd)) == [want]
    d = json.loads(out(g.run(cmd, "--json")))
    assert (d["verdict"], d["mode"], d["parsed"]) == ("block", mode, True)
    hit = next(t for t in d["targets"] if t["hit"] and t["control"])  # (row c also has a first-round hit)
    assert (hit["path"], hit["control"], hit["lines"]) == (path_of(g, rel), kind, None)
    assert tree(g.tmp) == before


def test_context_rows_a_to_h_record_lines_null(g):
    write_cfg(g.home, mode="log")
    for cmd, rel, kind in CONTEXT_AH:
        if (g.home / "ledger.jsonl").exists():
            (g.home / "ledger.jsonl").unlink()
        lines_of(g.run(cmd, "--record"))
        raw = (g.home / "ledger.jsonl").read_text()
        ev = json.loads(raw)
        ev.pop("ts")
        assert ev == {"event": "blocked", "session_id": SID, "file_path": path_of(g, rel), "lines": None,
                      "new_file": not os.path.exists(path_of(g, rel)), "vector": "bash", "control": kind}, cmd
        assert '"lines": null' in raw


@pytest.mark.parametrize("cmd", ["test -f ~/.pi-lead/config.json", "[ -f ~/.pi-lead/config.json ]",
                                 "echo ~/.pi-lead/config.json", "realpath ~/.pi-lead/config.json",
                                 "readlink ~/.pi-lead/config.json", "ls ~/.pi-lead",
                                 "cat ~/.pi-lead/ledger.jsonl | sort", "grep x $HOME/.pi-lead/ledger.jsonl"])
def test_plain_reads_through_the_verb(g, cmd):
    assert lines_of(g.run(cmd)) == ["allow"]
    if not cmd.startswith(("ls", "cat", "grep")):
        assert lines_of(g.run(cmd + " > ~/.pi-lead/config.json")) == [control_block(g, "config.json", "settings")]


def test_gate_unread_is_recorded_only_with_record(g):
    before = tree(g.tmp)
    assert lines_of(g.run("ls 'x")) == ["allow", "parsed: no"]
    assert tree(g.tmp) == before                                   # without --record: nothing
    assert lines_of(g.run("ls 'x", "--record")) == ["allow", "parsed: no"]
    ev = json.loads((g.home / "ledger.jsonl").read_text())
    ev.pop("ts")
    assert ev == {"event": "gate_unread", "session_id": SID, "command_sha": hashlib.sha256(b"ls 'x").hexdigest(),
                  "parsed": False}


THIRD_ROUND = [c for c, _r, _k in CONTEXT_AH] + ["ls 'x", "grep x $HOME/.pi-lead/ledger.jsonl", "ls ~/.pi-lead"]


def test_third_round_still_read_only_without_record(g):
    for mode in ("deny", "log", "off"):
        write_cfg(g.home, mode=mode)
        before = tree(g.tmp)
        for cmd in THIRD_ROUND:
            g.run(cmd)
            g.run(cmd, "--json")
        assert tree(g.tmp) == before


def test_third_round_a_missing_home_is_not_created(g):
    missing = g.tmp / "no-such-home"
    before = tree(g.tmp)
    for cmd in THIRD_ROUND:
        assert lines_of(g.run(cmd, "--record", env={"PI_LEAD_HOME": str(missing)})) == ["allow"]
    assert not missing.exists() and tree(g.tmp) == before

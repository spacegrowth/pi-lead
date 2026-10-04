"""An executor session id has one safe form (lib/pilead/state.py `valid_session_sid`), checked before anything
is read or written: every verb that takes a session id, every view that reads sessions/, `spawn` and the
usage / status engines only ever touch a session's files inside sessions/ of the home. A session's id is the
NAME OF ITS DIRECTORY; the `sid` field inside meta.json never builds a path. tests/session-ids.json is the
table this suite and tests/ts/pi-lead.test.ts both read.

Every home here sits two directories deep in the test's temp dir (<tmp>/outer/mid/home), so an id such as
`../../x` still points inside <tmp>, and the whole of <tmp> is compared before and after."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

from pilead import refs, state, status as status_mod, usage  # noqa: E402

IDS = json.loads((TESTS / "session-ids.json").read_text())
USABLE, NOT_USABLE = IDS["usable"], IDS["not_usable"]
LEAD_IDS = json.loads((TESTS / "lead-ids.json").read_text())
RULE = ("letters, digits, '.', '_' and '-' only, first and last a letter or digit, no '..', "
        "at most 128 characters")
SPAWN_RULE = ("use letters, digits, '.', '_' and '-', first character a letter or digit, no '..', "
              "at most 113 characters")

OSA_STUB = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*) printf 'OK\\nFAKE-UUID\\nfront\\n' ;;
  *) echo true ;;
esac
"""


def message(sid):
    return f"session id {sid!r} is not usable: {RULE}"


def line(sid):
    return f"pilead: {message(sid)}\n"


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    """No identity from the caller's shell, and a recording `osascript` first on PATH (conftest put
    <tmp>/fakebin there, holding the `pi` -> tests/fake_pi link): no test can reach the real iTerm."""
    for var in ("PI_LEAD_SID", "PI_SESSION_ID", "ITERM_SESSION_ID", "TERM_PROGRAM"):
        monkeypatch.delenv(var, raising=False)
    stub = tmp_path / "fakebin" / "osascript"
    stub.write_text(OSA_STUB)
    stub.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))


def pilead(*args, env=None):
    return subprocess.run([sys.executable, str(PILEAD), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def tree(root):
    """Every path under root: (kind, content, mtime) for a file, ("dir",) for a directory."""
    out = {}
    for p in Path(root).rglob("*"):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = ("link", os.readlink(p))
        elif p.is_dir():
            out[rel] = ("dir",)
        else:
            out[rel] = ("file", p.read_bytes(), p.stat().st_mtime_ns)
    return out


def outside(root, home, but=()):
    """tree(root) without anything under home, nor the named test logs (paths relative to root)."""
    h = str(Path(home).relative_to(root))
    return {k: v for k, v in tree(root).items()
            if k != h and not k.startswith(h + os.sep) and k not in but}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "outer" / "mid").mkdir(parents=True)
    return tmp_path / "outer" / "mid" / "home"


@pytest.fixture
def child():
    """A child process this test started: the only process any pid file here names."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield p
    p.kill()
    p.wait()


def meta_for(sid, wt, **kw):
    return {"sid": sid, "lead": "lead-1", "worktree": str(wt), "topic": sid, "model": "anthropic/claude-sonnet-5",
            "status": "reported", "created": "-", "packets": 1, "label": f"[Exec] {sid}", "layout": "tab", **kw}


def fill(sdir, meta, status, pid=None, report=True):
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (sdir / "status").write_text(status + "\n")
    (sdir / "packet-0001.md").write_text("GOAL: decoy\n")
    if report:
        (sdir / "report-0001.md").write_text("Decoy done.\nStatus: clean\nRisk flags: none\n"
                                             "UNVERIFIED: none\nChanged: x\n")
    if pid is not None:
        (sdir / "pid").write_text(f"{pid}\n")


@pytest.fixture
def world(tmp_path, home, child):
    """home with one lead (lead-1), one real session (real-1) and a ledger; a decoy where each of the
    command-line ids points, holding a valid record, `reported`, a packet, a report and the child's pid."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (tmp_path / "packet.md").write_text("GOAL: follow-up\n")
    (tmp_path / "findings.md").write_text("1. a finding\n")
    assert pilead("lead-start", "lead-1", "--project", "p", "--home", str(home)).returncode == 0
    fill(home / "sessions" / "real-1", meta_for("real-1", wt, status="idle"), "idle", report=False)
    abs_decoy = tmp_path / "abs-decoy"
    decoys = {
        "../../x": tmp_path / "outer" / "mid" / "x",
        str(abs_decoy): abs_decoy,
        "a/b": home / "sessions" / "a" / "b",
        "": home / "sessions",
    }
    for sid, d in decoys.items():
        d.mkdir(parents=True, exist_ok=True)
        fill(d, meta_for("victim", wt), "reported", pid=child.pid)
    assert (home / "sessions" / ".." / ".." / "x").resolve() == decoys["../../x"]
    assert (home / "ledger.jsonl").is_file()
    return {"wt": wt, "decoys": decoys, "abs": str(abs_decoy)}


# ── the rule ─────────────────────────────────────────────────────────────────
def test_table_covers_the_packet_minimum():
    for s in ["s1", "a", "7", "a-one", "alpha-120000", "topic-101602-2", "foo-r2", "foo-r12",
              "pilead-lint-doctor-081909-r2", "A-b_c.d9", "exec.v2", "x.usage", "lead-1.usage", "exec-1.usage",
              "0198c5d2-7b3a-7c4e-9f10-2a6b8c0d1e2f", "a" * 128]:
        assert s in USABLE, s
    for s in ["", ".", "..", "../../x", "../leads/lead-1", "a/b", "sessions/s1", "/tmp/x", "a\\b", "x..y", "-a",
              "a-", ".a", "a.", "_a", "a_", "a b", " a", "a\n", "\na", "a\0b", "é", "a*", "a:b", "~", "a" * 129]:
        assert s in NOT_USABLE, repr(s)
    assert not set(USABLE) & set(NOT_USABLE)


@pytest.mark.parametrize("sid", USABLE)
def test_usable(sid, tmp_path):
    assert state.valid_session_sid(sid) is True
    assert state.check_session_sid(sid) == sid
    assert state.session_dir(tmp_path, sid) == tmp_path / "sessions" / sid
    assert state.report_path(tmp_path, sid, 1) == tmp_path / "sessions" / sid / "report-0001.md"
    assert refs.baseline_path(tmp_path, sid, 1) == tmp_path / "sessions" / sid / "refs-0001.json"


@pytest.mark.parametrize("sid", NOT_USABLE)
def test_not_usable(sid, tmp_path):
    before = tree(tmp_path)
    assert state.valid_session_sid(sid) is False
    for fn in (state.check_session_sid, lambda s: state.session_dir(tmp_path, s),
               lambda s: state.load_meta(tmp_path, s), lambda s: state.report_path(tmp_path, s, 1),
               lambda s: refs.baseline_path(tmp_path, s, 1)):
        with pytest.raises(state.PileadError) as e:
            fn(sid)
        assert e.value.code == 2
        assert f"pilead: {e.value}\n" == line(sid)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("value", [None, 5, b"s1", ["a"], 1.5, {"a": 1}, object()])
def test_not_text_is_not_usable_and_never_raises(value):
    assert state.valid_session_sid(value) is False
    with pytest.raises(state.PileadError):
        state.check_session_sid(value)


def test_limits():
    assert (state.SESSION_SID_MAX, state.SPAWN_SID_MAX, state.LEAD_SID_MAX) == (128, 120, 128)


def test_the_lead_rule_did_not_move():
    for s in LEAD_IDS["usable"]:
        assert state.valid_lead_sid(s) is True, s
    for s in LEAD_IDS["not_usable"]:
        assert state.valid_lead_sid(s) is False, repr(s)


def test_the_first_message_line():
    assert line("../../x").startswith("pilead: session id '../../x' is not usable:")


# ── an id from the command line ──────────────────────────────────────────────
CLI_IDS = ["../../x", "ABS", "a/b", ""]
VERBS = [
    ("send", lambda sid, t: ["send", sid, str(t / "packet.md")]),
    ("close", lambda sid, t: ["close", sid]),
    ("refs accept", lambda sid, t: ["refs", "accept", sid]),
    ("verify", lambda sid, t: ["verify", sid]),
    ("verify --findings", lambda sid, t: ["verify", sid, "--findings", str(t / "findings.md")]),
    ("diff", lambda sid, t: ["diff", sid]),
    ("check", lambda sid, t: ["check", sid]),
]


def _id(world, sid):
    return world["abs"] if sid == "ABS" else sid


@pytest.mark.parametrize("sid", CLI_IDS)
@pytest.mark.parametrize("name,argv", VERBS, ids=[v[0] for v in VERBS])
def test_verbs_refuse_an_unusable_id_and_touch_nothing(tmp_path, home, world, child, sid, name, argv):
    sid = _id(world, sid)
    ledger = (home / "ledger.jsonl").read_text()
    before = tree(tmp_path)
    r = pilead(*argv(sid, tmp_path), "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (2, "", line(sid)), (name, sid)
    assert tree(tmp_path) == before, (name, sid)
    assert (home / "ledger.jsonl").read_text() == ledger
    assert child.poll() is None, "the child was signalled"
    assert not (tmp_path / "fake-pi.log").exists() and not (tmp_path / "osa.log").exists()
    assert not Path(os.environ["FAKE_OSA_LOG"]).exists()  # the terminal was never asked


@pytest.mark.parametrize("sid", CLI_IDS)
def test_check_refs_only_and_json_keep_their_shape(tmp_path, home, world, child, sid):
    sid = _id(world, sid)
    before = tree(tmp_path)
    r = pilead("check", sid, "--refs-only", "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (0, f"refs: not compared ({message(sid)})\n", "")
    r = pilead("check", sid, "--json", "--home", str(home))
    assert (r.returncode, r.stderr) == (0, "")
    assert json.loads(r.stdout) == [{"sid": sid, "error": message(sid)}]
    assert tree(tmp_path) == before
    assert child.poll() is None


def test_an_unusable_id_does_not_create_home(tmp_path):
    (tmp_path / "outer" / "mid").mkdir(parents=True)
    home = tmp_path / "outer" / "mid" / "home"
    (tmp_path / "packet.md").write_text("GOAL\n")
    before = tree(tmp_path)
    for argv in (["send", "../../x", str(tmp_path / "packet.md")], ["close", "../../x"], ["check", "../../x"]):
        r = pilead(*argv, "--home", str(home))
        assert (r.returncode, r.stdout, r.stderr) == (2, "", line("../../x")), argv
    assert not home.exists()
    assert tree(tmp_path) == before


def test_a_usable_unknown_id_is_unchanged(tmp_path, home, world):
    r = pilead("close", "nope", "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session nope\n")


# ── pilead status ────────────────────────────────────────────────────────────
def test_status_prints_nothing_for_an_unusable_id(tmp_path, home, world):
    before = tree(tmp_path)
    for args, env in ((["../../x"], {}), ([], {"PI_LEAD_SID": "../../x"}), ([world["abs"]], {})):
        r = pilead("status", "--home", str(home), *args, env=env)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), (args, env)
    assert tree(tmp_path) == before


# ── ids from files and directory names ───────────────────────────────────────
def test_the_sid_field_never_builds_a_path(tmp_path, home, world, child):
    good = home / "sessions" / "good-1"
    fill(good, meta_for("../../x", world["wt"], status="idle"), "idle", report=False)
    logs = ("osa.log", "fake-pi.log")
    before = outside(tmp_path, home, but=logs)
    decoy = tree(world["decoys"]["../../x"])
    for argv in (["list"], ["list", "--json"], ["check", "--all"], ["check", "good-1"], ["stats"], ["board"]):
        r = pilead(*argv, "--home", str(home))
        assert r.returncode == 0, (argv, r.stderr)
        assert outside(tmp_path, home, but=logs) == before, argv
        assert tree(world["decoys"]["../../x"]) == decoy, argv
    listed = json.loads(pilead("list", "--json", "--home", str(home)).stdout)
    names = [e["sid"] for e in listed["executors"]]
    assert "good-1" in names and "../../x" not in names
    r = pilead("close", "good-1", "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (0, "closed good-1\n", "")
    assert outside(tmp_path, home, but=logs) == before
    assert tree(world["decoys"]["../../x"]) == decoy
    assert (good / "status").read_text() == "closed\n"
    assert json.loads((good / "meta.json").read_text())["sid"] == "good-1"
    assert not (home / "sessions" / "victim").exists()
    assert child.poll() is None


def test_a_directory_whose_name_is_not_usable_holds_no_session(tmp_path, home, world):
    def run_all():
        return {k: pilead(*argv, "--home", str(home)) for k, argv in (
            ("list", ["list"]), ("json", ["list", "--json"]), ("all", ["check", "--all"]),
            ("stats", ["stats"]), ("status", ["status", "lead-1"]))}
    fill(home / "sessions" / "real-1", meta_for("real-1", world["wt"], status="busy"), "busy", report=False)
    without = run_all()
    bad = home / "sessions" / "a b"
    fill(bad, meta_for("a b", world["wt"], status="busy"), "busy", report=False)
    inside = tree(bad)
    with_ = run_all()
    for k in without:
        assert with_[k].returncode == without[k].returncode, k
    # list: a broken row for it, the real session as before
    assert "a b" in with_["list"].stdout and "broken" in with_["list"].stdout
    ex = {e["sid"]: e for e in json.loads(with_["json"].stdout)["executors"]}
    assert ex["a b"] == {"sid": "a b", "broken": True}
    assert "broken" not in ex["real-1"] and ex["real-1"]["status"] == "busy"
    # check --all: its error line, and the real session still checked
    assert f"== a b ==\nerror: {message('a b')}\n" in with_["all"].stdout
    assert "== real-1 ==\n" in with_["all"].stdout
    assert with_["all"].stdout.replace(f"== a b ==\nerror: {message('a b')}\n", "") == without["all"].stdout
    # stats and the lead's status line leave it out
    assert with_["stats"].stdout == without["stats"].stdout
    assert with_["status"].stdout == without["status"].stdout
    assert "busy: real-1" in with_["status"].stdout and "a b" not in with_["status"].stdout
    assert tree(bad) == inside


def test_usage_answers_nothing_for_an_unusable_id(tmp_path, home, world, monkeypatch):
    log = tmp_path / "outer" / "x-session.jsonl"
    log.write_text(json.dumps({"type": "session", "id": "../../x", "cwd": str(world["wt"])}) + "\n")
    calls = []
    monkeypatch.setattr(usage, "locate", lambda sid, wt=None: calls.append(sid) or log)
    before = tree(tmp_path)
    assert usage.session_reading(home, "../../x", None) == (None, None)
    assert usage.for_session(home, "../../x", None) is None
    assert calls == []  # no log was even looked for
    assert tree(tmp_path) == before
    # the patched locate would have found it for a usable id
    usage.session_reading(home, "real-1", None, write_cache=False)
    assert calls == ["real-1"]


def test_refresh_keeps_the_stored_status_for_an_unusable_id(tmp_path, home, world):
    before = tree(tmp_path)
    assert status_mod.refresh(home, {"sid": "../../x", "status": "busy", "packets": 1}) == "busy"
    assert tree(tmp_path) == before


# ── spawn ────────────────────────────────────────────────────────────────────
def spawn(tmp_path, home, topic):
    return pilead("spawn", str(tmp_path / "wt"), topic, str(tmp_path / "packet.md"), "--model", "sonnet",
                  "--lead", "lead-1", "--home", str(home))


@pytest.fixture
def spawn_env(tmp_path, home):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: x\n")
    assert pilead("lead-start", "lead-1", "--project", "p", "--home", str(home)).returncode == 0
    return tmp_path


@pytest.mark.parametrize("topic", ["a..b", "_a", "a" * 130])
def test_spawn_refuses_a_topic_whose_id_is_not_usable(spawn_env, home, topic):
    tmp_path = spawn_env
    ledger = (home / "ledger.jsonl").read_text()
    before = tree(tmp_path)
    r = spawn(tmp_path, home, topic)
    safe = state.safe_topic(topic)
    m = re.fullmatch(r"pilead: topic (.*) gives the session id (.*), which is not usable: (.*)\n", r.stderr)
    assert (r.returncode, r.stdout) == (2, ""), r.stderr
    assert m, r.stderr
    assert m.group(1) == repr(topic) and m.group(3) == SPAWN_RULE
    assert re.fullmatch(re.escape(repr(safe)[:-1]) + r"-\d{6}'", m.group(2)), m.group(2)
    assert not (home / "sessions").exists() or not any((home / "sessions").iterdir())
    assert (home / "ledger.jsonl").read_text() == ledger
    assert tree(tmp_path) == before
    assert not (tmp_path / "fake-pi.log").exists()
    assert not Path(os.environ["FAKE_OSA_LOG"]).exists()  # the terminal was never asked


@pytest.mark.parametrize("topic", ["t", "topic", "alpha", "beta-longer-topic", "a" * 100])
def test_spawn_keeps_the_id_it_makes_for_a_usable_topic(spawn_env, home, topic):
    r = spawn(spawn_env, home, topic)
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]  # "spawned <sid> ..."
    assert re.fullmatch(re.escape(topic) + r"-\d{6}", sid), sid
    assert (home / "sessions" / sid / "meta.json").is_file()
    assert json.loads((home / "sessions" / sid / "meta.json").read_text())["sid"] == sid
    assert len(sid) <= state.SPAWN_SID_MAX and state.valid_session_sid(sid + "-r2")


def test_the_spawn_refusal_gives_the_real_topic_limit(spawn_env, home):
    """The limit in the refusal is worked out (SPAWN_SID_MAX less the `-HHMMSS` spawn adds), not
    written: the longest topic it names is accepted, one character more is refused."""
    from pilead.verbs import spawn as spawn_verb
    assert spawn_verb.TOPIC_MAX == state.SPAWN_SID_MAX - len("-HHMMSS") == 113
    assert SPAWN_RULE.endswith(f"at most {spawn_verb.TOPIC_MAX} characters")
    ok = spawn(spawn_env, home, "a" * spawn_verb.TOPIC_MAX)
    assert ok.returncode == 0, ok.stderr
    assert len(ok.stdout.split()[1]) == state.SPAWN_SID_MAX
    r = spawn(spawn_env, home, "b" * (spawn_verb.TOPIC_MAX + 1))
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr.endswith(f": {SPAWN_RULE}\n"), r.stderr

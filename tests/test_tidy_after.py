"""tabs.maybe_tidy — the tidy that follows a verb (p23b changes 2 and 5).

Its process is `bin/pilead tidy --quiet --home <home>` (with `--lead` only when there is a caller); every way it can
fail prints ONE line on stderr and returns False; RELAY_NO_TIDY or `tidy_tabs: false` start no process at all. The
verbs that open a tab or change an owner call it once, last, and not after a refusal: they run in-process
(`cli.main`) with `tabs.maybe_tidy` replaced by a recorder, after a setup driven through bin/pilead with the fake
`pi`, a recording `osascript` stub and a `ps` stub first on PATH. With the recorder raising, a verb's exit code,
stdout and files are those of a run where it returns True. No real tab is opened, moved or closed."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import cli, config, tabs  # noqa: E402
from pilead.launch import backend  # noqa: E402
from pilead.verbs import close_predecessor  # noqa: E402
from test_resume import OSA, PS  # noqa: E402
from test_tidy_order import H, U, make_exec, make_lead, tree  # noqa: E402
from test_usage import assistant, u, write_log  # noqa: E402

it = backend.by_name("iterm")
PILEAD = ROOT / "bin" / "pilead"
CFG = config.DEFAULTS
LINE = "  · tab tidy skipped ({}) — pilead tidy --dry-run shows the order it wanted\n"


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "false")
    monkeypatch.delenv("FAKE_SPAWN", raising=False)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "leads").mkdir(parents=True)
    return h


def no_process(*a, **k):
    raise AssertionError("a process was started")


# ── maybe_tidy ────────────────────────────────────────────────────────────────────────────────────
def test_relay_no_tidy_starts_no_process_and_prints_nothing(home, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", no_process)
    assert tabs.maybe_tidy(home, CFG, after="spawn") is False
    assert capsys.readouterr() == ("", "")


def test_tidy_tabs_false_starts_no_process_and_prints_nothing(home, monkeypatch, capsys):
    monkeypatch.delenv("RELAY_NO_TIDY")
    monkeypatch.setattr(subprocess, "run", no_process)
    assert tabs.maybe_tidy(home, {**CFG, "tidy_tabs": False}, after="spawn") is False
    (home / "config.json").write_text(json.dumps({"tidy_tabs": False}))
    assert tabs.maybe_tidy(home, None, after="spawn") is False
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def runs(monkeypatch):
    """subprocess.run replaced: records (argv, timeout) and answers st["answer"] (a CompletedProcess or an
    exception)."""
    monkeypatch.delenv("RELAY_NO_TIDY")
    st = {"calls": [], "answer": None}

    def run(argv, **kw):
        st["calls"].append((argv, kw.get("timeout")))
        a = st["answer"]
        if isinstance(a, BaseException):
            raise a
        return a if a is not None else subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return st


def test_the_process_is_pilead_tidy_quiet_with_the_home(home, runs, capsys):
    assert tabs.maybe_tidy(home, CFG, after="spawn") is True
    assert runs["calls"] == [([sys.executable, str(PILEAD), "tidy", "--quiet", "--home", str(home)], 8)]
    assert capsys.readouterr() == ("", "")


def test_lead_is_passed_only_when_there_is_a_caller(home, runs, monkeypatch):
    make_lead(home, "lead-a", 1)
    monkeypatch.setenv("PI_LEAD_SID", "lead-unregistered")
    tabs.maybe_tidy(home, CFG, after="spawn")
    monkeypatch.setenv("PI_LEAD_SID", "lead-a")
    tabs.maybe_tidy(home, CFG, after="spawn")
    monkeypatch.delenv("PI_LEAD_SID")
    monkeypatch.setenv("PI_SESSION_ID", "lead-a")
    tabs.maybe_tidy(home, CFG, after="spawn")
    base = [sys.executable, str(PILEAD), "tidy", "--quiet", "--home", str(home)]
    assert [c[0] for c in runs["calls"]] == [base, base + ["--lead", "lead-a"], base + ["--lead", "lead-a"]]


@pytest.mark.parametrize("answer,reason", [
    (subprocess.CompletedProcess([], 1, "no iTerm tab found for any of the ordered session ids\n", ""),
     "no iTerm tab found for any of the ordered session ids"),
    (subprocess.CompletedProcess([], 2, "", "pilead: lead id 'x' is not usable\n"), "pilead: lead id 'x' is not usable"),
    (subprocess.CompletedProcess([], 3, "", ""), "pilead tidy exited 3"),
    (subprocess.TimeoutExpired(["pilead"], 8), "tidy ran longer than 8 s"),
    (PermissionError("not allowed"), "PermissionError: not allowed"),
])
def test_each_failure_prints_one_line_and_returns_false(home, runs, capsys, answer, reason):
    runs["answer"] = answer
    assert tabs.maybe_tidy(home, CFG, after="spawn") is False
    assert capsys.readouterr() == ("", LINE.format(reason))


def test_a_binary_that_cannot_be_started(home, monkeypatch, capsys):
    monkeypatch.delenv("RELAY_NO_TIDY")
    monkeypatch.setattr(sys, "executable", str(home / "no-such-python"))
    assert tabs.maybe_tidy(home, CFG, after="spawn") is False
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1
    assert err.startswith("  · tab tidy skipped (FileNotFoundError: ") and err.endswith(") — pilead tidy --dry-run shows the order it wanted\n")


def test_the_err_stream_can_be_given(home, runs, capsys):
    import io
    runs["answer"] = subprocess.CompletedProcess([], 1, "why\n", "")
    buf = io.StringIO()
    assert tabs.maybe_tidy(home, CFG, after="spawn", err=buf) is False
    assert buf.getvalue() == LINE.format("why") and capsys.readouterr() == ("", "")


# ── a real child process, through the stand-in ────────────────────────────────────────────────────
@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_NO_TIDY")
    monkeypatch.setenv("FAKE_ITERM2", "ok")
    log = tmp_path / "iterm2.log"
    monkeypatch.setenv("FAKE_ITERM2_LOG", str(log))
    return lambda: [json.loads(ln) for ln in log.read_text().splitlines()] if log.is_file() else []


def test_the_child_orders_the_tabs_and_prints_nothing(home, fake, monkeypatch, capsys):
    make_lead(home, "lead-a", 1)
    make_exec(home, "a-1", "lead-a", 11)
    monkeypatch.setenv("FAKE_ITERM2_LAYOUT", json.dumps([[[U(11)], [U(1)]]]))
    assert tabs.maybe_tidy(home, CFG, after="spawn") is True
    assert capsys.readouterr() == ("", "")
    assert fake() == [{"window": 0, "set_tabs": [[U(1)], [U(11)]]}]
    assert [e["event"] for e in map(json.loads, (home / "ledger.jsonl").read_text().splitlines())] == ["tidy"]


def test_the_child_that_cannot_order_prints_its_reason(home, fake, monkeypatch, capsys):
    make_lead(home, "lead-a", 1)
    monkeypatch.setenv("FAKE_ITERM2_LAYOUT", json.dumps([[[U(90)]]]))
    assert tabs.maybe_tidy(home, CFG, after="spawn") is False
    assert capsys.readouterr() == ("", LINE.format("no iTerm tab found for any of the ordered session ids"))
    assert fake() == []


# ── the verbs that tidy afterwards ────────────────────────────────────────────────────────────────
def setup_run(root, *args, caller=None, check=True):
    e = {**os.environ, "PI_LEAD_HOME": str(root / "home")}
    e.pop("PI_LEAD_SID", None)
    if caller:
        e["PI_LEAD_SID"] = caller
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def base(root):
    """A registered lead-1, a worktree and a packet under `root`."""
    (root / "wt").mkdir(parents=True)
    (root / "packet.md").write_text("GOAL: do the thing\n")
    (root / "p2.md").write_text("GOAL: the next thing\n")
    setup_run(root, "lead-start", "lead-1", "--project", "proj")


def S(root):
    """The sid `spawned` made under `root` (topic `s-a` and the time)."""
    return (root / "sid.txt").read_text()


def spawned(root, status=None):
    base(root)
    r = setup_run(root, "spawn", str(root / "wt"), "s-a", str(root / "packet.md"), "--model", "sonnet", "--lead",
                  "lead-1")
    sid = r.stdout.split()[1]
    (root / "sid.txt").write_text(sid)
    write_log(sid, (root / "wt").resolve(), [assistant(u(10, 5))])
    d = root / "home" / "sessions" / sid
    if status == "reported":
        (d / "report-0001.md").write_text("Done.\nStatus: clean\n")
    if status:
        (d / "status").write_text(status + "\n")
    return d


def owned_by(d, lead):
    m = json.loads((d / "meta.json").read_text())
    m["lead"] = lead
    (d / "meta.json").write_text(json.dumps(m, indent=2) + "\n")


def handoff_world(root):
    rec = {"sid": "lead-out", "project": "proj", "cwd": str(root.resolve()), "iterm_handle": H(1),
           "terminal": "iTerm.app", "started": "2026-10-01T09:00:00Z"}
    (root / "home" / "leads").mkdir(parents=True)
    (root / "home" / "leads" / "lead-out.json").write_text(json.dumps(rec, indent=2) + "\n")
    (root / "home" / "leads" / "lead-out.inbox.md").write_text("")
    write_log("lead-out", root.resolve(), [assistant(u(10, 5))])
    (root / "memo.md").write_text("# Handoff\n\nNext: review.\n")


def predecessor_world(root):
    (root / "home" / "leads").mkdir(parents=True)
    rec = {"sid": "lead-new", "project": "proj", "iterm_handle": H(2), "terminal": "iTerm.app",
           "predecessor": {"sid": "lead-old", "iterm_handle": H(1)}}
    (root / "home" / "leads" / "lead-new.json").write_text(json.dumps(rec, indent=2) + "\n")


def adopt_world(root):
    d = spawned(root)
    owned_by(d, "lead-gone")


def takeover_world(root):
    (root / "home" / "leads").mkdir(parents=True)
    rec = {"sid": "lead-old", "project": "proj", "cwd": "/repo", "inbox": str(root / "home" / "leads" / "x.md"),
           "started": "2020-01-01T00:00:00Z", "terminal": "iTerm.app", "iterm_handle": None}
    (root / "home" / "leads" / "lead-old.json").write_text(json.dumps(rec))
    d = root / "home" / "sessions" / "exec-a"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"sid": "exec-a", "lead": "lead-old", "worktree": "/wt", "packets": 1}))


def predecessor_terminal(monkeypatch):
    st = {"closed": False}
    monkeypatch.setattr(close_predecessor, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(it, "exists_by_id", lambda h, *a, **k: not st["closed"])
    monkeypatch.setattr(it, "tty_by_id", lambda h, *a, **k: None)

    def close(h, *a, **k):
        st["closed"] = True
        return True
    monkeypatch.setattr(it, "close_by_id", close)


# (name, setup(root), argv(root), caller, extra patch or None)
SUCCESS = [
    ("spawn", base, lambda r: ["spawn", str(r / "wt"), "topic", str(r / "packet.md"), "--model", "sonnet", "--lead",
                               "lead-1", "--name", "s-new"], None, None),
    ("send --rotate", lambda r: spawned(r, status="reported"),
     lambda r: ["send", S(r), str(r / "p2.md"), "--rotate", "--name", "s-b"], None, None),
    ("send relaunch", lambda r: spawned(r, status="closed"), lambda r: ["send", S(r), str(r / "p2.md")], None, None),
    ("send adopts", lambda r: owned_by(spawned(r, status="reported"), "lead-gone"),
     lambda r: ["send", S(r), str(r / "p2.md")], "lead-1", None),
    ("send --when-idle adopts", lambda r: owned_by(spawned(r, status="busy"), "lead-gone"),
     lambda r: ["send", S(r), str(r / "p2.md"), "--when-idle"], "lead-1", None),
    ("resume", lambda r: spawned(r, status="closed"), lambda r: ["resume", S(r)], None, None),
    ("restart", lambda r: spawned(r, status="closed"), lambda r: ["restart", S(r), "--name", "s-a2"], None, None),
    ("handoff", handoff_world, lambda r: ["handoff", str(r / "memo.md")], "lead-out", None),
    ("close-predecessor", predecessor_world, lambda r: ["close-predecessor"], "lead-new", predecessor_terminal),
    ("adopt", adopt_world, lambda r: ["adopt", S(r)], "lead-1", None),
    ("takeover", takeover_world, lambda r: ["takeover", "lead-old", "--as", "lead-new"], None, None),
]

REFUSED = [
    ("spawn", base, lambda r: ["spawn", str(r / "wt"), "topic", str(r / "missing.md"), "--model", "sonnet", "--lead",
                               "lead-1"], None, None),
    ("send to a busy session", lambda r: spawned(r, status="busy"), lambda r: ["send", S(r), str(r / "p2.md")],
     None, None),
    ("send --rotate of a busy session", lambda r: spawned(r, status="busy"),
     lambda r: ["send", S(r), str(r / "p2.md"), "--rotate"], None, None),
    ("resume", base, lambda r: ["resume", "s-unknown"], None, None),
    ("restart", base, lambda r: ["restart", "s-unknown"], None, None),
    ("handoff", handoff_world, lambda r: ["handoff", str(r / "no-memo.md")], "lead-out", None),
    ("close-predecessor", predecessor_world, lambda r: ["close-predecessor"], None, None),
    ("adopt", adopt_world, lambda r: ["adopt", S(r)], None, None),
    ("takeover", takeover_world, lambda r: ["takeover", "lead-old", "--as", "lead-old"], None, None),
]

# a send that neither relaunched nor adopted (the caller owns the session; it was reported) does not tidy
NO_TIDY = [
    ("plain send", lambda r: spawned(r, status="reported"), lambda r: ["send", S(r), str(r / "p2.md")], "lead-1",
     None),
    ("adopt of my own session", lambda r: spawned(r), lambda r: ["adopt", S(r)], "lead-1", None),
]


@pytest.fixture
def recorder(monkeypatch):
    st = {"calls": [], "raise": False}

    def maybe_tidy(home, cfg, after, err=None):
        st["calls"].append((str(home), after))
        if st["raise"]:
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(tabs, "maybe_tidy", maybe_tidy)
    return st


_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EPOCH = re.compile(r"\b1\d{9}(?:\.\d+)?\b")
_NS = re.compile(r"\bns:\d+")
_SPAWN_SID = re.compile(r"\b(topic|s-a)-\d{6}\b")


def norm(text, root):
    text = text.replace(str(root.resolve()), "<ROOT>").replace(str(root), "<ROOT>")
    if (root / "sid.txt").is_file():
        text = text.replace(S(root), "<SID>")
    text = _SPAWN_SID.sub(r"\1-<HHMMSS>", _NS.sub("ns:<NS>", text))
    return _EPOCH.sub("<EPOCH>", _UUID.sub("<UUID>", _TS.sub("<TS>", text)))


def run_case(tmp_path, monkeypatch, capsys, recorder, case, name, raising=False):
    _label, setup, argv, caller, patch = case
    root = tmp_path / name
    root.mkdir()
    monkeypatch.setenv("PI_LEAD_HOME", str(root / "home"))
    monkeypatch.setenv("FAKE_OSA_LOG", str(root / "osa.log"))
    monkeypatch.setenv("FAKE_PI_LOG", str(root / "fake-pi.log"))
    setup(root)
    if caller:
        monkeypatch.setenv("PI_LEAD_SID", caller)
    else:
        monkeypatch.delenv("PI_LEAD_SID", raising=False)
    if patch:
        patch(monkeypatch)
    import uuid
    fixed = uuid.UUID("5dc4c70d-9aea-4260-aa49-4a0af6a8763d")
    monkeypatch.setattr(uuid, "uuid4", lambda: fixed)  # a handoff's successor id: the same in both runs
    recorder["raise"] = raising
    recorder["calls"].clear()
    capsys.readouterr()
    rc = cli.main(argv(root))
    out = capsys.readouterr().out
    files = {norm(k, root): (norm(v.decode("utf-8", "replace"), root) if v != "dir" else v)
             for k, v in tree(root / "home").items()}
    return rc, norm(out, root), files, list(recorder["calls"])


@pytest.mark.parametrize("case", SUCCESS, ids=[c[0] for c in SUCCESS])
def test_a_verb_tidies_once_after_a_success_and_a_raising_tidy_changes_nothing(tmp_path, monkeypatch, capsys,
                                                                               recorder, case):
    rc, out, files, calls = run_case(tmp_path, monkeypatch, capsys, recorder, case, "true")
    assert rc == 0, out
    assert calls == [(str(tmp_path / "true" / "home"), case[0].split()[0])]
    rc2, out2, files2, calls2 = run_case(tmp_path, monkeypatch, capsys, recorder, case, "raise", raising=True)
    assert len(calls2) == 1
    assert (rc2, out2) == (rc, out)
    assert files2 == files


@pytest.mark.parametrize("case", REFUSED, ids=[c[0] for c in REFUSED])
def test_a_refused_verb_does_not_tidy(tmp_path, monkeypatch, capsys, recorder, case):
    rc, _out, _files, calls = run_case(tmp_path, monkeypatch, capsys, recorder, case, "refused")
    assert rc != 0 and calls == []


@pytest.mark.parametrize("case", NO_TIDY, ids=[c[0] for c in NO_TIDY])
def test_a_verb_that_opened_no_tab_and_changed_no_owner_does_not_tidy(tmp_path, monkeypatch, capsys, recorder,
                                                                      case):
    rc, _out, _files, calls = run_case(tmp_path, monkeypatch, capsys, recorder, case, "none")
    assert rc == 0 and calls == []


# ── b12: tidy is iTerm only — under tmux, and on Linux, the automatic tidy skips SILENTLY ──────────────
@pytest.mark.parametrize("how", ["PILEAD_TERMINAL", "TMUX", "config"])
def test_under_tmux_maybe_tidy_starts_no_process_and_prints_nothing(home, monkeypatch, capsys, how):
    monkeypatch.delenv("RELAY_NO_TIDY")
    monkeypatch.setattr(subprocess, "run", no_process)
    if how == "PILEAD_TERMINAL":
        monkeypatch.setenv("PILEAD_TERMINAL", "tmux")
        cfg = CFG
    elif how == "TMUX":
        monkeypatch.delenv("RELAY_TERMINAL")
        monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
        cfg = CFG
    else:
        monkeypatch.delenv("RELAY_TERMINAL")
        cfg = {**CFG, "terminal_app": "tmux"}
    assert tabs.maybe_tidy(home, cfg, after="spawn") is False
    assert capsys.readouterr() == ("", "")


def test_on_linux_maybe_tidy_starts_no_process_and_prints_nothing(home, monkeypatch, capsys):
    import platform_cmds
    monkeypatch.delenv("RELAY_NO_TIDY")
    monkeypatch.setattr(platform_cmds, "is_linux", lambda: True)
    monkeypatch.setattr(subprocess, "run", no_process)
    assert tabs.maybe_tidy(home, CFG, after="spawn") is False
    assert capsys.readouterr() == ("", "")


def test_an_iterm_selection_still_tidies(home, runs, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")  # RELAY_TERMINAL=iterm (conftest) beats it
    assert tabs.maybe_tidy(home, CFG, after="spawn") is True
    assert len(runs["calls"]) == 1

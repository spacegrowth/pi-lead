"""A lead id has one safe form (lib/pilead/state.py `valid_lead_sid`), checked before anything is read or
written: `pilead lead-start`, `spawn --lead`, `status` and `launch._lead_handle` never build a path from an
id in any other form. tests/lead-ids.json is the table this suite and tests/ts/pi-lead.test.ts both read.

Every home here sits two directories deep in the test's temp dir (<tmp>/outer/mid/home), so an id such as
`../../x` still points inside <tmp>, and the whole of <tmp> is compared before and after."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

from pilead import launch, state, tabs  # noqa: E402

IDS = json.loads((Path(__file__).resolve().parent / "lead-ids.json").read_text())
USABLE, NOT_USABLE = IDS["usable"], IDS["not_usable"]
# a command line cannot carry a NUL character
CLI_NOT_USABLE = [s for s in NOT_USABLE if "\0" not in s]
RULE = ("letters, digits, '.', '_' and '-' only, first and last a letter or digit, no '..', "
        "not ending in '.usage', at most 128 characters")

# What the unchanged verb (HEAD 18115b5) printed and wrote for `lead-start lead-1 --project p`, taken
# before this change; {home}, {cwd} and {ts} are filled in per run.
HEAD_STDOUT = "lead lead-1 ready inbox={home}/leads/lead-1.inbox.md\n"
HEAD_RECORD = """{{
  "started": "{ts}",
  "sid": "lead-1",
  "project": "p",
  "cwd": "{cwd}",
  "inbox": "{home}/leads/lead-1.inbox.md",
  "iterm_handle": null,
  "terminal": null,
  "backend": "iterm",
  "autonomous": false,
  "autonomous_source": "config",
  "tier": "auto",
  "tier_source": "config",
  "version": {version},
  "label": "[Lead] p",
  "color": {color},
  "no_rename": false
}}
"""
HEAD_LEDGER = '{{"ts": "{ts}", "event": "lead_started", "session_id": "lead-1", "project": "p"}}\n'


@pytest.fixture(autouse=True)
def no_identity(monkeypatch):
    for var in ("PI_LEAD_SID", "PI_SESSION_ID", "ITERM_SESSION_ID", "TERM_PROGRAM"):
        monkeypatch.delenv(var, raising=False)


def message(sid):
    return f"pilead: lead id {sid!r} is not usable: {RULE}\n"


def pilead(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(PILEAD), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, cwd=cwd)


def lead_start(home, sid, **kw):
    # `--` so argparse takes an id such as `-a` as the id, not as an option
    return pilead("lead-start", "--home", str(home), "--project", "p", "--", sid, **kw)


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


def outside(root, home):
    """tree(root) without anything under home."""
    h = str(Path(home).relative_to(root))
    return {k: v for k, v in tree(root).items() if k != h and not k.startswith(h + os.sep)}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "outer" / "mid").mkdir(parents=True)
    return tmp_path / "outer" / "mid" / "home"


# ── the rule ─────────────────────────────────────────────────────────────────
def test_table_covers_the_packet_minimum():
    for s in ["lead-1", "l2", "a", "7", "lead.v2", "lead_a", "A-b_c.d9", "usage", "x.usage.y",
              "0198c5d2-7b3a-7c4e-9f10-2a6b8c0d1e2f", "a" * 128]:
        assert s in USABLE, s
    for s in ["", ".", "..", "../../x", "a/b", "a\\b", "x..y", "-a", "a-", ".a", "a.", "_a", "a_", "a b", " a",
              "a\n", "\na", "a\0b", "é", "a*", "a:b", "~", "x.usage", "lead-1.usage", "a" * 129]:
        assert s in NOT_USABLE, repr(s)
    assert not set(USABLE) & set(NOT_USABLE)


@pytest.mark.parametrize("sid", USABLE)
def test_usable(sid, tmp_path):
    assert state.valid_lead_sid(sid) is True
    assert state.check_lead_sid(sid) == sid
    assert state.lead_path(tmp_path, sid) == tmp_path / "leads" / f"{sid}.json"


@pytest.mark.parametrize("sid", NOT_USABLE)
def test_not_usable(sid, tmp_path):
    assert state.valid_lead_sid(sid) is False
    for fn in (state.check_lead_sid, lambda s: state.lead_path(tmp_path, s)):
        with pytest.raises(state.PileadError) as e:
            fn(sid)
        assert e.value.code == 2
        assert f"pilead: {e.value}\n" == message(sid)


@pytest.mark.parametrize("value", [None, 5, b"lead-1", ["a"], 1.5, {"a": 1}, object()])
def test_not_text_is_not_usable_and_never_raises(value):
    assert state.valid_lead_sid(value) is False
    with pytest.raises(state.PileadError):
        state.check_lead_sid(value)


def test_limit_is_128():
    assert state.LEAD_SID_MAX == 128


# ── lead-start ───────────────────────────────────────────────────────────────
def test_lead_start_refuses_every_unusable_id_and_writes_nothing(tmp_path, home):
    # home not yet created
    before = tree(tmp_path)
    for sid in CLI_NOT_USABLE:
        r = lead_start(home, sid, cwd=tmp_path)
        assert (r.returncode, r.stdout, r.stderr) == (2, "", message(sid)), repr(sid)
        assert not home.exists(), repr(sid)
        assert tree(tmp_path) == before, repr(sid)

    # home created, holding one lead record and a ledger
    assert lead_start(home, "lead-1", cwd=tmp_path).returncode == 0
    ledger = (home / "ledger.jsonl").read_text()
    assert len(ledger.splitlines()) == 1
    before = tree(tmp_path)
    for sid in CLI_NOT_USABLE:
        r = lead_start(home, sid, cwd=tmp_path)
        assert (r.returncode, r.stdout, r.stderr) == (2, "", message(sid)), repr(sid)
        assert tree(tmp_path) == before, repr(sid)
    assert (home / "ledger.jsonl").read_text() == ledger


def test_lead_start_without_the_separator_refuses_too(tmp_path, home):
    before = tree(tmp_path)
    r = pilead("lead-start", "../../x", "--project", "p", "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (2, "", message("../../x"))
    assert r.stderr.startswith("pilead: lead id '../../x' is not usable:")
    assert tree(tmp_path) == before


def test_lead_start_accepts_every_usable_id(tmp_path, home):
    for sid in USABLE:
        before = outside(tmp_path, home)
        r = lead_start(home, sid, cwd=tmp_path)
        assert r.returncode == 0 and r.stderr == "", (sid, r.stderr)
        assert (home / "leads" / f"{sid}.json").is_file() and (home / "leads" / f"{sid}.inbox.md").is_file(), sid
        assert outside(tmp_path, home) == before, sid
    listed = json.loads(pilead("list", "--json", "--home", str(home)).stdout)
    lead_ids = {l.get("sid") for l in listed.get("leads", [])} if isinstance(listed, dict) else set()
    assert lead_ids == set(USABLE), listed
    # nothing but the usable ids' two files each, under leads/
    assert sorted(p.name for p in (home / "leads").iterdir()) == sorted(
        [f"{s}.json" for s in USABLE] + [f"{s}.inbox.md" for s in USABLE])


def test_lead_start_output_is_unchanged_from_head(tmp_path, home):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    r = pilead("lead-start", "lead-1", "--project", "p", "--home", str(home), cwd=cwd)
    assert r.returncode == 0 and r.stderr == ""
    rec_text = (home / "leads" / "lead-1.json").read_text()
    ts = json.loads(rec_text)["started"]
    led = (home / "ledger.jsonl").read_text()
    led_ts = json.loads(led)["ts"]
    fill = {"home": str(home), "cwd": str(cwd.resolve())}
    assert r.stdout == HEAD_STDOUT.format(**fill)
    color = json.dumps(tabs.lead_color("lead-1"), indent=2).replace("\n", "\n  ")
    assert rec_text == HEAD_RECORD.format(ts=ts, version=json.dumps(state.version()), color=color, **fill)
    assert led == HEAD_LEDGER.format(ts=led_ts)
    assert (home / "leads" / "lead-1.inbox.md").read_text() == ""
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == [
        "leads", "leads/lead-1.inbox.md", "leads/lead-1.json", "ledger.jsonl"]


# ── the other Python places ──────────────────────────────────────────────────
@pytest.fixture
def x_json(tmp_path, home):
    """x.json where leads/../../x.json points, so a read of it would succeed if it were made."""
    (home / "leads").mkdir(parents=True)
    x = home / "leads" / ".." / ".." / "x.json"
    x.write_text(json.dumps({"sid": "x", "project": "SHOULD-NOT-SHOW", "cwd": str(tmp_path),
                             "inbox": str(tmp_path / "x.inbox.md"), "iterm_handle": "w0t0p0:X-HANDLE"}))
    assert x.resolve() == tmp_path / "outer" / "mid" / "x.json"  # outside home
    return x


def test_spawn_refuses_an_unusable_lead(tmp_path, home, x_json):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: x\n")
    assert lead_start(home, "lead-1").returncode == 0
    ledger = (home / "ledger.jsonl").read_text()
    before = tree(tmp_path)
    r = pilead("spawn", str(tmp_path / "wt"), "topic", str(tmp_path / "packet.md"), "--model", "sonnet",
               "--lead", "../../x", "--home", str(home))
    assert (r.returncode, r.stdout, r.stderr) == (2, "", message("../../x"))
    assert not (home / "sessions").exists() or not any((home / "sessions").iterdir())
    assert (home / "ledger.jsonl").read_text() == ledger
    assert tree(tmp_path) == before
    assert not Path(os.environ["FAKE_PI_LOG"]).exists()  # fake_pi never ran
    assert not Path(os.environ["FAKE_OSA_LOG"]).exists()  # the terminal was never asked


def test_status_never_looks_up_an_unusable_lead(tmp_path, home, x_json):
    before = tree(tmp_path)
    for args, env in ((["../../x"], {}), ([], {"PI_LEAD_SID": "../../x"}), ([], {"PI_SESSION_ID": "../../x"})):
        r = pilead("status", "--home", str(home), *args, env=env)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), (args, env)
    assert tree(tmp_path) == before


def test_status_executor_line_leaves_out_an_unusable_lead(tmp_path, home, x_json):
    sdir = home / "sessions" / "topic-101010"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": "topic-101010", "lead": "../../x", "worktree": str(tmp_path / "wt"), "topic": "topic",
        "model": "anthropic/claude-sonnet-5", "status": "busy", "created": "-", "packets": 1}))
    (sdir / "status").write_text("busy\n")
    r = pilead("status", "--home", str(home), "topic-101010")
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == "pkt 0001 busy · ctx -\n"
    assert "for " not in r.stdout


def test_status_executor_id_that_is_not_a_usable_lead_id_still_prints(tmp_path, home):
    # `exec-1.usage` is a usable session id and not a usable lead id: its executor line is printed
    sdir = home / "sessions" / "exec-1.usage"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({"sid": "exec-1.usage", "lead": "lead-1", "packets": 1}))
    (sdir / "status").write_text("idle\n")
    r = pilead("status", "--home", str(home), "exec-1.usage")
    assert (r.returncode, r.stdout, r.stderr) == (0, "pkt 0001 idle · ctx -\n", "")


def test_status_id_that_is_neither_a_usable_lead_id_nor_a_usable_session_id_prints_nothing(tmp_path, home):
    # `a..b-101010` names a directory holding a readable record, but it is not a usable session id
    # (nor a usable lead id): nothing is read, nothing is printed, nothing is written
    bad = "a..b-101010"
    bad_dir = home / "sessions" / bad
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text(json.dumps({"sid": bad, "lead": "lead-1", "packets": 1}))
    (bad_dir / "status").write_text("idle\n")
    before = tree(tmp_path)
    out = pilead("status", "--home", str(home), bad)
    assert (out.returncode, out.stdout, out.stderr) == (0, "", "")
    assert tree(tmp_path) == before


def test_lead_handle_is_none_for_an_unusable_lead(home, x_json, monkeypatch):
    assert launch._lead_handle(home, "../../x") is None
    monkeypatch.setenv("PI_SESSION_ID", "../../x")
    monkeypatch.setenv("ITERM_SESSION_ID", "w0t0p0:LIVE-HANDLE")
    assert launch._lead_handle(home, "../../x") is None


# ── list and stats --lead: no path is built from the value ───────────────────
def test_list_and_stats_lead_filter_build_no_path(tmp_path, home, x_json):
    assert lead_start(home, "lead-1").returncode == 0
    for verb in ("list", "stats"):
        ghost = pilead(verb, "--home", str(home), "--lead", "ghost")
        before = outside(tmp_path, home)
        r = pilead(verb, "--home", str(home), "--lead", "../../x")
        assert r.returncode == ghost.returncode, (verb, r.stderr)
        assert outside(tmp_path, home) == before, verb


# ── usage's lead cache path follows the one rule ─────────────────────────────
@pytest.mark.parametrize("sid", USABLE)
def test_lead_cache_path_for_a_usable_lead_is_under_usage_leads(tmp_path, sid):
    from pilead import usage
    assert usage.lead_cache_path(tmp_path, sid) == tmp_path / "usage" / "leads" / f"{sid}.json"


@pytest.mark.parametrize("sid", NOT_USABLE + [None, 7, b"lead-1", ["lead-1"]])
def test_lead_cache_path_for_an_unusable_lead_is_none(tmp_path, sid):
    from pilead import usage
    assert usage.lead_cache_path(tmp_path, sid) is None

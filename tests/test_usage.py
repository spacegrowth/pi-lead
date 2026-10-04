"""lib/pilead/usage.py: finding a pi session log, reading its token usage and live context, the cache,
the model's window, the cells and the heaviness rule. Every log is a small synthetic file written here
(the shapes of pi 0.87.1's docs/session-format.md); no real session log is read, copied or quoted.
conftest.py points PI_CODING_AGENT_DIR at an empty dir under tmp_path."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import usage  # noqa: E402

TS = "2026-09-27T10-00-00-000Z"
_ids = iter(range(10 ** 6))


# ── synthetic logs ───────────────────────────────────────────────────────────────────────────────
def agent_dir():
    return Path(os.environ["PI_CODING_AGENT_DIR"])


def encoded(cwd):
    """pi's getDefaultSessionDirPath: the leading separator dropped, every / \\ : made a -."""
    return "--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", str(cwd))) + "--"


def u(i=0, o=0, cr=0, cw=0, total=None, cost=0.0, **extra):
    """A pi usage object; totalTokens is the four fields' sum unless given (None → the sum)."""
    d = {"input": i, "output": o, "cacheRead": cr, "cacheWrite": cw,
         "totalTokens": i + o + cr + cw if total is None else total,
         "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost}}
    d.update(extra)
    return d


def assistant(usage_, stop="stop", provider="anthropic", model="claude-sonnet-5",
              ts="2026-09-27T10:01:00.000Z", api="anthropic-messages"):
    m = {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "api": api, "provider": provider,
         "model": model, "stopReason": stop, "timestamp": 1790503260000}
    if usage_ is not None:
        m["usage"] = usage_
    if stop == "error":
        m["errorMessage"] = "synthetic failure"
    return {"type": "message", "id": f"e{next(_ids)}", "parentId": None, "timestamp": ts, "message": m}


def deepseek(usage_, stop="stop", **k):
    return assistant(usage_, stop, provider="deepseek", model="deepseek-v4-flash", api="openai-completions", **k)


def tool_result(usage_=None):
    m = {"role": "toolResult", "toolCallId": "t1", "toolName": "bash", "content": [], "isError": False,
         "timestamp": 1790503260000}
    if usage_ is not None:
        m["usage"] = usage_
    return {"type": "message", "id": f"e{next(_ids)}", "parentId": None, "timestamp": "2026-09-27T10:01:00.000Z",
            "message": m}


def user(text="hi"):
    return {"type": "message", "id": f"e{next(_ids)}", "parentId": None, "timestamp": "2026-09-27T10:00:30.000Z",
            "message": {"role": "user", "content": text, "timestamp": 1790503230000}}


def compaction(usage_=None):
    e = {"type": "compaction", "id": f"e{next(_ids)}", "parentId": None, "timestamp": "2026-09-27T10:02:00.000Z",
         "summary": "earlier work", "firstKeptEntryId": "e0", "tokensBefore": 100000}
    if usage_ is not None:
        e["usage"] = usage_
    return e


def header(sid, cwd):
    return {"type": "session", "version": 3, "id": sid, "timestamp": "2026-09-27T10:00:00.000Z", "cwd": str(cwd)}


def write_log(sid, cwd, entries, directory=None, ts=TS, head=None):
    """A session log at pi's default place for `cwd` (or in `directory`); entries may be dicts, str
    lines or raw bytes. Returns its path."""
    d = Path(directory) if directory is not None else agent_dir() / "sessions" / encoded(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ts}_{sid}.jsonl"
    out = b""
    for e in [header(sid, cwd) if head is None else head, *entries]:
        out += (e if isinstance(e, bytes) else (e if isinstance(e, str) else json.dumps(e)).encode()) + b"\n"
    p.write_bytes(out)
    return p


# ── locate ───────────────────────────────────────────────────────────────────────────────────────
def test_locate_default_layout(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p = write_log("s1", wt, [assistant(u(10, 5))])
    assert p.parent.name == encoded(wt) and p.parent.parent == agent_dir() / "sessions"
    assert usage.locate("s1", wt) == p
    assert usage.locate("s1") == p  # every project directory is searched


def test_locate_env_session_dir(tmp_path, monkeypatch):
    flat = tmp_path / "flat"
    p = write_log("s1", tmp_path / "wt", [assistant(u(10))], directory=flat)
    write_log("s1", tmp_path / "wt", [assistant(u(10))], ts="2020-01-01T00-00-00-000Z")  # default place: ignored
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(flat))
    assert usage.session_dirs(tmp_path / "wt") == [flat]
    assert usage.locate("s1", tmp_path / "wt") == p


def test_locate_session_dir_in_agent_settings(tmp_path):
    flat = tmp_path / "agent-flat"
    p = write_log("s1", tmp_path / "wt", [assistant(u(10))], directory=flat)
    (agent_dir() / "settings.json").write_text(json.dumps({"sessionDir": str(flat)}))
    assert usage.session_dirs(tmp_path / "wt") == [flat]
    assert usage.locate("s1", tmp_path / "wt") == p


def test_locate_project_settings_win_over_agent_settings(tmp_path):
    wt = tmp_path / "wt"
    (wt / ".pi").mkdir(parents=True)
    agent_flat, project_flat = tmp_path / "agent-flat", wt / "logs"
    write_log("s1", wt, [assistant(u(10))], directory=agent_flat)
    p = write_log("s1", wt, [assistant(u(20))], directory=project_flat)
    (agent_dir() / "settings.json").write_text(json.dumps({"sessionDir": str(agent_flat)}))
    (wt / ".pi" / "settings.json").write_text(json.dumps({"sessionDir": "logs"}))  # relative: from the worktree
    assert usage.session_dirs(wt) == [project_flat]
    assert usage.locate("s1", wt) == p


def test_a_matching_name_with_another_header_id_is_not_a_candidate(tmp_path):
    wt = tmp_path / "wt"
    write_log("s1", wt, [assistant(u(10))], head=header("someone-else", wt))
    assert usage.locate("s1", wt) is None
    write_log("s1", wt, [assistant(u(10))], ts="2026-09-27T11-00-00-000Z", head={"type": "note", "id": "s1"})
    assert usage.locate("s1", wt) is None


def test_two_candidates_the_one_in_the_worktree_wins(tmp_path):
    wt, other = tmp_path / "wt", tmp_path / "other"
    wt.mkdir()
    other.mkdir()
    mine = write_log("s1", wt, [assistant(u(10))])
    theirs = write_log("s1", other, [assistant(u(20))])
    os.utime(mine, (1_000_000_000, 1_000_000_000))  # the other one is newer, and still loses
    assert usage.locate("s1", wt) == mine
    assert usage.locate("s1", tmp_path / "elsewhere") == theirs  # none in the worktree: the newest
    assert usage.locate("s1") == theirs


def test_no_log_is_none(tmp_path):
    assert usage.locate("nothing-here", tmp_path) is None
    assert usage.locate("", tmp_path) is None
    assert not (agent_dir() / "sessions").exists()  # read-only: nothing was created


# ── read ─────────────────────────────────────────────────────────────────────────────────────────
def read_entries(tmp_path, entries, **k):
    return usage.read(write_log("s1", tmp_path / "wt", entries, **k))


def test_read_anthropic_shaped(tmp_path):
    r = read_entries(tmp_path, [user(), assistant(u(3, 200, 1000, 500, cost=0.01)),
                                assistant(u(4, 100, 1500, 20, cost=0.02), ts="2026-09-27T10:05:00.000Z")])
    assert set(r) == set(usage.KEYS)
    assert (r["requests"], r["input"], r["output"], r["cache_read"], r["cache_write"]) == (2, 7, 300, 2500, 520)
    assert r["prompt"] == 7 + 2500 + 520
    assert r["cost"] == pytest.approx(0.03)
    assert r["live"] == 4 + 100 + 1500 + 20 and r["max_context"] == 3 + 200 + 1000 + 500
    assert r["model"] == "anthropic/claude-sonnet-5" and r["models"] == {"anthropic/claude-sonnet-5": 2}
    assert r["last_ts"] == pytest.approx(1790503500.0)  # 2026-09-27T10:05:00Z
    assert r["cache_hit_rate"] == pytest.approx(2500 / 3027)
    assert r["compactions"] == 0 and r["path"].endswith("_s1.jsonl") and r["mb"] > 0


def test_read_deepseek_shaped_has_the_same_keys(tmp_path):
    a = read_entries(tmp_path, [assistant(u(3, 200, 1000, 0))])
    d = usage.read(write_log("s2", tmp_path / "wt", [deepseek(u(3, 200, 1000, 0, reasoning=50))]))
    assert set(d) == set(a) == set(usage.KEYS)
    assert d["model"] == "deepseek/deepseek-v4-flash" and d["models"] == {"deepseek/deepseek-v4-flash": 1}
    assert (d["requests"], d["prompt"], d["output"], d["live"]) == (1, 1003, 200, 1203)


@pytest.mark.parametrize("total", ["absent", 0])
def test_total_tokens_absent_or_zero_uses_the_sum(tmp_path, total):
    uo = u(10, 20, 30, 40)
    if total == "absent":
        del uo["totalTokens"]
    else:
        uo["totalTokens"] = 0
    r = read_entries(tmp_path, [assistant(uo)])
    assert r["live"] == 100 and r["requests"] == 1 and r["max_context"] == 100


def test_total_tokens_is_the_figure_when_present(tmp_path):
    assert read_entries(tmp_path, [assistant(u(10, 20, 30, 40, total=150))])["live"] == 150


def test_error_and_aborted_requests_are_not_good_and_do_not_move_live(tmp_path):
    r = read_entries(tmp_path, [assistant(u(100, 10)), assistant(u(), stop="error"),
                                assistant(u(), stop="aborted")])
    assert r["requests"] == 1 and r["live"] == 110 and r["models"] == {"anthropic/claude-sonnet-5": 1}
    # even with usage on it, an errored request is billed but does not move live
    r = read_entries(tmp_path, [assistant(u(100, 10)), assistant(u(900, 1), stop="error")], ts="2")
    assert r["requests"] == 1 and r["live"] == 110 and r["input"] == 1000


def test_zero_usage_on_a_stop_is_not_a_good_request(tmp_path):
    r = read_entries(tmp_path, [assistant(u())])
    assert r["requests"] == 0 and r["live"] is None and r["max_context"] == 0 and r["last_ts"] is None
    assert r["cache_hit_rate"] is None and r["cost"] == 0.0


def test_tool_result_usage_entry_and_compaction_count_in_billed_totals(tmp_path):
    r = read_entries(tmp_path, [
        assistant(u(10, 1, 0, 0, cost=0.1)),
        tool_result(u(20, 2, 0, 0, cost=0.2)),
        {"type": "usage", "id": "x", "parentId": None, "timestamp": "2026-09-27T10:01:30.000Z",
         "usage": u(30, 3, 5, 0, cost=0.3)},
        {"type": "branch_summary", "id": "y", "parentId": None, "timestamp": "2026-09-27T10:01:40.000Z",
         "summary": "s", "fromId": "e0", "usage": u(40, 4, 0, 0, cost=0.4)},
        compaction(u(50, 5, 0, 7, cost=0.5)),
    ])
    assert (r["input"], r["output"], r["cache_read"], r["cache_write"]) == (150, 15, 5, 7)
    assert r["prompt"] == 150 + 5 + 7 and r["cost"] == pytest.approx(1.5)
    assert r["requests"] == 1  # only the assistant message is a request
    assert r["compactions"] == 1


def test_after_a_compaction_live_is_unknown_until_the_next_good_request(tmp_path):
    r = read_entries(tmp_path, [assistant(u(140000, 10)), compaction()])
    assert r["live"] is None and r["max_context"] == 140010 and r["requests"] == 1
    r = read_entries(tmp_path, [assistant(u(140000, 10)), compaction(), assistant(u(), stop="aborted")], ts="2")
    assert r["live"] is None
    r = read_entries(tmp_path, [assistant(u(140000, 10)), compaction(), assistant(u(9000, 100))], ts="3")
    assert r["live"] == 9100 and r["compactions"] == 1 and r["requests"] == 2


def test_reasoning_and_cache_write_1h_are_not_added_twice(tmp_path):
    r = read_entries(tmp_path, [deepseek(u(10, 100, 0, 40, reasoning=60, cacheWrite1h=40))])
    assert (r["output"], r["cache_write"], r["prompt"], r["live"]) == (100, 40, 50, 150)


def test_bad_lines_do_not_raise_and_do_not_change_the_totals(tmp_path):
    good = [assistant(u(10, 1, 2, 3, cost=0.5))]
    clean = read_entries(tmp_path, good)
    noisy = read_entries(tmp_path, [
        b'{"type": "usage", "usage": {"input": 5\xff\xfe',  # invalid UTF-8, and not JSON
        "not json at all",
        "[1, 2, 3]",
        "",
        {"type": "custom_future_entry", "usage": "a string", "id": "z"},
        {"type": "usage", "id": "w"},  # no usage object
        {"type": "message", "message": "not an object"},
        assistant(None),  # an assistant message with no usage
        tool_result(None),
        {"type": "usage", "usage": {"input": "12", "output": None, "cacheRead": True, "cost": {"total": "x"}}},
        *good,
    ], ts="2")
    for k in ("requests", "input", "output", "cache_read", "cache_write", "prompt", "cost", "live", "max_context"):
        assert noisy[k] == clean[k], k


def test_no_header_or_empty_file_is_none(tmp_path):
    p = tmp_path / "x_s1.jsonl"
    p.write_text(json.dumps(assistant(u(10))) + "\n")
    assert usage.read(p) is None
    p.write_text("")
    assert usage.read(p) is None
    p.write_bytes(b"\xff\xfe not json\n")
    assert usage.read(p) is None
    assert usage.read(tmp_path / "missing.jsonl") is None


# ── for_session / for_lead: the cache ────────────────────────────────────────────────────────────
@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = usage.read

    def read(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(usage, "read", read)
    return calls


def session_home(tmp_path, sid="s1"):
    h = tmp_path / "home"
    (h / "sessions" / sid).mkdir(parents=True)
    (h / "leads").mkdir()
    return h


def test_second_call_uses_the_cache_and_appending_reparses(tmp_path, counted):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    p = write_log("s1", wt, [assistant(u(10, 1))])
    first = usage.for_session(h, "s1", wt)
    assert len(counted) == 1 and first["live"] == 11
    cache = json.loads((h / "sessions" / "s1" / "usage.json").read_text())
    assert cache["key"][0] == str(p) and cache["key"][1] == p.stat().st_size and cache["usage"] == first
    assert usage.for_session(h, "s1", wt) == first
    assert len(counted) == 1  # not re-parsed
    with open(p, "a") as f:
        f.write(json.dumps(assistant(u(20, 2))) + "\n")
    assert usage.for_session(h, "s1", wt)["live"] == 22
    assert len(counted) == 2


def test_a_corrupt_cache_reparses(tmp_path, counted):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    write_log("s1", wt, [assistant(u(10, 1))])
    usage.for_session(h, "s1", wt)
    for bad in ("{not json", "[]", json.dumps({"key": None, "usage": {}})):
        (h / "sessions" / "s1" / "usage.json").write_text(bad)
        assert usage.for_session(h, "s1", wt)["live"] == 11
    assert len(counted) == 4


def test_a_cache_with_the_right_key_but_an_old_shape_reparses(tmp_path, counted):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    write_log("s1", wt, [assistant(u(10, 1))])
    usage.for_session(h, "s1", wt)
    c = h / "sessions" / "s1" / "usage.json"
    d = json.loads(c.read_text())
    del d["usage"]["live"]
    c.write_text(json.dumps(d))
    assert usage.for_session(h, "s1", wt)["live"] == 11 and len(counted) == 2


def test_write_cache_false_writes_nothing(tmp_path):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    write_log("s1", wt, [assistant(u(10, 1))])
    before = sorted(p.relative_to(h) for p in h.rglob("*"))
    assert usage.for_session(h, "s1", wt, write_cache=False)["live"] == 11
    assert usage.for_lead(h, "s1", wt, write_cache=False)["live"] == 11
    assert sorted(p.relative_to(h) for p in h.rglob("*")) == before


def test_for_lead_caches_outside_the_lead_records(tmp_path):
    h, cwd = session_home(tmp_path), tmp_path / "lead-cwd"
    write_log("lead-9", cwd, [assistant(u(50, 5))])
    assert usage.for_lead(h, "lead-9", cwd)["live"] == 55
    assert json.loads((h / "usage" / "leads" / "lead-9.json").read_text())["usage"]["live"] == 55
    assert not (h / "leads" / "lead-9.usage.json").exists()


def test_no_log_is_none_and_writes_nothing(tmp_path):
    h = session_home(tmp_path)
    assert usage.for_session(h, "s1", tmp_path / "wt") is None
    assert usage.session_reading(h, "s1", tmp_path / "wt") == (None, None)
    assert not (h / "sessions" / "s1" / "usage.json").exists()


def test_an_unreadable_log_still_has_a_size(tmp_path):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    p = write_log("s1", wt, [])
    with open(p, "ab") as f:
        f.write(b"x" * 2048)
    orig = usage.read
    try:
        usage.read = lambda path: None  # as if the body could not be parsed
        got, mb = usage.session_reading(h, "s1", wt)
    finally:
        usage.read = orig
    assert got is None and mb == pytest.approx(p.stat().st_size / (1024 * 1024))


def test_snapshot():
    assert usage.snapshot(None) is None
    assert usage.snapshot({"prompt": 5, "output": 2, "requests": 1, "live": 3}) == {"prompt": 5, "output": 2,
                                                                                     "requests": 1}


# ── window ───────────────────────────────────────────────────────────────────────────────────────
MODELS = [{"provider": "anthropic", "id": "claude-sonnet-5", "context": "1M"},
          {"provider": "anthropic", "id": "claude-haiku-4-5", "context": "200K"},
          {"provider": "deepseek", "id": "deepseek-v4-flash", "context": "128K"},
          {"provider": "local", "id": "tiny", "context": "32768"},
          {"provider": "local", "id": "odd", "context": "lots"}]


def write_models(h, fetched=0):
    h.mkdir(parents=True, exist_ok=True)
    (h / "models.json").write_text(json.dumps({"fetched": fetched, "models": MODELS}, indent=2) + "\n")


@pytest.fixture
def no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError(f"usage.window ran a subprocess: {a!r}")

    for name in ("run", "Popen", "check_output"):
        monkeypatch.setattr(subprocess, name, boom)


@pytest.mark.parametrize("model,want", [
    ("anthropic/claude-sonnet-5", 1_000_000), ("anthropic/claude-haiku-4-5", 200_000),
    ("deepseek/deepseek-v4-flash", 128_000), ("local/tiny", 32768),
    ("local/odd", None), ("anthropic/not-listed", None), ("claude-sonnet-5", None), (None, None),
])
def test_window_from_models_json_without_running_anything(tmp_path, no_subprocess, model, want):
    import time
    write_models(tmp_path, fetched=int(time.time()))
    assert usage.window(tmp_path, model) == want


def test_window_with_no_models_json_is_none(tmp_path, no_subprocess):
    assert usage.window(tmp_path, "anthropic/claude-sonnet-5") is None
    assert not (tmp_path / "models.json").exists()


def test_a_stale_models_json_is_read_as_it_is_and_left_untouched(tmp_path, no_subprocess):
    """fetched = 0 is older than models.MAX_AGE, so models.load would refresh it (run `pi`); window does not."""
    from pilead import models
    write_models(tmp_path, fetched=0)
    before = (tmp_path / "models.json").read_bytes()
    assert json.loads(before)["fetched"] < __import__("time").time() - models.MAX_AGE
    assert usage.window(tmp_path, "anthropic/claude-haiku-4-5") == 200_000
    assert (tmp_path / "models.json").read_bytes() == before


@pytest.mark.parametrize("text,want", [("1M", 1_000_000), ("200K", 200_000), ("128K", 128_000), ("32768", 32768),
                                       ("1.5M", 1_500_000), ("200k", 200_000), ("", None), ("-", None), ("0", None)])
def test_parse_window(text, want):
    assert usage.parse_window(text) == want


# ── cells ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,want", [(0, "0"), (999, "999"), (1234, "1.2k"), (12345, "12k"), (1234567, "1.2M")])
def test_human_tokens(n, want):
    assert usage.human_tokens(n) == want


def test_usage_cell():
    assert usage.usage_cell(None) == "-"
    assert usage.usage_cell({"prompt": 1_234_567, "output": 34_000}) == "1.2M/34k"
    assert usage.usage_cell({"prompt": 0, "output": 0}) == "0/0"


def test_ctx_cell():
    base = {"live": 146_000, "max_context": 150_000}
    assert usage.ctx_cell(base, 1_000_000) == "146k/1M"
    assert usage.ctx_cell(base, 200_000) == "146k/200k"
    assert usage.ctx_cell(base, None) == "146k"  # window unknown: the live figure alone
    assert usage.ctx_cell({"live": None, "max_context": 150_000}, 1_000_000) == "-"
    assert usage.ctx_cell(None, 1_000_000) == "-"
    assert usage.ctx_cell({"live": 90_000, "max_context": 250_000}, 200_000) == "90k/200k !"
    assert usage.ctx_cell({"live": 90_000, "max_context": 250_000}, None) == "90k"


# ── the heaviness rule ───────────────────────────────────────────────────────────────────────────
def test_is_heavy_by_tokens():
    assert usage.is_heavy({"live": 150_000}, None, 150_000, 5) is True
    assert usage.is_heavy({"live": 149_999}, 99.0, 150_000, 5) is False  # tokens decide; the MB is not asked


def test_is_heavy_by_mb_when_usage_is_none_or_live_is_none():
    assert usage.is_heavy(None, 5.0, 150_000, 5) is True
    assert usage.is_heavy(None, 4.9, 150_000, 5) is False
    assert usage.is_heavy({"live": None}, 6.0, 150_000, 5) is True
    assert usage.is_heavy({"live": None}, 1.0, 150_000, 5) is False


def test_not_heavy_when_both_are_unknown():
    assert usage.is_heavy(None, None, 1, 0.001) is False
    assert usage.is_heavy({"live": None}, None, 1, 0.001) is False


def test_heavy_reading_text():
    assert usage.heavy_reading_text({"live": 164_000}, 9.0) == "164k ctx"
    assert usage.heavy_reading_text(None, 6.25) == "6.2MB (session log unreadable for usage; MB proxy)"
    assert usage.heavy_reading_text({"live": None}, 6.0) == "6.0MB (live context unknown; MB proxy)"
    assert usage.heavy_reading_text(None, None) == "unknown"


def test_approaching():
    assert usage.is_approaching({"live": 120_000}, 120_000, 150_000) is True
    assert usage.is_approaching({"live": 119_999}, 120_000, 150_000) is False
    assert usage.is_approaching({"live": 150_000}, 120_000, 150_000) is False  # heavy, not approaching
    assert usage.is_approaching({"live": None}, 120_000, 150_000) is False
    assert usage.is_approaching(None, 120_000, 150_000) is False


# ── the active branch (pi's SessionManager: leaf = the last entry; path = parentId links to the root) ──
def link(entry, eid, parent):
    """The entry with an explicit id and parentId (the writers above leave every parentId null)."""
    return {**entry, "id": eid, "parentId": parent}


def branch_log(tmp_path, abandoned_tail=(), active_tail=()):
    """u1 → a1 (10k) → {abandoned: a2 (90k, later, larger) + abandoned_tail} and {active: branch_summary →
    active_tail}; the active branch's last entry is the file's last line, so it is pi's leaf."""
    entries = [link(user(), "u1", None), link(assistant(u(10_000, 100)), "a1", "u1"),
               link(assistant(u(90_000, 900), ts="2026-09-27T11:00:00.000Z"), "a2", "a1")]
    prev = "a2"
    for i, e in enumerate(abandoned_tail):
        entries.append(link(e, f"x{i}", prev))
        prev = f"x{i}"
    entries.append({"type": "branch_summary", "id": "b1", "parentId": "a1", "timestamp": "2026-09-27T12:00:00.000Z",
                    "fromId": prev, "summary": "tried another approach"})
    prev = "b1"
    for i, e in enumerate(active_tail):
        entries.append(link(e, f"y{i}", prev))
        prev = f"y{i}"
    return read_entries(tmp_path, entries)


def test_live_follows_the_active_branch_and_billing_counts_both(tmp_path):
    r = branch_log(tmp_path)
    assert r["live"] == 10_100 and r["last_ts"] == usage._ts("2026-09-27T10:01:00.000Z")
    assert r["max_context"] == 90_900  # the largest good request anywhere in the file
    assert r["requests"] == 2 and r["input"] == 100_000 and r["output"] == 1000 and r["prompt"] == 100_000


def test_the_model_is_the_active_branchs(tmp_path):
    r = branch_log(tmp_path, abandoned_tail=[deepseek(u(5, 5))])
    assert r["model"] == "anthropic/claude-sonnet-5" and r["models"]["deepseek/deepseek-v4-flash"] == 1


def test_a_compaction_on_the_abandoned_branch_does_not_make_live_unknown(tmp_path):
    r = branch_log(tmp_path, abandoned_tail=[compaction(u(1, 1))])
    assert r["live"] == 10_100 and r["compactions"] == 1 and r["input"] == 100_001


def test_a_compaction_on_the_active_branch_does(tmp_path):
    r = branch_log(tmp_path, active_tail=[compaction()])
    assert r["live"] is None
    r = branch_log(tmp_path, active_tail=[compaction(), assistant(u(3_000, 30))])
    assert r["live"] == 3_030


def test_a_dangling_parent_or_a_cycle_falls_back_to_file_order(tmp_path):
    good = link(assistant(u(10_000, 100)), "a1", None)
    later = link(assistant(u(90_000, 900)), "a2", None)
    dangling = [good, later, {"type": "label", "id": "l1", "parentId": "nowhere", "timestamp": "t", "targetId": "a1"}]
    assert read_entries(tmp_path, dangling)["live"] == 90_900  # file order: the last good request
    cycle = [link(assistant(u(10_000, 100)), "c1", "c2"), link(assistant(u(90_000, 900)), "c2", "c1")]
    assert read_entries(tmp_path, cycle, ts="2")["live"] == 90_900


def test_a_log_without_parent_links_reads_as_today(tmp_path):
    r = read_entries(tmp_path, [assistant(u(90_000, 900)), assistant(u(10_000, 100)), assistant(u(), stop="aborted")])
    assert r["live"] == 10_100 and r["max_context"] == 90_900 and r["requests"] == 2


def test_an_entry_link_is_read_without_a_full_parse_and_with_one(tmp_path):
    # the fast path: a line that carries no usage / compaction / assistant is linked by its prefix
    head = '{"type":"custom","id":"k1","parentId":"a1","timestamp":"t","customType":"x","data":{}}'
    r = read_entries(tmp_path, [link(user(), "u1", None), link(assistant(u(10_000, 100)), "a1", "u1"),
                                link(assistant(u(90_000, 900)), "a2", "a1"), head])
    assert r["live"] == 10_100  # the leaf k1 hangs off a1: a2 is off the branch
    # the same line with its keys in another order is parsed in full, to the same answer
    other = '{"id":"k1","type":"custom","parentId":"a1","timestamp":"t"}'
    r = read_entries(tmp_path, [link(user(), "u1", None), link(assistant(u(10_000, 100)), "a1", "u1"),
                                link(assistant(u(90_000, 900)), "a2", "a1"), other], ts="2")
    assert r["live"] == 10_100


# ── check: a log that was found but could not be read ─────────────────────────────────────────────
def _check_session(tmp_path):
    h = Path(os.environ["PI_LEAD_HOME"])
    sdir = h / "sessions" / "s1"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({"sid": "s1", "lead": "l", "worktree": str(tmp_path), "topic": "t",
                                                "model": "anthropic/claude-sonnet-5", "packets": 1}))
    (sdir / "status").write_text("busy\n")
    (sdir / "packet-0001.md").write_text("GOAL\n")


def test_check_says_a_found_log_could_not_be_read(tmp_path, monkeypatch, capsys):
    from pilead import cli
    _check_session(tmp_path)
    bad = tmp_path / "headerless.jsonl"
    bad.write_text('{"type":"message"}\n')
    monkeypatch.setattr(usage, "locate", lambda sid, worktree=None: bad)
    assert cli.main(["check", "s1"]) in (0, None)
    out, err = capsys.readouterr()
    assert out == "busy\n"
    assert err == ("pilead: session log for s1 could not be read; tokens and context are unknown\n"
                   "pilead: s1 is an orphan — its lead l is no longer registered; pilead adopt s1\n")  # p22


def test_check_with_no_log_says_none_was_found(tmp_path, capsys):
    from pilead import cli
    _check_session(tmp_path)
    assert cli.main(["check", "s1"]) in (0, None)
    assert capsys.readouterr().err == ("pilead: no session log found for s1; tokens and context are unknown\n"
                                       "pilead: s1 is an orphan — its lead l is no longer registered; pilead adopt s1\n")  # p22


# ── last_stop: the last assistant message on the active branch (the status engine's pause) ───────
def test_last_stop_is_the_last_assistant_message_in_file_order_without_links(tmp_path):
    err = assistant(u(0, 0), stop="error")
    r = read_entries(tmp_path, [assistant(u(10, 1)), err])
    assert r["last_stop"] == {"stopReason": "error", "errorMessage": "synthetic failure"}
    r = read_entries(tmp_path, [err, assistant(u(10, 1)), user()])
    assert r["last_stop"] == {"stopReason": "stop", "errorMessage": None}
    assert read_entries(tmp_path, [user()])["last_stop"] is None


def test_last_stop_follows_the_active_branch(tmp_path):
    err = link(assistant(u(0, 0), stop="error"), "x1", "a1")  # written after the active branch's a2
    entries = [link(user(), "u1", None), link(assistant(u(10, 1)), "a1", "u1"),
               link(assistant(u(20, 2)), "a2", "a1"), err, link(user("more"), "u2", "a2")]
    assert read_entries(tmp_path, entries)["last_stop"]["stopReason"] == "stop"
    entries[-1] = link(user("more"), "u2", "x1")
    assert read_entries(tmp_path, entries)["last_stop"]["stopReason"] == "error"


def test_last_stop_keeps_a_bounded_error_text(tmp_path):
    err = assistant(u(0, 0), stop="error")
    err["message"]["errorMessage"] = "e" * 5000
    assert read_entries(tmp_path, [err])["last_stop"]["errorMessage"] == "e" * usage.ERROR_TEXT_LEN


def test_public_leaves_out_path_and_last_stop():
    assert usage.public(None) is None
    assert usage.public({"live": 1, "path": "/x", "last_stop": {}}) == {"live": 1}


# ── the lead cache path cannot leave usage/leads/ ────────────────────────────────────────────────
@pytest.mark.parametrize("sid", ["a/b", "../evil", "..", "x..y", "a\\b", "nul\0byte", ""])
def test_a_lead_sid_that_could_leave_usage_leads_is_not_cached(tmp_path, sid):
    h = tmp_path / "home"
    h.mkdir()
    assert usage.lead_cache_path(h, sid) is None


def test_an_unsafe_lead_sid_is_read_but_writes_nothing(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    sid, cwd = "../evil", tmp_path / "lead-cwd"
    p = write_log("lead-9", cwd, [assistant(u(50, 5))])
    monkeypatch.setattr(usage, "locate", lambda s, w=None: p if s == sid else None)
    before = sorted(x.relative_to(tmp_path) for x in tmp_path.rglob("*"))
    for _ in range(2):
        assert usage.for_lead(h, sid, cwd)["live"] == 55  # the reading is still returned
    assert sorted(x.relative_to(tmp_path) for x in tmp_path.rglob("*")) == before


def test_a_lead_sid_with_a_dot_is_cached_under_usage_leads(tmp_path):
    h, cwd = tmp_path / "home", tmp_path / "lead-cwd"
    h.mkdir()
    write_log("lead.v2", cwd, [assistant(u(50, 5))])
    assert usage.lead_cache_path(h, "lead.v2") == h / "usage" / "leads" / "lead.v2.json"
    assert usage.for_lead(h, "lead.v2", cwd)["live"] == 55
    assert [x.name for x in (h / "usage" / "leads").iterdir()] == ["lead.v2.json"]


def test_for_lead_without_write_cache_creates_no_directory(tmp_path):
    h, cwd = tmp_path / "home", tmp_path / "lead-cwd"
    h.mkdir()
    write_log("lead-9", cwd, [assistant(u(50, 5))])
    assert usage.for_lead(h, "lead-9", cwd, write_cache=False)["live"] == 55
    assert list(h.iterdir()) == []


# ── the usage cache keeps 120 characters of an error message, `limit` and the pattern (p24 change 6) ──
def _long_error(prefix_len=0, wording="quota exceeded", total=1000):
    err = assistant(u(0, 0), stop="error")
    msg = ("e" * prefix_len + wording).ljust(total, "x")
    err["message"]["errorMessage"] = msg
    return err, msg


def _set_pattern(h, value):
    (h / "config.json").write_text(json.dumps({"usage_limit_pattern": value}))


def test_the_cache_holds_120_characters_limit_and_the_pattern(tmp_path):
    from pilead import config
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, msg = _long_error()
    write_log("s1", wt, [err])
    got = usage.for_session(h, "s1", wt)
    d = json.loads((h / "sessions" / "s1" / "usage.json").read_text())
    assert d["pattern"] == config.USAGE_LIMIT_PATTERN
    assert d["usage"]["last_stop"] == {"stopReason": "error", "errorMessage": msg[:120], "limit": True}
    assert d["usage"] == got  # what is written is what is returned
    assert usage.CACHED_ERROR_TEXT_LEN == 120
    assert msg[120:240] not in (h / "sessions" / "s1" / "usage.json").read_text()


def test_a_limit_wording_past_120_characters_is_still_seen_and_not_kept(tmp_path):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, msg = _long_error(prefix_len=500)
    write_log("s1", wt, [err])
    ls = usage.for_session(h, "s1", wt)["last_stop"]
    assert ls["limit"] is True and ls["errorMessage"] == "e" * 120
    assert "quota" not in json.dumps(json.loads((h / "sessions" / "s1" / "usage.json").read_text())["usage"])
    assert usage.for_session(h, "s1", wt)["last_stop"] == ls  # from the cache


def test_changing_the_pattern_parses_again_and_changes_the_answer(tmp_path, counted):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, _ = _long_error(prefix_len=500)
    write_log("s1", wt, [err])
    assert usage.for_session(h, "s1", wt)["last_stop"]["limit"] is True
    assert usage.for_session(h, "s1", wt)["last_stop"]["limit"] is True and len(counted) == 1
    _set_pattern(h, "never matches this")
    assert usage.for_session(h, "s1", wt)["last_stop"]["limit"] is False and len(counted) == 2
    assert json.loads((h / "sessions" / "s1" / "usage.json").read_text())["pattern"] == "never matches this"
    _set_pattern(h, None)  # null: the built-in again
    assert usage.for_session(h, "s1", wt)["last_stop"]["limit"] is True and len(counted) == 3


def test_a_pattern_that_does_not_compile_is_the_built_in_one(tmp_path):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, _ = _long_error()
    write_log("s1", wt, [err])
    _set_pattern(h, "(unclosed")
    assert usage.for_session(h, "s1", wt)["last_stop"]["limit"] is True


def test_a_cache_in_the_old_shape_is_parsed_again_and_replaced(tmp_path, counted):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, msg = _long_error()
    p = write_log("s1", wt, [err])
    st = p.stat()
    old = usage.read(p)
    counted.clear()
    c = h / "sessions" / "s1" / "usage.json"
    c.write_text(json.dumps({"key": [str(p), st.st_size, st.st_mtime_ns], "usage": old}))  # no "pattern"
    got = usage.for_session(h, "s1", wt)
    assert len(counted) == 1 and got["last_stop"]["errorMessage"] == msg[:120]
    d = json.loads(c.read_text())
    assert "pattern" in d and len(d["usage"]["last_stop"]["errorMessage"]) == 120


def test_an_old_cache_read_without_writing_is_left_as_it_is(tmp_path):
    h, wt = session_home(tmp_path), tmp_path / "wt"
    err, _ = _long_error()
    p = write_log("s1", wt, [err])
    st = p.stat()
    c = h / "sessions" / "s1" / "usage.json"
    c.write_text(json.dumps({"key": [str(p), st.st_size, st.st_mtime_ns], "usage": usage.read(p)}))
    before = c.read_bytes()
    assert usage.for_session(h, "s1", wt, write_cache=False)["last_stop"]["limit"] is True
    assert c.read_bytes() == before


def test_read_itself_still_returns_the_long_text_without_limit(tmp_path):
    err, msg = _long_error()
    r = read_entries(tmp_path, [err])
    assert r["last_stop"] == {"stopReason": "error", "errorMessage": msg[:usage.ERROR_TEXT_LEN]}
    assert usage.ERROR_TEXT_LEN == 1000


def test_the_lead_cache_is_cut_too(tmp_path):
    h, cwd = session_home(tmp_path), tmp_path / "lead-cwd"
    err, msg = _long_error()
    write_log("lead-9", cwd, [err])
    usage.for_lead(h, "lead-9", cwd)
    d = json.loads((h / "usage" / "leads" / "lead-9.json").read_text())
    assert d["usage"]["last_stop"]["errorMessage"] == msg[:120] and d["usage"]["last_stop"]["limit"] is True

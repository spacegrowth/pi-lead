"""`pilead list`: LEADS and EXECUTORS over fixture homes this file writes, with synthetic pi session
logs (tests/test_usage.py's writers). Run in-process (`cli.main`) with the vendored iterm backend's
`running` / `run_osascript` monkeypatched — a lead's tab is looked up by its recorded handle only — and
through bin/pilead where the output must be a pipe."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, state  # noqa: E402
from pilead.launch import backend  # noqa: E402

LIVE_UUID = "AAAAAAAA-0000-4000-8000-000000000001"
ESC = "\x1b"


@pytest.fixture(autouse=True)
def terminal(monkeypatch, tmp_path):
    """iTerm "running"; a session exists only when its id is LIVE_UUID. No real osascript, ever. The
    cwd is a directory named after no project, and no lead identity is in the environment."""
    it = backend.by_name("iterm")
    calls = []

    def run_osascript(script, timeout=None):
        calls.append(script)
        return subprocess.CompletedProcess(["osascript"], 0, "true\n" if LIVE_UUID in script else "false\n", "")

    monkeypatch.setattr(it, "running", lambda: True)
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    for var in ("PI_LEAD_SID", "PI_SESSION_ID", "NO_COLOR"):
        monkeypatch.delenv(var, raising=False)
    nowhere = tmp_path / "no-such-project-dir"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    return calls


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def iso(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def lead(tmp_path, sid, project, handle=None, started=None, cwd=None, raw=None):
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (d / f"{sid}.json").write_text(raw)
        return
    rec = {"sid": sid, "project": project, "cwd": str(cwd or tmp_path / f"cwd-{sid}"), "inbox": "",
           "iterm_handle": handle, "terminal": "iTerm.app" if handle else None}
    if started is not None:
        rec["started"] = started
    (d / f"{sid}.json").write_text(json.dumps(rec))


def session(tmp_path, sid, status="busy", lead_sid="lead-a", topic="topic", model="anthropic/claude-sonnet-5",
            packets=1, report=False, created=None, raw=None):
    sdir = H(tmp_path) / "sessions" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (sdir / "meta.json").write_text(raw)
        return sdir
    wt = tmp_path / f"wt-{sid}"
    wt.mkdir(exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": lead_sid, "worktree": str(wt), "topic": topic, "model": model, "status": status,
        "created": created or iso(0), "packets": packets, "label": f"[Exec] {sid}", "layout": "tab"}))
    (sdir / "status").write_text(status + "\n")
    (sdir / f"packet-{packets:04d}.md").write_text("GOAL\n")
    if report:
        (sdir / f"report-{packets:04d}.md").write_text("Done.\nStatus: clean\n")
    return sdir


def health(tmp_path, sid, **fields):
    """wake/<sid>/health.json as the extension writes it: this (live) process, a beat just now, nothing
    wrong — its wake is `ok` unless `fields` say otherwise."""
    h = {"sid": sid, "pid": os.getpid(), "started": iso(60), "beat": iso(0), "watching": True, "poll_seconds": 3,
         "delivered": 0, "last_delivered": None, "pending": 0, "last_error": None, "last_error_at": None,
         "ended": None, **fields}
    d = H(tmp_path) / "wake" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps(h))


def models(tmp_path, *entries):
    H(tmp_path).mkdir(parents=True, exist_ok=True)
    (H(tmp_path) / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": p, "id": i, "context": c} for p, i, c in entries]}))


def run(capsys, *args):
    assert cli.main(["list", *args]) in (0, None)
    return capsys.readouterr().out


def section(out, name):
    """The lines of one section (LEADS / EXECUTORS), header line first, footnotes included."""
    lines = out.splitlines()
    i = lines.index(name)
    j = lines.index("", i) if "" in lines[i:] else len(lines)
    return lines[i + 1:j]


def rows(out, name):
    """{first cell: [cells]} of one section's table rows (cells split on 2+ spaces)."""
    sec = section(out, name)
    return {re.split(r"\s{2,}", ln)[0]: re.split(r"\s{2,}", ln) for ln in sec[1:] if not ln.startswith("  ")}


def lead_rows(out):
    """{SESSION: [cells]} of the LEADS table."""
    sec = section(out, "LEADS")
    return {re.split(r"\s{2,}", ln)[1]: re.split(r"\s{2,}", ln) for ln in sec[1:] if not ln.startswith("  ")}


def footnotes(out):
    # p20j: the CTX/TOKENS legend after the EXECUTORS rows is not a footnote
    return [ln for ln in out.splitlines() if ln.startswith("  ") and not ln.startswith("  CTX = live/window")]


# ── LEADS: LIVE ──────────────────────────────────────────────────────────────────────────────────
def test_each_live_value(tmp_path, capsys, terminal):
    lead(tmp_path, "l-live", "p", handle=f"w0t0p0:{LIVE_UUID}")
    lead(tmp_path, "l-unreach", "p", started=iso(60))
    lead(tmp_path, "l-ghost", "p", started=iso(2 * 86400))
    lead(tmp_path, "l-stale-handle", "p", handle="w0t0p0:BBBBBBBB-0000-4000-8000-000000000002", started=iso(5000))
    lead(tmp_path, "l-broken", None, raw="{not json")
    out = run(capsys)
    live = {sid: cells[4] for sid, cells in lead_rows(out).items()}
    assert live == {"l-live": "live", "l-unreach": "unreachable", "l-ghost": "ghost", "l-stale-handle": "ghost",
                    "l-broken": "broken"}
    # a lead is asked by its handle only: exactly the two leads with one, never by title
    asked = sorted(uid for uid in (LIVE_UUID, "BBBBBBBB-0000-4000-8000-000000000002") for s in terminal if uid in s)
    assert len(terminal) == 2 and asked == ["AAAAAAAA-0000-4000-8000-000000000001", "BBBBBBBB-0000-4000-8000-000000000002"]
    assert f"  ⚠ broken: lead record {H(tmp_path) / 'leads' / 'l-broken.json'} cannot be read — inspect that file" in out


def test_a_lead_with_no_handle_is_never_asked(tmp_path, capsys, terminal):
    lead(tmp_path, "l1", "p", started=iso(10))
    run(capsys)
    run(capsys, "--json")
    assert terminal == []


def test_an_unaskable_terminal_is_not_live(tmp_path, capsys, monkeypatch):
    it = backend.by_name("iterm")
    monkeypatch.setattr(it, "run_osascript", lambda s, timeout=None: subprocess.CompletedProcess(["osascript"], 1, "", "x"))
    lead(tmp_path, "l-fresh", "p", handle=f"w0t0p0:{LIVE_UUID}", started=iso(30))
    lead(tmp_path, "l-old", "p", handle=f"w0t0p0:{LIVE_UUID}", started=iso(3600))
    live = {sid: cells[4] for sid, cells in lead_rows(run(capsys)).items()}
    assert live == {"l-fresh": "unreachable", "l-old": "ghost"}


# ── LEADS: LAST ACTIVE, MODEL, TERM, CTX, MB ─────────────────────────────────────────────────────
def test_last_active_from_the_log_from_started_and_dash(tmp_path, capsys):
    lead(tmp_path, "l-log", "p", started=iso(5 * 86400), cwd=tmp_path / "c1")
    log = write_log("l-log", tmp_path / "c1", [assistant(u(1000, 10), provider="deepseek", model="deepseek-v4-pro")])
    t = time.time() - 125
    os.utime(log, (t, t))
    lead(tmp_path, "l-started", "p", started=iso(3 * 3600 + 5))
    lead(tmp_path, "l-none", "p")
    r = lead_rows(run(capsys, "--all-leads"))
    assert r["l-log"][7] == "2m ago" and r["l-started"][7] == "3h ago" and r["l-none"][7] == "-"
    assert r["l-log"][2] == "deepseek-v4-pro" and r["l-started"][2] == "-"
    assert r["l-log"][3] == "?" and r["l-log"][5] == "1.0k" and r["l-log"][6] == "0.0"
    assert r["l-none"][5] == "-" and r["l-none"][6] == "-"  # unknown: never 0


def test_term_column(tmp_path, capsys):
    lead(tmp_path, "l-it", "p", handle=f"w0t0p0:{LIVE_UUID}")
    r = lead_rows(run(capsys))
    assert r["l-it"][3] == "iterm"


# ── LEADS: ghost leads of other projects ─────────────────────────────────────────────────────────
def ghost_setup(tmp_path):
    lead(tmp_path, "l-mine", "mine", handle=f"w0t0p0:{LIVE_UUID}")
    lead(tmp_path, "l-mine-ghost", "mine", started=iso(86400))
    lead(tmp_path, "l-other-ghost", "other", started=iso(86400))
    lead(tmp_path, "l-other-live", "other", handle=f"w0t0p0:{LIVE_UUID}")


def test_a_ghost_lead_of_another_project_is_hidden_and_counted(tmp_path, capsys):
    ghost_setup(tmp_path)
    out = run(capsys, "--lead", "l-mine")
    assert set(lead_rows(out)) == {"l-mine", "l-mine-ghost", "l-other-live"}
    assert "  1 ghost lead(s) of other projects hidden — `pilead list --all-leads` shows them" in out
    out = run(capsys, "--lead", "l-mine", "--all-leads")
    assert set(lead_rows(out)) == {"l-mine", "l-mine-ghost", "l-other-ghost", "l-other-live"}
    assert "hidden" not in out


def test_reference_project_from_the_environment_and_the_cwd(tmp_path, capsys, monkeypatch):
    ghost_setup(tmp_path)
    monkeypatch.setenv("PI_LEAD_SID", "l-other-live")
    assert set(lead_rows(run(capsys))) == {"l-mine", "l-other-ghost", "l-other-live"}
    monkeypatch.delenv("PI_LEAD_SID")
    monkeypatch.setenv("PI_SESSION_ID", "l-mine")
    assert "l-other-ghost" not in lead_rows(run(capsys))
    monkeypatch.delenv("PI_SESSION_ID")
    d = tmp_path / "x" / "other"
    d.mkdir(parents=True)
    monkeypatch.chdir(d)
    assert set(lead_rows(run(capsys))) == {"l-mine", "l-other-ghost", "l-other-live"}


def test_with_no_reference_project_every_lead_is_listed(tmp_path, capsys):
    ghost_setup(tmp_path)
    out = run(capsys)
    assert set(lead_rows(out)) == {"l-mine", "l-mine-ghost", "l-other-ghost", "l-other-live"}
    assert "hidden" not in out


# ── EXECUTORS: closed and dead ───────────────────────────────────────────────────────────────────
def test_closed_and_dead_are_hidden_counted_and_shown_with_closed(tmp_path, capsys):
    session(tmp_path, "a-live")
    session(tmp_path, "b-closed", status="closed")
    session(tmp_path, "c-dead", status="dead")
    out = run(capsys)
    assert set(rows(out, "EXECUTORS")) == {"a-live"}
    assert "  (+2 closed/dead hidden — pilead list --closed)" in out
    out = run(capsys, "--closed")
    assert list(rows(out, "EXECUTORS")) == ["a-live", "b-closed", "c-dead"]  # live first; same `created`: sid order
    assert "closed/dead hidden" not in out


def test_closed_is_capped_at_the_15_most_recently_created(tmp_path, capsys):
    session(tmp_path, "live-1")
    for i in range(17):
        session(tmp_path, f"old-{i:02d}", status="closed", created=iso(1000 * (17 - i)))
    out = run(capsys, "--closed")
    names = list(rows(out, "EXECUTORS"))
    assert names[0] == "live-1" and len(names) == 16
    assert names[1:] == [f"old-{i:02d}" for i in range(16, 1, -1)]  # newest first; old-00, old-01 left out
    assert "  (+2 closed/dead hidden — pilead list --closed)" in out


# ── EXECUTORS: --lead ────────────────────────────────────────────────────────────────────────────
def test_lead_scoping(tmp_path, capsys):
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    lead(tmp_path, "lead-b", "pb", handle=f"w0t0p0:{LIVE_UUID}")
    session(tmp_path, "s-a", lead_sid="lead-a")
    session(tmp_path, "s-b", lead_sid="lead-b")
    session(tmp_path, "s-none", lead_sid=None)
    assert set(rows(run(capsys, "--lead", "lead-a"), "EXECUTORS")) == {"s-a", "s-none"}
    r = rows(run(capsys), "EXECUTORS")
    assert set(r) == {"s-a", "s-b", "s-none"}
    assert (r["s-a"][4], r["s-b"][4], r["s-none"][4]) == ("pa", "pb", "-")


# ── footnotes ────────────────────────────────────────────────────────────────────────────────────
def test_no_footnote_when_nothing_applies(tmp_path, capsys):
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    health(tmp_path, "lead-a")  # its wake is ok: no WAKE footnote applies either
    session(tmp_path, "s-1")
    write_log("s-1", tmp_path / "wt-s-1", [assistant(u(1000, 10))])
    assert footnotes(run(capsys)) == []


def test_each_footnote_appears_when_it_applies(tmp_path, capsys):
    models(tmp_path, ("anthropic", "claude-haiku-4-5", "200K"))
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 320_000, 0))])
    lead(tmp_path, "lead-g", "pa", started=iso(86400))
    lead(tmp_path, "lead-x", None, raw="[]")
    session(tmp_path, "s-heavy")
    write_log("s-heavy", tmp_path / "wt-s-heavy", [assistant(u(10, 5, 160_000, 0))])
    session(tmp_path, "s-warm")
    write_log("s-warm", tmp_path / "wt-s-warm", [assistant(u(10, 5, 125_000, 0))])
    session(tmp_path, "s-over", model="anthropic/claude-haiku-4-5")
    write_log("s-over", tmp_path / "wt-s-over", [assistant(u(10, 5, 239_000, 0)), assistant(u(10, 5, 90_000, 0))])
    session(tmp_path, "s-bad", raw="{nope")
    session(tmp_path, "s-closed", status="closed")
    out = run(capsys)
    notes = footnotes(out)
    want = [
        "  ⚠ heavy lead: pa (320k ctx, lead line 300k)",
        "  ⚠ LIVE=ghost: pa (lead-g)",
        f"  ⚠ broken: lead record {H(tmp_path) / 'leads' / 'lead-x.json'} cannot be read",
        "  ⚠ WAKE=off: pa, pa — registered, but no pi session with the pi-lead extension is watching the inbox",
        "  ⚠ heavy: s-heavy (1 pkts / 160k ctx)",
        "  ⚠ approaching heavy: s-warm (1 pkts / 125k ctx) — past the 120k warn line, not yet the 150k heavy line",
        "  ⚠ s-over: a request of 239k was larger than the 200k window",
        f"  ⚠ broken: s-bad — {H(tmp_path) / 'sessions' / 's-bad' / 'meta.json'} cannot be read",
        "  (+1 closed/dead hidden — pilead list --closed)",
    ]
    for w in want:
        assert sum(n.startswith(w) for n in notes) == 1, (w, notes)
    assert len(notes) == len(want), notes
    assert rows(out, "EXECUTORS")["s-over"][7] == "90k/200k !"
    assert rows(out, "EXECUTORS")["s-bad"][1] == "broken"
    # s-heavy is only in the heavy line, not the approaching one
    assert "s-heavy" not in next(n for n in notes if "approaching" in n)


def test_a_lead_on_a_200k_window_is_heavy_at_context_nudge_tokens(tmp_path, capsys):
    models(tmp_path, ("anthropic", "claude-haiku-4-5", "200K"))
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 155_000, 0), model="claude-haiku-4-5")])
    assert any(n.startswith("  ⚠ heavy lead: pa (155k ctx, lead line 150k)") for n in footnotes(run(capsys)))


def test_lead_nudge_tokens_is_read_from_config(tmp_path, capsys):
    H(tmp_path).mkdir(parents=True, exist_ok=True)
    (H(tmp_path) / "config.json").write_text(json.dumps({"lead_nudge_tokens": 50_000}))
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 60_000, 0))])
    assert any(n.startswith("  ⚠ heavy lead: pa (60k ctx, lead line 50k)") for n in footnotes(run(capsys)))


def help_verbs():
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "pilead"), "--help"], capture_output=True, text=True).stdout
    return set(re.findall(r"^    ([a-z][a-z-]*)\s", out, re.M))


def test_no_footnote_names_a_verb_that_is_not_in_help(tmp_path, capsys):
    test_each_footnote_appears_when_it_applies(tmp_path, capsys)
    ghost_setup(tmp_path)
    out = run(capsys, "--lead", "l-mine") + run(capsys)
    named = set(re.findall(r"pilead ([a-z][a-z-]*)", "\n".join(footnotes(out))))
    assert named and named <= help_verbs(), named - help_verbs()


# ── cells ────────────────────────────────────────────────────────────────────────────────────────
def test_a_long_value_is_cut_and_the_columns_line_up(tmp_path, capsys):
    long_topic = "t" * 55
    session(tmp_path, "s-long", topic=long_topic)
    session(tmp_path, "s-short", topic="x")
    out = run(capsys)
    sec = section(out, "EXECUTORS")
    header = sec[0]
    r = rows(out, "EXECUTORS")
    assert r["s-long"][2] == "t" * 39 + "…"
    for name in ("STATUS", "PKT", "REPORTED"):
        col = header.index(name)
        for ln in sec[1:]:
            if ln.startswith(("  ⚠ orphan: ", "  CTX = live/window")):  # p22: a footnote; p20j: the legend
                continue
            assert ln[col - 2:col] == "  " and ln[col] != " ", (name, ln)
    assert all(ln[header.index("PKT"):header.index("PKT") + 4] == "0001" for ln in sec[1:]
               if not ln.startswith(("  ⚠ orphan: ", "  CTX = live/window")))  # p22: a footnote; p20j: the legend


def test_unknown_usage_is_a_dash_never_zero(tmp_path, capsys):
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    session(tmp_path, "s-1")
    out = run(capsys)
    e = rows(out, "EXECUTORS")["s-1"]
    assert (e[7], e[8]) == ("-", "-")
    lr = lead_rows(out)["lead-a"]
    assert (lr[5], lr[6]) == ("-", "-")
    assert not re.search(r"(^|\s)0(\s|$)", "\n".join(section(out, "EXECUTORS")[1:] + section(out, "LEADS")[1:]))


def test_tokens_cell_carries_the_cache_hit_rate(tmp_path, capsys):
    session(tmp_path, "s-1")
    write_log("s-1", tmp_path / "wt-s-1", [assistant(u(250, 10, 750, 0))])
    assert rows(run(capsys), "EXECUTORS")["s-1"][8] == "1.0k/10 75%"


def test_paused_is_shown_as_paused_limit(tmp_path, capsys):
    session(tmp_path, "s-p", status="paused")
    assert rows(run(capsys), "EXECUTORS")["s-p"][1] == "paused (limit)"


# ── output channels ──────────────────────────────────────────────────────────────────────────────
def test_piped_output_and_json_have_no_escape_codes(tmp_path):
    lead(tmp_path, "lead-g", "pa", started=iso(86400))
    session(tmp_path, "s-1", report=True)
    session(tmp_path, "s-2", status="closed")
    env = {**os.environ, "PI_LEAD_HOME": str(H(tmp_path)), "PATH": os.environ["PATH"]}
    env.pop("NO_COLOR", None)
    for args in ([], ["--json"], ["--closed"]):
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "pilead"), "list", *args], capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0 and r.stdout and ESC not in r.stdout, r.stderr


def test_json_is_unfiltered_and_has_the_keys(tmp_path, capsys):
    ghost_setup(tmp_path)
    lead(tmp_path, "l-bad", None, raw="{x")
    session(tmp_path, "s-1", lead_sid="l-mine")
    write_log("s-1", tmp_path / "wt-s-1", [assistant(u(250, 10, 750, 0))])
    session(tmp_path, "s-closed", status="closed", lead_sid="l-other-live")
    session(tmp_path, "s-dead", status="dead", lead_sid=None)
    session(tmp_path, "s-bad", raw="{")
    d = json.loads(run(capsys, "--json", "--lead", "l-mine"))
    assert set(d) == {"leads", "executors"}
    assert {x["sid"] for x in d["leads"]} == {"l-mine", "l-mine-ghost", "l-other-ghost", "l-other-live", "l-bad"}
    assert {x["sid"] for x in d["executors"]} == {"s-1", "s-closed", "s-dead", "s-bad"}
    ex = {x["sid"]: x for x in d["executors"]}
    assert ex["s-bad"] == {"sid": "s-bad", "broken": True}
    for k in ("sid", "lead", "worktree", "topic", "model", "created", "packets", "label", "layout",
              "status", "reported", "project", "usage", "ctx_window", "heavy", "approaching"):
        assert k in ex["s-1"], k
    assert ex["s-1"]["project"] == "mine" and ex["s-1"]["usage"]["prompt"] == 1000 and "path" not in ex["s-1"]["usage"]
    assert ex["s-dead"]["usage"] is None and ex["s-dead"]["project"] is None and ex["s-closed"]["status"] == "closed"
    ld = {x["sid"]: x for x in d["leads"]}
    assert ld["l-bad"] == {"sid": "l-bad", "broken": True}
    for k in ("sid", "project", "cwd", "live", "model", "usage", "ctx_window", "heavy", "last_active"):
        assert k in ld["l-mine"], k
    assert ld["l-mine"]["live"] == "live" and ld["l-other-ghost"]["live"] == "ghost"
    assert abs(state.age_seconds(ld["l-other-ghost"]["last_active"]) - 86400) <= 5  # from `started`, UTC ISO
    assert ld["l-mine"]["usage"] is None and ld["l-mine"]["last_active"] is None


def test_list_caches_lead_usage_outside_the_lead_records(tmp_path, capsys):
    """`list` caches a lead's usage in usage/leads/<sid>.json; leads/ keeps only the lead's record."""
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 1000, 0))])
    run(capsys)
    cache = H(tmp_path) / "usage" / "leads" / "lead-a.json"
    assert json.loads(cache.read_text())["usage"]["live"] == 1015
    assert sorted(p.name for p in (H(tmp_path) / "leads").iterdir()) == ["lead-a.json"]
    run(capsys, "--json")
    assert sorted(p.name for p in (H(tmp_path) / "leads").iterdir()) == ["lead-a.json"]


def test_a_second_list_does_not_reparse_the_leads_log(tmp_path, capsys, monkeypatch):
    from pilead import usage
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    log = write_log("lead-a", tmp_path / "lc", [assistant(u(10, 5, 1000, 0))])
    real, calls = usage.read, []
    monkeypatch.setattr(usage, "read", lambda p: calls.append(p) or real(p))
    run(capsys)
    assert calls == [log]
    run(capsys)
    run(capsys, "--json")
    assert calls == [log]


def test_a_stray_lead_usage_cache_is_not_a_lead(tmp_path, capsys):
    """An older build's leads/<sid>.usage.json beside the record: `list`, `list --json` and the board
    each show exactly one lead. A lead id with a dot in it is still a lead."""
    from pilead import review
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}", cwd=tmp_path / "lc")
    (H(tmp_path) / "leads" / "lead-a.usage.json").write_text(json.dumps(
        {"key": ["x", 1, 1], "usage": {"live": 1}}))
    out = run(capsys)
    assert list(lead_rows(out)) == ["lead-a"] and not any("broken" in f for f in footnotes(out))
    assert [ld["sid"] for ld in json.loads(run(capsys, "--json"))["leads"]] == ["lead-a"]
    assert [ld["session_id"] for ld in review.board_data(H(tmp_path))["leads"]] == ["lead-a"]
    lead(tmp_path, "lead.v2", "pb")
    assert sorted(ld["sid"] for ld in json.loads(run(capsys, "--json"))["leads"]) == ["lead-a", "lead.v2"]
    assert sorted(ld["session_id"] for ld in review.board_data(H(tmp_path))["leads"]) == ["lead-a", "lead.v2"]


def test_list_parses_an_executors_log_once(tmp_path, capsys, monkeypatch):
    """The status engine takes the reading `list` already made (the pause and the stall both need the
    log): one parse per session per command, none on a second run over an unchanged log (the cache)."""
    from pilead import usage
    sdir = session(tmp_path, "s-1")
    child = subprocess.Popen(["sleep", "60"])
    try:
        (sdir / "pid").write_text(f"{child.pid}\n")
        write_log("s-1", tmp_path / "wt-s-1", [assistant(u(10, 5))])
        real, calls = usage.read, []
        monkeypatch.setattr(usage, "read", lambda p: calls.append(p) or real(p))
        run(capsys)
        assert len(calls) == 1
        run(capsys)
        assert len(calls) == 1
    finally:
        child.terminate()
        child.wait()


# ── a lead's id is its record's file name; a directory with an unusable name is footnoted as such ─────
def test_a_lead_shows_under_its_file_name_not_its_sid_field(tmp_path, capsys):
    lead(tmp_path, "l-file", "pf", started=iso(10))
    rec_p = H(tmp_path) / "leads" / "l-file.json"
    rec = json.loads(rec_p.read_text())
    rec["sid"] = "l-other"  # names another lead
    rec_p.write_text(json.dumps(rec))
    session(tmp_path, "s-1", lead_sid="l-file")
    out = run(capsys)
    assert set(lead_rows(out)) == {"l-file"}
    assert "l-other" not in out
    assert rows(out, "EXECUTORS")["s-1"][4] == "pf"  # its executor still finds the lead's project
    data = json.loads(run(capsys, "--json"))
    assert [ld["sid"] for ld in data["leads"]] == ["l-file"]


def test_a_session_directory_with_an_unusable_name_is_footnoted_by_that_name(tmp_path, capsys):
    session(tmp_path, "s-good")
    bad = session(tmp_path, "a b")  # a readable record, in a directory whose name is not a session id
    out = run(capsys)
    notes = footnotes(out)
    want = f"  ⚠ broken: a b — the name of the directory {bad} is not a usable session id"
    assert want in notes, notes
    assert not any("cannot be read" in n for n in notes), notes
    assert rows(out, "EXECUTORS")["a b"][1] == "broken"
    assert rows(out, "EXECUTORS")["s-good"][1] == "busy"


# ── SCOPE and EFFORT (`pilead spawn --scope` / `--effort`) ─────────────────────────────────────────
def test_scope_and_effort_columns(tmp_path, capsys):
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    sdir = session(tmp_path, "s-set", topic="the-topic")
    meta = json.loads((sdir / "meta.json").read_text())
    (sdir / "meta.json").write_text(json.dumps({**meta, "scope": "auth module", "effort": "xhigh"}))
    session(tmp_path, "s-none", topic="other")  # a record written before spawn stored either
    out = run(capsys)
    header = re.split(r"\s{2,}", section(out, "EXECUTORS")[0])
    assert header.index("SCOPE") == header.index("TOPIC") + 1
    assert header.index("EFFORT") == header.index("TOKENS") + 1
    r = rows(out, "EXECUTORS")
    s, e = header.index("SCOPE"), header.index("EFFORT")
    assert (r["s-set"][2], r["s-set"][s], r["s-set"][e]) == ("the-topic", "auth module", "xhigh")
    assert (r["s-none"][s], r["s-none"][e]) == ("-", "-")


def test_scope_and_effort_that_are_not_text_are_a_dash(tmp_path, capsys):
    lead(tmp_path, "lead-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    sdir = session(tmp_path, "s-odd")
    meta = json.loads((sdir / "meta.json").read_text())
    (sdir / "meta.json").write_text(json.dumps({**meta, "scope": "  ", "effort": 3}))
    out = run(capsys)
    header = re.split(r"\s{2,}", section(out, "EXECUTORS")[0])
    row = rows(out, "EXECUTORS")["s-odd"]
    assert (row[header.index("SCOPE")], row[header.index("EFFORT")]) == ("-", "-")


# ── auto-close: `pilead list` run by a lead parks its finished executors, after the table ─────────────
def _list(*argv, caller=None):
    env = {**os.environ, "COLUMNS": "200"}
    if caller:
        env["PI_LEAD_SID"] = caller
    return subprocess.run([str(ROOT / "bin" / "pilead"), "list", *argv], capture_output=True, text=True, env=env)


def test_list_by_a_caller_parks_a_landed_session_and_prints_the_park_line_last(tmp_path):
    from test_autoclose import H, session, status_of
    session(tmp_path, "s-landed")
    r = _list(caller="lead-1")
    assert r.returncode == 0 and r.stderr == ""
    lines = r.stdout.splitlines()
    assert lines[-1] == ("auto-closed s-landed (landed) — pilead send s-landed <packet> reopens it with its "
                         "conversation")
    (row,) = [ln for ln in lines[:-1] if ln.startswith("s-landed ")]
    assert " reported " in row  # the table shows the state list found
    assert status_of(tmp_path, "s-landed") == "closed"
    ev = [e for e in _events(H(tmp_path)) if e["event"] == "auto_closed"]
    assert [(e["session_id"], e["trigger"]) for e in ev] == [("s-landed", "list")]


def test_list_json_by_a_caller_keeps_stdout_json_and_puts_the_park_line_on_stderr(tmp_path):
    from test_autoclose import session, status_of
    session(tmp_path, "s-landed")
    r = _list("--json", caller="lead-1")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert [e["status"] for e in payload["executors"] if e["sid"] == "s-landed"] == ["reported"]
    assert r.stderr == ("auto-closed s-landed (landed) — pilead send s-landed <packet> reopens it with its "
                        "conversation\n")
    assert status_of(tmp_path, "s-landed") == "closed"


def test_list_with_no_caller_parks_nothing(tmp_path):
    from test_autoclose import H, session, status_of
    from test_close_variants import tree
    session(tmp_path, "s-landed")
    for argv in ([], ["--json"], ["--lead", "lead-1"]):
        before = tree(H(tmp_path) / "sessions")
        r = _list(*argv)
        assert r.returncode == 0 and "auto-clos" not in r.stdout + r.stderr
        assert status_of(tmp_path, "s-landed") == "reported"
        assert {k: v for k, v in tree(H(tmp_path) / "sessions").items() if not k.endswith("usage.json")} == \
            {k: v for k, v in before.items() if not k.endswith("usage.json")}
    r = _list(caller="not-a-lead")  # an id with no lead record is no caller
    assert "auto-clos" not in r.stdout + r.stderr and status_of(tmp_path, "s-landed") == "reported"


def test_list_closed_footnotes_an_auto_closed_session(tmp_path):
    from test_autoclose import session
    session(tmp_path, "s-landed")
    session(tmp_path, "s-manual", status="closed")
    _list(caller="lead-1")
    lines = _list("--closed").stdout.splitlines()
    assert "  auto-closed: s-landed (landed)" in lines
    assert not any(ln.startswith("  auto-closed: s-manual") for ln in lines)
    assert lines.index("  auto-closed: s-landed (landed)") > max(i for i, ln in enumerate(lines)
                                                                 if ln.startswith("s-landed "))
    assert "  auto-closed:" not in _list().stdout  # only with --closed


def _events(home):
    p = Path(home) / "ledger.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


# ── LEADS: AUTO (the autonomous posture) ─────────────────────────────────────────────────────────
AUTO_NOTE = ("  ⚡ AUTO: {names} — proceeding without asking on routine in-plan steps. Committing an executor's work "
             "still needs every condition of pilead verify <sid> --for-autocommit. pilead auto off --session <sid> "
             "reverts.")


def posture_leads(tmp_path, values):
    """One lead record per (sid, project, autonomous value); `...` = no `autonomous` field at all."""
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    for sid, project, value in values:
        rec = {"sid": sid, "project": project, "cwd": str(tmp_path / f"cwd-{sid}"), "inbox": "",
               "iterm_handle": None, "terminal": None, "started": iso(86400)}
        if value is not ...:
            rec["autonomous"] = value
            rec["autonomous_source"] = "command"
        (d / f"{sid}.json").write_text(json.dumps(rec))


def strip_posture(tmp_path):
    for p in (H(tmp_path) / "leads").glob("*.json"):
        try:
            rec = json.loads(p.read_text())
        except ValueError:
            continue
        if isinstance(rec, dict):
            rec.pop("autonomous", None)
            rec.pop("autonomous_source", None)
            p.write_text(json.dumps(rec))


POSTURES = [("l-a", "pa", True), ("l-b", "pb", False), ("l-c", "pc", ...), ("l-d", "pd", "yes"), ("l-e", None, True),
            ("l-f", "pf", 1), ("l-g", "pg", None)]


def test_the_auto_cell_is_the_cell_before_tier_in_a_lead_row(tmp_path, capsys):
    posture_leads(tmp_path, POSTURES)
    lead(tmp_path, "l-bad", None, raw="{x")
    out = run(capsys, "--all-leads")
    header = re.split(r"\s{2,}", section(out, "LEADS")[0])
    assert header == ["PROJECT", "SESSION", "MODEL", "TERM", "LIVE", "CTX", "MB", "LAST ACTIVE", "AUTO", "TIER", "WAKE", "VER"]
    r = lead_rows(out)
    assert {sid: cells[-4] for sid, cells in r.items()} == {
        "l-a": "AUTO", "l-b": "-", "l-c": "-", "l-d": "-", "l-e": "AUTO", "l-f": "-", "l-g": "-", "l-bad": "-"}
    assert all(len(cells) == 12 for cells in r.values()), r
    assert r["l-bad"][4] == "broken"


def test_the_auto_footnote_names_the_autonomous_leads_in_table_order(tmp_path, capsys):
    posture_leads(tmp_path, POSTURES)
    lead(tmp_path, "l-bad", None, raw="{x")
    out = run(capsys, "--all-leads")
    notes = [n for n in footnotes(out) if "⚡" in n]
    assert notes == [AUTO_NOTE.format(names="pa, l-e")]
    sec = section(out, "LEADS")
    # after the LEADS footnotes that were there before; only the WAKE footnotes follow it, then the blank line
    after = sec[sec.index(notes[0]) + 1:]
    assert after and all(n.startswith("  ⚠ WAKE=") for n in after), after
    lines = out.splitlines()
    assert lines[lines.index(after[-1]) + 1] == ""


def test_no_auto_footnote_when_no_listed_lead_is_autonomous(tmp_path, capsys):
    posture_leads(tmp_path, [(sid, p, v) for sid, p, v in POSTURES if v is not True])
    lead(tmp_path, "l-bad", None, raw="{x")
    out = run(capsys, "--all-leads")
    assert not any("⚡" in n or "AUTO:" in n for n in footnotes(out))
    assert all(cells[-4] == "-" for cells in lead_rows(out).values())


def test_a_hidden_autonomous_lead_is_not_in_the_footnote(tmp_path, capsys, monkeypatch):
    posture_leads(tmp_path, [("l-a", "pa", True), ("l-z", "pz", True)])
    lead(tmp_path, "l-mine", "pz", handle=f"w0t0p0:{LIVE_UUID}")
    monkeypatch.setenv("PI_LEAD_SID", "l-mine")  # project pz: the ghost lead l-a of pa is hidden
    out = run(capsys)
    assert set(lead_rows(out)) == {"l-mine", "l-z"}
    assert [n for n in footnotes(out) if "⚡" in n] == [AUTO_NOTE.format(names="pz")]


def test_the_posture_changes_no_other_cell_and_no_other_line(tmp_path, capsys):
    posture_leads(tmp_path, POSTURES)
    lead(tmp_path, "l-bad", None, raw="{x")
    session(tmp_path, "s-1", lead_sid="l-a")
    with_field = run(capsys, "--all-leads")
    strip_posture(tmp_path)
    without = run(capsys, "--all-leads")
    a, b = lead_rows(with_field), lead_rows(without)
    assert set(a) == set(b)
    for sid in a:
        assert a[sid][:-4] + a[sid][-3:] == b[sid][:-4] + b[sid][-3:], sid
        assert b[sid][-4] == "-"
    assert section(with_field, "LEADS")[0] == section(without, "LEADS")[0]
    assert [n for n in footnotes(with_field) if "⚡" not in n] == footnotes(without)
    assert section(with_field, "EXECUTORS") == section(without, "EXECUTORS")


def test_list_json_carries_the_record_fields_and_adds_no_key(tmp_path, capsys):
    posture_leads(tmp_path, [("l-a", "pa", True), ("l-c", "pc", ...)])
    d = {x["sid"]: x for x in json.loads(run(capsys, "--json"))["leads"]}
    assert (d["l-a"]["autonomous"], d["l-a"]["autonomous_source"]) == (True, "command")
    assert "autonomous" not in d["l-c"] and "autonomous_source" not in d["l-c"]


# ── LEADS: TIER (the model-tier posture) ─────────────────────────────────────────────────────────
TIER_NOTE = ("  ⚡ AUTO: {names} — proceeding without asking on routine in-plan steps. Committing an executor's work "
             "still needs every condition of pilead verify <sid> --for-autocommit. tier: manual for {manual} — "
             "every spawn stops for a model choice, under the autonomous posture too. pilead auto off --session "
             "<sid> reverts.")


def tier_leads(tmp_path, values):
    """One lead record per (sid, project, autonomous, tier); `...` = no such field at all."""
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    for sid, project, auto, tier in values:
        rec = {"sid": sid, "project": project, "cwd": str(tmp_path / f"cwd-{sid}"), "inbox": "",
               "iterm_handle": None, "terminal": None, "started": iso(86400)}
        if auto is not ...:
            rec["autonomous"] = auto
        if tier is not ...:
            rec["tier"] = tier
            rec["tier_source"] = "command"
        (d / f"{sid}.json").write_text(json.dumps(rec))


TIERS = [("l-a", "pa", False, "auto"), ("l-b", "pb", False, "manual"), ("l-c", "pc", False, "lead"),
         ("l-d", "pd", False, ...), ("l-e", "pe", False, "manul"), ("l-f", "pf", False, 7), ("l-g", "pg", False, None)]


def test_the_tier_cell_is_the_cell_before_wake_in_a_lead_row(tmp_path, capsys):
    tier_leads(tmp_path, TIERS)
    lead(tmp_path, "l-bad", None, raw="{x")
    out = run(capsys, "--all-leads")
    header = re.split(r"\s{2,}", section(out, "LEADS")[0])
    assert header == ["PROJECT", "SESSION", "MODEL", "TERM", "LIVE", "CTX", "MB", "LAST ACTIVE", "AUTO", "TIER", "WAKE", "VER"]
    r = lead_rows(out)
    assert {sid: cells[-3] for sid, cells in r.items()} == {
        "l-a": "auto", "l-b": "manual", "l-c": "lead", "l-d": "auto", "l-e": "?", "l-f": "?", "l-g": "?",
        "l-bad": "-"}
    assert all(len(cells) == 12 for cells in r.values()), r
    assert r["l-bad"][4] == "broken" and r["l-bad"][-4] == "-"


def test_the_tier_column_moves_no_other_cell(tmp_path, capsys):
    tier_leads(tmp_path, TIERS)
    with_tier = lead_rows(run(capsys, "--all-leads"))
    for p in (H(tmp_path) / "leads").glob("*.json"):
        rec = json.loads(p.read_text())
        rec.pop("tier", None), rec.pop("tier_source", None)
        p.write_text(json.dumps(rec))
    without = lead_rows(run(capsys, "--all-leads"))
    assert set(with_tier) == set(without)
    for sid in with_tier:
        assert with_tier[sid][:-3] + with_tier[sid][-2:] == without[sid][:-3] + without[sid][-2:], sid
        assert without[sid][-3] == "auto"


def test_the_auto_footnote_gains_the_tier_sentence_only_for_an_autonomous_manual_lead(tmp_path, capsys):
    tier_leads(tmp_path, [("l-a", "pa", True, "manual"), ("l-b", "pb", True, "auto"), ("l-c", "pc", True, "lead"),
                          ("l-d", None, True, "manual"), ("l-e", "pe", False, "manual"), ("l-f", "pf", True, "manul")])
    out = run(capsys, "--all-leads")
    assert [n for n in footnotes(out) if "⚡" in n] == [
        TIER_NOTE.format(names="pa, pb, pc, l-d, pf", manual="pa, l-d")]


@pytest.mark.parametrize("values", [
    [("l-a", "pa", True, "auto"), ("l-b", "pb", True, "lead"), ("l-c", "pc", True, ...), ("l-d", "pd", True, "manul")],
    [("l-a", "pa", False, "manual"), ("l-b", "pb", False, "lead")],
])
def test_no_tier_sentence_otherwise(tmp_path, capsys, values):
    tier_leads(tmp_path, values)
    out = run(capsys, "--all-leads")
    assert not any("tier:" in n for n in footnotes(out))
    assert all("tier: manual" not in n for n in out.splitlines())
    notes = [n for n in footnotes(out) if "⚡" in n]
    names = ", ".join(p for _s, p, a, _t in values if a is True)
    assert notes == ([AUTO_NOTE.format(names=names)] if names else [])


# ── --plan ────────────────────────────────────────────────────────────────────────────────────────
def plan_file(tmp_path, sid, items=None, raw=None):
    p = H(tmp_path) / "plans" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_text(raw)
        return p
    p.write_text(json.dumps({"version": 1, "items": items}))
    return p


def plan_item(name, **kw):
    return {"n": 1, "packet": f"/nowhere/{name}", "target": "fresh", "model": None, "note": None,
            "executor": None, "packet_n": None, "added": "2026-01-01T00:00:00Z", "done": None, **kw}


def plan_block(out):
    """Everything after the empty line that precedes PLAN, or None."""
    lines = out.splitlines()
    return lines[lines.index("PLAN") - 1:] if "PLAN" in lines else None


def test_plan_of_the_caller_follows_the_executor_footnotes(tmp_path, capsys, monkeypatch):
    lead(tmp_path, "l-a", "pa", handle=f"w0t0p0:{LIVE_UUID}")
    session(tmp_path, "s-1", lead_sid="l-a", status="busy", packets=1)
    session(tmp_path, "s-2", lead_sid="l-a", status="busy", packets=1, report=False)
    (H(tmp_path) / "sessions" / "s-2" / "queue.json").write_text("{not json")  # a footnote after the table
    plan_file(tmp_path, "l-a", [plan_item("one-packet.md", note="first"), plan_item("two-packet.md", done="x")])
    monkeypatch.setenv("PI_LEAD_SID", "l-a")
    out = run(capsys, "--plan")
    lines = out.splitlines()
    i = lines.index("PLAN")
    assert lines[i - 1] == ""
    assert any(ln.startswith("  queue unreadable: s-2") for ln in lines[:i])  # the footnotes come first
    assert lines.index("EXECUTORS") < i
    tbl = lines[i + 1:]
    assert tbl[0].split() == ["#", "STATE", "PACKET", "TARGET", "MODEL", "NOTE"]
    assert tbl[1].split() == ["1", "queued", "one-packet.md", "fresh", "-", "first"]
    assert tbl[2].split() == ["2", "done", "two-packet.md", "fresh", "-", "-"]
    assert tbl[3] == "  1 queued · 0 in flight · 0 reported · 1 done"


def test_plan_with_lead_names_that_leads_plan(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa")
    lead(tmp_path, "l-b", "pb")
    plan_file(tmp_path, "l-a", [plan_item("a-packet.md")])
    plan_file(tmp_path, "l-b", [plan_item("b-packet.md")])
    out = run(capsys, "--plan", "--lead", "l-b", "--all-leads")
    assert "b-packet.md" in "\n".join(plan_block(out)) and "a-packet.md" not in "\n".join(plan_block(out))
    assert plan_block(out)[1] == "PLAN"


def test_plan_of_an_empty_plan(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa")
    out = run(capsys, "--plan", "--lead", "l-a")
    assert plan_block(out) == ["", "PLAN", "  (plan is empty — pilead plan add <packet>)"]


def test_plan_with_no_lead_is_byte_identical_to_without_it(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa")
    session(tmp_path, "s-1", lead_sid="l-a")
    plan_file(tmp_path, "l-a", [plan_item("a-packet.md")])
    without = run(capsys)
    assert run(capsys, "--plan") == without  # no caller
    assert run(capsys, "--plan", "--lead", "nobody") == run(capsys, "--lead", "nobody")
    assert run(capsys, "--plan", "--lead", "../x") == run(capsys, "--lead", "../x")
    assert "PLAN" not in without


def test_plan_of_a_caller_that_has_no_lead_record_adds_nothing(tmp_path, capsys, monkeypatch):
    lead(tmp_path, "l-a", "pa")
    monkeypatch.setenv("PI_LEAD_SID", "not-registered")
    assert run(capsys, "--plan") == run(capsys)


def test_an_unreadable_plan_prints_its_line_and_list_exits_0(tmp_path, capsys, monkeypatch):
    lead(tmp_path, "l-a", "pa")
    f = plan_file(tmp_path, "l-a", raw="{not json")
    monkeypatch.setenv("PI_LEAD_SID", "l-a")
    assert cli.main(["list", "--plan"]) in (0, None)
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2:] == ["PLAN", f"plan: {f} cannot be read — inspect that file; nothing was changed"]
    assert f.read_text() == "{not json"


def test_list_json_is_not_changed_by_plan(tmp_path, capsys, monkeypatch):
    lead(tmp_path, "l-a", "pa")
    plan_file(tmp_path, "l-a", [plan_item("a-packet.md")])
    monkeypatch.setenv("PI_LEAD_SID", "l-a")
    a = run(capsys, "--json")
    b = run(capsys, "--json", "--plan")
    assert json.loads(a)["leads"][0].keys() == json.loads(b)["leads"][0].keys() and "PLAN" not in b


def test_the_plan_block_goes_before_the_park_lines(tmp_path, capsys, monkeypatch):
    from pilead import autoclose
    lead(tmp_path, "l-a", "pa")
    plan_file(tmp_path, "l-a", [plan_item("a-packet.md")])
    monkeypatch.setenv("PI_LEAD_SID", "l-a")
    monkeypatch.setattr(autoclose, "sweep", lambda home, trigger, sids=None, lead=None: [("s-1", "close", "landed")])
    lines = run(capsys, "--plan").splitlines()
    park = [i for i, ln in enumerate(lines) if "s-1" in ln]
    assert park and park[-1] == len(lines) - 1 and lines.index("PLAN") < park[0]
    assert lines[lines.index("PLAN") + 3] == "  1 queued · 0 in flight · 0 reported · 0 done"


# ── LEADS: WAKE (is each lead's wake working) ────────────────────────────────────────────────────
WAKE_NOTES = {
    "stuck": "  ⚠ WAKE=STUCK: {names} — a wake was claimed and not delivered ({detail}); the next drain retries it. "
             "If it stays, restart pi in that lead's tab.",
    "stale": "  ⚠ WAKE=STALE: {names} — the lead's pi session stopped ({detail}); {wait} in its inbox until pi runs "
             "there again: pilead resume {sid}",
    "off": "  ⚠ WAKE=off: {names} — registered, but no pi session with the pi-lead extension is watching the inbox",
    "unknown": "  ⚠ WAKE=?: {names} — the health file could not be read ({detail})",
}


def wake_leads(tmp_path):
    """One lead per wake word (ghosts, all listed with --all-leads), plus a record that cannot be read."""
    for sid, project in (("l-1ok", "p-ok"), ("l-2off", "p-off"), ("l-3stale", "p-stale"), ("l-4stuck", "p-stuck"),
                         ("l-5unk", "p-unk")):
        lead(tmp_path, sid, project, started=iso(86400))
    health(tmp_path, "l-1ok")
    health(tmp_path, "l-3stale", beat=iso(120))
    health(tmp_path, "l-4stuck", last_error="send broke", last_error_at=iso(5))
    d = H(tmp_path) / "wake" / "l-5unk"
    d.mkdir(parents=True)
    (d / "health.json").write_text("[1]")
    (H(tmp_path) / "leads" / "l-3stale.inbox.md").write_text("reported e1 /r/1.md\nreported e2 /r/2.md\n")
    lead(tmp_path, "l-6bad", None, raw="{x")


def wake_notes(out):
    return [n for n in footnotes(out) if n.startswith("  ⚠ WAKE=")]


def test_wake_is_the_last_cell_of_a_lead_row_for_each_word(tmp_path, capsys):
    wake_leads(tmp_path)
    out = run(capsys, "--all-leads")
    assert re.split(r"\s{2,}", section(out, "LEADS")[0])[-2] == "WAKE"
    r = lead_rows(out)
    assert {sid: cells[-2] for sid, cells in r.items()} == {
        "l-1ok": "ok", "l-2off": "off", "l-3stale": "STALE", "l-4stuck": "STUCK", "l-5unk": "?", "l-6bad": "?"}
    assert all(len(cells) == 12 for cells in r.values()), r


def test_each_wake_footnote_whole_in_word_order_and_only_for_words_that_occur(tmp_path, capsys):
    wake_leads(tmp_path)
    out = run(capsys, "--all-leads")
    assert wake_notes(out) == [
        WAKE_NOTES["stuck"].format(names="p-stuck", detail="send broke"),
        WAKE_NOTES["stale"].format(names="p-stale", detail="last beat 2m ago", wait="2 report(s) wait", sid="l-3stale"),
        WAKE_NOTES["off"].format(names="p-off"),
        WAKE_NOTES["unknown"].format(names="p-unk", detail="not a JSON object"),
    ]
    sec = section(out, "LEADS")
    assert sec[-4:] == wake_notes(out), "after the LEADS footnotes that were there before, before the blank line"
    # a lead whose record cannot be read has `?` in its row but no WAKE footnote (it has its own `broken` one)
    assert not any("l-6bad" in n for n in wake_notes(out))


def test_no_wake_footnote_when_every_wake_is_ok(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    lead(tmp_path, "l-b", "pb", started=iso(86400))
    health(tmp_path, "l-a")
    health(tmp_path, "l-b")
    out = run(capsys, "--all-leads")
    assert wake_notes(out) == []
    assert {sid: cells[-2] for sid, cells in lead_rows(out).items()} == {"l-a": "ok", "l-b": "ok"}


def test_a_wake_footnote_names_its_leads_in_table_order(tmp_path, capsys):
    lead(tmp_path, "l-c", "pc", started=iso(86400))
    lead(tmp_path, "l-a", None, started=iso(86400))  # no project: named by its sid
    lead(tmp_path, "l-b", "pb", started=iso(86400))
    out = run(capsys, "--all-leads")
    assert list(lead_rows(out)) == ["l-a", "l-b", "l-c"]
    assert wake_notes(out) == [WAKE_NOTES["off"].format(names="l-a, pb, pc")]


def test_stale_footnote_with_several_leads_and_an_unreadable_inbox(tmp_path, capsys):
    for sid in ("l-a", "l-b"):
        lead(tmp_path, sid, sid.replace("l-", "p"), started=iso(86400))
        health(tmp_path, sid, beat=iso(300))
    (H(tmp_path) / "leads" / "l-a.inbox.md").write_text("reported e1 /r/1.md\n")
    out = run(capsys, "--all-leads")
    assert wake_notes(out) == [WAKE_NOTES["stale"].format(
        names="pa, pb", detail="pa: last beat 5m ago; pb: last beat 5m ago", wait="1 report(s) wait", sid="<sid>")]
    (H(tmp_path) / "leads" / "l-b.inbox.md").mkdir()  # an inbox that cannot be read: the count is unknown
    out = run(capsys, "--all-leads")
    assert "reports may wait in its inbox" in wake_notes(out)[0]


def test_a_wake_that_could_not_be_read_is_never_ok_or_counted_healthy(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    for raw in ("", "{}", '{"beat": "yesterday"}', "null"):
        d = H(tmp_path) / "wake" / "l-a"
        d.mkdir(parents=True, exist_ok=True)
        (d / "health.json").write_text(raw)
        out = run(capsys, "--all-leads")
        assert lead_rows(out)["l-a"][-1] == "?", raw
        assert [n.split(" — ")[0] for n in wake_notes(out)] == ["  ⚠ WAKE=?: pa"], raw


def test_list_json_carries_wake_and_waiting(tmp_path, capsys):
    wake_leads(tmp_path)
    (H(tmp_path) / "leads" / "l-2off.inbox.md").mkdir()
    d = {x["sid"]: x for x in json.loads(run(capsys, "--json"))["leads"]}
    assert {sid: (x.get("wake"), x.get("waiting")) for sid, x in d.items()} == {
        "l-1ok": ("ok", 0), "l-2off": ("off", None), "l-3stale": ("stale", 2), "l-4stuck": ("stuck", 0),
        "l-5unk": ("unknown", 0), "l-6bad": (None, None)}
    assert d["l-6bad"] == {"sid": "l-6bad", "broken": True}


# ── LEADS: the last tab tidy was skipped (p23b change 6) ─────────────────────────────────────────
def tidy_event(tmp_path, event, sid, attempts=1, reason="no iTerm tab found for any of the ordered session ids"):
    H(tmp_path).mkdir(parents=True, exist_ok=True)
    rec = {"ts": iso(0), "event": event, "session_id": sid, "attempts": attempts, "reason": reason}
    if event == "tidy":
        rec.update(moved=1, windows=1, missing=0)
    with open(H(tmp_path) / "ledger.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def tidy_notes(out):
    return [n for n in footnotes(out) if "tab tidy" in n]


def test_a_skipped_tidy_is_footnoted_and_a_later_tidy_removes_it(tmp_path, capsys):
    lead(tmp_path, "l-a", "proj-a", started=iso(60))
    lead(tmp_path, "l-b", None, started=iso(60))
    before = lead_rows(run(capsys))
    tidy_event(tmp_path, "tidy", "l-a")
    tidy_event(tmp_path, "tidy_skipped", "l-a", attempts=2,
               reason="iterm2 python api unavailable (ConnectionClosedError: gone)")
    tidy_event(tmp_path, "tidy_skipped", "l-b")
    out = run(capsys)
    assert tidy_notes(out) == [
        "  · proj-a: the last tab tidy was skipped after 2 attempt(s) (iterm2 python api unavailable "
        "(ConnectionClosedError: gone)) — pilead tidy tries again",
        "  · l-b: the last tab tidy was skipped after 1 attempt(s) (no iTerm tab found for any of the ordered "
        "session ids) — pilead tidy tries again"]
    assert lead_rows(out) == before
    sec = section(out, "LEADS")
    assert sec[-2:] == tidy_notes(out)  # the last LEADS footnotes, then the blank line
    tidy_event(tmp_path, "tidy", "l-a")
    assert tidy_notes(run(capsys)) == [n for n in tidy_notes(out) if "l-b" in n]


def test_no_tidy_event_or_one_with_no_session_id_gives_no_footnote(tmp_path, capsys):
    lead(tmp_path, "l-a", "proj-a", started=iso(60))
    assert tidy_notes(run(capsys)) == []
    tidy_event(tmp_path, "tidy_skipped", None)
    tidy_event(tmp_path, "tidy_skipped", "l-gone")
    out = run(capsys)
    assert tidy_notes(out) == []


def test_the_json_listing_gains_no_key_for_a_skipped_tidy(tmp_path, capsys):
    lead(tmp_path, "l-a", "proj-a", started=iso(60))
    keys = set(json.loads(run(capsys, "--json"))["leads"][0])
    tidy_event(tmp_path, "tidy_skipped", "l-a")
    assert set(json.loads(run(capsys, "--json"))["leads"][0]) == keys


# ── --all (p23d) ─────────────────────────────────────────────────────────────────────────────────
def all_setup(tmp_path):
    """Two leads and three executors, two of them lead-a's; fixed times, so two runs print the same."""
    lead(tmp_path, "lead-a", "pa", started=iso(3 * 3600))
    lead(tmp_path, "lead-b", "pb", started=iso(5 * 3600))
    session(tmp_path, "s-one-101010", lead_sid="lead-a", created=iso(7200))
    session(tmp_path, "s-two-111111", lead_sid="lead-b", created=iso(7200), status="reported", report=True)
    session(tmp_path, "s-three-121212", lead_sid="lead-a", created=iso(7200))


def test_all_alone_is_byte_identical_to_list(tmp_path, capsys):
    all_setup(tmp_path)
    assert run(capsys, "--all") == run(capsys)
    assert run(capsys, "--all", "--json") == run(capsys, "--json")


def test_lead_with_all_lists_every_executor_and_keeps_the_leads_reference(tmp_path, capsys):
    all_setup(tmp_path)
    plain, by_lead, both = run(capsys), run(capsys, "--lead", "lead-a"), run(capsys, "--lead", "lead-a", "--all")
    assert "s-two-111111" not in "\n".join(section(by_lead, "EXECUTORS"))  # --lead alone still filters
    assert section(both, "EXECUTORS") == section(plain, "EXECUTORS")
    assert {"s-one-101010", "s-two-111111", "s-three-121212"} <= set(rows(both, "EXECUTORS"))
    assert section(both, "LEADS") == section(by_lead, "LEADS")


def test_lead_with_all_in_json(tmp_path, capsys):
    all_setup(tmp_path)
    plain = json.loads(run(capsys, "--json"))
    by_lead = json.loads(run(capsys, "--lead", "lead-a", "--json"))
    both = json.loads(run(capsys, "--lead", "lead-a", "--all", "--json"))
    for key in both:
        assert both[key] == (by_lead[key] if key == "leads" else plain[key]), key
    assert both.keys() == plain.keys()


def test_lead_with_all_and_all_leads_take_the_lead_as_reference(tmp_path, capsys):
    ghost_setup(tmp_path)
    session(tmp_path, "s-x-101010", lead_sid="l-other-live", created=iso(7200))
    out = run(capsys, "--lead", "l-mine", "--all")
    assert set(lead_rows(out)) == {"l-mine", "l-mine-ghost", "l-other-live"}
    assert "s-x-101010" in rows(out, "EXECUTORS")
    out = run(capsys, "--lead", "l-mine", "--all", "--all-leads")
    assert set(lead_rows(out)) == {"l-mine", "l-mine-ghost", "l-other-ghost", "l-other-live"}


def test_all_writes_nothing(tmp_path, capsys):
    all_setup(tmp_path)
    run(capsys, "--lead", "lead-a", "--all")  # a first run may write the usage caches every list run writes
    before = {str(p): p.read_bytes() for p in sorted(H(tmp_path).rglob("*")) if p.is_file()}
    run(capsys, "--lead", "lead-a", "--all")
    after = {str(p): p.read_bytes() for p in sorted(H(tmp_path).rglob("*")) if p.is_file()}
    assert after.keys() == before.keys()


def test_term_column_says_tmux_for_a_tmux_lead(tmp_path, capsys):
    """b12: a lead inside tmux reads `tmux` — by its recorded backend or its `tmux:%N` handle — even when
    $TERM_PROGRAM (inherited from the outer terminal on a Mac) said iTerm.app."""
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    base = {"project": "p", "cwd": str(tmp_path / "c"), "inbox": "", "started": iso(30)}
    (d / "l-pane.json").write_text(json.dumps({**base, "sid": "l-pane", "iterm_handle": "tmux:%4",
                                               "terminal": "iTerm.app"}))
    (d / "l-back.json").write_text(json.dumps({**base, "sid": "l-back", "iterm_handle": None, "terminal": None,
                                               "backend": "tmux"}))
    lead(tmp_path, "l-it", "p", handle=f"w0t0p0:{LIVE_UUID}")
    r = lead_rows(run(capsys, "--all-leads"))
    assert (r["l-pane"][3], r["l-back"][3], r["l-it"][3]) == ("tmux", "tmux", "iterm")

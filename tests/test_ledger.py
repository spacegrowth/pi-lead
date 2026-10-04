"""<home>/ledger.jsonl: every event the verbs emit, with exactly its listed fields, by running bin/pilead
against tests/fake_pi and the stub osascript / tests/fake_iterm2 (no real tab). Writing is best-effort."""
import json
import sys
from pathlib import Path

from test_cli_core import ROOT, env, home, make_reported, pilead, spawn, start_lead, stub_osascript  # noqa: F401  (fixtures)
from test_verify_diff_board import SID, make_session, report, stage, write_report

sys.path.insert(0, str(ROOT / "lib"))
from pilead import ledger  # noqa: E402


def events(tmp_path, event=None):
    return ledger.read(home(tmp_path), event=event)


def fields(rec):
    return {k: v for k, v in rec.items() if k not in ("ts", "event")}


def test_record_shape_and_read_filters(tmp_path):
    h = tmp_path / "h"
    ledger.append(h, "spawned", session_id="a", packet=1)
    ledger.append(h, "closed", session_id="b", reason="manual close")
    ledger.append(h, "closed", session_id="a", reason="manual close")
    first = json.loads((h / "ledger.jsonl").read_text().splitlines()[0])
    assert list(first) == ["ts", "event", "session_id", "packet"]
    assert first["ts"].endswith("Z") and "T" in first["ts"]
    assert [r["event"] for r in ledger.read(h)] == ["spawned", "closed", "closed"]
    assert [r["event"] for r in ledger.read(h, session_id="a")] == ["spawned", "closed"]
    assert [r["session_id"] for r in ledger.read(h, event="closed")] == ["b", "a"]
    assert ledger.read(h, session_id="a", event="closed") == [ledger.read(h)[2]]


def test_read_skips_corrupt_lines_and_missing_file(tmp_path):
    h = tmp_path / "h"
    assert ledger.read(h) == []
    ledger.append(h, "one", session_id="s")
    with open(h / "ledger.jsonl", "a") as f:
        f.write('{"ts": "x", "event": "half\n[1, 2]\nnot json at all\n\n')
    ledger.append(h, "two", session_id="s")
    assert [r["event"] for r in ledger.read(h)] == ["one", "two"]


def test_append_never_raises(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ledger.append(blocker / "home", "e", session_id="s")  # home's parent is a file
    (tmp_path / "h" / "ledger.jsonl").mkdir(parents=True)  # the ledger path is a directory
    ledger.append(tmp_path / "h", "e", session_id="s")
    ledger.append(tmp_path / "h2", "e", unserialisable=object())
    assert capsys.readouterr() == ("", "")
    assert not (tmp_path / "h2" / "ledger.jsonl").exists()  # a record that can't serialise writes nothing


def test_lead_started(env):
    start_lead()
    (rec,) = events(env, "lead_started")
    assert fields(rec) == {"session_id": "lead-1", "project": "proj"}


def test_spawned(env):
    sid = spawn(env)
    (rec,) = events(env, "spawned")
    assert fields(rec) == {"session_id": sid, "lead": "lead-1", "worktree": str((env / "wt").resolve()),
                           "topic": "topic", "model": "anthropic/claude-sonnet-5",
                           "tab_label": f"[Exec] {sid}", "source": str((env / "packet.md").resolve())}


def test_packet_sent_via_inbox_and_via_relaunch(env, monkeypatch):
    sid = spawn(env)
    (env / "p2.md").write_text("second\n")
    (env / "p3.md").write_text("third\n")
    monkeypatch.chdir(env)
    make_reported(env, sid)
    pilead("send", sid, "p2.md")  # a relative path: `source` is still absolute
    pilead("close", sid)
    pilead("send", sid, str(env / "p3.md"))
    recs = events(env, "packet_sent")
    assert [fields(r) for r in recs] == [
        {"session_id": sid, "packet": 2, "source": str((env / "p2.md").resolve()), "via": "inbox"},
        {"session_id": sid, "packet": 3, "source": str((env / "p3.md").resolve()), "via": "relaunch"},
    ]
    assert all(type(r["packet"]) is int for r in recs)


def test_closed(env):
    sid = spawn(env)
    pilead("close", sid)
    (rec,) = events(env, "closed")
    assert fields(rec) == {"session_id": sid, "reason": "manual close"}


def test_report_verify(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py")
    write_report(sdir, report())  # claims src/beta.py too, which is not staged → 1 mismatch
    assert pilead("verify", SID, check=False).returncode == 1
    (rec,) = events(tmp_path, "report_verify")
    assert fields(rec) == {"session_id": SID, "packet": 1, "verdict": "MISMATCH", "rerun": False, "mismatches": 1,
                           "caller": "unknown"}
    assert events(tmp_path, "report_reviewed") == [] and events(tmp_path, "auto_commit") == []


def test_report_reviewed_inline_and_with_findings(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    pilead("verify", SID, "--diff-reviewed")
    findings = tmp_path / "findings.md"
    findings.write_text("1. nothing\n")
    pilead("verify", SID, "--diff-reviewed", "--findings", str(findings))
    recs = events(tmp_path, "report_reviewed")
    assert [fields(r) for r in recs] == [
        {"session_id": SID, "packet": 1, "findings": "inline", "caller": "unknown"},
        {"session_id": SID, "packet": 1, "findings": str(sdir / "review-0001.md"), "caller": "unknown"},
    ]


def test_auto_commit(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    r = pilead("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed", check=False)
    assert r.returncode == 0, r.stdout
    (rec,) = events(tmp_path, "auto_commit")
    assert fields(rec) == {"session_id": SID, "packet": 1, "cleared": True, "reason": None}
    pilead("verify", SID, "--for-autocommit", check=False)  # not in-plan → blocked, with a reason
    blocked = events(tmp_path, "auto_commit")[-1]
    assert blocked["cleared"] is False and isinstance(blocked["reason"], str) and blocked["reason"]


def test_unwritable_ledger_changes_no_exit_code_or_output(env, tmp_path):
    """The ledger path is a directory: every verb still succeeds and prints exactly what it did."""
    (home(env) / "ledger.jsonl").mkdir(parents=True)
    out = pilead("lead-start", "lead-1", "--project", "proj").stdout
    assert out.startswith("lead lead-1 ready inbox=")
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    sid = r.stdout.split()[1]
    assert r.stderr == ""
    (env / "p2.md").write_text("second\n")
    make_reported(env, sid)
    r = pilead("send", sid, str(env / "p2.md"))
    assert (r.stdout, r.stderr) == (f"sent {sid} packet 0002\n", "")
    r = pilead("close", sid)
    assert (r.stdout, r.stderr) == (f"closed {sid}\n", f"pilead: {sid} was busy on packet 0002; no report was written\n")
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    r = pilead("verify", SID, "--diff-reviewed", "--for-autocommit", "--in-plan", check=False)
    assert r.returncode == 0 and r.stderr == ""
    assert ledger.read(home(env)) == []


def test_read_skips_a_line_of_invalid_utf8(tmp_path):
    h = tmp_path / "h"
    ledger.append(h, "one", session_id="s")
    with open(h / "ledger.jsonl", "ab") as f:
        f.write(b"\xff\xfe{\n")
    ledger.append(h, "two", session_id="s")
    assert [r["event"] for r in ledger.read(h)] == ["one", "two"]
    assert [r["event"] for r in ledger.read(h, session_id="s")] == ["one", "two"]


# ── usage_snapshot and heavy_override (lib/pilead/usage.py) ──────────────────────────────────────
import pytest  # noqa: E402
def snaps(tmp_path, sid=None):
    return [fields(r) for r in ledger.read(home(tmp_path), session_id=sid, event="usage_snapshot")]


def log_for(sid, wt, *entries):
    from test_usage import write_log
    return write_log(sid, Path(wt).resolve(), list(entries))


def test_spawn_writes_the_zero_snapshot(env):
    sid = spawn(env)
    assert snaps(env) == [{"session_id": sid, "packet": 1, "at": "sent",
                           "usage": {"prompt": 0, "output": 0, "requests": 0}}]
    # `spawned` keeps exactly its fields (test_spawned pins them too)
    assert set(fields(events(env, "spawned")[0])) == {"session_id", "lead", "worktree", "topic", "model",
                                                       "tab_label", "source"}


def test_send_writes_a_snapshot_and_null_with_no_log(env):
    from test_usage import assistant, u
    sid = spawn(env)
    (env / "p2.md").write_text("second\n")
    (env / "p3.md").write_text("third\n")
    make_reported(env, sid)
    pilead("send", sid, str(env / "p2.md"))  # no session log yet: null, not zeros
    log_for(sid, env / "wt", assistant(u(10, 5, 1000, 200)), assistant(u(20, 7, 1200, 0)))
    make_reported(env, sid, 2)
    pilead("send", sid, str(env / "p3.md"))
    assert snaps(env)[1:] == [
        {"session_id": sid, "packet": 2, "at": "sent", "usage": None},
        {"session_id": sid, "packet": 3, "at": "sent", "usage": {"prompt": 2430, "output": 12, "requests": 2}},
    ]
    assert [set(fields(r)) for r in events(env, "packet_sent")] == [{"session_id", "packet", "source", "via"}] * 2
    assert events(env, "heavy_override") == []


def test_heavy_override_event(env):
    from test_usage import assistant, u
    sid = spawn(env)
    log_for(sid, env / "wt", assistant(u(5, 100, 170_000, 0)))
    (env / "p2.md").write_text("second\n")
    make_reported(env, sid)
    pilead("send", sid, str(env / "p2.md"), "--heavy-override", "last packet in this area")
    (rec,) = events(env, "heavy_override")
    f = fields(rec)
    assert f["tokens"] == 170_105 and f["reason"] == "last packet in this area" and f["packet"] == 2
    assert isinstance(f["mb"], float) and set(f) == {"session_id", "packet", "tokens", "mb", "reason"}


def test_reported_snapshot_is_written_once_per_packet(tmp_path):
    from test_usage import assistant, u
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    log_for(SID, wt, assistant(u(3, 40, 900, 100)))
    for _ in range(3):
        pilead("check", SID)
    assert snaps(tmp_path, SID) == [{"session_id": SID, "packet": 1, "at": "reported",
                                     "usage": {"prompt": 1003, "output": 40, "requests": 1}, "caller": "unknown"}]
    (tmp_path / "p2.md").write_text("fix it\n")
    pilead("send", SID, str(tmp_path / "p2.md"))
    pilead("check", SID)  # busy on packet 2: no reported snapshot
    log_for(SID, wt, assistant(u(3, 40, 900, 100)), assistant(u(4, 60, 1000, 0)))
    write_report(sdir, report(), n=2)
    (sdir / "status").write_text("reported\n")
    for _ in range(3):
        pilead("check", SID)
    reported = [s for s in snaps(tmp_path, SID) if s["at"] == "reported"]
    assert reported == [
        {"session_id": SID, "packet": 1, "at": "reported", "usage": {"prompt": 1003, "output": 40, "requests": 1},
         "caller": "unknown"},
        {"session_id": SID, "packet": 2, "at": "reported", "usage": {"prompt": 2007, "output": 100, "requests": 2},
         "caller": "unknown"},
    ]


def test_reported_snapshot_is_null_with_no_log_and_needs_the_current_report(tmp_path):
    wt, sdir = make_session(tmp_path)
    pilead("check", SID)  # status reported, but report-0001.md is not there yet
    assert snaps(tmp_path, SID) == []
    write_report(sdir, report())
    pilead("check", SID)
    pilead("check", SID)
    assert snaps(tmp_path, SID) == [{"session_id": SID, "packet": 1, "at": "reported", "usage": None, "caller": "unknown"}]


def _count_ledger_reads(monkeypatch):
    """Counts ledger.read calls AND read-mode opens of ledger.jsonl (pathlib goes through io.open)."""
    import builtins
    import io
    from pilead import ledger as ledger_mod
    counts = {"read": 0, "open": 0}
    real_read, real_io_open, real_open = ledger_mod.read, io.open, builtins.open

    def read(*a, **k):
        counts["read"] += 1
        return real_read(*a, **k)

    def wrap(real):
        def opener(file, mode="r", *a, **k):
            if Path(str(file)).name == "ledger.jsonl" and "r" in mode:
                counts["open"] += 1
            return real(file, mode, *a, **k)
        return opener

    monkeypatch.setattr(ledger_mod, "read", read)
    monkeypatch.setattr(io, "open", wrap(real_io_open))
    monkeypatch.setattr(builtins, "open", wrap(real_open))
    return counts


def test_one_check_reads_the_ledger_once(tmp_path, monkeypatch, capsys):
    """Both uses need it — a refs move (the de-dupe read-back) and the reported snapshot — and the file is
    read once. Run in-process so the reads can be counted."""
    from test_refs import sneaky_commit
    from test_usage import assistant, u
    from pilead import cli
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    log_for(SID, wt, assistant(u(3, 40, 900, 100)))
    sneaky_commit(wt, "moved")
    counts = _count_ledger_reads(monkeypatch)
    assert cli.main(["check", SID]) == 0
    assert counts == {"read": 1, "open": 1}
    out = capsys.readouterr().out
    assert out.startswith("REFS MOVED since packet 0001") and "tokens 1.0k/40 over 1 requests" in out
    assert len(events(tmp_path, "refs_moved")) == 1 and len(snaps(tmp_path, SID)) == 1


def test_a_check_that_needs_no_ledger_reads_none(tmp_path, monkeypatch, capsys):
    make_session(tmp_path, status="busy")
    counts = _count_ledger_reads(monkeypatch)
    from pilead import cli
    assert cli.main(["check", SID]) == 0
    assert counts == {"read": 0, "open": 0}


# ── check: the tokens line ───────────────────────────────────────────────────────────────────────
def test_check_prints_the_tokens_line_between_the_tldr_and_the_refs_line(tmp_path):
    from test_usage import assistant, u
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    log_for(SID, wt, assistant(u(3, 1200, 20000, 4000)), assistant(u(2, 3400, 60000, 1000)))
    r = pilead("check", SID)
    lines = r.stdout.splitlines()
    assert lines[:3] == ["reported", str(sdir / "report-0001.md"), "Built the thing; tests pass."]
    assert lines[-3:] == ["Changed: src/alpha.py, src/beta.py",
                          "tokens 85k/4.6k over 2 requests · ctx 64k",  # anthropic/sonnet is not in models.json
                          "refs: unchanged since packet 0001"]
    # p22: the fixture's lead is not registered, so check names the orphan on stderr
    assert r.stderr == f"pilead: {SID} is an orphan — its lead lead-1 is no longer registered; pilead adopt {SID}\n"


def test_check_tokens_line_heavy_with_window(tmp_path):
    from test_usage import assistant, u
    wt, sdir = make_session(tmp_path, status="busy")
    meta = json.loads((sdir / "meta.json").read_text())
    meta["model"] = "anthropic/claude-haiku-4-5"
    (sdir / "meta.json").write_text(json.dumps(meta))
    (home(tmp_path) / "models.json").write_text(json.dumps({"fetched": 0, "models": [
        {"provider": "anthropic", "id": "claude-haiku-4-5", "context": "200K"}]}))
    log_for(SID, wt, assistant(u(10, 90, 170_000, 0)), assistant(u(10, 1000, 239_000, 0)))
    r = pilead("check", SID)
    assert r.stdout.splitlines() == ["busy", "tokens 409k/1.1k over 2 requests · ctx 240k/200k ! · heavy",
                                     "refs: unchanged since packet 0001"]


def test_check_with_no_log_stdout_unchanged_and_one_stderr_line(tmp_path):
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    r = pilead("check", SID)
    assert r.stdout == ("reported\n" + str(sdir / "report-0001.md") + "\n" + "Built the thing; tests pass.\n"
                        "Status: clean\nRisk flags: none\nUNVERIFIED: none\nChanged: src/alpha.py, src/beta.py\n"
                        "refs: unchanged since packet 0001\n")
    assert r.stderr == (f"pilead: no session log found for {SID}; tokens and context are unknown\n"
                        f"pilead: {SID} is an orphan — its lead lead-1 is no longer registered; pilead adopt {SID}\n")  # p22


@pytest.mark.parametrize("with_log", [False, True])
def test_check_refs_only_is_one_stdout_line_and_nothing_on_stderr(tmp_path, with_log):
    from test_usage import assistant, u
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    if with_log:
        log_for(SID, wt, assistant(u(3, 40, 900, 100)))
    r = pilead("check", SID, "--refs-only")
    assert (r.returncode, r.stdout, r.stderr) == (0, "refs: unchanged since packet 0001\n", "")
    assert snaps(tmp_path, SID) == []  # --refs-only writes no snapshot

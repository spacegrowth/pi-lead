"""`pilead retire`: write the successor seed, then close the session through `pilead close`'s own function
as superseded by `successor-seed`. Driven through bin/pilead with the fake `pi` and the terminal seam of
tests/test_resume.py (a recording `osascript` stub, a `ps` stub first on PATH); a live process is a child
this file starts and stops. No real tab is opened or closed and the real pi never runs."""
import json
import os
import time

from test_close_variants import tree
from test_resume import (H, child, env, events, mark_reported, meta, pilead, refused, sdir, spawn,  # noqa: F401
                         terminal)

REPORT = """Parser rewrite landed; 4 tests, suite green, staged.
Status: clean
Risk flags: none
UNVERIFIED: none
Changed: lib/x.py
"""


def seed_of(env, sid):
    return sdir(env, sid) / "successor-seed.md"


def osa_log(env):
    p = env / "osa.log"
    return p.read_text() if p.is_file() else ""


def report(env, sid, n=1, text=REPORT):
    (sdir(env, sid) / f"report-{n:04d}.md").write_text(text)
    (sdir(env, sid) / "status").write_text("reported\n")


def still_running(p):
    return p.poll() is None


# ── a retire ────────────────────────────────────────────────────────────────────────────────────
def test_a_reported_session_retires(env):
    sid = spawn(env)
    report(env, sid)
    d = sdir(env, sid)
    before = {k: v for k, v in tree(d).items() if k not in ("meta.json", "status")}
    r = pilead("retire", sid)
    p = seed_of(env, sid)
    assert r.stdout.splitlines() == [
        f"closed {sid} (superseded by successor-seed)",
        f"retired {sid} — successor seed (1 packet(s)) at:",
        f"  {p}",
        f"  spawn its successor with: pilead spawn {(env / 'wt').resolve()} topic <packet.md> --seed {sid}",
    ]
    assert r.stderr == ""
    text = p.read_text()
    assert text.startswith(f"# Successor seed — {sid}\n") and "### 0001 — do the thing\n" in text
    assert "- Outcome: Parser rewrite landed; 4 tests, suite green, staged.\n- Status: clean\n" in text
    assert "- Context window: 1M\n" in text and f"- Packet/report files: {d}\n" in text
    m = meta(env, sid)
    assert (d / "status").read_text() == "closed\n" and m["superseded_by"] == "successor-seed"
    after = {k: v for k, v in tree(d).items() if k not in ("meta.json", "status", "successor-seed.md")}
    assert after == before  # every other file of the session, byte for byte (retire wrote no usage.json)
    assert pilead("check", sid).stdout.splitlines()[0] == "superseded"


def test_the_ledger_has_close_superseded_then_retired(env):
    sid = spawn(env)
    report(env, sid)
    pilead("retire", sid)
    evs = [e for e in events(env) if e.get("session_id") == sid]
    assert [e["event"] for e in evs][-3:] == ["superseded", "retired", "report_looked_at"]
    assert evs[-3]["superseded_by"] == "successor-seed"
    assert {k: v for k, v in evs[-2].items() if k != "ts"} == {
        "event": "retired", "session_id": sid, "seed": str(seed_of(env, sid)), "packets": 1, "forced": False}


def test_a_packet_with_no_report_is_seeded_as_no_report(env):
    sid = spawn(env)
    mark_reported(env, sid)  # "Done.\nStatus: clean\n": a report missing some fields
    (env / "p2.md").write_text("GOAL: the second thing\n")
    pilead("send", sid, str(env / "p2.md"))
    r = pilead("retire", sid, "--force")
    assert "successor seed (2 packet(s))" in r.stdout
    assert r.stderr == f"pilead: {sid} was busy on packet 0002; no report was written\n"  # close's own note
    text = seed_of(env, sid).read_text()
    assert "- Outcome: Done.\n- Status: clean\n- Risk flags: (not stated in the report)\n" in text
    assert "### 0002 — the second thing\n- Outcome: NO REPORT" in text
    assert f"- Packet: {sdir(env, sid) / 'packet-0002.md'}\n" in text
    assert "- Packets worked: 2 (1 reported, 1 unreported)\n" in text
    (ev,) = events(env, "retired")
    assert (ev["packets"], ev["forced"]) == (2, True)


def test_a_stalled_session_needs_no_force(env):
    sid = spawn(env)
    (sdir(env, sid) / "status").write_text("stalled\n")
    r = pilead("retire", sid)
    assert r.stdout.splitlines()[0] == f"closed {sid} (superseded by successor-seed)"
    assert "NO REPORT" in seed_of(env, sid).read_text()


def test_idle_paused_dead_and_closed_need_no_force(env):
    for st in ("idle", "paused", "dead", "closed"):
        sid = spawn(env)
        (sdir(env, sid) / "status").write_text(st + "\n")
        pilead("retire", sid)
        assert seed_of(env, sid).is_file(), st


def test_an_alive_busy_session_needs_force_and_its_process_is_signalled(env, child):
    sid = spawn(env)
    p = child()
    (sdir(env, sid) / "pid").write_text(f"{p.pid}\n")
    before = tree(H(env))
    refused(pilead("retire", sid, check=False), "busy on packet 0001", "unreported", f"pilead check {sid}",
            "--force")
    assert tree(H(env)) == before
    pilead("retire", sid, "--force")
    for _ in range(50):
        if not still_running(p):
            break
        time.sleep(0.05)
    assert not still_running(p)


def test_keep_tab_closes_no_tab_and_signals_no_process(env, child):
    sid = spawn(env)
    p = child()
    (sdir(env, sid) / "pid").write_text(f"{p.pid}\n")
    report(env, sid)
    log_before = osa_log(env)
    r = pilead("retire", sid, "--keep-tab")
    assert r.stdout.splitlines()[0] == f"closed {sid} (superseded by successor-seed) (tab kept)"
    time.sleep(0.2)
    assert still_running(p)
    assert "tell s to close" not in osa_log(env)[len(log_before):]
    m = meta(env, sid)
    assert m["kept_tab"] is True and m["superseded_by"] == "successor-seed"


def test_without_keep_tab_the_tab_is_asked_to_close(env):
    sid = spawn(env)
    report(env, sid)
    before = osa_log(env)
    pilead("retire", sid)
    assert "tell s to close" in osa_log(env)[len(before):]  # the close went to the terminal stub


# ── refusals: home byte-identical ───────────────────────────────────────────────────────────────
def test_a_busy_unreported_session_is_refused_without_force(env):
    sid = spawn(env)
    before = tree(H(env))
    r = pilead("retire", sid, check=False)
    refused(r, f"session {sid} is busy on packet 0001", "ends that work unreported", f"`pilead check {sid}`",
            "--force", "Nothing was retired")
    assert tree(H(env)) == before


def test_a_stored_stalled_that_is_busy_now_is_refused_and_nothing_is_stored(env, child):
    """The status is worked out as refresh would, but a refusal stores nothing (stalled → busy is not written)."""
    sid = spawn(env)
    (sdir(env, sid) / "pid").write_text(f"{child().pid}\n")
    (sdir(env, sid) / "status").write_text("stalled\n")
    before = tree(H(env))
    refused(pilead("retire", sid, check=False), "busy on packet 0001")
    assert tree(H(env)) == before


def test_a_pinned_session_is_refused(env):
    sid = spawn(env)
    report(env, sid)
    pilead("keep", sid)
    before = tree(H(env))
    refused(pilead("retire", sid, "--force", check=False), f"pilead keep {sid} --off", "pinned")
    assert tree(H(env)) == before


def test_an_already_retired_session_is_refused_naming_the_seed_and_spawn(env):
    sid = spawn(env)
    report(env, sid)
    pilead("retire", sid)
    before = tree(H(env))
    r = pilead("retire", sid, "--force", check=False)
    refused(r, "already retired", str(seed_of(env, sid)), "pilead spawn ", f"--seed {sid}")
    assert tree(H(env)) == before


def test_an_unknown_or_unusable_sid(env):
    before = tree(H(env))
    r = pilead("retire", "nobody", check=False)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "pilead: unknown session nobody\n")
    r = pilead("retire", "../x", check=False)
    assert r.returncode == 2 and "not usable" in r.stderr
    assert tree(H(env)) == before


def test_retire_deletes_nothing_and_other_sessions_are_untouched(env):
    a = spawn(env)
    b = spawn(env)
    report(env, a)
    names = {p.name for p in sdir(env, a).iterdir()}
    other = {k: v for k, v in tree(H(env)).items() if k.startswith(f"sessions/{b}") or k.startswith("leads/")}
    pilead("retire", a)
    assert names <= {p.name for p in sdir(env, a).iterdir()}
    assert {k: v for k, v in tree(H(env)).items()
            if k.startswith(f"sessions/{b}") or k.startswith("leads/")} == other


def test_close_output_and_events_are_unchanged(env):
    sid = spawn(env)
    r = pilead("close", sid, "--supersede", "other")
    assert r.stdout == f"closed {sid} (superseded by other)\n"
    assert not events(env, "retired") and not seed_of(env, sid).exists()


def test_a_heavy_session_gets_the_hint(env):
    from test_cli_core import heavy_entries
    from test_usage import write_log
    sid = spawn(env, log=False)
    write_log(sid, (env / "wt").resolve(), heavy_entries())
    report(env, sid)
    pilead("retire", sid)
    text = seed_of(env, sid).read_text()
    assert text.count("- Hint: this session ran HEAVY") == 1
    assert not (sdir(env, sid) / "usage.json").exists()  # the reading wrote no cache


def test_meta_json_keeps_its_fields(env):
    sid = spawn(env)
    report(env, sid)
    old = meta(env, sid)
    pilead("retire", sid)
    new = meta(env, sid)
    assert {k: v for k, v in new.items() if k not in ("status", "superseded_by")} == \
           {k: v for k, v in old.items() if k not in ("status",)}
    assert json.loads((sdir(env, sid) / "meta.json").read_text())["status"] == "closed"
    assert os.path.isfile(seed_of(env, sid))

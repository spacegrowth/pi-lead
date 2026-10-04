"""`pilead stats` over a synthetic ledger (written line by line with explicit timestamps), session
directories and pi session logs this file writes."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"

REPORT = "Done.\nStatus: {st}\nRisk flags: none\nUNVERIFIED: none\nChanged: a.py\n"


def H():
    return Path(os.environ["PI_LEAD_HOME"])


def iso(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def ev(event, ago, **fields):
    H().mkdir(parents=True, exist_ok=True)
    with open(H() / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"ts": iso(ago), "event": event, **fields}) + "\n")


def snap(sid, n, at, ago, prompt, output):
    ev("usage_snapshot", ago, session_id=sid, packet=n, at=at,
       usage=None if prompt is None else {"prompt": prompt, "output": output, "requests": 1})


def session(tmp_path, sid, packets, statuses=(), lead="lead-1", model="anthropic/claude-sonnet-5", st="closed"):
    sdir = H() / "sessions" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": lead, "worktree": str(tmp_path / f"wt-{sid}"), "topic": sid, "model": model,
        "status": st, "created": "-", "packets": packets}))
    (sdir / "status").write_text(st + "\n")
    for n in range(1, packets + 1):
        (sdir / f"packet-{n:04d}.md").write_text("GOAL\n")
    for n, s in enumerate(statuses, 1):
        if s:
            (sdir / f"report-{n:04d}.md").write_text(REPORT.format(st=s))


def stats(*args):
    r = subprocess.run([sys.executable, str(PILEAD), "stats", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def data(*args):
    return json.loads(stats("--json", *args))


def rows_by(d):
    return {(r["sid"], r["packet"]): r for r in d["rows"]}


def build(tmp_path):
    """s-real: packet 1 with a snapshot pair, then fix packets 2 and 3, then refs_accepted.
    s-est: a missing reported snapshot (packet 1) and a negative difference (packet 2); a log (1000/100).
    s-none: no usage at all, no commit event."""
    session(tmp_path, "s-real", 3, statuses=("clean", "clean-with-caveats", "clean"))
    ev("spawned", 5000, session_id="s-real", lead="lead-1")
    snap("s-real", 1, "sent", 5000, 0, 0)
    ev("report_verify", 4800, session_id="s-real", packet=1, verdict="MISMATCH")
    snap("s-real", 1, "reported", 4700, 12_000, 800)
    ev("report_verify", 4600, session_id="s-real", packet=1, verdict="COUNTS-MATCH")
    ev("packet_sent", 4500, session_id="s-real", packet=2)
    snap("s-real", 2, "sent", 4500, 12_000, 800)
    snap("s-real", 2, "reported", 4000, 20_000, 1000)
    ev("packet_sent", 3500, session_id="s-real", packet=3)
    ev("auto_commit", 3400, session_id="s-real", packet=3, cleared=False, reason="x")  # not a commit
    ev("refs_accepted", 3000, session_id="s-real", packet=3)
    ev("packet_sent", 2000, session_id="s-real", packet=4)  # after the commit: not a round (and no packet file)
    write_log("s-real", tmp_path / "wt-s-real", [assistant(u(1000, 1200, 29_000, 0, cost=1.5))])

    session(tmp_path, "s-est", 2, statuses=("partial", None), model="deepseek/deepseek-v4-flash")
    ev("spawned", 2500, session_id="s-est")
    snap("s-est", 1, "sent", 2500, 0, 0)
    ev("packet_sent", 2400, session_id="s-est", packet=2)
    snap("s-est", 2, "sent", 2400, 50_000, 5000)
    snap("s-est", 2, "reported", 2300, 40_000, 4000)  # negative
    ev("auto_commit", 2200, session_id="s-est", packet=2, cleared=True, reason="cleared")
    write_log("s-est", tmp_path / "wt-s-est", [assistant(u(1000, 100), provider="deepseek", model="deepseek-v4-flash")])

    session(tmp_path, "s-none", 1, statuses=("clean",), lead="lead-2")
    ev("spawned", 100, session_id="s-none")
    snap("s-none", 1, "sent", 100, 0, 0)
    snap("s-none", 1, "reported", 50, None, None)


def test_tokens_real_estimated_and_unknown(tmp_path):
    build(tmp_path)
    r = rows_by(data())
    assert (r[("s-real", 1)]["tokens_prompt"], r[("s-real", 1)]["tokens_output"]) == (12_000, 800)
    assert (r[("s-real", 2)]["tokens_prompt"], r[("s-real", 2)]["tokens_output"]) == (8_000, 200)
    assert r[("s-real", 1)]["tokens_estimated"] is False
    # packet 3 has no reported snapshot: the session total (30000/1200) over 3 packets, estimated
    assert (r[("s-real", 3)]["tokens_prompt"], r[("s-real", 3)]["tokens_output"], r[("s-real", 3)]["tokens_estimated"]) \
        == (10_000, 400, True)
    # s-est: packet 1 missing a reported snapshot, packet 2 negative → both the estimate 1000/100 over 2
    for n in (1, 2):
        assert (r[("s-est", n)]["tokens_prompt"], r[("s-est", n)]["tokens_output"], r[("s-est", n)]["tokens_estimated"]) \
            == (500, 50, True)
    # s-none: no usage anywhere (the reported snapshot is null, no log)
    assert (r[("s-none", 1)]["tokens_prompt"], r[("s-none", 1)]["tokens_output"], r[("s-none", 1)]["tokens_estimated"]) \
        == (None, None, False)
    out = stats()
    lines = {ln.split()[0] + " " + ln.split()[1]: ln for ln in out.splitlines() if ln.startswith("s-")}
    tokens = {k: ln.split()[7] for k, ln in lines.items()}  # SESSION PKT MODEL EFFORT ROUNDS VERDICT STATUS TOKENS
    assert tokens == {"s-real 0001": "12k/800", "s-real 0002": "8.0k/200", "s-real 0003": "~10k/400",
                      "s-est 0001": "~500/50", "s-est 0002": "~500/50", "s-none 0001": "-"}
    assert all(not ln.rstrip().endswith("$") and "$" not in ln for ln in lines.values())  # cost: trailer only


def test_rounds(tmp_path):
    build(tmp_path)
    r = rows_by(data())
    assert r[("s-real", 1)]["rounds"] == 2   # two fix packets, then refs_accepted
    assert r[("s-real", 2)]["rounds"] == 1
    assert r[("s-real", 3)]["rounds"] == 0
    assert r[("s-est", 1)]["rounds"] == 1    # a cleared auto_commit ends it
    assert r[("s-none", 1)]["rounds"] is None  # no commit event follows


def test_rounds_unknown_send_time(tmp_path):
    session(tmp_path, "s-x", 1, statuses=("clean",))
    ev("refs_accepted", 10, session_id="s-x", packet=1)
    assert rows_by(data())[("s-x", 1)]["rounds"] is None


def test_verdict_is_the_last_report_verify_and_status_the_reports(tmp_path):
    build(tmp_path)
    r = rows_by(data())
    assert r[("s-real", 1)]["verdict"] == "COUNTS-MATCH" and r[("s-real", 2)]["verdict"] is None
    assert [r[("s-real", n)]["status"] for n in (1, 2, 3)] == ["clean", "clean-with-caveats", "clean"]
    assert r[("s-est", 2)]["status"] is None


def test_summary_pct_clean_counts_only_exactly_clean(tmp_path):
    build(tmp_path)
    s = {g["model"]: g for g in data()["summary"]}
    sonnet = s["anthropic/claude-sonnet-5"]
    assert sonnet["packets"] == 4 and sonnet["pct_clean"] == 75.0  # s-real 1, 3 and s-none; not caveats
    assert sonnet["pct_counts_match"] == 25.0
    assert sonnet["mean_rounds"] == 1.0  # 2, 1, 0 (s-none has none)
    assert sonnet["tok_per_pkt"] == (12_800 + 8_200) / 2 and sonnet["tok_per_pkt_estimated"] is False
    ds = s["deepseek/deepseek-v4-flash"]
    assert ds["pct_clean"] == 0.0 and ds["tok_per_pkt"] == 550 and ds["tok_per_pkt_estimated"] is True
    out = stats()
    summary = out.split("SUMMARY\n", 1)[1].splitlines()
    assert summary[0].split() == ["MODEL", "PACKETS", "MEAN", "ROUNDS", "%", "COUNTS-MATCH", "%", "CLEAN", "TOK/PKT", "COST"]
    assert summary[1].split() == ["claude-sonnet-5", "4", "1.0", "25%", "75%", "10k", "$1.50"]
    assert summary[2].split() == ["deepseek-v4-flash", "2", "0.5", "0%", "0%", "~550", "$0.00"]  # rounds 1 and 0


def test_trailer_lines(tmp_path):
    build(tmp_path)
    out = stats()
    assert "    s-real: 3 packet(s) · tokens 30k/1.2k (prompt/output) · 10k/packet · cost $1.50" in out
    assert "    s-none: 1 packet(s) · tokens - (prompt/output) · -/packet · cost -" in out
    lines = out.splitlines()
    i = lines.index(next(ln for ln in lines if ln.startswith("    s-real:")))
    assert lines[i - 1].startswith("s-real") and lines[i - 1].split()[1] == "0003"


def test_since_keeps_recent_packets_and_unknown_send_times(tmp_path):
    build(tmp_path)
    session(tmp_path, "s-nots", 1)  # no ledger events at all: send time unknown
    keys = set(rows_by(data("--since", "0.02")))  # 0.02 days = 1728 s
    assert keys == {("s-none", 1), ("s-nots", 1)}
    assert len(rows_by(data("--since", "1"))) == 7  # all of them: 3 + 2 + 1 + 1


def test_lead_keeps_that_leads_executors_and_the_leadless(tmp_path):
    build(tmp_path)
    session(tmp_path, "s-free", 1, lead=None)
    assert {k[0] for k in rows_by(data("--lead", "lead-2"))} == {"s-none", "s-free"}


def test_json_types(tmp_path):
    build(tmp_path)
    d = data()
    assert set(d) == {"rows", "sessions", "summary"}
    for r in d["rows"]:
        assert isinstance(r["packet"], int) and isinstance(r["tokens_estimated"], bool)
        assert r["rounds"] is None or isinstance(r["rounds"], int)
        assert r["tokens_prompt"] is None or isinstance(r["tokens_prompt"], int)
    sess = {s["sid"]: s for s in d["sessions"]}
    assert sess["s-real"] == {"sid": "s-real", "model": "anthropic/claude-sonnet-5", "packets": 3, "prompt": 30_000,
                              "output": 1200, "tok_per_pkt": 31_200 / 3, "cost": 1.5}
    assert sess["s-none"]["prompt"] is None and sess["s-none"]["cost"] is None and sess["s-none"]["tok_per_pkt"] is None
    for g in d["summary"]:
        assert isinstance(g["packets"], int) and isinstance(g["pct_clean"], float)


def test_no_packets(tmp_path):
    assert stats() == "(no packets recorded)\n"
    assert data() == {"rows": [], "sessions": [], "summary": []}


def test_stats_writes_nothing_but_the_usage_cache(tmp_path):
    build(tmp_path)
    before = {p.relative_to(H()): p.read_bytes() for p in H().rglob("*") if p.is_file()}
    stats()
    after = {p.relative_to(H()): p.read_bytes() for p in H().rglob("*") if p.is_file()}
    new = {k for k in after if k not in before}
    assert all(k.name == "usage.json" for k in new) and new
    assert all(after[k] == v for k, v in before.items() if k.name != "usage.json")


# ── the estimate divides by all the session's packets, whatever --since keeps ────────────────────
def two_packets_one_recent(tmp_path):
    """s-two: packet 1 sent 10 days ago, packet 2 an hour ago; no usage snapshots, so both rows are
    estimates; its log totals 602k prompt / 3k output."""
    session(tmp_path, "s-two", 2)
    ev("spawned", 10 * 86400, session_id="s-two")
    ev("packet_sent", 3600, session_id="s-two", packet=2)
    write_log("s-two", tmp_path / "wt-s-two", [assistant(u(602_000, 3_000))])


def test_since_estimate_divides_by_every_packet_of_the_session(tmp_path):
    two_packets_one_recent(tmp_path)
    d = data("--since", "1")
    assert [(r["sid"], r["packet"]) for r in d["rows"]] == [("s-two", 2)]
    r = d["rows"][0]
    assert (r["tokens_prompt"], r["tokens_output"], r["tokens_estimated"]) == (301_000, 1_500, True)
    assert d["sessions"] == [{"sid": "s-two", "model": "anthropic/claude-sonnet-5", "packets": 2,
                              "prompt": 602_000, "output": 3_000, "tok_per_pkt": 605_000 / 2, "cost": 0.0}]
    out = stats("--since", "1")
    row = next(ln for ln in out.splitlines() if ln.startswith("s-two"))
    assert row.split()[7] == "~301k/1.5k"
    assert "    s-two: 1 of 2 packet(s) shown · tokens 602k/3.0k (prompt/output) · 302k/packet · cost $0.00" in out
    summary = out.split("SUMMARY\n", 1)[1].splitlines()
    assert summary[1].split()[-2] == "~302k"  # the model's estimate uses all the session's packets too


def test_without_a_filter_the_estimate_and_trailer_are_as_before(tmp_path):
    two_packets_one_recent(tmp_path)
    d = data()
    assert [(r["packet"], r["tokens_prompt"], r["tokens_output"]) for r in d["rows"]] == [(1, 301_000, 1_500),
                                                                                         (2, 301_000, 1_500)]
    out = stats()
    assert "    s-two: 2 packet(s) · tokens 602k/3.0k (prompt/output) · 302k/packet · cost $0.00" in out


def test_lead_filter_trailer_says_what_it_counts(tmp_path):
    two_packets_one_recent(tmp_path)
    out = stats("--lead", "lead-1")
    assert "    s-two: 2 of 2 packet(s) shown · tokens 602k/3.0k (prompt/output) · 302k/packet · cost $0.00" in out


# ── EFFORT (`pilead spawn --effort`) ─────────────────────────────────────────────────────────────
def test_effort_column_and_json_field(tmp_path):
    session(tmp_path, "s-eff", 1, statuses=("clean",))
    mp = H() / "sessions" / "s-eff" / "meta.json"
    mp.write_text(json.dumps({**json.loads(mp.read_text()), "effort": "low"}))
    session(tmp_path, "s-old", 1, statuses=("clean",))  # a record with no effort
    out = stats()
    header = out.splitlines()[0].split()
    assert header[:4] == ["SESSION", "PKT", "MODEL", "EFFORT"]
    lines = {ln.split()[0]: ln.split() for ln in out.splitlines() if ln.startswith("s-")}
    assert lines["s-eff"][3] == "low" and lines["s-old"][3] == "-"
    d = data()
    r = rows_by(d)
    assert r[("s-eff", 1)]["effort"] == "low" and r[("s-old", 1)]["effort"] is None
    assert all("effort" not in s for s in d["sessions"])  # sessions and summary are unchanged
    assert all("effort" not in g for g in d["summary"])

"""pilead stats — one row per packet ever sent (closed and dead sessions included): rounds, verify
verdict, report status, tokens used while it was in flight; a trailer per session and a SUMMARY by
model (claude-relay's `stats_data` / `cmd_stats`). Reads the ledger and the session directories;
writes nothing but the usage cache.

- TOKENS = the packet's `usage_snapshot` at "reported" minus the one at "sent". When either is missing
  or null, or the difference is negative, the session's total over its packet count, prefixed `~` (an
  estimate); `-` with no usage at all. The packet count is ALL the session's packets, whatever
  `--since` or `--lead` keeps: a session's `packets` in the JSON, and its trailer's "N of M shown".
- ROUNDS = packets sent later to the same session before the work was committed — "committed" being the
  first `refs_accepted`, or `auto_commit` with `cleared` true, after the packet was sent; `-` when the
  send time is unknown or no such event follows.
- EFFORT = the session's thinking level from its meta.json (`pilead spawn`); `-` (JSON null) when it has none.
- VERDICT = the last `report_verify` for the packet; STATUS = its report's `Status:` field."""
import calendar
import json
import sys
import time
from pathlib import Path

from .. import ledger, state, usage
from ..views import c, model_id, table

ORDER = 56
RESOLVE = ("lead",)

sys.path.insert(0, str(state.ROOT / "vendor" / "relay"))
import report_verify  # noqa: E402


def _epoch(ts):
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def _sent_ts(events, n):
    """When packet n was sent: its `packet_sent`, else (packet 1) `spawned`, else its "sent" usage_snapshot."""
    for e in events:
        if e.get("event") == "packet_sent" and e.get("packet") == n and e.get("ts"):
            return e["ts"]
    if n == 1:
        for e in events:
            if e.get("event") == "spawned" and e.get("ts"):
                return e["ts"]
    for e in events:
        if e.get("event") == "usage_snapshot" and e.get("at") == "sent" and e.get("packet") == n and e.get("ts"):
            return e["ts"]
    return None


def _snapshot(events, n, at):
    """The packet's usage_snapshot `usage` at `at` (the first one), or None."""
    for e in events:
        if e.get("event") == "usage_snapshot" and e.get("packet") == n and e.get("at") == at:
            return e.get("usage") if isinstance(e.get("usage"), dict) else None
    return None


def real_tokens(events, n):
    """(prompt, output) used while packet n was in flight, or None (see the module docstring)."""
    start, end = _snapshot(events, n, "sent"), _snapshot(events, n, "reported")
    if start is None or end is None:
        return None
    try:
        dp = int(end.get("prompt") or 0) - int(start.get("prompt") or 0)
        do = int(end.get("output") or 0) - int(start.get("output") or 0)
    except (TypeError, ValueError):
        return None
    if dp < 0 or do < 0:
        return None
    return dp, do


def verdict(events, n):
    v = None
    for e in events:
        if e.get("event") == "report_verify" and e.get("packet") == n:
            v = e.get("verdict")
    return v


def rounds(events, n, sent):
    if sent is None:
        return None
    commits = [e["ts"] for e in events if e.get("ts") and e["ts"] > sent and (
        e.get("event") == "refs_accepted" or (e.get("event") == "auto_commit" and e.get("cleared") is True))]
    if not commits:
        return None
    commit = min(commits)
    return sum(1 for e in events if e.get("event") == "packet_sent" and isinstance(e.get("packet"), int)
               and e["packet"] > n and e.get("ts") and e["ts"] < commit)


def _report_status(path):
    try:
        return report_verify.parse_tldr(Path(path).read_text()).get("status")
    except Exception:
        return None


def _packets(sdir):
    out = []
    for p in sdir.glob("packet-*.md"):
        try:
            out.append(int(p.stem.split("-", 1)[1]))
        except ValueError:
            continue
    return sorted(out)


def stats_data(home, lead_sid=None, since_days=None):
    home = Path(home)
    by_sid = {}
    for e in ledger.read(home):
        by_sid.setdefault(e.get("session_id"), []).append(e)
    cutoff = time.time() - float(since_days) * 86400 if since_days is not None else None
    rows, sessions = [], []
    d = home / "sessions"
    for mp in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        sid = mp.parent.name  # a session's id is its directory's name, not the record's `sid` field
        if not state.valid_session_sid(sid):
            continue  # a directory named otherwise holds no session
        try:
            meta = json.loads(mp.read_text())
            if not isinstance(meta, dict):
                continue
        except Exception:
            continue
        meta["sid"] = sid
        if lead_sid and meta.get("lead") not in (None, "", lead_sid):
            continue
        events = by_sid.get(sid, [])
        model = meta.get("model") or None
        effort = meta.get("effort") if isinstance(meta.get("effort"), str) and meta["effort"].strip() else None
        srows = []
        allp = _packets(mp.parent)
        for n in allp:
            sent = _sent_ts(events, n)
            if cutoff is not None:
                ep = _epoch(sent)
                if ep is not None and ep < cutoff:
                    continue  # an unknown send time is kept
            real = real_tokens(events, n)
            row = {"sid": sid, "packet": n, "model": model, "effort": effort, "rounds": rounds(events, n, sent),
                   "verdict": verdict(events, n), "status": _report_status(state.report_path(home, sid, n)),
                   "tokens_prompt": real[0] if real else None, "tokens_output": real[1] if real else None,
                   "tokens_estimated": False}
            srows.append(row)
        if not srows:
            continue
        u = usage.for_session(home, sid, meta.get("worktree") or None)
        k = len(allp)  # every packet of the session, not only those --since kept
        prompt = int(u["prompt"]) if u else None
        output = int(u["output"]) if u else None
        for r in srows:
            if r["tokens_prompt"] is None and u:
                r["tokens_prompt"], r["tokens_output"] = round(prompt / k), round(output / k)
                r["tokens_estimated"] = True
        rows.extend(srows)
        sessions.append({"sid": sid, "model": model, "packets": k, "prompt": prompt, "output": output,
                         "tok_per_pkt": (prompt + output) / k if u else None,
                         "cost": (u or {}).get("cost")})

    groups = {}
    for r in rows:
        g = groups.setdefault(r["model"] or "-", {"packets": 0, "rounds": [], "match": 0, "clean": 0, "real": []})
        g["packets"] += 1
        if r["rounds"] is not None:
            g["rounds"].append(r["rounds"])
        if r["verdict"] == "COUNTS-MATCH":
            g["match"] += 1
        if (r["status"] or "").strip() == "clean":  # exactly `Status: clean`, not clean-with-caveats
            g["clean"] += 1
        if r["tokens_prompt"] is not None and not r["tokens_estimated"]:
            g["real"].append(r["tokens_prompt"] + r["tokens_output"])
    summary = []
    for key in sorted(groups):
        g = groups[key]
        ss = [s for s in sessions if (s["model"] or "-") == key]
        if g["real"]:
            tpp, est = sum(g["real"]) / len(g["real"]), False
        else:
            known = [s for s in ss if s["tok_per_pkt"] is not None]
            pk = sum(s["packets"] for s in known)
            tpp = sum(s["prompt"] + s["output"] for s in known) / pk if pk else None
            est = tpp is not None
        costs = [s["cost"] for s in ss if s["cost"] is not None]
        summary.append({"model": None if key == "-" else key, "packets": g["packets"],
                        "mean_rounds": sum(g["rounds"]) / len(g["rounds"]) if g["rounds"] else None,
                        "pct_counts_match": round(100.0 * g["match"] / g["packets"], 1),
                        "pct_clean": round(100.0 * g["clean"] / g["packets"], 1),
                        "tok_per_pkt": tpp, "tok_per_pkt_estimated": est,
                        "cost": sum(costs) if costs else None})
    return {"rows": rows, "sessions": sessions, "summary": summary}


def _tokens_cell(p, o, est):
    if p is None or o is None:
        return "-"
    return ("~" if est else "") + f"{usage.human_tokens(p)}/{usage.human_tokens(o)}"


def _cost(v):
    return f"${v:.2f}" if v is not None else "-"


def cmd_stats(a):
    home = state.home_dir(a.home)
    data = stats_data(home, lead_sid=a.lead, since_days=a.since)
    filtered = a.lead is not None or a.since is not None
    if a.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data["rows"]:
        print("(no packets recorded)")
        return 0
    cols = [("SESSION", 36), ("PKT", 4), ("MODEL", 24), ("EFFORT", 7), ("ROUNDS", 6), ("VERDICT", 14), ("STATUS", 22),
            ("TOKENS", 14), ("COST", 10)]
    body = []
    for r in data["rows"]:
        body.append([(r["sid"], ()), (f"{r['packet']:04d}", ()), (model_id(r["model"]) or "-", ()),
                     (r["effort"] or "-", ()), (str(r["rounds"]) if r["rounds"] is not None else "-", ()), (r["verdict"] or "-", ()),
                     (r["status"] or "-", ()),
                     (_tokens_cell(r["tokens_prompt"], r["tokens_output"], r["tokens_estimated"]), ()),
                     (" ", ())])  # the session's cost is on its trailer line only
    lines = table(cols, body)
    print(c(lines[0], "bold"))
    sess = {s["sid"]: s for s in data["sessions"]}
    rows = data["rows"]
    for i, r in enumerate(rows):
        print(lines[i + 1])
        if i + 1 == len(rows) or rows[i + 1]["sid"] != r["sid"]:
            s = sess[r["sid"]]
            tok = f"{usage.human_tokens(s['prompt'])}/{usage.human_tokens(s['output'])}" if s["prompt"] is not None else "-"
            per = usage.human_tokens(s["tok_per_pkt"]) if s["tok_per_pkt"] is not None else "-"
            shown = sum(1 for x in rows if x["sid"] == r["sid"])
            count = f"{shown} of {s['packets']} packet(s) shown" if filtered else f"{s['packets']} packet(s)"
            print(c(f"    {s['sid']}: {count} · tokens {tok} (prompt/output) · {per}/packet · "
                    f"cost {_cost(s['cost'])}", "dim"))
    print()
    print(c("SUMMARY", "bold"))
    srows = []
    for g in data["summary"]:
        tpp = "-" if g["tok_per_pkt"] is None else ("~" if g["tok_per_pkt_estimated"] else "") + usage.human_tokens(g["tok_per_pkt"])
        srows.append([(model_id(g["model"]) or "-", ()), (str(g["packets"]), ()),
                      (f"{g['mean_rounds']:.1f}" if g["mean_rounds"] is not None else "-", ()),
                      (f"{g['pct_counts_match']:.0f}%", ()), (f"{g['pct_clean']:.0f}%", ()), (tpp, ()),
                      (_cost(g["cost"]), ())])
    for line in table([("MODEL", 24), ("PACKETS", 7), ("MEAN ROUNDS", 11), ("% COUNTS-MATCH", 14),
                       ("% CLEAN", 7), ("TOK/PKT", 10), ("COST", 10)], srows):
        print(line)
    return 0


def _days(v):
    f = float(v)
    if not f >= 0 or f == float("inf"):
        raise ValueError(v)
    return f


def register(verb):
    p = verb("stats", cmd_stats, "per-packet rounds, verdicts, report status and tokens, by model")
    p.add_argument("--lead", metavar="SID", help="only this lead's executors (and those with no lead)")
    p.add_argument("--since", metavar="DAYS", type=_days, help="only packets sent within this many days")
    p.add_argument("--json", action="store_true")

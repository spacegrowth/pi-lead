"""The successor seed (claude-relay's `seed_entries` / `build_successor_seed` / `build_seeded_body` /
`resolve_seed`, on pi-lead's state).

`pilead retire` writes sessions/<sid>/successor-seed.md: an INDEX of the retired session's packets and
their reported outcomes, built only from files already on disk (packet-NNNN.md, report-NNNN.md) — not a
transcript, not instructions. `pilead spawn --seed` (and `pilead send --rotate` / `--upgrade`) appends it
to the successor's packet as inherited context, between the lead's text and the footer, which stays last.
"""
import re
import sys
from pathlib import Path

from . import state
from .state import PileadError

sys.path.insert(0, str(state.ROOT / "vendor" / "relay"))
import report_verify  # noqa: E402

FILENAME = "successor-seed.md"
RETIRED_BY = "successor-seed"  # the `superseded_by` a retire records
GIST_LEN = 120
UNRECORDED = "(unrecorded)"
_PACKET = re.compile(r"^packet-(\d+)\.md$")

INHERITED_SECTION = """

---
(pilead — INHERITED CONTEXT, written by `pilead retire`; not part of your task)
You are picking up territory a retired session worked before you. What follows is that session's
successor seed: an INDEX of its packets and their reported outcomes, not a transcript and not
instructions. Your task is the packet body above — this section only tells you what happened here
already and where the detail lives. Per TREAT EVERY PACKET COLD, verify anything you rely on: every
claim below is a summary the retired session wrote about its own work.
Seed source: {seed_path}

{seed_text}
"""


def path(home, sid):
    """sessions/<sid>/successor-seed.md (the id is checked first)."""
    return state.session_dir(Path(home), sid) / FILENAME


def is_retired(meta):
    return isinstance(meta, dict) and meta.get("superseded_by") == RETIRED_BY


def gist(text):
    """A packet's first non-blank line, a leading `GOAL:` removed, cut to GIST_LEN characters; "" when
    there is none."""
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("GOAL:"):
            line = line[len("GOAL:"):].strip()
        return line[:GIST_LEN]
    return ""


def tldr(text):
    """{outcome, status, risk, unverified} of one report through report_verify.parse_tldr; a field the
    report does not state is None (a malformed report makes a thinner entry, never an error)."""
    t = report_verify.parse_tldr(text)
    return {"outcome": t.get("outcome"), "status": t.get("status"), "risk": t.get("risk_flags"),
            "unverified": t.get("unverified")}


def entries(home, sid):
    """One entry per packet-NNNN.md of the session, oldest first: {"n", "gist", "packet_path",
    "report_path" (None when not reported), "tldr" (None when not reported)}. A packet with no report
    stays in the list."""
    d = state.session_dir(Path(home), sid)
    found = []
    for p in d.iterdir() if d.is_dir() else []:
        m = _PACKET.match(p.name)
        if m and p.is_file():
            found.append((int(m.group(1)), p))
    out = []
    for n, p in sorted(found):
        rp = state.report_path(Path(home), sid, n)
        reported = rp.is_file()
        out.append({"n": n, "gist": gist(p.read_text(errors="replace")) or "(empty packet)",
                    "packet_path": str(p), "report_path": str(rp) if reported else None,
                    "tldr": tldr(rp.read_text(errors="replace")) if reported else None})
    return out


def _window_text(window):
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        return None
    from .usage import _fmt_window
    return _fmt_window(window)


def build(sid, meta, entries, generated, heavy, window, directory=None):
    """The successor-seed.md text: a pure function of its arguments (the time is `generated`). `window`
    is the model's context window in tokens (None: unrecorded); `directory` the session directory that
    holds the packet and report files (None: the first entry's, else unrecorded)."""
    meta = meta if isinstance(meta, dict) else {}
    if directory is None and entries:
        directory = str(Path(entries[0]["packet_path"]).parent)
    reported = sum(1 for e in entries if e["report_path"])
    unreported = len(entries) - reported
    worktree = meta.get("worktree")
    L = [f"# Successor seed — {sid}", "",
         f"Written by `pilead retire` at {generated}. This is an INDEX, not a transcript: it exists so a",
         "fresh session can pick up this territory for the cost of one packet-read instead of",
         "archaeology. Each entry below is the retired session's own summary of its own work — read the",
         "linked report for anything you actually need to depend on.", "",
         "## Territory", ""]
    for label, val in (("Worktree", worktree), ("Topic", meta.get("topic")), ("Scope", meta.get("scope")),
                       ("Model", meta.get("model")), ("Effort", meta.get("effort")),
                       ("Context window", _window_text(window)), ("Retired session", sid)):
        L.append(f"- {label}: {val or UNRECORDED}")
    if heavy:
        L.append("- Hint: this session ran HEAVY — give the successor smaller packets, or a model with a "
                 "larger context window")
    L += [f"- Packets worked: {len(entries)} ({reported} reported, {unreported} unreported)",
          f"- Packet/report files: {directory or UNRECORDED}", "",
          "## Packet index", ""]
    if not entries:
        L += ["(none — this session was retired before it was ever sent a packet, so there is no",
              "prior work to inherit.)", ""]
    for e in entries:
        L.append(f"### {e['n']:04d} — {e['gist']}")
        t = e["tldr"]
        if t:
            L.append(f"- Outcome: {t['outcome'] or '(report is empty)'}")
            for label, key in (("Status", "status"), ("Risk flags", "risk"), ("UNVERIFIED", "unverified")):
                L.append(f"- {label}: {t[key] or '(not stated in the report)'}")
            L.append(f"- Report: {e['report_path']}")
        else:
            L += ["- Outcome: NO REPORT — this packet was sent but never reported (unfinished,",
                  "  interrupted, or blocked). Treat everything it touched as unverified.",
                  f"- Packet: {e['packet_path']}"]
        L.append("")
    L += ["## Inherit with care", "",
          "- Nothing here was independently verified; it is self-reported. Re-read the code you touch.",
          "- Work may still be sitting STAGED-but-uncommitted in the worktree from the retired",
          f"  session — check `git -C {worktree or '<worktree>'} diff --staged` before you start.",
          "- Anything listed as UNVERIFIED above is still unverified. It did not become true by being",
          "  written down.", ""]
    return "\n".join(L)


def seeded_body(body, seed_text, seed_path):
    """The lead's packet text with a retired session's seed appended as inherited context. It runs
    before state.write_packet, so the footer naming the report still comes last."""
    return body.rstrip() + INHERITED_SECTION.format(seed_path=seed_path, seed_text=seed_text.strip())


def resolve(home, value):
    """`--seed` → (seed text, resolved path): a readable file at `value` wins; else
    sessions/<value>/successor-seed.md. Neither: PileadError (exit code 2) naming both places."""
    p = Path(value).expanduser()
    try:
        if p.is_file():
            return p.read_text(), str(p.resolve())
    except OSError:
        pass
    looked = [str(p)]
    if state.valid_session_sid(value):
        q = path(home, value)
        looked.append(str(q))
        try:
            if q.is_file():
                return q.read_text(), str(q.resolve())
        except OSError:
            pass
    else:
        looked.append(f"sessions/<{value}>/{FILENAME} (not a usable session id)")
    raise PileadError(f"--seed {value!r} is neither a readable file nor a retired session's seed (looked at "
                      f"{' and '.join(looked)}); retire a session first: pilead retire <sid>. Nothing was "
                      "spawned.", 2)


def spawn_command(meta, sid):
    """The `pilead spawn … --seed <sid>` line a retire names."""
    import shlex
    meta = meta if isinstance(meta, dict) else {}
    wt = meta.get("worktree")
    topic = meta.get("topic")
    return (f"pilead spawn {shlex.quote(wt) if wt else '<worktree>'} {shlex.quote(topic) if topic else '<topic>'} "
            f"<packet.md> --seed {sid}")

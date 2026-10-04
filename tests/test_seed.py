"""The successor seed (lib/pilead/seed.py): `entries` read from a session's packet and report files, `build`
compared byte for byte with the text held here, and a seeded spawn's packet-0001.md (the lead's text, then
the inherited-context section, then the footer, last). The spawn is driven through bin/pilead with the fake
`pi` and the recording `osascript` stub of tests/test_resume.py; no real tab is opened."""
import sys
from pathlib import Path

from test_resume import H, env, pilead, sdir, terminal  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import seed, state  # noqa: E402

FULL = """Parser rewrite landed behind a flag; 9 new tests, suite green, staged.
Status: clean-with-caveats
Risk flags: touches   the ledger
UNVERIFIED: the live tab path
Changed: lib/x.py

## Detail
more
"""
THIN = "Half of it done.\nStatus: partial\n"


def session(home, sid, files):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text)
    return d


# ── entries ─────────────────────────────────────────────────────────────────────────────────────
def test_entries_reported_unreported_and_a_thin_report(tmp_path):
    home = tmp_path / "h"
    d = session(home, "s1", {
        "packet-0001.md": "\n\nGOAL:   rewrite the parser\nmore\n",
        "report-0001.md": FULL,
        "packet-0002.md": "# fix the flag\n",
        "packet-0010.md": "GOAL: " + "x" * 200 + "\n",
        "report-0010.md": THIN,
        "packet-notes.md": "not a packet\n",
    })
    es = seed.entries(home, "s1")
    assert [e["n"] for e in es] == [1, 2, 10]  # numeric order, oldest first; packet-notes.md is not a packet
    assert es[0] == {"n": 1, "gist": "rewrite the parser", "packet_path": str(d / "packet-0001.md"),
                     "report_path": str(d / "report-0001.md"),
                     "tldr": {"outcome": FULL.splitlines()[0], "status": "clean-with-caveats",
                              "risk": "touches the ledger", "unverified": "the live tab path"}}
    assert es[1] == {"n": 2, "gist": "# fix the flag", "packet_path": str(d / "packet-0002.md"),
                     "report_path": None, "tldr": None}
    assert es[2]["gist"] == "x" * 120
    assert es[2]["tldr"] == {"outcome": "Half of it done.", "status": "partial", "risk": None, "unverified": None}


def test_entries_of_an_empty_packet_and_of_no_packets(tmp_path):
    home = tmp_path / "h"
    session(home, "s1", {"packet-0001.md": "\n  \n"})
    assert seed.entries(home, "s1")[0]["gist"] == "(empty packet)"
    session(home, "s2", {"meta.json": "{}"})
    assert seed.entries(home, "s2") == []


# ── build, byte for byte ────────────────────────────────────────────────────────────────────────
META = {"worktree": "/wt/a", "topic": "parser", "scope": "the parser", "model": "anthropic/claude-sonnet-5",
        "effort": "high"}
TWO = [
    {"n": 1, "gist": "rewrite the parser", "packet_path": "/h/sessions/s1/packet-0001.md",
     "report_path": "/h/sessions/s1/report-0001.md",
     "tldr": {"outcome": "Parser rewrite landed.", "status": "clean", "risk": "none", "unverified": None}},
    {"n": 2, "gist": "fix the flag", "packet_path": "/h/sessions/s1/packet-0002.md", "report_path": None,
     "tldr": None},
]

HEADER = """# Successor seed — s1

Written by `pilead retire` at 2026-09-27T10:00:00Z. This is an INDEX, not a transcript: it exists so a
fresh session can pick up this territory for the cost of one packet-read instead of
archaeology. Each entry below is the retired session's own summary of its own work — read the
linked report for anything you actually need to depend on.

## Territory

"""
CARE = """## Inherit with care

- Nothing here was independently verified; it is self-reported. Re-read the code you touch.
- Work may still be sitting STAGED-but-uncommitted in the worktree from the retired
  session — check `git -C {wt} diff --staged` before you start.
- Anything listed as UNVERIFIED above is still unverified. It did not become true by being
  written down.
"""

EXPECTED_TWO = HEADER + """- Worktree: /wt/a
- Topic: parser
- Scope: the parser
- Model: anthropic/claude-sonnet-5
- Effort: high
- Context window: 1M
- Retired session: s1
- Packets worked: 2 (1 reported, 1 unreported)
- Packet/report files: /h/sessions/s1

## Packet index

### 0001 — rewrite the parser
- Outcome: Parser rewrite landed.
- Status: clean
- Risk flags: none
- UNVERIFIED: (not stated in the report)
- Report: /h/sessions/s1/report-0001.md

### 0002 — fix the flag
- Outcome: NO REPORT — this packet was sent but never reported (unfinished,
  interrupted, or blocked). Treat everything it touched as unverified.
- Packet: /h/sessions/s1/packet-0002.md

""" + CARE.format(wt="/wt/a")

HEAVY_LINE = ("- Hint: this session ran HEAVY — give the successor smaller packets, or a model with a larger "
              "context window\n")


def test_build_two_packets_one_unreported():
    assert seed.build("s1", META, TWO, "2026-09-27T10:00:00Z", False, 1_000_000) == EXPECTED_TWO


def test_build_heavy_adds_one_hint_line_after_the_retired_session():
    got = seed.build("s1", META, TWO, "2026-09-27T10:00:00Z", True, 1_000_000)
    want = EXPECTED_TWO.replace("- Retired session: s1\n", "- Retired session: s1\n" + HEAVY_LINE)
    assert got == want and got.count("HEAVY") == 1


def test_build_no_packets_and_unrecorded_fields():
    want = HEADER + """- Worktree: (unrecorded)
- Topic: (unrecorded)
- Scope: (unrecorded)
- Model: (unrecorded)
- Effort: (unrecorded)
- Context window: (unrecorded)
- Retired session: s1
- Packets worked: 0 (0 reported, 0 unreported)
- Packet/report files: (unrecorded)

## Packet index

(none — this session was retired before it was ever sent a packet, so there is no
prior work to inherit.)

""" + CARE.format(wt="<worktree>")
    assert seed.build("s1", {}, [], "2026-09-27T10:00:00Z", False, None) == want


def test_build_no_packets_with_a_directory_and_heavy():
    got = seed.build("s1", META, [], "2026-09-27T10:00:00Z", True, 200_000, directory="/h/sessions/s1")
    assert "- Context window: 200k\n" + "- Retired session: s1\n" + HEAVY_LINE in got
    assert "- Packets worked: 0 (0 reported, 0 unreported)\n- Packet/report files: /h/sessions/s1\n" in got


def test_build_is_pure():
    a = seed.build("s1", META, TWO, "T", False, None)
    assert a == seed.build("s1", dict(META), [dict(e) for e in TWO], "T", False, None)
    assert "Written by `pilead retire` at T." in a


def test_the_report_empty_and_unstated_fields():
    es = [{"n": 3, "gist": "g", "packet_path": "/p", "report_path": "/r",
           "tldr": {"outcome": None, "status": None, "risk": None, "unverified": None}}]
    got = seed.build("s1", META, es, "T", False, None)
    assert ("### 0003 — g\n- Outcome: (report is empty)\n- Status: (not stated in the report)\n"
            "- Risk flags: (not stated in the report)\n- UNVERIFIED: (not stated in the report)\n- Report: /r\n") in got


# ── the successor's packet ──────────────────────────────────────────────────────────────────────
def test_seeded_body_puts_the_section_between_the_text_and_the_footer(env):
    seed_file = env / "seed.md"
    seed_file.write_text(EXPECTED_TWO)
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--lead", "lead-1", "--seed",
               str(seed_file))
    sid = r.stdout.split()[1]
    text = (sdir(env, sid) / "packet-0001.md").read_text()
    footer = state.footer(H(env), sid, 1)
    section = seed.INHERITED_SECTION.format(seed_path=str(seed_file.resolve()), seed_text=EXPECTED_TWO.strip())
    assert text == "GOAL: do the thing" + section.rstrip("\n") + footer
    assert text.endswith(footer) and text.index("GOAL: do the thing") < text.index("INHERITED CONTEXT") < text.index(
        "(pi-lead — do not remove or reword this section)")
    assert "written by `pilead retire`" in text and "`relay" not in text

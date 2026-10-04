"""The commit gate reading a review file written by `pilead review` (a "measured review"): whose tree it is
of, whether it is valid, what it found and what it recommends. Over a session made as
test_verify_diff_board makes one, with review files written here (their `tree_end` taken from the test's
own `git write-tree`). No real pi, no model, no real tab."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_cli_core import home, pilead  # noqa: F401  (also brings the osascript stub fixture)
from test_verify_diff_board import SID, make_session, report, stage, write_report

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import reviewer  # noqa: E402

NAME5 = "the diff was reviewed by pilead review, the review is of the staged tree, and the lead has read it"
OLD5 = "the lead has READ the staged diff"
LEAD_MARK = "  [lead's attestation — this tool cannot check it]"

ANSWER_COMMIT = """Verify: VERDICT: COUNTS-MATCH
TL;DR: Status: clean / Risk flags: none / UNVERIFIED: none / Changed: src/alpha.py, src/beta.py
Tests: not re-run
Existing lines edited: none
Findings:
1. src/alpha.py:1 — note — a placeholder line.
Recommendation: commit — nothing stands in the way.
"""


def answer(findings="1. src/alpha.py:1 — note — a placeholder line.", rec="commit — nothing stands in the way."):
    return ANSWER_COMMIT.replace("1. src/alpha.py:1 — note — a placeholder line.", findings).replace(
        "commit — nothing stands in the way.", rec)


def write_tree(wt):
    return subprocess.run(["git", "-C", str(wt), "write-tree"], capture_output=True, text=True,
                          check=True).stdout.strip()


HEADER = ("# Review of {sid} packet {packet:04d} — pilead review\n"
          "Review: {outcome}\n"
          "Reviewer: headless pi, anthropic/claude-sonnet-5, with the lead's context; tools read, grep, find, ls\n"
          "Hash: {hash}; refs unchanged; diff sha 0123456789ab\n"
          "Measured: verify COUNTS-MATCH, exit 0; re-run not asked for\n"
          "Cost: 12k/340 tokens over 3 requests, cost $0.0100\n"
          "---\n")


def review_text(tree, sid=SID, packet=1, outcome="VALID", body=ANSWER_COMMIT, end=True):
    h = f"start {tree} / end {tree} — match" if end else f"start {tree} / end not measured — not compared"
    return HEADER.format(sid=sid, packet=packet, outcome=outcome, hash=h) + body


@pytest.fixture
def s(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    return {"wt": wt, "sdir": sdir, "tree": write_tree(wt), "file": tmp_path / "review.md"}


def put(s, text):
    s["file"].write_text(text)
    return str(s["file"])


def verify(*extra):
    return pilead("verify", SID, *extra, check=False)


def gate(f, *flags):
    return verify("--for-autocommit", "--in-plan", *flags, "--findings", f)


def events(tmp_path, name):
    p = home(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []
    return [r for r in recs if r["event"] == name]


def assert_ledger_shapes(tmp_path):
    for r in events(tmp_path, "report_reviewed"):
        assert set(r) - {"ts", "event"} == {"session_id", "packet", "findings", "caller"}, r
    for r in events(tmp_path, "auto_commit"):
        assert set(r) - {"ts", "event"} == {"session_id", "packet", "cleared", "reason"}, r


def cond5(stdout):
    """(mark line, detail line) of condition 5 in the clearance block."""
    lines = stdout.splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.startswith(("    ✓ 5. ", "    ✗ 5. ")))
    return lines[i], lines[i + 1]


# ── read_review ──────────────────────────────────────────────────────────────────────────────
def test_read_review_full(s):
    f = put(s, review_text(s["tree"]))
    assert reviewer.read_review(f) == {
        "sid": SID, "packet": 1, "outcome": "VALID", "valid": True, "tree_start": s["tree"], "tree_end": s["tree"],
        "findings": [{"n": 1, "where": "src/alpha.py:1", "level": "note", "text": "a placeholder line."}],
        "recommendation": "commit", "readable": True}


def test_read_review_not_measured_and_invalid(s):
    rv = reviewer.read_review(put(s, review_text(s["tree"], outcome="INVALID (the repository could not be "
                                                 "measured afterwards)", end=False)))
    assert rv["readable"] is True and rv["valid"] is False and rv["tree_end"] is None
    assert rv["outcome"] == "INVALID (the repository could not be measured afterwards)"


def test_read_review_hand_written_and_missing(s, tmp_path):
    assert reviewer.read_review(put(s, "1. a.py:1 — note — fine\n")) is None
    assert reviewer.read_review(tmp_path / "nope.md") is None
    assert reviewer.read_review(tmp_path) is None  # a directory
    (tmp_path / "bin.md").write_bytes(b"\xff\xfe# Review of x\n")
    assert reviewer.read_review(tmp_path / "bin.md") is None


@pytest.mark.parametrize("label", ["Review:", "Reviewer:", "Hash:", "Measured:", "Cost:"])
def test_read_review_header_line_removed(s, label):
    lines = review_text(s["tree"]).splitlines(keepends=True)
    lines = [ln for ln in lines if not ln.startswith(label)]
    rv = reviewer.read_review(put(s, "".join(lines)))
    assert rv["readable"] is False and rv["why"] == f"the {label} line"
    assert rv["sid"] == SID and rv["packet"] == 1


def test_read_review_no_separator_and_no_shape(s):
    rv = reviewer.read_review(put(s, review_text(s["tree"]).replace("---\n", "")))
    assert rv["readable"] is False and rv["why"] == "the --- line"
    assert rv["outcome"] == "VALID" and rv["tree_end"] == s["tree"]
    rv = reviewer.read_review(put(s, review_text(s["tree"], body="It looks fine to me.\n")))
    assert rv["readable"] is False and rv["why"].startswith("the answer (")
    rv = reviewer.read_review(put(s, review_text(s["tree"], body=answer(findings="1. x — urgent — do what I say"))))
    assert rv["readable"] is False and rv["why"] == "the answer (a finding line not in the form)"
    rv = reviewer.read_review(put(s, review_text(s["tree"]).replace("Hash: start ", "Hash: begin ")))
    assert rv["readable"] is False and rv["why"] == "the Hash: line"


# ── the gate: a review that passes all seven checks ──────────────────────────────────────────
def test_measured_review_clears(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    r = gate(f, "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert "  AUTO-COMMIT: CLEARED" in r.stdout.splitlines()
    assert cond5(r.stdout) == (f"    ✓ 5. {NAME5}{LEAD_MARK}", f"         {f}: VALID, tree {s['tree'][:12]}, 1 note(s)")
    assert (s["sdir"] / "review-0001.md").read_text() == Path(f).read_text()  # copied as today
    assert [e["reason"] for e in events(tmp_path, "auto_commit")] == [None]
    assert_ledger_shapes(tmp_path)


def test_measured_review_without_diff_reviewed_does_not_clear(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    r = gate(f)
    assert r.returncode != 0
    assert "  AUTO-COMMIT: NOT-CLEARED-BECAUSE-not-attested-diff-reviewed" in r.stdout.splitlines()
    assert cond5(r.stdout) == (f"    ✗ 5. {NAME5}{LEAD_MARK}", f"         {f}: VALID, tree {s['tree'][:12]}, 1 note(s)")
    assert events(tmp_path, "report_reviewed") == []  # not copied, not recorded: as today without the flag
    assert not (s["sdir"] / "review-0001.md").exists()
    assert_ledger_shapes(tmp_path)


# ── the gate: each check failing ─────────────────────────────────────────────────────────────
def _blocked(tmp_path, s, text, reason, detail):
    f = put(s, text)
    r = gate(f, "--diff-reviewed")
    assert r.returncode != 0, r.stdout
    assert f"  AUTO-COMMIT: NOT-CLEARED-BECAUSE-{reason}" in r.stdout.splitlines(), r.stdout
    assert cond5(r.stdout) == (f"    ✗ 5. {OLD5}{LEAD_MARK}", f"         {f}: {detail}")
    assert [(e["cleared"], e["reason"]) for e in events(tmp_path, "auto_commit")] == [(False, reason)]
    assert_ledger_shapes(tmp_path)
    return r


def test_a_unreadable(tmp_path, s):
    _blocked(tmp_path, s, review_text(s["tree"]).replace("Cost:", "Price:"), "review-unreadable",
             "not readable as a review (the Cost: line)")


def test_b_another_packet(tmp_path, s):
    _blocked(tmp_path, s, review_text(s["tree"], packet=2), "review-is-of-another-packet",
             f"a review of {SID} packet 0002, not of {SID} packet 0001")


def test_b_another_session(tmp_path, s):
    _blocked(tmp_path, s, review_text(s["tree"], sid="other-120000"), "review-is-of-another-packet",
             f"a review of other-120000 packet 0001, not of {SID} packet 0001")


def test_c_not_valid(tmp_path, s):
    _blocked(tmp_path, s, review_text(s["tree"], outcome="INVALID (tree changed during the review)"),
             "review-not-valid", "the review is INVALID (tree changed during the review), not VALID")


def test_d_tree_not_compared(tmp_path, s, monkeypatch, capsys):
    """In-process, with the staged tree unreadable and every other condition holding: the headline is d."""
    from pilead import cli
    f = put(s, review_text(s["tree"]))
    monkeypatch.setattr(reviewer, "staged_tree", lambda wt: None)
    rc = cli.main(["verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed", "--findings", f])
    out = capsys.readouterr().out
    assert rc != 0
    assert "  AUTO-COMMIT: NOT-CLEARED-BECAUSE-review-tree-not-compared" in out.splitlines()
    assert cond5(out) == (f"    ✗ 5. {OLD5}{LEAD_MARK}",
                          f"         {f}: the staged tree could not be read — the review was not compared")
    assert_ledger_shapes(tmp_path)


def test_d_repository_removed(tmp_path, s):
    """The repository gone: condition 5 names check d; the headline keeps the earlier refs condition."""
    f = put(s, review_text(s["tree"]))
    shutil.rmtree(s["wt"])
    r = gate(f, "--diff-reviewed")
    assert r.returncode != 0
    assert "  AUTO-COMMIT: NOT-CLEARED-BECAUSE-refs-not-compared" in r.stdout.splitlines()
    assert cond5(r.stdout) == (f"    ✗ 5. {OLD5}{LEAD_MARK}",
                               f"         {f}: the staged tree could not be read — the review was not compared")
    assert_ledger_shapes(tmp_path)


def test_d_decided_by_the_real_git(tmp_path, s, monkeypatch):
    """A `git` first on PATH that fails `--version` and `write-tree` (and passes the rest to the real git):
    the tree is still read with the real git, so the review is compared and the gate clears."""
    real = shutil.which("git")
    fake = tmp_path / "fakegit"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\n"
                              "for a in \"$@\"; do case \"$a\" in --version|write-tree) exit 1;; esac; done\n"
                              f"exec {real} \"$@\"\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ["PATH"])
    assert subprocess.run(["git", "-C", str(s["wt"]), "write-tree"], capture_output=True).returncode == 1
    f = put(s, review_text(s["tree"]))
    r = gate(f, "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert "  AUTO-COMMIT: CLEARED" in r.stdout.splitlines()
    assert cond5(r.stdout)[1] == f"         {f}: VALID, tree {s['tree'][:12]}, 1 note(s)"


def test_e_another_tree(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    stage(s["wt"], "src/gamma.py")
    now = {"tree": write_tree(s["wt"])}
    r = gate(f, "--diff-reviewed")
    assert r.returncode != 0
    # the report does not name src/gamma.py: condition 1 fails first and keeps the headline
    assert cond5(r.stdout) == (f"    ✗ 5. {OLD5}{LEAD_MARK}",
                               f"         {f}: the review is of tree {s['tree'][:12]}, the staged tree is "
                               f"{now['tree'][:12]}")


def test_e_another_tree_headline(tmp_path, s):
    """One more file staged after the review, and named by the report: only check e fails."""
    write_report(s["sdir"], report(changed="src/alpha.py, src/beta.py, src/gamma.py"))
    stage(s["wt"], "src/gamma.py")
    _blocked(tmp_path, s, review_text(s["tree"]), "review-is-of-another-tree",
             f"the review is of tree {s['tree'][:12]}, the staged tree is {write_tree(s['wt'])[:12]}")


def test_e_end_not_measured(tmp_path, s):
    _blocked(tmp_path, s, review_text(s["tree"], end=False), "review-is-of-another-tree",
             f"the review is of tree not measured, the staged tree is {s['tree'][:12]}")


@pytest.mark.parametrize("findings,detail", [
    ("1. src/alpha.py:1 — blocker — broken.", "1 blocker(s), 0 should-fix"),
    ("1. src/alpha.py:1 — should-fix — thin.\n2. src/beta.py:1 — note — ok.", "0 blocker(s), 1 should-fix"),
])
def test_f_open_findings(tmp_path, s, findings, detail):
    _blocked(tmp_path, s, review_text(s["tree"], body=answer(findings=findings)), "review-has-open-findings", detail)


@pytest.mark.parametrize("rec,word", [("fix-list — one item.", "fix-list"), ("send back — redo it.", "send back")])
def test_g_does_not_recommend_commit(tmp_path, s, rec, word):
    _blocked(tmp_path, s, review_text(s["tree"], body=answer(findings="No findings.", rec=rec)),
             "review-does-not-recommend-commit", f"the review recommends {word}")


def test_no_findings_counts_zero_notes(tmp_path, s):
    f = put(s, review_text(s["tree"], body=answer(findings="No findings.")))
    r = gate(f, "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert cond5(r.stdout)[1] == f"         {f}: VALID, tree {s['tree'][:12]}, 0 note(s)"


def test_earlier_condition_keeps_its_reason(tmp_path, s):
    write_report(s["sdir"], report(status="clean-with-caveats"))
    f = put(s, review_text(s["tree"], outcome="INCOMPLETE (timed out after 1800s)"))
    r = gate(f, "--diff-reviewed")
    assert r.returncode != 0
    assert "  AUTO-COMMIT: NOT-CLEARED-BECAUSE-status-not-clean" in r.stdout.splitlines()
    assert cond5(r.stdout) == (f"    ✗ 5. {OLD5}{LEAD_MARK}",
                               f"         {f}: the review is INCOMPLETE (timed out after 1800s), not VALID")
    assert [(e["cleared"], e["reason"]) for e in events(tmp_path, "auto_commit")] == [(False, "status-not-clean")]
    assert_ledger_shapes(tmp_path)


# ── hand-written findings: exactly as on the commit before this packet ───────────────────────
# Captured from `pilead verify` on c49b3f4 (the commit before this packet), the session id as {SID}.
HAND_PLAIN_STDOUT = """refs: unchanged since packet 0001
══════════════════════════════════════════════════════════════════════════
  pilead verify · {SID} · packet 001
══════════════════════════════════════════════════════════════════════════
  VERDICT: COUNTS-MATCH

  ⚠  COUNTS-MATCH means the numbers line up. It must NEVER be read as "the report is true".
  ⚠  This tool re-checks declared files and counts against staged reality. Premise-level
  ⚠  wrongness — wrong oracle, wrong write-side, suite green in the wrong venv — is INVISIBLE
  ⚠  to it: across ~15 field reports a counts-verifier would have caught exactly one thing.
  ⚠  The lead's judgement on the staged diff stays the real check. This never replaces it.

  TL;DR block: well-formed (Status / Risk flags / UNVERIFIED / Changed)
    Status: clean

  RISK FLAGS: none (the report's own claim — echoed, not confirmed)
  UNVERIFIED: none (the report's own claim — echoed, not confirmed)

  STAGED REALITY
    claim scope: the `Changed:` TL;DR line and/or "What changed" section
    claimed and staged:   2
    claimed, NOT staged:  0
    staged, not claimed:  0 (advisory — reports summarise)
    index: 2 file(s) staged and uncommitted

  DECLARED TESTS
    NOT RE-RUN (default — verify stays fast). Pass --rerun to actually run these.
    counts the report declares: (none declared)
    commands declared: (none this tool would re-run — pytest, or exactly npm test / npm run check / node --test)

══════════════════════════════════════════════════════════════════════════
  COUNTS-MATCH is the ceiling of what this tool can say. Read the staged diff.
══════════════════════════════════════════════════════════════════════════
"""
HAND_STDOUT = HAND_PLAIN_STDOUT + """──────────────────────────────────────────────────────────────────────────
  AUTO-COMMIT CLEARANCE (#16 phase 2) — all five conditions must hold
    ✓ 1. verify verdict is COUNTS-MATCH
         verdict is COUNTS-MATCH
    ✓ 2. TL;DR is clean / none / none
         Status: clean / Risk flags: none / UNVERIFIED: none
    ✓ 3. the packet was in the approved plan  [lead's attestation — this tool cannot check it]
         attested by the lead (--in-plan)
    ✓ 4. nothing sign-off-gated is touched
         no sign-off-gated path in the staged set
    ✓ 5. the lead has READ the staged diff  [lead's attestation — this tool cannot check it]
         attested by the lead (--diff-reviewed)

  AUTO-COMMIT: CLEARED
  This clears the AUTOMATION only. It is not a statement that the work is correct —
  see the caveat above. The lead owns the commit and everything in it.
──────────────────────────────────────────────────────────────────────────
"""
HAND_LEDGER = [
    {"event": "refs_snapshot", "session_id": SID, "packet": 1, "head": "{HEAD}", "branch": "main", "refs": 1},
    {"event": "report_verify", "session_id": SID, "packet": 1, "verdict": "COUNTS-MATCH", "rerun": False,
     "mismatches": 0, "caller": "unknown"},
    {"event": "report_reviewed", "session_id": SID, "packet": 1, "findings": "{SDIR}/review-0001.md", "caller": "unknown"},
    {"event": "auto_commit", "session_id": SID, "packet": 1, "cleared": True, "reason": None},
]


def _ledger_without_ts(tmp_path, sdir, wt):
    """The ledger with its `ts` removed and the test's own paths and HEAD written as {SDIR} / {HEAD}."""
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = []
    for ln in (home(tmp_path) / "ledger.jsonl").read_text().splitlines():
        r = json.loads(ln)
        r.pop("ts")
        for k, v in r.items():
            if isinstance(v, str):
                r[k] = v.replace(str(sdir), "{SDIR}").replace(head, "{HEAD}")
        out.append(r)
    return out


def _pin(s):
    """These two tests pin verify's output and ledger as they were at p26 (c49b3f4), before p18c's
    park-after-review (relay 0.5.2 row 95) existed; a pinned session is never parked, so pinning it keeps
    the comparison about the gate alone (tests/test_park_after_review.py covers the park)."""
    m = json.loads((s["sdir"] / "meta.json").read_text())
    m["keep"] = True
    (s["sdir"] / "meta.json").write_text(json.dumps(m))


def test_hand_written_findings_unchanged(tmp_path, s):
    _pin(s)
    f = put(s, "1. src/alpha.py:1 — note — fine\nRecommendation: commit\n")
    r = gate(f, "--diff-reviewed")
    assert r.returncode == 0
    assert r.stdout == HAND_STDOUT.replace("{SID}", SID)
    assert _ledger_without_ts(tmp_path, s["sdir"], s["wt"]) == HAND_LEDGER
    assert (s["sdir"] / "review-0001.md").read_text() == Path(f).read_text()


def test_hand_written_findings_unchanged_without_autocommit(tmp_path, s):
    _pin(s)
    f = put(s, "1. src/alpha.py:1 — note — fine\n")
    r = verify("--diff-reviewed", "--findings", f)
    assert r.returncode == 0
    assert r.stdout == HAND_PLAIN_STDOUT.replace("{SID}", SID)
    assert _ledger_without_ts(tmp_path, s["sdir"], s["wt"]) == HAND_LEDGER[:3]


# ── the review file itself given to --findings ───────────────────────────────────────────────
def test_review_file_itself_is_not_copied_onto_itself(tmp_path, s):
    rev = s["sdir"] / "review-0001.md"
    rev.write_text(review_text(s["tree"]))
    os.utime(rev, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = (rev.read_bytes(), rev.stat().st_mtime_ns)
    r = gate(str(rev), "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert (rev.read_bytes(), rev.stat().st_mtime_ns) == before
    assert [e["findings"] for e in events(tmp_path, "report_reviewed")] == [str(rev)]
    assert_ledger_shapes(tmp_path)


# ── without --for-autocommit: the one `review:` line ─────────────────────────────────────────
def test_review_line_matching_tree(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    r = verify("--findings", f)
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[1] == f"review: VALID, of tree {s['tree'][:12]} — the staged tree"
    assert lines[2].startswith("═") and sum(ln.startswith("review:") for ln in lines) == 1
    assert "AUTO-COMMIT" not in r.stdout
    assert_ledger_shapes(tmp_path)


def test_review_line_another_tree(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    stage(s["wt"], "src/gamma.py")
    r = verify("--diff-reviewed", "--findings", f)
    assert f"review: VALID, of tree {s['tree'][:12]} — the staged tree is now {write_tree(s['wt'])[:12]}" \
        in r.stdout.splitlines()
    assert sum(ln.startswith("review:") for ln in r.stdout.splitlines()) == 1
    assert_ledger_shapes(tmp_path)


def test_review_line_repository_unreadable(tmp_path, s):
    f = put(s, review_text(s["tree"]))
    shutil.rmtree(s["wt"])
    r = verify("--findings", f)
    assert f"review: VALID, of tree {s['tree'][:12]} — the staged tree could not be read" in r.stdout.splitlines()
    assert sum(ln.startswith("review:") for ln in r.stdout.splitlines()) == 1
    assert_ledger_shapes(tmp_path)

"""lib/pilead/handoff.py — the markers a memo dropped, and the memo copy with its SUCCESSOR AFTERCARE section.
In-process; nothing is read or written."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import handoff  # noqa: E402

SID = "0f1e2d3c-4b5a-4968-8776-655443322110"
MEMO = "# Handoff\n\nIn flight: s-1 on packet 3.\n[ops-not-lead-work] still holds.\n"


def section(posture_line, pins_line=None, moved=2, project="my proj"):
    lines = [
        "---",
        "(pi-lead — SUCCESSOR AFTERCARE; do not remove)",
        f"You were registered by pilead handoff under the session id {SID}, before your first turn.",
        f"1. CHECK YOUR REGISTRATION: run pilead lead-start \"$PI_SESSION_ID\" --project '{project}'. If your session",
        f"   id is not the one above, also run: pilead takeover {SID}",
        "2. THE OUTGOING LEAD'S TAB is recorded in your lead record. Close it only after the human has said yes:",
        "   pilead close-predecessor. Never close it unasked.",
        f"3. EXECUTORS: {moved} session(s) of the outgoing lead are yours now; pilead list shows them.",
        "4. REVIEWS: review every report with the pilead-review skill before you commit, and read its findings.",
        f"5. POSTURE: the outgoing lead's posture was {posture_line}. Neither is handed on: you",
        "   start in the configured default, and only the human grants another.",
    ]
    if pins_line:
        lines.append(pins_line)
    return "\n".join(lines) + "\n"


# ── dropped_markers ───────────────────────────────────────────────────────────────────────────────
def test_a_dropped_marker_is_named_a_kept_one_is_not():
    inherited = "rules: [ops-not-lead-work] and [no-force-push]\n"
    assert handoff.dropped_markers(inherited, "only [ops-not-lead-work] here") == ["[no-force-push]"]
    assert handoff.dropped_markers(inherited, "[no-force-push] [ops-not-lead-work]") == []


def test_a_marker_that_appears_twice_is_named_once_in_first_seen_order():
    inherited = "[b-rule] then [a-rule] then [b-rule] again and [a-rule]"
    assert handoff.dropped_markers(inherited, "nothing") == ["[b-rule]", "[a-rule]"]


@pytest.mark.parametrize("inherited", ["", None])
def test_an_empty_inherited_text_gives_none(inherited):
    assert handoff.dropped_markers(inherited, "[x-rule]") == []


def test_text_in_brackets_that_is_not_a_marker_is_ignored():
    inherited = "[Exec] s-1, [a b], [Lead] p, [x1], [ok-rule]"
    assert handoff.dropped_markers(inherited, "") == ["[ok-rule]"]


# ── build_copy ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("posture,line", [
    ((True, "auto"), "autonomous on, tier auto"),
    ((False, "manual"), "autonomous off, tier manual"),
    ((False, "lead"), "autonomous off, tier lead"),
    ((None, "auto"), "autonomous unknown, tier auto"),
    ((False, None), "autonomous off, tier unknown"),
    ((None, None), "autonomous unknown, tier unknown"),
])
def test_the_copy_is_the_memo_and_the_exact_section_for_each_posture(posture, line):
    got = handoff.build_copy(MEMO, SID, "my proj", 2, [], posture)
    assert got == MEMO + section(line)


def test_with_pins_one_more_line():
    got = handoff.build_copy(MEMO, SID, "my proj", 3, ["s-a", "s-b"], (False, "auto"))
    pins = ("6. PINNED: 2 pinned session(s) are yours: s-a, s-b — nothing closes them by itself; pilead keep <sid> "
            "--off releases one.")
    assert got == MEMO + section("autonomous off, tier auto", pins, moved=3)


def test_a_memo_with_no_final_newline_gets_one_before_the_section():
    assert handoff.build_copy("just this", SID, "p", 0, [], (False, "auto")) == \
        "just this\n" + section("autonomous off, tier auto", moved=0, project="p")


def test_a_memo_that_already_carries_an_aftercare_section_ends_with_exactly_one():
    first = handoff.build_copy(MEMO, "1111aaaa-0000-4000-8000-000000000000", "old", 5, ["s-z"], (True, "manual"))
    again = handoff.build_copy(first, SID, "my proj", 2, [], (False, "auto"))
    assert again == MEMO + section("autonomous off, tier auto")
    assert again.count("SUCCESSOR AFTERCARE") == 1
    assert handoff.build_copy(again, SID, "my proj", 2, [], (False, "auto")) == again


def test_a_rule_line_that_is_not_the_aftercare_head_is_kept():
    memo = "notes\n---\n(not the aftercare)\nmore\n"
    assert handoff.build_copy(memo, SID, "my proj", 2, [], (False, "auto")).startswith(memo)

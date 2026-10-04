"""A measured review whose Hash line says the trees differ is never VALID, whatever its Review: line says —
a hand-edited file that claims VALID over a `differ` Hash line does not clear the commit gate. Fixtures
from tests/test_review_gate.py; no real pi, no model, no real tab."""
from test_review_gate import _blocked, put, reviewer, review_text, s  # noqa: F401  (s: the fixture)

OTHER = "0" * 40


def differ_text(s, outcome="VALID", end=None):
    """A review of the staged tree whose Hash line was edited to say its start and end trees differ."""
    end = end or s["tree"]
    return review_text(s["tree"], outcome=outcome).replace(
        f"start {s['tree']} / end {s['tree']} — match", f"start {OTHER} / end {end} — differ")


def test_read_review_valid_over_differ_is_not_valid(s):
    rv = reviewer.read_review(put(s, differ_text(s)))
    assert rv["readable"] is True and rv["outcome"] == "VALID" and rv["valid"] is False
    assert rv["tree_start"] == OTHER and rv["tree_end"] == s["tree"]


def test_read_review_match_is_still_valid(s):
    rv = reviewer.read_review(put(s, review_text(s["tree"])))
    assert rv["valid"] is True


def test_read_review_invalid_over_differ_stays_invalid(s):
    rv = reviewer.read_review(put(s, differ_text(s, outcome="INVALID (tree changed during the review)")))
    assert rv["valid"] is False and rv["outcome"] == "INVALID (tree changed during the review)"


def test_gate_refuses_a_hand_edited_valid_over_differ(tmp_path, s):
    # the end tree IS the staged tree, every finding a note, recommendation commit: only the Hash line stops it
    _blocked(tmp_path, s, differ_text(s), "review-not-valid",
             "the review says VALID, but its Hash line says the trees differ")


def test_gate_invalid_over_differ_keeps_its_own_detail(tmp_path, s):
    _blocked(tmp_path, s, differ_text(s, outcome="INVALID (tree changed during the review)"), "review-not-valid",
             "the review is INVALID (tree changed during the review), not VALID")


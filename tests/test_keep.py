"""`pilead keep <sid> [--off]`: the pin's fields and ledger event, who the caller is, and the `list`
footnote (no table cell changes)."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def pilead(*args, check=True, env=None):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stderr
    return r


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def lead(h, sid, project):
    (h / "leads").mkdir(parents=True, exist_ok=True)
    (h / "leads" / f"{sid}.json").write_text(json.dumps({"sid": sid, "project": project, "cwd": "/nowhere",
                                                          "iterm_handle": None}))


def session(h, sid, lead_sid="lead-1", status="idle"):
    s = h / "sessions" / sid
    s.mkdir(parents=True, exist_ok=True)
    (s / "meta.json").write_text(json.dumps({"sid": sid, "lead": lead_sid, "worktree": "/nowhere", "topic": "t",
                                             "model": "anthropic/claude-sonnet-5", "status": status,
                                             "created": "-", "packets": 1, "label": f"[Exec] {sid}"}))
    (s / "status").write_text(status + "\n")
    (s / "packet-0001.md").write_text("GOAL\n")
    return s


def meta(h, sid):
    return json.loads((h / "sessions" / sid / "meta.json").read_text())


def keep_events(h):
    p = h / "ledger.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if '"keep"' in ln] if p.is_file() else []


def test_pin_and_unpin_fields_and_ledger(tmp_path):
    h = H(tmp_path)
    lead(h, "lead-1", "proj")
    session(h, "s-1")
    r = pilead("keep", "s-1", env={"PI_LEAD_SID": "lead-1"})
    assert (r.stdout, r.stderr) == ("pinned s-1 — it will be left alone\n", "")
    m = meta(h, "s-1")
    assert m["keep"] is True and m["keep_by"] == "lead-1" and ISO.match(m["keep_at"])
    r = pilead("keep", "s-1", "--off", env={"PI_LEAD_SID": "lead-1"})
    assert (r.stdout, r.stderr) == ("unpinned s-1\n", "")
    m = meta(h, "s-1")
    assert (m["keep"], m["keep_by"], m["keep_at"]) == (False, None, None)
    evs = keep_events(h)
    assert [(e["event"], e["session_id"], e["keep"], e["keep_by"]) for e in evs] == [
        ("keep", "s-1", True, "lead-1"), ("keep", "s-1", False, None)]


def test_keep_by_is_the_caller_and_null_with_no_caller(tmp_path):
    h = H(tmp_path)
    lead(h, "lead-1", "proj")
    session(h, "s-1")
    cases = [({}, None),                                  # no caller in the environment
             ({"PI_LEAD_SID": "no-record"}, None),        # a usable id with no lead record
             ({"PI_LEAD_SID": "../../x"}, None),          # an id that is not usable
             ({"PI_SESSION_ID": "lead-1"}, "lead-1"),     # pi's own id, when it is a lead
             ({"PI_LEAD_SID": "lead-1"}, "lead-1")]
    for env, want in cases:
        pilead("keep", "s-1", env=env)
        assert meta(h, "s-1")["keep_by"] == want, env
        assert keep_events(h)[-1]["keep_by"] == want, env


def test_an_unknown_or_unusable_session_is_refused(tmp_path):
    h = H(tmp_path)
    session(h, "s-1")
    for sid in ("nope", "../../x"):
        r = pilead("keep", sid, check=False)
        assert (r.returncode, r.stdout) == (2, "") and len(r.stderr.splitlines()) == 1
    assert "keep" not in meta(h, "s-1") and keep_events(h) == []


def _footnotes(out):
    return [ln for ln in out.splitlines() if ln.startswith("  pinned: ")]


def test_the_list_footnote_and_no_table_cell_changes(tmp_path):
    h = H(tmp_path)
    lead(h, "lead-1", "proj")
    session(h, "s-1")
    session(h, "s-2")
    unpinned = pilead("list").stdout
    assert _footnotes(unpinned) == []
    pilead("keep", "s-1", env={"PI_LEAD_SID": "lead-1"})
    pinned = pilead("list").stdout
    (note,) = _footnotes(pinned)
    assert re.fullmatch(r"  pinned: s-1 by proj \d+s ago — pilead keep s-1 --off releases it", note), note
    assert [ln for ln in pinned.splitlines() if ln != note] == unpinned.splitlines()
    pilead("keep", "s-1", "--off")
    assert pilead("list").stdout == unpinned


def test_the_footnote_names_the_pinning_lead_else_its_sid_else_a_dash(tmp_path):
    h = H(tmp_path)
    lead(h, "lead-1", "proj")
    session(h, "s-1")
    m = meta(h, "s-1")
    for by, who in (("lead-gone", "lead-gone"), (None, "-")):
        m.update({"keep": True, "keep_by": by, "keep_at": None})
        (h / "sessions" / "s-1" / "meta.json").write_text(json.dumps(m))
        (note,) = _footnotes(pilead("list").stdout)
        assert note == f"  pinned: s-1 by {who} - ago — pilead keep s-1 --off releases it"  # age unknown: `-`


def test_a_pinned_session_that_is_not_listed_has_no_footnote(tmp_path):
    h = H(tmp_path)
    lead(h, "lead-1", "proj")
    session(h, "s-closed", status="closed")
    pilead("keep", "s-closed")
    assert _footnotes(pilead("list").stdout) == []  # closed sessions are hidden without --closed
    assert len(_footnotes(pilead("list", "--closed").stdout)) == 1

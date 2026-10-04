"""`pilead adopt` (lib/pilead/verbs/adopt.py), driven through bin/pilead with the calling lead in the test's own
environment ($PI_LEAD_SID). Whether a lead's tab exists is answered by the terminal seam only: a recording
`osascript` stub answering $FAKE_TAB and a `ps` stub saying iTerm runs (as tests/test_resume.py does)."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
ME = "lead-me"
HANDLE = "w0t0p0:11111111-2222-3333-4444-555555555555"

OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*) printf 'OK\\nFAKE-UUID\\nfront\\n' ;;
  *) echo "${FAKE_TAB:-false}" ;;
esac
"""
PS = """#!/bin/sh
if [ "$1" = "-axo" ]; then
  [ -n "$FAKE_ITERM_RUNNING" ] && echo /Applications/iTerm.app/Contents/MacOS/iTerm2
  exit 0
fi
exec /bin/ps "$@"
"""


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    for name, text in (("osascript", OSA), ("ps", PS)):
        p = tmp_path / "fakebin" / name
        p.write_text(text)
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_OSA_LOG", str(tmp_path / "osa.log"))
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "true")


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def pilead(*args, env=None, caller=ME):
    e = {**os.environ, **(env or {})}
    if caller is not None:
        e["PI_LEAD_SID"] = caller
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)


def iso(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def lead(tmp_path, sid, live="ghost", project=None):
    """A lead record whose LIVE is `live`: `live` has a tab handle (the seam says it exists), `unreachable` started a
    minute ago, `ghost` a day ago, `broken` is not JSON."""
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.json"
    if live == "broken":
        p.write_text("{not json")
        return p
    rec = {"sid": sid, "project": project or f"proj-{sid}", "inbox": str(d / f"{sid}.inbox.md"),
           "started": iso(60 if live == "unreachable" else 86400), "terminal": "iTerm.app",
           "iterm_handle": HANDLE if live == "live" else None}
    p.write_text(json.dumps(rec))
    (d / f"{sid}.inbox.md").write_text("")
    return p


def session(tmp_path, sid, lead_sid, status="busy"):
    d = H(tmp_path) / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"sid": sid, "worktree": str(tmp_path), "topic": "t", "model": "m", "status": status, "created": "-",
            "packets": 1, "label": f"[Exec] {sid}", "layout": "tab"}
    if lead_sid is not ...:
        meta["lead"] = lead_sid
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "status").write_text(status + "\n")
    (d / "packet-0001.md").write_text("GOAL\n")
    return d


def lead_of(tmp_path, sid):
    return json.loads((H(tmp_path) / "sessions" / sid / "meta.json").read_text()).get("lead")


def tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        out[str(p.relative_to(root))] = "dir" if p.is_dir() else p.read_bytes()
    return out


def adopted_events(tmp_path):
    p = H(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if r["event"] == "adopted"]


@pytest.fixture
def me(tmp_path):
    lead(tmp_path, ME, "live")
    return ME


# ── the refusals of the command itself: exit 2, one stderr line, nothing changed ─────────────────────
@pytest.mark.parametrize("args,env,caller,words", [
    (["s-1"], {}, None, "pilead lead-start"),
    (["s-1"], {}, "lead-unregistered", "pilead lead-start"),
    (["s-1"], {}, "../bad", "pilead lead-start"),
    (["s-1"], {"PI_LEAD_ROLE": "executor"}, ME, "executor"),
    (["s-1", "--from", "lead-old"], {}, ME, "not both"),
    ([], {}, ME, "not both"),
    (["../x"], {}, ME, "is not usable"),
    (["--from", "../x"], {}, ME, "is not usable"),
    (["--from", "x.usage"], {}, ME, "is not usable"),
    (["--from", ME], {}, ME, "is you"),
])
def test_refusals_exit_2_and_change_nothing(tmp_path, me, args, env, caller, words):
    session(tmp_path, "s-1", "lead-old")
    before = tree(H(tmp_path))
    r = pilead("adopt", *args, env=env, caller=caller)
    assert r.returncode == 2 and r.stdout == "", r
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith("pilead: ") and words in r.stderr, r.stderr
    assert tree(H(tmp_path)) == before


# ── adopted ───────────────────────────────────────────────────────────────────────────────────────
def test_an_orphan_an_unowned_session_and_a_ghost_leads_session_are_adopted(tmp_path, me):
    session(tmp_path, "s-orphan", "lead-gone")
    session(tmp_path, "s-unowned", ...)
    session(tmp_path, "s-empty", "")
    lead(tmp_path, "lead-ghost", "ghost")
    session(tmp_path, "s-ghost", "lead-ghost")
    r = pilead("adopt", "s-orphan", "s-unowned", "s-empty", "s-ghost")
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == ("adopted s-orphan from lead-gone (no longer a registered lead)\n"
                        "adopted s-unowned from - (unowned)\n"
                        "adopted s-empty from - (unowned)\n"
                        "adopted s-ghost from lead-ghost (ghost lead)\n")
    for sid in ("s-orphan", "s-unowned", "s-empty", "s-ghost"):
        assert lead_of(tmp_path, sid) == ME
    assert [(e["session_id"], e["from_lead"], e["to_lead"], e["forced"], e["reason"]) for e in adopted_events(tmp_path)] == [
        ("s-orphan", "lead-gone", ME, False, "adopt"), ("s-unowned", None, ME, False, "adopt"),
        ("s-empty", "", ME, False, "adopt"), ("s-ghost", "lead-ghost", ME, False, "adopt")]


def test_already_yours_writes_nothing(tmp_path, me):
    session(tmp_path, "s-1", ME)
    before = tree(H(tmp_path))
    r = pilead("adopt", "s-1")
    assert (r.returncode, r.stdout, r.stderr) == (0, "s-1 is already yours\n", "")
    assert tree(H(tmp_path)) == before


def test_a_session_of_a_live_lead_is_refused_byte_identical_and_force_adopts_it(tmp_path, me):
    lead(tmp_path, "lead-live", "live", project="their proj")
    d = session(tmp_path, "s-1", "lead-live")
    (d / "report-0001.md").write_text("Done.\n")
    (H(tmp_path) / "leads" / "lead-live.inbox.md").write_text("reported s-1 /x/report-0001.md\n")
    before = tree(H(tmp_path))
    r = pilead("adopt", "s-1")
    assert r.returncode == 1 and r.stderr == ""
    assert r.stdout == ("refused s-1: owned by lead-live (their proj), LIVE live — its reports wake that lead; "
                        "pilead adopt s-1 --force takes it\n")
    assert tree(H(tmp_path)) == before, "the session's directory and both inboxes are byte-identical"
    r = pilead("adopt", "s-1", "--force")
    assert (r.returncode, r.stdout) == (0, "adopted s-1 from lead-live (forced)\n")
    assert lead_of(tmp_path, "s-1") == ME
    assert (H(tmp_path) / "leads" / "lead-live.inbox.md").read_text() == "reported s-1 /x/report-0001.md\n"
    assert adopted_events(tmp_path)[-1]["forced"] is True


@pytest.mark.parametrize("live", ["unreachable", "live"])
def test_unreachable_and_live_are_refused(tmp_path, me, live):
    lead(tmp_path, "lead-x", live)
    session(tmp_path, "s-1", "lead-x")
    r = pilead("adopt", "s-1")
    assert r.returncode == 1 and f"LIVE {live} —" in r.stdout and lead_of(tmp_path, "s-1") == "lead-x"


def test_one_refused_and_one_adopted_in_the_same_command_exits_1_and_adopts_the_one(tmp_path, me):
    lead(tmp_path, "lead-live", "live")
    session(tmp_path, "s-a", "lead-live")
    session(tmp_path, "s-b", "lead-gone")
    r = pilead("adopt", "s-a", "s-b", "no-such-session")
    assert r.returncode == 1
    lines = r.stdout.splitlines()
    assert lines[0].startswith("refused s-a: owned by lead-live")
    assert lines[1:] == ["adopted s-b from lead-gone (no longer a registered lead)", "unknown session no-such-session"]
    assert (lead_of(tmp_path, "s-a"), lead_of(tmp_path, "s-b")) == ("lead-live", ME)


def test_from_takes_every_session_naming_the_lead_terminal_ones_too(tmp_path, me):
    session(tmp_path, "s-b", "lead-gone", status="closed")
    session(tmp_path, "s-a", "lead-gone")
    session(tmp_path, "s-c", "lead-gone", status="dead")
    session(tmp_path, "s-other", "lead-else")
    r = pilead("adopt", "--from", "lead-gone")
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout.splitlines() == [f"adopted s-{x} from lead-gone (no longer a registered lead)" for x in "abc"]
    assert lead_of(tmp_path, "s-other") == "lead-else"


def test_from_with_no_session_prints_one_line(tmp_path, me):
    session(tmp_path, "s-1", "lead-else")
    before = tree(H(tmp_path))
    r = pilead("adopt", "--from", "lead-nobody")
    assert (r.returncode, r.stdout, r.stderr) == (0, "no session names lead-nobody as its lead\n", "")
    assert tree(H(tmp_path)) == before


@pytest.mark.parametrize("setup,what", [
    ("broken record", "lead record"),
    ("bad note", "forward note"),
    ("unusable id", "is not a usable lead id"),
])
def test_an_unknown_owner_is_refused_and_says_what_could_not_be_read(tmp_path, me, setup, what):
    if setup == "broken record":
        lead(tmp_path, "lead-x", "broken")
        session(tmp_path, "s-1", "lead-x")
    elif setup == "bad note":
        (H(tmp_path) / "handoffs").mkdir(parents=True)
        (H(tmp_path) / "handoffs" / "lead-x.json").write_text("not json")
        session(tmp_path, "s-1", "lead-x")
    else:
        session(tmp_path, "s-1", "../../x")
    before = tree(H(tmp_path))
    r = pilead("adopt", "s-1")
    assert r.returncode == 1 and r.stdout.startswith("refused s-1: owner unknown (") and what in r.stdout, r.stdout
    assert r.stdout.rstrip().endswith("pilead adopt s-1 --force takes it")
    assert tree(H(tmp_path)) == before
    r = pilead("adopt", "s-1", "--force")
    assert r.returncode == 0 and r.stdout.endswith(" (forced)\n") and lead_of(tmp_path, "s-1") == ME


def test_a_forward_note_to_a_live_lead_is_that_leads(tmp_path, me):
    lead(tmp_path, "lead-new", "live")
    (H(tmp_path) / "handoffs").mkdir(parents=True)
    (H(tmp_path) / "handoffs" / "lead-old.json").write_text(json.dumps({"from": "lead-old", "to": "lead-new"}))
    session(tmp_path, "s-1", "lead-old")
    r = pilead("adopt", "s-1")
    assert r.returncode == 1 and r.stdout.startswith("refused s-1: owned by lead-new (proj-lead-new), LIVE live")
    (H(tmp_path) / "handoffs" / f"lead-x.json").write_text(json.dumps({"to": ME}))
    session(tmp_path, "s-2", "lead-x")
    assert pilead("adopt", "s-2").stdout == "s-2 is already yours\n"


def test_an_adopted_report_wakes_the_new_lead_once(tmp_path, me):
    d = session(tmp_path, "s-1", "lead-gone")
    (d / "report-0001.md").write_text("Done.\n")
    (H(tmp_path) / "leads" / "lead-gone.inbox.md").write_text("reported other /o.md\n")
    assert pilead("adopt", "s-1").returncode == 0
    inbox = (H(tmp_path) / "leads" / f"{ME}.inbox.md").read_text()
    assert inbox == f"reported s-1 {os.path.abspath(d / 'report-0001.md')}\n"
    assert (d / ".notified").read_text().split(" ns:")[0] == os.path.abspath(d / "report-0001.md")
    assert pilead("adopt", "s-1").stdout == "s-1 is already yours\n"
    assert (H(tmp_path) / "leads" / f"{ME}.inbox.md").read_text() == inbox


def _announced_and_marked(tmp_path, owner):
    """Session s-1 owned by `owner`, its report written and `.notified` naming it (as when the old lead's extension
    announced it); returns (session dir, the report's line)."""
    d = session(tmp_path, "s-1", owner)
    (d / "report-0001.md").write_text("Done.\n")
    path = os.path.abspath(d / "report-0001.md")
    (d / ".notified").write_text(f"{path} ns:1\n")
    return d, f"reported s-1 {path}\n"


def test_a_report_still_pending_in_a_ghost_leads_inbox_wakes_the_adopter_once(tmp_path, me):
    lead(tmp_path, "lead-ghost", "ghost")
    d, line = _announced_and_marked(tmp_path, "lead-ghost")
    ghost = H(tmp_path) / "leads" / "lead-ghost.inbox.md"
    ghost.write_text("reported other /o.md\n" + line)
    before = ghost.read_bytes()
    assert pilead("adopt", "s-1").returncode == 0
    assert (H(tmp_path) / "leads" / f"{ME}.inbox.md").read_text() == line
    assert ghost.read_bytes() == before
    assert pilead("adopt", "s-1").stdout == "s-1 is already yours\n"
    assert (H(tmp_path) / "leads" / f"{ME}.inbox.md").read_text() == line


def test_a_report_still_pending_in_an_orphans_claim_file_wakes_the_adopter_once(tmp_path, me):
    d, line = _announced_and_marked(tmp_path, "lead-gone")
    leads = H(tmp_path) / "leads"
    inbox = leads / "lead-gone.inbox.md"
    inbox.write_text("reported other /o.md\n")
    claim = leads / "lead-gone.inbox.md.claim-123-456-0"
    claim.write_text(line)
    before = (inbox.read_bytes(), claim.read_bytes())
    assert pilead("adopt", "s-1").returncode == 0
    assert (leads / f"{ME}.inbox.md").read_text() == line
    assert (inbox.read_bytes(), claim.read_bytes()) == before


@pytest.mark.parametrize("owner", ["lead-ghost", "lead-gone"])
def test_a_report_the_old_lead_already_surfaced_is_not_sent_again(tmp_path, me, owner):
    if owner == "lead-ghost":
        lead(tmp_path, owner, "ghost")
    d, _ = _announced_and_marked(tmp_path, owner)
    leads = H(tmp_path) / "leads"
    (leads / f"{owner}.inbox.md").write_text("reported other /o.md\n")
    (leads / f"{owner}.inbox.md.claim-123-456-0").write_text("reported other /p.md\n")
    assert pilead("adopt", "s-1").returncode == 0
    assert (leads / f"{ME}.inbox.md").read_text() == ""


def test_adopt_is_listed_after_restart_in_help():
    r = subprocess.run([str(PILEAD), "--help"], capture_output=True, text=True)
    verbs = [ln.split()[0] for ln in r.stdout.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert verbs[verbs.index("restart") + 1] == "adopt"

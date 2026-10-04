"""A REVIEWED packet parks its executor (claude-relay 0.5.2's `_park_after_review`, row 95): `pilead verify
<sid> --diff-reviewed` closes the session right after `report_reviewed`, through `pilead close`'s own
function, unless something says it must stay. Over a real temp git repo and hand-built session state
(tests/test_verify_diff_board.py's fixtures), through bin/pilead with the conftest's recording `osascript`
stub, and in-process (`cli.main`) where `close_executor` is patched. No real tab is closed and the real pi
never runs."""
import json
import subprocess
import sys

import pytest

from test_close_variants import tree
from test_cli_core import ROOT, home, pilead  # noqa: F401  (also brings the osascript stub fixture)
from test_verify_diff_board import SID, make_session, report, stage, write_report

sys.path.insert(0, str(ROOT / "lib"))
from pilead import cli, ledger, lifecycle, queue  # noqa: E402

PARKED = (f"  parked {SID} (reviewed) — its staged work stays in the worktree; pilead send {SID} <packet> "
          "resumes it")


def events(tmp_path, event=None):
    return ledger.read(home(tmp_path), event=event)


def fields(rec):
    return {k: v for k, v in rec.items() if k != "ts"}


def reviewed_session(tmp_path, **kw):
    wt, sdir = make_session(tmp_path, **kw)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report(), n=kw.get("packets", 1))
    return wt, sdir


def staged(wt):
    return subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--name-only"], capture_output=True,
                          text=True, check=True).stdout


def status(sdir):
    return (sdir / "status").read_text().strip()


def meta(sdir):
    return json.loads((sdir / "meta.json").read_text())


@pytest.mark.parametrize("findings", [False, True])
def test_a_reviewed_packet_parks_its_executor(tmp_path, findings):
    wt, sdir = reviewed_session(tmp_path)
    wt_before, staged_before = tree(wt), staged(wt)
    extra = []
    if findings:
        f = tmp_path / "findings.md"
        f.write_text("1. nothing\n")
        extra = ["--findings", str(f)]
    r = pilead("verify", SID, "--diff-reviewed", *extra, check=False)
    assert r.returncode == 0, r.stdout
    assert r.stdout.splitlines()[-1] == PARKED
    assert r.stdout.count("parked ") == 1 and r.stderr == ""
    assert status(sdir) == "closed" and meta(sdir)["auto_closed"] == "reviewed"
    assert "superseded_by" not in meta(sdir)
    (ev,) = events(tmp_path, "auto_closed")
    assert fields(ev) == {"event": "auto_closed", "session_id": SID, "action": "close", "reason": "reviewed",
                          "trigger": "verify"}
    names = [e["event"] for e in events(tmp_path)]
    assert names.index("report_reviewed") < names.index("closed") < names.index("auto_closed")
    assert events(tmp_path, "closed")[0]["reason"] == "auto-close (reviewed)"
    assert events(tmp_path, "auto_close_error") == []
    assert tree(wt) == wt_before and staged(wt) == staged_before  # the staged work is untouched


def test_with_for_autocommit_the_clearance_is_unchanged_and_the_park_line_comes_last(tmp_path):
    wt, sdir = reviewed_session(tmp_path)
    r = pilead("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed", check=False)
    assert r.returncode == 0, r.stdout
    lines = r.stdout.splitlines()
    assert lines[-1] == PARKED and "CLEARED" in "\n".join(lines[:-1])
    (ac,) = events(tmp_path, "auto_commit")
    assert ac["cleared"] is True
    assert status(sdir) == "closed"


def skip_pinned(tmp_path, sdir):
    m = meta(sdir)
    m["keep"] = True
    (sdir / "meta.json").write_text(json.dumps(m))


def skip_superseded(tmp_path, sdir):
    m = meta(sdir)
    m["superseded_by"] = "someone-else"
    (sdir / "meta.json").write_text(json.dumps(m))


def skip_queued(tmp_path, sdir):
    p = tmp_path / "q.md"
    p.write_text("GOAL: next\n")
    queue.enqueue(home(tmp_path), SID, p.read_text(), p)


def skip_unreadable(tmp_path, sdir):
    (sdir / "queue.json").write_text("{not json")


@pytest.mark.parametrize("case", ["pinned", "closed", "dead", "superseded", "not the current packet", "queued",
                                  "unreadable queue"])
def test_each_skip_case_parks_nothing(tmp_path, case):
    if case in ("closed", "dead"):
        wt, sdir = reviewed_session(tmp_path, status=case)
    elif case == "not the current packet":
        wt, sdir = make_session(tmp_path, packets=2, status="busy")
        stage(wt, "src/alpha.py", "src/beta.py")
        write_report(sdir, report(), n=1)
    else:
        wt, sdir = reviewed_session(tmp_path)
        {"pinned": skip_pinned, "superseded": skip_superseded, "queued": skip_queued,
         "unreadable queue": skip_unreadable}[case](tmp_path, sdir)
    status_before, meta_before = status(sdir), meta(sdir)
    extra = ["--packet", "1"] if case == "not the current packet" else []
    r = pilead("verify", SID, "--diff-reviewed", *extra, check=False)
    assert r.returncode == 0, r.stdout
    assert "parked " not in r.stdout
    assert events(tmp_path, "auto_closed") == [] and events(tmp_path, "auto_close_error") == []
    assert events(tmp_path, "closed") == [] and events(tmp_path, "superseded") == []
    assert status(sdir) == status_before and meta(sdir) == meta_before


def test_a_failing_park_is_ledgered_and_verify_is_as_it_was(tmp_path, monkeypatch, capsys):
    wt, sdir = reviewed_session(tmp_path)
    # what verify prints and returns with no park at all: the same verify without --diff-reviewed
    rc_plain = cli.main(["verify", SID])
    plain = capsys.readouterr()

    def boom(*a, **k):
        raise RuntimeError("the tab would\nnot close")
    monkeypatch.setattr(lifecycle, "close_executor", boom)
    rc = cli.main(["verify", SID, "--diff-reviewed"])
    out = capsys.readouterr()
    assert (rc, out.out, out.err) == (rc_plain, plain.out, plain.err)
    (err,) = events(tmp_path, "auto_close_error")
    assert fields(err) == {"event": "auto_close_error", "session_id": SID, "error": "the tab would not close",
                           "trigger": "verify"}
    assert events(tmp_path, "auto_closed") == [] and status(sdir) == "reported"
    assert "auto_closed" not in meta(sdir)


def test_a_plain_verify_parks_nothing(tmp_path):
    wt, sdir = reviewed_session(tmp_path)
    for flags in ((), ("--for-autocommit", "--in-plan")):
        r = pilead("verify", SID, *flags, check=False)
        assert "parked " not in r.stdout
    assert events(tmp_path, "auto_closed") == [] and status(sdir) == "reported"

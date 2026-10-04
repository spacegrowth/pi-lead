"""pilead verify | diff | board over a real temp git repo + hand-built session state (no real tabs, no `open`)."""
import json
import subprocess
from pathlib import Path

import pytest

from test_cli_core import PILEAD, home, pilead  # noqa: F401  (also brings the osascript stub fixture)

SID = "topic-120000"


def git(wt, *args):
    subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


def make_session(tmp_path, sid=SID, status="reported", lead="lead-1", packets=1):
    wt = tmp_path / f"wt-{sid}"
    wt.mkdir()
    git(wt, "init", "-q")
    (wt / "seed.txt").write_text("seed\n")
    git(wt, "add", "seed.txt")
    git(wt, "commit", "-q", "-m", "seed")
    sdir = home(tmp_path) / "sessions" / sid
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({
        "sid": sid, "lead": lead, "worktree": str(wt), "topic": "topic", "model": "anthropic/sonnet",
        "status": status, "created": "2026-01-01T00:00:00Z", "packets": packets, "label": "[Exec] x"}))
    (sdir / "status").write_text(status + "\n")
    for n in range(1, packets + 1):
        (sdir / f"packet-{n:04d}.md").write_text(f"GOAL: task number {n}\n")
    # What `pilead spawn` / each `pilead send` store for every packet: its refs baseline
    # (lib/pilead/refs.py), so `verify` compares refs instead of reporting "not compared".
    import sys
    lib = str(Path(__file__).resolve().parents[1] / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    from pilead import refs
    for n in range(1, packets + 1):
        refs.record_baseline(home(tmp_path), sid, n, wt)
    return wt, sdir


def stage(wt, *names):
    for n in names:
        (wt / n).parent.mkdir(parents=True, exist_ok=True)
        (wt / n).write_text(f"content of {n}\n")
    git(wt, "add", *names)


def report(changed="src/alpha.py, src/beta.py", status="clean", risk="none", unverified="none", staged=True):
    return (f"Built the thing; tests pass.\n\nStatus: {status}\nRisk flags: {risk}\nUNVERIFIED: {unverified}\n"
            f"Changed: {changed}\n\n## What changed\n"
            + "".join(f"- {f.strip()}: edited\n" for f in changed.split(",") if f.strip())
            + ("\nChanges are staged (not committed).\n" if staged else ""))


def write_report(sdir, text, n=1):
    (sdir / f"report-{n:04d}.md").write_text(text)


def run(*args):
    return pilead(*args, check=False)


def test_counts_match(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    r = run("verify", SID)
    assert r.returncode == 0, r.stdout
    assert "VERDICT: COUNTS-MATCH" in r.stdout


def test_claimed_but_unstaged_is_mismatch(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py")
    (wt / "src" / "beta.py").write_text("x\n")  # modified/untracked, never staged
    write_report(sdir, report())
    r = run("verify", SID)
    assert r.returncode == 1
    assert "VERDICT: MISMATCH" in r.stdout
    assert "src/beta.py" in r.stdout


def test_no_tldr_is_malformed(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py")
    write_report(sdir, "I did some work on alpha.py and it went fine.\n")
    r = run("verify", SID)
    assert r.returncode == 2
    assert "VERDICT: MALFORMED" in r.stdout


def test_empty_index_is_inconclusive(tmp_path):
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report(changed="nothing was needed", staged=False))
    r = run("verify", SID)
    assert r.returncode == 3
    assert "VERDICT: INCONCLUSIVE" in r.stdout


def test_claims_over_empty_index_is_mismatch(tmp_path):
    wt, sdir = make_session(tmp_path)
    write_report(sdir, report())
    r = run("verify", SID)
    assert r.returncode == 1
    assert "VERDICT: MISMATCH" in r.stdout


def test_verify_without_report_or_session(tmp_path):
    make_session(tmp_path)
    r = run("verify", SID)
    assert r.returncode == 2 and "no report yet" in r.stderr
    r = run("verify", "nope")
    assert r.returncode == 2 and "unknown session" in r.stderr


def test_autocommit_clears_only_with_all_attestations(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert "AUTO-COMMIT: CLEARED" in r.stdout
    r = run("verify", SID, "--for-autocommit", "--diff-reviewed")
    assert r.returncode != 0 and "AUTO-COMMIT: NOT-CLEARED-BECAUSE-not-attested-in-plan" in r.stdout
    r = run("verify", SID, "--for-autocommit", "--in-plan")
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-not-attested-diff-reviewed" in r.stdout


@pytest.mark.parametrize("kw,slug", [
    (dict(status="clean-with-caveats"), "status-not-clean"),
    (dict(risk="touched the parser"), "risk-flags-present"),
    (dict(unverified="never ran it live"), "unverified-claims-present"),
])
def test_autocommit_blocked_by_unclean_tldr(tmp_path, kw, slug):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report(**kw))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode != 0
    assert f"NOT-CLEARED-BECAUSE-{slug}" in r.stdout


def test_autocommit_blocked_by_signoff_path(tmp_path):
    (wt, sdir) = make_session(tmp_path)
    stage(wt, "extensions/x.ts")
    write_report(sdir, report(changed="extensions/x.ts"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-signoff-gated-path-touched" in r.stdout


def test_moving_a_file_out_of_a_signoff_path_is_blocked(tmp_path):
    wt, sdir = make_session(tmp_path)
    (wt / "extensions").mkdir()
    (wt / "extensions" / "x.ts").write_text("x\n")
    git(wt, "add", "extensions/x.ts")
    git(wt, "commit", "-q", "-m", "x")
    from pilead import refs  # the commit above is the base the packet started from, not a stray ref move
    refs.record_baseline(home(tmp_path), SID, 1, wt)
    (wt / "lib").mkdir()
    git(wt, "mv", "extensions/x.ts", "lib/moved.ts")
    write_report(sdir, report(changed="lib/moved.ts"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-signoff-gated-path-touched" in r.stdout


def test_findings_copied_when_diff_reviewed(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    f = tmp_path / "findings.md"
    f.write_text("1. a.py:1 — note — fine\n")
    r = run("verify", SID, "--diff-reviewed", "--findings", str(f))
    assert r.returncode == 0
    assert (sdir / "review-0001.md").read_text() == f.read_text()


def test_verify_picks_latest_report_and_packet_flag(tmp_path):
    wt, sdir = make_session(tmp_path, packets=2)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report(), n=1)
    write_report(sdir, "no tldr here\n", n=2)
    assert run("verify", SID).returncode == 2            # latest = report-0002 (malformed)
    assert run("verify", SID, "--packet", "1").returncode == 0


def test_diff_html_contains_staged_files(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/beta.py")
    write_report(sdir, report())
    r = run("diff", SID)
    assert r.returncode == 0, r.stderr
    out = sdir / "diff-0001.html"
    assert str(out) in r.stdout
    html = out.read_text()
    assert "src/alpha.py" in html and "src/beta.py" in html
    assert "content of src/alpha.py" in html


def test_diff_scopes_to_report_and_all_bypasses(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py", "src/gamma.py")
    write_report(sdir, report(changed="src/alpha.py"))
    run("diff", SID)
    html = (sdir / "diff-0001.html").read_text()
    assert "src/alpha.py" in html and "src/gamma.py" not in html
    run("diff", SID, "--all")
    assert "src/gamma.py" in (sdir / "diff-0001.html").read_text()


def test_diff_open_runs_open(tmp_path, monkeypatch):
    """--open shells out to `open`: a stub `open` on PATH records it instead of touching anything."""
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py")
    write_report(sdir, report(changed="src/alpha.py"))
    log = tmp_path / "open.log"
    stub = tmp_path / "fakebin" / "open"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    stub.chmod(0o755)
    assert run("diff", SID, "--open").returncode == 0
    assert log.read_text().strip().endswith("diff-0001.html")


def test_board_lists_every_session_with_status(tmp_path):
    make_session(tmp_path, sid="one-1", status="reported")
    make_session(tmp_path, sid="two-2", status="busy")
    make_session(tmp_path, sid="three-3", status="closed")
    pilead("lead-start", "lead-1", "--project", "myproj")
    r = run("board")
    assert r.returncode == 0, r.stderr
    page = (home(tmp_path) / "board.html").read_text()
    for sid, label in (("one-1", "reported"), ("two-2", "busy"), ("three-3", "closed")):
        assert sid in page
        assert f'pill {"busy" if label == "busy" else label}">{label}' in page
    assert "myproj" in page


def test_board_empty_home_and_broken_meta(tmp_path):
    assert run("board").returncode == 0
    make_session(tmp_path, sid="ok-1")
    bad = home(tmp_path) / "sessions" / "bad-1"
    bad.mkdir()
    (bad / "meta.json").write_text("{not json")
    assert run("board").returncode == 0
    page = (home(tmp_path) / "board.html").read_text()
    assert "bad-1" in page and "ok-1" in page


# ── condition 4 reads config `signoff_paths` ─────────────────────────────────────────────────────
def _config(tmp_path, **kw):
    home(tmp_path).mkdir(parents=True, exist_ok=True)
    (home(tmp_path) / "config.json").write_text(json.dumps(kw))


def _auto_commits(tmp_path):
    p = home(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [{k: v for k, v in r.items() if k not in ("ts", "event")} for r in recs if r["event"] == "auto_commit"]


def test_a_configured_signoff_path_blocks_the_commit_gate(tmp_path):
    wt, sdir = make_session(tmp_path)
    _config(tmp_path, signoff_paths=["src/pay"])
    stage(wt, "src/pay/x.py")
    write_report(sdir, report(changed="src/pay/x.py"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode != 0
    assert "AUTO-COMMIT: NOT-CLEARED-BECAUSE-signoff-gated-path-touched" in r.stdout
    assert "configured in signoff_paths" in r.stdout
    assert _auto_commits(tmp_path) == [{"session_id": SID, "packet": 1, "cleared": False,
                                         "reason": "signoff-gated-path-touched"}]


@pytest.mark.parametrize("value", ["src", [""], [None, 7], [], ["   "], {"src/pay": True}, None])
def test_a_signoff_paths_value_that_names_no_usable_marker_clears(tmp_path, value):
    wt, sdir = make_session(tmp_path)
    _config(tmp_path, signoff_paths=value)
    stage(wt, "src/pay/x.py")
    write_report(sdir, report(changed="src/pay/x.py"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode == 0, r.stdout
    assert "AUTO-COMMIT: CLEARED" in r.stdout and "configured in signoff_paths" not in r.stdout
    assert _auto_commits(tmp_path) == [{"session_id": SID, "packet": 1, "cleared": True, "reason": None}]


def test_the_usable_entries_of_a_mixed_list_still_apply(tmp_path):
    wt, sdir = make_session(tmp_path)
    _config(tmp_path, signoff_paths=[None, "", 7, "src/pay"])
    stage(wt, "src/pay/x.py")
    write_report(sdir, report(changed="src/pay/x.py"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert "NOT-CLEARED-BECAUSE-signoff-gated-path-touched" in r.stdout and "configured in signoff_paths" in r.stdout


@pytest.mark.parametrize("value", [[], ["src/pay"]])
def test_a_built_in_marker_still_blocks_whatever_is_configured(tmp_path, value):
    wt, sdir = make_session(tmp_path)
    _config(tmp_path, signoff_paths=value)
    stage(wt, "extensions/x.ts")
    write_report(sdir, report(changed="extensions/x.ts"))
    r = run("verify", SID, "--for-autocommit", "--in-plan", "--diff-reviewed")
    assert r.returncode != 0 and "NOT-CLEARED-BECAUSE-signoff-gated-path-touched" in r.stdout
    assert "configured in signoff_paths" not in r.stdout
    assert _auto_commits(tmp_path) == [{"session_id": SID, "packet": 1, "cleared": False,
                                         "reason": "signoff-gated-path-touched"}]


# ── b12: --open goes through vendor/relay/platform_cmds.py `open_path` ─────────────────────────────────
def _open_stub(tmp_path, rc=0):
    log = tmp_path / "open.log"
    stub = tmp_path / "fakebin" / "open"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit {rc}\n')
    stub.chmod(0o755)
    return log


def test_board_open_runs_open_once(tmp_path):
    log = _open_stub(tmp_path)
    r = run("board", "--open")
    assert r.returncode == 0
    assert log.read_text().splitlines() == [str(home(tmp_path) / "board.html")]
    assert "no opener available" not in r.stdout


def test_diff_open_runs_open_once(tmp_path):
    wt, sdir = make_session(tmp_path)
    stage(wt, "src/alpha.py")
    write_report(sdir, report(changed="src/alpha.py"))
    log = _open_stub(tmp_path)
    assert run("diff", SID, "--open").returncode == 0
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("verb", ["board", "diff"])
def test_no_opener_prints_the_address_and_exits_0(tmp_path, verb):
    """`open` that fails stands for any platform where open_path gives False (a headless Linux box has no
    xdg-open): the page is still written, one line says where it is, exit 0."""
    _open_stub(tmp_path, rc=1)
    if verb == "diff":
        wt, sdir = make_session(tmp_path)
        stage(wt, "src/alpha.py")
        write_report(sdir, report(changed="src/alpha.py"))
        r = run("diff", SID, "--open")
        page = sdir / "diff-0001.html"
    else:
        r = run("board", "--open")
        page = home(tmp_path) / "board.html"
    assert r.returncode == 0 and page.is_file()
    lines = [ln for ln in r.stdout.splitlines() if "no opener" in ln]
    assert lines == [f"(no opener available — open it yourself: {page.resolve().as_uri()})"]


def test_open_or_print_in_process(tmp_path, monkeypatch, capsys):
    """open_path replaced to return False: exactly the one line, nothing raised."""
    import sys
    sys.path.insert(0, str(PILEAD.parents[1] / "lib"))
    from pilead import review
    monkeypatch.setattr(review.platform_cmds, "open_path", lambda p: False)
    page = tmp_path / "x.html"
    page.write_text("")
    review._open_or_print(page)
    assert capsys.readouterr().out == f"(no opener available — open it yourself: {page.resolve().as_uri()})\n"
    monkeypatch.setattr(review.platform_cmds, "open_path", lambda p: True)
    review._open_or_print(page)
    assert capsys.readouterr().out == ""

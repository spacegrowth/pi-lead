"""`pilead handoff` driven through bin/pilead with the fake `pi` (tests/fake_pi), a recording `osascript` stub and a
`ps` stub first on PATH (the terminal seam: whether iTerm "runs" and what a spawn answers come from the test's
environment). The caller is given by PI_LEAD_SID in the test's own environment. Session logs are small synthetic
files (tests/test_usage.py's writers). No real tab is opened and the real pi never runs."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from test_usage import assistant, u, write_log

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
LEAD = "lead-out"
MODEL = "anthropic/claude-sonnet-5"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# A spawn answers OK (NOTARGET when $FAKE_SPAWN is "fail") and first writes what is under leads/ and handoffs/ at
# that moment to $FAKE_SEEN (the record must exist before the tab is opened); any other question gets $FAKE_TAB.
OSA = """#!/bin/sh
printf '%s\\n=====\\n' "$2" >> "$FAKE_OSA_LOG"
case "$2" in
  *targetFound*)
    ls "$PI_LEAD_HOME/leads" "$PI_LEAD_HOME/handoffs" >> "$FAKE_SEEN" 2>&1
    if [ "$FAKE_SPAWN" = fail ]; then printf 'NOTARGET\\n\\nfront\\n'; else printf 'OK\\nFAKE-UUID\\nfront\\n'; fi ;;
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
    monkeypatch.setenv("FAKE_ITERM_RUNNING", "1")
    monkeypatch.setenv("FAKE_TAB", "false")
    monkeypatch.setenv("FAKE_SEEN", str(tmp_path / "seen-at-spawn.txt"))
    monkeypatch.delenv("FAKE_SPAWN", raising=False)


def H(tmp_path):
    return tmp_path / "pi-lead-home"


def pilead(*args, env=None, caller=LEAD):
    e = {**os.environ, **(env or {})}
    if caller is not None:
        e["PI_LEAD_SID"] = caller
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e)


def content_tree(root):
    """{relative path: bytes, or "dir"} for everything under `root`."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs:
            out[str((Path(dirpath) / name).relative_to(root))] = "dir"
        for name in files:
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def events(tmp_path, name):
    p = H(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if r["event"] == name]


def fake_pi_calls(tmp_path):
    p = tmp_path / "fake-pi.log"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


def lead(tmp_path, entries=None, **extra):
    """The outgoing lead: its record, cwd, inbox and a session log whose last answer is on MODEL."""
    cwd = (tmp_path / "lead-cwd")
    cwd.mkdir(exist_ok=True)
    cwd = cwd.resolve()
    d = H(tmp_path) / "leads"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sid": LEAD, "project": "proj", "cwd": str(cwd), "inbox": str(d / f"{LEAD}.inbox.md"),
           "iterm_handle": "w0t1p0:AAAAAAAA-0000-4000-8000-0000000000AA", "terminal": "iTerm.app",
           "started": "2026-10-01T09:00:00Z", "autonomous": True, "autonomous_source": "command", "tier": "manual",
           "tier_source": "command", **extra}
    (d / f"{LEAD}.json").write_text(json.dumps(rec, indent=2) + "\n")
    (d / f"{LEAD}.inbox.md").write_text("")
    write_log(LEAD, cwd, entries if entries is not None else [assistant(u(10, 5))])
    return rec


def session(tmp_path, sid, keep=False, status="busy"):
    sdir = H(tmp_path) / "sessions" / sid
    sdir.mkdir(parents=True)
    meta = {"sid": sid, "lead": LEAD, "worktree": str(tmp_path), "topic": "t", "model": MODEL, "status": status,
            "created": "2026-10-01T10:00:00Z", "packets": 1, "label": f"[Exec] {sid}", "layout": "tab"}
    if keep:
        meta.update(keep=True, keep_by=LEAD)
    (sdir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (sdir / "status").write_text(status + "\n")
    (sdir / "packet-0001.md").write_text("GOAL\n")


def memo(tmp_path, text="# Handoff\n\nIn flight: s-a on packet 1.\nNext: review s-b.\n"):
    p = tmp_path / "memo.md"
    p.write_text(text)
    return p


def successor_of(r):
    m = re.search(r"successor (\S+) \(", r.stdout.splitlines()[-1])
    assert m, r.stdout
    return m.group(1)


def run_bootstrap(boot):
    """Run a launch's bootstrap as the new tab's shell would: a new tab's shell does not inherit this
    process's PI_LEAD_* environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PI_LEAD_") or k == "PI_LEAD_HOME"}
    env.pop("PI_LEAD_HOME", None)
    subprocess.run(["sh", str(boot), "FAKE-UUID"], check=True, env={**env, "ITERM_SESSION_ID": "w0t2p0:FAKE-UUID"},
                   capture_output=True)


# ── refusals: exit code 2, one line on stderr, home byte-identical ────────────────────────────────
def refused(tmp_path, r, *words):
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert r.stdout == ""
    assert len(r.stderr.strip().splitlines()) == 1, r.stderr
    for w in words:
        assert w in r.stderr, (w, r.stderr)


def test_no_caller_is_refused_naming_lead_start(tmp_path):
    lead(tmp_path)
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(memo(tmp_path)), caller=None), "pilead lead-start")
    refused(tmp_path, pilead("handoff", str(memo(tmp_path)), caller="lead-not-registered"), "pilead lead-start")
    assert content_tree(H(tmp_path)) == before


def test_an_executor_is_refused(tmp_path):
    lead(tmp_path)
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(memo(tmp_path)), env={"PI_LEAD_ROLE": "executor"}), "executor")
    assert content_tree(H(tmp_path)) == before


@pytest.mark.parametrize("kind", ["missing", "dir", "blank"])
def test_a_memo_that_is_missing_not_a_file_or_blank_is_refused(tmp_path, kind):
    lead(tmp_path)
    p = tmp_path / "memo.md"
    if kind == "dir":
        p.mkdir()
    elif kind == "blank":
        p.write_text("  \n\t\n")
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(p)), "what is in flight", "open questions", "next steps")
    assert content_tree(H(tmp_path)) == before


def test_a_cwd_that_is_not_a_directory_is_refused(tmp_path):
    lead(tmp_path, cwd=str(tmp_path / "gone"))
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(memo(tmp_path))), "not a directory")
    assert content_tree(H(tmp_path)) == before


def test_no_model_is_refused_without_model_and_handed_off_with_it(tmp_path):
    lead(tmp_path, entries=[])  # the log holds no answer
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(memo(tmp_path))), "default model", "--model")
    assert content_tree(H(tmp_path)) == before
    r = pilead("handoff", str(memo(tmp_path)), "--model", "deepseek/deepseek-v4-flash")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "successor model: deepseek/deepseek-v4-flash (from --model)"


def test_an_effort_that_is_no_level_is_refused(tmp_path):
    lead(tmp_path)
    before = content_tree(H(tmp_path))
    refused(tmp_path, pilead("handoff", str(memo(tmp_path)), "--effort", "turbo"), "turbo")
    assert content_tree(H(tmp_path)) == before


# ── the handoff ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def world(tmp_path):
    rec = lead(tmp_path)
    session(tmp_path, "s-a")
    session(tmp_path, "s-b", keep=True)
    (H(tmp_path) / "leads" / f"{LEAD}.inbox.md").write_text("reported s-a /r/report-0001.md\n")
    plans = H(tmp_path) / "plans"
    plans.mkdir()
    (plans / f"{LEAD}.json").write_text(json.dumps({"items": []}) + "\n")
    return rec


def test_a_handoff_end_to_end(tmp_path, world):
    m = memo(tmp_path)
    memo_bytes = m.read_bytes()
    env = {"PI_LEAD_HELD_BY": "4242", "PI_LEAD_INBOX": "/somewhere/inbox.md",
           "ITERM_SESSION_ID": "w0t1p0:AAAAAAAA-0000-4000-8000-0000000000AA"}
    r = pilead("handoff", str(m), env=env)
    assert r.returncode == 0, r.stderr
    succ = successor_of(r)
    assert UUID_RE.match(succ)
    home = H(tmp_path)
    copy = home / "handoffs" / f"{succ}.md"
    lines = r.stdout.splitlines()
    assert lines[0] == f"successor model: {MODEL} (from this lead's session log)"
    assert lines[1].startswith("placement=")
    assert lines[2:] == [
        f"lead {LEAD} → {succ} (proj)",
        f"adopted s-a from {LEAD}",
        f"adopted s-b from {LEAD}",
        "moved 1 pending wake line(s)",
        "plan moved",
        "pinned: s-b — still pinned under the successor; nothing closes them by itself",
        f"handoff complete — successor {succ} (proj) is the lead; this session has stepped down",
    ]
    # the launch: next to the caller's tab, the model on the line, the message as the first prompt
    ldir = home / "leads" / f"{succ}.launch"
    boot = ldir / "bootstrap.sh"
    for f in ldir.iterdir():
        text = f.read_text()
        for word in ("PI_LEAD_HELD_BY", "PI_LEAD_SID", "PI_LEAD_INBOX", "PI_LEAD_ROLE", "PILEAD_SHIM_DIR"):
            assert word not in text, (f, word)
    run_bootstrap(boot)
    call = fake_pi_calls(tmp_path)[-1]
    argv = call["argv"]
    assert argv[:4] == ["--session-id", succ, "--model", MODEL]
    want_msg = (f"You are the successor lead for project 'proj', registered by pilead handoff. Read {copy} and "
                "follow its SUCCESSOR AFTERCARE section first.")
    assert argv[-1] == want_msg
    assert "PI_LEAD_ROLE" not in call["env"] and "PILEAD_SHIM_DIR" not in call["env"]
    osa = (tmp_path / "osa.log").read_text()
    assert "AAAAAAAA-0000-4000-8000-0000000000AA" in osa  # placed by the caller's handle
    assert "/name" not in osa
    assert json.loads((home / "leads" / f"{succ}.json").read_text())["label"] == f"[Lead] proj ·{succ[:4]}"
    # the successor's record
    rec = json.loads((home / "leads" / f"{succ}.json").read_text())
    assert rec["sid"] == succ and rec["project"] == "proj" and rec["cwd"] == world["cwd"]
    assert rec["inbox"] == str(home / "leads" / f"{succ}.inbox.md") and rec["terminal"] == "iTerm.app"
    assert rec["lineage_started"] == world["started"] and rec["handoff_memo"] == str(copy)
    assert rec["launch_model"] == MODEL and rec["iterm_handle"] == "FAKE-UUID"
    assert rec["predecessor"] == {"sid": LEAD, "project": "proj",
                                  "iterm_handle": "w0t1p0:AAAAAAAA-0000-4000-8000-0000000000AA",
                                  "terminal": "iTerm.app"}
    assert (rec["autonomous"], rec["autonomous_source"], rec["tier"], rec["tier_source"]) == (
        False, "config", "auto", "config")
    assert "started" in rec
    # the memo copy is the memo plus the section; the memo is byte-identical
    assert m.read_bytes() == memo_bytes
    text = copy.read_text()
    assert text.startswith(memo_bytes.decode() + "---\n(pi-lead — SUCCESSOR AFTERCARE; do not remove)\n")
    assert "3. EXECUTORS: 2 session(s)" in text and "6. PINNED: 1 pinned session(s) are yours: s-b" in text
    assert "autonomous on, tier manual" in text
    # what moved
    for sid in ("s-a", "s-b"):
        assert json.loads((home / "sessions" / sid / "meta.json").read_text())["lead"] == succ
    assert (home / "leads" / f"{succ}.inbox.md").read_text() == "reported s-a /r/report-0001.md\n"
    assert (home / "plans" / f"{succ}.json").is_file() and not (home / "plans" / f"{LEAD}.json").exists()
    assert not (home / "leads" / f"{LEAD}.json").exists()
    assert json.loads((home / "handoffs" / f"{LEAD}.json").read_text())["to"] == succ
    (ev,) = events(tmp_path, "lead_handoff")
    assert {k: v for k, v in ev.items() if k != "ts"} == {
        "event": "lead_handoff", "from_lead": LEAD, "to_lead": succ, "project": "proj", "memo": str(m),
        "model": MODEL}


def test_the_record_and_the_memo_copy_exist_before_the_tab_is_opened(tmp_path, world):
    r = pilead("handoff", str(memo(tmp_path)))
    succ = successor_of(r)
    seen = (tmp_path / "seen-at-spawn.txt").read_text().split()
    assert f"{succ}.json" in seen and f"{succ}.inbox.md" in seen and f"{succ}.md" in seen
    assert f"{LEAD}.json" in seen  # the caller is still the lead while the tab opens


def test_the_model_is_the_last_answers_not_the_first_nor_a_recorded_launch_model(tmp_path):
    lead(tmp_path, entries=[assistant(u(1, 1), provider="anthropic", model="claude-haiku-4-5"),
                            assistant(u(1, 1), provider="deepseek", model="deepseek-v4-pro")],
         launch_model="anthropic/claude-opus-5")
    r = pilead("handoff", str(memo(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "successor model: deepseek/deepseek-v4-pro (from this lead's session log)"
    succ = successor_of(r)
    run_bootstrap(H(tmp_path) / "leads" / f"{succ}.launch" / "bootstrap.sh")
    argv = fake_pi_calls(tmp_path)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "deepseek/deepseek-v4-pro"


def test_a_failed_launch_leaves_home_as_it_was_apart_from_the_ledger(tmp_path, world):
    before = {k: v for k, v in content_tree(H(tmp_path)).items() if k != "ledger.jsonl"}
    r = pilead("handoff", str(memo(tmp_path)), env={"FAKE_SPAWN": "fail"})
    assert r.returncode == 1
    assert "this session is still the lead" in r.stderr and len(r.stderr.strip().splitlines()) == 1
    after = {k: v for k, v in content_tree(H(tmp_path)).items() if k != "ledger.jsonl"}
    assert after == before
    assert events(tmp_path, "lead_handoff") == [] and events(tmp_path, "lead_moved") == []


def test_a_dropped_marker_is_named_and_the_handoff_completes(tmp_path, world):
    inherited = H(tmp_path) / "handoffs" / f"{LEAD}.md"
    inherited.parent.mkdir(parents=True, exist_ok=True)
    inherited.write_text("old memo\n[ops-not-lead-work] [keep-the-gate]\n")
    r = pilead("handoff", str(memo(tmp_path, "new memo, [keep-the-gate] only\n")))
    assert r.returncode == 0, r.stderr
    (line,) = r.stderr.strip().splitlines()
    assert "[ops-not-lead-work]" in line and "[keep-the-gate]" not in line and "word for word" in line
    assert r.stdout.splitlines()[-1].startswith("handoff complete — successor ")


def test_lead_start_of_the_successor_keeps_what_the_handoff_recorded(tmp_path, world):
    r = pilead("handoff", str(memo(tmp_path)))
    succ = successor_of(r)
    before = json.loads((H(tmp_path) / "leads" / f"{succ}.json").read_text())
    r2 = pilead("lead-start", succ, "--project", "other", caller=None)
    assert r2.returncode == 0, r2.stderr
    rec = json.loads((H(tmp_path) / "leads" / f"{succ}.json").read_text())
    assert rec["project"] == "proj"
    for k in ("predecessor", "handoff_memo", "launch_model"):
        assert rec[k] == before[k], k


def test_stop_of_the_successor_removes_its_memo_copy_and_names_it(tmp_path, world):
    r = pilead("handoff", str(memo(tmp_path)))
    succ = successor_of(r)
    copy = H(tmp_path) / "handoffs" / f"{succ}.md"
    assert copy.is_file()
    r2 = pilead("stop", succ, caller=None)
    assert r2.returncode == 0, r2.stderr
    assert f"removed {copy}" in r2.stdout.splitlines()
    assert not copy.exists()


def test_the_launch_line_parses_to_one_message_argument(tmp_path, world):
    r = pilead("handoff", str(memo(tmp_path)), "--project", "my proj", "--effort", "high")
    succ = successor_of(r)
    text = (H(tmp_path) / "leads" / f"{succ}.launch" / "bootstrap.sh").read_text()
    line = next(ln for ln in text.splitlines() if "exec pi" in ln)
    argv = shlex.split(line[line.index("PI_LEAD_HOME="):])
    assert argv[argv.index("--thinking") + 1] == "high"
    assert argv[-1].startswith("You are the successor lead for project 'my proj'")
    assert r.stdout.splitlines()[-1].endswith(f"successor {succ} (my proj) is the lead; this session has stepped down")

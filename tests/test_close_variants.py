"""`pilead close` and its variants (`--supersede`, `--keep-tab`, `--self`), the pin refusal, and how `send`
treats a superseded or kept-tab session. Driven through bin/pilead against fake_pi and the recording
`osascript` stub conftest puts first on PATH (no real tab is opened, renamed or closed)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"


def pilead(*args, check=True, env=None):
    r = subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stderr
    return r


def home(tmp_path):
    return tmp_path / "pi-lead-home"


def tree(root):
    """{relative path: (kind, bytes or link target, mtime_ns)} for everything under `root` (links not followed)."""
    out = {}
    root = Path(root)
    if not root.exists():
        return out
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(dirpath) / name
            st = p.lstat()
            if p.is_symlink():
                out[str(p.relative_to(root))] = ("link", os.readlink(p), st.st_mtime_ns)
            elif p.is_dir():
                out[str(p.relative_to(root))] = ("dir", None, st.st_mtime_ns)
            else:
                out[str(p.relative_to(root))] = ("file", p.read_bytes(), st.st_mtime_ns)
    return out


def osa_log(tmp_path):
    p = tmp_path / "osa.log"
    return p.read_text() if p.is_file() else ""


def events(tmp_path, sid=None):
    p = home(tmp_path) / "ledger.jsonl"
    recs = [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []
    return [r for r in recs if sid is None or r.get("session_id") == sid]


@pytest.fixture
def env(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    (tmp_path / "p2.md").write_text("GOAL: the second thing\n")
    pilead("lead-start", "lead-1", "--project", "proj")
    return tmp_path


def spawn(env):
    r = pilead("spawn", str(env / "wt"), "topic", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1")
    return r.stdout.split()[1]


def sdir(env, sid):
    return home(env) / "sessions" / sid


def meta(env, sid):
    return json.loads((sdir(env, sid) / "meta.json").read_text())


def mark_reported(env, sid):
    """The current packet reported: its report file written and `reported` stored (see the packet's rule
    for a plain `send` to a session that is not closed or dead)."""
    n = meta(env, sid)["packets"]
    (sdir(env, sid) / f"report-{n:04d}.md").write_text("Done.\nStatus: clean\n")
    (sdir(env, sid) / "status").write_text("reported\n")


@pytest.fixture
def child():
    """A throwaway process standing in for the executor's pi (never a real pi); stopped afterwards."""
    c = subprocess.Popen(["sleep", "120"])
    yield c
    if c.poll() is None:
        c.kill()
        c.wait()


# ── plain close ──────────────────────────────────────────────────────────────
def test_plain_close_of_a_busy_session_with_no_report_says_so(env):
    sid = spawn(env)
    r = pilead("close", sid)
    assert r.stdout == f"closed {sid}\n"
    assert r.stderr == f"pilead: {sid} was busy on packet 0001; no report was written\n"
    assert (sdir(env, sid) / "status").read_text() == "closed\n"
    assert [e["event"] for e in events(env, sid)][-1] == "closed"


def test_plain_close_of_a_busy_session_with_its_report_is_quiet(env):
    sid = spawn(env)
    (sdir(env, sid) / "report-0001.md").write_text("Done.\n")  # stored status is still `busy`
    assert (sdir(env, sid) / "status").read_text() == "busy\n"
    r = pilead("close", sid)
    assert (r.stdout, r.stderr) == (f"closed {sid}\n", "")


def test_neither_a_sid_nor_self_and_both_exit_2_and_change_nothing(env):
    sid = spawn(env)
    before = tree(env)
    for argv in (["close"], ["close", sid, "--self", "lead-1"]):
        r = pilead(*argv, check=False)
        assert r.returncode == 2 and r.stdout == "", argv
        assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith("pilead: "), argv
    assert tree(env) == before


# ── --supersede ──────────────────────────────────────────────────────────────
def test_supersede_is_stored_logged_and_shown(env):
    sid = spawn(env)
    mark_reported(env, sid)
    r = pilead("close", sid, "--supersede", "succ-1")
    assert (r.stdout, r.stderr) == (f"closed {sid} (superseded by succ-1)\n", "")
    m = meta(env, sid)
    assert m["superseded_by"] == "succ-1" and m["status"] == "closed"
    assert (sdir(env, sid) / "status").read_text() == "closed\n"
    evs = events(env, sid)
    assert [e for e in evs if e["event"] == "superseded"] == [
        {"ts": evs[-1]["ts"], "event": "superseded", "session_id": sid, "superseded_by": "succ-1"}]
    assert not [e for e in evs if e["event"] == "closed"]
    # shown as `superseded` by check, list --closed and status; the stored word stays `closed`
    assert pilead("check", sid).stdout.splitlines()[0] == "superseded"
    out = pilead("list", "--closed").stdout.splitlines()
    row = [ln for ln in out if ln.startswith(sid)]
    assert len(row) == 1 and row[0].split()[1] == "superseded"
    assert "superseded" not in pilead("list").stdout  # hidden like any closed session
    assert pilead("status", sid).stdout.startswith("pkt 0001 superseded")
    js = json.loads(pilead("list", "--json").stdout)["executors"][0]
    assert (js["status"], js["superseded_by"]) == ("closed", "succ-1")
    chk = json.loads(pilead("check", sid, "--json").stdout)[0]
    assert (chk["status"], chk["superseded_by"]) == ("closed", "succ-1")
    assert (sdir(env, sid) / "status").read_text() == "closed\n"


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_supersede_value_exits_2_and_changes_nothing(env, value):
    sid = spawn(env)
    before = tree(env)
    r = pilead("close", sid, "--supersede", value, check=False)
    assert (r.returncode, r.stdout) == (2, "") and len(r.stderr.splitlines()) == 1
    assert tree(env) == before


# ── --keep-tab ───────────────────────────────────────────────────────────────
def test_keep_tab_signals_nothing_closes_no_tab_and_renames_it(env, child):
    sid = spawn(env)
    (sdir(env, sid) / "pid").write_text(f"{child.pid}\n")
    label = meta(env, sid)["label"]
    before_log = osa_log(env)
    r = pilead("close", sid, "--keep-tab")
    assert (r.stdout, r.stderr) == (f"closed {sid} (tab kept)\n",
                                    f"pilead: {sid} was busy on packet 0001; no report was written\n")
    assert child.poll() is None, "the process was signalled"
    new_log = osa_log(env)[len(before_log):]
    assert "tell s to close" not in new_log
    assert "/name" not in new_log
    assert meta(env, sid)["tab_title"] == f"[closed] {sid}"
    m = meta(env, sid)
    assert (m["kept_tab"], m["label_open"], m["label"]) == (True, label, f"[closed] {sid}")
    assert (sdir(env, sid) / "status").read_text() == "closed\n"


def test_keep_tab_with_no_known_handle_keeps_the_label(env, child):
    sid = spawn(env)
    m = meta(env, sid)
    m["tab"] = ""
    (sdir(env, sid) / "meta.json").write_text(json.dumps(m))
    before_log = osa_log(env)
    pilead("close", sid, "--keep-tab")
    m = meta(env, sid)
    assert m["kept_tab"] is True and m["label"] == m["label_open"]
    assert osa_log(env) == before_log  # the terminal was not asked at all


def test_keep_tab_combines_with_supersede(env):
    sid = spawn(env)
    mark_reported(env, sid)
    r = pilead("close", sid, "--keep-tab", "--supersede", "succ-2")
    assert r.stdout == f"closed {sid} (superseded by succ-2) (tab kept)\n"
    m = meta(env, sid)
    assert m["kept_tab"] is True and m["superseded_by"] == "succ-2"


# ── a pinned session ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("extra", [[], ["--supersede", "x"], ["--keep-tab"]])
def test_a_pinned_session_is_refused_and_byte_identical(env, child, extra):
    sid = spawn(env)
    (sdir(env, sid) / "pid").write_text(f"{child.pid}\n")
    pilead("keep", sid)
    before, before_log = tree(env), osa_log(env)
    r = pilead("close", sid, *extra, check=False)
    assert (r.returncode, r.stdout) == (2, "")
    assert len(r.stderr.splitlines()) == 1 and f"pilead keep {sid} --off" in r.stderr
    assert tree(env) == before and osa_log(env) == before_log
    assert child.poll() is None


# ── send after a close ───────────────────────────────────────────────────────
def test_send_to_a_superseded_session_exits_2_and_writes_nothing(env):
    sid = spawn(env)
    mark_reported(env, sid)
    pilead("close", sid, "--supersede", "succ-1")
    before, before_log = tree(env), osa_log(env)
    r = pilead("send", sid, str(env / "p2.md"), check=False)
    assert (r.returncode, r.stdout) == (2, "")
    assert len(r.stderr.splitlines()) == 1 and "succ-1" in r.stderr
    assert tree(env) == before and osa_log(env) == before_log


def test_send_to_a_kept_tab_session_whose_process_lives_goes_through_the_inbox(env, child):
    sid = spawn(env)
    mark_reported(env, sid)
    (sdir(env, sid) / "pid").write_text(f"{child.pid}\n")
    label = meta(env, sid)["label"]
    pilead("close", sid, "--keep-tab")
    boot = (sdir(env, sid) / "bootstrap.sh").read_text()
    before_log = osa_log(env)
    r = pilead("send", sid, str(env / "p2.md"))
    assert r.stdout.splitlines()[-1] == f"sent {sid} packet 0002" and "placement=" not in r.stdout
    assert "the second thing" in (sdir(env, sid) / "inbox.md").read_text()
    assert (sdir(env, sid) / "bootstrap.sh").read_text() == boot  # nothing was launched
    new_log = osa_log(env)[len(before_log):]
    assert "create tab" not in new_log and "split vertically" not in new_log
    assert "/name" not in new_log
    assert meta(env, sid)["tab_title"] == label
    m = meta(env, sid)
    assert "kept_tab" not in m and m["label"] == label and m["packets"] == 2
    assert (sdir(env, sid) / "status").read_text() == "busy\n"
    assert [e["via"] for e in events(env, sid) if e["event"] == "packet_sent"][-1] == "inbox"
    assert child.poll() is None


def test_send_to_a_kept_tab_session_whose_process_is_gone_relaunches_under_its_open_title(env):
    sid = spawn(env)
    mark_reported(env, sid)
    label = meta(env, sid)["label"]
    pilead("close", sid, "--keep-tab")
    before_log = osa_log(env)
    r = pilead("send", sid, str(env / "p2.md"))
    assert "placement=" in r.stdout  # no live process: relaunched, as for any closed session
    assert "create tab" in osa_log(env)[len(before_log):]
    m = meta(env, sid)
    assert "kept_tab" not in m and m["label"] == label

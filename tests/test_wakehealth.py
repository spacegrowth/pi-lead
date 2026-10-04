"""lib/pilead/wakehealth.py: a lead's wake read from its health file (wake/<sid>/health.json, written by the
extension), its record and the claim files beside its inbox. In-process; every home is under the test's temp
directory. One test per row of the table, the order of the rows, the edges of the two ages, and `waiting`."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from pilead import wakehealth  # noqa: E402

NOW = 1_800_000_000  # the `now` every state() call is given


def iso(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - ago))


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "leads").mkdir(parents=True)
    (h / "leads" / "lead-1.json").write_text(json.dumps({"sid": "lead-1", "project": "p",
                                                         "inbox": str(h / "leads" / "lead-1.inbox.md")}))
    (h / "leads" / "lead-1.inbox.md").write_text("")
    return h


def health(home, raw=None, **fields):
    d = home / "wake" / "lead-1"
    d.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (d / "health.json").write_text(raw)
        return
    h = {"sid": "lead-1", "pid": os.getpid(), "started": iso(600), "beat": iso(0), "watching": True,
         "poll_seconds": 3, "delivered": 0, "last_delivered": None, "pending": 0, "last_error": None,
         "last_error_at": None, "ended": None, **fields}
    (d / "health.json").write_text(json.dumps(h))


def claim(home, age, pid=None, seq=0, text="reported exec-a /r/a.md\n"):
    p = home / "leads" / f"lead-1.inbox.md.claim-{pid or os.getpid()}-{int((NOW - age) * 1000)}-{seq}"
    p.write_text(text)
    return p


def word(home):
    return wakehealth.state(home, "lead-1", now=NOW)[0]


# ── row 1: unknown ──────────────────────────────────────────────────────────────────────────────
def test_row1_a_health_file_that_cannot_be_read_is_unknown(home):
    (home / "wake" / "lead-1" / "health.json").mkdir(parents=True)  # exists, cannot be read as a file
    w, detail = wakehealth.state(home, "lead-1", now=NOW)
    assert w == "unknown" and detail.startswith("cannot be read")


@pytest.mark.parametrize("raw,why", [("[1]", "not a JSON object"), ("{}", "no usable beat"), ("{nope", "not JSON"),
                                     ('{"beat": "not a time"}', "no usable beat"), ('{"beat": 5}', "no usable beat"),
                                     ('{"beat": null}', "no usable beat")])
def test_row1_not_an_object_or_no_usable_beat_is_unknown(home, raw, why):
    health(home, raw=raw)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("unknown", why)


# ── row 2: off ──────────────────────────────────────────────────────────────────────────────────
def test_row2_no_health_file_is_off(home):
    assert wakehealth.state(home, "lead-1", now=NOW) == ("off", "")


def test_row2_ended_is_off(home):
    health(home, ended=iso(5))
    assert word(home) == "off"


def test_row2_auto_wake_false_is_off_with_its_detail(home):
    health(home, auto_wake=False)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("off", "auto_wake is off")


def test_row2_auto_wake_false_is_off_also_when_its_pid_is_dead_and_when_an_old_claim_is_there(home):
    health(home, auto_wake=False, pid=dead_pid())
    assert wakehealth.state(home, "lead-1", now=NOW) == ("off", "auto_wake is off")
    health(home, auto_wake=False, last_error="boom", last_error_at=iso(5))
    claim(home, 500)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("off", "auto_wake is off")


@pytest.mark.parametrize("value", [True, None, 0, "no", "false", [], {}])
def test_row2_any_auto_wake_that_is_not_false_changes_nothing(home, value):
    health(home, auto_wake=value)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("ok", "")


def test_row2_a_health_file_without_auto_wake_is_as_before(home):
    health(home)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("ok", "")


# ── row 3: stale ────────────────────────────────────────────────────────────────────────────────
def test_row3_a_dead_pid_is_stale(home):
    pid = dead_pid()
    health(home, pid=pid)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("stale", f"process {pid} is gone, last beat 0s ago")


def test_row3_a_beat_89_seconds_old_is_not_stale_and_90_is(home):
    assert wakehealth.STALE_SECONDS == 90
    health(home, beat=iso(89))
    assert word(home) == "ok"
    health(home, beat=iso(90))
    assert wakehealth.state(home, "lead-1", now=NOW) == ("stale", "last beat 1m ago")


# ── row 4: stuck ────────────────────────────────────────────────────────────────────────────────
def test_row4_a_claim_59_seconds_old_is_not_stuck_and_60_is(home):
    assert wakehealth.CLAIM_STALE_SECONDS == 60
    health(home)
    c = claim(home, 59)
    assert word(home) == "ok"
    c.unlink()
    claim(home, 60)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("stuck", "oldest claim 1m old")


def test_row4_an_error_later_than_the_last_delivery_is_stuck(home):
    health(home, last_error="send broke", last_error_at=iso(10), last_delivered=iso(20))
    assert wakehealth.state(home, "lead-1", now=NOW) == ("stuck", "send broke")
    health(home, last_error="send broke", last_error_at=iso(10), last_delivered=None)
    assert word(home) == "stuck"
    health(home, last_error="send broke", last_error_at=iso(20), last_delivered=iso(10))
    assert word(home) == "ok", "delivered since the error"
    health(home, last_error="x", last_error_at=iso(10), last_delivered=iso(10))
    assert word(home) == "ok", "at the same second is not later"


# ── row 5: ok ───────────────────────────────────────────────────────────────────────────────────
def test_row5_ok(home):
    health(home, delivered=3, last_delivered=iso(30))
    claim(home, 5)
    assert wakehealth.state(home, "lead-1", now=NOW) == ("ok", "")


# ── the order of the rows ───────────────────────────────────────────────────────────────────────
def test_order_ended_and_a_dead_pid_is_off(home):
    health(home, ended=iso(1), pid=dead_pid())
    assert word(home) == "off"


def test_order_a_dead_pid_and_an_old_claim_is_stale(home):
    health(home, pid=dead_pid())
    claim(home, 600)
    assert word(home) == "stale"


def test_order_no_usable_beat_and_ended_is_unknown(home):
    health(home, raw=json.dumps({"ended": iso(1)}))
    assert word(home) == "unknown"


def test_order_an_old_beat_and_an_error_is_stale(home):
    health(home, beat=iso(300), last_error="x", last_error_at=iso(10))
    assert word(home) == "stale"


# ── waiting ─────────────────────────────────────────────────────────────────────────────────────
def test_waiting_counts_the_inbox_and_the_claim_files(home):
    (home / "leads" / "lead-1.inbox.md").write_text("reported e1 /r/1.md\ngarbage\n\nreported e2 /r/2.md\n")
    claim(home, 5, seq=0, text="reported e3 /r/3.md\n")
    claim(home, 5, seq=1, text="reported e4 /r/4.md\nreported e5 /r/5.md\n")
    (home / "leads" / "lead-1.inbox.md.claim-x").write_text("reported odd /r/o.md\n")  # not a claim file's name
    (home / "leads" / "other.inbox.md").write_text("reported e9 /r/9.md\n")
    assert wakehealth.waiting(home, "lead-1") == 5


def test_waiting_is_none_when_the_inbox_cannot_be_read(home):
    (home / "leads" / "lead-1.inbox.md").unlink()
    (home / "leads" / "lead-1.inbox.md").mkdir()
    assert wakehealth.waiting(home, "lead-1") is None
    (home / "leads" / "lead-1.json").write_text("{broken")
    assert wakehealth.waiting(home, "lead-1") is None


def test_waiting_for_a_missing_inbox_is_zero_and_the_default_inbox_is_used(home):
    (home / "leads" / "lead-1.inbox.md").unlink()
    assert wakehealth.waiting(home, "lead-1") == 0
    (home / "leads" / "lead-1.json").write_text(json.dumps({"sid": "lead-1", "inbox": ""}))
    (home / "leads" / "lead-1.inbox.md").write_text("reported e1 /r/1.md\n")
    assert wakehealth.waiting(home, "lead-1") == 1


# ── read-only, never raises ─────────────────────────────────────────────────────────────────────
def tree(root):
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in Path(root).rglob("*")}


def test_state_and_waiting_write_nothing(home):
    cases = [lambda: None, lambda: health(home), lambda: health(home, raw="[1]"), lambda: health(home, pid=dead_pid()),
             lambda: (health(home), claim(home, 120)), lambda: health(home, ended=iso(1))]
    for make in cases:
        make()
        before = tree(home)
        wakehealth.state(home, "lead-1", now=NOW)
        wakehealth.state(home, "lead-1")
        wakehealth.waiting(home, "lead-1")
        assert tree(home) == before


def test_state_never_raises(tmp_path):
    missing = tmp_path / "no" / "such" / "home"
    assert wakehealth.state(missing, "lead-1") == ("off", "")
    assert wakehealth.waiting(missing, "lead-1") is None
    assert not missing.exists()
    for sid in ("../x", "", None, "a..b"):
        assert wakehealth.state(tmp_path, sid)[0] == "unknown"
        assert wakehealth.waiting(tmp_path, sid) is None


# ── the constants are the extension's ───────────────────────────────────────────────────────────
def ts_const(name):
    ts = (ROOT / "extensions" / "pi-lead.ts").read_text()
    m = re.search(rf"^export const {name} = ([0-9_]+);", ts, re.M)
    assert m, name
    return int(m.group(1).replace("_", ""))


def test_the_constants_equal_the_extensions():
    assert wakehealth.CLAIM_STALE_SECONDS == ts_const("CLAIM_STALE_MS") / 1000
    assert wakehealth.STALE_SECONDS == 3 * ts_const("HEALTH_BEAT_MS") / 1000

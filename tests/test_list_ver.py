"""`pilead list`'s VER column, its old-extension footnote and the CTX/TOKENS legend line; `state.version()` and
the `version` lead-start writes. In-process over homes under the test's temp directory (tests/test_list.py's
fixtures, its terminal stub included), and through a copy of the tree where `package.json` itself must change."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_list import H, footnotes, health, iso, lead, lead_rows, run, section, session, terminal  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import state  # noqa: E402

LEGEND = ("  CTX = live/window (live: the context of the last completed request on the conversation's current branch; "
          "window from pi's model list, ! = a request was larger than that window); TOKENS = prompt/output billed so "
          "far, then the cache hit rate")


def footnote(names, old, installed):
    return (f"  ⚠ old extension: {names} — its pi runs pi-lead {old}, {installed} is installed; run /reload in that "
            "pi (it loads the extension again)")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """state.ROOT points at a directory whose package.json says the version given (None: no package.json)."""
    def set_(version, raw=None):
        root = tmp_path / "installed-root"
        root.mkdir(exist_ok=True)
        p = root / "package.json"
        if raw is not None:
            p.write_text(raw)
        elif version is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(json.dumps({"name": "pi-lead", "version": version}))
        monkeypatch.setattr(state, "ROOT", root)
    return set_


def old_notes(out):
    return [n for n in footnotes(out) if n.startswith("  ⚠ old extension:")]


# ── state.version() ──────────────────────────────────────────────────────────────────────────────
def test_version_reads_package_json_each_call(installed):
    installed("0.2.0")
    assert state.version() == "0.2.0"
    installed("0.3.1")
    assert state.version() == "0.3.1"


@pytest.mark.parametrize("raw", [None, "{not json", "[1, 2]", json.dumps({"version": 3}), json.dumps({"name": "x"})])
def test_version_is_none_when_missing_or_malformed(installed, raw):
    installed(None, raw=raw)
    assert state.version() is None


def test_version_of_this_tree_is_its_package_json():
    assert state.version() == json.loads((ROOT / "package.json").read_text())["version"]


# ── lead-start writes `version`, through a copy of the tree ─────────────────────────────────────
def tree_copy(tmp_path, package_text):
    t = tmp_path / "tree"
    for d in ("bin", "lib", "vendor"):
        shutil.copytree(ROOT / d, t / d, ignore=shutil.ignore_patterns("__pycache__"))
    if package_text is not None:
        (t / "package.json").write_text(package_text)
    return t


@pytest.mark.parametrize("package_text,want", [(json.dumps({"version": "7.8.9"}), "7.8.9"), ("{nope", None),
                                               (None, None)])
def test_lead_start_writes_version(tmp_path, package_text, want):
    t = tree_copy(tmp_path, package_text)
    env = {k: v for k, v in os.environ.items() if k not in ("ITERM_SESSION_ID", "PI_SESSION_ID")}
    env["PI_LEAD_HOME"] = str(H(tmp_path))
    r = subprocess.run([sys.executable, str(t / "bin" / "pilead"), "lead-start", "lead-v", "--project", "p",
                        "--cwd", str(tmp_path)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("lead lead-v ready ")
    rec = json.loads((H(tmp_path) / "leads" / "lead-v.json").read_text())
    assert "version" in rec and rec["version"] == want
    (ev,) = [json.loads(ln) for ln in (H(tmp_path) / "ledger.jsonl").read_text().splitlines()
             if json.loads(ln)["event"] == "lead_started"]
    assert set(ev) == {"ts", "event", "session_id", "project"}


# ── the VER cell ─────────────────────────────────────────────────────────────────────────────────
def write_version(tmp_path, sid, version):
    p = H(tmp_path) / "leads" / f"{sid}.json"
    rec = json.loads(p.read_text())
    rec["version"] = version
    p.write_text(json.dumps(rec))


def test_ver_is_the_last_column_from_health_then_record_then_unknown(tmp_path, capsys, installed):
    installed("0.2.0")
    lead(tmp_path, "l-health", "ph", started=iso(86400))
    write_version(tmp_path, "l-health", "0.0.9")
    health(tmp_path, "l-health", version="0.2.0")
    lead(tmp_path, "l-record", "pr", started=iso(86400))
    write_version(tmp_path, "l-record", "0.1.5")
    lead(tmp_path, "l-none", "pn", started=iso(86400))
    health(tmp_path, "l-none", version=7)  # not a string: falls through
    lead(tmp_path, "l-bad", None, raw="{x")
    out = run(capsys, "--all-leads")
    header = section(out, "LEADS")[0].split()
    assert header[-2:] == ["WAKE", "VER"]
    r = lead_rows(out)
    assert {sid: cells[-1] for sid, cells in r.items()} == {
        "l-health": "0.2.0", "l-record": "0.1.5", "l-none": "?", "l-bad": "?"}
    d = {x["sid"]: x for x in json.loads(run(capsys, "--json"))["leads"]}
    assert (d["l-health"]["version"], d["l-record"]["version"], d["l-none"]["version"]) == ("0.2.0", "0.1.5", None)


# ── the old-extension footnote ──────────────────────────────────────────────────────────────────
def test_footnote_for_an_old_extension_with_wake_ok(tmp_path, capsys, installed):
    installed("0.2.0")
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    health(tmp_path, "l-a", version="0.1.0")
    out = run(capsys, "--all-leads")
    assert old_notes(out) == [footnote("pa", "0.1.0", "0.2.0")]
    leads = section(out, "LEADS")
    assert leads[-1] == footnote("pa", "0.1.0", "0.2.0")  # after the LEADS footnotes, before the blank line


def test_footnote_for_wake_stuck_too(tmp_path, capsys, installed):
    installed("0.2.0")
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    health(tmp_path, "l-a", version="0.1.0", last_error="send broke", last_error_at=iso(5))
    assert old_notes(run(capsys, "--all-leads")) == [footnote("pa", "0.1.0", "0.2.0")]


@pytest.mark.parametrize("fields", [{"ended": iso(10)}, {"beat": iso(3600)}])  # WAKE off, WAKE stale
def test_no_footnote_when_its_pi_is_not_running(tmp_path, capsys, installed, fields):
    installed("0.2.0")
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    health(tmp_path, "l-a", version="0.1.0", **fields)
    assert old_notes(run(capsys, "--all-leads")) == []


@pytest.mark.parametrize("theirs,ours", [("0.2.0", "0.2.0"), ("0.3.0", "0.2.0"), ("0.10.0", "0.9.0"),
                                         ("0.1.x", "0.2.0"), ("0.1.0", "0.2.x"), (None, "0.2.0"), ("0.1.0", None),
                                         ("", "0.2.0")])
def test_no_footnote_when_equal_newer_or_not_comparable(tmp_path, capsys, installed, theirs, ours):
    installed(ours)
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    health(tmp_path, "l-a", **({"version": theirs} if theirs is not None else {}))
    assert old_notes(run(capsys, "--all-leads")) == []


def test_whole_number_comparison_not_text(tmp_path, capsys, installed):
    installed("0.10.0")
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    health(tmp_path, "l-a", version="0.9.0")
    assert old_notes(run(capsys, "--all-leads")) == [footnote("pa", "0.9.0", "0.10.0")]


def test_several_old_leads_name_them_in_order_and_the_oldest_version(tmp_path, capsys, installed):
    installed("0.3.0")
    lead(tmp_path, "l-1", "p-one", started=iso(86400))
    health(tmp_path, "l-1", version="0.2.0")
    lead(tmp_path, "l-2", None, started=iso(86400))
    health(tmp_path, "l-2", version="0.1.0")
    lead(tmp_path, "l-3", "p-three", started=iso(86400))
    health(tmp_path, "l-3", version="0.3.0")
    out = run(capsys, "--all-leads")
    assert old_notes(out) == [footnote("p-one, l-2", "0.1.0", "0.3.0")]


# ── the legend line ─────────────────────────────────────────────────────────────────────────────
def test_legend_after_the_last_executor_row_and_before_every_footnote(tmp_path, capsys):
    session(tmp_path, "s-1", report=True)
    sec = section(run(capsys), "EXECUTORS")
    i = sec.index(LEGEND)
    assert i == 2  # header, the one row, then the legend
    assert all(ln.startswith("  ") for ln in sec[i + 1:])  # only footnotes follow
    assert sec.count(LEGEND) == 1


def test_no_legend_with_no_executor_row(tmp_path, capsys):
    lead(tmp_path, "l-a", "pa", started=iso(86400))
    out = run(capsys, "--all-leads")
    assert LEGEND not in out.splitlines()
    assert "(no executor sessions)" in out

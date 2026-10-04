"""The harness itself (tests/conftest.py `_pi_lead_env`): every test, in every file, runs with a recording
`osascript` and the fake `pi` first on PATH, so no test can open a real terminal tab or start the real pi.

This file deliberately defines NO `stub_osascript` fixture of its own and imports none: whatever it sees on
PATH comes from conftest alone, which is what a newly written test file gets."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PILEAD = ROOT / "bin" / "pilead"


def pilead(*args):
    return subprocess.run([sys.executable, str(PILEAD), *args], capture_output=True, text=True,
                          env=dict(os.environ))


def test_osascript_on_path_is_the_stub(tmp_path):
    assert shutil.which("osascript") == str(tmp_path / "fakebin" / "osascript")


def test_pi_on_path_is_the_fake(tmp_path):
    found = shutil.which("pi")
    assert found == str(tmp_path / "fakebin" / "pi")
    assert Path(found).resolve() == (TESTS / "fake_pi").resolve()


def test_a_spawn_in_a_file_with_no_stub_of_its_own_reaches_only_the_stub(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "packet.md").write_text("GOAL: do the thing\n")
    r = pilead("lead-start", "lead-1", "--project", "proj")
    assert r.returncode == 0, r.stderr
    r = pilead("spawn", str(tmp_path / "wt"), "topic", str(tmp_path / "packet.md"), "--model", "sonnet",
               "--lead", "lead-1")
    assert r.returncode == 0, r.stderr
    osa_log = Path(os.environ["FAKE_OSA_LOG"])
    assert osa_log == tmp_path / "osa.log"
    assert osa_log.is_file() and osa_log.read_text() != ""  # the stub was asked, so no real osascript was
    allowed = {"pi-lead-home", "wt", "packet.md", "fakebin", "pi-agent", "osa.log", "fake-pi.log"}
    assert {p.name for p in tmp_path.iterdir()} <= allowed

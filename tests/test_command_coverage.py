"""One list of commands: the extension's SKILL_NAMES, doctor's, the verbs in `--help`, and the skill directories agree."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from pilead import doctor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# claude-relay 0.5.7's twenty skills: every one has a `skills/pilead-<name>`
RELAY_SKILLS = ("auto", "board", "check", "close", "diff", "focus", "handoff", "list", "mode", "plan", "restart", "resume",
                "retire", "review", "route", "send", "spawn", "stop", "tier", "verify")
# verbs a human does not type: the extension (or `/pilead:mode`) runs them, so they have no skill and no command
NO_SKILL_VERBS = ("lead-start", "turn-end", "escalate", "status")


def help_verbs():
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "pilead"), "--help"], capture_output=True, text=True).stdout
    return set(re.findall(r"^    ([a-z][a-z-]*)\s", out, re.M))


def ts_skill_names():
    text = (ROOT / "extensions" / "pi-lead.ts").read_text()
    body = re.search(r"export const SKILL_NAMES = \[(.*?)\] as const;", text, re.S).group(1)
    body = re.sub(r"//[^\n]*", "", body)
    return re.findall(r'"([a-z][a-z-]*)"', body)


def skill_dirs():
    return {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}


def test_extension_and_doctor_list_the_same_skills_in_the_same_order():
    names = ts_skill_names()
    assert len(names) == 36 and len(set(names)) == 36
    assert tuple(names) == doctor.SKILL_NAMES


def test_commands_are_the_skills_plus_status_and_route():
    assert doctor.COMMAND_NAMES == doctor.SKILL_NAMES + ("status", "route") and len(doctor.COMMAND_NAMES) == 38
    assert doctor.SKILL_FILES == doctor.SKILL_NAMES + ("route",) and len(doctor.SKILL_FILES) == 37


def test_every_help_verb_has_a_skill_or_is_one_nobody_types():
    verbs = help_verbs()
    assert set(NO_SKILL_VERBS) <= verbs
    dirs = skill_dirs()
    for v in verbs:
        assert v in NO_SKILL_VERBS or f"pilead-{v}" in dirs, f"`pilead {v}` has no skill and is not in NO_SKILL_VERBS"


def test_every_skill_directory_is_a_verb_or_mode():
    verbs = help_verbs()
    for d in skill_dirs():
        assert d.startswith("pilead-"), d
        assert d == "pilead-mode" or d[len("pilead-"):] in verbs, f"{d} names no verb in --help"


def test_every_relay_skill_has_a_pilead_skill():
    assert len(RELAY_SKILLS) == 20
    for n in RELAY_SKILLS:
        assert (ROOT / "skills" / f"pilead-{n}" / "SKILL.md").is_file(), n


def test_the_skill_files_are_exactly_doctors_skill_files():
    assert skill_dirs() == {f"pilead-{n}" for n in doctor.SKILL_FILES}

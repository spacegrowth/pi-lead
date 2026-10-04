"""Every fenced `pilead <verb> …` command in README + skills names a verb in `bin/pilead --help`."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# pi-lead's own verbs, each with a skill of at most 40 lines (the other skills are relay's, at most 60)
OWN_VERBS = ["adopt", "auto-close", "close-predecessor", "doctor", "gate", "keep", "lineup", "lint", "notify", "prune",
             "queue", "refs", "report", "stats", "takeover", "tidy", "whoami"]
FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
CMD = re.compile(r"(?:^|[;&|]\s*|\$\(\s*)pilead\s+([a-z][a-z-]*)", re.M)


def help_verbs():
    out = subprocess.run(["python3", str(ROOT / "bin" / "pilead"), "--help"], capture_output=True, text=True).stdout
    return set(re.findall(r"^    ([a-z][a-z-]*)\s", out, re.M))


def doc_files():
    return [ROOT / "README.md", ROOT / "docs" / "manual-checklist.md", *sorted((ROOT / "skills").glob("*/SKILL.md"))]


def test_help_lists_verbs():
    assert {"spawn", "send", "check", "list", "close", "verify", "diff", "board", "lead-start"} <= help_verbs()


def test_every_documented_verb_exists():
    verbs, seen = help_verbs(), 0
    for f in doc_files():
        for block in FENCE.findall(f.read_text()):
            for verb in CMD.findall(block):
                seen += 1
                assert verb in verbs, f"{f.relative_to(ROOT)}: `pilead {verb}` is not in bin/pilead --help"
    assert seen >= 10


def test_every_skill_documents_a_pilead_command_and_has_frontmatter():
    # relay 0.5.7's twenty skills, then pi-lead's own seventeen verbs (37 skill files; `route` is a file with no command)
    names = ["mode", "spawn", "send", "check", "list", "review", "verify", "close", "diff", "board",
             "auto", "tier", "plan", "handoff", "restart", "resume", "retire", "focus", "stop", "route",
             *OWN_VERBS]
    assert len(names) == 37
    for n in names:
        text = (ROOT / "skills" / f"pilead-{n}" / "SKILL.md").read_text()
        assert text.startswith(f"---\nname: pilead-{n}\ndescription: "), n
        limit = 90 if n == "mode" else 40 if n in OWN_VERBS else 60
        assert len(text.splitlines()) <= limit, n
        assert "pilead" in text


def test_checklist_refusal_string_matches_extension():
    # The first sentence of the refusal is the documented contract; the extension may append
    # detail after it (e.g. the blocked command), so compare the sentence, not the whole string.
    sentence = "pi-lead executor: stage your work and report; the lead commits."
    assert sentence in (ROOT / "extensions" / "pi-lead.ts").read_text()
    assert f"`{sentence}`" in (ROOT / "docs" / "manual-checklist.md").read_text()


def test_every_skill_name_equals_its_directory_and_is_prefixed():
    files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert len(files) == 37
    for f in files:
        m = re.search(r"^name: (.+)$", f.read_text(), re.M)
        assert m and m.group(1) == f.parent.name and m.group(1).startswith("pilead-"), f

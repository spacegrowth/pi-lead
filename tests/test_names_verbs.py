"""Every verb asks the resolver (lib/pilead/cli.py, each verb module's RESOLVE), driven through bin/pilead with
the fake `pi` and the `osascript` stub. A verb given a lead's project or a unique 8-character prefix does
exactly what it does given the whole id: one fixture home is built through the CLI and copied twice (its own
path rewritten), the verb runs with the whole id in one copy and with the token in the other, and stdout,
stderr, the exit code and every file under home are compared after the home path and the clock are
normalised."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

from pilead import cli, names  # noqa: E402

L1 = "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d"
L2 = "4c1d0e2b-5a49-4382-8170-6f5e4d3c2b1a"
AMBIGUOUS = "4c1d0e"
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?")


def run(home, *args, stdin=None, env=None):
    e = {**os.environ, "PI_LEAD_HOME": str(home), **(env or {})}
    return subprocess.run([str(PILEAD), *args], capture_output=True, text=True, env=e, input=stdin)


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in sorted(Path(root).rglob("*"))}


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Built once, through the CLI, under the conftest stubs of the first test that asks (module-scoped
    fixtures cannot use monkeypatch, so the stubs are set here by hand)."""
    base = tmp_path_factory.mktemp("names-verbs")
    bindir = base / "fakebin"
    bindir.mkdir()
    (bindir / "pi").symlink_to(ROOT / "tests" / "fake_pi")
    from conftest import OSA_STUB
    (bindir / "osascript").write_text(OSA_STUB)
    (bindir / "osascript").chmod(0o755)
    (base / "agent").mkdir()
    env = {"PATH": f"{bindir}{os.pathsep}{ROOT / 'tests'}{os.pathsep}{os.environ['PATH']}",
           "PILEAD_NO_LINT": "1", "RELAY_NO_NOTIFY": "1", "RELAY_NO_TIDY": "1", "PILEAD_NO_PAINT": "1",
           "RELAY_TERMINAL": "iterm", "FAKE_PI_LOG": str(base / "fake-pi.log"),
           "FAKE_OSA_LOG": str(base / "osa.log"), "PYTHONPATH": str(ROOT / "tests" / "fake_iterm2"),
           "PYTHONNOUSERSITE": "1", "PI_CODING_AGENT_DIR": str(base / "agent")}
    clean = {k: v for k, v in os.environ.items()
             if k not in ("ITERM_SESSION_ID", "TERM_SESSION_ID", "PI_SESSION_ID", "FAKE_ITERM2", "PI_LEAD_SID",
                          "PI_LEAD_ROLE", "PI_LEAD_LEAD", "PI_LEAD_INBOX", "PI_CODING_AGENT_SESSION_DIR")}
    wt = base / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    (base / "packet.md").write_text("GOAL: do the thing\n")
    home = base / "tpl" / "home"

    def go(*args):
        r = subprocess.run([str(PILEAD), *args, "--home", str(home)], capture_output=True, text=True,
                           env={**clean, **env})
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    go("lead-start", L1, "--project", "proj")
    go("lead-start", L2, "--project", "other")
    sids = []
    for topic, ld in (("alpha", L1), ("beta", L1), ("gamma", L2)):
        out = go("spawn", str(wt), topic, str(base / "packet.md"), "--lead", ld, "--model", "sonnet")
        sids.append(out.split()[1])
    for sid in sids[:2]:  # reported: a follow-up packet may be sent
        d = home / "sessions" / sid
        (d / "report-0001.md").write_text("Done the thing; staged.\nStatus: clean\nRisk flags: none\n"
                                          "UNVERIFIED: none\nChanged: x\n")
        (d / "status").write_text("reported\n")
    return {"base": base, "home": home, "E1": sids[0], "E2": sids[1], "E3": sids[2], "wt": wt}


def copy_home(template, dest):
    """The template home at `dest`, every mention of the template's path rewritten."""
    src = template["home"]
    shutil.copytree(src, dest, symlinks=True)
    for p in dest.rglob("*"):
        if p.is_file() and not p.is_symlink():
            b = p.read_bytes()
            if str(src).encode() in b:
                p.write_bytes(b.replace(str(src).encode(), str(dest).encode()))
    return dest


STAMP = re.compile(r"\b(why|review)-\d{8}-\d{6}")  # the review files' names carry the clock
AGE = re.compile(r"\b\d+[smhd]\b")  # an age printed by a run a second later than the other


def norm(text, home, extra=()):
    text = text.replace(str(home), "{HOME}")
    text = ISO.sub("{TS}", text)
    text = STAMP.sub(r"\1-STAMP", text)
    text = AGE.sub("{AGE}", text)
    for pat, rep in extra:
        text = re.sub(pat, rep, text)
    return text


def norm_tree(home, extra=()):
    out = {}
    for k, v in tree(home).items():
        k = norm(k, home, extra)
        out[k] = None if v is None else norm(v.decode("utf-8", "replace"), home, extra)
    return out


def same(tmp_path, template, argv_full, argv_token, pre=(), stdin=None, env=None, extra=()):
    """Run `argv_full` in one copy and `argv_token` in another; everything the two runs leave must match."""
    results = []
    for tag, argv in (("full", argv_full), ("token", argv_token)):
        home = copy_home(template, tmp_path / tag / "home")
        for p in pre:
            r = run(home, *p, env=env)
            assert r.returncode == 0, (p, r.stderr)
        r = run(home, *argv, stdin=stdin, env=env)
        results.append((r.returncode, norm(r.stdout, home, extra), norm(r.stderr, home, extra),
                        norm_tree(home, extra)))
    (rc1, out1, err1, t1), (rc2, out2, err2, t2) = results
    assert (rc2, out2, err2) == (rc1, out1, err1)
    assert t2.keys() == t1.keys()
    for k in t1:
        assert t2[k] == t1[k], k
    return results[0]


# ── the rows marked "yes" ─────────────────────────────────────────────────────────────────────────────
# (case id, argv with {E}/{E2}/{L}/{L2} for the whole ids, the placeholders given a token, steps before)
EXEC_CASES = [
    ("send", ["send", "{E}", "{packet}"], ()),
    ("check", ["check", "{E}"], ()),
    ("status", ["status", "{E}"], ()),
    ("verify", ["verify", "{E}"], ()),
    ("diff", ["diff", "{E}"], ()),
    ("refs-accept", ["refs", "accept", "{E}"], ()),
    ("queue", ["queue", "{E}"], ()),
    ("keep", ["keep", "{E}"], ()),
    ("retire", ["retire", "{E}"], ()),
    ("resume", ["resume", "{E}", "--force"], (["close", "{E}"],)),
    ("restart", ["restart", "{E}", "--force"], ()),
    ("stop", ["stop", "{E}"], ()),
    ("escalate", ["escalate", "{E}"], ()),
    ("close", ["close", "{E}"], ()),
    ("close-supersede", ["close", "{E}", "--supersede", "{E2}"], ()),
    ("review-why", ["review", "{E}", "--why", "--lead", "{L}", "--model", "sonnet"], ()),
    ("notify", ["notify", "{L}", "--executor", "{E}", "--packet", "1"], ()),
    ("whoami-exec", ["whoami", "{E}", "--json"], ()),
]
LEAD_CASES = [
    ("close-self", ["close", "--self", "{L}"], ()),
    ("takeover", ["takeover", "{L2}", "--as", "brand-new-lead-1", "--force"], ()),
    ("spawn", ["spawn", "{wt}", "newtopic", "{packet}", "--lead", "{L}", "--model", "sonnet"], ()),
    ("list", ["list", "--lead", "{L}"], ()),
    ("list-json", ["list", "--lead", "{L}", "--json"], ()),
    ("stats", ["stats", "--lead", "{L}"], ()),
    ("prune", ["prune", "--lead", "{L}"], ()),
    ("auto-close", ["auto-close", "--lead", "{L}"], ()),
    ("tidy", ["tidy", "--lead", "{L}"], ()),
    ("auto", ["auto", "on", "--session", "{L}"], ()),
    ("tier", ["tier", "manual", "--session", "{L}"], ()),
    ("lineup", ["lineup", "--session", "{L}"], ()),
    ("plan", ["plan", "--session", "{L}"], ()),
    ("plan-add", ["plan", "add", "{packet}", "--session", "{L}"], ()),
    ("plan-bind", ["plan", "bind", "1", "{E}", "--session", "{L}"], (["plan", "add", "{packet}", "--session", "{L}"],)),
    ("gate", ["gate", "--session", "{L}"], ()),
    ("turn-end", ["turn-end", "--session", "{L}"], ()),
    ("notify-lead", ["notify", "{L}", "--test"], ()),
    ("whoami-lead", ["whoami", "{L}"], ()),
]


def fill(argv, template, subs):
    out = []
    for a in argv:
        for k, v in subs.items():
            a = a.replace("{" + k + "}", v)
        out.append(a)
    return out


def full_subs(template):
    return {"E": template["E1"], "E2": template["E2"], "E3": template["E3"], "L": L1, "L2": L2,
            "wt": str(template["wt"]), "packet": str(template["base"] / "packet.md")}


SPAWNED = [(r"newtopic-\d{6}", "newtopic-N")]


@pytest.mark.parametrize("case", EXEC_CASES, ids=[c[0] for c in EXEC_CASES])
def test_an_executor_prefix_does_what_the_whole_id_does(tmp_path, template, case):
    name, argv, pre = case
    full = full_subs(template)
    tok = {**full, "E": template["E1"][:8], "E2": template["E2"][:8], "L": "proj"}
    same(tmp_path, template, fill(argv, template, full), fill(argv, template, tok),
         pre=[fill(p, template, full) for p in pre], stdin="echo hi\n")


@pytest.mark.parametrize("token", ["project", "prefix"])
@pytest.mark.parametrize("case", LEAD_CASES, ids=[c[0] for c in LEAD_CASES])
def test_a_leads_project_or_prefix_does_what_the_whole_id_does(tmp_path, template, case, token):
    name, argv, pre = case
    full = full_subs(template)
    if token == "project":
        tok = {**full, "L": "proj", "L2": "other", "E": template["E1"][:8], "E3": template["E3"][:8]}
    else:
        tok = {**full, "L": L1[:8], "L2": L2[:8], "E": template["E1"][:8], "E3": template["E3"][:8]}
    same(tmp_path, template, fill(argv, template, full), fill(argv, template, tok),
         pre=[fill(p, template, full) for p in pre], stdin="echo hi\n", extra=SPAWNED)


@pytest.mark.parametrize("which", ["sessions", "from"])
def test_adopt_resolves_its_sessions_and_from(tmp_path, template, which):
    """`adopt` given its sessions, or --from, as tokens; the calling lead comes from $PI_LEAD_SID."""
    e3 = template["E3"]
    full, tok = ((["adopt", e3, "--force"], ["adopt", e3[:8], "--force"]) if which == "sessions"
                 else (["adopt", "--from", L2, "--force"], ["adopt", "--from", "other", "--force"]))
    rc, out, _err, _tree = same(tmp_path, template, full, tok, env={"PI_LEAD_SID": L1})
    assert rc == 0 and "adopted" in out, out


# ── the rows marked "NO" ──────────────────────────────────────────────────────────────────────────────

def test_lead_start_takes_its_id_as_typed(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    r = run(home, "lead-start", "proj", "--project", "x")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("lead proj ready")
    assert (home / "leads" / "proj.json").is_file()
    assert json.loads((home / "leads" / f"{L1}.json").read_text())["project"] == "proj"


def test_takeover_as_takes_the_new_id_as_typed(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    prefix = L1[:8]
    r = run(home, "takeover", L2, "--as", prefix, "--force")
    assert r.returncode == 0, r.stderr
    assert (home / "leads" / f"{prefix}.json").is_file()
    assert not (home / "leads" / f"{L2}.json").exists()
    assert (home / "leads" / f"{L1}.json").is_file()


def test_spawn_name_is_a_tab_label_taken_as_typed(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    label = template["E1"][:8]
    r = run(home, "spawn", str(template["wt"]), "delta", str(template["base"] / "packet.md"), "--lead", L1,
            "--model", "sonnet", "--name", label)
    assert r.returncode == 0, r.stderr
    sid = r.stdout.split()[1]
    assert json.loads((home / "sessions" / sid / "meta.json").read_text())["label"] == label


def test_restart_name_is_the_new_sessions_id_as_typed(tmp_path, template):
    """`restart --name` names a session that does not exist yet: a text that is a unique prefix of an existing
    session's id is the new id as typed, never the session it would resolve to."""
    home = copy_home(template, tmp_path / "h" / "home")
    name = template["E2"][:8]
    r = run(home, "restart", template["E1"], "--force", "--name", name)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[1] == name
    assert (home / "sessions" / name / "meta.json").is_file()


def test_send_rotate_name_is_the_new_sessions_id_as_typed(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    name = "other"  # a lead's project: not resolved here
    r = run(home, "send", template["E1"], str(template["base"] / "packet.md"), "--rotate", "--name", name)
    assert r.returncode == 0, r.stderr
    assert (home / "sessions" / name / "meta.json").is_file()


def test_plan_add_target_is_taken_as_typed(tmp_path, template):
    """`plan add --target` is never resolved: a unique prefix of an existing session is refused as a session
    that does not exist, while the whole id is accepted."""
    home = copy_home(template, tmp_path / "h" / "home")
    prefix = template["E1"][:8]
    before = tree(home)
    r = run(home, "plan", "add", str(template["base"] / "packet.md"), "--target", prefix, "--session", L1)
    assert (r.returncode, r.stdout, r.stderr) == (2, "", f"pilead: plan: session {prefix} does not exist\n")
    assert tree(home) == before
    r = run(home, "plan", "add", str(template["base"] / "packet.md"), "--target", template["E1"], "--session", L1)
    assert r.returncode == 0, r.stderr


def test_handoff_memo_is_a_path(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    before = tree(home)
    r = run(home, "handoff", "proj", env={"PI_LEAD_SID": L1})
    assert r.returncode != 0
    assert "proj" in r.stderr and L1 not in r.stderr
    assert tree(home) == before


# ── an ambiguous token ────────────────────────────────────────────────────────────────────────────────

AMBIGUOUS_ARGV = [
    ["check", AMBIGUOUS], ["send", AMBIGUOUS, "packet.md"], ["close", AMBIGUOUS], ["close", "--self", AMBIGUOUS],
    ["status", AMBIGUOUS], ["list", "--lead", AMBIGUOUS], ["spawn", "wt", "t", "p.md", "--lead", AMBIGUOUS],
    ["auto", "on", "--session", AMBIGUOUS], ["gate", "--session", AMBIGUOUS], ["plan", "--session", AMBIGUOUS],
    ["notify", AMBIGUOUS, "--test"], ["adopt", AMBIGUOUS], ["takeover", AMBIGUOUS], ["whoami", AMBIGUOUS],
    ["resume", AMBIGUOUS], ["stop", AMBIGUOUS], ["tidy", "--lead", AMBIGUOUS],
]


@pytest.mark.parametrize("argv", AMBIGUOUS_ARGV, ids=[" ".join(a[:2]) for a in AMBIGUOUS_ARGV])
def test_an_ambiguous_token_is_refused_by_every_verb(tmp_path, template, argv):
    home = copy_home(template, tmp_path / "h" / "home")
    before = tree(home)
    r = run(home, *argv, stdin="echo hi\n", env={"PI_LEAD_SID": L1})
    assert (r.returncode, r.stdout) == (2, "")
    # the message's own form is pinned whole in tests/test_names.py; here: both leads, and nothing else
    assert AGE.sub("{AGE}", r.stderr) == AGE.sub("{AGE}", f"pilead: {names.candidates_message(home, AMBIGUOUS, [L1, L2])}\n")
    assert r.stderr.count("\n") == 4 and "lead 'proj', v" in r.stderr and "lead 'other', v" in r.stderr
    assert tree(home) == before


# ── an id from the environment is never resolved ──────────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["lead", "executor"])
@pytest.mark.parametrize("args", [[], ["--statusline"]])
def test_status_with_no_argument_lists_no_directory(tmp_path, template, args, who):
    """sessions/ and leads/ made searchable but not listable (mode 0o111; mode 000 would also stop the verb
    reading the caller's own record, by its id): `pilead status` for an id from $PI_LEAD_SID prints what
    it prints with them readable. The lead is one with no executor (its status line lists sessions/ to
    name its executors: the verb's own reading, not the resolver's). The same lock does stop a prefix
    given as the argument, which needs a listing."""
    home = copy_home(template, tmp_path / "h" / "home")
    lone = "5d2e1f3a-0000-4000-8000-00000000abcd"
    assert run(home, "lead-start", lone, "--project", "lone").returncode == 0
    env = {"PI_LEAD_SID": lone if who == "lead" else template["E1"]}
    readable = run(home, "status", *args, env=env)
    by_prefix = run(home, "status", template["E1"][:8])
    assert readable.returncode == 0 and readable.stdout, readable.stderr
    assert by_prefix.returncode == 0, by_prefix.stderr
    dirs = [home / "sessions", home / "leads"]
    for d in dirs:
        d.chmod(0o111)
    try:
        locked = run(home, "status", *args, env=env)
        locked_prefix = run(home, "status", template["E1"][:8])
    finally:
        for d in dirs:
            d.chmod(0o755)
    assert (locked.returncode, locked.stdout, locked.stderr) == \
        (readable.returncode, readable.stdout, readable.stderr)
    assert (locked_prefix.returncode, locked_prefix.stdout) != (by_prefix.returncode, by_prefix.stdout)


def test_an_environment_id_that_is_a_project_is_not_resolved(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    by_env = run(home, "status", env={"PI_LEAD_SID": "proj"})
    as_typed = run(home, "status", "proj")
    assert by_env.stdout != as_typed.stdout or by_env.returncode != as_typed.returncode


# ── every verb declares RESOLVE ───────────────────────────────────────────────────────────────────────

def _dests(parser):
    import argparse
    out = set(parser._defaults)
    for act in parser._actions:
        out.add(act.dest)
        if isinstance(act, argparse._SubParsersAction):
            for sp in act.choices.values():
                out |= _dests(sp)
    return out


def test_every_verb_declares_resolve():
    for mod in cli._verb_modules():
        r = getattr(mod, "RESOLVE", None)
        assert isinstance(r, tuple) and all(isinstance(x, str) for x in r), mod.__name__
        ap, verb = cli._parser()
        mod.register(verb)
        (sub,) = [a for a in ap._actions if a.__class__.__name__ == "_SubParsersAction"]
        dests = set()
        for sp in sub.choices.values():
            dests |= _dests(sp)
        for name in r:
            assert name in dests, (mod.__name__, name)


def copy_tree(tmp_path, **modules):
    tree_ = tmp_path / "tree"
    shutil.copytree(ROOT / "bin", tree_ / "bin")
    shutil.copytree(ROOT / "lib", tree_ / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    (tree_ / "vendor").symlink_to(ROOT / "vendor")
    for name, body in modules.items():
        (tree_ / "lib" / "pilead" / "verbs" / f"zz_{name}.py").write_text(body)
    return tree_ / "bin" / "pilead"


ECHO = ("ORDER = 98\n{resolve}\n\ndef register(verb):\n"
        "    p = verb('{name}', lambda a: print('got', a.sid), 'echo')\n    p.add_argument('sid')\n")


def test_a_module_without_resolve_or_with_a_text_takes_its_arguments_as_typed(tmp_path, template):
    home = copy_home(template, tmp_path / "h" / "home")
    bin_ = copy_tree(tmp_path, none=ECHO.format(resolve="", name="zz-none"),
                     text=ECHO.format(resolve='RESOLVE = "sid"', name="zz-text"),
                     good=ECHO.format(resolve='RESOLVE = ("sid",)', name="zz-good"))
    e = {**os.environ, "PI_LEAD_HOME": str(home)}
    for verb_name, want in (("zz-none", "proj"), ("zz-text", "proj"), ("zz-good", L1)):
        r = subprocess.run([sys.executable, str(bin_), verb_name, "proj"], capture_output=True, text=True, env=e)
        assert (r.returncode, r.stdout, r.stderr) == (0, f"got {want}\n", ""), verb_name

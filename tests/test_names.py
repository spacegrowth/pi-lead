"""lib/pilead/names.py: what a human may type where a verb wants an id. Every row of the resolver's table, in
process and through bin/pilead, with homes three directories deep under the test's temp directory. The
resolver only reads: the temp directory is byte-identical after every call."""
import calendar
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILEAD = ROOT / "bin" / "pilead"
sys.path.insert(0, str(ROOT / "lib"))

from pilead import names, state  # noqa: E402
from pilead.state import PileadError  # noqa: E402

L1 = "4c1d0e2a-7b3f-4a51-9c6e-0d2f8a1b3c4d"
L2 = "9e8d7c6b-5a49-4382-8170-6f5e4d3c2b1a"
E1 = "alpha-x-20261004-101010"
E2 = "alpha-y-20261004-111111"
E3 = "aaaa11-beta-20261004-121212"


def iso(secs_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - secs_ago))


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in sorted(Path(root).rglob("*"))}


def lead(home, sid, **rec):
    (home / "leads").mkdir(parents=True, exist_ok=True)
    (home / "leads" / f"{sid}.json").write_text(json.dumps({"sid": sid, **rec}))


def executor(home, sid, **meta):
    d = home / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"sid": sid, **meta}))


@pytest.fixture
def home(tmp_path):
    """Two leads and three executors, three directories deep."""
    h = tmp_path / "a" / "b" / "c" / "home"
    lead(h, L1, project="proj", started=iso(2 * 86400 + 100), version="0.3.0")
    lead(h, L2, project="other", started=iso(3 * 86400 + 100))
    executor(h, E1, topic="alpha-x", created=iso(3600 + 50), lead=L1)
    executor(h, E2, topic="alpha-y", created=iso(7200 + 50), lead=L1)
    executor(h, E3, topic="beta", created=iso(60), lead=L2)
    return h


def resolve(home, token, **kw):
    """`names.resolve`, with the whole temp directory compared before and after."""
    root = home.parents[3]
    before = tree(root)
    try:
        return names.resolve(home, token, **kw)
    finally:
        assert tree(root) == before, "resolve wrote something"


def refusal(home, token, **kw):
    with pytest.raises(PileadError) as ei:
        resolve(home, token, **kw)
    assert ei.value.code == 2
    return str(ei.value)


def cli(home, *args, **env):
    root = home.parents[3]
    before = tree(root)
    r = subprocess.run([str(PILEAD), *args, "--home", str(home)], capture_output=True, text=True,
                       env={**os.environ, **env})
    assert tree(root) == before, "bin/pilead wrote something"
    return r


# ── the rows ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token", ["", None, 5, ["x"]])
def test_row_0_empty_or_not_a_text_is_unchanged(home, token):
    assert resolve(home, token) is token


def test_row_a_an_executors_id(home):
    assert resolve(home, E1) == E1


def test_row_b_a_leads_id(home):
    assert resolve(home, L1) == L1


def test_row_c_one_leads_project(home):
    assert resolve(home, "other") == L2
    assert resolve(home, "proj") == L1


def test_row_c_is_off_with_projects_false(home):
    assert resolve(home, "other", projects=False) == "other"


def test_row_c2_two_leads_of_one_project(home):
    lead(home, "cafe0001-lead", project="proj", started=iso(86400 + 100))
    msg = refusal(home, "proj")
    assert msg == ("ambiguous session 'proj' — matches multiple sessions:\n"
                   "  cafe0001-lead  (lead 'proj', 1d)\n"
                   f"  {L1}  (lead 'proj', v0.3.0, 2d)\n"
                   "pass the session id (or a unique prefix of it) instead")


def test_row_d_a_unique_prefix(home):
    assert resolve(home, "alpha-x") == E1
    assert resolve(home, L2[:6]) == L2
    assert resolve(home, L1[:8]) == L1


def test_row_d2_a_prefix_of_two(home):
    msg = refusal(home, "alpha-")
    assert msg == ("ambiguous session 'alpha-' — matches multiple sessions:\n"
                   f"  {E1}  (executor 'alpha-x', 1h)\n"
                   f"  {E2}  (executor 'alpha-y', 2h)\n"
                   "pass the session id (or a unique prefix of it) instead")


def test_row_e_nothing_matches(home):
    for token in ("nothing-here", "zzzzzzzz", "Alpha-x", "alpha"):
        assert resolve(home, token) == token


# ── precedence and comparison ──────────────────────────────────────────────────────────────────────────

def test_an_exact_id_wins_over_a_project_of_the_same_text(home):
    lead(home, "beef0001-lead", project=E3)
    lead(home, "beef0002-lead", project=L2)
    assert resolve(home, E3) == E3
    assert resolve(home, L2) == L2


def test_an_exact_id_wins_over_a_longer_id_it_is_a_prefix_of(home):
    executor(home, E1 + "-r2", topic="alpha-x")
    lead(home, L1 + "-r2", project="later")
    assert resolve(home, E1) == E1
    assert resolve(home, L1) == L1


def test_a_project_is_compared_as_typed(home):
    assert resolve(home, "Proj") == "Proj"
    assert resolve(home, " proj") == " proj"
    assert resolve(home, "proj ") == "proj "
    assert resolve(home, "OTHER") == "OTHER"


def test_prefix_of_five_matches_nothing_six_matches(home):
    assert resolve(home, "aaaa1") == "aaaa1"
    assert resolve(home, "9e8d7") == "9e8d7"
    assert resolve(home, "9e8d7c") == L2


def test_a_prefix_that_fits_an_executor_and_a_lead_is_ambiguous(home):
    lead(home, "aaaa1111-lead", project="lead-of-a", started=iso(86400 * 5 + 10))
    msg = refusal(home, "aaaa11")
    assert msg == ("ambiguous session 'aaaa11' — matches multiple sessions:\n"
                   f"  {E3}  (executor 'beta', 1m)\n"
                   "  aaaa1111-lead  (lead 'lead-of-a', 5d)\n"
                   "pass the session id (or a unique prefix of it) instead")


def test_candidates_dash_for_missing_fields_unreadable_times_last_then_by_id(home):
    t = iso(3600 + 30)
    lead(home, "cccc3333-l")                                # no project, no started
    executor(home, "cccc33-y-101010", topic="y", created=t)
    executor(home, "cccc33-x-101010", created=t)            # no topic
    executor(home, "cccc33-a-101010", topic="a", created="not a time")
    lead(home, "cccc33-v-lead", project="pv", version="1.2.3", started=iso(60 * 5 + 10))
    msg = refusal(home, "cccc33")
    assert msg == ("ambiguous session 'cccc33' — matches multiple sessions:\n"
                   "  cccc33-v-lead  (lead 'pv', v1.2.3, 5m)\n"
                   "  cccc33-x-101010  (executor '-', 1h)\n"
                   "  cccc33-y-101010  (executor 'y', 1h)\n"
                   "  cccc33-a-101010  (executor 'a', -)\n"
                   "  cccc3333-l  (lead '-', -)\n"
                   "pass the session id (or a unique prefix of it) instead")


def test_a_version_that_is_not_a_text_is_left_out(home):
    lead(home, "dddd4444-a", project="pp", version=3, started=iso(120))
    lead(home, "dddd4444-b", project="pp", version=None, started=iso(60 * 60 * 2))
    assert refusal(home, "pp") == ("ambiguous session 'pp' — matches multiple sessions:\n"
                                   "  dddd4444-a  (lead 'pp', 2m)\n"
                                   "  dddd4444-b  (lead 'pp', 2h)\n"
                                   "pass the session id (or a unique prefix of it) instead")


# ── tokens that must come back unchanged and read nothing outside home ─────────────────────────────────

def _plant(home, token):
    """A readable meta.json and lead record where `token` would point, where the file system allows it."""
    for p in (home / "sessions" / token / "meta.json", home / "leads" / f"{token}.json"):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"sid": token, "project": "planted", "topic": "planted"}))
        except (OSError, ValueError):
            pass


@pytest.mark.parametrize("kind", ["dotdot", "absolute", "inner-dotdot", "nul", "newline", "empty", "long"])
def test_unusable_tokens_come_back_unchanged(home, tmp_path, kind):
    token = {"dotdot": "../../x", "absolute": str(tmp_path / "abs" / "x"), "inner-dotdot": "a..b-101010",
             "nul": "abc\x00def-101010", "newline": "abc\ndef-101010", "empty": "", "long": "a" * 300}[kind]
    _plant(home, token)
    assert resolve(home, token) == token
    assert resolve(home, token, projects=False) == token


def test_unusable_tokens_through_bin_pilead(home, tmp_path):
    for token in ("../../x", str(tmp_path / "abs" / "x"), "a..b-101010", "abc\ndef-101010", "a" * 300):
        _plant(home, token)
        r = cli(home, "whoami", token)
        assert (r.returncode, r.stdout) == (1, "")
        assert r.stderr == f"pilead: whoami: could not resolve '{token}' to a known executor or lead\n"


def test_names_that_are_not_usable_ids_are_never_returned_nor_candidates(home):
    executor(home, "zzexec..x-101010", topic="bad")
    lead(home, "zzlead..y", project="badlead")
    lead(home, "-zzlead-dash", project="badlead2")
    executor(home, "zzexec-ok-101010", topic="good")
    executor(home, "zzexec.", topic="trailing-dot")
    ex, ld = names.known_ids(home)
    assert ex == {E1, E2, E3, "zzexec-ok-101010"}
    assert ld == {L1, L2}
    assert resolve(home, "zzexec") == "zzexec-ok-101010"
    assert resolve(home, "zzlead") == "zzlead"
    assert resolve(home, "-zzlea") == "-zzlea"
    assert resolve(home, "badlead") == "badlead"       # the project of an unusable record names nothing
    assert resolve(home, "zzexec..x-101010") == "zzexec..x-101010"


def test_a_dir_without_meta_json_is_not_a_known_executor(home):
    (home / "sessions" / "lonely-dir-101010").mkdir(parents=True)
    assert resolve(home, "lonely-") == "lonely-"
    assert resolve(home, "lonely-dir-101010") == "lonely-dir-101010"


# ── read errors ───────────────────────────────────────────────────────────────────────────────────────

def test_a_leads_dir_that_cannot_be_listed(home):
    d = home / "leads"
    d.chmod(0o000)
    try:
        assert resolve(home, E1) == E1             # an exact id still works
        assert resolve(home, "alpha-x") == E1      # executors are still read
        assert resolve(home, "proj") == "proj"     # nothing to go on: unchanged
        assert resolve(home, "9e8d7c") == "9e8d7c"
    finally:
        d.chmod(0o755)


def test_a_sessions_dir_that_cannot_be_listed(home):
    d = home / "sessions"
    d.chmod(0o000)
    try:
        assert resolve(home, "alpha-x") == "alpha-x"
        assert resolve(home, "other") == L2
        assert resolve(home, L1[:8]) == L1
    finally:
        d.chmod(0o755)


def test_broken_and_list_records_are_skipped(home):
    (home / "leads" / "eeee5555-broken.json").write_text("{broken")
    (home / "leads" / "eeee5555-list.json").write_text(json.dumps(["proj"]))
    (home / "sessions" / "eeee5555-badmeta").mkdir()
    (home / "sessions" / "eeee5555-badmeta" / "meta.json").write_text("{broken")
    assert resolve(home, "other") == L2              # the readable record is used
    assert resolve(home, "proj") == L1               # the list record is no second "proj"
    assert resolve(home, "eeee5555-broken") == "eeee5555-broken"   # an exact id: its file is there
    msg = refusal(home, "eeee55")                    # unreadable ones are still known ids, shown with dashes
    assert msg == ("ambiguous session 'eeee55' — matches multiple sessions:\n"
                   "  eeee5555-badmeta  (executor '-', -)\n"
                   "  eeee5555-broken  (lead '-', -)\n"
                   "  eeee5555-list  (lead '-', -)\n"
                   "pass the session id (or a unique prefix of it) instead")


def test_a_home_that_does_not_exist(tmp_path):
    h = tmp_path / "a" / "b" / "c" / "nowhere"
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    for token in ("proj", "abcdefgh", L1):
        assert resolve(h, token) == token
    assert not h.exists()


def test_a_fault_inside_the_resolver_gives_the_token(home, monkeypatch):
    def boom(_home):
        raise RuntimeError("boom")
    monkeypatch.setattr(names, "_executor_ids", boom)
    assert resolve(home, "alpha-x") == "alpha-x"
    assert resolve(home, E1) == E1


def test_resolve_writes_nothing_ledger_included(home):
    (home / "ledger.jsonl").write_text('{"event": "x"}\n')
    for token in (E1, L1, "proj", "other", "alpha-x", "nothing-here", "../../x"):
        resolve(home, token)
    for token in ("alpha-",):
        refusal(home, token)


# ── through bin/pilead ────────────────────────────────────────────────────────────────────────────────

def test_through_bin_pilead_a_project_and_a_prefix(home):
    for token in ("proj", L1[:8], L1):
        r = cli(home, "whoami", token, "--json")
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["session"] == L1


def test_through_bin_pilead_an_ambiguous_token(home):
    r = cli(home, "whoami", "alpha-")
    assert (r.returncode, r.stdout) == (2, "")
    assert r.stderr == ("pilead: ambiguous session 'alpha-' — matches multiple sessions:\n"
                        f"  {E1}  (executor 'alpha-x', 1h)\n"
                        f"  {E2}  (executor 'alpha-y', 2h)\n"
                        "pass the session id (or a unique prefix of it) instead\n")

"""vendor/relay/board_render.py `render`, on hand-made data: the p24 changes to the page (sub-header, a lead's
wake / liveness / posture chips, an executor's chips and actions, a broken lead row), escaping, and data
with keys missing or null. Nothing here reads state or runs anything."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "relay"))
import board_render  # noqa: E402


def lead(**kw):
    return {"session_id": "lead-a", "project": "pa", "liveness": "live", "wake": "ok", "auto": False,
            "tier": "auto", **kw}


def ex(**kw):
    return {"session_id": "s-1", "owner_lead": "lead-a", "status": "busy", "rendered_status": "busy",
            "topic": "t", "pkt": "0001", "packets": [], **kw}


def page(leads=(), execs=(), **kw):
    return board_render.render({"generated": "2026-10-03T10:00:00", "relay_bin": "pilead", "leads": list(leads),
                                "executors": list(execs), "warnings": [], **kw})


def rail_group(html, name):
    """The <summary> of the rail group whose name span holds `name`."""
    for m in re.finditer(r"<summary>(.*?)</summary>", html, re.S):
        if f">{name}</span>" in m.group(1):
            return m.group(1)
    raise AssertionError(f"no rail group {name!r}")


def panel(html, sid):
    m = re.search(rf'<section class="panel[^"]*" id="ex-{re.escape(sid)}">(.*?)</section>', html, re.S)
    assert m, sid
    return m.group(1)


def actions(html, sid):
    return re.findall(r'data-cmd="([^"]*)"', panel(html, sid))


def test_the_whole_page_is_one_file():
    html = page([lead()], [ex()], version="1.2.3")
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert not re.search(r'(src|href)="https?://', html) and "@import" not in html


# ── sub-header ────────────────────────────────────────────────────────────────────────────────────
def test_sub_header_names_the_version_or_a_question_mark():
    assert ("generated 2026-10-03T10:00:00 · pilead 1.2.3 · click an executor to drill in"
            in page(version="1.2.3"))
    assert "generated 2026-10-03T10:00:00 · pilead ? · click an executor to drill in" in page(version=None)
    assert "· pilead ? ·" in page()


# ── a lead in the rail ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("wake,text,bad", [("stuck", "STUCK", True), ("stale", "STALE", True), ("off", "off", False),
                                           ("unknown", "?", False), (None, "?", False)])
def test_the_wake_chip(wake, text, bad):
    g = rail_group(page([lead(wake=wake, wake_detail='last beat 5m ago <b>"')]), "pa")
    m = re.search(r'<span class="wake ([^"]*)" title="([^"]*)">([^<]*)</span>', g)
    assert m and m.group(3) == text
    assert ("bad" in m.group(1).split()) is bad
    assert m.group(2) == "last beat 5m ago &lt;b&gt;&quot;"


def test_no_wake_chip_for_ok():
    g = rail_group(page([lead(wake="ok")]), "pa")
    assert 'class="wake' not in g


@pytest.mark.parametrize("liveness,bad", [("live", False), ("unreachable", True), ("ghost", True), ("broken", True)])
def test_the_name_class(liveness, bad):
    g = rail_group(page([lead(liveness=liveness)]), "pa")
    assert ('class="gl lv-bad">pa<' in g) is bad and (('class="gl">pa<' in g) is (not bad))


def test_posture_chips():
    assert '<span class="wake">auto</span>' in rail_group(page([lead(auto=True)]), "pa")
    assert '<span class="wake">auto</span>' not in rail_group(page([lead(auto=False)]), "pa")
    for t in ("manual", "lead"):
        assert f'<span class="wake">tier {t}</span>' in rail_group(page([lead(tier=t)]), "pa")
    assert "tier auto" not in rail_group(page([lead(tier="auto")]), "pa")
    q = '<span class="wake" title="posture not readable">?</span>'
    assert q in rail_group(page([lead(auto=None, tier=None)]), "pa")
    assert q in rail_group(page([lead(auto=False, tier=None)]), "pa")
    assert q in rail_group(page([lead(auto=None, tier="manual")]), "pa")
    assert q not in rail_group(page([lead()]), "pa")


def test_a_broken_lead_row():
    html = page([lead(), {"session_id": "lead-x", "broken": True, "liveness": "broken"}],
                [ex(session_id="s-x", owner_lead="lead-x")])
    m = re.search(r'<details class="grp"><summary>(?:(?!</summary>).)*>lead-x</span>(.*?)</summary></details>', html,
                  re.S)
    assert m, "the broken lead is in the rail, with no block of executors"
    assert 'class="gl lv-bad" title="lead-x">lead-x</span>' in html and ">broken</span>" in m.group(1)
    unowned = html[html.index(">Unowned / orphaned</span>"):]
    assert 'data-target="ex-s-x"' in unowned  # its executor is shown, under Unowned / orphaned


def test_a_colour_that_is_not_three_numbers_is_the_dim_dot():
    for color in ("red", [1, 2], ["a", "b", "c"], None, {"r": 1}):
        html = page([lead(color=color)])
        assert '<span class="cdot" style="background:var(--dim)"></span>' in rail_group(html, "pa")
        assert html.rstrip().endswith("</html>")
    assert "background:rgb(10,20,30)" in rail_group(page([lead(color=[10, 20, 30])]), "pa")


# ── an executor's chips ───────────────────────────────────────────────────────────────────────────
def test_the_effort_chip():
    assert '<span class="chip">effort<b>high</b></span>' in panel(page([lead()], [ex(effort="high")]), "s-1")
    assert "effort<b>" not in panel(page([lead()], [ex(effort=None)]), "s-1")


def test_the_approaching_chip_reads_usage_live():
    p = panel(page([lead()], [ex(approaching=True, usage={"live": 125_400, "last_prompt": 9_000})]), "s-1")
    assert ">approaching heavy · 125k</span>" in p
    p = panel(page([lead()], [ex(approaching=True, usage={"live": None})]), "s-1")
    assert ">approaching heavy</span>" in p and "0k" not in p


def test_the_queued_chip():
    assert "\U0001F4E5 2 queued" in panel(page([lead()], [ex(queued=2)]), "s-1")
    assert ">queue ?</span>" in panel(page([lead()], [ex(queued=None)]), "s-1")
    p = panel(page([lead()], [ex(queued=0)]), "s-1") + panel(page([lead()], [ex()]), "s-1")
    assert "queued</span>" not in p and "queue ?" not in p


# ── an executor's actions ─────────────────────────────────────────────────────────────────────────
def test_resume_for_an_ended_session():
    for st, shown in (("closed", "closed"), ("dead", "dead"), ("closed", "superseded")):
        a = actions(page([lead()], [ex(status=st, rendered_status=shown)]), "s-1")
        assert "pilead resume s-1" in a and "pilead focus s-1" not in a and "pilead retire s-1" not in a


def test_focus_for_every_session_that_is_not_terminal_and_the_four_actions_stay():
    for st in ("busy", "idle", "stalled", "paused", "reported"):
        a = actions(page([lead()], [ex(status=st, rendered_status=st)]), "s-1")
        assert "pilead focus s-1" in a and "pilead resume s-1" not in a
        assert "pilead send s-1 &lt;packet.md&gt;" in a and "pilead close s-1" in a
    a = actions(page([lead()], [ex(status="reported")]), "s-1")
    assert a[:2] == ["pilead verify s-1", "pilead diff s-1 --open"]


def test_retire_for_a_live_heavy_session():
    assert "pilead retire s-1" in actions(page([lead()], [ex(heavy=True)]), "s-1")
    assert "pilead retire s-1" not in actions(page([lead()], [ex(heavy=False)]), "s-1")
    assert "pilead retire s-1" not in actions(page([lead()], [ex(heavy=True, status="closed",
                                                                  rendered_status="closed")]), "s-1")


# ── escaping, missing and null ────────────────────────────────────────────────────────────────────
def test_values_from_state_are_escaped():
    evil = '<script>alert("x")</script>'
    html = board_render.render({"generated": evil, "version": evil, "relay_bin": "pilead",
                                "leads": [lead(project=evil, heavy=True, heavy_reading=evil, wake="stale",
                                               wake_detail=evil)],
                                "executors": [ex(topic=evil, scope=evil, effort=evil, heavy=True,
                                                 approaching_reading=evil)],
                                "warnings": [{"level": "bad", "text": evil}]})
    assert "<script>alert" not in html
    assert html.count("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;") >= 7


def test_data_with_every_optional_key_missing_renders():
    html = board_render.render({})
    assert html.rstrip().endswith("</html>")
    html = board_render.render({"leads": [{"session_id": "l"}], "executors": [{"session_id": "s"}]})
    assert html.rstrip().endswith("</html>") and 'class="wake' not in rail_group(html, "l")


def test_data_with_every_key_null_renders():
    lkeys = ("project", "label", "model", "color", "liveness", "wake", "wake_detail", "waiting", "auto", "tier",
             "last_active", "last_active_age", "ctx", "mb", "heavy", "heavy_reading")
    ekeys = ("owner_lead", "owner_project", "status", "rendered_status", "topic", "model", "worktree", "pkt",
             "packets", "refs_moved", "refs_not_compared", "orphan", "scope", "effort", "tokens", "hit_rate",
             "ctx_cell", "ctx_contradiction", "mb", "usage", "heavy", "approaching", "heavy_reading",
             "approaching_reading", "keep", "queued", "auto_closed", "unannounced", "reported")
    data = {"generated": None, "version": None, "relay_bin": None, "home": None, "warnings": None,
            "ledger_tail": None, "leads": [{"session_id": "l", **{k: None for k in lkeys}}],
            "executors": [{"session_id": "s", **{k: None for k in ekeys}}]}
    html = board_render.render(data)
    assert html.rstrip().endswith("</html>")
    g = rail_group(html, "l")
    assert ">?</span>" in g and 'title="posture not readable"' in g
    assert ">queue ?</span>" in panel(html, "s")


# ── the live page's badge and reload (p24b) ──────────────────────────────────────────────────────
def _live(generated, seconds=10):
    return board_render.render({"generated": generated, "relay_bin": "pilead", "leads": [], "executors": [],
                                "warnings": []}, live=True, refresh_seconds=seconds)


def _ago(secs):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - secs))


def test_the_live_badge_is_red_when_the_data_is_older_than_three_refreshes_and_not_red_when_fresh():
    stale = _live(_ago(31))
    assert re.search(r'<span id="board-updated" class="upd stale"', stale)
    fresh = _live(_ago(5))
    assert re.search(r'<span id="board-updated" class="upd"', fresh) and 'class="upd stale"' not in fresh
    assert re.search(r'<span id="board-updated" class="upd stale"', _live(_ago(31), seconds=30)) is None  # 3 × 30
    assert re.search(r'<span id="board-updated" class="upd stale"', _live(_ago(91), seconds=30))


def test_a_live_page_reloads_itself_by_the_refresh_seconds():
    assert '<meta http-equiv="refresh" content="30">' in _live(_ago(1), seconds=30)


def test_without_live_the_page_has_neither_the_badge_nor_the_refresh():
    html = page()
    assert 'id="board-updated"' not in html and 'http-equiv="refresh"' not in html

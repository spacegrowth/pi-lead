"""lib/pilead/tabs.py, in-process: lead colours (TAB_PALETTE, lead_color, pick_lead_color, owner_color), the lead's tab
label, painting a terminal line (a plain file under the test's temp directory, never a path under /dev), and
repainting an executor's tab through the terminal seam (the backend's `run_osascript` and `running` replaced)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from pilead import config, tabs  # noqa: E402
from pilead.launch import backend  # noqa: E402

it = backend.by_name("iterm")
CFG = config.DEFAULTS
OFF = {**config.DEFAULTS, "tab_colors": False}
HANDLE = "w0t0p0:11111111-2222-3333-4444-555555555555"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "leads").mkdir(parents=True)
    return h


def lead(home, sid, **fields):
    p = home / "leads" / f"{sid}.json"
    p.write_text(json.dumps({"sid": sid, "project": f"p-{sid}", **fields}, indent=2) + "\n")
    return p


def same_index_pair():
    """Two lead ids whose lead_color is the same palette entry."""
    seen = {}
    for i in range(1000):
        sid = f"lead-{i}"
        idx = tabs.TAB_PALETTE.index(tuple(tabs.lead_color(sid)))
        if idx in seen:
            return seen[idx], sid
        seen[idx] = sid
    raise AssertionError("no pair")


# ── the palette ───────────────────────────────────────────────────────────────────────────────────
def test_the_palette_is_relays_six_colours_in_relays_order():
    assert tabs.TAB_PALETTE == ((200, 140, 135), (210, 172, 124), (146, 185, 146), (136, 164, 198),
                                (172, 148, 192), (132, 180, 180))


def test_lead_color_is_stable_and_a_list_from_the_palette():
    c = tabs.lead_color("lead-x")
    assert isinstance(c, list) and tuple(c) in tabs.TAB_PALETTE and c == tabs.lead_color("lead-x")


@pytest.mark.parametrize("c,ok", [([1, 2, 3], True), ((0, 255, 9), True), ([1, 2], False), ([1, 2, 300], False),
                                  ([1, 2, "x"], False), ("red", False), (None, False), ([True, 1, 2], False),
                                  ([1.0, 2, 3], False), ([-1, 2, 3], False)])
def test_usable_color(c, ok):
    assert tabs.usable_color(c) is ok


# ── pick_lead_color ───────────────────────────────────────────────────────────────────────────────
def test_a_lead_with_a_palette_colour_keeps_it(home):
    keep = list(tabs.TAB_PALETTE[4])
    lead(home, "lead-a", color=keep)
    assert tabs.pick_lead_color(home, "lead-a") == keep


def test_two_leads_whose_lead_color_is_the_same_get_different_colours(home):
    a, b = same_index_pair()
    lead(home, a, color=tabs.pick_lead_color(home, a))
    ca, cb = tabs.pick_lead_color(home, a), tabs.pick_lead_color(home, b)
    assert ca == tabs.lead_color(a) and cb != ca
    i = tabs.TAB_PALETTE.index(tuple(ca))
    assert cb == list(tabs.TAB_PALETTE[(i + 1) % 6])  # the next one, walking forward


def test_a_colour_outside_the_palette_is_picked_again(home):
    lead(home, "lead-a", color=[1, 2, 3])
    assert tabs.pick_lead_color(home, "lead-a") == tabs.lead_color("lead-a")


def test_a_lead_named_in_exclude_does_not_hold_its_colour(home):
    lead(home, "lead-old", color=tabs.lead_color("lead-new"))
    assert tabs.pick_lead_color(home, "lead-new") != tabs.lead_color("lead-new")
    assert tabs.pick_lead_color(home, "lead-new", exclude=("lead-old",)) == tabs.lead_color("lead-new")


def test_all_six_held_by_others_gives_lead_color(home):
    for i, c in enumerate(tabs.TAB_PALETTE):
        lead(home, f"other-{i}", color=list(c))
    assert tabs.pick_lead_color(home, "lead-z") == tabs.lead_color("lead-z")


def test_a_leads_directory_that_cannot_be_listed_gives_lead_color(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    (h / "leads").write_text("a file, not a directory")
    assert tabs.pick_lead_color(h, "lead-a") == tabs.lead_color("lead-a")


def test_pick_lead_color_writes_nothing(home):
    p = lead(home, "lead-a")
    before = p.read_bytes()
    tabs.pick_lead_color(home, "lead-a")
    assert p.read_bytes() == before and sorted(x.name for x in (home / "leads").iterdir()) == ["lead-a.json"]


# ── owner_color ───────────────────────────────────────────────────────────────────────────────────
def test_tab_colors_false_gives_none_and_writes_nothing(home):
    p = lead(home, "lead-a")
    before = p.read_bytes()
    assert tabs.owner_color(home, "lead-a", OFF) is None
    assert p.read_bytes() == before


@pytest.mark.parametrize("sid", ["", None, "../x", "a/b", ".hidden"])
def test_no_usable_lead_id_gives_none(home, sid):
    assert tabs.owner_color(home, sid, CFG) is None
    assert list((home / "leads").iterdir()) == []


def test_a_record_with_a_usable_colour_gives_it_and_is_untouched(home):
    p = lead(home, "lead-a", color=[1, 2, 3])
    before = p.read_bytes()
    assert tabs.owner_color(home, "lead-a", CFG) == [1, 2, 3]
    assert p.read_bytes() == before


def test_a_registered_lead_without_a_colour_gains_one_every_other_field_kept(home):
    p = lead(home, "lead-a", cwd="/x", started="2026-01-01T00:00:00Z")
    old = json.loads(p.read_text())
    c = tabs.owner_color(home, "lead-a", CFG)
    assert c == tabs.pick_lead_color(home, "lead-a") and tuple(c) in tabs.TAB_PALETTE
    assert json.loads(p.read_text()) == {**old, "color": c}
    assert tabs.owner_color(home, "lead-a", CFG) == c  # asked again: the same


@pytest.mark.parametrize("bad", ["red", [1, 2], [1, 2, 300], [1, 2, "x"]])
def test_a_colour_that_is_not_usable_counts_as_none(home, bad):
    p = lead(home, "lead-a", color=bad)
    c = tabs.owner_color(home, "lead-a", CFG)
    assert tuple(c) in tabs.TAB_PALETTE and json.loads(p.read_text())["color"] == c


def test_a_lead_with_no_record_gets_a_colour_and_no_record_is_created(home):
    assert tabs.owner_color(home, "lead-gone", CFG) == tabs.pick_lead_color(home, "lead-gone")
    assert list((home / "leads").iterdir()) == []


def test_a_record_that_cannot_be_read_gets_a_colour_and_is_not_repaired(home):
    p = home / "leads" / "lead-a.json"
    p.write_text("{not json")
    assert tabs.owner_color(home, "lead-a", CFG) == tabs.lead_color("lead-a")
    assert p.read_text() == "{not json"


# ── lead_label ────────────────────────────────────────────────────────────────────────────────────
def test_lead_label():
    assert tabs.lead_label("proj") == "[Lead] proj"
    assert tabs.lead_label("my\tproj\nx  y", "lead-1") == "[Lead] my proj x y"
    assert tabs.lead_label("  \x1b[31mred\x07 ") == "[Lead] [31mred"
    assert tabs.lead_label("x" * 200) == "[Lead] " + "x" * 60
    assert tabs.lead_label("", "lead-1234") == "[Lead] lead-1234"
    assert tabs.lead_label(None, "lead-1234") == "[Lead] lead-1234"
    assert tabs.lead_label("proj", "abcdef-1", suffix=True) == "[Lead] proj ·abcd"
    assert tabs.lead_label("proj", "abcdef-1", suffix=True) == f"[Lead] proj{it.SUCCESSOR_LABEL_MARK}abcd"


# ── paint ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def painting(monkeypatch):
    monkeypatch.delenv("PILEAD_NO_PAINT", raising=False)


def test_paint_appends_exactly_the_colour_sequence(tmp_path, painting):
    tty = tmp_path / "tty"
    tty.write_text("held before")
    assert tabs.paint(str(tty), [1, 2, 3]) is True
    assert tty.read_text() == "held before" + it.tab_color_escape([1, 2, 3])


@pytest.mark.parametrize("color", [[1, 2], "red", None, [1, 2, 300]])
def test_paint_with_a_colour_that_is_not_usable_writes_nothing(tmp_path, painting, color):
    tty = tmp_path / "tty"
    tty.write_text("x")
    assert tabs.paint(str(tty), color) is False
    assert tty.read_text() == "x"


def test_paint_with_no_tty_or_a_missing_directory_writes_nothing(tmp_path, painting):
    assert tabs.paint("", [1, 2, 3]) is False
    assert tabs.paint(None, [1, 2, 3]) is False
    missing = tmp_path / "no-such-dir" / "tty"
    assert tabs.paint(str(missing), [1, 2, 3]) is False
    assert not missing.parent.exists()


def test_the_kill_switch_writes_nothing(tmp_path):
    tty = tmp_path / "tty"  # the suite sets PILEAD_NO_PAINT for every test
    assert tabs.paint(str(tty), [1, 2, 3]) is False and not tty.exists()


# ── repaint_executor ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def seam(monkeypatch, tmp_path, painting):
    """The terminal seam: `running` answers state["running"]; `run_osascript` records each script and answers
    the plain file `tty` (or fails, with state["fail"])."""
    st = {"running": True, "fail": False, "scripts": [], "tty": tmp_path / "exec-tty"}
    st["tty"].write_text("")

    def run_osascript(script, timeout=None):
        st["scripts"].append(script)
        if st["fail"]:
            return subprocess.CompletedProcess(["osascript"], 1, "", "boom")
        return subprocess.CompletedProcess(["osascript"], 0, f"{st['tty']}\n", "")

    monkeypatch.setattr(it, "running", lambda: st["running"])
    monkeypatch.setattr(it, "run_osascript", run_osascript)
    return st


def executor(home, sid="s-1", lead_sid="lead-a", handle=HANDLE, backend_name="iterm"):
    d = home / "sessions" / sid
    d.mkdir(parents=True)
    meta = {"sid": sid, "lead": lead_sid, "label": f"[Exec] {sid}"}
    if backend_name is not None:
        meta["backend"] = backend_name
    (d / "meta.json").write_text(json.dumps(meta))
    if handle:
        (d / "iterm-id").write_text(handle + "\n")
    return meta


def test_repaint_paints_the_line_the_terminal_names(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    assert tabs.repaint_executor(home, executor(home), CFG) is True
    assert seam["tty"].read_text() == it.tab_color_escape([9, 8, 7])
    assert len(seam["scripts"]) == 1 and HANDLE.split(":")[1] in seam["scripts"][0]


def test_repaint_uses_meta_tab_when_there_is_no_iterm_id(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    meta = executor(home, handle=None)
    meta["tab"] = "w1t1p0:AAAAAAAA-0000-4000-8000-0000000000AA"
    assert tabs.repaint_executor(home, meta, CFG) is True
    assert "AAAAAAAA-0000-4000-8000-0000000000AA" in seam["scripts"][0]


def test_repaint_with_no_handle_asks_nothing(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    assert tabs.repaint_executor(home, executor(home, handle=None), CFG) is False
    assert seam["scripts"] == [] and seam["tty"].read_text() == ""


def test_repaint_with_tab_colors_false_asks_nothing(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    assert tabs.repaint_executor(home, executor(home), OFF) is False
    assert seam["scripts"] == [] and seam["tty"].read_text() == ""


def test_repaint_with_iterm_not_running_asks_nothing(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    seam["running"] = False
    assert tabs.repaint_executor(home, executor(home), CFG) is False
    assert seam["scripts"] == [] and seam["tty"].read_text() == ""


def test_repaint_with_a_stub_that_fails_writes_nothing(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    seam["fail"] = True
    assert tabs.repaint_executor(home, executor(home), CFG) is False
    assert seam["tty"].read_text() == ""


def test_repaint_of_a_session_on_another_backend_asks_nothing(home, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    assert tabs.repaint_executor(home, executor(home, backend_name="terminal"), CFG) is False
    assert seam["scripts"] == []


def test_repaint_never_raises(home, seam):
    assert tabs.repaint_executor(home, None, CFG) is False
    assert tabs.repaint_executor(home, {"sid": "../x", "lead": "lead-a"}, CFG) is False


# ── b12: tmux — paint by handle, repaint a tmux executor, groups leave tmux out, `?` is no terminal ──────
tm = backend.by_name("tmux")


@pytest.fixture
def tmux_seam(monkeypatch, painting):
    """The tmux backend's one seam replaced by a recorder (no `tmux` runs). `st["panes"]` is `#{window_panes}` per
    pane; `st["server"]` whether `list-sessions` answers."""
    st = {"calls": [], "panes": {"%4": "1", "%5": "2"}, "server": True}

    def fake(args, timeout=5):
        args = [str(a) for a in args]
        st["calls"].append(args)
        out, rc = "", 0
        if args[0] == "list-sessions":
            rc = 0 if st["server"] else 1
        elif args[0] == "display-message" and args[-1] == "#{window_panes}":
            pane = args[args.index("-t") + 1]
            out, rc = (st["panes"][pane] + "\n", 0) if pane in st["panes"] else ("", 1)
        return subprocess.CompletedProcess(["tmux"] + args, rc, out, "")
    monkeypatch.setattr(tm, "_tmux", fake)
    return st


def test_paint_tab_tmux_handle_sets_the_window_style(tmp_path, tmux_seam):
    tty = tmp_path / "tty"
    assert tabs.paint_tab("tmux:%4", str(tty), [1, 2, 3]) is True
    assert ["set-option", "-w", "-t", "%4", "window-status-style", "bg=#010203"] in tmux_seam["calls"]
    assert not tty.exists()  # a tmux handle never writes a tty


def test_paint_tab_tmux_split_pane_is_not_painted(tmux_seam):
    assert tabs.paint_tab("tmux:%5", None, [1, 2, 3]) is False
    assert not any(c[0] == "set-option" for c in tmux_seam["calls"])


def test_paint_tab_tmux_unusable_colour_or_kill_switch_asks_nothing(tmux_seam, monkeypatch):
    assert tabs.paint_tab("tmux:%4", None, "red") is False
    monkeypatch.setenv("PILEAD_NO_PAINT", "1")
    assert tabs.paint_tab("tmux:%4", None, [1, 2, 3]) is False
    assert tmux_seam["calls"] == []


def test_paint_tab_iterm_handle_writes_the_tty(tmp_path, tmux_seam):
    tty = tmp_path / "tty"
    tty.write_text("")
    assert tabs.paint_tab(HANDLE, str(tty), [1, 2, 3]) is True
    assert tty.read_text() == it.tab_color_escape([1, 2, 3]) and tmux_seam["calls"] == []


def test_repaint_a_tmux_executor_through_its_handle(home, tmux_seam, seam):
    lead(home, "lead-a", color=[9, 8, 7])
    meta = executor(home, handle="tmux:%4", backend_name="tmux")
    assert tabs.repaint_executor(home, meta, CFG) is True
    assert ["set-option", "-w", "-t", "%4", "window-status-style", "bg=#090807"] in tmux_seam["calls"]
    assert seam["scripts"] == [] and seam["tty"].read_text() == ""


def test_repaint_a_split_or_unreachable_tmux_executor_paints_nothing(home, tmux_seam):
    lead(home, "lead-a", color=[9, 8, 7])
    assert tabs.repaint_executor(home, executor(home, "s-1", handle="tmux:%5", backend_name="tmux"), CFG) is False
    tmux_seam["server"] = False
    assert tabs.repaint_executor(home, executor(home, "s-2", handle="tmux:%4", backend_name="tmux"), CFG) is False
    assert not any(c[0] == "set-option" for c in tmux_seam["calls"])


def test_groups_leave_tmux_leads_and_executors_out(home):
    from test_tidy_order import make_exec, make_lead
    make_lead(home, "lead-a", 1)
    make_exec(home, "e-1", "lead-a", 2)
    make_exec(home, "e-2", "lead-a", 3, backend_name="tmux")
    rec = {"sid": "lead-t", "project": "p-t", "iterm_handle": "tmux:%4", "terminal": None, "backend": "tmux",
           "started": "2026-10-01T08:00:00Z"}
    (home / "leads" / "lead-t.json").write_text(json.dumps(rec))
    make_exec(home, "e-3", "lead-t", 4, backend_name="tmux")
    gs = tabs.groups(home)
    assert [g["lead"] for g in gs] == ["lead-a"]
    assert [e["sid"] for e in gs[0]["executors"]] == ["e-1"]


def test_ttys_of_ignores_a_question_mark(monkeypatch):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "  11 ?\n  12 pts/3\n  13 ??\n  14 -\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert tabs.ttys_of([11, 12, 13, 14]) == {12: "pts/3"}

"""<home>/config.json: defaults, recognised keys, bad files, wrong types, and the precedence for
`executor_layout` (flag over file over default), the one key Python reads today."""
import json
import sys

import pytest

from test_cli_core import ROOT, env, home, pilead, start_lead, stub_osascript  # noqa: F401  (fixtures)

sys.path.insert(0, str(ROOT / "lib"))
from pilead import config  # noqa: E402

WIRED = {"edit_line_threshold": 40, "block_on_new_file": True, "grace_seconds": 120, "poll_interval": 3,
         "executor_layout": "tab"}


def write(h, obj_or_text):
    h.mkdir(parents=True, exist_ok=True)
    config.path(h).write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))


def test_path_is_home_config_json(tmp_path):
    assert config.path(tmp_path) == tmp_path / "config.json"


def test_wired_defaults_are_todays_values():
    assert {k: config.DEFAULTS[k] for k in WIRED} == WIRED


def test_missing_file_is_pure_defaults(tmp_path):
    assert config.load(tmp_path / "nowhere") == config.DEFAULTS


@pytest.mark.parametrize("text", ["{not json", "", "[1, 2]", '"a string"', "null", "\x00\x01"])
def test_corrupt_or_non_object_file_is_pure_defaults(tmp_path, text):
    write(tmp_path, text)
    assert config.load(tmp_path) == config.DEFAULTS


def test_unreadable_file_is_pure_defaults(tmp_path):
    config.path(tmp_path).mkdir(parents=True)  # a directory where the file belongs
    assert config.load(tmp_path) == config.DEFAULTS


def test_recognised_keys_merge_and_unknown_keys_are_ignored(tmp_path):
    write(tmp_path, {"edit_line_threshold": 40, "grace_seconds": 120, "executor_layout": "pane",
                     "terminal_app": "terminal", "no_such_key": 1})
    cfg = config.load(tmp_path)
    assert cfg["edit_line_threshold"] == 40 and cfg["grace_seconds"] == 120 and cfg["executor_layout"] == "pane"
    assert cfg["terminal_app"] == "auto" and "no_such_key" not in cfg  # "terminal" is not a pi-lead value
    assert set(cfg) == set(config.DEFAULTS)
    assert cfg["block_on_new_file"] is True and cfg["poll_interval"] == 3


@pytest.mark.parametrize("key,bad", [
    ("edit_line_threshold", "40"), ("edit_line_threshold", True), ("edit_line_threshold", None),
    ("block_on_new_file", 0), ("block_on_new_file", "false"), ("grace_seconds", [600]),
    ("executor_layout", 1), ("signoff_paths", "a/b"), ("auto_wake", {"on": True}),
    ("executor_fallback_model", 3),
])
def test_wrong_typed_value_is_ignored_for_that_key_only(tmp_path, key, bad):
    write(tmp_path, {key: bad, "handoff_nudge_mb": 9})
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key]
    assert cfg["handoff_nudge_mb"] == 9  # the rest of the file still applies


def test_same_json_type_is_accepted(tmp_path):
    write(tmp_path, {"poll_interval": 1.5, "signoff_paths": ["migrations/"], "executor_fallback_model": "haiku",
                     "usage_limit_pattern": None, "surface_commits": True})
    cfg = config.load(tmp_path)
    assert cfg["poll_interval"] == 1.5 and cfg["signoff_paths"] == ["migrations/"]
    assert cfg["executor_fallback_model"] == "haiku" and cfg["usage_limit_pattern"] is None
    assert cfg["surface_commits"] is True


def test_load_never_shares_the_default_list(tmp_path):
    config.load(tmp_path)["signoff_paths"].append("x")
    assert config.DEFAULTS["signoff_paths"] == [] and config.load(tmp_path)["signoff_paths"] == []


# ── precedence for executor_layout: flag · (no env var exists today) · config.json · default ─────
def spawn_layout(env, *flags):
    r = pilead("spawn", str(env / "wt"), "t", str(env / "packet.md"), "--model", "sonnet", "--lead", "lead-1",
               *flags)
    sid = r.stdout.split()[1]
    return json.loads((home(env) / "sessions" / sid / "meta.json").read_text())["layout"]


def test_layout_default_without_a_file(env):
    start_lead()
    assert spawn_layout(env) == "tab"


def test_layout_file_over_default(env):
    start_lead()
    write(home(env), {"executor_layout": "pane"})
    assert spawn_layout(env) == "pane"


@pytest.mark.parametrize("file_value,flag,want", [("pane", "--tab", "tab"), ("tab", "--pane", "pane")])
def test_layout_flag_over_file(env, file_value, flag, want):
    start_lead()
    write(home(env), {"executor_layout": file_value})
    assert spawn_layout(env, flag) == want


def test_layout_unknown_value_in_file_falls_back_to_default(env):
    start_lead()
    write(home(env), {"executor_layout": "window"})
    assert spawn_layout(env) == "tab"


def test_layout_helper(tmp_path):
    assert config.executor_layout(tmp_path) == "tab"
    write(tmp_path, {"executor_layout": "pane"})
    assert config.executor_layout(tmp_path) == "pane"
    assert config.executor_layout(tmp_path, "tab") == "tab"


# ── ranges for the numeric keys code reads ───────────────────────────────────────────────────────
@pytest.mark.parametrize("key,bad", [
    ("edit_line_threshold", 0), ("edit_line_threshold", -5), ("edit_line_threshold", 12.5),
    ("grace_seconds", 0.5), ("grace_seconds", 0), ("grace_seconds", -60),
    ("poll_interval", 0.5), ("poll_interval", -3),
])
def test_out_of_range_value_falls_back_to_the_default(tmp_path, key, bad):
    write(tmp_path, {key: bad, "handoff_nudge_mb": 9})
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key]
    assert cfg["handoff_nudge_mb"] == 9


@pytest.mark.parametrize("key,good", [
    ("edit_line_threshold", 1), ("edit_line_threshold", 40), ("grace_seconds", 1), ("grace_seconds", 90.5),
    ("poll_interval", 0), ("poll_interval", 1), ("poll_interval", 2.5),
])
def test_in_range_value_is_used(tmp_path, key, good):
    write(tmp_path, {key: good})
    assert config.load(tmp_path)[key] == good


def test_poll_interval_zero_is_kept_as_disabled(tmp_path):
    write(tmp_path, {"poll_interval": 0})
    assert config.load(tmp_path)["poll_interval"] == 0


@pytest.mark.parametrize("text", ['{"grace_seconds": NaN}', '{"grace_seconds": Infinity}',
                                  '{"edit_line_threshold": -Infinity}'])
def test_non_finite_numbers_fall_back(tmp_path, text):
    write(tmp_path, text)
    assert config.load(tmp_path) == config.DEFAULTS


def test_ranges_cover_only_the_keys_code_reads():
    assert set(config.RANGES) == {"edit_line_threshold", "grace_seconds", "poll_interval"}


# ── a number that cannot be evaluated costs its own key, never the whole file ────────────────────
HUGE = "9" * 400  # an integer too large for a float: math.isfinite raises OverflowError on it


@pytest.mark.parametrize("key", ["edit_line_threshold", "poll_interval"])
@pytest.mark.parametrize("digits", [400, 5000])  # 5000 also passes Python 3.11+'s int() digit limit
def test_oversized_integer_is_ignored_for_its_key_only(tmp_path, key, digits):
    write(tmp_path, '{"%s": %s, "grace_seconds": 90, "block_on_new_file": false, "executor_layout": "pane"}'
          % (key, "9" * digits))
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key]
    assert (cfg["grace_seconds"], cfg["block_on_new_file"], cfg["executor_layout"]) == (90, False, "pane")


def test_oversized_negative_integer_and_unranged_key(tmp_path):
    write(tmp_path, '{"grace_seconds": -%s, "handoff_nudge_mb": %s, "edit_line_threshold": 40}' % (HUGE, HUGE))
    cfg = config.load(tmp_path)
    assert cfg["grace_seconds"] == 120 and cfg["handoff_nudge_mb"] == 5 and cfg["edit_line_threshold"] == 40


# ── the accounting thresholds: ranged in CLI_RANGES (only the Python CLI reads them) ──────────────
ACCOUNTING = {"context_warn_tokens": 120000, "context_nudge_tokens": 150000, "handoff_nudge_mb": 5}


def test_accounting_defaults_are_unchanged():
    assert {k: config.DEFAULTS[k] for k in ACCOUNTING} == ACCOUNTING


def test_range_dicts_share_no_key_and_name_only_defaults():
    assert not set(config.RANGES) & set(config.CLI_RANGES)
    assert set(config.RANGES) | set(config.CLI_RANGES) <= set(config.DEFAULTS)
    assert set(config.CLI_RANGES) == set(ACCOUNTING) | {"stall_threshold_seconds", "lead_nudge_tokens", "auto_close_idle_minutes", "board_refresh_seconds"}


@pytest.mark.parametrize("key,bad", [
    ("context_warn_tokens", 0), ("context_warn_tokens", -120000), ("context_warn_tokens", 1200.5),
    ("context_warn_tokens", "120000"), ("context_warn_tokens", True), ("context_warn_tokens", None),
    ("context_nudge_tokens", 0), ("context_nudge_tokens", -1), ("context_nudge_tokens", 150000.25),
    ("context_nudge_tokens", [150000]), ("context_nudge_tokens", False),
    ("handoff_nudge_mb", 0), ("handoff_nudge_mb", -5), ("handoff_nudge_mb", "5"), ("handoff_nudge_mb", None),
    ("handoff_nudge_mb", {"mb": 5}),
])
def test_accounting_key_out_of_range_or_wrong_type_falls_back(tmp_path, key, bad):
    write(tmp_path, {key: bad, "grace_seconds": 90})
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key]
    assert cfg["grace_seconds"] == 90  # the rest of the file still applies
    assert config.thresholds(tmp_path) == (cfg["context_warn_tokens"], cfg["context_nudge_tokens"],
                                           cfg["handoff_nudge_mb"])


@pytest.mark.parametrize("key,good", [
    ("context_warn_tokens", 1), ("context_warn_tokens", 90000), ("context_warn_tokens", 90000.0),
    ("context_nudge_tokens", 1), ("context_nudge_tokens", 400000),
    ("handoff_nudge_mb", 0.5), ("handoff_nudge_mb", 12),
])
def test_accounting_key_in_range_is_used(tmp_path, key, good):
    write(tmp_path, {key: good})
    assert config.load(tmp_path)[key] == good


def test_thresholds_defaults(tmp_path):
    assert config.thresholds(tmp_path / "nowhere") == (120000, 150000, 5)


@pytest.mark.parametrize("warn,nudge", [(150000, 150000), (200000, 150000)])
def test_warn_at_or_above_nudge_marks_nothing_approaching(tmp_path, warn, nudge):
    sys.path.insert(0, str(ROOT / "lib"))
    from pilead import usage
    write(tmp_path, {"context_warn_tokens": warn, "context_nudge_tokens": nudge})
    w, n, mb = config.thresholds(tmp_path)
    assert (w, n) == (warn, nudge)  # accepted as set: not an error
    for live in (1, 120000, 149999, 150000, 199999, 200000, 10 ** 7):
        assert usage.is_approaching({"live": live}, w, n) is False
    assert usage.is_heavy({"live": 150000}, None, n, mb) is True


# ── the status/list keys: stall_threshold_seconds, lead_nudge_tokens (CLI_RANGES), usage_limit_pattern ──
def test_status_and_list_defaults():
    assert config.DEFAULTS["stall_threshold_seconds"] == 2700 and config.DEFAULTS["lead_nudge_tokens"] == 300000
    assert config.DEFAULTS["usage_limit_pattern"] == config.USAGE_LIMIT_PATTERN


@pytest.mark.parametrize("key,bad", [
    ("stall_threshold_seconds", 59), ("stall_threshold_seconds", 0), ("stall_threshold_seconds", -2700),
    ("stall_threshold_seconds", "2700"), ("stall_threshold_seconds", True), ("stall_threshold_seconds", None),
    ("lead_nudge_tokens", 0), ("lead_nudge_tokens", -1), ("lead_nudge_tokens", 300000.5),
    ("lead_nudge_tokens", "300000"), ("lead_nudge_tokens", [1]), ("lead_nudge_tokens", None),
])
def test_status_keys_out_of_range_or_wrong_type_fall_back(tmp_path, key, bad):
    write(tmp_path, {key: bad})
    assert config.load(tmp_path)[key] == config.DEFAULTS[key]


@pytest.mark.parametrize("key,good", [
    ("stall_threshold_seconds", 60), ("stall_threshold_seconds", 90.5), ("stall_threshold_seconds", 86400),
    ("lead_nudge_tokens", 1), ("lead_nudge_tokens", 250000), ("lead_nudge_tokens", 400000.0),
])
def test_status_keys_in_range_are_used(tmp_path, key, good):
    write(tmp_path, {key: good})
    assert config.load(tmp_path)[key] == good


def test_the_default_usage_limit_pattern_compiles_and_matches_the_confirmed_wording():
    import re
    rx = re.compile(config.USAGE_LIMIT_PATTERN, re.I)
    assert rx.search("You're out of extra usage · resets 4pm (Europe/London)")
    for cited in ("Monthly usage limit reached", "insufficient_quota", "Quota exceeded for this key"):
        assert rx.search(cited), cited
    for other in ("overloaded_error", "rate limit", "synthetic failure", "context length exceeded"):
        assert not rx.search(other), other


def test_usage_limit_pattern_null_loads_as_none(tmp_path):
    write(tmp_path, {"usage_limit_pattern": None})
    assert config.load(tmp_path)["usage_limit_pattern"] is None  # null = the built-in pattern where it is read


def test_usage_limit_pattern_string_is_used_as_given(tmp_path):
    write(tmp_path, {"usage_limit_pattern": "credit balance is too low"})
    assert config.load(tmp_path)["usage_limit_pattern"] == "credit balance is too low"


@pytest.mark.parametrize("bad", ["(unclosed", "[a-", 42, ["out of extra usage"], True, {"p": "x"}])
def test_usage_limit_pattern_invalid_or_wrong_typed_falls_back(tmp_path, bad):
    write(tmp_path, {"usage_limit_pattern": bad, "handoff_nudge_mb": 9})
    cfg = config.load(tmp_path)
    assert cfg["usage_limit_pattern"] == config.USAGE_LIMIT_PATTERN and cfg["handoff_nudge_mb"] == 9


def test_only_usage_limit_pattern_takes_null():
    assert config.NULLABLE == {"usage_limit_pattern"}


# ── the executor model and effort keys `pilead spawn` reads ─────────────────────────────────────
def test_executor_keys_defaults(tmp_path):
    assert config.executor_models(tmp_path) == ("sonnet", None, "opus")
    assert config.executor_default_effort(tmp_path) == "high"


@pytest.mark.parametrize("key,good", [
    ("executor_default_model", "opus"), ("executor_default_model", "deepseek/deepseek-v4-pro"),
    ("executor_fallback_model", "haiku"), ("executor_fallback_model", "anthropic/claude-haiku-4-5"),
    ("executor_model_ceiling", "fable"), ("executor_model_ceiling", "haiku"),
])
def test_executor_model_keys_usable_value(tmp_path, key, good):
    write(tmp_path, {key: good})
    got = dict(zip(("executor_default_model", "executor_fallback_model", "executor_model_ceiling"),
                   config.executor_models(tmp_path)))
    assert got[key] == good


@pytest.mark.parametrize("key,bad", [
    ("executor_default_model", 5), ("executor_default_model", None), ("executor_default_model", ["opus"]),
    ("executor_fallback_model", 3), ("executor_fallback_model", True),
    ("executor_model_ceiling", 2), ("executor_model_ceiling", None),
    ("executor_default_effort", 7), ("executor_default_effort", None), ("executor_default_effort", ["high"]),
])
def test_executor_keys_wrong_type_is_ignored(tmp_path, key, bad):
    write(tmp_path, {key: bad, "handoff_nudge_mb": 9})
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key] and cfg["handoff_nudge_mb"] == 9
    assert config.executor_models(tmp_path) == ("sonnet", None, "opus")
    assert config.executor_default_effort(tmp_path) == "high"


def test_executor_default_effort_usable_and_unusable(tmp_path):
    for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
        write(tmp_path, {"executor_default_effort": level})
        assert config.executor_default_effort(tmp_path) == level
    write(tmp_path, {"executor_default_effort": " Medium "})
    assert config.executor_default_effort(tmp_path) == "medium"
    for word in ("ultra", "", "think hard"):  # text, but not a level: the default applies
        write(tmp_path, {"executor_default_effort": word})
        assert config.executor_default_effort(tmp_path) == "high"


def test_executor_model_ceiling_unusable_word_is_kept(tmp_path):
    """A ceiling that is text but not a tier word is kept as it is; spawn then puts every `anthropic`
    model above it (lib/pilead/models.py `above_ceiling`)."""
    write(tmp_path, {"executor_model_ceiling": "gpt"})
    assert config.executor_models(tmp_path)[2] == "gpt"
    from pilead import models
    assert models.above_ceiling("anthropic/claude-haiku-4-5", "gpt")
    assert not models.above_ceiling("deepseek/deepseek-v4-pro", "gpt")


def test_executor_model_unusable_words_are_kept_for_spawn_to_refuse(tmp_path):
    """An alias that is not one is kept by config; `pilead spawn` turns it into today's unknown-alias
    error (tests/test_spawn_options.py)."""
    write(tmp_path, {"executor_default_model": "gpt5", "executor_fallback_model": "nope"})
    assert config.executor_models(tmp_path)[:2] == ("gpt5", "nope")


# ── the auto-close keys: auto_close (boolean), auto_close_idle_minutes (CLI_RANGES, ≥ 0) ─────────────
def test_auto_close_defaults():
    assert config.DEFAULTS["auto_close"] is True and config.DEFAULTS["auto_close_idle_minutes"] == 60
    assert "auto_close_idle_minutes" in config.CLI_RANGES and "auto_close_idle_minutes" not in config.RANGES


@pytest.mark.parametrize("key,good", [
    ("auto_close", False), ("auto_close", True),
    ("auto_close_idle_minutes", 0), ("auto_close_idle_minutes", 0.0), ("auto_close_idle_minutes", 1),
    ("auto_close_idle_minutes", 7.5), ("auto_close_idle_minutes", 1440),
])
def test_auto_close_keys_usable_values_are_used(tmp_path, key, good):
    write(tmp_path, {key: good})
    assert config.load(tmp_path)[key] == good


@pytest.mark.parametrize("key,bad", [
    ("auto_close", "false"), ("auto_close", 0), ("auto_close", None), ("auto_close", [True]),
    ("auto_close_idle_minutes", -1), ("auto_close_idle_minutes", -0.5), ("auto_close_idle_minutes", "60"),
    ("auto_close_idle_minutes", True), ("auto_close_idle_minutes", None), ("auto_close_idle_minutes", {"m": 5}),
])
def test_auto_close_keys_wrong_type_or_negative_fall_back(tmp_path, key, bad):
    write(tmp_path, {key: bad, "grace_seconds": 90})
    cfg = config.load(tmp_path)
    assert cfg[key] == config.DEFAULTS[key]
    assert cfg["grace_seconds"] == 90  # the rest of the file still applies

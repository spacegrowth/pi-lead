"""pilead notify — post one desktop banner (lib/pilead/notify.py).

    pilead notify <lead> --executor S --packet N     the lead's report banner (the lead's own extension runs it)
    pilead notify --no-lead --executor S --packet N  the report banner of an executor whose lead is not listening
    pilead notify <lead> --test                      a test banner: proves the lead's banners work

One stdout line, exit code 0 whatever the outcome: `banner posted via <tier>`, or `banner not posted (<why>)`. A
banner that is switched off (the kill switch, or a config key) never uses up its claim; every other one is
announced once (`notify.claim`)."""
import json

from .. import config, notify, state, wakehealth
from ..state import PileadError

ORDER = 82
RESOLVE = ("lead_sid", "executor")

TEST_MESSAGE = "If you can read this, banners work."


def _posted(tier):
    print(f"banner posted via {tier}" if tier else "banner not posted (osascript failed)")


def _skip(why):
    print(f"banner not posted ({why})")


def _usage_error(msg):
    raise PileadError(f"notify: {msg}")


def _executor_lead(home, sid):
    """(lead sid or None, its record or None) from the executor's meta.json; an executor with no readable record
    or no usable lead id has neither."""
    try:
        meta = json.loads((state.session_dir(home, sid) / "meta.json").read_text())
        lead = meta.get("lead") if isinstance(meta, dict) else None
    except Exception:  # noqa: BLE001
        return None, None
    if not state.valid_lead_sid(lead):
        return None, None
    return lead, notify.lead_record(home, lead)


def _no_lead(home, cfg, sid, n):
    lead, rec = _executor_lead(home, sid)
    if not cfg["notify_on_wake"]:
        return _skip("notify_on_wake is off")
    if not cfg["executor_escalation"]:
        return _skip("executor_escalation is off")
    if rec is None:
        message = f"no lead is registered for it — pilead check {sid}"
    else:
        word, _detail = wakehealth.state(home, lead)
        if word in ("ok", "stuck"):
            return _skip("lead is listening")
        message = f"its lead is not listening ({word}) — the report waits in the inbox"
    if not notify.claim(home, lead or notify.NO_LEAD, f"{sid}.no-lead-{n}"):
        return _skip("already announced")
    _posted(notify.banner(home, cfg, "pi-lead — report ready", f"{sid} reported", message,
                          lead_rec=rec, lead_sid=lead))


def _for_lead(home, cfg, rec, lead_sid, sid, n):
    if not cfg["notify_on_wake"]:
        return _skip("notify_on_wake is off")
    if not notify.claim(home, lead_sid, f"{sid}.report-{n}"):
        return _skip("already announced")
    message = notify.report_brief(home, sid, n) or "report ready"
    _posted(notify.banner(home, cfg, notify.project_title(rec, lead_sid), f"{sid} reported", message,
                          lead_rec=rec, lead_sid=lead_sid))


def cmd_notify(a):
    # Every id is checked before anything is read or written.
    if a.no_lead:
        if a.lead_sid is not None:
            _usage_error("--no-lead takes no lead id")
        if a.test:
            _usage_error("--no-lead cannot be combined with --test")
    elif a.lead_sid is None:
        _usage_error("give a lead id, or --no-lead")
    if a.test:
        if a.executor is not None or a.packet is not None:
            _usage_error("--test takes no --executor or --packet")
    elif a.executor is None or a.packet is None:
        _usage_error("--executor SID and --packet N are needed (or --test)")
    if a.lead_sid is not None:
        state.check_lead_sid(a.lead_sid)
    if a.executor is not None:
        state.check_session_sid(a.executor)
    if a.packet is not None and a.packet < 1:
        _usage_error("--packet must be 1 or more")
    home = state.home_dir(a.home)
    rec = None
    if a.lead_sid is not None:
        rec = notify.lead_record(home, a.lead_sid)
        if rec is None:
            _usage_error(f"{a.lead_sid} is not a registered lead")
    if notify.kill_switch():
        return _skip("kill switch")
    cfg = config.load(home)
    if a.test:
        _posted(notify.banner(home, cfg, notify.project_title(rec, a.lead_sid), "test banner", TEST_MESSAGE,
                              lead_rec=rec, lead_sid=a.lead_sid))
    elif a.no_lead:
        _no_lead(home, cfg, a.executor, a.packet)
    else:
        _for_lead(home, cfg, rec, a.lead_sid, a.executor, a.packet)
    return 0


def register(verb):
    p = verb("notify", cmd_notify, "post a desktop banner: a report is ready, or --test")
    p.add_argument("lead_sid", nargs="?", help="the lead whose banner it is (not with --no-lead)")
    p.add_argument("--executor", metavar="SID", help="the executor that reported")
    p.add_argument("--packet", type=int, metavar="N", help="the packet number its report answers")
    p.add_argument("--no-lead", action="store_true",
                   help="the banner of an executor whose lead is not listening (or has no record)")
    p.add_argument("--test", action="store_true", help="post a test banner (ignores notify_on_wake)")

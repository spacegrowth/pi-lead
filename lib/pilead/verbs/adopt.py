"""pilead adopt — make the calling lead the owner of executor sessions (lib/pilead/ownership.py), so their
reports wake it. `pilead adopt <sid> [<sid> …] [--force]` or `pilead adopt --from LEAD_SID [--force]`.

A session whose owner is gone (`orphan`), that has none (`unowned`), or whose owner's LIVE is `ghost` is
adopted; one owned by a lead whose LIVE is `live`, `unreachable` or `broken`, or whose owner could not be read
(`unknown`), is refused unless `--force`. One line per session on stdout; exit code 0 when every session was
adopted or already the caller's, 1 when one was refused or unknown (the others are adopted either way), 2 for a
refusal of the command itself (nothing changed). `claim` is the same judgement made by `send`, `resume` and
`restart`, which adopt what they may and warn about the rest (a queued delivery never adopts)."""
import json
import os
import sys
from pathlib import Path

from .. import config, lifecycle, ownership, state, tabs
from ..state import PileadError

ORDER = 34
RESOLVE = ("sids", "from_lead")
TERMINAL = ("closed", "dead")
SUFFIX = {"orphan": " (no longer a registered lead)", "unowned": " (unowned)", "ghost": " (ghost lead)"}


def _caller(home):
    """The calling lead (lifecycle.caller), or None — also for an executor's own pi session."""
    if os.environ.get("PI_LEAD_ROLE") == "executor":
        return None
    return lifecycle.caller(home)


class _Leads:
    """Every lead's LIVE value (verbs/list.py `read_leads`, no cache written), read on first use."""

    def __init__(self, home):
        self.home, self._by_sid = home, None

    def live(self, sid):
        if self._by_sid is None:
            from . import list as list_verb  # at run time: a list module that fails to load never takes adopt down
            self._by_sid = {ld["sid"]: ld["live"] for ld in
                            list_verb.read_leads(self.home, config.load(self.home), write_cache=False)}
        return self._by_sid.get(sid)


def judge(home, meta, caller, leads):
    """What may be done with a session whose record is `meta`, for the lead `caller`:
    ("mine", owner, None) — it is the caller's already; ("adopt", owner, why) — `why` is `orphan`, `unowned` or
    `ghost`; ("refuse", owner, live) — `live` is the owner's LIVE value, None when the owner is `unknown`."""
    o = ownership.owner(home, meta)
    if meta.get("lead") == caller or (o["state"] == "owned" and o["lead"] == caller):
        return "mine", o, None
    if o["state"] in ("orphan", "unowned"):
        return "adopt", o, o["state"]
    if o["state"] == "owned":
        live = leads.live(o["lead"])
        if live == "ghost":
            return "adopt", o, "ghost"
        if live is None:  # the record vanished between the two reads: not known
            o = {**o, "state": "unknown", "why": f"the LIVE value of lead {o['lead']} could not be read"}
        return "refuse", o, live
    return "refuse", o, None


def adopted_line(sid, o, why=None, forced=False):
    """`adopted <sid> from <old owner, else ->` and its ending."""
    return f"adopted {sid} from {o['lead'] or '-'}" + (" (forced)" if forced else SUFFIX[why])


def refused_line(home, sid, o, live):
    if o["state"] == "unknown":
        return f"refused {sid}: owner unknown ({o['why']}) — pilead adopt {sid} --force takes it"
    return (f"refused {sid}: owned by {o['lead']} ({lifecycle.lead_project(home, o['lead']) or '-'}), LIVE {live} — "
            f"its reports wake that lead; pilead adopt {sid} --force takes it")


def claim(home, sid, meta, out=print, err=None):
    """Adoption when a lead claims a session (`send`, `resume <executor>`, `restart`), after the verb's own
    refusals and before it writes anything else. With no caller, or a session the caller owns: nothing. An
    orphan, unowned or ghost-owned session is adopted (reason `claim`) with one line on stdout, and `meta` gets
    the new `lead` in place; any other gets one line on stderr and is not adopted. Returns `meta`."""
    err = err or (lambda line: print(line, file=sys.stderr))
    caller = _caller(home)
    if caller is None or meta.get("lead") == caller:
        return meta
    action, o, why = judge(home, meta, caller, _Leads(home))
    if action == "mine":
        return meta
    if action == "adopt":
        ownership.adopt(home, sid, caller, "claim")
        meta["lead"] = caller
        out(adopted_line(sid, o, why))
        return meta
    if o["state"] == "unknown":
        err(f"pilead: {sid} is owned by {meta.get('lead')} (owner unknown: {o['why']}) — its report will wake that "
            f"lead, not you; pilead adopt {sid} --force takes it")
    else:
        err(f"pilead: {sid} is owned by {o['lead']} ({lifecycle.lead_project(home, o['lead']) or '-'}) — its report "
            f"will wake that lead, not you; pilead adopt {sid} --force takes it")
    return meta


def _sessions_of(home, lead_sid):
    """Every session whose meta.json names `lead_sid` (terminal ones included), in sid order."""
    d = Path(home) / "sessions"
    out = []
    for mp in sorted(d.glob("*/meta.json")) if d.is_dir() else []:
        sid = mp.parent.name
        if not state.valid_session_sid(sid):
            continue
        try:
            m = json.loads(mp.read_text())
        except Exception:  # noqa: BLE001 — a record that cannot be read names nobody
            continue
        if isinstance(m, dict) and m.get("lead") == lead_sid:
            out.append(sid)
    return out


def cmd_adopt(a):
    home = state.home_dir(a.home)
    if os.environ.get("PI_LEAD_ROLE") == "executor":
        raise PileadError("adopt: an executor cannot adopt sessions; only a lead can. Nothing was adopted.")
    caller = _caller(home)
    if caller is None:
        raise PileadError("adopt: no calling lead — run pilead lead-start \"$PI_SESSION_ID\" --project <name> in "
                          "your lead session first ($PI_LEAD_SID or $PI_SESSION_ID must name a registered lead). "
                          "Nothing was adopted.")
    if bool(a.sids) == (a.from_lead is not None):
        raise PileadError("adopt: give session ids, or --from LEAD_SID, not both and not neither. Nothing was adopted.")
    for sid in a.sids:
        state.check_session_sid(sid)
    if a.from_lead is not None:
        state.check_lead_sid(a.from_lead)
        if a.from_lead == caller:
            raise PileadError(f"adopt: --from {caller} is you; your own sessions are yours already. Nothing was "
                              "adopted.")
        sids = _sessions_of(home, a.from_lead)
        if not sids:
            print(f"no session names {a.from_lead} as its lead")
            return 0
    else:
        sids = list(dict.fromkeys(a.sids))
    leads = _Leads(home)
    code = 0
    changed = False
    for sid in sids:
        mp = state.session_dir(home, sid) / "meta.json"
        if not mp.is_file():
            print(f"unknown session {sid}")
            code = 1
            continue
        try:
            meta = json.loads(mp.read_text())
            if not isinstance(meta, dict):
                raise ValueError("not an object")
        except Exception:  # noqa: BLE001
            print(f"refused {sid}: owner unknown ({mp} cannot be read) — inspect that file")
            code = 1
            continue
        action, o, why = judge(home, meta, caller, leads)
        if action == "mine":
            print(f"{sid} is already yours")
        elif action == "adopt":
            ownership.adopt(home, sid, caller, "adopt")
            print(adopted_line(sid, o, why))
            changed = True
        elif a.force:
            ownership.adopt(home, sid, caller, "adopt", forced=True)
            print(adopted_line(sid, o, forced=True))
            changed = True
        else:
            print(refused_line(home, sid, o, why))
            code = 1
    if changed:
        tabs.tidy_after(home, "adopt")  # an owner changed; cosmetic, last
    return code


def register(verb):
    p = verb("adopt", cmd_adopt, "make the calling lead the owner of executor sessions, so their reports wake it")
    p.add_argument("sids", nargs="*", metavar="SID", help="the sessions to adopt")
    p.add_argument("--from", dest="from_lead", metavar="LEAD_SID",
                   help="every session whose meta.json names this lead (closed and dead ones included)")
    p.add_argument("--force", action="store_true",
                   help="adopt also a session owned by a lead that is live, unreachable or cannot be read")

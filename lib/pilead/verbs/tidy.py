"""pilead tidy — put every lead's tabs in order: the lead's tab, then the executors it owns (claude-relay 0.5.4's
`cmd_tidy`), each group painted in its lead's colour.

The order is worked out from state alone (lib/pilead/tabs.py `groups`); the tabs are moved through iTerm2's Python
API (`iterm.reorder_tabs`), in their own window: no tab is moved to another window, and a tab pi-lead does not
manage is never moved, closed, opened or named. `--dry-run` asks what would move and changes nothing. `--quiet` is
how a verb's automatic tidy runs this (tabs.maybe_tidy): nothing on success, one line (the reason) otherwise.
`--lead SID` names who asked, for the ledger event only: every lead's group is ordered whoever asks.

Unlike relay, a tidy that could not be applied exits 1."""
from .. import config, lifecycle, state, tabs

ORDER = 36
RESOLVE = ("lead",)


def _listing(gs):
    for g in gs:
        color = g.get("color")
        note = ",".join(str(v) for v in color) if tabs.usable_color(color) else "-"
        print(f"[Lead] {g['project'] or '-'} ({g['lead'][:8]}…) colour {note}")
        for i, e in enumerate(g["executors"], 1):
            print(f"  {i}. {e['label']}")
        for sid in g["elsewhere"]:
            print(f"  · {sid} — in another window, left where it is")


def cmd_tidy(a):
    if a.lead is not None:
        state.check_lead_sid(a.lead)  # first: nothing is asked of the terminal for an unusable id
    home = state.home_dir(a.home)
    if a.quiet and a.dry_run:
        return 0  # nothing is moved or painted, and nothing is printed
    gs = tabs.groups(home)
    if not gs:
        if a.quiet:
            print(tabs.NO_GROUP)
            return 1
        print(f"tidy: {tabs.NO_GROUP} — nothing to order")
        return 0
    if not a.quiet:
        _listing(gs)
    cfg = config.load(home)
    lead = a.lead if a.lead is not None else lifecycle.caller(home)
    if a.dry_run:
        ok, reason, _ = tabs.tidy_now(home, cfg, dry_run=True, lead=lead)
        m = tabs._MOVED_RE.search(reason or "") if ok else None
        if m:
            print(f"dry run — {m.group(1)} tab(s) would be moved ({reason}); nothing moved")
            return 0
        print(f"dry run — the tab order could not be read ({reason}); nothing moved")
        return 1
    ok, reason, _ = tabs.tidy_now(home, cfg, lead=lead)
    if a.quiet:
        if ok:
            return 0
        print(reason)
        return 1
    print(f"tidy: {reason}" if ok else f"tidy: not applied — {reason}")
    return 0 if ok else 1


def register(verb):
    p = verb("tidy", cmd_tidy, "put every lead's tabs in order: the lead's tab, then its executors")
    p.add_argument("--dry-run", action="store_true", help="show the order and what would move; change nothing")
    p.add_argument("--quiet", action="store_true", help="print nothing on success, the reason otherwise")
    p.add_argument("--lead", metavar="SID", help="the lead that asks (for the ledger event; default the caller)")

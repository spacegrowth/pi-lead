"""pilead queue — show, cancel or deliver the packets queued for a session with `pilead send --when-idle`
(lib/pilead/queue.py). A queue that cannot be read is said to be so — never shown as empty — and
nothing is changed in it. Only this verb's `--deliver` with the trigger `manual` (the default) reopens
a closed or dead session to deliver; the triggers `check` and `settle` never open a tab."""
import json
import re
import sys

from .. import queue as queue_mod, state
from ..state import PileadError

ORDER = 31
RESOLVE = ("sid",)


def _unreadable_text(home, sid, p):
    return (f"the queue of {sid} cannot be read: {p} — nothing was changed; the packet bodies are under "
            f"{queue_mod.body_dir(home, sid)}")


def _unreadable(home, sid, p, as_json):
    text = _unreadable_text(home, sid, p)
    if as_json:
        print(json.dumps({"sid": sid, "queued": None, "error": text, "items": None}, indent=2))
    else:
        print(f"pilead: {text}", file=sys.stderr)
    return 1


def _show(home, sid, as_json):
    try:
        items, _ = queue_mod.read(home, sid)
    except queue_mod.QueueUnreadable as e:
        return _unreadable(home, sid, e.path, as_json)
    if as_json:
        print(json.dumps({"sid": sid, "queued": len(items), "error": None, "items": items}, indent=2))
        return 0
    if not items:
        print(f"{sid}: nothing queued")
        return 0
    print(f"{sid}: {len(items)} queued packet(s), delivered oldest first, one each time the session finishes a "
          "packet")
    for it in items:
        print(f"  #{it['id']}  queued {it.get('queued_at') or '-'}  {it.get('summary') or '(no summary)'}")
        print(f"       body: {it.get('body_path') or '-'}")
        if it.get("claimed_at"):
            print(f"       being delivered since {it['claimed_at']}")
        if it.get("last_error"):
            print(f"       last delivery attempt failed: {it['last_error']}")
    print(f"  cancel with: pilead queue {sid} --cancel <id|all>")
    return 0


def _cancel(home, sid, which):
    if which != "all" and not re.fullmatch(r"[0-9]+", which):
        raise PileadError(f"--cancel takes a queued packet's id (a whole number) or all, not {which!r}. "
                          "Nothing was cancelled.")
    try:
        items, _ = queue_mod.read(home, sid)
    except queue_mod.QueueUnreadable as e:
        return _unreadable(home, sid, e.path, False)
    if not items:
        print(f"{sid} has nothing queued — nothing to cancel")
        return 0
    try:
        done = queue_mod.cancel(home, sid, which)
    except queue_mod.QueueUnreadable as e:
        return _unreadable(home, sid, e.path, False)
    if done.missing:
        queued = ", ".join(f"#{it['id']}" for it in done.remaining) or "none"
        raise PileadError(f"{sid} has no queued packet #{int(which)} (queued: {queued}). Nothing was cancelled.")
    if done.cancelled:
        n = len(done.remaining)
        print(f"cancelled {len(done.cancelled)} queued packet(s) for {sid}" + (f" — {n} still queued" if n else ""))
    for it in done.claimed:
        print(f"#{it['id']} is being delivered and was not cancelled", file=sys.stderr)
    if not done.cancelled and not done.claimed:  # emptied between the read and the lock
        print(f"{sid} has nothing queued — nothing to cancel")
    return 1 if done.claimed and not done.cancelled else 0


def _deliver(home, sid, trigger):
    r = queue_mod.deliver(home, sid, trigger)
    if r.kind == "delivered":
        return 0
    if r.reason == "unreadable":
        return _unreadable(home, sid, queue_mod.path(home, sid), False)
    if r.reason == "empty":
        print(f"{sid}: nothing queued")
    elif r.reason == "waiting":
        print(f"{sid}: still {r.status} — the queue waits")
    elif r.reason == "locked":
        print(f"{sid}: another delivery is running")
    else:
        print(f"{sid}: not delivered — {r.error}", file=sys.stderr)
        return 1
    return 0


def cmd_queue(a):
    if a.cancel is not None and a.deliver:
        raise PileadError("--cancel and --deliver cannot be combined. Nothing was changed.")
    if a.trigger is not None and not a.deliver:
        raise PileadError("--trigger is only for --deliver. Nothing was changed.")
    if a.json and (a.cancel is not None or a.deliver):
        raise PileadError("--json shows the queue; it does not combine with --cancel or --deliver. "
                          "Nothing was changed.")
    home = state.home_dir(a.home)
    state.load_meta(home, a.sid)  # an unknown session: the one line every verb prints for it (exit 2)
    if a.cancel is not None:
        return _cancel(home, a.sid, a.cancel)
    if a.deliver:
        return _deliver(home, a.sid, a.trigger or "manual")
    return _show(home, a.sid, a.json)


def register(verb):
    p = verb("queue", cmd_queue, "show, cancel or deliver the packets queued with send --when-idle")
    p.add_argument("sid")
    p.add_argument("--cancel", metavar="ID|all", help="remove the queued packet with this id, or all of them "
                                                      "(a packet being delivered is kept)")
    p.add_argument("--deliver", action="store_true",
                   help="deliver the oldest queued packet if the session has finished its current packet; "
                        "with the trigger manual a closed or dead session is reopened")
    p.add_argument("--trigger", choices=queue_mod.TRIGGERS,
                   help="with --deliver: what asks for the delivery (default manual)")
    p.add_argument("--json", action="store_true", help="the queue as JSON")

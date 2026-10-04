"""pilead send — send a follow-up packet to an executor (relaunches it if closed or dead). A heavy session
(lib/pilead/usage.py `is_heavy`) is refused unless `--heavy-override "<reason>"` is given. A superseded
session is refused; a closed session whose process is still alive (`close --keep-tab`) gets the packet
through its inbox, with no relaunch. A plain send that makes the packet number 3 or more prints one
note on stderr: stop sending fixes, upgrade.

`--rotate` retires the session (`pilead retire`) and spawns its successor over the same territory, with
the retired session's seed and THIS packet as its packet 1; `--upgrade` does the same one tier up. The
heaviness gate does not apply to them (they are the way out of a heavy session). Everything is checked
before anything changes — also the queue: one that cannot be read, or whose head is being delivered, is
refused. Once the successor has launched, the packets queued for the retired session move to it
(lib/pilead/queue.py `move`), except one that is this very packet.

Sending is a claim of the session: with a calling lead, a session whose owner is gone, missing or a ghost is
adopted by it once every refusal has passed (verbs/adopt.py `claim`; a queued delivery never adopts).

`--when-idle` queues the packet (lib/pilead/queue.py) when the session has not finished its current
packet, or when packets are already queued for it (a packet is never sent ahead of those queued before
it); otherwise it sends as a plain send does. A queued packet is delivered later through `send_packet`,
so it is numbered, footed and baselined at delivery exactly as a plain send."""
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

from .. import config, ledger, lifecycle, lint, models, notify, posture, queue as queue_mod, refs, review, seed, state, status as status_mod, tabs, usage
from ..launch import _launch
from ..state import PileadError

ORDER = 30
RESOLVE = ("sid",)


class WaitingRefused(PileadError):
    """A plain send refused because the session has not finished its current packet — `busy`, `stalled`
    or `paused` — or because its status could not be read at all. Exit code 2 (PileadError's default),
    like every other refusal of `pilead send`. Raised only for a plain send; `--rotate` and `--upgrade`
    keep the rules they have at HEAD."""


class HeavyRefused(PileadError):
    """A send refused by the heaviness gate (`_gate`). Exit code 2, like every refusal of `pilead send`;
    a subclass so a queued delivery can tell this refusal from the others without reading its text."""


def _check_override_reason(a):
    """Refuse (PileadError, exit 2) before anything is written when the override's reason is blank.
    The first half of `_gate` at HEAD, split out so it can run before the waiting-status check."""
    if a.heavy_override is not None and not a.heavy_override.strip():
        raise PileadError("--heavy-override needs a reason; an empty one is refused. Nothing was sent.")


def _waiting_message(sid, status, n, packet):
    """The waiting line's text, without the `pilead: ` prefix `cli.main` adds when it prints a
    PileadError."""
    return (f"session {sid} is {status}: packet {n:04d} has no report yet, and a send now would take its "
            f"place as the current packet. Nothing was sent. Queue this packet with pilead send {sid} {packet} "
            f"--when-idle, wait for the report (pilead check {sid}), or hand this packet to a successor with "
            f"pilead send {sid} {packet} --rotate.")


def _unreadable_message(sid, found, n):
    """The unreadable-status line's text, without the `pilead: ` prefix `cli.main` adds."""
    return (f"the status of session {sid} could not be read (found: {found}), so it is not known whether "
            f"packet {n:04d} is finished. Nothing was sent. pilead check {sid} shows what is on disk.")


def _check_waiting(home, a, meta, reading):
    """Step 4 of a plain send: bring the session's status up to date (`status.refresh`, which may store
    and log a change such as busy -> stalled or busy -> dead on its own — that write is `status.refresh`'s,
    not send's) and refuse before anything else is written when the session has not finished its current
    packet, or its status cannot be read at all. `reported`, `idle`, `closed` and `dead` are sent as at
    HEAD; the decision to relaunch (later in `cmd_send`) uses the status this refresh just found, because
    it re-reads the same just-written `status` file."""
    sid = a.sid
    n = int(meta.get("packets") or 1)
    status = status_mod.refresh(home, meta, reading=reading)
    if status in ("reported", "idle", "closed", "dead"):
        return
    if status in status_mod.BUSY_LIKE:
        raise WaitingRefused(_waiting_message(sid, status, n, a.packet))
    found = "nothing" if status == "unknown" else status
    raise WaitingRefused(_unreadable_message(sid, found, n))


def _gate(home, a, u, mb):
    """Refuse (PileadError, exit 2) before anything is written when the session is heavy and no override
    was given. Returns True when a heavy session is sent to under an override. A session whose usage and
    log size are both unknown is never refused. The blank-reason check runs earlier, in
    `_check_override_reason`."""
    _, nudge, mb_th = config.thresholds(home)
    if not usage.is_heavy(u, mb, nudge, mb_th):
        return False
    if a.heavy_override is not None:
        return True
    if u is not None and u.get("live") is not None:
        threshold = f"the {usage.human_tokens(nudge)}-token context_nudge_tokens threshold"
    else:
        threshold = f"the {mb_th:g}MB handoff_nudge_mb threshold"
    raise HeavyRefused(
        f"session {a.sid} is heavy: {usage.heavy_reading_text(u, mb)}, at or above {threshold}. "
        f"This is not a verdict on the executor's work; it is a point for a conscious choice. "
        f"Either `pilead close {a.sid}` and a fresh `pilead spawn` for the next packet (its context starts "
        f"empty) — `pilead send {a.sid} <packet> --rotate` does both in one step, seeded with this session's "
        f"packet index — or pass --heavy-override \"<reason>\" to send into this session anyway. Nothing was sent.")


def _third_packet_note(sid, n, model):
    """The one stderr line for a plain send of packet `n` >= 3 (None below 3)."""
    if n < 3:
        return None
    tier = models.tier(model) if model else None
    what = tier or model or "-"
    how = (f"pilead send {sid} <packet> --upgrade moves it one tier up with a seeded successor" if tier else
           f"pilead send {sid} <packet> --rotate --model <model> moves it to the model you name with a seeded "
           "successor")
    return (f"pilead: this is packet {n} into a {what} session — if the last two were fixes for the same work "
            f"and it still is not done, stop sending fixes: {how}")


def _check_flags(a):
    """--rotate and --upgrade exclude each other, --when-idle excludes both; --name, --model and
    --model-override need one of --rotate and --upgrade."""
    if a.rotate and a.upgrade:
        raise PileadError("--rotate and --upgrade cannot be combined (--upgrade already rotates, one tier up). "
                          "Nothing was sent.")
    if getattr(a, "when_idle", False) and (a.rotate or a.upgrade):
        raise PileadError(f"--when-idle cannot be combined with {'--rotate' if a.rotate else '--upgrade'} (a "
                          "rotation hands the packet to a successor now; --when-idle queues it for this "
                          "session). Nothing was sent.")
    if not (a.rotate or a.upgrade):
        given = [f for f, v in (("--name", a.name), ("--model", a.model), ("--model-override", a.model_override))
                 if v is not None]
        if given:
            raise PileadError(f"{', '.join(given)}: only with --rotate or --upgrade (a plain send reuses the "
                              "session as it was launched). Nothing was sent.")


def _successor(home, name, sid):
    if name is None:
        return lifecycle.successor_name(home, sid)
    try:
        usable = state.safe_topic(name) == name and state.valid_session_sid(name)
    except PileadError:
        usable = False
    if not usable:
        raise PileadError(f"--name {name!r} is not usable as a session id: letters, digits, '.', '_' and '-' "
                          "only, first and last a letter or digit, no '..'. Nothing was sent.")
    if state.session_dir(home, name).exists():
        raise PileadError(f"--name {name}: a session of that name already exists. Nothing was sent.")
    return name


def _upgrade_target(home, sid, current):
    """The alias one tier above `current`'s whose alias resolves (tiers that do not are skipped), as
    (alias, the tiers skipped); PileadError (exit code 2) when there is no tier or nothing above."""
    from_tier = models.tier(current) if current else None
    if from_tier is None:
        raise PileadError(f"--upgrade: the model of {sid} ({current or '-'}) has no tier, so there is no tier "
                          "above it; name the successor's model with --rotate --model <model>. Nothing was sent.")
    above = models.FAMILIES[models.FAMILIES.index(from_tier) + 1:]
    if not above:
        raise PileadError(f"--upgrade: {sid} already runs {from_tier}, the top tier — nothing above it. "
                          "Nothing was sent.")
    skipped = []
    for alias in above:
        try:
            models.resolve(home, alias)
        except models.UnmatchedAlias:
            skipped.append(alias)
            continue
        return alias, skipped
    raise PileadError(f"--upgrade: {sid} runs {from_tier} and no tier above it resolves here (no model for "
                      f"{', '.join(skipped)} in models.json); name one with --rotate --model <model>. "
                      "Nothing was sent.")


def _rotate(home, a, meta):
    """`send --rotate` / `--upgrade`: every check first, then retire and spawn the seeded successor."""
    from . import retire, spawn  # at run time: a verb module that fails to load never takes send out of --help
    sid = a.sid
    if meta.get("superseded_by") and not seed.is_retired(meta):
        raise PileadError(f"session {sid} was superseded by {meta['superseded_by']}; send to that, or spawn a "
                          "fresh session. Nothing was sent.")
    src = Path(a.packet).expanduser()
    if not src.is_file():
        raise PileadError(f"packet {a.packet} not found")
    now = retire.check(home, sid, meta, via_send=True)
    queue_mod.movable(home, sid, what="sent")
    successor = _successor(home, a.name, sid)
    effort, effort_source = spawn.inherited_effort(home, meta)
    effort = spawn._check_level("effort", effort)
    current = meta.get("model")
    from_tier = models.tier(current) if current else None
    note = None
    tier_pick = None
    fallback = config.executor_models(home)[1]
    if a.model is None:  # the owning lead's tier posture; a refusal comes before anything changes
        takes_fallback = bool(a.rotate) and now == "paused" and fallback is not None
        tier_pick = posture.model_choice(home, meta.get("lead"), a.packet, lead_applies=not (a.upgrade or takes_fallback))
    if a.model is not None:
        model = spawn.choose_model(home, a.model, a.model_override)
    elif a.upgrade:
        alias, skipped = _upgrade_target(home, sid, current)
        model = spawn.choose_model(home, models.resolve(home, alias), a.model_override)
        if skipped:
            note = f"model: skipped {', '.join(skipped)} (no model for it in models.json)"
    else:
        if now == "paused" and fallback is not None:
            model = spawn.choose_model(home, fallback, a.model_override)
            note = (f"model: {sid} was paused at its usage limit on {current}; its successor runs on the fallback "
                    f"{model['model']}")
        else:
            model = spawn.choose_model(home, tier_pick if tier_pick is not None else current, a.model_override)
    body = src.read_text()
    lint.advise(body, meta.get("worktree") or None, model["model"])

    # every check passed: from here on things change
    _claim(home, sid, meta)  # the successor names the lead this session names after it
    if a.upgrade:
        print(f"upgrading {sid} {from_tier or '-'} → {models.tier(model['model']) or '-'}: retire + spawn "
              f"{successor} on {model['model']}")
    else:
        print(f"rotating {sid} → retire + spawn {successor} on {model['model']}")
    if tier_pick is not None:
        print(spawn.tier_lead_line(tier_pick, model))
    if note:
        print(note)
    seed_path, _ = retire.retire(home, sid, meta)
    retired = state.load_meta(home, sid)
    try:
        new, placement = spawn.spawn_session(
            home, lead=retired.get("lead"), worktree=retired.get("worktree"), topic=retired.get("topic") or sid,
            body=body, model=model, effort=effort, effort_source=effort_source, scope=retired.get("scope"),
            keep=False, layout=retired.get("layout"), source=str(src.resolve()), sid=successor,
            seed=(seed_path.read_text(), str(seed_path.resolve())))
    except Exception as e:  # noqa: BLE001 — whatever failed, no successor is left behind
        d = state.session_dir(home, successor)
        if d.exists():
            shutil.rmtree(d)
        why = str(e) if isinstance(e, PileadError) else f"{type(e).__name__}: {e}"
        print(f"pilead: {sid} was retired, but its successor {successor} did not launch: {why}. Spawn it by hand: "
              f"{seed.spawn_command(retired, sid)}", file=sys.stderr)
        return 1
    if tier_pick is not None:
        ledger.append(home, "model_from_tier", session_id=successor, lead=retired.get("lead"),
                      lead_model=tier_pick.lead_model, model=model["model"])
    ledger.append(home, "rotated", session_id=sid, successor=successor, model=model["model"],
                  upgrade=bool(a.upgrade), from_tier=from_tier)
    for line in spawn.spawned_lines(new, model, placement):
        print(line)
    code = move_queue(home, sid, successor, src)
    print(f"successor: {successor}")
    review.refresh_after(home)  # a live board is rewritten, before the cosmetic tidy
    tabs.tidy_after(home, "send")  # the successor's tab was opened (a rotation spawns); cosmetic
    return code


def move_queue(home, sid, successor, sending):
    """After a successor has launched: move `sid`'s queue to it (lib/pilead/queue.py `move`) and print
    what moved, only when something did. A failure is one line on stderr and exit code 1; the successor
    stands."""
    try:
        moved, dropped = queue_mod.move(home, sid, successor, sending=sending)
    except Exception as e:  # noqa: BLE001 — the successor already runs; say what did not happen
        why = str(e) if isinstance(e, PileadError) else f"{type(e).__name__}: {e}"
        print(f"pilead: the packets queued for {sid} were not moved to {successor}: {why} — pilead queue {sid} "
              "shows them", file=sys.stderr)
        return 1
    if moved:
        print(f"moved {moved} queued packet(s) from {sid} to {successor}")
    if dropped:
        print(f"{dropped} queued packet(s) were the packet being sent — not queued a second time")
    return 0


def _superseded(sid, meta):
    if meta.get("superseded_by"):
        raise PileadError(f"session {sid} was superseded by {meta['superseded_by']}; send to that, or spawn a "
                          "fresh session. Nothing was sent.")


def _claim(home, sid, meta):
    """A lead's send claims the session (verbs/adopt.py `claim`, loaded at run time)."""
    from .adopt import claim
    return claim(home, sid, meta)


def send_packet(home, sid, src, heavy_override=None, source=None, queue_id=None, claim=False, tidy=False,
                refresh=False):
    """A plain send of the packet file `src` to session `sid`: every refusal is a PileadError
    (`WaitingRefused`, `HeavyRefused` or another), raised before anything is written; on success it
    prints what a plain `pilead send` prints and returns the new packet number. `source`, when given,
    is what `packet_sent` records as its source (default: `src` resolved); `queue_id`, when given, is
    added to `packet_sent` (a delivered queued packet). `claim` (a lead's own send, never a queued
    delivery): after the refusals, the calling lead adopts the session when it may (`_claim`). `tidy` (the
    `send` verb): when it relaunched the session or adopted it, the tabs are tidied last (tabs.tidy_after).
    `refresh` (the `send` verb): a live board is rewritten after the success, before that tidy."""
    a = SimpleNamespace(sid=sid, packet=str(src), heavy_override=heavy_override)
    meta = state.load_meta(home, sid)
    _superseded(sid, meta)
    src = Path(src).expanduser()
    if not src.is_file():
        raise PileadError(f"packet {a.packet} not found")
    _check_override_reason(a)
    # Read before anything changes, and write no cache: a refusal leaves the session's files as they were.
    u, mb = usage.session_reading(home, sid, meta.get("worktree") or None, write_cache=False)
    _check_waiting(home, a, meta, reading=(u, mb))
    overridden = _gate(home, a, u, mb)
    owner_before = meta.get("lead")
    if claim:
        _claim(home, sid, meta)
    adopted = meta.get("lead") != owner_before
    body = src.read_text()
    lint.advise(body, meta.get("worktree") or None, meta.get("model"))
    sdir = state.session_dir(home, sid)
    stored = state.read_status(home, sid, meta)
    # closed, but its process still runs (`close --keep-tab`): the packet goes through the inbox, no relaunch
    revive = stored == "closed" and status_mod.pid_alive(sdir / "pid") is True
    label_open = meta.get("label_open") if meta.get("kept_tab") else None
    n = meta["packets"] + 1
    packet = state.write_packet(home, sid, n, body)
    meta["packets"] = n
    if stored in ("closed", "dead"):
        meta.pop("kept_tab", None)
        if label_open and not revive:
            meta["label"] = label_open  # a relaunched tab takes its open title, not `[closed] <sid>`
    state.save_meta(home, meta)
    refs.record_baseline(home, sid, n, meta["worktree"])  # before the packet can reach the executor (inbox or relaunch)
    placement = None
    if stored in ("closed", "dead") and not revive:
        _, placement = _launch(home, meta, packet, resume=True)  # reopens it; the packet arrives via the inbox
    inbox = sdir / "inbox.md"
    pending = inbox.read_text().strip() if inbox.is_file() else ""
    text = packet.read_text()
    state.atomic_write(inbox, (pending + "\n\n" if pending else "") + text)
    if revive and label_open and lifecycle.rename_tab(meta, lifecycle.tab_handle(meta, sdir), label_open,
                                                        home=home):
        meta["label"] = label_open
        state.save_meta(home, meta)
    state.write_status(home, sid, "busy")
    if overridden:
        ledger.append(home, "heavy_override", session_id=sid, packet=n, tokens=(u or {}).get("live"),
                      mb=round(mb, 1) if mb is not None else None, reason=heavy_override)
    extra = {"queue_id": queue_id} if queue_id is not None else {}
    ledger.append(home, "packet_sent", session_id=sid, packet=n,
                  source=source if source is not None else str(src.resolve()),
                  via="relaunch" if placement else "inbox", **extra)
    ledger.append(home, "usage_snapshot", session_id=sid, packet=n, at="sent", usage=usage.snapshot(u))
    if u is not None:  # advisory; a session with no log prints what it always did (`check` reports the unknown)
        live = u.get("live")
        rate = u.get("cache_hit_rate")
        print(f"  ctx: {usage.human_tokens(live) if live is not None else '-'}, "
              f"hit-rate {f'{round(rate * 100)}%' if rate is not None else '-'}")
    print(f"sent {sid} packet {n:04d}")
    if placement:
        print(f"placement={placement}")
    note = _third_packet_note(sid, n, meta.get("model"))
    if note:
        print(note, file=sys.stderr)
        if state.valid_lead_sid(meta.get("lead")):  # after the note: a banner changes nothing that is printed
            notify.announce(home, meta["lead"], f"{sid}: packet {n}", note)
    if refresh:
        review.refresh_after(home)
    if tidy and (adopted or (stored in ("closed", "dead") and not revive)):
        tabs.tidy_after(home, "send")  # a tab was opened or an owner changed; cosmetic, last
    return n


def _when_idle(home, a, meta):
    """`send --when-idle`: queue the packet when the session has not finished its current packet or
    packets are already queued for it; otherwise a plain send. Every refusal before anything is written."""
    sid = a.sid
    src = Path(a.packet).expanduser()
    if not src.is_file():
        raise PileadError(f"packet {a.packet} not found")
    _check_override_reason(a)
    _superseded(sid, meta)
    try:
        items, _ = queue_mod.read(home, sid)
    except queue_mod.QueueUnreadable as e:
        raise PileadError(f"the queue of {sid} cannot be read: {e.path} — nothing was queued or sent. "
                          f"pilead queue {sid} shows it.") from None
    now = status_mod.refresh(home, meta)
    if now in status_mod.BUSY_LIKE or items:
        owner_before = meta.get("lead")
        _claim(home, sid, meta)
        item = queue_mod.enqueue(home, sid, src.read_text(), src, heavy_override=a.heavy_override)
        n = queue_mod.count(home, sid)
        print(f"queued {sid} #{item['id']} — delivers when the session has finished its current packet "
              f"({n if n is not None else '?'} queued)")
        print(f"  show or cancel: pilead queue {sid} [--cancel ID|all]")
        review.refresh_after(home)  # a live board is rewritten, before the cosmetic tidy
        if meta.get("lead") != owner_before:
            tabs.tidy_after(home, "send")  # the session was adopted; cosmetic, last
        return
    send_packet(home, sid, a.packet, heavy_override=a.heavy_override, claim=True, tidy=True, refresh=True)


def cmd_send(a):
    _check_flags(a)
    home = state.home_dir(a.home)
    meta = state.load_meta(home, a.sid)
    if a.rotate or a.upgrade:
        return _rotate(home, a, meta)
    if a.when_idle:
        return _when_idle(home, a, meta)
    send_packet(home, a.sid, a.packet, heavy_override=a.heavy_override, claim=True, tidy=True, refresh=True)


def register(verb):
    p = verb("send", cmd_send, "send a follow-up packet to an executor (relaunches it if closed or dead)")
    p.add_argument("sid"); p.add_argument("packet")
    p.add_argument("--heavy-override", metavar="REASON",
                   help="send even though the session is heavy (its live context is at or above "
                        "context_nudge_tokens); the reason goes to the ledger")
    p.add_argument("--when-idle", action="store_true",
                   help="queue the packet when the session has not finished its current packet (or packets are "
                        "already queued for it); it is delivered, oldest first, after that packet. Show or "
                        "cancel with pilead queue")
    p.add_argument("--rotate", action="store_true",
                   help="retire the session (pilead retire) and send this packet as packet 1 of a seeded "
                        "successor over the same territory; no --heavy-override needed")
    p.add_argument("--upgrade", action="store_true",
                   help="as --rotate, with the successor one tier up (haiku → sonnet → opus → fable)")
    p.add_argument("--name", help="with --rotate/--upgrade: the successor's session id (default: <sid>-r<N>)")
    p.add_argument("--model", help="with --rotate/--upgrade: the successor's model (tier alias or provider/id)")
    p.add_argument("--model-override", metavar="REASON",
                   help="with --rotate/--upgrade: launch a model above config executor_model_ceiling; the reason "
                        "is ledgered")

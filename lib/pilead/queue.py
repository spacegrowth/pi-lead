"""A session's queue of packets waiting for it to finish its current packet (`pilead send --when-idle`).

The queue of a session is `sessions/<sid>/queue.json` — `{"next_id": <int >= 1>, "items": [<item>, …]}`,
oldest first — plus one body file per item under `sessions/<sid>/queue/`, holding the packet text as it
was at queue time. An item is `{id, queued_at, source, body_path, summary, heavy_override, last_error,
error_kind, claimed_at}`. Ids are never reused within a session: `next_id` is stored, and `queue.json`
stays on disk once created, also when it has no items.
A claimed head also has `claimed_packets` (the session's packet count when it was claimed); it is absent
or null when an item is not claimed. An item `move` put there also has `moved_from` (`{session_id,
queue_id}` of the item it came from), so a move that died midway and is run again skips it.

A queue file that exists and cannot be read is UNREADABLE (`QueueUnreadable`): nothing here ever
treats it as empty, adds to it, delivers from it, cancels from it or overwrites it.

Every change to `queue.json` (`enqueue`, `cancel`, `deliver`) is made under `sessions/<sid>/queue.lock`.
A queued packet is numbered only when it is delivered: `deliver` goes through the plain send
(`verbs/send.py` `send_packet`), so it gets its number, footer, refs baseline and `packet_sent` event
there, exactly as a plain send does."""
import contextlib
import io
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import ledger, state, status as status_mod
from .state import PileadError

FILE = "queue.json"
DIR = "queue"
LOCK = "queue.lock"
LOCK_STALE_SECONDS = 120
LOCK_WAIT_SECONDS = 5  # enqueue and cancel wait this long for the lock; deliver never waits
LOCK_RETRY_SECONDS = 0.1
SUMMARY_LEN = 120
ERROR_LEN = 300
TRIGGERS = ("manual", "check", "settle")


class QueueUnreadable(PileadError):
    """`queue.json` exists and is not the shape this module writes. Carries the path."""

    def __init__(self, path, why=""):
        self.path = Path(path)
        super().__init__(f"the queue file {self.path} cannot be read" + (f" ({why})" if why else ""))


def path(home, sid):
    return state.session_dir(Path(home), sid) / FILE


def body_dir(home, sid):
    return state.session_dir(Path(home), sid) / DIR


def lock_path(home, sid):
    return state.session_dir(Path(home), sid) / LOCK


# ── reading and writing ──────────────────────────────────────────────────────────────────────────
def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def read(home, sid):
    """(items, next_id). No file: ([], 1). A file that exists and is not the stored shape raises
    `QueueUnreadable`; the file is never touched here."""
    p = path(home, sid)
    if not os.path.lexists(p):
        return [], 1
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — not JSON, not UTF-8, a directory, no permission: all unreadable
        raise QueueUnreadable(p, type(e).__name__) from None
    if not isinstance(data, dict):
        raise QueueUnreadable(p, "not an object")
    items = data.get("items")
    if not isinstance(items, list):
        raise QueueUnreadable(p, "items is not a list")
    for it in items:
        if not isinstance(it, dict) or not _whole(it.get("id")):
            raise QueueUnreadable(p, "an item without a whole-number id")
    next_id = data.get("next_id")
    if not _whole(next_id) or next_id < 1 or any(next_id <= it["id"] for it in items):
        raise QueueUnreadable(p, "next_id missing or not above every id")
    return items, next_id


def count(home, sid):
    """The number of queued items, or None when the queue cannot be read. Never raises."""
    try:
        return len(read(home, sid)[0])
    except Exception:  # noqa: BLE001
        return None


def _write(home, sid, items, next_id):
    state.atomic_write(path(home, sid), json.dumps({"next_id": next_id, "items": items}, indent=2) + "\n")


def summary(body):
    """The body's first non-blank line, a leading `GOAL:` removed, cut to SUMMARY_LEN; None when blank."""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("GOAL:"):
            s = s[len("GOAL:"):].strip()
        return s[:SUMMARY_LEN] or None
    return None


# ── the lock ─────────────────────────────────────────────────────────────────────────────────────
class _LockPath(type(Path())):
    """The path `lock` returns: a plain path (equal to `lock_path(...)`) that also carries the owner token
    written into the lock file, so `release` and the freshness beat can tell their own lock from another's."""
    token = None


def _now():
    """The clock the lock's age is judged by (tests may replace it)."""
    return time.time()


def _owner(p):
    """(pid, token) read from lock file `p`, which holds one line `<pid> <token>`. A lock written before
    the token existed holds the pid only: its token is None (never anyone's). A missing or unreadable
    file, or a first word that is not a number: (None, None)."""
    try:
        words = Path(p).read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return None, None
    try:
        pid = int(words[0]) if words else None
    except ValueError:
        pid = None
    return pid, (words[1] if len(words) > 1 else None)


def _ours(p):
    """True when lock file `p` still holds the token `lock` wrote into it (`p` is what `lock` returned)."""
    token = getattr(p, "token", None)
    return token is not None and _owner(p)[1] == token


def _break_stale(p):
    """True when the caller should try the exclusive create again: the lock was older than
    LOCK_STALE_SECONDS and has been removed, or it was released meanwhile. The stale file is moved
    aside first and its age checked again there, so a lock another process has just taken is put
    back instead of removed. Only the age decides; the pid and token in the file are not consulted."""
    try:
        if _now() - p.stat().st_mtime <= LOCK_STALE_SECONDS:
            return False
    except FileNotFoundError:
        return True
    aside = p.with_name(f"{p.name}.stale{os.getpid()}")
    try:
        os.rename(p, aside)
    except FileNotFoundError:
        return True
    try:
        if _now() - aside.stat().st_mtime > LOCK_STALE_SECONDS:
            return True
        try:
            os.link(aside, p)  # a fresh lock was moved: put it back (never over one taken since)
        except FileExistsError:
            pass
        return False
    finally:
        with contextlib.suppress(FileNotFoundError):
            aside.unlink()


def _try_lock(p, token):
    for attempt in (1, 2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if attempt == 1 and _break_stale(p):
                continue
            return False
        try:
            os.write(fd, f"{os.getpid()} {token}\n".encode())
        finally:
            os.close(fd)
        return True
    return False


def lock(home, sid, wait=0.0):
    """Take `sessions/<sid>/queue.lock` (exclusive create, holding one line `<pid> <token>`, the token a
    random hex string made now), waiting up to `wait` seconds; a lock older than LOCK_STALE_SECONDS is
    removed and taken once. Returns the lock's path — carrying the token as `.token`, which `release`
    checks — or None when it could not be taken."""
    p = _LockPath(lock_path(home, sid))
    p.token = secrets.token_hex(8)
    deadline = time.monotonic() + wait
    while True:
        if _try_lock(p, p.token):
            return p
        if time.monotonic() >= deadline:
            return None
        time.sleep(LOCK_RETRY_SECONDS)


def release(p):
    """Remove lock `p` (what `lock` returned) only when the file still holds `p`'s token: a lock that was
    judged stale and taken by another process — or one in the old pid-only format — is left alone. Never
    raises. (Between reading the token and removing the file there is a window of microseconds in which
    another process could break this lock as stale and take it; `_kept_fresh` keeps a held lock from
    going stale, so that needs a holder silent for LOCK_STALE_SECONDS first.)"""
    try:
        if p is not None and _ours(p):
            with contextlib.suppress(FileNotFoundError):
                Path(p).unlink()
    except Exception:  # noqa: BLE001 — release never raises
        pass


def _touch(p):
    """Refresh the lock's mtime (a lock that is being used is not stale), only while it is still ours.
    Never raises."""
    with contextlib.suppress(Exception):
        if _ours(p):
            t = _now()
            os.utime(p, (t, t))


@contextlib.contextmanager
def _kept_fresh(p):
    """While the block runs, refresh lock `p`'s mtime right before it, every quarter of
    LOCK_STALE_SECONDS during it (a background thread), and right after it — so a delivery whose send
    is slow keeps its lock instead of having it judged stale and taken by another process. `p` None:
    nothing is done."""
    if p is None:
        yield
        return
    _touch(p)
    done = threading.Event()

    def beat():
        while not done.wait(max(0.05, LOCK_STALE_SECONDS / 4)):
            _touch(p)
    t = threading.Thread(target=beat, name="pilead-queue-lock", daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join(timeout=5)
        _touch(p)


@contextlib.contextmanager
def _held(home, sid):
    """The lock for enqueue and cancel: wait up to LOCK_WAIT_SECONDS, else PileadError naming it."""
    p = lock(home, sid, wait=LOCK_WAIT_SECONDS)
    if p is None:
        raise PileadError(f"the queue of {sid} is locked by another pilead ({lock_path(home, sid)}); nothing was "
                          f"changed. Try again; a lock older than {LOCK_STALE_SECONDS}s is taken over.")
    try:
        yield p
    finally:
        release(p)


# ── enqueue and cancel ───────────────────────────────────────────────────────────────────────────
def enqueue(home, sid, body, source, heavy_override=None, moved_from=None):
    """Add one packet at the back of the queue and return the stored item. The body goes to
    `queue/<id:04d>-queued.md`, created exclusively: an existing file of that name is never
    overwritten (PileadError, nothing changed). `moved_from` (given by `move` only) is stored on the
    item and its `packet_queued` event."""
    home = Path(home)
    with _held(home, sid):
        items, next_id = read(home, sid)
        qid = next_id
        bdir = body_dir(home, sid)
        bdir.mkdir(parents=True, exist_ok=True)
        bp = (bdir / f"{qid:04d}-queued.md").resolve()
        try:
            with open(bp, "x", encoding="utf-8") as f:
                f.write(body)
        except FileExistsError:
            raise PileadError(f"{bp} already exists, so queued packet #{qid} of {sid} cannot be stored without "
                              "overwriting it. Nothing was queued.") from None
        item = {"id": qid, "queued_at": state.now_iso(), "source": str(Path(source).expanduser().resolve()),
                "body_path": str(bp), "summary": summary(body), "heavy_override": heavy_override,
                "last_error": None, "error_kind": None, "claimed_at": None}
        if moved_from is not None:
            item["moved_from"] = moved_from
        try:
            _write(home, sid, items + [item], qid + 1)
        except BaseException:
            with contextlib.suppress(OSError):
                bp.unlink()
            raise
    extra = {"moved_from": moved_from} if moved_from is not None else {}
    ledger.append(home, "packet_queued", session_id=sid, queue_id=qid, source=item["source"], **extra)
    return item


class Cancelled:
    """What `cancel` did: `cancelled` (items removed), `claimed` (a claimed head asked for and kept),
    `missing` (the id asked for is not queued), `remaining` (items still queued)."""

    def __init__(self, cancelled, claimed, missing, remaining):
        self.cancelled, self.claimed, self.missing, self.remaining = cancelled, claimed, missing, remaining


def cancel(home, sid, which):
    """Remove the item with id `which`, or every item when `which` is "all". A claimed head (a delivery
    in progress) is never cancelled. Body files stay on disk. Raises QueueUnreadable."""
    home = Path(home)
    with _held(home, sid):
        items, next_id = read(home, sid)
        claimed_head = items[0] if items and items[0].get("claimed_at") else None
        if which == "all":
            asked = list(items)
        else:
            asked = [it for it in items if it["id"] == int(which)]
        missing = which != "all" and not asked
        claimed = [it for it in asked if it is claimed_head]
        gone = [it for it in asked if it is not claimed_head]
        kept = [it for it in items if not any(it is g for g in gone)]
        if gone:
            _write(home, sid, kept, next_id)
    for it in gone:
        ledger.append(home, "queue_cancelled", session_id=sid, queue_id=it["id"], source=it.get("source"))
    return Cancelled(gone, claimed, missing, kept)


def stuck_on_heaviness(item):
    """True when the item's last delivery was refused by the heaviness gate (stored `error_kind`)."""
    return isinstance(item, dict) and bool(item.get("last_error")) and item.get("error_kind") == "heavy"


# ── delivery ─────────────────────────────────────────────────────────────────────────────────────
class Delivery:
    """The result of `deliver`: kind `delivered` (with `item` and `packet`) or `nothing` (with `reason`:
    unreadable, empty, locked, waiting, refused; `status` for waiting, `error` for refused)."""

    def __init__(self, kind, reason=None, item=None, packet=None, status=None, error=None, recovered=False):
        self.kind, self.reason, self.item, self.packet = kind, reason, item, packet
        self.status, self.error, self.recovered = status, error, recovered

    def __repr__(self):
        return f"Delivery({self.kind}, {self.reason or self.packet})"


def _nothing(reason, **kw):
    return Delivery("nothing", reason=reason, **kw)


def _one_line(e):
    text = str(e) if isinstance(e, PileadError) else f"{type(e).__name__}: {e}"
    return " ".join(text.split())[:ERROR_LEN]


def _sent_event(home, sid, qid):
    for rec in reversed(ledger.read(home, session_id=sid, event="packet_sent")):
        if rec.get("queue_id") == qid:
            return rec
    return None


def _delivered_line(sid, qid, n, trigger, remaining):
    n_text = f"{n:04d}" if _whole(n) else str(n)
    return (f"delivered queued packet #{qid} to {sid} as packet {n_text} (trigger: {trigger})"
            + (f"; {remaining} still queued" if remaining else ""))


def _refuse(home, sid, items, next_id, trigger, text, kind):
    head = items[0]
    text = " ".join(str(text).split())[:ERROR_LEN]
    before = head.get("last_error")
    head["claimed_at"] = None
    head.pop("claimed_packets", None)
    head["last_error"] = text
    head["error_kind"] = kind
    _write(home, sid, items, next_id)
    if text != before:
        ledger.append(home, "queue_delivery_failed", session_id=sid, queue_id=head["id"], trigger=trigger,
                      error=text)
    return _nothing("refused", error=text, item=head)


def _emit(buf, out):
    for line in buf.getvalue().splitlines():
        out(line)


def _finish(home, sid, items, next_id, trigger, n, out, recovered):
    head, rest = items[0], items[1:]
    _write(home, sid, rest, next_id)
    ledger.append(home, "queue_delivered", session_id=sid, queue_id=head["id"], packet=n, trigger=trigger,
                  remaining=len(rest), recovered=recovered)
    out(_delivered_line(sid, head["id"], n, trigger, len(rest)))
    return Delivery("delivered", item=head, packet=n, recovered=recovered)


def _recover_numbered(home, sid, items, next_id, trigger, out, n):
    """A claimed head whose send numbered its packet (`n`) and died before `packet_sent`: the packet is
    not sent again. The missing `packet_sent` is appended (via `recovered`), the head is removed as a
    delivery removes it, and the recovered line follows the delivered one."""
    head = items[0]
    ledger.append(home, "packet_sent", session_id=sid, packet=n, source=head.get("source"), via="recovered",
                  queue_id=head["id"])
    d = _finish(home, sid, items, next_id, trigger, n, out, recovered=True)
    out(f"  (recovered after an interrupted delivery — pilead check {sid} shows whether packet {n:04d} reached it)")
    return d


_FOOTER_MARK = "\n\n---\n(pi-lead — do not remove or reword this section)\n"


def _is_this_items_packet(home, sid, n, item):
    """True when packet `n` of `sid` holds `item`'s body: its text before the footer `state.write_packet`
    appends equals the queued body as `write_packet` stores it. False for a packet that is some other
    send's. Raises (OSError, …) when the packet or the queued body cannot be read."""
    text = state.packet_path(home, sid, n).read_text(encoding="utf-8")
    body = Path(item.get("body_path") or "").read_text(encoding="utf-8")
    before, mark, _ = text.rpartition(_FOOTER_MARK)
    return bool(mark) and before == body.rstrip("\n")


def _deliver_locked(home, sid, trigger, out, held=None):
    from .verbs import send as send_mod  # at run time: verbs/send.py imports this module
    try:
        items, next_id = read(home, sid)
    except QueueUnreadable:
        return _nothing("unreadable")
    if not items:
        return _nothing("empty")
    head = items[0]
    if head.get("claimed_at"):  # an earlier delivery was interrupted: did its send happen?
        sent = _sent_event(home, sid, head["id"])
        if sent is not None:
            return _finish(home, sid, items, next_id, trigger, sent.get("packet"), out, recovered=True)
        claimed_packets = head.get("claimed_packets")
        if _whole(claimed_packets):  # did the send get as far as numbering the packet?
            try:
                packets_now = state.load_meta(home, sid).get("packets")
            except Exception as e:  # noqa: BLE001 — not known: the claim stays, nothing is sent
                return _nothing("refused", error=_one_line(e), item=head)
            if _whole(packets_now) and packets_now > claimed_packets:
                try:
                    ours = _is_this_items_packet(home, sid, claimed_packets + 1, head)
                except Exception as e:  # noqa: BLE001 — not known: the claim stays, nothing is sent
                    return _nothing("refused", error=_one_line(e), item=head)
                if ours:
                    return _recover_numbered(home, sid, items, next_id, trigger, out, claimed_packets + 1)
                # the count rose through someone else's send (a plain lead send): this item was not sent
        head["claimed_at"] = None
        head.pop("claimed_packets", None)
        _write(home, sid, items, next_id)
    try:
        meta = state.load_meta(home, sid)
    except Exception as e:  # noqa: BLE001
        return _refuse(home, sid, items, next_id, trigger, _one_line(e), None)
    now = status_mod.refresh(home, meta)
    if now in status_mod.BUSY_LIKE:
        return _nothing("waiting", status=now)
    if now in status_mod.TERMINAL and trigger != "manual":
        return _refuse(home, sid, items, next_id, trigger,
                       f"session is {now}; pilead queue {sid} --deliver reopens it and delivers, --cancel all "
                       "empties the queue", None)
    head["claimed_at"] = state.now_iso()
    head["claimed_packets"] = meta.get("packets")
    _write(home, sid, items, next_id)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), _kept_fresh(held):
            n = send_mod.send_packet(home, sid, head.get("body_path"), heavy_override=head.get("heavy_override"),
                                     source=head.get("source"), queue_id=head["id"])
    except send_mod.WaitingRefused:
        _emit(buf, out)
        head["claimed_at"] = None
        head.pop("claimed_packets", None)
        _write(home, sid, items, next_id)
        return _nothing("waiting", status=state.read_status(home, sid, meta))
    except Exception as e:  # noqa: BLE001 — any failure keeps the head, with its reason
        _emit(buf, out)
        kind = "heavy" if isinstance(e, send_mod.HeavyRefused) else None
        return _refuse(home, sid, items, next_id, trigger, _one_line(e), kind)
    _emit(buf, out)
    return _finish(home, sid, items, next_id, trigger, n, out, recovered=False)


def deliver(home, sid, trigger, out=print):
    """Deliver AT MOST ONE item — the head — when the session has finished its current packet. Returns
    a `Delivery`; never raises. The head is marked `claimed_at` (with `claimed_packets`, the session's
    packet count then) before the send and removed only after the send is in the ledger, so an
    interrupted delivery is neither lost nor sent twice: the next call finds the claim and looks for
    this item's `packet_sent`; with none, a packet count above `claimed_packets` means the send numbered
    the packet before it died, so it is recorded (`via: recovered`) instead of sent again. The lock's
    mtime is kept fresh while `send_packet` runs, so a slow relaunch is not judged stale."""
    home = Path(home)
    try:
        try:
            items, _ = read(home, sid)
        except QueueUnreadable:
            return _nothing("unreadable")
        if not items:
            return _nothing("empty")
        held = lock(home, sid)
        if held is None:
            return _nothing("locked")
        try:
            return _deliver_locked(home, sid, trigger, out, held)
        finally:
            release(held)
    except Exception as e:  # noqa: BLE001 — the last resort: deliver never raises
        return _nothing("refused", error=_one_line(e))


# ── moving a queue to a successor ────────────────────────────────────────────────────────────────
def _file_key(p):
    """A comparable identity for a packet file: its resolved absolute path (a relative and an absolute
    spelling of one file are the same key); None for nothing."""
    if not p:
        return None
    try:
        return str(Path(str(p)).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return str(p)


def movable(home, sid, what="changed"):
    """The queue checks `send --rotate` / `--upgrade` and `restart` make before anything changes:
    PileadError (exit code 2) when `sid`'s queue cannot be read, or its head is claimed (a delivery is
    running or was interrupted). `what` ends the line: `Nothing was <what>.`"""
    try:
        items, _ = read(home, sid)
    except QueueUnreadable as e:
        raise PileadError(f"the queue of {sid} cannot be read: {e.path} — its packets could not be handed to a "
                          f"successor. Nothing was {what}.") from None
    if items and items[0].get("claimed_at"):
        raise PileadError(f"queued packet #{items[0]['id']} of {sid} is being delivered, or its delivery was "
                          f"interrupted; pilead queue {sid} --deliver settles it. Nothing was {what}.")


def _already_moved(home, old_sid, new_sid):
    """{old queue id: successor queue id} for the items of `old_sid` an earlier `move` already put in
    `new_sid`'s queue: found by `moved_from` on the successor's items and on its `packet_queued`
    events (an item the successor has since delivered or cancelled is in the ledger only). Raises
    QueueUnreadable for an unreadable successor queue.
    SHORTCUT: one window stays open — a death between `enqueue`'s `queue.json` write and its
    `packet_queued` line, AND the successor delivering or cancelling that item before the retry, lets
    the retry queue it again. Closing it needs a per-item intent record both sides can read (e.g. the
    successor's body file named after `moved_from`)."""
    def key(m):
        return m.get("queue_id") if isinstance(m, dict) and m.get("session_id") == old_sid else None
    done = {}
    for e in ledger.read(home, session_id=new_sid, event="packet_queued"):
        k = key(e.get("moved_from"))
        if _whole(k):
            done[k] = e.get("queue_id")
    for it in read(home, new_sid)[0]:
        k = key(it.get("moved_from"))
        if _whole(k):
            done[k] = it["id"]
    return done


def move(home, old_sid, new_sid, sending=None):
    """Move every item of `old_sid`'s queue to `new_sid`'s, oldest first, through `enqueue` (new ids, new
    body files copied from the old ones, the original `source` and `heavy_override`; `last_error` and
    `error_kind` start empty). An item whose `source` or `body_path` is the file `sending` (the packet
    being sent as the successor's packet 1) is dropped, not moved. Then `old_sid`'s queue keeps its
    `next_id` and its body files and has no items. Ledger `queue_moved` per item moved, `queue_deduped`
    per item dropped. Returns (moved, dropped). No queue file: (0, 0) and nothing is written. Raises
    QueueUnreadable, and PileadError for a claimed head or a body that cannot be read — before any item
    is moved.

    Each moved item carries `moved_from` (this item's session and id), in the successor's queue and on
    its `packet_queued` event, and the old queue is emptied only after the loop — so a move that died
    midway and is run again finds the items it already moved (still queued there, or queued there
    per the ledger) and does not enqueue them a second time: each item reaches the successor once."""
    home = Path(home)

    def claimed_head(items):
        return PileadError(f"queued packet #{items[0]['id']} of {old_sid} is being delivered, or its delivery "
                           f"was interrupted; pilead queue {old_sid} --deliver settles it. Nothing was moved.")
    items, _ = read(home, old_sid)  # without the lock first: a refusal, or nothing to move, writes nothing
    if not items:
        return 0, 0
    if items[0].get("claimed_at"):
        raise claimed_head(items)
    sent_key = _file_key(sending)
    moved = dropped = 0
    with _held(home, old_sid):
        items, next_id = read(home, old_sid)
        if not items:
            return 0, 0
        if items[0].get("claimed_at"):
            raise claimed_head(items)
        plan = []
        for it in items:
            if sent_key and sent_key in (_file_key(it.get("source")), _file_key(it.get("body_path"))):
                plan.append((it, None))
                continue
            try:
                body = Path(it.get("body_path") or "").read_text(encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                raise PileadError(f"the body of queued packet #{it['id']} of {old_sid} ({it.get('body_path')}) "
                                  f"cannot be read ({type(e).__name__}); nothing was moved.") from None
            plan.append((it, body))
        done = _already_moved(home, old_sid, new_sid)
        logged = {e.get("queue_id") for e in ledger.read(home, session_id=old_sid, event="queue_moved")
                  if e.get("successor") == new_sid}
        for it, body in plan:
            if body is None:
                dropped += 1
                ledger.append(home, "queue_deduped", session_id=old_sid, successor=new_sid, queue_id=it["id"],
                              source=it.get("source"))
                continue
            if it["id"] in done:  # an earlier move got this far and died
                moved += 1
                if it["id"] not in logged:
                    ledger.append(home, "queue_moved", session_id=old_sid, successor=new_sid, queue_id=it["id"],
                                  new_queue_id=done[it["id"]])
                continue
            new = enqueue(home, new_sid, body, it.get("source") or it["body_path"],
                          heavy_override=it.get("heavy_override"),
                          moved_from={"session_id": old_sid, "queue_id": it["id"]})
            moved += 1
            ledger.append(home, "queue_moved", session_id=old_sid, successor=new_sid, queue_id=it["id"],
                          new_queue_id=new["id"])
        _write(home, old_sid, [], next_id)
    return moved, dropped

"""pilead's entry: build the parser from the verb modules in pilead/verbs/ and dispatch.

Each verb is a module there exposing `ORDER` and `register(verb)`; they are discovered with pkgutil and
registered in ORDER, so a new verb is a new file and no edit here. A module's `RESOLVE` names the arguments
that are ids; `main` passes each through `names.resolve` before the verb runs (a project name or a unique
id prefix becomes the id). A module without it, or with one that is not a tuple of texts, gets its
arguments as typed."""
import argparse
import importlib
import pkgutil
import sys

from . import names, state, verbs
from .state import PileadError


def _verb_modules(pkg=verbs):
    """Every verb module in `pkg`, sorted by (ORDER, name). A module that raises on import, or lacks an
    int ORDER or a callable register, is skipped with one line on stderr — one broken verb must never
    take `close` (or any other verb) down with it."""
    mods = []
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):
            continue
        name = f"{pkg.__name__}.{m.name}"
        try:
            mod = importlib.import_module(name)
            if not isinstance(getattr(mod, "ORDER", None), int):
                raise AttributeError("no integer ORDER")
            if not callable(getattr(mod, "register", None)):
                raise AttributeError("no callable register")
        except Exception as e:
            _skipped(name, e)
            continue
        mods.append(mod)
    return sorted(mods, key=lambda m: (m.ORDER, m.__name__))


def _skipped(name, e):
    """Exactly one stderr line per skipped module (the message's own newlines folded)."""
    msg = " ".join(str(e).split())
    print(f"pilead: verb module {name!r} failed to load: {type(e).__name__}: {msg}", file=sys.stderr)


def _parser():
    """A fresh top-level parser and the `verb(name, fn, help_)` that adds a sub-command to it."""
    ap = argparse.ArgumentParser(prog="pilead", description="Lead/executor CLI for pi (iTerm2).")
    sub = ap.add_subparsers(dest="verb", required=True, metavar="VERB")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="state dir (default $PI_LEAD_HOME or ~/.pi-lead)")

    def verb(name, fn, help_):
        p = sub.add_parser(name, parents=[common], help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    return ap, verb


def build_parser():
    """Each module's `register` runs first against a throwaway parser; one that raises there is skipped.
    The rest are registered on a fresh real parser; if one raises there (after adding verbs or not), it is
    skipped too and the real parser is rebuilt from scratch without it, until a pass completes with no
    failure. So a `register` that adds a verb and then raises leaves nothing of itself in `pilead --help`,
    the choices or the reachable sub-commands, on either pass."""
    mods = []
    for mod in _verb_modules():
        try:
            mod.register(_parser()[1])  # the trial run; its parser is discarded
        except Exception as e:
            _skipped(mod.__name__, e)
            continue
        mods.append(mod)
    while True:  # each failed pass drops one more module, so this ends within len(mods) + 1 passes
        ap, verb = _parser()
        ap.pilead_resolve = {}
        for mod in mods:
            try:
                mod.register(_recording(verb, mod, ap.pilead_resolve))
            except Exception as e:  # passed its trial and failed on the real parser
                _skipped(mod.__name__, e)
                mods = [m for m in mods if m is not mod]
                break
        else:
            return ap


def _declared(mod):
    """The module's RESOLVE when it is a tuple of texts, else () (its arguments are taken as typed)."""
    r = getattr(mod, "RESOLVE", None)
    if isinstance(r, tuple) and all(isinstance(x, str) for x in r):
        return r
    return ()


def _recording(verb, mod, table):
    """`verb`, also noting in `table` which arguments of each sub-command `mod` adds are ids."""
    def v(name, fn, help_):
        p = verb(name, fn, help_)
        table[name] = _declared(mod)
        return p
    return v


def _resolve_ids(a, fields):
    """Each id argument named in `fields` that holds a non-empty text (or a list of them) through
    `names.resolve`. An id that comes from the environment is read by the verb itself, never here."""
    if not fields:
        return
    home = state.home_dir(getattr(a, "home", None))
    for f in fields:
        v = getattr(a, f, None)
        if isinstance(v, str) and v:
            setattr(a, f, names.resolve(home, v))
        elif isinstance(v, list):
            setattr(a, f, [names.resolve(home, x) if isinstance(x, str) and x else x for x in v])


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        _resolve_ids(a, getattr(ap, "pilead_resolve", {}).get(getattr(a, "verb", None), ()))
        rc = a.fn(a)
    except PileadError as e:
        print(f"pilead: {e}", file=sys.stderr)
        return e.code
    return rc or 0

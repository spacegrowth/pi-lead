"""models.json cache of `pi --list-models` + tier-alias resolution."""
import json
import re
import subprocess
import time

from .state import PileadError, atomic_write

MAX_AGE = 86400
FAMILIES = ("haiku", "sonnet", "opus", "fable")  # also the tiers, lowest first (`tier`, `above_ceiling`)
ALIASES = FAMILIES + ("dsflash", "dspro")


class UnmatchedAlias(PileadError):
    """A known alias that matches no model in models.json (the case `executor_fallback_model` covers)."""


def _anthropic_id(model):
    """The id of a `provider/id` model whose provider is `anthropic` (lower-cased), else None."""
    provider, _, mid = str(model).partition("/")
    return mid.lower() if provider.strip().lower() == "anthropic" else None


def tier(model):
    """The tier of a `provider/id` model: for provider `anthropic`, the first of haiku / sonnet / opus /
    fable its id contains (case-insensitive); else None (no tier word, or another provider)."""
    mid = _anthropic_id(model)
    return None if mid is None else next((t for t in FAMILIES if t in mid), None)


def above_ceiling(model, ceiling):
    """True when `model` (`provider/id`) ranks above `ceiling`: it has a tier above the ceiling's, or it is
    an `anthropic` model whose id holds no tier word. A model of any other provider is unranked and never
    above. A ceiling that is not one of the tier words puts every `anthropic` model above it."""
    if _anthropic_id(model) is None:
        return False
    t = tier(model)
    if t is None or ceiling not in FAMILIES:
        return True
    return FAMILIES.index(t) > FAMILIES.index(ceiling)


def parse_list_models(text):
    out = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 3 or cols[0] == "provider":
            continue
        out.append({"provider": cols[0], "id": cols[1], "context": cols[2]})
    return out


def refresh(home):
    try:
        r = subprocess.run(["pi", "--list-models"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PileadError(f"cannot run `pi --list-models`: {e}", 1)
    models = parse_list_models(r.stdout) or parse_list_models(r.stderr)
    if not models:
        raise PileadError("`pi --list-models` returned no models", 1)
    atomic_write(home / "models.json", json.dumps({"fetched": int(time.time()), "models": models}, indent=2) + "\n")
    return models


def load(home):
    p = home / "models.json"
    try:
        d = json.loads(p.read_text())
        if time.time() - d["fetched"] < MAX_AGE and d["models"]:
            return d["models"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return refresh(home)


def _version(model_id, family):
    """(5,), (4, 6), (5, 5) … from the digits after the family; date suffixes (8 digits) ignored."""
    rest = model_id.split(family, 1)[1]
    return tuple(int(t) for t in re.findall(r"\d+", rest) if len(t) < 8)


def pick(models, spec):
    """The `provider/id` a tier alias names in `models` (a list as `load` returns it); UnmatchedAlias when
    it matches none. `spec` must be one of ALIASES."""
    if spec in FAMILIES:
        cands = [m for m in models if m["provider"] == "anthropic" and spec in m["id"]]
        cands.sort(key=lambda m: _version(m["id"], spec))
    else:
        key = "flash" if spec == "dsflash" else "pro"
        cands = [m for m in models if m["provider"] == "deepseek" and key in m["id"]]
        cands.sort(key=lambda m: m["id"])
    if not cands:
        raise UnmatchedAlias(f"alias {spec!r} matches no model in models.json; known aliases: {', '.join(ALIASES)}")
    return f"{cands[-1]['provider']}/{cands[-1]['id']}"


def resolve(home, spec):
    """provider/id passes through verbatim; a tier alias resolves against models.json."""
    if "/" in spec:
        return spec
    if spec not in ALIASES:
        raise PileadError(f"unknown model alias {spec!r}; known aliases: {', '.join(ALIASES)} (or use provider/id)")
    return pick(load(home), spec)

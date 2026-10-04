"""Text rendering shared by the table verbs (`list`, `stats`): colour only on a terminal with NO_COLOR
unset, fixed-width cells cut with `…`, and short model ids. Not a verb module, so one broken verb never
takes another down with it."""
import os
import sys

_ANSI = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
         "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m"}


def color_on():
    try:
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    except Exception:
        return False


def c(text, *styles):
    if not styles or not color_on():
        return text
    return "".join(_ANSI[s] for s in styles) + text + _ANSI["reset"]


def cell(text, width):
    """Pad a short value; cut a long one with `…` so it never shifts the columns after it."""
    s = str(text) if text not in (None, "") else "-"
    return (s[: width - 1] + "…") if len(s) > width else s.ljust(width)


def table(cols, rows):
    """cols: [(header, cap)]; rows: [[(text, styles), …]]. Returns the lines, header first. Widths fit
    the content up to each column's cap; the last column is not padded."""
    widths = [min(cap, max([len(h)] + [len(str(r[i][0]) if r[i][0] not in (None, "") else "-") for r in rows]))
              for i, (h, cap) in enumerate(cols)]
    out = ["  ".join(cell(h, w) for (h, _), w in zip(cols, widths)).rstrip()]
    for r in rows:
        parts = []
        for i, ((text, styles), w) in enumerate(zip(r, widths)):
            txt = cell(text, w)
            if i == len(widths) - 1:
                txt = txt.rstrip()
            parts.append(c(txt, *styles))
        out.append("  ".join(parts).rstrip())
    return out


def model_id(model):
    """`provider/id` → `id`; None/empty → None."""
    if not model:
        return None
    return str(model).split("/", 1)[1] if "/" in str(model) else str(model)


def relative_age(secs):
    """30s ago / 12m ago / 2h ago / 3d ago; `-` when unknown."""
    if secs is None:
        return "-"
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"

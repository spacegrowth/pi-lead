"""Packet lint: a zero-token sanity pass over an outgoing packet, ported from claude-relay 0.5.4's
`lib/lead_guard.py` (the section headed "packet lint", `packet_reading_bytes` and `_PATH_RE`). Advisory
only: `lint_packet` never blocks anything, it only lists findings. Pure given its inputs; never raises.

Left out of relay's rule set, and why (see README "Packet lint"):
- the three `mcp-*` rules (`mcp-unparsable`, `mcp-mentioned-not-declared`, `mcp-unknown-server`): pi has
  no MCP support, so there is nothing to warn about.
- `context-unparsable`, `context-200k-big-reading`, `context-auto-1m`, `context-1m-on-haiku`: pi's model
  list carries the context window; pi-lead has no `CONTEXT:` declaration for a packet to get wrong.

`effort-unparsable` is relay's rule, with pi's seven thinking levels (`EFFORT_LEVELS`): `pilead spawn`
reads a packet's first `EFFORT:` line (`packet_effort`) when no `--effort` is given, and ignores a value
that is not a level, so lint warns about that value. An `EFFORT:` line with a level gives no finding.

`line-ignored` is new here: it stands in for the left-out rules by telling the packet's author that
pi-lead silently ignores an `MCP:` / `CONTEXT:` line, rather than saying nothing.
"""
import os
import re
import sys
from pathlib import Path

# ── ported verbatim from claude-relay's lib/lead_guard.py ──────────────────────────────────────────
PRECONDITIONS_RE = re.compile(r"^#{1,6}[ \t]*preconditions\b", re.IGNORECASE | re.MULTILINE)
PACKET_MCP_RE = re.compile(r"^\s*(?:[-*>]\s*)?\**\s*MCP\s*\**\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
CONTEXT_RE = re.compile(r"^\s*(?:[-*>]\s*)?\**\s*CONTEXT\s*\**\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
EFFORT_RE = re.compile(r"^\s*(?:[-*>]\s*)?\**\s*EFFORT\s*\**\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
COMMIT_RE = re.compile(r"\bgit (commit|push)\b|\b(commit|push) (your|the|these|all|this|it)\b", re.IGNORECASE)
ASK_RE = re.compile(r"\b(ask|check with|confirm with|clarify with) (the )?(user|human|lead|me)\b", re.IGNORECASE)
ACCEPTANCE_CMD_RE = re.compile(
    r"^(?:#{1,6}[ \t]*)?acceptance\b.*?(?:```|^\s*(?:pytest|npm|make|python3 -m|go test)\b)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL)
FILES_LIST_RE = re.compile(r"^\s*files\s*:", re.IGNORECASE | re.MULTILINE)
BACKTICK_PATH_RE = re.compile(r"`[^`\n]*[/.][^`\n]*`")
REPRO_RE = re.compile(r"\brepro(?:duce)?\b|\bsteps to\b", re.IGNORECASE)
_PATH_RE = re.compile(
    r"(?<![\w/.-])((?:~|\.{1,2})?/?(?:[\w.@-]+/)+[\w.@-]+\.[A-Za-z0-9]{1,8}"
    r"|[\w.@-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|rb|php|cs|c|h|cpp|hpp|md|txt|json|yaml|yml|toml|sql|sh|html|css))"
    r"(?![\w/])")

OPUS_SHAPE_WORDS = ("investigate", "figure out", "root cause", "unknown", "diagnose", "why does")
HAIKU_DISQUALIFY_WORDS = ("investigate", "figure out", "root cause", "why", "unclear", "diagnose")

READING_FRONT_LOADED_BYTES = 1_200_000  # packet_reading_bytes() total at/above which every relaunch pays

# pi's thinking levels (`pi --thinking <level>`, pi 0.87.1 docs/cli.md), lowest first: the one list `spawn
# --effort`, a packet's `EFFORT:` line, config `executor_default_effort` and `effort-unparsable` accept.
EFFORT_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def effort_level(raw):
    """`raw` as a thinking level (lower-cased, surrounding spaces ignored) when it is one of
    EFFORT_LEVELS, else None. Never raises."""
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower()
    return v if v in EFFORT_LEVELS else None


def packet_effort(body):
    """The value of the packet's first `EFFORT:` line (as written, surrounding spaces stripped), or None
    when it has none. Whether that value is a level is `effort_level`'s question. Never raises."""
    lines = (body or "").splitlines()
    line = _first_matching_line(EFFORT_RE, lines)
    return EFFORT_RE.search(line).group(1) if line is not None else None


# pi-lead's own alias set (lib/pilead/models.py ALIASES), not Claude Code's four families.
TIER_FAMILIES = ("haiku", "sonnet", "opus", "fable")


def model_tier(model):
    """`None` when `model` is `None`; otherwise, on the lower-cased text, the first of haiku / sonnet /
    opus / fable it contains; else "dsflash" when it is exactly that alias or contains "flash"; else
    "dspro" when it is exactly that alias or contains "pro"; else `None`. Never raises."""
    if model is None:
        return None
    s = str(model).strip().lower()
    for t in TIER_FAMILIES:
        if t in s:
            return t
    if s == "dsflash" or "flash" in s:
        return "dsflash"
    if s == "dspro" or "pro" in s:
        return "dspro"
    return None


def packet_reading_bytes(body, cwd=None):
    """Total size of the files a packet refers to (paths that exist — relative to `cwd` or
    absolute/home). Ported unchanged from claude-relay's `packet_reading_bytes` (BUG-lib-9: a
    `~name/...` path naming another user's home directory raises RuntimeError from
    `Path.expanduser()` on some platforms — that candidate goes through the same try/except as every
    other candidate, never ahead of it, so a packet merely mentioning such a path never crashes)."""
    total, seen = 0, set()
    for m in _PATH_RE.finditer(body or ""):
        raw = m.group(1)
        cands = []
        if cwd and not raw.startswith(("/", "~")):
            cands.append(Path(cwd) / raw)
        try:
            cands.append(Path(raw).expanduser())
        except Exception:
            pass
        for c in cands:
            try:
                rp = c.resolve()
                if rp in seen:
                    break
                if rp.is_file():
                    seen.add(rp)
                    total += rp.stat().st_size
                    break
            except Exception:
                continue
    return total


def _first_matching_line(regex, lines):
    """The first line (verbatim, as it appears in the packet) that `regex` matches. Matched line by
    line, not against the whole body: `regex`'s `^\\s*...` — ported from relay unchanged — would
    otherwise let `\\s*` sweep in blank lines above the real one under MULTILINE, since `\\s` matches
    a newline too."""
    return next((ln for ln in lines if regex.search(ln)), None)


def _line_ignored(text):
    out = []
    lines = text.splitlines()
    mcp_line = _first_matching_line(PACKET_MCP_RE, lines)
    if mcp_line is not None:
        out.append(("warn", "line-ignored",
                     f"pi-lead does not read this line: pi has no MCP support: {mcp_line!r}"))
    ctx_line = _first_matching_line(CONTEXT_RE, lines)
    if ctx_line is not None:
        out.append(("warn", "line-ignored",
                     "pi-lead does not read this line: the model's context window comes from pi's "
                     f"model list: {ctx_line!r}"))
    return out


def _effort_unparsable(text):
    value = packet_effort(text)
    if value is None or effort_level(value) is not None:
        return []
    return [("warn", "effort-unparsable", f"EFFORT: line present but value isn't one of "
             f"{'/'.join(EFFORT_LEVELS)}: '{value}' — pilead ignores it")]


def lint_packet(body, cwd=None, model=None):
    """List of `(level, code, message)` findings, `level` in {"warn", "info"}, in the order of the
    README's "Packet lint" table. Pure given its inputs; never raises."""
    out = []
    text = body or ""
    low = text.lower()
    if len(text.strip()) < 80:
        out.append(("warn", "short-packet", "packet body is very short — an executor treats it cold, "
                    "with no access to this conversation; spell out the task and acceptance criteria"))
    if PRECONDITIONS_RE.search(text) is None:
        out.append(("warn", "no-preconditions", "no ## Preconditions section — authoring one forces the "
                    "world-state walk before send (checkout pulled? code live? DDL applied?)"))
    out.extend(_line_ignored(text))
    out.extend(_effort_unparsable(text))
    rb = packet_reading_bytes(text, cwd=cwd)
    if rb >= READING_FRONT_LOADED_BYTES:
        out.append(("warn", "reading-front-loaded", f"packet front-loads ~{rb // 1024}KB of required "
                    "reading — every relaunch re-reads that prefix; trim the required reading to what "
                    "the first task needs"))
    if COMMIT_RE.search(text):
        out.append(("warn", "asks-to-commit", "packet tells the executor to commit/push — executors stage "
                    "only (commit/push are denied); phrase it as 'stage for review'"))
    if ASK_RE.search(text):
        out.append(("warn", "asks-to-ask", "packet tells the executor to ask someone — executors never ask "
                    "in the tab; phrase it as 'stop and report the blocker'"))
    tier = model_tier(model)
    has_acceptance_cmd = bool(ACCEPTANCE_CMD_RE.search(text))
    has_file_list = bool(FILES_LIST_RE.search(text)) or len(BACKTICK_PATH_RE.findall(text)) >= 2
    if tier not in ("haiku", "dsflash") and has_acceptance_cmd and has_file_list \
            and not any(w in low for w in HAIKU_DISQUALIFY_WORDS):
        out.append(("info", "shape-haiku", "this packet names its files and has a command that checks "
                    "it's done — a cheap model (haiku, dsflash) can do this; you picked something bigger"))
    if tier not in ("opus", "fable") and any(w in low for w in OPUS_SHAPE_WORDS) \
            and REPRO_RE.search(text) is None:
        out.append(("info", "shape-opus", "this packet asks the executor to figure something out "
                    "(investigate / root cause / why) and gives no repro — a wrong answer would look "
                    "right; consider opus"))
    return out


def advise(body, cwd, model, env=None, stream=None):
    """Print one line per `lint_packet` finding to `stream` (default `sys.stderr`):
    `  ⚠ lint[<code>]: <message>` for a warn, `  ℹ lint[<code>]: <message>` for an info. Nothing when
    there are no findings, and nothing when `env` (default `os.environ`) has `PILEAD_NO_LINT` set to a
    non-empty value. Never raises: any exception inside is swallowed, and a stream that cannot represent
    a character has that character replaced rather than failing."""
    try:
        if env is None:
            env = os.environ
        if env.get("PILEAD_NO_LINT"):
            return
        if stream is None:
            stream = sys.stderr
        findings = lint_packet(body, cwd=cwd, model=model)
        if not findings:
            return
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
        for level, code, message in findings:
            mark = "⚠" if level == "warn" else "ℹ"
            line = f"  {mark} lint[{code}]: {message}"
            try:
                print(line, file=stream)
            except UnicodeEncodeError:
                enc = getattr(stream, "encoding", None) or "ascii"
                print(line.encode(enc, errors="replace").decode(enc, errors="replace"), file=stream)
    except Exception:
        pass


def format_findings(findings, prefix=""):
    """The text `pilead lint` prints: one `prefix + mark + [code] + message` line per finding
    (⚠ for a warn, ℹ for an info), or, with no findings, the single line `✓ packet lint: no findings`."""
    if not findings:
        return f"{prefix}✓ packet lint: no findings"
    lines = []
    for level, code, message in findings:
        mark = "⚠" if level == "warn" else "ℹ"
        lines.append(f"{prefix}{mark} [{code}] {message}")
    return "\n".join(lines)

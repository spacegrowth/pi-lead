"""The memo copy a successor lead starts from (claude-relay 0.5.4's `build_handoff_copy`, `AFTERCARE_FOOTER` and
`dropped_discipline_markers`, with pi-lead's verbs).

`pilead handoff <memo>` never changes the memo the outgoing lead wrote. It writes its own copy,
`<home>/handoffs/<successor sid>.md`: the memo's text followed by the SUCCESSOR AFTERCARE section, which tells the
successor how to check its registration, that the outgoing lead's tab is closed only after the human says yes,
what it inherited and that no posture is handed on. A memo that already carries such a section (a memo copied
from an earlier copy) ends with exactly one.

A "marker" is a word in square brackets made of lower-case letters and `-`, such as `[ops-not-lead-work]`: a rule
a lead passes on to its successor word for word. `dropped_markers` names the ones the memo the caller was started
from carried and the new memo does not. Nothing here reads or writes a file."""
import re

MARKER_RE = re.compile(r"\[[a-z-]+\]")  # relay 0.5.4 DISCIPLINE_MARKER_RE
AFTERCARE_HEAD = "(pi-lead — SUCCESSOR AFTERCARE"


def dropped_markers(inherited_text, memo_text):
    """The markers in `inherited_text` that do not appear in `memo_text`, in first-seen order, each once."""
    out = []
    memo_text = memo_text or ""
    for m in MARKER_RE.findall(inherited_text or ""):
        if m not in out and m not in memo_text:
            out.append(m)
    return out


def strip_aftercare(text):
    """`text` without an aftercare section: from a line `---` directly followed by a line that begins
    `(pi-lead — SUCCESSOR AFTERCARE` to the end."""
    lines = text.splitlines(keepends=True)
    for i in range(len(lines) - 1):
        if lines[i].rstrip("\r\n") == "---" and lines[i + 1].startswith(AFTERCARE_HEAD):
            return "".join(lines[:i])
    return text


def _word(value):
    return value if isinstance(value, str) and value else "unknown"


def aftercare(successor_sid, project, moved, pins, posture):
    """The SUCCESSOR AFTERCARE section. `posture` is (autonomous, tier): autonomous True / False / None (could not
    be read), tier a word or None (could not be read); a posture that could not be read is written `unknown`."""
    autonomous, tier = posture
    auto_word = "on" if autonomous is True else "off" if autonomous is False else "unknown"
    lines = [
        "---",
        "(pi-lead — SUCCESSOR AFTERCARE; do not remove)",
        f"You were registered by pilead handoff under the session id {successor_sid}, before your first turn.",
        f"1. CHECK YOUR REGISTRATION: run pilead lead-start \"$PI_SESSION_ID\" --project '{project}'. If your session",
        f"   id is not the one above, also run: pilead takeover {successor_sid}",
        "2. THE OUTGOING LEAD'S TAB is recorded in your lead record. Close it only after the human has said yes:",
        "   pilead close-predecessor. Never close it unasked.",
        f"3. EXECUTORS: {moved} session(s) of the outgoing lead are yours now; pilead list shows them.",
        "4. REVIEWS: review every report with the pilead-review skill before you commit, and read its findings.",
        f"5. POSTURE: the outgoing lead's posture was autonomous {auto_word}, tier {_word(tier)}. Neither is handed on: you",
        "   start in the configured default, and only the human grants another.",
    ]
    if pins:
        lines.append(f"6. PINNED: {len(pins)} pinned session(s) are yours: {', '.join(pins)} — nothing closes them by "
                     "itself; pilead keep <sid> --off releases one.")
    return "\n".join(lines) + "\n"


def build_copy(memo_text, successor_sid, project, moved, pins, posture):
    """The memo copy: the memo's text (any aftercare section it carries removed) followed by the section."""
    body = strip_aftercare(memo_text)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + aftercare(successor_sid, project, moved, pins, posture)

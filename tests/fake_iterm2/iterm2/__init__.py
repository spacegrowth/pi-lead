"""Fake `iterm2` package (tests only) — shadows the real one via PYTHONPATH/sys.path in conftest, so no
test can ever reach iTerm2's real Python API. Behaviour is read from the environment at CALL time:

  FAKE_ITERM2          unset/"missing" → importing this package raises ImportError (not installed)
                       "ok"      → a fake app whose windows/tabs/sessions come from FAKE_ITERM2_LAYOUT
                       "hang"    → Connection.async_create never returns (the caller's timeout fires)
                       "refuse"  → Connection.async_create raises ConnectionRefusedError (API disabled)
  FAKE_ITERM2_LAYOUT   JSON: [window][tab][session id, ...]
  FAKE_ITERM2_LOG      every async_create_tab appends {"window": wi, "index": i} as a JSON line; every
                       async_set_tabs appends {"window": wi, "set_tabs": [[session ids of each tab], ...]}
  FAKE_ITERM2_TTYS     JSON object {session id: tty}: what Session.async_get_variable("tty") answers
"""
import asyncio
import json
import os

if os.environ.get("FAKE_ITERM2", "missing") == "missing":
    raise ImportError("fake iterm2: not installed (tests)")

NEW_SESSION_ID = "00000000-0000-4000-8000-00000000BEEF"


class Session:
    def __init__(self, session_id):
        self.session_id = session_id

    async def async_get_variable(self, name):
        if name != "tty":
            return None
        try:
            ttys = json.loads(os.environ.get("FAKE_ITERM2_TTYS", "{}"))
        except ValueError:
            ttys = {}
        return ttys.get(self.session_id, "") if isinstance(ttys, dict) else ""


class Tab:
    def __init__(self, session_ids):
        self.sessions = [Session(s) for s in session_ids]

    @property
    def current_session(self):
        return self.sessions[0] if self.sessions else None


class Window:
    def __init__(self, wi, tabs):
        self._wi = wi
        self.tabs = [Tab(t) for t in tabs]

    async def async_create_tab(self, profile=None, command=None, index=None, profile_customizations=None):
        log = os.environ.get("FAKE_ITERM2_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps({"window": self._wi, "index": index}) + "\n")
        tab = Tab([NEW_SESSION_ID])
        self.tabs.insert(len(self.tabs) if index is None else index, tab)
        return tab

    async def async_set_tabs(self, tabs):
        self.tabs = list(tabs)
        log = os.environ.get("FAKE_ITERM2_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps({"window": self._wi,
                                    "set_tabs": [[s.session_id for s in t.sessions] for t in self.tabs]}) + "\n")


class App:
    def __init__(self, layout):
        self.windows = [Window(i, w) for i, w in enumerate(layout)]


class Connection:
    @staticmethod
    async def async_create():
        mode = os.environ.get("FAKE_ITERM2")
        if mode == "hang":
            await asyncio.sleep(3600)
        if mode == "refuse":
            raise ConnectionRefusedError("fake iterm2: python api disabled")
        return Connection()


async def async_get_app(connection):
    return App(json.loads(os.environ.get("FAKE_ITERM2_LAYOUT", "[]")))

"""The Desktop for KDE Plasma on X11: windows and keys through X11 (voice/desktop/x11.py),
the clipboard through Klipper, terminals through Konsole's D-Bus interface."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from voice.agents import CLAUDE_DAEMON, SHELLS, ancestors, cmdline_has, comm, nearest_agent, process
from voice.desktop import Tab, Window
from voice.desktop.generic_x11 import AnyTerminal, GenericX11

KLIPPER = ("org.kde.klipper", "/klipper")
TYPE_PAUSE = 0.15  # seconds between the text and its Enter, so Konsole takes them in order


def qdbus(service: str, *args: str) -> str:
    return subprocess.run(["qdbus", service, *args], capture_output=True, text=True).stdout.strip()


class Konsole(AnyTerminal):
    def tab_of_caller(self) -> Tab:
        """KONSOLE_DBUS_SESSION is trusted only when that tab's foreground program is
        one of our ancestors with no Claude daemon in between. Otherwise the tab is
        found by its "<workspace> : claude" title, from the agent's folder upwards,
        and left empty when that is not unique. An agent in the Cursor editor has
        no tab at all: hands-free reaches it through its window."""
        agent = nearest_agent()
        service = os.environ.get("KONSOLE_DBUS_SERVICE", "")
        if (agent and agent.is_editor) or not service:
            return Tab()
        session = os.environ.get("KONSOLE_DBUS_SESSION", "")
        chain = ancestors(os.getpid())
        daemon = any(cmdline_has(p.pid, CLAUDE_DAEMON) for p in chain)
        foreground = qdbus(service, session, "foregroundProcessId") if session else ""
        if not daemon and foreground.isdigit() and int(foreground) in {p.pid for p in chain}:
            return Tab(service, session)
        titles = {s: qdbus(service, s, "title", "1") for s in qdbus(service).split() if s.startswith("/Sessions/")}
        starts = ([agent.folder] if agent else []) + [Path.cwd()]
        for folder in (f for start in starts for f in (start, *start.parents)):
            tabs = [s for s, title in titles.items() if title == f"{folder.name} : claude"]
            if len(tabs) == 1:
                return Tab(service, tabs[0])
            if tabs:
                break  # several Claude tabs in that folder: do not guess
        return Tab()

    def agent_running(self, tab: Tab) -> bool:
        """If the agent quit, the tab is a plain shell, and Enter after typed words would run them."""
        foreground = qdbus(tab.owner, tab.address, "foregroundProcessId")
        return foreground.isdigit() and any(p.agent for p in [process(int(foreground))] if p)

    def type_line(self, tab: Tab, text: str) -> bool:
        for chunk in (text, "\r"):
            if subprocess.run(["qdbus", tab.owner, tab.address, "sendText", chunk],
                              capture_output=True).returncode != 0:
                return False
            time.sleep(TYPE_PAUSE)
        return True

    def only_a_shell(self, window: Window) -> bool:
        if not window.is_app("konsole"):
            return super().only_a_shell(window)  # another terminal: its one shell child's foreground job
        try:
            service = f"org.kde.konsole-{window.pid}"
            windows = [p for p in qdbus(service).split() if re.fullmatch(r"/Windows/\d+", p)]
            if len(windows) != 1:
                return False
            tab = qdbus(service, windows[0], "currentSession")
            return comm(int(qdbus(service, f"/Sessions/{tab}", "foregroundProcessId"))) in SHELLS
        except (OSError, ValueError, IndexError):
            return False


class KdeX11(GenericX11):
    """Plain X11 windows and keys, with KDE's clipboard (Klipper) and terminal (Konsole)."""

    def __init__(self) -> None:
        super().__init__()
        self.terminals = Konsole(self)

    def set_clipboard(self, text: str) -> bool:
        """Through Klipper, KDE's clipboard, and checked."""
        os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        if subprocess.run(["qdbus", *KLIPPER, "setClipboardContents", text], capture_output=True).returncode != 0:
            return False
        return qdbus(*KLIPPER, "getClipboardContents") == text.strip()

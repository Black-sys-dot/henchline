"""The Desktop for KDE Plasma on X11: windows and keys through X11 (voice/x11.py),
the clipboard through Klipper, terminals through Konsole's D-Bus interface."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from voice import x11
from voice.agents import CLAUDE_DAEMON, ancestors, cmdline_has, nearest_agent, process, terminal_foreground
from voice.desktop import Desktop, Tab, Terminals, Window

KEYS = {"ctrl": x11.CONTROL, "shift": x11.SHIFT, "alt": x11.ALT, "return": x11.RETURN, "f12": x11.F12, "v": x11.V}
SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"}
TYPE_PAUSE = 0.15  # seconds between the text and its Enter, so Konsole takes them in order


def qdbus(service: str, *args: str) -> str:
    return subprocess.run(["qdbus", service, *args], capture_output=True, text=True).stdout.strip()


def comm(pid: int | str) -> str:
    return Path(f"/proc/{pid}/comm").read_text().strip()


class Konsole(Terminals):
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
        foreground = qdbus(tab.service, tab.session, "foregroundProcessId")
        return foreground.isdigit() and any(p.agent for p in [process(int(foreground))] if p)

    def type_line(self, tab: Tab, text: str) -> bool:
        for chunk in (text, "\r"):
            if subprocess.run(["qdbus", tab.service, tab.session, "sendText", chunk],
                              capture_output=True).returncode != 0:
                return False
            time.sleep(TYPE_PAUSE)
        return True

    def only_a_shell(self, window: Window) -> bool:
        try:
            if window.is_app("konsole"):
                service = f"org.kde.konsole-{window.pid}"
                windows = [p for p in qdbus(service).split() if re.fullmatch(r"/Windows/\d+", p)]
                if len(windows) != 1:
                    return False
                tab = qdbus(service, windows[0], "currentSession")
                fg = int(qdbus(service, f"/Sessions/{tab}", "foregroundProcessId"))
            else:  # another terminal: its one shell child, and that shell's foreground job
                children = Path(f"/proc/{window.pid}/task/{window.pid}/children").read_text().split()
                shells = [c for c in children if comm(c) in SHELLS]
                if len(shells) != 1:
                    return False
                fg = terminal_foreground(int(shells[0]))
            return comm(fg) in SHELLS
        except (OSError, ValueError, IndexError):
            return False


class KdeX11(Desktop):
    def __init__(self) -> None:
        os.environ.setdefault("DISPLAY", ":0")  # xprop and Xlib, when started outside the session
        self.terminals = Konsole()

    def focused(self) -> Window:
        wid = x11.active_window()
        return Window(wid, x11.window_class(wid), x11.window_role(wid), x11.window_pid(wid) if wid else 0)

    def windows(self, app: str) -> list[tuple[str, str]]:
        return [(wid, x11.window_title(wid)) for wid in x11.windows() if f'"{app}"' in x11.window_class(wid)]

    def set_clipboard(self, text: str) -> bool:
        return x11.set_clipboard(text)

    def press(self, *keys: str) -> None:
        with x11.Display() as display:
            display.press(*(KEYS[key.lower()] for key in keys))

    def raise_window(self, window_id: str) -> None:
        with x11.Display() as display:
            display.raise_window(window_id)

"""What push-to-talk, talkback and hands-free need from the desktop, and nothing else.

Every window, key, clipboard and terminal call goes through a Desktop. Today
there is one: KDE on X11 (voice/kde_x11.py). Another desktop, or Wayland, is
one new module that fills in these methods; no caller changes.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True)
class Window:
    id: str
    app: str  # the window class, lower case, e.g. '"konsole", "konsole"'
    role: str = ""
    pid: int = 0

    def is_app(self, *names: str) -> bool:
        return any(f'"{name}"' in self.app for name in names)


@dataclass(frozen=True)
class Tab:
    """Where an agent's terminal tab lives; empty when it has none (e.g. an editor's agent)."""

    service: str = ""
    session: str = ""

    def __bool__(self) -> bool:
        return bool(self.service and self.session)


class Terminals(ABC):
    @abstractmethod
    def tab_of_caller(self) -> Tab:
        """The tab the calling agent is shown in, found from our own process tree."""

    @abstractmethod
    def agent_running(self, tab: Tab) -> bool:
        """Is an agent the tab's foreground program (not a bare shell that would run typed words)?"""

    @abstractmethod
    def type_line(self, tab: Tab, text: str) -> bool:
        """Type the text into the tab, then Enter."""

    @abstractmethod
    def only_a_shell(self, window: Window) -> bool:
        """Does that focused terminal window show a tab running nothing but a shell? Unsure: False."""


class Desktop(ABC):
    terminals: Terminals

    @abstractmethod
    def focused(self) -> Window:
        """The focused window (id "" when there is none)."""

    @abstractmethod
    def windows(self, app: str) -> list[tuple[str, str]]:
        """(id, title) of every window of that application."""

    @abstractmethod
    def set_clipboard(self, text: str) -> bool:
        """Put text on the clipboard and check it arrived."""

    @abstractmethod
    def press(self, *keys: str) -> None:
        """Press keys together, like a person: "ctrl", "shift", "alt", "return", "f12", or a letter."""

    @abstractmethod
    def raise_window(self, window_id: str) -> None:
        """Bring the window to the front and focus it."""


@cache
def current() -> Desktop:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise NotImplementedError("Wayland is not supported yet: log in to the X11 session (see the README)")
    from voice.kde_x11 import KdeX11

    return KdeX11()

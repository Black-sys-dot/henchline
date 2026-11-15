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
    title: str = ""

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
    def windows(self, app: str) -> list[Window]:
        """Every window of that application, with its title."""

    @abstractmethod
    def set_clipboard(self, text: str) -> bool:
        """Put text on the clipboard and check it arrived."""

    @abstractmethod
    def press(self, *keys: str) -> None:
        """Press keys together, like a person: "ctrl", "shift", "alt", "return", "f12", or a letter."""

    @abstractmethod
    def raise_window(self, window_id: str) -> None:
        """Ask for the window to come to the front and take focus (the window manager may refuse)."""

    @abstractmethod
    def is_front(self, window_id: str) -> bool:
        """Is that window the focused one now?"""

    def prepare_threads(self) -> None:
        """Called once before threads use the desktop (Xlib needs it); nothing by default."""


def kde_session() -> bool:
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":") or bool(os.environ.get("KDE_FULL_SESSION"))


@cache
def current() -> Desktop:
    """KDE when it is running (Klipper, Konsole), any other X11 desktop otherwise."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise NotImplementedError("Wayland is not supported yet: log in to the X11 session (see the README)")
    if kde_session():
        from voice.kde_x11 import KdeX11

        return KdeX11()
    from voice.generic_x11 import GenericX11

    return GenericX11()

"""Where the words go: what kind of window has focus, and typing into it.

Terminals and editors may have an agent reading, so they get tone marks;
Okular and a Thunderbird mail window are left alone; a terminal tab that
only runs a shell gets the words without Enter, so they are not run.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from voice import agents, x11
from voice.state import ptt_log as log
from voice.state import set_target

TERMINALS = ("konsole", "yakuake", "kitty", "alacritty", "xterm", "gnome-terminal", "wezterm", "terminator",
             "tilix", "ghostty")
EDITORS = ("cursor", "code")
SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"}
SETTLE = 0.12  # seconds between the paste and Enter, for the window to take the text


def active_window() -> tuple[str, str]:
    """WM_CLASS and WM_WINDOW_ROLE of the focused window, lower case."""
    wid = x11.active_window()
    return x11.window_class(wid), x11.window_role(wid)


def is_terminal(cls: str) -> bool:
    return any(f'"{name}"' in cls for name in TERMINALS)


def can_type(cls: str, role: str) -> bool:
    if '"okular"' in cls:
        return False
    if "thunderbird" in cls or '"mail"' in cls:
        return "msgcompose" in role or "msgcompose" in cls
    return True


def agent_window(cls: str, role: str) -> bool:
    """A terminal or editor, where an agent may be reading what is pasted."""
    return any(f'"{name}"' in cls for name in TERMINALS + EDITORS) and can_type(cls, role)


def shell_only(cls: str) -> bool:
    """A terminal whose current tab runs nothing but a shell.

    Enter there would run the dictated words as a command, so paste() leaves
    it out. Unknown cases (several Konsole windows, odd terminals) say no and
    keep the usual Enter.
    """
    if not is_terminal(cls):
        return False
    try:
        pid = x11.window_pid(x11.active_window())
        if '"konsole"' in cls:
            service = f"org.kde.konsole-{pid}"
            windows = [p for p in agents.qdbus(service).split() if re.fullmatch(r"/Windows/\d+", p)]
            if len(windows) != 1:
                return False
            tab = agents.qdbus(service, windows[0], "currentSession")
            fg = int(agents.qdbus(service, f"/Sessions/{tab}", "foregroundProcessId"))
        else:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
            shells = [c for c in children if Path(f"/proc/{c}/comm").read_text().strip() in SHELLS]
            if len(shells) != 1:
                return False
            fg = agents.terminal_foreground(int(shells[0]))
        return Path(f"/proc/{fg}/comm").read_text().strip() in SHELLS
    except (OSError, ValueError, IndexError):
        return False


def deliver(text: str, rich: str) -> bool:
    """Clipboard, then paste and Enter where that is safe; rich (tone marks) for agent windows.

    True once the words are on the clipboard: even where nothing is typed, they can be pasted by hand.
    """
    if not x11.set_clipboard(rich if agent_window(*active_window()) else text):
        log("clipboard set failed")
        return False
    paste()
    return True


def paste() -> None:
    os.environ.setdefault("DISPLAY", ":0")
    cls, role = active_window()
    set_target(agent_window(cls, role))
    if not can_type(cls, role):
        log(f"clipboard only, focus {cls.strip() or 'unknown'} role {role.strip() or 'none'}")
        return
    terminal = is_terminal(cls)
    log(f"focus {cls.strip() or 'unknown'} chord={'ctrl+shift+v' if terminal else 'ctrl+v'}")
    try:
        with x11.Display() as display:
            display.press(*((x11.CONTROL, x11.SHIFT, x11.V) if terminal else (x11.CONTROL, x11.V)))
            time.sleep(SETTLE)
            if terminal and shell_only(cls):
                log("only a shell in that terminal: pasted without Enter")
            else:
                display.press(x11.RETURN)
    except OSError as exc:
        log(str(exc))

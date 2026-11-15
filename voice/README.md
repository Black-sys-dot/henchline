# The desktop layer

Everything the tools need from the desktop (the focused window, raising a window, keys, the clipboard, typing into a terminal tab) goes through one interface, `voice/desktop/__init__.py`. Two implementations exist:

- **KDE Plasma on X11** (`voice/desktop/kde_x11.py`): Klipper for the clipboard, Konsole's D-Bus interface for terminal tabs.
- **Any other X11 desktop** (`voice/desktop/generic_x11.py`): `xclip` for the clipboard; any terminal that sets `WINDOWID` and pastes with Ctrl+Shift+V (most do; plain xterm needs `XTerm*VT100.translations: #override Shift Ctrl <Key>V: insert-selection(CLIPBOARD)`, as the test sets). Hands-free raises the terminal, checks it really is in front, pastes and presses Enter.

`tests/generic_x11/run.sh` proves the second one without touching your system: a throwaway Fedora container with a virtual screen, the Openbox window manager and two xterms, one running a pretend agent; it checks window lookup, focus, the clipboard, finding an agent's tab and typing a line into it (10 checks). It found two bugs that this laptop's KDE setup hid.

Wayland is not implemented: a new module filling in the same interface is the way in, and pull requests are welcome.

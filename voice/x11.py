"""Windows, keystrokes and the clipboard on an X11 KDE desktop: the focused
window, a window by title, raising it, pressing keys through the XTest
extension, and setting Klipper's clipboard. Push-to-talk pastes with it;
hands-free reaches Cursor's chat with it."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time

# Keysyms used here
CONTROL = 0xFFE3
SHIFT = 0xFFE1
ALT = 0xFFE9
RETURN = 0xFF0D
F12 = 0xFFC9
V = 0x76

_CLIENT_MESSAGE = 33
_SUBSTRUCTURE = (1 << 20) | (1 << 19)  # SubstructureRedirect | SubstructureNotify


def _xprop(*args: str) -> str:
    return subprocess.run(["xprop", *args], capture_output=True, text=True).stdout


def active_window() -> str:
    """Id of the focused window, or ""."""
    wid = _xprop("-root", "_NET_ACTIVE_WINDOW").strip().split()[-1:] or [""]
    return "" if wid[0] in ("", "0x0", "0") else wid[0]


def window_class(wid: str) -> str:
    """WM_CLASS, lower case, e.g. '"konsole", "konsole"'."""
    return _xprop("-id", wid, "WM_CLASS").lower() if wid else ""


def window_role(wid: str) -> str:
    return _xprop("-id", wid, "WM_WINDOW_ROLE").lower() if wid else ""


def window_title(wid: str) -> str:
    return _xprop("-id", wid, "_NET_WM_NAME").split("=", 1)[-1].strip().strip('"')


def window_pid(wid: str) -> int:
    text = _xprop("-id", wid, "_NET_WM_PID").split()
    return int(text[-1]) if text and text[-1].isdigit() else 0


def set_clipboard(text: str) -> bool:
    """Put text on KDE's clipboard (Klipper) and check it arrived."""
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    klipper = ["qdbus", "org.kde.klipper", "/klipper"]
    if subprocess.run([*klipper, "setClipboardContents", text], capture_output=True).returncode != 0:
        return False
    got = subprocess.run([*klipper, "getClipboardContents"], capture_output=True, text=True).stdout
    return got.strip() == text


def windows() -> list[str]:
    return re.findall(r"0x[0-9a-f]+", _xprop("-root", "_NET_CLIENT_LIST"))


class Display:
    """An open X display for raising windows and pressing keys; use with `with`."""

    def __init__(self) -> None:
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.x11.XInternAtom.restype = ctypes.c_ulong
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.disp = None

    def __enter__(self) -> Display:
        self.disp = self.x11.XOpenDisplay(os.environ.setdefault("DISPLAY", ":0").encode())
        if not self.disp:
            raise OSError("no X display")
        return self

    def __exit__(self, *_) -> None:
        self.x11.XCloseDisplay(self.disp)

    def press(self, *keysyms: int) -> None:
        """Hold the keys down in order and let go in reverse, like a person."""
        codes = [self.x11.XKeysymToKeycode(self.disp, k) for k in keysyms]
        for code in codes:
            self.xtst.XTestFakeKeyEvent(self.disp, code, 1, 0)
            self.x11.XFlush(self.disp)
            time.sleep(0.02)
        for code in reversed(codes):
            self.xtst.XTestFakeKeyEvent(self.disp, code, 0, 0)
            self.x11.XFlush(self.disp)
            time.sleep(0.02)

    def raise_window(self, wid: str) -> None:
        """Ask the window manager to activate the window (EWMH, as a pager)."""

        class Event(ctypes.Structure):  # XClientMessageEvent, padded to sizeof(XEvent)
            _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                        ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                        ("data", ctypes.c_long * 5), ("pad", ctypes.c_long * 12)]

        event = Event(type=_CLIENT_MESSAGE, send_event=1, window=int(wid, 16), format=32,
                      message_type=self.x11.XInternAtom(self.disp, b"_NET_ACTIVE_WINDOW", 0))
        event.data[0] = 2  # source indication: a pager, so the window manager obeys
        self.x11.XSendEvent(self.disp, self.x11.XDefaultRootWindow(self.disp), 0, _SUBSTRUCTURE,
                            ctypes.byref(event))
        self.x11.XFlush(self.disp)

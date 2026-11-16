"""The hotkey: finding keyboards, noticing it go down and come back up, and
setting a new one from the tray.

The laptop Copilot key (F23) always counts. Other keys and chords come from
the settings; a chord counts when its last key goes down, and the recording
lasts until every key of it is up again.
"""

from __future__ import annotations

import select
import threading

from evdev import InputDevice, ecodes, list_devices

from voice.state import ptt_log as log
from voice.speech.whisper import Config

COPILOT_KEY = "F23"  # always a trigger, whatever else is set
RESCAN = 2.0  # seconds without a key event before looking for keyboards that came or went
HOLD_TIMEOUT = 60  # seconds with no event while held before checking whether the keys are up


def keycode(name: str) -> int:
    code = getattr(ecodes, "KEY_" + name.upper(), None)
    if code is None:
        raise SystemExit(f"unknown key {name}")
    return code


def key_name(code: int) -> str:
    raw = ecodes.KEY.get(code, str(code))
    if isinstance(raw, (list, tuple)):
        raw = raw[0]
    return str(raw).removeprefix("KEY_")


def triggers(cfg: Config) -> tuple[set[int], list[frozenset[int]]]:
    keys = {keycode(name) for name in cfg.keys}
    keys.add(keycode(COPILOT_KEY))
    chords = [frozenset(keycode(name) for name in chord) for chord in cfg.chords]
    return keys, [chord for chord in chords if chord]


def is_keyboard(dev: InputDevice) -> bool:
    # input-remapper's own keyboard counts too: a keyboard with a preset (the Dell receiver's mouse buttons) is
    # grabbed whole, and its keys come only from there (right Ctrl+Shift did nothing, the user's catch, 2026-11-16).
    # The grabbed original gives nothing meanwhile, so no key is seen twice. Its mouse-only devices aren't keyboards.
    keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
    return ecodes.KEY_A in keys and ecodes.KEY_ENTER in keys and len(keys) > 40


def open_keyboards() -> dict[str, InputDevice]:
    found = {}
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        if is_keyboard(dev):
            found[path] = dev
    return found


class HotkeyCapture:
    """"Set hotkey" in the tray: the next key or chord pressed becomes the hotkey."""

    IDLE_LABEL, ARMED_LABEL = "Set hotkey", "Press hotkey"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.armed = False
        self.gesture: set[int] = set()
        self.label = self.IDLE_LABEL

    def arm(self) -> None:
        with self.lock:
            self.armed, self.gesture, self.label = True, set(), self.ARMED_LABEL

    def disarm(self) -> None:
        with self.lock:
            self.armed, self.label = False, self.IDLE_LABEL
            self.gesture.clear()

    def feed(self, event, down: set[int], cfg: Config) -> None:
        """A key event while armed: Esc gives up; letting go of every key saves the gesture."""
        if event.code == ecodes.KEY_ESC:
            self.disarm()
        elif event.value == 1:
            self.gesture.add(event.code)
        elif self.gesture and self.gesture.isdisjoint(down):
            names = [key_name(code) for code in sorted(self.gesture)]
            self.disarm()
            if len(names) == 1:
                cfg.keys, cfg.chords = list(dict.fromkeys([COPILOT_KEY, *names])), []
            else:
                cfg.keys, cfg.chords = [COPILOT_KEY], [names]
            cfg.save()
            log("hotkey " + "+".join(names))


class Keyboards:
    """Every keyboard, and the keys held on each."""

    def __init__(self, cfg: Config, capture: HotkeyCapture) -> None:
        self.cfg, self.capture = cfg, capture
        self.boards = open_keyboards()
        if not self.boards:
            raise SystemExit("no keyboard found")
        self.held: dict[int, set[int]] = {dev.fd: set() for dev in self.boards.values()}
        for dev in self.boards.values():
            log(f"keyboard {dev.name} {dev.path}")
        log(f"keys {cfg.keys} chords {cfg.chords}")

    def wait(self) -> list[InputDevice]:
        """Keyboards with events; none after RESCAN seconds of quiet."""
        ready, _, _ = select.select([dev.fd for dev in self.boards.values()], [], [], RESCAN)
        return [dev for dev in self.boards.values() if dev.fd in ready]

    def rescan(self) -> None:
        current = open_keyboards()
        for path, dev in current.items():
            if path in self.boards:
                dev.close()
                continue
            self.boards[path] = dev
            self.held[dev.fd] = set()
            log(f"keyboard {dev.name} {dev.path}")
        for path in list(self.boards):
            if path not in current:
                dev = self.boards.pop(path)
                self.held.pop(dev.fd, None)
                dev.close()
                log(f"removed {path}")

    def read(self, dev: InputDevice, on_hold) -> None:
        """Handle a keyboard's events; on_hold(dev, event, hold) for each hotkey press."""
        if dev not in self.boards.values():  # dropped by a rescan earlier in this round
            return
        try:
            pending = list(dev.read())
        except OSError:
            # Unplugged: its file stays "ready" with nothing to read, so the wait never went quiet and no rescan
            # ran again; a keyboard plugged in later was never heard (the Dell receiver, 2026-11-16). Dropped first:
            # a quick re-plug can bring back the same path, which rescan would otherwise keep as this dead one
            path = next((p for p, d in self.boards.items() if d is dev), None)
            if path:
                self.boards.pop(path)
                self.held.pop(dev.fd, None)
                dev.close()
                log(f"removed {path}")
            self.rescan()
            return
        down = self.held[dev.fd]
        index = 0
        while index < len(pending):
            event = pending[index]
            index += 1
            if event.type != ecodes.EV_KEY or event.value == 2:
                continue
            before = set(down)
            if event.value == 1:
                down.add(event.code)
            elif event.value == 0:
                down.discard(event.code)
            if self.capture.armed:
                self.capture.feed(event, down, self.cfg)
                continue
            release = self.pressed(event, down, before)
            if release is None:
                continue
            hold = Hold(dev, down, release, pending[index:])
            on_hold(dev, event, hold)
            pending, index = [], 0  # what came after the press was read by the hold

    def pressed(self, event, down: set[int], before: set[int]) -> set[int] | None:
        """The keys that must all be up again, if this event pressed the hotkey."""
        if event.value != 1:
            return None
        single, chords = triggers(self.cfg)
        if event.code in single:
            return {event.code}
        for chord in chords:
            if chord <= down and not chord <= before:
                return set(chord)
        return None


class Hold:
    """One press of the hotkey on one keyboard, until its keys are up."""

    def __init__(self, dev: InputDevice, down: set[int], release: set[int], pending: list) -> None:
        self.dev, self.down, self.release, self.pending = dev, down, release, pending

    def _seen(self, event) -> bool:
        if event.type != ecodes.EV_KEY or event.value == 2:
            return False
        if event.value == 1:
            self.down.add(event.code)
        elif event.value == 0:
            self.down.discard(event.code)
        return self.release.isdisjoint(self.down)

    def until_released(self) -> None:
        """Block until every key of the hotkey is up (events after that, in the same read, are dropped)."""
        while True:
            if any(self._seen(event) for event in self.pending):
                return
            got, _, _ = select.select([self.dev.fd], [], [], HOLD_TIMEOUT)
            if not got and self.release.isdisjoint(self.down):
                return
            if got:
                try:
                    self.pending = list(self.dev.read())
                except OSError:
                    return
            else:
                self.pending = []

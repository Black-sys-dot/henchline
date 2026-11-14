#!/usr/bin/env python3
"""Hold a configured key or chord on any keyboard to record, then paste and Enter.

The laptop Copilot key is F23. An external keyboard uses the chord in
~/.config/ptt/config.json (default: right Ctrl + right Shift).
Default mic route only: pw-record with no --target.
"""
import ctypes
import functools
import gc
import os
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from evdev import InputDevice, ecodes, list_devices

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repository, for voice/
from voice import agents, x11  # noqa: E402
from voice import state as shared  # noqa: E402
from voice.state import BY_PTT, PHASE_COLOURS, PTT_LOG, PTT_WAV, Phase  # noqa: E402
from voice.whisper import Config, ding, load_model, transcribe  # noqa: E402

COPILOT_KEY = "F23"  # always a trigger, whatever else is set
SHORTEST = 0.35  # seconds: a shorter press is a slip, not speech
TRAY_PHASE: queue.Queue[Phase] = queue.Queue()  # phase changes for the tray icon
PASTE: queue.Queue[str] = queue.Queue()
TERMINALS = (
    "konsole",
    "yakuake",
    "kitty",
    "alacritty",
    "xterm",
    "gnome-terminal",
    "wezterm",
    "terminator",
    "tilix",
    "ghostty",
)


log = functools.partial(shared.log, PTT_LOG, echo=True)


def keycode(name: str) -> int:
    code = getattr(ecodes, "KEY_" + name.upper(), None)
    if code is None:
        raise SystemExit(f"unknown key {name}")
    return code


def triggers(cfg: Config) -> tuple[set[int], list[frozenset[int]]]:
    keys = {keycode(name) for name in cfg.keys}
    keys.add(keycode(COPILOT_KEY))
    chords = [frozenset(keycode(name) for name in chord) for chord in cfg.chords]
    return keys, [chord for chord in chords if chord]


def key_name(code: int) -> str:
    raw = ecodes.KEY.get(code, str(code))
    if isinstance(raw, (list, tuple)):
        raw = raw[0]
    return str(raw).removeprefix("KEY_")


class Capture:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.armed = False
        self.gesture: set[int] = set()
        self.label = "Set hotkey"


CAPTURE = Capture()


def is_keyboard(dev: InputDevice) -> bool:
    if "input-remapper" in dev.name.lower():
        return False
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


def set_phase(phase: Phase) -> None:
    """For the tray icon, and for talkback, which goes quiet while we record."""
    TRAY_PHASE.put(phase)
    shared.set_phase(phase, by=BY_PTT)


def agent_window(cls: str, role: str) -> bool:
    """A terminal or editor, where an agent may be reading what is pasted."""
    names = TERMINALS + ("cursor", "code")
    return any(f'"{name}"' in cls for name in names) and can_type(cls, role)


def clipboard(text: str) -> bool:
    if x11.set_clipboard(text):
        return True
    log("clipboard set failed")
    return False


def active_window() -> tuple[str, str]:
    """WM_CLASS and WM_WINDOW_ROLE of the focused window, lower case."""
    wid = x11.active_window()
    return x11.window_class(wid), x11.window_role(wid)


def can_type(cls: str, role: str) -> bool:
    if '"okular"' in cls:
        return False
    if "thunderbird" in cls or '"mail"' in cls:
        return "msgcompose" in role or "msgcompose" in cls
    return True


SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"}


def shell_only(cls: str) -> bool:
    """A terminal whose current tab runs nothing but a shell.

    Enter there would run the dictated words as a command, so paste() leaves
    it out. Unknown cases (several Konsole windows, odd terminals) say no and
    keep the usual Enter.
    """
    if not any(f'"{name}"' in cls for name in TERMINALS):
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


def paste() -> bool:
    os.environ.setdefault("DISPLAY", ":0")
    cls, role = active_window()
    shared.set_target(agent_window(cls, role))
    if not can_type(cls, role):
        log(f"clipboard only, focus {cls.strip() or 'unknown'} role {role.strip() or 'none'}")
        return False
    terminal = any(f'"{name}"' in cls for name in TERMINALS)
    log(f"focus {cls.strip() or 'unknown'} chord={'ctrl+shift+v' if terminal else 'ctrl+v'}")
    try:
        with x11.Display() as display:
            display.press(*((x11.CONTROL, x11.SHIFT, x11.V) if terminal else (x11.CONTROL, x11.V)))
            time.sleep(0.12)
            if terminal and shell_only(cls):
                log("only a shell in that terminal: pasted without Enter")
            else:
                display.press(x11.RETURN)
    except OSError as exc:
        log(str(exc))
        return False
    return True


def hush() -> None:
    # Talkback pauses by itself from the phase file
    if shutil.which("spd-say") is None:
        return
    subprocess.Popen(
        ["spd-say", "-C"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def start_recording() -> subprocess.Popen:
    hush()
    PTT_WAV.parent.mkdir(parents=True, exist_ok=True)
    PTT_WAV.unlink(missing_ok=True)
    return subprocess.Popen(
        [
            "pw-record",
            "--rate",
            "16000",
            "--channels",
            "1",
            "--format",
            "s16",
            str(PTT_WAV),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_recording(proc: subprocess.Popen, started: float) -> Path | None:
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    if time.monotonic() - started < SHORTEST or not PTT_WAV.exists() or PTT_WAV.stat().st_size < 1000:
        return None
    return PTT_WAV


def handle_text(model, path: Path, cfg: Config) -> None:
    set_phase(Phase.TRANSCRIBING)
    try:
        text, rich = transcribe(model, path, cfg, PTT_LOG)
    except Exception as exc:
        log(f"transcribe failed: {exc}")
        return
    finally:
        set_phase(Phase.IDLE)
    if not text:
        log("empty")
        return
    log(f"text: {text}")
    if rich != text:
        log(f"tone: {rich}")
    PASTE.put((text, rich))


def main(cfg: Config, stop: threading.Event) -> None:
    single, chords = triggers(cfg)
    boards = open_keyboards()
    if not boards:
        raise SystemExit("no keyboard found")
    held: dict[int, set[int]] = {dev.fd: set() for dev in boards.values()}
    for dev in boards.values():
        log(f"keyboard {dev.name} {dev.path}")
    log(f"keys {cfg.keys} chords {cfg.chords}")
    cuda = cfg.want_cuda()
    model = load_model(cfg, cuda, PTT_LOG)
    log("ready")
    while True:
        if stop.is_set():
            return
        fds = [dev.fd for dev in boards.values()]
        ready, _, _ = select.select(fds, [], [], 2.0)
        if not ready:
            cuda_now = cfg.want_cuda()
            if cuda_now != cuda:
                log("power source changed; reloading model")
                model = None
                gc.collect()
                model = load_model(cfg, cuda_now, PTT_LOG)
                cuda = cuda_now
            current = open_keyboards()
            for path, dev in current.items():
                if path in boards:
                    dev.close()
                    continue
                boards[path] = dev
                held[dev.fd] = set()
                log(f"keyboard {dev.name} {dev.path}")
            for path in list(boards):
                if path not in current:
                    dev = boards.pop(path)
                    held.pop(dev.fd, None)
                    dev.close()
                    log(f"removed {path}")
            continue
        for dev in boards.values():
            if dev.fd not in ready:
                continue
            try:
                events = list(dev.read())
            except OSError:
                continue
            down = held[dev.fd]
            pending = list(events)
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
                if CAPTURE.armed:
                    if event.code == ecodes.KEY_ESC:
                        with CAPTURE.lock:
                            CAPTURE.armed = False
                            CAPTURE.gesture.clear()
                            CAPTURE.label = "Set hotkey"
                        continue
                    if event.value == 1:
                        CAPTURE.gesture.add(event.code)
                        continue
                    if CAPTURE.gesture and CAPTURE.gesture.isdisjoint(down):
                        gesture = set(CAPTURE.gesture)
                        names = [key_name(code) for code in sorted(gesture)]
                        with CAPTURE.lock:
                            CAPTURE.armed = False
                            CAPTURE.gesture.clear()
                            CAPTURE.label = "Set hotkey"
                        if len(names) == 1:
                            cfg.keys = list(dict.fromkeys([COPILOT_KEY, *names]))
                            cfg.chords = []
                        else:
                            cfg.keys = [COPILOT_KEY]
                            cfg.chords = [names]
                        cfg.save()
                        log("hotkey " + "+".join(names))
                    continue
                single, chords = triggers(cfg)
                release: set[int] | None = None
                if event.value == 1 and event.code in single:
                    release = {event.code}
                elif event.value == 1:
                    for chord in chords:
                        if chord <= down and not chord <= before:
                            release = set(chord)
                            break
                if release is None:
                    continue
                label = ecodes.KEY.get(event.code, str(event.code))
                log(f"record {label} on {dev.name}")
                set_phase(Phase.RECORDING)
                proc = start_recording()
                started = time.monotonic()
                path = None

                def released(nxt) -> bool:
                    if nxt.type != ecodes.EV_KEY or nxt.value == 2:
                        return False
                    if nxt.value == 1:
                        down.add(nxt.code)
                    elif nxt.value == 0:
                        down.discard(nxt.code)
                    return release.isdisjoint(down)

                try:
                    while path is None:
                        for nxt in pending[index:]:
                            index = len(pending)
                            if released(nxt):
                                path = stop_recording(proc, started)
                                proc = None
                                break
                        if path is not None or proc is None:
                            break
                        got, _, _ = select.select([dev.fd], [], [], 60)
                        if not got and release.isdisjoint(down):
                            break
                        if not got:
                            continue
                        pending = list(dev.read())
                        index = 0
                finally:
                    if proc is not None:
                        path = stop_recording(proc, started)
                index = len(pending)
                if path is None:
                    set_phase(Phase.IDLE)
                    log("too short")
                else:
                    handle_text(model, path, cfg)


def ptt_icon(phase: Phase):
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(PHASE_COLOURS.get(phase, "#d8d8d8"))
    pen = QPen(color)
    pen.setWidth(2)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush if phase is Phase.IDLE else color)
    painter.drawRoundedRect(3, 3, 16, 11, 3, 3)
    painter.drawPolygon([QPointF(7, 14), QPointF(11, 14), QPointF(6, 19)])
    painter.end()
    return QIcon(pixmap)


def run_tray(cfg: Config, stop: threading.Event) -> None:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = QSystemTrayIcon(ptt_icon(Phase.IDLE))
    tray.setToolTip("Push to talk")
    menu = QMenu()

    def choose(key: str, value: str, actions: dict) -> None:
        setattr(cfg, key, value)
        cfg.save()
        for code, action in actions.items():
            action.setChecked(code == value)

    def add_group(title: str, key: str, options: list[tuple[str, str]]) -> None:
        sub = menu.addMenu(title)
        actions: dict[str, QAction] = {}
        current = getattr(cfg, key)
        for label, code in options:
            action = QAction(label, sub)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _checked, c=code, a=actions: choose(key, c, a))
            sub.addAction(action)
            actions[code] = action

    add_group("Language", "language", [("English", "en"), ("Hindi", "hi"), ("Detect", "auto")])
    add_group(
        "Power",
        "device",
        [("Auto, GPU on AC", "auto"), ("CPU", "cpu"), ("GPU", "cuda")],
    )
    hotkey = QAction("Set hotkey", menu)

    def arm_hotkey() -> None:
        with CAPTURE.lock:
            CAPTURE.armed = True
            CAPTURE.gesture = set()
            CAPTURE.label = "Press hotkey"
        hotkey.setText("Press hotkey")

    hotkey.triggered.connect(arm_hotkey)
    menu.addAction(hotkey)
    chime = QAction("Chime", menu)
    chime.setCheckable(True)
    chime.setChecked(cfg.chime)

    def toggle_chime(checked: bool) -> None:
        cfg.chime = bool(checked)
        cfg.save()

    chime.toggled.connect(toggle_chime)
    menu.addAction(chime)
    menu.addSeparator()
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(lambda: (stop.set(), app.quit()))
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log("no status tray; listener still running")
    tray.show()

    def poll() -> None:
        try:
            while True:
                phase = TRAY_PHASE.get_nowait()
                tray.setIcon(ptt_icon(phase))
        except queue.Empty:
            pass
        try:
            while True:
                text, rich = PASTE.get_nowait()
                if agent_window(*active_window()):
                    text = rich
                if clipboard(text):
                    paste()
                    ding(cfg)
        except queue.Empty:
            pass
        with CAPTURE.lock:
            label = CAPTURE.label
        if hotkey.text() != label:
            hotkey.setText(label)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    app.exec_()
    stop.set()


if __name__ == "__main__":
    try:
        ctypes.CDLL("libX11.so.6").XInitThreads()
        cfg = Config.load(PTT_LOG)
        cfg.save()  # every setting in the file, so it can be edited
        stop = threading.Event()
        threading.Thread(target=main, args=(cfg, stop), daemon=True).start()
        run_tray(cfg, stop)
    except KeyboardInterrupt:
        sys.exit(0)

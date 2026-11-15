"""The speech bubble in the system tray: its colour follows the phase, and
its menu sets the language, power, hotkey and chime. Qt wants the main
thread, so the words to paste come here through a queue and are pasted here."""

from __future__ import annotations

import queue
import sys
import threading

import paste
from keys import HotkeyCapture
from voice.state import PHASE_COLOURS, Phase
from voice.state import ptt_log as log
from voice.speech.whisper import Config, ding

IDLE_COLOUR = "#d8d8d8"
POLL_MS = 100
LANGUAGES = [("English", "en"), ("Hindi", "hi"), ("Detect", "auto")]
POWER = [("Auto, GPU on AC", "auto"), ("CPU", "cpu"), ("GPU", "cuda")]


def icon(phase: Phase):
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(PHASE_COLOURS.get(phase, IDLE_COLOUR))
    pen = QPen(color)
    pen.setWidth(2)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush if phase is Phase.IDLE else color)
    painter.drawRoundedRect(3, 3, 16, 11, 3, 3)
    painter.drawPolygon([QPointF(7, 14), QPointF(11, 14), QPointF(6, 19)])
    painter.end()
    return QIcon(pixmap)


def run(cfg: Config, stop: threading.Event, capture: HotkeyCapture, phases: queue.Queue,
        texts: queue.Queue) -> None:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = QSystemTrayIcon(icon(Phase.IDLE))
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

    add_group("Language", "language", LANGUAGES)
    add_group("Power", "device", POWER)
    hotkey = QAction(capture.IDLE_LABEL, menu)

    def arm_hotkey() -> None:
        capture.arm()
        hotkey.setText(capture.ARMED_LABEL)

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
        while not phases.empty():
            tray.setIcon(icon(phases.get_nowait()))
        while not texts.empty():
            if paste.deliver(*texts.get_nowait()):
                ding(cfg)
        with capture.lock:
            label = capture.label
        if hotkey.text() != label:
            hotkey.setText(label)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(POLL_MS)
    app.exec_()
    stop.set()

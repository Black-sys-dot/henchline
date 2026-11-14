"""The headset in the system tray: plain while only the wake word gets in,
green while listening, red while recording, yellow while transcribing."""

from __future__ import annotations

import sys
import threading

from listener import Listener, Mode
from voice.state import PHASE_COLOURS, Phase

COLOURS = {Mode.LISTENING: "#3cb371", Mode.RECORDING: PHASE_COLOURS[Phase.RECORDING],
           Mode.TRANSCRIBING: PHASE_COLOURS[Phase.TRANSCRIBING]}
TIPS = {
    Mode.WAITING: 'Hands-free on: only "hey Claude" gets in',
    Mode.LISTENING: "Hands-free: listening for your answer",
    Mode.RECORDING: "Hands-free: recording you",
    Mode.TRANSCRIBING: "Hands-free: transcribing",
}


def show(listener: Listener, stop: threading.Event) -> None:
    """Run the tray until `stop` is set (Qt wants the main thread)."""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QColor, QIcon, QPainter
    from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    headset = QIcon.fromTheme("audio-headset")  # not a mic: that is push-to-talk

    def tinted(colour: str) -> QIcon:
        pixmap = headset.pixmap(22, 22)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(colour))
        painter.end()
        return QIcon(pixmap)

    icons = {mode: tinted(colour) for mode, colour in COLOURS.items()}
    icon = QSystemTrayIcon(headset)
    menu = QMenu()
    off = QAction("Turn hands-free off")
    off.triggered.connect(stop.set)
    menu.addAction(off)
    icon.setContextMenu(menu)
    icon.show()
    shown = [None]

    def poll() -> None:
        if listener.mode is not shown[0]:
            shown[0] = listener.mode
            icon.setIcon(icons.get(listener.mode, headset))
            icon.setToolTip(TIPS[listener.mode])
        if stop.is_set():
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)
    app.exec_()

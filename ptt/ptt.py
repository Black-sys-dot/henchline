#!/usr/bin/env python3
"""Hold a configured key or chord on any keyboard to record, then paste and Enter.

The laptop Copilot key is F23. An external keyboard uses the chord in
~/.config/ptt/config.json (default: right Ctrl + right Shift).
Default mic route only: pw-record with no --target.

keys.py notices the hotkey, paste.py types the words, tray.py is the icon;
this file records and transcribes in between.
"""
import ctypes
import gc
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from evdev import ecodes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repository, for voice/
import tray  # noqa: E402
from keys import HotkeyCapture, Hold, Keyboards  # noqa: E402
from voice import state as shared  # noqa: E402
from voice.state import BY_PTT, PTT_LOG, PTT_WAV, Phase  # noqa: E402
from voice.state import ptt_log as log  # noqa: E402
from voice.whisper import Config, load_model, transcribe  # noqa: E402

SHORTEST = 0.35  # seconds: a shorter press is a slip, not speech
SMALLEST = 1000  # bytes: a recording this small has no audio in it
RECORD = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]


class Recording:
    """pw-record into PTT_WAV from the default mic, while the key is held."""

    def __init__(self) -> None:
        if shutil.which("spd-say"):  # desktop speech stops; talkback pauses by itself from the phase
            subprocess.Popen(["spd-say", "-C"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        PTT_WAV.parent.mkdir(parents=True, exist_ok=True)
        PTT_WAV.unlink(missing_ok=True)
        self.proc = subprocess.Popen([*RECORD, str(PTT_WAV)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started = time.monotonic()

    def stop(self) -> Path | None:
        """The recording, or None if it was too short to hold speech."""
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        if time.monotonic() - self.started < SHORTEST or not PTT_WAV.exists() or PTT_WAV.stat().st_size < SMALLEST:
            return None
        return PTT_WAV


class PushToTalk:
    def __init__(self, cfg: Config, stop: threading.Event) -> None:
        self.cfg, self.stop = cfg, stop
        self.capture = HotkeyCapture()
        self.phases: queue.Queue[Phase] = queue.Queue()  # for the tray icon
        self.texts: queue.Queue[tuple[str, str]] = queue.Queue()  # (plain, with tone marks), pasted by the tray
        self.model = None

    def set_phase(self, phase: Phase) -> None:
        """For the tray icon, and for talkback, which goes quiet while we record."""
        self.phases.put(phase)
        shared.set_phase(phase, by=BY_PTT)

    def run(self) -> None:
        keyboards = Keyboards(self.cfg, self.capture)
        cuda = self.cfg.want_cuda()
        self.model = load_model(self.cfg, cuda, PTT_LOG)
        log("ready")
        while not self.stop.is_set():
            ready = keyboards.wait()
            for dev in ready:
                keyboards.read(dev, self.hold)
            if ready:
                continue
            if self.cfg.want_cuda() != cuda:  # plugged in or unplugged
                log("power source changed; reloading model")
                cuda, self.model = not cuda, None
                gc.collect()
                self.model = load_model(self.cfg, cuda, PTT_LOG)
            keyboards.rescan()

    def hold(self, dev, event, hold: Hold) -> None:
        """Record while the hotkey is held, then transcribe."""
        log(f"record {ecodes.KEY.get(event.code, str(event.code))} on {dev.name}")
        self.set_phase(Phase.RECORDING)
        recording = Recording()
        try:
            hold.until_released()
        finally:
            path = recording.stop()
        if path is None:
            self.set_phase(Phase.IDLE)
            log("too short")
        else:
            self.transcribe(path)

    def transcribe(self, path: Path) -> None:
        self.set_phase(Phase.TRANSCRIBING)
        try:
            text, rich = transcribe(self.model, path, self.cfg, PTT_LOG)
        except Exception as exc:
            log(f"transcribe failed: {exc}")
            return
        finally:
            self.set_phase(Phase.IDLE)
        if not text:
            log("empty")
            return
        log(f"text: {text}")
        if rich != text:
            log(f"tone: {rich}")
        self.texts.put((text, rich))


if __name__ == "__main__":
    try:
        ctypes.CDLL("libX11.so.6").XInitThreads()
        cfg = Config.load(PTT_LOG)
        cfg.save()  # every setting in the file, so it can be edited
        stop = threading.Event()
        ptt = PushToTalk(cfg, stop)
        threading.Thread(target=ptt.run, daemon=True).start()
        tray.run(cfg, stop, ptt.capture, ptt.phases, ptt.texts)
    except KeyboardInterrupt:
        sys.exit(0)

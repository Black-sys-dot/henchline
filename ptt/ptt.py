#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Hold a configured key or chord on any keyboard to record, then paste and Enter.

The laptop Copilot key is F23. An external keyboard uses the chord in
~/.config/ptt/config.json (default: right Ctrl + right Shift).
Default mic route only: pw-record with no --target.

keys.py notices the hotkey, paste.py types the words, tray.py is the icon;
this file records and transcribes in between.
"""
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from evdev import ecodes

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "core"), str(HERE.parent)]  # push-to-talk's own modules (core/), and the repository for voice/
import ptt_tray as tray  # noqa: E402  (named apart from hands-free's tray: never two in one process)
from keys import HotkeyCapture, Hold, Keyboards  # noqa: E402
from voice import state as shared  # noqa: E402
from voice.desktop import current as desktop  # noqa: E402
from voice.state import BY_PTT, PTT_LOG, PTT_WAV, Phase  # noqa: E402
from voice.state import ptt_log as log  # noqa: E402
from voice.speech.whisper import Config, Transcriber  # noqa: E402

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
        self.transcriber: Transcriber | None = None  # loaded when the key listener starts

    def set_phase(self, phase: Phase) -> None:
        """For the tray icon, and for talkback, which goes quiet while we record."""
        self.phases.put(phase)
        shared.set_phase(phase, by=BY_PTT)

    def run(self) -> None:
        keyboards = Keyboards(self.cfg, self.capture)
        self.transcriber = Transcriber(self.cfg, PTT_LOG)
        log("ready")
        while not self.stop.is_set():
            ready = keyboards.wait()
            for dev in ready:
                keyboards.read(dev, self.hold)
            if ready:
                continue
            self.transcriber.settle()  # mains or battery; back to the GPU once a training run lets go
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
            text, rich = self.transcriber.file(path)  # out of GPU memory: on the CPU, words kept
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
        desktop().prepare_threads()
        cfg = Config.load(PTT_LOG)
        cfg.save()  # every setting in the file, so it can be edited
        stop = threading.Event()
        ptt = PushToTalk(cfg, stop)
        threading.Thread(target=ptt.run, daemon=True).start()
        tray.run(cfg, stop, ptt.capture, ptt.phases, ptt.texts)
    except KeyboardInterrupt:
        sys.exit(0)

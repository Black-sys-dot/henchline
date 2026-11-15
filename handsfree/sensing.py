"""What hands-free senses besides the mic stream itself: whether a frame
sounds like a voice, the "hey Claude" wake word, earbud taps, how loud the
speakers are playing, and the echo canceller that feeds the mic stream."""

from __future__ import annotations

import collections
import pickle
import re
import select
import subprocess
import threading
import time

import numpy as np
from faster_whisper.vad import VadOptions

from voice.state import EC_SOURCE, EC_UNIT, HANDSFREE_LOG, WAKE_MODEL, log, speaker_sink

RATE = 16000
FRAME = 480  # 30 ms at 16 kHz
PER_SECOND = RATE // FRAME  # 33 frames
LEVEL_SPAN = PER_SECOND // 2 + 1  # frames compared against the playback: the last half second
# Silero must find a voice before anything counts. While a clip plays the canceller
# squashes the user's voice, but 0.3/150 ms cut talkback off on its own echo and
# 0.45/200 ms on a throat clear (2026-11-14): 0.5/300 ms
SPEECH = VadOptions(threshold=0.6, min_speech_duration_ms=250, min_silence_duration_ms=100, speech_pad_ms=0)
SPEECH_OVER = VadOptions(threshold=0.5, min_speech_duration_ms=300, min_silence_duration_ms=100, speech_pad_ms=0)
VOICE_SHARE = 0.5  # share of a frame's energy below 1 kHz for it to sound like a voice


def level(frame: np.ndarray) -> float:
    """Loudness of a frame in dB (full scale)."""
    return float(20 * np.log10(np.sqrt((frame ** 2).mean()) + 1e-6))


def voiced(frame: np.ndarray) -> bool:
    """Speech carries most of its energy below 1 kHz; a spoon on a mug does not."""
    power = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
    freqs = np.fft.rfftfreq(len(frame), 1 / RATE)
    total = power[freqs >= 80].sum()
    return bool(total) and power[(freqs >= 80) & (freqs < 1000)].sum() / total > VOICE_SHARE


def mic_stream(device: str) -> subprocess.Popen:
    """Raw 16 kHz mono s16 from a PipeWire source, read FRAME samples at a time."""
    return subprocess.Popen(["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}",
                             "--channels=1", "--raw", "--latency-msec=30"], stdout=subprocess.PIPE)


def read_frame(stream: subprocess.Popen) -> np.ndarray | None:
    data = stream.stdout.read(FRAME * 2)
    if len(data) < FRAME * 2:
        return None
    return np.frombuffer(data, dtype="<i2")


def start_canceller(restart: bool = False) -> float:
    """Start (or restart) the echo canceller unit; return when it started (monotonic).

    It is its own unit so restarting hands-free does not reset it: a fresh
    canceller has to learn the room again, and leaks our voice meanwhile.
    """
    subprocess.run(["systemctl", "--user", "restart" if restart else "start", EC_UNIT], check=False)
    for _ in range(50):
        sources = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        if EC_SOURCE in sources:
            since = subprocess.run(["systemctl", "--user", "show", "-p", "ActiveEnterTimestampMonotonic",
                                    "--value", EC_UNIT], capture_output=True, text=True).stdout.strip()
            return int(since) / 1e6 if since.isdigit() else time.monotonic()
        time.sleep(0.1)
    raise SystemExit("echo canceller did not come up")


class EchoWatch:
    """How close our own voice comes back to the playback, clip by clip.

    A healthy canceller kept our echo 20 dB or more under the playback; it
    drifted to 10 dB under (2026-11-14, laptop overloaded), and from 15 dB a
    clip gets cut. The drift shows in short bursts, not on average: a 90th
    percentile per clip stayed near 58 dB under while bursts that sounded like
    speech came within 19 dB (2026-11-15). So a burst that sounds like speech,
    was taken for echo, and came within EARLY of the playback is a near miss,
    and a near miss asks for a fresh canceller before any cut happens; so does
    a clip whose loudest echo came that close (9 and 12 dB under, just before
    two cuts on 2026-11-15).
    """

    EARLY = -22.0  # dB: a near miss, or a clip's closest echo, nearer the playback than this asks for a restart
    SPEAKING = -40.0  # dB: playback quieter than this is a pause, not a voice to compare against
    REST = 5 * 60.0  # seconds between restarts: if a fresh canceller does no better, restarting again won't help

    def __init__(self, restarted: float = 0.0) -> None:
        self.closest: float | None = None  # this clip's echo, nearest to the playback (mic minus playback, dB)
        self.restarted = restarted  # a canceller younger than REST is still learning: no restart yet

    def add(self, mic_db: float, played_db: float | None) -> None:
        """Loudest mic and playback of the last half second, the measure a cut uses."""
        if played_db is not None and played_db > self.SPEAKING:
            margin = mic_db - played_db
            self.closest = margin if self.closest is None else max(self.closest, margin)

    def may_restart(self) -> bool:
        return time.monotonic() - self.restarted > self.REST

    def near_miss(self, margin: float) -> bool:
        """Speech-like echo, kept out; True when it came close enough to ask for a restart."""
        return margin > self.EARLY and self.may_restart()

    def discard(self) -> None:
        """The user talked over this clip: it says nothing about the canceller."""
        self.closest = None

    def clip_over(self) -> bool:
        """The clip ended; True when its echo came close enough to ask for a restart."""
        closest, self.closest = self.closest, None
        if closest is None:
            return False
        drifting = self.near_miss(closest)
        log(HANDSFREE_LOG, f"clip echo: {abs(closest):.0f} dB {'under' if closest < 0 else 'over'} the playback at the closest"
                           f"{', canceller drifting: restart when quiet' if drifting else ''}")
        return drifting


class Output:
    """The default output: is it the laptop speakers (echo possible), checked every 2 s."""

    def __init__(self) -> None:
        self.at, self.speakers = 0.0, True

    def on_speakers(self) -> bool:
        if time.monotonic() - self.at > 2:
            name = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout
            self.at, self.speakers = time.monotonic(), not re.search(r"bluez|headphone|headset|usb", name, re.I)
        return self.speakers


class Wake:
    """ "Hey Claude": a personal detector nominates, Whisper confirms.

    The detector (wakeword_train.py) scores openWakeWord speech embeddings
    every 80 ms and keeps no audio beyond the last two seconds. Only when it
    fires twice in a row are those two seconds transcribed, to check for
    "Claude"; the snippet is discarded either way and its words never logged.
    """

    THRESHOLD = 0.9
    RUN = 2  # scores in a row above THRESHOLD
    REARM = 0.3  # a score below this lets it fire again
    CHUNK = 1280  # samples per openWakeWord step (80 ms)

    def __init__(self) -> None:
        from openwakeword.utils import AudioFeatures

        self.clf = pickle.loads(WAKE_MODEL.read_bytes())
        self.features = AudioFeatures(inference_framework="onnx")
        self.ring: collections.deque = collections.deque(maxlen=2 * PER_SECOND)
        self.pending = np.zeros(0, dtype="<i2")
        self.run, self.armed = 0, True

    @classmethod
    def available(cls) -> Wake | None:
        return cls() if WAKE_MODEL.exists() else None

    def feed(self, samples: np.ndarray) -> bool:
        """Add 30 ms of audio; True when the detector says "maybe hey Claude"."""
        self.ring.append(samples)
        self.pending = np.concatenate([self.pending, samples])
        fired = False
        while len(self.pending) >= self.CHUNK:
            chunk, self.pending = self.pending[:self.CHUNK], self.pending[self.CHUNK:]
            self.features._streaming_features(chunk)
            score = self.clf.predict_proba(self.features.get_features(n_feature_frames=16).reshape(1, -1))[0, 1]
            self.run = self.run + 1 if score > self.THRESHOLD else 0
            if score < self.REARM:
                self.armed = True
            if self.run >= self.RUN and self.armed:
                self.armed, fired = False, True
        return fired

    def confirm(self, whisper) -> bool:
        audio = np.concatenate(self.ring).astype(np.float32) / 32768
        segments, _ = whisper.transcribe(audio, language="en", beam_size=1, vad_filter=False, hotwords="Claude")
        return bool(re.search(r"\bclaude\b", " ".join(seg.text for seg in segments), re.I))


class EarbudButton(threading.Thread):
    """Bluetooth earbud taps (AVRCP media keys) as a silent "hey Claude".

    While hands-free runs it grabs every "… (AVRCP)" input device, so a tap
    does not also pause music; when hands-free stops, the grab ends. Earbuds
    come and go, so it rescans. Taps only arrive in the music profile (A2DP).
    """

    TAP = {"KEY_PLAYCD", "KEY_PAUSECD", "KEY_PLAYPAUSE"}
    DEBOUNCE = 2.0  # seconds: one press, not its echo

    def __init__(self, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.stop, self.tap = stop, threading.Event()

    def run(self) -> None:
        from evdev import InputDevice, ecodes, list_devices

        held: dict[str, InputDevice] = {}
        last = 0.0
        while not self.stop.is_set():
            for path in list_devices():
                if path in held:
                    continue
                try:
                    device = InputDevice(path)
                    if device.name.endswith("(AVRCP)"):
                        device.grab()
                        held[path] = device
                        log(HANDSFREE_LOG, f"earbud button: {device.name}")
                except OSError:
                    continue
            if not held:
                self.stop.wait(3)
                continue
            ready, _, _ = select.select(list(held.values()), [], [], 3)
            for device in ready:
                try:
                    for event in device.read():
                        if event.type != ecodes.EV_KEY or event.value != 1:
                            continue
                        name = ecodes.KEY.get(event.code, "")
                        if name in self.TAP and time.monotonic() - last > self.DEBOUNCE:
                            last = time.monotonic()
                            self.tap.set()
                        else:
                            log(HANDSFREE_LOG, f"earbud {name} ignored")
                except OSError:  # disconnected
                    held.pop(device.path, None)
                    log(HANDSFREE_LOG, "earbud button gone")


def monitor_of_output() -> str:
    """What our voice goes out through: the phone during a call, the default output otherwise."""
    sink = speaker_sink()
    return "@DEFAULT_MONITOR@" if sink.startswith("@") else f"{sink}.monitor"


class Playback(threading.Thread):
    """Loudness of what the speakers are playing (the default output's monitor).

    Our own voice reaches the cancelled mic far quieter than it is played,
    since the canceller removes most of it; the user's voice does not get
    quieter for that. Measured on 2026-11-14: echo stayed 20 dB or more under
    the playback, the user often within a few dB of it or above.
    """

    def __init__(self, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.stop = stop
        self.levels: collections.deque = collections.deque(maxlen=LEVEL_SPAN)
        self.at = 0.0

    def run(self) -> None:
        while not self.stop.is_set():
            device = monitor_of_output()
            stream = mic_stream(device)
            while not self.stop.is_set() and (samples := read_frame(stream)) is not None and device == monitor_of_output():
                self.levels.append(level(samples.astype(np.float32) / 32768))
                self.at = time.monotonic()
            stream.terminate()
            self.stop.wait(1)  # the output changed or parec ended: start again

    def loudest(self) -> float | None:
        """Loudest playback level in the last half second, or None if unknown."""
        return max(self.levels) if self.levels and time.monotonic() - self.at < 1 else None

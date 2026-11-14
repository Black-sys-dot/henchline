#!/usr/bin/env python3
"""Hands-free conversation with the talkback agents.

    handsfree on | off | status        (handsfree run is the service itself)

While on, the laptop's built-in mic runs through an echo canceller
(~/.config/pipewire/talkback-echo-cancel.conf), and speech is listened for
only in two moments:
- for WINDOW seconds after a talkback clip finishes (the reply window), and
- while a clip is playing, to notice the user talking over it.
Outside those moments the audio is read and thrown away: nothing is
recorded, transcribed or logged. There is no wake word yet.

When speech starts, the push-to-talk phase file says "recording", so
talkback goes quiet and holds its clips. The utterance is recorded until a
pause, transcribed with push-to-talk's Whisper (tone marks included), and
typed into the Konsole session of the agent that spoke last, prefixed
[hands-free]. "Never mind" drops it; "hands-free off" ends the mode. With no
clip and no speech for IDLE seconds the mode turns itself off.
"""

from __future__ import annotations

import collections
import json
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ptt"))
import ptt  # noqa: E402  push-to-talk's Whisper, tone marks and state files

STATE = Path.home() / ".local/state/ptt"
FLAG = STATE / "handsfree"
TALKBACK = STATE / "talkback"
LOG = STATE / "handsfree.log"
WAV = STATE / "handsfree.wav"
LAST = STATE / "talkback.last"
PLAYING = STATE / "talkback.playing"
QUEUE = STATE / "talkback.queue"
TALKBACK_PID = STATE / "talkback.pid"
SAY = Path.home() / ".cursor/skills/talkback/scripts/say.py"
EC_CONF = Path.home() / ".config/pipewire/talkback-echo-cancel.conf"
EC_SOURCE = "talkback_ec_source"
UNIT = "handsfree.service"
WAKE_MODEL = Path.home() / ".local/share/handsfree/hey_claude_personal.pkl"
WAKE_PREFIX = re.compile(r"^\W*((hey|hi|okay|ok)\W+)?claude\W*", re.I)
PAUSE = STATE / "handsfree.pause"  # e.g. while wake-word samples are recorded
CLOSE = STATE / "handsfree.close"  # `handsfree close`: end the reply window now
LISTEN = STATE / "handsfree.listen"  # `handsfree listen N`: keep the window open N seconds

RATE = 16000
FRAME = 480  # 30 ms
WINDOW = 8.0  # seconds to answer after a clip
FOLLOW = 6.0  # seconds to carry on after their own message (once, never chained)
IDLE = 15 * 60
START_DB = 12  # above the noise floor, sustained, to count as speech
BARGE_DB = 15  # stricter while a clip plays: some echo is left over
START_FRAMES = 6  # 180 ms
BARGE_FRAMES = 8  # voiced frames out of the last 12 (360 ms) to talk over a clip
# The cheap checks above only nominate a moment; Silero (Whisper's own speech
# detector) must then find a voice in the last second before anything stops
SPEECH = VadOptions(threshold=0.6, min_speech_duration_ms=250, min_silence_duration_ms=100,
                    speech_pad_ms=0)
# While a clip plays the canceller squashes the user's voice; measured on a
# double-talk recording, 0.6/250 ms caught 11 of 48 windows of them talking;
# 0.3/150 ms caught 26 but in live use cut talkback off on its own echo four
# times in a row (2026-11-14 20:18), so this sits in between
SPEECH_OVER = VadOptions(threshold=0.45, min_speech_duration_ms=200, min_silence_duration_ms=100,
                         speech_pad_ms=0)
VOICE_SHARE = 0.5  # share of a frame's energy below 1 kHz for it to sound like a voice
END_DB = 6
END_FRAMES = 50  # 1.5 s of quiet ends the utterance
OVER_FRAMES = 20  # after 0.6 s of quiet, check whether the last word was "over"
BARGE_GRACE = 0.7  # seconds at the start of a clip before the canceller has caught up
THINK_WINDOW = 180  # how long to keep listening after "give me a minute"
PREROLL = 15  # frames kept from before speech started
MAX_SECONDS = 120  # the user talks in long stretches; 60 s cut them off
CANCEL = re.compile(r"^\W*(never ?mind|cancel( that)?|scratch that)\W*$", re.I)
OVER = re.compile(r"[\s,.!?]*\bover\b[\s.!?]*$", re.I)
THINK = re.compile(
    r"\b(give me a (minute|moment|second|sec)|let me think|hold on|hang on"
    r"|one (sec|second|moment)|let me (get|come) back)\b", re.I)
OFF = re.compile(r"\b(hands[- ]?free (mode )?off|stop hands[- ]?free|turn off hands[- ]?free)\b", re.I)


def log(msg: str) -> None:
    with open(LOG, "a") as out:
        out.write(f"{time.strftime('%F %T')} {msg}\n")


def say(text: str) -> None:
    subprocess.Popen([str(SAY), "--agent", "handsfree", "--session", "handsfree", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def talkback_busy() -> bool:
    return PLAYING.exists() or TALKBACK_PID.exists() or any(QUEUE.glob("*.json"))


def ptt_busy() -> bool:
    """Push-to-talk itself is recording; leave the mic to it."""
    state = ptt_phase()
    return state.get("phase") in ("recording", "transcribing") and state.get("by") != "handsfree"


def ptt_phase() -> dict:
    try:
        return json.loads((STATE / "phase").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def phase(name: str) -> None:
    ptt.note("phase", {"phase": name, "by": "handsfree"})


def send(text: str, how: str) -> bool:
    """Type the words into the Konsole session of the agent that spoke last."""
    try:
        last = json.loads(LAST.read_text())
    except (FileNotFoundError, ValueError):
        last = {}
    service, session = last.get("konsole") or ["", ""]
    if not service or not session:
        log(f"no konsole session for {last.get('agent', 'nobody')}")
        say("I heard you, but I don't know which terminal to type that into.")
        return False
    # Only ever type into a tab where an agent is in the foreground: if the
    # agent has quit, the tab is a plain shell and Enter would run the words
    fg = subprocess.run(["qdbus", service, session, "foregroundProcessId"],
                        capture_output=True, text=True).stdout.strip()
    try:
        command = Path(f"/proc/{fg}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except (OSError, ValueError):
        command = ""
    if not re.search(r"claude|cursor-agent|/agent\b", command):
        log(f"no agent running in {session} ({command.split(' ')[0] or 'tab gone'})")
        say("I heard you, but the agent I was talking to isn't running any more, "
            "so I didn't type it anywhere.")
        return False
    # How it was heard, so the agent can judge whether it was meant for it:
    # woke (wake word), reply (window after a clip), think (asked for time),
    # follow-on (right after their last message), over you (talked over a clip)
    message = f"[hands-free · {how}] {text}"
    for chunk in (message, "\r"):
        result = subprocess.run(
            ["qdbus", service, session, "sendText", chunk],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            log(f"sendText failed: {result.stderr.strip()}")
            say("I heard you, but that terminal is gone.")
            return False
        time.sleep(0.15)
    log(f"sent to {last.get('agent')} {session}")
    return True


def start_canceller() -> subprocess.Popen:
    proc = subprocess.Popen(["pipewire", "-c", str(EC_CONF)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        sources = subprocess.run(["pactl", "list", "short", "sources"],
                                 capture_output=True, text=True).stdout
        if EC_SOURCE in sources:
            return proc
        time.sleep(0.1)
    proc.terminate()
    raise SystemExit("echo canceller did not come up")


class Wake:
    """ "Hey Claude": a personal detector nominates, Whisper confirms.

    The detector (wakeword_train.py) scores openWakeWord speech embeddings
    every 80 ms and keeps no audio beyond the last two seconds. Only when it
    fires twice in a row are those two seconds transcribed, to check for
    "Claude"; the snippet is discarded either way and its words never logged.
    """

    THRESHOLD = 0.9
    RUN = 2

    def __init__(self) -> None:
        import pickle

        from openwakeword.utils import AudioFeatures

        self.clf = pickle.loads(WAKE_MODEL.read_bytes())
        self.features = AudioFeatures(inference_framework="onnx")
        self.ring = collections.deque(maxlen=2 * RATE // FRAME)
        self.pending = np.zeros(0, dtype="<i2")
        self.run, self.armed = 0, True

    def feed(self, samples: np.ndarray) -> bool:
        self.ring.append(samples)
        self.pending = np.concatenate([self.pending, samples])
        fired = False
        while len(self.pending) >= 1280:
            chunk, self.pending = self.pending[:1280], self.pending[1280:]
            self.features._streaming_features(chunk)
            x = self.features.get_features(n_feature_frames=16).reshape(1, -1)
            score = self.clf.predict_proba(x)[0, 1]
            self.run = self.run + 1 if score > self.THRESHOLD else 0
            if score < 0.3:
                self.armed = True
            if self.run >= self.RUN and self.armed:
                self.armed, fired = False, True
        return fired

    def confirm(self, whisper) -> bool:
        audio = np.concatenate(self.ring).astype(np.float32) / 32768
        segments, _ = whisper.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                         hotwords="Claude")
        return bool(re.search(r"\bclaude\b", " ".join(seg.text for seg in segments), re.I))


class Listener:
    def __init__(self, cfg: dict, model, stop: threading.Event) -> None:
        self.cfg, self.model, self.stop = cfg, model, stop
        self.state = "waiting"  # waiting / listening / recording / transcribing
        self.last_activity = time.monotonic()
        self.wake = Wake() if WAKE_MODEL.exists() else None

    @staticmethod
    def voiced(frame: np.ndarray) -> bool:
        """Speech carries most of its energy below 1 kHz; a spoon on a mug does not."""
        power = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
        freqs = np.fft.rfftfreq(len(frame), 1 / RATE)
        total = power[freqs >= 80].sum()
        return bool(total) and power[(freqs >= 80) & (freqs < 1000)].sum() / total > VOICE_SHARE

    def run(self) -> None:
        reader = subprocess.Popen(
            ["parec", f"--device={EC_SOURCE}", "--format=s16le", f"--rate={RATE}",
             "--channels=1", "--raw", "--latency-msec=30"],
            stdout=subprocess.PIPE,
        )
        # Frame levels for the noise floor, kept apart: while a clip plays the
        # cancelled mic sits far lower (leftover echo) than the room does
        room = collections.deque(maxlen=330)  # ~10 s
        echo = collections.deque(maxlen=330)
        preroll = collections.deque(maxlen=PREROLL)
        loud = collections.deque(maxlen=12)
        recent = collections.deque(maxlen=33)  # last second, for the speech check
        checked = 0
        window_until, was_busy, play_started = 0.0, False, 0.0
        kind = "reply"  # what opened the current window
        try:
            while not self.stop.is_set():
                data = reader.stdout.read(FRAME * 2)
                if len(data) < FRAME * 2:
                    break
                frame = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
                level = 20 * np.log10(np.sqrt((frame**2).mean()) + 1e-6)
                now = time.monotonic()
                busy = talkback_busy()
                if busy:
                    self.last_activity = now
                elif was_busy:  # a clip just ended: the user may answer
                    if now + WINDOW > window_until:  # never shortens "give me a minute"
                        window_until, kind = now + WINDOW, "reply"
                if CLOSE.exists():  # only the wake word opens it again
                    CLOSE.unlink(missing_ok=True)
                    LISTEN.unlink(missing_ok=True)
                    window_until = 0.0
                try:
                    asked = float(LISTEN.read_text()) - time.time()
                    if now + asked > window_until:
                        window_until, kind = now + asked, "think"
                except (FileNotFoundError, ValueError):
                    pass
                was_busy = busy
                if now - self.last_activity > IDLE:
                    log("idle, turning off")
                    say("Hands-free is going to sleep.")
                    self.stop.set()
                    break
                playing = PLAYING.exists()
                if playing and not play_started:
                    play_started = now
                elif not playing:
                    play_started = 0.0
                open_ = (playing or now < window_until) and not ptt_busy() and not PAUSE.exists()
                self.state = "listening" if open_ else "waiting"
                if not open_:
                    loud.clear()
                    preroll.clear()
                    (echo if playing else room).append(level)
                    if (self.wake and not playing and not PAUSE.exists() and not ptt_busy()
                            and self.wake.feed(np.frombuffer(data, dtype="<i2"))):
                        if self.wake.confirm(self.model):
                            log("woke")
                            ptt.ding(self.cfg)  # "I'm listening"
                            floor = np.percentile(room, 20) if len(room) > 30 else level
                            extra = self.utterance(reader, [], floor, "woke")
                            self.last_activity = time.monotonic()
                            window_until = time.monotonic() + (extra or FOLLOW)
                            kind = "think" if extra else "follow-on"
                            was_busy = talkback_busy()
                        else:
                            log("wake candidate rejected")
                    continue
                history = echo if playing else room
                floor = np.percentile(history, 20) if len(history) > 30 else level
                room_floor = np.percentile(room, 20) if len(room) > 30 else floor
                preroll.append(frame)
                recent.append(frame)
                checked += 1
                over = level > floor + (BARGE_DB if playing else START_DB)
                settled = not playing or now - play_started > BARGE_GRACE
                loud.append(over and settled and self.voiced(frame))
                speech = False
                if sum(loud) >= (BARGE_FRAMES if playing else START_FRAMES) and checked >= 3:
                    checked = 0  # at most one speech check per 90 ms
                    speech = bool(get_speech_timestamps(np.concatenate(recent),
                                                        SPEECH_OVER if playing else SPEECH))
                if speech:
                    how = "over you" if playing else kind
                    extra = self.utterance(reader, list(preroll), max(floor, room_floor), how)
                    self.last_activity = time.monotonic()
                    # One short follow-on window after they speak, for a pause mid-thought;
                    # a follow-on never opens another, so chatter cannot chain
                    if extra:
                        window_until, kind = time.monotonic() + extra, "think"
                    elif how == "follow-on":
                        window_until = 0.0
                    else:
                        window_until, kind = time.monotonic() + FOLLOW, "follow-on"
                    LISTEN.unlink(missing_ok=True)  # they spoke: an earlier long window is used up
                    if extra:
                        LISTEN.write_text(str(time.time() + extra))
                    was_busy = talkback_busy()
                    loud.clear()
                    preroll.clear()
                else:
                    history.append(level)

        finally:
            reader.terminate()

    def ends_with_over(self, frames: list) -> bool:
        """Quick look at the last few seconds: did they finish with "over"?"""
        tail = np.concatenate(frames[-130:])  # ~4 s
        segments, _ = self.model.transcribe(tail, language="en", beam_size=1, vad_filter=False)
        return bool(OVER.search(" ".join(seg.text for seg in segments).strip()))

    def utterance(self, reader, frames: list, floor: float, how: str) -> float:
        """Record, transcribe and send one turn; return seconds to keep listening."""
        phase("recording")  # talkback goes quiet and holds
        self.state = "recording"
        since, quiet, peak = time.monotonic(), 0, -120.0
        while quiet < END_FRAMES and time.monotonic() - since < MAX_SECONDS:
            data = reader.stdout.read(FRAME * 2)
            if len(data) < FRAME * 2:
                break
            if len(frames) % 10 == 0 and ptt_busy():
                # The Copilot key took over: push-to-talk will deliver these words,
                # so drop ours rather than send them twice (and leave its phase alone)
                log("push-to-talk took over; dropped the hands-free recording")
                return 0.0
            frame = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            frames.append(frame)
            level = 20 * np.log10(np.sqrt((frame**2).mean()) + 1e-6)
            peak = max(peak, level)
            # Quiet = back near the room's floor, or far below how loud they spoke
            quiet = quiet + 1 if level < max(floor + END_DB, peak - 25) else 0
            if quiet == OVER_FRAMES and self.ends_with_over(frames):
                break
        reason = "over" if quiet == OVER_FRAMES else "pause" if quiet >= END_FRAMES else "limit"
        log(f"recorded {time.monotonic() - since:.1f}s, ended by {reason}")
        phase("transcribing")
        self.state = "transcribing"
        audio = (np.concatenate(frames) * 32767).astype("<i2")
        with wave.open(str(WAV), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(audio.tobytes())
        # A cough or a bare "um" has no speech in it, and Whisper given only
        # that tends to write out its hint words ("Cursor, Cursor, Kokoro")
        if not get_speech_timestamps(audio.astype(np.float32) / 32768, SPEECH):
            text = rich = ""
            log("no speech in the recording")
        else:
            try:
                text, rich = ptt.transcribe(self.model, WAV, self.cfg)
            except Exception as exc:
                log(f"transcribe failed: {exc}")
                text = rich = ""
        WAV.unlink(missing_ok=True)
        tone = re.search(r"\s*\[tone: [^\]]*\]$", rich)
        note = tone.group(0) if tone else ""
        rich = rich[: len(rich) - len(note)] if note else rich
        text, rich = OVER.sub("", text), OVER.sub("", rich) + note
        text, rich = WAKE_PREFIX.sub("", text), WAKE_PREFIX.sub("", rich)
        thinking = bool(THINK.search(text))
        if thinking and len(text.split()) <= 12:  # only asking for time: nothing to send
            log("heard: give me a minute")
            ptt.note("target", {"agent": False})
            phase("idle")
            say("Take your time.")
            return THINK_WINDOW
        # One word is a real answer ("yes", "go", "stop"); only bare fillers are noise
        filler = re.fullmatch(r"\W*(um+|uh+|hmm+|mm+|ah+|oh|eh)\W*", text, re.I)
        to_agent = bool(text.strip()) and not filler and not CANCEL.match(text)
        if text and OFF.search(text):
            to_agent = False
            log("heard: hands-free off")
            self.stop.set()
            say("Hands-free off.")
        elif text and CANCEL.match(text):
            log("heard: never mind")
        elif not to_agent:
            log(f"ignored {len(text.split())} words")
        ptt.note("target", {"agent": to_agent})
        phase("idle")
        if to_agent and send(rich, how):
            ptt.ding(self.cfg)
        return THINK_WINDOW if thinking else 0.0


def tray(listener: Listener, stop: threading.Event) -> None:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
    from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    headset = QIcon.fromTheme("audio-headset")  # not a mic: that is push-to-talk

    def tinted(color: str) -> QIcon:
        """The headset in one colour, like push-to-talk's red and yellow bubble."""
        pixmap = headset.pixmap(22, 22)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        return QIcon(pixmap)

    icons = {"recording": tinted("#e24a4a"), "transcribing": tinted("#e0a100")}
    icon = QSystemTrayIcon(headset)
    menu = QMenu()
    off = QAction("Turn hands-free off")
    off.triggered.connect(stop.set)
    menu.addAction(off)
    icon.setContextMenu(menu)
    icon.show()
    labels = {
        "waiting": "Hands-free on: waiting for an agent to speak",
        "listening": "Hands-free: listening for your answer",
        "recording": "Hands-free: recording you",
        "transcribing": "Hands-free: transcribing",
    }

    shown = [""]

    def poll() -> None:
        icon.setToolTip(labels.get(listener.state, "Hands-free on"))
        if listener.state != shown[0]:
            shown[0] = listener.state
            icon.setIcon(icons.get(listener.state, headset))
        if stop.is_set():
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)
    app.exec_()


def run() -> int:
    cfg = ptt.load_config()
    canceller = start_canceller()
    model = ptt.load_model(cfg, ptt.want_cuda(cfg))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    listener = Listener(cfg, model, stop)
    worker = threading.Thread(target=listener.run, daemon=True)
    worker.start()
    log("on")
    say("Hands-free is on. I'll listen for a few seconds after I speak, and you can talk over me.")
    try:
        tray(listener, stop)
    finally:
        stop.set()
        canceller.terminate()
        if ptt_phase().get("by") == "handsfree":
            phase("idle")
        FLAG.unlink(missing_ok=True)
        log("off")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "run":
        return run()
    if command == "on":
        STATE.mkdir(parents=True, exist_ok=True)
        FLAG.touch()
        TALKBACK.touch()  # hands-free needs the agents to talk
        return subprocess.call(["systemctl", "--user", "start", UNIT])
    if command == "close":
        CLOSE.touch()
        return 0
    if command == "listen":  # e.g. after asking something that needs thought
        LISTEN.write_text(str(time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 180)))
        return 0
    if command == "off":
        FLAG.unlink(missing_ok=True)
        return subprocess.call(["systemctl", "--user", "stop", UNIT])
    active = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                            capture_output=True, text=True).stdout.strip()
    print(f"hands-free {'on' if active == 'active' else 'off'}")
    return 0


if __name__ == "__main__":
    if shutil.which("qdbus") is None:
        sys.exit("qdbus is needed to type into Konsole")
    sys.exit(main())

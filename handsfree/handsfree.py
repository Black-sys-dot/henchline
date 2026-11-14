#!/usr/bin/env python3
"""Hands-free conversation with the talkback agents.

    handsfree on | off | status        (handsfree run is the service itself)
    handsfree switch AGENT [WORKSPACE] hand the user over to that agent, announced

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
import os
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
ANNOUNCE = STATE / "handsfree.announce"  # set by `handsfree on`: say so once
TALKBACK = STATE / "talkback"
LOG = STATE / "handsfree.log"
WAV = STATE / "handsfree.wav"
LAST = STATE / "talkback.last"
PLAYING = STATE / "talkback.playing"
QUEUE = STATE / "talkback.queue"
TALKBACK_PID = STATE / "talkback.pid"
SAY = Path(__file__).resolve().parent.parent / "talkback/scripts/say.py"
EC_CONF = Path.home() / ".config/pipewire/talkback-echo-cancel.conf"
EC_SOURCE = "talkback_ec_source"
UNIT = "handsfree.service"
EC_UNIT = "talkback-echo-cancel.service"
WARY = 60.0  # after an echo cut a clip, how long talking over stays stricter
WARMUP = 60.0  # seconds after the canceller starts in which talking over a clip needs a louder voice
WAKE_MODEL = Path.home() / ".local/share/handsfree/hey_claude_personal.pkl"
WAKE_PREFIX = re.compile(r"^\W*((hey|hi|okay|ok)\W+)?claude\W*", re.I)
PAUSE = STATE / "handsfree.pause"  # e.g. while wake-word samples are recorded
CLOSE = STATE / "handsfree.close"  # `handsfree close`: end the reply window now
LISTEN = STATE / "handsfree.listen"  # `handsfree listen N`: keep the window open N seconds

RATE = 16000
FRAME = 480  # 30 ms
WINDOW = 8.0  # seconds to answer after a clip
WAIT_FOR_WORDS = 5.0  # after the wake word, seconds to start talking before it gives up
AFTER_SWITCH = 30.0  # seconds to start talking after "Now talking to …"
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
# times in a row (2026-11-14 20:18), so this sits in between; 0.45/200 ms
# still stopped on a throat clear, hence 0.5/300 ms (a real interruption is longer)
SPEECH_OVER = VadOptions(threshold=0.5, min_speech_duration_ms=300, min_silence_duration_ms=100,
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


def cursor_window(where: str) -> str:
    """The Cursor editor window for a workspace, by its title "… - <where> - Cursor"."""
    try:
        listing = subprocess.check_output(["xprop", "-root", "_NET_CLIENT_LIST"], text=True)
        for wid in re.findall(r"0x[0-9a-f]+", listing):
            cls = subprocess.run(["xprop", "-id", wid, "WM_CLASS"], capture_output=True, text=True).stdout
            if '"cursor"' not in cls.lower():
                continue
            title = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True, text=True).stdout
            title = title.split("=", 1)[-1].strip().strip('"')
            if title == f"{where} - Cursor" or title.endswith(f" - {where} - Cursor"):
                return wid
    except (subprocess.CalledProcessError, OSError):
        pass
    return ""


def type_into_cursor(wid: str, message: str) -> bool:
    """Raise the window, focus its agent chat, paste, Enter.

    Cursor's chat is not a terminal, so it is reached through the editor: the
    window is activated over EWMH, then ctrl+alt+shift+F12 runs
    composer.focusComposer (a user keybinding, see agents/README.md).
    """
    import ctypes

    if not ptt.clipboard(message):
        return False
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

    class Event(ctypes.Structure):  # XClientMessageEvent, padded to sizeof(XEvent)
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                    ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                    ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                    ("data", ctypes.c_long * 5), ("pad", ctypes.c_long * 12)]

    disp = x11.XOpenDisplay(os.environ.get("DISPLAY", ":0").encode())
    if not disp:
        return False
    root = x11.XDefaultRootWindow(disp)
    event = Event(type=33, send_event=1, window=int(wid, 16), format=32,
                  message_type=x11.XInternAtom(disp, b"_NET_ACTIVE_WINDOW", 0))
    event.data[0] = 2  # source: a pager, so the window manager obeys
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    x11.XSendEvent(disp, root, 0, (1 << 20) | (1 << 19), ctypes.byref(event))
    x11.XFlush(disp)
    time.sleep(0.4)

    def tap(*keysyms: int) -> None:
        codes = [x11.XKeysymToKeycode(disp, k) for k in keysyms]
        for code in codes:
            xtst.XTestFakeKeyEvent(disp, code, 1, 0)
        for code in reversed(codes):
            xtst.XTestFakeKeyEvent(disp, code, 0, 0)
        x11.XFlush(disp)
        time.sleep(0.15)

    tap(0xFFE3, 0xFFE9, 0xFFE1, 0xFFC9)  # ctrl+alt+shift+F12: focus the agent chat
    time.sleep(0.3)
    tap(0xFFE3, 0x76)  # ctrl+v
    time.sleep(0.2)
    tap(0xFF0D)  # Enter
    x11.XCloseDisplay(disp)
    return True


SPEAKERS = STATE / "talkback.speakers"  # every agent that has spoken (talkback)
AGENT_NAMES = r"(claude|cursor|codex)"
WHERE = r"(?:\s+(?:in|from|on)\s+(?:the\s+)?(.+?))?"
SWITCH = re.compile(
    rf"^\W*(?:(?:okay|ok|alright|right|so|no|actually)[\s,]+)?(?:(?:hey\s+)?(?:claude|cursor|codex)[\s,]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?"
    rf"(?:switch(?:\s+me)?(?:\s+back)?|(?:hand|pass)(?:\s+(?:me|it|this))?(?:\s+(?:over|back))?|(?:give|send)(?:\s+(?:me|it|this))?\s+(?:over|back)|go\s+back|"
    rf"(?:let\s+me\s+)?talk|connect\s+me|put\s+me\s+through|i\s+want\s+to\s+talk|back)"
    rf"\s+(?:over\s+)?to\s+{AGENT_NAMES}{WHERE}(?:\s+please)?\W*$", re.I)
SEND_THAT = re.compile(rf"^\W*(?:please\s+)?(?:send|forward|give)\s+(?:that|this|it)(?:\s+message)?\s+to\s+{AGENT_NAMES}{WHERE}\W*$", re.I)
# "…actually, scratch that, hand this to Cursor": drop what came before, then switch
DISCARD = re.compile(r".*\b(?:never\s*mind|scratch\s+that|forget\s+(?:it|that|all\s+that|what\s+i\s+(?:just\s+)?said))\W+(.+)$",
                     re.I | re.S)
WHO = re.compile(r"^\W*who\s+(?:am\s+i|are\s+you)\s+(?:talking\s+to|speaking\s+to)?\W*$|^\W*who\s+is\s+(?:this|listening)\W*$", re.I)


def squash(words: str) -> str:
    return re.sub(r"[^a-z0-9]", "", words.lower())


def find_speaker(agent: str, where: str | None) -> dict:
    """The most recent agent of that kind to speak, in that workspace if named."""
    found = []
    for item in SPEAKERS.glob("*.json"):
        try:
            record = json.loads(item.read_text())
        except (OSError, ValueError):
            continue
        if record.get("agent") != agent.lower() or time.time() - record.get("at", 0) > 24 * 3600:
            continue  # only agents heard in the last day
        if where and squash(where) not in squash(record.get("where", "")) \
                and squash(record.get("where", "")) not in squash(where):
            continue
        found.append(record)
    return max(found, key=lambda r: r.get("at", 0)) if found else {}


def spoken(record: dict) -> str:
    where = record.get("where", "").replace("_", " ").replace("-", " ")
    return f"{record['agent'].capitalize()}, in {where}" if where else record["agent"].capitalize()


def send(text: str, how: str) -> bool:
    """Type the words into the Konsole session of the agent that spoke last."""
    try:
        last = json.loads(LAST.read_text())
    except (FileNotFoundError, ValueError):
        last = {}
    service, session = last.get("konsole") or ["", ""]
    if (not service or not session) and last.get("agent") == "cursor" and last.get("where"):
        wid = cursor_window(last["where"])
        if not wid:
            log(f"no Cursor window for {last['where']}")
            say("I heard you, but I can't find Cursor's window for that project.")
            return False
        # In a child process: Xlib from this worker thread, beside the Qt tray
        # thread, crashed the whole service (SIGSEGV, 2026-11-14)
        ok = subprocess.run([sys.executable, str(Path(__file__).resolve()), "type-into-cursor", wid,
                             f"[hands-free · {how}] {text}"], timeout=20).returncode == 0
        log(f"sent to cursor window {last['where']}" if ok else "Cursor delivery failed")
        return ok
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
    if not re.search(r"claude|cursor-agent|/agent\b|codex|gemini|opencode", command):
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


def start_canceller() -> float:
    """Start the echo canceller unit if needed; return when it started (monotonic).

    It runs as its own unit (talkback-echo-cancel.service) so restarting
    hands-free does not reset it: a fresh canceller has to learn the room
    again, and every echo leak on 2026-11-14 came within a minute of one.
    """
    subprocess.run(["systemctl", "--user", "start", EC_UNIT], check=False)
    for _ in range(50):
        sources = subprocess.run(["pactl", "list", "short", "sources"],
                                 capture_output=True, text=True).stdout
        if EC_SOURCE in sources:
            since = subprocess.run(["systemctl", "--user", "show", "-p", "ActiveEnterTimestampMonotonic",
                                    "--value", EC_UNIT], capture_output=True, text=True).stdout.strip()
            return int(since) / 1e6 if since.isdigit() else time.monotonic()
        time.sleep(0.1)
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


class EarbudButton(threading.Thread):
    """Bluetooth earbud taps (AVRCP media keys) as a silent "hey Claude".

    While hands-free runs it grabs every "… (AVRCP)" input device, so a tap
    does not also pause music; when hands-free stops, the grab ends and the
    button is the desktop's again. Earbuds come and go, so it rescans.
    """

    TAP = {"KEY_PLAYCD", "KEY_PAUSECD", "KEY_PLAYPAUSE"}

    def __init__(self, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.stop, self.tap = stop, threading.Event()

    def run(self) -> None:
        import select

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
                        log(f"earbud button: {device.name}")
                except OSError:
                    continue
            ready, _, _ = select.select(list(held.values()), [], [], 3) if held else ([], [], [])
            if not held:
                self.stop.wait(3)
            for device in ready:
                try:
                    for event in device.read():
                        if event.type == ecodes.EV_KEY and event.value == 1:
                            name = ecodes.KEY.get(event.code, "")
                            if name in self.TAP and time.monotonic() - last > 2:  # one press, not its echo
                                last = time.monotonic()
                                self.tap.set()
                            else:
                                log(f"earbud {name} ignored")
                except OSError:  # disconnected
                    held.pop(device.path, None)
                    log("earbud button gone")


class Listener:
    def __init__(self, cfg: dict, model, stop: threading.Event) -> None:
        self.cfg, self.model, self.stop = cfg, model, stop
        self.state = "waiting"  # waiting / listening / recording / transcribing
        self.last_activity = time.monotonic()
        self.wake = Wake() if WAKE_MODEL.exists() else None
        self.on_cpu = False
        self.wary_until = 0.0
        self.canceller_since = time.monotonic()  # run() sets the real start
        self.last_message: tuple[str, str] | None = None  # for "send that to …"
        self.button = EarbudButton(stop)
        self.crashed = False

    @staticmethod
    def voiced(frame: np.ndarray) -> bool:
        """Speech carries most of its energy below 1 kHz; a spoon on a mug does not."""
        power = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
        freqs = np.fft.rfftfreq(len(frame), 1 / RATE)
        total = power[freqs >= 80].sum()
        return bool(total) and power[(freqs >= 80) & (freqs < 1000)].sum() / total > VOICE_SHARE

    def run(self) -> None:
        try:
            self.listen()
        except Exception:
            import traceback

            log("listener crashed, restarting: " + traceback.format_exc().strip().splitlines()[-1])
            self.crashed = True
            if ptt_phase().get("by") == "handsfree":
                phase("idle")
            self.stop.set()

    def listen(self) -> None:
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
                if self.button.tap.is_set():  # earbud tap: like "hey Claude", any time
                    self.button.tap.clear()
                    if not ptt_busy():
                        log("earbud tap")
                        ptt.ding(self.cfg)
                        floor = np.percentile(room, 20) if len(room) > 30 else level
                        extra = self.utterance(reader, [], floor, "woke")
                        self.last_activity = time.monotonic()
                        window_until, kind = time.monotonic() + (extra or FOLLOW), ("think" if extra else "follow-on")
                        was_busy = talkback_busy()
                        continue
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
                        if self.whisper(self.wake.confirm):
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
                # A fresh canceller still leaks some of our own voice: be stricter then
                warming = playing and (time.monotonic() - self.canceller_since < WARMUP
                                       or time.monotonic() < self.wary_until)
                over = level > floor + (BARGE_DB + 10 if warming else BARGE_DB if playing else START_DB)
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

    def whisper(self, use):
        """use(model); if the GPU is full, move Whisper to the CPU and try again.

        A training run on the same GPU left no room on 2026-11-14 and the
        CUDA error killed the listening thread.
        """
        try:
            return use(self.model)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or self.on_cpu:
                raise
            log("GPU out of memory: Whisper moves to the CPU")
            self.model, self.on_cpu = ptt.load_model(self.cfg, False), True
            return use(self.model)

    def ends_with_over(self, frames: list) -> bool:
        """Quick look at the last few seconds: did they finish with "over"?"""
        tail = np.concatenate(frames[-130:])  # ~4 s
        text = self.whisper(lambda model: " ".join(
            seg.text for seg in model.transcribe(tail, language="en", beam_size=1, vad_filter=False)[0]))
        return bool(OVER.search(text.strip()))

    def utterance(self, reader, frames: list, floor: float, how: str) -> float:
        """Record, transcribe and send one turn; return seconds to keep listening."""
        phase("recording")  # talkback goes quiet and holds
        self.state = "recording"
        since, quiet, peak = time.monotonic(), 0, -120.0
        # After "hey Claude" people pause for the chime: the pause that ends the
        # turn only counts once they have started speaking (up to WAIT_FOR_WORDS)
        started = how != "woke"
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
            if not started:
                started = level > floor + START_DB or time.monotonic() - since > WAIT_FOR_WORDS
                continue
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
                text, rich = self.whisper(lambda model: ptt.transcribe(model, WAV, self.cfg))
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
        routed = self.route(text)
        if routed is not None:
            ptt.note("target", {"agent": False})
            phase("idle")
            return routed
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
            if how == "over you":
                # Talking over a clip but nothing said: our own echo cut the clip.
                # Without this it cuts, finds nothing, resumes and cuts again.
                self.wary_until = time.monotonic() + WARY
                log(f"echo cut a clip: stricter about talking over for {WARY:.0f}s")
        ptt.note("target", {"agent": to_agent})
        phase("idle")
        if to_agent and send(rich, how):
            ptt.ding(self.cfg)
            self.last_message = (rich, how)
        return THINK_WINDOW if thinking else 0.0

    def route(self, text: str) -> float | None:
        """Spoken routing ("switch to Cursor", "send that to Claude in pc
        diagnostics", "who am I talking to?"). Returns None when it is not one."""
        try:
            current = json.loads(LAST.read_text())
        except (OSError, ValueError):
            current = {}
        if WHO.match(text):
            log("heard: who am I talking to")
            say(f"You're talking to {spoken(current)}." if current.get("agent") else
                "Nobody yet: no agent has spoken.")
            return 0.0
        match = SWITCH.match(text) or SEND_THAT.match(text)
        if not match and (tail := DISCARD.match(text)):
            match = SWITCH.match(tail.group(1))
            if match:
                log("heard: discard and switch")
        if not match:
            return None
        agent, where = match.group(1), match.group(2)
        target = find_speaker(agent, where)
        if not target:
            log(f"heard: route to {agent}, none found")
            say(f"I can't find {agent.capitalize()}{' in ' + where if where else ''}. "
                "It has to have spoken through talkback first.")
            return 0.0
        LAST.write_text(json.dumps({k: target[k] for k in ("session", "agent", "where", "konsole")}))
        LISTEN.write_text(str(time.time() + AFTER_SWITCH))  # time to start talking to the new agent
        log(f"heard: route to {target['agent']} in {target.get('where', '?')}")
        if SEND_THAT.match(text):
            if not self.last_message:
                say(f"Now talking to {spoken(target)}, but there's nothing to send yet.")
            elif send(*self.last_message):
                ptt.ding(self.cfg)
                say(f"Sent to {spoken(target)}.")
        else:
            say(f"Now talking to {spoken(target)}.")
        return 0.0


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

    # Plain headset: only the wake word gets in. Green: listening (reply window,
    # or while an agent speaks). Red: recording you. Yellow: transcribing.
    icons = {"listening": tinted("#3cb371"), "recording": tinted("#e24a4a"),
             "transcribing": tinted("#e0a100")}
    icon = QSystemTrayIcon(headset)
    menu = QMenu()
    off = QAction("Turn hands-free off")
    off.triggered.connect(stop.set)
    menu.addAction(off)
    icon.setContextMenu(menu)
    icon.show()
    labels = {
        "waiting": "Hands-free on: only \"hey Claude\" gets in",
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
    canceller_since = start_canceller()
    model = ptt.load_model(cfg, ptt.want_cuda(cfg))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    listener = Listener(cfg, model, stop)
    listener.canceller_since = canceller_since
    listener.button.start()
    worker = threading.Thread(target=listener.run, daemon=True)
    worker.start()
    log("on")
    if ANNOUNCE.exists():  # only when the user turned it on, not on every restart
        ANNOUNCE.unlink(missing_ok=True)
        say("Hands-free is on.")
    try:
        tray(listener, stop)
    finally:
        stop.set()
        if ptt_phase().get("by") == "handsfree":
            phase("idle")
        FLAG.unlink(missing_ok=True)
        log("off")
    return 1 if listener.crashed else 0  # non-zero: systemd starts it again


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "type-into-cursor":  # internal, see send()
        return 0 if type_into_cursor(sys.argv[2], sys.argv[3]) else 1
    if command == "run":
        return run()
    if command == "on":
        STATE.mkdir(parents=True, exist_ok=True)
        FLAG.touch()
        ANNOUNCE.touch()
        TALKBACK.touch()  # hands-free needs the agents to talk
        return subprocess.call(["systemctl", "--user", "start", UNIT])
    if command == "switch":  # e.g. an agent asked by the user to hand them over
        if len(sys.argv) < 3:
            sys.exit("usage: handsfree switch claude|cursor|codex [workspace words]")
        target = find_speaker(sys.argv[2], " ".join(sys.argv[3:]) or None)
        if not target:
            print(f"no {sys.argv[2]} has spoken through talkback in the last day")
            return 1
        LAST.write_text(json.dumps({k: target[k] for k in ("session", "agent", "where", "konsole")}))
        LISTEN.write_text(str(time.time() + AFTER_SWITCH))
        log(f"switched by command to {target['agent']} in {target.get('where', '?')}")
        say(f"Now talking to {spoken(target)}.")
        print(f"now talking to {spoken(target)}")
        return 0
    if command == "close":
        CLOSE.touch()
        return 0
    if command == "listen":  # e.g. after asking something that needs thought
        LISTEN.write_text(str(time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 180)))
        return 0
    if command == "off":
        FLAG.unlink(missing_ok=True)
        return subprocess.call(["systemctl", "--user", "stop", UNIT, EC_UNIT])
    active = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                            capture_output=True, text=True).stdout.strip()
    print(f"hands-free {'on' if active == 'active' else 'off'}")
    return 0


if __name__ == "__main__":
    if shutil.which("qdbus") is None:
        sys.exit("qdbus is needed to type into Konsole")
    sys.exit(main())

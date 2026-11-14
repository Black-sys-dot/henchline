"""The hands-free listener: when to listen, recording a turn, and what to do with it.

It reads the echo-cancelled mic 30 ms at a time. Speech counts only while a
clip plays (talking over it) or while a window is open: after a clip, after
the user spoke, after "give me a minute", after a switch. The rest of the
time only the wake word or an earbud tap gets in.
"""

from __future__ import annotations

import collections
import re
import time
import traceback
import wave
from dataclasses import dataclass
from enum import Enum

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

import routing
from routing import Heard, Meaning
from sensing import (EC_SOURCE, LEVEL_SPAN, PER_SECOND, RATE, EarbudButton, EchoWatch, Output, Playback, Wake,
                     level, mic_stream, read_frame, start_canceller, voiced)
from voice import whisper
from voice.spool import queued
from voice.state import (BY_HANDSFREE, CLOSE, HANDSFREE_LOG, HANDSFREE_WAV, LISTEN, PAUSE, PLAYING, WORKER_PID,
                         Phase, log, phase_now, release_phase, set_phase, set_target)

WINDOW = 8.0  # seconds to answer after a clip
FOLLOW = 6.0  # seconds to carry on after their own message (once, never chained)
THINK_WINDOW = 180  # after "give me a minute"
WAIT_FOR_WORDS = 5.0  # after the wake word, seconds to start talking before it gives up
IDLE = 15 * 60  # no clip and no speech for this long: hands-free turns itself off
START_DB, BARGE_DB, WARM_DB = 12, 15, 10  # dB over the floor to count; while a clip plays; extra when wary
LOUD_SPAN = 12  # frames looked at for loud, voiced ones
START_FRAMES, BARGE_FRAMES = 6, 8  # of the last LOUD_SPAN, voiced and loud enough to count; while a clip plays
CHECK_EVERY = 3  # frames between Silero checks (90 ms)
BARGE_GRACE = 0.7  # seconds at the start of a clip before the canceller has caught up
WARMUP = 60.0  # seconds after the canceller starts in which talking over needs a louder voice
WARY = 60.0  # after our own echo cut a clip, how long talking over stays stricter
ECHO_MARGIN = -15.0  # talking over must be no more than this many dB under the playback (echo is 20+ under)
FLOOR_SPAN = 10 * PER_SECOND  # frames of history a floor is taken from
FLOOR_MIN = 30  # frames of history before a floor is trusted; until then the frame itself is the floor
END_DB, END_FRAMES = 6, 50  # 1.5 s at most END_DB over the floor ends the turn
BELOW_PEAK = 25  # dB under their loudest also counts as quiet, for a noisy room
OVER_FRAMES = 20  # after 0.6 s of quiet, check whether the last word was "over"
OVER_TAIL = 4 * PER_SECOND  # frames Whisper looks at for "over"
PTT_CHECK = 10  # frames between checks that push-to-talk has not taken the mic
PREROLL = 15  # frames kept from before speech started
MAX_SECONDS = 120  # the user talks in long stretches; 60 s cut them off
# Silero must find a voice before anything stops. While a clip plays the canceller
# squashes the user's voice, but 0.3/150 ms cut talkback off on its own echo and
# 0.45/200 ms on a throat clear (2026-11-14): 0.5/300 ms
SPEECH = VadOptions(threshold=0.6, min_speech_duration_ms=250, min_silence_duration_ms=100, speech_pad_ms=0)
SPEECH_OVER = VadOptions(threshold=0.5, min_speech_duration_ms=300, min_silence_duration_ms=100, speech_pad_ms=0)
WAKE_PREFIX = re.compile(r"^\W*((hey|hi|okay|ok)\W+)?claude\W*", re.I)
OVER = re.compile(r"[\s,.!?]*\bover\b[\s.!?]*$", re.I)
TONE_NOTE = re.compile(r"\s*\[tone: [^\]]*\]$")


class Mode(str, Enum):
    """What the tray shows."""

    WAITING = "waiting"  # only the wake word gets in
    LISTENING = "listening"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


@dataclass
class Window:
    """When the user may talk without the wake word, and how that would be heard."""

    until: float = 0.0
    kind: Heard = Heard.REPLY

    def open(self, seconds: float, kind: Heard, never_shorter: bool = False) -> None:
        if not never_shorter or time.monotonic() + seconds > self.until:
            self.until, self.kind = time.monotonic() + seconds, kind

    def close(self) -> None:
        self.until = 0.0

    def is_open(self) -> bool:
        return time.monotonic() < self.until


def talkback_busy() -> bool:
    return PLAYING.exists() or WORKER_PID.exists() or bool(queued())


def ptt_busy() -> bool:
    """Push-to-talk itself is recording: leave the mic to it."""
    phase, by = phase_now()
    return phase is not Phase.IDLE and by != BY_HANDSFREE


def floor_of(history: collections.deque, fallback: float) -> float:
    return float(np.percentile(history, 20)) if len(history) > FLOOR_MIN else fallback


class Listener:
    def __init__(self, cfg: whisper.Config, model, stop, canceller_since: float) -> None:
        self.cfg, self.model, self.stop = cfg, model, stop
        self.canceller_since = canceller_since
        self.stream = None
        self.mode = Mode.WAITING
        self.window = Window()
        self.wake = Wake.available()
        self.button, self.playback, self.output = EarbudButton(stop), Playback(stop), Output()
        self.echo_watch, self.restart_due = EchoWatch(), False
        self.last_activity, self.wary_until, self.play_started = time.monotonic(), 0.0, 0.0
        self.on_cpu = self.crashed = False
        self.last_message: tuple[str, Heard] | None = None  # for "send that to …"
        # Floors kept apart: while a clip plays the cancelled mic sits far below the room
        self.room, self.echo = collections.deque(maxlen=FLOOR_SPAN), collections.deque(maxlen=FLOOR_SPAN)
        self.preroll = collections.deque(maxlen=PREROLL)
        self.loud = collections.deque(maxlen=LOUD_SPAN)
        self.recent = collections.deque(maxlen=PER_SECOND)  # last second, for the speech check
        self.levels = collections.deque(maxlen=LEVEL_SPAN)  # against the playback level
        self.checked = 0

    # --- the loop -----------------------------------------------------------

    def run(self) -> None:
        self.button.start()
        self.playback.start()
        try:
            self.listen()
        except Exception:
            log(HANDSFREE_LOG, "listener crashed, restarting: " + traceback.format_exc().strip().splitlines()[-1])
            self.crashed = True
            release_phase(BY_HANDSFREE)
            self.stop.set()

    def listen(self) -> None:
        self.stream = mic_stream(EC_SOURCE)
        was_busy = False
        try:
            while not self.stop.is_set() and (samples := read_frame(self.stream)) is not None:
                frame = samples.astype(np.float32) / 32768
                busy = talkback_busy()
                if busy:
                    self.last_activity = time.monotonic()
                elif was_busy:  # a clip just ended: the user may answer
                    self.window.open(WINDOW, Heard.REPLY, never_shorter=True)
                was_busy = busy
                self.follow_requests()
                if time.monotonic() - self.last_activity > IDLE:
                    log(HANDSFREE_LOG, "idle, turning off")
                    routing.say("Hands-free is going to sleep.")
                    self.stop.set()
                    break
                if self.button.tap.is_set():
                    self.button.tap.clear()
                    if not ptt_busy():
                        log(HANDSFREE_LOG, "earbud tap")
                        self.turn([], Heard.WOKE, floor_of(self.room, level(frame)))
                        was_busy = talkback_busy()
                        continue
                if self.step(samples, frame):
                    was_busy = talkback_busy()
                elif self.restart_due and not busy and not ptt_busy():  # between clips; a reply window may be open
                    self.restart_canceller()
        finally:
            self.stream.terminate()

    def restart_canceller(self) -> None:
        """A fresh canceller, between clips (about a second; a reply starting then loses its first words)."""
        log(HANDSFREE_LOG, "restarting the echo canceller before it drifts further")
        self.restart_due = False
        self.stream.terminate()
        self.canceller_since = start_canceller(restart=True)
        self.echo_watch.restarted = time.monotonic()
        self.echo.clear()
        self.stream = mic_stream(EC_SOURCE)

    def follow_requests(self) -> None:
        """`handsfree close` and `handsfree listen N` / a switch, through their files."""
        if CLOSE.exists():  # only the wake word opens it again
            CLOSE.unlink(missing_ok=True)
            LISTEN.unlink(missing_ok=True)
            self.window.close()
        try:
            self.window.open(float(LISTEN.read_text()) - time.time(), Heard.THINK, never_shorter=True)
        except (FileNotFoundError, ValueError):
            pass

    def step(self, samples: np.ndarray, frame: np.ndarray) -> bool:
        """One 30 ms frame; True when it led to a turn."""
        db = level(frame)
        playing = PLAYING.exists()
        if self.play_started and not playing:
            self.restart_due |= self.echo_watch.clip_over()
        self.play_started = (self.play_started or time.monotonic()) if playing else 0.0
        open_ = (playing or self.window.is_open()) and not ptt_busy() and not PAUSE.exists()
        self.mode = Mode.LISTENING if open_ else Mode.WAITING
        if not open_:
            self.loud.clear()
            self.preroll.clear()
            (self.echo if playing else self.room).append(db)
            return not playing and self.woke(samples, floor_of(self.room, db))
        history = self.echo if playing else self.room
        floor = floor_of(history, db)
        turn_floor = max(floor, floor_of(self.room, floor))
        self.preroll.append(frame)
        self.recent.append(frame)
        self.levels.append(db)
        self.checked += 1
        if playing and time.monotonic() - self.play_started > BARGE_GRACE:
            self.echo_watch.add(max(self.levels), self.playback.loudest())
        if playing and self.output.on_speakers() and self.playback.loudest() is None:
            history.append(db)  # playback level unknown: our voice might leak, so only the wake word
            return self.woke(samples, turn_floor)
        if not self.talking(frame, db, floor, playing):
            history.append(db)
            return False
        self.turn(list(self.preroll), Heard.OVER_YOU if playing else self.window.kind, turn_floor)
        return True

    def talking(self, frame: np.ndarray, db: float, floor: float, playing: bool) -> bool:
        """Is the user speaking now (and, over a clip, not just our own echo)?"""
        wary = playing and (time.monotonic() - self.canceller_since < WARMUP or time.monotonic() < self.wary_until)
        over = db > floor + (BARGE_DB + WARM_DB if wary else BARGE_DB if playing else START_DB)
        settled = not playing or time.monotonic() - self.play_started > BARGE_GRACE
        self.loud.append(over and settled and voiced(frame))
        if sum(self.loud) < (BARGE_FRAMES if playing else START_FRAMES) or self.checked < CHECK_EVERY:
            return False
        self.checked = 0
        if not get_speech_timestamps(np.concatenate(self.recent), SPEECH_OVER if playing else SPEECH):
            return False
        if not playing:
            return True
        played = self.playback.loudest()
        heard = max(self.levels)
        echo = played is not None and heard - played < ECHO_MARGIN  # far quieter than we play: our own echo
        near_miss = echo and self.echo_watch.near_miss(heard - played)
        self.restart_due |= near_miss
        # Every decision over a clip, in numbers (never audio), so a false cut can be explained
        log(HANDSFREE_LOG, f"over a clip: mic {heard:.0f} dB, playing {'?' if played is None else f'{played:.0f}'} dB, "
                           f"floor {floor:.0f} dB, {time.monotonic() - self.play_started:.1f}s in"
                           f"{', wary' if wary else ''}{', whisper on cpu' if self.on_cpu else ''}: "
                           f"{'echo, kept playing' if echo else 'cut'}{'; near miss: restart the canceller when quiet' if near_miss else ''}")
        return not echo

    def woke(self, samples: np.ndarray, floor: float) -> bool:
        if not self.wake or PAUSE.exists() or ptt_busy() or not self.wake.feed(samples):
            return False
        if not self.whisper(self.wake.confirm):
            log(HANDSFREE_LOG, "wake candidate rejected")
            return False
        log(HANDSFREE_LOG, "woke")
        self.turn([], Heard.WOKE, floor)
        return True

    # --- one turn -----------------------------------------------------------

    def turn(self, frames: list, heard: Heard, floor: float) -> None:
        """Record, transcribe, act; then decide how long to keep listening."""
        if heard is Heard.WOKE:
            whisper.ding(self.cfg)  # "I'm listening"
        if heard is Heard.OVER_YOU:
            self.echo_watch.discard()
        kept = self.window.until, self.window.kind
        LISTEN.unlink(missing_ok=True)  # they spoke: an earlier long window is used up (a switch writes a new one)
        extra = self.utterance(frames, floor, heard)
        self.last_activity = time.monotonic()
        self.loud.clear()
        self.preroll.clear()
        if extra is None:  # no words in it (a noise): the window they had stays theirs
            self.window.until, self.window.kind = kept
            if kept[0] > time.monotonic() and not LISTEN.exists():
                LISTEN.write_text(str(time.time() + kept[0] - time.monotonic()))
            return
        if extra:
            self.window.open(extra, Heard.THINK)
            LISTEN.write_text(str(time.time() + extra))
        elif heard is Heard.FOLLOW_ON:
            self.window.close()  # a follow-on never opens another, so chatter cannot chain
        else:
            self.window.open(FOLLOW, Heard.FOLLOW_ON)

    def record(self, frames: list, floor: float, heard: Heard) -> list | None:
        """Frames until a pause, "over", or the limit; None if push-to-talk took over."""
        set_phase(Phase.RECORDING, BY_HANDSFREE)  # talkback goes quiet and holds
        self.mode = Mode.RECORDING
        since, quiet, peak = time.monotonic(), 0, -120.0
        started = heard is not Heard.WOKE  # after "hey Claude" people pause for the chime
        while quiet < END_FRAMES and time.monotonic() - since < MAX_SECONDS:
            samples = read_frame(self.stream)
            if samples is None:
                break
            if len(frames) % PTT_CHECK == 0 and ptt_busy():
                log(HANDSFREE_LOG, "push-to-talk took over; dropped the hands-free recording")
                return None  # it delivers these words; ours would arrive twice
            frame = samples.astype(np.float32) / 32768
            frames.append(frame)
            db = level(frame)
            peak = max(peak, db)
            if not started:
                started = db > floor + START_DB or time.monotonic() - since > WAIT_FOR_WORDS
                continue
            quiet = quiet + 1 if db < max(floor + END_DB, peak - BELOW_PEAK) else 0
            if quiet == OVER_FRAMES and self.ends_with_over(frames):
                break
        reason = "over" if quiet == OVER_FRAMES else "pause" if quiet >= END_FRAMES else "limit"
        log(HANDSFREE_LOG, f"recorded {time.monotonic() - since:.1f}s, ended by {reason} (floor {floor:.0f} dB)")
        return frames

    def transcribe(self, frames: list) -> tuple[str, str]:
        """(plain text, text with tone marks); empty when there was no speech."""
        set_phase(Phase.TRANSCRIBING, BY_HANDSFREE)
        self.mode = Mode.TRANSCRIBING
        audio = (np.concatenate(frames) * 32767).astype("<i2") if frames else np.zeros(0, "<i2")
        # A cough or a bare "um" has no speech, and Whisper given only that tends
        # to write out its hint words ("Cursor, Cursor, Kokoro")
        if not len(audio) or not get_speech_timestamps(audio.astype(np.float32) / 32768, SPEECH):
            log(HANDSFREE_LOG, "no speech in the recording")
            return "", ""
        with wave.open(str(HANDSFREE_WAV), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(audio.tobytes())
        try:
            return self.whisper(lambda model: whisper.transcribe(model, HANDSFREE_WAV, self.cfg, HANDSFREE_LOG))
        except Exception as exc:
            log(HANDSFREE_LOG, f"transcribe failed: {exc}")
            return "", ""
        finally:
            HANDSFREE_WAV.unlink(missing_ok=True)

    def utterance(self, frames: list, floor: float, heard: Heard) -> float | None:
        """One turn, end to end; returns seconds to keep listening (0: the default), None if no words."""
        frames = self.record(frames, floor, heard)
        if frames is None:
            return 0.0
        try:
            text, rich = self.transcribe(frames)
            tone = TONE_NOTE.search(rich)
            note = tone.group(0) if tone else ""
            rich = rich[: len(rich) - len(note)] if note else rich
            text, rich = WAKE_PREFIX.sub("", OVER.sub("", text)), WAKE_PREFIX.sub("", OVER.sub("", rich)) + note
            return self.act(text, rich, heard)
        finally:
            release_phase(BY_HANDSFREE)  # act() normally has already, before anything is sent

    def act(self, text: str, rich: str, heard: Heard) -> float | None:
        meaning = routing.meaning(text)
        # Before anything is said or sent: talkback may speak again, and knows whether an agent got words
        set_target(meaning is Meaning.MESSAGE)
        set_phase(Phase.IDLE, BY_HANDSFREE)
        if meaning is Meaning.TIME:
            log(HANDSFREE_LOG, "heard: give me a minute")
            routing.say("Take your time.")
            return THINK_WINDOW
        if meaning is Meaning.ROUTING:
            if routing.route(text, self.last_message):
                whisper.ding(self.cfg)
            return 0.0
        if meaning is Meaning.OFF:
            log(HANDSFREE_LOG, "heard: hands-free off")
            self.stop.set()
            routing.say("Hands-free off.")
        elif meaning is Meaning.CANCEL:
            log(HANDSFREE_LOG, "heard: never mind")
        elif meaning is Meaning.NOTHING:
            log(HANDSFREE_LOG, f"ignored {len(text.split())} words")
            if heard is Heard.OVER_YOU:  # nothing said over a clip: our echo cut it; no cut-resume-cut loop
                self.wary_until = time.monotonic() + WARY
                self.restart_due |= self.echo_watch.may_restart()  # past the early warning already
                log(HANDSFREE_LOG, f"echo cut a clip: stricter about talking over for {WARY:.0f}s")
            return None
        elif routing.deliver(rich, heard):
            whisper.ding(self.cfg)
            self.last_message = (rich, heard)
        return THINK_WINDOW if routing.THINK.search(text) else 0.0

    # --- Whisper ------------------------------------------------------------

    def whisper(self, use):
        """use(model); if the GPU is full, move Whisper to the CPU and try again."""
        try:
            return use(self.model)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or self.on_cpu:
                raise
            log(HANDSFREE_LOG, "GPU out of memory: Whisper moves to the CPU")
            self.model, self.on_cpu = whisper.load_model(self.cfg, False, HANDSFREE_LOG), True
            return use(self.model)

    def ends_with_over(self, frames: list) -> bool:
        """Quick look at the last few seconds: did they finish with "over"?"""
        tail = np.concatenate(frames[-OVER_TAIL:])
        text = self.whisper(lambda model: " ".join(
            seg.text for seg in model.transcribe(tail, language="en", beam_size=1, vad_filter=False)[0]))
        return bool(OVER.search(text.strip()))

"""The hands-free listener: when the user may talk, and when a turn starts.

It reads the echo-cancelled mic 30 ms at a time. Speech counts only while a
clip plays (talking over it, talkover.py) or while a window is open: after a
clip, after the user spoke, after "give me a minute", after a switch. The rest
of the time only the wake word or an earbud tap gets in. A turn itself, from
recording to acting on the words, is turn.py.
"""

from __future__ import annotations

import collections
import time
import traceback
from dataclasses import dataclass

import numpy as np

from routing import Heard
from sensing import (EC_SOURCE, FRAME, PER_SECOND, RATE, EarbudButton, EchoWatch, Output, Playback, Wake, level,
                     mic_stream, read_frame, start_canceller)
from talkover import TalkOver, Verdict
from turn import Mode, Turn
from voicegate import VoiceGate
from voice import whisper
from voice.spool import queued
from voice.state import (BY_HANDSFREE, CLOSE, HANDSFREE_LOG, LISTEN, PAUSE, PLAYING, WORKER_PID, log, ptt_busy,
                         release_phase)

WINDOW = 8.0  # seconds to answer after a clip
FOLLOW = 6.0  # seconds to carry on after their own message (once, never chained)
BARGE_GRACE = 0.7  # seconds at the start of a clip before the canceller has caught up
WARMUP = 60.0  # seconds after the canceller starts in which talking over needs a louder voice
WARY = 60.0  # after our own echo slipped through, how long talking over stays stricter
FLOOR_SPAN = 10 * PER_SECOND  # frames of history a floor is taken from
FLOOR_MIN = 30  # frames of history before a floor is trusted; until then the frame itself is the floor
PREROLL = 15  # frames kept from before speech started


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


def floor_of(history: collections.deque, fallback: float) -> float:
    return float(np.percentile(history, 20)) if len(history) > FLOOR_MIN else fallback


class Listener:
    def __init__(self, cfg: whisper.Config, model, stop, canceller_since: float) -> None:
        self.cfg, self.stop = cfg, stop
        self.canceller_since = canceller_since
        self.transcriber = whisper.Transcriber(cfg, model, HANDSFREE_LOG)
        self.stream, self.opened, self.frames_read = None, 0.0, 0
        self.talkover = TalkOver(self.transcriber.quick_text)
        self.turns = Turn(cfg, self.transcriber, self.talkover, self.read, self.show, self.echo_slipped, stop)
        self.last_check = ""
        self.mode = Mode.WAITING
        self.window = Window()
        self.wake = Wake.available()
        self.button, self.playback, self.output = EarbudButton(stop), Playback(stop), Output()
        self.echo_watch, self.restart_due = EchoWatch(restarted=canceller_since), False
        self.wary_until, self.play_started = 0.0, 0.0
        self.crashed = False
        # Floors kept apart: while a clip plays the cancelled mic sits far below the room
        self.room, self.echo = collections.deque(maxlen=FLOOR_SPAN), collections.deque(maxlen=FLOOR_SPAN)
        self.preroll = collections.deque(maxlen=PREROLL)
        self.gate = VoiceGate()

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
        self.open_stream()
        was_busy = False
        try:
            while not self.stop.is_set() and (samples := self.read()) is not None:
                frame = samples.astype(np.float32) / 32768
                busy = talkback_busy()
                if not busy and was_busy:  # a clip just ended: the user may answer
                    self.window.open(WINDOW, Heard.REPLY, never_shorter=True)
                was_busy = busy
                self.follow_requests()
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

    def show(self, mode: Mode) -> None:
        self.mode = mode

    def open_stream(self) -> None:
        self.stream = mic_stream(EC_SOURCE)
        self.opened, self.frames_read = time.monotonic(), 0

    def read(self) -> np.ndarray | None:
        self.frames_read += 1
        return read_frame(self.stream)

    def behind(self) -> float:
        """Seconds the frame being handled lags real time (the stream's start latency included)."""
        return time.monotonic() - self.opened - self.frames_read * FRAME / RATE

    def echo_slipped(self, why: str) -> None:
        """Our own voice got past the canceller: a fresh one when it may, and stricter for a while."""
        self.restart_due |= self.echo_watch.may_restart()
        self.wary_until = time.monotonic() + WARY
        log(HANDSFREE_LOG, f"{why}: stricter about talking over for {WARY:.0f}s")

    def restart_canceller(self) -> None:
        """A fresh canceller, between clips (about a second; a reply starting then loses its first words)."""
        log(HANDSFREE_LOG, "restarting the echo canceller before it drifts further")
        self.restart_due = False
        self.stream.terminate()
        self.canceller_since = start_canceller(restart=True)
        self.echo_watch.restarted = time.monotonic()
        self.echo.clear()
        self.open_stream()

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
        self.talkover.track_onset(self.playback.loudest())
        if self.play_started and not playing:
            self.restart_due |= self.echo_watch.clip_over()
        self.play_started = (self.play_started or time.monotonic()) if playing else 0.0
        open_ = (playing or self.window.is_open()) and not ptt_busy() and not PAUSE.exists()
        self.mode = Mode.LISTENING if open_ else Mode.WAITING
        if not open_:
            self.gate.clear()
            self.preroll.clear()
            self.talkover.reset()
            (self.echo if playing else self.room).append(db)
            return not playing and self.woke(samples, floor_of(self.room, db))
        history = self.echo if playing else self.room
        floor = floor_of(history, db)
        turn_floor = max(floor, floor_of(self.room, floor))
        self.preroll.append(frame)
        self.gate.add(frame, db)
        if playing and time.monotonic() - self.play_started > BARGE_GRACE:
            self.echo_watch.add(max(self.gate.levels), self.playback.loudest())
        unknown = self.output.on_speakers() and self.playback.loudest() is None
        if playing and (self.talkover.notice_playing() or unknown):
            history.append(db)  # our own notice, or playback level unknown: only the wake word talks over it
            return self.woke(samples, turn_floor)
        speech = self.talking(frame, db, floor, playing)
        if playing or self.talkover.over:
            return self.talking_over(frame, speech, turn_floor, history, db)
        if not speech:
            history.append(db)
            return False
        self.turn(list(self.preroll), self.window.kind, turn_floor)
        return True

    def wary(self) -> bool:
        """A young canceller, or one that just let our echo through: it slips more."""
        return time.monotonic() - self.canceller_since < WARMUP or time.monotonic() < self.wary_until

    def talking_over(self, frame: np.ndarray, speech: bool, floor: float, history: collections.deque,
                     db: float) -> bool:
        """A voice over a clip (talkover.py decides); True when it stopped the clip and a turn ran."""
        if not self.talkover.over:
            history.append(db)
        how = self.last_check if speech else "a voice, quieter"
        playing = self.talkover.clip()
        verdict = self.talkover.feed(frame, speech or self.gate.still_speaking(), list(self.preroll), how,
                                     playing.text if playing else "")
        if verdict is Verdict.ECHO:
            self.echo_slipped("talking over was the clip's own words")
            return False
        if verdict is Verdict.WAITING:
            return False
        self.turn(self.talkover.taken.frames, Heard.OVER_YOU, floor)
        return True

    def talking(self, frame: np.ndarray, db: float, floor: float, playing: bool) -> bool:
        """Is the user speaking now (and, over a clip, not just our own echo)? voicegate.py decides."""
        wary = playing and self.wary()
        played = self.playback.loudest() if playing else None
        settled = not playing or time.monotonic() - self.play_started > BARGE_GRACE
        speech = self.gate.talking(frame, db, floor, playing, played, settled, wary)
        margin = self.gate.margin
        if not playing or margin is None or self.gate.checked:  # no check over a clip ran on this frame
            return speech
        near_miss = not speech and self.echo_watch.near_miss(margin)
        self.restart_due |= near_miss
        # In numbers (never audio), so a talk-over, or a false one, can be explained
        self.last_check = (f"mic {margin + played:.0f} dB, playing {played:.0f} dB, floor {floor:.0f} dB, "
                           f"{time.monotonic() - self.play_started:.1f}s in, {self.behind() * 1000:.0f} ms behind"
                           f"{', wary' if wary else ''}{', whisper on cpu' if self.transcriber.on_cpu else ''}"
                           f"{', near miss' if near_miss else ''}")
        return speech

    def woke(self, samples: np.ndarray, floor: float) -> bool:
        if not self.wake or PAUSE.exists() or ptt_busy() or not self.wake.feed(samples):
            return False
        if not self.transcriber.run(self.wake.confirm):
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
        extra = self.turns.take(frames, floor, heard)
        self.gate.clear()
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

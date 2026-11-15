"""One turn: record the user until they stop, transcribe it, and act on it.

The listener decides when a turn starts (a reply window, talking over a clip,
the wake word, an earbud tap); this records until a pause, "over" or the
limit, and then routes the words: to an agent, to a switch, or nowhere.
"""

from __future__ import annotations

import re
import threading
import time
import wave
from enum import Enum
from typing import Callable

import numpy as np
from faster_whisper.vad import get_speech_timestamps

import routing
from routing import Heard, Meaning
from sensing import PER_SECOND, RATE, SPEECH, level
from talkover import TalkOver, our_own_words
from voicegate import START_DB
from voice import whisper
from voice.state import (BY_HANDSFREE, CALL_PUSH, HANDSFREE_LOG, HANDSFREE_WAV, Phase, in_call, log, ptt_busy,
                         release_phase, set_phase, set_target)

THINK_WINDOW = 180  # seconds after "give me a minute"
WAIT_FOR_WORDS = 5.0  # after the wake word, seconds to start talking before it gives up
END_DB, END_FRAMES = 6, 50  # 1.5 s at most END_DB over the floor ends the turn
CALL_END_FRAMES = 30  # 0.9 s on a phone call: the user found 1.5 s slow there; a longer pause becomes a follow-on
BELOW_PEAK = 25  # dB under their loudest also counts as quiet, for a noisy room
OVER_FRAMES = 20  # after 0.6 s of quiet, check whether the last word was "over"
# On a call the line can carry steady hiss (Henchline's clear sound has no call-mode noise suppression) that never
# gets quiet enough to end a turn: "Hearing you…" for 55 s (2026-11-15). There a second without speech (Silero),
# looked at every 0.3 s, ends the turn as well
VOICE_EVERY, VOICE_GONE = 10, 1.0
OVER_TAIL = 4 * PER_SECOND  # frames Whisper looks at for "over"
PTT_CHECK = 10  # frames between checks that push-to-talk has not taken the mic
MAX_SECONDS = 120  # the user talks in long stretches; 60 s cut them off
WAKE_PREFIX = re.compile(r"^\W*((hey|hi|okay|ok)\W+)?claude\W*", re.I)
OVER = re.compile(r"[\s,.!?]*\bover\b[\s.!?]*$", re.I)
TONE_NOTE = re.compile(r"\s*\[tone: [^\]]*\]$")


class Mode(str, Enum):
    """What the tray shows."""

    WAITING = "waiting"  # only the wake word gets in
    LISTENING = "listening"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


def only_hint_words(text: str, hotwords: list[str]) -> bool:
    """Whisper given noise writes out the names it was told to expect ("Cursor, Codex, Kokoro",
    2026-11-15): a transcript of nothing but those is not the user talking."""
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and set(words) <= set(re.findall(r"[a-z']+", " ".join(hotwords).lower()))


class Turn:
    def __init__(self, cfg: whisper.Config, transcriber: whisper.Transcriber, talkover: TalkOver,
                 read: Callable[[], np.ndarray | None], show: Callable[[Mode], None],
                 echo_slipped: Callable[[str], None], stop: threading.Event) -> None:
        self.cfg, self.transcriber, self.talkover = cfg, transcriber, talkover
        self.read, self.show, self.echo_slipped, self.stop = read, show, echo_slipped, stop
        self.last_message: tuple[str, Heard] | None = None  # for "send that to …"

    def take(self, frames: list[np.ndarray], floor: float, heard: Heard) -> float | None:
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
            if only_hint_words(text, self.cfg.hotwords):
                log(HANDSFREE_LOG, "only Whisper's hint words: a noise, nothing sent")
                set_target(False)
                return None
            taken = self.talkover.taken
            if heard is Heard.OVER_YOU and taken and our_own_words(text, taken.clip_text):
                self.echo_slipped(f"what stopped the clip was its own words ({len(text.split())}): "
                                  "our echo; the clip plays on, nothing sent")
                set_target(False)
                return None
            return self.act(text, rich, heard)
        finally:
            release_phase(BY_HANDSFREE)  # act() normally has already, before anything is sent

    def record(self, frames: list[np.ndarray], floor: float, heard: Heard) -> list[np.ndarray] | None:
        """Frames until a pause, "over", or the limit; None if push-to-talk took over."""
        set_phase(Phase.RECORDING, BY_HANDSFREE)  # talkback goes quiet and holds
        self.show(Mode.RECORDING)
        since, quiet, peak = time.monotonic(), 0, -120.0
        started = heard is not Heard.WOKE  # after "hey Claude" people pause for the chime
        calling = in_call()
        end = CALL_END_FRAMES if calling else END_FRAMES
        voice_at, spoke = since, False  # when Silero last heard speech in the turn, and whether it ever did (calls)
        voiceless = False
        held = heard is Heard.PUSHED  # the button decides when it ends, not a pause
        released = False
        while (quiet < end or held) and time.monotonic() - since < MAX_SECONDS:
            if held and not CALL_PUSH.exists():
                released = True
                break
            samples = self.read()
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
            if calling and not held and len(frames) % VOICE_EVERY == 0:
                recent = np.concatenate(frames[-int(VOICE_GONE * PER_SECOND):])
                if get_speech_timestamps(recent, SPEECH):
                    voice_at, spoke = time.monotonic(), True
                elif time.monotonic() - voice_at > (VOICE_GONE if spoke else WAIT_FOR_WORDS):
                    voiceless = True  # their words ended a second ago, or none came at all
                    break
        reason = ("the button" if released else "limit" if time.monotonic() - since >= MAX_SECONDS
                  else "no voice for a second (steady sound on the line)" if voiceless
                  else "over" if quiet == OVER_FRAMES else "pause")
        log(HANDSFREE_LOG, f"recorded {time.monotonic() - since:.1f}s, ended by {reason} (floor {floor:.0f} dB)")
        return frames

    def transcribe(self, frames: list[np.ndarray]) -> tuple[str, str]:
        """(plain text, text with tone marks); empty when there was no speech."""
        set_phase(Phase.TRANSCRIBING, BY_HANDSFREE)
        self.show(Mode.TRANSCRIBING)
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
            return self.transcriber.file(HANDSFREE_WAV)
        except Exception as exc:
            log(HANDSFREE_LOG, f"transcribe failed: {exc}")
            return "", ""
        finally:
            HANDSFREE_WAV.unlink(missing_ok=True)

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
                self.echo_slipped("echo cut a clip")
            return None
        elif routing.deliver(rich, heard):
            whisper.ding(self.cfg)
            self.last_message = (rich, heard)
        return THINK_WINDOW if routing.THINK.search(text) else 0.0

    def ends_with_over(self, frames: list[np.ndarray]) -> bool:
        """Quick look at the last few seconds: did they finish with "over"?"""
        return bool(OVER.search(self.transcriber.quick_text(np.concatenate(frames[-OVER_TAIL:])).strip()))

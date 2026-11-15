"""Talking over a clip: when the user's voice should stop it, and when it is only our own echo.

A voice over a clip has to add up to SUSTAIN seconds first; then Whisper
writes out what was heard, and if that is mostly the clip's own words, it was
our echo and the clip plays on. Loudness rules alone could not tell them apart:
the canceller sometimes lets our voice through for two or three seconds.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from sensing import FRAME, RATE
from voice.state import BY_HANDSFREE, HANDSFREE_LOG, PLAYING as PLAYING_PATH, Playing, log

CHECK_EVERY = 3  # frames between voice checks (90 ms)
STEP = CHECK_EVERY * FRAME / RATE  # seconds of voice one positive check stands for
SUSTAIN = 0.6  # seconds of voice, added up, before the words are looked at
GAP = 1.5  # seconds without a voice that end an attempt: the user spoke in 1.5 s phrases with pauses
UNSURE_LIMIT = 2.0  # seconds of voice after which the clip stops even if the words stayed too few
RECHECK = 0.3  # more seconds of voice before looking at the words again
OWN_WORDS_MIN = 2  # words heard before telling our echo from the user
OWN_WORDS = 0.6  # share of what was heard that is the clip's own words, in order: our echo
OWN_RUN, OWN_SHARE = 3, 0.3  # or at least this many of the clip's words, in order, making up this share
ONSET_QUIET = 1.0  # seconds of silent playback before our voice counts as starting again (for the log)
AUDIBLE_DB = -45  # playback louder than this is our voice
NUMBERS = dict(enumerate("zero one two three four five six seven eight nine ten eleven twelve".split()))


def word_keys(text: str) -> list[str]:
    """Words as Whisper and talkback may spell them differently: "5" and "five", "recognize" and
    "recognised" (2026-11-15) match on their number word and first four letters."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    words = [NUMBERS.get(int(w), w) if w.isdigit() else w for w in words]
    return [w[:4] for w in words]


def our_own_words(heard: str, said: str) -> bool:
    """Is what was heard over a clip mostly that clip's own words, in order? Then it was our echo.

    The user talking over us says something else. A single word is too little to tell.
    """
    heard_words, said_words = word_keys(heard), word_keys(said)
    if len(heard_words) < OWN_WORDS_MIN or not said_words:
        return False
    row = [0] * (len(said_words) + 1)  # longest common subsequence of words
    for word in heard_words:
        prev = 0
        for j, other in enumerate(said_words, 1):
            prev, row[j] = row[j], prev + 1 if word == other else max(row[j], row[j - 1])
    shared = row[-1]
    # Whisper adds words of its own to echo ("That was a pretty good question, slowly, while nobody wa-":
    # all ours, 2026-11-15), so a run of our words counts even among others
    return shared / len(heard_words) >= OWN_WORDS or (shared >= OWN_RUN and shared / len(heard_words) >= OWN_SHARE)


@dataclass
class Overlap:
    """A voice over a clip, kept while it lasts, every frame since it began."""

    started: float
    frames: list[np.ndarray]
    first: str  # how it began, for the log
    clip_text: str  # what we were saying
    last: float = 0.0  # last time it still sounded like a voice
    voiced: float = STEP  # seconds that sounded like a voice, added up
    next_check: float = 0.0  # seconds of voice before the next look at its words (after SUSTAIN)

    def speaking_for(self) -> float:
        return self.last - self.started


class Verdict(str, Enum):
    WAITING = "waiting"  # not enough voice yet, or none at all
    ECHO = "echo"  # our own words: the clip plays on
    USER = "user"  # stop the clip: the user is talking


@dataclass
class TalkOver:
    """The one attempt at talking over a clip in progress, and when our voice last started."""

    quick_text: Callable[[np.ndarray], str]  # a fast transcript of a little audio
    over: Overlap | None = None
    playing: Playing | None = None  # the clip now playing, read once per clip
    taken: Overlap | None = None  # the last attempt that stopped a clip: its frames and the clip's words
    onset: float = 0.0  # when our voice last became audible after a silence
    silent_since: float = 0.0
    read_at: float = 0.0  # when the playing marker was last read
    clock: Callable[[], float] = time.monotonic  # replaced by the recording's own time in echo_replay.py
    sustain: float = SUSTAIN
    gap: float = GAP
    log_to: Path = HANDSFREE_LOG

    def track_onset(self, played_db: float | None) -> None:
        now = self.clock()
        if played_db is None or played_db < AUDIBLE_DB:
            self.silent_since = self.silent_since or now
        elif self.silent_since:
            if now - self.silent_since >= ONSET_QUIET:
                self.onset = now
            self.silent_since = 0.0

    def reset(self) -> None:
        self.over = None

    def clip(self) -> Playing | None:
        """The clip playing now; the marker is read again only when talkback starts another."""
        try:
            changed = PLAYING_PATH.stat().st_mtime
        except OSError:  # no clip, or talkback removed the marker just now
            self.playing = None
            return None
        if self.playing is None or changed > self.read_at:
            self.playing, self.read_at = Playing.now(), time.time()
        return self.playing

    def notice_playing(self) -> bool:
        """Hands-free's own short notices: their echo was taken for the user ("Now talking to Claude in
        peace", 2026-11-15), and nobody needs to talk over two seconds of "Now talking to …"."""
        playing = self.clip()
        return bool(playing) and playing.agent == BY_HANDSFREE

    def feed(self, frame: np.ndarray, voice: bool, preroll: list[np.ndarray], how: str, clip_text: str) -> Verdict:
        """One frame while a clip plays (or just after); voice: does it sound like someone talking;
        clip_text: what we are saying, to tell our echo by."""
        now = self.clock()
        if self.over is None:
            if voice:
                self.over = Overlap(now, list(preroll), f"{how}, {now - self.onset:.1f}s after our voice started",
                                    clip_text, last=now)
            return Verdict.WAITING
        over = self.over
        over.frames.append(frame)
        if voice:
            over.last = now
            over.voiced += STEP
        if now - over.last > self.gap:
            log(self.log_to, f"talking over: {over.voiced:.1f}s of voice in {over.speaking_for():.1f}s, "
                               f"then quiet: kept playing (first: {over.first})")
            self.over = None
            return Verdict.WAITING
        if over.voiced < max(self.sustain, over.next_check):
            return Verdict.WAITING
        echo = self.echo_of_clip(over)
        if echo is None and over.voiced < UNSURE_LIMIT:
            over.next_check = over.voiced + RECHECK  # too few words to tell yet: listen a little longer
            return Verdict.WAITING
        self.over = None
        if not echo:
            self.taken = over
        log(self.log_to, f"talking over: {over.voiced:.1f}s of voice in {over.speaking_for():.1f}s: "
                           + ("the clip's own words, our echo: kept playing" if echo else "stopping the clip")
                           + f" (first: {over.first})")
        return Verdict.ECHO if echo else Verdict.USER

    def echo_of_clip(self, over: Overlap) -> bool | None:
        """Are the words heard over the clip its own? None when there are too few words to tell.

        Checking only after stopping let the resumed clip's echo cut it again,
        over and over; deciding on one word did the same (2026-11-15).
        """
        if not over.clip_text:
            return False
        text = self.quick_text(np.concatenate(over.frames))
        if len(word_keys(text)) < OWN_WORDS_MIN:
            return None
        return our_own_words(text, over.clip_text)

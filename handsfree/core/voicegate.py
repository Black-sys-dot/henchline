"""Does this frame sound like the user talking? The cheap checks, frame by frame.

Loud and voiced frames over the floor, then Silero on the last second; over
a clip also near the playback's loudness (our echo is far quieter). The
listener runs it live and echo_replay.py runs it on recordings: the same code.
"""

from __future__ import annotations

import collections

import numpy as np
from faster_whisper.vad import get_speech_timestamps

from sensing import LEVEL_SPAN, PER_SECOND, SPEECH, SPEECH_OVER, voiced
from talkover import CHECK_EVERY

START_DB, BARGE_DB, WARM_DB = 12, 15, 10  # dB over the floor to count; while a clip plays; extra when wary
LOUD_SPAN = 12  # frames looked at for loud, voiced ones
START_FRAMES, BARGE_FRAMES = 6, 8  # of the last LOUD_SPAN, voiced and loud enough to count; while a clip plays
GOING_FRAMES = 4  # of the last LOUD_SPAN, loud and voiced, to count as still talking over (starting needs BARGE_FRAMES)
ECHO_MARGIN = -15.0  # talking over must be no more than this many dB under the playback (echo is 20+ under)


class VoiceGate:
    def __init__(self) -> None:
        self.loud: collections.deque[bool] = collections.deque(maxlen=LOUD_SPAN)
        self.recent: collections.deque[np.ndarray] = collections.deque(maxlen=PER_SECOND)  # for Silero
        self.levels: collections.deque[float] = collections.deque(maxlen=LEVEL_SPAN)  # against the playback
        self.checked = 0
        self.margin: float | None = None  # at the last check over a clip: mic minus playback, dB

    def add(self, frame: np.ndarray, db: float) -> None:
        self.recent.append(frame)
        self.levels.append(db)
        self.checked += 1

    def clear(self) -> None:
        self.loud.clear()

    def talking(self, frame: np.ndarray, db: float, floor: float, playing: bool, played: float | None,
                settled: bool, wary: bool) -> bool:
        """Is the user speaking (and, over a clip, near enough the playback's loudness not to be our echo)?"""
        over = db > floor + (BARGE_DB + WARM_DB if wary else BARGE_DB if playing else START_DB)
        self.loud.append(over and settled and voiced(frame))
        if sum(self.loud) < (BARGE_FRAMES if playing else START_FRAMES) or self.checked < CHECK_EVERY:
            return False
        self.checked, self.margin = 0, None
        if not get_speech_timestamps(np.concatenate(self.recent), SPEECH_OVER if playing else SPEECH):
            return False
        if not playing:
            return True
        self.margin = max(self.levels) - played if played is not None else None
        return self.margin is None or self.margin >= ECHO_MARGIN

    def still_speaking(self) -> bool:
        """A voice, near the playback's loudness or not, starts and keeps up talking over.

        Asking that it be nearly as loud as our playback lost a voice from the
        hallway between its words (2026-11-15). Our echo passes this too: the
        word check in talkover.py tells them apart.
        """
        if self.checked % CHECK_EVERY or sum(self.loud) < GOING_FRAMES:
            return False
        return bool(get_speech_timestamps(np.concatenate(self.recent), SPEECH_OVER))

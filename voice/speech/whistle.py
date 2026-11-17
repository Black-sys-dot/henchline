"""Whistle: a 17 MB speech model fast on the CPU, for the quick checks: the wake word, and "over".

When the laptop runs on battery, Whisper is on the CPU, and confirming "hey Claude" took about 2.5 s there: the
user felt it as a slow wake (2026-11-16). Cactus Compute's Whistle (Apache-2.0) does the same two seconds in about
0.15 s on the CPU. On the user's own takes it found every wake word with the hint words, and in 1,260 two-second
clips without one it heard "Claude" as often as Whisper did (5) and "Jarvis" never (Whisper, hinted: 84). It is not
used to transcribe turns: on whole sentences it makes more than twice Whisper's errors.

The weights live in HANDSFREE_DATA/whistle (`install.sh handsfree` fetches them); without them, Whisper confirms.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from voice.state import HANDSFREE_DATA, log

WEIGHTS = HANDSFREE_DATA / "whistle/whistle.cact"
THREADS = 4


def available() -> bool:
    return WEIGHTS.exists()


class Checker:
    """The model, loaded once: a little audio in, its words out."""

    def __init__(self, log_to: Path) -> None:
        os.environ["CACTUS_TELEMETRY"] = "0"  # its usage counts stay off, whatever the environment says
        os.environ["DO_NOT_TRACK"] = "1"  # the same, by the name it also honours
        import cactus

        cactus.set_threads(THREADS)
        self.model = cactus.Whistle(weights=str(WEIGHTS))
        self.lock = threading.Lock()
        log(log_to, f"whistle on the cpu for wake words and quick checks ({THREADS} threads)")

    def hear(self, audio, hints: str | None) -> str:
        """16 kHz floats → words; hints: words to expect, space-separated, or None."""
        with self.lock:
            return str(self.model.transcribe(audio, language="en", keywords=hints.split() if hints else None)["text"])

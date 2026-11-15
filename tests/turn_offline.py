#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Take one real hands-free turn offline: a recording of the user's voice as the mic, delivery stubbed.

    tests/turn_offline.py [WAV]   (default: the "talk normally" wake-word sample)

Runs Listener.turn (record, transcribe, act) on the recording and checks it
was delivered as a reply, without typing anywhere; exits 1 if not. A refactor
that breaks a turn shows here before the user finds it. It still uses the live
state files (phase, the reply window, the hands-free log) and plays the chime,
so run it while hands-free is quiet.
"""
import glob
import io
import sys
import threading
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "handsfree"), str(REPO)]
import numpy as np  # noqa: E402

import listener  # noqa: E402
import routing  # noqa: E402
from routing import Heard  # noqa: E402
from echo_replay import NO_LOG  # noqa: E402
from voice.speech import whisper  # noqa: E402

from voice.state import WAKE_SAMPLES  # noqa: E402

sample = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(str(WAKE_SAMPLES / "negative-*.wav")))[0]
with wave.open(sample) as w:
    audio = w.readframes(w.getnframes())
samples = np.frombuffer(audio, "<i2")
loud = np.flatnonzero(np.abs(samples) > 1500)
audio = samples[max(0, loud[0] - 4800):].tobytes()  # from just before the first word
silence = b"\0" * (16000 * 2 * 3)  # 3 s of quiet after, so the turn ends by a pause

class FakeStream:
    """A recording standing in for the mic stream (parec)."""

    def __init__(self, data: bytes) -> None:
        self.stdout = io.BytesIO(data)

    def terminate(self) -> None:
        pass

sent = []
routing.deliver = lambda text, heard: sent.append((text, heard)) or True
cfg = whisper.Config.load(NO_LOG)
listener_ = listener.Listener(cfg, whisper.Transcriber(cfg, NO_LOG), threading.Event(), canceller_since=0.0)
listener_.stream = FakeStream(audio + silence)
listener_.turn([], Heard.REPLY, -80.0)
print("delivered:", sent)
print("window after the turn:", listener_.window.kind, round(listener_.window.until - time.monotonic(), 1), "s")
ok = bool(sent) and sent[0][1] is Heard.REPLY and listener_.window.kind is Heard.FOLLOW_ON
print("turn ok" if ok else "TURN FAILED")
sys.exit(0 if ok else 1)

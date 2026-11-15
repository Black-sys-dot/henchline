#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Take one real hands-free turn offline: a recording of the user's voice as the mic, delivery stubbed.

    tests/turn_offline.py [WAV]   (default: the "talk normally" wake-word sample)

Runs Listener.turn (record, transcribe, act) on the recording and prints what
would have been delivered, without typing anywhere. A refactor that breaks a
turn shows here before the user finds it.
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
from voice import whisper  # noqa: E402

from voice.state import WAKE_SAMPLES  # noqa: E402

sample = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(str(WAKE_SAMPLES / "negative-*.wav")))[0]
with wave.open(sample) as w:
    audio = w.readframes(w.getnframes())
samples = np.frombuffer(audio, "<i2")
loud = np.flatnonzero(np.abs(samples) > 1500)
audio = samples[max(0, loud[0] - 4800):].tobytes()  # from just before the first word
silence = b"\0" * (16000 * 2 * 3)  # 3 s of quiet after, so the turn ends by a pause

class FakeStream:
    def __init__(self, data): self.stdout = io.BytesIO(data)
    def terminate(self): pass

sent = []
routing.deliver = lambda text, heard: sent.append((text, heard)) or True
cfg = whisper.Config.load(Path("/dev/null"))
model = whisper.load_model(cfg, cfg.want_cuda(), Path("/dev/null"))
l = listener.Listener(cfg, model, threading.Event(), canceller_since=0.0)
l.stream = FakeStream(audio + silence)
l.turn([], Heard.REPLY, -80.0)
print("delivered:", sent)
print("window after the turn:", l.window.kind, round(l.window.until - time.monotonic(), 1), "s")

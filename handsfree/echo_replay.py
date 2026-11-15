#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Replay an echo_collect.py session through the live talk-over rule (listener.py's thresholds), offline.

    echo_replay.py SESSION [SUSTAIN] [GAP] [GOING_FRAMES]

Tune thresholds here, against recordings, before the user has to fight them live.

Per take: whether the clip would have been stopped, when (relative to the
chime), and whether that was false (no chime yet, or a quiet take).
"""
import collections
import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]  # listener and sensing; voice/
import listener as L  # noqa: E402
from sensing import FRAME, RATE, level, voiced  # noqa: E402
from faster_whisper.vad import get_speech_timestamps  # noqa: E402

folder = Path(sys.argv[1])
SUSTAIN = float(sys.argv[2]) if len(sys.argv) > 2 else L.SUSTAIN
GAP = float(sys.argv[3]) if len(sys.argv) > 3 else L.GAP
GOING = int(sys.argv[4]) if len(sys.argv) > 4 else L.GOING_FRAMES
STEP = L.CHECK_EVERY * FRAME / RATE
takes = json.loads((folder / "trials.json").read_text())


def load(name):
    with wave.open(str(folder / name)) as w:
        return (np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768).reshape(-1, w.getnchannels())[:, 0]


def replay(t):
    ec, play = load(f"{t['take']}-ec.wav"), load(f"{t['take']}-play.wav")
    n = min(len(ec), len(play)) // FRAME
    c0, c1 = t["clip_start"], t["clip_end"]
    echo_hist = collections.deque(maxlen=L.FLOOR_SPAN)
    loud = collections.deque(maxlen=L.LOUD_SPAN)
    recent = collections.deque(maxlen=L.PER_SECOND)
    levels = collections.deque(maxlen=L.LEVEL_SPAN)
    plays = collections.deque(maxlen=L.LEVEL_SPAN)
    checked, over = 0, None
    for i in range(n):
        ts = i * FRAME / RATE
        frame = ec[i * FRAME:(i + 1) * FRAME]
        db = level(frame)
        plays.append(level(play[i * FRAME:(i + 1) * FRAME]))
        if not (c0 + L.BARGE_GRACE < ts < c1):
            continue
        floor = L.floor_of(echo_hist, db)
        recent.append(frame); levels.append(db); checked += 1
        loud.append(db > floor + L.BARGE_DB and voiced(frame))
        speech = False
        if sum(loud) >= L.BARGE_FRAMES and checked >= L.CHECK_EVERY:
            checked = 0
            if get_speech_timestamps(np.concatenate(recent), L.SPEECH_OVER):
                played = max(plays)
                speech = max(levels) - played >= L.ECHO_MARGIN
        going = (not checked % L.CHECK_EVERY and sum(loud) >= GOING
                 and bool(get_speech_timestamps(np.concatenate(recent), L.SPEECH_OVER)))
        if over is None:
            echo_hist.append(db)
            if speech or going:
                over = [ts, ts, STEP]
            continue
        if speech or going:
            over[1] = ts; over[2] += STEP
        if ts - over[1] > GAP:
            over = None
        elif over[2] >= SUSTAIN:
            return ts
    return None


false = miss = 0
lags = []
for t in takes:
    hit = replay(t)
    cue = t["cue"]
    if hit is not None and (cue is None or hit < cue):
        false += 1; verdict = f"FALSE at {hit:.1f}s"
    elif cue is not None and hit is None:
        miss += 1; verdict = "missed"
    elif hit is not None:
        lags.append(round(hit - cue, 2)); verdict = f"stopped {hit - cue:.2f}s after the chime"
    else:
        verdict = "kept playing (right)"
    print(f"  {t['take']:18s} {verdict}")
print(f"SUSTAIN {SUSTAIN} GAP {GAP} GOING {GOING}: false {false}, missed {miss}, delays {lags}")

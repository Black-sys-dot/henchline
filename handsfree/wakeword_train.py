#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Train a personal "hey Claude" detector from wakeword_collect.py recordings.

The published openWakeWord model missed most of the user's own "hey Claude"s,
so this fits a small classifier on openWakeWord's speech embeddings instead
(the same 16 x 96 features its models use, 1.28 s of audio):
- positives: each "hey Claude" in the samples, found with Whisper word
  timings, at a few offsets and gains;
- negatives: everything else in the samples, the user's normal-speech take,
  and Kokoro voices saying ordinary sentences and near misses.
It reports leave-one-position-out recall (train on three spots, test on the
fourth) and the false-wake rate on held-out speech, then saves the model to
~/.local/share/handsfree/hey_claude_personal.pkl.
"""

from __future__ import annotations

import glob
import pickle
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from openwakeword.utils import AudioFeatures
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA = Path.home() / ".local/share/handsfree"
SAMPLES = DATA / "samples"
OUT = DATA / "hey_claude_personal.pkl"
RATE = 16000
WIN = 2 * RATE  # 2 s of audio gives the 16 embedding frames a detector sees
KOKORO = Path.home() / ".local/share/talkback"
NEGATIVE_LINES = [
    "Okay, the build is done, and all the tests passed.",
    "Can you check the logs and tell me what happened?",
    "I'm making dinner, so give me a minute.",
    "Hey, could you look at the cloud settings?",
    "Hey Clyde, how was the game?",
    "The weather is lovely today, let's go outside.",
    "Claire said the meeting moved to Thursday.",
    "Hey, cool, that actually worked on the first try.",
    "Okay cloud, play some music.",
    "He clawed at the door until somebody let him in.",
]


def load(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)


def window(audio: np.ndarray, end: int) -> np.ndarray:
    """2 s ending at sample `end`, zero-padded at the front."""
    start = end - WIN
    clip = audio[max(0, start):end]
    return np.concatenate([np.zeros(WIN - len(clip), np.float32), clip])


def hey_claude_ends(model, path: str) -> list[int]:
    """Sample index where each "Claude" (after "hey") ends."""
    segments, _ = model.transcribe(path, language="en", beam_size=1, word_timestamps=True)
    words = [w for s in segments for w in s.words]
    ends = []
    for prev, word in zip(words, words[1:]):
        if re.sub(r"\W", "", word.word).lower() == "claude" and re.sub(r"\W", "", prev.word).lower() == "hey":
            ends.append(int(word.end * RATE))
    return ends


def kokoro_negatives() -> list[np.ndarray]:
    """Ordinary sentences and near misses in a few voices, at 16 kHz."""
    code = (
        "import sys, numpy as np, onnxruntime as rt; from kokoro_onnx import Kokoro\n"
        f"k = Kokoro('{KOKORO}/kokoro-v1.0.onnx', '{KOKORO}/voices-v1.0.bin')\n"
        "out = []\n"
        f"for line in {NEGATIVE_LINES!r}:\n"
        "    for v in ['am_michael', 'af_sarah', 'bm_george', 'af_heart']:\n"
        "        s, r = k.create(line, voice=v, lang='en-us')\n"
        "        s = np.interp(np.arange(0, len(s) * 16000 / r) * r / 16000, np.arange(len(s)), s)\n"
        "        out.append((s * 32767 * 0.3).astype(np.float32))\n"
        "np.save(sys.argv[1], np.array(out, dtype=object), allow_pickle=True)\n"
    )
    target = DATA / "kokoro_negatives.npy"
    subprocess.run([sys.executable, "-E", "-s", "-c", code, str(target)], check=True)
    return list(np.load(target, allow_pickle=True))


def main() -> int:
    from faster_whisper import WhisperModel

    whisper = WhisperModel("small", device="cpu", compute_type="int8")
    features = AudioFeatures(inference_framework="onnx")
    rng = np.random.default_rng(0)

    def embed(clips: list[np.ndarray]) -> np.ndarray:
        x = features.embed_clips(np.stack(clips).astype(np.int16), batch_size=64)
        return x.reshape(len(clips), -1)

    pos, neg = {}, {}
    for path in sorted(glob.glob(str(SAMPLES / "*.wav"))):
        spot = Path(path).name.rsplit("-", 1)[0]
        audio = load(path)
        audio = np.concatenate([np.zeros(RATE, np.float32), audio, np.zeros(RATE // 2, np.float32)])
        ends = [e + RATE for e in hey_claude_ends(whisper, path)] if spot != "negative" else []
        for end in ends:
            for shift in (-1600, 0, 1600, 3200, 4800):  # -0.1 .. +0.3 s after "Claude"
                for gain in (0.7, 1.0, 1.6):
                    pos.setdefault(spot, []).append(window(audio, end + shift) * gain)
        for end in range(WIN, len(audio), RATE // 4):  # every 0.25 s, away from any wake word
            if all(abs(end - e) > RATE for e in ends):
                neg.setdefault(spot, []).append(window(audio, end))
        print(f"{Path(path).name}: {len(ends)} hey Claude", flush=True)
    synthetic = []
    for audio in kokoro_negatives():
        audio = np.concatenate([np.zeros(RATE, np.float32), audio])
        synthetic += [window(audio, end) for end in range(WIN, len(audio), RATE // 4)]
    print(f"positives {sum(map(len, pos.values()))}, negatives {sum(map(len, neg.values()))} + {len(synthetic)} synthetic")

    X = {s: embed(c) for s, c in pos.items()}
    N = {s: embed(c) for s, c in neg.items()}
    S = embed(synthetic)

    def fit(pos_sets, neg_sets):
        Xp = np.concatenate(pos_sets)
        Xn = np.concatenate(neg_sets)
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=2000, class_weight="balanced"))
        clf.fit(np.concatenate([Xp, Xn]), np.r_[np.ones(len(Xp)), np.zeros(len(Xn))])
        return clf

    print("leave one spot out (centre offset only counts as a hit):")
    half = len(S) // 2
    for spot in X:
        clf = fit([X[s] for s in X if s != spot], [N[s] for s in N if s != spot] + [S[:half]])
        centre = X[spot][4::15] if len(X[spot]) >= 15 else X[spot]  # shift 0, gain 1.0 per utterance
        hits = (clf.predict_proba(centre)[:, 1] > 0.5).mean()
        false = (clf.predict_proba(np.concatenate([N.get("negative", S[:0]), S[half:]]))[:, 1] > 0.5).mean()
        print(f"  {spot:14s} recall {hits:.0%}  false wakes on held-out speech {false:.1%}")
    clf = fit(list(X.values()), list(N.values()) + [S])
    OUT.write_bytes(pickle.dumps(clf))
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The ring a banner plays (the phone in use): the agent's ringtone with its phrases on the beat, in one file.

    python3 call/android/banner_mix.py      (after ringtones.py or henchify_takes.py; numpy only)

A banner's sound is a single file its notification channel plays over and over, so the phrases the
full-screen ring speaks over the tone (IncomingActivity.sayAgain) are mixed in here the same way: each
phrase starts on a half-loop beat of the tone, the tone a little quieter under it. Writes
app/src/main/res/raw/banner_<agent>.wav from the default phrases, and, when your own phrases are in
app/src/debug/res/raw/ (henchify_takes.py), banner_<agent>.wav there too, git-ignored as they are.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MAIN, DEBUG = HERE / "app/src/main/res/raw", HERE / "app/src/debug/res/raw"
AGENTS = ("claude", "cursor", "codex", "handsfree")
TONE_VOLUME = 0.45  # as IncomingActivity plays it


def read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as f:
        return np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float32) / 32768, f.getframerate()


def at_rate(audio: np.ndarray, rate: int, to: int) -> np.ndarray:
    if rate == to:
        return audio
    return np.interp(np.arange(int(len(audio) * to / rate)) * rate / to, np.arange(len(audio)), audio).astype(np.float32)


def mix(ring_path: Path, phrase_paths: list[Path], out: Path) -> None:
    phrases = [read(p) for p in phrase_paths]
    rate = max(r for _, r in phrases)
    tone = at_rate(*read(ring_path), rate) * TONE_VOLUME
    beat = len(tone) // 2
    voice = []
    for audio, r in phrases:  # each phrase, then silence to the next beat after it
        audio = at_rate(audio, r, rate)
        span = beat * max(1, -(-len(audio) // beat))
        voice.append(np.pad(audio, (0, span - len(audio))))
    voice = np.concatenate(voice)
    length = len(tone) * -(-len(voice) // len(tone))  # whole loops of the tone, so the file loops cleanly
    track = np.resize(tone, length) + np.pad(voice, (0, length - len(voice)))
    track /= max(1.0, float(np.abs(track).max()) / 0.95)
    with wave.open(str(out), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((track * 32767).astype("<i2").tobytes())


def main() -> int:
    for folder in (MAIN, DEBUG):
        for agent in AGENTS:
            phrases = sorted(folder.glob(f"phrase_{agent}_*.wav"))
            if phrases and (MAIN / f"ring_{agent}.wav").exists():
                mix(MAIN / f"ring_{agent}.wav", phrases, folder / f"banner_{agent}.wav")  # both: for listening to
                for n, phrase in enumerate(phrases):  # one each: a ring says the one its kind calls for
                    mix(MAIN / f"ring_{agent}.wav", [phrase], folder / f"banner_{agent}_{n}.wav")
                print(folder.parent.parent.name, agent, "ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

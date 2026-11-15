#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/henchline/venv/bin/python3" -E -s "$0" "$@"'
"""Henchline's ringing: a ringtone per agent, and its henchman-speak phrases in the agent's talkback voice.

    call/android/ringtones.py

Writes app/src/main/res/raw/ring_<agent>.wav (synthesised here, a few seconds that loop) and
phrase_<agent>_<n>.wav (Kokoro, the voices talkback gives each agent), and phrases.json for the
banner under the henchman. The henchman-speak is this project's own nonsense, not anyone's film lines.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RAW = HERE / "app/src/main/res/raw"
RATE = 24000
HENCH_SPEED = 1.5  # how much quicker (and so higher: about seven semitones) a henchman says it
VOICES = {"claude": "af_heart", "cursor": "af_bella", "codex": "bf_alice", "handsfree": "bm_george"}  # talkback's
PHRASES = {  # Minion-style: a bouncy jumble of Spanish, Italian, French and English (no giggles: never performed)
    "claude": ["Boss-a! Ooh la la, pikka pikka! Para tu, mucho importanto!",
               "Kanpai, boss-a! Tasko finito, si si si!"],
    "cursor": ["Ay ay ay, boss-o! Clicka clicka, me so ready-o! Tank yu!",
               "Ohh, boss-o! Codey codey, la bella codey!"],
    "codex": ["Wahoo! Boss-ah, jobba donezo, para tu!",
              "Mira mira, boss-ah! Tutti complito, si! Wahoo!"],
    "handsfree": ["Psst psst, boss-a! La crew-a, calla calla!",
                  "Ooh! Boss-a, ringa ringa! Pikka upa, si?"],
}


def tone(freq: float, seconds: float, shape: str = "sine", decay: float = 4.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    if shape == "square":
        wave_ = np.sign(np.sin(2 * np.pi * freq * t)) * 0.5
    elif shape == "marimba":  # a sine and its quieter fourth harmonic, as a struck bar
        wave_ = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 4 * t) * np.exp(-t * 18)
    else:
        wave_ = np.sin(2 * np.pi * freq * t)
    attack = np.minimum(1, t / 0.005)
    return wave_ * attack * np.exp(-t * decay)


def sweep(f0: float, f1: float, seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    freq = f0 + (f1 - f0) * t / seconds
    return np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.sin(np.pi * t / seconds)


def gap(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds))


def note(name: str) -> float:
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    return 440 * 2 ** ((names[name[0]] + 12 * (int(name[-1]) - 4)) / 12)


def ringtone(agent: str) -> np.ndarray:
    if agent == "claude":  # warm: a marimba climbing a major seventh, twice
        bar = [tone(note(n), 0.18, "marimba", 6) for n in ("C5", "E5", "G5", "B5")]
        phrase = np.concatenate(bar + [tone(note("C6"), 0.5, "marimba", 4)])
        return np.concatenate([phrase, gap(0.25), phrase, gap(1.2)])
    if agent == "cursor":  # crisp: digital blips, a cursor blinking
        blip = lambda n: np.concatenate([tone(note(n), 0.07, "square", 20), gap(0.05)])  # noqa: E731
        burst = np.concatenate([blip(n) for n in ("A5", "E6", "A5", "E6", "B6")])
        return np.concatenate([burst, gap(0.3), burst, gap(1.3)])
    if agent == "codex":  # bubbly: rising chirps
        chirps = np.concatenate([np.concatenate([sweep(f, f * 1.8, 0.14), gap(0.08)]) for f in (500, 650, 820, 1000)])
        return np.concatenate([chirps, gap(0.3), chirps, gap(1.2)])
    ring = np.concatenate([tone(f, 0.05, "sine", 0) for f in (480, 620) * 10])  # a phone's double trill
    return np.concatenate([ring, gap(0.2), ring, gap(1.6)])


def henchify(samples: np.ndarray, rate: int) -> np.ndarray:
    """The agent's voice as its henchman's: higher and faster together (played back quicker, as the films do
    with a real voice), with a quick wobble for the bounce. A plain voice saying the words sounded like a
    person reading them (2026-11-15)."""
    sped = np.interp(np.arange(0, len(samples) - 1, HENCH_SPEED), np.arange(len(samples)), samples)
    t = np.arange(len(sped)) / rate
    wobble = 1 + 0.12 * np.sin(2 * np.pi * 7 * t)  # a lively tremolo, 7 times a second
    return sped * wobble


def write(path: Path, audio: np.ndarray, rate: int = RATE) -> None:
    audio = audio / max(1e-6, np.abs(audio).max()) * 0.8
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((audio * 32767).astype("<i2").tobytes())


def main() -> int:
    from kokoro_onnx import Kokoro

    sys.path.insert(0, str(HERE.parent.parent))  # the repository, for voice/
    from voice.state import KOKORO_MODELS

    kokoro = Kokoro(str(KOKORO_MODELS / "kokoro-v1.0.onnx"), str(KOKORO_MODELS / "voices-v1.0.bin"))
    RAW.mkdir(parents=True, exist_ok=True)
    for agent, lines in PHRASES.items():
        write(RAW / f"ring_{agent}.wav", ringtone(agent))
        for n, line in enumerate(lines):
            samples, rate = kokoro.create(line, voice=VOICES[agent], lang="en-us", speed=1.0)
            write(RAW / f"phrase_{agent}_{n}.wav", henchify(samples, rate), rate)
        print(agent, "ok", flush=True)
    (RAW / "phrases.json").write_text(json.dumps(PHRASES, indent=1))
    import banner_mix  # the banner's ring: tone and phrases in one file
    banner_mix.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Someone's own performances of the ring phrases, made into henchman voices for their own phone.

    python3 call/android/henchify_takes.py          (needs: pip install praat-parselmouth noisereduce scipy soundfile)

Reads record_phrases.py's takes (~/.local/share/handsfree/phrase-takes/<agent>_<n>.wav), cleans each
(the room's own sound, learnt from the take's quietest moments, taken out; rumble under 90 Hz cut;
the silence around it trimmed; its loudest peak set) and makes it henchman-sized: pitch about
five semitones up and formants 25 % up, the performer's own timing kept. A performance pitched up
this way sounded natural where generated voices did not (2026-11-15).

The results go to app/src/debug/res/raw/phrase_<agent>_<n>.wav, which overrides the default
(Kokoro) phrases in the debug build only, and which git ignores: a person's voice stays on their
own machine and phone, never in the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

import noisereduce
import numpy as np
import parselmouth
import soundfile
from parselmouth.praat import call
from scipy.signal import butter, sosfilt

TAKES = Path.home() / ".local/share/handsfree/phrase-takes"
OUT = Path(__file__).resolve().parent / "app/src/debug/res/raw"
PITCH_UP, FORMANTS_UP = 1.35, 1.25  # about five semitones; a smaller head
EDGE = 0.15  # seconds of room kept before and after the performance
QUIETEST = 0.02  # a take whose loudest moment is under this (full scale 1) is the room, not a performance


def clean(audio: np.ndarray, rate: int) -> np.ndarray:
    frame = rate // 50
    frames = audio[: len(audio) // frame * frame].reshape(-1, frame)
    energy = (frames ** 2).mean(1)
    room = np.concatenate([frames[i] for i in sorted(np.argsort(energy)[:25])])  # its quietest half second
    audio = noisereduce.reduce_noise(y=audio, sr=rate, y_noise=room, prop_decrease=0.75, stationary=True)
    audio = sosfilt(butter(4, 90, "highpass", fs=rate, output="sos"), audio)
    level = 10 * np.log10(energy + 1e-12)
    voiced = np.percentile(level, 90)  # the performance's level, so one click can't set it (it fills >10 %)
    loud = np.flatnonzero(level > voiced - 18)  # the performance; 30 dB under kept the room (-42 dB) as a tail
    start, end = max(0, loud[0] * frame - int(EDGE * rate)), min(len(audio), (loud[-1] + 1) * frame + int(EDGE * rate))
    audio = audio[start:end]
    return audio / np.abs(audio).max() * 0.9  # by the loudest peak only: a soft clipper made it sound brittle


def henchify(path: Path, out: Path) -> str:
    """Makes the henchman phrase; or why not (left out, so the app keeps its default phrase)."""
    audio, rate = soundfile.read(str(path))
    if len(audio) < rate // 2:
        return "shorter than half a second: re-record it"
    if (peak := np.abs(audio).max()) < QUIETEST:
        return f"too quiet (peak {peak:.3f}, needs {QUIETEST}): re-record it closer to the mic"
    tmp = out.with_suffix(".clean.wav")
    soundfile.write(str(tmp), clean(audio, rate), rate)
    try:
        sound = parselmouth.Sound(str(tmp))
        median = call(sound.to_pitch(), "Get quantile", 0, 0, 0.5, "Hertz")
        if not np.isfinite(median):  # whispered, or no voice at all
            return "no voiced pitch found: re-record it out loud"
        call(sound, "Change gender", 75, 600, FORMANTS_UP, median * PITCH_UP, 1.2, 1.0).save(str(out), "WAV")
        return ""
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    takes = sorted(TAKES.glob("*_*.wav"))
    if not takes:
        print(f"no takes in {TAKES}: record them with record_phrases.py")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for take in takes:
        out = OUT / f"phrase_{take.stem}.wav"
        problem = henchify(take, out)
        if problem:
            out.unlink(missing_ok=True)  # the app's default phrase, rather than a broken one
        print(take.stem, problem or "ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

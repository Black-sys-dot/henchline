"""Emphasis marks and a tone note for words dictated to an agent.

Stressed words come out as *word*; when the whole utterance stands out from
the user's usual voice, a note such as [tone: loud, fast] goes on the end.
Reads only the recording and Whisper's word timings. The running baseline of
the user's voice (numbers, never words) lives in ~/.local/state/ptt/voice.json.
"""

from __future__ import annotations

import json
import re
import wave
from pathlib import Path

import numpy as np

BASELINE = Path.home() / ".local/state/ptt/voice.json"
FRAME = 0.04  # seconds per analysis frame
WARMUP = 5  # utterances before tone notes start
MAX_MARKS = 2
ALPHA = 0.1  # how fast the baseline follows the user's voice
# Glue words that pick up stress from their neighbours; never worth a mark
GLUE = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "um", "uh"}


def load(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as wav:
        rate = wav.getframerate()
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0, rate


def frames(audio: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Loudness in dB and pitch in Hz (nan when unvoiced) per frame."""
    size = int(rate * FRAME)
    count = len(audio) // size
    chunks = audio[: count * size].reshape(count, size)
    db = 20 * np.log10(np.sqrt((chunks**2).mean(axis=1)) + 1e-6)
    lo, hi = int(rate / 400), int(rate / 75)
    f0 = np.full(count, np.nan)
    floor = np.percentile(db, 20) + 10 if count else 0
    for i, chunk in enumerate(chunks):
        if db[i] < floor:
            continue
        chunk = chunk - chunk.mean()
        corr = np.correlate(chunk, chunk, mode="full")[size - 1:]
        if corr[0] <= 0:
            continue
        corr = corr / corr[0]
        lag = lo + int(np.argmax(corr[lo:hi]))
        if corr[lag] > 0.4:
            f0[i] = rate / lag
    return db, f0


def zscores(values: np.ndarray) -> np.ndarray:
    spread = np.nanstd(values)
    if not spread or np.isnan(spread):
        return np.zeros_like(values)
    return (values - np.nanmean(values)) / spread


def mark(word: str) -> str:
    """'wife.' -> '*wife*.' keeping spaces and punctuation outside."""
    match = re.match(r"^(\W*)(.*?)(\W*)$", word)
    lead, core, tail = match.groups() if match else ("", word, "")
    return f"{lead}*{core}*{tail}" if core else word


def describe(level: float, rate: float, spread: float) -> list[str]:
    """Compare this utterance with the baseline and update it."""
    base = {}
    try:
        base = json.loads(BASELINE.read_text())
    except (FileNotFoundError, ValueError):
        pass
    labels = []
    if base.get("count", 0) >= WARMUP:
        if level >= base["level"] + 6:
            labels.append("loud")
        elif level <= base["level"] - 6:
            labels.append("quiet")
        if rate >= base["rate"] * 1.25:
            labels.append("fast")
        elif rate <= base["rate"] * 0.75:
            labels.append("slow")
        if spread and base["spread"]:
            if spread >= base["spread"] * 1.4:
                labels.append("animated")
            elif spread <= base["spread"] * 0.6:
                labels.append("flat")
    if not base:
        base = {"level": level, "rate": rate, "spread": spread, "count": 0}
    else:
        for key, value in (("level", level), ("rate", rate), ("spread", spread)):
            if value:  # no pitch found says nothing about the voice
                base[key] = value if not base[key] else base[key] + ALPHA * (value - base[key])
    base["count"] = base.get("count", 0) + 1
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(base))
    return labels


def annotate(path: Path, words: list[tuple[float, float, str]], text: str) -> str:
    """Return the text with stressed words marked and a tone note, if any."""
    if not words or "".join(w for _, _, w in words).strip() != text:
        return text  # timings do not line up with the transcript; leave it be
    audio, rate = load(path)
    db, f0 = frames(audio, rate)
    if len(db) == 0:
        return text
    loud, pitch, length = [], [], []
    for start, end, word in words:
        a = int(start / FRAME)
        b = max(a + 1, int(end / FRAME))
        loud.append(np.percentile(db[a:b], 90) if b <= len(db) else np.nan)
        voiced = f0[a:b][~np.isnan(f0[a:b])] if b <= len(f0) else []
        pitch.append(np.log2(voiced.max()) * 12 if len(voiced) else np.nan)
        letters = max(1, len(re.sub(r"\W", "", word)))
        length.append((end - start) / letters)
    loud, pitch, length = map(np.array, (loud, pitch, length))

    marked = [word for _, _, word in words]
    if len(words) >= 4:
        score = zscores(loud) + np.nan_to_num(zscores(pitch)) + 0.5 * zscores(length)
        middle = np.nanmedian(loud)
        picks = [
            i for i in np.argsort(-np.nan_to_num(score, nan=-9))
            if score[i] >= 2.5 and loud[i] >= middle + 4
            and re.sub(r"\W", "", words[i][2]).lower() not in GLUE
        ]
        for i in picks[:MAX_MARKS]:
            marked[i] = mark(marked[i])

    voiced = f0[~np.isnan(f0)]
    spread = float(np.std(np.log2(voiced) * 12)) if len(voiced) > 5 else 0.0
    talk = max(0.5, words[-1][1] - words[0][0])
    labels = describe(float(np.nanmedian(loud)), len(words) / talk, spread)
    rich = "".join(marked).strip()
    return f"{rich} [tone: {', '.join(labels)}]" if labels else rich

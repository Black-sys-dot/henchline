#!/usr/bin/env python3
"""Guided "hey Claude" recordings around the house, scored as they come in.

    wakeword_collect.py [POSITION ...]

For each position talkback announces where to stand, gives the user time to
get there, plays a short chime, and records a few seconds from the same
echo-cancelled mic hands-free uses. Each take is scored with the wake-word
model at once, and talkback says how many "hey Claude"s it caught; a miss
is repeated. Hands-free listening is paused meanwhile, so the samples are
not sent to an agent. Clips land in ~/.local/share/handsfree/samples/ for
a later custom verifier. A last "just talk normally" take checks for false
wakes.
"""

from __future__ import annotations

import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from openwakeword.model import Model

HOME = Path.home()
STATE = HOME / ".local/state/ptt"
DATA = HOME / ".local/share/handsfree"
SAMPLES = DATA / "samples"
PAUSE = STATE / "handsfree.pause"
SAY = HOME / ".cursor/skills/talkback/scripts/say.py"
CHIME = "/usr/share/sounds/freedesktop/stereo/message.oga"
SOURCE = "talkback_ec_source"
RATE = 16000
THRESHOLD = 0.5
POSITIONS = [
    ("desk", "at your desk, facing me"),
    ("dining", "at the dining table next to me"),
    ("kitchen-away", "in the kitchen, facing away from me"),
    ("hall-towards", "in the hall, facing towards me"),
]


def say(text: str) -> None:
    subprocess.run([str(SAY), "--agent", "handsfree", "--session", "handsfree", text])
    time.sleep(0.5)
    while (STATE / "talkback.playing").exists() or any((STATE / "talkback.queue").glob("*.json")):
        time.sleep(0.2)


def record(seconds: float) -> np.ndarray:
    proc = subprocess.run(
        ["timeout", str(seconds), "parec", f"--device={SOURCE}", "--format=s16le",
         f"--rate={RATE}", "--channels=1", "--raw"],
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype="<i2")


def wakes(model: Model, audio: np.ndarray) -> tuple[int, float]:
    """How many separate times it woke, and the best score."""
    model.reset()
    count, best, armed = 0, 0.0, True
    for start in range(0, len(audio) - 1280, 1280):
        score = model.predict(audio[start:start + 1280])["hey_claude"]
        best = max(best, score)
        if score > THRESHOLD and armed:
            count, armed = count + 1, False
        elif score < 0.2:
            armed = True
    return count, best


def save(name: str, audio: np.ndarray) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    with wave.open(str(SAMPLES / f"{name}-{time.strftime('%H%M%S')}.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(audio.tobytes())


def main() -> int:
    wanted = set(sys.argv[1:])
    positions = [p for p in POSITIONS if not wanted or p[0] in wanted]
    model = Model(wakeword_models=[str(DATA / "hey_claude.onnx")], inference_framework="onnx")
    PAUSE.touch()
    results = []
    try:
        say("Wake word recording. For each spot I'll say where to go, then play a chime. "
            "After the chime, say hey Claude twice, with a short pause in between.")
        for name, where in positions:
            for attempt in range(3):
                say(f"Next: {where}. Chime in ten seconds." if attempt == 0
                    else "Let's do that spot once more. Chime in three seconds.")
                time.sleep(10 if attempt == 0 else 3)
                subprocess.run(["pw-play", CHIME])
                audio = record(6)
                save(name, audio)
                count, best = wakes(model, audio)
                results.append((name, attempt, count, best))
                print(f"{name} try {attempt + 1}: woke {count}x, best {best:.2f}", flush=True)
                if count >= 2:
                    say(f"Got both. Score {best:.2f}.")
                    break
                say(f"I only caught {count} of 2." if count else "I didn't catch either.")
        say("Last one, from wherever you are: after the chime, talk normally for ten seconds "
            "about anything, but don't say hey Claude.")
        time.sleep(1)
        subprocess.run(["pw-play", CHIME])
        audio = record(10)
        save("negative", audio)
        count, best = wakes(model, audio)
        print(f"negative: woke {count}x, best {best:.2f}", flush=True)
        say("No false wakes. That's all, thank you." if not count
            else f"That woke it {count} times by mistake. Good to know. That's all, thank you.")
    finally:
        PAUSE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

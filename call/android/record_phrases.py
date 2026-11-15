#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Record someone performing the ring phrases, for the henchman voices.

    call/android/record_phrases.py [--device SOURCE] [AGENT ...]   (default: the default mic, every agent)

For each phrase: talkback says which one, a chime, then the default mic (a good external one, ideally)
records at 48 kHz until the performer has finished: two seconds quiet after they started, at most
twenty. Hands-free is paused meanwhile, so nothing reaches an agent.
The takes land in ~/.local/share/handsfree/phrase-takes/<agent>_<n>.wav, for ringtones.py to turn
into henchman voices. They stay on this machine: a person's voice is theirs.
"""

from __future__ import annotations

import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent.parent), str(HERE.parent.parent / "handsfree")]  # voice/, hands-free's tools
from collect import CHIME, speak as say  # noqa: E402
from ringtones import PHRASES  # noqa: E402
from voice.state import HANDSFREE_DATA, PAUSE  # noqa: E402

TAKES = HANDSFREE_DATA / "phrase-takes"
RATE = 48000
FRAME = RATE // 50  # 20 ms
DONE_AFTER, LONGEST = 2.0, 20.0  # seconds quiet after they started, and at most
LOUDER = 15  # dB over the room's own sound counts as performing


def record_take(device: str) -> np.ndarray:
    """From the chime until they have finished, with a little of the room before their first sound."""
    stream = subprocess.Popen(["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}", "--channels=1",
                               "--raw", "--latency-msec=20"], stdout=subprocess.PIPE)
    frames, levels, started, quiet = [], [], None, 0
    try:
        while len(frames) * FRAME / RATE < LONGEST:
            chunk = stream.stdout.read(FRAME * 2)
            if len(chunk) < FRAME * 2:
                break
            frame = np.frombuffer(chunk, "<i2")
            frames.append(frame)
            db = 20 * np.log10(np.sqrt((frame.astype(float) ** 2).mean()) / 32768 + 1e-9)
            levels.append(db)
            room = np.percentile(levels, 10)
            if started is None:
                if len(levels) > 10 and db > room + LOUDER:
                    started = len(frames)
                continue
            quiet = quiet + 1 if db < room + LOUDER else 0
            if quiet * FRAME / RATE >= DONE_AFTER:
                break
    finally:
        stream.terminate()
    begin = max(0, (started or 0) - 15)  # 0.3 s before their first sound
    return np.concatenate(frames[begin:]) if frames else np.zeros(0, "<i2")


def save(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(audio.tobytes())


def main() -> int:
    args = sys.argv[1:]
    device = "@DEFAULT_SOURCE@"
    if "--device" in args:
        at = args.index("--device")
        device, args = args[at + 1], args[:at] + args[at + 2:]
    agents = args or list(PHRASES)
    TAKES.mkdir(parents=True, exist_ok=True)
    PAUSE.touch()
    try:
        say("Henchman recording. For each phrase I'll name it, then chime. After the chime, perform it, "
            "full henchman energy. Take your time: I stop two seconds after you finish.")
        for agent in agents:
            for n, line in enumerate(PHRASES[agent]):
                print(f"\n{agent} {n + 1}: {line}", flush=True)
                # The words on screen, so the performer needn't look anywhere else
                subprocess.run(["notify-send", "--app-name=Henchline", "--expire-time=20000",
                                f"{agent.capitalize()}, phrase {n + 1}", line], check=False)
                say(f"{agent.capitalize()}, phrase {n + 1}.")
                time.sleep(1.5)
                subprocess.run(["pw-play", CHIME])
                take = record_take(device)
                save(TAKES / f"{agent}_{n}.wav", take)
                print(f"  {len(take) / RATE:.1f} s", flush=True)
        say("That's all, thank you!")
    finally:
        PAUSE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

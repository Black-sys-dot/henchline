#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Guided recordings of talking over talkback, to build a better talk-over detector offline.

    echo_collect.py [POSITION ...]

For each position, talkback plays clips in the usual voice while three
recorders run side by side: the raw built-in mic (both channels), the
echo-cancelled mic hands-free uses, and what the speakers play. Some clips
the user stays quiet (only our echo); in the others a chime partway through
says "talk over me now". Hands-free is paused meanwhile. Each session lands
in ~/.local/share/handsfree/echo-set/<time>/ with trials.json describing
every take; the user's voice stays on this machine, never in the repository.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repository, for voice/
from routing import SAY  # noqa: E402
from sensing import EC_SOURCE, RATE  # noqa: E402
from voice.spool import queued  # noqa: E402
from voice.state import BY_HANDSFREE, PAUSE, PLAYING, read_json  # noqa: E402

SET = Path.home() / ".local/share/handsfree/echo-set"
CHIME = "/usr/share/sounds/freedesktop/stereo/message.oga"
VOICE = "af_heart"  # the voice the user talks over most
CUE = (4.0, 10.0)  # seconds into a clip for the "talk now" chime, at random
TAIL = 4.0  # seconds recorded after a clip ends, for the end of what the user says
TRIALS = ["quiet", "talk", "talk", "quiet", "talk", "talk"]
POSITIONS = [
    ("desk", "at your desk"),
    ("hallway", "in the hallway, where you were earlier"),
]
CLIPS = [
    "The library opens at nine and closes at five. Snow fell over the city during the night, and in the "
    "morning the streets were silent. Bread rises best in a warm kitchen, slowly, while nobody watches it.",
    "The train left the station on time, crossed the bridge, and disappeared behind the hills. Rivers carry "
    "sand to the sea, and the sea carries it back again, one wave at a time.",
    "A clock that drifts a little is still useful, if you know how much it drifts. Numbers help more than "
    "guesses, and a good measurement beats a long argument.",
    "Summer evenings in the north stay light until almost midnight. People sit outside, talk, and forget "
    "the time, until the first cold wind reminds them.",
]


def ours_queued() -> bool:
    """Clips of this tool still waiting (an agent's held clips are not ours to wait for)."""
    return any(read_json(path).get("agent") == BY_HANDSFREE for path in queued())


def say(text: str, voice: str = "") -> None:
    """Speak and wait until it is done."""
    subprocess.run([str(SAY), "--agent", BY_HANDSFREE, "--session", BY_HANDSFREE,
                    *(["--voice", voice] if voice else []), text])
    time.sleep(0.5)
    while PLAYING.exists() or ours_queued():
        time.sleep(0.1)


class Recorders:
    """The raw mic, the cancelled mic and the speakers, recorded from the same moment."""

    SOURCES = {
        "raw": ("alsa_input.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Mic__source", 2),
        "ec": (EC_SOURCE, 1),
        "play": ("@DEFAULT_MONITOR@", 1),
    }

    def __init__(self, folder: Path, take: str) -> None:
        self.started = time.monotonic()
        self.procs = []
        for name, (device, channels) in self.SOURCES.items():  # wav straight to disk: a pipe would fill and stall
            self.procs.append(subprocess.Popen(
                ["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}", f"--channels={channels}",
                 "--latency-msec=30", "--file-format=wav", str(folder / f"{take}-{name}.wav")]))

    def stop(self) -> None:
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            proc.wait()


def trial(folder: Path, take: str, kind: str, text: str) -> dict:
    """One clip, recorded; for a "talk" trial, a chime partway through asks the user to talk over it."""
    recorders = Recorders(folder, take)
    time.sleep(1.0)  # some room sound before the clip
    subprocess.Popen([str(SAY), "--agent", BY_HANDSFREE, "--session", BY_HANDSFREE, "--voice", VOICE, text])
    while not PLAYING.exists():
        time.sleep(0.02)
    clip_start = time.monotonic() - recorders.started
    cue = None
    if kind == "talk":
        time.sleep(random.uniform(*CUE))
        cue = time.monotonic() - recorders.started
        subprocess.Popen(["pw-play", CHIME])
    while PLAYING.exists() or ours_queued():
        time.sleep(0.05)
    clip_end = time.monotonic() - recorders.started
    time.sleep(TAIL)
    recorders.stop()
    return {"take": take, "kind": kind, "clip_start": round(clip_start, 3), "clip_end": round(clip_end, 3),
            "cue": None if cue is None else round(cue, 3), "text": text}


def main() -> int:
    wanted = set(sys.argv[1:])
    positions = [p for p in POSITIONS if not wanted or p[0] in wanted]
    folder = SET / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    takes: list[dict] = []
    PAUSE.touch()
    try:
        say("Talk-over recording. I'll read some ordinary sentences in my usual voice. In some, a chime will "
            "sound partway through: that means talk over me, the way you'd interrupt me, with a sentence or "
            "two of anything. With no chime, please stay quiet.")
        for name, where in positions:
            say(f"Next: {where}. First clip in ten seconds.")
            time.sleep(10)
            for i, kind in enumerate(TRIALS):
                take = f"{name}-{i + 1}-{kind}"
                takes.append({"position": name, **trial(folder, take, kind, random.choice(CLIPS))})
                (folder / "trials.json").write_text(json.dumps(takes, indent=1))
                print(take, "done", flush=True)
                time.sleep(1.5)
        say("That's all. Thank you. The recordings stay on this laptop.")
    finally:
        PAUSE.unlink(missing_ok=True)
    print(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())

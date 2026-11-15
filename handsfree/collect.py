"""What the guided recording tools share: speaking a prompt and waiting for it, and the chime.

wakeword_collect.py and echo_collect.py both talk the user through takes
while hands-free is paused.
"""

from __future__ import annotations

import subprocess
import time

from routing import SAY
from voice.spool import queued
from voice.state import BY_HANDSFREE, PLAYING, read_json

CHIME = "/usr/share/sounds/freedesktop/stereo/message.oga"  # "now": start talking


def ours_queued() -> bool:
    """Prompts of these tools still waiting (an agent's held clips are not ours to wait for)."""
    return any(read_json(path).get("agent") == BY_HANDSFREE for path in queued())


def speak(text: str, voice: str = "", wait: bool = True) -> None:
    """Speak in hands-free's voice (or another), and wait until it has been said."""
    command = [str(SAY), "--agent", BY_HANDSFREE, "--session", BY_HANDSFREE,
               *(["--voice", voice] if voice else []), text]
    if not wait:
        subprocess.Popen(command)
        return
    subprocess.run(command)
    time.sleep(0.5)
    while PLAYING.exists() or ours_queued():
        time.sleep(0.1)


def chime() -> None:
    subprocess.Popen(["pw-play", CHIME])

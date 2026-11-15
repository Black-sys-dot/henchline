#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""A test set for tone marks: the user's voice with known stress and style.

    tone_collect.py

Hands-free, at the desk: talkback asks for a sentence with one word stressed
("stress: cursor"), or said a certain way (loudly, quietly, fast, slowly),
chimes, and records the user. Each stress sentence comes twice with
different words stressed, so the words alone cannot tell which one should
be marked. Takes and what was asked (set.json) land in
~/.local/share/handsfree/asr-set/tone/, for tone_bench.py. Hands-free
listening is paused meanwhile.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "core"), str(Path(__file__).resolve().parents[2])]  # hands-free's modules; voice/
from asr_collect import record, save  # noqa: E402
from collect import CHIME, speak as say  # noqa: E402
from voice.state import HANDSFREE_DATA, PAUSE  # noqa: E402

SET = HANDSFREE_DATA / "asr-set" / "tone"
STRESS = [  # (sentence, the word to stress): each sentence twice, a different word each time
    ("Switch me over to Cursor, not Codex.", "cursor"),
    ("Switch me over to Cursor, not Codex.", "not"),
    ("I said the red cable, not the blue one.", "red"),
    ("I said the red cable, not the blue one.", "blue"),
    ("Push it after the review, not before.", "after"),
    ("Push it after the review, not before.", "before"),
    ("The training run on Berzelius failed again.", "again"),
    ("The training run on Berzelius failed again.", "Berzelius"),
    ("Can you check the logs right now?", "now"),
    ("Can you check the logs right now?", "logs"),
]
STYLE_SENTENCE = "Okay, let's look at the echo canceller after lunch."
STYLES = ["normally", "loudly", "quietly", "fast", "slowly", "normally"]


def main() -> int:
    SET.mkdir(parents=True, exist_ok=True)
    index = SET / "set.json"
    asked = json.loads(index.read_text()) if index.exists() else {}
    PAUSE.touch()
    try:
        say("Tone test, at your desk. I'll tell you a sentence and which word to stress, or how to say it. "
            "After the chime, say it that way. Stress only the one word.")
        time.sleep(3)
        items = [(s, w, None) for s, w in STRESS] + [(STYLE_SENTENCE, None, how) for how in STYLES]
        for n, (sentence, stressed, how) in enumerate(items, 1):
            say(f"{sentence} Stress: {stressed}." if stressed else f"Say it {how}: {sentence}")
            subprocess.run(["pw-play", CHIME])
            audio = record(len(sentence.split()) * 0.55 + 3)
            take = f"tone-{n:02d}-{time.strftime('%H%M%S')}.wav"
            save(SET / take, audio)
            asked[take] = {"text": sentence, "stress": stressed, "style": how}
            index.write_text(json.dumps(asked, indent=1))
            print(f"{take}: {stressed or how}", flush=True)
        say("That's all, thank you.")
    finally:
        PAUSE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

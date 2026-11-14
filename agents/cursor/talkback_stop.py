#!/usr/bin/env python3
"""Speak the one-line recap written for this turn, if talkback is on."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

STATE = Path.home() / ".local/state/ptt"
FLAG = STATE / "talkback"
LINE = STATE / "talkback.txt"
SAY = Path.home() / ".cursor/skills/talkback/scripts/say.py"


def main() -> int:
    try:
        json.load(sys.stdin)
    except json.JSONDecodeError:
        pass
    print("{}")
    if not FLAG.exists() or not LINE.exists() or not SAY.exists():
        return 0
    text = LINE.read_text(encoding="utf-8").strip()
    LINE.unlink(missing_ok=True)
    if not text:
        return 0
    subprocess.Popen(
        [str(SAY), text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

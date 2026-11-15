#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""While a phone call is up, the terminal side of a Claude Code conversation in the phone's chat too.

    a Claude Code hook (UserPromptSubmit and Stop): call/mirror.py, the hook's JSON on stdin

What you type in the terminal and Claude's written replies (not its tool calls or their output) go into a
spool the call server empties into the chat of that agent. Off a call it does nothing: the phone keeps only
what it was shown. Words that came from the call itself (tagged [hands-free …]) are not sent back to it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice.agents import identity  # noqa: E402
from voice.state import CALL_MIRROR, in_call, write_json  # noqa: E402

LONGEST = 4000  # characters of one line in the chat


def reply(transcript: Path) -> str:
    """Claude's written reply to the last prompt: its text blocks since then, in order."""
    try:
        lines = transcript.read_text().splitlines()[-400:]
    except OSError:
        return ""
    said = []
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "user":
            blocks = content if isinstance(content, list) else [{"type": "text"}]
            if any(b.get("type") == "text" for b in blocks if isinstance(b, dict)):
                break  # the prompt: the reply is everything after it
        elif entry.get("type") == "assistant" and isinstance(content, list):
            said += [b.get("text", "") for b in reversed(content) if isinstance(b, dict) and b.get("type") == "text"]
    return "\n\n".join(t.strip() for t in reversed(said) if t.strip())


def main() -> int:
    if not in_call():
        return 0
    try:
        hook = json.load(sys.stdin)
    except ValueError:
        return 0
    _, session = identity("claude", str(hook.get("session_id", "")))
    event = hook.get("hook_event_name", "")
    if event == "UserPromptSubmit":
        text, who = str(hook.get("prompt", "")), "you"
        if text.lstrip().startswith("[hands-free"):  # said on the call: the chat has it already
            return 0
    elif event == "Stop":
        text, who = reply(Path(str(hook.get("transcript_path", "")))), "Claude"
    else:
        return 0
    if text.strip():
        if len(text) > LONGEST:
            text = text[:LONGEST] + " …"
        write_json(CALL_MIRROR / f"{time.time_ns()}.json", {"session": session, "from": who, "text": text},
                   private=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

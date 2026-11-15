#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""While a phone call is up, the terminal side of a Claude Code conversation in the phone's chat too.

    a Claude Code hook (UserPromptSubmit and Stop) runs call/mirror.sh, which runs this only during a call

Key blocks, tokens and other secret-looking values are replaced by [hidden] first: the phone keeps its
chats (90 days), and a pasted secret has no business there.

What you type in the terminal and Claude's written replies (not its tool calls or their output) go into a
spool the call server empties into the chat of that agent. Off a call it does nothing: the phone keeps only
what it was shown. Words that came from the call itself (tagged [hands-free …]) are not sent back to it.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice.agents import identity  # noqa: E402
from voice.state import CALL_MIRROR, in_call, write_json  # noqa: E402

LONGEST = 4000  # characters of one line in the chat
TAIL = 512 * 1024  # bytes of the transcript read for the reply: a long session's whole file would be megabytes
SECRETS = [  # the shapes secrets usually come in, and what each becomes
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[hidden]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
                r"|sk-[A-Za-z0-9_-]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}"
                r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)"),
     "[hidden]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)(\s*[:=]\s*)\S+"), r"\1\2[hidden]"),
    (re.compile(r"\b(\w+://[^:/\s@]+:)[^@\s]+@"), r"\1[hidden]@"),  # a password in a URL
]


def hidden(text: str) -> str:
    for shape, instead in SECRETS:
        text = shape.sub(instead, text)
    return text


def reply(transcript: Path) -> str:
    """Claude's written reply to the last prompt: its text blocks since then, in order."""
    try:
        with transcript.open("rb") as f:
            f.seek(max(0, f.seek(0, 2) - TAIL))
            lines = f.read().decode(errors="replace").splitlines()[1:]  # the first may be cut: dropped
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
        text = hidden(text)
        write_json(CALL_MIRROR / f"{time.time_ns()}.json", {"session": session, "from": who, "text": text},
                   private=True)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a hook must never get in Claude Code's way
        pass
    sys.exit(0)

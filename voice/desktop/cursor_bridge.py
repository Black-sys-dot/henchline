"""Henchline's bridge inside Cursor (cursor-bridge/): each Cursor window listens on a private socket and, asked,
brings one agent chat to the front by its id, so words typed into the window land in that chat.

A Cursor editor chat's commands run with CURSOR_CONVERSATION_ID (its chat id) and CURSOR_WORKSPACE_LABEL (its
window's workspace); voice/agents.py keys the chat by the first and names it by the second.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

from voice.state import STATE

DIR = STATE / "cursor-bridge"
CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def chat_of(session: str) -> str:
    """The Cursor chat id in a session key ("cursor-1e6e96a3-…"), or "" for a key that isn't one chat's."""
    found = CHAT_ID.fullmatch(session.removeprefix("cursor-"))
    return found.group(0) if found else ""


def open_chat(workspace: str, chat: str) -> bool:
    """Asks Cursor's windows to bring that chat to the front: the window showing that workspace first, then any
    other (a label can differ from the window's name); only the window that has the chat says yes. False when none
    did: then nothing may be typed, or the words would land in whatever chat is on screen."""
    if not CHAT_ID.fullmatch(chat):
        return False
    windows = []
    for note in DIR.glob("*.json"):
        try:
            windows.append((json.loads(note.read_text()), note.with_suffix(".sock")))
        except (OSError, ValueError):
            continue
    windows.sort(key=lambda w: w[0].get("workspace") != workspace)  # its own window first
    for _, sock in windows:
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(5)
                conn.connect(str(sock))
                conn.sendall(json.dumps({"open": chat}).encode() + b"\n")
                answer = b""
                while b"\n" not in answer and len(answer) < 4096:  # the whole answer line, however it arrives
                    part = conn.recv(4096)
                    if not part:
                        break
                    answer += part
            if json.loads(answer.decode() or "{}").get("ok"):
                return True
        except (OSError, ValueError):
            continue  # a window that has gone: its note stays until Cursor cleans it up
    return False


def chat_title(chat: str) -> str:
    """A few words naming a Cursor chat, from the first thing the user asked in it (its transcript), so several
    chats in one window can be told apart on the phone."""
    if not CHAT_ID.fullmatch(chat):
        return ""
    for transcript in (Path.home() / ".cursor/projects").glob(f"*/agent-transcripts/{chat}/{chat}.jsonl"):
        try:
            with transcript.open("rb") as f:
                first = json.loads(f.readline(65536) or b"{}")
            text = " ".join(part.get("text", "") for part in first["message"]["content"] if isinstance(part, dict))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        asked = re.search(r"<user_query>\s*(.*?)\s*(?:</user_query>|$)", text, re.S)
        words = (asked.group(1) if asked else text).split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return ""


def installed() -> bool:
    return any(DIR.glob("*.sock"))


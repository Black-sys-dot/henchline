"""Henchline's bridge inside Cursor (cursor-bridge/): each Cursor window listens on a private socket and, asked,
brings one agent chat to the front by its id, so words typed into the window land in that chat.

A Cursor editor chat's commands run with CURSOR_CONVERSATION_ID (its chat id) and CURSOR_WORKSPACE_LABEL (its
window's workspace); voice/agents.py keys the chat by the first and names it by the second.
"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import time
from pathlib import Path

from voice.state import STATE

DIR = STATE / "cursor-bridge"
CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def chat_of(session: str) -> str:
    """The Cursor chat id in a session key ("cursor-1e6e96a3-…"), or "" for a key that isn't one chat's."""
    found = CHAT_ID.fullmatch(session.removeprefix("cursor-"))
    return found.group(0) if found else ""


def project_of(folder: str) -> str:
    """Cursor's name for a workspace folder's project under ~/.cursor/projects: its path, dashed."""
    return re.sub(r"[^A-Za-z0-9]+", "-", folder).strip("-")


def owner_of(chat: str) -> str:
    """The project (~/.cursor/projects/<project>) whose transcripts hold that chat; "" when none does."""
    found = list((Path.home() / ".cursor/projects").glob(f"*/agent-transcripts/{chat}"))
    return found[0].parent.parent.name if len(found) == 1 else ""


def open_chat(workspace: str, chat: str) -> bool:
    """Brings that chat to the front in the one Cursor window that has it: the window whose workspace folder is
    the project holding the chat's transcript (Cursor's own commands say yes to any id, 2026-11-16). False when no
    window has it: then nothing may be typed, or the words would land in whatever chat is on screen."""
    if not CHAT_ID.fullmatch(chat):
        return False
    project = owner_of(chat)
    if not project:
        return False
    owners = []
    for note in DIR.glob("*.json"):
        try:
            window = json.loads(note.read_text())
        except (OSError, ValueError):
            continue
        if project in {project_of(f) for f in window.get("folders", []) if isinstance(f, str)}:
            owners.append(note)
    if len(owners) != 1:  # none, or two windows on that project: which has the chat can't be told, so neither
        return False
    for note in owners:
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(5)
                conn.connect(str(note.with_suffix(".sock")))
                conn.sendall(json.dumps({"open": chat}).encode() + b"\n")
                answer = b""
                while b"\n" not in answer and len(answer) < 4096:  # the whole answer line, however it arrives
                    part = conn.recv(4096)
                    if not part:
                        break
                    answer += part
            if json.loads(answer.decode() or "{}").get("ok"):
                time.sleep(0.4)  # Cursor moving its focus to that chat, before anything is typed
                return True
        except (OSError, ValueError):
            continue  # a window that has gone: its note stays until Cursor cleans it up
    return False


STATE_DB = Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"


def cursor_title(chat: str) -> str:
    """The chat's title as Cursor shows it ("Ozer's PR review rules"), from Cursor's own store, read only."""
    try:
        with sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=1) as db:
            row = db.execute("select value from cursorDiskKV where key = ?", (f"composerData:{chat}",)).fetchone()
        return str(json.loads(row[0]).get("name") or "").strip() if row else ""
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return ""


def chat_title(chat: str) -> str:
    """A chat's name for the phone: Cursor's own title for it (the user's catch, 2026-11-16: the phone's didn't
    match the IDE's), else a few words of the first thing asked in it (its transcript)."""
    if not CHAT_ID.fullmatch(chat):
        return ""
    if title := cursor_title(chat):
        return title
    for transcript in (Path.home() / ".cursor/projects").glob(f"*/agent-transcripts/{chat}/{chat}.jsonl"):
        try:
            with transcript.open("rb") as f:
                first = json.loads(f.readline(65536) or b"{}")
            text = " ".join(part.get("text", "") for part in first["message"]["content"] if isinstance(part, dict))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        asked = re.search(r"<user_query>\s*(.*?)\s*(?:</user_query>|$)", text, re.S)
        asked_words = asked.group(1) if asked else text
        # Not Henchline's own tag and one-time introduction, which can be the first thing a chat was sent
        asked_words = re.sub(r"^\s*\[[^\]]*\]\s*(\(Henchline, said once:.*?How it all works: \S+\)\s*)?", "", asked_words, flags=re.S)
        words = asked_words.split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return ""


def installed() -> bool:
    return any(DIR.glob("*.sock"))


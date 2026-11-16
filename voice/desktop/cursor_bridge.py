"""Henchline's bridge inside Cursor (cursor-bridge/): each Cursor window listens on a private socket and, asked,
brings one agent chat to the front by its id, so words typed into the window land in that chat.

A Cursor editor chat's commands run with CURSOR_CONVERSATION_ID (its chat id) and CURSOR_WORKSPACE_LABEL (its
window's workspace); voice/agents.py keys the chat by the first and names it by the second.
"""

from __future__ import annotations

import contextlib
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


def ask_window(chat: str, request: dict) -> bool:
    """Sends one request to the one Cursor window that has the chat (see open_chat); True when it said yes."""
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
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(5)
            conn.connect(str(owners[0].with_suffix(".sock")))
            conn.sendall(json.dumps(request).encode() + b"\n")
            answer = b""
            while b"\n" not in answer and len(answer) < 4096:  # the whole answer line, however it arrives
                part = conn.recv(4096)
                if not part:
                    break
                answer += part
        return bool(json.loads(answer.decode() or "{}").get("ok"))
    except (OSError, ValueError):
        return False


def chats_of(chat: str) -> list[str]:
    """The other chats of the chat's project worked in this last week (its transcripts' neighbours): to check a
    switch touched only it."""
    project = owner_of(chat)
    folder = Path.home() / ".cursor/projects" / project / "agent-transcripts"
    try:
        return [p.name for p in folder.iterdir() if p.name != chat and CHAT_ID.fullmatch(p.name)
                and time.time() - p.stat().st_mtime < 7 * 86400] if project else []
    except OSError:
        return []


def switch_model(chat: str, wanted: str) -> str:
    """Moves a Cursor editor chat to a model by Cursor's own "cycle model", one step at a time, read back from its
    store after each step, until the chat is on it ("" then). Stops at once when a step didn't move that chat, or
    moved another one (Cursor's cycle works on the selected chat, so only that chat may change)."""
    if not CHAT_ID.fullmatch(chat):
        return "Not a Cursor chat."
    start = chat_model(chat)
    if not start:
        return "Cursor's record of that chat can't be read, so it can't be checked: not switched."
    # Only chats whose model could be read: one becoming readable meanwhile isn't a change Cursor made
    others = {other: model for other in chats_of(chat) if (model := chat_model(other))}
    steps = 0
    now = start
    while not (wanted.startswith(now) or now.startswith(wanted)):
        if steps >= 40:
            return f"Didn't reach {wanted} in 40 steps: left on {now}."
        if not ask_window(chat, {"cycle": chat}):
            return ("Cursor's bridge wouldn't step that chat (is another chat selected there?)"
                    + (f": after {steps} steps it's on {now}." if steps else ": not switched."))
        steps += 1
        before = now
        for _ in range(15):  # Cursor writes its store a moment later
            time.sleep(0.2)
            now = chat_model(chat)
            if now and now != before:
                break
        moved = [other for other, model in others.items() if chat_model(other) != model]
        if moved:
            return f"Stopped: Cursor changed another chat's model instead ({len(moved)}). This chat is on {now}."
        if not now or now == before:
            return f"Cursor hasn't changed the model yet (it may still be changing): it's on {now or before}."
        if now == start:
            return f"{wanted} isn't among this chat's models in Cursor: left on {start}."
    return ""


def open_chat(workspace: str, chat: str) -> bool:
    """Brings that chat to the front in the one Cursor window that has it: the window whose workspace folder is
    the project holding the chat's transcript (Cursor's own commands say yes to any id, 2026-11-16). False when no
    window has it: then nothing may be typed, or the words would land in whatever chat is on screen. (workspace:
    the chat's label, kept for the callers; the transcript decides.)"""
    if not CHAT_ID.fullmatch(chat) or not ask_window(chat, {"open": chat}):
        return False
    time.sleep(0.4)  # Cursor moving its focus to that chat, before anything is typed
    return True


STATE_DB = Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"
_titles: dict[str, tuple[float, str]] = {}  # chat id: (when read, title); the list is rebuilt every few seconds


def composer(chat: str) -> dict:
    """Cursor's own record of a chat (title, model), from its store, read only; {} when it can't be read."""
    try:
        with contextlib.closing(sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute("select value from cursorDiskKV where key = ?", (f"composerData:{chat}",)).fetchone()
        found = json.loads(row[0]) if row else {}
        return found if isinstance(found, dict) else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}


def cursor_title(chat: str) -> str:
    """The chat's title as Cursor shows it ("Ozer's PR review rules"), kept a minute."""
    at, title = _titles.get(chat, (0.0, ""))
    if time.monotonic() - at > 60:
        title = str(composer(chat).get("name") or "").strip()
        _titles[chat] = (time.monotonic(), title)
    return title


def chat_model(chat: str) -> str:
    """The model a Cursor editor chat uses ("grok-4.7"), from Cursor's own store, read only."""
    if not CHAT_ID.fullmatch(chat):
        return ""
    config = composer(chat).get("modelConfig")
    return str(config.get("modelName") or "") if isinstance(config, dict) else ""


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
        asked_words = re.sub(r"^\s*\[[^\]]*\]\s*(\(Henchline, said once:.*?How it all works: \S+\)\s*)?", "",
                             asked_words, flags=re.S)
        words = asked_words.split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return ""


def installed() -> bool:
    return any(DIR.glob("*.sock"))


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


last_why = ""  # what the bridge said when it said no ("not a chat id" from an older bridge, before a reload)


def ask_window(chat: str, request: dict) -> bool:
    """Sends one request to the one Cursor window that has the chat (see open_chat); True when it said yes."""
    global last_why
    last_why = ""
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
        said = json.loads(answer.decode() or "{}")
        last_why = str(said.get("why", ""))
        return bool(said.get("ok"))
    except (OSError, ValueError):
        return False


EFFORTS = ("xhigh", "high", "medium", "low")


def default_model() -> str:
    """Cursor's default model for new chats ("composer" in its settings), read only; "" when unknown."""
    try:
        with contextlib.closing(sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=1)) as db:
            for (raw,) in db.execute("select value from ItemTable where key like ?",
                                     ("%reactiveStorageServiceImpl.persistentStorage.applicationUser",)):
                config = ((json.loads(raw).get("aiSettings") or {}).get("modelConfig") or {}).get("composer") or {}
                if config.get("modelName"):
                    return str(config["modelName"])
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        pass
    return ""


def switch_model(chat: str, slug: str) -> str:
    """Sets a Cursor editor chat's model from a model's listed id ("grok-4.7-medium", "gpt-5.6-sol-xhigh-fast": the
    model, its effort, fast), through Cursor's own "Switch to Model Slug", then reads the chat's model back from
    Cursor's store. "" when it is on it; else why not."""
    base, params = slug, []
    if base.endswith("-fast"):
        base = base.removesuffix("-fast")
        params.append({"id": "fast", "value": "true"})
    for effort in EFFORTS:
        if base.endswith("-" + effort):
            base = base.removesuffix("-" + effort)
            params.append({"id": "reasoning_effort", "value": effort})
            break
    before = chat_model(chat)
    if not before:
        return "Cursor's record of that chat can't be read, so a switch couldn't be checked: not switched."
    keep = default_model()
    if not ask_window(chat, {"model": chat, "config": {"modelId": base, "params": params}, "keepDefault": keep}):
        if last_why == "not a chat id":
            return "Cursor runs an older Henchline bridge: Reload Window in Cursor, then try again."
        return f"Cursor didn't take it ({last_why or 'no answer'}): still on {before}."
    for _ in range(15):  # Cursor writes its store a moment later
        time.sleep(0.2)
        now = chat_model(chat)
        if now == base:
            return ""
    return f"Cursor still shows {chat_model(chat) or before} for that chat: it may not know {base}."


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


def chat_effort(chat: str) -> str:
    """The reasoning effort a Cursor editor chat's model runs at ("xhigh"), kept apart from the model in Cursor's
    store; "" when it has none."""
    config = composer(chat).get("modelConfig") if CHAT_ID.fullmatch(chat) else None
    for picked in (config or {}).get("selectedModels") or []:
        for parameter in (picked.get("parameters") or []) if isinstance(picked, dict) else []:
            if isinstance(parameter, dict) and parameter.get("id") == "reasoning_effort":
                return str(parameter.get("value") or "")
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
        asked_words = re.sub(r"^\s*\[[^\]]*\]\s*(\(Henchline, said once:.*?How it all works: \S+\)\s*)?", "",
                             asked_words, flags=re.S)
        words = asked_words.split()
        return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return ""


def installed() -> bool:
    return any(DIR.glob("*.sock"))


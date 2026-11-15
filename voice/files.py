"""Files between the user's phone (Henchline) and the agents, both ways.

From an agent: `handsfree send FILE … [WORDS]` puts a copy in OUTBOX/<secret>/, with meta.json saying who sent
it and when. The call server serves it at /files/<secret>/<name> (only your own devices reach the server, and
only who has the secret finds the file), shows it in the call's chat while a call is up, and otherwise
Henchline is pushed a notification for it. Copies go after KEEP_DAYS.

From the phone: Henchline's share target uploads each file to the call server, which saves it under
INBOX/<date>/ and then tells the agent the user is talking to where the files are.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from voice.state import INBOX, OUTBOX, read_json

KEEP_DAYS = 7  # an agent's file stays downloadable this long
SECRET = re.compile(r"[A-Za-z0-9_-]{16,64}")  # an outbox folder's name: token_urlsafe


def safe_name(name: str) -> str:
    """A file name as given, minus anything that could leave its folder or upset a terminal."""
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", Path(name.strip()).name).lstrip(".")
    return name[:180] or "file"


def unused(folder: Path, name: str) -> Path:
    """`name` in `folder`, or "name (2).ext" and so on when that is taken: nothing is overwritten."""
    path, stem, suffix = folder / name, Path(name).stem, Path(name).suffix
    n = 2
    while path.exists():
        path, n = folder / f"{stem} ({n}){suffix}", n + 1
    return path


def kind(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def size_text(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


# --- from an agent to the phone ---------------------------------------------------------------------------

def post(path: Path, words: str, agent: str, session: str) -> dict:
    """A copy of the file in the outbox (a hard link when it can be: same disk, no space), and its meta."""
    clean()
    secret = secrets.token_urlsafe(18)
    folder = OUTBOX / secret
    folder.mkdir(parents=True, mode=0o700)
    copy = folder / safe_name(path.name)
    try:
        os.link(path, copy)
    except OSError:  # another disk, or a file system without links
        shutil.copyfile(path, copy)
    meta = {"secret": secret, "name": copy.name, "type": kind(copy), "size": copy.stat().st_size,
            "text": words, "agent": agent, "session": session, "at": time.time()}
    (folder / "meta.json").write_text(json.dumps(meta))
    return meta


def link(meta: dict) -> str:
    """Where the call server serves it, relative to the call page's address."""
    return f"/files/{meta['secret']}/{meta['name']}"


def sent_since(since: float) -> list[dict]:
    """The files agents sent after `since`, oldest first."""
    metas = [read_json(meta) for meta in OUTBOX.glob("*/meta.json")]
    return sorted((m for m in metas if m and m.get("at", 0) > since), key=lambda m: m["at"])


def find(secret: str, name: str) -> Path | None:
    """The file behind /files/<secret>/<name>, or None: a wrong secret finds nothing, and nothing outside."""
    if not SECRET.fullmatch(secret) or safe_name(name) != name:
        return None
    path = OUTBOX / secret / name
    return path if name != "meta.json" and path.is_file() else None


def clean() -> None:
    """Agents' files older than KEEP_DAYS go."""
    for folder in OUTBOX.glob("*"):
        try:
            if time.time() - folder.stat().st_mtime > KEEP_DAYS * 86400:
                shutil.rmtree(folder)
        except OSError:
            pass


# --- from the phone to the agents -------------------------------------------------------------------------

def inbox_today() -> Path:
    folder = INBOX / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def received(name: str) -> Path | None:
    """A file the phone uploaded, by the name the server gave it (today's folder, or yesterday's after
    midnight); None for anything else, so the phone can only point an agent at its own uploads."""
    if safe_name(name) != name:
        return None
    for day in (time.time(), time.time() - 86400):
        path = INBOX / time.strftime("%Y-%m-%d", time.localtime(day)) / name
        if path.is_file():
            return path
    return None


def message(paths: list[Path], text: str, note: str) -> str:
    """What the agent is told: where each file is, its type and size, and anything the user wrote."""
    parts = [f"{p} ({kind(p)}, {size_text(p.stat().st_size)})" for p in paths]
    said = " ".join(" ".join(w.split()) for w in (note, text) if w.strip())  # one line: it is typed into a prompt
    if not parts:
        return f"The user shared this from their phone: {said}"
    files = parts[0] if len(parts) == 1 else "; ".join(parts)
    noun = "a file" if len(parts) == 1 else f"{len(parts)} files"
    return f"The user shared {noun} from their phone: {files}." + (f" They wrote: {said}" if said else "")

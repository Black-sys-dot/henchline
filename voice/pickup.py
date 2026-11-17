"""Has the agent taken in a message the phone sent? The phone's ticks (the user's wish, 2026-11-16): one when the
laptop typed it in (it may wait in the agent's queue while the agent works), two once the agent has picked it up,
blue once it answered. Claude Code and Codex log each message they take in; for Cursor it isn't known (None).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

KEY_WORDS = 12  # the first words of a message identify it in the log


def squash(words: str) -> str:
    """Words only, lower case, one space apart (punctuation and spacing differ between what was typed and logged)."""
    return " ".join(re.findall(r"[a-z0-9']+", words.lower()))


def key(words: str) -> str:
    """A message's first words: what identifies it in the log ("" for one with no Latin letters or digits)."""
    return " ".join(squash(words).split()[:KEY_WORDS])


def says(said: str, wanted: str) -> bool:
    """Those whole words, in that order: "ok" isn't found in "look"."""
    return bool(wanted) and f" {wanted} " in f" {squash(said)} "


def _when(entry: dict) -> float:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _backwards(path: Path, most: int = 4 * 1024 * 1024):
    """A log's JSON lines, newest first, read from its end a block at a time (usually only the last block is
    needed), up to `most` bytes."""
    with path.open("rb") as f:
        end = f.seek(0, 2)
        tail, read = b"", 0
        while end > 0 and read < most:
            size = min(256 * 1024, end)
            end -= size
            f.seek(end)
            block = f.read(size) + tail
            read += size
            lines = block.split(b"\n")
            tail = lines[0] if end > 0 else b""  # maybe cut: completed by the next block
            for raw in reversed(lines[1:] if end > 0 else lines):
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry


def claude_took(uuid: str, wanted: str, since: float, home: Path | None = None) -> float:
    """Claude Code took it in: a user turn with those words, or a queued message handed to it mid-turn. Not the
    "enqueue" of a message typed while it works: that one is still waiting. When it did (0.0: not yet)."""
    for log_file in ((home or Path.home()) / ".claude/projects").glob(f"*/{uuid}.jsonl"):
        for entry in _backwards(log_file):
            when = _when(entry)
            if when and when < since - 1:  # its own line can't be older than the moment it was typed
                break
            if entry.get("type") == "user":
                said = _text((entry.get("message") or {}).get("content"))
            elif entry.get("type") == "attachment" and (entry.get("attachment") or {}).get("type") == "queued_command":
                said = str(entry["attachment"].get("prompt", ""))
            else:
                continue
            if says(said, wanted):
                return when or time.time()
    return 0.0


def codex_took(folder_key: str, wanted: str, since: float, home: Path | None = None) -> float:
    """Codex took it in: a user message in the newest session log of that folder. When it did (0.0: not yet)."""
    root = (home or Path.home()) / ".codex/sessions"
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:  # gone since it was listed
            return 0.0

    logs = sorted(root.glob("*/*/*/rollout-*.jsonl"), key=mtime, reverse=True)[:20]
    for log_file in logs:
        try:
            with log_file.open("rb") as f:
                meta = json.loads(f.readline(1024 * 1024) or b"{}")
        except (OSError, ValueError):
            continue
        cwd = str((meta.get("payload") or {}).get("cwd", "")) if isinstance(meta, dict) else ""
        if not cwd or re.sub(r"[^A-Za-z0-9_.-]+", "_", cwd)[-120:] != folder_key[-120:]:
            continue
        for entry in _backwards(log_file):
            when = _when(entry)
            if when and when < since - 1:
                break
            payload = entry.get("payload") or {}
            if entry.get("type") == "event_msg" and payload.get("type") == "user_message":
                if says(str(payload.get("message", "")), wanted):
                    return when or time.time()
        return 0.0
    return 0.0


def took(agent: str, session: str, words: str, since: float | None = None) -> float | None:
    """When that agent took in the message (0.0: it still waits); None when that agent can't say."""
    wanted = key(words)
    if not wanted:  # nothing to know it by (only emoji, another script): can't say
        return None
    since = since if since is not None else time.time() - 3600
    if agent == "claude":
        return claude_took(session.removeprefix("claude-"), wanted, since)
    if agent == "codex":
        return codex_took(session.removeprefix("codex-"), wanted, since)
    return None

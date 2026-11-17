"""Scheduled messages: words typed into an agent at a set time (the user's idea, 2026-11-17).

`handsfree remind 08:30 "…"` (or the phone's /later) keeps a reminder here; the call server, always running,
delivers each one when it is due through the same path as a message from the phone (routing.deliver: paste, a
pause, then Enter), so it reaches Claude, Cursor and Codex alike. A one-off timer typing into a tmux pane with its
own Enter failed for Cursor (the wake-up call, 2026-11-17: the line sat unsent for an hour).

A reminder can keep the laptop awake until it is delivered (keep_awake): the server then holds a sleep inhibitor.
"""

from __future__ import annotations

import fcntl
import json
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from voice.state import STATE

FILE = STATE / "reminders.json"
LOCK = STATE / "reminders.lock"
RETRY_FOR = 3600  # seconds a due reminder keeps being tried (the agent not reachable yet), then it is given up
KEEP = 7 * 86400  # delivered or given-up reminders stay listed this long


@contextmanager
def _locked():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read() -> list[dict]:
    try:
        data = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _write(items: list[dict]) -> None:
    now = time.time()
    items = [r for r in items if r.get("state") == "pending" or now - r.get("at", now) < KEEP]
    aside = FILE.with_suffix(".new")
    aside.write_text(json.dumps(items, indent=1))
    aside.replace(FILE)


def when(text: str, now: datetime | None = None) -> float:
    """"08:30" (the next one), "tomorrow 08:30", "2026-11-18 08:30", "in 20m" / "in 2h" → seconds since the epoch."""
    now = now or datetime.now()
    text = text.strip().lower()
    later = re.fullmatch(r"in\s+(\d+)\s*(s|sec|m|min|h|hr|hours?|minutes?|seconds?)", text)
    if later:
        seconds = int(later.group(1)) * {"s": 1, "m": 60, "h": 3600}[later.group(2)[0]]
        return now.timestamp() + seconds  # elapsed time: right across a clock change too
    day = re.fullmatch(r"(?:(tomorrow|today)\s+|(\d{4}-\d{2}-\d{2})\s+)?(\d{1,2}):(\d{2})", text)
    if not day:
        raise ValueError(f"not a time: {text!r} (try 08:30, tomorrow 08:30, 2026-11-18 08:30, in 20m)")
    hour, minute = int(day.group(3)), int(day.group(4))
    if day.group(2):
        base = datetime.strptime(day.group(2), "%Y-%m-%d")
    else:
        base = now + timedelta(days=1) if day.group(1) == "tomorrow" else now
    at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not day.group(1) and not day.group(2) and at <= now:  # a bare time already past today: tomorrow's
        at += timedelta(days=1)
    elif at <= now:
        raise ValueError(f"{text!r} has already passed")
    return at.timestamp()


def add(at: float, session: str, words: str, by: str = "", keep_awake: bool = False) -> dict:
    item = {"id": secrets.token_hex(4), "at": at, "session": session, "words": words, "by": by,
            "keep_awake": keep_awake, "state": "pending", "made": time.time()}
    with _locked():
        _write(_read() + [item])
    return item


def listed(session: str = "") -> list[dict]:
    return sorted((r for r in _read() if not session or r.get("session") == session), key=lambda r: r.get("at", 0))


def cancel(rid: str) -> bool:
    with _locked():
        items = _read()
        hit = [r for r in items if r.get("id") == rid and r.get("state") == "pending"]
        for r in hit:
            r["state"] = "cancelled"
        _write(items)
    return bool(hit)


BACKOFF = [60, 120, 300, 600]  # seconds before the next try when one fails, then every 10 minutes


def due(now: float | None = None) -> list[dict]:
    now = now or time.time()
    return [r for r in _read() if r.get("state") == "pending" and r.get("at", 0) <= now
            and r.get("next", 0) <= now]


def later(rid: str) -> None:
    """A try that failed (the agent not reachable yet): the next one after a backoff, not every 10 s."""
    with _locked():
        items = _read()
        for r in items:
            if r.get("id") == rid:
                tries = r.get("tries", 0)
                r["tries"], r["next"] = tries + 1, time.time() + BACKOFF[min(tries, len(BACKOFF) - 1)]
        _write(items)


def settle(rid: str, state: str, note: str = "") -> None:
    """A due reminder delivered ("delivered"), or given up ("failed", with why)."""
    with _locked():
        items = _read()
        for r in items:
            if r.get("id") == rid:
                r["state"], r["note"], r["settled"] = state, note, time.time()
        _write(items)


def keep_awake_until() -> float:
    """The latest due time among pending reminders that keep the laptop awake (0: none)."""
    return max((r.get("at", 0) + 300 for r in _read() if r.get("state") == "pending" and r.get("keep_awake")),
               default=0.0)

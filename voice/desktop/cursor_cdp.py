"""Words into a Cursor IDE chat through Cursor's own debugging port: no keys on the desktop, no window raised or
focused, and it works with the screen locked (2026-11-16, Cursor 3.18).

Cursor must run with --remote-debugging-port=PORT (localhost; install.sh agents adds it to Cursor's launcher).
Each chat's input box sits under an element carrying its chat id (data-composer-id), so the box is found by id,
given the words (Input.insertText) and sent with a real Enter (Input.dispatchKeyEvent): Cursor submits a follow-up
only on its box's Enter, and no command an extension can reach does it. The chat must be open in its window first
(the bridge's "open"), or its box isn't on the page. Focus inside Cursor moves to the box for the moment it takes
and is then put back where it was.

The port is a TCP socket on 127.0.0.1: while it is open, any program or user on this computer can drive Cursor
through it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import urllib.request

PORT = int(os.environ.get("HENCHLINE_CURSOR_DEBUG_PORT", "9333"))
CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SUBMIT_WAIT = 5.0  # seconds for the box to empty after Enter: Cursor took it
WHOLE_SEND = 20.0  # seconds for all of it, connecting included


def page_of(where: str) -> str:
    """The debugging address of the Cursor window for that workspace (its title, as cursor_window reads it), or ""
    when Cursor runs without the port or has no such window."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as answer:
            targets = json.load(answer)
    except (OSError, ValueError):
        return ""
    found = [t.get("webSocketDebuggerUrl", "") for t in targets if isinstance(t, dict) and t.get("type") == "page"
             and (t.get("title") == f"{where} - Cursor" or str(t.get("title", "")).endswith(f" - {where} - Cursor"))]
    return found[0] if len(found) == 1 else ""


def available(where: str) -> bool:
    """The port answers for that window, and the websocket client is installed (it comes with the call piece and
    the hands-free one; without it, Cursor chats go the keyed way)."""
    return importlib.util.find_spec("aiohttp") is not None and bool(page_of(where))


def same_words(a: str, b: str) -> bool:
    """Equal but for whitespace: the box reads back paragraphs as blank lines and may hold non-breaking spaces."""
    return " ".join(a.split()) == " ".join(b.split())


def _box(chat: str) -> str:
    return f'document.querySelector(\'[data-composer-id="{chat}"] .aislash-editor-input\')'


def _focus(chat: str) -> str:
    """JS: note where focus was, focus that chat's box; its text, or null when the box isn't on the page or won't
    take focus."""
    return (f"(() => {{ const box = {_box(chat)}; if (!box) return null;"
            " if (document.activeElement !== box) window.__henchlineFocus = document.activeElement;"
            " box.focus(); return document.activeElement === box ? box.innerText : null; })()")


_REFOCUS = ("(() => { const was = window.__henchlineFocus; window.__henchlineFocus = undefined;"
            " if (was && was.isConnected && was.focus) was.focus(); return true; })()")


def _text(chat: str) -> str:
    """JS: the box's text, or "" when it is gone (Cursor redrew the chat after taking the words)."""
    return f"(() => {{ const box = {_box(chat)}; return box ? box.innerText : ''; }})()"


def _key(key: str, code: int, modifiers: int = 0, text: str = "") -> list[tuple[str, dict]]:
    down = {"type": "rawKeyDown", "key": key, "code": key, "windowsVirtualKeyCode": code, "modifiers": modifiers}
    calls = [("Input.dispatchKeyEvent", down)]
    if text:
        calls.append(("Input.dispatchKeyEvent", {**down, "type": "char", "text": text}))
    calls.append(("Input.dispatchKeyEvent", {**down, "type": "keyUp"}))
    return calls


class _Page:
    def __init__(self, ws) -> None:
        self.ws, self.n = ws, 0

    async def call(self, method: str, params: dict) -> dict:
        self.n += 1
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        while True:
            message = await self.ws.receive_json(timeout=5)
            if message.get("id") == self.n:
                if "error" in message:
                    raise OSError(str(message["error"]))
                return message.get("result") or {}

    async def js(self, expression: str):
        result = await self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (result.get("result") or {}).get("value")

    async def keys(self, calls: list[tuple[str, dict]]) -> None:
        for method, params in calls:
            await self.call(method, params)

    async def put_back(self, chat: str, before: str) -> None:
        """The box as it was before Henchline wrote in it: a refused message must not ride along with the next."""
        if await self.js(_focus(chat)) is None:
            return
        await self.keys(_key("a", 65, modifiers=2) + _key("Backspace", 8))  # ctrl+a, Backspace
        if before.strip():
            await self.call("Input.insertText", {"text": before.strip()})


async def _send(url: str, chat: str, message: str) -> str:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=WHOLE_SEND)) as session:
        async with session.ws_connect(url, max_msg_size=0) as ws:
            page = _Page(ws)
            before = await page.js(_focus(chat))
            if before is None:
                return "that chat's box isn't open in Cursor"
            try:
                # A draft of the user's stays in front, a space apart, as a paste at its end always did
                words = message.strip()
                if before.strip():
                    await page.keys(_key("End", 35, modifiers=2))  # ctrl+End
                    words = " " + words
                await page.call("Input.insertText", {"text": words})
                held = await page.js(_focus(chat))
                if held is None or not same_words(held, before + words):
                    await page.put_back(chat, before)
                    return "the words didn't land in that chat's box, so nothing was sent"
                await page.keys(_key("Enter", 13, text="\r"))
                for _ in range(int(SUBMIT_WAIT / 0.1)):
                    await asyncio.sleep(0.1)
                    if not str(await page.js(_text(chat)) or "").strip():  # emptied (or redrawn): Cursor took it
                        return ""
                await page.put_back(chat, before)
                return "Cursor didn't take the Enter, so nothing was sent"
            finally:
                await page.js(_REFOCUS)


def send(where: str, chat: str, message: str) -> str:
    """Types the words into that chat's box and sends them, through the debugging port: "" when Cursor took them,
    else why not (and then the box is as it was). The chat must already be open in its window (the bridge's
    open_chat). Never raises: a delivery that fails says why."""
    if not CHAT_ID.fullmatch(chat):
        return "not a chat id"
    url = page_of(where)
    if not url:
        return "Cursor's debugging port isn't open for that window"
    try:
        return asyncio.run(asyncio.wait_for(_send(url, chat, message), WHOLE_SEND))
    except Exception as e:  # a page closed or reloaded mid-send, a timeout, a closed socket: refused, never raised
        return f"Cursor's debugging port: {str(e) or type(e).__name__}"

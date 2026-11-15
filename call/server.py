#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""A phone call with the agents: a web page that streams the phone's mic in and talkback out.

    call/server.py [--port 8790] [--host tailscale|ADDRESS] [--cert PEM --key PEM]

It listens on this machine's Tailscale address, with its own certificate
(install.sh call makes one; the phone accepts it once), so only your own
devices reach it; or on 127.0.0.1 behind `tailscale serve`, once Tailscale
lets this user configure it. Open the https address on the phone, tap Call. While a call is up, two virtual sinks exist: the
phone's mic is played into PHONE_MIC, whose monitor hands-free listens to,
and talkback plays into PHONE_SPEAKER, whose monitor is streamed back to the
phone. The phone's browser cancels its own echo. One call at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import ssl
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "handsfree")]  # voice/, and hands-free's routing
import routing  # noqa: E402
from routing import Heard  # noqa: E402
from voice.desktop import current as desktop  # noqa: E402
from voice.state import (BY_HANDSFREE, CALL, CALL_BUTTON_ONLY, CALL_PORT, CALL_PUSH, CALL_TLS, HANDSFREE_MODE,  # noqa: E402
                         HANDSFREE_ON, PHONE_MIC, PHONE_SPEAKER, Phase, Playing, last_speaker, log, phase_now,
                         recent_speakers, write_json)
from voice.state import HANDSFREE_LOG  # noqa: E402  (calls are logged with hands-free, which they drive)

MIC_RATE = 16000  # what the page sends: what hands-free listens at
SPEAKER_RATE = 24000  # what the page plays: Kokoro's own rate
CHUNK = SPEAKER_RATE // 50 * 2  # bytes of s16 mono sent to the phone at a time: 20 ms


STATUS_EVERY = 0.2  # seconds between looks at what hands-free and talkback are doing
AGENTS_EVERY = 3.0  # seconds between looks at which agents can be switched to


class Status:
    """What the phone shows: the talk button's colour, a status line, and the chat.

    Read from the shared state (hands-free's mode, the phase, the clip playing)
    and from the hands-free log's lines since the call began, which say who a
    message went to, never what it said. The agents' own words become chat.
    """

    def __init__(self) -> None:
        self.log = HANDSFREE_LOG.open()
        self.log.seek(0, 2)
        self.waiting_for = ""  # the agent a message went to, until it starts speaking
        self.clip = ""  # the clip last put in the chat

    def events(self) -> list[dict]:
        """New chat lines: what an agent started saying, and that a spoken message went out."""
        lines = []
        for line in self.log.readlines():
            if " sent to " in line:
                self.waiting_for = line.split(" sent to ", 1)[1].split()[0].capitalize()
                lines.append({"chat": {"from": "you", "text": f"(spoken, sent to {self.waiting_for})"}})
        playing = Playing.now()
        if playing and playing.clip != self.clip:
            self.clip = playing.clip
            lines.append({"chat": {"from": playing.agent.capitalize(), "text": playing.text}})
        return lines

    def now(self) -> dict:
        mode = HANDSFREE_MODE.read_text().strip() if HANDSFREE_MODE.exists() else "waiting"
        playing = Playing.now()
        if playing:
            self.waiting_for = ""
            return {"mode": mode, "state": f"{playing.agent.capitalize()} is speaking"}
        phase, by = phase_now()
        if by == BY_HANDSFREE and phase is Phase.RECORDING:
            return {"mode": mode, "state": "Hearing you…"}
        if by == BY_HANDSFREE and phase is Phase.TRANSCRIBING:
            return {"mode": mode, "state": "Writing it down…"}
        if self.waiting_for:
            return {"mode": mode, "state": f"Sent to {self.waiting_for}: waiting for the answer…"}
        if CALL_BUTTON_ONLY.exists():
            return {"mode": mode, "state": "Open line off: hold the button to talk"}
        return {"mode": mode, "state": "Listening"}

    def close(self) -> None:
        self.log.close()


def pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True).stdout.strip()


class Devices:
    """The two virtual sinks of a call, there only while it lasts."""

    def __init__(self) -> None:
        self.modules: list[str] = []

    def __enter__(self) -> Devices:
        for name, what in ((PHONE_MIC, "Phone mic (call)"), (PHONE_SPEAKER, "Phone speaker (call)")):
            self.modules.append(pactl("load-module", "module-null-sink", f"sink_name={name}",
                                      f"sink_properties=device.description='{what}'",
                                      "channels=1", f"rate={MIC_RATE if name == PHONE_MIC else SPEAKER_RATE}"))
        return self

    def __exit__(self, *_) -> None:
        for module in self.modules:
            if module.isdigit():
                pactl("unload-module", module)


async def from_page(message: dict) -> None:
    """The talk button held or let go, or a typed chat message for the agent that spoke last."""
    if "push" in message:
        CALL_PUSH.touch() if message["push"] else CALL_PUSH.unlink(missing_ok=True)
    elif "button_only" in message:
        CALL_BUTTON_ONLY.touch() if message["button_only"] else CALL_BUTTON_ONLY.unlink(missing_ok=True)
    elif "switch" in message:
        await asyncio.get_running_loop().run_in_executor(None, switch, message["switch"])
    elif words := message.get("chat", "").strip():
        await asyncio.get_running_loop().run_in_executor(None, typed, words)


def agents() -> dict:
    """The agents a call can switch to: heard lately and still running, and the one replies go to now."""
    current, locked = last_speaker(), desktop().locked()
    return {"agents": [{"session": s.session, "name": s.spoken() + (" (laptop locked)" if s.in_editor and locked else "")}
                       for s in recent_speakers() if routing.reachable(s)],
            "current": current.session if current else ""}


def switch(session: str) -> None:
    """The page's agent menu: replies go to that agent now, announced as when spoken."""
    target = next((s for s in recent_speakers() if s.session == session), None)
    if target and routing.reachable(target):
        routing.switch_to(target)
        routing.say(f"Now talking to {target.spoken()}.")


def typed(words: str) -> None:
    """A typed line: a switch ("switch me to Cursor", "who am I talking to") works as when spoken."""
    if routing.meaning(words) is routing.Meaning.ROUTING:
        routing.route(words, None)
    else:
        routing.deliver(words, Heard.TYPED)


async def call(request: web.Request) -> web.WebSocketResponse:
    # Browsers let any web page open a WebSocket to any address: only the call page itself may, or a page
    # on some other site, open on the phone, could type into the agents' terminals
    if request.headers.get("Origin") != f"https://{request.host}":
        raise web.HTTPForbidden(text="only the call page may connect")
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    if CALL.exists():
        await ws.close(message=b"busy: another call is up")
        return ws
    with Devices():
        mic = await asyncio.create_subprocess_exec(
            "pacat", "--playback", "--raw", f"--device={PHONE_MIC}", "--format=s16le", f"--rate={MIC_RATE}",
            "--channels=1", "--latency-msec=60", "--client-name=phone-call", stdin=asyncio.subprocess.PIPE)
        speaker = await asyncio.create_subprocess_exec(
            "parec", "--raw", f"--device={PHONE_SPEAKER}.monitor", "--format=s16le", f"--rate={SPEAKER_RATE}",
            "--channels=1", "--latency-msec=40", "--client-name=phone-call", stdout=asyncio.subprocess.PIPE)
        write_json(CALL, {"from": request.remote or ""})
        HANDSFREE_ON.touch()
        subprocess.run(["systemctl", "--user", "start", "handsfree.service"], check=False)
        log(HANDSFREE_LOG, "phone call started")

        async def to_phone() -> None:
            while not ws.closed:
                data = await speaker.stdout.read(CHUNK)
                if not data:
                    return
                await ws.send_bytes(data)

        async def show_status() -> None:
            status, shown = Status(), None
            try:
                listed, shown_agents = 0.0, None
                while not ws.closed:
                    if time.monotonic() - listed > AGENTS_EVERY:  # the menu: who is reachable, who is current
                        listed = time.monotonic()
                        now_agents = await asyncio.get_running_loop().run_in_executor(None, agents)
                        if now_agents != shown_agents:
                            await ws.send_json(now_agents)
                            shown_agents = now_agents
                    for event in status.events():
                        await ws.send_json(event)
                    now = status.now()
                    if now != shown:
                        await ws.send_json(now)
                        shown = now
                    await asyncio.sleep(STATUS_EVERY)
            finally:
                status.close()

        sender = asyncio.create_task(to_phone())
        shower = asyncio.create_task(show_status())
        heard = 0  # bytes of the phone's mic, for the log
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    heard += len(msg.data)
                    mic.stdin.write(msg.data)
                    await mic.stdin.drain()
                elif msg.type == WSMsgType.TEXT:
                    await from_page(msg.json())
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            CALL_PUSH.unlink(missing_ok=True)
            CALL_BUTTON_ONLY.unlink(missing_ok=True)  # the next call starts with an open line again
            sender.cancel()
            shower.cancel()
            CALL.unlink(missing_ok=True)  # hands-free and talkback go back to the laptop first
            for proc in (mic, speaker):
                proc.terminate()
                await proc.wait()
            log(HANDSFREE_LOG, f"phone call ended ({heard / 2 / MIC_RATE:.0f} s of the phone's mic arrived)")
    return ws


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=CALL_PORT)
    parser.add_argument("--cert", type=Path, help="serve https with this certificate (and --key): a self-signed one\n"
                        "works when `tailscale serve` is not available; the phone accepts it once")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--host", default="tailscale",
                        help="tailscale (default): this machine's Tailscale address, so only your own devices "
                             "reach it; 127.0.0.1 behind `tailscale serve`")
    args = parser.parse_args()
    CALL.unlink(missing_ok=True)  # a leftover from a crash
    app = web.Application()
    app.router.add_get("/", lambda _: web.FileResponse(HERE / "page.html"))
    app.router.add_get("/call", call)
    if args.host == "tailscale":
        args.host = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True).stdout.split()[0]
    if args.cert is None and (CALL_TLS / "cert.pem").exists():
        args.cert, args.key = CALL_TLS / "cert.pem", CALL_TLS / "key.pem"
    tls = None
    if args.cert:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(args.cert, args.key)
    web.run_app(app, host=args.host, port=args.port, ssl_context=tls, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

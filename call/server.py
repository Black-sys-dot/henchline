#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""A phone call with the agents: a web page that streams the phone's mic in and talkback out.

    call/server.py [--port 8790] [--host ADDRESS]

Serve it over Tailscale (`tailscale serve --bg 8790`), open the https address
on the phone and tap Call. While a call is up, two virtual sinks exist: the
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
from pathlib import Path

from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # voice/
from voice.state import (BY_HANDSFREE, CALL, HANDSFREE_ON, PHONE_MIC, PHONE_SPEAKER, Phase, Playing,  # noqa: E402
                         log, phase_now, write_json)
from voice.state import HANDSFREE_LOG  # noqa: E402  (calls are logged with hands-free, which they drive)

MIC_RATE = 16000  # what the page sends: what hands-free listens at
SPEAKER_RATE = 24000  # what the page plays: Kokoro's own rate
CHUNK = SPEAKER_RATE // 50 * 2  # bytes of s16 mono sent to the phone at a time: 20 ms


STATUS_EVERY = 0.2  # seconds between looks at what hands-free and talkback are doing


class Status:
    """What the phone shows: hearing you, writing it down, waiting for the agent, or the agent's words.

    Read from the shared state (the phase, the clip playing) and from the
    hands-free log's lines since the call began (which say who a message
    went to, never what it said). The agent's own words are its captions.
    """

    def __init__(self) -> None:
        self.log = HANDSFREE_LOG.open()
        self.log.seek(0, 2)
        self.waiting_for = ""  # the agent a message went to, until it starts speaking

    def now(self) -> dict:
        for line in self.log.readlines():
            if " sent to " in line:
                self.waiting_for = line.split(" sent to ", 1)[1].split()[0].capitalize()
        playing = Playing.now()
        if playing:
            self.waiting_for = ""
            return {"state": f"{playing.agent.capitalize()} is speaking", "caption": playing.text}
        phase, by = phase_now()
        if by == BY_HANDSFREE and phase is Phase.RECORDING:
            return {"state": "Hearing you…"}
        if by == BY_HANDSFREE and phase is Phase.TRANSCRIBING:
            return {"state": "Writing it down…"}
        if self.waiting_for:
            return {"state": f"Sent to {self.waiting_for}: waiting for the answer…"}
        return {"state": "Listening"}

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


async def call(request: web.Request) -> web.WebSocketResponse:
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
                while not ws.closed:
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
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
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
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--cert", type=Path, help="serve https with this certificate (and --key): a self-signed one\n"
                        "works when `tailscale serve` is not available; the phone accepts it once")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--host", default="127.0.0.1",
                        help="127.0.0.1 behind `tailscale serve`; or this machine's Tailscale address, reached as "
                             "http:// from a phone whose browser treats that address as secure")
    args = parser.parse_args()
    CALL.unlink(missing_ok=True)  # a leftover from a crash
    app = web.Application()
    app.router.add_get("/", lambda _: web.FileResponse(HERE / "page.html"))
    app.router.add_get("/call", call)
    tls = None
    if args.cert:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(args.cert, args.key)
    web.run_app(app, host=args.host, port=args.port, ssl_context=tls, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

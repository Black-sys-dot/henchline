#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""A phone call with the agents: a web page that streams the phone's mic in and talkback out.

    call/server.py [--port 8790]

Serve it over Tailscale (`tailscale serve --bg 8790`), open the https address
on the phone and tap Call. While a call is up, two virtual sinks exist: the
phone's mic is played into PHONE_MIC, whose monitor hands-free listens to,
and talkback plays into PHONE_SPEAKER, whose monitor is streamed back to the
phone. The phone's browser cancels its own echo. One call at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # voice/
from voice.state import CALL, HANDSFREE_ON, PHONE_MIC, PHONE_SPEAKER, log, write_json  # noqa: E402
from voice.state import HANDSFREE_LOG as CALL_LOG  # noqa: E402

MIC_RATE = 16000  # what the page sends: what hands-free listens at
SPEAKER_RATE = 24000  # what the page plays: Kokoro's own rate
CHUNK = SPEAKER_RATE // 50 * 2  # bytes of s16 mono sent to the phone at a time: 20 ms


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
        log(CALL_LOG, "phone call started")

        async def to_phone() -> None:
            while not ws.closed:
                data = await speaker.stdout.read(CHUNK)
                if not data:
                    return
                await ws.send_bytes(data)

        sender = asyncio.create_task(to_phone())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    mic.stdin.write(msg.data)
                    await mic.stdin.drain()
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            sender.cancel()
            CALL.unlink(missing_ok=True)  # hands-free and talkback go back to the laptop first
            for proc in (mic, speaker):
                proc.terminate()
                await proc.wait()
            log(CALL_LOG, "phone call ended")
    return ws


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    CALL.unlink(missing_ok=True)  # a leftover from a crash
    app = web.Application()
    app.router.add_get("/", lambda _: web.FileResponse(HERE / "page.html"))
    app.router.add_get("/call", call)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)  # Tailscale serve is the way in
    return 0


if __name__ == "__main__":
    sys.exit(main())

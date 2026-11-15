#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Hands-free conversation with the talkback agents: no keys.

    handsfree on | off | status        (handsfree run is the service itself)
    handsfree switch AGENT [WORKSPACE] hand the user over to that agent, announced
    handsfree listen [SECONDS]         keep listening that long (default 180)
    handsfree close                    stop listening until the wake word
    handsfree hangup                   end the phone call (call/), once talkback has finished speaking
    handsfree ring [--now] [--no-wait] [WHY]
                                       ring the user's phone (Henchline full-screen, else an ntfy notification
                                       whose Answer opens Henchline), and wait for the answer: exit 0 answered,
                                       2 no answer (a missed call with WHY is left in ntfy), 1 not rung.
                                       The ntfy topic is read from ~/.config/ptt/ntfy-topic

The built-in mic runs through the echo canceller (sensing.py). The listener
(listener.py) records a turn when the user talks after a clip, over a clip,
after the wake word or an earbud tap, and routing.py types it, tagged with
how it was heard, into the agent that spoke last.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]  # here, and the repository for voice/

import routing  # noqa: E402
import tray  # noqa: E402
from listener import Listener  # noqa: E402
from sensing import EC_UNIT, start_canceller  # noqa: E402
from voice import whisper  # noqa: E402
from voice.state import (ANNOUNCE, BY_HANDSFREE, CALL_HANGUP, CALL_RING, CLOSE, HANDSFREE_LOG,  # noqa: E402
                         HANDSFREE_ON, LISTEN, in_call, last_speaker, log, release_phase,
                         turn_on_handsfree)
from voice.state import HANDSFREE_UNIT as UNIT  # noqa: E402


RING_TOPIC = Path.home() / ".config/ptt/ntfy-topic"  # the user's own ntfy topic: never printed or logged
RING_SERVER = "https://ntfy.sh"
RING_EVERY = 120  # seconds: an agent can't keep ringing
RING_WAIT = 60  # seconds a ring waits for the answer: it rings for 30, and on a locked phone Answer connects
               # only once it is unlocked
RANG = Path.home() / ".local/state/ptt/call.rang"  # when the user was last rung


def ring(why: str, now: bool = False, wait: bool = True) -> int:
    """An agent wants to talk: the phone rings (Henchline full-screen, or an ntfy notification whose Answer
    opens Henchline's call). Then it waits for the answer: 0 answered, 2 no answer (a "missed call" with
    the reason is left in ntfy, so the user sees what it wanted), 1 not rung."""
    if in_call():
        print("a phone call is up already")
        return 1
    if not now and RANG.exists() and time.time() - RANG.stat().st_mtime < RING_EVERY:
        print(f"rang less than {RING_EVERY} s ago; not again yet (--now if the user asked for it)")
        return 1
    try:  # only for the ntfy fallback and the missed call: Henchline's push address works without it
        topic = RING_TOPIC.read_text().strip()
    except OSError:
        topic = ""
    speaker = last_speaker()
    who = (speaker.agent.capitalize() if speaker else "Your crew").encode("ascii", "ignore").decode()  # headers
    if not (ring_henchline(speaker.agent if speaker else "handsfree", why, who)
            or topic and ntfy(topic, f"{who} is calling", why or "Tap Answer to talk.",
                    {"Priority": "high", "Tags": "telephone", "Click": "henchline://call",
                     "Actions": "view, Answer, henchline://call, clear=true"})):
        print("couldn't ring" + ("" if topic else f": no Henchline push address, and no ntfy topic in {RING_TOPIC}"))
        return 1
    RANG.parent.mkdir(parents=True, exist_ok=True)
    RANG.touch()
    log(HANDSFREE_LOG, f"rang the user ({who})")
    print("rang", flush=True)
    if not wait:
        return 0
    deadline = time.time() + RING_WAIT
    while time.time() < deadline:
        if in_call():
            log(HANDSFREE_LOG, f"the user answered {who}'s call")
            print("answered")
            return 0
        time.sleep(1)
    if topic:
        ntfy(topic, f"Missed call from {who}", why or "They wanted to talk.", {"Tags": "telephone_receiver"})
    log(HANDSFREE_LOG, f"no answer to {who}'s call: left a missed call")
    print("no answer (a missed call was left in ntfy)")
    return 2


def ring_henchline(agent: str, why: str, who: str) -> bool:
    """Henchline's own push address, if it gave one: a full-screen call with the agent's henchman and tone."""
    if not CALL_RING.exists():
        return False
    body = json.dumps({"agent": agent, "why": why}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(CALL_RING.read_text().strip(), data=body,
                                                      headers={"Content-Type": "application/json"}),
                               timeout=10).close()
        return True
    except (OSError, ValueError) as exc:
        print(f"Henchline's push address failed ({exc.__class__.__name__}): an ntfy notification instead")
        return False


def ntfy(topic: str, title: str, message: str, headers: dict[str, str]) -> bool:
    """A notification on the user's phone; the topic is never printed."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{RING_SERVER}/{topic}", data=message.encode(),
                                                      headers={"Title": title, **headers}), timeout=10).close()
        return True
    except (OSError, ValueError) as exc:
        print(f"ntfy failed: {exc.__class__.__name__}")
        return False


def run() -> int:
    cfg = whisper.Config.load(HANDSFREE_LOG)
    canceller_since = start_canceller()
    transcriber = whisper.Transcriber(cfg, HANDSFREE_LOG)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    listener = Listener(cfg, transcriber, stop, canceller_since)
    threading.Thread(target=listener.run, daemon=True).start()
    log(HANDSFREE_LOG, "on")
    if ANNOUNCE.exists():  # only when the user turned it on, not on every restart
        ANNOUNCE.unlink(missing_ok=True)
        routing.say("Hands-free is on.")
    try:
        tray.show(listener, stop)
    finally:
        stop.set()
        release_phase(BY_HANDSFREE)
        HANDSFREE_ON.unlink(missing_ok=True)
        log(HANDSFREE_LOG, "off")
    return 1 if listener.crashed else 0  # non-zero: systemd starts it again


def switch(words: list[str]) -> int:
    if not words:
        sys.exit("usage: handsfree switch claude|cursor|codex [workspace words]")
    target = routing.find(words[0], " ".join(words[1:]) or None)
    if target is None:
        print(f"no {words[0]} has spoken through talkback in the last day")
        return 1
    routing.switch_to(target)
    routing.say(f"Now talking to {target.spoken()}.")
    print(f"now talking to {target.spoken()}")
    return 0


def main() -> int:
    command, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("status", [])
    if command == "type-into-cursor":  # internal: routing.deliver runs it in a child process
        return 0 if routing.type_into_cursor(*args) else 1
    if command == "run":
        return run()
    if command == "on":
        return turn_on_handsfree()
    if command == "off":
        HANDSFREE_ON.unlink(missing_ok=True)
        return subprocess.call(["systemctl", "--user", "stop", UNIT, EC_UNIT])
    if command == "switch":
        return switch(args)
    if command == "listen":
        LISTEN.write_text(str(time.time() + float(args[0] if args else 180)))
        return 0
    if command == "ring":
        now = "--now" in args  # the user asked for this ring: past the limit meant for agents
        wait = "--no-wait" not in args
        return ring(" ".join(a for a in args if a not in ("--now", "--no-wait")), now, wait)
    if command == "hangup":
        if not in_call():
            print("no phone call is up")
            return 1
        CALL_HANGUP.touch()
        return 0
    if command == "close":
        CLOSE.touch()
        return 0
    active = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True).stdout
    print(f"hands-free {'on' if active.strip() == 'active' else 'off'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

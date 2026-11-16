#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/henchline/venv/bin/python3" -E -s "$0" "$@"'
"""A phone call with the agents: a web page that streams the phone's mic in and talkback out.

    call/server.py [--port 8790] [--host serve|tailscale|ADDRESS] [--cert PEM --key PEM]

It listens on this machine's Tailscale address, so your own devices (and
programs on this machine) reach it and nothing else does. In front of it,
`tailscale serve` gives https://<this machine's Tailscale name>/ with a real
certificate, once Tailscale lets this user configure it (`sudo tailscale set
--operator=$USER`, once); until then the page has its own certificate
(install.sh call makes one; the phone accepts it once). Open the address and tap
Call. While a call is up, two virtual sinks exist: the phone's mic is played
into PHONE_MIC, whose monitor hands-free listens to, and talkback plays into
PHONE_SPEAKER, whose monitor is streamed back to the phone. The phone's
browser cancels its own echo. One call at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "handsfree/core")]  # voice/, and hands-free's routing
import routing  # noqa: E402
from routing import Heard  # noqa: E402
from voice import files  # noqa: E402
from voice.desktop import current as desktop, tmux  # noqa: E402
from voice.state import (BY_HANDSFREE, CALL, CALL_BUTTON_ONLY, CALL_HANGUP, CALL_MIRROR, CALL_ORIGIN,  # noqa: E402
                         SPAWNED, TEXTS, VOICE_NOTE_STALE, VOICE_NOTES,
                         CALL_PORT, CALL_PUSH,
                         CALL_RING, CALL_RINGER, CALL_TLS, HANDSFREE_LOG, HANDSFREE_MODE,
                         PHONE_MIC, PHONE_SPEAKER, PHONE_SPEAKER_MONITOR, Phase, Playing, SENT, SPEAKERS,
                         SPOOL, Sent, Speaker, in_call, last_speaker, log, phase_now, read_json,
                         recent_speakers, turn_on_handsfree, write_json)

MIC_RATE = 16000  # what the page sends: what hands-free listens at
SPEAKER_RATE = 24000  # what the page plays: Kokoro's own rate
CHUNK = SPEAKER_RATE // 50 * 2  # bytes of s16 mono sent to the phone at a time: 20 ms


STATUS_EVERY = 0.2  # seconds between looks at what hands-free and talkback are doing
AGENTS_EVERY = 3.0  # seconds between looks at which agents can be switched to
RING_HOSTS = ("ntfy.sh",)  # where Henchline's UnifiedPush addresses may live: the ntfy app's server
RINGER_FRESH = 180  # seconds a ring stays the one a call answers (it rings 30; Answer may wait for an unlock)
MAX_UPLOAD = 16 * 1024 ** 3  # bytes in one shared file: a long video fits
KEEP_FREE = 2 * 1024 ** 3  # bytes an upload leaves free on the laptop's disk: past that it is refused
HANGUP_SAY, HANGUP_MAX = 0.5, 10.0  # a hang-up waits for talkback to finish (its queue empty, nothing playing),
# at least this long for the goodbye to be queued, and at most this: past it the call ends even mid-sentence


class Status:
    """What the phone shows: the talk button's colour, a status line, and the chat.

    Read from the shared state: hands-free's mode, the phase, the clip
    playing, and the record of the last message delivered (to whom, how
    heard, and during a call the words as heard). The agents' own words become chat.
    """

    def __init__(self) -> None:
        self.since = time.time()
        self.waiting_for = ""  # the agent a message went to, until it starts speaking
        self.clip = ""  # the clip last put in the chat

    def events(self) -> list[dict]:
        """New chat lines: what an agent started saying, and that a spoken message went out."""
        lines = []
        sent = Sent.last()
        if sent and sent.at > self.since:
            self.since = sent.at
            self.waiting_for = sent.agent.capitalize()
            if sent.heard != Heard.TYPED.value:  # a typed one is in the chat already, as the page sent it
                if sent.heard == Heard.SHARED.value:  # the phone put the share in the chat itself
                    said = ""
                else:  # what it heard you say, so a mishearing shows
                    said = f"🎙 {sent.words}" if sent.words else f"(spoken, sent to {self.waiting_for})"
                if said:
                    lines.append({"chat": {"from": "you", "text": said, "session": sent.session}})
        playing = Playing.now()
        if playing and playing.clip != self.clip:
            self.clip = playing.clip
            lines.append({"chat": {"from": playing.agent.capitalize(), "text": playing.text,
                                   "session": playing.session}})
        for line in sorted(CALL_MIRROR.glob("*.json")):  # typed in the terminal (call/hooks)
            mirrored = read_json(line)
            line.unlink(missing_ok=True)
            if mirrored.get("text"):
                lines.append({"chat": {"from": mirrored.get("from", "Claude"), "session": mirrored.get("session", ""),
                                       "text": ("⌨ " if mirrored.get("from") == "you" else "") + mirrored["text"],
                                       **({"tid": mirrored["tid"]} if mirrored.get("tid") else {})}})
        for meta in files.undelivered():  # `handsfree send`: a file in the chat, or one that never reached the phone
            files.delivered(meta)
            lines.append({"chat": {"from": meta["agent"].capitalize(), "text": meta["text"], "session": meta["session"],
                                   "file": {"url": files.link(meta), "name": meta["name"], "type": meta["type"],
                                            "size": meta["size"]}}})
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


class Ear:
    """What the phone says it played, every 2 s: the laptop cannot hear the phone, so a clip lost on the
    way (suspended audio, dropped chunks, the page in the background) only shows up here."""

    def __init__(self) -> None:
        self.loud_ms = 0  # played out loud over the call
        self.while_speaking = 0  # 2 s spans with a clip playing on the laptop
        self.silent = 0  # … of which the phone played (almost) nothing
        self.clip = ""  # the clip playing at the last report
        self.said = ""  # the last trouble logged: said once, not every 2 s

    def report(self, stats: dict) -> None:
        self.loud_ms += stats.get("loud_ms", 0)
        playing = Playing.now()
        clip, self.clip = self.clip, playing.clip if playing else ""
        # Judged only once the same clip was playing a report ago: its first seconds are still being
        # rendered, silent on the laptop too (every "silent" span of the first test call, 2026-11-15)
        if not playing or playing.clip != clip:
            return
        self.while_speaking += 1
        trouble = []
        if stats.get("audio") != "running":
            trouble.append(f"the phone's audio is {stats.get('audio')}")
        if stats.get("dropped"):
            trouble.append(f"dropped {stats['dropped']} chunks (fell behind)")
        if stats.get("hidden"):
            trouble.append("the page is in the background (the app: the screen is off)")
        if stats.get("loud_ms", 0) < 200:
            self.silent += 1
            trouble.append(f"played only {stats.get('loud_ms', 0)} ms of 2 s, got {stats.get('got', 0)} chunks")
        said = "; ".join(trouble)
        if said and said != self.said:
            log(HANDSFREE_LOG, f"call: talkback playing, but {said}")
        self.said = said

    def summary(self) -> str:
        return (f"the phone played {self.loud_ms / 1000:.0f} s out loud; silent in {self.silent} of "
                f"{self.while_speaking} 2-s spans while talkback played")


async def keep_unmuted() -> None:
    """The laptop's mute key mutes every output, the call's two sinks too: the phone then hears nothing and
    hands-free hears nothing from the phone (2026-11-15). They are the call's own plumbing, not speakers,
    so while the call lasts they are unmuted again at once; the laptop's speakers stay muted."""
    try:
        watch = await asyncio.create_subprocess_exec("pactl", "subscribe", stdout=asyncio.subprocess.PIPE)
    except OSError as exc:
        log(HANDSFREE_LOG, f"call: can't watch for mutes ({exc}): the laptop's mute key would silence this call")
        return
    try:
        while line := await watch.stdout.readline():
            if b"'change' on sink" not in line:
                continue
            for sink in (PHONE_MIC, PHONE_SPEAKER):
                if pactl("get-sink-mute", sink).endswith("yes"):
                    pactl("set-sink-mute", sink, "0")
                    log(HANDSFREE_LOG, f"call: {sink} was muted (the laptop's mute key?): unmuted, the call goes on")
    finally:
        if watch.returncode is None:
            watch.terminate()
            await watch.wait()


def route_to_ringer(reply: str) -> None:
    """The phone answered a ring (it says which, by the ring's reply name, as the call opens): the call goes
    to the agent that rang, not to whoever spoke last. A Call the user starts themselves names no ring and
    goes where they were. A ringer hands-free can't type to yet is said out loud, never routed around."""
    ringer = read_json(CALL_RINGER)
    if not ringer or ringer.get("reply") != reply or time.time() - ringer.get("at", 0) > RINGER_FRESH:
        return
    CALL_RINGER.unlink(missing_ok=True)
    speaker = Speaker.load(SPEAKERS / f"{ringer.get('session', '')}.json")
    if speaker and routing.reachable(speaker):
        routing.switch_to(speaker)
        log(HANDSFREE_LOG, f"call: answering {speaker.agent}'s ring, so to {speaker.agent}")
        return
    name, now = str(ringer.get("agent", "the agent")).capitalize(), last_speaker()
    where = (f"so your words go to {now.spoken()}. Say switch me to, to change" if now
             else "so pick someone: say switch me to")
    routing.say(f"{name} rang, but I can't type to it yet, {where}.")
    log(HANDSFREE_LOG, f"call: answering {name}'s ring, but it can't be reached yet")


def blank_words() -> None:
    """The record of the last delivery keeps the words only during a call: blanked when one starts and ends.
    Sent.last never returns words outside a call, so a delivery racing the end shows nothing either."""
    try:
        data = read_json(SENT)
        if data.get("words"):
            write_json(SENT, {**data, "words": ""}, private=True)
    except OSError as exc:
        log(HANDSFREE_LOG, f"call: couldn't blank the last message's words ({exc})")


def flag(path: Path, on: bool) -> None:
    if on:
        path.touch()
    else:
        path.unlink(missing_ok=True)


async def from_page(message: dict, ear: Ear) -> str:
    """The talk button held or let go, a switch, or a typed chat message; a note for the page, if any."""
    if "answering" in message:  # this call answers a ring: to the agent that rang
        await asyncio.get_running_loop().run_in_executor(None, route_to_ringer, str(message["answering"]))
    elif "ring_endpoint" in message:  # where `handsfree ring` reaches the app: kept private, never logged
        url = urllib.parse.urlsplit(str(message["ring_endpoint"]))
        if url.scheme != "https" or url.hostname not in RING_HOSTS:  # only the phone's distributor, nobody's URL
            log(HANDSFREE_LOG, f"call: refused a ring address on {url.hostname or 'no host'} "
                               f"(not {', '.join(RING_HOSTS)})")
            return ""
        fd = os.open(CALL_RING, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # an older file keeps its mode through O_CREAT
        with os.fdopen(fd, "w") as out:
            out.write(url.geturl())
    elif "sound" in message:  # the app's sound mode: turns after this line ran in it, for comparing them
        log(HANDSFREE_LOG, f"call: phone sound {message['sound']}")
    elif "stats" in message:
        ear.report(message["stats"])
    elif "push" in message:
        flag(CALL_PUSH, message["push"])
    elif "button_only" in message:
        flag(CALL_BUTTON_ONLY, message["button_only"])
    elif "switch" in message:
        await asyncio.get_running_loop().run_in_executor(None, switch, message["switch"])
    elif words := message.get("chat", "").strip():
        sent, note = await asyncio.get_running_loop().run_in_executor(None, typed, words, message.get("session", ""))
        if note:
            return json.dumps({"note": note, "unsent": message.get("id", "")})
        if sent:  # typed into the agent: the phone's two ticks, in the chat it was typed in
            return json.dumps({"delivered": message.get("id", ""), "session": message.get("session", "")})
        return ""
    return ""


def agents() -> dict:
    """The agents a call can switch to: heard lately and still running, and the one replies go to now."""
    current, locked = last_speaker(), desktop().locked()
    started = spawned()
    listed = [{"session": s.session, "name": s.spoken() + (" (laptop locked)" if s.in_editor and locked else ""),
               "spawned": s.tab.owner == tmux.OWNER and s.tab.address in started}
              for s in recent_speakers() if routing.reachable(s)]
    return {"agents": listed,
            "current": current.session if current else ""}


def switch(session: str) -> None:
    """The page's agent menu: replies go to that agent now, announced as when spoken."""
    target = next((s for s in recent_speakers() if s.session == session), None)
    if target and routing.reachable(target):
        routing.switch_to(target)
        routing.say(f"Now talking to {target.spoken()}.")


def typed(words: str, session: str) -> tuple[bool, str]:
    """A typed line, to the agent whose chat it was typed in; a switch ("switch me to Cursor") works as spoken.
    (typed into an agent, a note why not): a switch is neither."""
    if routing.meaning(words) is routing.Meaning.ROUTING:
        routing.route(words, None)
        return False, ""
    got, note = to_agent(words, session, Heard.TYPED)
    return bool(got), note


def to_agent(words: str, session: str, heard: Heard, switch: bool = True) -> tuple[str, str]:
    """Words for the agent whose chat the phone shows: (who got them, "") or ("", a note why nobody did).

    Another agent may have spoken since the phone last heard who is current: the chat shown wins, so the words
    go to exactly that session (typed to it by name, never to whoever is last by then), or nowhere, with a note.
    On a call that agent also becomes the one spoken words go to (switch); a text leaves that alone. No session:
    no agent yet, to the last one (calls only; texts need a session).
    """
    target = None
    if session:
        target = next((s for s in recent_speakers() if s.session == session), None)
        if not target or not routing.reachable(target):
            return "", "That agent isn't reachable, so I didn't send that. Pick another and send again."
        current = last_speaker()
        if switch and (not current or current.session != session):
            routing.switch_to(target)
    if not routing.deliver(words, heard, to=target):  # it says why out loud on the laptop
        return "", "No agent could take that just now (the laptop says why)."
    got = target or last_speaker()
    return (got.spoken() if got else "your agent"), ""


def trusted(request: web.Request) -> None:
    """Only the call page, or Henchline, which sends the page's own Origin. Browsers let any web page open a
    WebSocket or post a form to any address: without this, a page on some other site, open on the phone, could
    type into the agents' terminals. Not a password: a program on the tailnet can send any Origin. Only your
    own devices reaching the Tailscale address is the real boundary."""
    origin = f"https://{request.host}"
    if request.headers.get("Origin") != origin:
        raise web.HTTPForbidden(text="only the call page may connect")
    if CALL_ORIGIN.exists() and CALL_ORIGIN.read_text() == origin:
        return
    CALL_ORIGIN.parent.mkdir(parents=True, exist_ok=True)  # a fresh install has no state folder yet
    CALL_ORIGIN.write_text(origin)  # where `handsfree send` points a notification's link


async def share_file(request: web.Request) -> web.Response:
    """One file shared from the phone, streamed to INBOX/<today>/: the name it was saved under, for share_tell.
    Nothing is overwritten, and an upload that would leave under KEEP_FREE on the disk is refused."""
    trusted(request)
    folder = files.inbox_today()
    name = files.safe_name(request.query.get("name", "file"))
    part = folder / f".{secrets.token_hex(8)}.part"  # its own: two uploads of one name don't share it
    size = 0
    try:
        with part.open("xb") as out:
            while chunk := await request.content.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise OSError(f"bigger than {files.size_text(MAX_UPLOAD)}")
                if size % (64 << 20) < len(chunk) and shutil.disk_usage(folder).free < KEEP_FREE:
                    raise OSError("the laptop's disk is nearly full")
                out.write(chunk)
        if shutil.disk_usage(folder).free < KEEP_FREE:
            raise OSError("the laptop's disk is nearly full")
        while True:  # a link never replaces a file: another upload of the same name may land meanwhile
            path = files.unused(folder, name)
            try:
                os.link(part, path)
                break
            except FileExistsError:
                continue
        part.unlink()
    except (OSError, ConnectionError) as exc:
        part.unlink(missing_ok=True)
        log(HANDSFREE_LOG, f"call: a shared file didn't arrive whole ({exc})")
        return web.json_response({"note": f"Not saved: {exc}"}, status=507 if "disk" in str(exc) else 400)
    log(HANDSFREE_LOG, f"call: the phone shared a file ({files.size_text(size)})")
    return web.json_response({"saved": path.name})


async def share_tell(request: web.Request) -> web.Response:
    """After the uploads: the agent the user is talking to is told where the files are, with any words shared.
    {"files": [saved names], "text": shared text, "note": what the user added, "session": the chat shown}"""
    trusted(request)
    try:
        shared = await request.json()
        names = [str(n) for n in shared.get("files", [])]
    except (ValueError, AttributeError, TypeError):
        raise web.HTTPBadRequest(text="expected JSON")
    paths = [files.received(n) for n in names]
    if None in paths:
        return web.json_response({"note": "Some of those files never arrived on the laptop."}, status=400)
    text, note = str(shared.get("text", "")), str(shared.get("note", ""))
    if not paths and not (text + note).strip():
        return web.json_response({"note": "Nothing to share."}, status=400)
    words = files.message(paths, text, note)
    session = str(shared.get("session", ""))
    if not session and not in_call():  # off a call a share is a text: for one agent, named
        return web.json_response({"note": "Pick an agent's chat in Henchline first, then share again."}, status=400)
    got, why = await asyncio.get_running_loop().run_in_executor(  # off a call it is a text: to that agent only
        None, to_agent, words, session, Heard.SHARED, in_call())
    return web.json_response({"to": got} if got else {"note": why})


async def texted(request: web.Request) -> web.Response:
    """A reply typed into an agent's text notification on the phone (`handsfree text`): to that agent.
    {"session": the agent session that texted, "text": the reply}"""
    trusted(request)
    try:
        message = await request.json()
        words, session = str(message.get("text", "")).strip(), str(message.get("session", ""))
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text="expected JSON")
    # A text always names its agent, never whoever spoke last; only a call's own chat may go to the last speaker
    if not words or not session:
        return web.json_response({"note": "Nothing to send, or no agent named."}, status=400)
    got, why = await asyncio.get_running_loop().run_in_executor(
        None, to_agent, " ".join(words.split()), session, Heard.TEXTED, False)
    return web.json_response({"to": got} if got else {"note": why})


async def crew(request: web.Request) -> web.Response:
    """Henchline's pull-to-refresh: every agent running is looked for and registered, then the crew is listed as
    on a call ({"agents": [{session, name}], "current": session})."""
    trusted(request)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, routing.discover_all)
    return web.json_response(await loop.run_in_executor(None, agents))


SPAWNED_LOCK = threading.Lock()  # SPAWNED is read, changed and written from the loop and executor threads


def spawned(change=None) -> list[str]:
    """The tmux panes of agents started from the phone that are still those panes: each kept with its process id,
    since tmux numbers panes from 0 again after a restart, and a reused id may be one the user started. `change`
    edits the list under the same lock (a new pane, an ended one)."""
    with SPAWNED_LOCK:
        kept = [e for e in read_json(SPAWNED).get("panes", []) if isinstance(e, dict) and e.get("pid")]
        panes = tmux.pane_pids()  # one tmux call; None: tmux didn't answer, so nothing is taken as gone
        alive = kept if panes is None else [e for e in kept if panes.get(str(e.get("pane"))) == e["pid"]]
        if change:
            alive = change(alive)
        if alive != kept:  # gone, or now someone else's: forgotten
            write_json(SPAWNED, {"panes": alive}, private=True)
        return [str(e["pane"]) for e in alive]


def trusted_folders() -> list[Path]:
    """Where a new chat can start: folders Claude Code already trusts (its own trust prompt would otherwise be the
    first thing the phone's words answer), that still exist, most recently used first."""
    try:
        projects = json.loads((Path.home() / ".claude.json").read_text()).get("projects", {})
    except (OSError, ValueError, AttributeError):
        return []
    found = [Path(p) for p, v in projects.items() if isinstance(v, dict) and v.get("hasTrustDialogAccepted")]

    def used(folder: Path) -> float:  # Claude keeps each folder's sessions in ~/.claude/projects/<path, dashed>
        try:
            return (Path.home() / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(folder))).stat().st_mtime
        except OSError:
            return 0.0
    # Not a hidden folder's insides (~/.claude/jobs/…: an agent's own scratch folders, which once showed up here)
    shown = (f for f in found if f.is_dir() and not any(part.startswith(".") for part in f.parts))
    return sorted(shown, key=used, reverse=True)[:30]


def notice(title: str, words: str) -> None:
    """A notification on the user's phone through their own ntfy topic (handsfree's; never printed or logged)."""
    topic_file = Path.home() / ".config/ptt/ntfy-topic"
    topic = topic_file.read_text().strip() if topic_file.exists() else ""
    if not topic:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{topic}", data=words.encode(),
                                                      headers={"Title": title, "Tags": "lock"}), timeout=10).close()
    except (OSError, ValueError):
        log(HANDSFREE_LOG, "trust notice not sent")


def typed_folder(given: str) -> Path | None:
    """A folder typed on the phone for a new chat: an existing folder of the user's (under home, or a drive mounted
    under /media/<user>), never home's credential folders (files.PRIVATE) and never home itself, which holds them."""
    path = Path(given).expanduser()
    if not path.is_absolute():
        return None
    path = path.resolve()
    roots = [Path.home(), Path("/media") / Path.home().name]
    if not path.is_dir() or path in roots or not any(r in path.parents for r in roots) or files.private(path):
        return None
    return path


async def crew_folders(request: web.Request) -> web.Response:
    """A new chat's choices: the folders it can start in and the agents installed."""
    trusted(request)
    folders = await asyncio.get_running_loop().run_in_executor(None, trusted_folders)
    listed = [{"path": str(f), "name": "home" if f == Path.home() else f.name} for f in folders]
    return web.json_response({"folders": listed, "agents": tmux.available()})


async def crew_new(request: web.Request) -> web.Response:
    """A new chat: the agent started in its own tmux window in one of those folders (nothing else: the phone names
    one from the list), registered, and its session returned with the crew."""
    trusted(request)
    try:
        body = await request.json()
        folder, agent, typed = str(body.get("folder", "")), str(body.get("agent", "claude")), bool(body.get("typed"))
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text="expected JSON")
    loop = asyncio.get_running_loop()
    match = next((f for f in await loop.run_in_executor(None, trusted_folders) if str(f) == folder), None)
    if match is None and typed:  # a path the user typed (and confirmed trusting): any folder of theirs that exists
        match = typed_folder(folder)
        if match is None:
            return web.json_response({"note": f"Not a folder of yours on the laptop: {folder}"}, status=400)
    if match is None or agent not in tmux.available():
        return web.json_response({"note": "Not a folder or agent the laptop offers."}, status=400)
    tab = await loop.run_in_executor(None, tmux.start, agent, match)
    if not tab:
        return web.json_response({"note": f"{agent} didn't start."}, status=500)
    # Claude's question only comes for a typed folder (the listed ones are those it trusts); Codex keeps its own
    # list, so it may ask about any folder: answered once that exact question is on screen (it may not come at all)
    asks = agent == "codex" or (typed and agent == "claude"
                                and match not in await loop.run_in_executor(None, trusted_folders))
    if asks:
        name = agent.capitalize()
        wait = 8.0 if agent == "codex" else 12.0
        accepted = await loop.run_in_executor(None, tmux.accept_trust, tab, match, agent, wait)
        if accepted or typed:
            log(HANDSFREE_LOG, f"{name}'s trust for {match} {'accepted' if accepted else 'left to answer in tmux'}")
        if accepted:  # said on the phone too: a request from anything else on the tailnet can't do this unseen
            await loop.run_in_executor(None, notice, f"{name} now trusts a folder",
                                       f"Started from a new chat, {name}'s trust question was answered yes for "
                                       f"{match}.")
    # Every start is told to the phone: the agent runs commands without asking (auto mode, Run Everything, Codex's
    # reviewer), so one started by anything else on the tailnet can't go unseen
    await loop.run_in_executor(None, notice, f"{agent.capitalize()} started in {match.name}",
                               f"From a new chat ({match}). It runs commands without asking; "
                               "`tmux attach -t henchline` to watch.")
    pid = await loop.run_in_executor(None, tmux.pane_pid, tab)
    await loop.run_in_executor(None, spawned, lambda alive: [*alive[-49:], {"pane": tab.address, "pid": pid}])
    log(HANDSFREE_LOG, f"started {agent} in {match} (tmux {tab.address}) from the phone")
    for _ in range(16):  # the pane alone is asked (cheap) until the agent runs in it, then the desktop once
        if await loop.run_in_executor(None, desktop().terminals.agent_running, tab):
            break
        await asyncio.sleep(0.5)
    await loop.run_in_executor(None, routing.discover_all)
    found = next((s for s in recent_speakers() if s.tab == tab), None)
    if found:
        return web.json_response({"session": found.session, **await loop.run_in_executor(None, agents)})
    return web.json_response({"note": f"{agent} started, but didn't show up: pull down to refresh."}, status=504)


async def crew_end(request: web.Request) -> web.Response:
    """Ending an agent from the phone (the long-press menu, after asking): only one Henchline started itself, in
    its tmux pane. Agents the user started never are."""
    trusted(request)
    try:
        session = str((await request.json()).get("session", ""))
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text="expected JSON")
    target = next((s for s in recent_speakers() if s.session == session), None)
    if target is None or target.tab.owner != tmux.OWNER or target.tab.address not in spawned():
        return web.json_response({"note": "Only agents started from the phone can be ended from it."}, status=403)
    await asyncio.get_running_loop().run_in_executor(None, tmux.end, target.tab)
    await asyncio.get_running_loop().run_in_executor(
        None, spawned, lambda alive: [e for e in alive if e.get("pane") != target.tab.address])
    (SPEAKERS / f"{session}.json").unlink(missing_ok=True)  # an exact match from recent_speakers: never a made-up path
    log(HANDSFREE_LOG, f"ended {target.spoken()} (tmux {target.tab.address}) from the phone")
    return web.json_response({"ok": True})


async def text_status(request: web.Request) -> web.Response:
    """The phone saying how far agents' texts got: {"ids": [...], "status": "delivered" | "read"}. Only ever
    forward (a read text isn't made delivered again), only texts this laptop sent."""
    trusted(request)
    try:
        body = await request.json()
        ids, status = [str(i) for i in body.get("ids", [])], str(body.get("status", ""))
    except (ValueError, AttributeError, TypeError):
        raise web.HTTPBadRequest(text="expected JSON")
    if status not in ("delivered", "read"):
        raise web.HTTPBadRequest(text="status: delivered or read")
    order = ["sent", "delivered", "read"]
    for tid in ids:
        if not re.fullmatch(r"[0-9a-f]{16}", tid):
            continue
        record = read_json(TEXTS / f"{tid}.json")
        if record and order.index(status) > order.index(record.get("status", "sent")):
            write_json(TEXTS / f"{tid}.json", {**record, "status": status, f"{status}_at": time.time()}, private=True)
    return web.json_response({"ok": True})


VOICE_NOTE_MAX = 20 * 1024 * 1024  # bytes of one voice message: about ten minutes at 16 kHz


async def voice_note(request: web.Request) -> web.Response:
    """A voice message recorded in Henchline off a call (a WAV, the chat's agent session in the query): kept for
    hands-free to transcribe and type to that agent. Its id, to ask after it."""
    trusted(request)
    session = request.query.get("session", "")
    # Only an agent hands-free knows (as /text): the session is never made into a path
    if not any(s.session == session for s in recent_speakers()):
        return web.json_response({"note": "That agent isn't known to the laptop."}, status=400)
    VOICE_NOTES.mkdir(parents=True, exist_ok=True)
    if sum(1 for m in VOICE_NOTES.glob("*.json") if read_json(m).get("status") in ("waiting", "transcribing")) >= 5:
        return web.json_response({"note": "Too many voice messages waiting."}, status=429)
    nid = secrets.token_hex(8)
    wav = VOICE_NOTES / f"{nid}.wav"
    size = 0
    with wav.open("wb") as out:
        while chunk := await request.content.read(1 << 16):
            size += len(chunk)
            if size > VOICE_NOTE_MAX:
                out.close()
                wav.unlink(missing_ok=True)
                return web.json_response({"note": "Too long."}, status=413)
            out.write(chunk)
    os.chmod(wav, 0o600)
    write_json(VOICE_NOTES / f"{nid}.json", {"id": nid, "session": session, "status": "waiting", "at": time.time()},
               private=True)
    return web.json_response({"id": nid})


async def voice_note_status(request: web.Request) -> web.Response:
    """How a voice message is getting on: waiting, done (with what was heard) or failed (with why)."""
    trusted(request)
    nid = request.match_info["nid"]
    if not re.fullmatch(r"[0-9a-f]{16}", nid):
        raise web.HTTPNotFound()
    meta = read_json(VOICE_NOTES / f"{nid}.json")
    if not meta:
        raise web.HTTPNotFound()
    if meta.get("status") == "waiting" and time.time() - meta.get("at", 0) > VOICE_NOTE_STALE:
        # Hands-free never got to it (off, or down): dropped now, so it is never typed after the phone gave up
        meta.update(status="failed", note="Not sent: the laptop didn't transcribe it in time.")
        write_json(VOICE_NOTES / f"{nid}.json", meta, private=True)
        (VOICE_NOTES / f"{nid}.wav").unlink(missing_ok=True)
    return web.json_response({k: meta.get(k, "") for k in ("status", "text", "note")})


async def sent_file(request: web.Request) -> web.StreamResponse:
    """A file an agent sent (`handsfree send`): only its secret folder name finds it."""
    path = files.find(request.match_info["secret"], request.match_info["name"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers=files.headers(path))


async def call(request: web.Request) -> web.WebSocketResponse:
    trusted(request)
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    if in_call():
        await ws.close(message=b"busy: another call is up")
        return ws
    with Devices():
        mic = await asyncio.create_subprocess_exec(
            "pacat", "--playback", "--raw", f"--device={PHONE_MIC}", "--format=s16le", f"--rate={MIC_RATE}",
            "--channels=1", "--latency-msec=60", "--client-name=phone-call", stdin=asyncio.subprocess.PIPE)
        speaker = await asyncio.create_subprocess_exec(
            "parec", "--raw", f"--device={PHONE_SPEAKER_MONITOR}", "--format=s16le", f"--rate={SPEAKER_RATE}",
            "--channels=1", "--latency-msec=40", "--client-name=phone-call", stdout=asyncio.subprocess.PIPE)
        CALL_HANGUP.unlink(missing_ok=True)  # a hang-up asked for when no call was up is not for this one
        blank_words()  # words a killed server never blanked
        for stale in CALL_MIRROR.glob("*.json"):  # terminal lines from before this call
            stale.unlink(missing_ok=True)
        write_json(CALL, {"from": request.remote or "", "pid": os.getpid(),
                          "cmdline": Path("/proc/self/cmdline").read_text()})
        await asyncio.get_running_loop().run_in_executor(None, turn_on_handsfree, False)  # with talkback
        log(HANDSFREE_LOG, "phone call started")

        async def to_phone() -> None:
            while not ws.closed:
                data = await speaker.stdout.read(CHUNK)
                if not data:
                    return
                await ws.send_bytes(data)

        async def show_status() -> None:
            status, shown = Status(), None
            listed, shown_agents = 0.0, None
            asked = 0.0  # when a hang-up was asked for
            while not ws.closed:
                if CALL_HANGUP.exists():  # the user said "hang up", or an agent ran `handsfree hangup`
                    asked = asked or time.monotonic()
                    waited = time.monotonic() - asked
                    quiet = Playing.now() is None and not any(SPOOL.glob("*.json"))  # said and nothing waiting
                    if (waited > HANGUP_SAY and quiet) or waited > HANGUP_MAX:
                        CALL_HANGUP.unlink(missing_ok=True)
                        log(HANDSFREE_LOG, "call: hung up from the laptop")
                        await ws.close(message=b"Hung up from the laptop")
                        return
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

        sender = asyncio.create_task(to_phone())
        shower = asyncio.create_task(show_status())
        unmuter = asyncio.create_task(keep_unmuted())
        heard = 0  # bytes of the phone's mic, for the log
        ear = Ear()
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    heard += len(msg.data)
                    mic.stdin.write(msg.data)
                    await mic.stdin.drain()
                elif msg.type == WSMsgType.TEXT and (note := await from_page(msg.json(), ear)):
                    await ws.send_str(note)  # only a refused typed line has one
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            CALL_PUSH.unlink(missing_ok=True)
            CALL_BUTTON_ONLY.unlink(missing_ok=True)  # the next call starts with an open line again
            CALL_HANGUP.unlink(missing_ok=True)  # the phone went first: the hang-up is moot
            sender.cancel()
            shower.cancel()
            unmuter.cancel()
            CALL.unlink(missing_ok=True)  # hands-free and talkback go back to the laptop first
            blank_words()  # the call's words don't outlast it
            for proc in (mic, speaker):
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:  # gone already: the service being stopped kills the whole group
                        pass
                await proc.wait()
            log(HANDSFREE_LOG, f"phone call ended ({heard / 2 / MIC_RATE:.0f} s of the phone's mic arrived; "
                               f"{ear.summary()})")
    return ws


def serve(address: str, port: int) -> bool:
    """Put the page behind `tailscale serve` (https on this machine's Tailscale name, a real certificate).

    The proxy points at the Tailscale address, not 127.0.0.1, so the page's own socket stays where it was:
    reachable from your devices (and, as any address of this machine, from programs on it), and nothing
    more. False when Tailscale won't (`sudo tailscale set --operator=$USER` lets this user configure it,
    once) or its https root already serves something else, which is left alone.
    """
    target = f"http://{address}:{port}"
    status = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True).stdout
    root = re.search(r"\|-- / proxy (\S+)", status)
    if root and root.group(1) != target:
        log(HANDSFREE_LOG, f"tailscale serve already proxies {root.group(1)}: left alone, own certificate instead")
        return False
    done = subprocess.run(["tailscale", "serve", "--bg", "--https=443", target], capture_output=True, text=True)
    if done.returncode != 0:
        log(HANDSFREE_LOG, f"tailscale serve refused ({done.stderr.strip()[:120]}): own certificate instead")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=CALL_PORT)
    parser.add_argument("--cert", type=Path, help="serve https with this certificate (and --key): a self-signed one\n"
                        "works when `tailscale serve` is not available; the phone accepts it once")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--host", default="serve",
                        help="serve (default): behind `tailscale serve`, at https://<this machine's Tailscale name>/ "
                             "with a real certificate, falling back to `tailscale` when this user may not configure "
                             "Tailscale; tailscale: this machine's Tailscale address, with its own certificate. "
                             "Either way the socket is on the Tailscale address: your devices reach it")
    args = parser.parse_args()
    if in_call():  # another server has a call up: starting would repoint the proxy away from it
        print("a call is up on another call server; not starting", file=sys.stderr)
        return 1
    CALL.unlink(missing_ok=True)  # leftovers from a crash: the marker, a hang-up never done, the call's sinks
    CALL_HANGUP.unlink(missing_ok=True)
    for line in pactl("list", "short", "modules").splitlines():
        found = re.search(r"sink_name=(\S+)", line)
        if "module-null-sink" in line and found and found.group(1) in (PHONE_MIC, PHONE_SPEAKER):
            pactl("unload-module", line.split()[0])
    app = web.Application()
    app.router.add_get("/", lambda _: web.FileResponse(HERE / "page.html"))
    app.router.add_get("/call", call)
    app.router.add_post("/share/file", share_file)
    app.router.add_post("/share/tell", share_tell)
    app.router.add_post("/text", texted)
    app.router.add_post("/text-status", text_status)
    app.router.add_post("/voice-note", voice_note)
    app.router.add_get("/voice-note/{nid}", voice_note_status)
    app.router.add_post("/crew", crew)  # it registers what it finds: a POST
    app.router.add_get("/crew/folders", crew_folders)
    app.router.add_post("/crew/new", crew_new)
    app.router.add_post("/crew/end", crew_end)
    app.router.add_get("/files/{secret}/{name}", sent_file)
    proxied = False
    if args.host in ("serve", "tailscale"):
        try:
            found = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True).stdout.split()
        except OSError:
            found = []
        if not found:  # the page is only for your own devices: no tailnet, no page
            print("Tailscale has no address (or is not installed): sudo tailscale up", file=sys.stderr)
            return 1
        mode, args.host = args.host, found[0]
        proxied = mode == "serve" and args.cert is None and serve(args.host, args.port)
    if args.cert is None and not proxied:
        if not (CALL_TLS / "cert.pem").exists():  # plain http: the phone's browser would refuse the mic
            print(f"no certificate in {CALL_TLS} and no tailscale serve: ./install.sh call", file=sys.stderr)
            return 1
        args.cert, args.key = CALL_TLS / "cert.pem", CALL_TLS / "key.pem"
    tls = None
    if args.cert:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(args.cert, args.key)
    web.run_app(app, host=args.host, port=args.port, ssl_context=tls, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

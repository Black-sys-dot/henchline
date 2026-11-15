"""Who hands-free's words go to, and getting them there.

Replies go to the agent that spoke last (talkback records it). Fixed phrases
switch to another agent; there is no language model in the loop, by choice.
Delivery types into the agent's Konsole tab, or into the Cursor editor's
chat for an agent that lives there.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path

from voice.agents import SPOKEN_TO
from voice.desktop import current as desktop
from voice.state import (BY_HANDSFREE, HANDSFREE_LOG, LAST, LISTEN, Sent, Speaker, in_call, last_speaker, log,
                         recent_speakers)

SAY = Path(__file__).resolve().parent.parent / "talkback/scripts/say.py"
AFTER_SWITCH = 30.0  # seconds to start talking after "Now talking to …"


class Heard(str, Enum):
    """How a message was heard, in its tag, so the agent can judge whether it was meant for it."""

    WOKE = "woke"  # "hey Claude" or an earbud tap
    REPLY = "reply"  # in the window right after a clip
    THINK = "think"  # after the user asked for time
    FOLLOW_ON = "follow-on"  # just after their own last message
    OVER_YOU = "over you"  # talked over a clip
    CALL = "call"  # on a phone call, where the user talks whenever they like
    PUSHED = "call, held the talk button"  # on a phone call, while holding the page's talk button
    TYPED = "call, typed"  # typed into the call page's chat, not spoken


NAMES = "|".join(SPOKEN_TO)
AGENT = rf"({NAMES})"
WHERE = r"(?:\s+(?:in|from|on)\s+(?:the\s+)?(.+?))?"
SWITCH = re.compile(
    rf"^\W*(?:(?:okay|ok|alright|right|so|no|actually|hey)[\s,]+)?(?:(?:hey\s+)?(?:{NAMES})[\s,]+)?"
    rf"(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:just\s+)?"
    rf"(?:switch(?:\s+me)?(?:\s+back)?|(?:hand|pass)(?:\s+(?:me|it|this))?(?:\s+(?:over|back))?|"
    rf"(?:give|send)(?:\s+(?:me|it|this))?\s+(?:over|back)|go\s+back|(?:let\s+me\s+)?talk|connect\s+me|"
    rf"put\s+me\s+through|i\s+want\s+to\s+talk|back)\s+(?:over\s+)?to\s+{AGENT}{WHERE}(?:\s+please)?\W*$", re.I)
SEND_THAT = re.compile(rf"^\W*(?:please\s+)?(?:send|forward|give)\s+(?:that|this|it)(?:\s+message)?"
                       rf"\s+to\s+{AGENT}{WHERE}\W*$", re.I)
# "…actually, scratch that, hand this to Cursor": drop what came before, then switch
DISCARD = re.compile(r".*\b(?:never\s*mind|scratch\s+that|forget\s+(?:it|that|all\s+that|what\s+i\s+(?:just\s+)?said))"
                     r"\W+(.+)$", re.I | re.S)
# A short lead-in sentence before the request: a long message that merely ends that way is a message
LAST_SENTENCE = re.compile(r"^(.*[.!?])\s+([^.!?]+[.!?]?)\s*$", re.S)
LEAD_IN_WORDS = 8
CLAUSE = re.compile(r"^(.*[.,;!?])\s+([^.,;!?]+[.!?]?)\s*$", re.S)  # a lead-in, then the last clause
WHO = re.compile(r"^\W*who\s+(?:am\s+i|are\s+you)\s+(?:talking\s+to|speaking\s+to)?\W*$"
                 r"|^\W*who\s+is\s+(?:this|listening)\W*$", re.I)
THINK = re.compile(r"\b(give me a (minute|moment|second|sec)|let me think|hold on|hang on"
                   r"|one (sec|second|moment)|let me (get|come) back)\b", re.I)
JUST_TIME = 12  # words: "hold on, let me think about that" asks for time and says nothing else
CANCEL = re.compile(r"^\W*(never ?mind|cancel( that)?|scratch that)\W*$", re.I)
OFF = re.compile(r"\b(hands[- ]?free (mode )?off|stop hands[- ]?free|turn off hands[- ]?free)\b", re.I)
# On a phone call only: the whole turn is the request, so "how do I hang up?" is still a message
HANGUP = re.compile(r"^\W*(okay\W+|ok\W+|alright\W+)?(please\W+)?(hang up|end (the|this) call)(\s+(now|please))?\W*$",
                    re.I)
# One word is a real answer ("yes", "go", "stop"); only bare fillers are noise
FILLER = re.compile(r"\W*(um+|uh+|hmm+|mm+|ah+|oh|eh)\W*", re.I)


class Meaning(str, Enum):
    """What a transcribed turn asks for, before any agent sees it."""

    TIME = "time"  # only "give me a minute"
    ROUTING = "routing"  # who am I talking to, switch, send that
    OFF = "off"
    HANGUP = "hang up"  # end the phone call
    CANCEL = "cancel"
    NOTHING = "nothing"  # empty or a bare filler
    MESSAGE = "message"


def meaning(text: str) -> Meaning:
    if THINK.search(text) and len(text.split()) <= JUST_TIME:
        return Meaning.TIME
    if WHO.match(text) or switch_request(text):
        return Meaning.ROUTING
    if OFF.search(text):
        return Meaning.OFF
    if hangup_request(text) and in_call():
        return Meaning.HANGUP
    if CANCEL.match(text):
        return Meaning.CANCEL
    if not text.strip() or FILLER.fullmatch(text):
        return Meaning.NOTHING
    return Meaning.MESSAGE


def say(text: str) -> subprocess.Popen:
    """A hands-free notice, in its own voice; it never takes the user's replies. The process ends once the
    notice is in talkback's queue (the worker renders it), so waiting for it means it will be said."""
    return subprocess.Popen([str(SAY), "--agent", BY_HANDSFREE, "--session", BY_HANDSFREE, text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def squash(words: str) -> str:
    return re.sub(r"[^a-z0-9]", "", words.lower())


def find(agent: str, where: str | None = None) -> Speaker | None:
    """The most recent agent of that kind to speak, in that workspace if named."""
    found = [s for s in recent_speakers() if s.agent == agent.lower()
             and (not where or squash(where) in squash(s.where) or squash(s.where) in squash(where))]
    return max(found, key=lambda s: s.at) if found else None


def switch_to(speaker: Speaker) -> None:
    """Replies go to this agent now, with time to start talking."""
    speaker.save(LAST)
    LISTEN.write_text(str(time.time() + AFTER_SWITCH))
    log(HANDSFREE_LOG, f"switched to {speaker.agent} in {speaker.where or '?'}")


def hangup_request(text: str) -> bool:
    """The turn asks to end the call: all of it, or its last clause after a short lead-in ("I'm in, hang up")."""
    if HANGUP.match(text):
        return True
    last = CLAUSE.match(text)
    return bool(last) and len(last.group(1).split()) <= LEAD_IN_WORDS and bool(HANGUP.match(last.group(2)))


def switch_request(text: str) -> tuple[str, str | None, bool] | None:
    """(agent, workspace, forward the last message?) if the words ask for a switch."""
    match = SWITCH.match(text) or SEND_THAT.match(text)
    if not match and (tail := DISCARD.match(text)):
        match = SWITCH.match(tail.group(1))
    if not match and (last := LAST_SENTENCE.match(text)) and len(last.group(1).split()) <= LEAD_IN_WORDS:
        match = SWITCH.match(last.group(2))  # "Yeah, I'm talking to you now. Please switch me to Cursor."
    if not match:
        return None
    return match.group(1).lower(), match.group(2), bool(SEND_THAT.match(text))


def route(text: str, last_message: tuple[str, Heard] | None) -> bool:
    """Who am I talking to, switch, send that; True when something was sent along."""
    if WHO.match(text):
        current = last_speaker()
        say(f"You're talking to {current.spoken()}." if current else "Nobody yet: no agent has spoken.")
        return False
    agent, where, forward = switch_request(text)
    target = find(agent, where)
    if target is None:
        log(HANDSFREE_LOG, f"heard: route to {agent}, none found")
        say(f"I can't find {agent.capitalize()}{' in ' + where if where else ''}. "
            "It has to have spoken through talkback first.")
        return False
    if not reachable(target):
        current = last_speaker()
        log(HANDSFREE_LOG, f"heard: route to {target.agent} in {target.where or '?'}, not running any more")
        say(f"{target.spoken()} isn't running any more, so I can't switch you there. "
            + (f"You're still talking to {current.spoken()}." if current else ""))
        return False
    switch_to(target)
    if not forward:
        say(f"Now talking to {target.spoken()}.")
    elif not last_message:
        say(f"Now talking to {target.spoken()}, but there's nothing to send yet.")
    elif deliver(*last_message):
        say(f"Sent to {target.spoken()}.")
        return True
    return False


def reachable(speaker: Speaker) -> bool:
    """Is that agent still running where its words would be typed: its terminal tab, or its Cursor window?"""
    if speaker.tab:
        return desktop().terminals.agent_running(speaker.tab)
    return speaker.in_editor and bool(cursor_window(speaker.where))


def cursor_window(where: str) -> str:
    """The Cursor editor window for a workspace, by its title "… - <where> - Cursor"."""
    for window in desktop().windows("cursor"):
        if window.title == f"{where} - Cursor" or window.title.endswith(f" - {where} - Cursor"):
            return window.id
    return ""


def type_into_cursor(wid: str, message: str) -> bool:
    """Bring the window forward, focus its agent chat, paste, Enter.

    ctrl+alt+shift+F12 runs composer.focusComposer (a user keybinding that
    install.sh agents adds). Run in a child process: Xlib from the listening
    thread, beside the Qt tray thread, crashed hands-free (SIGSEGV).
    """
    if desktop().paste_into(wid, message, ("ctrl", "alt", "shift", "f12"), ("ctrl", "v"), ("return",)):
        return True
    log(HANDSFREE_LOG, f"Cursor's window did not stay in front (focus: {desktop().focused().app.strip()})")
    return False


def deliver(text: str, heard: Heard) -> bool:
    """Type the words, tagged with how they were heard, to the agent that spoke last."""
    message = f"[hands-free · {heard.value}] {text}"
    speaker = last_speaker()
    if speaker is None:
        log(HANDSFREE_LOG, "nobody to send to")
        say("I heard you, but no agent has spoken yet.")
        return False
    tab = speaker.tab
    if speaker.in_editor:
        wid = cursor_window(speaker.where)
        if not wid:
            log(HANDSFREE_LOG, f"no Cursor window for {speaker.where}")
            say("I heard you, but I can't find Cursor's window for that project.")
            return False
        ok = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "handsfree.py"),
                             "type-into-cursor", wid, message], timeout=20).returncode == 0
        log(HANDSFREE_LOG, f"sent to cursor window {speaker.where}" if ok else "Cursor delivery failed")
        if ok:
            Sent(speaker.agent, heard.value, session=speaker.session).save()
        if not ok and desktop().locked():
            say("The laptop's screen is locked, so I can't type into Cursor. "
                "Agents in a terminal still get your words.")
        elif not ok:
            say("I couldn't bring Cursor's window to the front, so I didn't type that anywhere. "
                "Click Cursor's window and say it again.")
        return ok
    if not tab:
        log(HANDSFREE_LOG, f"no terminal tab for {speaker.agent}")
        say("I heard you, but I don't know which terminal to type that into.")
        return False
    if not desktop().terminals.agent_running(tab):
        log(HANDSFREE_LOG, f"no agent running in {tab.address}")
        say("I heard you, but the agent I was talking to isn't running any more, so I didn't type it anywhere.")
        return False
    if not desktop().terminals.type_line(tab, message):
        log(HANDSFREE_LOG, f"typing into {tab.address} failed")
        say("I heard you, but that terminal is gone.")
        return False
    log(HANDSFREE_LOG, f"sent to {speaker.agent} {tab.address}")
    Sent(speaker.agent, heard.value, session=speaker.session).save()
    return True


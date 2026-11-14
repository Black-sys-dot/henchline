"""The files push-to-talk, talkback and hands-free share, and what is in them.

Every name under ~/.local/state/ptt lives here and is read from here; no tool
spells a path of its own. The files carry state, never the user's words.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

STATE = Path.home() / ".local/state/ptt"

# Push-to-talk (and hands-free, which records the same way)
PHASE = STATE / "phase"  # recording / transcribing / idle, and who wrote it
TARGET = STATE / "target"  # whether the last paste went to an agent window
PTT_LOG = STATE / "ptt.log"
PTT_WAV = STATE / "utterance.wav"  # the recording, deleted once transcribed
VOICE_BASELINE = STATE / "voice.json"  # the user's usual loudness and pitch, for tone marks

# Talkback
TALKBACK_ON = STATE / "talkback"  # agents speak while this exists
SPOOL = STATE / "talkback.queue"  # one JSON clip per file, oldest first
HOLDS = STATE / "talkback.holds"  # one file per session whose clips wait
LOCK = STATE / "talkback.lock"  # the one worker holds it
WORKER_PID = STATE / "talkback.pid"
PLAYING = STATE / "talkback.playing"
LAST = STATE / "talkback.last"  # the agent a hands-free reply goes to
SPEAKERS = STATE / "talkback.speakers"  # every agent heard lately, one file each
TALKBACK_LOG = STATE / "talkback.log"

# Hands-free
HANDSFREE_ON = STATE / "handsfree"
ANNOUNCE = STATE / "handsfree.announce"  # set by `handsfree on`: say so once
PAUSE = STATE / "handsfree.pause"  # e.g. while wake-word samples are recorded
LISTEN = STATE / "handsfree.listen"  # keep the reply window open until this time
CLOSE = STATE / "handsfree.close"  # end the reply window now
HANDSFREE_LOG = STATE / "handsfree.log"
HANDSFREE_WAV = STATE / "handsfree.wav"  # a turn being transcribed, deleted after

# Who wrote the phase, and the agent name hands-free's own notices speak as
BY_PTT = "ptt"
BY_HANDSFREE = "handsfree"

STALE_PHASE = 300  # seconds after which a "recording" phase is a leftover from a crash
SPEAKER_MEMORY = 24 * 3600  # how long an agent counts as reachable after it last spoke


class Phase(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


# The tray icons of push-to-talk and hands-free, in the same colours
PHASE_COLOURS = {Phase.RECORDING: "#e24a4a", Phase.TRANSCRIBING: "#e0a100"}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    """Write whole or not at all: readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def log(path: Path, line: str, echo: bool = False) -> None:
    """Append a timestamped line; echo also prints it (to the journal, for a service)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = f"{time.strftime('%F %T')} {line}"
    with open(path, "a") as out:
        out.write(stamped + "\n")
    if echo:
        print(stamped, flush=True)


def set_phase(phase: Phase, by: str = "") -> None:
    """What the mic is doing; talkback goes quiet and holds while it records."""
    write_json(PHASE, {"phase": phase.value, "by": by, "at": time.time()})


def phase_now() -> tuple[Phase, str]:
    """The current phase and who set it; a stale recording counts as idle."""
    data = read_json(PHASE)
    try:
        phase = Phase(data.get("phase", Phase.IDLE.value))
    except ValueError:
        phase = Phase.IDLE
    if phase is not Phase.IDLE and time.time() - data.get("at", 0) > STALE_PHASE:
        phase = Phase.IDLE
    return phase, data.get("by", "")


def release_phase(by: str) -> None:
    """Back to idle, unless someone else has the mic now."""
    if phase_now()[1] == by:
        set_phase(Phase.IDLE, by)


def mic_busy() -> bool:
    return phase_now()[0] is not Phase.IDLE


def set_target(to_agent: bool) -> None:
    """Whether the words just went into an agent's window (never the words)."""
    write_json(TARGET, {"agent": to_agent, "at": time.time()})


def target_since(since: float) -> bool | None:
    """True/False if a paste was noted after `since` (to an agent or not), else None."""
    data = read_json(TARGET)
    return bool(data.get("agent")) if data.get("at", 0) >= since else None


@dataclass
class Speaker:
    """An agent that spoke through talkback, and where its replies go."""

    session: str
    agent: str
    where: str = ""  # its workspace folder name
    konsole: tuple[str, str] = ("", "")  # D-Bus service and session of its Konsole tab
    at: float = field(default_factory=time.time)

    @classmethod
    def load(cls, path: Path) -> Speaker | None:
        data = read_json(path)
        if not data.get("session") or not data.get("agent"):
            return None
        return cls(data["session"], data["agent"], data.get("where", ""),
                   tuple(data.get("konsole") or ("", "")), data.get("at", 0.0))

    def save(self, path: Path) -> None:
        write_json(path, {**asdict(self), "konsole": list(self.konsole)})

    def remember(self) -> None:
        """This agent spoke: replies go to it, and it can be switched to by name."""
        self.at = time.time()
        self.save(LAST)
        self.save(SPEAKERS / f"{self.session}.json")

    def spoken(self) -> str:
        """How the agent is named out loud: "Claude, in pc diagnostics"."""
        where = self.where.replace("_", " ").replace("-", " ")
        return f"{self.agent.capitalize()}, in {where}" if where else self.agent.capitalize()


def last_speaker() -> Speaker | None:
    return Speaker.load(LAST)


def recent_speakers() -> list[Speaker]:
    found = (Speaker.load(path) for path in SPEAKERS.glob("*.json"))
    return [s for s in found if s and time.time() - s.at < SPEAKER_MEMORY]

"""Which agent is running us, where it lives, and whether it is still there.

Talkback asks this when an agent speaks (whose voice, which workspace); the
KDE desktop (voice/kde_x11.py) uses it to find the agent's Konsole tab. The
answers come from the process tree, because environment markers lie: an
editor started from a Claude shell inherits CLAUDECODE, and a session
moved into Claude's background daemon inherits another tab's Konsole address.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Agent programs by command line, checked nearest ancestor first. Node-based
# CLIs run as "node /path/to/codex", so the first two words are checked.
AGENT_PROCESSES = [
    (re.compile(r"(^|/)claude(/|$)"), "claude"),
    (re.compile(r"cursor-agent|(^|/)agent$|(^|/)cursor$"), "cursor"),
    (re.compile(r"(^|/)codex$"), "codex"),
    (re.compile(r"(^|/)gemini$"), "gemini"),
    (re.compile(r"(^|/)opencode$"), "opencode"),
]
SPOKEN_TO = ("claude", "cursor", "codex")  # agents hands-free can switch to by name
CURSOR_EDITOR = re.compile(r"(^|/)cursor$")  # the editor itself, not its CLI agent
CLAUDE_DAEMON = b"bg-pty-host"  # Claude Code's background-session host


@dataclass
class Process:
    pid: int
    words: list[str]  # the first two words of its command line

    @property
    def agent(self) -> str:
        for word in self.words:
            for pattern, name in AGENT_PROCESSES:
                if pattern.search(word):
                    return name
        return ""

    @property
    def is_editor(self) -> bool:
        return any(CURSOR_EDITOR.search(word) for word in self.words)

    @property
    def folder(self) -> Path:
        return Path(os.readlink(f"/proc/{self.pid}/cwd"))


SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"}


def comm(pid: int | str) -> str:
    """A process's program name, e.g. "bash"."""
    return Path(f"/proc/{pid}/comm").read_text().strip()


def _stat(pid: int, field: int) -> int:
    """A number from /proc/<pid>/stat, counted from the field after the name."""
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[field])


def parent_of(pid: int) -> int:
    return _stat(pid, 1)


def terminal_foreground(pid: int) -> int:
    """The process group in the foreground of pid's terminal."""
    return _stat(pid, 5)


def ancestors(start: int | None = None) -> list[Process]:
    """Our parent, its parent, … up to init, nearest first."""
    chain, pid = [], start or os.getppid()
    while pid > 1 and (found := process(pid)):
        chain.append(found)
        try:
            pid = parent_of(pid)
        except (OSError, ValueError, IndexError):
            break
    return chain


def nearest_agent() -> Process | None:
    return next((p for p in ancestors() if p.agent), None)


def agent_folder() -> Path:
    """The workspace of the calling agent, wherever its shell happens to be."""
    agent = nearest_agent()
    if agent is None:
        return Path.cwd()
    if agent.is_editor:
        # One Cursor editor process serves every window, from whatever folder it
        # was first started in; the agent's own shell starts in the workspace
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
        return Path(top) if top else Path.cwd()
    try:
        return agent.folder
    except OSError:
        return Path.cwd()


def identity(agent: str = "", session: str = "") -> tuple[str, str]:
    """(agent kind, session key) of the caller; given values win."""
    if not agent:
        process = nearest_agent()
        if os.environ.get("TALKBACK_AGENT"):
            agent = os.environ["TALKBACK_AGENT"].lower()
        elif process is not None:
            agent = process.agent  # the process we really run under beats inherited markers
        elif os.environ.get("CLAUDECODE"):
            agent = "claude"
        elif "cursor" in os.environ.get("AI_AGENT", "").lower() or any(k.startswith("CURSOR_") for k in os.environ):
            agent = "cursor"
        else:
            agent = "agent"
    if not session:
        session = ((os.environ.get("CLAUDE_CODE_SESSION_ID") if agent == "claude" else None)
                   or (os.environ.get("CURSOR_TRACE_ID") if agent == "cursor" else None)
                   or str(agent_folder()))
    return agent, re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{agent}-{session}")[-120:]


def process(pid: int) -> Process | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:2]
    except OSError:
        return None
    return Process(pid, [w.decode(errors="replace") for w in raw])


def cmdline_has(pid: int, needle: bytes) -> bool:
    try:
        return needle in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False

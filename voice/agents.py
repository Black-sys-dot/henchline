"""Which agent is running us, where it lives, and whether it is still there.

Talkback asks this when an agent speaks (whose voice, which workspace, which
Konsole tab to type replies into); hands-free asks it before typing into a
tab. The answers come from the process tree, because environment markers lie:
an editor started from a Claude shell inherits CLAUDECODE, and a session
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
    while pid > 1 and (process := _process(pid)):
        chain.append(process)
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


def qdbus(service: str, *args: str) -> str:
    return subprocess.run(["qdbus", service, *args], capture_output=True, text=True).stdout.strip()


def konsole_tab() -> tuple[str, str]:
    """(D-Bus service, session) of the Konsole tab the calling agent is shown in.

    KONSOLE_DBUS_SESSION is trusted only when that tab's foreground program is
    one of our ancestors with no Claude daemon in between. Otherwise the tab is
    found by its "<workspace> : claude" title, from the agent's folder upwards,
    and left empty when that is not unique. An agent in the Cursor editor has
    no tab at all: hands-free reaches it through its window.
    """
    agent = nearest_agent()
    service = os.environ.get("KONSOLE_DBUS_SERVICE", "")
    if (agent and agent.is_editor) or not service:
        return "", ""
    session = os.environ.get("KONSOLE_DBUS_SESSION", "")
    chain = ancestors(os.getpid())
    daemon = any(_cmdline_has(p.pid, CLAUDE_DAEMON) for p in chain)
    foreground = qdbus(service, session, "foregroundProcessId") if session else ""
    if not daemon and foreground.isdigit() and int(foreground) in {p.pid for p in chain}:
        return service, session
    titles = {s: qdbus(service, s, "title", "1") for s in qdbus(service).split() if s.startswith("/Sessions/")}
    starts = ([agent.folder] if agent else []) + [Path.cwd()]
    for folder in (f for start in starts for f in (start, *start.parents)):
        tabs = [s for s, title in titles.items() if title == f"{folder.name} : claude"]
        if len(tabs) == 1:
            return service, tabs[0]
        if tabs:
            break  # several Claude tabs in that folder: do not guess
    return "", ""


def agent_in_tab(service: str, session: str) -> bool:
    """Is an agent the foreground program of that tab? If it quit, the tab is a
    plain shell, and Enter after typed words would run them as a command."""
    foreground = qdbus(service, session, "foregroundProcessId")
    return foreground.isdigit() and any(p.agent for p in [_process(int(foreground))] if p)


def _process(pid: int) -> Process | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:2]
    except OSError:
        return None
    return Process(pid, [w.decode(errors="replace") for w in raw])


def _cmdline_has(pid: int, needle: bytes) -> bool:
    try:
        return needle in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False

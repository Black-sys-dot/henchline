"""Agents in tmux: the ones Henchline starts from the phone (a new chat), and any the user runs in tmux.

A tmux pane is a tab whose owner is "tmux" and whose address is the pane id (%12). Typing into it needs no
window, no focus and no desktop: `tmux send-keys`. So an agent started from the phone works on any Linux
(the user's point, 2026-11-16: not tied to KDE), and the user can watch it with `tmux attach -t henchline`.
WithTmux puts this in front of the desktop's own terminals: tmux panes here, every other tab there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from voice.agents import process
from voice.desktop import Tab, Terminals, Window

OWNER = "tmux"
SESSION = "henchline"  # where the phone's new chats run: `tmux attach -t henchline`
TYPE_PAUSE = 0.15  # seconds between the text and its Enter, as for Konsole
COMMANDS = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent"}  # what a new chat starts, per agent


def tmux(*args: str, own_scope: bool = False) -> subprocess.CompletedProcess | None:
    """own_scope: the tmux server started in a systemd scope of its own, so restarting the service that asked
    (the call server) doesn't take every agent started from the phone down with its cgroup."""
    scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect"] \
        if own_scope and shutil.which("systemd-run") else []
    try:
        return subprocess.run([*scope, "tmux", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def out(*args: str) -> str:
    done = tmux(*args)
    return done.stdout.strip() if done and done.returncode == 0 else ""


def agent_in(pane_pid: int) -> int:
    """The agent the pane runs: the pane's own process, or one of its children (a shell that started it)."""
    if (p := process(pane_pid)) and p.agent:
        return pane_pid
    try:
        children = Path(f"/proc/{pane_pid}/task/{pane_pid}/children").read_text().split()
    except OSError:
        return 0
    return next((int(c) for c in children if (p := process(int(c))) and p.agent), 0)


def available() -> list[str]:
    """The agents a new chat can start: those installed, with tmux."""
    if not shutil.which("tmux"):
        return []
    return [agent for agent, command in COMMANDS.items() if shutil.which(command)]


def start(agent: str, folder: Path) -> Tab:
    """A new agent in its own tmux window in that folder; its tab, or an empty one when it couldn't start."""
    command = shutil.which(COMMANDS.get(agent, ""))
    if not command:
        return Tab()
    where = ["-c", str(folder), "-n", f"{agent}:{folder.name}", "-P", "-F", "#{pane_id}", command]
    running = tmux("has-session", "-t", SESSION)
    if running and running.returncode == 0:
        pane = out("new-window", "-d", "-t", f"{SESSION}:", *where)
    else:
        started = tmux("new-session", "-d", "-s", SESSION, *where, own_scope=True)
        pane = started.stdout.strip() if started and started.returncode == 0 else ""
    return Tab(OWNER, pane) if pane.startswith("%") else Tab()


def end(tab: Tab) -> bool:
    return tab.owner == OWNER and (done := tmux("kill-pane", "-t", tab.address)) is not None and done.returncode == 0


class WithTmux(Terminals):
    def __init__(self, inner: Terminals) -> None:
        self.inner = inner

    def tab_of_caller(self) -> Tab:
        pane = os.environ.get("TMUX_PANE", "")
        if pane and os.environ.get("TMUX"):  # inside tmux, even in a Konsole tab: the pane is the precise place
            return Tab(OWNER, pane)
        return self.inner.tab_of_caller()

    def agent_running(self, tab: Tab) -> bool:
        if tab.owner != OWNER:
            return self.inner.agent_running(tab)
        pid = out("display-message", "-p", "-t", tab.address, "#{pane_pid}")
        return pid.isdigit() and agent_in(int(pid)) > 0

    def type_line(self, tab: Tab, text: str) -> bool:
        if tab.owner != OWNER:
            return self.inner.type_line(tab, text)
        typed = tmux("send-keys", "-t", tab.address, "-l", text)
        if not typed or typed.returncode != 0:
            return False
        time.sleep(TYPE_PAUSE)
        entered = tmux("send-keys", "-t", tab.address, "Enter")
        return bool(entered and entered.returncode == 0)

    def only_a_shell(self, window: Window) -> bool:
        return self.inner.only_a_shell(window)

    def agent_tabs(self) -> list[tuple[Tab, int]]:
        found = list(self.inner.agent_tabs())
        for line in out("list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").splitlines():
            pane, _, pid = line.partition(" ")
            if pid.isdigit() and (agent := agent_in(int(pid))):
                found.append((Tab(OWNER, pane), agent))
        return found

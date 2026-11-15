"""Inside the container: drive voice/desktop.py on a plain X11 desktop (no KDE) and check each answer.

Run by inside.sh after Xvfb, openbox and two xterms are up: one runs a pretend
agent (a program called "claude" that writes what it is typed to a file), the
other only a shell.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/repo")
from voice.desktop import Tab, current  # noqa: E402

AGENT_OUT = Path("/tmp/agent-heard.txt")
results: list[tuple[str, bool, str]] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    results.append((what, ok, detail))
    print(f"[{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""), flush=True)


screen = current()
check("plain X11 desktop chosen (no KDE here)", type(screen).__name__ == "GenericX11", type(screen).__name__)

xterms = screen.windows("xterm")
check("finds both terminal windows", len(xterms) == 2, ", ".join(w.title for w in xterms))
agent_window = next((w for w in xterms if w.title == "agent"), None)
shell_window = next((w for w in xterms if w.title != "agent"), None)  # bash retitles its own xterm

check("clipboard set and read back", screen.set_clipboard("clipboard check ✓"))

screen.raise_window(shell_window.id)
time.sleep(0.5)
check("raises a window and sees it in front", screen.is_front(shell_window.id))
focused = screen.focused()
check("focused window is a terminal running only a shell", screen.terminals.only_a_shell(focused), focused.app.strip())

tab = Tab(*Path("/tmp/agent-tab").read_text().split())  # written by the pretend agent's terminal (tab_of_caller)
check("the agent's own terminal found its tab", bool(tab), f"{tab.service} {tab.session}")
check("an agent is running in that tab", screen.terminals.agent_running(tab))
check("the shell window is not an agent's tab", not screen.terminals.agent_running(
    Tab(shell_window.id, Path("/tmp/shell-tty").read_text().strip())))

check("typed a line into the agent's terminal", screen.terminals.type_line(tab, "hello from hands-free"))
time.sleep(1)
heard = AGENT_OUT.read_text() if AGENT_OUT.exists() else ""
check("the agent received exactly that line", heard.strip() == "hello from hands-free", repr(heard))

failed = [w for w, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)} of {len(results)} passed on {os.uname().sysname} "
      f"{Path('/etc/os-release').read_text().split('PRETTY_NAME=')[1].splitlines()[0]}")
sys.exit(1 if failed else 0)

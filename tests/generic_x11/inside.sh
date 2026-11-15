#!/bin/sh
# Inside a throwaway Fedora container: a virtual X11 screen with a plain window manager,
# a pretend agent in one xterm and a bare shell in another, then check.py.
set -eu
dnf -y -q install python3 xorg-x11-server-Xvfb xterm xclip xprop openbox libXtst >/dev/null 2>&1
export DISPLAY=:1
Xvfb :1 -screen 0 1280x800x24 >/dev/null 2>&1 &
sleep 1
openbox >/dev/null 2>&1 &
sleep 1
# The pretend agent: its program name is "claude"; it first records its own tab, then writes what it is typed
xterm -T agent -xrm 'XTerm*VT100.translations: #override Shift Ctrl <Key>V: insert-selection(CLIPBOARD)' -e sh -c 'python3 -c "import sys; sys.path.insert(0, \"/repo\"); from voice.desktop import current; \
t = current().terminals.tab_of_caller(); open(\"/tmp/agent-tab\", \"w\").write(f\"{t.owner} {t.address}\")"; \
exec -a claude head -n 1 > /tmp/agent-heard.txt' &
xterm -T shell -e sh -c 'tty > /tmp/shell-tty; exec bash' &
sleep 3
python3 /repo/tests/generic_x11/check.py

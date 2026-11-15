#!/bin/sh
# Claude Code's hook for call/hooks/mirror.py: off a call (nearly always) it costs a file test and nothing else, and
# whatever happens it never fails a prompt or prints anything to Claude Code.
[ -e "$HOME/.local/state/ptt/call" ] || exit 0
"$(dirname "$(readlink -f "$0")")/mirror.py" >/dev/null 2>&1
exit 0

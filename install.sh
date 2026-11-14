#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

# ./install.sh agents: talkback command and instructions for the agents found here
if [ "${1:-}" = agents ]; then
  mkdir -p "$HOME/.local/share/talkback"
  ln -sfn "$root/talkback" "$HOME/.local/share/talkback/skill"
  ln -sfn "$root/talkback/scripts/say.py" "$HOME/.local/bin/talkback"
  snippet="$root/agents/AGENTS-snippet.md"
  if [ -d "$HOME/.claude" ]; then
    mkdir -p "$HOME/.claude/skills" && ln -sfn "$root/talkback" "$HOME/.claude/skills/talkback"
    grep -qF "agents/AGENTS-snippet.md" "$HOME/.claude/CLAUDE.md" 2>/dev/null || printf '\n@%s\n' "$snippet" >> "$HOME/.claude/CLAUDE.md"
    echo "Claude Code: imports the snippet from ~/.claude/CLAUDE.md"
  fi
  if [ -d "$HOME/.cursor" ]; then
    mkdir -p "$HOME/.cursor/rules" "$HOME/.cursor/skills"
    install -m 644 "$root/agents/cursor/voice.mdc" "$HOME/.cursor/rules/voice.mdc"
    ln -sfn "$root/talkback" "$HOME/.cursor/skills/talkback"
    echo "Cursor: rule ~/.cursor/rules/voice.mdc"
  fi
  if [ -d "$HOME/.codex" ]; then
    if [ -e "$HOME/.codex/AGENTS.md" ] && [ ! -L "$HOME/.codex/AGENTS.md" ]; then
      grep -qF "## Talkback" "$HOME/.codex/AGENTS.md" || cat "$snippet" >> "$HOME/.codex/AGENTS.md"
    else
      ln -sfn "$snippet" "$HOME/.codex/AGENTS.md"
    fi
    echo "Codex: ~/.codex/AGENTS.md"
  fi
  exit 0
fi
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.config/ptt"
ln -sfn "$root/ptt/ptt.py" "$HOME/.local/bin/ptt"
install -m 644 "$root/ptt/ptt.service" "$HOME/.config/systemd/user/ptt.service"
if [ ! -f "$HOME/.config/ptt/config.json" ]; then
  install -m 644 "$root/ptt/config.example.json" "$HOME/.config/ptt/config.json"
fi
systemctl --user daemon-reload
systemctl --user enable --now ptt.service
echo "ptt is enabled for this graphical session."
install -m 644 "$root/handsfree/handsfree.service" "$HOME/.config/systemd/user/handsfree.service"
mkdir -p "$HOME/.config/pipewire"
install -m 644 "$root/handsfree/talkback-echo-cancel.conf" "$HOME/.config/pipewire/talkback-echo-cancel.conf"
ln -sfn "$root/handsfree/handsfree.py" "$HOME/.local/bin/handsfree"
systemctl --user daemon-reload
echo "hands-free is installed: handsfree on (it then starts again at login)"

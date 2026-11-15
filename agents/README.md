# Telling your agents about talkback and hands-free

`AGENTS-snippet.md` is the one text every agent needs: when to speak through talkback, how to read tone marks, and how careful to be with `[hands-free …]` messages. It is written for any agent; only where each tool looks for instructions differs. `install.sh agents` sets up all of the below for the tools it finds.

The snippet points at `~/.local/share/talkback/skill/SKILL.md`, a link to this repository's `talkback/` folder, and the skill speaks with the `talkback` command (`~/.local/bin/talkback`, a link to `talkback/scripts/say.py`).

| Agent | Where it reads instructions | What to do |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` | add the line `@~/copilot-ptt/agents/AGENTS-snippet.md` (Claude Code imports it). Optional: `ln -s ~/copilot-ptt/talkback ~/.claude/skills/talkback` for a `/talkback` command |
| Codex CLI | `~/.codex/AGENTS.md` (global) | link or append the snippet |
| Cursor | rules in `~/.cursor/rules/` | `~/.cursor/rules/voice.mdc`: the snippet under a header with `alwaysApply: true`, written by `install.sh agents` (no copy in the repo). For hands-free replies into the editor's chat, also the keybinding `ctrl+alt+shift+f12` → `composer.focusComposer` in Cursor's `keybindings.json` (`install.sh agents` adds it) |
| Anything else that reads `AGENTS.md` (Gemini CLI, opencode, Aider with a convention file, …) | the project's or its global `AGENTS.md` | append the snippet |

Talkback tells agents apart by their process (Claude Code, Cursor's agent, Codex), or by `TALKBACK_AGENT=name` in the agent's environment. Each gets its own voice; unknown agents get a default one and introduce themselves by that name.

Tested so far: Claude Code on Linux with KDE (speaking, push-to-talk, hands-free), and Cursor agents speaking through the same skill. Hands-free replies reach agents in Konsole tabs and in the Cursor editor's chat (X11). Codex is wired the same way but not yet tested end to end.

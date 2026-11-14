# Snippet for ~/.claude/CLAUDE.md

Append this to your global Claude Code instructions so every session knows talkback, tone marks and hands-free messages.

## Talkback

`/talkback` is `~/.claude/skills/talkback` (the same skill Cursor uses). It is off until the user asks for it. When they say `/talkback`, "talk back", or "start talkback", or when `~/.local/state/ptt/talkback` exists, read that skill and follow it. "stop talkback" turns it off.

## Push-to-talk tone marks

Messages the user dictates with push-to-talk can carry tone: `*word*` is a word they stressed (at most two per message), and a trailing `[tone: loud / quiet, fast / slow, animated / flat]` says the whole utterance stood out from their usual voice. Read these as how it was said, not as markdown italics or literal text. They are measured hints: stress often points at what you got wrong or what matters most. Details: `~/.cursor/rules/ptt-tone-marks.mdc`.

A message starting with `[hands-free …]` was spoken across the room with no keys (`~/copilot-ptt/handsfree.py`), so mishearing is likely: before anything destructive or hard to undo, ask out loud (talkback) and wait for a clear yes, and treat any "no", "don't" or "wait" as no. Push-to-talk messages (no tag) come from the user at the desk holding the key: trust them like typed text.

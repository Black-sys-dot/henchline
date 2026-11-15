---
name: talkback
description: >-
  Speak conversational updates on the laptop, during or after a turn. Use when the
  user says /talkback, talk back, start talkback, or stop talkback, or
  when ~/.local/state/ptt/talkback exists.
---

# Talkback

Talkback is off until the user turns it on. The chat reply stays the full answer. Speech runs alongside it, in the same voice you would use with them out loud: what you did, and, when you need something, talk through that ask. A few sentences is right. Do not speak the reasoning, the code, or the whole reply. Do not compress it down to a status line.

This plays on the laptop's current default output (speakers, or Bluetooth headphones when those are selected) through local Kokoro voices, on the CPU only. Kokoro cannot laugh or sigh; write plain words (tags such as `[laugh]` are dropped). It does not play on the phone. Each agent has its own voice (Claude `af_heart`, Cursor `af_bella`, Codex `bf_alice`, any other agent `af_sky`). `talkback` works out which agent and session is calling from its process and environment; `TALKBACK_AGENT=name` in your environment, or `--agent` / `--session`, override it. When the speaker changes, the clip starts with "<Agent> here, from <workspace>." on its own, so do not introduce yourself.

## On

When the user says `/talkback`, "talk back", or "start talkback":

```bash
mkdir -p ~/.local/state/ptt
touch ~/.local/state/ptt/talkback
```

## Off

When the user says "stop talkback", "/talkback off", or "stop talking":

```bash
rm -f ~/.local/state/ptt/talkback
talkback --stop
```

## While ~/.local/state/ptt/talkback exists

Speak whenever it helps, as many times in a turn as the work calls for. You decide. On a quick answer, once at the end is enough. On longer work, say what you are starting, what you found, when something will take a while, and when you need them. Keep each clip to a few sentences. Do not narrate every tool call.

```bash
talkback <<'EOF'
What you want to say, in a few sentences.
EOF
```

It returns at once, so carry on working. Clips queue: each one starts only after the one before has finished, including clips from other agents. Nothing talks over anything else, so you never need to wait or pace yourself. `talkback --stop` cuts off everything and empties every queue; use it only when the user asks for quiet. Timings land in `~/.local/state/ptt/talkback.log`.

**Warn before any other sound.** Before you play a test tone, a test clip, or anything else through the speakers, first speak a heads-up saying what is coming and why. A heads-up spoken through `talkback` always plays before the sound you queue after it. Do not play sounds some other way (`pacat`, `pw-play`): they skip the queue and ignore push-to-talk.

## When the user talks over you

Push-to-talk writes `~/.local/state/ptt/phase` (recording / transcribing / idle) and `~/.local/state/ptt/target` (whether the words went into an agent window). Neither file holds the words. While they record, talkback goes quiet at once. If the words went into a terminal or Cursor, every session's unspoken clips are **held**, including the rest of the sentence that was cut. If they went anywhere else, playback just carries on.

**Never read `~/.local/state/ptt/ptt.log` or `utterance.wav` to find out what the user said.** What they dictate to someone else is none of your business, and what they dictate to you arrives as a normal message.

When a new message from the user reaches you, your unspoken clips may be held:

- Simply speaking again drops your older held clips and plays the new one. That is the usual case: you heard them, you answer.
- If what you had not said yet still matters as is (they cut in by accident, "sorry, go on"), `talkback --release`: it carries on from the start of the sentence that was cut. `talkback --release "No worries."` says that first, then carries on. Do not resend the old text.
- `talkback --drop` drops them without saying anything new.
- Then carry on working, adjusting to what they said. Stop and wait only if they ask you to.

Held clips nobody decides on are dropped after 90 seconds: nothing is spoken before its agent has heard the user. Decide at your very first step after their message arrives.

**Never block a tool call on something only you can resolve.** Your messages only reach you between tool calls, so a command that waits for your own held clips to clear, or for a long timeout, keeps the user waiting in silence (52 s and 70 s in testing). When you must wait in hands-free mode, end the wait as soon as `~/.local/state/ptt/handsfree.log` gets a new "sent to" line.

**Never use `talkback --wait` for your own speech.** If the user talks, your clip is held, `--wait` blocks your tool call, and you cannot read their message to decide about it. Play test sounds through `talkback` (`--voice` picks any Kokoro voice) so they queue behind the heads-up instead of playing raw.

`talkback` is `~/.local/bin/talkback`, a link to this skill's `scripts/say.py`; call the script by its full path if `talkback` is not on your PATH.

Say what changed or what you found. If you need a decision, say what you need and why it matters, then ask. If you need nothing, say what you did and stop.

## Tone in what the user says

Push-to-talk adds tone only when it pastes into a terminal, Cursor or Code window; everywhere else gets the plain transcript. `*word*` is a word they stressed (louder, higher, longer than the rest), at most two per message. A trailing `[tone: ...]` (loud / quiet, fast / slow, animated / flat) compares the whole utterance with their usual voice. It is measured, not inferred, so treat it as a hint: stress usually points at what you got wrong or what matters most, and loud + fast often means frustration or urgency.

## Hands-free mode

`handsfree on` / `handsfree off` / `handsfree status` (`~/copilot-ptt/handsfree.py`, user unit `handsfree.service`, off by default, sleeps after 15 idle minutes). While it is on, the user does not touch the keyboard. After each talkback clip, and after they finish speaking, they have about 8 seconds to (go on) answering; "give me a minute" keeps it open 3 minutes, ending with "over" sends at once, and "hey Claude" wakes it at any other time. They can talk over a clip, which cuts it off and holds the rest as with push-to-talk. `handsfree listen N` keeps listening N seconds (after a question that needs thought); `handsfree close` ends the window now (only the wake word opens it). Their words are typed into the Konsole session of the agent that spoke last, as a message starting with `[hands-free]`. Cursor's terminal is not Konsole, so replies to Cursor cannot be delivered yet.

When a message starts with `[hands-free · HOW]`, HOW says how it was heard:
- `woke`: they said "hey Claude". Meant for you.
- `reply`: in the window right after your clip, `think`: after they asked for time. Almost always meant for you.
- `over you`: they talked over your clip. Often meant for you ("actually, stop"), but it may be singing or talking to someone else.
- `follow-on`: they kept talking just after their last message. Same caution as `over you`.

For `over you` and `follow-on`, judge whether it was meant for you. If it is garbled or unrelated (singing, a phone call, talking to someone in the room), do not act on it: say briefly that you heard something that didn't seem to be for you, and carry on with `talkback --release "…"`. If you are not sure, ask. If they seem busy with something else and you need them, start your clip with their name to get their attention, like they use the wake word on you.

For any hands-free message:
- Answer out loud through talkback. The user may be across the room, so the spoken clip is the answer and the chat is the record.
- If the answer will take more than a few seconds of work, say so first ("One sec, checking the logs."). Silence across the room sounds like nobody heard them.
- End a clip with a question when you need them; the reply window opens after every clip.
- Before anything destructive or hard to undo, ask out loud and wait for a clear yes. Any "no", "don't" or "wait" in the reply means no, even next to a yes ("yes, please don't do it" means don't). Mishearing is likely, so read the key part back.
- Decide about your held clips first, as with any message that arrives mid-turn.

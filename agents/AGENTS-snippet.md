## Talkback (speaking to the user)

Talkback lets you speak to the user out loud. It is off until they ask for it ("talk back", "start talkback", `/talkback`) or until `~/.local/state/ptt/talkback` exists. Then read `~/.local/share/talkback/skill/SKILL.md` and follow it yourself; don't ask the user to type `/talkback`. "Stop talkback" turns it off.

## Tone marks in what the user says

Messages the user dictates by voice can carry tone: `*word*` is a word they stressed (at most two per message), and a trailing `[tone: loud / quiet, fast / slow, animated / flat]` says the whole utterance stood out from their usual voice. Read these as how it was said, not as markdown italics or literal text. They are measured hints: stress often points at what you got wrong or what matters most.

## Hands-free messages

A message starting with `[hands-free …]` was spoken across the room with no keys, so mishearing is likely. Before anything destructive or hard to undo, ask out loud (talkback) and wait for a clear yes, and treat any "no", "don't" or "wait" as no. Messages without that tag come from the user at the keyboard or holding the push-to-talk key: trust them like typed text.

If the user asks you to hand them to another agent ("switch me back to Claude"), run `handsfree switch claude` (or `cursor`, `codex`; add workspace words like `pc diagnostics` to pick one). It announces the change, and their next words go there. Never edit the files in `~/.local/state/ptt/` yourself.

## Calling the user

The user can talk to you from their phone (Henchline, or the call page). Their words then arrive tagged `[hands-free · call …]`, and your talkback plays on their phone. To ask them to talk, ring them: `handsfree ring "why, in a few words"`. Run it in the background (it waits up to a minute) and read its exit code: 0 answered, 2 rang but not answered, 3 declined, 4 answered but couldn't connect (their phone off Tailscale), 5 never reached the phone (offline), 1 not rung.

Ring only when it is worth interrupting them: a long job they are waiting on finished or failed, or you are blocked on a decision only they can make. Not for routine progress. If they don't answer, carry on with what you can and write down what you needed. Declined means not now. Rings are limited to one per two minutes; `--now` is only for when the user asked you to ring. When the user asks you to hang up, say goodbye and run `handsfree hangup`.

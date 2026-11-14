# copilot-ptt

On Linux the Copilot key is a chord, Left Meta + Left Shift + F23. The desktop does not bind it to anything. Hold that key, or another key you choose. The microphone opens only while the key is down. Whisper runs on this machine, the text is pasted into the focused window, and Enter is pressed.

People already remap that chord with [keyd](https://github.com/rvaiya/keyd) and with [remap-copilot](https://github.com/m-bartlett/remap-copilot). A few tools open a launcher from F23. Hold-to-talk with Whisper already exists as well. This program is those two ideas together.

On some laptops the Copilot key sends the chord down and up immediately, so it cannot be held. This program needs a key that stays down while you speak. If yours does not, push-to-talk on that key will not work. Set another hotkey from the status icon.

## What is in here

| Folder | What it does | Needs |
|---|---|---|
| `ptt/` | **Push-to-talk:** hold a key, speak, the words are typed into the focused window. Works on its own. | the rest of this page |
| `talkback/` | **Talkback:** AI agents (Claude Code, Cursor) speak their replies through a local voice, one at a time. | `ptt/` only if you want it to go quiet while you talk |
| `handsfree/` | **Hands-free:** talk with those agents without touching a key: a reply window after they speak, talking over them, and a personal wake word. | `ptt/` and `talkback/` |
| `agents/` | What the agents need to be told: one snippet for Claude Code, Cursor, Codex and anything reading `AGENTS.md`. | `talkback/` |
| `voice/` | What the three share: the state files in `~/.local/state/ptt/` and their names (`state.py`), the settings, Whisper and the chime (`whisper.py`), tone marks (`tone.py`), talkback's queue of clips (`spool.py`), which agent and terminal tab a process belongs to (`agents.py`), windows, keys and the clipboard on X11 (`x11.py`). | nothing |

## Wayland

Wayland is not implemented. Paste sends keys with the X11 test extension, and the focused window is read with `xprop`. Pull requests that make paste work on Wayland are welcome.

## Requirements

- Linux, X11
- PipeWire (`pw-record`, `pw-play`)
- KDE Klipper (`qdbus org.kde.klipper`)
- Python 3.11 or newer
- Your user in the `input` group

While the laptop is plugged in, Whisper small stays on the GPU. On battery it runs on the CPU, so a CUDA context is not held and the GPU can sleep. The microphone is not opened until the key is down.

## Install

```bash
git clone https://github.com/NinjaTuna007/copilot-ptt
cd copilot-ptt
sudo apt install python3-pyqt5 python3-evdev
python3 -m pip install --user faster-whisper
./install.sh
```

The first start downloads the Whisper small weights. The user service starts again at graphical login.

Hold the Copilot key, speak, release. A two-key hold stays one recording until both keys are up. The words are typed into the focused window. Okular and a Thunderbird mail window are left alone; a Thunderbird compose window still receives the paste and Enter. Holding the key also stops desktop speech from speech-dispatcher, if that is playing. Right-click the speech bubble for language, power, another hotkey, or to turn the chime off. The chime is the freedesktop sound `complete.oga` if that file is installed. A missing sound is skipped. `"chime_sound"` in the config can point at another file. The Copilot key stays on when another hotkey is set.

Log: `~/.local/state/ptt/ptt.log`

When the words go to a terminal, Cursor or Code window, a word said louder, higher or longer than the rest comes out as `*word*`, and an utterance that stands out from your usual voice ends with a note such as `[tone: loud, fast]` (`voice/tone.py`). Other windows get the plain text. Only numbers about the voice are kept, in `~/.local/state/ptt/voice.json`. `"hotwords"` in the config lists names Whisper should expect; keep it short.

The listener writes `~/.local/state/ptt/phase` and `~/.local/state/ptt/target` (whether the paste went to an agent window, never the words) for a speech helper that should go quiet while you talk.

## Talkback

`talkback/` lets AI agents (Claude Code, Cursor, others that read a skill or rule) speak back through a local [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) voice on the CPU. Each call to `talkback/scripts/say.py` joins one queue (`voice/spool.py`), played by one worker (`talkback/scripts/worker.py`), so agents never talk over each other; each agent has its own voice and introduces itself when the speaker changes. When push-to-talk or hands-free hears you, it goes quiet, holds what it had not said, and the agent decides whether to carry on, acknowledge, or answer you instead.

Install the voice once:

```bash
python3 -m venv ~/.local/share/talkback/venv
~/.local/share/talkback/venv/bin/pip install kokoro-onnx soundfile
# kokoro-v1.0.onnx and voices-v1.0.bin from the kokoro-onnx releases into ~/.local/share/talkback/
./install.sh agents   # talkback command + instructions for Claude Code, Cursor, Codex
```

`agents/` holds what the agents need to know, in one text for all of them (`AGENTS-snippet.md`); `./install.sh agents` wires it into Claude Code, Cursor and Codex if they are installed, and `agents/README.md` shows where other agents look. Say "talk back" to an agent to turn it on.

## Hands-free

`handsfree on` talks to the same agents without a key (`handsfree/handsfree.py` is the command; `listener.py` decides when to listen, `sensing.py` hears the mic, wake word, earbuds and speakers, `routing.py` picks the agent and types to it, `tray.py` is the icon; user unit `handsfree/handsfree.service`; `install.sh` installs it, `systemctl --user enable handsfree` starts it at every login). It needs a speech helper that writes `~/.local/state/ptt/talkback.*` (the talkback skill this was built for), and the echo canceller in `handsfree/talkback-echo-cancel.conf` copied to `~/.config/pipewire/`; it captures the built-in mic, which shares a clock with the speakers. It listens for a few seconds after an agent speaks, while an agent speaks (talking over it stops it), and for a wake word the rest of the time. The words are typed into the Konsole tab of the agent that spoke last, prefixed `[hands-free · how it was heard]`. "Over" sends at once, "give me a minute" keeps listening, "never mind" drops it, "hands-free off" ends it. A tap on Bluetooth earbuds works like the wake word, silently: hands-free grabs the earbuds' media-key device ("… (AVRCP)") while it runs, so the tap does not also pause music, and lets go when it stops. Earbuds send those keys in their music profile (A2DP); in the headset profile a tap becomes a call command that never reaches hands-free. Hands-free listens through the laptop mic anyway, so music mode loses nothing.

Replies go to the agent that spoke last; switching is by voice.

### Switching agents: fixed phrases, on purpose

Switching recognises a handful of phrase shapes, not free-form language: there is no language model in the loop, by choice, to keep the dependencies small. Say the whole utterance as one of these (an agent name is Claude, Cursor or Codex; "in <workspace>" picks one of several):

| Say | Does |
|---|---|
| "switch to Cursor", "hand it back to Claude", "pass me over to Cursor", "back to Claude", "let me talk to Cursor in copilot ptt", "put me through to Claude", "can you switch me to Cursor?" | your replies go to that agent from now on |
| "send that to Claude" | forwards your last message there, and switches |
| "…never mind / scratch that / forget what I said, hand this to Cursor" | drops what came before, then switches |
| "who am I talking to?" | says who your replies go to |

An agent asked to hand you over runs `handsfree switch claude|cursor|codex [workspace]`, which does the same and announces it (the agent snippet tells them to).

A leading "okay", "no" or "actually" is fine. Anything negated ("don't switch me to Claude") or longer is an ordinary message to the current agent. A switch never sends your words anywhere by itself and is announced out loud, so a misheard one is undone with "back to …". `handsfree listen N` and `handsfree close` widen or close the window.

The wake word is a personal detector: `handsfree/wakeword_collect.py` records you at a few spots around the house, `handsfree/wakeword_train.py` fits it on openWakeWord embeddings (`pip install --user --no-deps openwakeword`; its TFLite dependency has no Python 3.12 build), and Whisper confirms each candidate on just those two seconds.

Log: `~/.local/state/ptt/handsfree.log` (events, no words)

## License

MIT

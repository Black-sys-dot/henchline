# Push-to-talk

On Linux the Copilot key is a chord, Left Meta + Left Shift + F23. The desktop does not bind it to anything. Hold that key, or another key you choose. The microphone opens only while the key is down. Whisper runs on this machine, the text is pasted into the focused window, and Enter is pressed.

People already remap that chord with [keyd](https://github.com/rvaiya/keyd) and with [remap-copilot](https://github.com/m-bartlett/remap-copilot). A few tools open a launcher from F23. Hold-to-talk with Whisper already exists as well. This program is those two ideas together.

On some laptops the Copilot key sends the chord down and up immediately, so it cannot be held. This program needs a key that stays down while you speak. If yours does not, push-to-talk on that key will not work. Set another hotkey from the status icon.

Hold the Copilot key, speak, release. A two-key hold stays one recording until both keys are up. The words are typed into the focused window. Okular and a Thunderbird mail window are left alone; a Thunderbird compose window still receives the paste and Enter. Holding the key also stops desktop speech from speech-dispatcher, if that is playing. Right-click the speech bubble for language, power, another hotkey, or to turn the chime off. The chime is the freedesktop sound `complete.oga` if that file is installed. A missing sound is skipped. `"chime_sound"` in the config can point at another file. The Copilot key stays on when another hotkey is set.

Log: `~/.local/state/ptt/ptt.log`

When the words go to a terminal, Cursor or Code window, a word said louder, higher or longer than the rest comes out as `*word*`, and an utterance that stands out from your usual voice ends with a note such as `[tone: loud, fast]` (`voice/speech/tone.py`). Other windows get the plain text. Only numbers about the voice are kept, in `~/.local/state/ptt/voice.json`. `"hotwords"` in the config lists names Whisper should expect; keep it short.

The listener writes `~/.local/state/ptt/phase` and `~/.local/state/ptt/target` (whether the paste went to an agent window, never the words) for a speech helper that should go quiet while you talk.

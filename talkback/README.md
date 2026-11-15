# Talkback

`talkback/` lets AI agents (Claude Code, Cursor, others that read a skill or rule) speak back through a local [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) voice on the CPU. Each call to `talkback/scripts/say.py` joins one queue (`voice/spool.py`), played by one worker (`talkback/scripts/worker.py`), so agents never talk over each other; each agent has its own voice and introduces itself when the speaker changes. When push-to-talk or hands-free hears you, it goes quiet, holds what it had not said, and the agent decides whether to carry on, acknowledge, or answer you instead.

```bash
./install.sh talkback agents   # the voice (downloads Kokoro, ~330 MB) + instructions for Claude Code, Cursor, Codex
```

`agents/` holds what the agents need to know, in one text for all of them (`AGENTS-snippet.md`); `./install.sh agents` wires it into Claude Code, Cursor and Codex if they are installed, and `agents/README.md` shows where other agents look. Say "talk back" to an agent to turn it on.

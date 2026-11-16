# Henchline bridge for Cursor

A Cursor extension that lets Henchline open a particular agent chat before typing a phone message into it, so
each Cursor IDE chat is its own chat on the phone. Each Cursor window listens on a private Unix socket under
`~/.local/state/ptt/cursor-bridge/` and accepts one request, `{"open": "<chat id>"}`, which runs Cursor's own
`composer.openComposer` for that chat. A chat's id is the `CURSOR_CONVERSATION_ID` its commands run with.

Installed by `./install.sh agents` when Cursor is there (packs it with `pack.py`, no Node tools needed, and runs
`cursor --install-extension`). A window picks it up without a reload.

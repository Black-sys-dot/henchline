# Henchline bridge for Cursor

Without it, Henchline can only type into the Cursor chat that happens to be open. With it, each Cursor IDE chat is
its own chat on the phone: named by Cursor's own title, and what you text, record or switch reaches that very chat.

## Install

```bash
./install.sh agents      # with Cursor installed: packs the extension and runs `cursor --install-extension`
```

No Node tools are needed (`pack.py` makes the `.vsix`). A Cursor window picks it up without a reload; after an
update, run **Developer: Restart Extension Host** once (Ctrl+Shift+P) when no chat is in the middle of work.

## How it works

- A Cursor chat's commands run with `CURSOR_CONVERSATION_ID` (its chat id) and `CURSOR_WORKSPACE_LABEL`. Henchline
  keys the chat by the first (`cursor-<id>`) and names it by Cursor's title for it (read only from Cursor's store,
  `state.vscdb`), so several chats in one window are told apart.
- Each Cursor window runs the extension, which listens on a private socket in
  `~/.local/state/ptt/cursor-bridge/` (folder 0700, socket 0600) and accepts exactly two requests:
  `{"open": "<chat id>"}` (Cursor's `composer.openComposer`) and `{"cycle": "<chat id>"}` (Cursor's
  `composer.cycleModel`, only when that chat is the one selected). No text and no other command.
- Cursor says yes to any id, so Henchline picks the window itself: the one whose workspace folder is the project
  holding that chat's transcript (`~/.cursor/projects/<project>/agent-transcripts/<id>`). No such window, or two:
  nothing is typed, and the phone says why.
- Then Henchline types into the window as before (the chat box, paste, Enter).
- A model switch steps Cursor's own "cycle model" on that chat, reading the chat's model back from Cursor's store
  after each step, and stops on the model picked, or the moment anything looks wrong (another chat changed, no
  change, back where it started).

## Limits

- Linux and X11, like the rest of Henchline. A locked screen can't be typed into.
- Cursor's internal commands and its store's layout aren't a public API: a Cursor update can change them, and then
  the bridge refuses rather than typing into the wrong chat.

# Henchline bridge for Cursor

Without it, Henchline can only type into the Cursor chat that happens to be open. With it, each Cursor IDE chat is
its own chat on the phone: named by Cursor's own title, and what you text, record or switch reaches that very chat.

## Install

```bash
./install.sh agents      # with Cursor installed: packs the extension and runs `cursor --install-extension`
```

No Node tools are needed (`pack.py` makes the `.vsix`). A Cursor window picks up a first install without a reload;
after an update, run **Developer: Reload Window** (Ctrl+Shift+P) when no chat is in the middle of work (restarting
the extension host alone keeps the old version).

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
- The phone shows each chat's model (with its effort) from Cursor's store, and switches it with Cursor's own "Switch
  to Model Slug" (`{"model": "<chat id>", "config": {...}}`), only when that chat is the selected one. As when the
  model is changed in Cursor itself, it also becomes Cursor's default for new chats. The effort and fast picked
  go with it, and the chat's context size stays when the model does. The chat's model is read back from the store:
  the phone says so if Cursor didn't take it.
- The phone's stop button runs Cursor's own stop (`composer.cancelComposerStep`) on that chat, once the bridge has
  brought it to the front and it is the selected one.

## Limits

- Linux and X11, like the rest of Henchline. A locked screen can't be typed into.
- Cursor's internal commands and its store's layout aren't a public API: a Cursor update can change them, and then
  the bridge refuses rather than typing into the wrong chat.

## Thought bubbles

While a Cursor IDE chat is open on the phone, its thinking (what Cursor's IDE lets you expand under each step) is
read from Cursor's store and shown as dashed thought bubbles. Only while you look: never pushed, never a
notification, not kept in the chat. Settings → Show agents' thinking turns them off.

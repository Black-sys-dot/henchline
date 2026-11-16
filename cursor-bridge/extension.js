// Henchline's bridge inside Cursor: each window listens on a private Unix socket and takes a few requests about
// one agent chat, by its id: {"open"} brings it to the front (Cursor's composer.openComposer), {"cycle"} moves it
// to its next model (composer.cycleModel), {"stop"} stops what it's doing (composer.cancelComposerStep),
// {"model", "config"} sets its model
// (cursorai.action.switchToModelSlug). Henchline writes into the chat itself (through Cursor's debugging port,
// else with keys). Nothing else is accepted: no text, no other command. The socket and its note are only the
// user's (0700 folder, 0600 files), under ~/.local/state/ptt/cursor-bridge/.
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const DIR = path.join(os.homedir(), ".local/state/ptt/cursor-bridge");
const CHAT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
let server = null;
let files = [];

// Cursor's stop and model commands act on its selected chat: the most recently focused of those open. Asked with
// {"focused": true}, Henchline has just focused this chat's box through Cursor's debugging port and seen it hold
// focus, so it is the selected one wherever its pane is; then it need only be open. Without
// that, the old rule: the chat must be the last listed (refuses a chat in the left pane, never the wrong one).
async function actsOn(chat, focused) {
  if (!focused) await vscode.commands.executeCommand("composer.openComposer", chat);
  const selected = await vscode.commands.executeCommand("composer.getOrderedSelectedComposerIds");
  const listed = Array.isArray(selected) ? selected : [];
  return { ok: focused ? listed.includes(chat) : listed[listed.length - 1] === chat, selected };
}

function activate(context) {
  fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
  const base = path.join(DIR, String(process.pid));
  const sock = base + ".sock";
  try { fs.unlinkSync(sock); } catch (_) {}
  server = net.createServer((conn) => {
    let buffered = "";
    conn.on("data", async (chunk) => {
      buffered += chunk.toString();
      if (buffered.length > 4096) { conn.destroy(); return; }  // one short line is all it ever takes
      if (!buffered.includes("\n")) return;
      let answer;
      try {
        const asked = JSON.parse(buffered.split("\n")[0]);
        if (asked.commands === "model") {
          // Which of Cursor's commands an extension can reach that name a model: to find a way to switch one
          const all = await vscode.commands.getCommands(true);
          conn.end(JSON.stringify({ ok: true, commands: all.filter((c) => /model/i.test(c)) }) + "\n");
          return;
        }
        if (typeof asked.stop === "string" && CHAT_ID.test(asked.stop)) {
          // The phone's stop button: that chat stops what it's doing, as its own stop button does
          // Cursor's stop takes no chat id and works on the selected chat, which must be this one
          const check = await actsOn(asked.stop, asked.focused === true);
          if (!check.ok) {
            conn.end(JSON.stringify({ ok: false, why: "another chat is selected", selected: check.selected }) + "\n");
            return;
          }
          await vscode.commands.executeCommand("composer.cancelComposerStep");
          conn.end(JSON.stringify({ ok: true }) + "\n");
          return;
        }
        if (typeof asked.model === "string" && CHAT_ID.test(asked.model) && asked.config && typeof asked.config.modelId === "string") {
          // A chat's model, by Cursor's own "Switch to Model Slug": it sets the selected chat's model and, as a
          // change made in Cursor itself does, the default for new chats
          const check = await actsOn(asked.model, asked.focused === true);
          if (!check.ok) {
            conn.end(JSON.stringify({ ok: false, why: "another chat is selected", selected: check.selected }) + "\n");
            return;
          }
          const config = { modelId: String(asked.config.modelId), params: Array.isArray(asked.config.params) ? asked.config.params : [] };
          await vscode.commands.executeCommand("cursorai.action.switchToModelSlug", { modelIdWithParams: JSON.stringify(config) });
          conn.end(JSON.stringify({ ok: true }) + "\n");
          return;
        }
        if (typeof asked.cycle === "string" && CHAT_ID.test(asked.cycle)) {
          // The chat's next model, as Cursor's own "cycle model" does: no keys, no text; Henchline reads the model
          // it lands on from Cursor's store and asks again until it's the one picked on the phone
          await vscode.commands.executeCommand("composer.openComposer", asked.cycle);
          // Cycle only when that chat is the one Cursor has selected now: its "cycle model" takes no chat id and
          // works on the selected one, which must not be another chat
          const selected = await vscode.commands.executeCommand("composer.getOrderedSelectedComposerIds");
          const first = Array.isArray(selected) ? selected[selected.length - 1] : undefined;
          if (first !== asked.cycle) {
            conn.end(JSON.stringify({ ok: false, why: "another chat is selected", selected }) + "\n");
            return;
          }
          await vscode.commands.executeCommand("composer.cycleModel");
          conn.end(JSON.stringify({ ok: true }) + "\n");
          return;
        }
        if (typeof asked.open !== "string" || !CHAT_ID.test(asked.open)) throw new Error("not a chat id");
        await vscode.commands.executeCommand("composer.openComposer", asked.open);
        // The chat words will be typed into must be the selected one now (Henchline picked this window by the
        // chat's transcript; a chat in another window, or focus moved, says no)
        // (Cursor lists every chat open side by side, in pane order, not the focused one last: a second chat
        // open beside it refused every text to the first, 2026-11-16. openComposer has just made this one the
        // selected chat in its pane, so it must be among them.)
        // {"strict": true} (Henchline about to type with keys, which go to the focused chat) keeps the old rule:
        // the last listed. Sending through Cursor's debugging port finds the box by id, so it needs only "open".
        const selected = await vscode.commands.executeCommand("composer.getOrderedSelectedComposerIds");
        const listed = Array.isArray(selected) ? selected : [];
        const open = asked.strict === true ? listed[listed.length - 1] === asked.open : listed.includes(asked.open);
        answer = open ? { ok: true } : { ok: false, why: "another chat is selected", selected };
      } catch (e) {
        answer = { ok: false, why: String(e && e.message ? e.message : e) };
      }
      conn.end(JSON.stringify(answer) + "\n");
    });
    conn.on("error", () => {});
  });
  server.listen(sock, () => {
    fs.chmodSync(sock, 0o600);
    const folders = (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
    fs.writeFileSync(base + ".json", JSON.stringify({ pid: process.pid, sock, workspace: vscode.workspace.name || "", folders }),
      { mode: 0o600 });
  });
  files = [sock, base + ".json"];
  context.subscriptions.push({ dispose: deactivate });
}

function deactivate() {
  if (server) { server.close(); server = null; }
  for (const f of files) { try { fs.unlinkSync(f); } catch (_) {} }
  files = [];
}

module.exports = { activate, deactivate };

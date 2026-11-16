// Henchline's bridge inside Cursor: each window listens on a private Unix socket and takes three requests about
// one agent chat, by its id: {"open"} brings it to the front (Cursor's composer.openComposer), {"cycle"} moves it
// to its next model (composer.cycleModel, only when it is the selected chat), {"stop"} stops what it's doing
// (composer.cancelComposerStep). Henchline then types into the chat as it always has. Nothing else is accepted:
// no text, no other command. The socket and its note are only the user's (0700 folder, 0600 files), under
// ~/.local/state/ptt/cursor-bridge/.
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const DIR = path.join(os.homedir(), ".local/state/ptt/cursor-bridge");
const CHAT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
let server = null;
let files = [];

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
        if (typeof asked.stop === "string" && CHAT_ID.test(asked.stop)) {
          // The phone's stop button: that chat stops what it's doing, as its own stop button does
          await vscode.commands.executeCommand("composer.openComposer", asked.stop);
          // As for cycle: Cursor's stop takes no chat id and works on the selected chat, which must be this one
          const selected = await vscode.commands.executeCommand("composer.getOrderedSelectedComposerIds");
          const last = Array.isArray(selected) ? selected[selected.length - 1] : undefined;
          if (last !== asked.stop) {
            conn.end(JSON.stringify({ ok: false, why: "another chat is selected", selected }) + "\n");
            return;
          }
          await vscode.commands.executeCommand("composer.cancelComposerStep");
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
        // Belt only: Cursor hands back a handle even for an id it doesn't have, so Henchline itself picks the
        // window whose project holds the chat's transcript (voice/desktop/cursor_bridge.py)
        const handle = await vscode.commands.executeCommand("composer.getComposerHandleById", asked.open);
        if (!handle) throw new Error("not a chat of this window");
        await vscode.commands.executeCommand("composer.openComposer", asked.open);
        answer = { ok: true };
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

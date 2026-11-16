// Henchline's bridge inside Cursor: each window listens on a private Unix socket and, asked {"open": "<chat id>"},
// brings that agent chat to the front (Cursor's own composer.openComposer). Henchline then types into it as it
// always has. Nothing else is accepted: no text, no other command. The socket and its note are only the user's
// (0700 folder, 0600 files), under ~/.local/state/ptt/cursor-bridge/.
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
        if (typeof asked.open !== "string" || !CHAT_ID.test(asked.open)) throw new Error("not a chat id");
        // Only a chat this window has: asked of every window, just the one that owns it says yes
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

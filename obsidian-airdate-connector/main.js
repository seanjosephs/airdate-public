/*
 * airdate connector. A deliberately small, desktop-only Obsidian companion.
 * It owns the authenticated Substack session and exposes only authenticated
 * loopback status/connect/draft endpoints. It never returns session material.
 *
 * Trust model:
 * - The Substack session lives in Obsidian's secret storage on this device.
 * - The bridge token also lives in secret storage, never in data.json, so it
 *   does not travel with a synced or committed vault. "Pair with airdate"
 *   writes the token into airdate's own secrets folder, the only other copy.
 * - Pairing pins the airdate folder and the Python that runs its
 *   substack_draft.py. /draft ignores any path a caller sends except the one
 *   draft file, which must sit inside the pinned folder's drafts/ directory.
 */
let obsidian = {};
try { obsidian = require("obsidian"); } catch { /* loaded outside Obsidian (tests) */ }
const { Plugin = class {}, Notice = class {}, Modal = class {}, Setting = class {} } = obsidian;
const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const DEFAULT_PORT = 17777;
const SESSION_SECRET = "airdate-substack-session";
const TOKEN_SECRET = "airdate-bridge-token";
const DRAFT_TIMEOUT_MS = 120000;

// What airdate learns from the draft subprocess. execFile's timeout kills the
// child with SIGTERM and reports no exit code; that used to come back as
// returncode 0 with empty stdout, which read as success with no draft id.
// A kill is a `timeout` here because this is the layer that sees it; the
// subprocess names `auth` itself in its stdout JSON; anything else is
// `transport`.
function classifyDraftResult(error, stdout, stderr) {
  let result;
  try { result = JSON.parse(stdout || "{}"); } catch { result = {}; }
  if (!result || typeof result !== "object" || Array.isArray(result)) result = {};
  const out = {
    ...result,
    ok: !error && result.ok !== false,
    stdout: String(stdout || "").slice(-4000),
    stderr: String(stderr || "").slice(-4000),
    returncode: error ? (typeof error.code === "number" ? error.code : null) : 0,
  };
  if (error && error.killed) {
    out.ok = false;
    out.error_kind = "timeout";
    out.returncode = null;
    out.message = `Substack draft command did not finish within ${Math.round(DRAFT_TIMEOUT_MS / 1000)}s. Check Substack for the draft before sending again.`;
  } else if (!out.ok) {
    if (!out.error_kind) out.error_kind = "transport";
    if (!out.message) out.message = (error && error.message) || "Substack draft was not created.";
  } else {
    delete out.error_kind;
  }
  return out;
}

// Check a pairing before pinning it. Returns {ok, message, airdateRoot, python}.
function validatePairing(airdateRoot, python) {
  const root = String(airdateRoot || "").trim();
  if (!root || !path.isAbsolute(root)) return { ok: false, message: "Enter the full path to your airdate folder." };
  let realRoot;
  try { realRoot = fs.realpathSync(root); } catch { return { ok: false, message: "That airdate folder does not exist." }; }
  if (!fs.existsSync(path.join(realRoot, "substack_draft.py")) || !fs.existsSync(path.join(realRoot, "server.py"))) {
    return { ok: false, message: "That folder does not look like airdate (no server.py and substack_draft.py)." };
  }
  const py = String(python || "").trim() || path.join(realRoot, ".venv-substack", "bin", "python");
  if (!path.isAbsolute(py) || !fs.existsSync(py)) {
    return { ok: false, message: `Python not found at ${py}. Run airdate's scripts/setup first.` };
  }
  return { ok: true, message: "", airdateRoot: realRoot, python: py };
}

// The one draft file a /draft call may name: a .md file inside the pinned
// folder's drafts/ directory, after resolving symlinks.
function resolveDraftFile(airdateRoot, file) {
  if (!airdateRoot) return { ok: false, message: "Pair with airdate first (command: Pair with airdate)." };
  let realFile;
  try { realFile = fs.realpathSync(String(file || "")); } catch { return { ok: false, message: "Draft file not found." }; }
  const draftsDir = path.join(airdateRoot, "drafts") + path.sep;
  if (!realFile.startsWith(draftsDir) || path.extname(realFile) !== ".md") {
    return { ok: false, message: "Draft path is outside airdate's drafts folder." };
  }
  return { ok: true, file: realFile };
}

function writePairingFile(dataDir, port, token) {
  const secrets = path.join(dataDir, "secrets");
  fs.mkdirSync(secrets, { recursive: true, mode: 0o700 });
  const target = path.join(secrets, "connector.json");
  const tmp = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ port, token }, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, target);
  return target;
}

function json(res, status, payload) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => { raw += chunk; if (raw.length > 1024 * 1024) req.destroy(); });
    req.on("end", () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error("Invalid JSON.")); } });
    req.on("error", reject);
  });
}

class PairModal extends Modal {
  constructor(app, plugin) {
    super(app);
    this.plugin = plugin;
    this.values = {
      airdateRoot: plugin.settings.airdateRoot || "",
      python: plugin.settings.python || "",
      dataDir: plugin.settings.dataDir || "",
    };
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl("h3", { text: "Pair with airdate" });
    contentEl.createEl("p", { text: "The connector will only ever run substack_draft.py from this folder, with this Python. It creates drafts and never publishes." });
    new Setting(contentEl).setName("airdate folder").setDesc("Full path to the folder with server.py")
      .addText((text) => text.setValue(this.values.airdateRoot).onChange((v) => { this.values.airdateRoot = v; }));
    new Setting(contentEl).setName("Python").setDesc("Leave blank for <airdate folder>/.venv-substack/bin/python")
      .addText((text) => text.setValue(this.values.python).onChange((v) => { this.values.python = v; }));
    new Setting(contentEl).setName("airdate data folder").setDesc("Leave blank for <airdate folder>/.airdate-data")
      .addText((text) => text.setValue(this.values.dataDir).onChange((v) => { this.values.dataDir = v; }));
    new Setting(contentEl).addButton((button) => button.setButtonText("Pair").setCta().onClick(async () => {
      const result = await this.plugin.pair(this.values);
      new Notice(result.message);
      if (result.ok) this.close();
    }));
  }

  onClose() { this.contentEl.empty(); }
}

class AirdateConnector extends Plugin {
  async onload() {
    const loaded = (await this.loadData()) || {};
    this.settings = Object.assign({ port: DEFAULT_PORT, connectedAt: "", airdateRoot: "", python: "", dataDir: "" }, loaded);
    // Earlier versions kept the bridge token in data.json, which syncs with the
    // vault. Drop it; pairing again issues a new one into secret storage.
    if ("bridgeToken" in this.settings) {
      delete this.settings.bridgeToken;
      await this.saveData(this.settings);
    }
    this.addCommand({ id: "pair-airdate", name: "Pair with airdate", callback: () => new PairModal(this.app, this).open() });
    this.addCommand({ id: "connect-substack", name: "Connect Substack for airdate", callback: () => this.openLogin() });
    this.addCommand({ id: "disconnect-substack", name: "Disconnect Substack for airdate", callback: () => this.disconnect() });
    this.server = http.createServer((req, res) => { void this.handle(req, res); });
    this.server.listen(this.settings.port, "127.0.0.1", () => new Notice(`airdate connector ready on localhost:${this.settings.port}`));
    this.server.on("error", (error) => new Notice(`airdate connector could not start: ${error.message}`));
  }

  onunload() { if (this.server) this.server.close(); }

  token() { return String(this.app.secretStorage.getSecret(TOKEN_SECRET) || ""); }

  authenticated(req) {
    const expected = Buffer.from(this.token());
    if (!expected.length) return false;
    const supplied = Buffer.from(String(req.headers["x-airdate-token"] || ""));
    return supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected);
  }

  hasSession() { return Boolean(this.app.secretStorage.getSecret(SESSION_SECRET)); }

  async pair(values) {
    const checked = validatePairing(values.airdateRoot, values.python);
    if (!checked.ok) return checked;
    const dataDir = String(values.dataDir || "").trim() || path.join(checked.airdateRoot, ".airdate-data");
    if (!path.isAbsolute(dataDir)) return { ok: false, message: "Write the data folder as a full path." };
    const token = crypto.randomBytes(32).toString("hex");
    try {
      writePairingFile(dataDir, this.settings.port, token);
    } catch (error) {
      return { ok: false, message: `Could not write the pairing file: ${error.message || error}` };
    }
    this.app.secretStorage.setSecret(TOKEN_SECRET, token);
    Object.assign(this.settings, { airdateRoot: checked.airdateRoot, python: checked.python, dataDir });
    await this.saveData(this.settings);
    return { ok: true, message: "Paired with airdate. Reload airdate's settings to see the connection." };
  }

  async handle(req, res) {
    if (!this.authenticated(req)) return json(res, 401, { ok: false, error: "Unauthorized connector request." });
    if (req.method === "GET" && req.url === "/status") {
      return json(res, 200, { ok: true, paired: Boolean(this.settings.airdateRoot), connected: this.hasSession(), connected_at: this.settings.connectedAt || "" });
    }
    if (req.method !== "POST") return json(res, 404, { ok: false, error: "Not found." });
    if (req.url === "/connect") { this.openLogin(); return json(res, 202, { ok: true, pending: true, message: "Complete Substack sign-in in the Obsidian window." }); }
    if (req.url === "/draft") {
      try { return json(res, 200, await this.sendDraft(await readBody(req))); }
      catch (error) {
        const message = error.message || String(error);
        return json(res, 400, { ok: false, error_kind: error.error_kind || "transport", error: message, message });
      }
    }
    return json(res, 404, { ok: false, error: "Not found." });
  }

  async openLogin() {
    try {
      const electron = window.require("electron");
      const remote = electron.remote || electron;
      const authSession = remote.session.fromPartition("persist:airdate-substack-auth");
      const authWindow = new remote.BrowserWindow({ width: 520, height: 760, title: "Connect Substack to airdate", webPreferences: { nodeIntegration: false, contextIsolation: true, session: authSession } });
      authWindow.setMenuBarVisibility(false);
      let captured = false;
      const capture = async () => {
        if (captured || authWindow.isDestroyed()) return captured;
        const cookies = await authWindow.webContents.session.cookies.get({ domain: ".substack.com" });
        const parts = cookies.filter((cookie) => cookie.name === "substack.sid" || cookie.name === "connect.sid" || cookie.name.startsWith("substack"))
          .map((cookie) => `${cookie.name}=${cookie.value}`);
        if (!parts.some((part) => part.startsWith("substack.sid=") || part.startsWith("connect.sid="))) return false;
        captured = true;
        this.app.secretStorage.setSecret(SESSION_SECRET, parts.join("; "));
        this.settings.connectedAt = new Date().toISOString();
        await this.saveData(this.settings);
        new Notice("Substack connected to airdate.");
        authWindow.close();
        return true;
      };
      const check = () => { void capture().catch(() => {}); };
      authWindow.webContents.on("did-navigate", check);
      authWindow.webContents.on("did-navigate-in-page", check);
      authWindow.webContents.on("did-finish-load", check);
      // Google sign-in can complete without a top-level navigation event in
      // Electron. Poll the isolated session while the window is open so the
      // connector captures the resulting Substack session either way.
      const interval = window.setInterval(check, 1500);
      authWindow.on("closed", () => window.clearInterval(interval));
      authWindow.loadURL("https://substack.com/sign-in");
    } catch (error) { new Notice(`Could not open Substack sign-in: ${error.message || error}`); }
  }

  async disconnect() {
    this.app.secretStorage.setSecret(SESSION_SECRET, "");
    this.settings.connectedAt = "";
    await this.saveData(this.settings);
    new Notice("Substack disconnected from airdate.");
  }

  async sendDraft(payload) {
    const draft = resolveDraftFile(this.settings.airdateRoot, payload.file);
    if (!draft.ok) throw new Error(draft.message);
    const session = this.app.secretStorage.getSecret(SESSION_SECRET);
    if (!session) {
      const missing = new Error("Connect Substack in Obsidian before sending from airdate.");
      missing.error_kind = "auth";
      throw missing;
    }
    const script = path.join(this.settings.airdateRoot, "substack_draft.py");
    return new Promise((resolve) => execFile(this.settings.python, [script, "--file", draft.file], { env: { ...process.env, SUBSTACK_COOKIES: session }, timeout: DRAFT_TIMEOUT_MS, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
      resolve(classifyDraftResult(error, stdout, stderr));
    }));
  }
}

module.exports = AirdateConnector;
module.exports.classifyDraftResult = classifyDraftResult;
module.exports.validatePairing = validatePairing;
module.exports.resolveDraftFile = resolveDraftFile;
module.exports.writePairingFile = writePairingFile;

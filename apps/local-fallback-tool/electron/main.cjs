// Electron main process — Channel Collector desktop shell.
// Quản lý subprocess collect.py, scheduler hẹn giờ, config và log.
const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT_DIR    = path.join(__dirname, "../../..");
const COLLECT_PY    = path.join(__dirname, "..", "collect.py");
const CHANNELS_DB_PY = path.join(__dirname, "..", "channels_db.py");
const CONFIG_PATH   = path.join(ROOT_DIR, ".dev", "collector-config.json");
const LOG_PATH      = path.join(ROOT_DIR, ".dev", "collector.log");
const STATUS_PATH   = path.join(ROOT_DIR, ".dev", "collector-status.json");
const LOG_TAIL_LINES = 200;

/** @type {import('child_process').ChildProcess|null} */
let collectorProcess = null;
/** @type {BrowserWindow|null} */
let mainWindow = null;
/** Trạng thái khi collector được scheduler tự khởi động. */
let schedAutoState = null; // { platforms: string[], startedAt: number, endsAt: number }
let schedTickId = null;

// ── Collector spawn ──────────────────────────────────────────────────────────

function killCollector() {
  if (!collectorProcess) return;
  const pid = collectorProcess.pid;
  if (process.platform === "win32") spawn("taskkill", ["/pid", String(pid), "/T", "/F"]);
  else collectorProcess.kill("SIGTERM");
  collectorProcess = null;
}

function buildCollectorArgs(cfg, platformsOverride) {
  const args = [COLLECT_PY];
  if (cfg.gateway)         args.push("--gateway",         cfg.gateway);
  if (cfg.api_key)         args.push("--api-key",         cfg.api_key);
  if (cfg.checkview_dir)   args.push("--checkview-dir",   cfg.checkview_dir);
  if (cfg.youtube_api_key) args.push("--youtube-api-key", cfg.youtube_api_key);
  if (cfg.fb_cookie)       args.push("--fb-cookie",       cfg.fb_cookie);
  if (cfg.proxy)           args.push("--proxy",           cfg.proxy);
  if (cfg.poll_interval)   args.push("--poll-interval",   String(cfg.poll_interval));
  if (cfg.max_concurrent)  args.push("--max-concurrent",  String(cfg.max_concurrent));
  args.push("--status-file", STATUS_PATH);

  const platforms = platformsOverride || ["youtube", "tiktok", "facebook"].filter((p) => {
    const pc = cfg.platforms?.[p];
    return pc === undefined || pc.enabled !== false;
  });
  if (platforms.length > 0 && platforms.length < 3)
    args.push("--platforms", platforms.join(","));
  return args;
}

function spawnCollector(cfg, platformsOverride) {
  if (collectorProcess) return { ok: false, reason: "already_running" };
  fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
  const logStream = fs.createWriteStream(LOG_PATH, { flags: "a" });
  const pythonBin = process.platform === "win32" ? "python" : "python3";
  const child = spawn(pythonBin, buildCollectorArgs(cfg, platformsOverride), {
    cwd: path.dirname(COLLECT_PY), windowsHide: true,
  });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.on("exit", () => { logStream.end(); if (collectorProcess === child) collectorProcess = null; });
  child.on("error", (err) => {
    logStream.write(`[electron] spawn error: ${err.message}\n`);
    logStream.end();
    if (collectorProcess === child) collectorProcess = null;
  });
  collectorProcess = child;
  return { ok: true, pid: child.pid };
}

// ── Scheduler ────────────────────────────────────────────────────────────────

function tickSchedule() {
  // Kiểm tra auto-stop khi hết thời gian lịch
  if (schedAutoState && collectorProcess) {
    if (Date.now() >= schedAutoState.endsAt) {
      killCollector();
      schedAutoState = null;
      mainWindow?.webContents.send("collector:schedule:event", { type: "auto_stopped" });
    }
    return;
  }
  // Đang chạy thủ công — không schedule
  if (collectorProcess) return;

  const cfg = readConfigFile();
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

  const toStart = ["youtube", "tiktok", "facebook"].filter((p) => {
    const pc = cfg.platforms?.[p];
    return pc?.enabled && pc?.schedule_enabled && Array.isArray(pc?.schedule_times) && pc.schedule_times.includes(hhmm);
  });
  if (!toStart.length) return;

  const result = spawnCollector(cfg, toStart);
  if (result.ok) {
    const maxDur = Math.max(...toStart.map((p) => cfg.platforms?.[p]?.schedule_duration_min ?? 60));
    schedAutoState = { platforms: toStart, startedAt: Date.now(), endsAt: Date.now() + maxDur * 60_000 };
    mainWindow?.webContents.send("collector:schedule:event", { type: "auto_started", platforms: toStart, time: hhmm });
  }
}

// ── Window ───────────────────────────────────────────────────────────────────

function readConfigFile() {
  try { return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8")); }
  catch { return {}; }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 920, height: 640,
    title: "Channel Collector",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  if (app.isPackaged) mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  else mainWindow.loadURL("http://localhost:5176");
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(() => {
  createWindow();
  schedTickId = setInterval(tickSchedule, 30_000);
});

app.on("window-all-closed", () => {
  if (schedTickId) { clearInterval(schedTickId); schedTickId = null; }
  killCollector();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => { killCollector(); });

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle("collector:start", (_event, cfg) => {
  schedAutoState = null; // Manual start clears auto state
  return spawnCollector(cfg || readConfigFile());
});

ipcMain.handle("collector:stop", () => {
  schedAutoState = null;
  killCollector();
  return { ok: true };
});

ipcMain.handle("collector:status", () => ({
  running: !!collectorProcess,
  pid: collectorProcess?.pid ?? null,
  autoState: schedAutoState ? {
    platforms: schedAutoState.platforms,
    startedAt: schedAutoState.startedAt,
    endsAt: schedAutoState.endsAt,
  } : null,
}));

ipcMain.handle("collector:log", () => {
  try {
    const lines = fs.readFileSync(LOG_PATH, "utf-8").split(/\r?\n/);
    return lines.slice(-LOG_TAIL_LINES).join("\n");
  } catch { return ""; }
});

ipcMain.handle("collector:config:read", () => {
  const data = readConfigFile();
  const defP = { enabled: true, interval: 30, schedule_enabled: false, schedule_times: [], schedule_duration_min: 60 };
  const defaultPlatforms = {
    youtube:  { ...defP },
    tiktok:   { ...defP, interval: 60 },
    facebook: { ...defP, enabled: false, interval: 120 },
  };
  const merge = (key) => ({ ...defaultPlatforms[key], ...(data.platforms?.[key] || {}) });
  return {
    gateway:         data.gateway          || "",
    api_key:         data.api_key          || "",
    youtube_api_key: data.youtube_api_key  || "",
    checkview_dir:   data.checkview_dir    || "",
    fb_cookie:       data.fb_cookie        || "",
    proxy:           data.proxy            || "",
    poll_interval:   data.poll_interval    ?? 30,
    max_concurrent:  data.max_concurrent   ?? 3,
    platforms: { youtube: merge("youtube"), tiktok: merge("tiktok"), facebook: merge("facebook") },
  };
});

ipcMain.handle("collector:config:write", (_event, cfg) => {
  const merged = { ...readConfigFile(), ...cfg };
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), "utf-8");
  return { ok: true };
});

/** Nạp file kết nối Admin cấp cùng key dùng chung ({format:"manager-connection/1", gateway, api_key}). Không log secret. */
function parseConnectionFile(text) {
  let data;
  try { data = JSON.parse(text); } catch { return { error: "File không phải JSON hợp lệ" }; }
  if (!data || data.format !== "manager-connection/1") return { error: "Sai định dạng (cần format=manager-connection/1)" };
  if (typeof data.gateway !== "string" || !/^https?:\/\//.test(data.gateway)) return { error: "File thiếu gateway (http/https)" };
  if (typeof data.api_key !== "string" || !data.api_key.startsWith("mpk_")) return { error: "File thiếu api_key hợp lệ" };
  return { gateway: data.gateway.replace(/\/$/, ""), api_key: data.api_key };
}

ipcMain.handle("collector:import-connection", async () => {
  const pick = await dialog.showOpenDialog(mainWindow, {
    title: "Chọn file kết nối do Admin cấp", properties: ["openFile"], filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (pick.canceled || !pick.filePaths[0]) return { ok: false, canceled: true };
  let text;
  try { text = fs.readFileSync(pick.filePaths[0], "utf-8"); } catch { return { ok: false, error: "Không đọc được file" }; }
  const parsed = parseConnectionFile(text);
  if (parsed.error) return { ok: false, error: parsed.error };
  const merged = { ...readConfigFile(), gateway: parsed.gateway, api_key: parsed.api_key };
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), "utf-8");
  machineInstallation = null;   // key mới => bind/installation mới
  return { ok: true, gateway: parsed.gateway };
});

ipcMain.handle("collector:health", async () => {
  const cfg = readConfigFile();
  if (!cfg.gateway) return { ok: false, reason: "Chưa cấu hình Gateway URL" };
  try {
    const res = await fetch(`${cfg.gateway.replace(/\/$/, "")}/health/ready`, { signal: AbortSignal.timeout(4000) });
    return { ok: res.ok, status: res.status };
  } catch (e) { return { ok: false, reason: e.message }; }
});

/** installation_id đã bind cho api_key hiện tại (device binding: mọi request máy ngoài /handshake phải gửi X-Installation-Id). */
let machineInstallation = null; // { apiKey: string, installationId: string }

/**
 * Handshake CÙNG thiết bị với collect.py (device_id `shared-collector-<hostname>`, cùng capabilities) để lấy lại
 * installation_id đã bind — 1 credential chỉ kích hoạt cho 1 thiết bị, handshake lặp lại trên cùng thiết bị là idempotent.
 */
async function machineInstallationId(cfg, base) {
  if (machineInstallation && machineInstallation.apiKey === cfg.api_key) return machineInstallation.installationId;
  const host = require("os").hostname();
  const res = await fetch(`${base}/v1/integrations/handshake`, {
    method: "POST",
    headers: { Authorization: `Bearer ${cfg.api_key}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      installation: { device_id: `shared-collector-${host}`, hostname: host, os: "windows", app_version: "collect.py/1.0" },
      platforms: [],
      capabilities: ["data.collect"],
    }),
    signal: AbortSignal.timeout(6000),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body?.error?.message ?? `Handshake lỗi HTTP ${res.status}`);
  machineInstallation = { apiKey: cfg.api_key, installationId: body.data.installation_id };
  return machineInstallation.installationId;
}

ipcMain.handle("collector:api:get", async (_event, apiPath) => {
  const cfg = readConfigFile();
  if (!cfg.gateway || !cfg.api_key) return { error: "Chưa cấu hình gateway / api_key" };
  const base = cfg.gateway.replace(/\/$/, "");
  try {
    const installationId = await machineInstallationId(cfg, base);
    const res = await fetch(`${base}${apiPath}`, {
      headers: { Authorization: `Bearer ${cfg.api_key}`, "X-Installation-Id": installationId },
      signal: AbortSignal.timeout(6000),
    });
    return await res.json();
  } catch (e) { return { error: e.message }; }
});

ipcMain.handle("collector:status:read", () => {
  try { return JSON.parse(fs.readFileSync(STATUS_PATH, "utf-8")); }
  catch { return null; }
});

// ── Channels DB helpers ───────────────────────────────────────────────────────

/**
 * Spawn channels_db.py với JSON command qua stdin, trả về JSON result từ stdout.
 * Mỗi call tạo một Python process ngắn hạn (~200ms), phù hợp vì số lần gọi ít.
 * @param {object} cmd
 * @returns {Promise<object>}
 */
function callChannelsDb(cmd) {
  return new Promise((resolve, reject) => {
    const pythonBin = process.platform === "win32" ? "python" : "python3";
    const child = spawn(pythonBin, [CHANNELS_DB_PY], {
      windowsHide: true,
      stdio: "pipe",
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (d) => stdout.push(d));
    child.stderr.on("data", (d) => stderr.push(d));
    child.on("close", (code) => {
      const out = Buffer.concat(stdout).toString("utf-8").trim();
      if (!out) {
        const err = Buffer.concat(stderr).toString("utf-8").trim();
        reject(new Error(err || `channels_db.py thoát với code ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(out));
      } catch {
        reject(new Error(`channels_db.py trả về JSON không hợp lệ: ${out.slice(0, 300)}`));
      }
    });
    child.on("error", (err) => reject(new Error(`Không tìm thấy Python: ${err.message}`)));
    try {
      child.stdin.write(JSON.stringify(cmd), "utf-8");
      child.stdin.end();
    } catch (err) {
      reject(err);
    }
  });
}

// ── Channels IPC ──────────────────────────────────────────────────────────────

ipcMain.handle("channels:init", () =>
  callChannelsDb({ action: "init" }));

ipcMain.handle("channels:users:save", (_event, users) =>
  callChannelsDb({ action: "save_users", users }));

ipcMain.handle("channels:users:list", () =>
  callChannelsDb({ action: "list_users" }));

ipcMain.handle("channels:pull", (_event, { owner_id, owner_username, channels }) =>
  callChannelsDb({ action: "save_channels", owner_id, owner_username, channels }));

ipcMain.handle("channels:list", (_event, filters) =>
  callChannelsDb({ action: "list_channels", ...(filters ?? {}) }));

ipcMain.handle("channels:platform:counts", () =>
  callChannelsDb({ action: "platform_counts" }));

ipcMain.handle("channels:delete", (_event, ids) =>
  callChannelsDb({ action: "delete_channels", ids }));

ipcMain.handle("channels:set-enabled", (_event, { ids, enabled }) =>
  callChannelsDb({ action: "set_enabled", ids, enabled }));

/**
 * Đăng nhập lấy JWT dùng cho /v1/ops/* (require_ops: admin/dash_admin).
 * Dùng gateway URL từ collector-config hoặc url được truyền vào.
 */
ipcMain.handle("channels:login", async (_event, { url, username, password }) => {
  const base = (url || readConfigFile().gateway || "").replace(/\/$/, "");
  if (!base) return { ok: false, error: "Chưa cấu hình Gateway URL (tab Thu thập → Cấu hình)" };
  try {
    const res = await fetch(`${base}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      signal: AbortSignal.timeout(8000),
    });
    const data = await res.json();
    if (!res.ok) return { ok: false, error: data.detail || data.message || `Lỗi ${res.status}` };
    return { ok: true, access_token: data.access_token, user: data.user ?? { username } };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

/**
 * Gọi /v1/ops/* bằng user JWT (khác với collector:api:get dùng machine api_key).
 */
ipcMain.handle("channels:api-call", async (_event, { path, token }) => {
  const base = readConfigFile().gateway?.replace(/\/$/, "") || "";
  if (!base) return { error: "Chưa cấu hình Gateway URL (tab Thu thập → Cấu hình)" };
  if (!token) return { error: "Chưa đăng nhập — cần JWT admin" };
  try {
    const res = await fetch(`${base}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return { error: err.detail || err.message || `HTTP ${res.status}` };
    }
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
});

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
/** Python con trên Windows mặc định ghi stdout/stderr bằng codepage hệ thống (cp1252): tiếng Việt làm UnicodeEncodeError.
 *  Mọi giao tiếp với Python đều là UTF-8 (JSON qua stdin/stdout, log). */
const PY_ENV = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };

// Địa chỉ máy chủ nhúng sẵn + danh sách endpoint hệ thống: nguồn sự thật duy nhất, dùng chung với system_api.py.
const SYSTEM = require("../system-endpoints.json");
const SYSTEM_API_PY = path.join(__dirname, "..", "system_api.py");
/** Gateway đang dùng: cấu hình người dùng (file kết nối / nhập tay) nếu có, không thì địa chỉ nhúng sẵn. */
const gatewayOf = (cfg) => String((cfg && cfg.gateway) || SYSTEM.default_gateway).replace(/\/$/, "");

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
  args.push("--gateway", gatewayOf(cfg));
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
    cwd: path.dirname(COLLECT_PY), windowsHide: true, env: PY_ENV,
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
  // Mốc giờ của lịch tính theo GIỜ VIỆT NAM (report_timezone cố định), không phụ thuộc múi giờ của máy chạy tool.
  const hhmm = new Intl.DateTimeFormat("en-GB", { timeZone: SYSTEM.report_timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(new Date());

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
  const defP = { enabled: true, schedule_enabled: false, schedule_times: [], schedule_duration_min: 60 };
  const defaultPlatforms = {
    youtube:  { ...defP },
    tiktok:   { ...defP },
    facebook: { ...defP, enabled: false },
  };
  const merge = (key) => ({ ...defaultPlatforms[key], ...(data.platforms?.[key] || {}) });
  return {
    gateway:         data.gateway          || SYSTEM.default_gateway,
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
  return { ok: true, gateway: parsed.gateway };
});

/** Kiểm tra gateway còn sống bằng endpoint API thật (`health` trong system-endpoints.json), không phải trang web:
 *  `/health/ready` ở cổng web trả HTML 200 nên từng báo "khoẻ" cả khi API chết. */
ipcMain.handle("collector:health", async () => {
  const base = gatewayOf(readConfigFile());
  const ep = SYSTEM.endpoints.find((e) => e.name === "health");
  try {
    const res = await fetch(`${base}${ep.path}`, { signal: AbortSignal.timeout(4000) });
    const body = await res.json().catch(() => null);
    if (!body || body.ok !== true) return { ok: false, status: res.status, reason: "Địa chỉ này không phải API của hệ thống" };
    return { ok: res.ok, status: res.status };
  } catch (e) { return { ok: false, reason: e.message }; }
});

ipcMain.handle("collector:endpoints", () => ({ default_gateway: SYSTEM.default_gateway, gateway: gatewayOf(readConfigFile()),
                                                endpoints: SYSTEM.endpoints }));

/** Chạy một lệnh của system_api.py (key đi qua stdin, không lên dòng lệnh); trả JSON của lệnh hoặc `{ error }`. */
function runSystemApi(command) {
  return new Promise((resolve) => {
    const cfg = readConfigFile();
    const pythonBin = process.platform === "win32" ? "python" : "python3";
    const child = spawn(pythonBin, [SYSTEM_API_PY, command], { windowsHide: true, stdio: "pipe", env: PY_ENV });
    const out = [], err = [];
    child.stdout.on("data", (d) => out.push(d));
    child.stderr.on("data", (d) => err.push(d));
    child.on("error", (e) => resolve({ error: `Không chạy được Python: ${e.message}` }));
    child.on("close", () => {
      const text = Buffer.concat(out).toString("utf-8").trim();
      try { resolve(JSON.parse(text)); }
      catch { resolve({ error: Buffer.concat(err).toString("utf-8").trim().slice(-300) || "system_api.py không trả kết quả" }); }
    });
    child.stdin.end(JSON.stringify({ gateway: gatewayOf(cfg), api_key: cfg.api_key || "" }), "utf-8");
  });
}

/** Chạy thử từng endpoint an toàn. */
ipcMain.handle("collector:probe", () => runSystemApi("probe"));

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
      env: PY_ENV,
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

/** Fetch: kéo từ website CHỈ user/kênh dùng tool dùng chung (user có Tool riêng bị bỏ) rồi đồng bộ vào DB cục bộ.
 *  Kênh mới ở trạng thái CHƯA KÉO; trạng thái ĐÃ KÉO của kênh cũ được giữ nguyên. */
ipcMain.handle("channels:sync", async () => {
  const shared = await runSystemApi("shared");
  if (shared.error) return { error: shared.error };
  const res = await callChannelsDb({ action: "sync_shared", users: shared.owners, channels: shared.channels });
  return { ...res, skipped: shared.skipped };
});

ipcMain.handle("channels:users:list", () =>
  callChannelsDb({ action: "list_users" }));

/** Kéo các kênh đã chọn về tool (kênh đã kéo rồi bị bỏ qua, không kéo lại). */
ipcMain.handle("channels:pull", (_event, { ids }) =>
  callChannelsDb({ action: "pull_channels", ids }));

/** Gỡ kết nối kênh đã kéo (về lại CHƯA KÉO). */
ipcMain.handle("channels:unpull", (_event, { ids }) =>
  callChannelsDb({ action: "unpull_channels", ids }));

ipcMain.handle("channels:list", (_event, filters) =>
  callChannelsDb({ action: "list_channels", ...(filters ?? {}) }));

ipcMain.handle("channels:platform:counts", () =>
  callChannelsDb({ action: "platform_counts" }));

ipcMain.handle("channels:set-enabled", (_event, { ids, enabled }) =>
  callChannelsDb({ action: "set_enabled", ids, enabled }));

/**
 * Đăng nhập lấy JWT dùng cho /v1/ops/* (require_ops: admin/dash_admin).
 * Dùng gateway URL từ collector-config hoặc url được truyền vào.
 */
ipcMain.handle("channels:login", async (_event, { url, username, password }) => {
  const base = (url || gatewayOf(readConfigFile())).replace(/\/$/, "");
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
 * Gọi /v1/ops/* bằng user JWT admin (KHÔNG dùng machine api_key).
 */
ipcMain.handle("channels:api-call", async (_event, { path, token }) => {
  const base = gatewayOf(readConfigFile());
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

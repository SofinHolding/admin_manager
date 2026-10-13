// Electron main process — Tool B desktop shell. Quản lý subprocess collect.py, config
// `.dev/collector-config.json` và log `.dev/collector.log` ở ROOT_DIR (gốc monorepo).
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

// electron/ -> local-fallback-tool/ -> apps/ -> <ROOT_DIR>
const ROOT_DIR = path.join(__dirname, "../../..");
const COLLECT_PY = path.join(__dirname, "..", "collect.py");
const CONFIG_PATH = path.join(ROOT_DIR, ".dev", "collector-config.json");
const LOG_PATH = path.join(ROOT_DIR, ".dev", "collector.log");
const LOG_TAIL_LINES = 200;

/** @type {import('child_process').ChildProcess|null} */
let collectorProcess = null;
/** @type {BrowserWindow|null} */
let mainWindow = null;

function killCollector() {
  if (!collectorProcess) return;
  const pid = collectorProcess.pid;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"]);
  } else {
    collectorProcess.kill("SIGTERM");
  }
  collectorProcess = null;
}

function buildCollectorArgs(cfg) {
  const args = [COLLECT_PY];
  if (cfg.gateway) args.push("--gateway", cfg.gateway);
  if (cfg.api_key) args.push("--api-key", cfg.api_key);
  if (cfg.checkview_dir) args.push("--checkview-dir", cfg.checkview_dir);
  if (cfg.youtube_api_key) args.push("--youtube-api-key", cfg.youtube_api_key);
  if (cfg.poll_interval) args.push("--poll-interval", String(cfg.poll_interval));
  return args;
}

function readConfigFile() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 620,
    title: "Channel Collector",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    mainWindow.loadURL("http://localhost:5176");
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  killCollector();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  killCollector();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── IPC handlers ────────────────────────────────────────────────────────────

ipcMain.handle("collector:start", (_event, cfg) => {
  if (collectorProcess) {
    return { ok: true, pid: collectorProcess.pid };
  }
  fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
  const logStream = fs.createWriteStream(LOG_PATH, { flags: "a" });
  const pythonBin = process.platform === "win32" ? "python" : "python3";
  const child = spawn(pythonBin, buildCollectorArgs(cfg || {}), {
    cwd: path.dirname(COLLECT_PY),
    windowsHide: true,
  });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.on("exit", () => {
    logStream.end();
    if (collectorProcess === child) collectorProcess = null;
  });
  child.on("error", (err) => {
    logStream.write(`[electron] spawn error: ${err.message}\n`);
    logStream.end();
    if (collectorProcess === child) collectorProcess = null;
  });
  collectorProcess = child;
  return { ok: true, pid: child.pid };
});

ipcMain.handle("collector:stop", () => {
  killCollector();
  return { ok: true };
});

ipcMain.handle("collector:status", () => ({
  running: !!collectorProcess,
  pid: collectorProcess ? collectorProcess.pid : null,
}));

ipcMain.handle("collector:log", () => {
  try {
    const raw = fs.readFileSync(LOG_PATH, "utf-8");
    const lines = raw.split(/\r?\n/);
    return lines.slice(-LOG_TAIL_LINES).join("\n");
  } catch {
    return "";
  }
});

ipcMain.handle("collector:config:read", () => {
  const data = readConfigFile();
  return {
    gateway: data.gateway || "",
    api_key: data.api_key || "",
    youtube_api_key: data.youtube_api_key || "",
    checkview_dir: data.checkview_dir || "",
    poll_interval: data.poll_interval || 15,
  };
});

ipcMain.handle("collector:config:write", (_event, cfg) => {
  const existing = readConfigFile();
  const merged = { ...existing, ...cfg };
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), "utf-8");
  return { ok: true };
});

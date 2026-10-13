const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("collector", {
  start: (cfg) => ipcRenderer.invoke("collector:start", cfg),
  stop: () => ipcRenderer.invoke("collector:stop"),
  status: () => ipcRenderer.invoke("collector:status"),
  readLog: () => ipcRenderer.invoke("collector:log"),
  readConfig: () => ipcRenderer.invoke("collector:config:read"),
  writeConfig: (cfg) => ipcRenderer.invoke("collector:config:write", cfg),
});

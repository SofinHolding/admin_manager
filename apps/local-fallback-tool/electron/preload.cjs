const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("collector", {
  start:         (cfg)      => ipcRenderer.invoke("collector:start", cfg),
  stop:          ()         => ipcRenderer.invoke("collector:stop"),
  status:        ()         => ipcRenderer.invoke("collector:status"),
  readLog:       ()         => ipcRenderer.invoke("collector:log"),
  readConfig:    ()         => ipcRenderer.invoke("collector:config:read"),
  writeConfig:   (cfg)      => ipcRenderer.invoke("collector:config:write", cfg),
  /** Chọn file kết nối Admin cấp (gateway + api_key) và ghi vào cấu hình. */
  importConnection: ()      => ipcRenderer.invoke("collector:import-connection"),
  health:        ()         => ipcRenderer.invoke("collector:health"),
  apiGet:        (apiPath)  => ipcRenderer.invoke("collector:api:get", apiPath),
  readStatus:    ()         => ipcRenderer.invoke("collector:status:read"),
  /** Lắng nghe sự kiện scheduler (auto_started / auto_stopped). */
  onScheduleEvent: (cb) => ipcRenderer.on("collector:schedule:event", (_ev, data) => cb(data)),
});

contextBridge.exposeInMainWorld("channels", {
  /** Khởi tạo schema DB — gọi khi app mount. */
  init:
    ()                            => ipcRenderer.invoke("channels:init"),
  /** Lưu danh sách user fetch về từ API, trả về { added: [id], total: N }. */
  saveUsers:
    (users)                       => ipcRenderer.invoke("channels:users:save", users),
  /** Đọc tất cả user kèm channel_count từ DB. */
  listUsers:
    ()                            => ipcRenderer.invoke("channels:users:list"),
  /** Upsert kênh cho một user; trả về { added: N, updated: N }. */
  pull:
    (owner_id, owner_username, channels) =>
                                     ipcRenderer.invoke("channels:pull", { owner_id, owner_username, channels }),
  /** Trả về danh sách kênh; filters: { platform?, owner_id? }. */
  listChannels:
    (filters)                     => ipcRenderer.invoke("channels:list", filters ?? {}),
  /** Trả về { total, youtube, tiktok, facebook, other } counts. */
  platformCounts:
    ()                                    => ipcRenderer.invoke("channels:platform:counts"),
  /** Xóa kênh khỏi DB; ids: string[]. */
  deleteChannels:
    (ids)                                 => ipcRenderer.invoke("channels:delete", ids),
  /** Bật/tắt kênh; ids: string[], enabled: boolean. */
  setEnabled:
    (ids, enabled)                        => ipcRenderer.invoke("channels:set-enabled", { ids, enabled }),
  /**
   * Đăng nhập admin lấy JWT dùng cho /v1/ops/*.
   * Nếu url rỗng, dùng gateway từ collector config.
   */
  login:
    (url, username, password)             => ipcRenderer.invoke("channels:login", { url, username, password }),
  /** Gọi /v1/ops/* với JWT admin (KHÔNG dùng machine api_key). */
  apiCall:
    (path, token)                         => ipcRenderer.invoke("channels:api-call", { path, token }),
});

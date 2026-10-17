const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("collector", {
  start:         (cfg)      => ipcRenderer.invoke("collector:start", cfg),
  /** "Chạy ngay": một lượt rồi tự thoát. opts: { platforms?: string[], resource_ids?: string[] } — rỗng = mọi kênh đã kéo + đang bật. */
  runNow:        (opts)     => ipcRenderer.invoke("collector:run-now", opts ?? {}),
  stop:          ()         => ipcRenderer.invoke("collector:stop"),
  status:        ()         => ipcRenderer.invoke("collector:status"),
  readLog:       ()         => ipcRenderer.invoke("collector:log"),
  readConfig:    ()         => ipcRenderer.invoke("collector:config:read"),
  writeConfig:   (cfg)      => ipcRenderer.invoke("collector:config:write", cfg),
  /** Chọn file kết nối Admin cấp (gateway + api_key) và ghi vào cấu hình. */
  importConnection: ()      => ipcRenderer.invoke("collector:import-connection"),
  health:        ()         => ipcRenderer.invoke("collector:health"),
  /** Danh sách endpoint hệ thống đã ánh xạ + gateway đang dùng (system-endpoints.json). */
  endpoints:     ()         => ipcRenderer.invoke("collector:endpoints"),
  /** Chạy thử từng endpoint an toàn bằng cấu hình hiện tại (system_api.py probe). */
  probe:         ()         => ipcRenderer.invoke("collector:probe"),
  /** Kiểm tra từng YouTube API key / proxy còn sống không. kind: "youtube-keys" | "proxies"; items: chuỗi gốc. Trả { results:[{item,state,detail,ms,ip?}] } hoặc { error }. */
  checkPool:     (kind, items) => ipcRenderer.invoke("pool:check", { kind, items }),
  /** Thêm hàng loạt vào danh sách key/proxy (mỗi dòng một mục). Trả { items, added, duplicate, bad } hoặc { error }. */
  mergePool:     (kind, existing, text) => ipcRenderer.invoke("pool:merge", { kind, existing, text }),
  readStatus:    ()         => ipcRenderer.invoke("collector:status:read"),
  /** Lắng nghe sự kiện scheduler (auto_started / auto_stopped). */
  onScheduleEvent: (cb) => ipcRenderer.on("collector:schedule:event", (_ev, data) => cb(data)),
});

contextBridge.exposeInMainWorld("channels", {
  /** Khởi tạo schema DB — gọi khi app mount. */
  init:
    ()                            => ipcRenderer.invoke("channels:init"),
  /** Fetch: kéo user/kênh dùng tool dùng chung từ website vào DB cục bộ (kênh mới = chưa kéo). Trả thống kê + `skipped`, hoặc { error }. */
  sync:
    ()                            => ipcRenderer.invoke("channels:sync"),
  /** Đọc tất cả user kèm channel_count / pulled_count từ DB. */
  listUsers:
    ()                            => ipcRenderer.invoke("channels:users:list"),
  /** Kéo các kênh đã chọn về tool; kênh đã kéo bị bỏ qua. Trả { pulled, already, unknown }. */
  pull:
    (ids)                         => ipcRenderer.invoke("channels:pull", { ids }),
  /** Gỡ kết nối kênh đã kéo (về lại chưa kéo). Trả { unpulled }. */
  unpull:
    (ids)                         => ipcRenderer.invoke("channels:unpull", { ids }),
  /** Trả về danh sách kênh; filters: { platform?, owner_id?, mapping_status?, enabled_filter?, pulled_filter?, search? }. */
  listChannels:
    (filters)                     => ipcRenderer.invoke("channels:list", filters ?? {}),
  /** Trả về { total, youtube, tiktok, facebook, other } counts. */
  platformCounts:
    ()                                    => ipcRenderer.invoke("channels:platform:counts"),
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
  /** Lấy TẤT CẢ link video của một kênh (platform: "youtube" | "tiktok") về máy. Trả { ok, found, added, total, kind, via, attempts, export } hoặc { error }. */
  fetchLinks:
    (platform, channel_id, url)           => ipcRenderer.invoke("links:fetch", { platform, channel_id, url }),
  /** Link video đã lưu của một kênh. */
  listVideos:
    (channel_id)                          => ipcRenderer.invoke("channels:videos:list", { channel_id }),
  /** Mở thư mục chứa file link đã xuất. */
  revealLinks:
    (path)                                => ipcRenderer.invoke("links:reveal", { path }),
  /** Lưu danh sách link ra file .txt (hộp thoại chọn nơi lưu). */
  saveLinks:
    (name, urls)                          => ipcRenderer.invoke("links:save", { name, urls }),
});

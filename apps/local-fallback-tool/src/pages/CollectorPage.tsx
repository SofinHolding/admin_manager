/**
 * Channel Collector — điều khiển thu thập dữ liệu kênh (Electron desktop).
 * Luồng: claim collect-job → thu thập YT/TikTok/FB → push snapshot lên VPS.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

// ── Kiểu ─────────────────────────────────────────────────────────────────────

interface PlatformCfg {
  enabled:              boolean;
  interval:             number;
  schedule_enabled:     boolean;
  schedule_times:       string[];   // ["09:00", "15:00"]
  schedule_duration_min: number;
}

interface CollectorConfig {
  gateway:         string;
  api_key:         string;
  youtube_api_key: string;
  checkview_dir:   string;
  fb_cookie:       string;
  proxy:           string;
  poll_interval:   number;
  max_concurrent:  number;
  platforms: { youtube: PlatformCfg; tiktok: PlatformCfg; facebook: PlatformCfg };
}

interface RecentJob {
  job_id:       string;
  platform:     string;
  channel:      string;
  channel_id:   string;
  owner:        string;
  status:       "done" | "failed" | "running";
  items?:       number;
  error?:       string | null;
  duration_ms?: number;
  at:           string;
}

interface ChannelStat {
  channel:       string;
  platform:      string;
  owner:         string;
  total_ok:      number;
  total_fail:    number;
  last_ok?:      string | null;
  last_error?:   string | null;
  last_error_at?: string | null;
}

interface CollectorStatus {
  session_start: string;
  stats: { claimed: number; done: number; failed: number };
  recent_jobs:   RecentJob[];
  channels:      Record<string, ChannelStat>;
}

interface AutoState {
  platforms:  string[];
  startedAt:  number;
  endsAt:     number;
}

// ── Platform metadata ─────────────────────────────────────────────────────────

const PLATFORMS = [
  { key: "youtube"  as const, label: "YouTube",  color: "#ef4444", icon: "▶" },
  { key: "tiktok"   as const, label: "TikTok",   color: "#a855f7", icon: "♪" },
  { key: "facebook" as const, label: "Facebook", color: "#3b82f6", icon: "f" },
];

type PlatformKey = "youtube" | "tiktok" | "facebook";

// ── Defaults ──────────────────────────────────────────────────────────────────

const DEF_PLAT: PlatformCfg = { enabled: true, interval: 30, schedule_enabled: false, schedule_times: [], schedule_duration_min: 60 };

const DEFAULT_CONFIG: CollectorConfig = {
  gateway: "", api_key: "", youtube_api_key: "", checkview_dir: "",
  fb_cookie: "", proxy: "", poll_interval: 30, max_concurrent: 3,
  platforms: {
    youtube:  { ...DEF_PLAT },
    tiktok:   { ...DEF_PLAT, interval: 60 },
    facebook: { ...DEF_PLAT, enabled: false, interval: 120 },
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }); }
  catch { return iso.slice(11, 16) || "—"; }
}

function uptimeStr(startedAt: number | null): string {
  if (!startedAt) return "";
  const s = Math.floor((Date.now() - startedAt) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function nowHHMM(): string {
  const n = new Date();
  return `${String(n.getHours()).padStart(2, "0")}:${String(n.getMinutes()).padStart(2, "0")}`;
}

function minutesUntil(hhmm: string): number {
  const [th, tm] = hhmm.split(":").map(Number);
  const target = th * 60 + tm;
  const n = new Date();
  const cur = n.getHours() * 60 + n.getMinutes();
  return target >= cur ? target - cur : 1440 - cur + target;
}

function formatCountdown(minutes: number): string {
  if (minutes <= 0) return "bây giờ";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function colorLine(line: string): string {
  if (/\bERROR\b|\bFailed\b|\bException\b/i.test(line)) return "var(--red)";
  if (/\bWARNING\b|\bWARN\b/i.test(line)) return "var(--amber)";
  if (/claimed|completed|DONE|snapshot/i.test(line)) return "var(--green)";
  if (/started|handshake|registered/i.test(line)) return "var(--accent)";
  return "var(--muted)";
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function CollectorPage() {
  const isElectron = !!window.collector;

  // Runtime
  const [running,    setRunning]    = useState(false);
  const [pid,        setPid]        = useState<number | null>(null);
  const [startedAt,  setStartedAt]  = useState<number | null>(null);
  const [autoState,  setAutoState]  = useState<AutoState | null>(null);
  const [starting,   setStarting]   = useState(false);
  const [startError, setStartError] = useState("");
  const [uptime,     setUptime]     = useState("");

  // Config
  const [config,    setConfig]    = useState<CollectorConfig>(DEFAULT_CONFIG);
  const [dirty,     setDirty]     = useState(false);
  const [saving,    setSaving]    = useState(false);
  const [saveMsg,   setSaveMsg]   = useState("");
  const [configOpen,setConfigOpen]= useState(false);

  // Connection
  const [health,         setHealth]         = useState<"unknown" | "ok" | "fail">("unknown");
  const [healthMsg,      setHealthMsg]       = useState("");
  const [checkingHealth, setCheckingHealth]  = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  // Log
  const [logText, setLogText] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  // Status (per-channel stats)
  const [cStatus, setCStatus] = useState<CollectorStatus | null>(null);

  // Schedule "add time" draft per platform
  const [timeDraft, setTimeDraft] = useState<Record<string, string>>({});

  // Tabs
  const [tab, setTab] = useState<"log" | "channels" | "schedule" | "config">("log");

  // Channel tab filters
  const [filterPlat,   setFilterPlat]   = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  // Ticker for uptime + schedule countdown
  const [tick, setTick] = useState(0);

  // ── Init + polling ──────────────────────────────────────────────────────────

  const refreshStatus = useCallback(async () => {
    if (!isElectron) return;
    const s = await window.collector.status();
    setRunning(s.running);
    setPid(s.running ? s.pid : null);
    setAutoState(s.autoState ?? null);
  }, [isElectron]);

  const refreshLog = useCallback(async () => {
    if (!isElectron) return;
    setLogText(await window.collector.readLog() || "");
  }, [isElectron]);

  const refreshCStatus = useCallback(async () => {
    if (!isElectron || !window.collector?.readStatus) return;
    setCStatus(await window.collector.readStatus());
  }, [isElectron]);

  useEffect(() => {
    if (!isElectron) return;
    window.collector.readConfig().then((cfg: CollectorConfig) =>
      setConfig({
        ...DEFAULT_CONFIG, ...cfg,
        platforms: {
          youtube:  { ...DEF_PLAT, ...cfg.platforms?.youtube },
          tiktok:   { ...DEF_PLAT, interval: 60, ...cfg.platforms?.tiktok },
          facebook: { ...DEF_PLAT, enabled: false, interval: 120, ...cfg.platforms?.facebook },
        },
      })
    );
    refreshStatus();
    refreshLog();
    refreshCStatus();

    if (window.collector.onScheduleEvent) {
      window.collector.onScheduleEvent((ev: { type: string }) => {
        refreshStatus();
        if (ev.type === "auto_started") refreshLog();
      });
    }
  }, [isElectron, refreshStatus, refreshLog, refreshCStatus]);

  useEffect(() => {
    if (!isElectron) return;
    const id = setInterval(() => {
      refreshStatus();
      if (running) { refreshLog(); refreshCStatus(); }
    }, 3000);
    return () => clearInterval(id);
  }, [isElectron, running, refreshStatus, refreshLog, refreshCStatus]);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => { setUptime(uptimeStr(startedAt)); }, [tick, startedAt]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logText]);

  // Không dùng debounce auto-save vì có thể có stale configRef khi timeout fires.
  // Thay vào đó, setPlatCfg tự tính newCfg và writeConfig ngay lập tức.

  // ── Actions ──────────────────────────────────────────────────────────────────

  const handleStart = async () => {
    if (!isElectron) return;
    setStartError(""); setStarting(true);
    try {
      const res = await window.collector.start(config);
      if (res.ok) { setRunning(true); setPid(res.pid ?? null); setStartedAt(Date.now()); }
      else setStartError(res.reason || "Không thể khởi động");
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally { setStarting(false); }
  };

  const handleStop = async () => {
    if (!isElectron) return;
    await window.collector.stop();
    setRunning(false); setPid(null); setStartedAt(null); setAutoState(null);
    await refreshLog();
  };

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!isElectron) return;
    setSaving(true); setSaveMsg("");
    try {
      await window.collector.writeConfig(config);
      setSaveMsg("Đã lưu"); setDirty(false);
      setTimeout(() => setSaveMsg(""), 2500);
    } finally { setSaving(false); }
  };

  const handleHealth = async () => {
    if (!isElectron) return;
    setCheckingHealth(true); setHealth("unknown"); setHealthMsg("");
    try {
      const r = await window.collector.health();
      setHealth(r.ok ? "ok" : "fail");
      setHealthMsg(r.ok ? `Kết nối OK (${r.status ?? 200})` : (r.reason || "Không kết nối được"));
    } finally { setCheckingHealth(false); }
  };

  const setPlatCfg = useCallback((p: PlatformKey, patch: Partial<PlatformCfg>) => {
    const newCfg: CollectorConfig = {
      ...config,
      platforms: { ...config.platforms, [p]: { ...config.platforms[p], ...patch } },
    };
    setConfig(newCfg);
    setDirty(true);
    if (isElectron) {
      window.collector.writeConfig(newCfg)
        .then(() => { setSaveMsg("Đã lưu ✓"); setTimeout(() => setSaveMsg(""), 1500); })
        .catch(() => {});
    }
  }, [config, isElectron]);

  const setField = <K extends keyof CollectorConfig>(k: K, v: CollectorConfig[K]) => {
    setConfig(c => ({ ...c, [k]: v }));
    setDirty(true);
  };

  const addScheduleTime = (p: PlatformKey) => {
    const draft = timeDraft[p];
    if (!draft) return;
    const existing = config.platforms[p].schedule_times;
    if (!existing.includes(draft))
      setPlatCfg(p, { schedule_times: [...existing, draft].sort() });
    setTimeDraft(d => ({ ...d, [p]: "" }));
  };

  const removeScheduleTime = (p: PlatformKey, t: string) => {
    setPlatCfg(p, { schedule_times: config.platforms[p].schedule_times.filter(x => x !== t) });
  };

  // ── Derived: channel stats per platform ──────────────────────────────────────

  const channelsByPlatform: Record<string, ChannelStat[]> = { youtube: [], tiktok: [], facebook: [] };
  Object.values(cStatus?.channels ?? {}).forEach(ch => {
    if (channelsByPlatform[ch.platform]) channelsByPlatform[ch.platform].push(ch);
  });

  const platformStats: Record<string, { ok: number; fail: number }> = {};
  PLATFORMS.forEach(p => {
    const chs = channelsByPlatform[p.key];
    platformStats[p.key] = {
      ok:   chs.reduce((s, c) => s + (c.total_ok ?? 0), 0),
      fail: chs.reduce((s, c) => s + (c.total_fail ?? 0), 0),
    };
  });

  const globalStats = cStatus?.stats ?? { claimed: 0, done: 0, failed: 0 };

  // ── Derived: schedule entries (sorted) ───────────────────────────────────────

  const schedEntries = PLATFORMS.flatMap(p => {
    const pc = config.platforms[p.key];
    if (!pc.enabled || !pc.schedule_enabled || !pc.schedule_times.length) return [];
    return pc.schedule_times.map(t => ({
      key: p.key, label: p.label, color: p.color, icon: p.icon,
      time: t, duration_min: pc.schedule_duration_min,
      isPast: t < nowHHMM(),
    }));
  }).sort((a, b) => a.time.localeCompare(b.time));

  const nextEntry = schedEntries.find(e => !e.isPast) ?? null;

  // ── Channels tab data ─────────────────────────────────────────────────────────

  const allChannels = Object.values(cStatus?.channels ?? {});

  const filteredChannels = allChannels.filter(ch => {
    if (filterPlat !== "all" && ch.platform !== filterPlat) return false;
    if (filterStatus === "ok"   && ch.total_ok === 0)   return false;
    if (filterStatus === "fail" && ch.total_fail === 0) return false;
    return true;
  });

  // Group by owner
  const byOwner: Record<string, ChannelStat[]> = {};
  filteredChannels.forEach(ch => {
    const key = ch.owner || "unknown";
    (byOwner[key] ??= []).push(ch);
  });

  // ── Render ────────────────────────────────────────────────────────────────────

  const hasSchedule = schedEntries.length > 0;
  const isAutoRunning = !!autoState && running;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg)", overflow: "hidden" }}>

      {/* ── HEADER ── */}
      <header style={{
        flexShrink: 0, height: 52, display: "flex", alignItems: "center",
        justifyContent: "space-between", padding: "0 16px",
        background: "var(--surface)", borderBottom: "1px solid var(--border)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 9, height: 9, borderRadius: "50%", flexShrink: 0,
            background: running ? "var(--green)" : "#d1d5db",
            boxShadow: running ? "0 0 0 3px rgba(22,163,74,.15)" : "none",
          }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>Channel Collector</span>
          <span style={{
            fontSize: 11, fontWeight: 500, borderRadius: 4, padding: "2px 8px",
            background: running ? "rgba(22,163,74,.1)" : "#f3f4f6",
            color: running ? "var(--green)" : "#6b7280",
            border: `1px solid ${running ? "rgba(22,163,74,.25)" : "#e5e7eb"}`,
          }}>
            {isAutoRunning ? "Đang chạy (lịch)" : running ? "Đang chạy" : "Đã dừng"}
          </span>
          {running && uptime && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>{uptime}</span>
          )}
          {pid !== null && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>PID {pid}</span>
          )}
          {isAutoRunning && autoState && (
            <span style={{ fontSize: 11, color: "var(--accent)" }}>
              dừng lúc {new Date(autoState.endsAt).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {!isElectron && (
            <span style={{ fontSize: 11, color: "var(--amber)", padding: "0 8px" }}>Web preview</span>
          )}
          {running ? (
            <button onClick={handleStop} style={btnCss("danger")}>Dừng</button>
          ) : (
            <button onClick={handleStart} disabled={!isElectron || starting} style={btnCss("primary", !isElectron || starting)}>
              {starting ? "Đang khởi động…" : "Bắt đầu"}
            </button>
          )}
        </div>
      </header>

      {/* ── BODY ── */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

        {/* ── LEFT PANEL ── */}
        <aside style={{
          width: 300, flexShrink: 0, display: "flex", flexDirection: "column",
          borderRight: "1px solid var(--border)", overflow: "hidden",
        }}>
          <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>

            {/* Connection */}
            <PanelSection title="Kết nối">
              <div style={{ marginBottom: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border-light)" }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>Gateway</span>
                  <span style={{ fontSize: 11, color: "var(--muted)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "right" }}>
                    {config.gateway || <em>chưa cấu hình</em>}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "5px 0" }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>Trạng thái</span>
                  <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
                    <span style={{
                      width: 7, height: 7, borderRadius: "50%",
                      background: health === "ok" ? "var(--green)" : health === "fail" ? "var(--red)" : "#d1d5db",
                    }} />
                    <span style={{ color: health === "ok" ? "var(--green)" : health === "fail" ? "var(--red)" : "var(--muted)" }}>
                      {health === "ok" ? healthMsg : health === "fail" ? healthMsg : "Chưa kiểm tra"}
                    </span>
                  </span>
                </div>
              </div>
              <button onClick={handleHealth} disabled={!isElectron || checkingHealth} style={{ ...btnCss("ghost", !isElectron || checkingHealth), width: "100%", fontSize: 12 }}>
                {checkingHealth ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
              </button>
            </PanelSection>

            {/* Platforms */}
            <PanelSection title="Nền tảng">
              {PLATFORMS.map(p => {
                const pc = config.platforms[p.key];
                const ps = platformStats[p.key];
                const draft = timeDraft[p.key] ?? "";
                return (
                  <div key={p.key} style={{
                    marginBottom: 8, borderRadius: 6, overflow: "hidden",
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${pc.enabled ? p.color : "var(--border)"}`,
                    background: "var(--surface)",
                  }}>
                    {/* Card header */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 10px" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 7, cursor: "pointer", flex: 1 }}>
                        <input type="checkbox" checked={pc.enabled}
                               onChange={e => setPlatCfg(p.key, { enabled: e.target.checked })}
                               style={{ width: 14, height: 14, accentColor: p.color }} />
                        <span style={{ fontSize: 13, fontWeight: 500 }}>
                          <span style={{ color: p.color, marginRight: 4 }}>{p.icon}</span>
                          {p.label}
                        </span>
                      </label>
                      {(ps.ok > 0 || ps.fail > 0) && (
                        <span style={{ fontSize: 10, display: "flex", gap: 4 }}>
                          {ps.ok > 0 && <span style={{ color: "var(--green)" }}>✓{ps.ok}</span>}
                          {ps.fail > 0 && <span style={{ color: "var(--red)" }}>✗{ps.fail}</span>}
                        </span>
                      )}
                    </div>

                    {pc.enabled && (
                      <div style={{ padding: "0 10px 10px", borderTop: "1px solid var(--border-light)" }}>
                        {/* Wait interval */}
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
                          <span style={{ fontSize: 11, color: "var(--muted)", flex: 1 }}>Chờ khi trống</span>
                          <input type="number" min={5} max={3600} value={pc.interval}
                                 onChange={e => setPlatCfg(p.key, { interval: Number(e.target.value) })}
                                 style={{ ...inputCss, width: 60, textAlign: "right" }} />
                          <span style={{ fontSize: 11, color: "var(--muted)" }}>giây</span>
                        </div>

                        {/* Schedule toggle */}
                        <label style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 8, cursor: "pointer" }}>
                          <input type="checkbox" checked={pc.schedule_enabled}
                                 onChange={e => setPlatCfg(p.key, { schedule_enabled: e.target.checked })}
                                 style={{ width: 14, height: 14, accentColor: "var(--accent)" }} />
                          <span style={{ fontSize: 12, fontWeight: 500 }}>Hẹn giờ tự động</span>
                        </label>

                        {pc.schedule_enabled && (
                          <div style={{ marginTop: 8, paddingLeft: 21 }}>
                            {/* Time chips */}
                            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 5 }}>Chạy lúc:</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
                              {pc.schedule_times.length === 0 && (
                                <span style={{ fontSize: 11, color: "var(--muted)", fontStyle: "italic" }}>Chưa có giờ nào</span>
                              )}
                              {pc.schedule_times.map(t => (
                                <span key={t} style={{
                                  display: "inline-flex", alignItems: "center", gap: 3,
                                  background: "#e0e7ff", color: "#3730a3",
                                  borderRadius: 4, padding: "2px 6px", fontSize: 11, fontWeight: 500,
                                }}>
                                  {t}
                                  <button onClick={() => removeScheduleTime(p.key, t)}
                                          style={{ border: "none", background: "none", cursor: "pointer",
                                            color: "#6366f1", fontSize: 13, lineHeight: 1, padding: 0 }}>
                                    ×
                                  </button>
                                </span>
                              ))}
                            </div>
                            {/* Add time */}
                            <div style={{ display: "flex", gap: 4 }}>
                              <input type="time" value={draft}
                                     onChange={e => setTimeDraft(d => ({ ...d, [p.key]: e.target.value }))}
                                     style={{ ...inputCss, flex: 1, fontSize: 11 }} />
                              <button onClick={() => addScheduleTime(p.key)} disabled={!draft}
                                      style={btnCss("ghost", !draft)}>
                                + Thêm
                              </button>
                            </div>
                            {/* Duration */}
                            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 7 }}>
                              <span style={{ fontSize: 11, color: "var(--muted)", flex: 1 }}>Chạy trong</span>
                              <input type="number" min={1} max={480} value={pc.schedule_duration_min}
                                     onChange={e => setPlatCfg(p.key, { schedule_duration_min: Number(e.target.value) })}
                                     style={{ ...inputCss, width: 60, textAlign: "right" }} />
                              <span style={{ fontSize: 11, color: "var(--muted)" }}>phút</span>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </PanelSection>

            {/* Config */}
            <PanelSection title="Cấu hình">
              {startError && (
                <p style={{ fontSize: 11, color: "var(--red)", marginBottom: 6 }}>{startError}</p>
              )}
              <p style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
                {config.api_key
                  ? <span style={{ color: "var(--green)" }}>API key đã đặt.</span>
                  : <span style={{ color: "var(--amber)" }}>API key chưa cấu hình.</span>
                }
                {" "}
                {config.checkview_dir
                  ? "CheckView OK."
                  : <span style={{ color: "var(--amber)" }}>Thiếu CheckView Dir.</span>
                }
                {dirty && <span style={{ color: "var(--amber)" }}> · Chưa lưu.</span>}
              </p>
              <button onClick={() => setTab("config")}
                      style={{ ...btnCss(tab === "config" ? "primary" : "ghost"), width: "100%", fontSize: 12 }}>
                {tab === "config" ? "Đang xem cấu hình" : "Mở rộng cấu hình →"}
              </button>
            </PanelSection>

          </div>

          {/* Stats bar */}
          <div style={{
            flexShrink: 0, padding: "6px 14px",
            borderTop: "1px solid var(--border)",
            background: "var(--surface)",
            display: "flex", alignItems: "center",
          }}>
            {[
              { label: "Claimed",    value: globalStats.claimed, color: "var(--accent)" },
              { label: "Hoàn thành", value: globalStats.done,    color: "var(--green)"  },
              { label: "Lỗi",        value: globalStats.failed,  color: globalStats.failed > 0 ? "var(--red)" : "var(--muted)" },
            ].map(s => (
              <div key={s.label} style={{ flex: 1, textAlign: "center" }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: s.color, lineHeight: 1 }}>{s.value}</div>
                <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 1 }}>{s.label}</div>
              </div>
            ))}
            {/* Save status indicator */}
            <div style={{ fontSize: 10, textAlign: "right", minWidth: 80 }}>
              {saveMsg
                ? <span style={{ color: "var(--green)", fontWeight: 500 }}>{saveMsg}</span>
                : dirty
                  ? <span style={{ color: "var(--amber)" }}>Chưa lưu…</span>
                  : <span style={{ color: "var(--muted)" }}>Đã đồng bộ</span>
              }
            </div>
          </div>
        </aside>

        {/* ── RIGHT PANEL ── */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>

          {/* Tab bar */}
          <div style={{
            flexShrink: 0, display: "flex", alignItems: "stretch",
            borderBottom: "1px solid var(--border)", background: "var(--surface)",
            padding: "0 14px", gap: 0,
          }}>
            {(["log", "channels", "schedule", "config"] as const).map(t => {
              const labels: Record<string, string> = {
                log: "Nhật ký", channels: "Kênh & người dùng",
                schedule: "Lịch tự động", config: "Cấu hình",
              };
              return (
                <button key={t} onClick={() => setTab(t)} style={{
                  border: "none", background: "none", cursor: "pointer",
                  padding: "0 12px", height: 40, fontSize: 12, fontWeight: 500,
                  color: tab === t ? "var(--accent)" : "var(--muted)",
                  borderBottom: `2px solid ${tab === t ? "var(--accent)" : "transparent"}`,
                  display: "flex", alignItems: "center", gap: 4,
                  fontFamily: "inherit",
                }}>
                  {labels[t]}
                  {t === "schedule" && hasSchedule && (
                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--accent)" }} />
                  )}
                  {t === "config" && dirty && (
                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--amber)" }} />
                  )}
                </button>
              );
            })}
          </div>

          {/* ── TAB: Log ── */}
          {tab === "log" && (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
              <div style={{
                flexShrink: 0, height: 38,
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "0 14px", borderBottom: "1px solid var(--border-light)",
              }}>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  Activity log
                  {running && <span style={{ marginLeft: 8, fontSize: 10, color: "var(--green)" }}>● live</span>}
                </span>
                <div style={{ display: "flex", gap: 6 }}>
                  <button onClick={() => setLogText("")} disabled={!logText}
                          style={btnCss("ghost", !logText)}>Xóa</button>
                  <button onClick={refreshLog} disabled={!isElectron}
                          style={btnCss("ghost", !isElectron)}>Làm mới</button>
                </div>
              </div>
              {!running && !logText && (
                <div style={{ margin: "12px 14px 0", padding: "10px 12px", borderRadius: 6,
                  background: "#eff6ff", border: "1px solid #bfdbfe", fontSize: 11, color: "#1e40af", lineHeight: 1.6 }}>
                  <strong>Luồng hoạt động:</strong> Collector nhận lệnh (job) từ VPS → thu thập dữ liệu kênh → gửi kết quả lên VPS. Dữ liệu không kéo về máy này.
                </div>
              )}
              <pre ref={logRef} style={{
                flex: 1, margin: 0, padding: "10px 14px", overflow: "auto",
                fontFamily: "'Cascadia Code', Consolas, monospace",
                fontSize: 11.5, lineHeight: 1.65,
                whiteSpace: "pre-wrap", wordBreak: "break-all",
                background: "var(--bg)",
              }}>
                {logText
                  ? logText.split("\n").map((line, i) => (
                      <span key={i} style={{ display: "block", color: colorLine(line) }}>{line || "\u00a0"}</span>
                    ))
                  : <span style={{ color: "var(--muted)", fontStyle: "italic" }}>(chưa có log)</span>
                }
              </pre>
            </div>
          )}

          {/* ── TAB: Channels ── */}
          {tab === "channels" && (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
              {/* Info banner */}
              <div style={{ flexShrink: 0, margin: "10px 14px 0", padding: "10px 14px", borderRadius: 6,
                background: "#fffbeb", border: "1px solid #fde68a", fontSize: 11, color: "#92400e",
                display: "flex", alignItems: "flex-start", gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>Dữ liệu kênh trong phiên này</div>
                  <div style={{ lineHeight: 1.6 }}>
                    Collector claim job của tài khoản gắn với machine key này.
                    Sau khi bắt đầu thu thập, mỗi kênh được xử lý sẽ xuất hiện ở đây — bao gồm thông tin owner, platform, số lần OK/lỗi.
                    <br />
                    <strong>Xem toàn bộ người dùng &amp; kênh trên hệ thống:</strong> vào Manager Web → trang Admin.
                  </div>
                </div>
                {config.gateway && (
                  <button
                    onClick={() => { if (window.open) window.open(config.gateway.replace(/\/$/, "") + "/admin/dashboard", "_blank"); }}
                    style={{ ...btnCss("ghost"), flexShrink: 0, fontSize: 11, whiteSpace: "nowrap",
                      borderColor: "#f59e0b", color: "#92400e", background: "#fef3c7" }}
                  >
                    Mở Manager Web ↗
                  </button>
                )}
              </div>

              {/* Filter bar */}
              <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 8, padding: "8px 14px",
                borderBottom: "1px solid var(--border-light)" }}>
                <div style={{ display: "flex", gap: 4 }}>
                  {[["all", "Tất cả"], ["youtube", "YouTube"], ["tiktok", "TikTok"], ["facebook", "Facebook"]].map(([v, l]) => (
                    <button key={v} onClick={() => setFilterPlat(v)}
                            style={{ ...filterChip, background: filterPlat === v ? "var(--accent)" : "var(--surface2)",
                              color: filterPlat === v ? "#fff" : "var(--muted)" }}>
                      {l}
                    </button>
                  ))}
                </div>
                <div style={{ width: 1, height: 16, background: "var(--border)", flexShrink: 0 }} />
                <div style={{ display: "flex", gap: 4 }}>
                  {[["all", "Tất cả"], ["ok", "✓ OK"], ["fail", "✗ Lỗi"]].map(([v, l]) => (
                    <button key={v} onClick={() => setFilterStatus(v)}
                            style={{ ...filterChip, background: filterStatus === v ? "var(--accent)" : "var(--surface2)",
                              color: filterStatus === v ? "#fff" : "var(--muted)" }}>
                      {l}
                    </button>
                  ))}
                </div>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
                  {filteredChannels.length} kênh
                </span>
              </div>

              <div style={{ flex: 1, overflowY: "auto", padding: "0 14px 12px" }}>
                {allChannels.length === 0 ? (
                  /* Chưa có data: hướng dẫn rõ ràng */
                  <div style={{ marginTop: 28, padding: "20px 24px", borderRadius: 8,
                    background: "var(--surface)", border: "1px solid var(--border)", textAlign: "center" }}>
                    <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>
                      {running ? "Đang thu thập…" : "Chưa có dữ liệu kênh"}
                    </div>
                    {running ? (
                      <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 12px" }}>
                        Collector đang chờ job. Dữ liệu kênh sẽ xuất hiện sau khi job đầu tiên được xử lý xong.
                      </p>
                    ) : (
                      <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 14px" }}>
                        Bấm <strong>Bắt đầu</strong> để collector nhận job từ VPS và bắt đầu thu thập dữ liệu kênh.<br />
                        Mỗi kênh được xử lý sẽ hiện ở đây với trạng thái OK / Lỗi và thông tin người dùng sở hữu.
                      </p>
                    )}
                    {!running && (
                      <button onClick={handleStart} disabled={!isElectron || starting}
                              style={{ ...btnCss("primary", !isElectron || starting), height: 32, padding: "0 20px" }}>
                        {starting ? "Đang khởi động…" : "Bắt đầu thu thập"}
                      </button>
                    )}
                  </div>
                ) : filteredChannels.length === 0 ? (
                  <div style={{ paddingTop: 32, textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
                    Không có kênh nào khớp bộ lọc.
                  </div>
                ) : (
                  Object.entries(byOwner).map(([owner, chs]) => (
                    <div key={owner} style={{ marginTop: 12 }}>
                      {/* Owner header */}
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6,
                        paddingBottom: 4, borderBottom: "1px solid var(--border)" }}>
                        <span style={{ fontSize: 13 }}>👤</span>
                        <span style={{ fontSize: 12, fontWeight: 600, fontFamily: "monospace" }}>
                          {owner.length > 20 ? `${owner.slice(0, 8)}…${owner.slice(-8)}` : owner}
                        </span>
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>({chs.length} kênh)</span>
                      </div>
                      {/* Channel rows */}
                      {chs.map((ch, i) => {
                        const platMeta = PLATFORMS.find(p => p.key === ch.platform);
                        return (
                          <div key={i} style={{
                            display: "flex", alignItems: "center", gap: 8,
                            padding: "5px 8px", borderRadius: 5, marginBottom: 2,
                            background: ch.total_fail > 0 ? "#fff5f5" : "var(--surface)",
                            border: `1px solid ${ch.total_fail > 0 ? "#fecaca" : "var(--border)"}`,
                          }}>
                            <span style={{ fontSize: 12, color: platMeta?.color, flexShrink: 0, width: 14, textAlign: "center" }}>
                              {platMeta?.icon}
                            </span>
                            <span style={{ flex: 1, fontSize: 12, fontWeight: 500, overflow: "hidden",
                              textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {ch.channel || ch.platform}
                            </span>
                            <span style={{ fontSize: 11, color: "var(--green)", flexShrink: 0 }}>✓{ch.total_ok}</span>
                            <span style={{ fontSize: 11, color: ch.total_fail > 0 ? "var(--red)" : "var(--muted)", flexShrink: 0 }}>
                              ✗{ch.total_fail}
                            </span>
                            <span style={{ fontSize: 10, color: "var(--muted)", flexShrink: 0 }}>
                              {fmtTime(ch.last_ok || ch.last_error_at)}
                            </span>
                            {ch.last_error && (
                              <span style={{ fontSize: 10, color: "var(--red)", maxWidth: 120,
                                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                flexShrink: 0 }} title={ch.last_error}>
                                {ch.last_error.slice(0, 30)}…
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* ── TAB: Schedule ── */}
          {tab === "schedule" && (
            <div style={{ flex: 1, overflowY: "auto", padding: "14px" }}>
              {/* Auto-running banner */}
              {isAutoRunning && autoState && (
                <div style={{ marginBottom: 14, padding: "10px 14px", borderRadius: 8,
                  background: "rgba(59,91,219,.08)", border: "1px solid rgba(59,91,219,.2)" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 2 }}>
                    Đang chạy theo lịch
                  </div>
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>
                    Platforms: {autoState.platforms.map(p => PLATFORMS.find(x => x.key === p)?.label).join(", ")}
                    {" · "} Dừng lúc: {new Date(autoState.endsAt).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
                    {" (còn "}
                    {Math.max(0, Math.ceil((autoState.endsAt - Date.now()) / 60_000))}m)
                  </div>
                </div>
              )}

              {/* Next run */}
              {nextEntry ? (
                <div style={{ marginBottom: 14, padding: "14px 16px", borderRadius: 8,
                  background: "var(--surface)", border: "1px solid var(--border)",
                  display: "flex", alignItems: "center", gap: 14 }}>
                  <div style={{ textAlign: "center", minWidth: 64 }}>
                    <div style={{ fontSize: 26, fontWeight: 700, color: "var(--text)", lineHeight: 1 }}>
                      {nextEntry.time}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>lần tiếp theo</div>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 3 }}>
                      <span style={{ color: nextEntry.color }}>{nextEntry.icon}</span> {nextEntry.label}
                      {" · "}{nextEntry.duration_min} phút
                    </div>
                    <div style={{ fontSize: 11, color: "var(--accent)", fontWeight: 600 }}>
                      còn {formatCountdown(minutesUntil(nextEntry.time))}
                    </div>
                  </div>
                </div>
              ) : (
                <div style={{ marginBottom: 14, padding: "14px 16px", borderRadius: 8,
                  background: "var(--surface)", border: "1px solid var(--border)",
                  textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
                  {hasSchedule
                    ? "Tất cả lịch hôm nay đã qua. Lần tiếp theo vào ngày mai."
                    : "Chưa có lịch tự động. Bật Hẹn giờ trong từng nền tảng ở bảng trái."}
                </div>
              )}

              {/* Timeline hôm nay */}
              {schedEntries.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 8 }}>
                    Lịch hôm nay ({schedEntries.length} lần)
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {schedEntries.map((e, i) => (
                      <div key={i} style={{
                        display: "flex", alignItems: "center", gap: 10,
                        padding: "7px 10px", borderRadius: 6,
                        background: e.isPast ? "var(--surface2)" : "var(--surface)",
                        border: "1px solid var(--border)",
                        opacity: e.isPast ? 0.55 : 1,
                      }}>
                        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text)", minWidth: 44, fontFamily: "monospace" }}>
                          {e.time}
                        </span>
                        <span style={{ color: e.color, fontSize: 12 }}>{e.icon} {e.label}</span>
                        <span style={{ fontSize: 11, color: "var(--muted)", flex: 1 }}>{e.duration_min} phút</span>
                        {e.isPast ? (
                          <span style={{ fontSize: 10, color: "var(--muted)" }}>đã qua</span>
                        ) : (
                          <span style={{ fontSize: 10, color: "var(--accent)", fontWeight: 500 }}>
                            còn {formatCountdown(minutesUntil(e.time))}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Notes */}
              <div style={{ marginTop: 16, padding: "10px 12px", borderRadius: 6,
                background: "var(--surface2)", border: "1px solid var(--border)", fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                <strong>Lưu ý:</strong> Khi bạn bấm Bắt đầu thủ công, lịch tự động tạm dừng cho đến khi bạn dừng lại.
                Lịch chỉ tự khởi động khi collector đang ở trạng thái Đã dừng.
              </div>
            </div>
          )}
          {/* ── TAB: Config ── */}
          {tab === "config" && (
            <div style={{ flex: 1, overflowY: "auto" }}>
              <form onSubmit={handleSave} style={{ padding: "20px 28px", maxWidth: 700, margin: "0 auto" }}>

                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", marginBottom: 14 }}>Kết nối</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <CfgField label="Gateway URL">
                      <input value={config.gateway} onChange={e => setField("gateway", e.target.value)}
                             placeholder="http://127.0.0.1:57595" style={{ ...inputCss, height: 32 }} />
                    </CfgField>
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <CfgField label="API Key (machine credential)">
                      <div style={{ position: "relative" }}>
                        <input
                          type={showApiKey ? "text" : "password"}
                          value={config.api_key}
                          onChange={e => setField("api_key", e.target.value)}
                          placeholder="mpk_<key_id>.<secret>"
                          style={{ ...inputCss, height: 32, paddingRight: 68, fontFamily: showApiKey ? "inherit" : "monospace" }}
                        />
                        {/* Masked preview: hiển thị đầu/cuối khi ẩn */}
                        {!showApiKey && config.api_key.length > 12 && (
                          <span style={{
                            position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)",
                            fontSize: 12, color: "var(--text)", pointerEvents: "none",
                            background: "var(--surface2)", paddingRight: 4,
                            fontFamily: "monospace",
                          }}>
                            {config.api_key.slice(0, 8)}
                            <span style={{ color: "var(--muted)" }}>……</span>
                            {config.api_key.slice(-6)}
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => setShowApiKey(v => !v)}
                          title={showApiKey ? "Ẩn API key" : "Hiển thị API key"}
                          style={{
                            position: "absolute", right: 0, top: 0, bottom: 0,
                            width: 62, border: "none", background: "transparent",
                            cursor: "pointer", display: "flex", alignItems: "center",
                            justifyContent: "center", gap: 4, fontSize: 11,
                            color: "var(--muted)", borderLeft: "1px solid var(--border)",
                          }}
                        >
                          {showApiKey ? (
                            <>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
                                <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                                <line x1="1" y1="1" x2="23" y2="23"/>
                              </svg>
                              Ẩn
                            </>
                          ) : (
                            <>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                <circle cx="12" cy="12" r="3"/>
                              </svg>
                              Hiện
                            </>
                          )}
                        </button>
                      </div>
                    </CfgField>
                  </div>
                </div>

                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", marginBottom: 14 }}>Credentials nền tảng</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
                  <div>
                    <CfgField label="YouTube API Key">
                      <input value={config.youtube_api_key} onChange={e => setField("youtube_api_key", e.target.value)}
                             placeholder="AIza…" style={{ ...inputCss, height: 32 }} />
                    </CfgField>
                  </div>
                  <div>
                    <CfgField label="Proxy (tùy chọn)">
                      <input value={config.proxy} onChange={e => setField("proxy", e.target.value)}
                             placeholder="socks5://127.0.0.1:1080" style={{ ...inputCss, height: 32 }} />
                    </CfgField>
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <CfgField label="Facebook Cookie">
                      <textarea value={config.fb_cookie} onChange={e => setField("fb_cookie", e.target.value)}
                                placeholder="c_user=…; xs=…" rows={3}
                                style={{ ...inputCss, height: "auto", resize: "vertical", padding: "6px 9px", lineHeight: 1.5 }} />
                      <p style={{ fontSize: 10, color: "var(--muted)", margin: "3px 0 0" }}>
                        Lấy từ browser DevTools → Application → Cookies. Cần thiết để thu thập dữ liệu Facebook.
                      </p>
                    </CfgField>
                  </div>
                </div>

                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", marginBottom: 14 }}>Cài đặt nâng cao</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 8 }}>
                  <div>
                    <CfgField label="Chờ khi trống (giây)">
                      <input type="number" min={5} max={3600} value={config.poll_interval}
                             onChange={e => setField("poll_interval", Number(e.target.value))}
                             style={{ ...inputCss, height: 32, textAlign: "right" }} />
                    </CfgField>
                  </div>
                  <div>
                    <CfgField label="Job chạy song song">
                      <input type="number" min={1} max={10} value={config.max_concurrent}
                             onChange={e => setField("max_concurrent", Number(e.target.value))}
                             style={{ ...inputCss, height: 32, textAlign: "right" }} />
                    </CfgField>
                  </div>
                  <div>
                    <CfgField label="CheckView Dir">
                      <input value={config.checkview_dir} onChange={e => setField("checkview_dir", e.target.value)}
                             placeholder="D:/workspace/…" style={{ ...inputCss, height: 32 }} />
                    </CfgField>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
                  <button type="submit" disabled={!isElectron || saving}
                          style={{ ...btnCss("primary", !isElectron || saving), height: 34, padding: "0 20px", fontSize: 13 }}>
                    {saving ? "Đang lưu…" : "Lưu cấu hình"}
                  </button>
                  {dirty && !saveMsg && <span style={{ fontSize: 12, color: "var(--amber)" }}>Có thay đổi chưa lưu</span>}
                  {saveMsg && <span style={{ fontSize: 12, color: "var(--green)" }}>✓ {saveMsg}</span>}
                </div>
              </form>
            </div>
          )}

        </main>
      </div>
    </div>
  );
}

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputCss: React.CSSProperties = {
  width: "100%", height: 28, padding: "0 9px", borderRadius: 5,
  background: "var(--surface2)", border: "1px solid var(--border)",
  color: "var(--text)", fontSize: 12, outline: "none", fontFamily: "inherit",
};

const filterChip: React.CSSProperties = {
  padding: "3px 10px", borderRadius: 4, border: "none",
  cursor: "pointer", fontSize: 11, fontWeight: 500, fontFamily: "inherit",
};

function btnCss(variant: "primary" | "danger" | "ghost", disabled = false): React.CSSProperties {
  const base: React.CSSProperties = {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    padding: "0 11px", height: 28, borderRadius: 5, fontSize: 12, fontWeight: 500,
    border: "1px solid transparent", cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.4 : 1, fontFamily: "inherit",
  };
  if (variant === "primary") return { ...base, background: "var(--accent)", color: "#fff" };
  if (variant === "danger")  return { ...base, background: "#fef2f2", color: "var(--red)", borderColor: "#fecaca" };
  return { ...base, background: "var(--surface2)", color: "var(--muted)", borderColor: "var(--border)" };
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PanelSection({ title, children, action }: {
  title: string; children: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function CfgField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", marginBottom: 3 }}>{label}</div>
      {children}
    </div>
  );
}

// Extend Window for Electron IPC bridge
declare global {
  interface Window {
    collector?: {
      start:           (cfg: CollectorConfig) => Promise<{ ok: boolean; pid?: number; reason?: string }>;
      stop:            () => Promise<{ ok: boolean }>;
      status:          () => Promise<{ running: boolean; pid: number | null; autoState: AutoState | null }>;
      readLog:         () => Promise<string>;
      readConfig:      () => Promise<CollectorConfig>;
      writeConfig:     (cfg: CollectorConfig) => Promise<{ ok: boolean }>;
      health:          () => Promise<{ ok: boolean; status?: number; reason?: string }>;
      apiGet:          (path: string) => Promise<unknown>;
      readStatus:      () => Promise<CollectorStatus | null>;
      onScheduleEvent: (cb: (ev: { type: string; platforms?: string[]; time?: string }) => void) => void;
    };
  }
}

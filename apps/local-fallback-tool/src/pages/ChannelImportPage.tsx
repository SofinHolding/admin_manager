/**
 * Trang Kênh — local-fallback-tool.
 *
 * Dùng api_key máy (scope `jobs:claim`, cấu hình ở tab Thu thập) để lấy resource —
 * không cần đăng nhập admin riêng.
 *
 * Luồng:
 *  1. [Fetch] → GET /v1/integrations/resources (machine api_key) → trả resource
 *     của chủ sở hữu duy nhất của máy này.
 *  2. Lọc bỏ group, upsert vào DB cục bộ, phân loại platform tự động.
 *  3. Kênh trong DB: tìm kiếm, lọc, tích chọn, xóa hoặc bật/tắt hàng loạt.
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";

// ── Platform metadata ─────────────────────────────────────────────────────────

const PLATFORM_META: Record<string, { label: string; icon: string; color: string; bg: string }> = {
  youtube:  { label: "YouTube",  icon: "▶", color: "#ef4444", bg: "rgba(239,68,68,.12)" },
  tiktok:   { label: "TikTok",   icon: "♪", color: "#a855f7", bg: "rgba(168,85,247,.12)" },
  facebook: { label: "Facebook", icon: "f", color: "#3b82f6", bg: "rgba(59,130,246,.12)" },
  other:    { label: "Khác",     icon: "?", color: "#637082", bg: "rgba(99,112,130,.10)" },
};
const PLATFORM_ORDER = ["youtube", "tiktok", "facebook", "other"] as const;
type PlatformKey = (typeof PLATFORM_ORDER)[number] | "all";

// ── Resource trả về từ GET /v1/integrations/resources ──────────────────────────

interface ApiResourceItem {
  id: string;
  platform: string;
  resource_type: string;
  external_id: string;
  display_name: string;
  username: string;
  url: string;
  state: string;
  mapping_status: string;
  execution_mode: string;
  enabled: boolean;
}

// ── Gateway response type guards (envelope {ok,data,meta} hoặc lỗi IPC {error}) ─

/** Type guard: lỗi cấp IPC (gateway chưa cấu hình, network lỗi) — không có `ok`. */
function isIpcError(v: unknown): v is { error: string } {
  return !!v && typeof v === "object" && !("ok" in v) && "error" in v
    && typeof (v as Record<string, unknown>).error === "string";
}
/** Type guard: envelope lỗi từ API (`ok: false`). */
function isApiError(v: unknown): v is { ok: false; error: { message?: string; code?: string } } {
  return !!v && typeof v === "object" && (v as Record<string, unknown>).ok === false;
}
/** Type guard: envelope thành công chứa `data.items` (resources). */
function isResourcesOk(v: unknown): v is { ok: true; data: { items: ApiResourceItem[]; owner_account_id: string; is_shared: boolean } } {
  if (!v || typeof v !== "object") return false;
  const obj = v as Record<string, unknown>;
  if (obj.ok !== true || !obj.data || typeof obj.data !== "object") return false;
  return Array.isArray((obj.data as Record<string, unknown>).items);
}
/** Type guard: envelope thành công chứa `data.users` (resource-owners). */
function isOwnersOk(v: unknown): v is { ok: true; data: { users: { id: string; username: string; resource_count: number }[] } } {
  if (!v || typeof v !== "object") return false;
  const obj = v as Record<string, unknown>;
  if (obj.ok !== true || !obj.data || typeof obj.data !== "object") return false;
  return Array.isArray((obj.data as Record<string, unknown>).users);
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ChannelImportPage() {
  const isElectron = !!window.channels;

  // ── Users state ───────────────────────────────────────────────────────────

  const [dbUsers,     setDbUsers]     = useState<DbUser[]>([]);
  const [newUserIds,  setNewUserIds]  = useState<Set<string>>(new Set());
  const [selected,    setSelected]    = useState<Set<string>>(new Set());
  const [fetching,    setFetching]    = useState(false);
  const [fetchError,  setFetchError]  = useState("");
  const [pulling,     setPulling]     = useState(false);
  const [pullingId,   setPullingId]   = useState("");
  const [pullError,   setPullError]   = useState("");
  const [pullResults, setPullResults] = useState<Record<string, { added: number; updated: number }>>({});
  const [dbReady,     setDbReady]     = useState(false);

  // ── Channels state ────────────────────────────────────────────────────────

  const [channels,      setChannels]      = useState<LocalChannel[]>([]);
  const [platFilter,    setPlatFilter]    = useState<PlatformKey>("all");
  const [mappingFilter, setMappingFilter] = useState<"all" | "mapped" | "unmapped">("all");
  const [enabledFilter, setEnabledFilter] = useState<"all" | "on" | "off">("all");
  const [search,        setSearch]        = useState("");
  const [selectedCh,    setSelectedCh]    = useState<Set<string>>(new Set());
  const [chActionBusy,  setChActionBusy]  = useState(false);

  // ── Init ──────────────────────────────────────────────────────────────────

  const refreshChannels = useCallback(async () => {
    if (!isElectron) return;
    const res = await window.channels!.listChannels({
      platform:       platFilter === "all" ? undefined : platFilter,
      mapping_status: mappingFilter === "all" ? undefined : mappingFilter,
      enabled_filter: enabledFilter === "all" ? undefined : enabledFilter,
      search:         search || undefined,
    });
    setChannels(res.channels ?? []);
    setSelectedCh(new Set());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isElectron, platFilter, mappingFilter, enabledFilter, search]);

  const refreshUsers = useCallback(async () => {
    if (!isElectron) return;
    const res = await window.channels!.listUsers();
    setDbUsers(res.users ?? []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isElectron]);

  useEffect(() => {
    if (!isElectron) return;
    window.channels!.init()
      .then(() => { setDbReady(true); return Promise.all([refreshUsers(), refreshChannels()]); })
      .catch((e: unknown) => setFetchError(e instanceof Error ? e.message : String(e)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isElectron]);

  useEffect(() => { refreshChannels(); }, [refreshChannels]);

  // ── Fetch users via machine api_key (không cần login) ────────────────────

  const handleFetch = async () => {
    if (!isElectron || fetching || !dbReady) return;
    setFetching(true); setFetchError(""); setNewUserIds(new Set());
    try {
      const raw = await window.collector!.apiGet("/v1/integrations/resource-owners");
      if (isIpcError(raw)) throw new Error(raw.error);
      if (isApiError(raw)) throw new Error(raw.error.message ?? "Lỗi từ API");
      if (!isOwnersOk(raw)) throw new Error("Phản hồi không hợp lệ từ /resource-owners");
      const users = raw.data.users.map((u) => ({ id: u.id, username: u.username || u.id }));
      const saveRes = await window.channels!.saveUsers(users);
      setNewUserIds(new Set(saveRes.added ?? []));
      await refreshUsers();
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setFetching(false);
    }
  };

  // ── Pull channels cho user đã chọn (machine api_key) ─────────────────────

  const handlePull = async () => {
    if (!isElectron || selected.size === 0 || pulling) return;
    setPulling(true); setPullError(""); setPullResults({});
    const results: Record<string, { added: number; updated: number }> = {};
    try {
      for (const uid of selected) {
        const user = dbUsers.find((u) => u.id === uid);
        if (!user) continue;
        setPullingId(uid);
        const raw = await window.collector!.apiGet(
          `/v1/integrations/resources?owner_account_id=${encodeURIComponent(uid)}`,
        );
        if (isIpcError(raw) || isApiError(raw) || !isResourcesOk(raw)) {
          results[uid] = { added: -1, updated: -1 };
          continue;
        }
        // Chỉ kênh — bỏ group
        const channelItems: ApiInventoryItem[] = raw.data.items
          .filter((r) => r.resource_type !== "group")
          .map((r) => ({
            id: r.id, owner_account_id: uid, owner_username: user.username,
            platform: r.platform, resource_type: r.resource_type,
            canonical_external_id: r.external_id,
            input_ref: r.username || r.external_id,
            display_name: r.display_name, url: r.url, mapping_status: r.mapping_status,
            enabled: r.enabled,
          }));
        const pullRes = await window.channels!.pull(uid, user.username, channelItems);
        results[uid] = { added: pullRes.added ?? 0, updated: pullRes.updated ?? 0 };
      }
    } catch (e) {
      setPullError(e instanceof Error ? e.message : String(e));
    } finally {
      setPullingId(""); setPulling(false); setPullResults(results);
      await Promise.all([refreshUsers(), refreshChannels()]);
    }
  };

  // ── Channel bulk actions ──────────────────────────────────────────────────

  const chAction = async (action: "delete" | "enable" | "disable") => {
    if (!isElectron || selectedCh.size === 0 || chActionBusy) return;
    setChActionBusy(true);
    const ids = [...selectedCh];
    if (action === "delete") await window.channels!.deleteChannels(ids);
    else await window.channels!.setEnabled(ids, action === "enable");
    await Promise.all([refreshUsers(), refreshChannels()]);
    setChActionBusy(false);
  };

  // ── Derived ───────────────────────────────────────────────────────────────

  const platCounts: Record<string, number> = { all: 0, youtube: 0, tiktok: 0, facebook: 0, other: 0 };
  channels.forEach((c) => { platCounts.all++; if (c.platform in platCounts) platCounts[c.platform]++; });

  const channelsByOwner: Record<string, LocalChannel[]> = {};
  channels.forEach((c) => { (channelsByOwner[c.owner_id] ??= []).push(c); });

  let pullAddedTotal = 0; let pullUpdatedTotal = 0; let pullHasErr = false;
  Object.values(pullResults).forEach(({ added, updated }) => {
    if (added < 0) { pullHasErr = true; return; }
    pullAddedTotal += added; pullUpdatedTotal += updated;
  });

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden", background: "var(--bg)" }}>

      {/* ──────── Panel trái: Người dùng ──────── */}
      <aside style={{
        width: 280, flexShrink: 0, display: "flex", flexDirection: "column",
        borderRight: "1px solid var(--border)", overflow: "hidden",
      }}>
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>

          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", letterSpacing: ".04em" }}>
              NGƯỜI DÙNG
            </span>
            <button onClick={handleFetch} disabled={!isElectron || fetching || !dbReady} style={btnCss("ghost", !isElectron || fetching || !dbReady)}>
              {fetching ? "Đang tải…" : "⟳ Fetch"}
            </button>
          </div>

          {fetchError && (
            <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 8, padding: "7px 9px", background: "rgba(220,38,38,.06)", borderRadius: 5, border: "1px solid rgba(220,38,38,.2)" }}>
              {fetchError}
            </div>
          )}

          {!fetching && dbUsers.length === 0 && (
            <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: "24px 8px", lineHeight: 1.7 }}>
              Bấm <strong>Fetch</strong> để lấy danh sách người dùng.
            </div>
          )}

          {dbUsers.length > 0 && (
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <button onClick={() => setSelected(new Set(dbUsers.map((u) => u.id)))} style={{ ...btnCss("ghost"), fontSize: 10, height: 22, padding: "0 8px" }}>Chọn tất cả</button>
              {selected.size > 0 && <button onClick={() => setSelected(new Set())} style={{ ...btnCss("ghost"), fontSize: 10, height: 22, padding: "0 8px" }}>Bỏ chọn</button>}
            </div>
          )}

          {dbUsers.map((user) => {
            const isNew      = newUserIds.has(user.id);
            const isSel      = selected.has(user.id);
            const isPull     = pulling && pullingId === user.id;
            const result     = pullResults[user.id];
            const pullFailed = result && result.added < 0;
            return (
              <label key={user.id} style={{
                display: "flex", alignItems: "center", gap: 8, padding: "7px 8px",
                borderRadius: 5, cursor: "pointer", marginBottom: 3,
                background: isSel ? "rgba(59,91,219,.07)" : "transparent",
                border: `1px solid ${isSel ? "rgba(59,91,219,.25)" : "transparent"}`,
              }}>
                <input type="checkbox" checked={isSel}
                  onChange={(e) => { const s = new Set(selected); e.target.checked ? s.add(user.id) : s.delete(user.id); setSelected(s); }}
                  style={{ width: 14, height: 14, accentColor: "var(--accent)", flexShrink: 0 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.username}</div>
                  {!isPull && user.channel_count > 0 && <div style={{ fontSize: 10, color: "var(--muted)" }}>{user.channel_count} kênh</div>}
                  {isPull && <div style={{ fontSize: 10, color: "var(--accent)" }}>Đang kéo…</div>}
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2, flexShrink: 0 }}>
                  {isNew && <span style={{ fontSize: 9, fontWeight: 700, color: "#fff", background: "var(--accent)", borderRadius: 3, padding: "1px 5px" }}>MỚI</span>}
                  {result && !pullFailed && result.added > 0 && <span style={{ fontSize: 10, color: "var(--green)", fontWeight: 600 }}>+{result.added}</span>}
                  {pullFailed && <span style={{ fontSize: 10, color: "var(--red)" }}>lỗi</span>}
                </div>
              </label>
            );
          })}
        </div>

        {/* Footer: Kéo kênh */}
        <div style={{ flexShrink: 0, padding: "10px 14px", borderTop: "1px solid var(--border)", background: "var(--surface)" }}>
          {pullError && <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 6 }}>{pullError}</div>}
          {Object.keys(pullResults).length > 0 && !pulling && (
            <div style={{ fontSize: 11, marginBottom: 6, color: pullHasErr ? "var(--amber)" : "var(--green)" }}>
              {pullHasErr ? "⚠ " : "✓ "}+{pullAddedTotal} mới, cập nhật {pullUpdatedTotal}{pullHasErr ? " (có lỗi)" : ""}
            </div>
          )}
          <button onClick={handlePull} disabled={!isElectron || pulling || selected.size === 0}
            style={{ ...btnCss("primary", !isElectron || pulling || selected.size === 0), width: "100%", height: 32, fontSize: 12 }}>
            {pulling ? "Đang kéo kênh…" : selected.size > 0 ? `Kéo kênh (${selected.size} user)` : "Kéo kênh"}
          </button>
        </div>
      </aside>

      {/* ──────── Panel phải: Kênh ──────── */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>

        {/* ── Toolbar: search + filter ── */}
        <div style={{
          flexShrink: 0, padding: "8px 12px", borderBottom: "1px solid var(--border)",
          background: "var(--surface)", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
        }}>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="🔍  Tìm tên kênh / handle…"
            style={{ ...inputCss, width: 200, height: 26, fontSize: 11 }}
          />
          <PlatTab active={platFilter === "all"} count={platCounts.all} color="var(--muted)" onClick={() => setPlatFilter("all")}>Tất cả</PlatTab>
          {PLATFORM_ORDER.map((pk) => {
            const m = PLATFORM_META[pk];
            return (
              <PlatTab key={pk} active={platFilter === pk} count={platCounts[pk] ?? 0} color={m.color} onClick={() => setPlatFilter(pk)}>
                <span style={{ marginRight: 2 }}>{m.icon}</span>{m.label}
              </PlatTab>
            );
          })}
          <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
          {(["all", "mapped", "unmapped"] as const).map((v) => (
            <button key={v} onClick={() => setMappingFilter(v)} style={{ ...filterChip, background: mappingFilter === v ? "rgba(59,91,219,.12)" : "transparent", color: mappingFilter === v ? "var(--accent)" : "var(--muted)", fontWeight: mappingFilter === v ? 600 : 400 }}>
              {v === "all" ? "Mọi trạng thái" : v === "mapped" ? "✓ mapped" : "— chưa map"}
            </button>
          ))}
          <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
          {(["all", "on", "off"] as const).map((v) => (
            <button key={v} onClick={() => setEnabledFilter(v)} style={{ ...filterChip, background: enabledFilter === v ? "rgba(59,91,219,.12)" : "transparent", color: enabledFilter === v ? "var(--accent)" : "var(--muted)", fontWeight: enabledFilter === v ? 600 : 400 }}>
              {v === "all" ? "Tất cả" : v === "on" ? "⚡ Bật" : "○ Tắt"}
            </button>
          ))}
        </div>

        {/* ── Action bar (hiện khi có kênh được chọn) ── */}
        {selectedCh.size > 0 && (
          <div style={{ flexShrink: 0, padding: "6px 12px", background: "rgba(59,91,219,.06)", borderBottom: "1px solid rgba(59,91,219,.15)", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>{selectedCh.size} kênh đã chọn</span>
            <button onClick={() => setSelectedCh(new Set())} style={{ ...btnCss("ghost"), fontSize: 10, height: 22, padding: "0 8px" }}>Bỏ chọn</button>
            <div style={{ flex: 1 }} />
            <button onClick={() => chAction("enable")}  disabled={chActionBusy} style={{ ...btnCss("ghost", chActionBusy), fontSize: 11, height: 26, color: "var(--green)" }}>⚡ Bật</button>
            <button onClick={() => chAction("disable")} disabled={chActionBusy} style={{ ...btnCss("ghost", chActionBusy), fontSize: 11, height: 26 }}>○ Tắt</button>
            <button onClick={() => chAction("delete")}  disabled={chActionBusy} style={{ ...btnCss("ghost", chActionBusy), fontSize: 11, height: 26, color: "var(--red)" }}>✕ Xóa</button>
          </div>
        )}

        {/* ── Channel list ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "10px 12px" }}>
          {channels.length === 0 ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", minHeight: 160, color: "var(--muted)", textAlign: "center", gap: 8 }}>
              <span style={{ fontSize: 24, opacity: 0.2 }}>⚡</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>
                {search || platFilter !== "all" || mappingFilter !== "all" || enabledFilter !== "all"
                  ? "Không có kênh phù hợp bộ lọc"
                  : "Chưa có kênh nào trong DB"}
              </span>
            </div>
          ) : (
            Object.entries(channelsByOwner).map(([ownerId, chs]) => (
              <OwnerGroup
                key={ownerId}
                ownerName={chs[0]?.owner_username ?? ownerId}
                channels={chs}
                selectedCh={selectedCh}
                onToggleCh={(id) => { const s = new Set(selectedCh); s.has(id) ? s.delete(id) : s.add(id); setSelectedCh(s); }}
                onToggleAll={(ids, add) => { const s = new Set(selectedCh); ids.forEach((id) => (add ? s.add(id) : s.delete(id))); setSelectedCh(s); }}
              />
            ))
          )}
        </div>
      </main>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PlatTab({ active, count, color, onClick, children }: {
  active: boolean; count: number; color: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button onClick={onClick} style={{
      display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 9px",
      borderRadius: 4, cursor: "pointer", fontSize: 11, fontWeight: 500, fontFamily: "inherit",
      background: active ? "var(--accent)" : "var(--surface2)",
      color: active ? "#fff" : color,
      border: active ? "1px solid transparent" : "1px solid var(--border)",
    }}>
      {children}
      {count > 0 && <span style={{ fontSize: 10, opacity: active ? 0.85 : 0.6 }}>{count}</span>}
    </button>
  );
}

function OwnerGroup({ ownerName, channels, selectedCh, onToggleCh, onToggleAll }: {
  ownerName: string;
  channels: LocalChannel[];
  selectedCh: Set<string>;
  onToggleCh: (id: string) => void;
  onToggleAll: (ids: string[], add: boolean) => void;
}) {
  const [open, setOpen] = useState(true);
  const allIds = channels.map((c) => c.id);
  const allSelected = allIds.every((id) => selectedCh.has(id));
  const someSelected = !allSelected && allIds.some((id) => selectedCh.has(id));

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "3px 0 5px" }}>
        <input
          type="checkbox"
          checked={allSelected}
          ref={(el) => { if (el) el.indeterminate = someSelected; }}
          onChange={(e) => onToggleAll(allIds, e.target.checked)}
          style={{ width: 13, height: 13, accentColor: "var(--accent)", flexShrink: 0 }}
        />
        <button
          onClick={() => setOpen((o) => !o)}
          style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
        >
          <span style={{ fontSize: 10, color: "var(--muted)", width: 10 }}>{open ? "▾" : "▸"}</span>
          <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>{ownerName}</span>
          <span style={{ fontSize: 10, color: "var(--muted)" }}>· {channels.length} kênh</span>
        </button>
      </div>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingLeft: 20 }}>
          {channels.map((ch) => (
            <ChannelRow key={ch.id} ch={ch} selected={selectedCh.has(ch.id)} onToggle={() => onToggleCh(ch.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ChannelRow({ ch, selected, onToggle }: { ch: LocalChannel; selected: boolean; onToggle: () => void }) {
  const meta  = PLATFORM_META[ch.platform] ?? PLATFORM_META.other;
  const label = ch.display_name || ch.input_ref || ch.canonical_id || ch.id;

  let dateStr = "—";
  if (ch.imported_at) {
    try { dateStr = new Date(ch.imported_at).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" }); }
    catch { dateStr = ch.imported_at.slice(0, 10); }
  }

  return (
    <label style={{
      display: "flex", alignItems: "center", gap: 7, padding: "5px 8px",
      borderRadius: 5, cursor: "pointer",
      background: selected ? "rgba(59,91,219,.06)" : "var(--surface)",
      border: `1px solid ${selected ? "rgba(59,91,219,.2)" : "var(--border)"}`,
      opacity: ch.enabled ? 1 : 0.5,
    }}>
      <input type="checkbox" checked={selected} onChange={onToggle}
        style={{ width: 13, height: 13, accentColor: "var(--accent)", flexShrink: 0 }} />
      <span style={{ width: 20, height: 20, borderRadius: 4, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9, fontWeight: 700, background: meta.bg, color: meta.color }}>
        {meta.icon}
      </span>
      <span style={{ flex: 1, fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {!ch.enabled && <span style={{ fontSize: 9, color: "var(--muted)", marginRight: 4, verticalAlign: "middle" }}>○</span>}
        {label}
      </span>
      {ch.input_ref && ch.input_ref !== label && (
        <span style={{ fontSize: 10, color: "var(--muted)", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 0 }}>{ch.input_ref}</span>
      )}
      <span style={{
        fontSize: 10, padding: "1px 5px", borderRadius: 3, flexShrink: 0,
        background: ch.mapping_status === "mapped" ? "rgba(22,163,74,.1)" : "var(--bg)",
        color: ch.mapping_status === "mapped" ? "var(--green)" : "var(--muted)",
        border: `1px solid ${ch.mapping_status === "mapped" ? "rgba(22,163,74,.2)" : "var(--border)"}`,
      }}>
        {ch.mapping_status === "mapped" ? "mapped" : "chưa map"}
      </span>
      <span style={{ fontSize: 10, color: "var(--muted)", flexShrink: 0, minWidth: 56, textAlign: "right" }}>{dateStr}</span>
    </label>
  );
}

// ── Shared styles ─────────────────────────────────────────────────────────────

/** 4+ call sites with lockstep primary/ghost variants. */
function btnCss(variant: "primary" | "ghost", disabled = false): CSSProperties {
  const base: CSSProperties = {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    padding: "0 11px", height: 28, borderRadius: 5, fontSize: 12, fontWeight: 500,
    border: "1px solid transparent", cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.4 : 1, fontFamily: "inherit",
  };
  if (variant === "primary") return { ...base, background: "var(--accent)", color: "#fff" };
  return { ...base, background: "var(--surface2)", color: "var(--muted)", borderColor: "var(--border)" };
}

const inputCss: CSSProperties = {
  width: "100%", height: 28, padding: "0 9px", borderRadius: 5,
  background: "var(--surface2)", border: "1px solid var(--border)",
  color: "var(--text)", fontSize: 12, outline: "none", fontFamily: "inherit",
};

const filterChip: CSSProperties = {
  padding: "2px 8px", borderRadius: 4, border: "none",
  cursor: "pointer", fontSize: 11, fontFamily: "inherit",
};

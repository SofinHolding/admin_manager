/**
 * Trang Kênh — local-fallback-tool.
 *
 * Dùng api_key máy (cấu hình ở tab Thu thập) để lấy kênh từ website — không cần đăng nhập admin riêng.
 * CHỈ lấy user/kênh dùng tool dùng chung (execution_mode=fallback_tool). User có Tool riêng (kênh qua primary_tool) tự đẩy
 * dữ liệu bằng Tool của họ nên KHÔNG xuất hiện ở đây.
 *
 * Hai trạng thái trên mỗi kênh:
 *  - CHƯA KÉO: kênh hiện có trên website, người vận hành chưa chọn kéo về tool.
 *  - ĐÃ KÉO: đã chọn kéo về; tool giữ kết nối với kênh để đẩy dữ liệu lên. Kênh đã kéo KHÔNG kéo lại được (trạng thái nằm trong DB cục bộ
 *    nên đổi tab/khởi động lại vẫn giữ).
 *
 * Luồng:
 *  1. [Fetch] → làm tươi danh sách user + kênh từ website vào DB cục bộ (kênh mới = chưa kéo, kênh đã kéo giữ nguyên).
 *  2. Tích chọn từng kênh (hoặc cả nhóm / "chọn tất cả chưa kéo") → [Kéo N kênh].
 *  3. Kênh đã kéo: bật/tắt hoặc gỡ kết nối (về lại chưa kéo).
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";
import VideoLinksDialog from "../components/VideoLinksDialog";
import ChannelDataBadges from "../components/ChannelDataBadges";

// ── Platform metadata ─────────────────────────────────────────────────────────

const PLATFORM_META: Record<string, { label: string; icon: string; color: string; bg: string }> = {
  youtube:  { label: "YouTube",  icon: "▶", color: "#ef4444", bg: "rgba(239,68,68,.12)" },
  tiktok:   { label: "TikTok",   icon: "♪", color: "#a855f7", bg: "rgba(168,85,247,.12)" },
  facebook: { label: "Facebook", icon: "f", color: "#3b82f6", bg: "rgba(59,130,246,.12)" },
  other:    { label: "Khác",     icon: "?", color: "#637082", bg: "rgba(99,112,130,.10)" },
};
const PLATFORM_ORDER = ["youtube", "tiktok", "facebook", "other"] as const;
type PlatformKey = (typeof PLATFORM_ORDER)[number] | "all";
type PulledFilter = "all" | "unpulled" | "pulled";

// ── Component ─────────────────────────────────────────────────────────────────

export default function ChannelImportPage() {
  const isElectron = !!window.channels;

  const [dbReady,    setDbReady]    = useState(false);
  const [dbUsers,    setDbUsers]    = useState<DbUser[]>([]);
  const [all,        setAll]        = useState<LocalChannel[]>([]);
  const [newUserIds, setNewUserIds] = useState<Set<string>>(new Set());
  const [fetching,   setFetching]   = useState(false);
  const [fetchError, setFetchError] = useState("");
  const [fetchNote,  setFetchNote]  = useState("");
  const [busy,       setBusy]       = useState(false);
  const [pullMsg,    setPullMsg]    = useState<{ ok: boolean; text: string } | null>(null);
  const [linksBusy,  setLinksBusy]  = useState<string | null>(null);   // nhãn kênh đang lấy link
  const [linksOf,    setLinksOf]    = useState<LocalChannel | null>(null);

  // Bộ lọc (lọc phía giao diện: dữ liệu cục bộ chỉ vài trăm dòng)
  const [ownerFilter,   setOwnerFilter]   = useState("");
  const [platFilter,    setPlatFilter]    = useState<PlatformKey>("all");
  const [pulledFilter,  setPulledFilter]  = useState<PulledFilter>("all");
  const [mappingFilter, setMappingFilter] = useState<"all" | "mapped" | "unmapped">("all");
  const [enabledFilter, setEnabledFilter] = useState<"all" | "on" | "off">("all");
  const [search,        setSearch]        = useState("");
  const [selected,      setSelected]      = useState<Set<string>>(new Set());

  // ── Dữ liệu cục bộ ────────────────────────────────────────────────────────

  const reload = useCallback(async () => {
    if (!window.channels) return;
    const [u, c] = await Promise.all([window.channels.listUsers(), window.channels.listChannels({})]);
    setDbUsers(u.users ?? []);
    setAll(c.channels ?? []);
    // Chỉ giữ lựa chọn còn hợp lệ (kênh vừa bị dọn khỏi DB thì bỏ chọn)
    setSelected((prev) => new Set([...prev].filter((id) => (c.channels ?? []).some((ch) => ch.id === id))));
  }, []);

  useEffect(() => {
    if (!window.channels) return;
    window.channels.init()
      .then(() => { setDbReady(true); return reload(); })
      .catch((e: unknown) => setFetchError(e instanceof Error ? e.message : String(e)));
  }, [reload]);

  // ── Fetch: làm tươi danh sách từ website ─────────────────────────────────

  const handleFetch = async () => {
    if (!window.channels || fetching || !dbReady) return;
    setFetching(true); setFetchError(""); setFetchNote(""); setPullMsg(null);
    try {
      const res = await window.channels.sync();
      if ("error" in res) throw new Error(res.error);
      setNewUserIds(new Set(res.added_users));
      const { personal_tool_owners: po, personal_tool_channels: pc } = res.skipped;
      setFetchNote([
        `Có thêm ${res.added_channels} kênh mới.`,
        po > 0 ? `Bỏ qua ${po} user dùng Tool riêng (${pc} kênh).` : "",
        res.removed_channels > 0 ? `Đã dọn ${res.removed_channels} kênh không còn thuộc tool dùng chung.` : "",
      ].filter(Boolean).join(" "));
      await reload();
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setFetching(false);
    }
  };

  // ── Hành động trên kênh đã chọn ───────────────────────────────────────────

  const byId = new Map(all.map((c) => [c.id, c]));
  const selUnpulled = [...selected].filter((id) => byId.get(id)?.pulled === 0);
  const selPulled   = [...selected].filter((id) => byId.get(id)?.pulled === 1);
  const selLinkable = selPulled.filter((id) => ["youtube", "tiktok"].includes(byId.get(id)?.platform ?? ""));   // nền tảng đã hỗ trợ lấy toàn bộ link

  /** Kéo CHỈ các kênh chưa kéo trong lựa chọn; kênh đã kéo không bao giờ được gửi đi kéo lại. */
  const handlePull = async () => {
    if (!window.channels || busy || selUnpulled.length === 0) return;
    setBusy(true); setPullMsg(null);
    try {
      const res = await window.channels.pull(selUnpulled);
      setPullMsg({ ok: true, text: `Đã kéo ${res.pulled} kênh. Tool giữ kết nối để đẩy dữ liệu lên.` });
      setSelected(new Set());
      await reload();
    } catch (e) {
      setPullMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const pulledAction = async (action: "enable" | "disable" | "unpull") => {
    if (!window.channels || busy || selPulled.length === 0) return;
    setBusy(true);
    try {
      if (action === "unpull") await window.channels.unpull(selPulled);
      else await window.channels.setEnabled(selPulled, action === "enable");
      await reload();
    } finally {
      setBusy(false);
    }
  };

  /** Lấy TẤT CẢ link video của các kênh YouTube/TikTok đã kéo trong lựa chọn (lần lượt từng kênh; kênh lớn có thể mất vài phút). */
  const handleFetchLinks = async () => {
    if (!window.channels || busy || linksBusy || selLinkable.length === 0) return;
    setPullMsg(null);
    const lines: string[] = [];
    let failed = 0;
    try {
      for (const id of selLinkable) {
        const ch = byId.get(id);
        if (!ch) continue;
        const label = ch.display_name || ch.input_ref || ch.id;
        setLinksBusy(label);
        const res = await window.channels.fetchLinks(ch.platform, ch.id, ch.input_ref || ch.url);
        if ("error" in res) { failed++; lines.push(`${label}: ${res.error}`); continue; }
        lines.push(res.kind === "empty" ? `${label}: kênh chưa có video` : `${label}: ${res.found} link (mới ${res.added}, tổng ${res.total}) → ${res.export}`);
      }
    } finally {
      setLinksBusy(null);
      await reload();
    }
    setPullMsg({ ok: failed === 0, text: lines.join("\n") });
  };

  // ── Dẫn xuất ──────────────────────────────────────────────────────────────

  const q = search.trim().toLowerCase();
  const visible = all.filter((c) =>
    (!ownerFilter || c.owner_id === ownerFilter)
    && (platFilter === "all" || c.platform === platFilter)
    && (pulledFilter === "all" || (pulledFilter === "pulled") === (c.pulled === 1))
    && (mappingFilter === "all" || (mappingFilter === "mapped") === (c.mapping_status === "mapped"))
    && (enabledFilter === "all" || (enabledFilter === "on") === (c.enabled === 1))
    && (!q || [c.display_name, c.input_ref, c.canonical_id].some((s) => s.toLowerCase().includes(q))));

  const platCounts: Record<string, number> = { all: all.length, youtube: 0, tiktok: 0, facebook: 0, other: 0 };
  all.forEach((c) => { if (c.platform in platCounts) platCounts[c.platform]++; });
  const pulledCount = all.filter((c) => c.pulled === 1).length;

  const channelsByOwner: Record<string, LocalChannel[]> = {};
  visible.forEach((c) => { (channelsByOwner[c.owner_id] ??= []).push(c); });

  const toggle = (ids: string[], add: boolean) =>
    setSelected((prev) => { const s = new Set(prev); ids.forEach((id) => (add ? s.add(id) : s.delete(id))); return s; });

  const canPull = !!window.channels && !busy && selUnpulled.length > 0;
  const filtersOn = !!(q || ownerFilter || platFilter !== "all" || pulledFilter !== "all" || mappingFilter !== "all" || enabledFilter !== "all");

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
              NGƯỜI DÙNG TOOL DÙNG CHUNG
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

          {fetchNote && (
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8, padding: "7px 9px", background: "var(--surface)", borderRadius: 5, border: "1px solid var(--border)" }}>
              {fetchNote}
            </div>
          )}

          {!fetching && dbUsers.length === 0 && (
            <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: "24px 8px", lineHeight: 1.7 }}>
              Bấm <strong>Fetch</strong> để lấy kênh của người dùng đang dùng tool dùng chung (người dùng có Tool riêng không hiện ở đây).
            </div>
          )}

          {dbUsers.length > 0 && (
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>
              Bấm vào người dùng để chỉ xem kênh của họ.
            </div>
          )}

          {dbUsers.map((user) => {
            const active = ownerFilter === user.id;
            return (
              <button key={user.id} onClick={() => setOwnerFilter(active ? "" : user.id)} style={{
                display: "flex", alignItems: "center", gap: 8, padding: "7px 8px", width: "100%", textAlign: "left",
                borderRadius: 5, cursor: "pointer", marginBottom: 3, fontFamily: "inherit", color: "inherit",
                background: active ? "rgba(59,91,219,.07)" : "transparent",
                border: `1px solid ${active ? "rgba(59,91,219,.25)" : "transparent"}`,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.username}</div>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>{user.pulled_count}/{user.channel_count} kênh đã kéo</div>
                </div>
                {newUserIds.has(user.id) && (
                  <span style={{ fontSize: 9, fontWeight: 700, color: "#fff", background: "var(--accent)", borderRadius: 3, padding: "1px 5px" }}>MỚI</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Footer: Kéo kênh đã chọn */}
        <div style={{ flexShrink: 0, padding: "10px 14px", borderTop: "1px solid var(--border)", background: "var(--surface)" }}>
          {pullMsg && (
            <div style={{ fontSize: 11, marginBottom: 6, whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 140, overflowY: "auto", color: pullMsg.ok ? "var(--green)" : "var(--red)" }}>
              {pullMsg.ok ? "✓ " : ""}{pullMsg.text}
            </div>
          )}
          <button onClick={handlePull} disabled={!canPull}
            style={{ ...btnCss("primary", !canPull), width: "100%", height: 32, fontSize: 12 }}>
            {busy ? "Đang xử lý…" : selUnpulled.length > 0 ? `Kéo ${selUnpulled.length} kênh đã chọn` : "Chọn kênh chưa kéo để kéo"}
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
          {(["all", "unpulled", "pulled"] as const).map((v) => (
            <button key={v} onClick={() => setPulledFilter(v)} style={chip(pulledFilter === v)}>
              {v === "all" ? "Mọi kênh" : v === "unpulled" ? `Chưa kéo ${all.length - pulledCount}` : `✓ Đã kéo ${pulledCount}`}
            </button>
          ))}
          <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
          {(["all", "mapped", "unmapped"] as const).map((v) => (
            <button key={v} onClick={() => setMappingFilter(v)} style={chip(mappingFilter === v)}>
              {v === "all" ? "Mọi trạng thái liên kết" : v === "mapped" ? "✓ Đã liên kết" : "— Chưa liên kết"}
            </button>
          ))}
          <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
          {(["all", "on", "off"] as const).map((v) => (
            <button key={v} onClick={() => setEnabledFilter(v)} style={chip(enabledFilter === v)}>
              {v === "all" ? "Bật/tắt" : v === "on" ? "⚡ Bật" : "○ Tắt"}
            </button>
          ))}
          <div style={{ flex: 1 }} />
          <button
            onClick={() => toggle(visible.filter((c) => c.pulled === 0).map((c) => c.id), true)}
            disabled={!visible.some((c) => c.pulled === 0)}
            style={{ ...btnCss("ghost", !visible.some((c) => c.pulled === 0)), fontSize: 11, height: 26 }}>
            Chọn tất cả chưa kéo
          </button>
        </div>

        {/* ── Action bar (hiện khi có kênh được chọn) ── */}
        {selected.size > 0 && (
          <div style={{ flexShrink: 0, padding: "6px 12px", background: "rgba(59,91,219,.06)", borderBottom: "1px solid rgba(59,91,219,.15)", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>
              {selected.size} kênh đã chọn ({selUnpulled.length} chưa kéo · {selPulled.length} đã kéo)
            </span>
            <button onClick={() => setSelected(new Set())} style={{ ...btnCss("ghost"), fontSize: 10, height: 22, padding: "0 8px" }}>Bỏ chọn</button>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 10, color: "var(--muted)" }}>Với kênh đã kéo:</span>
            <button onClick={() => pulledAction("enable")}  disabled={busy || selPulled.length === 0} style={{ ...btnCss("ghost", busy || selPulled.length === 0), fontSize: 11, height: 26, color: "var(--green)" }}>⚡ Bật</button>
            <button onClick={() => pulledAction("disable")} disabled={busy || selPulled.length === 0} style={{ ...btnCss("ghost", busy || selPulled.length === 0), fontSize: 11, height: 26 }}>○ Tắt</button>
            <button onClick={() => pulledAction("unpull")}  disabled={busy || selPulled.length === 0} style={{ ...btnCss("ghost", busy || selPulled.length === 0), fontSize: 11, height: 26, color: "var(--red)" }}>Gỡ kết nối</button>
            <div style={{ width: 1, height: 16, background: "var(--border)" }} />
            <button onClick={handleFetchLinks} disabled={busy || !!linksBusy || selLinkable.length === 0}
                    title="Lấy toàn bộ video của kênh YouTube (Data API) / TikTok (yt-dlp qua proxy), lưu trên máy + file .txt"
                    style={{ ...btnCss("ghost", busy || !!linksBusy || selLinkable.length === 0), fontSize: 11, height: 26, color: "var(--accent)" }}>
              {linksBusy ? `Đang lấy: ${linksBusy}…` : `🔗 Lấy toàn bộ link video (${selLinkable.length} kênh YouTube/TikTok)`}
            </button>
          </div>
        )}

        {/* ── Channel list ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "10px 12px" }}>
          {visible.length === 0 ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", minHeight: 160, color: "var(--muted)", textAlign: "center", gap: 8 }}>
              <span style={{ fontSize: 24, opacity: 0.2 }}>⚡</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>
                {filtersOn ? "Không có kênh phù hợp bộ lọc" : "Chưa có kênh nào — bấm Fetch để lấy từ website"}
              </span>
            </div>
          ) : (
            Object.entries(channelsByOwner).map(([ownerId, chs]) => (
              <OwnerGroup
                key={ownerId}
                ownerName={chs[0]?.owner_username ?? ownerId}
                channels={chs}
                selected={selected}
                onToggleCh={(id) => toggle([id], !selected.has(id))}
                onToggleAll={(ids, add) => toggle(ids, add)}
                onShowLinks={setLinksOf}
              />
            ))
          )}
        </div>
      </main>
      {linksOf && <VideoLinksDialog channel={linksOf} onClose={() => setLinksOf(null)} />}
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

function OwnerGroup({ ownerName, channels, selected, onToggleCh, onToggleAll, onShowLinks }: {
  ownerName: string;
  channels: LocalChannel[];
  selected: Set<string>;
  onToggleCh: (id: string) => void;
  onToggleAll: (ids: string[], add: boolean) => void;
  onShowLinks: (ch: LocalChannel) => void;
}) {
  const [open, setOpen] = useState(true);
  const allIds = channels.map((c) => c.id);
  const allSelected = allIds.every((id) => selected.has(id));
  const someSelected = !allSelected && allIds.some((id) => selected.has(id));
  const pulled = channels.filter((c) => c.pulled === 1).length;

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
          <span style={{ fontSize: 10, color: "var(--muted)" }}>· {channels.length} kênh · {pulled} đã kéo</span>
        </button>
      </div>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, paddingLeft: 20 }}>
          {channels.map((ch) => (
            <ChannelRow key={ch.id} ch={ch} selected={selected.has(ch.id)} onToggle={() => onToggleCh(ch.id)} onShowLinks={() => onShowLinks(ch)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ChannelRow({ ch, selected, onToggle, onShowLinks }: { ch: LocalChannel; selected: boolean; onToggle: () => void; onShowLinks: () => void }) {
  const meta   = PLATFORM_META[ch.platform] ?? PLATFORM_META.other;
  const label  = ch.display_name || ch.input_ref || ch.canonical_id || ch.id;
  const pulled = ch.pulled === 1;

  let dateStr = "—";
  if (pulled && ch.pulled_at) {
    try { dateStr = new Date(ch.pulled_at).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" }); }
    catch { dateStr = ch.pulled_at.slice(0, 10); }
  }

  return (
    <label style={{
      display: "flex", alignItems: "center", gap: 7, padding: "5px 8px",
      borderRadius: 5, cursor: "pointer",
      background: selected ? "rgba(59,91,219,.06)" : "var(--surface)",
      border: `1px solid ${selected ? "rgba(59,91,219,.2)" : "var(--border)"}`,
      opacity: pulled && !ch.enabled ? 0.5 : 1,
    }}>
      <input type="checkbox" checked={selected} onChange={onToggle}
        style={{ width: 13, height: 13, accentColor: "var(--accent)", flexShrink: 0 }} />
      <span style={{ width: 20, height: 20, borderRadius: 4, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9, fontWeight: 700, background: meta.bg, color: meta.color }}>
        {meta.icon}
      </span>
      <span style={{ flex: 1, fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {pulled && !ch.enabled && <span style={{ fontSize: 9, color: "var(--muted)", marginRight: 4, verticalAlign: "middle" }}>○</span>}
        {label}
      </span>
      {ch.input_ref && ch.input_ref !== label && (
        <span style={{ fontSize: 10, color: "var(--muted)", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 0 }}>{ch.input_ref}</span>
      )}
      <span
        title={pulled ? "Đã kéo về tool — tool giữ kết nối với kênh này để đẩy dữ liệu lên" : "Chưa kéo về tool"}
        style={{
          fontSize: 10, padding: "1px 6px", borderRadius: 3, flexShrink: 0, fontWeight: 600,
          background: pulled ? "rgba(22,163,74,.1)" : "rgba(217,119,6,.1)",
          color: pulled ? "var(--green)" : "var(--amber)",
          border: `1px solid ${pulled ? "rgba(22,163,74,.2)" : "rgba(217,119,6,.25)"}`,
        }}>
        {pulled ? "✓ Đã kéo" : "Chưa kéo"}
      </span>
      <ChannelDataBadges ch={ch} onShowLinks={onShowLinks} />
      <span title="Ngày kéo về" style={{ fontSize: 10, color: "var(--muted)", flexShrink: 0, minWidth: 56, textAlign: "right" }}>{dateStr}</span>
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

const chip = (active: boolean): CSSProperties => ({
  ...filterChip,
  background: active ? "rgba(59,91,219,.12)" : "transparent",
  color: active ? "var(--accent)" : "var(--muted)",
  fontWeight: active ? 600 : 400,
});

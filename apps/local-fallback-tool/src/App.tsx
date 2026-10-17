import { useState, type CSSProperties } from "react";
import CollectorPage from "./pages/CollectorPage";
import ChannelImportPage from "./pages/ChannelImportPage";
import SystemApiPage from "./pages/SystemApiPage";

type PageKey = "collector" | "channels" | "system";

const NAV_TABS: { key: PageKey; label: string }[] = [
  { key: "collector", label: "◎ Thu thập" },
  { key: "channels",  label: "⚡ Người dùng & Kênh" },
  { key: "system",    label: "🔌 API hệ thống" },
];

export default function App() {
  const [page, setPage] = useState<PageKey>("collector");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* ── Top nav bar ── */}
      <nav style={{
        display: "flex", alignItems: "center", gap: 2,
        padding: "0 10px", flexShrink: 0, height: 36,
        background: "var(--surface)", borderBottom: "1px solid var(--border)",
      }}>
        {NAV_TABS.map(({ key, label }) => {
          const active = page === key;
          const tabStyle: CSSProperties = {
            display: "inline-flex", alignItems: "center",
            padding: "0 12px", height: 28, borderRadius: 5,
            fontSize: 12, fontWeight: active ? 600 : 500,
            cursor: "pointer", border: "none", fontFamily: "inherit",
            background: active ? "rgba(59,91,219,.1)" : "transparent",
            color: active ? "var(--accent)" : "var(--muted)",
          };
          return (
            <button key={key} onClick={() => setPage(key)} style={tabStyle}>
              {label}
            </button>
          );
        })}
      </nav>

      {/* ── Page content ── */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        {page === "collector" ? <CollectorPage /> : page === "channels" ? <ChannelImportPage /> : <SystemApiPage />}
      </div>
    </div>
  );
}

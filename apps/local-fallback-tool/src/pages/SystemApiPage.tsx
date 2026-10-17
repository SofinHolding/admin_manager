/**
 * API hệ thống — danh sách mọi endpoint của hệ thống đã được ánh xạ vào tool (system-endpoints.json) và nút
 * "Kiểm tra kết nối" chạy thử từng endpoint an toàn bằng cấu hình hiện tại (địa chỉ nhúng sẵn hoặc file kết nối).
 * Endpoint ghi / đổi trạng thái job KHÔNG bị chạy thử (hiện là "bỏ qua").
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";

const STATUS_STYLE: Record<SystemProbeResult["status"], { label: string; color: string; bg: string }> = {
  ok:      { label: "OK",       color: "var(--green)", bg: "rgba(22,163,74,.1)" },
  fail:    { label: "Lỗi",      color: "var(--red)",   bg: "rgba(220,38,38,.08)" },
  skipped: { label: "Bỏ qua",   color: "var(--muted)", bg: "var(--bg)" },
};

const th: CSSProperties = { textAlign: "left", padding: "6px 8px", fontSize: 11, color: "var(--muted)", fontWeight: 700 };
const td: CSSProperties = { padding: "6px 8px", fontSize: 12, verticalAlign: "top", borderTop: "1px solid var(--border)" };

export default function SystemApiPage() {
  const [info, setInfo] = useState<{ default_gateway: string; gateway: string; endpoints: SystemEndpoint[] } | null>(null);
  const [results, setResults] = useState<Record<string, SystemProbeResult>>({});
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState("");
  const [probedAt, setProbedAt] = useState("");

  const load = useCallback(async () => {
    if (!window.collector) return;
    setInfo(await window.collector.endpoints());
  }, []);

  useEffect(() => { void load(); }, [load]);

  const probe = async () => {
    if (!window.collector || probing) return;
    setProbing(true); setError("");
    try {
      const res = await window.collector.probe();
      if ("error" in res) { setError(res.error); return; }
      setResults(Object.fromEntries(res.results.map((r) => [r.name, r])));
      setProbedAt(new Date().toLocaleTimeString());
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProbing(false);
    }
  };

  if (!window.collector) {
    return <div style={{ padding: 20, fontSize: 13, color: "var(--muted)" }}>Trang này chỉ chạy trong ứng dụng Electron.</div>;
  }

  const list = Object.values(results);
  const count = (s: SystemProbeResult["status"]) => list.filter((r) => r.status === s).length;
  const custom = !!info && info.gateway !== info.default_gateway;

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "14px 18px", background: "var(--bg)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", letterSpacing: ".04em" }}>MÁY CHỦ HỆ THỐNG</div>
          <div style={{ fontFamily: "monospace", fontSize: 14, fontWeight: 600 }}>{info?.gateway ?? "…"}</div>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            {custom
              ? `Đang dùng địa chỉ từ file kết nối / cấu hình (mặc định nhúng sẵn: ${info?.default_gateway})`
              : "Địa chỉ nhúng sẵn trong tool"}
          </div>
        </div>
        <button onClick={probe} disabled={probing || !info}
                style={{ padding: "7px 14px", borderRadius: 6, border: "none", fontWeight: 600, fontSize: 12, cursor: probing ? "wait" : "pointer",
                         background: "var(--accent)", color: "#fff", opacity: probing ? 0.6 : 1 }}>
          {probing ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 10, padding: "7px 9px", background: "rgba(220,38,38,.06)",
                      borderRadius: 5, border: "1px solid rgba(220,38,38,.2)" }}>{error}</div>
      )}
      {list.length > 0 && (
        <div style={{ fontSize: 12, marginBottom: 10, color: "var(--muted)" }}>
          Lúc {probedAt}: <b style={{ color: "var(--green)" }}>{count("ok")} OK</b>
          {" · "}<b style={{ color: count("fail") ? "var(--red)" : "var(--muted)" }}>{count("fail")} lỗi</b>
          {" · "}{count("skipped")} bỏ qua
        </div>
      )}

      <table style={{ width: "100%", borderCollapse: "collapse", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6 }}>
        <thead>
          <tr><th style={th}>Endpoint</th><th style={th}>Quyền (scope)</th><th style={th}>Mô tả</th><th style={th}>Kết quả</th></tr>
        </thead>
        <tbody>
          {(info?.endpoints ?? []).map((e) => {
            const r = results[e.name];
            const st = r ? STATUS_STYLE[r.status] : null;
            return (
              <tr key={e.name}>
                <td style={td}>
                  <span style={{ fontWeight: 700, marginRight: 6 }}>{e.method}</span>
                  <span style={{ fontFamily: "monospace" }}>{e.path}</span>
                </td>
                <td style={{ ...td, fontFamily: "monospace", color: "var(--muted)" }}>{e.scope ?? "không cần key"}</td>
                <td style={td}>{e.summary}</td>
                <td style={td}>
                  {st && r ? (
                    <span>
                      <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 3, color: st.color, background: st.bg, fontWeight: 600 }}>
                        {st.label}{r.http ? ` ${r.http}` : ""}{r.ms !== null ? ` · ${r.ms}ms` : ""}
                      </span>
                      {r.detail && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{r.detail}</div>}
                    </span>
                  ) : <span style={{ color: "var(--muted)" }}>—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

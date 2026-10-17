/**
 * Danh sách NHIỀU YouTube API key hoặc proxy: nhập hàng loạt (mỗi dòng một mục), kiểm tra còn sống không,
 * xoá từng mục / xoá hàng loạt / xoá mục chết. Phân tích + bỏ trùng chạy ở Python (pools.py) để cùng quy tắc với collector.
 * Thay đổi chỉ nằm trong cấu hình đang soạn — cha quyết định lưu (nút "Lưu cấu hình").
 */
import { useMemo, useState } from "react";

export type PoolKind = "youtube-keys" | "proxies";
export type PoolState = "alive" | "quota" | "dead" | "error";

interface CheckResult { item: string; state: PoolState; detail: string; ms: number; ip?: string }

interface Props {
  kind: PoolKind;
  title: string;
  hint: string;
  placeholder: string;
  items: string[];
  onChange: (items: string[]) => void;
  disabled?: boolean;
}

const STATE_META: Record<PoolState, { label: string; color: string }> = {
  alive: { label: "Sống",        color: "var(--green)" },
  quota: { label: "Hết quota",   color: "var(--amber)" },
  dead:  { label: "Chết",        color: "var(--red)" },
  error: { label: "Chưa rõ",     color: "var(--amber)" },
};

/** Che phần bí mật khi hiển thị: key giữ đầu/đuôi, proxy che mật khẩu. Chuỗi gốc vẫn là khoá của dòng. */
export function maskItem(kind: PoolKind, item: string): string {
  if (kind === "youtube-keys") return item.length <= 10 ? item : `${item.slice(0, 6)}…${item.slice(-4)}`;
  return item.replace(/^([a-z0-9]+:\/\/[^:@/]+):[^@]*@/i, "$1:***@");
}

const field: React.CSSProperties = {
  width: "100%", padding: "6px 9px", borderRadius: 5, background: "var(--surface2)", border: "1px solid var(--border)",
  color: "var(--text)", fontSize: 12, outline: "none", fontFamily: "monospace", lineHeight: 1.5, resize: "vertical",
};

function btn(variant: "primary" | "danger" | "ghost", disabled = false): React.CSSProperties {
  const base: React.CSSProperties = {
    display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "0 11px", height: 28, borderRadius: 5,
    fontSize: 12, fontWeight: 500, border: "1px solid transparent", cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.4 : 1, fontFamily: "inherit",
  };
  if (variant === "primary") return { ...base, background: "var(--accent)", color: "#fff" };
  if (variant === "danger") return { ...base, background: "#fef2f2", color: "var(--red)", borderColor: "#fecaca" };
  return { ...base, background: "var(--surface2)", color: "var(--muted)", borderColor: "var(--border)" };
}

export default function PoolEditor({ kind, title, hint, placeholder, items, onChange, disabled }: Props) {
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [checking, setChecking] = useState<Set<string>>(new Set());

  const deadItems = useMemo(() => items.filter((i) => results[i]?.state === "dead"), [items, results]);
  const allSelected = items.length > 0 && items.every((i) => selected.has(i));
  const busy = checking.size > 0;

  const say = (text: string, error = false) => setMsg({ text, error });

  const add = async () => {
    if (!draft.trim() || !window.collector) return;
    const res = await window.collector.mergePool(kind, items, draft);
    if ("error" in res) return say(res.error, true);
    onChange(res.items);
    setDraft(res.bad.join("\n"));                       // dòng không hợp lệ ở lại ô nhập để sửa
    const parts = [`Đã thêm ${res.added}`];
    if (res.duplicate) parts.push(`${res.duplicate} trùng (bỏ qua)`);
    if (res.bad.length) parts.push(`${res.bad.length} dòng không hợp lệ (còn lại trong ô nhập)`);
    say(parts.join(" · "), res.bad.length > 0 && res.added === 0);
  };

  const check = async (targets: string[]) => {
    if (!targets.length || !window.collector) return;
    setChecking(new Set(targets));
    say(`Đang kiểm tra ${targets.length} mục…`);
    const res = await window.collector.checkPool(kind, targets);
    setChecking(new Set());
    if ("error" in res) return say(res.error, true);
    setResults((prev) => ({ ...prev, ...Object.fromEntries(res.results.map((r) => [r.item, r])) }));
    const count = (s: PoolState) => res.results.filter((r) => r.state === s).length;
    say(`Sống ${count("alive")} · hết quota ${count("quota")} · chết ${count("dead")} · chưa rõ ${count("error")}`);
  };

  const remove = (targets: string[]) => {
    const drop = new Set(targets);
    onChange(items.filter((i) => !drop.has(i)));
    setSelected((s) => new Set([...s].filter((i) => !drop.has(i))));
    setResults((r) => Object.fromEntries(Object.entries(r).filter(([k]) => !drop.has(k))));
  };

  const toggle = (item: string) =>
    setSelected((s) => { const n = new Set(s); if (n.has(item)) n.delete(item); else n.add(item); return n; });

  const chosen = items.filter((i) => selected.has(i));

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{title}</span>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{items.length} mục</span>
      </div>
      <p style={{ fontSize: 10, color: "var(--muted)", margin: "0 0 6px" }}>{hint}</p>

      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={4} placeholder={placeholder} disabled={disabled}
                spellCheck={false} style={field} aria-label={`Thêm ${title}`} />
      <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "6px 0 10px", flexWrap: "wrap" }}>
        <button type="button" onClick={add} disabled={disabled || !draft.trim()} style={btn("primary", disabled || !draft.trim())}>
          Thêm vào danh sách
        </button>
        {msg && <span style={{ fontSize: 11, color: msg.error ? "var(--red)" : "var(--muted)" }}>{msg.text}</span>}
      </div>

      {items.length > 0 && (
        <>
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--muted)" }}>
              <input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(items))} />
              Chọn tất cả
            </label>
            <button type="button" onClick={() => check(items)} disabled={disabled || busy} style={btn("ghost", disabled || busy)}>
              {busy ? "Đang kiểm tra…" : "Kiểm tra tất cả"}
            </button>
            <button type="button" onClick={() => check(chosen)} disabled={disabled || busy || !chosen.length} style={btn("ghost", disabled || busy || !chosen.length)}>
              Kiểm tra đã chọn ({chosen.length})
            </button>
            <button type="button" onClick={() => remove(chosen)} disabled={disabled || !chosen.length} style={btn("danger", disabled || !chosen.length)}>
              Xóa đã chọn
            </button>
            <button type="button" onClick={() => remove(deadItems)} disabled={disabled || !deadItems.length} style={btn("danger", disabled || !deadItems.length)}>
              Xóa mục chết ({deadItems.length})
            </button>
          </div>

          <div style={{ border: "1px solid var(--border)", borderRadius: 5, maxHeight: 260, overflowY: "auto" }}>
            {items.map((item) => {
              const r = results[item];
              const meta = r ? STATE_META[r.state] : null;
              return (
                <div key={item} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 8px", borderBottom: "1px solid var(--border)",
                                         background: selected.has(item) ? "var(--surface2)" : "transparent" }}>
                  <input type="checkbox" checked={selected.has(item)} onChange={() => toggle(item)} aria-label={`Chọn ${maskItem(kind, item)}`} />
                  <span style={{ flex: 1, minWidth: 0, fontFamily: "monospace", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={maskItem(kind, item)}>{maskItem(kind, item)}</span>
                  {checking.has(item) ? (
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>đang kiểm tra…</span>
                  ) : meta ? (
                    <span style={{ fontSize: 11, color: meta.color, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={`${r.detail} (${r.ms} ms)`}>
                      ● {meta.label}{r.ip ? ` · IP ${r.ip}` : ""}{r.state !== "alive" && r.detail ? ` — ${r.detail}` : ""}
                    </span>
                  ) : (
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>chưa kiểm tra</span>
                  )}
                  <button type="button" onClick={() => remove([item])} disabled={disabled} title="Xóa mục này" aria-label={`Xóa ${maskItem(kind, item)}`}
                          style={{ ...btn("ghost", disabled), height: 22, padding: "0 7px" }}>✕</button>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

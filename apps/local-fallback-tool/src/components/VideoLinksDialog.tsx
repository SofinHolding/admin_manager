/** Link video đã lấy về máy của MỘT kênh: xem, sao chép tất cả, lưu ra file .txt. */
import { useEffect, useState } from "react";

interface Props {
  channel: LocalChannel;
  onClose: () => void;
}

const fmtDate = (iso: string) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric" }); }
  catch { return iso.slice(0, 10); }
};

export default function VideoLinksDialog({ channel, onClose }: Props) {
  const [videos, setVideos] = useState<VideoLink[] | null>(null);
  const [note, setNote] = useState("");
  const label = channel.display_name || channel.input_ref || channel.id;

  useEffect(() => {
    let alive = true;
    window.channels?.listVideos(channel.id).then((r) => { if (alive) setVideos(r.videos); }).catch(() => { if (alive) setVideos([]); });
    return () => { alive = false; };
  }, [channel.id]);

  const urls = (videos ?? []).map((v) => v.url);

  const copy = async () => {
    try { await navigator.clipboard.writeText(urls.join("\n")); setNote(`Đã sao chép ${urls.length} link`); }
    catch { setNote("Không sao chép được — dùng nút Lưu .txt"); }
  };

  const save = async () => {
    const r = await window.channels?.saveLinks(label, urls);
    if (r?.ok) setNote(`Đã lưu ${urls.length} link vào ${r.path}`);
    else if (r && !r.canceled) setNote(r.error ?? "Không lưu được file");
  };

  return (
    <div role="dialog" aria-label={`Link video của ${label}`}
         style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}
         onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ width: 760, maxWidth: "92vw", maxHeight: "82vh", display: "flex", flexDirection: "column", background: "var(--surface)",
                    border: "1px solid var(--border)", borderRadius: 8, boxShadow: "0 12px 40px rgba(0,0,0,.25)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</div>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>{videos === null ? "Đang tải…" : `${videos.length} link video đã lấy về máy`}</div>
          </div>
          <button type="button" onClick={copy} disabled={!urls.length} style={dlgBtn(!urls.length)}>Sao chép tất cả</button>
          <button type="button" onClick={save} disabled={!urls.length} style={dlgBtn(!urls.length)}>Lưu .txt…</button>
          <button type="button" onClick={onClose} style={dlgBtn(false)}>Đóng</button>
        </div>
        {note && <div style={{ padding: "6px 16px", fontSize: 11, color: "var(--muted)", borderBottom: "1px solid var(--border)" }}>{note}</div>}
        <div style={{ overflowY: "auto", padding: "6px 8px" }}>
          {videos?.length === 0 && <div style={{ padding: 20, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>Chưa có link nào.</div>}
          {(videos ?? []).map((v) => (
            <div key={v.video_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 8px", fontSize: 11, borderBottom: "1px solid var(--border)" }}>
              <span style={{ width: 70, color: "var(--muted)", flexShrink: 0 }}>{fmtDate(v.published_at)}</span>
              <span style={{ flex: 1, minWidth: 0, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={v.title || v.url}>{v.url}</span>
              <span style={{ width: 70, textAlign: "right", color: "var(--muted)", flexShrink: 0 }}>{v.views != null ? `${v.views.toLocaleString("vi-VN")} lượt` : ""}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function dlgBtn(disabled: boolean): React.CSSProperties {
  return { height: 28, padding: "0 11px", borderRadius: 5, fontSize: 12, fontWeight: 500, fontFamily: "inherit", background: "var(--surface2)",
           color: "var(--muted)", border: "1px solid var(--border)", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.4 : 1 };
}

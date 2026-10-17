/**
 * Số liệu của MỘT kênh, dùng chung cho mọi nơi liệt kê kênh (tab "Người dùng & Kênh" và "Thu thập → Kênh & người dùng") để hai nơi luôn
 * hiện cùng một thông tin, cùng từ ngữ với website:
 *  - đã/chưa liên kết số liệu: kênh đã có số liệu trên website hay chưa (server tự suy ra từ dữ liệu thật, không phải cờ gõ tay);
 *  - số video đã có trên website + lần thu mới nhất;
 *  - số link video đã lấy về máy (bấm để xem).
 */
import type { CSSProperties } from "react";

const TZ = "Asia/Ho_Chi_Minh";

export function fmtCollected(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("vi-VN", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

const chip: CSSProperties = { fontSize: 10, padding: "1px 5px", borderRadius: 3, flexShrink: 0 };

export default function ChannelDataBadges({ ch, onShowLinks }: {
  ch: Pick<LocalChannel, "platform" | "mapping_status" | "video_count" | "server_video_count" | "last_collected_at">;
  onShowLinks?: () => void;
}) {
  const mapped = ch.mapping_status === "mapped";
  const web = ch.server_video_count ?? 0;
  const when = fmtCollected(ch.last_collected_at);
  const links = ch.video_count ?? 0;
  return (
    <>
      <span title={mapped ? "Kênh đã có số liệu trên website" : "Kênh chưa có số liệu nào trên website"} style={{
        ...chip,
        background: mapped ? "rgba(22,163,74,.1)" : "var(--bg)",
        color: mapped ? "var(--green)" : "var(--muted)",
        border: `1px solid ${mapped ? "rgba(22,163,74,.2)" : "var(--border)"}`,
      }}>
        {mapped ? "Đã liên kết số liệu" : "Chưa liên kết số liệu"}
      </span>
      {web > 0 && (
        <span title={when ? `Số liệu mới nhất đến website lúc ${when}` : "Số video đã có trên website"}
              style={{ ...chip, background: "var(--bg)", color: "var(--muted)", border: "1px solid var(--border)" }}>
          📊 {web} video{when ? ` · ${when}` : ""}
        </span>
      )}
      {["youtube", "tiktok"].includes(ch.platform) && links > 0 && onShowLinks && (
        <button type="button" title="Xem link video đã lấy về máy" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onShowLinks(); }}
                style={{ ...chip, padding: "1px 6px", cursor: "pointer", fontFamily: "inherit", fontWeight: 600,
                         background: "var(--bg)", color: "var(--accent)", border: "1px solid var(--border)" }}>
          🔗 {links} link
        </button>
      )}
    </>
  );
}

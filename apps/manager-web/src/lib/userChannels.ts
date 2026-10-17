import type { UserChannelsUser } from "../api";

/** Giờ Việt Nam (khớp ngày báo cáo của hệ thống), dạng `17/10/2026 14:08`; không đọc được => `—`. */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(d).replace(",", "");
}

/** Vì sao user này (không) xuất hiện ở Tool dùng chung — một câu để admin đọc là hiểu. */
export function toolReason(u: UserChannelsUser): string {
  const t = u.totals;
  if (u.visible_in_shared_tool) return `Tool dùng chung thấy ${t.shared_tool} kênh của user này.`;
  if (t.active === 0 && t.removed > 0) return `Không hiện ở Tool: user đã xoá hết kênh (${t.removed} kênh đã xoá).`;
  if (t.active === 0) return "Không hiện ở Tool: user chưa có kênh nào.";
  if (t.personal_tool === t.active) return "Không hiện ở Tool dùng chung: toàn bộ kênh chạy bằng Tool riêng của user.";
  return "Không hiện ở Tool dùng chung: không có kênh nào ở chế độ Tool dùng chung.";
}

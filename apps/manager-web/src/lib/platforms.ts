/**
 * Tiện ích dùng chung cho "nền tảng" (platform) — nền tảng giờ là một DANH MỤC do Admin/dash_admin
 * quản lý qua Platform Registry (`/v1/integrations/admin/platforms`), KHÔNG còn danh sách cứng trong code.
 * File này KHÔNG chứa danh sách lựa chọn nền tảng nào — chỉ tri thức hiển thị (nhãn/màu/emoji dự phòng)
 * cho vài nền tảng quen thuộc, dùng khi server chưa trả `display_name` (vd lỗi mạng tạm thời).
 *
 * `usePlatformNames()` nạp `resourcesApi.listPlatforms()` MỘT lần và trả về map khóa -> tên hiển thị;
 * nơi gọi tự quyết định fallback (thường là chính khóa) khi tải lỗi hoặc platform không có trong map.
 */

import { useEffect, useState } from "react";
import { resourcesApi } from "../api";

export type PlatformNameMap = Record<string, string>;

/** Nhãn dự phòng cho vài nền tảng quen thuộc — chỉ dùng khi server chưa có/chưa trả `display_name`. */
const KNOWN_LABEL: Record<string, string> = {
  youtube: "YouTube",
  facebook: "Facebook",
  tiktok: "TikTok",
};

const KNOWN_EMOJI: Record<string, string> = {
  youtube: "📹",
  facebook: "👤",
  tiktok: "🎵",
};

/** Tên hiển thị: ưu tiên `names` (từ Platform Registry), rồi tới nhãn quen thuộc, cuối cùng là chính khóa. */
export function platformLabel(key: string, names?: PlatformNameMap): string {
  if (!key) return key;
  return names?.[key] || KNOWN_LABEL[key] || key;
}

/** Emoji dự phòng khi chưa có icon riêng; "🔗" cho nền tảng không nằm trong danh sách quen thuộc. */
export function platformEmoji(key: string): string {
  return KNOWN_EMOJI[key] ?? "🔗";
}

/** Nạp danh mục nền tảng (đã lọc theo registry + web-entry) MỘT lần, trả về map khóa -> tên hiển thị.
 * Lỗi mạng -> map rỗng, nơi gọi tự rơi về hiển thị khóa thô (KHÔNG chặn trang). */
export function usePlatformNames(): PlatformNameMap {
  const [names, setNames] = useState<PlatformNameMap>({});
  useEffect(() => {
    let alive = true;
    resourcesApi.listPlatforms()
      .then((d) => {
        if (!alive) return;
        const map: PlatformNameMap = {};
        for (const p of d.items) if (p.platform) map[p.platform] = p.display_name || p.platform;
        setNames(map);
      })
      .catch(() => { if (alive) setNames({}); });
    return () => { alive = false; };
  }, []);
  return names;
}

/**
 * Trạng thái dữ liệu Analytics: độ tươi (freshness), không khả dụng, và bộ lọc server THỰC SỰ áp dụng.
 * Nói bằng lời giản dị: chuyện gì xảy ra + cách làm tiếp (docs/architecture/ui-design-v1.md §6).
 */

import { Button } from "./ui/button";
import type { AppliedFilter, Freshness } from "../api";
import { formatLag } from "../filters/freshness";
import { Notice } from "./kit";

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("vi-VN");
}

/** "2026-09-07" -> "07/09/2026" */
export function fmtDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

/** Số ngày của khoảng [from, to] (gồm cả hai đầu); 0 nếu ngày sai định dạng. */
export function daysInclusive(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return 0;
  return Math.round((b - a) / 86_400_000) + 1;
}

const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** Khoảng ngày bằng lời: "30 ngày qua (từ 08/09/2026)" khi kết thúc hôm nay, ngược lại "từ A đến B". */
export function rangeInWords(from: string, to: string): string {
  const n = daysInclusive(from, to);
  if (n > 0 && to === todayIso()) return `${n} ngày qua (từ ${fmtDay(from)})`;
  return `từ ${fmtDay(from)} đến ${fmtDay(to)}`;
}

export function FreshnessBanner({ freshness }: { freshness: Freshness | null }) {
  if (!freshness) return null;
  const asOf = fmtDateTime(freshness.as_of);
  if (freshness.stale) {
    const lag = formatLag(freshness.lag_seconds);
    return (
      <div data-testid="stale-banner">
        <Notice tone="warning" title="Dữ liệu cũ">
          Lần cập nhật gần nhất {asOf ? `lúc ${asOf}` : "đã lâu"}{lag ? `, trễ ${lag}` : ""}.
          {" "}Bạn đang xem số liệu gần nhất hệ thống có, nên có thể chưa khớp với nguồn dữ liệu. Số liệu sẽ tự mới lại khi nguồn đẩy dữ liệu lên.
        </Notice>
      </div>
    );
  }
  if (!asOf) return null;
  return (
    <p data-testid="freshness-line" className="text-sm text-muted-foreground">
      Dữ liệu cập nhật lúc {asOf}{freshness.lag_seconds != null ? `, trễ ${formatLag(freshness.lag_seconds)}` : ""}
    </p>
  );
}

export function AnalyticsUnavailableBanner({ hasData, onRetry }: { hasData: boolean; onRetry?: () => void }) {
  return (
    <div data-testid="analytics-unavailable">
      <Notice
        tone="error"
        title="Dịch vụ thống kê tạm thời không khả dụng"
        action={onRetry && <Button type="button" size="sm" variant="outline" onClick={onRetry}>Thử lại</Button>}
      >
        {hasData
          ? "Đang hiển thị dữ liệu tải thành công gần nhất, có thể đã cũ. Bạn không cần làm gì thêm; bấm Thử lại sau ít phút để lấy số liệu mới."
          : "Chưa có dữ liệu để hiển thị. Hãy thử lại sau ít phút."}
        {" "}Các trang khác (hồ sơ, quản trị, kết nối dữ liệu) vẫn dùng bình thường.
      </Notice>
    </div>
  );
}

const PLATFORM_NAMES: Record<string, string> = { youtube: "YouTube", facebook: "Facebook" };

/** "Đang xem: …" bằng lời, từ bộ lọc server ĐÃ áp dụng (sau khi ép quyền) — có thể khác bộ lọc người dùng yêu cầu. */
export function AppliedFilterBar({ applied, selfId, ownerName, groupName }: {
  applied: AppliedFilter | undefined;
  selfId?: string;
  ownerName?: (id: string) => string | undefined;
  groupName?: (id: string) => string | undefined;
}) {
  if (!applied) return null;
  const parts: string[] = [];
  if (applied.date_from && applied.date_to) parts.push(rangeInWords(applied.date_from, applied.date_to));
  if (applied.owner_account_id) {
    parts.push(`nhân sự ${applied.owner_account_id === selfId ? "là tài khoản của bạn" : ownerName?.(applied.owner_account_id) ?? applied.owner_account_id}`);
  }
  if (applied.platform) parts.push(`nền tảng ${PLATFORM_NAMES[applied.platform] ?? applied.platform}`);
  if (applied.country) parts.push(`quốc gia ${applied.country}`);
  if (applied.project) parts.push(`chủ đề ${applied.project}`);
  if (applied.platform_tag) parts.push(`nhãn ${applied.platform_tag}`);
  if (applied.group_id) parts.push(`nhóm kênh ${groupName?.(applied.group_id) ?? applied.group_id}`);
  if (!parts.length) return null;
  return (
    <p data-testid="applied-filter" className="text-sm text-muted-foreground">
      <span className="font-semibold text-foreground">Đang xem:</span> {parts.join(", ")}.
    </p>
  );
}

/** Trạng thái dữ liệu Analytics: độ tươi (freshness), không khả dụng, và bộ lọc server THỰC SỰ áp dụng. */

import { AlertTriangle, CloudOff, Info } from "lucide-react";
import type { AppliedFilter, Freshness } from "../api";
import { formatLag } from "../filters/freshness";

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("vi-VN");
}

export function FreshnessBanner({ freshness }: { freshness: Freshness | null }) {
  if (!freshness) return null;
  const asOf = fmtDateTime(freshness.as_of);
  if (freshness.stale) {
    const lag = formatLag(freshness.lag_seconds);
    return (
      <div role="status" data-testid="stale-banner"
           className="flex items-start gap-2 rounded-xl border border-yellow-500/30 bg-yellow-500/10 px-4 py-2.5 text-sm text-yellow-700">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <div>
          <span className="font-medium">Dữ liệu cũ</span>
          {asOf ? ` — cập nhật lần cuối ${asOf}` : ""}{lag ? ` (trễ ${lag})` : ""}.
          {" "}Số liệu có thể chưa khớp Tool mới nhất.
        </div>
      </div>
    );
  }
  return null; // dữ liệu còn mới: không hiện dòng "Dữ liệu cập nhật lúc …" (chỉ cảnh báo khi dữ liệu cũ)
}

export function AnalyticsUnavailableBanner({ hasData, onRetry }: { hasData: boolean; onRetry?: () => void }) {
  return (
    <div role="alert" data-testid="analytics-unavailable"
         className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      <CloudOff className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <div className="font-medium">Dịch vụ thống kê tạm thời không khả dụng</div>
        <div className="text-xs opacity-80">
          {hasData
            ? "Đang hiển thị dữ liệu tải thành công gần nhất — có thể đã cũ. "
            : "Chưa có dữ liệu để hiển thị. "}
          Các trang khác (hồ sơ, quản trị, tích hợp) vẫn dùng bình thường.
        </div>
      </div>
      {onRetry && (
        <button type="button" onClick={onRetry} className="shrink-0 rounded-md border border-destructive/40 px-2 py-1 text-xs hover:bg-destructive/10">
          Thử lại
        </button>
      )}
    </div>
  );
}

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
  if (!asOf) return null;
  return (
    <p data-testid="freshness-line" className="px-1 text-[11px] text-muted-foreground">
      Dữ liệu cập nhật lúc {asOf}{freshness.lag_seconds != null ? ` · trễ ${formatLag(freshness.lag_seconds)}` : ""}
    </p>
  );
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

const LABELS: Record<string, string> = {
  platform: "Nền tảng", country: "Quốc gia", project: "Chủ đề", group_id: "Nhóm", platform_tag: "Nhãn nền tảng",
};

/** Hiển thị bộ lọc server ĐÃ áp dụng (sau khi ép quyền) — có thể khác bộ lọc người dùng yêu cầu. */
export function AppliedFilterBar({ applied, selfId, ownerName, groupName }: {
  applied: AppliedFilter | undefined;
  selfId?: string;
  ownerName?: (id: string) => string | undefined;
  groupName?: (id: string) => string | undefined;
}) {
  if (!applied) return null;
  const parts: string[] = [];
  if (applied.date_from && applied.date_to) parts.push(`${applied.date_from} → ${applied.date_to}`);
  if (applied.owner_account_id) {
    parts.push(`Nhân sự: ${applied.owner_account_id === selfId ? "tài khoản của bạn" : ownerName?.(applied.owner_account_id) ?? applied.owner_account_id}`);
  }
  for (const k of ["platform", "country", "project", "platform_tag"] as const) {
    if (applied[k]) parts.push(`${LABELS[k]}: ${applied[k]}`);
  }
  if (applied.group_id) parts.push(`Nhóm: ${groupName?.(applied.group_id) ?? applied.group_id}`);
  if (!parts.length) return null;
  return (
    <p data-testid="applied-filter" className="flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
      <Info className="size-3 shrink-0" />
      <span>Bộ lọc áp dụng:</span>
      {parts.map((t) => (
        <span key={t} className="rounded border border-hairline bg-elev-1 px-1.5 py-0.5">{t}</span>
      ))}
    </p>
  );
}

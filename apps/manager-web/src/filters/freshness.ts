/** Gộp `freshness` của nhiều response analytics thành một trạng thái hiển thị duy nhất (xấu nhất thắng). */

import type { Freshness } from "../api";

export function mergeFreshness(list: (Freshness | null | undefined)[]): Freshness | null {
  const present = list.filter((f): f is Freshness => !!f);
  if (!present.length) return null;
  let asOf: string | null = null;
  let lag: number | null = null;
  let stale = false;
  for (const f of present) {
    if (f.stale) stale = true;
    if (f.as_of && (asOf === null || f.as_of < asOf)) asOf = f.as_of; // cũ nhất
    if (typeof f.lag_seconds === "number" && (lag === null || f.lag_seconds > lag)) lag = f.lag_seconds;
  }
  return { as_of: asOf, lag_seconds: lag, stale };
}

export function formatLag(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  if (seconds < 90) return `${Math.round(seconds)} giây`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} phút`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} giờ`;
  return `${Math.round(seconds / 86400)} ngày`;
}

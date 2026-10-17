/**
 * Filter Context — phần THUẦN (không React): kiểu, mặc định, parse/serialize URL, dựng query.
 *
 * Hợp đồng: docs/api/analytics-api-v1.md §1. Một bộ tham số duy nhất cho MỌI KPI/biểu đồ:
 *   date_from, date_to, owner_account_id, platform, country, group_id, platform_tag
 *
 * Quy tắc: mọi endpoint DỮ LIỆU (summary, traffic, top-channels, views-by-country, channel-breakdown,
 * compare/*) gửi CHÍNH XÁC cùng bộ tham số do `filterToParams` dựng — không widget nào tự thêm/bớt.
 * Endpoint DANH MỤC dùng để nạp ô chọn lọc (country-tags, platform-tags) có hàm dựng riêng bên dưới
 * (bỏ các tham số mà chính ô chọn đó điều khiển, nếu không danh sách sẽ co lại theo lựa chọn hiện tại).
 */

export interface FilterState {
  date_from: string;
  date_to: string;
  owner_account_id: string;
  platform: string;
  country: string;
  group_id: string;
  platform_tag: string;
}

export const FILTER_KEYS = [
  "date_from", "date_to", "owner_account_id", "platform", "country", "group_id", "platform_tag",
] as const satisfies readonly (keyof FilterState)[];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_LEN = 200;

export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function todayStr(now: Date = new Date()): string {
  return isoDate(now);
}

export function daysAgo(n: number, now: Date = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  d.setDate(d.getDate() - n);
  return isoDate(d);
}

export function defaultFilter(now: Date = new Date()): FilterState {
  return {
    date_from: daysAgo(29, now),
    date_to: todayStr(now),
    owner_account_id: "",
    platform: "",
    country: "",
    group_id: "",
    platform_tag: "",
  };
}

function cleanText(v: string | null): string {
  if (!v) return "";
  const t = v.trim();
  return t.length > MAX_LEN ? t.slice(0, MAX_LEN) : t;
}

/** URL -> FilterState. Giá trị sai định dạng bị bỏ về mặc định (link chia sẻ không làm vỡ trang). */
export function parseFilter(sp: URLSearchParams, now: Date = new Date()): FilterState {
  const base = defaultFilter(now);
  const from = sp.get("date_from");
  const to = sp.get("date_to");
  let date_from = from && DATE_RE.test(from) ? from : base.date_from;
  let date_to = to && DATE_RE.test(to) ? to : base.date_to;
  if (date_from > date_to) [date_from, date_to] = [date_to, date_from];
  return {
    date_from,
    date_to,
    owner_account_id: cleanText(sp.get("owner_account_id")),
    platform: cleanText(sp.get("platform")),
    country: cleanText(sp.get("country")),
    group_id: cleanText(sp.get("group_id")),
    platform_tag: cleanText(sp.get("platform_tag")),
  };
}

/** FilterState -> tham số gửi server (CHỈ giá trị không rỗng; ngày luôn có). */
export function filterToParams(f: FilterState): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of FILTER_KEYS) {
    const v = f[k];
    if (v) out[k] = v;
  }
  return out;
}

export function filterToQuery(f: FilterState, extra?: Record<string, string | number>): URLSearchParams {
  const q = new URLSearchParams(filterToParams(f));
  if (extra) for (const [k, v] of Object.entries(extra)) q.set(k, String(v));
  return q;
}

/** Tham số cho danh mục "quốc gia": mọi thứ trừ ngày và chính `country` (ô chọn quốc gia không được tự co lại). */
export function countryCatalogParams(f: FilterState): URLSearchParams {
  const q = new URLSearchParams();
  for (const k of ["owner_account_id", "platform", "group_id", "platform_tag"] as const) {
    if (f[k]) q.set(k, f[k]);
  }
  return q;
}

/** Tham số cho danh mục "nhãn nền tảng": chỉ phạm vi người sở hữu. */
export function platformTagCatalogParams(f: FilterState): URLSearchParams {
  const q = new URLSearchParams();
  if (f.owner_account_id) q.set("owner_account_id", f.owner_account_id);
  return q;
}

/** FilterState -> URL (giữ tham số không thuộc bộ lọc). Ngày chỉ ghi khi người dùng đã chọn khác mặc định hoặc URL đã có. */
export function writeFilterToUrl(
  current: URLSearchParams,
  f: FilterState,
  now: Date = new Date(),
): URLSearchParams {
  const next = new URLSearchParams(current);
  const def = defaultFilter(now);
  for (const k of FILTER_KEYS) {
    const v = f[k];
    const isDate = k === "date_from" || k === "date_to";
    const keep = isDate ? (v !== def[k] || current.has(k)) : !!v;
    if (keep && v) next.set(k, v); else next.delete(k);
  }
  return next;
}

export function filtersEqual(a: FilterState, b: FilterState): boolean {
  return FILTER_KEYS.every((k) => a[k] === b[k]);
}

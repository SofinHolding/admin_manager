/**
 * Trang Tổng quan (ui-design-v1.md): MỘT câu tóm tắt có số ở đầu trang, lọc nhanh theo ngày, dải số liệu,
 * rồi biểu đồ/danh sách xếp hạng. Mọi KPI/biểu đồ dùng CÙNG một FilterContext (8 tham số, đồng bộ URL).
 * Biểu đồ nào cũng có aria-label và "Xem dưới dạng bảng"; số trên trục viết gọn (N = nghìn, tr = triệu), tooltip luôn số đầy đủ.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowDown, ArrowDownWideNarrow, ArrowUp, Check, ExternalLink, Maximize2, Minus, RefreshCw, SlidersHorizontal, Unplug,
} from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart, Pie,
  PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  adminApi,
  analyticsApi,
  groupsApi,
  type Account,
  type AnalyticsMetric,
  type AnalyticsSummary,
  type AppliedFilter,
  type ChannelBreakdown,
  type CompareDimension,
  type CompareItem,
  type CountryViewsSeries,
  type TopChannel,
  type TrafficMetricKey,
  type TrafficMetricSeries,
  type Platform,
} from "../api";
import { Button } from "../components/ui/button";
import { DateInput } from "../components/ui/date-input";
import { Skeleton } from "../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { EmptyState, Field, Notice, PageHeader, Section, StatStrip, TechDetails, type Stat } from "../components/kit";
import { useAuth } from "../auth";
import { FilterProvider, useFilterContext } from "../filters/FilterContext";
import { countryCatalogParams, daysAgo, todayStr } from "../filters/filterState";
import { useAnalyticsQuery } from "../filters/useAnalyticsQuery";
import { mergeFreshness } from "../filters/freshness";
import {
  AnalyticsUnavailableBanner, AppliedFilterBar, FreshnessBanner, fmtDay,
} from "../components/AnalyticsStatus";
import { errorMessage } from "../lib/errors";
import { cn } from "../lib/utils";
import { COMMON_COMPARE_DIMENSIONS, type CommonCompareDimension } from "../filters/compareDimensions";
import { safeHttpUrl } from "../lib/safeUrl";

// ── Định dạng số/ngày (kiểu Việt Nam) ───────────────────────────────────────

const fmtNum = (n: number | null | undefined) =>
  n === null || n === undefined ? "–" : n.toLocaleString("vi-VN");

const shortDay = (iso: string) => (iso.length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : iso);

/** Số viết gọn cho TRỤC biểu đồ: 797.604 -> "797,6 N" (N = nghìn), 1.234.567 -> "1,2 tr", 2,5 tỷ. Tooltip/bảng luôn số đầy đủ. */
export function compactNum(v: number): string {
  const a = Math.abs(v);
  const f = (x: number) => (+x.toFixed(1)).toLocaleString("vi-VN");
  if (a >= 1e9) return `${f(v / 1e9)} tỷ`;
  if (a >= 1e6) return `${f(v / 1e6)} tr`;
  if (a >= 1e3) return `${f(v / 1e3)} N`;
  return v.toLocaleString("vi-VN");
}

const pluralDays = (n: number) => `${fmtNum(n)} ngày`;

// ── So sánh bằng lời ────────────────────────────────────────────────────────

interface Cmp { dir: "up" | "down" | "same" | "none"; phrase: (period: string) => string }

function compare(cur: number, prev: number): Cmp {
  if (!(prev > 0)) return { dir: "none", phrase: () => "chưa có kỳ trước để so sánh" };
  const ratio = cur / prev;
  const pct = Math.round((ratio - 1) * 100);
  if (ratio >= 10) return { dir: "up", phrase: (p) => `gấp ${fmtNum(Math.round(ratio))} lần so với ${p}` };
  if (pct === 0) return { dir: "same", phrase: (p) => `ngang với ${p}` };
  if (pct > 0) return { dir: "up", phrase: (p) => `nhiều hơn ${fmtNum(pct)}% so với ${p}` };
  return { dir: "down", phrase: (p) => `ít hơn ${fmtNum(-pct)}% so với ${p}` };
}

/** Dấu hiệu tăng/giảm: mũi tên + chữ (không chỉ màu). */
function DeltaNote({ cmp, period = "kỳ trước" }: { cmp: Cmp; period?: string }) {
  const Icon = cmp.dir === "up" ? ArrowUp : cmp.dir === "down" ? ArrowDown : Minus;
  return (
    <span className="inline-flex items-start gap-1">
      <Icon className="mt-1 size-4 shrink-0" aria-hidden />
      <span>{cmp.phrase(period)}</span>
    </span>
  );
}

const toneOf = (c: Cmp): Stat["tone"] => (c.dir === "up" ? "good" : c.dir === "down" ? "bad" : "default");

// ── Màu biểu đồ (từ biến CSS --chart-1..6; cập nhật khi đổi sáng/tối) ──────────

function useThemeColors() {
  const read = useCallback(() => {
    const s = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => s.getPropertyValue(name).trim() || fallback;
    return {
      ramp: [
        v("--chart-1", "#3b8a86"), v("--chart-2", "#5b52c8"), v("--chart-3", "#c9a23a"),
        v("--chart-4", "#c0506a"), v("--chart-5", "#2f8a5c"), v("--chart-6", "#6b7c8e"),
      ],
      grid: v("--border", "#8884"),
      axis: v("--muted-foreground", "#6b7280"),
    };
  }, []);
  const [colors, setColors] = useState(read);
  useEffect(() => {
    const ob = new MutationObserver(() => setColors(read()));
    ob.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => ob.disconnect();
  }, [read]);
  return colors;
}

const platformColor = (platform: string, ramp: string[]) =>
  platform === "youtube" ? ramp[3] : platform === "facebook" ? ramp[1] : ramp[0];
const platformName = (p: string) => (p === "youtube" ? "YouTube" : p === "facebook" ? "Facebook" : p);

// Radix Select cấm `value=""` — dùng sentinel cho "tất cả"
const MOI_QUOC_GIA = "__all";
const SCOPE_ALL = "__both";
const MOI_NHAN_SU = "__owner_all";
const MOI_NHOM = "__group_all";

/** Id dạng "page:<numeric>" / "profile:<numeric>" -> liên kết Facebook; null nếu không phải. */
function fbLink(id: string): string | null {
  const sep = id.indexOf(":");
  if (sep < 0) return null;
  const kind = id.slice(0, sep);
  const fid = id.slice(sep + 1);
  if (!fid || (kind !== "page" && kind !== "profile")) return null;
  return safeHttpUrl(`https://www.facebook.com/profile.php?id=${encodeURIComponent(fid)}`);
}

// ── Thành phần dùng chung của trang ─────────────────────────────────────────

function EmptyNote({ text }: { text: string }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center px-4 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

/** Khung chờ ổn định (không nhảy bố cục, không tự chuyển động). */
function LoadingBlock({ className, label = "Đang tải số liệu…" }: { className?: string; label?: string }) {
  return (
    <div className={cn("flex w-full flex-col justify-center gap-3", className)} aria-busy="true">
      <span className="sr-only" role="status">{label}</span>
      <Skeleton className="h-5 w-3/4 animate-none" />
      <Skeleton className="h-5 w-full animate-none" />
      <Skeleton className="h-5 w-2/3 animate-none" />
    </div>
  );
}

type TipEntry = { name?: string | number; value?: unknown; color?: string; payload?: { fill?: string } };

/** Tooltip rõ ràng: ngày đầy đủ, tên chuỗi, số ĐẦY ĐỦ (không rút gọn). */
function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: readonly TipEntry[]; label?: unknown }) {
  if (!active || !payload?.length) return null;
  const head = typeof label === "string" ? fmtDay(label) : null;
  return (
    <div className="rounded-lg border border-border-strong bg-popover px-3 py-2 text-sm text-popover-foreground shadow-[var(--shadow-overlay)]">
      {head && <p className="mb-1 font-semibold">{head}</p>}
      <ul className="space-y-0.5">
        {payload.map((p, i) => (
          <li key={`${p.name}-${i}`} className="flex items-center gap-2">
            <span className="size-3 shrink-0 rounded-sm" style={{ background: p.color ?? p.payload?.fill }} aria-hidden />
            <span className="text-muted-foreground">{p.name}</span>
            <span className="ml-auto pl-3 font-semibold tabular-nums">{typeof p.value === "number" ? fmtNum(p.value) : "–"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Khung biểu đồ: đọc được bằng trình đọc màn hình (aria-label tóm tắt) — bảng dữ liệu nằm ngay dưới. */
function ChartFrame({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return <div role="img" aria-label={label} className={cn("w-full min-w-0", className)}>{children}</div>;
}

type TableCell = string | number | null;

/** "Xem dưới dạng bảng": bản thay thế của biểu đồ (cuộn trong khung bảng, không làm trang cuộn ngang). */
function ChartTable({ caption, headers, rows, footer }: { caption: string; headers: string[]; rows: TableCell[][]; footer?: TableCell[] }) {
  const show = (c: TableCell) => (typeof c === "number" ? fmtNum(c) : c ?? "–");
  return (
    <TechDetails summary="Xem dưới dạng bảng" className="mt-3">
      <div className="max-h-72 overflow-auto">
        <table className="w-full min-w-max text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={h} scope="col" className={cn("sticky top-0 bg-surface-1 py-2 pr-4 font-semibold", i === 0 ? "text-left" : "text-right")}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className="border-t border-border">
                {r.map((c, ci) => ci === 0
                  ? <th key={ci} scope="row" className="py-1.5 pr-4 text-left font-normal">{show(c)}</th>
                  : <td key={ci} className="py-1.5 pr-4 text-right tabular-nums">{show(c)}</td>)}
              </tr>
            ))}
          </tbody>
          {footer && (
            <tfoot>
              <tr className="border-t-2 border-border-strong font-semibold">
                {footer.map((c, ci) => ci === 0
                  ? <th key={ci} scope="row" className="py-2 pr-4 text-left">{show(c)}</th>
                  : <td key={ci} className="py-2 pr-4 text-right tabular-nums">{show(c)}</td>)}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </TechDetails>
  );
}

interface RankItem {
  key: string; name: string; value: number; color: string;
  /** Phần phụ cạnh tên (nhãn nền tảng, liên kết...). */
  extra?: React.ReactNode;
}

/** Danh sách xếp hạng xếp chồng (đọc tốt trên điện thoại): hạng, tên, số, thanh tỉ lệ. */
function RankList({ items, label, valueLabel = "lượt xem" }: { items: RankItem[]; label: string; valueLabel?: string }) {
  const max = items.length ? Math.max(...items.map((i) => i.value), 1) : 1;
  return (
    <div>
      <p className="mb-2 inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <ArrowDownWideNarrow className="size-4" aria-hidden />
        Xếp theo {valueLabel}, cao nhất ở trên
      </p>
      <ol aria-label={label} className="divide-y divide-border">
        {items.map((it, i) => (
          <li key={it.key} className="py-2.5">
            <div className="flex items-center gap-2">
              <span className="w-6 shrink-0 text-right text-sm text-muted-foreground tabular-nums">{i + 1}</span>
              <span className="min-w-0 flex-1 truncate font-medium" title={it.name}>{it.name}</span>
              {it.extra}
              <span className="shrink-0 pl-2 font-semibold tabular-nums">{fmtNum(it.value)}</span>
            </div>
            <div className="ml-8 mt-1.5 h-2 overflow-hidden rounded-full bg-elev-2" aria-hidden>
              <div className="h-full rounded-full" style={{ width: `${Math.max(2, (it.value / max) * 100)}%`, backgroundColor: it.color }} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Chú giải cho biểu đồ tròn: tên, số, phần trăm (không chỉ màu). */
function PieLegend({ items }: { items: { name: string; value: number; color: string }[] }) {
  const total = items.reduce((s, i) => s + i.value, 0);
  return (
    <ul className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      {items.map((it) => (
        <li key={it.name} className="flex items-center gap-2">
          <span className="size-3 shrink-0 rounded-sm" style={{ background: it.color }} aria-hidden />
          <span className="min-w-0 flex-1 truncate" title={it.name}>{it.name}</span>
          <span className="shrink-0 font-semibold tabular-nums">{fmtNum(it.value)}</span>
          <span className="w-12 shrink-0 text-right text-muted-foreground tabular-nums">
            {total > 0 ? `${fmtNum(Math.round((it.value / total) * 100))}%` : "–"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function PlatformTag({ platform }: { platform: Platform }) {
  const c = useThemeColors();
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-sm text-muted-foreground">
      <span className="size-2.5 rounded-full" style={{ background: platformColor(platform, c.ramp) }} aria-hidden />
      {platformName(platform)}
    </span>
  );
}

const DASHES: (string | undefined)[] = [undefined, "6 3", "2 3", "8 3 2 3"];

// ══════════════════════════════════════════════════════════════════════════════
// TRANG
// ══════════════════════════════════════════════════════════════════════════════

export default function DashboardPage() {
  return (
    <FilterProvider>
      <DashboardInner />
    </FilterProvider>
  );
}

/** Hai nền tảng gốc của Tool. Nhãn tự do khác lấy từ `platform-tags` của server. */
const BUILTIN_PLATFORMS = ["youtube", "facebook"];
const PRESETS = [7, 30, 90] as const;

function DashboardInner() {
  // MỘT nguồn sự thật cho bộ lọc: URL query <-> FilterContext. Chỉ có bản nháp ngày (cho tới khi bấm "Áp dụng") và `topLimit` (không phải bộ lọc).
  const { filter, key: filterKey, setFilter } = useFilterContext();
  const { user } = useAuth();
  const isDashRole = user?.role === "admin" || user?.role === "dash_admin";

  const [draftFrom, setDraftFrom] = useState(filter.date_from);
  const [draftTo, setDraftTo] = useState(filter.date_to);
  useEffect(() => {
    setDraftFrom(filter.date_from); setDraftTo(filter.date_to);
  }, [filter.date_from, filter.date_to]);

  const [topLimit, setTopLimit] = useState(15);
  const [topTab, setTopTab] = useState<"rieng" | "chung">("rieng");
  const [reloadKey, setReloadKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [customToggled, setCustomToggled] = useState(false);

  // Nền tảng dùng CHUNG 1 ô chọn (trong biểu đồ diễn biến) cho toàn trang: gốc -> `platform`, nhãn tự do -> `platform_tag`.
  const scope = filter.platform || filter.platform_tag;

  // Danh sách nhân sự (role=viewer) — chỉ admin/dash_admin cần
  const [viewerAccounts, setViewerAccounts] = useState<Account[]>([]);
  useEffect(() => {
    if (!isDashRole) return;
    const ctl = new AbortController();
    adminApi.listUsers({ signal: ctl.signal })
      .then((d) => setViewerAccounts(d.users.filter((u) => u.role === "viewer")))
      .catch(() => { if (!ctl.signal.aborted) setViewerAccounts([]); });
    return () => ctl.abort();
  }, [isDashRole]);

  // Nhóm kênh + nhãn nền tảng theo nhân sự đang chọn (viewer: của chính mình). admin/dash_admin CHƯA chọn
  // nhân sự -> không có "chủ sở hữu" rõ ràng để gộp nên để rỗng.
  const ownerKnown = !isDashRole || !!filter.owner_account_id;
  const groupsQ = useAnalyticsQuery((signal) => groupsApi.list(filter.owner_account_id, { signal }),
    [filter.owner_account_id], { enabled: ownerKnown });
  const groupsList = ownerKnown ? groupsQ.data?.items ?? [] : [];
  const platformTagsQ = useAnalyticsQuery((signal) => analyticsApi.platformTags(filter, { signal }),
    [filter.owner_account_id, reloadKey], { enabled: ownerKnown });
  const platformTags = ownerKnown ? platformTagsQ.data?.items ?? [] : [];
  const allPlatforms = useMemo(
    () => [...BUILTIN_PLATFORMS, ...platformTags.filter((p) => !BUILTIN_PLATFORMS.includes(p))],
    [platformTags],
  );

  // Danh mục lọc (không co theo chính ô đang chọn — xem filterState.ts)
  const countryCatKey = countryCatalogParams(filter).toString();
  const tagsQ = useAnalyticsQuery((signal) => analyticsApi.countryTags(filter, { signal }), [countryCatKey, reloadKey]);
  const tags = tagsQ.data?.items ?? [];
  const metricsQ = useAnalyticsQuery((signal) => analyticsApi.metrics({ signal }), [reloadKey]);
  const metricList: AnalyticsMetric[] = metricsQ.data?.metrics ?? [];

  // ── DỮ LIỆU: mọi KPI/biểu đồ phụ thuộc CÙNG `filterKey` và gửi CÙNG bộ tham số ──────────────
  const summaryQ = useAnalyticsQuery((signal) => analyticsApi.summary(filter, { signal }), [filterKey, reloadKey]);
  const breakdownQ = useAnalyticsQuery((signal) => analyticsApi.channelBreakdown(filter, { signal }), [filterKey, reloadKey]);
  const trafficQ = useAnalyticsQuery((signal) => analyticsApi.traffic(filter, { signal }), [filterKey, reloadKey]);
  const byCountryQ = useAnalyticsQuery((signal) => analyticsApi.viewsByCountry(filter, { signal }), [filterKey, reloadKey]);
  const topQ = useAnalyticsQuery((signal) => analyticsApi.topChannels(filter, topLimit, { signal }),
    [filterKey, topLimit, reloadKey]);

  const dataQueries = [summaryQ, breakdownQ, trafficQ, byCountryQ, topQ];
  const unavailable = dataQueries.some((q) => q.unavailable);
  const hasAnyData = dataQueries.some((q) => q.data !== null);
  const hardError = dataQueries.find((q) => q.error && !q.unavailable)?.error ?? null;
  const freshness = mergeFreshness(dataQueries.map((q) => q.data?.freshness));
  const applied: AppliedFilter | undefined = dataQueries.map((q) => q.data?.filter).find((f) => !!f);

  useEffect(() => {
    // Một toast duy nhất (id cố định) dù nhiều widget cùng lỗi
    if (hardError) toast.error(`Không tải được thống kê: ${hardError.message}`, { id: "analytics-error" });
  }, [hardError?.message]); // eslint-disable-line react-hooks/exhaustive-deps

  const summary = summaryQ.data;

  // ── Lọc nhanh theo ngày ──
  const today = todayStr();
  const activePreset = PRESETS.find((n) => filter.date_to === today && filter.date_from === daysAgo(n - 1)) ?? null;
  const customOpen = customToggled || activePreset === null;
  const pickPreset = (n: number) => {
    setCustomToggled(false);
    setFilter({ date_from: daysAgo(n - 1), date_to: todayStr() });
  };

  const dirty = draftFrom !== filter.date_from || draftTo !== filter.date_to;
  const applyFilter = () => {
    if (!dirty) return;
    const [a, b] = draftFrom <= draftTo ? [draftFrom, draftTo] : [draftTo, draftFrom];
    setDraftFrom(a); setDraftTo(b);
    setFilter({ date_from: a, date_to: b });
  };

  const extraCount = (filter.country ? 1 : 0) + (filter.owner_account_id ? 1 : 0) + (filter.group_id ? 1 : 0);
  const [moreOpen, setMoreOpen] = useState(extraCount > 0);

  const onScope = (v: string) => {
    if (!v) setFilter({ platform: "", platform_tag: "" });
    else if (BUILTIN_PLATFORMS.includes(v) && !platformTags.includes(v)) setFilter({ platform: v, platform_tag: "" });
    else setFilter({ platform: "", platform_tag: v });
  };

  const lamMoi = async () => {
    setRefreshing(true);
    try {
      await adminApi.flushCache();
      setReloadKey((k) => k + 1);
      toast.success("Đã làm mới số liệu từ cơ sở dữ liệu.");
    } catch (e) {
      toast.error(`Không làm mới được: ${errorMessage(e)}`);
    } finally {
      setRefreshing(false);
    }
  };

  const showGroupFilter = ownerKnown && groupsList.length > 0;
  const noChannels = !!summary && summary.managed_channels === 0 && summary.total_views === 0 && summary.videos_posted === 0;

  const ownerName = (id: string) => viewerAccounts.find((a) => a.id === id)?.username;
  const subject = !isDashRole ? "Kênh của bạn"
    : filter.owner_account_id ? `Các kênh của ${ownerName(filter.owner_account_id) ?? "nhân sự đang chọn"}` : "Tất cả các kênh";

  return (
    <div>
      <PageHeader
        title="Tổng quan"
        description={
          <SummaryLine subject={subject} data={summary} loading={summaryQ.loading} noChannels={noChannels}
                       from={filter.date_from} to={filter.date_to} />
        }
        actions={isDashRole && (
          <Button variant="outline" onClick={lamMoi} disabled={refreshing}>
            <RefreshCw aria-hidden /> {refreshing ? "Đang làm mới…" : "Làm mới số liệu"}
          </Button>
        )}
      />

      {!isDashRole && summary?.linked === false && (
        <Notice tone="warning" title="Tài khoản chưa nối với nguồn dữ liệu" className="mb-4"
                action={<Button asChild size="sm" variant="outline"><Link to="/profile">Liên kết ngay</Link></Button>}>
          Số liệu sẽ trống cho đến khi bạn liên kết tài khoản với công cụ đẩy dữ liệu.
        </Notice>
      )}

      {/* ═══ Bộ lọc — mọi giá trị đi vào FilterContext ═══ */}
      <section aria-label="Chọn dữ liệu cần xem" className="mb-4 space-y-3">
        <div role="group" aria-label="Khoảng thời gian" className="flex flex-wrap items-center gap-2">
          {PRESETS.map((n) => (
            <Button key={n} type="button" variant={activePreset === n ? "default" : "outline"}
                    aria-pressed={activePreset === n} onClick={() => pickPreset(n)}>
              {activePreset === n && <Check aria-hidden />} {pluralDays(n)}
            </Button>
          ))}
          <Button type="button" variant={activePreset === null ? "default" : "outline"}
                  aria-pressed={activePreset === null} aria-expanded={customOpen} aria-controls="loc-ngay"
                  onClick={() => setCustomToggled((v) => !v)}>
            {activePreset === null && <Check aria-hidden />} Tùy chọn khoảng ngày
          </Button>
          <Button type="button" variant="ghost" aria-expanded={moreOpen} aria-controls="loc-them"
                  onClick={() => setMoreOpen((v) => !v)}>
            <SlidersHorizontal aria-hidden /> Lọc thêm{extraCount > 0 && <span className="text-primary-text">({extraCount})</span>}
          </Button>
        </div>

        {customOpen && (
          <div id="loc-ngay" className="flex flex-wrap items-end gap-3">
            <Field label="Từ ngày" className="w-44">
              {(p) => <DateInput id={p.id} value={draftFrom} max={draftTo} className="w-full" onChange={setDraftFrom} onEnter={applyFilter} />}
            </Field>
            <Field label="Đến ngày" className="w-44">
              {(p) => <DateInput id={p.id} value={draftTo} min={draftFrom} max={today} className="w-full" onChange={setDraftTo} onEnter={applyFilter} />}
            </Field>
            <Button type="button" variant={dirty ? "default" : "outline"} onClick={applyFilter} disabled={!dirty}>Áp dụng</Button>
          </div>
        )}

        {moreOpen && (
          <div id="loc-them" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Quốc gia">
              {(p) => (
                <Select value={filter.country || MOI_QUOC_GIA}
                        onValueChange={(v) => setFilter({ country: v === MOI_QUOC_GIA ? "" : v })}>
                  <SelectTrigger id={p.id}><SelectValue placeholder="Mọi quốc gia" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={MOI_QUOC_GIA}>Mọi quốc gia</SelectItem>
                    {tags.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>

            {/* Nhân sự ĐỨNG TRƯỚC nhóm kênh — chọn người trước rồi mới lọc theo nhóm/nhãn của người đó */}
            {isDashRole && (
              <Field label="Nhân sự">
                {(p) => (
                  <Select value={filter.owner_account_id || MOI_NHAN_SU}
                          onValueChange={(v) => setFilter({ owner_account_id: v === MOI_NHAN_SU ? "" : v, group_id: "" })}>
                    <SelectTrigger id={p.id}><SelectValue placeholder="Chọn nhân sự" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={MOI_NHAN_SU}>Tất cả nhân sự</SelectItem>
                      {viewerAccounts.map((a) => <SelectItem key={a.id} value={a.id}>{a.username}</SelectItem>)}
                    </SelectContent>
                  </Select>
                )}
              </Field>
            )}

            {showGroupFilter && (
              <Field label="Nhóm kênh">
                {(p) => (
                  <Select value={filter.group_id || MOI_NHOM}
                          onValueChange={(v) => setFilter({ group_id: v === MOI_NHOM ? "" : v })}>
                    <SelectTrigger id={p.id}><SelectValue placeholder="Mọi nhóm kênh" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={MOI_NHOM}>Mọi nhóm kênh</SelectItem>
                      {groupsList.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                )}
              </Field>
            )}
          </div>
        )}

        <div className="min-h-6">
          <AppliedFilterBar
            applied={applied}
            selfId={user?.id}
            ownerName={ownerName}
            groupName={(id) => groupsList.find((g) => g.id === id)?.name}
          />
        </div>
      </section>

      <div className="mb-6 space-y-3">
        {unavailable && (
          <AnalyticsUnavailableBanner hasData={hasAnyData} onRetry={() => setReloadKey((k) => k + 1)} />
        )}
        <FreshnessBanner freshness={freshness} />

        {hardError && !unavailable && (
          <Notice tone="error" title="Không tải được số liệu"
                  action={<Button type="button" size="sm" variant="outline" onClick={() => setReloadKey((k) => k + 1)}>Thử lại</Button>}>
            Máy chủ chưa trả được số liệu cho lựa chọn này. Hãy thử lại; nếu vẫn lỗi, hãy báo quản trị viên kèm chi tiết bên dưới.
            <TechDetails className="mt-2">{hardError.message}</TechDetails>
          </Notice>
        )}
      </div>

      {noChannels ? (
        <EmptyState
          icon={Unplug}
          title="Chưa có số liệu để hiển thị"
          className="mb-6"
          action={
            <div className="flex flex-wrap gap-2">
              <Button asChild><Link to="/integrations">Kết nối công cụ đẩy dữ liệu</Link></Button>
              <Button asChild variant="outline"><Link to="/resources">Thêm kênh thủ công</Link></Button>
            </div>
          }
        >
          Hãy kết nối một công cụ đẩy dữ liệu hoặc thêm kênh của bạn, rồi quay lại đây. Nếu bạn đang lọc theo quốc gia hay nhóm kênh, thử bỏ bớt bộ lọc.
        </EmptyState>
      ) : (
        <SummaryStrip data={summary} loading={summaryQ.loading} breakdown={breakdownQ.data}
                      lBreakdown={breakdownQ.loading} country={filter.country} />
      )}

      {/* ═══ Chi tiết theo kênh (theo lát cắt đang chọn) / So sánh chung ═══ */}
      <Tabs value={topTab} onValueChange={(v) => setTopTab(v as "rieng" | "chung")} className="mt-6">
        <TabsList className="w-auto">
          <TabsTrigger value="rieng">Chi tiết theo kênh</TabsTrigger>
          <TabsTrigger value="chung">So sánh chung</TabsTrigger>
        </TabsList>

        <TabsContent value="rieng" className="mt-6">
          <TrafficMetricChart data={trafficQ.data} loading={trafficQ.loading} metrics={metricList}
                              scope={scope} onScope={onScope} platformTags={allPlatforms} />
          <CountryComparisonCard byCountry={byCountryQ.data} />
          {filter.country && (
            <PlatformBreakdownCard country={filter.country} reloadKey={reloadKey} />
          )}
          <div className="grid grid-cols-1 gap-x-10 lg:grid-cols-2">
            <TopChannelsCard items={topQ.data?.items ?? null} loading={topQ.loading} country={filter.country} limit={topLimit} onLimit={setTopLimit} />
            <CountryViewsCard data={byCountryQ.data} loading={byCountryQ.loading} country={filter.country} />
          </div>
        </TabsContent>

        <TabsContent value="chung" className="mt-6">
          <ComparisonView isDashRole={isDashRole} reloadKey={reloadKey} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Câu tóm tắt (điểm nhấn của trang) và dải số liệu
// ══════════════════════════════════════════════════════════════════════════════

function SummaryLine({ subject, data, loading, noChannels, from, to }: {
  subject: string; data: AnalyticsSummary | null; loading: boolean; noChannels: boolean; from: string; to: string;
}) {
  if (!data) {
    return loading
      ? <Skeleton className="h-7 w-full max-w-xl animate-none" />
      : <span>Chưa tải được số liệu tổng quan. Hãy chọn lại khoảng thời gian hoặc thử lại sau.</span>;
  }
  if (noChannels) return <span>Chưa có số liệu nào để tóm tắt.</span>;

  const n = data.days_in_range;
  const endsToday = to === todayStr();
  const range = endsToday ? `${pluralDays(n)} qua` : `${pluralDays(n)} từ ${fmtDay(from)} đến ${fmtDay(to)}`;
  const period = endsToday ? `${pluralDays(n)} trước` : "kỳ trước đó";
  const cmp = compare(data.total_views, data.total_views_prev);

  if (data.total_views === 0) {
    return <span className="text-lg leading-snug">{subject} chưa có lượt xem nào trong {range}.</span>;
  }
  return (
    <span className="text-lg leading-snug">
      {subject} được xem <strong className="font-bold text-foreground">{fmtNum(data.total_views)}</strong> lần trong {range}
      {cmp.dir === "none"
        ? `. Hệ thống chưa có số liệu của ${period} nên chưa so sánh được.`
        : <>, <strong className="font-semibold text-foreground">{cmp.phrase(period)}</strong>.</>}
    </span>
  );
}

function SummaryStrip({ data, loading, breakdown, lBreakdown, country }: {
  data: AnalyticsSummary | null; loading: boolean;
  breakdown: ChannelBreakdown | null; lBreakdown: boolean; country: string;
}) {
  if (loading && !data) {
    return (
      <div className="min-h-[7.5rem] border-y border-border py-4" aria-busy="true">
        <span className="sr-only" role="status">Đang tải số liệu tổng quan…</span>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className={cn("space-y-2", i === 2 && "col-span-2 md:col-span-1")}>
              <Skeleton className="h-4 w-24 animate-none" />
              <Skeleton className="h-8 w-32 animate-none" />
              <Skeleton className="h-4 w-full animate-none" />
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (!data) return null;

  const perDay = data.days_in_range > 0 ? data.videos_posted / data.days_in_range : 0;
  const viewsCmp = compare(data.total_views, data.total_views_prev);
  const videosCmp = compare(data.videos_posted, data.videos_posted_prev);

  const ch = channelCounts(breakdown, country, data);

  const stats: Stat[] = [
    {
      label: "Tổng lượt xem",
      value: fmtNum(data.total_views),
      note: <DeltaNote cmp={viewsCmp} />,
      tone: toneOf(viewsCmp),
    },
    {
      label: "Video đã đăng",
      value: fmtNum(data.videos_posted),
      note: (
        <>
          <span className="block">khoảng {perDay.toLocaleString("vi-VN", { maximumFractionDigits: 1 })} video mỗi ngày</span>
          <DeltaNote cmp={videosCmp} />
        </>
      ),
      tone: toneOf(videosCmp),
    },
    {
      label: country ? `Kênh đang hoạt động (${country})` : "Kênh đang hoạt động",
      value: lBreakdown && !breakdown && !ch ? "…" : fmtNum(ch?.active ?? 0),
      note: ch ? `trên tổng số ${fmtNum(ch.managed)} kênh` : undefined,
    },
  ];

  return (
    <div>
      <StatStrip stats={stats} className="md:grid-cols-3" />
      {breakdown && <ChannelBreakdownDetails breakdown={breakdown} country={country} />}
    </div>
  );
}

/** Số kênh đang hoạt động / đang quản lý — thu hẹp theo quốc gia đang lọc (không hiện số toàn hệ thống khi đang lọc). */
function channelCounts(breakdown: ChannelBreakdown | null, country: string, summary: AnalyticsSummary) {
  if (!breakdown) return { active: summary.active_channels, managed: summary.managed_channels };
  let managed = breakdown.total.managed;
  let active = breakdown.total.active;
  if (country) {
    managed = 0; active = 0;
    for (const p of breakdown.by_platform) {
      const c = p.by_country?.find((x) => x.country === country);
      managed += c?.managed ?? 0;
      active += c?.active ?? 0;
    }
  }
  return { active, managed };
}

/** Kênh theo từng nền tảng (và theo chủ đề khi một trang chạy nhiều chủ đề) — gập lại cho gọn. */
function ChannelBreakdownDetails({ breakdown, country }: { breakdown: ChannelBreakdown; country: string }) {
  const rows = breakdown.by_platform
    .map((p) => {
      const c = country ? p.by_country?.find((x) => x.country === country) : null;
      return { p, managed: country ? (c?.managed ?? 0) : p.managed, active: country ? (c?.active ?? 0) : p.active };
    })
    .filter((r) => r.managed > 0);
  if (!rows.length) return null;
  return (
    <TechDetails summary="Xem số kênh theo nền tảng" className="mt-3">
      <ul className="divide-y divide-border">
        {rows.map(({ p, managed, active }) => (
          <li key={p.platform} className="py-2">
            <div className="flex items-center justify-between gap-3">
              <span>{platformName(p.platform)}{p.is_custom_tag ? " (nhãn tự đặt)" : ""}</span>
              <span className="tabular-nums"><strong>{fmtNum(active)}</strong> đang hoạt động / {fmtNum(managed)} kênh</span>
            </div>
            {!country && p.by_group && p.by_group.length > 1 && (
              <ul className="mt-1 space-y-0.5 pl-4 text-muted-foreground">
                {p.by_group.map((g) => (
                  <li key={g.group} className="flex items-center justify-between gap-3">
                    <span>Chủ đề {g.group}</span>
                    <span className="tabular-nums">{fmtNum(g.active)} / {fmtNum(g.managed)}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </TechDetails>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Diễn biến theo ngày (TrafficMetricChart)
// ══════════════════════════════════════════════════════════════════════════════

type MetricKind = "line" | "bar" | "pie" | "mixed";

const METRIC_ORDER: TrafficMetricKey[] = ["views", "interactions", "comments", "videos"];
/** Chỉ số -> vị trí trong dải màu --chart-1..6. */
const METRIC_RAMP: Record<TrafficMetricKey, number> = { views: 0, interactions: 2, comments: 3, videos: 4 };
const KIND_NAME: Record<MetricKind, string> = { line: "đường", bar: "cột", pie: "tròn", mixed: "kết hợp" };

function TrafficMetricChart({ data, loading, metrics, scope, onScope, platformTags }: {
  data: TrafficMetricSeries | null;
  loading: boolean;
  metrics: AnalyticsMetric[];
  scope: string;
  onScope: (s: string) => void;
  platformTags: string[];
}) {
  const c = useThemeColors();
  const [kind, setKind] = useState<MetricKind>("line");
  const [selected, setSelected] = useState<Set<TrafficMetricKey>>(() => new Set(["views"]));
  const [splitAxes, setSplitAxes] = useState(false);

  const label = useCallback((k: TrafficMetricKey) =>
    metrics.find((m) => m.key === k)?.label
      ?? ({ views: "Lượt xem", interactions: "Lượt tương tác", comments: "Bình luận", videos: "Số video" }[k]),
    [metrics]);
  const color = (k: TrafficMetricKey) => c.ramp[METRIC_RAMP[k]];

  const toggle = (k: TrafficMetricKey) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });

  const shown = METRIC_ORDER.filter((k) => selected.has(k));

  // Trục Y riêng: lượt xem giữ trục trái, chỉ số khác dùng trục phải tự co-scale
  const showRightAxis =
    (kind === "mixed" && shown.includes("videos")) ||
    (splitAxes && kind !== "mixed" && shown.includes("views") && shown.length > 1);
  const yAxisIdOf = (k: TrafficMetricKey): string => {
    if (kind === "mixed") return k === "videos" ? "right" : "left";
    if (showRightAxis && splitAxes) return k === "views" ? "left" : "right";
    return "left";
  };

  const rows = data?.days ?? [];
  const pie = useMemo(
    () => shown.map((k) => ({ key: k, name: label(k), value: data?.totals?.[k] ?? 0, fill: color(k) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, shown.join("|"), c],
  );

  const customTags = platformTags.filter((p) => p !== "youtube" && p !== "facebook");
  const scopeLabel = scope === "" ? "tất cả nền tảng" : scope === "youtube" ? "chỉ YouTube"
    : scope === "facebook" ? "chỉ Facebook" : `nhãn "${scope}"`;

  const ariaLabel = rows.length
    ? `Biểu đồ ${KIND_NAME[kind]}: ${shown.map(label).join(", ")} ${kind === "pie" ? "tổng cả khoảng ngày" : "theo ngày"}, ${scopeLabel}, từ ${fmtDay(rows[0].date)} đến ${fmtDay(rows[rows.length - 1].date)}. `
      + shown.map((k) => `Tổng ${label(k).toLowerCase()}: ${fmtNum(data?.totals?.[k] ?? 0)}`).join("; ") + "."
    : "Biểu đồ chưa có số liệu";

  const kindTabs = (
    <Tabs value={kind} onValueChange={(v) => setKind(v as MetricKind)}>
      <TabsList aria-label="Kiểu biểu đồ" className="w-auto">
        <TabsTrigger value="line" className="px-3">Đường</TabsTrigger>
        <TabsTrigger value="bar" className="px-3">Cột</TabsTrigger>
        <TabsTrigger value="pie" className="px-3">Tròn</TabsTrigger>
        <TabsTrigger value="mixed" className="px-3">Kết hợp</TabsTrigger>
      </TabsList>
    </Tabs>
  );

  return (
    <Section id="dien-bien" title="Diễn biến theo ngày"
             description={`Đang xem ${scopeLabel}${kind === "pie" ? ", tổng cả khoảng ngày" : ", từng ngày"}.`}
             actions={kindTabs}>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Nền tảng" className="w-full sm:w-56">
          {(p) => (
            <Select value={scope || SCOPE_ALL} onValueChange={(v) => onScope(v === SCOPE_ALL ? "" : v)}>
              <SelectTrigger id={p.id}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={SCOPE_ALL}>Tất cả nền tảng</SelectItem>
                <SelectItem value="youtube">YouTube</SelectItem>
                <SelectItem value="facebook">Facebook</SelectItem>
                {customTags.map((p2) => <SelectItem key={p2} value={p2}>{p2}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
        </Field>
        <div role="group" aria-label="Chỉ số cần vẽ" className="flex flex-wrap items-center gap-2">
          {METRIC_ORDER.map((k) => {
            const on = selected.has(k);
            const meta = metrics.find((m) => m.key === k);
            const zero = meta && !meta.ready;
            return (
              <button key={k} type="button" onClick={() => toggle(k)} aria-pressed={on}
                      className={cn(
                        "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm transition-colors md:min-h-10",
                        on ? "border-primary bg-primary/10 font-semibold text-foreground" : "border-border-strong text-muted-foreground hover:bg-elev-2")}>
                <span className="size-3 shrink-0 rounded-sm" style={{ background: color(k) }} aria-hidden />
                {on && <Check className="size-4" aria-hidden />}
                {label(k)}{zero ? " (chưa có số liệu theo ngày)" : ""}
              </button>
            );
          })}
          {kind !== "pie" && kind !== "mixed" && (
            <button type="button" onClick={() => setSplitAxes((v) => !v)} aria-pressed={splitAxes}
                    className={cn(
                      "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm transition-colors md:min-h-10",
                      splitAxes ? "border-primary bg-primary/10 font-semibold text-foreground" : "border-border-strong text-muted-foreground hover:bg-elev-2")}
                    title="Lượt xem dùng trục bên trái, các chỉ số còn lại dùng trục bên phải với thang riêng">
              {splitAxes && <Check className="size-4" aria-hidden />} Tách trục số
            </button>
          )}
        </div>
      </div>

      {loading ? <LoadingBlock className="h-[320px] sm:h-[380px]" />
        : !shown.length ? <div className="h-[320px] sm:h-[380px]"><EmptyNote text="Chọn ít nhất một chỉ số để vẽ biểu đồ." /></div>
        : !rows.length ? <div className="h-[320px] sm:h-[380px]"><EmptyNote text="Chưa có số liệu trong khoảng ngày này." /></div>
        : kind === "pie" ? (
          <>
            <ChartFrame label={ariaLabel} className="h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart accessibilityLayer={false}>
                  <Pie data={pie} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="80%" paddingAngle={2} stroke="var(--surface-1)">
                    {pie.map((p) => <Cell key={p.key} fill={p.fill} />)}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                </PieChart>
              </ResponsiveContainer>
            </ChartFrame>
            <PieLegend items={pie.map((p) => ({ name: p.name, value: p.value, color: p.fill }))} />
          </>
        ) : (
          <ChartFrame label={ariaLabel} className="h-[320px] sm:h-[380px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} accessibilityLayer={false} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={12} interval="preserveStartEnd" minTickGap={24} tickMargin={6} />
                <YAxis yAxisId="left" stroke={c.axis} fontSize={12} tickFormatter={compactNum} width={56} />
                {showRightAxis && (
                  <YAxis yAxisId="right" orientation="right" stroke={c.axis} fontSize={12}
                         tickFormatter={compactNum} allowDecimals={false} width={52} />
                )}
                <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                {shown.map((k, i) => {
                  const onRight = yAxisIdOf(k) === "right";
                  const asBar = kind === "bar" || (kind === "mixed" && k === "videos");
                  return asBar ? (
                    <Bar key={k} yAxisId={onRight ? "right" : "left"} dataKey={k} name={label(k)}
                         fill={color(k)} {...(onRight ? { barSize: 10, radius: [2, 2, 0, 0] as [number, number, number, number] } : {})} />
                  ) : (
                    <Line key={k} yAxisId={yAxisIdOf(k)} type="monotone" dataKey={k} name={label(k)}
                          stroke={color(k)} strokeWidth={2.5} strokeDasharray={DASHES[i % DASHES.length]}
                          dot={rows.length <= 31 ? { r: 2.5, strokeWidth: 0, fill: color(k) } : false}
                          activeDot={{ r: 5, strokeWidth: 0, fill: color(k) }} />
                  );
                })}
              </ComposedChart>
            </ResponsiveContainer>
          </ChartFrame>
        )}

      {!loading && shown.length > 0 && rows.length > 0 && (
        <ChartTable
          caption={`Số liệu theo ngày: ${shown.map(label).join(", ")}`}
          headers={["Ngày", ...shown.map(label)]}
          rows={rows.map((r) => [fmtDay(r.date), ...shown.map((k) => r[k])])}
          footer={["Tổng", ...shown.map((k) => data?.totals?.[k] ?? 0)]}
        />
      )}

      <p className="mt-3 text-sm text-muted-foreground">
        Trên trục dọc, N là nghìn và tr là triệu; rê chuột hoặc chạm vào biểu đồ để xem số đầy đủ.{" "}
        {scope === "youtube"
          ? "YouTube chưa có số lượt tương tác và bình luận theo ngày."
          : "Lượt tương tác và bình luận lấy từ Business Suite; hiện 0 nếu trang chưa có cột đó."}
      </p>
    </Section>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Kênh nổi bật
// ══════════════════════════════════════════════════════════════════════════════

const TOP_LIMITS = [10, 15, 20, 30, 50];

function TopChannelsCard({ items, loading, country, limit, onLimit }: {
  items: TopChannel[] | null; loading: boolean; country: string; limit: number; onLimit: (n: number) => void;
}) {
  const c = useThemeColors();
  const rank: RankItem[] = (items ?? []).map((it) => {
    const link = it.platform === "facebook" ? fbLink(it.id) : null;
    return {
      key: it.id,
      name: it.name,
      value: it.views,
      color: platformColor(it.platform, c.ramp),
      extra: (
        <>
          <PlatformTag platform={it.platform} />
          {it.country && <span className="shrink-0 text-sm text-muted-foreground">{it.country}</span>}
          {link && (
            <a href={link} target="_blank" rel="noopener noreferrer" aria-label={`Mở trang Facebook của ${it.name} (tab mới)`}
               className="-m-3 grid size-10 shrink-0 place-items-center rounded-md text-muted-foreground hover:text-foreground">
              <ExternalLink className="size-4" aria-hidden />
            </a>
          )}
        </>
      ),
    };
  });

  return (
    <Section id="kenh-noi-bat" title="Kênh nổi bật"
             description={country ? `Trong quốc gia ${country}` : "Ở mọi quốc gia"}
             actions={
               <Select value={String(limit)} onValueChange={(v) => onLimit(Number(v))}>
                 <SelectTrigger className="w-40" aria-label="Số kênh hiển thị"><SelectValue /></SelectTrigger>
                 <SelectContent>
                   {TOP_LIMITS.map((n) => <SelectItem key={n} value={String(n)}>{n} kênh đầu</SelectItem>)}
                 </SelectContent>
               </Select>
             }>
      {loading && !items ? <LoadingBlock className="min-h-48" />
        : !rank.length ? <EmptyNote text="Chưa có kênh nào có lượt xem trong khoảng ngày này." />
        : <RankList items={rank} label="Kênh nổi bật theo lượt xem" />}
    </Section>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Lượt xem theo quốc gia
// ══════════════════════════════════════════════════════════════════════════════

function CountryViewsCard({ data, loading, country }: {
  data: CountryViewsSeries | null; loading: boolean; country: string;
}) {
  const c = useThemeColors();
  const [showDays, setShowDays] = useState(false);
  const allCountries = data?.countries ?? [];
  const shownCountries = country ? allCountries.filter((k) => k === country) : allCountries;
  const rows = data?.days ?? [];
  const hasAny = rows.some((r) => shownCountries.some((k) => Number(r[k] ?? 0) > 0));
  const ranked: RankItem[] = shownCountries
    .map((name, i) => ({ key: name, name, value: data?.totals[name] ?? 0, color: c.ramp[i % c.ramp.length] }))
    .sort((a, b) => b.value - a.value);

  return (
    <Section id="theo-quoc-gia" title="Lượt xem theo quốc gia"
             description={country ? `Chỉ ${country}` : "Quốc gia nào được xem nhiều nhất trong khoảng đang chọn"}>
      {loading && !data ? <LoadingBlock className="min-h-48" />
        : !shownCountries.length ? <EmptyNote text="Chưa gắn quốc gia cho kênh nào. Hãy tạo và gắn thẻ quốc gia ở trang Nhóm kênh." />
        : !hasAny ? <EmptyNote text="Quốc gia này chưa có lượt xem trong khoảng ngày đã chọn." />
        : (
          <>
            <RankList items={ranked} label="Quốc gia theo lượt xem" />
            <details className="mt-4" onToggle={(e) => setShowDays((e.currentTarget as HTMLDetailsElement).open)}>
              <summary className="min-h-11 cursor-pointer list-none py-2 text-sm font-medium text-primary-text hover:underline">
                Xem từng ngày theo quốc gia
              </summary>
              {showDays && (
                <>
                  <ChartFrame label={`Biểu đồ cột: lượt xem mỗi ngày của ${shownCountries.join(", ")}`} className="h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={rows} accessibilityLayer={false} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                        <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={12} interval="preserveStartEnd" minTickGap={24} />
                        <YAxis stroke={c.axis} fontSize={12} tickFormatter={compactNum} width={56} />
                        <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                        <Legend wrapperStyle={{ fontSize: 13 }} />
                        {shownCountries.map((name, i) => (
                          <Bar key={name} dataKey={name} name={name} fill={c.ramp[i % c.ramp.length]} />
                        ))}
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartFrame>
                  <ChartTable
                    caption="Lượt xem mỗi ngày theo quốc gia"
                    headers={["Ngày", ...shownCountries]}
                    rows={rows.map((r) => [fmtDay(r.date), ...shownCountries.map((k) => (typeof r[k] === "number" ? (r[k] as number) : null))])}
                  />
                </>
              )}
            </details>
          </>
        )}
    </Section>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// So sánh (nền tảng/nhân sự/kênh/nhóm kênh; quốc gia nằm ở "Chi tiết theo kênh")
// ══════════════════════════════════════════════════════════════════════════════
//
// Mỗi lát cắt so sánh xem được ở 3 dạng: Cột (xếp hạng)/Đường/Tròn. Bộ lọc áp dụng CHUNG cho cả hai tab qua FilterContext.
// So sánh QUỐC GIA KHÔNG nằm ở "So sánh chung" (có phần riêng trong "Chi tiết theo kênh").

interface CompareSeries { key: string; name: string; total: number; days?: { date: string; value: number | null }[] }

type CompareKind = "bar" | "line" | "pie";
const LINE_MAX = 6;

function ComparisonChart({ id, title, subtitle, series, loading, emptyText, allowLine = true }: {
  id: string; title: string; subtitle?: string; series: CompareSeries[] | null; loading: boolean;
  emptyText: string; allowLine?: boolean;
}) {
  const c = useThemeColors();
  const [kind, setKind] = useState<CompareKind>("bar");
  const [expanded, setExpanded] = useState(false);
  const sorted = useMemo(() => (series ? [...series].sort((a, b) => b.total - a.total) : null), [series]);

  // Gộp `days` của mọi chuỗi thành 1 mảng theo ngày — {date, [tên]: giá trị} — đúng shape recharts LineChart cần.
  const lineSeries = useMemo(() => (sorted ?? []).slice(0, LINE_MAX), [sorted]);
  const dayRows = useMemo(() => {
    if (!lineSeries.some((s) => s.days?.length)) return [];
    const byDate: Record<string, Record<string, number | string | null>> = {};
    for (const s of lineSeries) {
      for (const d of s.days || []) {
        byDate[d.date] = byDate[d.date] || { date: d.date };
        byDate[d.date][s.name] = d.value;
      }
    }
    return Object.values(byDate).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [lineSeries]);

  const pieData = useMemo(
    () => (sorted || []).map((s, i) => ({ name: s.name, value: s.total, color: c.ramp[i % c.ramp.length] })),
    [sorted, c],
  );
  const rankItems: RankItem[] = (sorted ?? []).map((s, i) => ({ key: s.key, name: s.name, value: s.total, color: c.ramp[i % c.ramp.length] }));

  const ariaLabel = sorted?.length
    ? `Biểu đồ ${kind === "bar" ? "cột xếp hạng" : kind === "line" ? "đường theo ngày" : "tròn"} — ${title}: ${sorted.slice(0, 5).map((s) => `${s.name} ${fmtNum(s.total)}`).join("; ")}${sorted.length > 5 ? "; và các mục khác" : ""}.`
    : title;

  // Tách phần vẽ ra hàm riêng để dùng lại y hệt cho bản thu nhỏ LẪN bản phóng to trong hộp thoại.
  const renderBody = (heightCls: string) => (
    <div>
      {loading && !sorted ? <LoadingBlock className="min-h-48" />
        : !sorted?.length ? <div className="min-h-48"><EmptyNote text={emptyText} /></div>
        : kind === "pie" ? (
          <>
            <ChartFrame label={ariaLabel} className={heightCls}>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart accessibilityLayer={false}>
                  <Pie data={pieData} dataKey="value" nameKey="name" innerRadius="42%" outerRadius="80%" paddingAngle={2} stroke="var(--surface-1)">
                    {pieData.map((p) => <Cell key={p.name} fill={p.color} />)}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                </PieChart>
              </ResponsiveContainer>
            </ChartFrame>
            <PieLegend items={pieData} />
          </>
        ) : kind === "line" && allowLine ? (
          !dayRows.length ? <div className="min-h-48"><EmptyNote text="Chưa có số liệu theo ngày." /></div> : (
            <>
              <ChartFrame label={ariaLabel} className={heightCls}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={dayRows} accessibilityLayer={false} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={12} interval="preserveStartEnd" minTickGap={24} />
                    <YAxis stroke={c.axis} fontSize={12} tickFormatter={compactNum} width={56} />
                    <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                    <Legend wrapperStyle={{ fontSize: 13 }} />
                    {lineSeries.map((s, i) => (
                      <Line key={s.key} type="monotone" dataKey={s.name} name={s.name}
                            stroke={c.ramp[i % c.ramp.length]} strokeWidth={2.5} strokeDasharray={DASHES[i % DASHES.length]}
                            dot={false} activeDot={{ r: 5, strokeWidth: 0 }} connectNulls={false} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </ChartFrame>
              {(sorted?.length ?? 0) > LINE_MAX && (
                <p className="mt-2 text-sm text-muted-foreground">Biểu đồ đường chỉ vẽ {LINE_MAX} mục cao nhất; các mục còn lại xem ở dạng Cột hoặc bảng.</p>
              )}
            </>
          )
        ) : (
          <RankList items={rankItems} label={title} />
        )}
      {sorted?.length ? (
        kind === "line" && allowLine && dayRows.length ? (
          <ChartTable caption={`${title}: lượt xem theo ngày`} headers={["Ngày", ...lineSeries.map((s) => s.name)]}
                      rows={dayRows.map((r) => [fmtDay(String(r.date)), ...lineSeries.map((s) => (typeof r[s.name] === "number" ? (r[s.name] as number) : null))])} />
        ) : (
          <ChartTable caption={`${title}: tổng lượt xem`} headers={["Tên", "Tổng lượt xem"]} rows={sorted.map((s) => [s.name, s.total])} />
        )
      ) : null}
    </div>
  );

  const kindTabs = (
    <Tabs value={kind} onValueChange={(v) => setKind(v as CompareKind)}>
      <TabsList aria-label={`Kiểu biểu đồ: ${title}`} className="w-auto">
        <TabsTrigger value="bar" className="px-3">Cột</TabsTrigger>
        {allowLine && <TabsTrigger value="line" className="px-3">Đường</TabsTrigger>}
        <TabsTrigger value="pie" className="px-3">Tròn</TabsTrigger>
      </TabsList>
    </Tabs>
  );

  return (
    <Section id={id} title={title} description={subtitle}
             actions={
               <>
                 {kindTabs}
                 <Button size="icon" variant="outline" onClick={() => setExpanded(true)}
                         aria-label={`Phóng to biểu đồ: ${title}`} title="Phóng to">
                   <Maximize2 aria-hidden />
                 </Button>
               </>
             }>
      {renderBody("h-[280px]")}

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="max-w-5xl">
          <DialogTitle>{title}</DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">{kindTabs}</div>
          <div className="mt-3">{renderBody("h-[60vh]")}</div>
        </DialogContent>
      </Dialog>
    </Section>
  );
}

// Quốc gia — hiển thị TRỰC TIẾP trong "Chi tiết theo kênh". Dùng `byCountry` đã tải sẵn (không gọi thêm API) và tự thu hẹp theo
// bộ lọc hiện hành vì `byCountry` đã được server tính theo đúng bộ lọc đó.
function CountryComparisonCard({ byCountry }: { byCountry: CountryViewsSeries | null }) {
  const countryCompare: CompareSeries[] | null = useMemo(() => byCountry
    ? byCountry.countries.map((name) => ({
        key: name, name, total: byCountry.totals[name] || 0,
        days: byCountry.days.map((d) => ({
          date: d.date, value: typeof d[name] === "number" ? (d[name] as number) : null,
        })),
      }))
    : null, [byCountry]);
  return (
    <ComparisonChart id="so-sanh-quoc-gia" title="So sánh quốc gia"
      subtitle="Chọn dạng Cột, Đường hoặc Tròn. Tự thu hẹp theo bộ lọc đang chọn ở trên."
      series={countryCompare} loading={!byCountry} emptyText="Chưa có dữ liệu quốc gia." />
  );
}

const toSeries = (items: CompareItem[]): CompareSeries[] =>
  items.map((it) => ({ key: it.key, name: it.name, total: it.total, days: it.series }));

/**
 * Một phần so sánh = MỘT request `GET /v1/analytics/compare/{dimension}` với CHÍNH bộ lọc của FilterContext.
 */
function CompareCard({ dimension, title, subtitle, emptyText, reloadKey }: {
  dimension: CommonCompareDimension; title: string; subtitle: string; emptyText: string; reloadKey: number;
}) {
  const { filter, key } = useFilterContext();
  const q = useAnalyticsQuery(
    (signal) => analyticsApi.compare(dimension as CompareDimension, filter, { signal }),
    [dimension, key, reloadKey],
  );
  const series = useMemo(() => (q.data ? toSeries(q.data.items) : null), [q.data]);
  const hasDays = !!series?.some((s) => s.days?.length);
  const unavailableText = q.unavailable && !q.data ? "Dịch vụ thống kê tạm thời không khả dụng. Thử lại sau ít phút." : null;
  return (
    <ComparisonChart id={`so-sanh-${dimension}`} title={title} subtitle={subtitle} series={series} loading={q.loading}
      emptyText={unavailableText ?? (q.error && !q.data ? "Chưa tải được phần này. Thử chọn lại khoảng ngày hoặc tải lại trang." : emptyText)}
      allowLine={hasDays} />
  );
}

const COMPARE_META: Record<CommonCompareDimension, { title: string; subtitle: string; emptyText: string; dashOnly?: boolean }> = {
  platform: {
    title: "So sánh nền tảng", subtitle: "Theo nhãn nền tảng đã gắn ở trang Nhóm kênh",
    emptyText: "Chưa gắn nhãn nền tảng nào cho kênh. Hãy vào Nhóm kênh để gắn.",
  },
  owner: {
    title: "So sánh nhân sự", subtitle: "Tổng lượt xem và xu hướng của từng nhân sự",
    emptyText: "Chưa có nhân sự nào.", dashOnly: true,
  },
  channel: {
    title: "So sánh kênh", subtitle: "Cùng bộ lọc với phần Kênh nổi bật",
    emptyText: "Chưa có kênh nào có lượt xem.",
  },
  group: {
    title: "So sánh nhóm kênh", subtitle: "Tổng lượt xem và xu hướng của từng nhóm đã tạo",
    emptyText: "Chưa tạo nhóm kênh nào.",
  },
};

// Chỉ render các chiều trong COMMON_COMPARE_DIMENSIONS — so sánh QUỐC GIA KHÔNG nằm ở đây.
function ComparisonView({ isDashRole, reloadKey }: { isDashRole: boolean; reloadKey: number }) {
  return (
    <div>
      {COMMON_COMPARE_DIMENSIONS.map((dim) => {
        const m = COMPARE_META[dim];
        if (m.dashOnly && !isDashRole) return null;
        return <CompareCard key={dim} dimension={dim} reloadKey={reloadKey} {...m} />;
      })}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Phân bổ nền tảng cho 1 quốc gia (khi đang lọc theo quốc gia)
// ══════════════════════════════════════════════════════════════════════════════

function PlatformBreakdownCard({ country, reloadKey }: { country: string; reloadKey: number }) {
  const c = useThemeColors();
  const { filter, key } = useFilterContext();
  const q = useAnalyticsQuery((signal) => analyticsApi.compare("platform", filter, { signal }), [key, reloadKey]);
  const items = (q.data?.items ?? [])
    .map((it) => ({ key: it.key, name: it.name, value: it.total || 0 }))
    .filter((r) => r.value > 0)
    .map((r, i) => ({ ...r, color: c.ramp[i % c.ramp.length] }));

  return (
    <Section id="nen-tang-quoc-gia" title={`Nền tảng ở ${country}`}
             description="Theo nhãn nền tảng đã gắn ở Nhóm kênh cho các kênh thuộc quốc gia này.">
      {q.loading && !q.data ? <LoadingBlock className="min-h-48" />
        : !items.length ? <EmptyNote text="Kênh ở quốc gia này chưa gắn nhãn nền tảng nào." />
        : (
          <>
            <ChartFrame label={`Biểu đồ tròn: phân bổ lượt xem theo nền tảng ở ${country}: ${items.map((i) => `${i.name} ${fmtNum(i.value)}`).join("; ")}.`} className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart accessibilityLayer={false}>
                  <Pie data={items} dataKey="value" nameKey="name" innerRadius="40%" outerRadius="80%" paddingAngle={2} stroke="var(--surface-1)">
                    {items.map((it) => <Cell key={it.key} fill={it.color} />)}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} wrapperStyle={{ zIndex: 20 }} />
                </PieChart>
              </ResponsiveContainer>
            </ChartFrame>
            <PieLegend items={items} />
            <ChartTable caption={`Lượt xem theo nền tảng ở ${country}`} headers={["Nền tảng", "Lượt xem"]} rows={items.map((i) => [i.name, i.value])} />
          </>
        )}
    </Section>
  );
}

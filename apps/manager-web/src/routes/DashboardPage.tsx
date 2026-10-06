import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Filter, Lock, Maximize2, RefreshCw, Unlock } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, LabelList, Line, LineChart, Pie,
  PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  adminApi,
  analyticsApi,
  groupsApi,
  type Account,
  type AnalyticsMetric,
  type AnalyticsSummary,
  type ChannelBreakdown,
  type CompareDimension,
  type CompareItem,
  type CountryViewsSeries,
  type TopChannel,
  type TrafficMetricKey,
  type TrafficMetricPoint,
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
import { useAuth } from "../auth";
import { FilterProvider, useFilterContext } from "../filters/FilterContext";
import { countryCatalogParams, todayStr } from "../filters/filterState";
import { useAnalyticsQuery } from "../filters/useAnalyticsQuery";
import { mergeFreshness } from "../filters/freshness";
import { FreshnessBanner, AnalyticsUnavailableBanner } from "../components/AnalyticsStatus";
import { bindLockToUser, useDataLock } from "../filters/dataLock";
import { errorMessage } from "../lib/errors";
import { COMMON_COMPARE_DIMENSIONS, type CommonCompareDimension } from "../filters/compareDimensions";
import { safeHttpUrl } from "../lib/safeUrl";

// ── Helpers (copy từ AnalyticsView — cùng logic, cùng output) ──────────────

const fmtNum = (n: number | null | undefined) =>
  n === null || n === undefined ? "–" : n.toLocaleString("vi-VN");

const shortDay = (iso: string) => (iso.length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : iso);

/** Số rút gọn cho nhãn cột — tránh tràn khi cột hẹp */
const shortNum = (v: number): string => {
  if (!v) return "";
  if (v >= 1_000_000) return `${+(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${+(v / 1_000).toFixed(1)}K`;
  return v.toLocaleString("vi-VN");
};

type TipValue = number | string | readonly (number | string)[] | undefined;
const asNum = (v: TipValue): number | null => (typeof v === "number" ? v : null);
const tipViews = (v: TipValue) => fmtNum(asNum(v));

/** Từ id dạng "page:<numeric>" hoặc "profile:<numeric>" → link Facebook (dùng profile.php?id để
 * cover cả page lẫn trang cá nhân qua cùng một dạng URL). Trả null nếu id không phải FB. */
function fbLink(id: string): string | null {
  const sep = id.indexOf(":");
  if (sep < 0) return null;
  const kind = id.slice(0, sep);
  const fid  = id.slice(sep + 1);
  if (!fid || (kind !== "page" && kind !== "profile")) return null;
  return safeHttpUrl(`https://www.facebook.com/profile.php?id=${encodeURIComponent(fid)}`);
}

function useThemeColors() {
  const read = useCallback(() => {
    const s = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => s.getPropertyValue(name).trim() || fallback;
    return {
      yt: v("--destructive", "#ef4444"),
      fb: v("--accent-2", "#38bdf8"),
      ramp: [
        v("--chart-1", "#3b6fd4"), v("--chart-2", "#b07d1e"), v("--chart-3", "#3a8a86"),
        v("--chart-4", "#8a52c8"), v("--chart-5", "#2f8a5c"), v("--chart-6", "#c05563"),
      ],
      grid: v("--hairline", "#ffffff22"),
      axis: v("--muted-foreground", "#9ca3af"),
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

// Radix Select cấm `value=""` — dùng sentinel cho "tất cả"
const MOI_QUOC_GIA = "__all";
const SCOPE_ALL = "__both";
const MOI_NHAN_SU = "__owner_all";
const MOI_NHOM = "__group_all";

function cn(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

// ── Delta label ────────────────────────────────────────────────────────────

function deltaPct(cur: number, prev: number): number | null {
  return prev > 0 ? Math.round(((cur - prev) / prev) * 100) : null;
}

function DeltaLabel({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-muted-foreground">— chưa so được</span>;
  if (pct === 0) return <span className="text-muted-foreground">— không đổi</span>;
  const up = pct > 0;
  return (
    <span className={up ? "text-success" : "text-destructive"}>
      {up ? "▲" : "▼"} {Math.abs(pct)}%
    </span>
  );
}

// ── Platform tag ───────────────────────────────────────────────────────────

function PlatformTag({ platform }: { platform: Platform }) {
  const c = useThemeColors();
  const color = platform === "youtube" ? c.yt : c.fb;
  return (
    <span
      className="shrink-0 rounded border px-1 text-[10px] font-semibold leading-[1.35]"
      style={{
        color,
        backgroundColor: `color-mix(in oklch, ${color} 16%, transparent)`,
        borderColor: `color-mix(in oklch, ${color} 45%, transparent)`,
      }}
    >
      {platform === "youtube" ? "YT" : "FB"}
    </span>
  );
}

// ── Empty note ─────────────────────────────────────────────────────────────

function EmptyNote({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

// ── Lazy load cho sections bên dưới ────────────────────────────────────────

function useLazyLoad(): [(el: HTMLDivElement | null) => void, boolean] {
  const [visible, setVisible] = useState(false);
  const setRef = useCallback((el: HTMLDivElement | null) => {
    if (!el || visible) return;
    const ob = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { setVisible(true); ob.disconnect(); }
    }, { rootMargin: "200px" });
    ob.observe(el);
  }, [visible]);
  return [setRef, visible];
}

// ══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ══════════════════════════════════════════════════════════════════════════════

export default function DashboardPage() {
  return (
    <FilterProvider>
      <DashboardInner />
    </FilterProvider>
  );
}

/** Hai nền tảng gốc của Tool (giữ nguyên UX cũ). Nhãn tự do khác lấy từ `platform-tags` của server. */
const BUILTIN_PLATFORMS = ["youtube", "facebook"];

function DashboardInner() {
  // MỘT nguồn sự thật cho bộ lọc: URL query <-> FilterContext. Không state lọc nào khác trong trang này
  // (chỉ có bản nháp ngày/quốc gia cho tới khi bấm "Lọc", và `topLimit` không phải bộ lọc).
  const { filter, key: filterKey, setFilter } = useFilterContext();
  const { user } = useAuth();
  const isDashRole = user?.role === "admin" || user?.role === "dash_admin";

  const [draftFrom, setDraftFrom] = useState(filter.date_from);
  const [draftTo, setDraftTo] = useState(filter.date_to);
  const [draftCountry, setDraftCountry] = useState(filter.country);
  useEffect(() => {
    setDraftFrom(filter.date_from); setDraftTo(filter.date_to); setDraftCountry(filter.country);
  }, [filter.date_from, filter.date_to, filter.country]);

  const [topLimit, setTopLimit] = useState(15);
  const [topTab, setTopTab] = useState<"rieng" | "chung">("rieng");
  const [reloadKey, setReloadKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const dataLock = useDataLock();
  useEffect(() => { bindLockToUser(user?.id); }, [user?.id]);

  // Nền tảng dùng CHUNG 1 ô chọn (trong Traffic chart) cho toàn trang: gốc -> `platform`, nhãn tự do -> `platform_tag`.
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
  const summaryQ = useAnalyticsQuery((signal) => analyticsApi.summary(filter, { signal }), [filterKey, reloadKey], { cacheKey: `summary|${filterKey}` });
  const breakdownQ = useAnalyticsQuery((signal) => analyticsApi.channelBreakdown(filter, { signal }), [filterKey, reloadKey], { cacheKey: `breakdown|${filterKey}` });
  const trafficQ = useAnalyticsQuery((signal) => analyticsApi.traffic(filter, { signal }), [filterKey, reloadKey], { cacheKey: `traffic|${filterKey}` });
  const byCountryQ = useAnalyticsQuery((signal) => analyticsApi.viewsByCountry(filter, { signal }), [filterKey, reloadKey], { cacheKey: `bycountry|${filterKey}` });
  const topQ = useAnalyticsQuery((signal) => analyticsApi.topChannels(filter, topLimit, { signal }),
    [filterKey, topLimit, reloadKey], { cacheKey: `top|${filterKey}|${topLimit}` });

  const dataQueries = [summaryQ, breakdownQ, trafficQ, byCountryQ, topQ];
  const unavailable = dataQueries.some((q) => q.unavailable);
  const hasAnyData = dataQueries.some((q) => q.data !== null);
  const hardError = dataQueries.find((q) => q.error && !q.unavailable)?.error ?? null;
  const freshness = mergeFreshness(dataQueries.map((q) => q.data?.freshness));

  useEffect(() => {
    // Một toast duy nhất (id cố định) dù nhiều widget cùng lỗi
    if (hardError) toast.error(`Lỗi tải thống kê: ${hardError.message}`, { id: "analytics-error" });
  }, [hardError?.message]); // eslint-disable-line react-hooks/exhaustive-deps

  const summary = summaryQ.data;
  const top = topQ.data?.items ?? null;

  const dirty = draftFrom !== filter.date_from || draftTo !== filter.date_to || draftCountry !== filter.country;
  const applyFilter = () => {
    if (!dirty) return;
    const [a, b] = draftFrom <= draftTo ? [draftFrom, draftTo] : [draftTo, draftFrom];
    setDraftFrom(a); setDraftTo(b);
    setFilter({ date_from: a, date_to: b, country: draftCountry });
  };

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
      toast.success("Đã tổng hợp lại từ cơ sở dữ liệu.");
    } catch (e) {
      toast.error(`Không làm mới được: ${errorMessage(e)}`);
    } finally {
      setRefreshing(false);
    }
  };

  const [topRef] = useLazyLoad();
  const showGroupFilter = ownerKnown && groupsList.length > 0;

  return (
    <div className="flex flex-col gap-3">
      {!isDashRole && summary?.linked === false && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-yellow-500/30 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-700">
          <AlertTriangle className="size-4 shrink-0" />
          <div className="flex-1">
            Chưa liên kết Tool — số liệu sẽ trống cho đến khi bạn liên kết tài khoản với Tool.
          </div>
          <Link to="/profile" className="whitespace-nowrap font-medium underline underline-offset-2">
            Liên kết ngay
          </Link>
        </div>
      )}

      {/* ═══ Filter bar — mọi giá trị đi vào FilterContext ═══ */}
      <div className="glass flex flex-wrap items-center gap-3 rounded-xl px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Khoảng ngày</span>
        <DateInput value={draftFrom} max={draftTo} onChange={setDraftFrom} onEnter={applyFilter} />
        <span className="text-muted-foreground">–</span>
        <DateInput value={draftTo} min={draftFrom} max={todayStr()} onChange={setDraftTo} onEnter={applyFilter} />

        <Select value={draftCountry || MOI_QUOC_GIA}
                onValueChange={(v) => setDraftCountry(v === MOI_QUOC_GIA ? "" : v)}>
          <SelectTrigger className="h-8 w-44 text-xs">
            <SelectValue placeholder="Mọi quốc gia" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={MOI_QUOC_GIA}>Mọi quốc gia</SelectItem>
            {tags.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}
          </SelectContent>
        </Select>

        {/* Nhân sự ĐỨNG TRƯỚC nhóm kênh — chọn WHO trước rồi mới lọc theo group/nhãn của người đó */}
        {isDashRole && (
          <Select value={filter.owner_account_id || MOI_NHAN_SU}
                  onValueChange={(v) => setFilter({ owner_account_id: v === MOI_NHAN_SU ? "" : v, group_id: "" })}>
            <SelectTrigger className="h-8 w-48 text-xs">
              <SelectValue placeholder="Chọn nhân sự" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={MOI_NHAN_SU}>Tất cả nhân sự</SelectItem>
              {viewerAccounts.map((a) => <SelectItem key={a.id} value={a.id}>{a.username}</SelectItem>)}
            </SelectContent>
          </Select>
        )}

        {showGroupFilter && (
          <Select value={filter.group_id || MOI_NHOM}
                  onValueChange={(v) => setFilter({ group_id: v === MOI_NHOM ? "" : v })}>
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue placeholder="Mọi nhóm kênh" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={MOI_NHOM}>Mọi nhóm kênh</SelectItem>
              {groupsList.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}

        <Button size="sm" variant={dirty ? "default" : "outline"} onClick={applyFilter} disabled={!dirty}>
          <Filter className="size-4" /> Lọc
        </Button>

        {isDashRole && (
          <Button size="sm" variant="outline" onClick={lamMoi} disabled={refreshing || dataLock.locked}
                  title={dataLock.locked ? "Đang khóa dữ liệu — mở khóa trước khi làm mới" : "Xoá cache và tổng hợp lại từ cơ sở dữ liệu"}>
            <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
            {refreshing ? "Đang làm mới…" : "Làm mới"}
          </Button>
        )}

        {/* Kích thước CỐ ĐỊNH ở cả hai trạng thái: bật/tắt khóa không làm hàng bộ lọc xuống dòng hay đẩy nội dung bên dưới */}
        <Button size="sm" variant={dataLock.locked ? "default" : "outline"} aria-pressed={dataLock.locked}
                className="w-[10.5rem] justify-center"
                aria-label={dataLock.locked ? `Mở khóa dữ liệu (còn ${dataLock.minutesLeft} phút)` : "Khóa dữ liệu"}
                onClick={() => (dataLock.locked ? dataLock.unlock() : dataLock.lock())}
                title={dataLock.locked
                  ? "Đang giữ nguyên số liệu để bạn xem; tự mở khóa sau tối đa 1 giờ. Dữ liệu mới vẫn được đồng bộ phía sau. Bấm để mở khóa và xem số liệu mới nhất. Bộ lọc chưa xem trước đó sẽ lấy số liệu hiện tại."
                  : "Giữ nguyên số liệu đang xem tối đa 1 giờ. Dữ liệu vẫn được đồng bộ phía sau."}>
          {dataLock.locked ? <Unlock className="size-4" /> : <Lock className="size-4" />}
          <span aria-hidden>{dataLock.locked ? `Đã khóa · ${dataLock.minutesLeft} phút` : "Khóa dữ liệu"}</span>
        </Button>
      </div>
      {/* Đọc cho trình đọc màn hình, không chiếm chỗ trên giao diện */}
      <p role="status" data-testid="data-lock-note" className="sr-only">
        {dataLock.locked ? "Đang khóa dữ liệu: số liệu trên trang được giữ nguyên, dữ liệu mới vẫn được đồng bộ phía sau." : ""}
      </p>

      {unavailable && (
        <AnalyticsUnavailableBanner hasData={hasAnyData} onRetry={() => setReloadKey((k) => k + 1)} />
      )}
      <FreshnessBanner freshness={freshness} />

      {hardError && !unavailable && (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div className="font-medium">Không tải được số liệu — {hardError.message}</div>
        </div>
      )}

      {/* ═══ Biểu đồ riêng (theo từng lát cắt đang chọn) / Biểu đồ chung (so sánh) ═══ */}
      <Tabs value={topTab} onValueChange={(v) => setTopTab(v as "rieng" | "chung")}>
        <TabsList className="w-auto">
          <TabsTrigger value="rieng">Biểu đồ riêng</TabsTrigger>
          <TabsTrigger value="chung">Biểu đồ chung</TabsTrigger>
        </TabsList>

        <TabsContent value="rieng" className="flex flex-col gap-3">
          <SummaryStrip data={summary} loading={summaryQ.loading} breakdown={breakdownQ.data}
                       lBreakdown={breakdownQ.loading} country={filter.country} />
          <TrafficMetricChart data={trafficQ.data} loading={trafficQ.loading} metrics={metricList}
                              scope={scope} onScope={onScope} platformTags={allPlatforms} />
          <CountryComparisonCard byCountry={byCountryQ.data} />
          {filter.country && (
            <PlatformBreakdownCard country={filter.country} reloadKey={reloadKey} />
          )}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div ref={topRef}>
              <TopChannelsCard items={top} loading={topQ.loading} country={filter.country} limit={topLimit} onLimit={setTopLimit} />
            </div>
            <CountryViewsCard data={byCountryQ.data} loading={byCountryQ.loading} country={filter.country} />
          </div>
        </TabsContent>

        <TabsContent value="chung">
          <ComparisonView isDashRole={isDashRole} reloadKey={reloadKey} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// A — Dải KPI (SummaryStrip)
// ══════════════════════════════════════════════════════════════════════════════

function KpiCard({ label, children, footer }: {
  label: string; children: React.ReactNode; footer: React.ReactNode;
}) {
  return (
    <div className="glass rounded-xl px-4 py-3.5">
      <div className="text-2xl font-semibold tabular-nums">{children}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-[11px] font-medium">{footer}</div>
    </div>
  );
}

function SummaryStrip({ data, loading, breakdown, lBreakdown, country }: {
  data: AnalyticsSummary | null; loading: boolean;
  breakdown: ChannelBreakdown | null; lBreakdown: boolean; country: string;
}) {
  if (loading && !data) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-xl" />)}
      </div>
    );
  }
  if (!data) return null;

  const perDay = data.days_in_range > 0 ? data.videos_posted / data.days_in_range : 0;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <KpiCard label="Tổng view (YT + FB)" footer={<>so với kỳ trước <DeltaLabel pct={deltaPct(data.total_views, data.total_views_prev)} /></>}>
          {fmtNum(data.total_views)}
        </KpiCard>
        <KpiCard label="Video đã đăng trong kỳ" footer={<span className="text-muted-foreground">≈ {perDay.toFixed(1)}/ngày · <DeltaLabel pct={deltaPct(data.videos_posted, data.videos_posted_prev)} /></span>}>
          {fmtNum(data.videos_posted)}
        </KpiCard>
        <ChannelKpiCard breakdown={breakdown} loading={lBreakdown} country={country} />
      </div>
    </div>
  );
}

// Ô "Kênh/Page đang bật" — thay vì 1 số tổng, hiển thị breakdown theo TỪNG nền tảng (facebook,
// youtube, + nhãn tự do như "tiktok") và theo chủ đề (ITL/Sible...) khi 1 trang chạy nhiều chủ đề
// cùng lúc (mục 25.4 đặc tả). Khi đang lọc 1 quốc gia cụ thể, tổng + từng dòng platform đều thu
// hẹp về đúng quốc gia đó (không hiện số toàn hệ thống trong lúc filter — mục 25.3).
function ChannelKpiCard({ breakdown, loading, country }: {
  breakdown: ChannelBreakdown | null; loading: boolean; country: string;
}) {
  if (loading && !breakdown) return <Skeleton className="h-[92px] rounded-xl" />;
  if (!breakdown) return null;

  const rows = breakdown.by_platform;
  let managed = breakdown.total.managed;
  let active = breakdown.total.active;
  if (country) {
    managed = 0; active = 0;
    for (const p of rows) {
      const c = p.by_country?.find((x) => x.country === country);
      managed += c?.managed ?? 0;
      active += c?.active ?? 0;
    }
  }

  const platformLabel = (p: string) => (p === "youtube" ? "YouTube" : p === "facebook" ? "Facebook" : p);

  return (
    <div className="glass rounded-xl px-4 py-3.5">
      <div className="text-2xl font-semibold tabular-nums">
        {fmtNum(active)} <span className="text-base text-muted-foreground">/ {fmtNum(managed)}</span>
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        Kênh / page đang bật{country ? ` · ${country}` : ""}
      </div>
      <div className="mt-2 space-y-1 border-t border-border/60 pt-2">
        {rows.map((p) => {
          const c = country ? p.by_country?.find((x) => x.country === country) : null;
          const m = country ? (c?.managed ?? 0) : p.managed;
          const a = country ? (c?.active ?? 0) : p.active;
          if (m === 0) return null;
          return (
            <div key={p.platform} className="text-[11px]">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">
                  {platformLabel(p.platform)}{p.is_custom_tag ? " (nhãn)" : ""}
                </span>
                <span className="font-medium tabular-nums">{fmtNum(a)} / {fmtNum(m)}</span>
              </div>
              {!country && p.by_group && p.by_group.length > 1 && (
                <details className="mt-0.5 ml-2">
                  <summary className="cursor-pointer text-muted-foreground/80">
                    {p.by_group.length} chủ đề
                  </summary>
                  {p.by_group.map((g) => (
                    <div key={g.group} className="flex items-center justify-between pl-2 text-muted-foreground">
                      <span>{g.group}</span>
                      <span className="tabular-nums">{fmtNum(g.active)} / {fmtNum(g.managed)}</span>
                    </div>
                  ))}
                </details>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// B — Traffic theo chỉ số (TrafficMetricChart)
// ══════════════════════════════════════════════════════════════════════════════

type MetricKind = "line" | "bar" | "pie" | "mixed";

const METRIC_ORDER: TrafficMetricKey[] = ["views", "interactions", "comments", "videos"];
const METRIC_COLOR: Record<TrafficMetricKey, string> = {
  views: "#2f80ff",
  interactions: "#f59e0b",
  comments: "#e5484d",
  videos: "#22c55e",
};

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
  const color = (k: TrafficMetricKey) => METRIC_COLOR[k];

  const toggle = (k: TrafficMetricKey) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });

  const shown = METRIC_ORDER.filter((k) => selected.has(k));

  // Trục Y riêng: views giữ trục trái, chỉ số khác dùng trục phải tự co-scale
  const showRightAxis =
    (kind === "mixed" && shown.includes("videos")) ||
    (splitAxes && kind !== "mixed" && shown.includes("views") && shown.length > 1);
  const yAxisIdOf = (k: TrafficMetricKey): string => {
    if (kind === "mixed") return k === "videos" ? "right" : "left";
    if (showRightAxis && splitAxes) return k === "views" ? "left" : "right";
    return "left";
  };

  const rows = data?.days ?? [];

  /** Dot renderer cho recharts Line — LUÔN vẽ chấm + nhãn số cho MỌI ngày có dữ liệu (kể cả 0,
   * phân biệt với `null` = "chưa có dữ liệu ngày đó" theo đúng ngữ nghĩa `stats_core`) — người
   * dùng cần thấy số của từng ngày, không chỉ số cao nhất. Dùng `shortNum` (dạng rút gọn, giống
   * nhãn cột) để đỡ tràn chữ khi khoảng ngày dài; so le trên/dưới theo chỉ số chẵn/lẻ để giảm
   * chồng chữ giữa các điểm liền kề. Màu nhãn = màu đường (tham số `clr`), khớp legend. */
  const dayDot = (k: TrafficMetricKey, clr: string) =>
    (props: { cx?: number; cy?: number; index?: number; payload?: TrafficMetricPoint }) => {
      const { cx, cy, index, payload } = props;
      if (cx == null || cy == null || index == null || !payload) return <g />;
      const v = payload[k];
      if (v == null) return <g />;
      const baseTy = cy < 18 ? cy + 14 : cy - 6;
      const ty = index % 2 === 0 ? baseTy : baseTy + (baseTy > cy ? 9 : -9);
      return (
        <g>
          <circle cx={cx} cy={cy} r={2.5} fill={clr} stroke="none" />
          <text x={cx} y={ty} textAnchor="middle" fontSize={8} fontWeight="600"
                fill={clr} style={{ fontVariantNumeric: "tabular-nums" }}>
            {shortNum(v)}
          </text>
        </g>
      );
    };
  const pie = useMemo(
    () => shown.map((k) => ({ key: k, name: label(k), value: data?.totals?.[k] ?? 0, fill: color(k) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, shown.join("|"), c],
  );

  const customTags = platformTags.filter((p) => p !== "youtube" && p !== "facebook");
  const scopeLabel = scope === "" ? "cả hai nền tảng" : scope === "youtube" ? "chỉ YouTube"
    : scope === "facebook" ? "chỉ Facebook" : `nhãn "${scope}"`;

  return (
    <div className="glass rounded-xl p-4">
      <div className="mb-3 flex flex-wrap items-start gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold">Traffic theo chỉ số</h3>
          <p className="text-xs text-muted-foreground">
            {data?.scope_label || "Tổng — cả hai nền tảng"}
            {kind === "pie" ? " · tổng cả khoảng ngày" : " · theo từng ngày"}
          </p>
        </div>
        <Tabs value={kind} onValueChange={(v) => setKind(v as MetricKind)} className="ml-auto shrink-0">
          <TabsList>
            <TabsTrigger value="bar">Cột</TabsTrigger>
            <TabsTrigger value="line">Đường</TabsTrigger>
            <TabsTrigger value="pie">Tròn</TabsTrigger>
            <TabsTrigger value="mixed">Hỗn hợp</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Scope selector + metric toggles */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <Select value={scope || SCOPE_ALL}
                onValueChange={(v) => onScope(v === SCOPE_ALL ? "" : v)}>
          <SelectTrigger className="mr-1 h-8 w-48 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={SCOPE_ALL}>Tổng — cả hai nền tảng</SelectItem>
            <SelectItem value="youtube">YouTube</SelectItem>
            <SelectItem value="facebook">Facebook</SelectItem>
            {customTags.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
          </SelectContent>
        </Select>
        {METRIC_ORDER.map((k) => {
          const on = selected.has(k);
          const meta = metrics.find((m) => m.key === k);
          const zero = meta && !meta.ready;
          return (
            <button key={k} type="button" onClick={() => toggle(k)}
                    title={zero ? "Chưa cào theo ngày — hiện ở mốc 0" : ""}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs transition-colors",
                      on ? "border-primary bg-primary/10 text-foreground font-medium" : "border-hairline text-muted-foreground hover:bg-elev-2")}>
              <span className="size-2.5 rounded-full" style={{
                backgroundColor: on ? color(k) : "transparent",
                border: on ? "none" : `1px solid ${color(k)}`,
              }} />
              {label(k)}{zero ? " (0)" : ""}
            </button>
          );
        })}
        {kind !== "pie" && kind !== "mixed" && (
          <button
            type="button"
            onClick={() => setSplitAxes((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs transition-colors",
              splitAxes
                ? "border-primary bg-primary/10 text-foreground font-medium"
                : "border-hairline text-muted-foreground hover:bg-elev-2",
            )}
            title="Chia trục Y: Lượt xem dùng trục trái, chỉ số còn lại co-scale trục phải riêng"
          >
            Trục Y riêng
          </button>
        )}
      </div>

      {/* Chart area */}
      <div className="h-[420px] w-full">
        {loading ? <EmptyNote text="Đang tải…" />
          : !shown.length ? <EmptyNote text="Chọn ít nhất một chỉ số để vẽ." />
          : !rows.length ? <EmptyNote text="Chưa có số liệu trong khoảng ngày này." />
          : kind === "pie" ? (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={pie} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="72%" paddingAngle={2}>
                  {pie.map((p) => <Cell key={p.key} fill={p.fill} />)}
                </Pie>
                <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows}>
                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} />
                <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={11} />
                <YAxis yAxisId="left" stroke={c.axis} fontSize={11} tickFormatter={(v) => fmtNum(v)} width={70} />
                {showRightAxis && (
                  <YAxis yAxisId="right" orientation="right" stroke={c.axis} fontSize={11}
                         tickFormatter={(v) => fmtNum(v)} allowDecimals={false} width={50} />
                )}
                <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
                <Legend />
                {shown.map((k) => {
                  const onRight = yAxisIdOf(k) === "right";
                  const asBar = kind === "bar" || (kind === "mixed" && k === "videos");
                  return asBar ? (
                    <Bar key={k} yAxisId={onRight ? "right" : "left"} dataKey={k} name={label(k)}
                         fill={color(k)} {...(onRight ? { barSize: 10, radius: [2, 2, 0, 0] as [number, number, number, number] } : {})}>
                      <LabelList dataKey={k} position="top"
                                 formatter={(v: unknown) => typeof v === "number" ? shortNum(v) : ""}
                                 style={{ fontSize: 9, fontWeight: 600, fill: color(k) }} />
                    </Bar>
                  ) : (
                    <Line key={k} yAxisId={yAxisIdOf(k)} type="monotone" dataKey={k} name={label(k)}
                          stroke={color(k)} strokeWidth={2}
                          dot={dayDot(k, color(k))}
                          activeDot={{ r: 4, strokeWidth: 0, fill: color(k) }} />
                  );
                })}
              </ComposedChart>
            </ResponsiveContainer>
          )}
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Lọc theo {scopeLabel}.{" "}
        {scope === "youtube"
          ? "Lượt tương tác & bình luận chưa có dữ liệu theo ngày cho YouTube."
          : "Lượt tương tác & bình luận cào từ cột Business Suite (hiện 0 nếu cột chưa có trên trang)."}
      </p>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// C — Top kênh / page theo view
// ══════════════════════════════════════════════════════════════════════════════

const TOP_LIMITS = [10, 15, 20, 30, 50];

function TopChannelsCard({ items, loading, country, limit, onLimit }: {
  items: TopChannel[] | null; loading: boolean; country: string; limit: number; onLimit: (n: number) => void;
}) {
  const c = useThemeColors();
  const max = items?.length ? Math.max(...items.map((i) => i.views), 1) : 1;

  return (
    <div className="glass rounded-xl p-4">
      <div className="mb-3 flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">Top kênh / page theo view</h3>
          <p className="text-xs text-muted-foreground">
            {country ? `Trong quốc gia ${country} · ` : "Mọi quốc gia · "}xếp từ cao xuống thấp
          </p>
        </div>
        <Select value={String(limit)} onValueChange={(v) => onLimit(Number(v))}>
          <SelectTrigger className="h-8 w-28 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            {TOP_LIMITS.map((n) => <SelectItem key={n} value={String(n)}>Top {n}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {loading && !items ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-6 rounded" />)}
        </div>
      ) : !items?.length ? (
        <EmptyNote text="Chưa có kênh nào có view trong khoảng ngày này." />
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((it) => {
            const color = it.platform === "youtube" ? c.yt : c.fb;
            return (
              <div key={it.id} className="flex items-center gap-2 text-[13px]">
                <span className="w-28 shrink-0 truncate" title={it.name}>{it.name}</span>
                <PlatformTag platform={it.platform} />
                {it.platform === "facebook" && fbLink(it.id) ? (
                  <a
                    href={fbLink(it.id)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-muted-foreground/50 hover:text-[oklch(0.55_0.11_218)] transition-colors"
                    title={`Mở Facebook: ${it.name}`}
                  >
                    <ExternalLink className="size-3" />
                  </a>
                ) : (
                  <span className="size-3 shrink-0" />
                )}
                <span className="w-6 shrink-0 text-[11px] text-muted-foreground">{it.country || "—"}</span>
                <div className="h-4 flex-1 overflow-hidden rounded bg-elev-2">
                  <div className="h-full rounded" style={{
                    width: `${Math.max(2, (it.views / max) * 100)}%`,
                    backgroundColor: `color-mix(in oklch, ${color} 65%, transparent)`,
                  }} />
                </div>
                <span className="min-w-[56px] shrink-0 text-right text-xs tabular-nums">{fmtNum(it.views)}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// D — View theo quốc gia (grouped bar chart by day)
// ══════════════════════════════════════════════════════════════════════════════

function CountryViewsCard({ data, loading, country }: {
  data: CountryViewsSeries | null; loading: boolean; country: string;
}) {
  const c = useThemeColors();
  const allCountries = data?.countries ?? [];
  const shownCountries = country ? allCountries.filter((k) => k === country) : allCountries;
  const rows = data?.days ?? [];
  const hasAny = rows.some((r) => shownCountries.some((k) => Number(r[k] ?? 0) > 0));

  return (
    <div className="glass rounded-xl p-4">
      <div className="mb-3">
        <h3 className="font-semibold">View theo quốc gia</h3>
        <p className="text-xs text-muted-foreground">
          {country ? `Chỉ ${country} · view theo ngày` : "Mỗi quốc gia một cột, đứng cạnh nhau theo ngày"}
        </p>
      </div>
      <div className="h-[300px] w-full">
        {loading && !data ? <EmptyNote text="Đang tải…" />
          : !shownCountries.length ? <EmptyNote text="Chưa gắn được quốc gia cho view nào — tạo & gắn thẻ quốc gia trước." />
          : !hasAny ? <EmptyNote text="Chưa có view cho quốc gia này trong khoảng ngày." />
          : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows}>
                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} />
                <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={11} />
                <YAxis stroke={c.axis} fontSize={11} tickFormatter={(v) => fmtNum(v)} width={60} />
                <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
                <Legend />
                {shownCountries.map((name, i) => (
                  <Bar key={name} dataKey={name} name={name} fill={c.ramp[i % c.ramp.length]}>
                    <LabelList dataKey={name} position="top"
                               formatter={(v: unknown) => typeof v === "number" ? shortNum(v) : ""}
                               style={{ fontSize: 9, fontWeight: 600, fill: c.ramp[i % c.ramp.length] }} />
                  </Bar>
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// E — Biểu đồ chung (so sánh nền tảng/nhân sự/kênh/nhóm kênh — KHÔNG gồm quốc gia)
// ══════════════════════════════════════════════════════════════════════════════
//
// Giống hệt tính linh hoạt của "Biểu đồ riêng": mỗi lát cắt so sánh đều xem được ở 3 dạng — Cột/
// Đường/Tròn. Bộ lọc ở filter bar áp dụng CHUNG cho cả tab này qua FilterContext: chọn 1 nền tảng/quốc gia/
// nhóm → mọi so sánh tự thu hẹp. So sánh QUỐC GIA không nằm ở đây (biểu đồ riêng, tab "Biểu đồ riêng").

interface CompareSeries { key: string; name: string; total: number; days?: { date: string; value: number | null }[] }

type CompareKind = "bar" | "line" | "pie";

function ComparisonChart({ title, subtitle, series, loading, emptyText, allowLine = true }: {
  title: string; subtitle?: string; series: CompareSeries[] | null; loading: boolean;
  emptyText: string; allowLine?: boolean;
}) {
  const c = useThemeColors();
  const [kind, setKind] = useState<CompareKind>("bar");
  const [expanded, setExpanded] = useState(false);
  const sorted = series ? [...series].sort((a, b) => b.total - a.total) : null;
  const max = sorted?.length ? Math.max(...sorted.map((s) => s.total), 1) : 1;

  // Gộp `days` của mọi series thành 1 mảng row theo ngày — {date, [tên series]: giá trị, ...} —
  // đúng shape recharts LineChart cần cho nhiều đường trên cùng 1 trục ngày.
  const dayRows = useMemo(() => {
    if (!sorted?.some((s) => s.days?.length)) return [];
    const byDate: Record<string, Record<string, number | string | null>> = {};
    for (const s of sorted) {
      for (const d of s.days || []) {
        byDate[d.date] = byDate[d.date] || { date: d.date };
        byDate[d.date][s.name] = d.value;
      }
    }
    return Object.values(byDate).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [sorted]);

  const pieData = useMemo(() => (sorted || []).map((s) => ({ name: s.name, value: s.total })), [sorted]);

  // Tách phần vẽ chart ra hàm riêng để dùng lại y hệt cho cả bản thu nhỏ trong card LẪN bản phóng
  // to trong Dialog — chỉ khác chiều cao (`heightCls`), tránh lệch số liệu giữa 2 bản.
  const renderBody = (heightCls: string) => (
    <div className={`${heightCls} w-full`}>
      {loading && !sorted ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-6 rounded" />)}
        </div>
      ) : !sorted?.length ? <EmptyNote text={emptyText} />
        : kind === "pie" ? (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" innerRadius="42%" outerRadius="80%"
                   paddingAngle={2} label={(e: { name?: string; value?: number }) => `${e.name}: ${fmtNum(e.value ?? 0)}`}>
                {pieData.map((p, i) => <Cell key={p.name} fill={c.ramp[i % c.ramp.length]} />)}
              </Pie>
              <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        ) : kind === "line" && allowLine ? (
          !dayRows.length ? <EmptyNote text="Chưa có số liệu theo ngày." /> : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={dayRows}>
                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} />
                <XAxis dataKey="date" tickFormatter={shortDay} stroke={c.axis} fontSize={11} />
                <YAxis stroke={c.axis} fontSize={11} tickFormatter={(v) => fmtNum(v)} width={60} />
                <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
                <Legend />
                {sorted.map((s, i) => (
                  <Line key={s.key} type="monotone" dataKey={s.name} name={s.name}
                        stroke={c.ramp[i % c.ramp.length]} strokeWidth={2} dot={false} connectNulls={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )
        ) : (
          <div className="flex h-full flex-col gap-2 overflow-y-auto pr-1">
            {sorted.map((s, i) => (
              <div key={s.key} className="flex items-center gap-2 text-[13px]">
                <span className="w-28 shrink-0 truncate" title={s.name}>{s.name}</span>
                <div className="h-4 flex-1 overflow-hidden rounded bg-elev-2">
                  <div className="h-full rounded" style={{
                    width: `${Math.max(2, (s.total / max) * 100)}%`,
                    backgroundColor: `color-mix(in oklch, ${c.ramp[i % c.ramp.length]} 65%, transparent)`,
                  }} />
                </div>
                <span className="min-w-[64px] shrink-0 text-right text-xs tabular-nums">{fmtNum(s.total)}</span>
              </div>
            ))}
          </div>
        )}
    </div>
  );

  const kindTabs = (
    <Tabs value={kind} onValueChange={(v) => setKind(v as CompareKind)}>
      <TabsList>
        <TabsTrigger value="bar">Cột</TabsTrigger>
        {allowLine && <TabsTrigger value="line">Đường</TabsTrigger>}
        <TabsTrigger value="pie">Tròn</TabsTrigger>
      </TabsList>
    </Tabs>
  );

  return (
    <div className="glass rounded-xl p-4">
      <div className="mb-2 flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">{title}</h3>
          {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {kindTabs}
          <Button size="icon" variant="outline" className="size-8" onClick={() => setExpanded(true)}
                  title="Phóng to biểu đồ này">
            <Maximize2 className="size-4" />
          </Button>
        </div>
      </div>
      {renderBody("h-[260px]")}

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="max-w-5xl">
          <DialogTitle>{title}</DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            {kindTabs}
          </div>
          <div className="mt-3">
            {renderBody("h-[65vh]")}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// Quốc gia — tách riêng khỏi `ComparisonView`, hiển thị TRỰC TIẾP trong tab "Biểu đồ riêng" (yêu
// cầu người dùng: không còn nằm trong "Biểu đồ chung"). Tự dùng `byCountry` đã tải sẵn cho tab
// riêng — không gọi thêm API, và tự thu hẹp theo filter country/platform/group đang áp dụng vì
// `byCountry` đã được tính theo đúng filter đó.
function CountryComparisonCard({ byCountry }: { byCountry: CountryViewsSeries | null }) {
  const countryCompare: CompareSeries[] | null = byCountry
    ? byCountry.countries.map((name) => ({
        key: name, name, total: byCountry.totals[name] || 0,
        days: byCountry.days.map((d) => ({
          date: d.date, value: typeof d[name] === "number" ? (d[name] as number) : null,
        })),
      }))
    : null;
  return (
    <ComparisonChart title="So sánh quốc gia"
      subtitle="Chọn Cột/Đường/Tròn — tự thu hẹp theo nền tảng/nhóm/quốc gia đang lọc ở trên"
      series={countryCompare} loading={!byCountry} emptyText="Chưa có dữ liệu quốc gia." />
  );
}

const toSeries = (items: CompareItem[]): CompareSeries[] =>
  items.map((it) => ({ key: it.key, name: it.name, total: it.total, days: it.series }));

/**
 * Một thẻ so sánh = MỘT request `GET /v1/analytics/compare/{dimension}` với CHÍNH bộ lọc của FilterContext
 * (không còn fan-out nhiều request traffic với tham số lệch nhau như bản cũ).
 */
function CompareCard({ dimension, title, subtitle, emptyText, reloadKey }: {
  dimension: CommonCompareDimension; title: string; subtitle: string; emptyText: string; reloadKey: number;
}) {
  const { filter, key } = useFilterContext();
  const q = useAnalyticsQuery(
    (signal) => analyticsApi.compare(dimension as CompareDimension, filter, { signal }),
    [dimension, key, reloadKey],
    { cacheKey: `compare|${dimension}|${key}` },
  );
  const series = q.data ? toSeries(q.data.items) : null;
  const hasDays = !!series?.some((s) => s.days?.length);
  const unavailableText = q.unavailable && !q.data ? "Dịch vụ thống kê không khả dụng." : null;
  return (
    <ComparisonChart title={title} subtitle={subtitle} series={series} loading={q.loading}
      emptyText={unavailableText ?? (q.error && !q.data ? `Không tải được: ${q.error.message}` : emptyText)}
      allowLine={hasDays} />
  );
}

const COMPARE_META: Record<CommonCompareDimension, { title: string; subtitle: string; emptyText: string; dashOnly?: boolean }> = {
  platform: {
    title: "So sánh nền tảng", subtitle: "Theo nhãn nền tảng đã gắn ở trang Nhóm kênh",
    emptyText: "Chưa gắn nhãn nền tảng nào cho kênh — vào Nhóm kênh để gắn.",
  },
  owner: {
    title: "So sánh nhân sự", subtitle: "Tổng + xu hướng view của từng nhân sự",
    emptyText: "Chưa có nhân sự (account viewer) nào.", dashOnly: true,
  },
  channel: {
    title: "So sánh kênh", subtitle: "Cùng bộ lọc với Top kênh ở tab Biểu đồ riêng",
    emptyText: "Chưa có kênh nào có view.",
  },
  group: {
    title: "So sánh nhóm kênh", subtitle: "Tổng + xu hướng view của từng nhóm đã tạo",
    emptyText: "Chưa tạo nhóm kênh nào.",
  },
};

// Chỉ render các chiều trong COMMON_COMPARE_DIMENSIONS — so sánh QUỐC GIA KHÔNG nằm ở đây (có biểu đồ riêng).
function ComparisonView({ isDashRole, reloadKey }: { isDashRole: boolean; reloadKey: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {COMMON_COMPARE_DIMENSIONS.map((dim) => {
        const m = COMPARE_META[dim];
        if (m.dashOnly && !isDashRole) return null;
        return <CompareCard key={dim} dimension={dim} reloadKey={reloadKey} {...m} />;
      })}
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// F — Phân bổ nền tảng cho 1 quốc gia (drill-down của "Biểu đồ riêng theo quốc gia")
// ══════════════════════════════════════════════════════════════════════════════

function PlatformBreakdownCard({ country, reloadKey }: { country: string; reloadKey: number }) {
  const c = useThemeColors();
  const { filter, key } = useFilterContext();
  const q = useAnalyticsQuery((signal) => analyticsApi.compare("platform", filter, { signal }), [key, reloadKey], { cacheKey: `compare|platform|${key}` });
  const items = (q.data?.items ?? [])
    .map((it) => ({ key: it.key, name: it.name, value: it.total || 0 }))
    .filter((r) => r.value > 0);

  return (
    <div className="glass rounded-xl p-4">
      <h3 className="font-semibold">Phân bổ nền tảng — {country}</h3>
      <p className="text-xs text-muted-foreground">Theo nhãn nền tảng đã gắn ở Nhóm kênh cho các kênh thuộc quốc gia này.</p>
      <div className="h-[220px] w-full">
        {q.loading && !q.data ? <EmptyNote text="Đang tải…" />
          : !items.length ? <EmptyNote text="Kênh trong quốc gia này chưa gắn nhãn nền tảng nào." />
          : (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={items} dataKey="value" nameKey="name" innerRadius="40%" outerRadius="75%"
                     paddingAngle={2} label={(e: { name?: string; value?: number }) => `${e.name}: ${fmtNum(e.value ?? 0)}`}>
                  {items.map((it, i) => <Cell key={it.key} fill={c.ramp[i % c.ramp.length]} />)}
                </Pie>
                <Tooltip formatter={tipViews} wrapperStyle={{ zIndex: 200 }} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
      </div>
    </div>
  );
}

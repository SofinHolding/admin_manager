/**
 * Dashboard thống kê — giống hệt AnalyticsView bên Electron.
 *
 * Đồng bộ 1:1 giao diện và chức năng, chỉ KHÔNG có:
 * - Thiếu video hôm nay / Đăng ổn định 7 ngày / Video/ngày trung bình (HealthPanel)
 * - Nút quản lý thẻ quốc gia (CountryTagManager) — thẻ lấy trực tiếp từ data
 *
 * Nguồn data: viewerApi (admin-server) thay vì analytics (local API).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Filter, RefreshCw } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, LabelList, Line, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  viewerApi,
  adminApi,
  type AnalyticsMetric,
  type AnalyticsSummary,
  type CountryTag,
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
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { useAuth } from "../auth";

// ── Helpers (copy từ AnalyticsView — cùng logic, cùng output) ──────────────

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

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
  return `https://www.facebook.com/profile.php?id=${fid}`;
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
  // Bộ lọc ngày + quốc gia
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(todayStr());
  const [country, setCountry] = useState("");
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);
  const [draftCountry, setDraftCountry] = useState("");

  const [scope, setScope] = useState<"" | "youtube" | "facebook">("");
  const [topLimit, setTopLimit] = useState(15);

  const { user } = useAuth();
  const [reloadKey, setReloadKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  // Data
  const [tags, setTags] = useState<CountryTag[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [traffic, setTraffic] = useState<TrafficMetricSeries | null>(null);
  const [byCountry, setByCountry] = useState<CountryViewsSeries | null>(null);
  const [top, setTop] = useState<TopChannel[] | null>(null);
  const [metricList, setMetricList] = useState<AnalyticsMetric[]>([]);

  // Loading
  const [lSummary, setLSummary] = useState(true);
  const [lTraffic, setLTraffic] = useState(true);
  const [lByCountry, setLByCountry] = useState(true);
  const [lTop, setLTop] = useState(true);
  const [error, setError] = useState("");

  const [topRef, topVisible] = useLazyLoad();

  const bao_loi = useCallback((e: unknown) => {
    setError((e as Error).message);
    toast.error(`Lỗi tải thống kê: ${(e as Error).message}`);
  }, []);

  // Tải tags quốc gia từ data (thay cho CountryTagManager)
  useEffect(() => {
    viewerApi.countryTags().then((d) => setTags(d.items)).catch(() => setTags([]));
  }, [reloadKey]);
  useEffect(() => {
    viewerApi.metrics().then((d) => setMetricList(d.metrics)).catch(() => setMetricList([]));
  }, [reloadKey]);

  // Summary
  useEffect(() => {
    setLSummary(true);
    viewerApi.summary(from, to, country)
      .then((d) => { setSummary(d); setError(""); })
      .catch((e) => { setSummary(null); bao_loi(e); })
      .finally(() => setLSummary(false));
  }, [from, to, country, bao_loi, reloadKey]);

  // Traffic
  useEffect(() => {
    setLTraffic(true);
    viewerApi.traffic(from, to, scope, country)
      .then((d) => { setTraffic(d); setError(""); })
      .catch((e) => { setTraffic(null); bao_loi(e); })
      .finally(() => setLTraffic(false));
  }, [from, to, scope, country, bao_loi, reloadKey]);

  // Views by country
  useEffect(() => {
    setLByCountry(true);
    viewerApi.viewsByCountry(from, to)
      .then((d) => { setByCountry(d); setError(""); })
      .catch((e) => { setByCountry(null); bao_loi(e); })
      .finally(() => setLByCountry(false));
  }, [from, to, bao_loi, reloadKey]);

  // Top channels (lazy)
  useEffect(() => {
    if (!topVisible) return;
    setLTop(true);
    viewerApi.topChannels(from, to, country, topLimit)
      .then((d) => { setTop(d.items); setError(""); })
      .catch((e) => { setTop(null); bao_loi(e); })
      .finally(() => setLTop(false));
  }, [topVisible, from, to, country, topLimit, bao_loi, reloadKey]);

  const dirty = draftFrom !== from || draftTo !== to || draftCountry !== country;
  const applyFilter = () => {
    if (!dirty) return;
    const [a, b] = draftFrom <= draftTo ? [draftFrom, draftTo] : [draftTo, draftFrom];
    setDraftFrom(a); setDraftTo(b); setFrom(a); setTo(b); setCountry(draftCountry);
  };

  const lamMoi = async () => {
    setRefreshing(true);
    try {
      await adminApi.flushCache();
      setReloadKey((k) => k + 1);
      toast.success("Đã tổng hợp lại từ cơ sở dữ liệu.");
    } catch (e) {
      toast.error(`Không làm mới được: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {/* ═══ Filter bar — giống Electron ═══ */}
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

        <Button size="sm" variant={dirty ? "default" : "outline"} onClick={applyFilter} disabled={!dirty}>
          <Filter className="size-4" /> Lọc
        </Button>

        {user?.role === "admin" && (
          <Button size="sm" variant="outline" onClick={lamMoi} disabled={refreshing}
                  title="Xoá cache và tổng hợp lại từ cơ sở dữ liệu">
            <RefreshCw className={cn("size-4", refreshing && "animate-spin")} />
            {refreshing ? "Đang làm mới…" : "Làm mới"}
          </Button>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <div className="font-medium">Không tải được số liệu — {error}</div>
            <div className="text-xs opacity-80">
              Nếu báo 404 hoặc "Not Found": backend đang chạy chưa có endpoint mới. Restart backend.
            </div>
          </div>
        </div>
      )}

      {/* ═══ A — Dải KPI ═══ */}
      <SummaryStrip data={summary} loading={lSummary} />

      {/* ═══ B — Traffic theo chỉ số ═══ */}
      <TrafficMetricChart data={traffic} loading={lTraffic} metrics={metricList} scope={scope} onScope={setScope} />

      {/* ═══ C + D — Top kênh + View theo quốc gia ═══ */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div ref={topRef}>
          <TopChannelsCard items={top} loading={lTop} country={country} limit={topLimit} onLimit={setTopLimit} />
        </div>
        <CountryViewsCard data={byCountry} loading={lByCountry} country={country} />
      </div>
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

function SummaryStrip({ data, loading }: { data: AnalyticsSummary | null; loading: boolean }) {
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
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <KpiCard label="Tổng view (YT + FB)" footer={<>so với kỳ trước <DeltaLabel pct={deltaPct(data.total_views, data.total_views_prev)} /></>}>
        {fmtNum(data.total_views)}
      </KpiCard>
      <KpiCard label="Video đã đăng trong kỳ" footer={<span className="text-muted-foreground">≈ {perDay.toFixed(1)}/ngày · <DeltaLabel pct={deltaPct(data.videos_posted, data.videos_posted_prev)} /></span>}>
        {fmtNum(data.videos_posted)}
      </KpiCard>
      <KpiCard label="Kênh / page đang bật"
               footer={<span className="text-muted-foreground">trên tổng {fmtNum(data.managed_channels)} nơi đăng quản lý</span>}>
        {fmtNum(data.active_channels)} <span className="text-base text-muted-foreground">/ {fmtNum(data.managed_channels)}</span>
      </KpiCard>
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

function TrafficMetricChart({ data, loading, metrics, scope, onScope }: {
  data: TrafficMetricSeries | null;
  loading: boolean;
  metrics: AnalyticsMetric[];
  scope: "" | "youtube" | "facebook";
  onScope: (s: "" | "youtube" | "facebook") => void;
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

  /** Dot renderer cho recharts Line — chỉ vẽ chấm + nhãn giá trị tại đỉnh cục bộ.
   * Đỉnh cục bộ: điểm có giá trị LỚN HƠN cả hai hàng xóm (biên = không có hàng xóm → tính là đỉnh).
   * Màu nhãn = màu đường (tham số `clr`), khớp legend. */
  const peakDot = (k: TrafficMetricKey, clr: string) =>
    (props: { cx?: number; cy?: number; index?: number; payload?: TrafficMetricPoint }) => {
      const { cx, cy, index, payload } = props;
      if (cx == null || cy == null || index == null || !payload) return <g />;
      const v = payload[k];
      if (v == null || v === 0) return <g />;
      const pv = rows[index - 1]?.[k];
      const nv = rows[index + 1]?.[k];
      if ((pv != null && v <= pv) || (nv != null && v <= nv)) return <g />;
      // Nhãn: mặc định trên chấm, cạnh mép trên → dịch xuống dưới
      const ty = cy < 18 ? cy + 14 : cy - 6;
      return (
        <g>
          <circle cx={cx} cy={cy} r={3} fill={clr} stroke="none" />
          <text x={cx} y={ty} textAnchor="middle" fontSize={9} fontWeight="600"
                fill={clr} style={{ fontVariantNumeric: "tabular-nums" }}>
            {v.toLocaleString("vi-VN")}
          </text>
        </g>
      );
    };
  const pie = useMemo(
    () => shown.map((k) => ({ key: k, name: label(k), value: data?.totals?.[k] ?? 0, fill: color(k) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, shown.join("|"), c],
  );

  const scopeLabel = { "": "cả hai nền tảng", youtube: "chỉ YouTube", facebook: "chỉ Facebook" }[scope];

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
                onValueChange={(v) => onScope(v === SCOPE_ALL ? "" : v as "youtube" | "facebook")}>
          <SelectTrigger className="mr-1 h-8 w-48 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={SCOPE_ALL}>Tổng — cả hai nền tảng</SelectItem>
            <SelectItem value="youtube">YouTube</SelectItem>
            <SelectItem value="facebook">Facebook</SelectItem>
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
                          dot={peakDot(k, color(k))}
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

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Filter, Maximize2, RefreshCw } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, LabelList, Line, LineChart, Pie,
  PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  viewerApi,
  adminApi,
  groupsApi,
  type Account,
  type AnalyticsMetric,
  type AnalyticsSummary,
  type ChannelBreakdown,
  type CountryTag,
  type CountryViewsSeries,
  type ScopeOpts,
  type TopChannel,
  type TrafficMetricKey,
  type TrafficMetricPoint,
  type TrafficMetricSeries,
  type ViewerGroup,
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
  // Bộ lọc ngày + quốc gia
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(todayStr());
  const [country, setCountry] = useState("");
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);
  const [draftCountry, setDraftCountry] = useState("");

  const [scope, setScope] = useState("");
  const [topLimit, setTopLimit] = useState(15);
  const [topTab, setTopTab] = useState<"rieng" | "chung">("rieng");

  const { user } = useAuth();
  const isDashRole = user?.role === "admin" || user?.role === "dash_admin";
  const [reloadKey, setReloadKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  // Lọc theo nhân sự (chỉ admin/dash_admin) → nhóm kênh (phụ thuộc nhân sự đang chọn). Nền tảng
  // dùng CHUNG 1 biến `scope` với ô chọn trong Traffic chart — KHÔNG còn 2 nút nền tảng tách rời.
  const [selectedOwner, setSelectedOwner] = useState("");
  const [selectedGroup, setSelectedGroup] = useState("");
  const [viewerAccounts, setViewerAccounts] = useState<Account[]>([]);
  const [groupsList, setGroupsList] = useState<ViewerGroup[]>([]);
  const [platformTags, setPlatformTags] = useState<string[]>([]);
  // Danh sách nền tảng hợp nhất cho dropdown/so sánh: 2 nền tảng GỐC (suy thẳng từ dữ liệu Tool,
  // luôn có) + nhãn tự do đã gắn ở Nhóm kênh (lọc trùng nếu ai đó lỡ gắn nhãn "youtube"/"facebook").
  const allPlatforms = useMemo(
    () => ["youtube", "facebook", ...platformTags.filter((p) => p !== "youtube" && p !== "facebook")],
    [platformTags],
  );
  const apiOpts: ScopeOpts = useMemo(
    () => ({ owner_account_id: selectedOwner, group_id: selectedGroup, platform_tag: scope }),
    [selectedOwner, selectedGroup, scope],
  );

  // Data
  const [tags, setTags] = useState<CountryTag[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [traffic, setTraffic] = useState<TrafficMetricSeries | null>(null);
  const [byCountry, setByCountry] = useState<CountryViewsSeries | null>(null);
  const [top, setTop] = useState<TopChannel[] | null>(null);
  const [channelBreakdown, setChannelBreakdown] = useState<ChannelBreakdown | null>(null);
  const [metricList, setMetricList] = useState<AnalyticsMetric[]>([]);

  // Loading
  const [lSummary, setLSummary] = useState(true);
  const [lTraffic, setLTraffic] = useState(true);
  const [lByCountry, setLByCountry] = useState(true);
  const [lTop, setLTop] = useState(true);
  const [lChannelBreakdown, setLChannelBreakdown] = useState(true);
  const [error, setError] = useState("");

  const [topRef, topVisible] = useLazyLoad();

  const bao_loi = useCallback((e: unknown) => {
    setError((e as Error).message);
    toast.error(`Lỗi tải thống kê: ${(e as Error).message}`);
  }, []);

  // Danh sách nhân sự (role=viewer) — chỉ admin/dash_admin cần
  useEffect(() => {
    if (!isDashRole) return;
    adminApi.listUsers()
      .then((d) => setViewerAccounts(d.users.filter((u) => u.role === "viewer")))
      .catch(() => setViewerAccounts([]));
  }, [isDashRole]);

  // Danh sách nhóm kênh + nhãn nền tảng theo nhân sự đang chọn (hoặc của chính mình nếu viewer).
  // admin/dash_admin CHƯA chọn nhân sự cụ thể → không có "chủ sở hữu" rõ ràng để gộp nhóm có ý
  // nghĩa, để rỗng thay vì gộp lẫn của mọi người (nhãn nền tảng GỐC youtube/facebook vẫn dùng được
  // bình thường vì không phụ thuộc nhân sự nào — chỉ nhãn TỰ DO mới cần biết chủ sở hữu).
  useEffect(() => {
    if (isDashRole && !selectedOwner) {
      setGroupsList([]); setPlatformTags([]); setSelectedGroup("");
      return;
    }
    groupsApi.list(selectedOwner).then((d) => setGroupsList(d.items)).catch(() => setGroupsList([]));
    viewerApi.platformTags(selectedOwner).then((d) => setPlatformTags(d.items)).catch(() => setPlatformTags([]));
    setSelectedGroup("");
  }, [isDashRole, selectedOwner]);

  // Tải tags quốc gia từ data (thay cho CountryTagManager)
  useEffect(() => {
    viewerApi.countryTags(apiOpts).then((d) => setTags(d.items)).catch(() => setTags([]));
  }, [apiOpts, reloadKey]);
  useEffect(() => {
    viewerApi.metrics().then((d) => setMetricList(d.metrics)).catch(() => setMetricList([]));
  }, [reloadKey]);

  // Summary
  useEffect(() => {
    setLSummary(true);
    viewerApi.summary(from, to, country, apiOpts)
      .then((d) => { setSummary(d); setError(""); })
      .catch((e) => { setSummary(null); bao_loi(e); })
      .finally(() => setLSummary(false));
  }, [from, to, country, apiOpts, bao_loi, reloadKey]);

  // Kênh/page breakdown — KHÔNG phụ thuộc khoảng ngày (trạng thái hiện tại, giống
  // managed_channels/active_channels cũ), chỉ phụ thuộc owner/platform/group đang lọc.
  useEffect(() => {
    setLChannelBreakdown(true);
    viewerApi.channelBreakdown(apiOpts)
      .then((d) => { setChannelBreakdown(d); setError(""); })
      .catch((e) => { setChannelBreakdown(null); bao_loi(e); })
      .finally(() => setLChannelBreakdown(false));
  }, [apiOpts, bao_loi, reloadKey]);

  // Traffic — `scope` (nền tảng) nay đi qua `apiOpts.platform_tag`, không còn tham số riêng.
  useEffect(() => {
    setLTraffic(true);
    viewerApi.traffic(from, to, country, apiOpts)
      .then((d) => { setTraffic(d); setError(""); })
      .catch((e) => { setTraffic(null); bao_loi(e); })
      .finally(() => setLTraffic(false));
  }, [from, to, country, apiOpts, bao_loi, reloadKey]);

  // Views by country
  useEffect(() => {
    setLByCountry(true);
    viewerApi.viewsByCountry(from, to, apiOpts)
      .then((d) => { setByCountry(d); setError(""); })
      .catch((e) => { setByCountry(null); bao_loi(e); })
      .finally(() => setLByCountry(false));
  }, [from, to, apiOpts, bao_loi, reloadKey]);

  // Top channels — KHÔNG lazy nữa: cần sẵn cho tab "Biểu đồ chung" (so sánh kênh) kể cả khi
  // TopChannelsCard chưa lọt vào viewport.
  useEffect(() => {
    setLTop(true);
    viewerApi.topChannels(from, to, country, topLimit, apiOpts)
      .then((d) => { setTop(d.items); setError(""); })
      .catch((e) => { setTop(null); bao_loi(e); })
      .finally(() => setLTop(false));
  }, [from, to, country, topLimit, apiOpts, bao_loi, reloadKey]);

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

  // Nhóm kênh CHỈ hiện khi đã có "chủ sở hữu" rõ ràng (viewer = chính mình; admin/dash_admin =
  // đã chọn 1 nhân sự cụ thể) VÀ người đó thật sự có nhóm — không dữ liệu thì không cần chiếm chỗ.
  const showGroupFilter = (isDashRole ? !!selectedOwner : true) && groupsList.length > 0;

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

        {/* Nhân sự ĐỨNG TRƯỚC nhóm kênh — chọn WHO trước rồi mới lọc theo group/nhãn của người đó */}
        {isDashRole && (
          <Select value={selectedOwner || MOI_NHAN_SU}
                  onValueChange={(v) => setSelectedOwner(v === MOI_NHAN_SU ? "" : v)}>
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
          <Select value={selectedGroup || MOI_NHOM}
                  onValueChange={(v) => setSelectedGroup(v === MOI_NHOM ? "" : v)}>
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue placeholder="Mọi nhóm kênh" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={MOI_NHOM}>Mọi nhóm kênh</SelectItem>
              {groupsList.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}

        {/* Nền tảng KHÔNG còn Select riêng ở đây — hợp nhất vào 1 ô chọn duy nhất bên trong
            Traffic chart (mục B) áp dụng cho toàn trang, tránh 2 nút nền tảng chồng chéo. */}

        <Button size="sm" variant={dirty ? "default" : "outline"} onClick={applyFilter} disabled={!dirty}>
          <Filter className="size-4" /> Lọc
        </Button>

        {isDashRole && (
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

      {/* ═══ Biểu đồ riêng (theo từng lát cắt đang chọn) / Biểu đồ chung (so sánh) ═══ */}
      <Tabs value={topTab} onValueChange={(v) => setTopTab(v as "rieng" | "chung")}>
        <TabsList className="w-auto">
          <TabsTrigger value="rieng">Biểu đồ riêng</TabsTrigger>
          <TabsTrigger value="chung">Biểu đồ chung</TabsTrigger>
        </TabsList>

        <TabsContent value="rieng" className="flex flex-col gap-3">
          <SummaryStrip data={summary} loading={lSummary} breakdown={channelBreakdown}
                       lBreakdown={lChannelBreakdown} country={country} />
          <TrafficMetricChart data={traffic} loading={lTraffic} metrics={metricList}
                              scope={scope} onScope={setScope} platformTags={allPlatforms} />
          <CountryComparisonCard byCountry={byCountry} />
          {country && (
            <PlatformBreakdownCard from={from} to={to} country={country} apiOpts={apiOpts}
                                   platformTags={allPlatforms} reloadKey={reloadKey} />
          )}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div ref={topRef}>
              <TopChannelsCard items={top} loading={lTop} country={country} limit={topLimit} onLimit={setTopLimit} />
            </div>
            <CountryViewsCard data={byCountry} loading={lByCountry} country={country} />
          </div>
        </TabsContent>

        <TabsContent value="chung">
          <ComparisonView from={from} to={to} country={country} apiOpts={apiOpts}
                          isDashRole={isDashRole} viewerAccounts={viewerAccounts}
                          groupsList={groupsList} platformTags={allPlatforms}
                          topChannels={top} reloadKey={reloadKey} />
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
  // Ngưỡng 2h: dưới đó coi là đồng bộ bình thường (Tool push định kỳ), trên đó cảnh báo số kênh
  // có thể lệch Tool thật (mục 26 đặc tả — vấn đề VPS/Tool không đồng nhất).
  const staleHours = data.last_synced_at
    ? (Date.now() - new Date(data.last_synced_at).getTime()) / 3_600_000 : null;

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
      {staleHours !== null && staleHours > 2 && (
        <p className="flex items-center gap-1 text-[11px] text-amber-600">
          <AlertTriangle className="size-3" />
          Dữ liệu đồng bộ lần cuối{" "}
          {staleHours < 48 ? `${Math.round(staleHours)} giờ` : `${Math.round(staleHours / 24)} ngày`} trước
          — số kênh/view có thể chưa khớp Tool mới nhất.
        </p>
      )}
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
// E — Biểu đồ chung (so sánh quốc gia/nền tảng/nhân sự/kênh/nhóm kênh)
// ══════════════════════════════════════════════════════════════════════════════
//
// Giống hệt tính linh hoạt của "Biểu đồ riêng": mỗi lát cắt so sánh đều xem được ở 3 dạng — Cột/
// Đường/Tròn — KHÔNG chỉ 1 dạng cố định. Bộ lọc quốc gia/nền tảng/nhân sự/nhóm ở filter bar phía
// trên áp dụng CHUNG cho cả tab này (đã truyền qua `apiOpts`/`country`): chọn 1 nền tảng cụ thể →
// mọi so sánh (kể cả so sánh quốc gia) tự thu hẹp về đúng nền tảng đó; chọn 1 quốc gia cụ thể →
// mọi so sánh tự thu hẹp về đúng quốc gia đó — không cần thao tác gì thêm ở đây.

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

function ComparisonView({
  from, to, country, apiOpts, isDashRole, viewerAccounts, groupsList, platformTags,
  topChannels, reloadKey,
}: {
  from: string; to: string; country: string; apiOpts: ScopeOpts;
  isDashRole: boolean; viewerAccounts: Account[]; groupsList: ViewerGroup[]; platformTags: string[];
  topChannels: TopChannel[] | null; reloadKey: number;
}) {
  const [platformCompare, setPlatformCompare] = useState<CompareSeries[] | null>(null);
  const [lPlatform, setLPlatform] = useState(true);
  const [staffCompare, setStaffCompare] = useState<CompareSeries[] | null>(null);
  const [lStaff, setLStaff] = useState(true);
  const [groupCompare, setGroupCompare] = useState<CompareSeries[] | null>(null);
  const [lGroup, setLGroup] = useState(true);

  // Dùng `traffic()` (không phải `summary()`) cho fan-out — trả CẢ tổng (`totals.views`) LẪN chuỗi
  // theo ngày (`days[].views`) trong 1 lượt gọi, đủ dữ liệu cho cả 3 dạng Cột/Đường/Tròn.

  // So sánh nền tảng — theo nhãn platform_tag đã gắn ở Nhóm kênh (KHÔNG phải phân loại tự động).
  useEffect(() => {
    if (!platformTags.length) { setPlatformCompare([]); setLPlatform(false); return; }
    let cancelled = false;
    setLPlatform(true);
    Promise.all(platformTags.map((tag) =>
      viewerApi.traffic(from, to, country, { ...apiOpts, platform_tag: tag })
        .then((d) => ({ key: tag, name: tag, total: d.totals?.views || 0,
                        days: d.days.map((p) => ({ date: p.date, value: p.views })) }))
        .catch(() => ({ key: tag, name: tag, total: 0, days: [] })),
    )).then((rows) => { if (!cancelled) setPlatformCompare(rows); })
      .finally(() => { if (!cancelled) setLPlatform(false); });
    return () => { cancelled = true; };
  }, [platformTags, from, to, country, apiOpts, reloadKey]);

  // So sánh nhân sự — chỉ admin/dash_admin, đè `owner_account_id` lên apiOpts hiện tại (giữ nguyên
  // bộ lọc nhóm/nền tảng/quốc gia nếu có, để so sánh đúng lát cắt đang xem).
  useEffect(() => {
    if (!isDashRole) { setStaffCompare(null); setLStaff(false); return; }
    if (!viewerAccounts.length) { setStaffCompare([]); setLStaff(false); return; }
    let cancelled = false;
    setLStaff(true);
    Promise.all(viewerAccounts.map((acc) =>
      viewerApi.traffic(from, to, country, { ...apiOpts, owner_account_id: acc.id })
        .then((d) => ({ key: acc.id, name: acc.username, total: d.totals?.views || 0,
                        days: d.days.map((p) => ({ date: p.date, value: p.views })) }))
        .catch(() => ({ key: acc.id, name: acc.username, total: 0, days: [] })),
    )).then((rows) => { if (!cancelled) setStaffCompare(rows); })
      .finally(() => { if (!cancelled) setLStaff(false); });
    return () => { cancelled = true; };
  }, [isDashRole, viewerAccounts, from, to, country, apiOpts, reloadKey]);

  // So sánh nhóm kênh — đè `group_id` lên apiOpts hiện tại.
  useEffect(() => {
    if (!groupsList.length) { setGroupCompare([]); setLGroup(false); return; }
    let cancelled = false;
    setLGroup(true);
    Promise.all(groupsList.map((g) =>
      viewerApi.traffic(from, to, country, { ...apiOpts, group_id: g.id })
        .then((d) => ({ key: g.id, name: g.name, total: d.totals?.views || 0,
                        days: d.days.map((p) => ({ date: p.date, value: p.views })) }))
        .catch(() => ({ key: g.id, name: g.name, total: 0, days: [] })),
    )).then((rows) => { if (!cancelled) setGroupCompare(rows); })
      .finally(() => { if (!cancelled) setLGroup(false); });
    return () => { cancelled = true; };
  }, [groupsList, from, to, country, apiOpts, reloadKey]);

  // So sánh kênh — tận dụng `top_channels` đã tải cho tab Riêng. Không có API trả view/kênh/ngày
  // nên CHỈ hỗ trợ Cột/Tròn (không có "Đường") cho lát cắt này.
  const channelCompare: CompareSeries[] | null = topChannels
    ? topChannels.map((ch) => ({ key: ch.id, name: ch.name, total: ch.views }))
    : null;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <ComparisonChart title="So sánh nền tảng" subtitle="Theo nhãn nền tảng đã gắn ở trang Nhóm kênh"
        series={platformCompare} loading={lPlatform}
        emptyText="Chưa gắn nhãn nền tảng nào cho kênh — vào Nhóm kênh để gắn." />
      {isDashRole && (
        <ComparisonChart title="So sánh nhân sự" subtitle="Tổng + xu hướng view của từng nhân sự"
          series={staffCompare} loading={lStaff} emptyText="Chưa có nhân sự (account viewer) nào." />
      )}
      <ComparisonChart title="So sánh kênh" subtitle="Cùng danh sách với Top kênh ở tab Biểu đồ riêng"
        series={channelCompare} loading={!topChannels} emptyText="Chưa có kênh nào có view." allowLine={false} />
      <ComparisonChart title="So sánh nhóm kênh" subtitle="Tổng + xu hướng view của từng nhóm đã tạo"
        series={groupCompare} loading={lGroup} emptyText="Chưa tạo nhóm kênh nào." />
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// F — Phân bổ nền tảng cho 1 quốc gia (drill-down của "Biểu đồ riêng theo quốc gia")
// ══════════════════════════════════════════════════════════════════════════════

function PlatformBreakdownCard({ from, to, country, apiOpts, platformTags, reloadKey }: {
  from: string; to: string; country: string; apiOpts: ScopeOpts; platformTags: string[]; reloadKey: number;
}) {
  const c = useThemeColors();
  const [items, setItems] = useState<{ key: string; name: string; value: number }[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!platformTags.length) { setItems(null); setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    Promise.all(platformTags.map((tag) =>
      viewerApi.summary(from, to, country, { ...apiOpts, platform_tag: tag })
        .then((d) => ({ key: tag, name: tag, value: d.total_views || 0 }))
        .catch(() => ({ key: tag, name: tag, value: 0 })),
    )).then((rows) => { if (!cancelled) setItems(rows.filter((r) => r.value > 0)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [country, platformTags, from, to, apiOpts, reloadKey]);

  if (!platformTags.length) return null;

  return (
    <div className="glass rounded-xl p-4">
      <h3 className="font-semibold">Phân bổ nền tảng — {country}</h3>
      <p className="text-xs text-muted-foreground">Theo nhãn nền tảng đã gắn ở Nhóm kênh cho các kênh thuộc quốc gia này.</p>
      <div className="h-[220px] w-full">
        {loading ? <EmptyNote text="Đang tải…" />
          : !items?.length ? <EmptyNote text="Kênh trong quốc gia này chưa gắn nhãn nền tảng nào." />
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

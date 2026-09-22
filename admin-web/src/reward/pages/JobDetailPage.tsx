/**
 * Theo dõi job — 8 chỉ số realtime qua SSE (useJobStream), điều khiển chạy/dừng, bảng item,
 * drawer chi tiết attempts, tab sự kiện, thao tác cho item `unknown`, xuất CSV.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { Download, Loader2, Pause, Play, Search, Square, X } from "lucide-react";
import {
  jobsApi, type JobAttempt, type JobDetail, type JobEvent, type JobItem,
} from "../api";
import { useJobStream } from "../hooks/useJobStream";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Badge } from "../../components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";

const ITEM_STATUS_BADGE: Record<string, "success" | "destructive" | "warning" | "secondary" | "default"> = {
  pending: "secondary",
  processing: "default",
  retrying: "warning",
  success: "success",
  failed: "destructive",
  unknown: "warning",
  skipped: "secondary",
};

const METRIC_KEYS = ["total", "pending", "processing", "retrying", "success", "failed", "unknown", "skipped"] as const;
const METRIC_LABEL: Record<(typeof METRIC_KEYS)[number], string> = {
  total: "Tổng",
  pending: "Chờ",
  processing: "Đang gửi",
  retrying: "Thử lại",
  success: "Thành công",
  failed: "Thất bại",
  unknown: "Không rõ",
  skipped: "Bỏ qua",
};

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [items, setItems] = useState<JobItem[]>([]);
  const [itemStatus, setItemStatus] = useState("");
  const [search, setSearch] = useState("");
  const [loadingItems, setLoadingItems] = useState(true);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [selectedItem, setSelectedItem] = useState<JobItem | null>(null);
  const [attempts, setAttempts] = useState<JobAttempt[]>([]);
  const [actionLoading, setActionLoading] = useState(false);

  const stream = useJobStream(id);

  const loadJob = useCallback(async () => {
    if (!id) return;
    try {
      const d = await jobsApi.get(id);
      setJob(d);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [id]);

  const loadItems = useCallback(async () => {
    if (!id) return;
    setLoadingItems(true);
    try {
      const d = await jobsApi.items(id, { status: itemStatus || undefined, q: search || undefined, limit: 200 });
      setItems(d.items);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoadingItems(false);
    }
  }, [id, itemStatus, search]);

  const loadEvents = useCallback(async () => {
    if (!id) return;
    try {
      const d = await jobsApi.events(id);
      setEvents(d.events);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [id]);

  useEffect(() => { loadJob(); }, [loadJob]);
  useEffect(() => { loadItems(); }, [loadItems]);
  useEffect(() => { loadEvents(); }, [loadEvents]);

  // SSE báo có item vừa đổi trạng thái → refetch bảng item + job (đủ đơn giản, tránh state hai nguồn).
  useEffect(() => {
    if (stream.itemTick === 0) return;
    loadItems();
    loadJob();
  }, [stream.itemTick, loadItems, loadJob]);

  const effectiveStatus = stream.status ?? job?.status ?? "";
  const effectiveCounts = stream.counts ?? job?.counts;

  const runControl = async (action: "run" | "pause" | "resume" | "stop") => {
    if (!id) return;
    setActionLoading(true);
    try {
      const fn = { run: jobsApi.run, pause: jobsApi.pause, resume: jobsApi.resume, stop: jobsApi.stop }[action];
      await fn(id);
      toast.success("Đã gửi lệnh điều khiển");
      await loadJob();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  };

  const openItem = async (item: JobItem) => {
    setSelectedItem(item);
    if (!id) return;
    try {
      const d = await jobsApi.attempts(id, item.id);
      setAttempts(d.attempts);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const resolveItem = async (item: JobItem, to: "success" | "pending") => {
    if (!id) return;
    if (to === "pending" && !confirm("Cho phép gửi lại item unknown? Hệ thống không tự gửi lại — chỉ mở lại hàng đợi.")) return;
    try {
      await jobsApi.resolveItem(id, item.id, to);
      toast.success(to === "success" ? "Đã đánh dấu đã cộng điểm" : "Đã cho phép gửi lại");
      setSelectedItem(null);
      await loadItems();
      await loadJob();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const exportCsv = async () => {
    if (!id || !job) return;
    try {
      const blob = await jobsApi.exportCsv(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.name || id}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const exportXlsx = async () => {
    if (!id || !job) return;
    try {
      const blob = await jobsApi.exportXlsx(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.name || id}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  if (!job) {
    return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải job…</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">{job.name}</h1>
          <p className="text-xs text-muted-foreground">
            Trạng thái: <Badge variant={effectiveStatus === "running" ? "success" : effectiveStatus === "paused" ? "warning" : "secondary"}>{effectiveStatus}</Badge>{" "}
            · Kết nối: {stream.mode === "sse" ? "realtime (SSE)" : stream.mode === "poll" ? "polling (dự phòng)" : "đang kết nối…"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={actionLoading || !["validated", "stopped"].includes(effectiveStatus)} onClick={() => runControl("run")}>
            <Play className="size-4" /> Chạy
          </Button>
          <Button size="sm" variant="outline" disabled={actionLoading || effectiveStatus !== "running"} onClick={() => runControl("pause")}>
            <Pause className="size-4" /> Tạm dừng
          </Button>
          <Button size="sm" variant="outline" disabled={actionLoading || effectiveStatus !== "paused"} onClick={() => runControl("resume")}>
            <Play className="size-4" /> Tiếp tục
          </Button>
          <Button size="sm" variant="destructive" disabled={actionLoading || !["running", "paused"].includes(effectiveStatus)} onClick={() => runControl("stop")}>
            <Square className="size-4" /> Dừng
          </Button>
          <Button size="sm" variant="outline" onClick={exportCsv}>
            <Download className="size-4" /> Xuất CSV
          </Button>
          <Button size="sm" variant="outline" onClick={exportXlsx}>
            <Download className="size-4" /> Xuất Excel
          </Button>
        </div>
      </div>

      {effectiveCounts && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
          {METRIC_KEYS.map((key) => (
            <div key={key} className="glass rounded-lg p-3 text-center">
              <p className="text-xl font-semibold">{effectiveCounts[key]}</p>
              <p className="text-xs text-muted-foreground">{METRIC_LABEL[key]}</p>
            </div>
          ))}
        </div>
      )}

      <Tabs defaultValue="items">
        <TabsList className="w-fit">
          <TabsTrigger value="items">Item</TabsTrigger>
          <TabsTrigger value="events">Sự kiện</TabsTrigger>
        </TabsList>

        <TabsContent value="items">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <div className="relative w-56">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="pl-8" placeholder="Tìm theo username" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <div className="w-44">
              <Select value={itemStatus || "all"} onValueChange={(v) => setItemStatus(v === "all" ? "" : v)}>
                <SelectTrigger><SelectValue placeholder="Tất cả trạng thái" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tất cả trạng thái</SelectItem>
                  {Object.keys(ITEM_STATUS_BADGE).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {loadingItems && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          </div>

          <div className="glass overflow-auto rounded-xl">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface-2 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Username</th>
                  <th className="px-3 py-2">Discord ID</th>
                  <th className="px-3 py-2">Điểm</th>
                  <th className="px-3 py-2">Trạng thái</th>
                  <th className="px-3 py-2">Xác nhận</th>
                  <th className="px-3 py-2">Lỗi</th>
                  <th className="px-3 py-2">Số lần thử</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="cursor-pointer border-t border-hairline hover:bg-elev-1" onClick={() => openItem(item)}>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{item.row_index + 1}</td>
                    <td className="px-3 py-1.5">{item.raw_username}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{item.resolved_user_id ?? "—"}</td>
                    <td className="px-3 py-1.5">{item.point}</td>
                    <td className="px-3 py-1.5"><Badge variant={ITEM_STATUS_BADGE[item.status] === "default" ? undefined : ITEM_STATUS_BADGE[item.status]}>{item.status}</Badge></td>
                    <td className="px-3 py-1.5 text-xs">{item.confirmation_level ?? "—"}</td>
                    <td className="px-3 py-1.5 text-xs text-destructive">{item.failure_code ?? ""}</td>
                    <td className="px-3 py-1.5 text-xs">{item.attempt_count}</td>
                  </tr>
                ))}
                {items.length === 0 && !loadingItems && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-sm text-muted-foreground">Không có item</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </TabsContent>

        <TabsContent value="events">
          <div className="glass flex flex-col divide-y divide-hairline rounded-xl">
            {events.length === 0 && <p className="p-4 text-sm text-muted-foreground">Chưa có sự kiện</p>}
            {events.map((ev) => (
              <div key={ev.id} className="flex items-center justify-between px-4 py-2 text-sm">
                <span>{ev.message}</span>
                <span className="text-xs text-muted-foreground">{new Date(ev.created_at).toLocaleString("vi-VN")}</span>
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>

      {selectedItem && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={() => setSelectedItem(null)}>
          <div className="glass h-full w-full max-w-md overflow-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold">{selectedItem.raw_username}</h2>
              <button onClick={() => setSelectedItem(null)}><X className="size-5" /></button>
            </div>

            <div className="mb-4 flex flex-col gap-1 text-sm">
              <p>Trạng thái: <Badge variant={ITEM_STATUS_BADGE[selectedItem.status] === "default" ? undefined : ITEM_STATUS_BADGE[selectedItem.status]}>{selectedItem.status}</Badge></p>
              <p>Điểm: {selectedItem.point}</p>
              <p>Discord ID: {selectedItem.resolved_user_id ?? "—"}</p>
              <p>Mã lỗi: {selectedItem.failure_code ?? "—"}</p>
              <p>Thông điệp lỗi: {selectedItem.failure_message ?? "—"}</p>
            </div>

            {selectedItem.status === "unknown" && (
              <div className="mb-4 flex flex-col gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs">
                <p>Hệ thống <strong>không tự gửi lại</strong> item unknown để tránh cộng trùng điểm.</p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => resolveItem(selectedItem, "success")}>Đánh dấu đã cộng</Button>
                  <Button size="sm" variant="outline" onClick={() => resolveItem(selectedItem, "pending")}>Cho phép gửi lại</Button>
                </div>
              </div>
            )}

            <h3 className="mb-2 text-sm font-semibold">Lịch sử thử ({attempts.length})</h3>
            <div className="flex flex-col gap-2">
              {attempts.map((a) => (
                <div key={a.id} className="rounded-lg border border-border-strong p-2 text-xs">
                  <p>#{a.attempt_no} · nonce {a.nonce} · HTTP {a.http_status ?? "—"} · {a.link_mode ?? "—"}</p>
                  {a.reply_excerpt && <p className="mt-1 text-muted-foreground">{a.reply_excerpt}</p>}
                  <p className="mt-1 text-muted-foreground">{new Date(a.created_at).toLocaleString("vi-VN")}</p>
                </div>
              ))}
              {attempts.length === 0 && <p className="text-xs text-muted-foreground">Chưa có lần thử nào</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

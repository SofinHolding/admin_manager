/**
 * Theo dõi một công việc phân phối — trạng thái + tiến độ + nút điều khiển đúng trạng thái, dòng thời gian sự kiện,
 * danh sách mục (lọc, phân trang), chi tiết từng mục (lịch sử thử), đối soát mục "chưa rõ", xuất CSV/Excel.
 *
 * Cập nhật trực tiếp qua `useJobStream` (SSE, tự chuyển sang hỏi lại mỗi vài giây khi stream hỏng). Hook KHÔNG phơi bày
 * việc server đóng stream vì thu quyền (sự kiện `revoked`), nên trang chỉ nhận ra thu quyền khi một request trả 403.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  CircleAlert, CircleCheck, CirclePause, CirclePlay, CircleStop, Download, Loader2, Play, Radio, RefreshCw, RotateCcw, Search,
} from "lucide-react";
import {
  jobsApi, type JobAttempt, type JobDetail, type JobEvent, type JobItem,
} from "../api";
import { ApiError } from "../../lib/errors";
import { useJobStream } from "../hooks/useJobStream";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { Field, Notice, PageHeader, Section, StatStrip, TechDetails } from "../../components/kit";
import {
  ConfirmDialog, ITEM_STATUS, ItemStatusBadge, ProgressBar, StatusBadge, explainCode, explainPause, fmtNumber, formatDateTime,
  jobStatus, progressSummary, relativeTime, type ConfirmSpec,
} from "./rewardUi";

const PAGE_SIZE = 50;

/** Dòng nhật ký như backend thực sự trả: `{id, job_id, at, type, actor, payload}`. Giữ tên cũ (kind/message/created_at) làm dự phòng. */
type RawEvent = Partial<JobEvent> & { type?: string; at?: string; actor?: string; payload?: unknown; id: number };

function parsePayload(p: unknown): Record<string, unknown> {
  if (typeof p === "string") {
    try { const v = JSON.parse(p); return v && typeof v === "object" ? v : {}; } catch { return {}; }
  }
  return p && typeof p === "object" ? (p as Record<string, unknown>) : {};
}

interface EventView { title: string; code?: string; tone: "default" | "warning" | "success" | "bad" }

function describeEvent(ev: RawEvent): EventView {
  const type = ev.type ?? ev.kind ?? "";
  const pl = parsePayload(ev.payload);
  const num = (k: string) => (typeof pl[k] === "number" ? (pl[k] as number) : 0);
  switch (type) {
    case "created": return { title: "Công việc được tạo", tone: "default" };
    case "validated": return { title: "Danh sách đã được kiểm tra", tone: "default" };
    case "auto_pause": {
      const reason = typeof pl.reason === "string" ? pl.reason : undefined;
      return { title: explainPause(reason), code: reason, tone: "warning" };
    }
    case "stopped": return { title: "Công việc đã dừng", tone: "default" };
    case "recovery": return { title: "Hệ thống khôi phục công việc sau khi bị gián đoạn", tone: "warning" };
    case "token_disabled": {
      const reason = typeof pl.reason === "string" ? pl.reason : undefined;
      const label = typeof pl.label === "string" && pl.label ? ` "${pl.label}"` : "";
      return { title: `Tài khoản Discord${label} bị loại khỏi vòng gửi. ${reason ? explainCode(reason) : ""}`.trim(), code: reason, tone: "warning" };
    }
    case "reconcile":
      return { title: `Đối soát với lịch sử kênh: ${num("success")} thành công, ${num("failed")} thất bại, ${num("no_reply")} không có phản hồi`, tone: "success" };
    case "retry_unknown": return { title: "Đối soát và mở lại các mục chưa rõ để gửi lại", tone: "default" };
    case "operator_resolve": return { title: "Một mục chưa rõ được xử lý thủ công", tone: "default" };
    case "warn_no_pattern": return { title: "Chưa có mẫu nhận biết thành công nên kết quả từ bot có thể không xác nhận được", tone: "warning" };
    default: return { title: ev.message || "Sự kiện hệ thống", code: type || undefined, tone: "default" };
  }
}

const EVENT_ICON = { default: CirclePlay, warning: CirclePause, success: CircleCheck, bad: CircleAlert } as const;

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [items, setItems] = useState<JobItem[]>([]);
  const [itemsTotal, setItemsTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [itemStatus, setItemStatus] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [loadingItems, setLoadingItems] = useState(true);
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [selectedItem, setSelectedItem] = useState<JobItem | null>(null);
  const [attempts, setAttempts] = useState<JobAttempt[]>([]);
  const [actionLoading, setActionLoading] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  const [denied, setDenied] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const deniedRef = useRef(false);

  const stream = useJobStream(id);

  /** 403 = quyền bị thu hồi/không còn: dừng mọi cập nhật và nói rõ; lỗi khác báo toast như cũ. */
  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 403) {
      deniedRef.current = true;
      setDenied(true);
      return;
    }
    toast.error((err as Error).message);
  }, []);

  const loadJob = useCallback(async () => {
    if (!id || deniedRef.current) return;
    try {
      const d = await jobsApi.get(id);
      setJob(d);
      setLoadFailed(false);
    } catch (err) {
      setLoadFailed(true);
      handleError(err);
    }
  }, [id, handleError]);

  const loadItems = useCallback(async () => {
    if (!id || deniedRef.current) return;
    setLoadingItems(true);
    try {
      const d = await jobsApi.items(id, { status: itemStatus || undefined, q: search || undefined, limit: PAGE_SIZE, offset });
      setItems(d.items);
      setItemsTotal(d.total ?? d.items.length);
    } catch (err) {
      handleError(err);
    } finally {
      setLoadingItems(false);
    }
  }, [id, itemStatus, search, offset, handleError]);

  const loadEvents = useCallback(async () => {
    if (!id || deniedRef.current) return;
    try {
      const d = await jobsApi.events(id);
      setEvents(d.events as RawEvent[]);
    } catch (err) {
      handleError(err);
    }
  }, [id, handleError]);

  // Tìm kiếm chờ người dùng gõ xong rồi mới gọi (tránh một request cho mỗi phím).
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput.trim()); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => { loadJob(); }, [loadJob]);
  useEffect(() => { loadItems(); }, [loadItems]);

  const effectiveStatus = stream.status ?? job?.status ?? "";
  const effectiveCounts = stream.counts ?? job?.counts;

  // Trạng thái đổi (vd tự tạm dừng) ⇒ nhật ký có thể có sự kiện mới.
  useEffect(() => { loadEvents(); }, [loadEvents, effectiveStatus]);

  // SSE báo có item vừa đổi trạng thái → refetch bảng item + job (đủ đơn giản, tránh state hai nguồn).
  useEffect(() => {
    if (stream.itemTick === 0) return;
    loadItems();
    loadJob();
  }, [stream.itemTick, loadItems, loadJob]);

  const runControl = async (action: "run" | "pause" | "resume" | "stop") => {
    if (!id) return;
    setActionLoading(true);
    try {
      const fn = { run: jobsApi.run, pause: jobsApi.pause, resume: jobsApi.resume, stop: jobsApi.stop }[action];
      await fn(id);
      toast.success({
        run: "Đã bắt đầu phân phối",
        pause: "Đã gửi lệnh tạm dừng. Công việc sẽ nghỉ sau lệnh đang gửi",
        resume: "Đã tiếp tục phân phối",
        stop: "Đã dừng công việc",
      }[action]);
      await loadJob();
    } catch (err) {
      handleError(err);
    } finally {
      setActionLoading(false);
    }
  };

  const askStop = () => setConfirm({
    title: "Dừng công việc này?",
    body: "Dừng sẽ kết thúc lần chạy hiện tại: các mục chưa gửi sẽ không được gửi nữa, trừ khi bạn chạy lại công việc. Điểm đã gửi rồi không thu hồi được. Nếu chỉ muốn nghỉ một lúc, hãy chọn Tạm dừng.",
    confirmLabel: "Dừng công việc",
    destructive: true,
    onConfirm: () => runControl("stop"),
  });

  const unknownCount = effectiveCounts?.unknown ?? 0;
  const canRecover = unknownCount > 0 && effectiveStatus !== "running";

  const reconcile = async () => {
    if (!id) return;
    setActionLoading(true);
    try {
      const r = await jobsApi.reconcile(id);
      const s = r.summary;
      toast.success(
        `Đối soát xong: ${s.success} thành công, ${s.failed} thất bại, ${s.no_reply} không có phản hồi` +
        (s.inconclusive ? `, ${s.inconclusive} chưa kết luận được` : ""),
      );
      await loadItems();
      await loadJob();
    } catch (err) {
      handleError(err);
    } finally {
      setActionLoading(false);
    }
  };

  const doRetryUnknown = async () => {
    if (!id) return;
    setActionLoading(true);
    try {
      const r = await jobsApi.retryUnknown(id);
      toast.success(`Đã cộng ${r.summary.success} mục đối soát được; mở lại ${r.requeued} mục để gửi lại`);
      if (r.requeued > 0) {
        await jobsApi.resume(id);
        toast.success("Đã chạy tiếp để gửi lại các mục còn thiếu");
      }
      await loadItems();
      await loadJob();
    } catch (err) {
      handleError(err);
    } finally {
      setActionLoading(false);
    }
  };

  const askRetryUnknown = () => setConfirm({
    title: "Đối soát rồi gửi lại các mục chưa rõ?",
    body: "Hệ thống kiểm tra lịch sử kênh Discord trước. Mục nào đã được cộng điểm sẽ chuyển sang thành công và KHÔNG gửi lại. Chỉ những mục chắc chắn chưa có phản hồi mới được gửi lại.",
    confirmLabel: "Đối soát và gửi lại",
    onConfirm: doRetryUnknown,
  });

  const openItem = async (item: JobItem) => {
    setSelectedItem(item);
    setAttempts([]);
    if (!id) return;
    try {
      const d = await jobsApi.attempts(id, item.id);
      setAttempts(d.attempts);
    } catch (err) {
      handleError(err);
    }
  };

  const resolveItem = async (item: JobItem, to: "success" | "pending") => {
    if (!id) return;
    try {
      await jobsApi.resolveItem(id, item.id, to);
      toast.success(to === "success" ? "Đã đánh dấu đã cộng điểm" : "Đã cho phép gửi lại");
      setSelectedItem(null);
      await loadItems();
      await loadJob();
    } catch (err) {
      handleError(err);
    }
  };

  const askResolvePending = (item: JobItem) => {
    setSelectedItem(null);
    setConfirm({
      title: `Cho phép gửi lại mục ${item.raw_username}?`,
      body: "Hệ thống không tự gửi lại mục chưa rõ để tránh cộng trùng điểm. Nếu bạn chắc chắn mục này chưa được cộng, bấm xác nhận để đưa nó về hàng đợi; sau đó bấm Tiếp tục để gửi.",
      confirmLabel: "Cho phép gửi lại",
      onConfirm: () => resolveItem(item, "pending"),
    });
  };

  const download = async (kind: "csv" | "xlsx") => {
    if (!id || !job) return;
    try {
      const blob = kind === "csv" ? await jobsApi.exportCsv(id) : await jobsApi.exportXlsx(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.name || id}.${kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      handleError(err);
    }
  };

  if (!job) {
    if (denied) {
      return (
        <Notice tone="error" title="Bạn không còn quyền xem công việc này"
                action={<Button asChild variant="outline"><Link to="/reward/jobs">Về danh sách</Link></Button>}>
          Quyền phân phối điểm của bạn có thể đã bị thu hồi, hoặc công việc thuộc về người khác. Liên hệ quản trị viên nếu bạn cần truy cập.
        </Notice>
      );
    }
    if (loadFailed) {
      return (
        <Notice tone="error" title="Chưa tải được công việc" action={<Button variant="outline" onClick={loadJob}>Thử lại</Button>}>
          Công việc không tồn tại hoặc mạng đang gián đoạn. Kiểm tra rồi bấm Thử lại.
        </Notice>
      );
    }
    return <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4" aria-hidden /> Đang tải công việc…</p>;
  }

  const info = jobStatus(effectiveStatus);
  const counts = effectiveCounts ?? job.counts;
  const waiting = counts.pending + counts.processing + counts.retrying;
  const donePct = counts.total > 0 ? Math.round((counts.success / counts.total) * 100) : 0;
  const lastPause = effectiveStatus === "paused" ? [...events].reverse().find((e) => (e.type ?? e.kind) === "auto_pause") : undefined;
  const pauseView = lastPause ? describeEvent(lastPause) : null;
  const live = !info.final && !denied;

  const controls = denied ? null : (
    <>
      {effectiveStatus === "validated" && (
        <Button disabled={actionLoading} onClick={() => runControl("run")}><Play aria-hidden /> Bắt đầu phân phối</Button>
      )}
      {effectiveStatus === "stopped" && (
        <Button disabled={actionLoading} onClick={() => runControl("run")}><Play aria-hidden /> Chạy lại</Button>
      )}
      {effectiveStatus === "paused" && (
        <Button disabled={actionLoading} onClick={() => runControl("resume")}><Play aria-hidden /> Tiếp tục</Button>
      )}
      {effectiveStatus === "running" && (
        <Button variant="outline" disabled={actionLoading} onClick={() => runControl("pause")}><CirclePause aria-hidden /> Tạm dừng</Button>
      )}
      {(effectiveStatus === "running" || effectiveStatus === "paused") && (
        <Button variant="outline" disabled={actionLoading} onClick={askStop} className="text-destructive hover:text-destructive">
          <CircleStop aria-hidden /> Dừng
        </Button>
      )}
    </>
  );

  const fromItem = itemsTotal === 0 ? 0 : offset + 1;
  const toItem = Math.min(offset + items.length, itemsTotal);

  return (
    <div>
      <PageHeader
        title={job.name}
        description={<span className="flex flex-wrap items-center gap-x-3 gap-y-1"><StatusBadge status={effectiveStatus} /><span>{info.hint}</span></span>}
        actions={controls}
      />

      <div className="mb-6 space-y-3">
        {denied && (
          <Notice tone="error" title="Quyền của bạn đã bị thu hồi, trang này ngừng cập nhật"
                  action={<Button asChild variant="outline"><Link to="/reward/jobs">Về danh sách</Link></Button>}>
            Bạn không còn quyền xem hoặc điều khiển công việc này nên các nút điều khiển đã ẩn. Công việc đã chạy phía máy chủ vẫn được giữ nguyên. Liên hệ quản trị viên để được cấp lại quyền.
          </Notice>
        )}
        {effectiveStatus === "invalid" && (
          <Notice tone="error" title="Danh sách có dòng sai nên công việc chưa chạy được">
            {job.issues?.length > 0 ? (
              <ul className="mt-1 space-y-1">
                {job.issues.slice(0, 20).map((i, n) => <li key={n}>Dòng {i.row_index + 1}: {i.message}</li>)}
                {job.issues.length > 20 && <li>… và {job.issues.length - 20} vấn đề khác (tải tệp kết quả để xem đủ).</li>}
              </ul>
            ) : "Sửa danh sách rồi tạo công việc mới."}
            <p className="mt-2"><Link to="/reward/jobs/new" className="font-medium text-primary-text underline underline-offset-2">Tạo công việc mới</Link></p>
          </Notice>
        )}
        {pauseView && (
          <Notice tone="warning" title="Công việc tự tạm dừng">
            {pauseView.title}
            {pauseView.code && <TechDetails className="mt-2"><p>Mã lý do: <code className="font-mono">{pauseView.code}</code></p></TechDetails>}
          </Notice>
        )}
        {live && stream.mode === "poll" && (
          <Notice tone="info" title="Đang cập nhật bằng cách hỏi lại mỗi vài giây">
            Kết nối cập nhật trực tiếp không dùng được nên trang tự hỏi lại máy chủ khoảng 2 giây một lần. Số liệu vẫn đúng, chỉ chậm hơn một chút.
          </Notice>
        )}
        {live && stream.mode === "connecting" && (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Radio className="size-4" aria-hidden /> Đang kết nối để cập nhật trực tiếp…</p>
        )}
        {live && stream.mode === "sse" && (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Radio className="size-4" aria-hidden /> Đang cập nhật trực tiếp</p>
        )}
        {canRecover && !denied && (
          <Notice tone="warning" title={`Có ${fmtNumber(unknownCount)} mục chưa xác nhận được`}
                  action={
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button variant="outline" disabled={actionLoading} onClick={reconcile}
                              title="Kiểm tra lịch sử kênh Discord để tìm phản hồi thật của các mục chưa rõ">
                        <RefreshCw aria-hidden /> Đối soát ({unknownCount})
                      </Button>
                      <Button variant="outline" disabled={actionLoading} onClick={askRetryUnknown}
                              title="Đối soát rồi gửi lại các mục chắc chắn chưa được cộng">
                        <RotateCcw aria-hidden /> Gửi lại mục chưa rõ
                      </Button>
                    </div>
                  }>
            "Chưa rõ" không có nghĩa là chưa cộng điểm. Bấm <b>Đối soát</b> để kiểm tra lịch sử kênh Discord (phản hồi thật sẽ chuyển mục sang thành công), hoặc <b>Gửi lại mục chưa rõ</b> để hệ thống đối soát rồi chỉ gửi lại những mục chắc chắn chưa được cộng.
          </Notice>
        )}
      </div>

      <Section id="tien-do" title={`Tiến độ: ${progressSummary(counts)} (${donePct}%)`}>
        <ProgressBar counts={counts} label={`Tiến độ của ${job.name}`} />
        <StatStrip
          className="mt-5"
          stats={[
            { label: "Thành công", value: fmtNumber(counts.success), note: `trên ${fmtNumber(counts.total)} mục` },
            { label: "Đang chờ gửi", value: fmtNumber(waiting), note: counts.retrying > 0 ? `${fmtNumber(counts.retrying)} đang thử lại` : undefined },
            { label: "Thất bại", value: fmtNumber(counts.failed), note: counts.failed > 0 ? "cần xem lại" : undefined, tone: counts.failed > 0 ? "bad" : "default" },
            { label: "Chưa rõ", value: fmtNumber(counts.unknown), note: counts.unknown > 0 ? "cần đối soát" : undefined },
          ]}
        />
        {counts.skipped > 0 && <p className="mt-2 text-sm text-muted-foreground">{fmtNumber(counts.skipped)} mục được bỏ qua.</p>}
      </Section>

      <Tabs defaultValue="items" className="mt-2">
        <TabsList>
          <TabsTrigger value="items">Danh sách mục ({fmtNumber(counts.total)})</TabsTrigger>
          <TabsTrigger value="events">Dòng thời gian</TabsTrigger>
        </TabsList>

        <TabsContent value="items" className="pt-5">
          <div className="mb-4 flex flex-wrap items-end gap-x-4 gap-y-3">
            <Field label="Tìm theo tên thành viên" className="w-full sm:w-64">
              {(p) => (
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                  <Input {...p} className="pl-9" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} />
                </div>
              )}
            </Field>
            <Field label="Lọc theo trạng thái" className="w-full sm:w-52">
              {(p) => (
                <Select value={itemStatus || "all"} onValueChange={(v) => { setItemStatus(v === "all" ? "" : v); setOffset(0); }}>
                  <SelectTrigger {...p}><SelectValue placeholder="Tất cả trạng thái" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Tất cả trạng thái</SelectItem>
                    {Object.entries(ITEM_STATUS).map(([s, v]) => <SelectItem key={s} value={s}>{v.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <div className="ml-auto flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => download("csv")}><Download aria-hidden /> Xuất CSV</Button>
              <Button variant="outline" onClick={() => download("xlsx")}><Download aria-hidden /> Xuất Excel</Button>
            </div>
          </div>

          {items.length === 0 && !loadingItems ? (
            <p className="border-y border-border py-8 text-muted-foreground">
              {search || itemStatus ? "Không có mục nào khớp bộ lọc. Thử đổi từ khóa hoặc chọn Tất cả trạng thái." : "Công việc này chưa có mục nào."}
            </p>
          ) : (
            <ul className="divide-y divide-border border-y border-border" aria-label="Danh sách mục" aria-busy={loadingItems}>
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button" onClick={() => openItem(item)}
                    aria-label={`Xem chi tiết ${item.raw_username}`}
                    className="flex min-h-11 w-full cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3 text-left hover:bg-elev-1"
                  >
                    <span className="w-9 shrink-0 text-sm text-muted-foreground">#{item.row_index + 1}</span>
                    <span className="min-w-0 flex-1 basis-40 break-all font-medium">{item.raw_username}</span>
                    <span className="text-sm">{fmtNumber(item.point)} điểm</span>
                    <ItemStatusBadge status={item.status} />
                    {(item.failure_code || item.attempt_count > 1) && (
                      <span className="basis-full pl-[3.25rem] text-sm text-muted-foreground">
                        {item.failure_code && <span className="text-destructive">{explainCode(item.failure_code)} </span>}
                        {item.attempt_count > 1 && <span>Đã thử {item.attempt_count} lần.</span>}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground" role="status">
              {loadingItems ? "Đang tải…" : itemsTotal > 0 ? `Hiển thị ${fromItem}–${toItem} trong ${fmtNumber(itemsTotal)} mục` : ""}
            </p>
            {itemsTotal > PAGE_SIZE && (
              <div className="flex gap-2">
                <Button variant="outline" disabled={offset === 0 || loadingItems} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Trang trước</Button>
                <Button variant="outline" disabled={offset + PAGE_SIZE >= itemsTotal || loadingItems} onClick={() => setOffset(offset + PAGE_SIZE)}>Trang sau</Button>
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="events" className="pt-5">
          {events.length === 0 ? (
            <p className="border-y border-border py-8 text-muted-foreground">Chưa có sự kiện nào. Các bước quan trọng như tạo, tạm dừng, dừng sẽ hiện ở đây.</p>
          ) : (
            <ol className="border-t border-border" aria-label="Dòng thời gian sự kiện">
              {[...events].reverse().map((ev) => {
                const v = describeEvent(ev);
                const Icon = EVENT_ICON[v.tone];
                const at = ev.at ?? ev.created_at;
                return (
                  <li key={ev.id} className="flex gap-3 border-b border-border py-3">
                    <Icon className={`mt-0.5 size-5 shrink-0 ${v.tone === "warning" ? "text-warning" : v.tone === "success" ? "text-success" : "text-muted-foreground"}`} aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p>{v.title}</p>
                      <p className="mt-0.5 text-sm text-muted-foreground">
                        <time dateTime={at} title={formatDateTime(at)}>{relativeTime(at)}</time> · {formatDateTime(at)}
                      </p>
                      {v.code && <p className="mt-1 text-sm text-muted-foreground">Mã: <code className="font-mono">{v.code}</code></p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={selectedItem !== null} onOpenChange={(o) => { if (!o) setSelectedItem(null); }}>
        <DialogContent className="max-w-lg">
          {selectedItem && (
            <>
              <DialogTitle className="break-all">{selectedItem.raw_username}</DialogTitle>
              <DialogDescription className="mt-1">Chi tiết một mục trong công việc.</DialogDescription>

              <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[0.9375rem]">
                <dt className="text-muted-foreground">Trạng thái</dt>
                <dd><ItemStatusBadge status={selectedItem.status} /></dd>
                <dt className="text-muted-foreground">Số điểm</dt>
                <dd>{fmtNumber(selectedItem.point)}</dd>
                <dt className="text-muted-foreground">Số lần thử</dt>
                <dd>{selectedItem.attempt_count}</dd>
              </dl>

              {selectedItem.failure_code && (
                <Notice className="mt-4" tone={selectedItem.status === "unknown" ? "warning" : "error"} title="Vì sao chưa thành công">
                  {explainCode(selectedItem.failure_code)}
                </Notice>
              )}

              {selectedItem.status === "unknown" && (
                <div className="mt-4">
                  <p className="text-sm text-muted-foreground">Hệ thống <strong>không tự gửi lại</strong> mục chưa rõ để tránh cộng trùng điểm. Hãy tự xác nhận trong kênh Discord rồi chọn một cách xử lý.</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button onClick={() => resolveItem(selectedItem, "success")}>Đánh dấu đã cộng điểm</Button>
                    <Button variant="outline" onClick={() => askResolvePending(selectedItem)}>Cho phép gửi lại</Button>
                  </div>
                </div>
              )}

              <h3 className="mt-6">Lịch sử thử ({attempts.length})</h3>
              <ul className="mt-2 divide-y divide-border border-y border-border">
                {attempts.map((a) => (
                  <li key={a.id} className="py-2 text-sm">
                    <p className="font-medium">Lần {a.attempt_no} · {formatDateTime(a.created_at)}</p>
                    {a.reply_excerpt && <p className="mt-0.5 break-words text-muted-foreground">Bot trả lời: {a.reply_excerpt}</p>}
                  </li>
                ))}
                {attempts.length === 0 && <li className="py-2 text-sm text-muted-foreground">Chưa có lần thử nào.</li>}
              </ul>

              <TechDetails className="mt-4">
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                  <dt className="text-muted-foreground">Mã lỗi</dt><dd className="break-all font-mono">{selectedItem.failure_code ?? "—"}</dd>
                  <dt className="text-muted-foreground">Thông điệp gốc</dt><dd className="break-words">{selectedItem.failure_message ?? "—"}</dd>
                  <dt className="text-muted-foreground">Discord ID</dt><dd className="break-all font-mono">{selectedItem.resolved_user_id ?? "—"}</dd>
                  <dt className="text-muted-foreground">Mức xác nhận</dt><dd>{selectedItem.confirmation_level ?? "—"}</dd>
                </dl>
                {attempts.map((a) => (
                  <p key={a.id} className="mt-2 break-all font-mono text-xs text-muted-foreground">
                    #{a.attempt_no} · nonce {a.nonce} · HTTP {a.http_status ?? "—"} · {a.link_mode ?? "—"}
                  </p>
                ))}
              </TechDetails>
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}

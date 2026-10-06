/**
 * Tạo công việc phân phối điểm — dán/kéo-thả danh sách "tên|điểm", xem trước, kiểm tra, rồi bắt đầu phân phối.
 *
 * Luồng: "Kiểm tra danh sách" tạo công việc ở trạng thái Sẵn sàng/Có lỗi (chưa gửi gì cho ai). "Bắt đầu phân phối" tạo công việc
 * (nếu chưa) và, CHỈ KHI danh sách hợp lệ, chạy ngay sau khi người dùng xác nhận. Danh sách có lỗi thì không bao giờ tự chạy.
 */

import { useMemo, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CircleCheck, CircleX, Loader2, Play, UploadCloud } from "lucide-react";
import { jobsApi, type JobOverrides, type ValidationIssue } from "../api";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Field, Notice, PageHeader, Section, TechDetails } from "../../components/kit";
import { ConfirmDialog, type ConfirmSpec, fmtNumber } from "./rewardUi";

interface PreviewRow {
  index: number;
  raw: string;
  username: string;
  point: number | null;
  error: string | null;
}

/** Xem trước cục bộ ở trình duyệt — kiểm tra sơ bộ trước khi gọi API, không thay thế việc kiểm tra của máy chủ. */
function parsePreview(rawText: string): PreviewRow[] {
  const lines = rawText.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  return lines.map((raw, index) => {
    const parts = raw.split(/[|,\t]|\s{2,}| (?=\d+$)/).map((p) => p.trim()).filter(Boolean);
    if (parts.length < 2) {
      return { index, raw, username: raw, point: null, error: "Thiếu số điểm. Mỗi dòng cần dạng tên|điểm." };
    }
    const username = parts[0];
    const point = Number(parts[parts.length - 1]);
    if (!username) return { index, raw, username, point: null, error: "Thiếu tên thành viên." };
    if (!Number.isFinite(point) || point <= 0) {
      return { index, raw, username, point: null, error: "Số điểm phải là số nguyên dương." };
    }
    return { index, raw, username, point, error: null };
  });
}

const OVERRIDE_KEYS = ["guild_id", "channel_id", "command_name", "confirm_mode", "delay_ms", "jitter_ms", "max_item_retries", "unknown_pause_threshold"] as const;
type OverrideKey = (typeof OVERRIDE_KEYS)[number];
const NUMBER_KEYS: OverrideKey[] = ["delay_ms", "jitter_ms", "max_item_retries", "unknown_pause_threshold"];

const PREVIEW_ROW_LIMIT = 100;

interface Created { id: string; status: string; total: number; issues: ValidationIssue[] }

export default function JobNewPage() {
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [rawText, setRawText] = useState("");
  const [overrideEnabled, setOverrideEnabled] = useState(false);
  const [overrides, setOverrides] = useState<Record<OverrideKey, string>>({
    guild_id: "", channel_id: "", command_name: "", confirm_mode: "",
    delay_ms: "", jitter_ms: "", max_item_retries: "", unknown_pause_threshold: "",
  });
  const [created, setCreated] = useState<Created | null>(null);
  const [checking, setChecking] = useState(false);
  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [tried, setTried] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);

  const preview = useMemo(() => parsePreview(rawText), [rawText]);
  const errorRows = preview.filter((r) => r.error);
  const okRows = preview.filter((r) => !r.error);
  const totalPoints = okRows.reduce((s, r) => s + (r.point ?? 0), 0);
  const commandName = (overrideEnabled && overrides.command_name.trim()) || "give-xp";
  const locked = created !== null;

  const errors = {
    name: name.trim() ? null : "Đặt tên cho công việc để dễ tìm lại.",
    list: rawText.trim() ? null : "Dán danh sách thành viên và số điểm.",
  };
  const numberErrors = NUMBER_KEYS.reduce<Partial<Record<OverrideKey, string>>>((acc, k) => {
    const v = overrides[k].trim();
    if (overrideEnabled && v !== "" && !/^\d+$/.test(v)) acc[k] = "Nhập số nguyên từ 0 trở lên.";
    return acc;
  }, {});
  const formValid = !errors.name && !errors.list && Object.keys(numberErrors).length === 0;

  const buildOverrides = (): JobOverrides | undefined => {
    if (!overrideEnabled) return undefined;
    const out: JobOverrides = {};
    if (overrides.guild_id) out.guild_id = overrides.guild_id;
    if (overrides.channel_id) out.channel_id = overrides.channel_id;
    if (overrides.command_name) out.command_name = overrides.command_name;
    if (overrides.confirm_mode) out.confirm_mode = overrides.confirm_mode;
    if (overrides.delay_ms) out.delay_ms = Number(overrides.delay_ms);
    if (overrides.jitter_ms) out.jitter_ms = Number(overrides.jitter_ms);
    if (overrides.max_item_retries) out.max_item_retries = Number(overrides.max_item_retries);
    if (overrides.unknown_pause_threshold) out.unknown_pause_threshold = Number(overrides.unknown_pause_threshold);
    return out;
  };

  const handleFile = async (file: File) => {
    const text = await file.text();
    setRawText((prev) => (prev ? `${prev}\n${text}` : text));
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (locked) return;
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  /** Tạo công việc trên máy chủ (chưa chạy). */
  const createJob = async (): Promise<Created> => {
    const r = await jobsApi.create({ name: name.trim(), raw_text: rawText, overrides: buildOverrides() });
    const c = { id: r.job_id, status: r.status, total: r.total_items, issues: r.issues ?? [] };
    setCreated(c);
    return c;
  };

  const handleCheck = async () => {
    if (created) {
      navigate(`/reward/jobs/${created.id}`);
      return;
    }
    setTried(true);
    if (!formValid) return;
    setChecking(true);
    try {
      const c = await createJob();
      if (c.status === "validated") toast.success(`Danh sách hợp lệ: ${c.total} dòng, không có lỗi`);
      else toast.warning(`Danh sách có ${c.issues.length} vấn đề, công việc chưa chạy được`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const askStart = () => {
    setTried(true);
    if (!formValid) return;
    if (created) {
      if (created.status !== "validated") return;
    } else if (errorRows.length > 0) {
      return; // dòng sai cục bộ: hiện lỗi ngay, không tạo công việc để rồi bị từ chối
    }
    setConfirm({
      title: "Bắt đầu phân phối điểm?",
      body: `Hệ thống sẽ gửi lệnh /${commandName} cho ${fmtNumber(okRows.length)} thành viên, tổng ${fmtNumber(totalPoints)} điểm. Điểm đã gửi không thu hồi được. Bạn có thể tạm dừng hoặc dừng bất cứ lúc nào.`,
      confirmLabel: "Bắt đầu phân phối",
      onConfirm: doStart,
    });
  };

  const doStart = async () => {
    setCreating(true);
    try {
      const c = created ?? (await createJob());
      if (c.status !== "validated") {
        toast.warning(`Danh sách có ${c.issues.length} vấn đề nên công việc chưa được chạy`);
        return;
      }
      try {
        await jobsApi.run(c.id);
        toast.success(`Đã bắt đầu phân phối "${name.trim()}"`);
      } catch (err) {
        toast.error(`Đã tạo công việc nhưng chưa chạy được: ${(err as Error).message}`);
      }
      navigate(`/reward/jobs/${c.id}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const busy = checking || creating;
  const startDisabled = busy || (created !== null && created.status !== "validated");

  return (
    <div>
      <PageHeader
        title="Tạo công việc phân phối"
        description="Dán danh sách thành viên Discord và số điểm. Bạn có thể kiểm tra danh sách trước; chưa có điểm nào được gửi cho tới khi bạn bấm Bắt đầu phân phối."
      />

      {created && (
        created.status === "validated" ? (
          <Notice className="mb-6" tone="success" title={`Danh sách hợp lệ: ${fmtNumber(created.total)} dòng, không có lỗi`}
                  action={<Button variant="outline" onClick={() => navigate(`/reward/jobs/${created.id}`)}>Mở công việc</Button>}>
            Công việc đã được tạo nhưng chưa chạy. Bấm Bắt đầu phân phối để gửi điểm, hoặc mở công việc để xem lại.
          </Notice>
        ) : (
          <Notice className="mb-6" tone="error" title={`Danh sách có ${created.issues.length} vấn đề nên chưa chạy được`}
                  action={<Button variant="outline" onClick={() => setCreated(null)}>Sửa danh sách</Button>}>
            Bấm Sửa danh sách, chỉnh các dòng bên dưới rồi kiểm tra lại. Công việc lỗi này sẽ được giữ lại trong danh sách để bạn tham khảo.
          </Notice>
        )
      )}

      <Section id="thong-tin" title="1. Đặt tên">
        <div className="max-w-xl">
          <Field label="Tên công việc" hint="Chỉ để bạn tìm lại, ví dụ Tuần 38 - Top voice." error={tried ? errors.name : null}>
            {(p) => <Input {...p} value={name} disabled={locked} onChange={(e) => setName(e.target.value)} />}
          </Field>
        </div>
      </Section>

      <Section id="danh-sach" title="2. Danh sách nhận điểm"
               description="Mỗi dòng một thành viên: tên Discord, dấu gạch đứng, rồi số điểm. Ví dụ: ten_thanh_vien|100. Dấu phẩy hoặc tab cũng được.">
        <div
          onDragOver={(e) => { e.preventDefault(); if (!locked) setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={`rounded-lg border-2 border-dashed p-3 ${dragOver ? "border-primary bg-primary/5" : "border-border-strong"}`}
        >
          <Field label="Danh sách thành viên và điểm" hint="Dán trực tiếp, hoặc kéo thả tệp .txt/.csv vào khung này." error={tried ? errors.list : null}>
            {(p) => (
              <Textarea
                {...p}
                className="min-h-[14rem] font-mono text-sm"
                value={rawText} disabled={locked}
                onChange={(e) => setRawText(e.target.value)}
                placeholder={"ten_thanh_vien_1|100\nten_thanh_vien_2|250\nten_thanh_vien_3,50"}
              />
            )}
          </Field>
          <div className="mt-3">
            <input
              ref={fileRef} type="file" accept=".txt,.csv,text/plain,text/csv" className="sr-only" tabIndex={-1} aria-label="Chọn tệp danh sách"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void handleFile(f); e.target.value = ""; }}
            />
            <Button variant="outline" disabled={locked} onClick={() => fileRef.current?.click()}>
              <UploadCloud aria-hidden /> Chọn tệp từ máy
            </Button>
          </div>
        </div>

        {preview.length > 0 && (
          <div className="mt-4">
            <p role="status" className="flex flex-wrap items-center gap-x-4 gap-y-1 font-medium">
              <span>{fmtNumber(preview.length)} dòng</span>
              {errorRows.length > 0 ? (
                <span className="inline-flex items-center gap-1.5 text-destructive"><CircleX className="size-4" aria-hidden /> {fmtNumber(errorRows.length)} dòng lỗi</span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-success"><CircleCheck className="size-4" aria-hidden /> Định dạng hợp lệ</span>
              )}
              {okRows.length > 0 && <span className="text-muted-foreground">Tổng {fmtNumber(totalPoints)} điểm</span>}
            </p>
            {errorRows.length > 0 && (
              <p className="mt-1 text-sm text-muted-foreground">Dòng lỗi được liệt kê trước. Sửa trong khung trên rồi danh sách này tự cập nhật.</p>
            )}
            <ul className="mt-3 max-h-72 divide-y divide-border overflow-auto border-y border-border" aria-label="Xem trước danh sách">
              {[...errorRows, ...okRows].slice(0, PREVIEW_ROW_LIMIT).map((row) => (
                <li key={row.index} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 text-sm">
                  <span className="w-10 shrink-0 text-muted-foreground">#{row.index + 1}</span>
                  <span className="min-w-0 flex-1 break-all font-medium">{row.username}</span>
                  <span>{row.point != null ? `${fmtNumber(row.point)} điểm` : "—"}</span>
                  {row.error && <span className="flex basis-full items-center gap-1.5 pl-[3.25rem] font-medium text-destructive"><CircleX className="size-4 shrink-0" aria-hidden /> {row.error}</span>}
                </li>
              ))}
            </ul>
            {preview.length > PREVIEW_ROW_LIMIT && (
              <p className="mt-2 text-sm text-muted-foreground">Chỉ hiển thị {PREVIEW_ROW_LIMIT} dòng đầu để trang gọn; toàn bộ {fmtNumber(preview.length)} dòng vẫn được gửi đi.</p>
            )}
          </div>
        )}
      </Section>

      <Section id="cai-dat" title="3. Cài đặt riêng" description="Mặc định công việc dùng đúng cấu hình ở trang Kết nối Discord. Chỉ bật mục này khi công việc này cần khác đi.">
        <label className="flex min-h-11 cursor-pointer items-center gap-3 font-medium">
          <input type="checkbox" checked={overrideEnabled} disabled={locked} onChange={(e) => setOverrideEnabled(e.target.checked)} className="size-4 rounded accent-primary" />
          Dùng cài đặt khác cho công việc này
        </label>
        {overrideEnabled && (
          <div className="mt-4 grid max-w-3xl gap-4 md:grid-cols-2">
            <Field label="Mã máy chủ Discord" optional hint="Để trống để dùng máy chủ đã lưu ở Kết nối Discord.">
              {(p) => <Input {...p} value={overrides.guild_id} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, guild_id: e.target.value }))} />}
            </Field>
            <Field label="Mã kênh Discord" optional hint="Để trống để dùng kênh đã lưu.">
              {(p) => <Input {...p} value={overrides.channel_id} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, channel_id: e.target.value }))} />}
            </Field>
            <Field label="Tên lệnh" optional hint="Mặc định give-xp.">
              {(p) => <Input {...p} value={overrides.command_name} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, command_name: e.target.value }))} />}
            </Field>
            <Field label="Cách xác nhận kết quả" optional hint="Chờ bot trả lời giúp biết chắc điểm đã được cộng.">
              {(p) => (
                <Select value={overrides.confirm_mode || "default"} disabled={locked}
                        onValueChange={(v) => setOverrides((o) => ({ ...o, confirm_mode: v === "default" ? "" : v }))}>
                  <SelectTrigger {...p}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">Dùng cài đặt đã lưu</SelectItem>
                    <SelectItem value="reply">Chờ bot trả lời</SelectItem>
                    <SelectItem value="off">Không chờ xác nhận</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label="Độ trễ giữa các lệnh (ms)" optional hint="Khoảng chờ tối thiểu giữa hai lệnh của cùng một tài khoản." error={tried ? numberErrors.delay_ms : null}>
              {(p) => <Input {...p} inputMode="numeric" value={overrides.delay_ms} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, delay_ms: e.target.value }))} />}
            </Field>
            <Field label="Độ lệch ngẫu nhiên (ms)" optional hint="Thời gian chờ ngẫu nhiên cộng thêm." error={tried ? numberErrors.jitter_ms : null}>
              {(p) => <Input {...p} inputMode="numeric" value={overrides.jitter_ms} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, jitter_ms: e.target.value }))} />}
            </Field>
            <Field label="Số lần thử lại tối đa cho mỗi dòng" optional hint="Quá số lần này dòng được tính là thất bại." error={tried ? numberErrors.max_item_retries : null}>
              {(p) => <Input {...p} inputMode="numeric" value={overrides.max_item_retries} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, max_item_retries: e.target.value }))} />}
            </Field>
            <Field label="Tự tạm dừng sau số kết quả chưa rõ liên tiếp" optional hint="Để bạn đối soát trước khi gửi tiếp, tránh cộng trùng điểm." error={tried ? numberErrors.unknown_pause_threshold : null}>
              {(p) => <Input {...p} inputMode="numeric" value={overrides.unknown_pause_threshold} disabled={locked} onChange={(e) => setOverrides((o) => ({ ...o, unknown_pause_threshold: e.target.value }))} />}
            </Field>
          </div>
        )}
      </Section>

      <Section id="ket-qua" title="Điều gì sẽ xảy ra">
        <p className="reading">
          {okRows.length > 0
            ? <>Khi bạn bấm <strong>Bắt đầu phân phối</strong>, hệ thống gửi lần lượt {fmtNumber(okRows.length)} lệnh cộng điểm (tổng {fmtNumber(totalPoints)} điểm) vào kênh Discord đã cấu hình.</>
            : <>Khi bạn bấm <strong>Bắt đầu phân phối</strong>, hệ thống gửi lần lượt từng lệnh cộng điểm vào kênh Discord đã cấu hình.</>}
          {" "}Bạn theo dõi tiến độ trực tiếp ở trang công việc, có thể tạm dừng hoặc dừng bất cứ lúc nào. Mục nào không biết chắc đã được cộng điểm hay chưa sẽ được đánh dấu "Chưa rõ" và không tự gửi lại, để tránh cộng trùng.
        </p>
        <p className="reading mt-2 text-sm text-muted-foreground">
          Cần kết nối Discord đang hoạt động. <Link to="/reward/credentials" className="font-medium text-primary-text underline underline-offset-2">Kiểm tra kết nối Discord</Link>
        </p>
        {okRows.length > 0 && (
          <TechDetails summary="Xem trước 5 lệnh đầu tiên (chưa gửi gì)" className="mt-4 max-w-3xl">
            <ul className="space-y-1 font-mono text-xs">
              {okRows.slice(0, 5).map((row) => (
                <li key={row.index} className="break-all">/{commandName} member:{row.username} amount:{row.point}</li>
              ))}
            </ul>
          </TechDetails>
        )}
      </Section>

      {created && created.issues.length > 0 && (
        <Section id="van-de" title="Vấn đề máy chủ tìm thấy">
          <ul className="space-y-1.5 text-sm">
            {created.issues.map((issue, i) => (
              <li key={i} className="flex items-start gap-2">
                <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                <span>Dòng {issue.row_index + 1} ({issue.severity === "error" ? "lỗi" : issue.severity === "warning" ? "cảnh báo" : issue.severity}): {issue.message}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-6">
        <Button onClick={askStart} disabled={startDisabled}>
          {creating ? <Loader2 aria-hidden /> : <Play aria-hidden />} Bắt đầu phân phối
        </Button>
        <Button variant="outline" onClick={handleCheck} disabled={busy}>
          {checking && <Loader2 aria-hidden />} {created ? "Mở công việc đã tạo" : "Kiểm tra danh sách"}
        </Button>
        {(checking || creating) && <p role="status" className="text-sm text-muted-foreground">{creating ? "Đang tạo và bắt đầu công việc…" : "Đang kiểm tra danh sách…"}</p>}
      </div>
      {tried && !formValid && (
        <p role="alert" className="mt-3 text-sm font-medium text-destructive">Hãy sửa các ô báo lỗi ở trên trước khi tiếp tục.</p>
      )}
      {tried && formValid && !created && errorRows.length > 0 && (
        <p role="alert" className="mt-3 text-sm font-medium text-destructive">Danh sách còn {fmtNumber(errorRows.length)} dòng lỗi. Sửa các dòng được đánh dấu rồi bấm lại.</p>
      )}

      <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}

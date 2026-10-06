/**
 * Thành phần và từ điển dùng chung cho các trang Phân phối điểm (Discord).
 * Mục đích: người vận hành không rành kỹ thuật đọc được trạng thái, lý do tạm dừng và mã lỗi bằng tiếng Việt;
 * mã gốc luôn được giữ lại để đặt trong "Chi tiết kỹ thuật". Không chứa logic gọi API.
 */

import type { ComponentType, ReactNode } from "react";
import {
  Ban, CircleCheck, CircleDashed, CircleHelp, CirclePause, CirclePlay, CircleStop, CircleX,
  ClipboardCheck, FilePen, Hourglass, RotateCw, Send,
} from "lucide-react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import type { JobCounts } from "../api";

type Icon = ComponentType<{ className?: string }>;
type Tone = "default" | "success" | "warning" | "destructive" | "secondary";

/* ── Trạng thái công việc ──────────────────────────────────────────────────── */

export interface StatusInfo {
  label: string;
  tone: Tone;
  icon: Icon;
  /** Một câu nói trạng thái này nghĩa là gì và làm gì tiếp. */
  hint: string;
  /** Công việc đã kết thúc hẳn: không còn cập nhật. */
  final?: boolean;
}

export const JOB_STATUS: Record<string, StatusInfo> = {
  draft: { label: "Bản nháp", tone: "secondary", icon: FilePen, hint: "Danh sách chưa được kiểm tra." },
  validated: { label: "Sẵn sàng chạy", tone: "default", icon: ClipboardCheck, hint: "Danh sách hợp lệ. Bấm Bắt đầu phân phối để gửi điểm." },
  queued: { label: "Đang chờ chạy", tone: "default", icon: Hourglass, hint: "Công việc đã vào hàng đợi và sẽ tự bắt đầu." },
  running: { label: "Đang chạy", tone: "success", icon: CirclePlay, hint: "Hệ thống đang gửi điểm lần lượt cho từng thành viên." },
  stopping: { label: "Đang dừng", tone: "warning", icon: CircleStop, hint: "Hệ thống đang hoàn tất lệnh hiện tại rồi sẽ dừng." },
  paused: { label: "Tạm dừng", tone: "warning", icon: CirclePause, hint: "Công việc đang nghỉ. Bấm Tiếp tục để chạy phần còn lại." },
  stopped: { label: "Đã dừng", tone: "secondary", icon: CircleStop, hint: "Công việc đã dừng. Các dòng chưa gửi sẽ không được gửi nữa trừ khi bạn chạy lại.", final: true },
  completed: { label: "Hoàn tất", tone: "success", icon: CircleCheck, hint: "Đã xử lý hết danh sách.", final: true },
  invalid: { label: "Danh sách có lỗi", tone: "destructive", icon: CircleX, hint: "Danh sách có dòng sai nên chưa chạy được. Sửa danh sách rồi tạo công việc mới.", final: true },
};

export function jobStatus(status: string): StatusInfo {
  return JOB_STATUS[status] ?? { label: "Trạng thái khác", tone: "secondary", icon: CircleHelp, hint: `Trạng thái hệ thống: ${status}.` };
}

export function StatusBadge({ status }: { status: string }) {
  const s = jobStatus(status);
  const Icon = s.icon;
  return (
    <Badge variant={s.tone === "default" ? undefined : s.tone}>
      <Icon className="size-3.5 shrink-0" aria-hidden />
      {s.label}
    </Badge>
  );
}

/* ── Trạng thái từng mục (dòng trong danh sách) ────────────────────────────── */

export const ITEM_STATUS: Record<string, { label: string; tone: Tone; icon: Icon }> = {
  pending: { label: "Chờ gửi", tone: "secondary", icon: CircleDashed },
  processing: { label: "Đang gửi", tone: "default", icon: Send },
  retrying: { label: "Đang thử lại", tone: "warning", icon: RotateCw },
  success: { label: "Thành công", tone: "success", icon: CircleCheck },
  failed: { label: "Thất bại", tone: "destructive", icon: CircleX },
  unknown: { label: "Chưa rõ", tone: "warning", icon: CircleHelp },
  skipped: { label: "Bỏ qua", tone: "secondary", icon: Ban },
};

export function ItemStatusBadge({ status }: { status: string }) {
  const s = ITEM_STATUS[status] ?? { label: status, tone: "secondary" as Tone, icon: CircleHelp };
  const Icon = s.icon;
  return (
    <Badge variant={s.tone === "default" ? undefined : s.tone}>
      <Icon className="size-3.5 shrink-0" aria-hidden />
      {s.label}
    </Badge>
  );
}

/* ── Mã lỗi / lý do tạm dừng → câu giải thích ─────────────────────────────── */

/** Lý do `auto_pause` (payload.reason). Câu trả lời cho: "vì sao công việc tự tạm dừng và tôi làm gì?" */
const PAUSE_REASON: Record<string, string> = {
  ROLE_REVOKED: "Công việc tạm dừng vì quyền phân phối điểm của bạn đã bị thu hồi. Nhờ quản trị viên cấp lại quyền, sau đó bấm Tiếp tục.",
  CREDENTIAL_INVALID: "Công việc tạm dừng vì kết nối Discord không còn dùng được. Kiểm tra lại ở trang Kết nối Discord, sau đó bấm Tiếp tục.",
  NO_USABLE_TOKEN: "Công việc tạm dừng vì không còn tài khoản Discord nào dùng được. Thêm hoặc kiểm tra lại tài khoản ở trang Kết nối Discord, sau đó bấm Tiếp tục.",
  SESSION_ERROR: "Công việc tạm dừng vì không thể mở phiên làm việc với Discord. Bấm Tiếp tục để thử lại; nếu lặp lại, kiểm tra trang Kết nối Discord.",
  WORKER_ERROR: "Công việc tạm dừng vì tiến trình xử lý gặp lỗi bất ngờ. Bấm Tiếp tục để chạy lại; nếu lỗi lặp lại, hãy báo quản trị viên.",
  WORKER_CANCELLED: "Công việc tạm dừng vì tiến trình bị gián đoạn (ví dụ hệ thống vừa khởi động lại). Bấm Tiếp tục để chạy lại.",
  unknown_streak: "Công việc tạm dừng vì có quá nhiều kết quả \"chưa rõ\" liên tiếp. Hãy bấm Đối soát để kiểm tra lịch sử kênh Discord trước khi chạy tiếp.",
};

/** Mã lỗi của từng mục / mã lý do kỹ thuật khác. */
const CODE_TEXT: Record<string, string> = {
  USER_NOT_FOUND: "Không tìm thấy thành viên này trong máy chủ Discord.",
  INVALID_POINT: "Số điểm không hợp lệ.",
  BOT_REJECTED: "Bot đã từ chối lệnh cộng điểm.",
  CONFLICTING_REPLIES: "Bot trả lời mâu thuẫn nên không kết luận được.",
  BOT_REPLY_UNMATCHED: "Bot có trả lời nhưng hệ thống không nhận ra là thành công hay thất bại.",
  CONFIRM_TIMEOUT: "Bot không trả lời kịp nên chưa xác nhận được.",
  CONFIRM_DISABLED: "Đang tắt chế độ chờ bot xác nhận nên không biết kết quả.",
  NETWORK: "Lỗi mạng khi gửi lệnh.",
  SEND_RESULT_UNKNOWN: "Không biết lệnh đã được gửi đi hay chưa. Hãy đối soát để tránh cộng trùng.",
  RATE_LIMITED: "Discord đang giới hạn tốc độ gửi lệnh.",
  INVALID_COMMAND_TEXT: "Nội dung lệnh không hợp lệ.",
  AUTH_INVALID: "Discord từ chối mã đăng nhập của tài khoản này.",
  MISSING_PERMISSION: "Tài khoản Discord thiếu quyền gửi lệnh trong kênh.",
  CHANNEL_NOT_FOUND: "Không tìm thấy kênh Discord đã cấu hình.",
  ...PAUSE_REASON,
};

/** Câu giải thích cho một mã; mã lạ trả về câu chung (mã gốc nên đặt trong "Chi tiết kỹ thuật"). */
export function explainCode(code: string | null | undefined): string {
  if (!code) return "";
  return CODE_TEXT[code] ?? "Lỗi hệ thống chưa có mô tả. Xem mã trong Chi tiết kỹ thuật khi cần báo quản trị viên.";
}

export function explainPause(reason: string | null | undefined): string {
  if (reason && PAUSE_REASON[reason]) return PAUSE_REASON[reason];
  if (reason) return `Công việc tự tạm dừng. ${explainCode(reason)} Kiểm tra rồi bấm Tiếp tục.`;
  return "Công việc tự tạm dừng. Kiểm tra lại rồi bấm Tiếp tục.";
}

/* ── Thời gian ─────────────────────────────────────────────────────────────── */

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("vi-VN");
}

/** "vừa xong", "5 phút trước"… — quá 30 ngày thì ngày đầy đủ. */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "vừa xong";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} phút trước`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} giờ trước`;
  const d = Math.round(h / 24);
  if (d <= 30) return `${d} ngày trước`;
  return new Date(t).toLocaleDateString("vi-VN");
}

export function fmtNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("vi-VN");
}

/* ── Tiến độ ───────────────────────────────────────────────────────────────── */

/** Số mục "đã xong" = thành công. Mục thất bại / chưa rõ được nêu riêng bằng chữ. */
export function progressSummary(c: Pick<JobCounts, "total" | "success" | "failed" | "unknown">): string {
  const parts = [`đã xong ${fmtNumber(c.success)}/${fmtNumber(c.total)}`];
  if (c.failed > 0) parts.push(`${fmtNumber(c.failed)} thất bại`);
  if (c.unknown > 0) parts.push(`${fmtNumber(c.unknown)} chưa rõ`);
  return parts.join(" · ");
}

/** Thanh tiến độ tĩnh (không hoạt ảnh). Màu chỉ phụ trợ: ý nghĩa nằm ở chữ đi kèm. */
export function ProgressBar({ counts, label }: { counts: Pick<JobCounts, "total" | "success" | "failed" | "unknown">; label: string }) {
  const total = counts.total || 0;
  const pct = (n: number) => (total > 0 ? `${Math.min(100, (n / total) * 100)}%` : "0%");
  const done = total > 0 ? Math.round((counts.success / total) * 100) : 0;
  return (
    <div
      role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={done}
      aria-valuetext={progressSummary(counts)}
      className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-border-strong"
    >
      <div className="bg-success" style={{ width: pct(counts.success) }} />
      <div className="bg-destructive" style={{ width: pct(counts.failed) }} />
      <div className="bg-warning" style={{ width: pct(counts.unknown) }} />
    </div>
  );
}

/* ── Hộp thoại xác nhận ────────────────────────────────────────────────────── */

export interface ConfirmSpec {
  title: string;
  /** Nói rõ hậu quả. */
  body: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
}

/** Xác nhận trước thao tác không hoàn tác. Nút đầu tiên (nhận focus) là "Hủy" để tránh bấm nhầm. */
export function ConfirmDialog({ spec, onClose }: { spec: ConfirmSpec | null; onClose: () => void }) {
  return (
    <Dialog open={spec !== null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        {spec && (
          <>
            <DialogTitle>{spec.title}</DialogTitle>
            <DialogDescription className="mt-2 text-[0.9375rem]">{spec.body}</DialogDescription>
            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button variant="outline" onClick={onClose}>Hủy</Button>
              <Button
                variant={spec.destructive ? "destructive" : "default"}
                onClick={() => { const run = spec.onConfirm; onClose(); void run(); }}
              >
                {spec.confirmLabel}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

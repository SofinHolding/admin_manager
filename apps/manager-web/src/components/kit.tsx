/**
 * Bộ thành phần dùng chung của hệ thiết kế v1 (docs/architecture/ui-design-v1.md §5).
 * Quy tắc: mỗi trang có MỘT PageHeader, nội dung chia bằng Section (đường kẻ, không thẻ bo góc), trạng thái rỗng dùng EmptyState,
 * thông báo trong trang dùng Notice (luôn có biểu tượng + chữ), chi tiết kỹ thuật cho vào TechDetails.
 */

import * as React from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Info, XCircle } from "lucide-react";
import { cn } from "../lib/utils";

/* ── PageHeader ─────────────────────────────────────────────────────────────── */

export function PageHeader({
  title, description, actions, className,
}: {
  title: string;
  /** Một câu: trang này giúp làm gì. */
  description?: React.ReactNode;
  /** Hành động chính của trang (tối đa MỘT nút nổi bật). */
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3", className)}>
      <div className="min-w-0">
        <h1>{title}</h1>
        {description && <p className="reading mt-1.5 text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/* ── Section ────────────────────────────────────────────────────────────────── */

export function Section({
  title, description, actions, children, className, id,
}: {
  title?: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={title ? headingId : undefined} className={cn("border-t border-border py-6 first:border-t-0 first:pt-0", className)}>
      {(title || actions) && (
        <div className="mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {title && <h2 id={headingId}>{title}</h2>}
            {description && <p className="reading mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/* ── StatStrip ──────────────────────────────────────────────────────────────── */

export interface Stat {
  label: string;
  value: React.ReactNode;
  /** So sánh hoặc chú thích bằng lời, vd "nhiều hơn 12% so với kỳ trước". */
  note?: React.ReactNode;
  tone?: "default" | "good" | "bad";
}

/** Dải số liệu liền mạch (thay cho hàng thẻ KPI): các ô ngăn bằng đường kẻ, xuống dòng gọn trên điện thoại. */
export function StatStrip({ stats, className }: { stats: Stat[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 divide-x divide-border border-y border-border md:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]", className)}>
      {stats.map((s) => (
        <div key={s.label} className="min-w-0 px-4 py-4 first:pl-0">
          <dt className="text-sm text-muted-foreground">{s.label}</dt>
          <dd className="figure mt-1 text-[1.625rem] leading-tight">{s.value}</dd>
          {s.note && (
            <p className={cn("mt-1 text-sm", s.tone === "good" && "text-success", s.tone === "bad" && "text-destructive", (!s.tone || s.tone === "default") && "text-muted-foreground")}>
              {s.note}
            </p>
          )}
        </div>
      ))}
    </dl>
  );
}

/* ── EmptyState ─────────────────────────────────────────────────────────────── */

export function EmptyState({
  icon: Icon, title, children, action, className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  /** Nói đang thiếu gì. */
  title: string;
  /** Nói bước kế tiếp. */
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-start gap-3 rounded-lg border border-dashed border-border-strong px-5 py-8", className)}>
      {Icon && <Icon className="size-7 text-muted-foreground" aria-hidden />}
      <div>
        <p className="font-semibold">{title}</p>
        {children && <p className="reading mt-1 text-muted-foreground">{children}</p>}
      </div>
      {action}
    </div>
  );
}

/* ── Notice ─────────────────────────────────────────────────────────────────── */

type NoticeTone = "info" | "success" | "warning" | "error";

const NOTICE: Record<NoticeTone, { icon: React.ComponentType<{ className?: string }>; box: string; icon_cls: string; role: "status" | "alert" }> = {
  info: { icon: Info, box: "border-primary/40 bg-primary/8", icon_cls: "text-primary-text", role: "status" },
  success: { icon: CheckCircle2, box: "border-success/45 bg-success/8", icon_cls: "text-success", role: "status" },
  warning: { icon: AlertTriangle, box: "border-warning/50 bg-warning/10", icon_cls: "text-warning", role: "status" },
  error: { icon: XCircle, box: "border-destructive/50 bg-destructive/8", icon_cls: "text-destructive", role: "alert" },
};

/** Thông báo trong trang. Lỗi: nói chuyện gì xảy ra + cách sửa; không dựa vào màu (luôn có biểu tượng + chữ). */
export function Notice({
  tone = "info", title, children, action, className,
}: {
  tone?: NoticeTone;
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  const t = NOTICE[tone];
  const Icon = t.icon;
  return (
    <div role={t.role} className={cn("flex items-start gap-3 rounded-lg border px-4 py-3", t.box, className)}>
      <Icon className={cn("mt-0.5 size-5 shrink-0", t.icon_cls)} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cn("text-[0.9375rem]", title && "mt-0.5 text-muted-foreground")}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* ── Field ──────────────────────────────────────────────────────────────────── */

/** Nhãn + điều khiển + gợi ý + lỗi, nối `aria-describedby` đúng. `children` là hàm nhận props cần gắn vào ô nhập. */
export function Field({
  label, hint, error, optional, children, className,
}: {
  label: string;
  hint?: React.ReactNode;
  error?: string | null;
  optional?: boolean;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => React.ReactNode;
  className?: string;
}) {
  const uid = React.useId();
  const id = `f${uid.replace(/:/g, "")}`;
  const described = [hint ? `${id}-hint` : null, error ? `${id}-err` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-sm font-semibold">
        {label}
        {optional && <span className="ml-1.5 font-normal text-muted-foreground">(không bắt buộc)</span>}
      </label>
      {children({ id, "aria-describedby": described, "aria-invalid": error ? true : undefined })}
      {hint && <p id={`${id}-hint`} className="text-sm text-muted-foreground">{hint}</p>}
      {error && <p id={`${id}-err`} className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}

/* ── TechDetails ────────────────────────────────────────────────────────────── */

/** "Chi tiết kỹ thuật" gập được: id, khóa, JSON, mã lỗi — để người không rành kỹ thuật không bị rối. */
export function TechDetails({ summary = "Chi tiết kỹ thuật", children, className }: { summary?: string; children: React.ReactNode; className?: string }) {
  return (
    <details className={cn("group rounded-md border border-border", className)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-sm font-medium text-muted-foreground hover:text-foreground">
        <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden />
        {summary}
      </summary>
      <div className="border-t border-border px-3 py-3 text-sm">{children}</div>
    </details>
  );
}

/**
 * Bộ dùng chung cho các trang đăng nhập / đăng ký / tài khoản (ui-design-v1.md §5–§6).
 * Chỉ chứa phần trình bày và câu chữ; KHÔNG chứa logic xác thực (nằm ở src/auth*, src/api.ts).
 */

import * as React from "react";
import { Link } from "react-router-dom";
import { BarChart3, Eye, EyeOff, Loader2 } from "lucide-react";
import { ApiError } from "../api";
import { Input } from "./ui/input";
import { Field, PageHeader } from "./kit";
import { roleLabel } from "../nav";
import { cn } from "../lib/utils";

/* ── Quy tắc mật khẩu ───────────────────────────────────────────────────────── */

export const MIN_PASSWORD_CHARS = 8;
/** bcrypt chỉ xét 72 byte đầu (UTF-8); server trả 422 khi vượt. */
export const MAX_PASSWORD_BYTES = 72;

/** Quy tắc hiển thị TRƯỚC khi người dùng gõ. */
export const PASSWORD_RULE =
  "Ít nhất 8 ký tự, tối đa 72 byte (khoảng 70 ký tự không dấu; tiếng Việt có dấu tính nhiều byte hơn).";

export function utf8Bytes(s: string): number {
  return new TextEncoder().encode(s).length;
}

/** Lỗi độ dài mật khẩu bằng lời dễ hiểu; null nếu hợp lệ. Chỉ báo "quá ngắn" khi `strictShort` (lúc bấm gửi). */
export function passwordLengthError(pw: string, strictShort = false): string | null {
  const bytes = utf8Bytes(pw);
  if (bytes > MAX_PASSWORD_BYTES) {
    return `Mật khẩu dài ${bytes} byte, vượt giới hạn 72 byte. Hãy rút ngắn hoặc bớt chữ có dấu (mỗi chữ có dấu chiếm 2 đến 3 byte).`;
  }
  if (strictShort && pw.length < MIN_PASSWORD_CHARS) return "Mật khẩu cần ít nhất 8 ký tự.";
  return null;
}

export const PASSWORD_TOO_LONG_SERVER_TEXT =
  "Máy chủ từ chối mật khẩu vì dài quá 72 byte. Hãy dùng mật khẩu ngắn hơn hoặc bớt chữ có dấu (mỗi chữ có dấu chiếm 2 đến 3 byte).";

/* ── Lỗi API bằng lời dễ hiểu ───────────────────────────────────────────────── */

export interface FriendlyError { title: string; body: string }

function isUnavailable(e: unknown): boolean {
  return e instanceof ApiError
    && (e.code === "NETWORK" || e.code === "TIMEOUT" || e.code === "UNAVAILABLE" || e.status >= 502 && e.status <= 504);
}

const NETWORK_ERROR: FriendlyError = {
  title: "Chưa kết nối được tới máy chủ",
  body: "Kiểm tra kết nối mạng rồi thử lại. Nếu vẫn lỗi sau vài phút, hãy báo quản trị viên.",
};

/** Lỗi 422 do mật khẩu quá 72 byte (thông điệp server: "Mật khẩu quá dài (tối đa 72 byte ...)"). */
export function isPasswordTooLong(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /72|quá dài|too long/i.test(e.message);
}

/** Đăng nhập: không tiết lộ tài khoản có tồn tại hay không. */
export function loginError(e: unknown): FriendlyError {
  if (isUnavailable(e)) return NETWORK_ERROR;
  if (e instanceof ApiError) {
    if (e.status === 401 || e.code === "AUTH_INVALID") {
      return {
        title: "Tên đăng nhập hoặc mật khẩu chưa đúng",
        body: "Kiểm tra lại cả hai ô, chú ý chữ hoa, chữ thường và phím Caps Lock. Nếu quên mật khẩu, chọn “Quên mật khẩu” bên dưới.",
      };
    }
    if (e.status === 403 && /chưa xác thực/i.test(e.message)) {
      return {
        title: "Tài khoản chưa xác thực email",
        body: "Mở thư xác thực đã gửi lúc đăng ký (xem cả mục thư rác) và bấm vào liên kết trong thư. Nếu không tìm thấy thư, hãy báo quản trị viên.",
      };
    }
    if (e.status === 403 && /khoá|khóa/i.test(e.message)) {
      return {
        title: "Tài khoản đang bị khóa",
        body: "Bạn chưa thể đăng nhập. Hãy liên hệ quản trị viên để được mở khóa.",
      };
    }
    if (e.status === 429) {
      return { title: "Bạn đã thử quá nhiều lần", body: `${e.message} Sau đó thử lại.` };
    }
  }
  const message = e instanceof Error && e.message ? e.message : "";
  return { title: "Chưa đăng nhập được", body: message || "Thử lại sau ít phút. Nếu vẫn lỗi, hãy báo quản trị viên." };
}

/** Lỗi chung của biểu mẫu có mật khẩu mới (đăng ký, đặt lại, đổi mật khẩu). */
export function formError(e: unknown, fallbackTitle: string): FriendlyError {
  if (isUnavailable(e)) return NETWORK_ERROR;
  if (isPasswordTooLong(e)) return { title: "Mật khẩu quá dài", body: PASSWORD_TOO_LONG_SERVER_TEXT };
  if (e instanceof ApiError && e.status === 422) {
    return { title: fallbackTitle, body: "Một số thông tin chưa hợp lệ. Kiểm tra lại các ô đã điền rồi thử lại." };
  }
  const message = e instanceof Error && e.message ? e.message : "";
  return { title: fallbackTitle, body: message || "Thử lại sau ít phút. Nếu vẫn lỗi, hãy báo quản trị viên." };
}

/* ── Vai trò: mỗi vai trò làm được gì (một câu) ─────────────────────────────── */

const ROLE_ABOUT: Record<string, string> = {
  admin: "Xem mọi dữ liệu, tạo mã mời, cấp quyền và khóa hoặc mở khóa tài khoản.",
  dash_admin: "Xem mọi dữ liệu và quản lý người dùng, trừ việc khóa, sửa hay xóa quản trị viên.",
  viewer: "Xem thống kê các kênh của mình, quản lý nhóm kênh và nguồn dữ liệu của mình.",
  discord: "Kết nối Discord và theo dõi công việc phân phối điểm.",
};

export function roleAbout(role: string | undefined): string {
  return (role && ROLE_ABOUT[role]) || "Dùng các tính năng mà quản trị viên đã cấp cho bạn.";
}

export { roleLabel };

/* ── Khung trang công khai ──────────────────────────────────────────────────── */

/** Một cột giữa, rộng tối đa 28rem, không thanh bên. Tên sản phẩm + một câu nói trang để làm gì. */
export function AuthShell({
  title, description, children, footer,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  React.useEffect(() => {
    const prev = document.title;
    document.title = `${title} - Manager`;
    return () => { document.title = prev; };
  }, [title]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[28rem] flex-col justify-center px-4 py-8">
      <p className="mb-6 flex items-center gap-2 text-lg font-bold">
        <span className="grid size-9 place-items-center rounded-md bg-primary text-primary-foreground">
          <BarChart3 className="size-4" aria-hidden />
        </span>
        Manager
      </p>
      <PageHeader title={title} description={description} className="mb-5" />
      <div className="border border-border-strong bg-surface-1 p-5 sm:p-6">{children}</div>
      {footer && <div className="mt-5 text-[0.9375rem] text-muted-foreground">{footer}</div>}
    </main>
  );
}

/** Liên kết văn bản đủ lớn để chạm (>= 44px). */
export function TextLink({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) {
  return (
    <Link to={to} className={cn("inline-flex min-h-11 items-center font-semibold text-primary-text underline underline-offset-4 hover:no-underline", className)}>
      {children}
    </Link>
  );
}

/** Biểu tượng quay khi đang xử lý; dừng quay nếu người dùng chọn giảm chuyển động (chữ đi kèm vẫn cho biết trạng thái). */
export function Spinner() {
  return <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />;
}

/* ── Ô mật khẩu có nút hiện/ẩn ──────────────────────────────────────────────── */

export function PasswordField({
  label, value, onChange, autoComplete, hint, error, autoFocus, noun = "mật khẩu", name,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: "current-password" | "new-password";
  hint?: React.ReactNode;
  error?: string | null;
  autoFocus?: boolean;
  /** Dùng trong nhãn nút hiện/ẩn, vd "mật khẩu mới" -> "Hiện mật khẩu mới". */
  noun?: string;
  name?: string;
}) {
  const [show, setShow] = React.useState(false);
  return (
    <Field label={label} hint={hint} error={error}>
      {(p) => (
        <div className="relative">
          <Input
            {...p}
            name={name}
            type={show ? "text" : "password"}
            autoComplete={autoComplete}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="pr-12"
            autoFocus={autoFocus}
            autoCapitalize="none"
            spellCheck={false}
          />
          <button
            type="button"
            onClick={() => setShow((s) => !s)}
            aria-label={`${show ? "Ẩn" : "Hiện"} ${noun}`}
            aria-pressed={show}
            className="absolute right-0 top-0 grid size-11 place-items-center rounded-md text-muted-foreground hover:text-foreground"
          >
            {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
          </button>
        </div>
      )}
    </Field>
  );
}

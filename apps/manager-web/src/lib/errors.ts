/**
 * Lỗi API thống nhất.
 *
 * Backend có HAI kiểu thân lỗi và UI phải xử lý cả hai:
 *  - envelope mới:  {"ok": false, "error": {"code": "...", "message": "...", "details": ...}, "meta": {"request_id": "..."}}
 *  - legacy:        {"detail": "thông điệp"}  hoặc  {"detail": [{"loc": [...], "msg": "..."}]}  (FastAPI validation)
 *
 * Quy tắc: KHÔNG phản chiếu giá trị người dùng nhập vào thông điệp (chỉ lấy `msg`, bỏ `input`/`ctx`).
 */

export type ApiErrorCode =
  | "NETWORK"          // không tới được máy chủ (offline, DNS, CORS, gateway chết)
  | "TIMEOUT"          // quá thời gian chờ phía client
  | "AUTH_REVOKED"     // phiên bị thu hồi (đăng xuất ở nơi khác, đổi mật khẩu, khoá tài khoản)
  | "AUTH_INVALID"
  | "UNAVAILABLE"      // 503 — dịch vụ đích không khả dụng
  | (string & {});

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details?: unknown;
  readonly requestId?: string;

  constructor(message: string, opts: { status?: number; code?: ApiErrorCode; details?: unknown; requestId?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status ?? 0;
    this.code = opts.code ?? "";
    this.details = opts.details;
    this.requestId = opts.requestId;
  }
}

/** Lỗi do chính caller chủ động huỷ (AbortController) — KHÔNG phải lỗi cần báo người dùng. */
export function isAbortError(e: unknown): boolean {
  return (
    (e instanceof DOMException && e.name === "AbortError") ||
    (typeof e === "object" && e !== null && (e as { name?: string }).name === "AbortError")
  );
}

/** Dịch vụ không khả dụng (503/502/504, mạng, timeout): UI giữ dữ liệu cuối và hiện trạng thái rõ ràng. */
export function isUnavailableError(e: unknown): boolean {
  if (!(e instanceof ApiError)) return false;
  return e.code === "NETWORK" || e.code === "TIMEOUT" || e.code === "UNAVAILABLE"
    || e.status === 502 || e.status === 503 || e.status === 504;
}

export function errorMessage(e: unknown, fallback = "Đã xảy ra lỗi"): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

type Json = Record<string, unknown>;

function asRecord(v: unknown): Json | null {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Json) : null;
}

function detailToText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic: [{loc, msg, type, input?}] — chỉ lấy msg, KHÔNG lấy input.
    const msgs = detail
      .map((d) => asRecord(d)?.msg)
      .filter((m): m is string => typeof m === "string" && m.length > 0);
    if (msgs.length) return msgs.join("; ");
  }
  const rec = asRecord(detail);
  if (rec && typeof rec.message === "string") return rec.message;
  return "";
}

/** Dựng ApiError từ status + thân phản hồi đã parse (hoặc null nếu không phải JSON). */
export function errorFromResponse(status: number, body: unknown, fallbackText?: string): ApiError {
  const root = asRecord(body);
  const env = asRecord(root?.error);
  const meta = asRecord(root?.meta);
  const requestId = typeof meta?.request_id === "string" ? meta.request_id : undefined;

  let code = "";
  let message = "";
  let details: unknown;

  if (env) {
    // Envelope mới
    code = typeof env.code === "string" ? env.code : "";
    message = typeof env.message === "string" ? env.message : "";
    details = env.details;
  } else if (root) {
    // Legacy {"detail": ...} (+ {"message": ...} của vài endpoint cũ)
    message = detailToText(root.detail) || (typeof root.message === "string" ? root.message : "");
    if (typeof root.code === "string") code = root.code;
  }

  // Mã suy từ thông điệp/ trạng thái khi server chưa gửi `code` (legacy)
  if (!code) {
    if (status === 401 && /thu hồi/i.test(message)) code = "AUTH_REVOKED";
    else if (status === 401) code = "AUTH_INVALID";
    else if (status === 503) code = "UNAVAILABLE";
  }

  if (!message) {
    if (status === 403) message = "Không có quyền";
    else if (status === 503) message = "Dịch vụ tạm thời không khả dụng";
    else message = fallbackText || `Lỗi ${status}`;
  }

  return new ApiError(message, { status, code, details, requestId });
}

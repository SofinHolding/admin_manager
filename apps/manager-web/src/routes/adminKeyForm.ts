/**
 * Helpers thuần cho trang Quản trị: lỗi thao tác quản trị -> lời dễ hiểu, validate tham số mã mời,
 * danh sách scope cho wizard công cụ dùng chung, nhãn capability cho PermissionsDialog.
 */

import {
  CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT,
  CAP_TOOL_INTEGRATION, CAP_API_READ, CAP_API_WRITE, CAP_MULTI_TOOL,
} from "../auth/permissions";
import { ApiError, errorMessage } from "../lib/errors";

/** Scope của client dùng chung (integration-api-v1 §2). `jobs:claim:system` chỉ dành cho `fallback_tool`. */
export const SHARED_SCOPE_OPTIONS = [
  "integration:handshake", "resources:write", "snapshots:write", "metrics:write",
  "jobs:claim", "jobs:complete", "jobs:claim:system",
] as const;

/** Tên dễ hiểu của từng scope (mã gốc vẫn hiện kèm trong ngoặc ở giao diện). */
export const SCOPE_LABEL: Record<string, string> = {
  "integration:handshake": "Kết nối ban đầu",
  "resources:write": "Ghi danh sách kênh",
  "snapshots:write": "Ghi bản chụp dữ liệu",
  "metrics:write": "Ghi số liệu",
  "jobs:claim": "Nhận công việc",
  "jobs:complete": "Báo hoàn thành công việc",
  "jobs:claim:system": "Nhận công việc hệ thống (mọi người dùng)",
};

/** Lý do hiển thị khi dash_admin không được thao tác một tài khoản quản trị viên (server trả 403 ADMIN_TARGET_FORBIDDEN). */
export const ADMIN_ONLY_REASON = "Chỉ quản trị viên mới thao tác được tài khoản này";

/** Lỗi thao tác quản trị -> câu dễ hiểu. 403 ADMIN_TARGET_FORBIDDEN nói rõ ai làm được; không phản chiếu giá trị nhập. */
export function adminErrorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.code === "ADMIN_TARGET_FORBIDDEN") return `${ADMIN_ONLY_REASON}. Quản lý Dashboard không khóa, sửa hay xóa được tài khoản quản trị viên.`;
    if (e.status === 403) return "Bạn không có quyền thực hiện thao tác này.";
  }
  return errorMessage(e, fallback);
}

// ── Capability model (đồng bộ capabilities.py) — dùng cho PermissionsDialog ─

/** Tất cả 7 capability theo thứ tự hiển thị. */
export const ALL_CAPS = [
  CAP_WEB_ACCESS, CAP_DASHBOARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT,
  CAP_TOOL_INTEGRATION, CAP_API_READ, CAP_API_WRITE, CAP_MULTI_TOOL,
] as const;

/** Nhãn tiếng Việt cho từng capability (đồng bộ ALL_CAPABILITIES_DESC). */
export const CAP_LABEL: Record<string, string> = {
  [CAP_WEB_ACCESS]:          "Đăng nhập website",
  [CAP_DASHBOARD_ACCESS]:    "Xem Dashboard thống kê",
  [CAP_MANUAL_CHANNEL_MGMT]: "Tự quản lý kênh",
  [CAP_TOOL_INTEGRATION]:    "Liên kết Tool cá nhân",
  [CAP_API_READ]:            "Credential đọc dữ liệu",
  [CAP_API_WRITE]:           "Credential gửi snapshot/metrics",
  [CAP_MULTI_TOOL]:          "Tạo nhiều integration client",
};

/** Validate max_uses và expires_days trước khi gọi API. Trả về null nếu hợp lệ, message nếu lỗi. */
export function validateKeyParams(maxUses: string, expiresDays: string): string | null {
  const maxU = Number(maxUses);
  if (!Number.isInteger(maxU) || maxU < 1 || maxU > 1000) return "Số lần dùng phải từ 1 đến 1000";
  const expD = Number(expiresDays);
  if (!Number.isInteger(expD) || expD < 1 || expD > 365) return "Thời hạn phải từ 1 đến 365 ngày";
  return null;
}

export function parseList(raw: string): string[] {
  return Array.from(new Set(raw.split(/[\s,;]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)));
}

export const TOKEN_RE = /^[a-z0-9][a-z0-9_.:-]{0,63}$/;

/**
 * Dựng phần mở rộng của body `POST /v1/admin/keys`: `integration_policy` + `allowed_apps`
 * (docs/architecture/architecture-assessment-v2.md §2.3 và §2.9b). Tách thuần để test.
 */

import type { CreateKeyExtra } from "../api";
import { ApiError, errorMessage } from "../lib/errors";

/** Scope mặc định của client thường (integration-api-v1 §2). `jobs:claim:system` do admin cấp riêng qua PATCH client. */
export const POLICY_SCOPE_OPTIONS = [
  "integration:handshake", "resources:write", "snapshots:write", "metrics:write", "jobs:claim", "jobs:complete",
] as const;

/** Tên dễ hiểu của từng scope (mã gốc vẫn hiện kèm trong ngoặc ở giao diện). */
export const POLICY_SCOPE_LABEL: Record<string, string> = {
  "integration:handshake": "Kết nối ban đầu",
  "resources:write": "Ghi danh sách kênh",
  "snapshots:write": "Ghi bản chụp dữ liệu",
  "metrics:write": "Ghi số liệu",
  "jobs:claim": "Nhận công việc",
  "jobs:complete": "Báo hoàn thành công việc",
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

export const DEFAULT_POLICY_SCOPES: readonly string[] = [
  "integration:handshake", "resources:write", "snapshots:write", "metrics:write",
];

export interface KeyExtraForm {
  allowIntegration: boolean;
  platforms: string;
  scopes: readonly string[];
  maxClients: string;
  apps: string;
  /** `capability:*` được chọn cấp thêm lúc đăng ký bằng mã này (docs/architecture/capability-model-v1.md §4). */
  capabilities?: readonly string[];
}

export function parseList(raw: string): string[] {
  return Array.from(new Set(raw.split(/[\s,;]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)));
}

const TOKEN_RE = /^[a-z0-9][a-z0-9_.:-]{0,63}$/;

export type KeyExtraResult = { ok: true; extra: CreateKeyExtra } | { ok: false; error: string };

export function buildKeyExtra(f: KeyExtraForm): KeyExtraResult {
  const extra: CreateKeyExtra = {};
  const apps = parseList(f.apps);
  const badApp = apps.find((a) => !TOKEN_RE.test(a));
  if (badApp) return { ok: false, error: "Tên ứng dụng chỉ gồm chữ thường, số, dấu - và _" };
  if (apps.length) extra.allowed_apps = apps;
  if (f.capabilities?.length) extra.capability_grant = { capabilities: [...f.capabilities] };

  if (f.allowIntegration) {
    const platforms = parseList(f.platforms);
    if (!platforms.length) return { ok: false, error: "Nhập ít nhất một nền tảng được phép" };
    const badPlat = platforms.find((p) => !TOKEN_RE.test(p));
    if (badPlat) return { ok: false, error: "Tên nền tảng chỉ gồm chữ thường, số, dấu - và _" };
    const scopes = f.scopes.filter((s) => (POLICY_SCOPE_OPTIONS as readonly string[]).includes(s));
    if (!scopes.length) return { ok: false, error: "Chọn ít nhất một quyền cho công cụ" };
    const n = Number(f.maxClients);
    if (!Number.isInteger(n) || n < 1 || n > 100) return { ok: false, error: "Số công cụ tối đa phải từ 1 đến 100" };
    extra.integration_policy = {
      allow_integration_registration: true,
      allowed_platforms: platforms,
      allowed_scopes: scopes,
      max_integration_clients: n,
    };
  }
  return { ok: true, extra };
}

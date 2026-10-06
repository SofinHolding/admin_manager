/**
 * Dựng phần mở rộng của body `POST /v1/admin/keys`: `integration_policy` + `allowed_apps`
 * (docs/architecture/architecture-assessment-v2.md §2.3 và §2.9b). Tách thuần để test.
 */

import type { CreateKeyExtra } from "../api";

/** Scope mặc định của client thường (integration-api-v1 §2). `jobs:claim:system` do admin cấp riêng qua PATCH client. */
export const POLICY_SCOPE_OPTIONS = [
  "integration:handshake", "resources:write", "snapshots:write", "metrics:write", "jobs:claim", "jobs:complete",
] as const;

export const DEFAULT_POLICY_SCOPES: readonly string[] = [
  "integration:handshake", "resources:write", "snapshots:write", "metrics:write",
];

export interface KeyExtraForm {
  allowIntegration: boolean;
  platforms: string;
  scopes: readonly string[];
  maxClients: string;
  apps: string;
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
  if (badApp) return { ok: false, error: "Tên app chỉ gồm chữ thường, số, '-', '_'" };
  if (apps.length) extra.allowed_apps = apps;

  if (f.allowIntegration) {
    const platforms = parseList(f.platforms);
    if (!platforms.length) return { ok: false, error: "Chọn ít nhất một nền tảng cho phép đăng ký Tool" };
    const badPlat = platforms.find((p) => !TOKEN_RE.test(p));
    if (badPlat) return { ok: false, error: "Tên nền tảng không hợp lệ" };
    const scopes = f.scopes.filter((s) => (POLICY_SCOPE_OPTIONS as readonly string[]).includes(s));
    if (!scopes.length) return { ok: false, error: "Chọn ít nhất một scope" };
    const n = Number(f.maxClients);
    if (!Number.isInteger(n) || n < 1 || n > 100) return { ok: false, error: "Số client tối đa phải từ 1 đến 100" };
    extra.integration_policy = {
      allow_integration_registration: true,
      allowed_platforms: platforms,
      allowed_scopes: scopes,
      max_integration_clients: n,
    };
  }
  return { ok: true, extra };
}

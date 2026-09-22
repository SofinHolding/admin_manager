/**
 * API client cho module reward (discord) — TÁI SỬ DỤNG token + refresh của admin-web.
 *
 * Không tự quản token riêng: mọi request đi qua `apiFetchRaw` của `../api` (đã gắn Authorization +
 * tự refresh 401), chỉ khác là gọi đường dẫn tuyệt đối `/v1/reward/...` (reward-service :8422 qua
 * proxy) thay vì `/v1/...` (admin-server :8421). JWT dùng chung nên cùng một access token.
 */

import { apiFetchRaw, getAccessToken } from "../api";

export { getAccessToken };

const BASE = "/v1/reward";

/** Gọi JSON: gắn Content-Type, ném lỗi có `detail` giống admin-web, chịu được 204. */
async function rj<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith("/") ? path : `${BASE}/${path}`;
  const r = await apiFetchRaw(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers as Record<string, string> || {}) },
  });
  if (r.status === 403) {
    const d = await r.json().catch(() => ({ detail: "Không có quyền" }));
    throw new Error(d.detail || "Không có quyền");
  }
  if (!r.ok) {
    const d = await r.json().catch(() => ({ detail: `Lỗi ${r.status}` }));
    throw new Error(d.detail || d.message || `Lỗi ${r.status}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

// ── Types ────────────────────────────────────────────────────────────────

export interface RewardUser {
  id: string;
  username: string;
  role: string;
  token_role: string;
  status: string;
  reward_access: boolean;
  has_credential: boolean;
  credential_status: string;
  discord_username: string;
}

export interface CredentialInfo {
  exists: boolean;
  status: "valid" | "invalid" | "unverified" | "revoked" | string;
  discord_user_id: string;
  discord_username: string;
  guild_id: string;
  channel_id: string;
  command_name: string;
  confirm_mode: string;
  success_pattern: string;
  failure_pattern: string;
  leveling_bot_id: string;
  delay_ms: number;
  jitter_ms: number;
  verified_at: string;
  last_error: string;
}

export interface CredentialPutBody {
  token?: string;
  guild_id: string;
  channel_id: string;
  command_name: string;
  confirm_mode: string;
  success_pattern?: string;
  failure_pattern?: string;
  leveling_bot_id?: string;
  delay_ms?: number;
  jitter_ms?: number;
}

export interface CommandMeta {
  application_id: string;
  command_id: string;
  version: string;
  member_option_name: string;
  member_option_type: number;
  amount_option_name: string;
  amount_option_type: number;
}

export interface CredentialResult {
  ok: boolean;
  status: string;
  discord_username: string;
  command: CommandMeta;
}

export interface ValidationIssue {
  row_index: number;
  severity: "error" | "warning" | string;
  code: string;
  message: string;
}

export interface JobOverrides {
  guild_id?: string;
  channel_id?: string;
  command_name?: string;
  confirm_mode?: string;
  success_pattern?: string;
  failure_pattern?: string;
  leveling_bot_id?: string;
  delay_ms?: number;
  jitter_ms?: number;
  max_item_retries?: number;
  unknown_pause_threshold?: number;
}

export interface JobCounts {
  total: number;
  pending: number;
  processing: number;
  retrying: number;
  success: number;
  failed: number;
  unknown: number;
  skipped: number;
  total_points?: number;
}

export interface JobSummary {
  id: string;
  name: string;
  status: string;
  total_items: number;
  counts: JobCounts;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  owner_username?: string;
}

export interface JobDetail extends JobSummary {
  issues: ValidationIssue[];
}

export interface JobItem {
  id: string;
  row_index: number;
  raw_username: string;
  resolved_user_id: string | null;
  point: number;
  status: string;
  resolve_level: string | null;
  confirmation_level: string | null;
  failure_code: string | null;
  failure_message: string | null;
  attempt_count: number;
  first_sent_at: string | null;
  finalized_at: string | null;
}

export interface JobAttempt {
  id: string;
  attempt_no: number;
  nonce: string;
  http_status: number | null;
  reply_excerpt: string | null;
  link_mode: string | null;
  created_at: string;
}

export interface JobEvent {
  id: number;
  kind: string;
  message: string;
  created_at: string;
}

export interface AdminAccount {
  id: string;
  username: string;
  email: string;
  role: string;
  status: string;
  created_at: string;
  last_login: string;
  has_credential: boolean;
  credential_status: string;
  jobs_total: number;
  jobs_running: number;
}

export interface RoleAuditEntry {
  id: number;
  account_id: string;
  username: string;
  from_role: string;
  to_role: string;
  reason: string;
  actor_username: string;
  created_at: string;
}

export interface RunnerLock {
  job_id: string;
  account_id: string;
  channel_id: string;
  pid: number;
  host: string;
  heartbeat_at: string;
  age_s: number;
}

// ── Credentials API (role discord | admin) ──────────────────────────────────

export const credentialsApi = {
  get: () => rj<CredentialInfo>("credentials"),
  put: (body: CredentialPutBody) => rj<CredentialResult>("credentials", { method: "PUT", body: JSON.stringify(body) }),
  verify: () => rj<CredentialResult>("credentials/verify", { method: "POST" }),
  delete: () => rj<{ ok: boolean }>("credentials", { method: "DELETE" }),
};

// ── Jobs API ─────────────────────────────────────────────────────────────

export const jobsApi = {
  create: (body: { name: string; raw_text?: string; items?: { username: string; point: number }[]; overrides?: JobOverrides }) =>
    rj<{ job_id: string; status: string; total_items: number; issues: ValidationIssue[]; counts: JobCounts }>(
      "jobs", { method: "POST", body: JSON.stringify(body) },
    ),

  list: (params: { status?: string; limit?: number; offset?: number; account_id?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    if (params.account_id) q.set("account_id", params.account_id);
    const qs = q.toString();
    return rj<{ jobs: JobSummary[]; total: number }>(`jobs${qs ? `?${qs}` : ""}`);
  },

  get: (id: string) => rj<JobDetail>(`jobs/${id}`),

  items: (id: string, params: { status?: string; limit?: number; offset?: number; q?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    if (params.q) q.set("q", params.q);
    const qs = q.toString();
    return rj<{ items: JobItem[]; total: number }>(`jobs/${id}/items${qs ? `?${qs}` : ""}`);
  },

  attempts: (id: string, itemId: string) => rj<{ attempts: JobAttempt[] }>(`jobs/${id}/items/${itemId}/attempts`),

  events: (id: string, afterId?: number) =>
    rj<{ events: JobEvent[] }>(`jobs/${id}/events${afterId != null ? `?after_id=${afterId}` : ""}`),

  validate: (id: string) =>
    rj<{ status: string; issues: ValidationIssue[]; counts: JobCounts }>(`jobs/${id}/validate`, { method: "POST" }),

  run: (id: string) => rj<{ status: string }>(`jobs/${id}/run`, { method: "POST", body: "{}" }),
  pause: (id: string) => rj<{ status: string }>(`jobs/${id}/pause`, { method: "POST" }),
  resume: (id: string) => rj<{ status: string }>(`jobs/${id}/resume`, { method: "POST" }),
  stop: (id: string) => rj<{ status: string }>(`jobs/${id}/stop`, { method: "POST" }),

  resolveItem: (id: string, itemId: string, to: "success" | "pending", note?: string) =>
    rj<{ ok: boolean; status: string }>(`jobs/${id}/items/${itemId}/resolve`, {
      method: "POST", body: JSON.stringify({ to, note }),
    }),

  delete: (id: string) => rj<{ ok: boolean }>(`jobs/${id}`, { method: "DELETE" }),

  exportCsvUrl: (id: string) => `${BASE}/jobs/${id}/export.csv`,

  exportCsv: async (id: string): Promise<Blob> => {
    const r = await apiFetchRaw(`${BASE}/jobs/${id}/export.csv`);
    if (!r.ok) throw new Error(`Lỗi ${r.status}`);
    return r.blob();
  },

  exportXlsxUrl: (id: string) => `${BASE}/jobs/${id}/export.xlsx`,

  exportXlsx: async (id: string): Promise<Blob> => {
    const r = await apiFetchRaw(`${BASE}/jobs/${id}/export.xlsx`);
    if (!r.ok) throw new Error(`Lỗi ${r.status}`);
    return r.blob();
  },

  streamUrl: (id: string) => `${BASE}/jobs/${id}/stream`,
};

// ── Admin API (role admin) ───────────────────────────────────────────────

export const adminApi = {
  listAccounts: (params: { q?: string; role?: string; status?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.q) q.set("q", params.q);
    if (params.role) q.set("role", params.role);
    if (params.status) q.set("status", params.status);
    const qs = q.toString();
    return rj<{ accounts: AdminAccount[] }>(`admin/accounts${qs ? `?${qs}` : ""}`);
  },

  grantDiscord: (id: string, reason?: string) =>
    rj<{ ok: boolean; username: string; from_role: string; to_role: string }>(
      `admin/accounts/${id}/grant-discord`, { method: "POST", body: JSON.stringify({ reason }) },
    ),

  revokeDiscord: (id: string, reason?: string) =>
    rj<{ ok: boolean; to_role: string; paused_jobs: string[] }>(
      `admin/accounts/${id}/revoke-discord`, { method: "POST", body: JSON.stringify({ reason }) },
    ),

  roleAudit: (params: { account_id?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.account_id) q.set("account_id", params.account_id);
    if (params.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return rj<{ entries: RoleAuditEntry[] }>(`admin/role-audit${qs ? `?${qs}` : ""}`);
  },

  jobs: (params: { status?: string; account_id?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.account_id) q.set("account_id", params.account_id);
    const qs = q.toString();
    return rj<{ jobs: JobSummary[]; total: number }>(`admin/jobs${qs ? `?${qs}` : ""}`);
  },

  locks: () => rj<{ locks: RunnerLock[] }>("admin/locks"),

  releaseLock: (jobId: string) => rj<{ ok: boolean }>(`admin/locks/${jobId}`, { method: "DELETE" }),
};

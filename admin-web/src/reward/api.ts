/**
 * API client cho module reward (Discord) — TÁI SỬ DỤNG token + refresh của admin-web.
 *
 * Mọi request đi qua `apiFetchRaw` của `../api` (Bearer + refresh 401 + timeout), gọi `/v1/reward/...` CÙNG ORIGIN
 * (API Gateway định tuyến tới reward-service). Quyền vào module do claim `perms` (`reward:access` / `reward:admin`)
 * của access token quyết định — KHÔNG còn dựa vào role `discord`. Cấp/thu quyền làm ở Identity
 * (`permissionsApi` trong ../api); reward-service không còn tự sửa role tài khoản.
 */

import { apiFetchRaw, getAccessToken } from "../api";
import { errorFromResponse } from "../lib/errors";

export { getAccessToken };

const BASE = "/v1/reward";

/** Gọi JSON: gắn Content-Type, ném `ApiError` (envelope mới hoặc `{detail}` legacy), chịu được 204. */
async function rj<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith("/") ? path : `${BASE}/${path}`;
  const r = await apiFetchRaw(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers as Record<string, string> || {}) },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw errorFromResponse(r.status, body);
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

export interface DiscordToken {
  id: number;
  label: string;
  discord_user_id: string | null;
  discord_username: string | null;
  status: "valid" | "invalid" | "unverified" | string;
  enabled: boolean;
  last_error: string | null;
  verified_at: string | null;
  last_used_at: string | null;
}

export interface CredentialInfo {
  exists: boolean;
  status: "valid" | "invalid" | "unverified" | "revoked" | string;
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
  tokens: DiscordToken[];
}

export interface CredentialPutBody {
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
  last_error: string | null;
  command: CommandMeta | null;
  tokens: DiscordToken[];
}

export interface TokenResult {
  ok: boolean;
  token: DiscordToken;
  command: CommandMeta | null;
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

export interface ReconcileSummary {
  checked: number;
  success: number;
  failed: number;
  no_reply: number;
  waiting: number;
  inconclusive: number;
}

export interface ReconcileResult {
  summary: ReconcileSummary;
  counts: JobCounts;
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
  addToken: (body: { token: string; label?: string }) =>
    rj<TokenResult>("credentials/tokens", { method: "POST", body: JSON.stringify(body) }),
  patchToken: (id: number, body: { label?: string; enabled?: boolean }) =>
    rj<TokenResult>(`credentials/tokens/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  verifyToken: (id: number) => rj<TokenResult>(`credentials/tokens/${id}/verify`, { method: "POST" }),
  deleteToken: (id: number) => rj<{ ok: boolean }>(`credentials/tokens/${id}`, { method: "DELETE" }),
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

  /** Đối soát item `unknown` với lịch sử kênh Discord: reply thật tìm thấy ⇒ success (không gửi lại). */
  reconcile: (id: string) => rj<ReconcileResult>(`jobs/${id}/reconcile`, { method: "POST" }),
  /** Đối soát rồi mở lại (pending) các item đã CHỨNG MINH không có reply — để Tiếp tục gửi lại an toàn. */
  retryUnknown: (id: string) => rj<ReconcileResult & { requeued: number; status: string }>(
    `jobs/${id}/retry-unknown`, { method: "POST" },
  ),

  resolveItem: (id: string, itemId: string, to: "success" | "pending", note?: string) =>
    rj<{ ok: boolean; status: string }>(`jobs/${id}/items/${itemId}/resolve`, {
      method: "POST", body: JSON.stringify({ to, note }),
    }),

  delete: (id: string) => rj<{ ok: boolean }>(`jobs/${id}`, { method: "DELETE" }),

  exportCsvUrl: (id: string) => `${BASE}/jobs/${id}/export.csv`,

  exportCsv: async (id: string): Promise<Blob> => {
    const r = await apiFetchRaw(`${BASE}/jobs/${id}/export.csv`);
    if (!r.ok) throw errorFromResponse(r.status, await r.json().catch(() => null));
    return r.blob();
  },

  exportXlsxUrl: (id: string) => `${BASE}/jobs/${id}/export.xlsx`,

  exportXlsx: async (id: string): Promise<Blob> => {
    const r = await apiFetchRaw(`${BASE}/jobs/${id}/export.xlsx`);
    if (!r.ok) throw errorFromResponse(r.status, await r.json().catch(() => null));
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

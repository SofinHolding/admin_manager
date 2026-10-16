/**
 * API client cho admin web — MỌI lời gọi đi qua API Gateway cùng origin (`/v1/...`).
 *
 * Lõi (Bearer, refresh 401, timeout, huỷ request, lỗi envelope/legacy) nằm ở `lib/http.ts`;
 * kho token (access trong bộ nhớ, refresh trong sessionStorage/bộ nhớ) ở `auth/tokenStore.ts`.
 * File này chỉ khai báo kiểu + hàm gọi endpoint theo hợp đồng trong `docs/api/*`.
 *
 * Phân vùng endpoint:
 *  - Identity:     /v1/auth/*, /v1/admin/keys (+ /presets, PATCH), /v1/admin/users, /v1/admin/permissions,
 *                  /v1/admin/accounts/{id}/permissions/{perm}, /v1/accounts/{id}/permissions
 *  - Analytics:    /v1/analytics/*   (alias legacy /v1/viewer/* KHÔNG còn dùng cho dữ liệu)
 *  - Integration:  /v1/integrations/clients[...], /v1/integrations/me/*  (mặt phẳng USER — token user, không bao giờ API key máy)
 *  - Legacy-compat (giữ đường dẫn): /v1/resources/*, /v1/viewer/links* (pairing, deprecated)
 */

import { apiFetch, type RequestOpts } from "./lib/http";
import {
  countryCatalogParams, filterToQuery, platformTagCatalogParams, type FilterState,
} from "./filters/filterState";

export {
  clearTokens, getAccessToken, getRefreshToken, hasAccessToken, setRefreshToken, setTokens,
} from "./auth/tokenStore";
export { apiFetch, apiFetchRaw, apiFetchStream, onSessionExpired, setRefreshStrategy } from "./lib/http";
export { ApiError, errorMessage, isAbortError, isUnavailableError } from "./lib/errors";

type Opts = Pick<RequestOpts, "signal" | "timeoutMs">;

const enc = encodeURIComponent;
const qs = (q: URLSearchParams) => (q.toString() ? `?${q}` : "");
const json = (method: string, body?: unknown): RequestInit => ({
  method, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

// ── Auth API (Identity) ─────────────────────────────────────────────────────

export interface AuthUserDto {
  id: string; username: string; email: string; role: string;
}

export interface ValidateKeyResult {
  valid: boolean; role?: string; reason?: string;
  allowed_apps?: string[];
}

const PUB: RequestOpts = { public: true };

export const authApi = {
  validateKey: (key: string) =>
    apiFetch<ValidateKeyResult>("/v1/auth/validate-key", json("POST", { key }), PUB),

  checkUsername: (username: string) =>
    apiFetch<{ available: boolean }>("/v1/auth/check-username", json("POST", { username }), PUB),

  checkEmail: (email: string) =>
    apiFetch<{ available: boolean }>("/v1/auth/check-email", json("POST", { email }), PUB),

  register: (key: string, username: string, password: string, email: string) =>
    apiFetch<{ ok: boolean; email: string; message: string }>(
      "/v1/auth/register",
      json("POST", { key, username, password, email }),
      PUB,
    ),

  verifyEmail: (token: string) =>
    apiFetch<{ ok: boolean; message: string }>(`/v1/auth/verify-email?token=${enc(token)}`, undefined, PUB),

  resendVerify: (email: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/resend-verify", json("POST", { email }), PUB),

  login: (username: string, password: string) =>
    apiFetch<{ access_token: string; refresh_token: string; expires_in: number; user: AuthUserDto }>(
      "/v1/auth/login", json("POST", { username, password }), PUB,
    ),

  refresh: (refresh_token: string) =>
    apiFetch<{ access_token: string; refresh_token?: string; expires_in: number }>(
      "/v1/auth/refresh", json("POST", { refresh_token }), PUB,
    ),

  logout: (refresh_token: string) =>
    apiFetch("/v1/auth/logout", json("POST", { refresh_token }), PUB),

  me: (o?: Opts) =>
    apiFetch<AuthUserDto & { created_at: string }>("/v1/auth/me", undefined, o),

  forgotPassword: (email: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/forgot-password", json("POST", { email }), PUB),

  resetPassword: (token: string, new_password: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/reset-password", json("POST", { token, new_password }), PUB),
};

// ── Analytics: kiểu ─────────────────────────────────────────────────────────

export type Platform = "youtube" | "facebook";
export type TrafficMetricKey = "views" | "interactions" | "comments" | "videos";

/** Độ tươi dữ liệu — docs/api/analytics-api-v1.md §2. */
export interface Freshness {
  as_of?: string | null;
  committed_at?: string | null;
  snapshot_id?: string | null;
  revision?: number | null;
  lag_seconds?: number | null;
  stale?: boolean;
}

/** Bộ lọc server THỰC SỰ áp dụng (đã ép quyền, vd viewer luôn bị ép owner = chính mình). */
export type AppliedFilter = Partial<FilterState>;

export interface AnalyticsMeta {
  filter?: AppliedFilter;
  freshness?: Freshness;
}

export interface AnalyticsSummary extends AnalyticsMeta {
  total_views: number;
  total_views_prev: number;
  videos_posted: number;
  videos_posted_prev: number;
  active_channels: number;
  managed_channels: number;
  days_in_range: number;
  linked?: boolean;
  last_synced_at?: string | null;
}

export interface AnalyticsMetric {
  key: string;
  label: string;
  ready: boolean;
  note: string;
}

export interface TrafficMetricPoint {
  date: string;
  views: number | null;
  interactions: number | null;
  comments: number | null;
  videos: number | null;
}

export interface TrafficMetricSeries extends AnalyticsMeta {
  scope: string;
  scope_label: string;
  country: string;
  days: TrafficMetricPoint[];
  totals: Record<TrafficMetricKey, number>;
}

export interface TopChannel {
  id: string;
  name: string;
  platform: Platform;
  country: string;
  views: number;
}

export type CountryViewsDay = { date: string } & Record<string, number | string>;

export interface CountryViewsSeries extends AnalyticsMeta {
  countries: string[];
  days: CountryViewsDay[];
  totals: Record<string, number>;
}

export interface CountryTag {
  id: string;
  name: string;
}

export interface ChannelBreakdownRow {
  platform: string;
  managed: number;
  active: number;
  is_custom_tag?: boolean;
  by_country?: { country: string; managed: number; active: number }[];
}

export interface ChannelBreakdown extends AnalyticsMeta {
  total: { managed: number; active: number };
  by_platform: ChannelBreakdownRow[];
}

/** `GET /v1/analytics/compare/{dimension}` — country có biểu đồ RIÊNG, "Biểu đồ chung" không dùng. */
export type CompareDimension = "platform" | "owner" | "country" | "channel" | "group";
/** Chiều xuất hiện trong "Biểu đồ chung" — KHÔNG có `country` (biểu đồ riêng). */
export type CommonCompareDimension = Exclude<CompareDimension, "country">;

export interface CompareItem {
  key: string;
  name: string;
  total: number;
  series?: { date: string; value: number | null }[];
}

export interface CompareResult extends AnalyticsMeta {
  dimension: string;
  items: CompareItem[];
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Chuẩn hoá phản hồi compare. Hợp đồng nói "mỗi mục có `series` theo ngày + `total`"; hình dạng bao ngoài
 * chưa đóng băng nên chấp nhận `items` | `data` | `series` | mảng trần, và mục `{key|id, name|label, total, series|days}`.
 */
export function normalizeCompare(raw: unknown, dimension: string): CompareResult {
  const root = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const list = (Array.isArray(raw) ? raw
    : Array.isArray(root.items) ? root.items
    : Array.isArray(root.data) ? root.data
    : Array.isArray(root.series) ? root.series : []) as unknown[];
  const items: CompareItem[] = list.map((x, i) => {
    const r = (x ?? {}) as Record<string, unknown>;
    const name = String(r.name ?? r.label ?? r.key ?? r.id ?? `#${i + 1}`);
    const rawSeries = (Array.isArray(r.series) ? r.series : Array.isArray(r.days) ? r.days : undefined) as
      | Record<string, unknown>[] | undefined;
    return {
      key: String(r.key ?? r.id ?? name),
      name,
      total: num(r.total) ?? 0,
      series: rawSeries?.map((p) => ({ date: String(p.date ?? ""), value: num(p.value ?? p.views) })),
    };
  });
  return {
    dimension,
    items,
    filter: root.filter as AppliedFilter | undefined,
    freshness: root.freshness as Freshness | undefined,
  };
}

export interface SyncStatus {
  cursor?: string | number | null;
  lag_seconds?: number | null;
  dead_letter_count?: number;
  stale?: boolean;
  as_of?: string | null;
}

// ── Analytics API (`/v1/analytics/*`) ───────────────────────────────────────

export const analyticsApi = {
  summary: (f: FilterState, o?: Opts) =>
    apiFetch<AnalyticsSummary>(`/v1/analytics/summary?${filterToQuery(f)}`, undefined, o),

  traffic: (f: FilterState, o?: Opts) =>
    apiFetch<TrafficMetricSeries>(`/v1/analytics/traffic?${filterToQuery(f)}`, undefined, o),

  topChannels: (f: FilterState, limit = 15, o?: Opts) =>
    apiFetch<{ items: TopChannel[] } & AnalyticsMeta>(
      `/v1/analytics/top-channels?${filterToQuery(f, { limit })}`, undefined, o),

  viewsByCountry: (f: FilterState, o?: Opts) =>
    apiFetch<CountryViewsSeries>(`/v1/analytics/views-by-country?${filterToQuery(f)}`, undefined, o),

  channelBreakdown: (f: FilterState, o?: Opts) =>
    apiFetch<ChannelBreakdown>(`/v1/analytics/channel-breakdown?${filterToQuery(f)}`, undefined, o),

  compare: async (dimension: CompareDimension, f: FilterState, o?: Opts): Promise<CompareResult> =>
    normalizeCompare(
      await apiFetch<unknown>(`/v1/analytics/compare/${enc(dimension)}?${filterToQuery(f)}`, undefined, o),
      dimension,
    ),

  // Danh mục lọc — xem filterState.ts về tham số.
  countryTags: (f: FilterState, o?: Opts) =>
    apiFetch<{ items: CountryTag[] }>(`/v1/analytics/country-tags${qs(countryCatalogParams(f))}`, undefined, o),

  platformTags: (f: FilterState, o?: Opts) =>
    apiFetch<{ items: string[] }>(`/v1/analytics/platform-tags${qs(platformTagCatalogParams(f))}`, undefined, o),

  /** Danh mục nền tảng ĐANG CÓ dữ liệu của phạm vi người gọi (không co theo ô nền tảng đang chọn). */
  platforms: (f: FilterState, o?: Opts) =>
    apiFetch<{ items: string[] }>(`/v1/analytics/platforms${qs(platformTagCatalogParams(f))}`, undefined, o),

  metrics: (o?: Opts) =>
    apiFetch<{ metrics: AnalyticsMetric[] }>("/v1/analytics/metrics", undefined, o),

  freshness: (o?: Opts) =>
    apiFetch<SyncStatus>("/v1/analytics/freshness", undefined, o),
};

// ── Viewer Links API (pairing legacy, deprecated — giữ đường dẫn legacy-compat) ──

export interface ViewerLink {
  tool_user_id: string;
  label: string;
  status: string;
  linked_at: string;
}

export interface LinkCode {
  code: string;
  expires_at: string;
}

export const linksApi = {
  createCode: () => apiFetch<LinkCode>("/v1/viewer/links/code", json("POST")),
  list: () => apiFetch<{ items: ViewerLink[] }>("/v1/viewer/links"),
  revoke: (tool_user_id: string) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/links/${enc(tool_user_id)}`, json("DELETE")),
};

// ── Nhóm kênh (`/v1/analytics/groups`) ──────────────────────────────────────

export interface GroupMember {
  channel_ref: string;
  platform_tag: string;
  country_override: string;
}

export interface ViewerGroup {
  id: string;
  account_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  members: GroupMember[];
}

export const groupsApi = {
  create: (name: string) => apiFetch<ViewerGroup>("/v1/analytics/groups", json("POST", { name })),

  list: (owner_account_id = "", o?: Opts) => {
    const q = new URLSearchParams();
    if (owner_account_id) q.set("owner_account_id", owner_account_id);
    return apiFetch<{ items: ViewerGroup[] }>(`/v1/analytics/groups${qs(q)}`, undefined, o);
  },

  rename: (id: string, name: string) =>
    apiFetch<{ ok: boolean }>(`/v1/analytics/groups/${enc(id)}`, json("PATCH", { name })),

  delete: (id: string) => apiFetch<{ ok: boolean }>(`/v1/analytics/groups/${enc(id)}`, json("DELETE")),

  addMember: (id: string, data: { channel_ref: string; platform_tag?: string; country_override?: string }) =>
    apiFetch<{ ok: boolean }>(`/v1/analytics/groups/${enc(id)}/members`, json("POST", data)),

  removeMember: (id: string, channel_ref: string) =>
    apiFetch<{ ok: boolean }>(`/v1/analytics/groups/${enc(id)}/members/${enc(channel_ref)}`, json("DELETE")),
};

// ── Admin API ───────────────────────────────────────────────────────────────

export interface CapabilityGrant {
  capabilities: string[];
  override_defaults?: boolean;
}

export interface InviteKey {
  key: string;
  role: string;
  label: string;
  max_uses: number;
  used_count: number;
  created_at: string;
  expires_at: string;
  status: string;
  preset?: string;
  allowed_apps?: string[];
  capability_grant?: CapabilityGrant;
}

export interface KeyPreset {
  key: string;
  label: string;
  role: string;
  capabilities: string[];
  admin_only: boolean;
}

export interface Account {
  id: string;
  username: string;
  email: string;
  role: string;
  created_at: string;
  last_login: string;
  status: string;
  invite_key: string;
}

export interface CountryAlias { alias: string; canonical: string; created_at: string }
export interface CountryCanonical { name: string; created_at: string }

export const adminApi = {
  createKey: (body: { preset: string; label?: string; max_uses?: number; expires_days?: number; allowed_apps?: string[] }) =>
    apiFetch<InviteKey>("/v1/admin/keys", json("POST", body)),

  updateKey: (key: string, body: { preset?: string; label?: string; max_uses?: number; expires_days?: number }) =>
    apiFetch<InviteKey>(`/v1/admin/keys/${enc(key)}`, json("PATCH", body)),

  listPresets: () => apiFetch<{ presets: KeyPreset[] }>("/v1/admin/keys/presets"),

  listKeys: () => apiFetch<{ keys: InviteKey[] }>("/v1/admin/keys"),

  revokeKey: (key: string) => apiFetch<{ ok: boolean }>(`/v1/admin/keys/${enc(key)}`, json("DELETE")),

  suspendKey: (key: string) => apiFetch<{ ok: boolean }>(`/v1/admin/keys/${enc(key)}/suspend`, json("PATCH", {})),

  unsuspendKey: (key: string) => apiFetch<{ ok: boolean }>(`/v1/admin/keys/${enc(key)}/unsuspend`, json("PATCH", {})),

  listUsers: (o?: Opts) => apiFetch<{ users: Account[] }>("/v1/admin/users", undefined, o),

  setUserStatus: (userId: string, status: "active" | "suspended") =>
    apiFetch<{ ok: boolean; status: string }>(`/v1/admin/users/${enc(userId)}/status`, json("PATCH", { status })),

  deleteUser: (userId: string) => apiFetch<{ ok: boolean }>(`/v1/admin/users/${enc(userId)}`, json("DELETE")),

  updateUser: (userId: string, data: { username?: string; email?: string; role?: string }) =>
    apiFetch<{ ok: boolean; updates: Record<string, string> }>(`/v1/admin/users/${enc(userId)}`, json("PATCH", data)),

  flushCache: () =>
    apiFetch<{ ok: boolean; removed: number; enabled: boolean }>("/v1/analytics/admin/cache/flush", json("POST")),

  listCountryAliases: () => apiFetch<{ items: CountryAlias[] }>("/v1/analytics/admin/country-aliases"),

  upsertCountryAlias: (alias: string, canonical: string) =>
    apiFetch<CountryAlias>("/v1/analytics/admin/country-aliases", json("POST", { alias, canonical })),

  deleteCountryAlias: (alias: string) =>
    apiFetch<{ ok: boolean }>(`/v1/analytics/admin/country-aliases/${enc(alias)}`, json("DELETE")),

  listCountryCanonicals: () => apiFetch<{ items: CountryCanonical[] }>("/v1/analytics/admin/country-canonicals"),

  createCountryCanonical: (name: string) =>
    apiFetch<CountryCanonical>("/v1/analytics/admin/country-canonicals", json("POST", { name })),

  renameCountryCanonical: (name: string, newName: string) =>
    apiFetch<{ ok: boolean; name: string }>(
      `/v1/analytics/admin/country-canonicals/${enc(name)}`, json("PATCH", { name: newName })),

  deleteCountryCanonical: (name: string) =>
    apiFetch<{ ok: boolean }>(`/v1/analytics/admin/country-canonicals/${enc(name)}`, json("DELETE")),
};

// ── Permission (Identity) ───────────────────────────────────────────────────

export interface PermissionCatalogEntry { name: string; description?: string }

/** `reward:access` -> giữ dấu `:` (hợp lệ trong path segment), mã hoá phần còn lại. */
const permSeg = (p: string) => enc(p).replace(/%3A/gi, ":");

export const permissionsApi = {
  catalog: async (): Promise<PermissionCatalogEntry[]> => {
    const d = await apiFetch<{ permissions?: unknown[] }>("/v1/admin/permissions");
    return (d.permissions ?? []).map((p) =>
      typeof p === "string" ? { name: p } : {
        name: String((p as Record<string, unknown>).name ?? (p as Record<string, unknown>).permission ?? ""),
        description: (p as Record<string, unknown>).description as string | undefined,
      }).filter((p) => p.name);
  },

  forAccount: (accountId: string, o?: Opts) =>
    apiFetch<{ account_id: string; permissions: string[] }>(`/v1/accounts/${enc(accountId)}/permissions`, undefined, o),

  grant: (accountId: string, perm: string) =>
    apiFetch<{ ok: boolean; permission: string; granted: boolean }>(
      `/v1/admin/accounts/${enc(accountId)}/permissions/${permSeg(perm)}`, json("PUT")),

  revoke: (accountId: string, perm: string) =>
    apiFetch<{ ok: boolean; permission: string; granted: boolean }>(
      `/v1/admin/accounts/${enc(accountId)}/permissions/${permSeg(perm)}`, json("DELETE")),
};

// ── Integration (mặt phẳng USER) ────────────────────────────────────────────

export type ClientKind = "primary_tool" | "fallback_tool" | "service";
export type ClientStatus = "draft" | "pending_email_verification" | "active" | "suspended" | "deleted";
export type Ownership = "personal" | "shared";
export type RevealState = "pending" | "revealed" | "expired";

export interface CredentialSummary {
  key_id: string;
  status: "active" | "revoked";
  reveal_state?: RevealState | null;
  bound_device_id?: string | null;
  activated_at?: string | null;
  last_used_at?: string | null;
  expires_at?: string | null;
}

export interface IntegrationClient {
  client_id: string;
  owner_account_id: string;
  tool_name: string;
  client_kind: ClientKind;
  status: ClientStatus;
  ownership: Ownership;
  purpose: string;
  contract_version: string;
  allowed_platforms: string[];
  allowed_scopes: string[];
  created_from: "api" | "provisioning";
  configured_at?: string | null;
  activated_at?: string | null;
  activated_by?: string | null;
  activated_by_username?: string | null;   // username của admin kích hoạt — ưu tiên hiển thị thay vì account_id
  created_at: string;
  updated_at: string;
  owner_username?: string | null;
  credentials?: CredentialSummary[] | null;
}

export interface IntegrationCredential {
  key_id: string;
  scopes: string[];
  status: "active" | "revoked";
  label: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  created_at: string;
  revoked_at?: string | null;
  reveal_state?: RevealState | null;
  reveal_expires_at?: string | null;
  revealed_at?: string | null;
  bound_device_id?: string | null;
  bound_installation_id?: string | null;
  activated_at?: string | null;
}

export interface InstallationInfo {
  installation_id: string;
  device_id: string;
  hostname: string;
  os: string;
  app_version: string;
  capabilities: string[];
  status: string;
  first_seen_at: string;
  last_seen_at: string;
}

export interface IntegrationClientDetail extends IntegrationClient {
  installations: InstallationInfo[];
  credentials: IntegrationCredential[];
}

/** Phản hồi cấp/xoay credential — `api_key` CHỈ xuất hiện khi chủ (hoặc admin trên client shared) tự nhận plaintext;
 * admin cấp/xoay hộ client personal => `api_key: null`, `reveal_state: "pending"` (chủ tự nhận qua `revealCredential`). */
export interface IssuedCredential {
  client_id: string;
  key_id: string;
  api_key: string | null;
  scopes: string[];
  expires_at?: string | null;
  contract_version: string;
  allowed_platforms: string[];
  reveal_state?: RevealState | null;
  previous_key_id?: string | null;
  previous_key_expires_at?: string | null;
}

export interface ConnectionGuideHeader { name: string; required_when: string; description: string }

export interface ConnectionGuideEndpoint {
  method: string;
  path: string;
  scope: string;
  summary: string;
  description: string;
  request_schema?: Record<string, unknown> | null;
  example_request?: Record<string, unknown> | null;
  example_response?: Record<string, unknown> | null;
  error_codes: string[];
}

export interface ConnectionGuideErrorCode { code: string; http_status: number; meaning: string }

export interface ConnectionGuideSnapshot {
  snapshot_id: string;
  platform: string;
  status: string;
  counts?: Record<string, number>;
  created_at: string;
  committed_at?: string | null;
  rejections?: { item_key: string; code: string; message: string }[];
}

export interface ConnectionGuide {
  base_url: string | null;
  contract_version: string;
  auth: { header: "Authorization"; format: string };
  required_headers: ConnectionGuideHeader[];
  client: IntegrationClient;
  credential: IntegrationCredential | null;
  installations: InstallationInfo[];
  endpoints: ConnectionGuideEndpoint[];
  limits: { batch_max_items: number; body_max_bytes: number };
  error_codes: ConnectionGuideErrorCode[];
  recent_snapshots: ConnectionGuideSnapshot[];
  /** Nền tảng + mã Tool này được dùng ở trường `platform` (mã chính `key` hoặc mã phụ `aliases`). Chỉ Admin sửa được. */
  platforms?: { key: string; display_name: string; aliases: string[]; codes: string[] }[];
  platform_rules?: string[];
}

export interface PlatformRegistryEntry {
  key: string; display_name: string; enabled: boolean; description?: string | null;
  /** Mã phụ (Tool gửi mã nào trong `key` + `aliases` cũng được gom về nền tảng này). */
  aliases?: string[];
}

export const integrationApi = {
  listClients: (owner = "", o?: Opts) => {
    const q = new URLSearchParams();
    if (owner) q.set("owner", owner);
    return apiFetch<{ items: IntegrationClient[] }>(`/v1/integrations/clients${qs(q)}`, undefined, o);
  },

  getClient: (clientId: string, o?: Opts) =>
    apiFetch<IntegrationClientDetail>(`/v1/integrations/clients/${enc(clientId)}`, undefined, o),

  /** Bước 1 wizard công cụ dùng chung (CHỈ admin). */
  createSharedClient: (data: { tool_name: string; client_kind: "fallback_tool" | "service"; purpose: string; platforms?: string[] }) =>
    apiFetch<IntegrationClient>("/v1/integrations/clients", json("POST", data)),

  /** Bước 2 wizard (CHỈ admin, chỉ khi client `draft`). */
  configureClient: (clientId: string, data: { allowed_platforms: string[]; allowed_scopes: string[] }) =>
    apiFetch<IntegrationClient>(`/v1/integrations/clients/${enc(clientId)}/config`, json("PUT", data)),

  /** Bước 3 wizard (CHỈ admin): `confirm_tool_name` phải khớp `tool_name`. */
  activateClient: (clientId: string, confirm_tool_name: string) =>
    apiFetch<IntegrationClient>(`/v1/integrations/clients/${enc(clientId)}/activate`, json("POST", { confirm_tool_name })),

  /** Chuyển chủ client cá nhân (CHỈ admin). */
  transferClient: (clientId: string, new_owner_account_id: string) =>
    apiFetch<IntegrationClient>(`/v1/integrations/clients/${enc(clientId)}/transfer`, json("POST", { new_owner_account_id })),

  patchClient: (clientId: string, data: { status?: string; allowed_scopes?: string[]; allowed_platforms?: string[]; tool_name?: string }) =>
    apiFetch<IntegrationClient>(`/v1/integrations/clients/${enc(clientId)}`, json("PATCH", data)),

  issueCredential: (clientId: string, data: { scopes?: string[]; expires_in_days?: number; label?: string } = {}) =>
    apiFetch<IssuedCredential>(`/v1/integrations/clients/${enc(clientId)}/credentials`, json("POST", data)),

  rotateCredential: (clientId: string, keyId: string, grace_seconds = 0) =>
    apiFetch<IssuedCredential>(
      `/v1/integrations/clients/${enc(clientId)}/credentials/${enc(keyId)}/rotate`, json("POST", { grace_seconds })),

  revokeCredential: (clientId: string, keyId: string) =>
    apiFetch<{ ok?: boolean }>(
      `/v1/integrations/clients/${enc(clientId)}/credentials/${enc(keyId)}`, json("DELETE")),

  /** `GET /v1/integrations/me/connection-guide` — chủ client cá nhân (cần capability:tool_integration). */
  connectionGuide: (o?: Opts) =>
    apiFetch<ConnectionGuide>("/v1/integrations/me/connection-guide", undefined, o),

  /** OpenAPI rút gọn chỉ gồm endpoint của connection-guide — dùng để tải `openapi.json`. */
  myOpenapi: (o?: Opts) =>
    apiFetch<Record<string, unknown>>("/v1/integrations/me/openapi.json", undefined, o),

  /** Nhận plaintext credential cấp hộ (`reveal_state=pending`) — chỉ hiện MỘT lần. */
  revealCredential: () =>
    apiFetch<IssuedCredential>("/v1/integrations/me/credential/reveal", json("POST", {})),

  /** Platform Registry (chỉ admin/dash_admin) — dùng cho wizard công cụ dùng chung, bước cấu hình.
   *  Server trả `data` là MẢNG (không bọc `items`); chuẩn hoá về `{items}` cho nơi dùng. */
  listPlatformRegistry: async (): Promise<{ items: PlatformRegistryEntry[] }> => {
    const d = await apiFetch<PlatformRegistryEntry[] | { items?: PlatformRegistryEntry[] }>("/v1/integrations/admin/platforms");
    return { items: Array.isArray(d) ? d : (d.items ?? []) };
  },
};

// ── Platform Registry (admin/dash_admin) ───────────────────────────────────

export const platformsApi = {
  /** Sửa tên hiển thị / bật-tắt một nền tảng ĐÃ CÓ (admin|dash_admin). */
  putPlatform: (key: string, data: { display_name: string; enabled: boolean }) =>
    apiFetch<PlatformRegistryEntry>(`/v1/integrations/admin/platforms/${enc(key)}`, json("PUT", data)),

  /** Tạo nền tảng mới: server chặn trùng MỌI mã (chính + phụ) bằng 409 `PLATFORM_CODE_EXISTS`. */
  createPlatform: (data: { key: string; display_name: string; aliases?: string[] }) =>
    apiFetch<PlatformRegistryEntry>("/v1/integrations/admin/platforms", json("POST", data)),

  /** Thêm mã phụ cho nền tảng `key` (409 nếu mã đã được giữ bởi bất kỳ nền tảng nào). */
  addAlias: (key: string, alias: string) =>
    apiFetch<PlatformRegistryEntry>(`/v1/integrations/admin/platforms/${enc(key)}/aliases`, json("POST", { alias })),

  removeAlias: (key: string, alias: string) =>
    apiFetch<PlatformRegistryEntry>(`/v1/integrations/admin/platforms/${enc(key)}/aliases/${enc(alias)}`, json("DELETE")),
};

// ── Profile API (user tự quản lý) ──────────────────────────────────────────

export const profileApi = {
  sendChangeCode: () =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/change-password/send-code", json("POST")),

  changePassword: (code: string, new_password: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/change-password", json("POST", { code, new_password })),

  updateProfile: (data: { username?: string; email?: string }) =>
    apiFetch<{ ok: boolean; message: string; updates?: Record<string, string> }>(
      "/v1/auth/profile", json("PATCH", data)),
};

// ── Resources API (legacy-compat — Local Fallback Tool v1) ──────────────────

export interface ResourceSource {
  id: string;
  owner_account_id: string;
  name: string;
  platform: string;
  project_key: string;
  default_country: string;
  origin: string;
  created_at: string;
  updated_at: string;
}

export interface ManagedResource {
  id: string;
  owner_account_id: string;
  platform: string;
  resource_type: string;
  canonical_external_id: string;
  input_ref: string;
  username: string;
  url: string;
  display_name: string;
  country: string;
  origin: "web_manual" | "tool_sync" | string;
  mapping_status: "unmapped" | "mapped" | string;
  execution_mode: "primary_tool" | "fallback_local" | "disabled" | string;
  verification_status: "unverified" | "valid" | "invalid" | string;
  enabled: boolean | number;
  created_at: string;
  updated_at: string;
}

export interface ImportSkip {
  input_ref: string;
  reason: string;
}

export const resourcesApi = {
  listPlatforms: () => apiFetch<{ items: { platform: string; display_name?: string }[] }>("/v1/resources/platforms"),

  listSources: () => apiFetch<{ items: ResourceSource[] }>("/v1/resources/sources"),

  createSource: (data: { name: string; platform: string; project_key?: string; default_country?: string }) =>
    apiFetch<ResourceSource>("/v1/resources/sources", json("POST", data)),

  updateSource: (id: string, data: Partial<{ name: string; project_key: string; default_country: string }>) =>
    apiFetch<{ ok: boolean }>(`/v1/resources/sources/${enc(id)}`, json("PATCH", data)),

  listResources: (filters: Partial<{
    platform: string; source_instance_id: string; country: string; origin: string;
    mapping_status: string; execution_mode: string; verification_status: string;
    enabled: boolean; q: string;
  }> = {}) => {
    const q = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== undefined && v !== "") q.set(k, String(v));
    });
    return apiFetch<{ items: ManagedResource[] }>(`/v1/resources${qs(q)}`);
  },

  createResource: (data: {
    source_instance_id: string; platform: string; resource_type?: string; input_ref: string;
    username?: string; url?: string; display_name?: string; country?: string;
  }) => apiFetch<ManagedResource>("/v1/resources", json("POST", data)),

  importResources: (data: {
    source_instance_id: string; platform: string; resource_type?: string; lines: string[];
  }) => apiFetch<{ created: ManagedResource[]; skipped: ImportSkip[]; created_count: number; skipped_count: number }>(
    "/v1/resources/import", json("POST", data)),

  updateResource: (id: string, data: Partial<{
    display_name: string; username: string; url: string; country: string; resource_type: string;
  }>) => apiFetch<{ ok: boolean }>(`/v1/resources/${enc(id)}`, json("PATCH", data)),

  deleteResource: (id: string) => apiFetch<{ ok: boolean }>(`/v1/resources/${enc(id)}`, json("DELETE")),

  /** Chuyển chế độ thu thập: 'fallback_local' (máy tập trung) | 'primary_tool' (tool riêng) | 'disabled'. */
  updateResourceMode: (id: string, execution_mode: string) =>
    apiFetch<{ ok: boolean }>(`/v1/resources/${enc(id)}/execution-mode`, json("PATCH", { execution_mode })),
};

// ── Nhóm quốc gia (integration-service /v1/user/channel-groups) ───────────

export interface ChannelGroup {
  id: string;
  owner_account_id: string;
  name: string;
  country_code: string;
  sort_order: number;
  created_at: string;
}

export const channelGroupsApi = {
  list: () =>
    apiFetch<{ data: ChannelGroup[] }>("/v1/user/channel-groups").then((r) => ({ items: r.data ?? [] })),

  create: (name: string, country_code = "") =>
    apiFetch<{ data: ChannelGroup }>("/v1/user/channel-groups", json("POST", { name, country_code }))
      .then((r) => r.data),

  update: (id: string, data: Partial<{ name: string; country_code: string }>) =>
    apiFetch<{ data: ChannelGroup }>(`/v1/user/channel-groups/${enc(id)}`, json("PATCH", data))
      .then((r) => r.data),

  delete: (id: string) =>
    apiFetch<{ data: { deleted: boolean } }>(`/v1/user/channel-groups/${enc(id)}`, json("DELETE")),
};

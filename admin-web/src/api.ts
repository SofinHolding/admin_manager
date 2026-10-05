/**
 * API client cho admin web — tự gắn JWT, tự refresh khi 401.
 *
 * Access token lưu trong memory (biến JS) — mất khi refresh trang là đúng ý: refresh trang thì
 * dùng refresh token lấy access mới. Không bao giờ lưu access token vào storage.
 */

let _accessToken = "";
let _refreshToken = "";
let _onSessionExpired: (() => void) | null = null;
let _refreshing: Promise<boolean> | null = null;

export function setTokens(access: string, refresh: string) {
  _accessToken = access;
  _refreshToken = refresh;
}

export function setRefreshToken(token: string) {
  _refreshToken = token;
}

export function getRefreshToken(): string {
  return _refreshToken;
}

export function clearTokens() {
  _accessToken = "";
  _refreshToken = "";
}

export function hasAccessToken(): boolean {
  return !!_accessToken;
}

export function onSessionExpired(cb: () => void) {
  _onSessionExpired = cb;
}

async function tryRefresh(): Promise<boolean> {
  if (!_refreshToken) return false;
  // Gộp nhiều request refresh cùng lúc thành MỘT — hai tab gọi API cùng lúc đều nhận 401, cả hai
  // cùng gọi refresh thì cái sau dùng refresh token đã bị xoay vòng → thất bại.
  if (_refreshing) return _refreshing;
  _refreshing = (async () => {
    try {
      const r = await fetch("/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: _refreshToken }),
      });
      if (!r.ok) return false;
      const d = await r.json();
      _accessToken = d.access_token;
      return true;
    } catch {
      return false;
    } finally {
      _refreshing = null;
    }
  })();
  return _refreshing;
}

async function apiFetch<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> || {}),
  };
  if (_accessToken) {
    headers["Authorization"] = `Bearer ${_accessToken}`;
  }

  let r = await fetch(url, { ...init, headers });

  // 401 → thử refresh rồi retry đúng 1 lần
  if (r.status === 401 && _refreshToken) {
    const ok = await tryRefresh();
    if (ok) {
      headers["Authorization"] = `Bearer ${_accessToken}`;
      r = await fetch(url, { ...init, headers });
    } else {
      _onSessionExpired?.();
      throw new Error("Phiên đã hết hạn");
    }
  }

  if (r.status === 403) {
    const d = await r.json().catch(() => ({ detail: "Không có quyền" }));
    throw new Error(d.detail || "Không có quyền");
  }

  if (!r.ok) {
    const d = await r.json().catch(() => ({ detail: `Lỗi ${r.status}` }));
    throw new Error(d.detail || d.message || `Lỗi ${r.status}`);
  }

  return r.json();
}

/** Token truy cập hiện tại — module reward dùng cho SSE/CSV (fetch thủ công có Authorization). */
export function getAccessToken(): string {
  return _accessToken;
}

/** Fetch raw (không parse JSON) — tự gắn Authorization + refresh 401 một lần. Dùng cho SSE/CSV. */
export async function apiFetchRaw(url: string, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> || {}) };
  if (_accessToken) headers["Authorization"] = `Bearer ${_accessToken}`;
  let r = await fetch(url, { ...init, headers });
  if (r.status === 401 && _refreshToken) {
    const ok = await tryRefresh();
    if (ok) {
      headers["Authorization"] = `Bearer ${_accessToken}`;
      r = await fetch(url, { ...init, headers });
    } else {
      _onSessionExpired?.();
      throw new Error("Phiên đã hết hạn");
    }
  }
  return r;
}

// ── Auth API (không cần JWT) ────────────────────────────────────────────────

export const authApi = {
  validateKey: (key: string) =>
    apiFetch<{ valid: boolean; role?: string; reason?: string }>("/v1/auth/validate-key", {
      method: "POST", body: JSON.stringify({ key }),
    }),

  checkUsername: (username: string) =>
    apiFetch<{ available: boolean }>("/v1/auth/check-username", {
      method: "POST", body: JSON.stringify({ username }),
    }),

  checkEmail: (email: string) =>
    apiFetch<{ available: boolean }>("/v1/auth/check-email", {
      method: "POST", body: JSON.stringify({ email }),
    }),

  register: (key: string, username: string, password: string, email: string) =>
    apiFetch<{ ok: boolean; email: string; message: string }>("/v1/auth/register", {
      method: "POST", body: JSON.stringify({ key, username, password, email }),
    }),

  verifyEmail: (token: string) =>
    apiFetch<{ ok: boolean; message: string }>(`/v1/auth/verify-email?token=${token}`),

  resendVerify: (email: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/resend-verify", {
      method: "POST", body: JSON.stringify({ email }),
    }),

  login: (username: string, password: string) =>
    apiFetch<{
      access_token: string; refresh_token: string; expires_in: number;
      user: { id: string; username: string; email: string; role: string };
    }>("/v1/auth/login", {
      method: "POST", body: JSON.stringify({ username, password }),
    }),

  refresh: (refresh_token: string) =>
    apiFetch<{ access_token: string; expires_in: number }>("/v1/auth/refresh", {
      method: "POST", body: JSON.stringify({ refresh_token }),
    }),

  logout: (refresh_token: string) =>
    apiFetch("/v1/auth/logout", {
      method: "POST", body: JSON.stringify({ refresh_token }),
    }),

  me: () => apiFetch<{
    id: string; username: string; email: string; role: string; created_at: string;
  }>("/v1/auth/me"),

  forgotPassword: (email: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/forgot-password", {
      method: "POST", body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, new_password: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/reset-password", {
      method: "POST", body: JSON.stringify({ token, new_password }),
    }),
};

// ── Types — giống Electron AnalyticsView ────────────────────────────────────

export type Platform = "youtube" | "facebook";
export type TrafficMetricKey = "views" | "interactions" | "comments" | "videos";

export interface AnalyticsSummary {
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

export interface TrafficMetricSeries {
  scope: "" | "youtube" | "facebook";
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

export interface CountryViewsSeries {
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
  by_group?: { group: string; managed: number; active: number }[];
  by_country?: { country: string; managed: number; active: number }[];
}

export interface ChannelBreakdown {
  total: { managed: number; active: number };
  by_platform: ChannelBreakdownRow[];
}

// ── Viewer API (cần JWT) ────────────────────────────────────────────────────

export interface ScopeOpts {
  owner_account_id?: string;
  group_id?: string;
  platform_tag?: string;
}

function appendScope(q: URLSearchParams, opts?: ScopeOpts) {
  if (opts?.owner_account_id) q.set("owner_account_id", opts.owner_account_id);
  if (opts?.group_id) q.set("group_id", opts.group_id);
  if (opts?.platform_tag) q.set("platform_tag", opts.platform_tag);
}

export const viewerApi = {
  summary: (date_from: string, date_to: string, country = "", opts?: ScopeOpts) => {
    const q = new URLSearchParams({ date_from, date_to });
    if (country) q.set("country", country);
    appendScope(q, opts);
    return apiFetch<AnalyticsSummary>(`/v1/viewer/summary?${q}`);
  },

  traffic: (date_from: string, date_to: string, country = "", opts?: ScopeOpts) => {
    const q = new URLSearchParams({ date_from, date_to });
    if (country) q.set("country", country);
    appendScope(q, opts);
    return apiFetch<TrafficMetricSeries>(`/v1/viewer/traffic?${q}`);
  },

  topChannels: (date_from: string, date_to: string, country = "", limit = 15, opts?: ScopeOpts) => {
    const q = new URLSearchParams({ date_from, date_to, limit: String(limit) });
    if (country) q.set("country", country);
    appendScope(q, opts);
    return apiFetch<{ items: TopChannel[] }>(`/v1/viewer/top-channels?${q}`);
  },

  viewsByCountry: (date_from: string, date_to: string, opts?: ScopeOpts) => {
    const q = new URLSearchParams({ date_from, date_to });
    appendScope(q, opts);
    return apiFetch<CountryViewsSeries>(`/v1/viewer/views-by-country?${q}`);
  },

  countryTags: (opts?: ScopeOpts) => {
    const q = new URLSearchParams();
    appendScope(q, opts);
    const qs = q.toString();
    return apiFetch<{ items: CountryTag[] }>(`/v1/viewer/country-tags${qs ? `?${qs}` : ""}`);
  },

  platformTags: (owner_account_id = "") => {
    const q = new URLSearchParams();
    if (owner_account_id) q.set("owner_account_id", owner_account_id);
    const qs = q.toString();
    return apiFetch<{ items: string[] }>(`/v1/viewer/platform-tags${qs ? `?${qs}` : ""}`);
  },

  channelBreakdown: (opts?: ScopeOpts) => {
    const q = new URLSearchParams();
    appendScope(q, opts);
    const qs = q.toString();
    return apiFetch<ChannelBreakdown>(`/v1/viewer/channel-breakdown${qs ? `?${qs}` : ""}`);
  },

  metrics: () =>
    apiFetch<{ metrics: AnalyticsMetric[] }>("/v1/viewer/metrics"),
};

// ── Viewer Links API (liên kết Tool — cần JWT) ──────────────────────────────

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
  createCode: () =>
    apiFetch<LinkCode>("/v1/viewer/links/code", { method: "POST" }),

  list: () =>
    apiFetch<{ items: ViewerLink[] }>("/v1/viewer/links"),

  revoke: (tool_user_id: string) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/links/${encodeURIComponent(tool_user_id)}`, { method: "DELETE" }),
};

// ── Viewer Groups API (nhóm kênh — cần JWT) ─────────────────────────────────

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
  create: (name: string) =>
    apiFetch<ViewerGroup>("/v1/viewer/groups", { method: "POST", body: JSON.stringify({ name }) }),

  list: (owner_account_id = "") => {
    const q = new URLSearchParams();
    if (owner_account_id) q.set("owner_account_id", owner_account_id);
    const qs = q.toString();
    return apiFetch<{ items: ViewerGroup[] }>(`/v1/viewer/groups${qs ? `?${qs}` : ""}`);
  },

  rename: (id: string, name: string) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/groups/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),

  delete: (id: string) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/groups/${id}`, { method: "DELETE" }),

  addMember: (id: string, data: { channel_ref: string; platform_tag?: string; country_override?: string }) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/groups/${id}/members`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  removeMember: (id: string, channel_ref: string) =>
    apiFetch<{ ok: boolean }>(`/v1/viewer/groups/${id}/members/${encodeURIComponent(channel_ref)}`, {
      method: "DELETE",
    }),
};

// ── Admin API (cần JWT + role='admin') ──────────────────────────────────────

export interface InviteKey {
  key: string;
  role: string;
  label: string;
  max_uses: number;
  used_count: number;
  created_at: string;
  expires_at: string;
  status: string;
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

export const adminApi = {
  createKey: (role: string, label = "", max_uses = 1, expires_days = 7) =>
    apiFetch<InviteKey>("/v1/admin/keys", {
      method: "POST",
      body: JSON.stringify({ role, label, max_uses, expires_days }),
    }),

  listKeys: () =>
    apiFetch<{ keys: InviteKey[] }>("/v1/admin/keys"),

  revokeKey: (key: string) =>
    apiFetch<{ ok: boolean }>(`/v1/admin/keys/${key}`, { method: "DELETE" }),

  listUsers: () =>
    apiFetch<{ users: Account[] }>("/v1/admin/users"),

  setUserStatus: (userId: string, status: "active" | "suspended") =>
    apiFetch<{ ok: boolean; status: string }>(`/v1/admin/users/${userId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  deleteUser: (userId: string) =>
    apiFetch<{ ok: boolean }>(`/v1/admin/users/${userId}`, { method: "DELETE" }),

  updateUser: (userId: string, data: { username?: string; email?: string; role?: string }) =>
    apiFetch<{ ok: boolean; updates: Record<string, string> }>(`/v1/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  flushCache: () =>
    apiFetch<{ ok: boolean; removed: number; enabled: boolean }>("/v1/admin/cache/flush",
      { method: "POST" }),

  listCountryAliases: () =>
    apiFetch<{ items: CountryAlias[] }>("/v1/admin/country-aliases"),

  upsertCountryAlias: (alias: string, canonical: string) =>
    apiFetch<CountryAlias>("/v1/admin/country-aliases", {
      method: "POST",
      body: JSON.stringify({ alias, canonical }),
    }),

  deleteCountryAlias: (alias: string) =>
    apiFetch<{ ok: boolean }>(`/v1/admin/country-aliases/${encodeURIComponent(alias)}`,
      { method: "DELETE" }),

  listCountryCanonicals: () =>
    apiFetch<{ items: CountryCanonical[] }>("/v1/admin/country-canonicals"),

  createCountryCanonical: (name: string) =>
    apiFetch<CountryCanonical>("/v1/admin/country-canonicals", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  renameCountryCanonical: (name: string, newName: string) =>
    apiFetch<{ ok: boolean; name: string }>(`/v1/admin/country-canonicals/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify({ name: newName }),
    }),

  deleteCountryCanonical: (name: string) =>
    apiFetch<{ ok: boolean }>(`/v1/admin/country-canonicals/${encodeURIComponent(name)}`,
      { method: "DELETE" }),
};

export interface CountryAlias {
  alias: string;
  canonical: string;
  created_at: string;
}

export interface CountryCanonical {
  name: string;
  created_at: string;
}

// ── Profile API (user tự quản lý) ──────────────────────────────────────────

export const profileApi = {
  sendChangeCode: () =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/change-password/send-code", {
      method: "POST",
    }),

  changePassword: (code: string, new_password: string) =>
    apiFetch<{ ok: boolean; message: string }>("/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ code, new_password }),
    }),

  updateProfile: (data: { username?: string; email?: string }) =>
    apiFetch<{ ok: boolean; message: string; updates?: Record<string, string> }>("/v1/auth/profile", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
};

// ── Resources API (Source/Resource — Local Fallback Tool v1, cần JWT) ──────
//
// Theo `tasks/backlog/local-operations-hub-v1.md`. Chỉ phạm vi tài khoản đang đăng nhập — xem
// toàn hệ thống là việc của Tool B (`/v1/ops/*`, không gọi từ admin-web).

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
  listPlatforms: () =>
    apiFetch<{ items: { platform: string }[] }>("/v1/resources/platforms"),

  listSources: () =>
    apiFetch<{ items: ResourceSource[] }>("/v1/resources/sources"),

  createSource: (data: { name: string; platform: string; project_key?: string; default_country?: string }) =>
    apiFetch<ResourceSource>("/v1/resources/sources", { method: "POST", body: JSON.stringify(data) }),

  updateSource: (id: string, data: Partial<{ name: string; project_key: string; default_country: string }>) =>
    apiFetch<{ ok: boolean }>(`/v1/resources/sources/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  listResources: (filters: Partial<{
    platform: string; source_instance_id: string; country: string; origin: string;
    mapping_status: string; execution_mode: string; verification_status: string;
    enabled: boolean; q: string;
  }> = {}) => {
    const q = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== undefined && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return apiFetch<{ items: ManagedResource[] }>(`/v1/resources${qs ? `?${qs}` : ""}`);
  },

  createResource: (data: {
    source_instance_id: string; platform: string; resource_type?: string; input_ref: string;
    username?: string; url?: string; display_name?: string; country?: string;
  }) => apiFetch<ManagedResource>("/v1/resources", { method: "POST", body: JSON.stringify(data) }),

  importResources: (data: {
    source_instance_id: string; platform: string; resource_type?: string; lines: string[];
  }) => apiFetch<{ created: ManagedResource[]; skipped: ImportSkip[]; created_count: number; skipped_count: number }>(
    "/v1/resources/import", { method: "POST", body: JSON.stringify(data) }),

  updateResource: (id: string, data: Partial<{
    display_name: string; username: string; url: string; country: string; resource_type: string;
  }>) => apiFetch<{ ok: boolean }>(`/v1/resources/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  deleteResource: (id: string) =>
    apiFetch<{ ok: boolean }>(`/v1/resources/${id}`, { method: "DELETE" }),
};

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

// ── Viewer API (cần JWT) ────────────────────────────────────────────────────

export const viewerApi = {
  summary: (date_from: string, date_to: string, country = "") => {
    const q = new URLSearchParams({ date_from, date_to });
    if (country) q.set("country", country);
    return apiFetch<AnalyticsSummary>(`/v1/viewer/summary?${q}`);
  },

  traffic: (date_from: string, date_to: string, scope = "", country = "") => {
    const q = new URLSearchParams({ date_from, date_to });
    if (scope) q.set("scope", scope);
    if (country) q.set("country", country);
    return apiFetch<TrafficMetricSeries>(`/v1/viewer/traffic?${q}`);
  },

  topChannels: (date_from: string, date_to: string, country = "", limit = 15) => {
    const q = new URLSearchParams({ date_from, date_to, limit: String(limit) });
    if (country) q.set("country", country);
    return apiFetch<{ items: TopChannel[] }>(`/v1/viewer/top-channels?${q}`);
  },

  viewsByCountry: (date_from: string, date_to: string) =>
    apiFetch<CountryViewsSeries>(
      `/v1/viewer/views-by-country?date_from=${date_from}&date_to=${date_to}`),

  countryTags: () =>
    apiFetch<{ items: CountryTag[] }>("/v1/viewer/country-tags"),

  metrics: () =>
    apiFetch<{ metrics: AnalyticsMetric[] }>("/v1/viewer/metrics"),
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
};

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

/**
 * API client cho Tool B (Local Fallback Tool) — gọi `admin-server` qua `/v1/auth/*` (đăng nhập,
 * dùng chung hệ account hiện có) và `/v1/ops/*` (inventory, Run Queue, platform policy).
 *
 * Access token CHỈ giữ trong biến JS memory — KHÔNG BAO GIỜ localStorage/sessionStorage (quy ước
 * toàn dự án, xem `.omp/RULES.md`). Refresh token lưu localStorage để tồn tại qua reload — Tool B
 * là app vận hành nội bộ của Admin/ADD, không phải trang công khai.
 */

let _accessToken = "";
let _refreshToken = localStorage.getItem("lfh_refresh") || "";
let _onSessionExpired: (() => void) | null = null;

export function setTokens(access: string, refresh: string) {
  _accessToken = access;
  _refreshToken = refresh;
  localStorage.setItem("lfh_refresh", refresh);
}

export function clearTokens() {
  _accessToken = "";
  _refreshToken = "";
  localStorage.removeItem("lfh_refresh");
}

export function hasRefreshToken(): boolean {
  return !!_refreshToken;
}

export function onSessionExpired(cb: () => void) {
  _onSessionExpired = cb;
}

async function tryRefresh(): Promise<boolean> {
  if (!_refreshToken) return false;
  const r = await fetch("/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: _refreshToken }),
  });
  if (!r.ok) return false;
  const d = await r.json();
  _accessToken = d.access_token;
  return true;
}

async function apiFetch<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> || {}),
  };
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
  if (!r.ok) {
    const d = await r.json().catch(() => ({ detail: `Lỗi ${r.status}` }));
    throw new Error(d.detail || d.message || `Lỗi ${r.status}`);
  }
  return r.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────

export interface LoginUser { id: string; username: string; email: string; role: string }

export const authApi = {
  login: (username: string, password: string) =>
    apiFetch<{ access_token: string; refresh_token: string; expires_in: number; user: LoginUser }>(
      "/v1/auth/login", {
        method: "POST",
        // X-Client-Type phân biệt Tool B với web — admin-server chỉ cho role admin/dash_admin
        // đăng nhập qua header này (mục 9 đặc tả local-operations-hub-v1.md) + ghi audit log
        // `login_fallback_tool`.
        headers: { "X-Client-Type": "local_fallback_tool" },
        body: JSON.stringify({ username, password }),
      }),

  me: () => apiFetch<LoginUser & { created_at: string }>("/v1/auth/me"),

  logout: () => {
    const rt = _refreshToken;
    clearTokens();
    if (rt) {
      fetch("/v1/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      }).catch(() => {});
    }
  },
};

// ── Ops: Inventory ───────────────────────────────────────────────────────

export interface InventoryResource {
  id: string;
  owner_account_id: string;
  owner_username: string;
  platform: string;
  resource_type: string;
  canonical_external_id: string;
  input_ref: string;
  username: string;
  url: string;
  display_name: string;
  country: string;
  origin: string;
  mapping_status: string;
  execution_mode: string;
  verification_status: string;
  enabled: boolean | number;
  created_at: string;
  updated_at: string;
  source_ids: string[];
}

export interface OpsSource {
  id: string;
  owner_account_id: string;
  owner_username: string;
  name: string;
  platform: string;
  project_key: string;
  default_country: string;
}

export interface OpsUser { id: string; username: string }
export interface PlatformPolicy {
  platform: string;
  web_entry_enabled: boolean | number;
  fallback_enabled: boolean | number;
  adapter_status: string;
}

export interface InventoryFilters {
  owner_account_id?: string;
  platform?: string;
  source_instance_id?: string;
  country?: string;
  origin?: string;
  mapping_status?: string;
  execution_mode?: string;
  verification_status?: string;
  enabled?: boolean;
  q?: string;
}

function toQuery<T extends object>(filters: T): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

export const opsApi = {
  inventory: (filters: InventoryFilters = {}) =>
    apiFetch<{ items: InventoryResource[] }>(`/v1/ops/inventory${toQuery(filters)}`),

  inventoryRevision: (owner_account_id = "") =>
    apiFetch<{ revision: string }>(`/v1/ops/inventory/revision${toQuery({ owner_account_id })}`),

  users: () => apiFetch<{ items: OpsUser[] }>("/v1/ops/users"),

  platforms: () => apiFetch<{ items: PlatformPolicy[] }>("/v1/ops/platforms"),

  sources: (owner_account_id = "") =>
    apiFetch<{ items: OpsSource[] }>(`/v1/ops/sources${toQuery({ owner_account_id })}`),

  // ── Run Queue ──
  createBatch: (platform: string, name: string) =>
    apiFetch<{ id: string; platform: string; name: string; status: string; created_at: string }>(
      "/v1/ops/batches", { method: "POST", body: JSON.stringify({ platform, name }) }),

  listBatches: () =>
    apiFetch<{ items: { id: string; platform: string; name: string; status: string; created_at: string }[] }>(
      "/v1/ops/batches"),

  getBatch: (id: string) =>
    apiFetch<{
      id: string; platform: string; name: string; status: string; created_at: string;
      items: { id: string; resource_id: string; status: string; owner_account_id: string }[];
    }>(`/v1/ops/batches/${id}`),

  addItems: (batchId: string, resourceIds: string[]) =>
    apiFetch<{ added: string[]; skipped: { resource_id: string; reason: string }[] }>(
      `/v1/ops/batches/${batchId}/items`,
      { method: "POST", body: JSON.stringify({ resource_ids: resourceIds }) }),

  cancelItem: (itemId: string) =>
    apiFetch<{ ok: boolean }>(`/v1/ops/items/${itemId}/cancel`, { method: "PATCH" }),
};

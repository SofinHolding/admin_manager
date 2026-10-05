/**
 * Lõi HTTP của admin-web — mọi lời gọi API đi qua đây (cùng origin -> API Gateway `/v1/...`).
 *
 * - Gắn `Authorization: Bearer <access>` từ bộ nhớ (xem auth/tokenStore.ts).
 * - 401 -> refresh MỘT lần (gộp các request đồng thời) rồi thử lại đúng 1 lần.
 *   401 "Phiên đã bị thu hồi" (`AUTH_REVOKED`) -> KHÔNG refresh, kết thúc phiên ngay (đăng xuất sạch + thông báo).
 * - Timeout + AbortController: caller truyền `signal`; đổi bộ lọc huỷ request cũ, request cũ KHÔNG được
 *   ghi đè dữ liệu mới. Huỷ chủ động ném `AbortError` (caller bỏ qua), timeout ném `ApiError(TIMEOUT)`.
 * - Lỗi: hiểu cả envelope mới `{ok:false,error:{code,message}}` lẫn legacy `{"detail":...}` (lib/errors.ts).
 * - Thành công: nếu thân là envelope `{ok:true,data:...}` thì bóc `data`; legacy trả nguyên.
 */

import { clearTokens, getAccessToken, getRefreshToken, setAccessToken, setRefreshToken } from "../auth/tokenStore";
import { ApiError, errorFromResponse, isAbortError } from "./errors";

export const DEFAULT_TIMEOUT_MS = 30_000;

export interface RequestOpts {
  /** Huỷ từ phía caller (đổi bộ lọc, unmount). */
  signal?: AbortSignal;
  /** 0 = không giới hạn. Mặc định 30s. */
  timeoutMs?: number;
  /** Endpoint công khai (login/register/...): không gắn Bearer, không chạy luồng refresh/hết phiên. */
  public?: boolean;
}

export type SessionEndReason = "expired" | "revoked";
type RefreshOutcome = "ok" | "revoked" | "rejected" | "unavailable";
type RefreshResult = { access_token: string; refresh_token?: string };
export type RefreshStrategy = (refreshToken: string, signal?: AbortSignal) => Promise<RefreshResult>;

let sessionEndedCb: ((reason: SessionEndReason) => void) | null = null;
let refreshing: Promise<RefreshOutcome> | null = null;

export function onSessionExpired(cb: ((reason: SessionEndReason) => void) | null): void {
  sessionEndedCb = cb;
}

function endSession(reason: SessionEndReason): void {
  clearTokens();
  sessionEndedCb?.(reason);
}

// ── Chiến lược refresh (password mode mặc định; OIDC mode thay thế) ─────────────────────────

async function passwordRefresh(refreshToken: string, signal?: AbortSignal): Promise<RefreshResult> {
  const r = await rawFetch("/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }, { signal, public: true });
  const body = await r.json().catch(() => null);
  if (!r.ok) throw errorFromResponse(r.status, body);
  return unwrap(body) as RefreshResult;
}

let refreshStrategy: RefreshStrategy = passwordRefresh;

export function setRefreshStrategy(fn: RefreshStrategy | null): void {
  refreshStrategy = fn ?? passwordRefresh;
}

// ── fetch cơ sở: timeout + huỷ + ánh xạ lỗi mạng ────────────────────────────────────────────

interface Pending { response: Response; done: () => void }

async function fetchWithTimeout(url: string, init: RequestInit, opts: RequestOpts): Promise<Pending> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const ctl = new AbortController();
  let timedOut = false;
  const outer = opts.signal;

  const onOuterAbort = () => ctl.abort();
  if (outer) {
    if (outer.aborted) throw new DOMException("Đã huỷ", "AbortError");
    outer.addEventListener("abort", onOuterAbort, { once: true });
  }
  const timer = timeoutMs > 0
    ? setTimeout(() => { timedOut = true; ctl.abort(); }, timeoutMs)
    : undefined;
  const done = () => {
    if (timer) clearTimeout(timer);
    outer?.removeEventListener("abort", onOuterAbort);
  };

  try {
    const response = await fetch(url, { ...init, signal: ctl.signal });
    return { response, done };
  } catch (e) {
    done();
    if (timedOut) throw new ApiError("Máy chủ phản hồi quá lâu — thử lại sau", { code: "TIMEOUT" });
    if (outer?.aborted || isAbortError(e)) throw new DOMException("Đã huỷ", "AbortError");
    throw new ApiError("Không kết nối được máy chủ", { code: "NETWORK" });
  }
}

/** fetch + timeout, KHÔNG gắn auth, KHÔNG xử lý 401. Chỉ dùng nội bộ/ OIDC. */
export async function rawFetch(url: string, init: RequestInit = {}, opts: RequestOpts = {}): Promise<Response> {
  const { response, done } = await fetchWithTimeout(url, init, opts);
  done();
  return response;
}

// ── Refresh ─────────────────────────────────────────────────────────────────────────────────

async function tryRefresh(): Promise<RefreshOutcome> {
  const rt = getRefreshToken();
  if (!rt) return "rejected";
  // Gộp nhiều request refresh đồng thời thành MỘT: refresh token xoay vòng, lần dùng thứ hai sẽ bị coi là tái sử dụng.
  if (refreshing) return refreshing;
  refreshing = (async (): Promise<RefreshOutcome> => {
    try {
      const d = await refreshStrategy(rt);
      setAccessToken(d.access_token);
      if (d.refresh_token) setRefreshToken(d.refresh_token); // xoay vòng: thay token mới, giữ nguyên chế độ lưu
      return "ok";
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === "AUTH_REVOKED") return "revoked";
        if (e.status === 401 || e.status === 400 || e.status === 403) return "rejected";
      }
      return "unavailable"; // mạng/5xx: KHÔNG đăng xuất, để UI báo không khả dụng
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

// ── Fetch có xác thực ───────────────────────────────────────────────────────────────────────

function withAuth(init: RequestInit, opts: RequestOpts): RequestInit {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token && !opts.public) headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers };
}

async function authedFetch(url: string, init: RequestInit, opts: RequestOpts): Promise<Pending> {
  let pending = await fetchWithTimeout(url, withAuth(init, opts), opts);
  if (opts.public || pending.response.status !== 401) return pending;

  // 401: đọc thân (bản clone) để phân biệt "thu hồi" với "hết hạn"
  const body = await pending.response.clone().json().catch(() => null);
  const err = errorFromResponse(401, body);
  if (err.code === "AUTH_REVOKED") {
    pending.done();
    endSession("revoked");
    throw err;
  }
  if (!getRefreshToken()) {
    pending.done();
    if (getAccessToken()) endSession("expired");
    throw err;
  }
  pending.done();
  const outcome = await tryRefresh();
  if (outcome === "revoked") { endSession("revoked"); throw new ApiError("Phiên đã bị thu hồi", { status: 401, code: "AUTH_REVOKED" }); }
  if (outcome === "rejected") { endSession("expired"); throw new ApiError("Phiên đã hết hạn", { status: 401, code: "AUTH_INVALID" }); }
  if (outcome === "unavailable") throw new ApiError("Không làm mới được phiên — dịch vụ tạm thời không khả dụng", { status: 503, code: "UNAVAILABLE" });

  pending = await fetchWithTimeout(url, withAuth(init, opts), opts);
  if (pending.response.status === 401) {
    const b2 = await pending.response.clone().json().catch(() => null);
    const e2 = errorFromResponse(401, b2);
    pending.done();
    endSession(e2.code === "AUTH_REVOKED" ? "revoked" : "expired");
    throw e2;
  }
  return pending;
}

/** Bóc envelope thành công `{ok:true,data}`; thân legacy giữ nguyên. */
export function unwrap(body: unknown): unknown {
  if (typeof body === "object" && body !== null && !Array.isArray(body)) {
    const rec = body as Record<string, unknown>;
    if (rec.ok === true && "data" in rec && Object.keys(rec).every((k) => k === "ok" || k === "data" || k === "meta")) {
      return rec.data;
    }
  }
  return body;
}

function jsonInit(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return { ...init, headers };
}

/** Gọi JSON. Ném `ApiError` (lib/errors.ts) khi lỗi; `AbortError` khi caller huỷ. */
export async function apiFetch<T = unknown>(url: string, init?: RequestInit, opts: RequestOpts = {}): Promise<T> {
  const { response: r, done } = await authedFetch(url, jsonInit(init), opts);
  try {
    if (!r.ok) {
      const body = await r.json().catch(() => null);
      throw errorFromResponse(r.status, body);
    }
    if (r.status === 204) return undefined as T;
    const text = await r.text();
    if (!text) return undefined as T;
    try {
      return unwrap(JSON.parse(text)) as T;
    } catch {
      throw new ApiError("Phản hồi máy chủ không hợp lệ", { status: r.status, code: "BAD_RESPONSE" });
    }
  } catch (e) {
    if (isAbortError(e)) throw e;
    if (e instanceof ApiError) throw e;
    // abort khi đang đọc thân
    if (opts.signal?.aborted) throw new DOMException("Đã huỷ", "AbortError");
    throw new ApiError("Không đọc được phản hồi máy chủ", { code: "NETWORK" });
  } finally {
    done();
  }
}

/** Fetch thô (CSV/XLSX/SSE) — vẫn có Bearer + refresh 401; trả `Response` cho caller tự xử lý. */
export async function apiFetchRaw(url: string, init?: RequestInit, opts: RequestOpts = {}): Promise<Response> {
  const { response, done } = await authedFetch(url, init ?? {}, { timeoutMs: 60_000, ...opts });
  done();
  return response;
}

/** Test seam: trạng thái gộp refresh đang chạy. */
export function __resetHttpForTests(): void {
  refreshing = null;
  sessionEndedCb = null;
  refreshStrategy = passwordRefresh;
}

/**
 * OIDC Authorization Code + PKCE (S256) cho client PUBLIC `manager-web`.
 * Bật bằng `VITE_AUTH_MODE=oidc` (mặc định `password` — luồng /v1/auth/login giữ nguyên).
 *
 * Tuân thủ docs/architecture/architecture-assessment-v2.md §2.5 / §2.9:
 *  - Chỉ Authorization Code + PKCE S256 (không implicit, không plain).
 *  - `state` (chống CSRF) và `nonce` (chống replay id_token) sinh bằng WebCrypto, kiểm tra khi callback.
 *  - `redirect_uri` khớp CHÍNH XÁC với client đã đăng ký (mặc định `<origin>/admin/callback`).
 *  - Giao dịch đăng nhập (verifier/state/nonce) chỉ sống trong sessionStorage, dùng MỘT lần (xoá ngay khi callback),
 *    hết hạn sau 10 phút. Token sau đó đi vào tokenStore (access: bộ nhớ; refresh: sessionStorage/bộ nhớ).
 *  - Refresh token xoay vòng: mỗi lần refresh thay token mới; server phát hiện dùng lại sẽ thu hồi cả chuỗi.
 *  - Không có client secret nào trong bundle (public client).
 */

import { ApiError } from "../lib/errors";
import { rawFetch } from "../lib/http";
import { decodeJwtClaims } from "./tokenStore";

export interface OidcConfig {
  clientId: string;
  redirectUri: string;
  scope: string;
  authorizeUrl: string;
  tokenUrl: string;
  logoutUrl: string;
  /** Nếu đặt, id_token.iss phải khớp. */
  issuer?: string;
}

export type AuthMode = "password" | "oidc";

export function getAuthMode(env: Record<string, unknown> = import.meta.env): AuthMode {
  return env.VITE_AUTH_MODE === "oidc" ? "oidc" : "password";
}

export function getOidcConfig(
  env: Record<string, unknown> = import.meta.env,
  origin: string = typeof location !== "undefined" ? location.origin : "http://127.0.0.1:5174",
): OidcConfig {
  const base = String(env.BASE_URL ?? "/admin/");
  const str = (k: string, d: string) => (typeof env[k] === "string" && (env[k] as string).trim() ? (env[k] as string).trim() : d);
  return {
    clientId: str("VITE_OIDC_CLIENT_ID", "manager-web"),
    redirectUri: str("VITE_OIDC_REDIRECT_URI", `${origin}${base.endsWith("/") ? base : base + "/"}callback`),
    scope: str("VITE_OIDC_SCOPE", "openid profile email"),
    authorizeUrl: str("VITE_OIDC_AUTHORIZE_URL", "/oauth/authorize"),
    tokenUrl: str("VITE_OIDC_TOKEN_URL", "/oauth/token"),
    logoutUrl: str("VITE_OIDC_LOGOUT_URL", "/oauth/logout"),
    issuer: typeof env.VITE_OIDC_ISSUER === "string" && env.VITE_OIDC_ISSUER ? (env.VITE_OIDC_ISSUER as string) : undefined,
  };
}

export class OidcError extends Error {
  readonly code: "state_mismatch" | "missing_tx" | "expired_tx" | "provider_error" | "missing_code"
    | "token_exchange_failed" | "nonce_mismatch" | "invalid_id_token";
  constructor(code: OidcError["code"], message: string) {
    super(message);
    this.name = "OidcError";
    this.code = code;
  }
}

// ── PKCE primitives (RFC 7636) ──────────────────────────────────────────────

const UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

/** Chuỗi ngẫu nhiên từ tập ký tự unreserved, lấy mẫu loại bỏ (rejection) để KHÔNG lệch phân phối. */
export function randomString(length: number, alphabet: string = UNRESERVED): string {
  const n = alphabet.length;
  const limit = 256 - (256 % n);
  let out = "";
  while (out.length < length) {
    const buf = new Uint8Array(Math.max(32, length));
    crypto.getRandomValues(buf);
    for (const b of buf) {
      if (b < limit) {
        out += alphabet[b % n];
        if (out.length === length) break;
      }
    }
  }
  return out;
}

/** code_verifier: 43..128 ký tự unreserved. Mặc định 64. */
export function generateCodeVerifier(length = 64): string {
  if (length < 43 || length > 128) throw new RangeError("code_verifier phải dài 43..128 ký tự");
  return randomString(length);
}

export function base64UrlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

/** code_challenge = BASE64URL(SHA256(ASCII(code_verifier))) */
export async function codeChallengeS256(verifier: string): Promise<string> {
  const data = new Uint8Array(verifier.length);
  for (let i = 0; i < verifier.length; i++) data[i] = verifier.charCodeAt(i) & 0xff;
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64UrlEncode(new Uint8Array(digest));
}

/** state / nonce: 256 bit ngẫu nhiên, base64url (43 ký tự). */
export function generateOpaque(): string {
  const buf = new Uint8Array(32);
  crypto.getRandomValues(buf);
  return base64UrlEncode(buf);
}
export const generateState = generateOpaque;
export const generateNonce = generateOpaque;

/** So sánh chuỗi thời gian-hằng (state). */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ── Giao dịch đăng nhập (sessionStorage, một lần) ───────────────────────────

const TX_KEY = "mp_oidc_tx";
const TX_TTL_MS = 10 * 60 * 1000;

interface OidcTx { state: string; nonce: string; verifier: string; returnTo: string; createdAt: number }

function saveTx(tx: OidcTx): void { sessionStorage.setItem(TX_KEY, JSON.stringify(tx)); }

/** Đọc giao dịch KHÔNG tiêu thụ (để kiểm `?error=` mà không đốt giao dịch hợp lệ bằng callback giả). */
function peekTx(): OidcTx | null {
  const raw = sessionStorage.getItem(TX_KEY);
  if (!raw) return null;
  try {
    const t = JSON.parse(raw) as OidcTx;
    return typeof t.state === "string" && typeof t.verifier === "string" ? t : null;
  } catch {
    return null;
  }
}

function takeTx(): OidcTx | null {
  const raw = sessionStorage.getItem(TX_KEY);
  sessionStorage.removeItem(TX_KEY); // một lần: xoá ngay dù thành công hay không
  if (!raw) return null;
  try {
    const t = JSON.parse(raw) as OidcTx;
    return typeof t.state === "string" && typeof t.verifier === "string" ? t : null;
  } catch {
    return null;
  }
}

/** Chặn open-redirect: chỉ nhận đường dẫn tương đối cùng origin. */
export function safeReturnTo(v: string | null | undefined, fallback = "/dashboard"): string {
  if (!v || !v.startsWith("/") || v.startsWith("//") || v.includes("\\") || /[\r\n]/.test(v)) return fallback;
  return v;
}

export function buildAuthorizeUrl(cfg: OidcConfig, p: { challenge: string; state: string; nonce: string }): string {
  const q = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: cfg.scope,
    state: p.state,
    nonce: p.nonce,
    code_challenge: p.challenge,
    code_challenge_method: "S256",
  });
  return `${cfg.authorizeUrl}?${q}`;
}

/** Bước 1: sinh verifier/state/nonce, lưu giao dịch rồi chuyển hướng tới /oauth/authorize. */
export async function beginLogin(
  cfg: OidcConfig,
  returnTo = "/dashboard",
  navigate: (url: string) => void = (u) => window.location.assign(u),
): Promise<string> {
  const verifier = generateCodeVerifier();
  const state = generateState();
  const nonce = generateNonce();
  const challenge = await codeChallengeS256(verifier);
  saveTx({ state, nonce, verifier, returnTo: safeReturnTo(returnTo), createdAt: Date.now() });
  const url = buildAuthorizeUrl(cfg, { challenge, state, nonce });
  navigate(url);
  return url;
}

// ── Token endpoint ──────────────────────────────────────────────────────────

export interface OidcTokens {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
  token_type?: string;
}

async function postToken(cfg: OidcConfig, form: Record<string, string>, signal?: AbortSignal): Promise<OidcTokens> {
  const r = await rawFetch(cfg.tokenUrl, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams(form).toString(),
  }, { signal, public: true });
  if (r.status >= 500) throw new ApiError("Máy chủ đăng nhập tạm thời không khả dụng", { status: r.status, code: "UNAVAILABLE" });
  const body = await r.json().catch(() => null) as Record<string, unknown> | null;
  if (!r.ok || !body || typeof body.access_token !== "string") {
    // RFC 6749 §5.2: {error, error_description}. Không phản chiếu giá trị đầu vào.
    const desc = typeof body?.error_description === "string" ? body.error_description : "";
    throw new OidcError("token_exchange_failed", desc || "Không đổi được mã đăng nhập lấy token");
  }
  return body as unknown as OidcTokens;
}

function audOk(aud: unknown, clientId: string): boolean {
  return Array.isArray(aud) ? aud.includes(clientId) : aud === clientId;
}

/**
 * Bước 2 (callback): kiểm tra `state` (từ chối nếu sai/thiếu giao dịch/hết hạn), đổi `code` + `code_verifier`
 * lấy token, kiểm `nonce` của id_token nếu có.
 */
export async function completeLogin(
  cfg: OidcConfig,
  search: string,
): Promise<{ tokens: OidcTokens; returnTo: string }> {
  const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const providerError = sp.get("error");
  if (providerError) {
    // Lỗi từ provider: chỉ tiêu giao dịch khi `state` khớp (lỗi thật của chính luồng này). Callback giả/thiếu state
    // KHÔNG được đốt giao dịch đang chờ của người dùng.
    const pending = peekTx();
    const st = sp.get("state") ?? "";
    if (pending && st && safeEqual(st, pending.state)) takeTx();
    throw new OidcError("provider_error", sp.get("error_description") || "Máy chủ đăng nhập từ chối yêu cầu");
  }
  const tx = takeTx();
  if (!tx) throw new OidcError("missing_tx", "Không tìm thấy phiên đăng nhập đang chờ — hãy đăng nhập lại");
  const state = sp.get("state") ?? "";
  if (!state || !safeEqual(state, tx.state)) {
    throw new OidcError("state_mismatch", "Tham số state không khớp — yêu cầu bị từ chối");
  }
  if (Date.now() - tx.createdAt > TX_TTL_MS) {
    throw new OidcError("expired_tx", "Phiên đăng nhập đã hết hạn — hãy thử lại");
  }
  const code = sp.get("code");
  if (!code) throw new OidcError("missing_code", "Thiếu mã uỷ quyền (code)");

  const tokens = await postToken(cfg, {
    grant_type: "authorization_code",
    code,
    redirect_uri: cfg.redirectUri,
    client_id: cfg.clientId,
    code_verifier: tx.verifier,
  });

  if (tokens.id_token) {
    const c = decodeJwtClaims(tokens.id_token);
    if (!c) throw new OidcError("invalid_id_token", "id_token không hợp lệ");
    if (typeof c.nonce !== "string" || !safeEqual(c.nonce, tx.nonce)) {
      throw new OidcError("nonce_mismatch", "nonce không khớp — yêu cầu bị từ chối");
    }
    if (!audOk(c.aud, cfg.clientId)) throw new OidcError("invalid_id_token", "id_token sai audience");
    if (cfg.issuer && c.iss !== cfg.issuer) throw new OidcError("invalid_id_token", "id_token sai issuer");
  }
  return { tokens, returnTo: safeReturnTo(tx.returnTo) };
}

/** Refresh (xoay vòng): trả cặp token MỚI; caller phải thay refresh token cũ. */
export function oidcRefresh(cfg: OidcConfig, refreshToken: string, signal?: AbortSignal): Promise<OidcTokens> {
  return postToken(cfg, { grant_type: "refresh_token", refresh_token: refreshToken, client_id: cfg.clientId }, signal);
}

/** Đăng xuất phía server (thu hồi refresh/phiên). Best-effort — caller vẫn xoá token cục bộ dù lỗi. */
export async function oidcLogout(cfg: OidcConfig, refreshToken: string): Promise<void> {
  try {
    await rawFetch(cfg.logoutUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: cfg.clientId, refresh_token: refreshToken }).toString(),
    }, { public: true, timeoutMs: 5000 });
  } catch { /* best-effort */ }
}

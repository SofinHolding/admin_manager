/**
 * Kho token phía trình duyệt — nơi DUY NHẤT chạm vào storage liên quan phiên.
 *
 * Quyết định bảo mật (ghi lại để review):
 *  - Access token (RS256, ~15 phút): CHỈ nằm trong biến module (bộ nhớ). Không bao giờ vào storage.
 *  - Refresh token: KHÔNG BAO GIỜ vào `localStorage` (tồn tại vô thời hạn, sống sót qua đóng trình duyệt,
 *    đọc được bởi mọi script cùng origin). Hai lựa chọn còn lại:
 *      * bộ nhớ thuần  -> an toàn nhất nhưng F5 là mất phiên;
 *      * `sessionStorage` -> sống trong đúng một tab, xoá khi đóng tab, không chia sẻ giữa các tab.
 *    Chọn `sessionStorage` làm mặc định (giữ UX "F5 không bị đăng xuất") và bộ nhớ thuần khi người dùng
 *    bỏ chọn "Ghi nhớ". Refresh token đã xoay vòng (rotation) phía server nên cửa sổ rủi ro nhỏ.
 *  - Khoá cũ `admin_refresh_token` trong localStorage (phiên bản trước) bị XOÁ ngay khi nạp module.
 *  - API key máy (`mpk_...`) / secret: KHÔNG bao giờ qua module này, không vào storage (xem SecretOnceDialog).
 */

const REFRESH_KEY = "mp_refresh_token";
export const LEGACY_REFRESH_KEY = "admin_refresh_token";

let accessToken = "";
let refreshToken = "";
/** Thế hệ phiên: tăng mỗi khi xoá/đặt lại token (đăng xuất, đăng nhập mới). Refresh đang bay ghi nhận epoch lúc bắt
 *  đầu và BỎ kết quả nếu epoch đã đổi — tránh ghi lại token của phiên cũ sau khi đã đăng xuất. */
let authEpoch = 0;
const listeners = new Set<() => void>();

function safeSession(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

/** Xoá dấu vết bản cũ lưu refresh token dài hạn. An toàn khi gọi nhiều lần. */
export function purgeLegacyStorage(): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(LEGACY_REFRESH_KEY);
  } catch { /* storage bị chặn */ }
  try {
    safeSession()?.removeItem(LEGACY_REFRESH_KEY);
  } catch { /* noop */ }
}
purgeLegacyStorage();

function emit() {
  listeners.forEach((l) => l());
}

/** Đăng ký lắng nghe thay đổi access token (để cập nhật claim `perms`). Trả hàm huỷ. */
export function subscribeTokens(cb: () => void): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

export function getAuthEpoch(): number {
  return authEpoch;
}

export function getAccessToken(): string {
  return accessToken;
}

export function hasAccessToken(): boolean {
  return !!accessToken;
}

export function getRefreshToken(): string {
  return refreshToken;
}

export function setAccessToken(token: string): void {
  accessToken = token;
  emit();
}

/** Lưu refresh token. `persist=true` -> sessionStorage (tab hiện tại); false -> chỉ bộ nhớ. */
export function setRefreshToken(token: string, persist?: boolean): void {
  refreshToken = token;
  const ss = safeSession();
  if (!ss) return;
  try {
    const shouldPersist = persist ?? ss.getItem(REFRESH_KEY) !== null;
    if (token && shouldPersist) ss.setItem(REFRESH_KEY, token);
    else ss.removeItem(REFRESH_KEY);
  } catch { /* storage đầy/bị chặn: giữ bộ nhớ */ }
}

export function setTokens(access: string, refresh: string, persist = true): void {
  authEpoch++;
  accessToken = access;
  setRefreshToken(refresh, persist);
  emit();
}

/** Refresh token đã lưu trong tab (nếu có) — dùng khi khởi động để khôi phục phiên sau F5. */
export function loadPersistedRefreshToken(): string {
  try {
    return safeSession()?.getItem(REFRESH_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Xoá SẠCH mọi thứ liên quan phiên (bộ nhớ + storage + khoá legacy). */
export function clearTokens(): void {
  authEpoch++;
  accessToken = "";
  refreshToken = "";
  try { safeSession()?.removeItem(REFRESH_KEY); } catch { /* noop */ }
  purgeLegacyStorage();
  emit();
}

// ── Giải mã claim (CHỈ để hiển thị/gate UI — KHÔNG phải xác minh; server mới là nơi enforce) ──

export interface TokenClaims {
  sub?: string;
  username?: string;
  role?: string;
  perms?: string[];
  email_verified?: boolean;
  sid?: string;
  exp?: number;
  [k: string]: unknown;
}

export function decodeJwtClaims(token: string): TokenClaims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const bin = atob(padded);
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    const json = new TextDecoder().decode(bytes);
    const obj = JSON.parse(json);
    return typeof obj === "object" && obj !== null ? (obj as TokenClaims) : null;
  } catch {
    return null;
  }
}

export function currentClaims(): TokenClaims | null {
  return accessToken ? decodeJwtClaims(accessToken) : null;
}

/**
 * Auth context + inactivity timeout + quản lý phiên.
 *
 * ── Lưu trữ token (quyết định bảo mật) ───────────────────────────────────────
 *  - Access token (RS256, ~15 phút): CHỈ trong bộ nhớ JS. Mất khi F5 -> khôi phục bằng refresh token.
 *  - Refresh token: KHÔNG dùng localStorage nữa (bản cũ lưu 30 ngày, sống qua đóng trình duyệt và đọc được bởi
 *    mọi script cùng origin). Dùng `sessionStorage` (một tab, xoá khi đóng tab) khi tick "Ghi nhớ"
 *    (F5 không mất phiên), hoặc CHỈ bộ nhớ khi bỏ tick (an toàn nhất, F5 = đăng nhập lại). Server xoay vòng
 *    refresh token và thu hồi cả chuỗi khi phát hiện dùng lại. Chi tiết: auth/tokenStore.ts.
 *  - Khoá cũ `admin_refresh_token` trong localStorage bị xoá khi nạp ứng dụng.
 *  - API key máy / secret KHÔNG đi qua module này và không bao giờ vào storage.
 *
 * ── Chế độ đăng nhập ─────────────────────────────────────────────────────────
 *  - `password` (mặc định): POST /v1/auth/login.
 *  - `oidc` (VITE_AUTH_MODE=oidc): Authorization Code + PKCE qua /oauth/* (auth/oidc.ts).
 *
 * ── Phiên hết hạn / bị thu hồi ───────────────────────────────────────────────
 *  401 "Phiên đã bị thu hồi" (AUTH_REVOKED) hoặc refresh bị từ chối -> xoá sạch token, hiện overlay có lý do,
 *  người dùng về trang đăng nhập. Mất mạng khi refresh KHÔNG bị coi là hết phiên.
 *
 * Inactivity timeout: 60 phút không thao tác -> cảnh báo 5 phút -> đăng xuất.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, authApi, clearTokens, getRefreshToken, onSessionExpired, permissionsApi, setRefreshStrategy, setTokens,
  type AuthUserDto,
} from "./api";
import {
  currentClaims, loadPersistedRefreshToken, purgeLegacyStorage, setRefreshToken, subscribeTokens,
} from "./auth/tokenStore";
import {
  beginLogin, completeLogin, getAuthMode, getOidcConfig, OidcError, oidcLogout, oidcRefresh, type AuthMode,
} from "./auth/oidc";
import { hasPerm as hasPermFn } from "./auth/permissions";

const INACTIVITY_MS = 60 * 60 * 1000;      // 60 phút
const WARNING_MS = 5 * 60 * 1000;           // cảnh báo 5 phút trước
const CHANNEL = "mp-auth";

export interface User extends AuthUserDto {
  /** Quyền mịn (claim `perms` của access token) — chỉ để ẩn/hiện UI. */
  perms: string[];
}

export type SessionEndReason = "expired" | "revoked";

export interface AuthCtx {
  user: User | null;
  loading: boolean;
  mode: AuthMode;
  sessionExpired: boolean;
  sessionReason: SessionEndReason;
  showWarning: boolean;
  warningSeconds: number;
  login: (username: string, password: string, remember: boolean) => Promise<void>;
  loginWithOidc: (returnTo?: string) => Promise<void>;
  completeOidcLogin: (search: string) => Promise<string>;
  logout: () => void;
  continueSession: () => void;
  dismissExpired: () => void;
  hasPerm: (perm: string) => boolean;
}

export const AuthContext = createContext<AuthCtx | null>(null);

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth ngoài AuthProvider");
  return ctx;
}

const MODE = getAuthMode();

// OIDC: refresh qua /oauth/token (refresh_token xoay vòng). Lỗi 4xx của token endpoint -> phiên bị từ chối.
if (MODE === "oidc") {
  const cfg = getOidcConfig();
  setRefreshStrategy(async (rt, signal) => {
    try {
      const t = await oidcRefresh(cfg, rt, signal);
      return { access_token: t.access_token, refresh_token: t.refresh_token };
    } catch (e) {
      if (e instanceof OidcError) {
        const revoked = /thu hồi|revoked/i.test(e.message);
        throw new ApiError(e.message, { status: 400, code: revoked ? "AUTH_REVOKED" : "AUTH_INVALID" });
      }
      throw e;
    }
  });
}

function permsFromToken(): string[] | null {
  const claims = currentClaims();
  return claims && Array.isArray(claims.perms) ? claims.perms.filter((p): p is string => typeof p === "string") : null;
}

const oidcInflight = new Map<string, Promise<string>>();

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [sessionReason, setSessionReason] = useState<SessionEndReason>("expired");
  const [showWarning, setShowWarning] = useState(false);
  const [warningSeconds, setWarningSeconds] = useState(300);
  const navigate = useNavigate();

  const inactivityTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const warningTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const countdownRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const channelRef = useRef<BroadcastChannel | null>(null);

  // Dựng User từ phản hồi me/login + claim perms. Token thiếu `perms` -> hỏi Identity (best-effort).
  const buildUser = useCallback((dto: AuthUserDto): User => {
    const perms = permsFromToken() ?? [];
    const u: User = { id: dto.id, username: dto.username, email: dto.email, role: dto.role, perms };
    if (permsFromToken() === null) {
      permissionsApi.forAccount(dto.id)
        .then((d) => setUser((cur) => (cur && cur.id === dto.id ? { ...cur, perms: d.permissions } : cur)))
        .catch(() => { /* không có quyền mịn thì UI dùng role */ });
    }
    return u;
  }, []);

  // ── Inactivity tracking ──────────────────────────────────────────────────

  const clearTimers = useCallback(() => {
    clearTimeout(inactivityTimer.current);
    clearTimeout(warningTimer.current);
    clearInterval(countdownRef.current);
    setShowWarning(false);
  }, []);

  const doLogout = useCallback((broadcast = true) => {
    clearTimers();
    const rt = getRefreshToken();
    clearTokens();                                   // xoá SẠCH trước, gọi server sau (best-effort)
    if (rt) {
      if (MODE === "oidc") void oidcLogout(getOidcConfig(), rt);
      else authApi.logout(rt).catch(() => {});
    }
    setUser(null);
    setSessionExpired(false);
    if (broadcast) {
      try { channelRef.current?.postMessage("logout"); } catch { /* noop */ }
    }
  }, [clearTimers]);

  const resetInactivity = useCallback(() => {
    if (!user) return;
    clearTimers();

    warningTimer.current = setTimeout(() => {
      setShowWarning(true);
      setWarningSeconds(300);
      countdownRef.current = setInterval(() => {
        setWarningSeconds((s) => {
          if (s <= 1) {
            clearInterval(countdownRef.current);
            doLogout();
            navigate("/login");
            return 0;
          }
          return s - 1;
        });
      }, 1000);
    }, INACTIVITY_MS - WARNING_MS);

    inactivityTimer.current = setTimeout(() => {
      doLogout();
      navigate("/login");
    }, INACTIVITY_MS);
  }, [user, clearTimers, doLogout, navigate]);

  const continueSession = useCallback(() => {
    setShowWarning(false);
    clearInterval(countdownRef.current);
    resetInactivity();
  }, [resetInactivity]);

  useEffect(() => {
    if (!user) return;
    const events = ["mousemove", "keydown", "click", "scroll", "touchstart"] as const;
    let throttled = false;
    const handler = () => {
      if (throttled) return;
      throttled = true;
      setTimeout(() => { throttled = false; }, 30_000);
      if (!showWarning) resetInactivity();
    };
    events.forEach((e) => window.addEventListener(e, handler, { passive: true }));
    resetInactivity();
    return () => {
      events.forEach((e) => window.removeEventListener(e, handler));
      clearTimers();
    };
  }, [user, showWarning, resetInactivity, clearTimers]);

  // ── Phiên hết hạn / bị thu hồi (từ lõi HTTP) ────────────────────────────

  useEffect(() => {
    onSessionExpired((reason) => {
      setSessionReason(reason);
      setSessionExpired(true);
      clearTimers();
    });
    return () => onSessionExpired(null);
  }, [clearTimers]);

  // ── Đồng bộ đăng xuất giữa các tab (sessionStorage là riêng từng tab nên dùng BroadcastChannel) ──

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(CHANNEL);
    channelRef.current = ch;
    ch.onmessage = (e) => {
      if (e.data === "logout") {
        clearTokens();
        setUser(null);
        setSessionReason("expired");
        setSessionExpired(true);
        clearTimers();
      }
    };
    return () => { ch.close(); channelRef.current = null; };
  }, [clearTimers]);

  // Cập nhật claim `perms` mỗi khi access token đổi (sau refresh)
  useEffect(() => subscribeTokens(() => {
    const p = permsFromToken();
    if (p) {
      setUser((cur) => (cur && cur.perms.join("|") !== p.join("|") ? { ...cur, perms: p } : cur));
    }
  }), []);

  // ── Khởi tạo: khôi phục phiên từ refresh token của TAB này (nếu có) ─────────

  useEffect(() => {
    purgeLegacyStorage(); // xoá refresh token dài hạn của phiên bản cũ (localStorage)
    const stored = loadPersistedRefreshToken();
    if (!stored) {
      setLoading(false);
      return;
    }
    // Giữ cờ "persist" (token đã ở sessionStorage); refresh token được xoay vòng thì ghi đè tại chỗ.
    // Pre-refresh để lấy AT hợp lệ trước khi gọi /me — tránh 401 trung gian hiện ra trong console.
    // (Pattern cũ: gọi /me → 401 → lõi HTTP tự refresh → retry; đúng về nghiệp vụ nhưng gây noise.)
    (async () => {
      try {
        const refreshed = await authApi.refresh(stored);
        setTokens(refreshed.access_token, refreshed.refresh_token ?? stored, true);
        const me = await authApi.me();
        setUser(buildUser(me));
      } catch (e) {
        if (e instanceof ApiError && (e.code === "UNAVAILABLE" || e.code === "NETWORK" || e.code === "TIMEOUT")) {
          // Không xoá phiên khi chỉ là mất mạng; người dùng thấy trang đăng nhập và có thể thử lại.
        } else {
          clearTokens();
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [buildUser]);
  // ── Login ───────────────────────────────────────────────────────────────

  const login = useCallback(async (username: string, password: string, remember: boolean) => {
    if (MODE === "oidc") throw new Error("Đăng nhập mật khẩu bị tắt — dùng đăng nhập SSO");
    const d = await authApi.login(username, password);
    setTokens(d.access_token, d.refresh_token, remember);
    setUser(buildUser(d.user));
    setSessionExpired(false);
  }, [buildUser]);

  const loginWithOidc = useCallback(async (returnTo = "/dashboard") => {
    await beginLogin(getOidcConfig(), returnTo);
  }, []);

  const completeOidcLogin = useCallback((search: string): Promise<string> => {
    // StrictMode chạy effect hai lần: dùng chung MỘT promise cho cùng một callback URL (code chỉ đổi được một lần).
    const existing = oidcInflight.get(search);
    if (existing) return existing;
    const p = (async () => {
      const { tokens, returnTo } = await completeLogin(getOidcConfig(), search);
      setTokens(tokens.access_token, tokens.refresh_token ?? "", true);
      const me = await authApi.me();
      setUser(buildUser(me));
      setSessionExpired(false);
      return returnTo;
    })();
    oidcInflight.set(search, p);
    p.catch(() => oidcInflight.delete(search));
    return p;
  }, [buildUser]);

  const logout = useCallback(() => {
    doLogout();
    navigate("/login");
  }, [doLogout, navigate]);

  const dismissExpired = useCallback(() => {
    clearTokens();
    setUser(null);
    setSessionExpired(false);
    navigate("/login");
  }, [navigate]);

  const hasPerm = useCallback((perm: string) => hasPermFn(user, perm), [user]);

  return (
    <AuthContext.Provider value={{
      user, loading, mode: MODE, sessionExpired, sessionReason, showWarning, warningSeconds,
      login, loginWithOidc, completeOidcLogin, logout, continueSession, dismissExpired, hasPerm,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

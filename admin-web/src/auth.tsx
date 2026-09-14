/**
 * Auth context + inactivity timeout + session management.
 *
 * Hai token:
 * - Access token (15 phút) → lưu trong memory JS, mất khi refresh trang
 * - Refresh token (30 ngày) → lưu localStorage hoặc sessionStorage tuỳ "Ghi nhớ"
 *
 * Inactivity timeout: 60 phút không thao tác → cảnh báo 5 phút → đăng xuất.
 * Session expired overlay: phủ lên trang hiện tại (backdrop-blur), không redirect đột ngột.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  authApi, clearTokens, getRefreshToken, hasAccessToken, onSessionExpired, setRefreshToken, setTokens,
} from "./api";

const INACTIVITY_MS = 60 * 60 * 1000;      // 60 phút
const WARNING_MS = 5 * 60 * 1000;           // cảnh báo 5 phút trước
const STORAGE_KEY = "admin_refresh_token";

interface User {
  id: string;
  username: string;
  email: string;
  role: string;
}

interface AuthCtx {
  user: User | null;
  loading: boolean;
  sessionExpired: boolean;
  showWarning: boolean;
  warningSeconds: number;
  login: (username: string, password: string, remember: boolean) => Promise<void>;
  logout: () => void;
  continueSession: () => void;
  dismissExpired: () => void;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth ngoài AuthProvider");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [showWarning, setShowWarning] = useState(false);
  const [warningSeconds, setWarningSeconds] = useState(300);
  const navigate = useNavigate();

  const inactivityTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const warningTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const countdownRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // ── Inactivity tracking ──────────────────────────────────────────────────

  const clearTimers = useCallback(() => {
    clearTimeout(inactivityTimer.current);
    clearTimeout(warningTimer.current);
    clearInterval(countdownRef.current);
    setShowWarning(false);
  }, []);

  const doLogout = useCallback(() => {
    clearTimers();
    const rt = getRefreshToken();
    if (rt) authApi.logout(rt).catch(() => {});
    clearTokens();
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    setUser(null);
    setSessionExpired(false);
  }, [clearTimers]);

  const resetInactivity = useCallback(() => {
    if (!user) return;
    clearTimers();

    // Cảnh báo sau 55 phút
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

    // Đăng xuất sau 60 phút
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

  // Lắng nghe sự kiện người dùng
  useEffect(() => {
    if (!user) return;
    const events = ["mousemove", "keydown", "click", "scroll", "touchstart"] as const;
    let throttled = false;
    const handler = () => {
      if (throttled) return;
      throttled = true;
      setTimeout(() => { throttled = false; }, 30_000); // throttle 30s
      if (!showWarning) resetInactivity();
    };
    events.forEach((e) => window.addEventListener(e, handler, { passive: true }));
    resetInactivity();
    return () => {
      events.forEach((e) => window.removeEventListener(e, handler));
      clearTimers();
    };
  }, [user, showWarning, resetInactivity, clearTimers]);

  // ── Session expired callback (từ API client) ────────────────────────────

  useEffect(() => {
    onSessionExpired(() => {
      setSessionExpired(true);
      clearTimers();
    });
  }, [clearTimers]);

  // ── Đồng bộ logout giữa các tab ────────────────────────────────────────

  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && !e.newValue) {
        // Tab khác đăng xuất → xoá token + hiện overlay
        clearTokens();
        setUser(null);
        setSessionExpired(true);
        clearTimers();
      }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [clearTimers]);

  // ── Khởi tạo: thử lấy user từ refresh token có sẵn ─────────────────────

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY);
    if (!stored) {
      setLoading(false);
      return;
    }
    setRefreshToken(stored);
    authApi.refresh(stored)
      .then((d) => {
        setTokens(d.access_token, stored);
        return authApi.me();
      })
      .then((u) => setUser(u))
      .catch(() => {
        clearTokens();
        localStorage.removeItem(STORAGE_KEY);
        sessionStorage.removeItem(STORAGE_KEY);
      })
      .finally(() => setLoading(false));
  }, []);

  // ── Login ───────────────────────────────────────────────────────────────

  const login = useCallback(async (username: string, password: string, remember: boolean) => {
    const d = await authApi.login(username, password);
    setTokens(d.access_token, d.refresh_token);
    if (remember) {
      localStorage.setItem(STORAGE_KEY, d.refresh_token);
    } else {
      sessionStorage.setItem(STORAGE_KEY, d.refresh_token);
    }
    setUser(d.user);
    setSessionExpired(false);
  }, []);

  const logout = useCallback(() => {
    doLogout();
    navigate("/login");
  }, [doLogout, navigate]);

  const dismissExpired = useCallback(() => {
    setSessionExpired(false);
    navigate("/login");
  }, [navigate]);

  return (
    <AuthContext.Provider value={{
      user, loading, sessionExpired, showWarning, warningSeconds,
      login, logout, continueSession, dismissExpired,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

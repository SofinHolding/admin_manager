import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { authApi, hasRefreshToken, onSessionExpired, setTokens, type LoginUser } from "./api";

interface AuthCtx {
  user: LoginUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<LoginUser | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    authApi.logout();
    setUser(null);
  }, []);

  useEffect(() => {
    onSessionExpired(() => setUser(null));
    // Reload trang: còn refresh token thì thử /me (access token trong memory đã mất, nhưng
    // apiFetch tự refresh khi 401) — nếu hỏng thì coi như chưa đăng nhập.
    if (!hasRefreshToken()) { setLoading(false); return; }
    authApi.me().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const d = await authApi.login(username, password);
    setTokens(d.access_token, d.refresh_token);
    setUser(d.user);
  }, []);

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth phải nằm trong AuthProvider");
  return ctx;
}

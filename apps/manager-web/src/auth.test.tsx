import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider, useAuth, type AuthCtx } from "./auth";
import { SessionExpired } from "./components/SessionExpired";
import { authApi } from "./api";
import { clearTokens, getAccessToken, getRefreshToken } from "./auth/tokenStore";
import { __resetHttpForTests } from "./lib/http";
import { fakeJwt, installFetch, jsonResponse } from "./test/helpers";

let ctx: AuthCtx;
function Consumer() {
  ctx = useAuth();
  return (
    <div>
      <output data-testid="user">{ctx.user?.username ?? "none"}</output>
      <output data-testid="perms">{ctx.user?.perms.join(",") ?? ""}</output>
      <output data-testid="loading">{String(ctx.loading)}</output>
      <output data-testid="expired">{ctx.sessionExpired ? ctx.sessionReason : "no"}</output>
    </div>
  );
}

function mount() {
  return render(<MemoryRouter><AuthProvider><Consumer /></AuthProvider></MemoryRouter>);
}

const ACCESS = fakeJwt({ sub: "u1", role: "viewer", perms: ["reward:access"], sid: "s1" });
const user = { id: "u1", username: "alice", email: "a@x.y", role: "viewer" };

function allStorageText(): string {
  const parts: string[] = [];
  for (const st of [localStorage, sessionStorage]) {
    for (let i = 0; i < st.length; i++) { const k = st.key(i)!; parts.push(k, st.getItem(k) ?? ""); }
  }
  return parts.join("\n");
}

beforeEach(() => { __resetHttpForTests(); clearTokens(); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetModules(); });

describe("AuthProvider — đăng nhập mật khẩu & lưu token", () => {
  it("login(remember=true): access ở bộ nhớ, refresh ở sessionStorage, localStorage TRỐNG; perms từ claim", async () => {
    installFetch((c) => c.path === "/v1/auth/login"
      ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-1", expires_in: 900, user })
      : undefined);
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => { await ctx.login("alice", "pw-123456", true); });
    expect(screen.getByTestId("user").textContent).toBe("alice");
    expect(screen.getByTestId("perms").textContent).toBe("reward:access");
    expect(ctx.hasPerm("reward:access")).toBe(true);
    expect(ctx.hasPerm("reward:admin")).toBe(false);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.getItem("mp_refresh_token")).toBe("RT-1");
    expect(allStorageText()).not.toContain(ACCESS);
  });

  it("login(remember=false): refresh token KHÔNG nằm trong storage nào", async () => {
    installFetch((c) => c.path === "/v1/auth/login"
      ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-SECRET", expires_in: 900, user })
      : undefined);
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => { await ctx.login("alice", "pw-123456", false); });
    expect(getRefreshToken()).toBe("RT-SECRET");
    expect(allStorageText()).not.toContain("RT-SECRET");
  });

  it("logout: gọi /v1/auth/logout với refresh token rồi xoá SẠCH mọi token và storage", async () => {
    const f = installFetch(
      (c) => c.path === "/v1/auth/login" ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-1", expires_in: 900, user }) : undefined,
      (c) => c.path === "/v1/auth/logout" ? jsonResponse({ ok: true }) : undefined,
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => { await ctx.login("alice", "pw-123456", true); });
    act(() => ctx.logout());
    expect(screen.getByTestId("user").textContent).toBe("none");
    expect(getAccessToken()).toBe("");
    expect(getRefreshToken()).toBe("");
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
    await waitFor(() => expect(f.byPath("/v1/auth/logout")).toHaveLength(1));
    expect(JSON.parse(f.byPath("/v1/auth/logout")[0].body!)).toEqual({ refresh_token: "RT-1" });
  });

  it("F5: khôi phục phiên — pre-refresh lấy AT trước rồi gọi /me (không có 401 trung gian)", async () => {
    sessionStorage.setItem("mp_refresh_token", "RT-PERSISTED");
    const f = installFetch(
      (c) => c.path === "/v1/auth/refresh" ? jsonResponse({ access_token: ACCESS, expires_in: 900 }) : undefined,
      (c) => c.path === "/v1/auth/me" ? jsonResponse({ ...user, created_at: "" }) : undefined,
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("alice"));
    expect(f.byPath("/v1/auth/refresh")).toHaveLength(1);
    expect(f.byPath("/v1/auth/me")).toHaveLength(1);
    expect(screen.getByTestId("perms").textContent).toBe("reward:access");
  });

  it("khoá refresh dài hạn của phiên bản cũ trong localStorage KHÔNG được dùng để đăng nhập", async () => {
    localStorage.setItem("admin_refresh_token", "OLD-LONG-LIVED");
    const f = installFetch(() => jsonResponse({ detail: "x" }, { status: 401 }));
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    expect(f.calls).toHaveLength(0);
    expect(screen.getByTestId("user").textContent).toBe("none");
    expect(localStorage.getItem("admin_refresh_token")).toBeNull();
  });
});

describe("Phiên bị thu hồi / hết hạn", () => {
  it("401 'Phiên đã bị thu hồi' => đăng xuất sạch + cờ revoked cho overlay", async () => {
    installFetch(
      (c) => c.path === "/v1/auth/login" ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-1", expires_in: 900, user }) : undefined,
      (c) => c.path === "/v1/auth/me" ? jsonResponse({ detail: "Phiên đã bị thu hồi" }, { status: 401 }) : undefined,
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => { await ctx.login("alice", "pw-123456", true); });
    await act(async () => { await authApi.me().catch(() => {}); });
    expect(screen.getByTestId("expired").textContent).toBe("revoked");
    expect(getAccessToken()).toBe("");
    expect(getRefreshToken()).toBe("");
    expect(sessionStorage.length).toBe(0);
  });

  it("overlay hiển thị thông báo đúng lý do", () => {
    const { rerender } = render(<SessionExpired onLogin={() => {}} reason="revoked" />);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Phiên đã bị thu hồi");
    rerender(<SessionExpired onLogin={() => {}} reason="expired" />);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Phiên đã hết hạn");
  });

  it("đăng xuất ở tab khác (BroadcastChannel) => tab này cũng thoát", async () => {
    installFetch((c) => c.path === "/v1/auth/login"
      ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-1", expires_in: 900, user })
      : undefined);
    mount();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => { await ctx.login("alice", "pw-123456", true); });
    const other = new BroadcastChannel("mp-auth");
    other.postMessage("logout");
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("none"));
    expect(screen.getByTestId("expired").textContent).toBe("expired");
    other.close();
  });
});

describe("Chế độ OIDC (VITE_AUTH_MODE=oidc)", () => {
  it("callback hợp lệ: đổi code, lưu token đúng chỗ, refresh đi qua /oauth/token (xoay vòng)", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "oidc");
    vi.resetModules();
    const [{ AuthProvider: AP, useAuth: useA }, oidc, store, http] = await Promise.all([
      import("./auth"), import("./auth/oidc"), import("./auth/tokenStore"), import("./lib/http"),
    ]);
    store.clearTokens();
    let c2!: ReturnType<typeof useA>;
    const C = () => { c2 = useA(); return <output data-testid="u">{c2.user?.username ?? "none"}|{c2.mode}</output>; };

    // bước 1 giả lập: sinh giao dịch (không điều hướng thật)
    let authorizeUrl = "";
    await oidc.beginLogin(oidc.getOidcConfig(), "/groups", (u) => { authorizeUrl = u; });
    const state = new URL(authorizeUrl, "http://x").searchParams.get("state")!;

    const f = installFetch(
      (c) => c.path === "/oauth/token" && new URLSearchParams(c.body!).get("grant_type") === "authorization_code"
        ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-OIDC-1", expires_in: 900 }) : undefined,
      (c) => c.path === "/oauth/token" && new URLSearchParams(c.body!).get("grant_type") === "refresh_token"
        ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-OIDC-2", expires_in: 900 }) : undefined,
      (c) => c.path === "/v1/auth/me" ? jsonResponse({ ...user, created_at: "" }) : undefined,
    );
    render(<MemoryRouter><AP><C /></AP></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("u").textContent).toContain("oidc"));

    let returnTo = "";
    await act(async () => { returnTo = await c2.completeOidcLogin(`?code=AUTHCODE&state=${state}`); });
    expect(returnTo).toBe("/groups");
    expect(screen.getByTestId("u").textContent).toBe("alice|oidc");
    expect(store.getRefreshToken()).toBe("RT-OIDC-1");
    expect(localStorage.length).toBe(0);

    // refresh qua /oauth/token: 401 -> refresh_token grant -> token xoay vòng
    f.calls.length = 0;
    let first = true;
    installFetch(
      (c) => c.path === "/oauth/token" ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-OIDC-2" }) : undefined,
      () => first ? ((first = false), jsonResponse({ detail: "exp" }, { status: 401 })) : jsonResponse({ ok: true }),
    );
    await http.apiFetch("/v1/anything");
    expect(store.getRefreshToken()).toBe("RT-OIDC-2");
  });

  it("đăng xuất ở chế độ OIDC: gọi /oauth/logout (không phải /v1/auth/logout) và xoá sạch token", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "oidc");
    vi.resetModules();
    const [{ AuthProvider: AP, useAuth: useA }, oidc, store] = await Promise.all([
      import("./auth"), import("./auth/oidc"), import("./auth/tokenStore"),
    ]);
    store.clearTokens();
    let c2!: ReturnType<typeof useA>;
    const C = () => { c2 = useA(); return <output data-testid="u">{c2.user?.username ?? "none"}</output>; };
    let authorizeUrl = "";
    await oidc.beginLogin(oidc.getOidcConfig(), "/dashboard", (u) => { authorizeUrl = u; });
    const state = new URL(authorizeUrl, "http://x").searchParams.get("state")!;
    const f = installFetch(
      (c) => c.path === "/oauth/token" ? jsonResponse({ access_token: ACCESS, refresh_token: "RT-OIDC-1" }) : undefined,
      (c) => c.path === "/v1/auth/me" ? jsonResponse({ ...user, created_at: "" }) : undefined,
      (c) => c.path === "/oauth/logout" ? new Response(null, { status: 204 }) : undefined,
    );
    render(<MemoryRouter><AP><C /></AP></MemoryRouter>);
    await act(async () => { await c2.completeOidcLogin(`?code=C&state=${state}`); });
    expect(screen.getByTestId("u").textContent).toBe("alice");
    act(() => c2.logout());
    await waitFor(() => expect(f.byPath("/oauth/logout")).toHaveLength(1));
    expect(new URLSearchParams(f.byPath("/oauth/logout")[0].body!).get("refresh_token")).toBe("RT-OIDC-1");
    expect(f.byPath("/v1/auth/logout")).toHaveLength(0);
    expect(store.getRefreshToken()).toBe("");
    expect(store.getAccessToken()).toBe("");
  });

  it("callback sai state => lỗi, KHÔNG có token", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "oidc");
    vi.resetModules();
    const [{ AuthProvider: AP, useAuth: useA }, oidc, store] = await Promise.all([
      import("./auth"), import("./auth/oidc"), import("./auth/tokenStore"),
    ]);
    store.clearTokens();
    let c2!: ReturnType<typeof useA>;
    const C = () => { c2 = useA(); return null; };
    await oidc.beginLogin(oidc.getOidcConfig(), "/dashboard", () => {});
    const f = installFetch(() => jsonResponse({ access_token: "STOLEN", refresh_token: "STOLEN" }));
    render(<MemoryRouter><AP><C /></AP></MemoryRouter>);
    await expect(c2.completeOidcLogin("?code=C&state=FORGED")).rejects.toMatchObject({ code: "state_mismatch" });
    expect(f.calls).toHaveLength(0);
    expect(store.getAccessToken()).toBe("");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import LoginPage from "./LoginPage";
import OidcCallbackPage from "./OidcCallbackPage";
import { PermGate, RoleGate, HomeRedirect } from "../components/RoleGate";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { hasPerm, homePath } from "../auth/permissions";

afterEach(() => vi.unstubAllGlobals());

function authValue(user: User | null, over: Partial<AuthCtx> = {}): AuthCtx {
  const perms = user?.perms ?? [];
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(),
    hasPerm: (p) => hasPerm(user, p) || perms.includes(p), ...over,
  };
}
const mk = (role: string, perms: string[] = []): User => ({ id: "u", username: "n", email: "e", role, perms });

describe("permission / điều hướng theo claim perms", () => {
  it("hasPerm: admin có mọi quyền; dash_admin cần cấp tường minh; role discord (shim) có reward:access", () => {
    expect(hasPerm(mk("admin"), "reward:admin")).toBe(true);
    expect(hasPerm(mk("dash_admin"), "reward:access")).toBe(false);
    expect(hasPerm(mk("dash_admin", ["reward:access"]), "reward:access")).toBe(true);
    expect(hasPerm(mk("viewer", ["reward:access"]), "reward:admin")).toBe(false);
    expect(hasPerm(mk("discord"), "reward:access")).toBe(true);
    expect(hasPerm(mk("discord"), "reward:admin")).toBe(false);
    expect(hasPerm(null, "reward:access")).toBe(false);
  });

  it("homePath: chỉ có reward:access (không có quyền dashboard) -> /reward/jobs; còn lại /dashboard", () => {
    expect(homePath(mk("discord"))).toBe("/reward/jobs");
    expect(homePath(mk("viewer"))).toBe("/dashboard");
    expect(homePath(mk("admin"))).toBe("/dashboard");
    expect(homePath(mk("viewer", ["reward:access"]))).toBe("/dashboard");
  });

  function renderRoutes(user: User, at: string) {
    return render(
      <MemoryRouter initialEntries={[at]}>
        <AuthContext.Provider value={authValue(user)}>
          <Routes>
            <Route element={<PermGate perm="reward:access" />}>
              <Route path="/reward/jobs" element={<div>REWARD-PAGE</div>} />
            </Route>
            <Route element={<RoleGate allow={["admin", "dash_admin", "viewer"]} />}>
              <Route path="/dashboard" element={<div>DASH-PAGE</div>} />
            </Route>
            <Route path="/" element={<HomeRedirect />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );
  }

  it("route Reward: viewer có perm reward:access vào được (không cần role discord)", () => {
    renderRoutes(mk("viewer", ["reward:access"]), "/reward/jobs");
    expect(screen.getByText("REWARD-PAGE")).toBeInTheDocument();
  });

  it("route Reward: viewer KHÔNG có perm không vào được, thấy trang giải thích và nút về trang chính", async () => {
    renderRoutes(mk("viewer"), "/reward/jobs");
    expect(screen.queryByText("REWARD-PAGE")).not.toBeInTheDocument();
    expect(screen.getByText(/Bạn đang đăng nhập với vai trò Người xem/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "Về trang chính" }));
    expect(screen.getByText("DASH-PAGE")).toBeInTheDocument();
  });

  it("route Dashboard: role discord không vào được, trang nêu đúng vai trò và lý do, nút dẫn về /reward/jobs", async () => {
    renderRoutes(mk("discord"), "/dashboard");
    expect(screen.queryByText("DASH-PAGE")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bạn chưa có quyền vào trang này" })).toBeInTheDocument();
    expect(screen.getByText(/vai trò Vận hành Discord/)).toBeInTheDocument();
    expect(screen.getByText(/chỉ dành cho: Quản trị viên, Quản lý Dashboard, Người xem/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "Về trang chính" }));
    expect(screen.getByText("REWARD-PAGE")).toBeInTheDocument();
  });

  it("route Reward: admin được vào; role discord legacy vẫn vào được (shim)", () => {
    const { unmount } = renderRoutes(mk("admin"), "/reward/jobs");
    expect(screen.getByText("REWARD-PAGE")).toBeInTheDocument();
    unmount();
    renderRoutes(mk("discord"), "/reward/jobs");
    expect(screen.getByText("REWARD-PAGE")).toBeInTheDocument();
  });
});

describe("LoginPage", () => {
  it("chế độ password (mặc định): có form tên đăng nhập + mật khẩu", () => {
    render(<MemoryRouter><AuthContext.Provider value={authValue(null)}><LoginPage /></AuthContext.Provider></MemoryRouter>);
    expect(screen.getByLabelText("Tên đăng nhập")).toBeInTheDocument();
    expect(screen.getByLabelText("Mật khẩu")).toBeInTheDocument();
    expect(screen.queryByText("Đăng nhập bằng tài khoản Manager")).not.toBeInTheDocument();
  });

  it("chế độ oidc: chỉ có nút SSO, bấm => loginWithOidc", async () => {
    const loginWithOidc = vi.fn().mockResolvedValue(undefined);
    render(<MemoryRouter><AuthContext.Provider value={authValue(null, { mode: "oidc", loginWithOidc })}><LoginPage /></AuthContext.Provider></MemoryRouter>);
    expect(screen.queryByLabelText("Mật khẩu")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Đăng nhập bằng tài khoản Manager" }));
    expect(loginWithOidc).toHaveBeenCalledWith("/dashboard");
  });
});

describe("OidcCallbackPage", () => {
  it("callback hợp lệ => chuyển tới returnTo", async () => {
    const completeOidcLogin = vi.fn().mockResolvedValue("/groups");
    render(
      <MemoryRouter initialEntries={["/callback?code=C&state=S"]}>
        <AuthContext.Provider value={authValue(null, { completeOidcLogin })}>
          <Routes>
            <Route path="/callback" element={<OidcCallbackPage />} />
            <Route path="/groups" element={<div>GROUPS</div>} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    await screen.findByText("GROUPS");
    expect(completeOidcLogin).toHaveBeenCalledWith("?code=C&state=S");
  });

  it("callback bị từ chối (state sai) => hiển thị lỗi và liên kết quay lại đăng nhập, không chuyển trang", async () => {
    const completeOidcLogin = vi.fn().mockRejectedValue(Object.assign(new Error("Tham số state không khớp — yêu cầu bị từ chối"), { code: "state_mismatch" }));
    render(
      <MemoryRouter initialEntries={["/callback?code=C&state=FORGED"]}>
        <AuthContext.Provider value={authValue(null, { completeOidcLogin })}>
          <Routes>
            <Route path="/callback" element={<OidcCallbackPage />} />
            <Route path="/groups" element={<div>GROUPS</div>} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("state không khớp");
    expect(screen.getByRole("link", { name: "Quay lại đăng nhập" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("GROUPS")).not.toBeInTheDocument());
  });
});

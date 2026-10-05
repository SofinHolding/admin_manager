import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import AdminPage from "./AdminPage";
import { buildKeyExtra, DEFAULT_POLICY_SCOPES, parseList } from "./adminKeyForm";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const admin: User = { id: "a1", username: "root", email: "r@x", role: "admin", perms: [] };
const dashAdmin: User = { id: "d1", username: "dash", email: "d@x", role: "dash_admin", perms: [] };

function authValue(user: User, perms: string[] | "all"): AuthCtx {
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: (p) => perms === "all" || perms.includes(p),
  };
}

function renderAdmin(u: User = admin, perms: string[] | "all" = "all") {
  return render(<MemoryRouter><AuthContext.Provider value={authValue(u, perms)}><AdminPage /></AuthContext.Provider></MemoryRouter>);
}

const bob = { id: "u2", username: "bob", email: "b@x", role: "viewer", status: "active", created_at: "2026-10-01T00:00:00Z", last_login: "", invite_key: "" };

function server(state: { perms: string[] }): Responder[] {
  return [
    (c) => c.method === "GET" && c.path === "/v1/admin/keys" ? jsonResponse({ keys: [] }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/admin/keys"
      ? jsonResponse({ key: "KEY-1", role: "viewer", label: "", max_uses: 1, used_count: 0, created_at: "2026-10-05T00:00:00Z", expires_at: "2026-10-12T00:00:00Z", status: "active" }) : undefined,
    (c) => c.path === "/v1/admin/users" ? jsonResponse({ users: [bob] }) : undefined,
    (c) => c.path === "/v1/admin/permissions" ? jsonResponse({ permissions: [{ name: "reward:access" }, { name: "reward:admin" }] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/accounts/u2/permissions" ? jsonResponse({ account_id: "u2", permissions: state.perms }) : undefined,
    (c) => c.method === "PUT" && c.path.startsWith("/v1/admin/accounts/u2/permissions/") ? ((state.perms = [...state.perms, decodeURIComponent(c.path.split("/").pop()!)]), jsonResponse({ ok: true, granted: true })) : undefined,
    (c) => c.method === "DELETE" && c.path.startsWith("/v1/admin/accounts/u2/permissions/") ? ((state.perms = state.perms.filter((p) => p !== decodeURIComponent(c.path.split("/").pop()!))), jsonResponse({ ok: true, granted: false })) : undefined,
  ];
}

describe("adminKeyForm (thuần)", () => {
  it("parseList tách theo dấu phẩy/khoảng trắng, hạ chữ thường, bỏ trùng", () => {
    expect(parseList("Facebook, youtube;facebook  TikTok")).toEqual(["facebook", "youtube", "tiktok"]);
    expect(parseList("")).toEqual([]);
  });

  it("không bật policy và không có app => extra rỗng", () => {
    expect(buildKeyExtra({ allowIntegration: false, platforms: "", scopes: [], maxClients: "1", apps: "" })).toEqual({ ok: true, extra: {} });
  });

  it("bật policy => integration_policy đầy đủ 4 trường + allowed_apps", () => {
    const r = buildKeyExtra({ allowIntegration: true, platforms: "facebook, youtube", scopes: [...DEFAULT_POLICY_SCOPES], maxClients: "3", apps: "reward" });
    expect(r).toEqual({
      ok: true,
      extra: {
        integration_policy: {
          allow_integration_registration: true, allowed_platforms: ["facebook", "youtube"],
          allowed_scopes: [...DEFAULT_POLICY_SCOPES], max_integration_clients: 3,
        },
        allowed_apps: ["reward"],
      },
    });
  });

  it("từ chối đầu vào sai: thiếu nền tảng/scope, max ngoài 1..100, tên app lạ, scope không thuộc danh sách cho phép", () => {
    const base = { allowIntegration: true, platforms: "facebook", scopes: ["resources:write"], maxClients: "1", apps: "" };
    expect(buildKeyExtra({ ...base, platforms: "" })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, scopes: [] })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, scopes: ["jobs:claim:system"] })).toMatchObject({ ok: false }); // chỉ admin cấp riêng qua PATCH client
    expect(buildKeyExtra({ ...base, maxClients: "0" })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, maxClients: "101" })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, maxClients: "1.5" })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, apps: "<script>" })).toMatchObject({ ok: false });
    expect(buildKeyExtra({ ...base, platforms: "face book!" })).toMatchObject({ ok: false });
  });
});

describe("AdminPage — tạo invite key", () => {
  it("không chọn gì thêm: body như cũ (không có integration_policy / allowed_apps)", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /Tạo key/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST" && c.path === "/v1/admin/keys")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body).toEqual({ role: "viewer", label: "", max_uses: 1, expires_days: 7 });
  });

  it("nhập integration_policy + allowed_apps => gửi đúng body", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /tuỳ chọn nâng cao/ }));
    const form = screen.getByTestId("policy-form");
    await user.click(within(form).getByRole("checkbox", { name: /allow_integration_registration/ }));
    const plat = within(form).getByLabelText(/allowed_platforms/);
    await user.clear(plat);
    await user.type(plat, "facebook, tiktok");
    const max = within(form).getByLabelText(/max_integration_clients/);
    await user.clear(max);
    await user.type(max, "2");
    await user.click(within(form).getByRole("checkbox", { name: "jobs:claim" }));
    await user.type(within(form).getByLabelText(/allowed_apps/), "reward");
    await user.click(screen.getByRole("button", { name: /Tạo key/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body.integration_policy).toEqual({
      allow_integration_registration: true,
      allowed_platforms: ["facebook", "tiktok"],
      allowed_scopes: [...DEFAULT_POLICY_SCOPES, "jobs:claim"],
      max_integration_clients: 2,
    });
    expect(body.allowed_apps).toEqual(["reward"]);
  });

  it("policy không hợp lệ (thiếu nền tảng) => không gọi API", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /tuỳ chọn nâng cao/ }));
    const form = screen.getByTestId("policy-form");
    await user.click(within(form).getByRole("checkbox", { name: /allow_integration_registration/ }));
    await user.clear(within(form).getByLabelText(/allowed_platforms/));
    await user.click(screen.getByRole("button", { name: /Tạo key/ }));
    expect(f.calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("AdminPage — quản lý permission của tài khoản", () => {
  it("xem quyền, cấp reward:access (PUT) rồi thu (DELETE) qua Identity", async () => {
    const state = { perms: [] as string[] };
    const f = installFetch(...server(state));
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    await user.click(screen.getByRole("button", { name: "Quản lý quyền của bob" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("reward:access")).toBeInTheDocument();
    expect(f.calls.some((c) => c.method === "GET" && c.path === "/v1/accounts/u2/permissions")).toBe(true);

    await user.click(within(dialog).getByRole("button", { name: "Cấp quyền reward:access" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "PUT" && c.path === "/v1/admin/accounts/u2/permissions/reward:access")).toBe(true));
    await within(dialog).findByRole("button", { name: "Thu quyền reward:access" });

    await user.click(within(dialog).getByRole("button", { name: "Thu quyền reward:access" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/accounts/u2/permissions/reward:access")).toBe(true));
    await within(dialog).findByRole("button", { name: "Cấp quyền reward:access" });
  });

  it("không còn endpoint cấp quyền riêng của reward-service (grant-discord/revoke-discord)", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    expect(f.calls.some((c) => /grant-discord|revoke-discord/.test(c.path))).toBe(false);
  });
});

describe("AdminPage — tab Reward theo permission reward:admin", () => {
  it("có reward:admin => thấy 3 tab Reward", () => {
    installFetch(...server({ perms: [] }));
    renderAdmin(admin, "all");
    expect(screen.getByRole("tab", { name: /Job toàn hệ thống/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Khoá kênh/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Nhật ký role/ })).toBeInTheDocument();
  });

  it("dash_admin không có reward:admin => không thấy tab Reward (không còn 403 vô ích)", () => {
    installFetch(...server({ perms: [] }));
    renderAdmin(dashAdmin, []);
    expect(screen.queryByRole("tab", { name: /Job toàn hệ thống/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Mã mời/ })).toBeInTheDocument();
  });
});

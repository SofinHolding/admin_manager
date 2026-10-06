import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import AdminPage, { relativeExpiry } from "./AdminPage";
import { adminErrorText, buildKeyExtra, DEFAULT_POLICY_SCOPES, parseList } from "./adminKeyForm";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";
import { ApiError } from "../lib/errors";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const DAY_MS = 86_400_000;
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
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST" && c.path === "/v1/admin/keys")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body).toEqual({ role: "viewer", label: "", max_uses: 1, expires_days: 7 });
  });

  it("nhập integration_policy + allowed_apps => gửi đúng body", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByText("Tùy chọn nâng cao"));
    const form = screen.getByTestId("policy-form");
    await user.click(within(form).getByRole("checkbox", { name: /Cho phép tự tạo công cụ đẩy dữ liệu/ }));
    const plat = within(form).getByLabelText(/Nền tảng được phép/);
    await user.clear(plat);
    await user.type(plat, "facebook, tiktok");
    const max = within(form).getByLabelText(/Số công cụ tối đa/);
    await user.clear(max);
    await user.type(max, "2");
    await user.click(within(form).getByRole("checkbox", { name: /\(jobs:claim\)/ }));
    await user.type(within(form).getByLabelText(/Ứng dụng được mở sẵn/), "reward");
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
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
    await user.click(screen.getByText("Tùy chọn nâng cao"));
    const form = screen.getByTestId("policy-form");
    await user.click(within(form).getByRole("checkbox", { name: /Cho phép tự tạo công cụ đẩy dữ liệu/ }));
    await user.clear(within(form).getByLabelText(/Nền tảng được phép/));
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
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
  it("có reward:admin => thấy các mục Phân phối điểm và Khóa kênh", () => {
    installFetch(...server({ perms: [] }));
    renderAdmin(admin, "all");
    expect(screen.getByRole("tab", { name: /Phân phối điểm/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Khóa kênh/ })).toBeInTheDocument();
  });

  it("dash_admin không có reward:admin => không thấy mục Reward (không còn 403 vô ích)", () => {
    installFetch(...server({ perms: [] }));
    renderAdmin(dashAdmin, []);
    expect(screen.queryByRole("tab", { name: /Phân phối điểm/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Khóa kênh/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Mã mời/ })).toBeInTheDocument();
  });
});

const rootUser = { ...bob, id: "u9", username: "rootadmin", email: "root@x", role: "admin" };

function usersServer(extra: Responder[] = []): Responder[] {
  return [
    ...extra,
    (c) => c.method === "GET" && c.path === "/v1/admin/users" ? jsonResponse({ users: [bob, rootUser] }) : undefined,
    (c) => c.method === "PATCH" && c.path === "/v1/admin/users/u2/status" ? jsonResponse({ ok: true, status: "suspended" }) : undefined,
    (c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2" ? jsonResponse({ ok: true }) : undefined,
    ...server({ perms: [] }),
  ];
}

const REASON = "Chỉ quản trị viên mới thao tác được tài khoản này";

describe("AdminPage — quản lý dashboard không thao tác được quản trị viên", () => {
  it("hiện Notice đầu trang nói rõ việc không được làm", () => {
    installFetch(...usersServer());
    renderAdmin(dashAdmin, []);
    expect(screen.getByText(/không khóa, sửa hay xóa được tài khoản quản trị viên/i)).toBeInTheDocument();
  });

  it("admin thường không thấy Notice đó", () => {
    installFetch(...usersServer());
    renderAdmin(admin, "all");
    expect(screen.queryByText(/Bạn đang dùng vai trò Quản lý Dashboard/)).not.toBeInTheDocument();
  });

  it("dòng quản trị viên: nút Sửa/Quyền/Khóa vô hiệu + lời giải thích; dòng người xem vẫn dùng được", async () => {
    installFetch(...usersServer());
    renderAdmin(dashAdmin, []);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("rootadmin");
    for (const name of ["Sửa rootadmin", "Quản lý quyền của rootadmin", "Khóa rootadmin"]) {
      const b = screen.getByRole("button", { name });
      expect(b).toBeDisabled();
      expect(b).toHaveAccessibleDescription(REASON);
    }
    expect(screen.getByRole("button", { name: "Sửa bob" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Khóa bob" })).toBeEnabled();
    expect(screen.getAllByText(REASON)).toHaveLength(1);
  });

  it("quản trị viên thao tác được dòng quản trị viên", async () => {
    installFetch(...usersServer());
    renderAdmin(admin, "all");
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("rootadmin");
    expect(screen.getByRole("button", { name: "Khóa rootadmin" })).toBeEnabled();
    expect(screen.queryByText(REASON)).not.toBeInTheDocument();
  });

  it("server vẫn trả 403 ADMIN_TARGET_FORBIDDEN => hiện lời dễ hiểu, không hiện mã lỗi", async () => {
    const f = installFetch(
      (c) => c.method === "PATCH" && c.path === "/v1/admin/users/u2/status"
        ? jsonResponse({ ok: false, error: { code: "ADMIN_TARGET_FORBIDDEN", message: "forbidden" } }, { status: 403 }) : undefined,
      ...usersServer(),
    );
    renderAdmin(dashAdmin, []);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    await user.click(screen.getByRole("button", { name: "Khóa bob" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Khóa tài khoản" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(REASON);
    expect(alert).not.toHaveTextContent("ADMIN_TARGET_FORBIDDEN");
    expect(f.calls.some((c) => c.method === "PATCH")).toBe(true);
  });
});

describe("AdminPage — xác nhận trước khi khóa/xóa", () => {
  it("khóa: hộp xác nhận nêu hậu quả; Hủy thì không gọi API; Xác nhận thì PATCH", async () => {
    const f = installFetch(...usersServer());
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    await user.click(screen.getByRole("button", { name: "Khóa bob" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Người này sẽ bị đăng xuất ngay/)).toBeInTheDocument();
    expect(f.calls.some((c) => c.method === "PATCH")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Hủy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(f.calls.some((c) => c.method === "PATCH")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Khóa bob" }));
    dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Khóa tài khoản" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "PATCH" && c.path === "/v1/admin/users/u2/status")).toBe(true));
    expect(JSON.parse(f.calls.find((c) => c.method === "PATCH")!.body!)).toEqual({ status: "suspended" });
    expect(await screen.findByRole("button", { name: "Mở khóa bob" })).toBeInTheDocument();
  });

  it("xóa: nằm trong hộp Sửa (không sát nút an toàn), qua bước xác nhận mới gọi DELETE", async () => {
    const f = installFetch(...usersServer());
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    expect(screen.queryByRole("button", { name: /Xóa/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sửa bob" }));
    await user.click(await screen.findByRole("button", { name: /Xóa tài khoản…/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/không hoàn tác được/)).toBeInTheDocument();
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Xóa tài khoản" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/users/u2")).toBe(true));
    await waitFor(() => expect(screen.queryByText("bob")).not.toBeInTheDocument());
  });

  it("tìm kiếm lọc theo tên/email", async () => {
    installFetch(...usersServer());
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    await user.type(screen.getByLabelText("Tìm tài khoản"), "root@");
    expect(screen.queryByText("bob")).not.toBeInTheDocument();
    expect(screen.getByText("rootadmin")).toBeInTheDocument();
  });

  it("vai trò hiện bằng tên tiếng Việt", async () => {
    installFetch(...usersServer());
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    expect(screen.getByText("Người xem")).toBeInTheDocument();
    expect(screen.getAllByText("Quản trị viên").length).toBeGreaterThan(0);
  });
});

describe("AdminPage — mã mời: sao chép, hạn dùng, thu hồi", () => {
  const live = {
    key: "KEY-LIVE", role: "viewer", label: "nhóm A", max_uses: 1, used_count: 0, status: "active",
    created_at: new Date(Date.now() - DAY_MS).toISOString(), expires_at: new Date(Date.now() + 6.5 * DAY_MS).toISOString(),
  };
  function keysServer(): Responder[] {
    return [
      (c) => c.method === "GET" && c.path === "/v1/admin/keys" ? jsonResponse({ keys: [live] }) : undefined,
      (c) => c.method === "DELETE" && c.path.startsWith("/v1/admin/keys") ? jsonResponse({ ok: true }) : undefined,
      ...server({ perms: [] }),
    ];
  }

  it("tạo xong hiện mã nổi bật + 'Sao chép mã' đổi thành 'Đã sao chép' và chép vào clipboard", async () => {
    installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    expect(await screen.findByTestId("created-key")).toHaveTextContent("KEY-1");
    await user.click(screen.getByRole("button", { name: "Sao chép mã" }));
    expect(await screen.findByRole("button", { name: "Đã sao chép" })).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toBe("KEY-1");
  });

  it("danh sách: trạng thái bằng chữ, hạn tương đối 'còn 6 ngày'", async () => {
    installFetch(...keysServer());
    renderAdmin();
    expect(await screen.findByText("Dùng được")).toBeInTheDocument();
    expect(screen.getByText("còn 6 ngày")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("Người xem")).toBeInTheDocument();
  });

  it("relativeExpiry: ngày, giờ, đã hết hạn", () => {
    const now = Date.parse("2026-10-06T00:00:00Z");
    expect(relativeExpiry("2026-10-12T12:00:00Z", now)).toBe("còn 6 ngày");
    expect(relativeExpiry("2026-10-06T05:30:00Z", now)).toBe("còn 5 giờ");
    expect(relativeExpiry("2026-10-06T00:10:00Z", now)).toBe("còn dưới 1 giờ");
    expect(relativeExpiry("2026-10-04T00:00:00Z", now)).toBe("đã hết hạn 2 ngày trước");
  });

  it("thu hồi có xác nhận; Hủy thì không gọi API", async () => {
    const f = installFetch(...keysServer());
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText("KEY-LIVE");
    await user.click(screen.getByRole("button", { name: "Thu hồi mã KEY-LIVE" }));
    let dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Hủy" }));
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Thu hồi mã KEY-LIVE" }));
    dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Thu hồi mã mời" }));
    expect(await screen.findByText("Đã thu hồi")).toBeInTheDocument();
    expect(f.calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/keys/KEY-LIVE")).toBe(true);
  });
});

describe("AdminPage — lỗi API hiện bằng lời dễ hiểu", () => {
  it("tạo mã thất bại => Notice lỗi trong trang", async () => {
    installFetch(
      (c) => c.method === "POST" && c.path === "/v1/admin/keys" ? jsonResponse({ detail: "Phiên chưa đủ quyền" }, { status: 400 }) : undefined,
      ...server({ perms: [] }),
    );
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Phiên chưa đủ quyền");
  });

  it("adminErrorText: 403 chung, lỗi thường và giá trị lạ", () => {
    expect(adminErrorText(new ApiError("x", { status: 403 }), "f")).toMatch(/không có quyền/);
    expect(adminErrorText(new ApiError("Hết giờ", { status: 500 }), "f")).toBe("Hết giờ");
    expect(adminErrorText("lạ", "mặc định")).toBe("mặc định");
  });
});

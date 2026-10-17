import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import AdminPage, { relativeExpiry } from "./AdminPage";
import { adminErrorText, parseList, validateKeyParams } from "./adminKeyForm";
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

const PRESETS = [
  { key: "viewer", label: "Người xem", role: "viewer", capabilities: ["capability:web_access", "capability:dashboard_access", "capability:manual_channel_mgmt"], admin_only: false },
  { key: "viewer_tool", label: "Người xem + Tool riêng", role: "viewer", capabilities: ["capability:web_access", "capability:dashboard_access", "capability:manual_channel_mgmt", "capability:tool_integration"], admin_only: false },
  { key: "dash_admin", label: "Quản lý Dashboard", role: "dash_admin", capabilities: ["capability:web_access", "capability:dashboard_access"], admin_only: false },
  { key: "admin", label: "Quản trị viên", role: "admin", capabilities: ["capability:web_access"], admin_only: true },
  { key: "discord", label: "Vận hành Discord", role: "discord", capabilities: [], admin_only: false },
];

function server(state: { perms: string[] }): Responder[] {
  return [
    (c) => c.method === "GET" && c.path === "/v1/admin/keys" ? jsonResponse({ keys: [] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/admin/keys/presets" ? jsonResponse({ presets: PRESETS }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/admin/keys"
      ? jsonResponse({ key: "KEY-1", role: "viewer", preset: "viewer", label: "", max_uses: 1, used_count: 0, created_at: "2026-10-05T00:00:00Z", expires_at: "2026-10-12T00:00:00Z", status: "active" }) : undefined,
    (c) => c.path === "/v1/admin/users" ? jsonResponse({ users: [bob] }) : undefined,
    (c) => c.path === "/v1/integrations/clients" ? jsonResponse({ items: [] }) : undefined,
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
});

describe("AdminPage — tạo mã mời theo preset", () => {
  it("mặc định preset=viewer: body chỉ gồm preset/label/max_uses/expires_days", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST" && c.path === "/v1/admin/keys")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body).toEqual({ preset: "viewer", label: "", max_uses: 1, expires_days: 7 });
  });

  it("đổi gói quyền sang viewer_tool => preset gửi đúng", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("combobox", { name: /Gói quyền/ }));
    await user.click(await screen.findByRole("option", { name: "Người xem + Tool riêng" }));
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body.preset).toBe("viewer_tool");
  });

  it("dash_admin không thấy preset admin_only trong danh sách", async () => {
    installFetch(...server({ perms: [] }));
    renderAdmin(dashAdmin, []);
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    await user.click(screen.getByRole("combobox", { name: /Gói quyền/ }));
    expect(screen.queryByRole("option", { name: "Quản trị viên" })).toBeNull();
    expect(await screen.findByRole("option", { name: "Người xem" })).toBeInTheDocument();
  });
});

describe("AdminPage — sửa mã mời (PATCH)", () => {
  const live = {
    key: "KEY-LIVE", role: "viewer", preset: "viewer", label: "nhóm A", max_uses: 1, used_count: 0, status: "active",
    created_at: new Date(Date.now() - DAY_MS).toISOString(), expires_at: new Date(Date.now() + 6.5 * DAY_MS).toISOString(),
  };

  function keysServer(): Responder[] {
    return [
      (c) => c.method === "GET" && c.path === "/v1/admin/keys" ? jsonResponse({ keys: [live] }) : undefined,
      (c) => c.method === "PATCH" && c.path === "/v1/admin/keys/KEY-LIVE"
        ? jsonResponse({ ...live, label: "nhóm B" }) : undefined,
      ...server({ perms: [] }),
    ];
  }

  it("mở dialog sửa, chỉ đổi ghi chú => PATCH chỉ gửi label", async () => {
    const f = installFetch(...keysServer());
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText("KEY-LIVE");
    await user.click(screen.getByRole("button", { name: "Sửa mã KEY-LIVE" }));
    const dialog = await screen.findByRole("dialog");
    const labelInput = within(dialog).getByLabelText("Ghi chú");
    await user.clear(labelInput);
    await user.type(labelInput, "nhóm B");
    await user.click(within(dialog).getByRole("button", { name: "Lưu thay đổi" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "PATCH" && c.path === "/v1/admin/keys/KEY-LIVE")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "PATCH")!.body!);
    expect(body).toEqual({ label: "nhóm B" });
  });

  it("dialog ghi rõ chỉ áp dụng cho lần đăng ký sau", async () => {
    installFetch(...keysServer());
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText("KEY-LIVE");
    await user.click(screen.getByRole("button", { name: "Sửa mã KEY-LIVE" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Chỉ áp dụng cho lần đăng ký sau, không đổi tài khoản đã tạo/)).toBeInTheDocument();
  });
});

describe("AdminPage — suspend/unsuspend invite key", () => {
  const activeKey = { key: "ACTV-1234", role: "viewer", preset: "viewer", label: "", max_uses: 5, used_count: 0, created_at: "2026-10-05T00:00:00Z", expires_at: "2030-01-01T00:00:00Z", status: "active" };
  const suspendedKey = { ...activeKey, key: "SUSP-5678", status: "suspended" };

  function keyServer(state: { keys: typeof activeKey[] }): Responder[] {
    return [
      (c) => c.method === "GET" && c.path === "/v1/admin/keys" ? jsonResponse({ keys: state.keys }) : undefined,
      (c) => c.method === "PATCH" && c.path === `/v1/admin/keys/${state.keys[0]?.key}/suspend`
        ? ((state.keys[0].status = "suspended"), jsonResponse({ ok: true })) : undefined,
      (c) => c.method === "PATCH" && c.path === `/v1/admin/keys/${state.keys[0]?.key}/unsuspend`
        ? ((state.keys[0].status = "active"), jsonResponse({ ok: true })) : undefined,
      (c) => c.method === "GET" && c.path === "/v1/admin/keys/presets" ? jsonResponse({ presets: PRESETS }) : undefined,
      (c) => c.path === "/v1/admin/users" ? jsonResponse({ users: [] }) : undefined,
      (c) => c.path === "/v1/integrations/clients" ? jsonResponse({ items: [] }) : undefined,
      (c) => c.path === "/v1/admin/permissions" ? jsonResponse({ permissions: [] }) : undefined,
    ];
  }

  it("key active: nút Tạm dừng hiện, xác nhận => gọi PATCH suspend, badge đổi thành Tạm dừng", async () => {
    const state = { keys: [{ ...activeKey }] };
    const f = installFetch(...keyServer(state));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText("ACTV-1234");
    await user.click(screen.getByRole("button", { name: /Tạm dừng mã ACTV-1234/ }));
    await screen.findByText("Tạm dừng mã mời này?");
    await user.click(screen.getByRole("button", { name: /Tạm dừng mã mời$/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "PATCH" && c.path.includes("suspend"))).toBe(true));
    await screen.findByText("Tạm dừng");   // badge trạng thái
  });

  it("key suspended: nút Khôi phục hiện, xác nhận => gọi PATCH unsuspend", async () => {
    const state = { keys: [{ ...suspendedKey }] };
    const f = installFetch(...keyServer(state));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText("SUSP-5678");
    expect(screen.getByRole("button", { name: /Khôi phục mã SUSP-5678/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Khôi phục mã SUSP-5678/ }));
    await screen.findByText("Khôi phục mã mời này?");
    await user.click(screen.getByRole("button", { name: /Khôi phục mã mời$/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "PATCH" && c.path.includes("unsuspend"))).toBe(true));
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

  it("tài khoản không có khóa Tool => cột Khóa Tool hiện gợi ý cấp quyền", async () => {
    installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Tài khoản/ }));
    await screen.findByText("bob");
    expect(screen.getByRole("button", { name: "Cấp quyền Tool trong Phân quyền" })).toBeInTheDocument();
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
    key: "KEY-LIVE", role: "viewer", preset: "viewer", label: "nhóm A", max_uses: 1, used_count: 0, status: "active",
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

  it("danh sách: trạng thái bằng chữ, hạn tương đối 'còn 6 ngày', gói quyền hiện đúng tên", async () => {
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

  it("thu hồi có xác nhận; sau khi thu hồi key bị ẩn, toggle 'Hiện đã thu hồi' mới thấy lại", async () => {
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
    // sau khi thu hồi: key biến khỏi danh sách (showRevoked=false), API được gọi
    await waitFor(() => expect(screen.queryByText("KEY-LIVE")).not.toBeInTheDocument());
    expect(f.calls.some((c) => c.method === "DELETE" && c.path === "/v1/admin/keys/KEY-LIVE")).toBe(true);
    // toggle "Hiện đã thu hồi" → key quay lại với trạng thái Đã thu hồi
    await user.click(screen.getByRole("button", { name: /Hiện đã thu hồi/ }));
    expect(await screen.findByText("KEY-LIVE")).toBeInTheDocument();
    expect(screen.getByText("Đã thu hồi")).toBeInTheDocument();
  });
});

describe("validateKeyParams (thuần)", () => {
  it("hợp lệ: trả về null", () => {
    expect(validateKeyParams("1", "7")).toBeNull();
    expect(validateKeyParams("1000", "365")).toBeNull();
    expect(validateKeyParams("50", "30")).toBeNull();
  });

  it("max_uses ngoài 1..1000 => lỗi", () => {
    expect(validateKeyParams("0", "7")).toMatch(/1 đến 1000/);
    expect(validateKeyParams("1001", "7")).toMatch(/1 đến 1000/);
    expect(validateKeyParams("", "7")).toMatch(/1 đến 1000/);
    expect(validateKeyParams("1.5", "7")).toMatch(/1 đến 1000/);
  });

  it("expires_days ngoài 1..365 => lỗi", () => {
    expect(validateKeyParams("1", "0")).toMatch(/1 đến 365/);
    expect(validateKeyParams("1", "366")).toMatch(/1 đến 365/);
    expect(validateKeyParams("1", "abc")).toMatch(/1 đến 365/);
  });
});

describe("AdminPage — mã mời: max_uses / expires_days (UI)", () => {
  it("nhập max_uses=5 và expires_days=30 => POST body có đúng giá trị đó", async () => {
    const f = installFetch(...server({ perms: [] }));
    renderAdmin();
    const user = userEvent.setup();
    await screen.findByText(/Chưa có mã mời nào/);
    const maxUsesInput = screen.getByLabelText(/Số lần dùng tối đa/);
    await user.clear(maxUsesInput);
    await user.type(maxUsesInput, "5");
    const expDaysInput = screen.getByLabelText(/Thời hạn \(ngày\)/);
    await user.clear(expDaysInput);
    await user.type(expDaysInput, "30");
    await user.click(screen.getByRole("button", { name: /Tạo mã mời/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST")).toBe(true));
    const body = JSON.parse(f.calls.find((c) => c.method === "POST")!.body!);
    expect(body.max_uses).toBe(5);
    expect(body.expires_days).toBe(30);
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

describe("AdminPage — Công cụ dùng chung: wizard không cho nhảy bước", () => {
  const draft = {
    client_id: "cli_1", owner_account_id: "a1", tool_name: "Fallback Collector", client_kind: "fallback_tool",
    status: "draft", ownership: "shared", purpose: "Thu thập hộ", contract_version: "2026-10-01",
    allowed_platforms: [] as string[], allowed_scopes: [] as string[], created_from: "api",
    configured_at: null as string | null, activated_at: null as string | null, activated_by: null as string | null,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", credentials: [] as unknown[],
  };

  function toolsServer(state: { client: typeof draft }): Responder[] {
    return [
      (c) => c.method === "GET" && c.path === "/v1/integrations/clients" ? jsonResponse({ items: [state.client] }) : undefined,
      (c) => c.method === "GET" && c.path === "/v1/integrations/clients/cli_1"
        ? jsonResponse({ ...state.client, installations: [], credentials: [] }) : undefined,
      (c) => c.method === "GET" && c.path === "/v1/integrations/admin/platforms"
        ? jsonResponse({ ok: true, data: [{ key: "facebook", display_name: "Facebook", enabled: true }] }) : undefined,
      (c) => c.method === "POST" && c.path === "/v1/integrations/clients/cli_1/activate"
        ? jsonResponse({ ...state.client, status: "active", activated_at: "2026-10-02T00:00:00Z" }) : undefined,
      ...server({ perms: [] }),
    ];
  }

  it("client ở draft, chưa cấu hình: bước 3 chưa cho kích hoạt (không có ô xác nhận)", async () => {
    const state = { client: { ...draft } };
    installFetch(...toolsServer(state));
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Công cụ dùng chung/ }));
    await screen.findByText("Fallback Collector");
    await user.click(screen.getByText("Fallback Collector"));
    await screen.findByText("Bước 2 — Cấu hình");
    expect(screen.getByText("Lưu cấu hình ở bước 2 trước đã.")).toBeInTheDocument();
    expect(screen.queryByText(/Gõ lại tên Tool để xác nhận/)).toBeNull();
    expect(screen.getByText("Kích hoạt ở bước 3 trước đã.")).toBeInTheDocument();
  });

  it("đã cấu hình (configured_at) nhưng chưa active: cho gõ xác nhận kích hoạt; sai tên thì nút vô hiệu", async () => {
    const state = { client: { ...draft, configured_at: "2026-10-01T12:00:00Z", allowed_platforms: ["facebook"], allowed_scopes: ["integration:handshake"] } };
    installFetch(...toolsServer(state));
    renderAdmin();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Công cụ dùng chung/ }));
    await screen.findByText("Fallback Collector");
    await user.click(screen.getByText("Fallback Collector"));
    const confirmInput = await screen.findByLabelText(/Gõ lại tên Tool để xác nhận/);
    const activateBtn = screen.getByRole("button", { name: "Kích hoạt" });
    expect(activateBtn).toBeDisabled();
    await user.type(confirmInput, "tên sai");
    expect(activateBtn).toBeDisabled();
    await user.clear(confirmInput);
    await user.type(confirmInput, "Fallback Collector");
    expect(activateBtn).toBeEnabled();
    expect(screen.getByText("Kích hoạt ở bước 3 trước đã.")).toBeInTheDocument(); // bước 4 vẫn khóa
  });

  it("dash_admin chỉ xem: không có nút tạo/cấu hình/kích hoạt/cấp khóa", async () => {
    const state = { client: { ...draft } };
    installFetch(...toolsServer(state));
    renderAdmin(dashAdmin, []);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Công cụ dùng chung/ }));
    expect(screen.getByText(/chế độ chỉ đọc/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Tạo nháp/ })).toBeNull();
    await screen.findByText("Fallback Collector");
    await user.click(screen.getByText("Fallback Collector"));
    await screen.findByText("Bước 2 — Cấu hình");
    expect(screen.queryByRole("button", { name: "Lưu cấu hình" })).toBeNull();
  });
});

describe("AdminPage — chuẩn hoá quốc gia (cả admin lẫn dash_admin)", () => {
  function countriesServer(state: { canon: string[]; aliases: { alias: string; canonical: string }[] }): Responder[] {
    return [
      ...server({ perms: [] }),
      (c) => c.method === "GET" && c.path === "/v1/analytics/admin/country-canonicals"
        ? jsonResponse({ items: state.canon.map((name) => ({ name, created_at: "" })) }) : undefined,
      (c) => c.method === "GET" && c.path === "/v1/analytics/admin/country-aliases"
        ? jsonResponse({ items: state.aliases.map((a) => ({ ...a, created_at: "" })) }) : undefined,
      (c) => {
        if (c.method !== "POST" || c.path !== "/v1/analytics/admin/country-canonicals") return undefined;
        const name = JSON.parse(c.body!).name; state.canon.push(name); return jsonResponse({ name, created_at: "" });
      },
      (c) => {
        if (c.method !== "POST" || c.path !== "/v1/analytics/admin/country-aliases") return undefined;
        const b = JSON.parse(c.body!); state.aliases.push(b); return jsonResponse({ alias: b.alias.toLowerCase(), canonical: b.canonical });
      },
    ];
  }

  it.each([["admin", admin], ["dash_admin", dashAdmin]])("%s thấy tab Quốc gia, tạo tên chuẩn rồi gộp cách viết người dùng tự gõ", async (_n, who) => {
    const f = installFetch(...countriesServer({ canon: [], aliases: [] }));
    renderAdmin(who, []);                                   // không có quyền domain nào: vẫn phải dùng được
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Quốc gia" }));

    await user.type(await screen.findByLabelText("Tên chuẩn mới"), "Nigeria");
    await user.click(screen.getByRole("button", { name: /Tạo tên chuẩn/ }));
    await waitFor(() => expect(f.byPath("/v1/analytics/admin/country-canonicals").some((c) => c.method === "POST")).toBe(true));

    await user.type(screen.getByLabelText("Cách viết khác"), "naija");
    await user.click(screen.getByRole("combobox", { name: /Gộp về tên chuẩn/ }));
    await user.click(await screen.findByRole("option", { name: "Nigeria" }));
    await user.click(screen.getByRole("button", { name: /Lưu cách viết/ }));
    await waitFor(() => expect(f.byPath("/v1/analytics/admin/country-aliases").some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(f.byPath("/v1/analytics/admin/country-aliases").find((c) => c.method === "POST")!.body!))
      .toEqual({ alias: "naija", canonical: "Nigeria" });
  });
});

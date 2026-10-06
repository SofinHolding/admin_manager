import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { MemoryRouter } from "react-router-dom";
import IntegrationClientsPage from "./IntegrationClientsPage";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); clearTokens(); __resetHttpForTests(); });

// API key giả (cùng định dạng mpk_<key_id>.<secret>) — chỉ tồn tại trong test
const API_KEY = "mpk_k2abcdefghi1.SECRETSECRETSECRETSECRETSECRETSECRETSECR";
const client = {
  client_id: "cli_1", tool_name: "Tool A", client_kind: "primary_tool", platforms: ["facebook"],
  status: "active", created_at: "2026-10-01T00:00:00Z", owner_account_id: "u1", owner_username: "alice",
};
const detail = {
  ...client,
  credentials: [{ key_id: "k1", scopes: ["resources:write"], status: "active", expires_at: null, last_used_at: "2026-10-04T00:00:00Z" }],
};

function server(extra: Responder = () => undefined, prov: unknown = null): Responder[] {
  return [
    extra,
    (c) => c.path === "/v1/accounts/me/integration" ? (prov ? jsonResponse(prov) : jsonResponse({ detail: "Not Found" }, { status: 404 })) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/integrations/clients" ? jsonResponse({ items: [client] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/integrations/clients/cli_1" ? jsonResponse(detail) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/integrations/clients/cli_1/credentials"
      ? jsonResponse({ key_id: "k2", api_key: API_KEY, scopes: ["resources:write"], expires_at: null }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/integrations/clients/cli_1/credentials/k1/rotate"
      ? jsonResponse({ key_id: "k3", api_key: API_KEY.replace("k2abcdefghi1", "k3abcdefghi1"), scopes: ["resources:write"] }) : undefined,
    (c) => c.method === "DELETE" && c.path.startsWith("/v1/integrations/clients/cli_1/credentials/") ? jsonResponse({ ok: true }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/integrations/clients" ? jsonResponse({ ...client, client_id: "cli_2" }, { status: 201 }) : undefined,
    (c) => c.path === "/v1/admin/users" ? jsonResponse({ users: [] }) : undefined,
  ];
}

function authValue(user: User): AuthCtx {
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: () => true,
  };
}
const viewer: User = { id: "u1", username: "alice", email: "a@x", role: "viewer", perms: [] };
const admin: User = { id: "a1", username: "root", email: "r@x", role: "admin", perms: [] };
const dashAdmin: User = { id: "d1", username: "dash", email: "d@x", role: "dash_admin", perms: [] };

function renderPage(u: User = viewer) {
  return render(
    <MemoryRouter><AuthContext.Provider value={authValue(u)}><IntegrationClientsPage /></AuthContext.Provider></MemoryRouter>,
  );
}

function storageText(): string {
  const parts: string[] = [];
  for (const st of [localStorage, sessionStorage]) {
    for (let i = 0; i < st.length; i++) { const k = st.key(i)!; parts.push(k, st.getItem(k) ?? ""); }
  }
  return parts.join("\n");
}

type User_ = ReturnType<typeof userEvent.setup>;

/** Đóng hộp thoại khóa: bắt buộc tick "đã lưu khóa" trước. */
async function saveAndClose(user: User_, dialog: HTMLElement) {
  await user.click(within(dialog).getByRole("checkbox", { name: /Tôi đã lưu khóa/ }));
  await user.click(within(dialog).getByRole("button", { name: "Xong, đóng hộp này" }));
  await waitFor(() => expect(screen.queryByTestId("secret-value")).not.toBeInTheDocument());
}

async function openClient(user: User_) {
  await screen.findByText("Tool A");
  await user.click(screen.getByRole("button", { name: "Chi tiết Tool A" }));
  await screen.findByText("k1");
}

describe("IntegrationClientsPage — danh sách & trạng thái", () => {
  it("liệt kê công cụ của mình, khóa KHÔNG lộ secret/hash", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    expect(f.byPath("/v1/integrations/clients")[0].method).toBe("GET");
    expect(screen.getByText("resources:write")).toBeInTheDocument(); // scope gốc nằm trong Chi tiết kỹ thuật
    expect(screen.getByText("Gửi danh sách kênh")).toBeInTheDocument(); // quyền nói bằng lời
    expect(screen.getAllByText("Đang hoạt động", { selector: "span" })).toHaveLength(2); // công cụ + khóa: chữ kèm biểu tượng
    expect(document.body.textContent).not.toMatch(/mpk_/);
  });

  it("hiển thị trạng thái provisioning: pending / failed (+ error.code)", async () => {
    installFetch(...server(() => undefined, { provisioning: "pending", client_id: "cli_9" }));
    const { unmount } = renderPage();
    expect(await screen.findByTestId("provisioning-status")).toHaveTextContent("đang được khởi tạo");
    unmount();
    installFetch(...server(() => undefined, { provisioning: "failed", error: { code: "PLATFORM_NOT_ALLOWED" } }));
    renderPage();
    expect(await screen.findByTestId("provisioning-status")).toHaveTextContent("PLATFORM_NOT_ALLOWED");
  });

  it("không đăng ký kèm công cụ (404) => không có banner provisioning", async () => {
    const f = installFetch(...server());
    renderPage();
    await screen.findByText("Tool A");
    await waitFor(() => expect(f.byPath("/v1/accounts/me/integration")).toHaveLength(1));
    expect(screen.queryByTestId("provisioning-status")).not.toBeInTheDocument();
  });

  it("admin thấy chủ sở hữu và bộ lọc owner; gọi ?owner=", async () => {
    const f = installFetch(...server());
    renderPage(admin);
    await screen.findByText("Tool A");
    expect(screen.getByText("Chủ sở hữu")).toBeInTheDocument();
    expect(screen.getByLabelText("Lọc theo chủ sở hữu")).toBeInTheDocument();
    expect(f.byPath("/v1/integrations/clients")[0].params.has("owner")).toBe(false);
  });

  it("banner chờ xác thực email nói rõ cách xử lý", async () => {
    installFetch(...server(() => undefined, { provisioning: "pending", client_id: "cli_9" }));
    renderPage();
    const banner = await screen.findByTestId("provisioning-status");
    expect(banner).toHaveTextContent("xác thực email");
    expect(banner).toHaveTextContent("thư rác");
  });
});

describe("IntegrationClientsPage — hướng dẫn cho người mới", () => {
  it("chưa có công cụ nào: EmptyState hướng dẫn 3 bước có đánh số", async () => {
    installFetch(
      (c) => c.method === "GET" && c.path === "/v1/integrations/clients" ? jsonResponse({ items: [] }) : undefined,
      (c) => c.path === "/v1/accounts/me/integration" ? jsonResponse({ detail: "Not Found" }, { status: 404 }) : undefined,
    );
    renderPage();
    expect(await screen.findByText("Bạn chưa có công cụ nào")).toBeInTheDocument();
    const ol = document.querySelector("ol")!;
    expect(ol).not.toBeNull();
    const steps = within(ol as HTMLElement).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toContain("Bước 1. Tạo công cụ");
    expect(steps[1]).toContain("Bước 2. Tạo khóa kết nối");
    expect(steps[2]).toContain("Bước 3. Dán khóa vào công cụ");
  });

  it("mở đầu bằng lời giải thích công cụ là gì", async () => {
    installFetch(...server());
    renderPage();
    await screen.findByText("Tool A");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Công cụ đẩy dữ liệu");
    expect(document.body.textContent).toContain("Mỗi công cụ có một khóa kết nối riêng");
  });
});

describe("IntegrationClientsPage — khóa kết nối hiển thị MỘT lần", () => {
  it("tạo khóa: khóa chỉ trong hộp thoại; đóng => biến mất khỏi DOM/state; không vào storage hay toast", async () => {
    const f = installFetch(...server());
    const okSpy = vi.spyOn(toast, "success");
    const errSpy = vi.spyOn(toast, "error");
    renderPage();
    const user = userEvent.setup();
    await openClient(user);

    await user.click(screen.getByRole("button", { name: /Tạo khóa kết nối/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("secret-value")).toHaveTextContent(API_KEY);
    expect(dialog).toHaveTextContent("một lần");
    expect(dialog).toHaveTextContent("k2"); // mã khóa (không nhạy cảm) được hiển thị
    expect(f.byPath("/v1/integrations/clients/cli_1/credentials").some((c) => c.method === "POST")).toBe(true);

    // không rò rỉ ra storage / toast
    expect(storageText()).not.toContain(API_KEY);
    expect(storageText()).not.toContain("SECRETSECRET");
    for (const spy of [okSpy, errSpy]) for (const call of spy.mock.calls) expect(String(call[0])).not.toContain("SECRETSECRET");

    // Esc KHÔNG đóng (tránh mất khóa do lỡ tay)
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("secret-value")).toBeInTheDocument();

    // nút Sao chép dùng clipboard và báo "Đã sao chép"
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await user.click(within(dialog).getByRole("button", { name: "Sao chép khóa" }));
    expect(writeText).toHaveBeenCalledWith(API_KEY);
    expect(await within(dialog).findByRole("button", { name: "Đã sao chép" })).toBeInTheDocument();

    await saveAndClose(user, dialog);
    expect(document.body.textContent).not.toContain("SECRETSECRET");
    expect(document.body.innerHTML).not.toContain("SECRETSECRET");
    expect(storageText()).not.toContain("SECRETSECRET");

    // tải lại danh sách / mở lại chi tiết không bao giờ đưa khóa trở lại
    await user.click(screen.getByRole("button", { name: "Tải lại" }));
    await waitFor(() => expect(f.byPath("/v1/integrations/clients").filter((c) => c.method === "GET").length).toBeGreaterThan(1));
    expect(document.body.textContent).not.toContain("SECRETSECRET");
  });

  it("chỉ đóng được hộp thoại khóa sau khi tick xác nhận đã lưu (cả nút đóng lẫn dấu X)", async () => {
    installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: /Tạo khóa kết nối/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("đóng hộp này là không xem lại được");

    // nút đóng bị vô hiệu khi chưa tick
    const closeBtn = within(dialog).getByRole("button", { name: "Xong, đóng hộp này" });
    expect(closeBtn).toBeDisabled();

    // dấu X khi chưa tick: không đóng, nhắc người dùng
    await user.click(within(dialog).getByRole("button", { name: "Đóng" }));
    expect(screen.getByTestId("secret-value")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Hãy tick ô xác nhận trước khi đóng");

    await user.click(within(dialog).getByRole("checkbox", { name: /Tôi đã lưu khóa/ }));
    expect(closeBtn).toBeEnabled();
    await user.click(closeBtn);
    await waitFor(() => expect(screen.queryByTestId("secret-value")).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain("SECRETSECRET");
  });

  it("xoay khóa: xác nhận trước, gửi grace_seconds, hiển thị khóa mới một lần", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    const grace = screen.getByLabelText("Thời gian giữ khóa cũ khi xoay (giây)");
    await user.clear(grace);
    await user.type(grace, "120");
    await user.click(screen.getByRole("button", { name: "Xoay khóa k1" }));
    const confirmDlg = await screen.findByRole("dialog");
    expect(confirmDlg).toHaveTextContent("ngừng gửi được dữ liệu");
    expect(confirmDlg).toHaveTextContent("120 giây");
    expect(f.byPath("/v1/integrations/clients/cli_1/credentials/k1/rotate")).toHaveLength(0);
    await user.click(within(confirmDlg).getByRole("button", { name: "Xoay khóa" }));
    const dialog = await screen.findByRole("dialog", { name: /Đã xoay khóa/ });
    expect(within(dialog).getByTestId("secret-value")).toHaveTextContent("mpk_k3abcdefghi1.");
    const rot = f.byPath("/v1/integrations/clients/cli_1/credentials/k1/rotate")[0];
    expect(JSON.parse(rot.body!)).toEqual({ grace_seconds: 120 });
    await saveAndClose(user, dialog);
  });

  it("hủy xác nhận xoay => KHÔNG gọi API xoay", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Xoay khóa k1" }));
    const confirmDlg = await screen.findByRole("dialog");
    await user.click(within(confirmDlg).getByRole("button", { name: "Hủy, giữ nguyên" }));
    expect(f.byPath("/v1/integrations/clients/cli_1/credentials/k1/rotate")).toHaveLength(0);
  });

  it("thu hồi khóa: hỏi xác nhận nêu hậu quả rồi mới DELETE", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Thu hồi khóa k1" }));
    const confirmDlg = await screen.findByRole("dialog");
    expect(confirmDlg).toHaveTextContent("Công cụ đang dùng khóa này sẽ ngừng gửi được dữ liệu");
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false); // chưa xác nhận thì chưa gọi
    await user.click(within(confirmDlg).getByRole("button", { name: "Thu hồi khóa" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "DELETE" && c.path.endsWith("/credentials/k1"))).toBe(true));
  });

  it("từ chối xác nhận => KHÔNG thu hồi", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Thu hồi khóa k1" }));
    const confirmDlg = await screen.findByRole("dialog");
    await user.click(within(confirmDlg).getByRole("button", { name: "Hủy, giữ nguyên" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("dash_admin chỉ xem: nút tạo/xoay/thu hồi khóa bị vô hiệu kèm lời giải thích", async () => {
    const f = installFetch(...server());
    renderPage(dashAdmin);
    const user = userEvent.setup();
    expect(await screen.findByText("Bạn đang ở chế độ chỉ xem")).toBeInTheDocument();
    await openClient(user);
    expect(screen.getByRole("button", { name: "Xoay khóa k1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Thu hồi khóa k1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Tạo khóa kết nối/ })).toBeDisabled();
    expect(screen.getByTestId("no-manage-note")).toHaveTextContent("Chỉ chủ công cụ hoặc quản trị viên");
    await user.click(screen.getByRole("button", { name: "Thu hồi khóa k1" })); // vô hiệu: không mở hộp thoại
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(f.calls.some((c) => c.method === "DELETE" || (c.method === "POST" && c.path.includes("/credentials")))).toBe(false);
  });

  it("server trả 403 khi thu hồi => thông báo dễ hiểu, không lộ mã lỗi", async () => {
    installFetch(
      (c) => c.method === "DELETE" ? jsonResponse({ ok: false, error: { code: "FORBIDDEN", message: "forbidden" } }, { status: 403 }) : undefined,
      ...server(),
    );
    const errSpy = vi.spyOn(toast, "error");
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Thu hồi khóa k1" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Thu hồi khóa" }));
    await waitFor(() => expect(errSpy).toHaveBeenCalledWith(expect.stringContaining("Chỉ chủ công cụ hoặc quản trị viên")));
  });

  it("lỗi tạo khóa (email chưa xác thực) hiện thông báo, không có hộp thoại", async () => {
    installFetch(
      (c) => c.method === "POST" && c.path.endsWith("/credentials")
        ? jsonResponse({ ok: false, error: { code: "EMAIL_NOT_VERIFIED", message: "Email chưa được xác thực" } }, { status: 403 }) : undefined,
      ...server(),
    );
    const errSpy = vi.spyOn(toast, "error");
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: /Tạo khóa kết nối/ }));
    await waitFor(() => expect(errSpy).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("IntegrationClientsPage — tạo công cụ", () => {
  it("POST /v1/integrations/clients với tool_name, client_kind, platforms", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Tool A");
    await user.type(screen.getByLabelText("Tên công cụ"), "Tool B");
    await user.type(screen.getByLabelText(/Nền tảng/), "Facebook, youtube");
    await user.selectOptions(screen.getByLabelText("Loại"), "fallback_tool");
    await user.click(screen.getByRole("button", { name: /Tạo công cụ/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST" && c.path === "/v1/integrations/clients")).toBe(true));
    const post = f.calls.find((c) => c.method === "POST" && c.path === "/v1/integrations/clients")!;
    expect(JSON.parse(post.body!)).toEqual({ tool_name: "Tool B", client_kind: "fallback_tool", platforms: ["facebook", "youtube"] });
  });

  it("nút gợi ý nền tảng bật/tắt giá trị trong ô nhập", async () => {
    installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Tool A");
    const input = screen.getByLabelText(/Nền tảng/);
    await user.click(screen.getByRole("button", { name: "YouTube" }));
    expect(input).toHaveValue("youtube");
    expect(screen.getByRole("button", { name: "YouTube" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "YouTube" }));
    expect(input).toHaveValue("");
  });

  it("thiếu tên hoặc nền tảng => không gọi API, báo lỗi ngay tại ô", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Tool A");
    await user.click(screen.getByRole("button", { name: /Tạo công cụ/ }));
    expect(f.calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.getByText(/Hãy nhập tên công cụ/)).toBeInTheDocument();
    expect(screen.getByText(/ít nhất một nền tảng/)).toBeInTheDocument();
  });
});

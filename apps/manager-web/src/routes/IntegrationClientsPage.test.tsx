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

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

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

async function openClient(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Tool A");
  await user.click(screen.getByRole("button", { name: "Chi tiết Tool A" }));
  await screen.findByText("k1");
}

describe("IntegrationClientsPage — danh sách & trạng thái", () => {
  it("liệt kê client của mình, credential KHÔNG lộ secret/hash", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    expect(f.byPath("/v1/integrations/clients")[0].method).toBe("GET");
    expect(screen.getByText("resources:write")).toBeInTheDocument();
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

  it("không đăng ký kèm Tool (404) => không có banner provisioning", async () => {
    const f = installFetch(...server());
    renderPage();
    await screen.findByText("Tool A");
    await waitFor(() => expect(f.byPath("/v1/accounts/me/integration")).toHaveLength(1));
    expect(screen.queryByTestId("provisioning-status")).not.toBeInTheDocument();
  });

  it("admin thấy cột chủ sở hữu và bộ lọc owner; gọi ?owner=", async () => {
    const f = installFetch(...server());
    renderPage(admin);
    await screen.findByText("Tool A");
    expect(screen.getByText("Chủ sở hữu")).toBeInTheDocument();
    expect(screen.getByLabelText("Lọc theo chủ sở hữu")).toBeInTheDocument();
    expect(f.byPath("/v1/integrations/clients")[0].params.has("owner")).toBe(false);
  });
});

describe("IntegrationClientsPage — API key hiển thị MỘT lần", () => {
  it("cấp credential: key chỉ trong modal; đóng modal => biến mất khỏi DOM/state; không vào storage hay toast", async () => {
    const f = installFetch(...server());
    const okSpy = vi.spyOn(toast, "success");
    const errSpy = vi.spyOn(toast, "error");
    renderPage();
    const user = userEvent.setup();
    await openClient(user);

    await user.click(screen.getByRole("button", { name: /Cấp credential/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("secret-value")).toHaveTextContent(API_KEY);
    expect(dialog).toHaveTextContent("một lần");
    expect(dialog).toHaveTextContent("k2"); // key_id (không nhạy cảm) được hiển thị
    expect(f.byPath("/v1/integrations/clients/cli_1/credentials").some((c) => c.method === "POST")).toBe(true);

    // không rò rỉ ra storage / toast
    expect(storageText()).not.toContain(API_KEY);
    expect(storageText()).not.toContain("SECRETSECRET");
    for (const spy of [okSpy, errSpy]) for (const call of spy.mock.calls) expect(String(call[0])).not.toContain("SECRETSECRET");

    // Esc KHÔNG đóng (tránh mất key do lỡ tay)
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("secret-value")).toBeInTheDocument();

    // nút Copy dùng clipboard
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await user.click(within(dialog).getByRole("button", { name: /Copy/ }));
    expect(writeText).toHaveBeenCalledWith(API_KEY);

    await user.click(within(dialog).getByRole("button", { name: "Đã lưu, đóng" }));
    await waitFor(() => expect(screen.queryByTestId("secret-value")).not.toBeInTheDocument());
    expect(document.body.textContent).not.toContain("SECRETSECRET");
    expect(document.body.innerHTML).not.toContain("SECRETSECRET");
    expect(storageText()).not.toContain("SECRETSECRET");

    // tải lại danh sách / mở lại chi tiết không bao giờ đưa key trở lại
    await user.click(screen.getByRole("button", { name: "Tải lại" }));
    await waitFor(() => expect(f.byPath("/v1/integrations/clients").filter((c) => c.method === "GET").length).toBeGreaterThan(1));
    expect(document.body.textContent).not.toContain("SECRETSECRET");
  });

  it("xoay credential: gửi grace_seconds, hiển thị key mới một lần", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    const grace = screen.getByLabelText("Ân hạn khi xoay (giây)");
    await user.clear(grace);
    await user.type(grace, "120");
    await user.click(screen.getByRole("button", { name: "Xoay k1" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("secret-value")).toHaveTextContent("mpk_k3abcdefghi1.");
    const rot = f.byPath("/v1/integrations/clients/cli_1/credentials/k1/rotate")[0];
    expect(JSON.parse(rot.body!)).toEqual({ grace_seconds: 120 });
    await user.click(within(dialog).getByRole("button", { name: "Đã lưu, đóng" }));
    await waitFor(() => expect(screen.queryByTestId("secret-value")).not.toBeInTheDocument());
  });

  it("thu hồi credential: hỏi xác nhận rồi DELETE", async () => {
    const f = installFetch(...server());
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Thu hồi k1" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "DELETE" && c.path.endsWith("/credentials/k1"))).toBe(true));
  });

  it("từ chối xác nhận => KHÔNG thu hồi", async () => {
    const f = installFetch(...server());
    vi.stubGlobal("confirm", vi.fn(() => false));
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: "Thu hồi k1" }));
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("lỗi cấp credential (email chưa xác thực) hiện thông báo, không có modal", async () => {
    installFetch(
      (c) => c.method === "POST" && c.path.endsWith("/credentials")
        ? jsonResponse({ ok: false, error: { code: "EMAIL_NOT_VERIFIED", message: "Email chưa được xác thực" } }, { status: 403 }) : undefined,
      ...server(),
    );
    const errSpy = vi.spyOn(toast, "error");
    renderPage();
    const user = userEvent.setup();
    await openClient(user);
    await user.click(screen.getByRole("button", { name: /Cấp credential/ }));
    await waitFor(() => expect(errSpy).toHaveBeenCalledWith("Email chưa được xác thực"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("IntegrationClientsPage — tạo client", () => {
  it("POST /v1/integrations/clients với tool_name, client_kind, platforms", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Tool A");
    await user.type(screen.getByLabelText("Tên Tool"), "Tool B");
    await user.type(screen.getByLabelText(/Nền tảng/), "Facebook, youtube");
    await user.selectOptions(screen.getByLabelText("Loại"), "fallback_tool");
    await user.click(screen.getByRole("button", { name: /Tạo client/ }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST" && c.path === "/v1/integrations/clients")).toBe(true));
    const post = f.calls.find((c) => c.method === "POST" && c.path === "/v1/integrations/clients")!;
    expect(JSON.parse(post.body!)).toEqual({ tool_name: "Tool B", client_kind: "fallback_tool", platforms: ["facebook", "youtube"] });
  });

  it("thiếu tên hoặc nền tảng => không gọi API", async () => {
    const f = installFetch(...server());
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Tool A");
    await user.click(screen.getByRole("button", { name: /Tạo client/ }));
    expect(f.calls.some((c) => c.method === "POST")).toBe(false);
  });
});

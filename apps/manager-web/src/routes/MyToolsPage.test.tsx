import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import MyToolsPage from "./MyToolsPage";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); clearTokens(); __resetHttpForTests(); });

const guideBase = {
  base_url: "https://api.example.com", contract_version: "2026-10-01",
  auth: { header: "Authorization", format: "Bearer mpk_<key_id>.<secret>" },
  required_headers: [
    { name: "Authorization", required_when: "luôn", description: "Xác thực khóa" },
    { name: "X-Installation-Id", required_when: "sau handshake", description: "Định danh thiết bị" },
  ],
  client: {
    client_id: "cli_1", owner_account_id: "u1", tool_name: "Tool cá nhân", client_kind: "primary_tool",
    status: "active", ownership: "personal", purpose: "", contract_version: "2026-10-01",
    allowed_platforms: ["facebook"], allowed_scopes: ["integration:handshake", "snapshots:write"],
    created_from: "provisioning", configured_at: null, activated_at: null, activated_by: null,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  },
  installations: [],
  endpoints: [
    {
      method: "POST", path: "/v1/integrations/handshake", scope: "integration:handshake",
      summary: "Kích hoạt thiết bị", description: "Gọi một lần để bind credential vào thiết bị.",
      request_schema: { type: "object" }, example_request: { device_id: "abc" }, example_response: { ok: true },
      error_codes: ["AUTH_INVALID"],
    },
  ],
  limits: { batch_max_items: 500, body_max_bytes: 1_000_000 },
  error_codes: [{ code: "AUTH_INVALID", http_status: 401, meaning: "Thiếu/sai API key" }],
  recent_snapshots: [],
};

function server(credential: unknown, extra: Responder[] = []): Responder[] {
  return [
    ...extra,
    (c) => c.method === "GET" && c.path === "/v1/integrations/me/connection-guide"
      ? jsonResponse({ ...guideBase, credential }) : undefined,
  ];
}

function renderPage() {
  return render(<MemoryRouter><MyToolsPage /></MemoryRouter>);
}

describe("MyToolsPage — 404 CLIENT_NOT_FOUND", () => {
  it("hiện EmptyState hướng dẫn thử lại sau", async () => {
    installFetch(() => jsonResponse({ ok: false, error: { code: "CLIENT_NOT_FOUND", message: "x" } }, { status: 404 }));
    renderPage();
    expect(await screen.findByText("Tool cá nhân đang được khởi tạo")).toBeInTheDocument();
    expect(screen.getByText(/thử tải lại sau ít giây/i)).toBeInTheDocument();
  });
});

describe("MyToolsPage — render endpoint từ connection-guide", () => {
  it("liệt kê endpoint, schema, ví dụ, mã lỗi — không hardcode", async () => {
    installFetch(...server(null));
    renderPage();
    await screen.findByText("Tool cá nhân");
    const endpointRow = screen.getByText(/POST \/v1\/integrations\/handshake/);
    expect(endpointRow).toBeInTheDocument();
    await userEvent.setup().click(endpointRow);
    expect(screen.getByText("Gọi một lần để bind credential vào thiết bị.")).toBeInTheDocument();
    expect(screen.getByText(/AUTH_INVALID/)).toBeInTheDocument();
    expect(screen.getAllByText(/<key_id>/).length).toBeGreaterThan(0);
  });

  it("chưa có khóa nào => nút Cấp khóa mới", async () => {
    installFetch(...server(null));
    renderPage();
    expect(await screen.findByRole("button", { name: /Cấp khóa mới/ })).toBeInTheDocument();
  });
});

describe("MyToolsPage — mã nền tảng dùng ở trường platform", () => {
  it("hiện mã chính + mã phụ từng nền tảng và quy tắc, chỉ xem (không có ô nhập/nút sửa mã)", async () => {
    installFetch((c) => c.path === "/v1/integrations/me/connection-guide" ? jsonResponse({
      ...guideBase, credential: null,
      platforms: [{ key: "tiktok", display_name: "TikTok", aliases: ["tt", "tik_tok"], codes: ["tiktok", "tt", "tik_tok"] },
                  { key: "facebook", display_name: "Facebook", aliases: [], codes: ["facebook"] }],
      platform_rules: ["Mỗi snapshot chỉ chứa dữ liệu của MỘT nền tảng, ghi ở trường `platform`."],
    }) : undefined);
    renderPage();
    const section = (await screen.findByText("Mã nền tảng dùng khi gửi dữ liệu")).closest("section") as HTMLElement;
    const tiktok = within(within(section).getByText("TikTok").closest("tr") as HTMLElement);
    expect(tiktok.getByText("tiktok")).toBeInTheDocument();
    expect(tiktok.getByText("tt")).toBeInTheDocument();
    expect(tiktok.getByText("tik_tok")).toBeInTheDocument();
    expect(within(within(section).getByText("Facebook").closest("tr") as HTMLElement).getByText("—")).toBeInTheDocument();
    expect(within(section).getByText(/chỉ chứa dữ liệu của MỘT nền tảng/)).toBeInTheDocument();
    expect(within(section).getByText("platform", { selector: "code" })).toBeInTheDocument();   // không hiện dấu ` thô
    expect(section.textContent).not.toContain("`");
    expect(within(section).queryByRole("textbox")).toBeNull();
    expect(within(section).queryByRole("button")).toBeNull();
  });
});

describe("MyToolsPage — reveal hiện secret một lần, không ghi storage", () => {
  const pendingCred = {
    key_id: "cred_1", scopes: ["integration:handshake"], status: "active", label: "", expires_at: null,
    last_used_at: null, created_at: "2026-10-01T00:00:00Z", reveal_state: "pending", reveal_expires_at: "2026-10-08T00:00:00Z",
    revealed_at: null, bound_device_id: null, bound_installation_id: null, activated_at: null,
  };

  it("bấm Nhận khóa => gọi reveal, hiện SecretOnceDialog, KHÔNG ghi localStorage/sessionStorage", async () => {
    const f = installFetch(...server(pendingCred, [
      (c) => c.method === "POST" && c.path === "/v1/integrations/me/credential/reveal"
        ? jsonResponse({
          client_id: "cli_1", key_id: "cred_1", api_key: "mpk_cred_1.SECRETSECRETSECRETSECRETSECRET",
          scopes: ["integration:handshake"], expires_at: null, contract_version: "2026-10-01",
          allowed_platforms: ["facebook"], reveal_state: "revealed",
        }) : undefined,
    ]));
    const user = userEvent.setup();
    renderPage();
    const revealBtn = await screen.findByRole("button", { name: /Nhận khóa/ });
    await user.click(revealBtn);
    await waitFor(() => expect(f.calls.some((c) => c.path === "/v1/integrations/me/credential/reveal")).toBe(true));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("secret-value")).toHaveTextContent("mpk_cred_1.SECRETSECRETSECRETSECRETSECRET");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("MyToolsPage — tải xuống không chứa secret", () => {
  it(".env mẫu chỉ có BASE_URL/KEY_ID/CONTRACT_VERSION", async () => {
    installFetch(...server({
      key_id: "cred_1", scopes: ["integration:handshake"], status: "active", label: "", expires_at: null,
      last_used_at: null, created_at: "2026-10-01T00:00:00Z", reveal_state: "revealed", reveal_expires_at: null,
      revealed_at: "2026-10-01T00:00:00Z", bound_device_id: "dev-1", bound_installation_id: "ins-1", activated_at: "2026-10-01T00:00:00Z",
    }));
    const RealBlob = globalThis.Blob;
    const blobParts: BlobPart[][] = [];
    vi.stubGlobal("Blob", vi.fn((parts: BlobPart[], opts?: BlobPropertyBag) => {
      blobParts.push(parts);
      return new RealBlob(parts, opts);
    }));
    const createObjectURL = vi.fn((_blob: Blob) => "blob:fake");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
    renderPage();
    const btn = await screen.findByRole("button", { name: /\.env mẫu/ });
    await userEvent.setup().click(btn);
    expect(createObjectURL).toHaveBeenCalled();
    const text = blobParts[0].join("");
    expect(text).toContain("BASE_URL=https://api.example.com");
    expect(text).toContain("KEY_ID=cred_1");
    expect(text).toContain("CONTRACT_VERSION=2026-10-01");
    expect(text).not.toMatch(/mpk_/);
  });
});

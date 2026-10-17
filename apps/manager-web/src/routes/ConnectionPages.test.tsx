import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import ResourcesPage from "./ResourcesPage";
import GroupsPage from "./GroupsPage";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const source = {
  id: "s1", owner_account_id: "u1", name: "Facebook ITL", platform: "facebook", project_key: "",
  default_country: "Nigeria", origin: "web_manual", created_at: "", updated_at: "",
};
const res = (id: string, ref: string) => ({
  id, owner_account_id: "u1", platform: "facebook", resource_type: "page", canonical_external_id: "", input_ref: ref,
  username: "", url: "", display_name: "", country: "", origin: "web_manual", mapping_status: "unmapped",
  execution_mode: "disabled", verification_status: "unverified", enabled: 1, created_at: "", updated_at: "",
});

function resourcesServer(sources: unknown[], items: unknown[] = [], extra: Responder = () => undefined): Responder[] {
  return [
    extra,
    (c) => c.path === "/v1/resources/platforms" ? jsonResponse({ items: [{ platform: "facebook" }] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources/sources" ? jsonResponse({ items: sources }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources" ? jsonResponse({ items }) : undefined,
  ];
}

describe("ResourcesPage — Kênh và nguồn", () => {
  it("chưa có nguồn: giải thích bằng lời giản dị và dẫn tới tạo nguồn đầu tiên", async () => {
    installFetch(...resourcesServer([]));
    render(<MemoryRouter><ResourcesPage /></MemoryRouter>);
    expect(await screen.findByText("Bạn chưa có nguồn nào")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Kênh và nguồn");
    expect(document.body.textContent).toContain("Nguồn là nơi công cụ lấy dữ liệu; kênh là trang hoặc kênh cụ thể");
    expect(document.body.textContent).not.toMatch(/Tool B|Resource/);
    expect(screen.getByRole("button", { name: /Tạo nguồn/ })).toBeDisabled();
  });

  it("điều hướng hai bước: danh sách nguồn -> kênh của nguồn -> quay lại", async () => {
    installFetch(...resourcesServer([source], [res("r1", "https://facebook.com/a")]));
    render(<MemoryRouter><ResourcesPage /></MemoryRouter>);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Facebook ITL/ }));
    expect(await screen.findByText("https://facebook.com/a")).toBeInTheDocument();
    expect(screen.getByText("Chưa liên kết số liệu")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Quay lại danh sách nguồn/ }));
    expect(await screen.findByRole("button", { name: /Facebook ITL/ })).toBeInTheDocument();
  });

  it("nhập hàng loạt: xem trước (bỏ dòng lặp/đã có) rồi báo kết quả", async () => {
    const f = installFetch(...resourcesServer([source], [res("r1", "https://facebook.com/a")], (c) =>
      c.method === "POST" && c.path === "/v1/resources/import"
        ? jsonResponse({ created: [], skipped: [], created_count: 2, skipped_count: 0 }) : undefined));
    render(<MemoryRouter><ResourcesPage /></MemoryRouter>);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Facebook ITL/ }));
    await screen.findByText("https://facebook.com/a");
    await user.click(screen.getByRole("button", { name: /Nhập nhiều kênh/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Danh sách kênh/), "https://facebook.com/a{Enter}@b{Enter}@c{Enter}@b");
    await user.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    expect(within(dialog).getByTestId("import-preview")).toHaveTextContent("Sẽ thêm 2 kênh, bỏ qua 2 kênh đã có hoặc bị lặp");
    expect(f.byPath("/v1/resources/import")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Nhập 2 kênh" }));
    expect(await within(dialog).findByTestId("import-result")).toHaveTextContent("Đã thêm 2 kênh, bỏ qua 2 kênh đã có");
    expect(JSON.parse(f.byPath("/v1/resources/import")[0].body!).lines).toEqual(["@b", "@c"]);
  });
});

describe("GroupsPage — Nhóm kênh", () => {
  const viewer: User = { id: "u1", username: "vi", email: "v@x", role: "viewer", perms: [] };
  const authValue: AuthCtx = {
    user: viewer, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired",
    showWarning: false, warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(),
    logout: vi.fn(), continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: () => true,
  };
  const renderGroups = () => render(
    <MemoryRouter><AuthContext.Provider value={authValue}><GroupsPage /></AuthContext.Provider></MemoryRouter>,
  );
  const empty: Responder[] = [
    (c) => c.method === "GET" && c.path === "/v1/analytics/groups" ? jsonResponse({ items: [] }) : undefined,
  ];

  it("chưa có nhóm: giải thích lợi ích và hướng dẫn tạo nhóm đầu tiên", async () => {
    installFetch(...empty);
    renderGroups();
    expect(await screen.findByText("Bạn chưa có nhóm kênh nào")).toBeInTheDocument();
    expect(document.body.textContent).toContain("Gom nhiều kênh thành một nhóm để xem tổng riêng và so sánh");
  });

  it("tạo nhóm gọi API và hiện nhóm mới", async () => {
    const f = installFetch(
      (c) => c.method === "POST" && c.path === "/v1/analytics/groups"
        ? jsonResponse({ id: "g1", account_id: "u1", name: "Kênh chính", created_at: "", updated_at: "", members: [] }) : undefined,
      ...empty,
    );
    renderGroups();
    const user = userEvent.setup();
    await screen.findByText("Bạn chưa có nhóm kênh nào");
    await user.type(screen.getAllByLabelText("Tên nhóm")[0], "Kênh chính");
    await user.click(screen.getByRole("button", { name: /Tạo nhóm/ }));
    await waitFor(() => expect(f.byPath("/v1/analytics/groups").some((c) => c.method === "POST")).toBe(true));
    expect(await screen.findByText("Nhóm này chưa có kênh nào. Chọn kênh ở bên dưới để thêm vào.")).toBeInTheDocument();
  });

  it("quốc gia ghi đè được mô tả bằng lời; KHÔNG còn hiện nhãn nền tảng tự do", async () => {
    installFetch((c) => c.method === "GET" && c.path === "/v1/analytics/groups"
      ? jsonResponse({ items: [{ id: "g1", account_id: "u1", name: "N1", created_at: "", updated_at: "",
        members: [{ channel_ref: "ch1", platform_tag: "tiktok", country_override: "Nigeria" }] }] }) : undefined);
    renderGroups();
    expect(await screen.findByText("Tính vào quốc gia: Nigeria")).toBeInTheDocument();
    expect(screen.queryByText(/Nhãn nền tảng/)).not.toBeInTheDocument();
  });
});

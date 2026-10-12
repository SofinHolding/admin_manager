import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { toast } from "sonner";
import { MemoryRouter } from "react-router-dom";
import ChannelsPage from "./ChannelsPage";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

// ── Fixtures ──────────────────────────────────────────────────────────────────

const autoSource = {
  id: "src_auto", owner_account_id: "u1", name: "Nigeria", platform: "facebook", project_key: "",
  default_country: "", origin: "web_manual", created_at: "", updated_at: "",
};

const makeGroup = (id = "grp1", name = "Nigeria", country_code = "NG") => ({
  id, owner_account_id: "u1", name, country_code, sort_order: 0, created_at: "",
});

const makeResource = (id: string, overrides: Record<string, unknown> = {}) => ({
  id, owner_account_id: "u1", platform: "facebook", resource_type: "page", canonical_external_id: "",
  input_ref: "https://facebook.com/kenh-a", username: "", url: "", display_name: "Kênh A", country: "",
  origin: "web_manual", mapping_status: "unmapped", execution_mode: "fallback_local",
  verification_status: "unverified", enabled: 1, created_at: "", updated_at: "", ...overrides,
});

/**
 * Server mock cho ChannelsPage.
 * groups → /v1/user/channel-groups (dạng envelope { data: [...] })
 * items  → /v1/resources
 */
function channelsServer(
  items: unknown[] = [],
  groups: unknown[] = [],
  extra: Responder = () => undefined,
): Responder[] {
  return [
    extra,
    (c) => c.path === "/v1/resources/platforms"
      ? jsonResponse({ items: [{ platform: "facebook" }] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources/sources"
      ? jsonResponse({ items: [] }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/resources/sources"
      ? jsonResponse(autoSource) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources"
      ? jsonResponse({ items }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/user/channel-groups"
      ? jsonResponse({ data: groups }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/user/channel-groups"
      ? jsonResponse({ data: makeGroup("grp-new", JSON.parse(c.body ?? "{}").name ?? "Mới", JSON.parse(c.body ?? "{}").country_code ?? "") })
      : undefined,
    (c) => c.method === "DELETE" && c.path.startsWith("/v1/user/channel-groups/")
      ? jsonResponse({ data: { deleted: true } }) : undefined,
    (c) => c.method === "PATCH" && c.path.startsWith("/v1/user/channel-groups/")
      ? jsonResponse({ data: makeGroup(c.path.split("/").pop()!, JSON.parse(c.body ?? "{}").name ?? "") })
      : undefined,
  ];
}

/** Mở combobox đầu tiên và chọn option bằng regex (option có thể có emoji prefix). */
async function choosePlatform(user: UserEvent, label: string | RegExp) {
  await user.click(screen.getAllByRole("combobox")[0]);
  await user.click(await screen.findByRole("option", { name: typeof label === "string" ? new RegExp(label) : label }));
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ChannelsPage — Nhóm quốc gia + kênh (capability:manual_channel_mgmt)", () => {
  it("không có nhóm nào: tiêu đề đúng + hướng dẫn tạo nhóm đầu tiên", async () => {
    installFetch(...channelsServer([], []));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Kênh của tôi");
    expect(await screen.findByText("Chưa có nhóm quốc gia nào")).toBeInTheDocument();
  });

  it("tạo nhóm: gọi POST /v1/user/channel-groups, nhóm mới hiện trong danh sách", async () => {
    const f = installFetch(...channelsServer([], []));
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có nhóm quốc gia nào");

    await user.type(screen.getByPlaceholderText("Nigeria"), "Nigeria");
    await user.type(screen.getByPlaceholderText("NG"), "NG");
    await user.click(screen.getByRole("button", { name: /Tạo nhóm/ }));

    await waitFor(() =>
      expect(f.byPath("/v1/user/channel-groups").some((c) => c.method === "POST")).toBe(true),
    );
    expect(JSON.parse(f.byPath("/v1/user/channel-groups").find((c) => c.method === "POST")!.body!))
      .toEqual({ name: "Nigeria", country_code: "NG" });
    expect(await screen.findByText("Nigeria")).toBeInTheDocument();  // tên nhóm từ body gửi lên
    expect(okSpy).toHaveBeenCalledWith(expect.stringContaining("nhóm"));
  });

  it("thêm kênh vào nhóm: gọi GET sources → POST sources → POST resource với country=group.name", async () => {
    const group = makeGroup();
    const created = makeResource("r1", { country: "Nigeria" });
    const f = installFetch(
      ...channelsServer([], [group], (c) =>
        c.method === "POST" && c.path === "/v1/resources" ? jsonResponse(created) : undefined,
      ),
    );
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    // Accordion nhóm Nigeria đã mở mặc định (open=true); platform default = "facebook" (duy nhất từ mock)
    await screen.findByText("Nigeria");
    await user.type(screen.getByPlaceholderText("@username hoặc link"), "https://facebook.com/kenh-a");
    await user.type(screen.getByLabelText(/Tên hiển thị/), "Kênh A");
    await user.click(screen.getByRole("button", { name: /Thêm kênh/ }));

    await waitFor(() => expect(f.byPath("/v1/resources").some((c) => c.method === "POST")).toBe(true));
    const body = JSON.parse(f.byPath("/v1/resources").find((c) => c.method === "POST")!.body!);
    expect(body).toMatchObject({
      source_instance_id: "src_auto",
      platform: "facebook",
      input_ref: "https://facebook.com/kenh-a",
      display_name: "Kênh A",
      country: "Nigeria",   // liên kết với nhóm
    });
    expect(await screen.findByText("Kênh A")).toBeInTheDocument();
    expect(okSpy).toHaveBeenCalledWith(expect.stringContaining("Nigeria"));
  });

  it("kênh không thuộc nhóm nào (country='') hiện ở mục 'Kênh chưa phân nhóm'", async () => {
    installFetch(...channelsServer([makeResource("r-ung")], []));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    expect(await screen.findByText("Kênh A")).toBeInTheDocument();
    expect(screen.getByText(/Kênh chưa phân nhóm/)).toBeInTheDocument();
  });

  it("xóa kênh chưa phân nhóm: gọi DELETE đúng id, bỏ khỏi giao diện", async () => {
    const existing = makeResource("r1");   // country='' → ungrouped
    const f = installFetch(
      ...channelsServer([existing], [], (c) =>
        c.method === "DELETE" && c.path === "/v1/resources/r1" ? jsonResponse({ ok: true }) : undefined,
      ),
    );
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Kênh A");
    await user.click(screen.getByRole("button", { name: "Xóa kênh Kênh A" }));
    await waitFor(() =>
      expect(f.byPath("/v1/resources/r1").some((c) => c.method === "DELETE")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Kênh A")).not.toBeInTheDocument());
    // Sau khi xóa kênh duy nhất, mục ungrouped biến mất
    expect(screen.queryByText(/Kênh chưa phân nhóm/)).not.toBeInTheDocument();
  });

  it("xóa nhóm: gọi DELETE /v1/user/channel-groups/{id}, nhóm biến khỏi danh sách", async () => {
    vi.stubGlobal("confirm", () => true);   // stub trước render để không ném "Not implemented"
    const group = makeGroup();
    const f = installFetch(...channelsServer([], [group]));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Nigeria");

    await user.click(screen.getByRole("button", { name: /Xóa nhóm Nigeria/ }));
    await waitFor(() =>
      expect(f.byPath("/v1/user/channel-groups/grp1").some((c) => c.method === "DELETE")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Nigeria")).not.toBeInTheDocument());
  });
});

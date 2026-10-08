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

const autoSource = {
  id: "src_auto", owner_account_id: "u1", name: "Kênh của tôi", platform: "facebook", project_key: "",
  default_country: "", origin: "web_manual", created_at: "", updated_at: "",
};
const res = (id: string, overrides: Record<string, unknown> = {}) => ({
  id, owner_account_id: "u1", platform: "facebook", resource_type: "page", canonical_external_id: "",
  input_ref: "https://facebook.com/kenh-a", username: "", url: "", display_name: "Kênh A", country: "",
  origin: "web_manual", mapping_status: "unmapped", execution_mode: "fallback_local",
  verification_status: "unverified", enabled: 1, created_at: "", updated_at: "", ...overrides,
});

/** `handleAdd` luôn: GET sources -> (POST sources nếu chưa có) -> POST resource. */
function channelsServer(items: unknown[] = [], extra: Responder = () => undefined): Responder[] {
  return [
    extra,
    (c) => c.path === "/v1/resources/platforms" ? jsonResponse({ items: [{ platform: "facebook" }] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources/sources" ? jsonResponse({ items: [] }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/resources/sources" ? jsonResponse(autoSource) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources" ? jsonResponse({ items }) : undefined,
  ];
}

/** Chọn nền tảng qua dropdown thật (tránh đua trạng thái tự chọn lúc Select vừa mount trong jsdom). */
async function choosePlatform(user: UserEvent, label: string) {
  await user.click(screen.getByRole("combobox"));
  await user.click(await screen.findByRole("option", { name: label }));
}

describe("ChannelsPage — Kênh của tôi (website-only, capability:manual_channel_mgmt)", () => {
  it("chưa có kênh nào: tiêu đề đúng + hướng dẫn thêm kênh đầu tiên", async () => {
    installFetch(...channelsServer([]));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Kênh của tôi");
    expect(await screen.findByText("Chưa có kênh nào")).toBeInTheDocument();
  });

  it("thêm kênh: tự tạo nguồn mặc định rồi tạo resource, báo đang lên lịch thu thập, hiện trong danh sách", async () => {
    const created = res("r1");
    const f = installFetch(...channelsServer([], (c) =>
      c.method === "POST" && c.path === "/v1/resources" ? jsonResponse(created) : undefined));
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có kênh nào");

    await choosePlatform(user, "Facebook");
    await user.type(screen.getByPlaceholderText("https://facebook.com/ten-trang"), "https://facebook.com/kenh-a");
    await user.type(screen.getByLabelText(/Tên hiển thị/), "Kênh A");
    await user.click(screen.getByRole("button", { name: /Thêm kênh/ }));

    await waitFor(() => expect(f.byPath("/v1/resources/sources").some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(f.byPath("/v1/resources/sources").find((c) => c.method === "POST")!.body!))
      .toEqual({ name: "Kênh của tôi", platform: "facebook" });
    await waitFor(() => expect(f.byPath("/v1/resources").some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(f.byPath("/v1/resources").find((c) => c.method === "POST")!.body!)).toEqual({
      source_instance_id: "src_auto", platform: "facebook", input_ref: "https://facebook.com/kenh-a",
      display_name: "Kênh A",
    });
    expect(await screen.findByText("Kênh A")).toBeInTheDocument();
    expect(okSpy).toHaveBeenCalledWith("Đã thêm kênh. Thu thập dữ liệu đang được lên lịch.");
  });

  it("platform đang disabled (không fallback_local): báo thêm thành công nhưng không nói đang lên lịch", async () => {
    const created = res("r2", { execution_mode: "disabled" });
    installFetch(...channelsServer([], (c) =>
      c.method === "POST" && c.path === "/v1/resources" ? jsonResponse(created) : undefined));
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có kênh nào");
    await choosePlatform(user, "Facebook");
    await user.type(screen.getByPlaceholderText("https://facebook.com/ten-trang"), "https://facebook.com/kenh-b");
    await user.click(screen.getByRole("button", { name: /Thêm kênh/ }));
    await waitFor(() => expect(okSpy).toHaveBeenCalledWith("Đã thêm kênh"));
  });

  it("xóa kênh: gọi DELETE đúng id, bỏ khỏi danh sách", async () => {
    const existing = res("r1");
    const f = installFetch(...channelsServer([existing], (c) =>
      c.method === "DELETE" && c.path === "/v1/resources/r1" ? jsonResponse({ ok: true }) : undefined));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Kênh A");
    await user.click(screen.getByRole("button", { name: "Xóa kênh Kênh A" }));
    await waitFor(() => expect(f.byPath("/v1/resources/r1").some((c) => c.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.queryByText("Kênh A")).not.toBeInTheDocument());
    expect(await screen.findByText("Chưa có kênh nào")).toBeInTheDocument();
  });
});

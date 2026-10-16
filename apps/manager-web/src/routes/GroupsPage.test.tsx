import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import GroupsPage from "./GroupsPage";
import { installFetch, jsonResponse } from "../test/helpers";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

describe("GroupsPage — trang Nhóm kênh chỉ còn quản lý nhóm (danh mục nền tảng ở trang riêng)", () => {
  it("KHÔNG còn mục 'Nền tảng' trong trang Nhóm kênh", async () => {
    installFetch(
      (c) => c.method === "GET" && c.path === "/v1/analytics/groups" ? jsonResponse({ items: [] }) : undefined,
      (c) => c.method === "GET" && c.path === "/v1/analytics/top-channels" ? jsonResponse({ items: [] }) : undefined,
    );
    render(<MemoryRouter><GroupsPage /></MemoryRouter>);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText("Nền tảng")).not.toBeInTheDocument();
    expect(screen.queryByText("Thêm nền tảng")).not.toBeInTheDocument();
  });

  it("addMember KHÔNG gửi platform_tag trong thân yêu cầu", async () => {
    const group = { id: "g1", account_id: "u1", name: "Nigeria", created_at: "", updated_at: "", members: [] };
    const channel = { id: "ch1", name: "Kênh A", platform: "facebook", country: "VN", views: 10 };
    const f = installFetch(
      (c) => {
        if (c.method === "GET" && c.path === "/v1/analytics/groups") return jsonResponse({ items: [group] });
        if (c.method === "GET" && c.path === "/v1/analytics/top-channels") return jsonResponse({ items: [channel] });
        if (c.method === "POST" && c.path === "/v1/analytics/groups/g1/members") return jsonResponse({ ok: true });
        return undefined;
      },
    );
    render(<MemoryRouter><GroupsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByDisplayValue("Nigeria");
    await user.click(screen.getByRole("combobox", { name: /Thêm kênh vào nhóm/ }));
    await user.click(await screen.findByRole("option", { name: /Kênh A/ }));
    await user.click(screen.getByRole("button", { name: /Thêm kênh vào nhóm/ }));

    await waitFor(() =>
      expect(f.byPath("/v1/analytics/groups/g1/members").some((c) => c.method === "POST")).toBe(true),
    );
    const call = f.byPath("/v1/analytics/groups/g1/members").find((c) => c.method === "POST")!;
    const body = JSON.parse(call.body!);
    expect(body).toEqual({ channel_ref: "ch1", country_override: "" });
    expect(body).not.toHaveProperty("platform_tag");
  });
});

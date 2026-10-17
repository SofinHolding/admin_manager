import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import UserChannelsPage from "./UserChannelsPage";
import type { UserChannel, UserChannelsResult } from "../api";
import { toolReason } from "../lib/userChannels";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const PATH = "/v1/integrations/admin/user-channels";

function ch(over: Partial<UserChannel> & { id: string }): UserChannel {
  return {
    platform: "youtube", resource_type: "channel", external_id: over.id, display_name: "", url: "", origin: "web_manual",
    execution_mode: "fallback_tool", enabled: true, removed: false, shared_tool_visible: true, created_at: "2026-10-17T02:02:29+00:00",
    updated_at: "2026-10-17T02:02:29+00:00", last_job_status: null, last_job_at: null, content_count: 0, last_collected_at: null, ...over,
  };
}

const DATA: UserChannelsResult = {
  users: [
    { owner_account_id: "a1", username: "hieutruong", visible_in_shared_tool: false,
      totals: { active: 0, removed: 2, shared_tool: 0, personal_tool: 0 },
      channels: [
        ch({ id: "y1", display_name: "Kênh A", removed: true, shared_tool_visible: false, updated_at: "2026-10-17T07:08:42+00:00" }),
        ch({ id: "y2", display_name: "Kênh B", removed: true, shared_tool_visible: false }),
      ] },
    { owner_account_id: "a2", username: "phinguyen74", visible_in_shared_tool: true,
      totals: { active: 2, removed: 0, shared_tool: 1, personal_tool: 1 },
      channels: [
        ch({ id: "t1", platform: "tiktok", display_name: "@sk1kr", last_job_status: "succeeded", content_count: 14 }),
        ch({ id: "t2", platform: "facebook", display_name: "Trang FB", execution_mode: "primary_tool", origin: "tool", shared_tool_visible: false }),
      ] },
  ],
  summary: { users: 2, visible_in_shared_tool: 1, active_channels: 2, shared_tool_channels: 1, removed_channels: 2 },
};

function server(extra: Responder = () => undefined) {
  return installFetch(extra, (c) => c.method === "GET" && c.path === PATH ? jsonResponse(DATA) : undefined);
}

const renderPage = () => render(<MemoryRouter><UserChannelsPage /></MemoryRouter>);

describe("Trang Kênh theo người dùng (admin)", () => {
  it("hiện mọi user kể cả user đã xoá hết kênh, kèm lý do vì sao không có ở Tool", async () => {
    const f = server();
    renderPage();
    expect(await screen.findByText("hieutruong")).toBeInTheDocument();
    expect(screen.getByText("phinguyen74")).toBeInTheDocument();
    expect(screen.getByText("Không hiện ở Tool: user đã xoá hết kênh (2 kênh đã xoá).")).toBeInTheDocument();
    expect(screen.getByText("Tool dùng chung thấy 1 kênh của user này.")).toBeInTheDocument();
    expect(f.byPath(PATH)[0].url).toContain("include_removed=true");
  });

  it("từng kênh nói rõ Tool dùng chung có thấy không; kênh đã xoá kèm giờ xoá theo giờ Việt Nam", async () => {
    server();
    renderPage();
    await screen.findByText("hieutruong");
    expect(screen.getAllByText(/Đã xoá 17\/10\/2026 14:08/)).toHaveLength(1);          // 07:08 UTC = 14:08 giờ VN
    const rows = screen.getAllByRole("row");
    const sk1kr = rows.find((r) => within(r).queryByText("@sk1kr"))!;
    expect(within(sk1kr).getByText("Thấy")).toBeInTheDocument();
    expect(within(sk1kr).getByText("14 video")).toBeInTheDocument();
    expect(within(sk1kr).getByText("Xong")).toBeInTheDocument();
    const fb = rows.find((r) => within(r).queryByText("Trang FB"))!;
    expect(within(fb).getByText("Không")).toBeInTheDocument();
    expect(within(fb).getByText("Tool riêng")).toBeInTheDocument();
  });

  it("lọc: chỉ user không hiện ở Tool; ẩn kênh đã xoá; tìm theo tên", async () => {
    server();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("hieutruong");
    await user.click(screen.getByLabelText(/Chỉ user KHÔNG hiện ở Tool/));
    expect(screen.queryByText("phinguyen74")).not.toBeInTheDocument();
    expect(screen.getByText("hieutruong")).toBeInTheDocument();
    await user.click(screen.getByLabelText(/Chỉ user KHÔNG hiện ở Tool/));
    await user.click(screen.getByLabelText(/Hiện kênh đã xoá/));
    expect(screen.queryByText("hieutruong")).not.toBeInTheDocument();                  // user chỉ có kênh đã xoá => hết kênh khớp
    await user.click(screen.getByLabelText(/Hiện kênh đã xoá/));
    await user.type(screen.getByLabelText("Tìm user hoặc kênh"), "sk1kr");
    expect(screen.queryByText("hieutruong")).not.toBeInTheDocument();
    expect(screen.getByText("@sk1kr")).toBeInTheDocument();
    expect(screen.queryByText("Trang FB")).not.toBeInTheDocument();
  });

  it("thu gọn một user bằng cách bấm tên", async () => {
    server();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("phinguyen74");
    expect(screen.getByText("@sk1kr")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /phinguyen74/ }));
    expect(screen.queryByText("@sk1kr")).not.toBeInTheDocument();
  });
});

describe("toolReason", () => {
  const base = { owner_account_id: "x", username: "x", channels: [] };
  it("phân biệt các lý do user không hiện ở Tool", () => {
    expect(toolReason({ ...base, visible_in_shared_tool: false, totals: { active: 0, removed: 0, shared_tool: 0, personal_tool: 0 } })).toContain("chưa có kênh");
    expect(toolReason({ ...base, visible_in_shared_tool: false, totals: { active: 3, removed: 0, shared_tool: 0, personal_tool: 3 } })).toContain("Tool riêng");
    expect(toolReason({ ...base, visible_in_shared_tool: false, totals: { active: 3, removed: 0, shared_tool: 0, personal_tool: 1 } })).toContain("không có kênh nào ở chế độ Tool dùng chung");
  });
});

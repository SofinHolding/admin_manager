import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JobsPage from "./JobsPage";
import { clearTokens } from "../../auth/tokenStore";
import { __resetHttpForTests } from "../../lib/http";
import { installFetch, jsonResponse } from "../../test/helpers";
import { adminUser, counts, jobSummary, renderAt } from "./rewardTestUtil";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const render = (u?: Parameters<typeof renderAt>[3]) => renderAt(<JobsPage />, "/reward/jobs", "/reward/jobs", u);

describe("JobsPage", () => {
  it("mỗi công việc có tên, trạng thái tiếng Việt, 'đã xong 120/300', thanh tiến độ và thời gian tương đối", async () => {
    installFetch((c) => c.path === "/v1/reward/jobs" ? jsonResponse({ jobs: [
      jobSummary(),
      jobSummary({ id: "2", name: "Tuần 37", status: "completed", counts: counts({ total: 10, success: 10, failed: 0, unknown: 0, pending: 0 }), finished_at: new Date(Date.now() - 3 * 3600_000).toISOString() }),
    ], total: 2 }) : undefined);
    render();
    const link = await screen.findByRole("link", { name: "Tuần 38 - Top voice" });
    expect(link).toHaveAttribute("href", "/reward/jobs/1");
    expect(screen.getByText("Đang chạy")).toBeInTheDocument();
    expect(screen.getByText("Hoàn tất")).toBeInTheDocument();
    expect(screen.getByText("đã xong 120/300 · 7 thất bại · 3 chưa rõ")).toBeInTheDocument();
    expect(screen.getByText("đã xong 10/10")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Tiến độ của Tuần 38 - Top voice" })).toBeInTheDocument();
    expect(screen.getByText("Cập nhật 5 phút trước")).toBeInTheDocument();
    expect(screen.getByText("Cập nhật 3 giờ trước")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Tạo công việc/ })).toHaveAttribute("href", "/reward/jobs/new");
  });

  it("chưa có công việc nào: EmptyState dẫn tới tạo công việc đầu tiên (chỉ một nút chính)", async () => {
    installFetch((c) => c.path === "/v1/reward/jobs" ? jsonResponse({ jobs: [], total: 0 }) : undefined);
    render();
    expect(await screen.findByText("Bạn chưa có công việc phân phối nào")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Tạo công việc/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/reward/jobs/new");
    expect(screen.getByRole("link", { name: "kết nối Discord" })).toHaveAttribute("href", "/reward/credentials");
  });

  it("lọc theo trạng thái gửi đúng tham số và có trạng thái rỗng riêng", async () => {
    const f = installFetch((c) => c.path === "/v1/reward/jobs"
      ? jsonResponse({ jobs: c.params.get("status") === "paused" ? [] : [jobSummary()], total: 1 }) : undefined);
    const user = userEvent.setup();
    render();
    await screen.findByText("Tuần 38 - Top voice");
    await user.click(screen.getByRole("combobox", { name: "Lọc theo trạng thái" }));
    await user.click(await screen.findByRole("option", { name: "Tạm dừng" }));
    await waitFor(() => expect(f.calls.some((c) => c.params.get("status") === "paused")).toBe(true));
    expect(await screen.findByText(/Không có công việc nào ở trạng thái/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Xem tất cả công việc" }));
    expect(await screen.findByText("Tuần 38 - Top voice")).toBeInTheDocument();
  });

  it("lỗi tải: Notice nói cách sửa và nút Thử lại", async () => {
    let n = 0;
    installFetch((c) => c.path === "/v1/reward/jobs"
      ? (n++ === 0 ? jsonResponse({ detail: "boom" }, { status: 500 }) : jsonResponse({ jobs: [jobSummary()], total: 1 })) : undefined);
    const user = userEvent.setup();
    render();
    expect(await screen.findByText("Chưa tải được danh sách công việc")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(await screen.findByText("Tuần 38 - Top voice")).toBeInTheDocument();
  });

  it("admin thấy chủ sở hữu từng công việc", async () => {
    installFetch((c) => c.path === "/v1/reward/jobs" ? jsonResponse({ jobs: [jobSummary({ owner_username: "linh" })], total: 1 }) : undefined);
    render(adminUser);
    const row = (await screen.findByRole("link", { name: "Tuần 38 - Top voice" })).closest("li")!;
    expect(within(row).getByText("Của linh")).toBeInTheDocument();
  });
});

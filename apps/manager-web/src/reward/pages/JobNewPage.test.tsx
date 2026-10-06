import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JobNewPage from "./JobNewPage";
import { clearTokens } from "../../auth/tokenStore";
import { __resetHttpForTests } from "../../lib/http";
import { installFetch, jsonResponse } from "../../test/helpers";
import { renderAt } from "./rewardTestUtil";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const render = () => renderAt(<JobNewPage />, "/reward/jobs/new", "/reward/jobs/new");

function server(created: { status: string; issues?: unknown[] } = { status: "validated" }) {
  return installFetch(
    (c) => c.method === "POST" && c.path === "/v1/reward/jobs"
      ? jsonResponse({ job_id: "7", status: created.status, total_items: 2, issues: created.issues ?? [], counts: {} }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/reward/jobs/7/run" ? jsonResponse({ status: "running" }) : undefined,
  );
}

async function fill(user: ReturnType<typeof userEvent.setup>, text = "alice|100\nbob|250") {
  await user.type(screen.getByLabelText("Tên công việc"), "Tuần 38");
  await user.click(screen.getByLabelText("Danh sách thành viên và điểm"));
  await user.paste(text);
}

describe("JobNewPage", () => {
  it("chia nhóm có nhãn + gợi ý, mô tả kết quả sẽ xảy ra và có nút chính 'Bắt đầu phân phối'", () => {
    server();
    render();
    expect(screen.getByRole("heading", { level: 1, name: "Tạo công việc phân phối" })).toBeInTheDocument();
    expect(screen.getByLabelText("Tên công việc")).toHaveAccessibleDescription(/Chỉ để bạn tìm lại/);
    expect(screen.getByText("Điều gì sẽ xảy ra")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bắt đầu phân phối/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kiểm tra danh sách" })).toBeInTheDocument();
  });

  it("gửi trống: báo lỗi từng ô, không gọi API", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    expect(await screen.findByText("Đặt tên cho công việc để dễ tìm lại.")).toBeInTheDocument();
    expect(screen.getByText("Dán danh sách thành viên và số điểm.")).toBeInTheDocument();
    expect(screen.getByLabelText("Tên công việc")).toHaveAttribute("aria-invalid", "true");
    expect(f.calls).toHaveLength(0);
  });

  it("xem trước: đếm dòng, tổng điểm, liệt kê dòng lỗi bằng lời và chặn bắt đầu khi còn dòng lỗi", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await fill(user, "alice|100\nbob\ncarol|-5");
    expect(await screen.findByText("3 dòng")).toBeInTheDocument();
    expect(screen.getByText("2 dòng lỗi")).toBeInTheDocument();
    expect(screen.getByText("Thiếu số điểm. Mỗi dòng cần dạng tên|điểm.")).toBeInTheDocument();
    expect(screen.getByText("Số điểm phải là số nguyên dương.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    expect(await screen.findByText(/Danh sách còn 2 dòng lỗi/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(f.calls).toHaveLength(0);
  });

  it("Bắt đầu phân phối: xác nhận nêu số người/điểm, rồi tạo + chạy + chuyển sang trang chi tiết", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    const dlg = await screen.findByRole("dialog", { name: "Bắt đầu phân phối điểm?" });
    expect(within(dlg).getByText(/cho 2 thành viên, tổng 350 điểm/)).toBeInTheDocument();
    expect(f.byPath("/v1/reward/jobs")).toHaveLength(0); // chưa gửi gì khi chưa xác nhận
    await user.click(within(dlg).getByRole("button", { name: "Bắt đầu phân phối" }));
    expect(await screen.findByText("TRANG CHI TIẾT")).toBeInTheDocument();
    expect(f.byPath("/v1/reward/jobs").filter((c) => c.method === "POST")).toHaveLength(1);
    expect(f.byPath("/v1/reward/jobs/7/run")).toHaveLength(1);
    const body = JSON.parse(f.byPath("/v1/reward/jobs")[0].body!);
    expect(body.name).toBe("Tuần 38");
    expect(body.raw_text).toBe("alice|100\nbob|250");
  });

  it("Hủy ở hộp xác nhận: không tạo, không chạy", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Hủy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(f.calls).toHaveLength(0);
  });

  it("máy chủ báo danh sách có lỗi: KHÔNG chạy, hiện vấn đề, cho sửa lại", async () => {
    const f = server({ status: "invalid", issues: [{ row_index: 1, severity: "error", code: "INVALID_ROW", message: "Dòng sai định dạng" }] });
    const user = userEvent.setup();
    render();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Bắt đầu phân phối" }));
    expect(await screen.findByText(/Danh sách có 1 vấn đề nên chưa chạy được/)).toBeInTheDocument();
    expect(screen.getByText(/Dòng 2 \(lỗi\): Dòng sai định dạng/)).toBeInTheDocument();
    expect(f.byPath("/v1/reward/jobs/7/run")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /Bắt đầu phân phối/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Sửa danh sách" }));
    expect(screen.getByLabelText("Tên công việc")).toBeEnabled();
  });

  it("Kiểm tra danh sách chỉ tạo công việc, chưa chạy; sau đó mở được công việc đã tạo", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Kiểm tra danh sách" }));
    expect(await screen.findByText("Danh sách hợp lệ: 2 dòng, không có lỗi")).toBeInTheDocument();
    expect(f.byPath("/v1/reward/jobs/7/run")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Mở công việc đã tạo" }));
    expect(await screen.findByText("TRANG CHI TIẾT")).toBeInTheDocument();
  });

  it("cài đặt riêng: số không hợp lệ bị báo lỗi tại ô", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await fill(user);
    await user.click(screen.getByLabelText("Dùng cài đặt khác cho công việc này"));
    await user.type(screen.getByLabelText(/Độ trễ giữa các lệnh/), "abc");
    await user.click(screen.getByRole("button", { name: /Bắt đầu phân phối/ }));
    expect(await screen.findByText("Nhập số nguyên từ 0 trở lên.")).toBeInTheDocument();
    expect(f.calls).toHaveLength(0);
  });
});

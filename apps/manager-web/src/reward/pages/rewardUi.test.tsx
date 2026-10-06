import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ITEM_STATUS, ItemStatusBadge, JOB_STATUS, ProgressBar, StatusBadge, explainCode, explainPause, jobStatus, progressSummary, relativeTime } from "./rewardUi";

describe("trạng thái công việc", () => {
  const ALL = ["draft", "validated", "queued", "running", "stopping", "paused", "stopped", "completed", "invalid"];

  it.each(ALL)("%s có tên tiếng Việt, biểu tượng và câu giải thích (không chỉ màu)", (s) => {
    const { container, unmount } = render(<StatusBadge status={s} />);
    const info = JOB_STATUS[s];
    expect(info).toBeDefined();
    expect(screen.getByText(info.label)).toBeInTheDocument();
    expect(info.label).not.toBe(s); // không hiện mã thô
    expect(container.querySelector("svg")).not.toBeNull();
    expect(info.hint.length).toBeGreaterThan(10);
    unmount();
  });

  it("mỗi trạng thái có tên riêng, không trùng nhau", () => {
    const labels = ALL.map((s) => JOB_STATUS[s].label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("trạng thái lạ rơi về câu chung, không vỡ", () => {
    expect(jobStatus("zzz").label).toBe("Trạng thái khác");
  });

  it.each(Object.keys(ITEM_STATUS))("mục %s có tên tiếng Việt và biểu tượng", (s) => {
    const { container, unmount } = render(<ItemStatusBadge status={s} />);
    expect(screen.getByText(ITEM_STATUS[s].label)).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
    unmount();
  });
});

describe("giải thích mã", () => {
  it("lý do tự tạm dừng được dịch thành câu có hướng dẫn, kèm 'Tiếp tục'", () => {
    expect(explainPause("ROLE_REVOKED")).toMatch(/quyền .* thu hồi/);
    expect(explainPause("WORKER_CANCELLED")).toMatch(/gián đoạn/);
    expect(explainPause("WORKER_CANCELLED")).toMatch(/Tiếp tục/);
    expect(explainPause("MÃ_LẠ")).toMatch(/Tiếp tục/);
  });

  it("mã lỗi mục được dịch; mã lạ không lộ ra như câu trả lời", () => {
    expect(explainCode("USER_NOT_FOUND")).toMatch(/Không tìm thấy thành viên/);
    expect(explainCode("XYZ_UNKNOWN")).not.toMatch(/XYZ_UNKNOWN/);
    expect(explainCode(null)).toBe("");
  });
});

describe("tiến độ và thời gian", () => {
  it("tóm tắt 'đã xong 120/300' kèm số thất bại/chưa rõ bằng chữ", () => {
    expect(progressSummary({ total: 300, success: 120, failed: 0, unknown: 0 })).toBe("đã xong 120/300");
    expect(progressSummary({ total: 300, success: 120, failed: 7, unknown: 3 })).toBe("đã xong 120/300 · 7 thất bại · 3 chưa rõ");
  });

  it("thanh tiến độ là progressbar có giá trị chữ", () => {
    render(<ProgressBar label="Tiến độ X" counts={{ total: 10, success: 5, failed: 0, unknown: 0 }} />);
    const bar = screen.getByRole("progressbar", { name: "Tiến độ X" });
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar).toHaveAttribute("aria-valuetext", "đã xong 5/10");
  });

  it("thời gian tương đối", () => {
    const now = Date.parse("2026-10-06T12:00:00Z");
    expect(relativeTime("2026-10-06T11:59:50Z", now)).toBe("vừa xong");
    expect(relativeTime("2026-10-06T11:55:00Z", now)).toBe("5 phút trước");
    expect(relativeTime("2026-10-06T09:00:00Z", now)).toBe("3 giờ trước");
    expect(relativeTime("2026-10-04T12:00:00Z", now)).toBe("2 ngày trước");
    expect(relativeTime(null, now)).toBe("—");
  });
});

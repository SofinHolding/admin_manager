import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JobDetailPage from "./JobDetailPage";
import { clearTokens } from "../../auth/tokenStore";
import { __resetHttpForTests } from "../../lib/http";
import { installFetch, jsonResponse, type Responder } from "../../test/helpers";
import { counts, jobSummary, renderAt, sseStream } from "./rewardTestUtil";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const item = (o: Record<string, unknown> = {}) => ({
  id: "i1", row_index: 0, raw_username: "nguoi_a", resolved_user_id: "111", point: 100, status: "success",
  resolve_level: null, confirmation_level: "reply", failure_code: null, failure_message: null, attempt_count: 1,
  first_sent_at: null, finalized_at: null, ...o,
});

/** Máy chủ giả cho job 1. `stream`: Responder của /stream (mặc định: stream mở, không phát gì). */
function server(opts: { job?: Record<string, unknown>; items?: unknown[]; total?: number; events?: unknown[]; stream?: Responder; extra?: Responder } = {}) {
  const s = sseStream();
  const job = { ...jobSummary(), issues: [], ...(opts.job ?? {}) };
  const f = installFetch(
    opts.extra ?? (() => undefined),
    opts.stream ?? ((c) => c.path === "/v1/reward/jobs/1/stream" ? s.response() : undefined),
    (c) => c.path === "/v1/reward/jobs/1" && c.method === "GET" ? jsonResponse(job) : undefined,
    (c) => c.path === "/v1/reward/jobs/1/items" ? jsonResponse({ items: opts.items ?? [item()], total: opts.total ?? (opts.items?.length ?? 1) }) : undefined,
    (c) => c.path === "/v1/reward/jobs/1/events" ? jsonResponse({ events: opts.events ?? [] }) : undefined,
    (c) => c.path === "/v1/reward/jobs/1/items/i1/attempts" ? jsonResponse({ attempts: [{ id: "a1", attempt_no: 1, nonce: "n-123", http_status: 200, reply_excerpt: "100 XP has been given to @a", link_mode: "reply", created_at: "2026-10-05T10:00:00Z" }] }) : undefined,
    (c) => c.method === "POST" && /\/v1\/reward\/jobs\/1\/(run|pause|resume|stop)$/.test(c.path) ? jsonResponse({ status: "ok" }) : undefined,
  );
  return { f, s };
}

const render = () => renderAt(<JobDetailPage />, "/reward/jobs/1", "/reward/jobs/:id");

describe("JobDetailPage", () => {
  it("đầu trang: tên, trạng thái tiếng Việt, tiến độ và nút điều khiển đúng trạng thái đang chạy", async () => {
    server();
    render();
    expect(await screen.findByRole("heading", { level: 1, name: "Tuần 38 - Top voice" })).toBeInTheDocument();
    expect(screen.getByText("Đang chạy")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /Tiến độ: đã xong 120\/300/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tạm dừng/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dừng$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Tiếp tục/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Bắt đầu phân phối/ })).toBeNull();
  });

  it("Dừng phải xác nhận: Hủy thì KHÔNG gọi /stop, xác nhận mới gọi", async () => {
    const { f } = server();
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: /Dừng$/ }));
    const dlg = await screen.findByRole("dialog", { name: "Dừng công việc này?" });
    expect(within(dlg).getByText(/không thu hồi được/)).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Hủy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(f.byPath("/v1/reward/jobs/1/stop")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: /Dừng$/ }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Dừng công việc" }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/stop")).toHaveLength(1));
  });

  it("Tạm dừng gọi /pause ngay (hoàn tác được bằng Tiếp tục)", async () => {
    const { f } = server();
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: /Tạm dừng/ }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/pause")).toHaveLength(1));
  });

  it("đang tạm dừng vì tiến trình bị gián đoạn: Notice giải thích bằng lời, mã gốc nằm trong Chi tiết kỹ thuật, nút chính là Tiếp tục", async () => {
    const { f } = server({
      job: { status: "paused" },
      events: [{ id: 1, job_id: 1, at: "2026-10-05T10:00:00Z", type: "auto_pause", actor: "system", payload: { reason: "WORKER_CANCELLED" } }],
    });
    const user = userEvent.setup();
    render();
    const notice = (await screen.findByText("Công việc tự tạm dừng")).closest("[role]")!;
    expect(within(notice as HTMLElement).getByText(/tiến trình bị gián đoạn/)).toBeInTheDocument();
    expect(within(notice as HTMLElement).getByText("WORKER_CANCELLED")).toBeInTheDocument();
    expect(screen.getByText("Tạm dừng", { selector: "span" })).toBeInTheDocument(); // trạng thái, tên tiếng Việt
    await user.click(screen.getByRole("button", { name: /Tiếp tục/ }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/resume")).toHaveLength(1));
  });

  it("dòng thời gian: sự kiện đọc được bằng tiếng Việt, mới nhất ở trên, không hiện 'Invalid Date'", async () => {
    server({
      events: [
        { id: 1, job_id: 1, at: "2026-10-05T09:00:00Z", type: "created", actor: "u1", payload: null },
        { id: 2, job_id: 1, at: "2026-10-05T10:00:00Z", type: "auto_pause", actor: "system", payload: JSON.stringify({ reason: "ROLE_REVOKED" }) },
      ],
    });
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("tab", { name: "Dòng thời gian" }));
    const list = await screen.findByRole("list", { name: "Dòng thời gian sự kiện" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent(/quyền phân phối điểm của bạn đã bị thu hồi/);
    expect(rows[1]).toHaveTextContent("Công việc được tạo");
    expect(list).not.toHaveTextContent(/Invalid/);
  });

  it("stream hỏng: Notice nói đang hỏi lại mỗi vài giây (hành vi polling thật)", async () => {
    const { f } = server({ stream: (c) => c.path === "/v1/reward/jobs/1/stream" ? jsonResponse({ detail: "no" }, { status: 502 }) : undefined });
    render();
    expect(await screen.findByText("Đang cập nhật bằng cách hỏi lại mỗi vài giây")).toBeInTheDocument();
    expect(f.byPath("/v1/reward/jobs/1").length).toBeGreaterThan(0);
  });

  it("stream sống: hiện 'Đang cập nhật trực tiếp', không có Notice polling; sự kiện job đổi trạng thái được phản ánh", async () => {
    const { s } = server();
    render();
    expect(await screen.findByText("Đang cập nhật trực tiếp")).toBeInTheDocument();
    expect(screen.queryByText("Đang cập nhật bằng cách hỏi lại mỗi vài giây")).toBeNull();
    act(() => s.push("job", { status: "paused" }));
    expect(await screen.findByRole("button", { name: /Tiếp tục/ })).toBeInTheDocument();
  });

  it("công việc đã kết thúc thì không nói chuyện kết nối cập nhật", async () => {
    server({ job: { status: "completed" } });
    render();
    await screen.findByText("Hoàn tất");
    expect(screen.queryByText(/cập nhật trực tiếp/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Tạm dừng|Tiếp tục|Dừng$/ })).toBeNull();
  });

  it("bị thu quyền (403): Notice rõ ràng, ẩn nút điều khiển, ngừng gọi lại", async () => {
    let revoked = false;
    const { f } = server({
      extra: (c) => revoked && c.path.startsWith("/v1/reward/jobs/1") && c.path !== "/v1/reward/jobs/1/stream"
        ? jsonResponse({ detail: "forbidden" }, { status: 403 }) : undefined,
    });
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: /Tạm dừng/ }));
    revoked = true;
    await user.click(screen.getByRole("button", { name: /Tạm dừng/ }));
    expect(await screen.findByText("Quyền của bạn đã bị thu hồi, trang này ngừng cập nhật")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Tạm dừng/ })).toBeNull();
    const before = f.calls.length;
    await new Promise((r) => setTimeout(r, 150));
    expect(f.calls.filter((c, i) => i >= before && c.path !== "/v1/reward/jobs/1/stream" && c.path === "/v1/reward/jobs/1/items")).toHaveLength(0);
  });

  it("danh sách mục: tên trạng thái tiếng Việt, mã lỗi được giải thích; lọc gửi đúng tham số; phân trang", async () => {
    const many = Array.from({ length: 50 }, (_, i) => item({ id: `i${i + 1}`, row_index: i, raw_username: `user_${i + 1}`, status: i === 1 ? "failed" : "success", failure_code: i === 1 ? "USER_NOT_FOUND" : null }));
    const { f } = server({ items: many, total: 120 });
    const user = userEvent.setup();
    render();
    expect(await screen.findByText("user_2")).toBeInTheDocument();
    expect(screen.getByText("Không tìm thấy thành viên này trong máy chủ Discord.")).toBeInTheDocument();
    expect(screen.getByText("Hiển thị 1–50 trong 120 mục")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Trang sau" }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/items").some((c) => c.params.get("offset") === "50" && c.params.get("limit") === "50")).toBe(true));

    await user.click(screen.getByRole("combobox", { name: "Lọc theo trạng thái" }));
    await user.click(await screen.findByRole("option", { name: "Thất bại" }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/items").some((c) => c.params.get("status") === "failed" && c.params.get("offset") === "0")).toBe(true));
  });

  it("mục chưa rõ: mở chi tiết, 'Cho phép gửi lại' phải xác nhận trước khi gọi API", async () => {
    const { f } = server({
      job: { counts: counts({ unknown: 1 }), status: "paused" },
      items: [item({ status: "unknown", failure_code: "SEND_RESULT_UNKNOWN", failure_message: "x" })],
      extra: (c) => c.method === "POST" && c.path === "/v1/reward/jobs/1/items/i1/resolve" ? jsonResponse({ ok: true, status: "pending" }) : undefined,
    });
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: "Xem chi tiết nguoi_a" }));
    const dlg = await screen.findByRole("dialog", { name: "nguoi_a" });
    expect(await within(dlg).findByText(/Bot trả lời: 100 XP/)).toBeInTheDocument();
    expect(within(dlg).getByText(/Không biết lệnh đã được gửi đi hay chưa/)).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Cho phép gửi lại" }));
    const confirm = await screen.findByRole("dialog", { name: /Cho phép gửi lại mục nguoi_a/ });
    expect(f.byPath("/v1/reward/jobs/1/items/i1/resolve")).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Cho phép gửi lại" }));
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/1/items/i1/resolve")).toHaveLength(1));
  });

  it("danh sách rỗng khi lọc: nói cách sửa", async () => {
    server({ items: [], total: 0 });
    render();
    expect(await screen.findByText("Công việc này chưa có mục nào.")).toBeInTheDocument();
  });
});

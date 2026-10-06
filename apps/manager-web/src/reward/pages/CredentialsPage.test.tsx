import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CredentialsPage from "./CredentialsPage";
import { clearTokens } from "../../auth/tokenStore";
import { __resetHttpForTests } from "../../lib/http";
import { installFetch, jsonResponse, type Responder } from "../../test/helpers";
import { renderAt } from "./rewardTestUtil";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

// Mã đăng nhập giả — chỉ tồn tại trong test
const SECRET = "MTIzNDU2.FAKE.NOT-A-REAL-DISCORD-TOKEN-0000";

const tok = (o: Record<string, unknown> = {}) => ({
  id: 1, label: "Tài khoản phụ 1", discord_user_id: "9", discord_username: "phu1", status: "valid", enabled: true,
  last_error: null, verified_at: new Date(Date.now() - 2 * 3600_000).toISOString(), last_used_at: null, ...o,
});
const cred = (o: Record<string, unknown> = {}) => ({
  exists: true, status: "valid", guild_id: "111111111111111111", channel_id: "222222222222222222", command_name: "give-xp",
  confirm_mode: "reply", success_pattern: "", failure_pattern: "", leveling_bot_id: "", delay_ms: 1200, jitter_ms: 400,
  verified_at: "", last_error: "", tokens: [tok()], ...o,
});

function server(info: unknown = cred(), extra: Responder = () => undefined) {
  return installFetch(
    extra,
    (c) => c.method === "GET" && c.path === "/v1/reward/credentials" ? jsonResponse(info) : undefined,
    (c) => c.method === "PUT" && c.path === "/v1/reward/credentials" ? jsonResponse({ ok: true, status: "valid", last_error: null, command: null, tokens: [] }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/reward/credentials/tokens"
      ? jsonResponse({ ok: true, token: tok({ id: 2, label: "Mới", discord_username: "moi" }), command: null }) : undefined,
    (c) => c.method === "DELETE" ? jsonResponse({ ok: true }) : undefined,
    (c) => c.method === "POST" && /credentials\/tokens\/\d+\/verify$/.test(c.path) ? jsonResponse({ ok: true, token: tok(), command: null }) : undefined,
  );
}

const render = () => renderAt(<CredentialsPage />, "/reward/credentials", "/reward/credentials");

describe("CredentialsPage", () => {
  it("giải thích để làm gì + an toàn thế nào, và trạng thái kết nối bằng chữ + biểu tượng", async () => {
    server();
    render();
    expect(await screen.findByText("Đang hoạt động")).toBeInTheDocument();
    expect(screen.getByText(/được mã hóa khi lưu và không bao giờ hiện lại/)).toBeInTheDocument();
    expect(screen.getByText("Dùng tài khoản Discord phụ, không dùng tài khoản chính")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Tài khoản Discord (1/1 dùng được)" })).toBeInTheDocument();
    expect(screen.getByText("Dùng được")).toBeInTheDocument();
  });

  it.each([
    ["unverified", "Cần kiểm tra lại"],
    ["revoked", "Bị thu hồi"],
    ["invalid", "Không dùng được"],
  ])("trạng thái kết nối %s hiện tên '%s'", async (status, label) => {
    server(cred({ status, last_error: status === "invalid" ? "Discord từ chối" : "" }));
    render();
    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
  });

  it("chưa có tài khoản nào: EmptyState chỉ bước kế tiếp", async () => {
    server(cred({ tokens: [], status: "unverified" }));
    render();
    expect(await screen.findByText("Chưa có tài khoản Discord nào")).toBeInTheDocument();
    expect(screen.getByText(/Thêm ít nhất một tài khoản/)).toBeInTheDocument();
  });

  it("thêm tài khoản: ô mã là password, mã được gửi đi MỘT lần rồi bị xóa khỏi ô và không hiện trên trang", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    const input = await screen.findByLabelText("Mã đăng nhập Discord");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    await user.type(input, SECRET);
    await user.click(screen.getByRole("button", { name: /Thêm tài khoản/ }));
    expect(await screen.findByText("Đã thêm tài khoản moi")).toBeInTheDocument();
    expect(JSON.parse(f.byPath("/v1/reward/credentials/tokens")[0].body!).token).toBe(SECRET);
    expect(screen.getByLabelText("Mã đăng nhập Discord")).toHaveValue("");
    expect(document.body.textContent).not.toContain(SECRET);
    // và không rơi vào bộ nhớ trình duyệt
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(SECRET);
  });

  it("thêm tài khoản khi ô mã trống: báo lỗi tại ô, không gọi API", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await screen.findByLabelText("Mã đăng nhập Discord");
    await user.click(screen.getByRole("button", { name: /Thêm tài khoản/ }));
    expect(await screen.findByText("Hãy dán mã đăng nhập Discord.")).toBeInTheDocument();
    expect(f.byPath("/v1/reward/credentials/tokens").filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("mã bị Discord từ chối: cảnh báo nói rõ, vẫn xóa mã khỏi ô", async () => {
    server(cred(), (c) => c.method === "POST" && c.path === "/v1/reward/credentials/tokens"
      ? jsonResponse({ ok: true, token: tok({ id: 3, status: "invalid", last_error: "Discord từ chối mã này" }), command: null }) : undefined);
    const user = userEvent.setup();
    render();
    await user.type(await screen.findByLabelText("Mã đăng nhập Discord"), SECRET);
    await user.click(screen.getByRole("button", { name: /Thêm tài khoản/ }));
    expect(await screen.findByText("Đã thêm nhưng tài khoản chưa dùng được")).toBeInTheDocument();
    expect(screen.getByText("Discord từ chối mã này", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByLabelText("Mã đăng nhập Discord")).toHaveValue("");
  });

  it("xóa tài khoản phải xác nhận và nêu hậu quả; Hủy thì không xóa", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: "Xóa tài khoản Tài khoản phụ 1" }));
    const dlg = await screen.findByRole("dialog", { name: /Xóa tài khoản Tài khoản phụ 1\?/ });
    expect(within(dlg).getByText(/mã đăng nhập đã lưu bị hủy/)).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Hủy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(f.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Xóa tài khoản Tài khoản phụ 1" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Xóa tài khoản" }));
    await waitFor(() => expect(f.calls.filter((c) => c.method === "DELETE" && c.path === "/v1/reward/credentials/tokens/1")).toHaveLength(1));
  });

  it("xóa cả kết nối phải xác nhận, nêu hậu quả", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: /Xóa kết nối/ }));
    const dlg = await screen.findByRole("dialog", { name: "Xóa kết nối Discord?" });
    expect(within(dlg).getByText(/thiết lập từ đầu/)).toBeInTheDocument();
    expect(f.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    await user.click(within(dlg).getByRole("button", { name: "Xóa kết nối" }));
    await waitFor(() => expect(f.calls.filter((c) => c.method === "DELETE" && c.path === "/v1/reward/credentials")).toHaveLength(1));
  });

  it("lưu cấu hình thiếu mã kênh: báo lỗi tại ô, không gọi PUT", async () => {
    const f = server(cred({ channel_id: "" }));
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: "Lưu cấu hình" }));
    expect(await screen.findByText("Nhập mã kênh Discord.")).toBeInTheDocument();
    expect(f.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("lưu thành công: báo bằng Notice, gửi đúng cấu hình", async () => {
    const f = server();
    const user = userEvent.setup();
    render();
    await user.click(await screen.findByRole("button", { name: "Lưu cấu hình" }));
    expect(await screen.findByText("Đã lưu và kết nối được với Discord")).toBeInTheDocument();
    const put = f.calls.find((c) => c.method === "PUT")!;
    expect(JSON.parse(put.body!)).toMatchObject({ guild_id: "111111111111111111", command_name: "give-xp", confirm_mode: "reply" });
  });

  it("thử regex trên câu mẫu: nói khớp/không khớp bằng chữ", async () => {
    server();
    const user = userEvent.setup();
    render();
    await user.type(await screen.findByLabelText(/Thử mẫu thành công/), "100 XP has been given to @a");
    expect(await screen.findByText(/Khớp: câu này sẽ được tính là thành công/)).toBeInTheDocument();
  });
});

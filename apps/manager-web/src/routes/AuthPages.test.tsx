/**
 * Trang đăng nhập / đăng ký / tài khoản theo ui-design-v1: quy tắc mật khẩu, lỗi bằng lời, Field gắn aria,
 * màn "Kiểm tra email", hộp thoại phiên (focus mặc định), hồ sơ.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "./LoginPage";
import RegisterPage from "./RegisterPage";
import ProfilePage from "./ProfilePage";
import ResetPasswordPage from "./ResetPasswordPage";
import VerifyEmailPage from "./VerifyEmailPage";
import { SessionWarning, spokenRemaining } from "../components/SessionWarning";
import { SessionExpired } from "../components/SessionExpired";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { ApiError } from "../api";
import { hasPerm } from "../auth/permissions";
import { __resetHttpForTests } from "../lib/http";
import { clearTokens } from "../auth/tokenStore";
import { installFetch, jsonResponse } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

function authValue(user: User | null, over: Partial<AuthCtx> = {}): AuthCtx {
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: (p) => hasPerm(user, p), ...over,
  };
}
const RULE = /Ít nhất 8 ký tự, tối đa 72 byte \(khoảng 70 ký tự không dấu; tiếng Việt có dấu tính nhiều byte hơn\)/;

describe("LoginPage — lỗi nói rõ cách xử lý, không lộ tài khoản có tồn tại hay không", () => {
  function renderLogin(login: AuthCtx["login"]) {
    render(<MemoryRouter><AuthContext.Provider value={authValue(null, { login })}><LoginPage /></AuthContext.Provider></MemoryRouter>);
  }
  async function submit(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText("Tên đăng nhập"), "alice");
    await user.type(screen.getByLabelText("Mật khẩu"), "wrong-password{Enter}"); // Enter gửi biểu mẫu
  }

  it("sai mật khẩu (401): một thông điệp chung cho cả sai tên lẫn sai mật khẩu, chỉ cách sửa", async () => {
    const login = vi.fn().mockRejectedValue(new ApiError("Tên đăng nhập hoặc mật khẩu không đúng", { status: 401, code: "AUTH_INVALID" }));
    renderLogin(login);
    await submit(userEvent.setup());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Tên đăng nhập hoặc mật khẩu chưa đúng");
    expect(alert).toHaveTextContent("Quên mật khẩu");
    expect(login).toHaveBeenCalledWith("alice", "wrong-password", true);
  });

  it("tài khoản bị khóa (403): nói cần liên hệ quản trị viên", async () => {
    renderLogin(vi.fn().mockRejectedValue(new ApiError("Tài khoản đã bị khoá. Liên hệ quản trị viên.", { status: 403 })));
    await submit(userEvent.setup());
    expect(await screen.findByRole("alert")).toHaveTextContent("Tài khoản đang bị khóa");
  });

  it("mất mạng: nói chưa kết nối được, không nhầm thành sai mật khẩu", async () => {
    renderLogin(vi.fn().mockRejectedValue(new ApiError("x", { status: 0, code: "NETWORK" })));
    await submit(userEvent.setup());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Chưa kết nối được tới máy chủ");
    expect(alert).not.toHaveTextContent("chưa đúng");
  });

  it("gửi khi để trống: lỗi nằm ngay dưới ô, ô gắn aria-invalid + aria-describedby, không gọi login", async () => {
    const login = vi.fn();
    renderLogin(login);
    await userEvent.setup().click(screen.getByRole("button", { name: "Đăng nhập" }));
    const u = screen.getByLabelText("Tên đăng nhập");
    expect(u).toHaveAttribute("aria-invalid", "true");
    const described = document.getElementById(u.getAttribute("aria-describedby")!.split(" ").pop()!);
    expect(described).toHaveTextContent("Nhập tên đăng nhập");
    expect(login).not.toHaveBeenCalled();
  });

  it("autocomplete đúng, nút hiện/ẩn mật khẩu có nhãn đọc được, 'Ghi nhớ' nói đúng hành vi (chỉ trong tab này)", async () => {
    renderLogin(vi.fn());
    expect(screen.getByLabelText("Tên đăng nhập")).toHaveAttribute("autocomplete", "username");
    const pw = screen.getByLabelText("Mật khẩu");
    expect(pw).toHaveAttribute("autocomplete", "current-password");
    expect(pw).toHaveAttribute("type", "password");
    await userEvent.setup().click(screen.getByRole("button", { name: "Hiện mật khẩu" }));
    expect(pw).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Ẩn mật khẩu" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Ghi nhớ đăng nhập")).toHaveAccessibleDescription(/Chỉ giữ trong tab này/);
  });
});

describe("RegisterPage — quy tắc mật khẩu và lỗi 422", () => {
  async function toStep2(user: ReturnType<typeof userEvent.setup>) {
    render(<MemoryRouter><RegisterPage /></MemoryRouter>);
    await user.type(screen.getByLabelText("Mã mời"), "INVITE-KEY");
    await user.click(screen.getByRole("button", { name: "Tiếp tục" }));
    await screen.findByRole("heading", { name: "Tạo tài khoản" });
  }
  function server(register?: (c: { path: string }) => Response | undefined) {
    return installFetch(
      (c) => c.path === "/v1/auth/validate-key" ? jsonResponse({ valid: true, role: "viewer" }) : undefined,
      (c) => c.path === "/v1/auth/check-username" || c.path === "/v1/auth/check-email" ? jsonResponse({ available: true }) : undefined,
      (c) => c.path === "/v1/auth/register" ? (register?.(c) ?? jsonResponse({ ok: true, email: "a@b.co", message: "ok" })) : undefined,
    );
  }
  async function fill(user: ReturnType<typeof userEvent.setup>, password: string) {
    await user.type(screen.getByLabelText("Tên đăng nhập"), "newuser");
    await user.type(screen.getByLabelText("Email"), "a@b.co");
    await user.type(screen.getByLabelText("Mật khẩu"), password);
    await user.type(screen.getByLabelText("Nhập lại mật khẩu"), password);
    await waitFor(() => expect(document.querySelectorAll(".text-success").length).toBeGreaterThanOrEqual(2), { timeout: 5000 });
  }

  it("quy tắc mật khẩu hiện TRƯỚC khi gõ và được gắn vào ô bằng aria-describedby", async () => {
    server();
    await toStep2(userEvent.setup());
    const pw = screen.getByLabelText("Mật khẩu");
    expect(pw).toHaveValue("");
    expect(pw).toHaveAccessibleDescription(RULE);
    expect(pw).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText("Tên đăng nhập")).toHaveAttribute("autocomplete", "username");
  });

  it("mật khẩu quá 72 byte (chữ có dấu): lỗi nói số byte, ô aria-invalid, nút Đăng ký chưa bấm được", async () => {
    server();
    const user = userEvent.setup();
    await toStep2(user);
    await user.type(screen.getByLabelText("Mật khẩu"), "ệ".repeat(30)); // 30 ký tự = 90 byte
    const pw = screen.getByLabelText("Mật khẩu");
    expect(pw).toHaveAttribute("aria-invalid", "true");
    expect(pw).toHaveAccessibleDescription(/90 byte, vượt giới hạn 72 byte/);
    expect(screen.getByRole("button", { name: "Đăng ký" })).toBeDisabled();
  });

  it("server trả 422 'quá dài': hiển thị bằng lời dễ hiểu, không phản chiếu giá trị nhập", async () => {
    server(() => jsonResponse(
      { detail: [{ loc: ["body", "password"], msg: "Value error, Mật khẩu quá dài (tối đa 72 byte; ký tự có dấu chiếm nhiều byte)", input: "SECRET-PW-123" }] },
      { status: 422 },
    ));
    const user = userEvent.setup();
    await toStep2(user);
    await fill(user, "SECRET-PW-123");
    await user.click(screen.getByRole("button", { name: "Đăng ký" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Mật khẩu quá dài");
    expect(alert).toHaveTextContent("dài quá 72 byte");
    expect(alert).not.toHaveTextContent("SECRET-PW-123");
    expect(alert).not.toHaveTextContent("Value error");
  });

  it("đăng ký xong: màn 'Kiểm tra email' nêu email, các bước kế tiếp và nút gửi lại", async () => {
    const f = server();
    const user = userEvent.setup();
    await toStep2(user);
    await fill(user, "password-123");
    await user.click(screen.getByRole("button", { name: "Đăng ký" }));
    expect(await screen.findByRole("heading", { name: "Kiểm tra email" })).toBeInTheDocument();
    expect(screen.getByText("a@b.co")).toBeInTheDocument();
    expect(screen.getByText(/Bấm vào liên kết xác thực trong thư/)).toBeInTheDocument();
    expect(screen.getByText(/xem cả mục thư rác/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Đăng nhập" })).toHaveAttribute("href", "/login");

    f.fn.mockClear();
    installFetch((c) => c.path === "/v1/auth/resend-verify" ? jsonResponse({ ok: true, message: "ok" }) : undefined);
    await user.click(screen.getByRole("button", { name: "Gửi lại thư xác thực" }));
    expect(await screen.findByText(/Đã gửi lại thư xác thực/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gửi lại thư sau \d+ giây/ })).toBeDisabled();
  });

  it("mã mời sai: lỗi nằm dưới ô, nói cách xử lý", async () => {
    installFetch((c) => c.path === "/v1/auth/validate-key" ? jsonResponse({ valid: false, reason: "Mã mời đã hết hạn" }) : undefined);
    const user = userEvent.setup();
    render(<MemoryRouter><RegisterPage /></MemoryRouter>);
    await user.type(screen.getByLabelText("Mã mời"), "OLD{Enter}");
    const input = screen.getByLabelText("Mã mời");
    await waitFor(() => expect(input).toHaveAttribute("aria-invalid", "true"));
    expect(input).toHaveAccessibleDescription(/Mã mời đã hết hạn.*xin quản trị viên một mã mới/);
  });
});

describe("Đặt lại mật khẩu / xác thực email", () => {
  it("quên mật khẩu: sau khi gửi hiện 'Kiểm tra email', không khẳng định email có tồn tại", async () => {
    installFetch((c) => c.path === "/v1/auth/forgot-password" ? jsonResponse({ ok: true, message: "ok" }) : undefined);
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/reset-password"]}><ResetPasswordPage /></MemoryRouter>);
    await user.type(screen.getByLabelText("Email"), "x@y.co{Enter}");
    expect(await screen.findByRole("heading", { name: "Kiểm tra email" })).toBeInTheDocument();
    expect(screen.getByText(/Nếu email này có tài khoản/)).toBeInTheDocument();
  });

  it("đặt mật khẩu mới: hiện quy tắc 72 byte và báo quá dài bằng lời", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/reset-password?token=T"]}><ResetPasswordPage /></MemoryRouter>);
    const pw = screen.getByLabelText("Mật khẩu mới");
    expect(pw).toHaveAccessibleDescription(RULE);
    await user.type(pw, "ệ".repeat(30));
    expect(pw).toHaveAccessibleDescription(/vượt giới hạn 72 byte/);
  });

  it("xác thực email thất bại: nói rõ chuyện gì và nút về đăng nhập", async () => {
    installFetch((c) => c.path === "/v1/auth/verify-email" ? jsonResponse({ detail: "Link xác thực đã hết hạn." }, { status: 400 }) : undefined);
    render(<MemoryRouter initialEntries={["/verify-email?token=T"]}><VerifyEmailPage /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Link xác thực đã hết hạn.");
    expect(screen.getByRole("link", { name: "Về trang đăng nhập" })).toBeInTheDocument();
  });
});

describe("Hộp thoại phiên", () => {
  it("SessionWarning: alertdialog, 'Tiếp tục sử dụng' nhận focus mặc định, có chữ và đếm ngược đọc được", async () => {
    const onContinue = vi.fn();
    const onLogout = vi.fn();
    render(<SessionWarning seconds={125} onContinue={onContinue} onLogout={onLogout} />);
    const dlg = screen.getByRole("alertdialog");
    expect(dlg).toHaveTextContent("Phiên đăng nhập sắp hết hạn");
    expect(dlg).toHaveTextContent("02:05");
    expect(dlg).toHaveTextContent("Còn lại khoảng 3 phút");
    await waitFor(() => expect(screen.getByRole("button", { name: "Tiếp tục sử dụng" })).toHaveFocus());
    const user = userEvent.setup();
    await user.keyboard("{Enter}");
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onLogout).not.toHaveBeenCalled();
  });

  it("SessionWarning: Tab không thoát khỏi hộp thoại; Esc = tiếp tục sử dụng; Đăng xuất gọi onLogout", async () => {
    const onContinue = vi.fn();
    const onLogout = vi.fn();
    render(<><button>NGOAI</button><SessionWarning seconds={60} onContinue={onContinue} onLogout={onLogout} /></>);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole("button", { name: "Tiếp tục sử dụng" })).toHaveFocus());
    for (let i = 0; i < 4; i++) {
      await user.tab();
      expect(screen.getByRole("alertdialog").contains(document.activeElement)).toBe(true);
    }
    await user.keyboard("{Escape}");
    expect(onContinue).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Đăng xuất" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("câu đọc đếm ngược chỉ đổi ở vài mốc (không đọc lại mỗi giây)", () => {
    expect(spokenRemaining(299)).toBe(spokenRemaining(241));
    expect(spokenRemaining(61)).toBe("khoảng 2 phút");
    expect(spokenRemaining(45)).toBe("dưới 1 phút");
    expect(spokenRemaining(20)).toBe("dưới 30 giây");
    expect(spokenRemaining(5)).toBe("dưới 10 giây");
  });

  it("SessionExpired: focus vào 'Đăng nhập lại', Esc không đóng", async () => {
    const onLogin = vi.fn();
    render(<SessionExpired onLogin={onLogin} reason="revoked" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Đăng nhập lại" })).toHaveFocus());
    await userEvent.setup().keyboard("{Escape}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(onLogin).not.toHaveBeenCalled();
  });
});

describe("ProfilePage", () => {
  const me: User = { id: "u1", username: "alice", email: "alice@x.co", role: "dash_admin", perms: [] };
  function renderProfile() {
    return render(<MemoryRouter><AuthContext.Provider value={authValue(me)}><ProfilePage /></AuthContext.Provider></MemoryRouter>);
  }
  const base = (c: { path: string }) => c.path === "/v1/viewer/links" ? jsonResponse({ items: [] }) : undefined;

  it("vai trò dùng roleLabel kèm một câu giải thích; có các phần Thông tin / Đổi mật khẩu / Công cụ", async () => {
    installFetch(base);
    renderProfile();
    expect(screen.getByRole("heading", { name: "Thông tin tài khoản" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Đổi mật khẩu" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Công cụ của tôi" })).toBeInTheDocument();
    expect(screen.getByText("Quản lý Dashboard")).toBeInTheDocument();
    expect(screen.getByText(/trừ việc khóa, sửa hay xóa quản trị viên/)).toBeInTheDocument();
    expect(await screen.findByText("Chưa liên kết công cụ nào")).toBeInTheDocument();
  });

  it("đổi email: nói rõ email mới CHƯA xác thực và đã gửi lại thư xác thực", async () => {
    const f = installFetch(
      (c) => c.path === "/v1/auth/profile" ? jsonResponse({ ok: true, message: "ok", updates: { email: "new@x.co" } }) : undefined,
      base,
    );
    renderProfile();
    const user = userEvent.setup();
    const email = screen.getByLabelText("Email");
    expect(email).toHaveAccessibleDescription(/email mới sẽ ở trạng thái chưa xác thực/);
    await user.clear(email);
    await user.type(email, "new@x.co");
    await user.click(screen.getByRole("button", { name: "Lưu thay đổi" }));
    const note = await screen.findByText("Email mới chưa được xác thực");
    expect(note.closest("[role=status]")).toHaveTextContent("đã gửi thư xác thực tới new@x.co");
    expect(JSON.parse(f.byPath("/v1/auth/profile")[0].body!)).toEqual({ email: "new@x.co" });
  });

  it("đổi mật khẩu: mô tả 3 bước; sau khi gửi mã hiện quy tắc mật khẩu; 422 quá dài báo bằng lời", async () => {
    installFetch(
      (c) => c.path === "/v1/auth/change-password/send-code" ? jsonResponse({ ok: true, message: "ok" }) : undefined,
      (c) => c.path === "/v1/auth/change-password"
        ? jsonResponse({ detail: [{ msg: "Value error, Mật khẩu quá dài (tối đa 72 byte; ký tự có dấu chiếm nhiều byte)" }] }, { status: 422 })
        : undefined,
      base,
    );
    renderProfile();
    const user = userEvent.setup();
    expect(screen.getByText(/Chúng tôi gửi mã 6 số về email của bạn/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Gửi mã về email" }));
    const pw = await screen.findByLabelText("Mật khẩu mới");
    expect(pw).toHaveAccessibleDescription(RULE);
    await user.type(screen.getByLabelText("Mã xác thực"), "123456");
    await user.type(pw, "password-123");
    await user.type(screen.getByLabelText("Nhập lại mật khẩu mới"), "password-123");
    await user.click(screen.getByRole("button", { name: "Đổi mật khẩu" }));
    expect(await screen.findByText("Mật khẩu quá dài")).toBeInTheDocument();
  });
});

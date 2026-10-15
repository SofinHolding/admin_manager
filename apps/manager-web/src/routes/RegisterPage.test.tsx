import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import RegisterPage from "./RegisterPage";
import { __resetHttpForTests } from "../lib/http";
import { clearTokens } from "../auth/tokenStore";
import { installFetch, jsonResponse } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

function server(validate: Record<string, unknown>) {
  return installFetch(
    (c) => c.path === "/v1/auth/validate-key" ? jsonResponse(validate) : undefined,
    (c) => c.path === "/v1/auth/check-username" || c.path === "/v1/auth/check-email" ? jsonResponse({ available: true }) : undefined,
    (c) => c.path === "/v1/auth/register" ? jsonResponse({ ok: true, email: "a@b.co", message: "ok" }) : undefined,
  );
}

async function toStep2(user: ReturnType<typeof userEvent.setup>) {
  render(<MemoryRouter><RegisterPage /></MemoryRouter>);
  await user.type(screen.getByLabelText("Mã mời"), "INVITE-KEY");
  await user.click(screen.getByRole("button", { name: /Tiếp tục/ }));
  await screen.findByText("Tạo tài khoản");
}

async function fillAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Tên đăng nhập"), "newuser");
  await user.type(screen.getByLabelText("Email"), "a@b.co");
  await user.type(screen.getByLabelText("Mật khẩu"), "password-123");
  await user.type(screen.getByLabelText("Nhập lại mật khẩu"), "password-123");
  // chờ debounce kiểm tra username/email (500ms)
  await waitFor(() => expect(document.querySelectorAll(".text-success").length).toBeGreaterThanOrEqual(2), { timeout: 5000 });
}

describe("RegisterPage — đăng ký bằng mã mời (không còn mục Tool)", () => {
  it("không còn mục đăng ký kèm Tool trên form", async () => {
    const user = userEvent.setup();
    server({ valid: true, role: "viewer" });
    await toStep2(user);
    expect(screen.queryByTestId("register-tool")).not.toBeInTheDocument();
    expect(screen.queryByText(/Tạo công cụ đẩy dữ liệu/)).not.toBeInTheDocument();
  });

  it("đăng ký: body chỉ gồm key/username/password/email", async () => {
    const f = server({ valid: true, role: "viewer" });
    const user = userEvent.setup();
    await toStep2(user);
    await fillAccount(user);
    await user.click(screen.getByRole("button", { name: /Đăng ký/ }));
    await waitFor(() => expect(f.byPath("/v1/auth/register")).toHaveLength(1));
    expect(JSON.parse(f.byPath("/v1/auth/register")[0].body!)).toEqual({
      key: "INVITE-KEY", username: "newuser", password: "password-123", email: "a@b.co",
    });
  });

  it("các endpoint đăng ký là công khai: không gắn Authorization", async () => {
    const f = server({ valid: true, role: "viewer" });
    const user = userEvent.setup();
    await toStep2(user);
    for (const c of f.calls) expect(c.headers.get("Authorization")).toBeNull();
  });
});

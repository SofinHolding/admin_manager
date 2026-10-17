import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { toast } from "sonner";
import { MemoryRouter } from "react-router-dom";
import PlatformsPage from "./PlatformsPage";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

const PLATFORMS = "/v1/integrations/admin/platforms";
const REGISTRY = [
  { key: "youtube", display_name: "YouTube", enabled: true, aliases: ["yt"] },
  { key: "tiktok", display_name: "TikTok", enabled: false, aliases: [] },
];

function server(extra: Responder = () => undefined): Responder[] {
  return [extra, (c) => c.method === "GET" && c.path === PLATFORMS ? jsonResponse(REGISTRY) : undefined];
}

function renderPage() {
  return render(<MemoryRouter><PlatformsPage /></MemoryRouter>);
}

const createBtn = () => screen.getByRole("button", { name: /Thêm nền tảng/ });
const typeCreate = async (user: UserEvent, key: string, name: string, aliases = "") => {
  await user.type(screen.getByPlaceholderText("kwai"), key);
  if (aliases) await user.type(screen.getByPlaceholderText("kwai_vn, kwai_app"), aliases);
  await user.type(screen.getByPlaceholderText("Kwai"), name);
};

describe("Trang Nền tảng — danh mục + mã nền tảng", () => {
  it("hiện tiêu đề, từng nền tảng với trạng thái, mã chính và mã phụ; KHÔNG có cấu hình thu thập", async () => {
    const f = installFetch(...server());
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Nền tảng");
    expect(await screen.findByText("YouTube")).toBeInTheDocument();
    expect(screen.getByText("Đang bật")).toBeInTheDocument();
    expect(screen.getByText("Đã tắt")).toBeInTheDocument();
    expect(screen.getByText("youtube")).toBeInTheDocument();           // mã chính
    expect(screen.getByRole("button", { name: "Gỡ mã yt khỏi YouTube" })).toBeInTheDocument();   // mã phụ
    expect(screen.queryByText(/Tự động thu thập/)).not.toBeInTheDocument();
    expect(f.byPath("/v1/ops/platform-policies")).toHaveLength(0);
  });

  it("thêm nền tảng kèm mã phụ: POST đúng thân yêu cầu", async () => {
    const f = installFetch(
      ...server((c) => c.method === "POST" && c.path === PLATFORMS
        ? jsonResponse({ key: "kwai", display_name: "Kwai", enabled: true, aliases: ["kwai_vn", "kwai_app"] }, { status: 201 }) : undefined),
    );
    renderPage();
    await screen.findByText("YouTube");
    const user = userEvent.setup();
    await typeCreate(user, "kwai", "Kwai", "kwai_vn, Kwai_App kwai_vn");   // viết hoa/trùng trong ô bị chuẩn hoá
    await user.click(createBtn());
    await waitFor(() => expect(f.byPath(PLATFORMS).some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(f.byPath(PLATFORMS).find((c) => c.method === "POST")!.body!))
      .toEqual({ key: "kwai", display_name: "Kwai", aliases: ["kwai_vn", "kwai_app"] });
  });

  it("CHẶN nhập trùng mã: mã chính trùng mã chính, mã chính trùng mã phụ, mã phụ trùng mã đang có", async () => {
    const f = installFetch(...server());
    renderPage();
    await screen.findByText("YouTube");
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("Kwai"), "Tên bất kỳ");

    await user.type(screen.getByPlaceholderText("kwai"), "youtube");               // trùng mã chính
    expect(await screen.findByRole("alert")).toHaveTextContent('Mã "youtube" đã thuộc nền tảng "YouTube"');
    expect(createBtn()).toBeDisabled();

    await user.clear(screen.getByPlaceholderText("kwai"));
    await user.type(screen.getByPlaceholderText("kwai"), "yt");                    // trùng mã PHỤ của YouTube
    expect(screen.getByRole("alert")).toHaveTextContent('Mã "yt" đã thuộc nền tảng "YouTube"');
    expect(createBtn()).toBeDisabled();

    await user.clear(screen.getByPlaceholderText("kwai"));
    await user.type(screen.getByPlaceholderText("kwai"), "kwai");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(createBtn()).toBeEnabled();
    await user.type(screen.getByPlaceholderText("kwai_vn, kwai_app"), "tiktok");   // mã phụ trùng mã chính của nền tảng khác
    expect(screen.getByRole("alert")).toHaveTextContent('Mã "tiktok" đã thuộc nền tảng "TikTok"');
    expect(createBtn()).toBeDisabled();
    await user.clear(screen.getByPlaceholderText("kwai_vn, kwai_app"));
    await user.type(screen.getByPlaceholderText("kwai_vn, kwai_app"), "kwai");     // mã phụ trùng chính mã chính mới
    expect(screen.getByRole("alert")).toHaveTextContent("trùng mã chính");
    expect(createBtn()).toBeDisabled();
    expect(f.byPath(PLATFORMS).filter((c) => c.method === "POST")).toHaveLength(0);   // không gửi gì lên server
  });

  it("mã sai định dạng bị chặn", async () => {
    installFetch(...server());
    renderPage();
    await screen.findByText("YouTube");
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("kwai"), "1abc");
    await user.type(screen.getByPlaceholderText("Kwai"), "Abc");
    expect(await screen.findByRole("alert")).toHaveTextContent("sai định dạng");
    expect(createBtn()).toBeDisabled();
  });

  it("server vẫn chặn khi có người khác vừa tạo trùng (409): báo lỗi của server, không im lặng", async () => {
    installFetch(...server((c) => c.method === "POST" && c.path === PLATFORMS
      ? jsonResponse({ ok: false, error: { code: "PLATFORM_CODE_EXISTS", message: "Mã nền tảng 'kwai' đã tồn tại", details: {} } },
          { status: 409 }) : undefined));
    const err = vi.spyOn(toast, "error");
    renderPage();
    await screen.findByText("YouTube");
    const user = userEvent.setup();
    await typeCreate(user, "kwai", "Kwai");
    await user.click(createBtn());
    await waitFor(() => expect(err).toHaveBeenCalledWith(expect.stringContaining("đã tồn tại")));
  });

  it("thêm mã phụ cho một nền tảng: POST /aliases; mã trùng bị chặn ngay trong ô", async () => {
    const f = installFetch(...server((c) => c.method === "POST" && c.path === `${PLATFORMS}/tiktok/aliases`
      ? jsonResponse({ key: "tiktok", display_name: "TikTok", enabled: false, aliases: ["tt"] }, { status: 201 }) : undefined));
    renderPage();
    await screen.findByText("TikTok");
    const user = userEvent.setup();
    const input = screen.getByLabelText("Thêm mã cho TikTok");
    await user.type(input, "yt");                                                   // đang là mã phụ của YouTube
    expect(await screen.findByRole("alert")).toHaveTextContent('Mã "yt" đã thuộc nền tảng "YouTube"');
    expect(screen.getByRole("button", { name: "Thêm mã vào TikTok" })).toBeDisabled();
    await user.clear(input);
    await user.type(input, "tt");
    await user.click(screen.getByRole("button", { name: "Thêm mã vào TikTok" }));
    await waitFor(() => expect(f.byPath(`${PLATFORMS}/tiktok/aliases`)).toHaveLength(1));
    expect(JSON.parse(f.byPath(`${PLATFORMS}/tiktok/aliases`)[0].body!)).toEqual({ alias: "tt" });
    expect(await screen.findByRole("button", { name: "Gỡ mã tt khỏi TikTok" })).toBeInTheDocument();
  });

  it("gỡ mã phụ: DELETE đúng đường dẫn, chip biến mất", async () => {
    const f = installFetch(...server((c) => c.method === "DELETE" && c.path === `${PLATFORMS}/youtube/aliases/yt`
      ? jsonResponse({ key: "youtube", display_name: "YouTube", enabled: true, aliases: [] }) : undefined));
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Gỡ mã yt khỏi YouTube" }));
    await waitFor(() => expect(f.byPath(`${PLATFORMS}/youtube/aliases/yt`).some((c) => c.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Gỡ mã yt khỏi YouTube" })).not.toBeInTheDocument());
  });

  it("bật/tắt gửi đúng PUT giữ nguyên tên; sửa tên gửi tên mới giữ nguyên trạng thái", async () => {
    const f = installFetch(...server((c) =>
      c.method === "PUT" && c.path.startsWith(`${PLATFORMS}/`)
        ? jsonResponse({ key: c.path.split("/").pop(), ...JSON.parse(c.body!) }) : undefined));
    renderPage();
    await screen.findByText("YouTube");
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: "Tắt" })[0]);
    await waitFor(() => expect(f.byPath(`${PLATFORMS}/youtube`)).toHaveLength(1));
    expect(JSON.parse(f.byPath(`${PLATFORMS}/youtube`)[0].body!)).toEqual({ display_name: "YouTube", enabled: false });

    await user.click(screen.getByRole("button", { name: "Sửa tên hiển thị TikTok" }));
    const input = screen.getByLabelText("Tên hiển thị cho tiktok");
    await user.clear(input);
    await user.type(input, "TikTok VN");
    await user.click(screen.getByRole("button", { name: /Lưu/ }));
    await waitFor(() => expect(f.byPath(`${PLATFORMS}/tiktok`)).toHaveLength(1));
    expect(JSON.parse(f.byPath(`${PLATFORMS}/tiktok`)[0].body!)).toEqual({ display_name: "TikTok VN", enabled: false });
  });
});

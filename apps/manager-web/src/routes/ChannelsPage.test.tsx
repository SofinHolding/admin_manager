import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { toast } from "sonner";
import { MemoryRouter } from "react-router-dom";
import ChannelsPage from "./ChannelsPage";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

// ── Fixtures ──────────────────────────────────────────────────────────────────

const autoSource = {
  id: "src_auto", owner_account_id: "u1", name: "Nigeria", platform: "facebook", project_key: "",
  default_country: "", origin: "web_manual", created_at: "", updated_at: "",
};

const makeGroup = (id = "grp1", name = "Nigeria", country_code = "NG") => ({
  id, owner_account_id: "u1", name, country_code, sort_order: 0, created_at: "",
});

const makeResource = (id: string, overrides: Record<string, unknown> = {}) => ({
  id, owner_account_id: "u1", platform: "facebook", resource_type: "page", canonical_external_id: "",
  input_ref: "https://facebook.com/kenh-a", username: "", url: "", display_name: "Kênh A", country: "",
  origin: "web_manual", mapping_status: "unmapped", execution_mode: "fallback_local",
  verification_status: "unverified", enabled: 1, created_at: "", updated_at: "", ...overrides,
});

/** Server THẬT trả envelope `{ok:true,data,meta}` (đã bắt gặp lỗi khi mock thiếu `ok`: UI hiện 0 nhóm dù server có nhóm). */
const envelope = (data: unknown) => ({ ok: true, data, meta: { request_id: "req_test", contract_version: "2026-10-01" } });

/**
 * Server mock cho ChannelsPage.
 * groups → /v1/user/channel-groups (envelope thật)
 * items  → /v1/resources
 */
function channelsServer(
  items: unknown[] = [],
  groups: unknown[] = [],
  extra: Responder = () => undefined,
): Responder[] {
  return [
    extra,
    (c) => c.path === "/v1/resources/platforms"
      ? jsonResponse({ items: [{ platform: "facebook", display_name: "Facebook" }] }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources/sources"
      ? jsonResponse({ items: [] }) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/resources/sources"
      ? jsonResponse(autoSource) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/resources"
      ? jsonResponse({ items }) : undefined,
    (c) => c.method === "GET" && c.path === "/v1/user/channel-groups"
      ? jsonResponse(envelope(groups)) : undefined,
    (c) => c.method === "POST" && c.path === "/v1/user/channel-groups"
      ? jsonResponse(envelope(makeGroup("grp-new", JSON.parse(c.body ?? "{}").name ?? "Mới", JSON.parse(c.body ?? "{}").country_code ?? "")))
      : undefined,
    (c) => c.method === "DELETE" && c.path.startsWith("/v1/user/channel-groups/")
      ? jsonResponse(envelope({ deleted: true })) : undefined,
    (c) => c.method === "PATCH" && c.path.startsWith("/v1/user/channel-groups/")
      ? jsonResponse(envelope(makeGroup(c.path.split("/").pop()!, JSON.parse(c.body ?? "{}").name ?? "")))
      : undefined,
  ];
}

/** Mở combobox theo tên (Nền tảng / Quốc gia) và chọn option bằng regex (option có thể có emoji prefix). */
async function chooseOption(user: UserEvent, comboName: string, label: RegExp) {
  await user.click(screen.getByRole("combobox", { name: comboName }));
  await user.click(await screen.findByRole("option", { name: label }));
}

/** POST /v1/resources: trả lại kênh vừa tạo theo body (id tăng dần) — mỗi link một lần gọi. */
function createsChannels(): Responder {
  let n = 0;
  return (c) => {
    if (c.method !== "POST" || c.path !== "/v1/resources") return undefined;
    const b = JSON.parse(c.body!);
    return jsonResponse(makeResource(`r-new-${++n}`, {
      platform: b.platform, country: b.country, input_ref: b.input_ref, display_name: b.input_ref,
    }));
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ChannelsPage — Nhóm quốc gia + kênh (capability:manual_channel_mgmt)", () => {
  it("không có nhóm nào: tiêu đề đúng + hướng dẫn tạo nhóm đầu tiên", async () => {
    installFetch(...channelsServer([], []));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Kênh của tôi");
    expect(await screen.findByText("Chưa có nhóm quốc gia nào")).toBeInTheDocument();
  });

  it("tạo nhóm: gọi POST /v1/user/channel-groups, nhóm mới hiện trong danh sách", async () => {
    const f = installFetch(...channelsServer([], []));
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có nhóm quốc gia nào");

    await user.type(screen.getByPlaceholderText("Nigeria"), "Nigeria");
    await user.type(screen.getByPlaceholderText("NG"), "NG");
    await user.click(screen.getByRole("button", { name: /Tạo nhóm/ }));

    await waitFor(() =>
      expect(f.byPath("/v1/user/channel-groups").some((c) => c.method === "POST")).toBe(true),
    );
    expect(JSON.parse(f.byPath("/v1/user/channel-groups").find((c) => c.method === "POST")!.body!))
      .toEqual({ name: "Nigeria", country_code: "NG" });
    expect(await screen.findByText("Nigeria")).toBeInTheDocument();  // tên nhóm từ body gửi lên
    expect(okSpy).toHaveBeenCalledWith(expect.stringContaining("nhóm"));
  });

  it("thêm nhiều link: chọn Nền tảng + Quốc gia, mỗi dòng một link → mỗi link một kênh gắn đúng nền tảng + quốc gia", async () => {
    const f = installFetch(...channelsServer([], [makeGroup()], createsChannels()));
    const okSpy = vi.spyOn(toast, "success");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có kênh nào trong nhóm này.");
    await chooseOption(user, "Nền tảng", /Facebook/);
    await chooseOption(user, "Quốc gia", /Nigeria/);
    await user.type(screen.getByLabelText(/Link kênh/), "https://facebook.com/a{enter}https://facebook.com/b{enter}  {enter}https://facebook.com/a");
    await user.click(screen.getByRole("button", { name: /Thêm 2 kênh/ }));   // dòng trống + dòng trùng không tính

    await waitFor(() => expect(f.byPath("/v1/resources").filter((c) => c.method === "POST")).toHaveLength(2));
    expect(JSON.parse(f.byPath("/v1/resources/sources").find((c) => c.method === "POST")!.body!))
      .toMatchObject({ platform: "facebook", default_country: "Nigeria" });
    const bodies = f.byPath("/v1/resources").filter((c) => c.method === "POST").map((c) => JSON.parse(c.body!));
    expect(bodies.map((b) => b.input_ref).sort()).toEqual(["https://facebook.com/a", "https://facebook.com/b"]);
    for (const b of bodies) expect(b).toMatchObject({ source_instance_id: "src_auto", platform: "facebook", country: "Nigeria" });
    expect(await screen.findByLabelText("Xóa kênh https://facebook.com/a")).toBeInTheDocument();   // hiện trong nhóm Nigeria
    expect(okSpy).toHaveBeenCalledWith(expect.stringContaining("2 kênh"));
  });

  it("bắt buộc chọn nền tảng VÀ quốc gia (và có link) mới thêm được", async () => {
    installFetch(...channelsServer([], [makeGroup()]));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có kênh nào trong nhóm này.");
    await user.type(screen.getByLabelText(/Link kênh/), "https://facebook.com/kenh-b");
    const submit = () => screen.getByRole("button", { name: /Thêm kênh/ });
    expect(submit()).toBeDisabled();
    await chooseOption(user, "Nền tảng", /Facebook/);
    expect(submit()).toBeDisabled();                       // còn thiếu quốc gia
    await chooseOption(user, "Quốc gia", /Nigeria/);
    expect(submit()).toBeEnabled();
  });

  it("tạo quốc gia mới ngay trong form thêm kênh: tạo nhóm rồi gắn kênh đúng quốc gia đó", async () => {
    const f = installFetch(...channelsServer([], [], createsChannels()));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có nhóm quốc gia nào");
    await chooseOption(user, "Nền tảng", /Facebook/);
    await chooseOption(user, "Quốc gia", /Tạo quốc gia mới/);
    await user.type(screen.getAllByPlaceholderText("Nigeria").at(-1)!, "Kenya");
    await user.type(screen.getByLabelText(/Link kênh/), "https://facebook.com/ke");
    await user.click(screen.getByRole("button", { name: /Thêm kênh/ }));

    await waitFor(() => expect(f.byPath("/v1/resources").some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(f.byPath("/v1/user/channel-groups").find((c) => c.method === "POST")!.body!)).toMatchObject({ name: "Kenya" });
    expect(JSON.parse(f.byPath("/v1/resources").find((c) => c.method === "POST")!.body!))
      .toMatchObject({ platform: "facebook", country: "Kenya", input_ref: "https://facebook.com/ke" });
    expect(await screen.findByRole("button", { name: /Xóa nhóm Kenya/ })).toBeInTheDocument();   // nhóm mới xuất hiện trong danh sách
  });

  it("link đã có trong tài khoản được bỏ qua và báo rõ (không tạo trùng, không im lặng)", async () => {
    const f = installFetch(
      ...channelsServer([makeResource("r-old", { country: "Nigeria", input_ref: "https://facebook.com/a" })],
        [makeGroup()], createsChannels()),
    );
    const warn = vi.spyOn(toast, "warning");
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByLabelText(/Xóa nhóm Nigeria/);
    await chooseOption(user, "Nền tảng", /Facebook/);
    await chooseOption(user, "Quốc gia", /Nigeria/);
    await user.type(screen.getByLabelText(/Link kênh/), "https://facebook.com/a");
    await user.click(screen.getByRole("button", { name: /Thêm kênh/ }));
    await waitFor(() => expect(warn).toHaveBeenCalledWith(expect.stringContaining("đã có")));
    expect(f.byPath("/v1/resources").filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("một link lỗi không làm mất các link khác: link lỗi được giữ lại trong ô để thử lại", async () => {
    installFetch(
      ...channelsServer([], [makeGroup()], (c) => {
        if (c.method !== "POST" || c.path !== "/v1/resources") return undefined;
        const ref = JSON.parse(c.body!).input_ref as string;
        return ref.endsWith("/bad") ? jsonResponse({ detail: "lỗi" }, { status: 422 })
          : jsonResponse(makeResource("r-ok", { country: "Nigeria", input_ref: ref }));
      }),
    );
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Chưa có kênh nào trong nhóm này.");
    await chooseOption(user, "Nền tảng", /Facebook/);
    await chooseOption(user, "Quốc gia", /Nigeria/);
    await user.type(screen.getByLabelText(/Link kênh/), "https://facebook.com/ok{enter}https://facebook.com/bad");
    await user.click(screen.getByRole("button", { name: /Thêm 2 kênh/ }));
    await waitFor(() => expect(screen.getByLabelText(/Link kênh/)).toHaveValue("https://facebook.com/bad"));
  });

  it("kênh không thuộc nhóm nào (country='') hiện ở mục 'Kênh chưa phân nhóm'", async () => {
    installFetch(...channelsServer([makeResource("r-ung")], []));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    expect(await screen.findByText("Kênh A")).toBeInTheDocument();
    expect(screen.getByText(/Kênh chưa phân nhóm/)).toBeInTheDocument();
  });

  it("xóa kênh chưa phân nhóm: gọi DELETE đúng id, bỏ khỏi giao diện", async () => {
    const existing = makeResource("r1");   // country='' → ungrouped
    const f = installFetch(
      ...channelsServer([existing], [], (c) =>
        c.method === "DELETE" && c.path === "/v1/resources/r1" ? jsonResponse({ ok: true }) : undefined,
      ),
    );
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Kênh A");
    await user.click(screen.getByRole("button", { name: "Xóa kênh Kênh A" }));
    await waitFor(() =>
      expect(f.byPath("/v1/resources/r1").some((c) => c.method === "DELETE")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Kênh A")).not.toBeInTheDocument());
    // Sau khi xóa kênh duy nhất, mục ungrouped biến mất
    expect(screen.queryByText(/Kênh chưa phân nhóm/)).not.toBeInTheDocument();
  });

  it("xóa nhóm: gọi DELETE /v1/user/channel-groups/{id}, nhóm biến khỏi danh sách", async () => {
    vi.stubGlobal("confirm", () => true);   // stub trước render để không ném "Not implemented"
    const group = makeGroup();
    const f = installFetch(...channelsServer([], [group]));
    render(<MemoryRouter><ChannelsPage /></MemoryRouter>);
    const user = userEvent.setup();
    await screen.findByText("Nigeria");

    await user.click(screen.getByRole("button", { name: /Xóa nhóm Nigeria/ }));
    await waitFor(() =>
      expect(f.byPath("/v1/user/channel-groups/grp1").some((c) => c.method === "DELETE")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Nigeria")).not.toBeInTheDocument());
  });
});

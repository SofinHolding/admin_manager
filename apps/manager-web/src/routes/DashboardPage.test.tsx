import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import DashboardPage, { compactNum } from "./DashboardPage";
import { daysAgo, todayStr } from "../filters/filterState";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { __resetHttpForTests } from "../lib/http";
import { installFetch, jsonResponse, parseCall, type FetchCall, type Responder } from "../test/helpers";

afterEach(() => { vi.unstubAllGlobals(); clearTokens(); __resetHttpForTests(); });

// ── dữ liệu giả ở RANH GIỚI MẠNG ────────────────────────────────────────────

const FRESH = { as_of: "2026-10-05T06:55:00Z", lag_seconds: 3.2, stale: false };

interface Opts {
  stale?: boolean;
  views?: number;
  channelName?: string;
  failAll?: number;
  appliedOwner?: string;
  prev?: number;
  empty?: boolean;
}

function echoFilter(c: FetchCall) {
  const f: Record<string, string> = {};
  for (const k of ["date_from", "date_to", "owner_account_id", "platform", "country", "project", "group_id", "platform_tag"]) {
    const v = c.params.get(k);
    if (v) f[k] = v;
  }
  return f;
}

function analytics(o: Opts = {}): Responder {
  return (c) => {
    if (!c.path.startsWith("/v1/analytics/") && c.path !== "/v1/admin/users") return undefined;
    if (o.failAll) return jsonResponse({ detail: "Dịch vụ tạm thời không khả dụng" }, { status: o.failAll });
    const meta = {
      filter: o.appliedOwner ? { ...echoFilter(c), owner_account_id: o.appliedOwner } : echoFilter(c),
      freshness: { ...FRESH, stale: !!o.stale, lag_seconds: o.stale ? 7200 : 3.2 },
    };
    const p = c.path.replace("/v1/analytics/", "");
    if (c.path === "/v1/admin/users") {
      return jsonResponse({ users: [{ id: "u1", username: "alice", email: "a@x", role: "viewer", status: "active", created_at: "", last_login: "", invite_key: "" }] });
    }
    if (p === "summary") {
      if (o.empty) return jsonResponse({ total_views: 0, total_views_prev: 0, videos_posted: 0, videos_posted_prev: 0, active_channels: 0, managed_channels: 0, days_in_range: 30, ...meta });
      return jsonResponse({ total_views: o.views ?? 1234567, total_views_prev: o.prev ?? 1, videos_posted: 5, videos_posted_prev: 4, active_channels: 2, managed_channels: 3, days_in_range: 30, ...meta });
    }
    if (p === "traffic") return jsonResponse({ scope: "", scope_label: "Tổng", country: "", days: [{ date: "2026-09-06", views: 1, interactions: 0, comments: 0, videos: 0 }], totals: { views: 1, interactions: 0, comments: 0, videos: 0 }, ...meta });
    if (p === "top-channels") return jsonResponse({ items: [{ id: "page:1", name: o.channelName ?? "Kenh A", platform: "facebook", country: "VN", views: 10 }], ...meta });
    if (p === "views-by-country") return jsonResponse({ countries: ["Vietnam"], days: [{ date: "2026-09-06", Vietnam: 3 }], totals: { Vietnam: 3 }, ...meta });
    if (p === "channel-breakdown") return jsonResponse({ total: { managed: 3, active: 2 }, by_platform: [{ platform: "facebook", managed: 3, active: 2 }], ...meta });
    if (p === "country-tags") return jsonResponse({ items: [{ id: "1", name: "Vietnam" }] });
    if (p === "platform-tags") return jsonResponse({ items: [] });
    if (p === "metrics") return jsonResponse({ metrics: [] });
    if (p === "groups") return jsonResponse({ items: [] });
    if (p.startsWith("compare/")) {
      const dim = p.slice("compare/".length);
      return jsonResponse({ dimension: dim, items: [{ key: "a", name: `${dim}-A`, total: 5, series: [{ date: "2026-09-06", value: 5 }] }], ...meta });
    }
    return jsonResponse({ detail: "unknown" }, { status: 404 });
  };
}

const DATA_ENDPOINTS = ["summary", "traffic", "top-channels", "views-by-country", "channel-breakdown"] as const;
const at = (p: string) => `/v1/analytics/${p}`;
const dataParams = (c: FetchCall) => { const o = Object.fromEntries(c.params); delete o.limit; return o; };

// ── render ──────────────────────────────────────────────────────────────────

const adminUser: User = { id: "admin-1", username: "root", email: "r@x", role: "admin", perms: [] };
const viewerUser: User = { id: "viewer-1", username: "vi", email: "v@x", role: "viewer", perms: [] };

function authValue(user: User): AuthCtx {
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: () => true,
  };
}

let navigateTo: (to: string) => void = () => {};
function Spy() {
  const nav = useNavigate();
  const loc = useLocation();
  navigateTo = (to) => nav(to);
  return <output data-testid="loc">{loc.pathname + loc.search}</output>;
}

function renderDashboard(url: string, user: User = adminUser) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <AuthContext.Provider value={authValue(user)}>
        <Spy />
        <DashboardPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

const FULL = "/dashboard?date_from=2026-09-01&date_to=2026-09-30&owner_account_id=u1&platform=youtube&country=Vietnam&project=p1&group_id=g1&platform_tag=tiktok";
const FULL_PARAMS = {
  date_from: "2026-09-01", date_to: "2026-09-30", owner_account_id: "u1", platform: "youtube",
  country: "Vietnam", project: "p1", group_id: "g1", platform_tag: "tiktok",
};

const waitAllData = (f: ReturnType<typeof installFetch>, n = 1) =>
  waitFor(() => { for (const e of DATA_ENDPOINTS) expect(f.byPath(at(e)).length).toBeGreaterThanOrEqual(n); });

describe("Dashboard — MỌI KPI/biểu đồ dùng CÙNG MỘT Filter Context", () => {
  it("5 endpoint dữ liệu gửi CHÍNH XÁC cùng bộ tham số (đủ 8 tham số chuẩn)", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    for (const e of DATA_ENDPOINTS) {
      const calls = f.byPath(at(e));
      expect(calls).toHaveLength(1);
      expect(dataParams(calls[0])).toEqual(FULL_PARAMS);
    }
    expect(f.byPath(at("top-channels"))[0].params.get("limit")).toBe("15");
  });

  it("danh mục lọc (country-tags/platform-tags) không bị co theo chính ô đang chọn", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitFor(() => expect(f.byPath(at("country-tags"))).toHaveLength(1));
    const p = Object.fromEntries(f.byPath(at("country-tags"))[0].params);
    expect(p.country).toBeUndefined();
    expect(p.date_from).toBeUndefined();
    expect(p.owner_account_id).toBe("u1");
    await waitFor(() => expect(f.byPath(at("platform-tags"))).toHaveLength(1));
    expect(Object.fromEntries(f.byPath(at("platform-tags"))[0].params)).toEqual({ owner_account_id: "u1" });
  });

  it("đổi bộ lọc (URL) => TẤT CẢ widget cùng gửi lại với CÙNG tham số mới", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    act(() => navigateTo("/dashboard?date_from=2026-08-01&date_to=2026-08-31&owner_account_id=u2&country=Thailand"));
    await waitAllData(f, 2);
    const next = { date_from: "2026-08-01", date_to: "2026-08-31", owner_account_id: "u2", country: "Thailand" };
    for (const e of DATA_ENDPOINTS) {
      const calls = f.byPath(at(e));
      expect(calls).toHaveLength(2);
      expect(dataParams(calls[1])).toEqual(next);
    }
  });

  it("đổi bộ lọc bằng giao diện (ngày + nút Lọc) => ghi lên URL và mọi widget đổi cùng nhau", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard?date_from=2026-09-01&date_to=2026-09-30");
    await waitAllData(f);
    const user = userEvent.setup();
    const [fromInput] = screen.getAllByPlaceholderText("dd/mm/yyyy");
    await user.clear(fromInput);
    await user.type(fromInput, "10/09/2026");
    await user.tab();
    await user.click(screen.getByRole("button", { name: /Áp dụng/ }));
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toContain("date_from=2026-09-10"));
    await waitAllData(f, 2);
    for (const e of DATA_ENDPOINTS) {
      expect(dataParams(f.byPath(at(e))[1])).toEqual({ date_from: "2026-09-10", date_to: "2026-09-30" });
    }
  });
});

describe("Dashboard — request cũ bị huỷ, không đè dữ liệu mới", () => {
  it("đổi lọc huỷ request đang bay (signal.aborted) và dữ liệu về muộn của bộ lọc cũ KHÔNG hiển thị", async () => {
    // fetch cố tình KHÔNG tôn trọng abort (như proxy/trình duyệt vẫn trả về muộn) để chứng minh chính UI chặn dữ liệu cũ.
    const pendingSummary: { resolve: (r: Response) => void; signal?: AbortSignal | null }[] = [];
    const base = analytics({ views: 555000 });
    const calls: FetchCall[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const c = parseCall(input, init);
      calls.push(c);
      if (c.path === at("summary")) {
        return new Promise<Response>((resolve) => pendingSummary.push({ resolve, signal: init?.signal }));
      }
      return Promise.resolve(base(c) ?? jsonResponse({}, { status: 404 }));
    }));

    renderDashboard("/dashboard?country=Vietnam");
    await waitFor(() => expect(pendingSummary).toHaveLength(1));
    act(() => navigateTo("/dashboard?country=Thailand"));
    await waitFor(() => expect(pendingSummary).toHaveLength(2));

    expect(pendingSummary[0].signal?.aborted).toBe(true);   // cũ bị huỷ
    expect(pendingSummary[1].signal?.aborted).toBe(false);

    const body = (n: number) => jsonResponse({ total_views: n, total_views_prev: 1, videos_posted: 1, videos_posted_prev: 1, active_channels: 1, managed_channels: 1, days_in_range: 30 });
    await act(async () => { pendingSummary[1].resolve(body(222222)); });
    await waitFor(() => expect(screen.getAllByText("222.222").length).toBeGreaterThan(0));
    await act(async () => { pendingSummary[0].resolve(body(111111)); });   // cũ về MUỘN sau cùng
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getAllByText("222.222").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("111.111")).toHaveLength(0);
  });
});

describe("Dashboard — freshness & analytics không khả dụng", () => {
  it("stale=true => banner 'Dữ liệu cũ' kèm thời điểm và độ trễ", async () => {
    const f = installFetch(analytics({ stale: true }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    const b = await screen.findByTestId("stale-banner");
    expect(b).toHaveTextContent("Dữ liệu cũ");
    expect(b).toHaveTextContent("2 giờ");
  });

  it("stale=false => không có banner cũ, có dòng 'Dữ liệu cập nhật lúc'", async () => {
    const f = installFetch(analytics({ stale: false }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    await screen.findByTestId("freshness-line");
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
  });

  it("503 ngay từ đầu => trạng thái rõ ràng, chưa có dữ liệu, KHÔNG vỡ trang", async () => {
    installFetch(analytics({ failAll: 503 }));
    renderDashboard("/dashboard");
    const b = await screen.findByTestId("analytics-unavailable");
    expect(b).toHaveTextContent("không khả dụng");
    expect(b).toHaveTextContent("Chưa có dữ liệu");
    expect(screen.getByRole("tab", { name: "Chi tiết theo kênh" })).toBeInTheDocument();
  });

  it("lỗi mạng (fetch reject) cũng được coi là không khả dụng", async () => {
    installFetch((c) => { if (c.path.startsWith("/v1/analytics/")) throw new TypeError("Failed to fetch"); });
    renderDashboard("/dashboard");
    expect(await screen.findByTestId("analytics-unavailable")).toBeInTheDocument();
  });

  it("503 SAU khi đã có dữ liệu => GIỮ dữ liệu cuối + banner", async () => {
    let down = false;
    const ok = analytics({ views: 1234567 });
    const f = installFetch((c) => (down && c.path.startsWith("/v1/analytics/") ? jsonResponse({ detail: "down" }, { status: 503 }) : ok(c)));
    renderDashboard("/dashboard");
    await waitFor(() => expect(screen.getAllByText("1.234.567").length).toBeGreaterThan(0));
    down = true;
    act(() => navigateTo("/dashboard?country=Thailand"));
    await screen.findByTestId("analytics-unavailable");
    expect(screen.getByTestId("analytics-unavailable")).toHaveTextContent("dữ liệu tải thành công gần nhất");
    expect(screen.getAllByText("1.234.567").length).toBeGreaterThan(0);
    expect(f.byPath(at("summary")).length).toBeGreaterThanOrEqual(2);
  });

  it("lỗi 403 (không phải không khả dụng) => báo lỗi và KHÔNG giữ số cũ", async () => {
    installFetch(analytics({ failAll: 403 }));
    renderDashboard("/dashboard");
    const a = await screen.findByText(/Không tải được số liệu/);
    expect(a).toBeInTheDocument();
    expect(screen.queryByTestId("analytics-unavailable")).not.toBeInTheDocument();
  });
});

describe("Dashboard — bộ lọc server thực sự áp dụng", () => {
  it("viewer bị ép owner = chính mình => hiển thị 'tài khoản của bạn'", async () => {
    const f = installFetch(analytics({ appliedOwner: "viewer-1" }));
    renderDashboard("/dashboard?owner_account_id=someone-else", viewerUser);
    await waitAllData(f);
    const bar = await screen.findByTestId("applied-filter");
    expect(bar).toHaveTextContent("tài khoản của bạn");
    expect(bar).not.toHaveTextContent("someone-else");
  });
});

describe("Dashboard — so sánh quốc gia chỉ ở biểu đồ RIÊNG", () => {
  it("tab 'Chi tiết theo kênh' có 'So sánh quốc gia'; tab 'So sánh chung' KHÔNG render lại và không gọi compare/country", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    expect(await screen.findByText("So sánh quốc gia")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "So sánh chung" }));
    await waitFor(() => {
      for (const d of ["platform", "owner", "channel", "group"]) expect(f.byPath(at(`compare/${d}`)).length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("So sánh quốc gia")).not.toBeInTheDocument();
    expect(f.byPath(at("compare/country"))).toHaveLength(0);
    // 4 thẻ so sánh của "chung"
    for (const t of ["So sánh nền tảng", "So sánh nhân sự", "So sánh kênh", "So sánh nhóm kênh"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    // và các request so sánh dùng CÙNG bộ lọc với KPI/biểu đồ
    for (const d of ["platform", "owner", "channel", "group"]) {
      expect(dataParams(f.byPath(at(`compare/${d}`))[0])).toEqual(FULL_PARAMS);
    }
  });

  it("viewer không có thẻ so sánh nhân sự", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard", viewerUser);
    await waitAllData(f);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "So sánh chung" }));
    await waitFor(() => expect(f.byPath(at("compare/platform")).length).toBeGreaterThan(0));
    expect(screen.queryByText("So sánh nhân sự")).not.toBeInTheDocument();
    expect(f.byPath(at("compare/owner"))).toHaveLength(0);
  });
});

describe("Dashboard — chống XSS", () => {
  it("tên kênh chứa HTML được render như văn bản, không tạo phần tử", async () => {
    const evil = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>';
    const f = installFetch(analytics({ channelName: evil }));
    const { container } = renderDashboard("/dashboard");
    await waitAllData(f);
    await waitFor(() => expect(screen.getByText(evil)).toBeInTheDocument());
    expect(container.querySelector("img[src='x']")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });
});

describe("Dashboard — câu tóm tắt bằng lời có số", () => {
  const headerOf = () => screen.getByRole("heading", { level: 1, name: "Tổng quan" }).parentElement!;

  it("có kỳ trước: nói số lượt xem và so sánh bằng phần trăm", async () => {
    const f = installFetch(analytics({ views: 1000, prev: 800 }));
    renderDashboard("/dashboard", viewerUser);
    await waitAllData(f);
    await waitFor(() => expect(headerOf()).toHaveTextContent("Kênh của bạn được xem 1.000 lần trong 30 ngày qua"));
    expect(headerOf()).toHaveTextContent("nhiều hơn 25% so với 30 ngày trước");
  });

  it("kỳ trước nhiều hơn: nói 'ít hơn'", async () => {
    const f = installFetch(analytics({ views: 600, prev: 800 }));
    renderDashboard("/dashboard", viewerUser);
    await waitAllData(f);
    await waitFor(() => expect(headerOf()).toHaveTextContent("ít hơn 25% so với 30 ngày trước"));
  });

  it("chưa có kỳ trước: nói giản dị, không để 'chưa so được' cụt lủn", async () => {
    const f = installFetch(analytics({ views: 1000, prev: 0 }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    await waitFor(() => expect(headerOf()).toHaveTextContent("chưa có số liệu của 30 ngày trước nên chưa so sánh được"));
    expect(headerOf()).not.toHaveTextContent("chưa so được");
  });

  it("không có dữ liệu: EmptyState chỉ đường tới /integrations và /resources", async () => {
    const f = installFetch(analytics({ empty: true }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    expect(await screen.findByText("Chưa có số liệu để hiển thị")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kết nối công cụ đẩy dữ liệu/ })).toHaveAttribute("href", "/integrations");
    expect(screen.getByRole("link", { name: /Thêm kênh thủ công/ })).toHaveAttribute("href", "/resources");
  });

  it("dải số liệu: so sánh bằng chữ kèm biểu tượng, có số video mỗi ngày và số kênh", async () => {
    const f = installFetch(analytics({ views: 1100, prev: 800 }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    expect((await screen.findAllByText("Tổng lượt xem")).some((e) => e.tagName === "DT")).toBe(true);
    expect(screen.getByText("Video đã đăng")).toBeInTheDocument();
    expect(screen.getByText("Kênh đang hoạt động")).toBeInTheDocument();
    expect(screen.getByText(/khoảng 0,2 video mỗi ngày/)).toBeInTheDocument();
    const note = screen.getByText("nhiều hơn 38% so với kỳ trước");
    expect(note.previousElementSibling?.tagName.toLowerCase()).toBe("svg");
  });
});

describe("Dashboard — lọc nhanh và Lọc thêm", () => {
  it("mặc định chọn '30 ngày'; bấm 7 và 90 ngày => URL và MỌI widget đổi cùng bộ tham số", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard");
    await waitAllData(f);
    expect(screen.getByRole("button", { name: /30 ngày/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^7 ngày/ })).toHaveAttribute("aria-pressed", "false");
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /^7 ngày/ }));
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toContain(`date_from=${daysAgo(6)}`));
    await waitAllData(f, 2);
    for (const e of DATA_ENDPOINTS) {
      expect(dataParams(f.byPath(at(e))[1])).toEqual({ date_from: daysAgo(6), date_to: todayStr() });
    }
    expect(screen.getByRole("button", { name: /^7 ngày/ })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: /^90 ngày/ }));
    await waitAllData(f, 3);
    for (const e of DATA_ENDPOINTS) {
      expect(dataParams(f.byPath(at(e))[2])).toEqual({ date_from: daysAgo(89), date_to: todayStr() });
    }
  });

  it("'Tùy chọn khoảng ngày' mở ô nhập ngày; mặc định chưa hiện", async () => {
    installFetch(analytics());
    renderDashboard("/dashboard");
    expect(screen.queryAllByPlaceholderText("dd/mm/yyyy")).toHaveLength(0);
    await userEvent.setup().click(screen.getByRole("button", { name: "Tùy chọn khoảng ngày" }));
    expect(screen.getAllByPlaceholderText("dd/mm/yyyy")).toHaveLength(2);
  });

  it("quốc gia và nhân sự nằm trong 'Lọc thêm' (gập được)", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard");
    await waitAllData(f);
    const user = userEvent.setup();
    expect(screen.queryByLabelText("Quốc gia")).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: /Lọc thêm/ });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Quốc gia")).toBeInTheDocument();
    expect(screen.getByLabelText("Nhân sự")).toBeInTheDocument();
  });

  it("'Đang xem' nói bằng lời, không còn mũi tên ngày thô", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard?country=Vietnam");
    await waitAllData(f);
    const bar = await screen.findByTestId("applied-filter");
    expect(bar).toHaveTextContent("Đang xem:");
    expect(bar).toHaveTextContent("quốc gia Vietnam");
    expect(bar.textContent).not.toContain("→");
  });
});

describe("Dashboard — biểu đồ có bản thay thế cho trình đọc màn hình", () => {
  it("biểu đồ diễn biến có aria-label tóm tắt và bảng 'Xem dưới dạng bảng' chứa số liệu", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard");
    await waitAllData(f);
    const img = await screen.findByRole("img", { name: /Biểu đồ đường: Lượt xem theo ngày/ });
    expect(img).toHaveAccessibleName(/từ 06\/09\/2026 đến 06\/09\/2026/);
    expect(screen.getAllByText("Xem dưới dạng bảng").length).toBeGreaterThanOrEqual(1);
    const table = screen.getAllByRole("table")[0];
    expect(table).toHaveTextContent("06/09/2026");
    expect(table).toHaveTextContent("Tổng");
  });

  it("kênh nổi bật là danh sách có thứ tự kèm chỉ báo sắp xếp", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard");
    await waitAllData(f);
    const list = await screen.findByRole("list", { name: "Kênh nổi bật theo lượt xem" });
    expect(list.tagName).toBe("OL");
    expect(screen.getAllByText(/Xếp theo lượt xem, cao nhất ở trên/).length).toBeGreaterThan(0);
  });
});

describe("compactNum — quy ước số trên trục", () => {
  it("nghìn = N, triệu = tr, tỷ; dấu phẩy thập phân kiểu Việt Nam", () => {
    expect(compactNum(797604)).toBe("797,6 N");
    expect(compactNum(1234567)).toBe("1,2 tr");
    expect(compactNum(2_500_000_000)).toBe("2,5 tỷ");
    expect(compactNum(950)).toBe("950");
    expect(compactNum(0)).toBe("0");
  });
});

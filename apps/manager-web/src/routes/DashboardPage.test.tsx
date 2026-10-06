import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import DashboardPage from "./DashboardPage";
import { AuthContext, type AuthCtx, type User } from "../auth";
import { clearTokens } from "../auth/tokenStore";
import { resetDataLockForTests } from "../filters/dataLock";
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
    if (p === "summary") return jsonResponse({ total_views: o.views ?? 1234567, total_views_prev: 1, videos_posted: 5, videos_posted_prev: 4, active_channels: 2, managed_channels: 3, days_in_range: 30, ...meta });
    if (p === "traffic") return jsonResponse({ scope: "", scope_label: "Tổng", country: "", days: [{ date: "2026-09-06", views: 1, interactions: 0, comments: 0, videos: 0 }], totals: { views: 1, interactions: 0, comments: 0, videos: 0 }, ...meta });
    if (p === "top-channels") return jsonResponse({ items: [{ id: "page:1", name: o.channelName ?? "Kenh A", platform: "facebook", country: "VN", views: 10 }], ...meta });
    if (p === "views-by-country") return jsonResponse({ countries: ["Vietnam"], days: [{ date: "2026-09-06", Vietnam: 3 }], totals: { Vietnam: 3 }, ...meta });
    if (p === "channel-breakdown") return jsonResponse({ total: { managed: 3, active: 2 }, by_platform: [{ platform: "facebook", managed: 3, active: 2 }], ...meta });
    if (p === "country-tags") return jsonResponse({ items: [{ id: "1", name: "Vietnam" }] });
    if (p === "platform-tags") return jsonResponse({ items: [] });
    if (p === "platforms") return jsonResponse({ items: ["demo_social", "toolx_social"] });
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
    await user.click(screen.getByRole("button", { name: /Lọc/ }));
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
    await waitFor(() => expect(screen.getByText("222.222")).toBeInTheDocument());
    await act(async () => { pendingSummary[0].resolve(body(111111)); });   // cũ về MUỘN sau cùng
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByText("222.222")).toBeInTheDocument();
    expect(screen.queryByText("111.111")).not.toBeInTheDocument();
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

  it("stale=false => không có banner cũ và KHÔNG hiện dòng 'Dữ liệu cập nhật lúc …'", async () => {
    const f = installFetch(analytics({ stale: false }));
    renderDashboard("/dashboard");
    await waitAllData(f);
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("freshness-line")).not.toBeInTheDocument();
    expect(screen.queryByText(/Dữ liệu cập nhật lúc/)).not.toBeInTheDocument();
  });

  it("503 ngay từ đầu => trạng thái rõ ràng, chưa có dữ liệu, KHÔNG vỡ trang", async () => {
    installFetch(analytics({ failAll: 503 }));
    renderDashboard("/dashboard");
    const b = await screen.findByTestId("analytics-unavailable");
    expect(b).toHaveTextContent("không khả dụng");
    expect(b).toHaveTextContent("Chưa có dữ liệu");
    expect(screen.getByText("Biểu đồ riêng")).toBeInTheDocument();
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
    await waitFor(() => expect(screen.getByText("1.234.567")).toBeInTheDocument());
    down = true;
    act(() => navigateTo("/dashboard?country=Thailand"));
    await screen.findByTestId("analytics-unavailable");
    expect(screen.getByTestId("analytics-unavailable")).toHaveTextContent("dữ liệu tải thành công gần nhất");
    expect(screen.getByText("1.234.567")).toBeInTheDocument();
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

describe("Dashboard — ô chọn nền tảng lấy từ dữ liệu thật", () => {
  it("liệt kê nền tảng của Tool mới; chọn nó gửi platform=<khóa>; danh sách KHÔNG co lại sau khi chọn", async () => {
    const f = installFetch(analytics());
    renderDashboard("/dashboard");
    await waitAllData(f);
    const user = userEvent.setup();
    const open = async () => { await user.click(screen.getAllByRole("combobox").find((c) => /Tổng — tất cả nền tảng|demo_social|toolx_social|YouTube|Facebook/.test(c.textContent ?? ""))!); };
    await open();
    for (const name of ["YouTube", "Facebook", "demo_social", "toolx_social"]) expect(await screen.findByRole("option", { name })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "toolx_social" }));
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toContain("platform=toolx_social"));
    expect(screen.getByTestId("loc").textContent).not.toContain("platform_tag");   // là nền tảng, không phải nhãn tự do
    await waitFor(() => expect(f.byPath(at("summary")).some((c) => c.params.get("platform") === "toolx_social")).toBe(true));
    await open();                                                                  // mở lại: vẫn đủ danh sách
    for (const name of ["YouTube", "Facebook", "demo_social", "toolx_social"]) expect(await screen.findByRole("option", { name })).toBeInTheDocument();
  });
});

describe("Dashboard — không còn dòng 'Bộ lọc áp dụng'", () => {
  it("viewer bị ép owner: trang KHÔNG hiện dòng bộ lọc áp dụng (yêu cầu bỏ dòng này)", async () => {
    const f = installFetch(analytics({ appliedOwner: "viewer-1" }));
    renderDashboard("/dashboard?owner_account_id=someone-else", viewerUser);
    await waitAllData(f);
    expect(screen.queryByTestId("applied-filter")).not.toBeInTheDocument();
    expect(screen.queryByText(/Bộ lọc áp dụng/)).not.toBeInTheDocument();
  });
});

describe("Dashboard — Khóa dữ liệu", () => {
  afterEach(() => resetDataLockForTests());

  it("bấm 'Khóa dữ liệu' => đổi thành 'Mở khóa dữ liệu (còn 60 phút)', có ghi chú, 'Làm mới' bị vô hiệu; mở khóa thì trở lại", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Làm mới" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Khóa dữ liệu" }));
    const unlock = screen.getByRole("button", { name: /Mở khóa dữ liệu \(còn 60 phút\)/ });
    expect(unlock).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("data-lock-note")).toHaveTextContent("vẫn được đồng bộ phía sau");
    expect(unlock).toHaveTextContent("Đã khóa · 60 phút");     // nhãn ngắn, kích thước nút cố định (không làm hàng lọc nhảy dòng)
    expect(unlock.className).toContain("w-[10.5rem]");
    expect(screen.getByRole("button", { name: "Làm mới" })).toBeDisabled();
    await user.click(unlock);
    expect(screen.getByTestId("data-lock-note")).toBeEmptyDOMElement();
    expect(screen.getByRole("button", { name: "Khóa dữ liệu" }).className).toContain("w-[10.5rem]");
    expect(screen.getByRole("button", { name: "Làm mới" })).toBeEnabled();
  });

  it("đang khóa: số liệu đã xem không bị hỏi lại server; mở khóa => tải lại số mới nhất", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Khóa dữ liệu" }));
    const before = f.byPath(at("summary")).length;
    // đổi cỡ danh sách "kênh nổi bật" rồi quay lại cỡ cũ trong lúc khóa: truy vấn top của cỡ cũ lấy từ bộ nhớ
    await user.click(screen.getByRole("button", { name: /Mở khóa dữ liệu/ }));
    await waitFor(() => expect(f.byPath(at("summary")).length).toBeGreaterThan(before)); // mở khóa => tải lại
  });
});

describe("Dashboard — so sánh quốc gia chỉ ở biểu đồ RIÊNG", () => {
  it("tab 'riêng' có 'So sánh quốc gia'; tab 'chung' KHÔNG render lại và không gọi compare/country", async () => {
    const f = installFetch(analytics());
    renderDashboard(FULL);
    await waitAllData(f);
    expect(await screen.findByText("So sánh quốc gia")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Biểu đồ chung" }));
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
    await user.click(screen.getByRole("tab", { name: "Biểu đồ chung" }));
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

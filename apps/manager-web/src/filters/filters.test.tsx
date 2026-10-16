import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { FilterProvider, useFilterContext } from "./FilterContext";
import {
  countryCatalogParams, defaultFilter, filterToParams, parseFilter, platformTagCatalogParams, writeFilterToUrl,
  FILTER_KEYS,
} from "./filterState";
import { COMMON_COMPARE_DIMENSIONS } from "./compareDimensions";
import { mergeFreshness } from "./freshness";

const NOW = new Date(2026, 9, 5); // 2026-10-05

describe("filterState (thuần)", () => {
  it("mặc định 30 ngày gần nhất, các bộ lọc khác rỗng", () => {
    const f = defaultFilter(NOW);
    expect(f.date_to).toBe("2026-10-05");
    expect(f.date_from).toBe("2026-09-06");
    for (const k of FILTER_KEYS) if (k !== "date_from" && k !== "date_to") expect(f[k]).toBe("");
  });

  it("parse đủ 7 tham số chuẩn từ URL", () => {
    const f = parseFilter(new URLSearchParams(
      "date_from=2026-01-01&date_to=2026-01-31&owner_account_id=u1&platform=youtube&country=Vietnam&group_id=g1&platform_tag=tiktok"), NOW);
    expect(f).toEqual({
      date_from: "2026-01-01", date_to: "2026-01-31", owner_account_id: "u1", platform: "youtube",
      country: "Vietnam", group_id: "g1", platform_tag: "tiktok",
    });
  });

  it("giá trị rác trong link chia sẻ rơi về mặc định; ngày đảo thì tự hoán đổi", () => {
    const f = parseFilter(new URLSearchParams("date_from=hack&date_to=2026-02-01"), NOW);
    // date_from rác -> mặc định 2026-09-06, lớn hơn date_to -> hoán đổi
    expect(f.date_from).toBe("2026-02-01");
    expect(f.date_to).toBe("2026-09-06");
    const g = parseFilter(new URLSearchParams("date_from=2026-03-10&date_to=2026-03-01"), NOW);
    expect(g.date_from).toBe("2026-03-01");
    expect(g.date_to).toBe("2026-03-10");
  });

  it("filterToParams chỉ gồm giá trị không rỗng (ngày luôn có)", () => {
    const p = filterToParams({ ...defaultFilter(NOW), country: "Vietnam" });
    expect(Object.keys(p).sort()).toEqual(["country", "date_from", "date_to"]);
  });

  it("danh mục: country-tags bỏ ngày + country; platform-tags chỉ owner", () => {
    const f = { ...defaultFilter(NOW), country: "VN", owner_account_id: "u1", group_id: "g", platform_tag: "t", platform: "youtube" };
    expect(Object.fromEntries(countryCatalogParams(f))).toEqual({ owner_account_id: "u1", group_id: "g", platform_tag: "t", platform: "youtube" });
    expect(Object.fromEntries(platformTagCatalogParams(f))).toEqual({ owner_account_id: "u1" });
  });

  it("writeFilterToUrl giữ tham số lạ, không ghi ngày mặc định nếu URL chưa có", () => {
    const f = { ...defaultFilter(NOW), country: "VN" };
    const u = writeFilterToUrl(new URLSearchParams("foo=bar"), f, NOW);
    expect(u.get("foo")).toBe("bar");
    expect(u.get("country")).toBe("VN");
    expect(u.has("date_from")).toBe(false);
  });
});

describe("compareDimensions & freshness", () => {
  it("'Biểu đồ chung' KHÔNG gồm so sánh quốc gia", () => {
    expect(COMMON_COMPARE_DIMENSIONS).not.toContain("country");
    expect([...COMMON_COMPARE_DIMENSIONS].sort()).toEqual(["channel", "group", "owner", "platform"]);
  });

  it("mergeFreshness: xấu nhất thắng (stale bất kỳ, as_of cũ nhất, lag lớn nhất)", () => {
    expect(mergeFreshness([])).toBeNull();
    expect(mergeFreshness([undefined, null])).toBeNull();
    const m = mergeFreshness([
      { as_of: "2026-10-05T06:00:00Z", lag_seconds: 10, stale: false },
      { as_of: "2026-10-05T04:00:00Z", lag_seconds: 7200, stale: true },
    ]);
    expect(m).toEqual({ as_of: "2026-10-05T04:00:00Z", lag_seconds: 7200, stale: true });
  });
});

function Probe() {
  const { filter, params, key, setFilter, resetFilter } = useFilterContext();
  const loc = useLocation();
  const nav = useNavigate();
  return (
    <div>
      <output data-testid="params">{JSON.stringify(params)}</output>
      <output data-testid="key">{key}</output>
      <output data-testid="country">{filter.country}</output>
      <output data-testid="search">{loc.search}</output>
      <button onClick={() => setFilter({ country: "Vietnam" })}>country</button>
      <button onClick={() => { setFilter({ owner_account_id: "u9" }); setFilter({ group_id: "g9" }); }}>two</button>
      <button onClick={() => setFilter({ date_from: "2026-05-02", date_to: "2026-05-01" })}>swap</button>
      <button onClick={() => resetFilter()}>reset</button>
      <button onClick={() => nav("/dashboard?country=Thailand&platform=facebook")}>nav</button>
    </div>
  );
}

function renderProbe(initial = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <FilterProvider><Probe /></FilterProvider>
    </MemoryRouter>,
  );
}
const read = (id: string) => screen.getByTestId(id).textContent ?? "";

describe("FilterProvider — một nguồn sự thật, đồng bộ URL", () => {
  it("đọc bộ lọc từ URL khi khởi tạo (link chia sẻ)", () => {
    renderProbe("/dashboard?country=Vietnam&owner_account_id=u1");
    expect(read("country")).toBe("Vietnam");
    expect(JSON.parse(read("params")).owner_account_id).toBe("u1");
  });

  it("setFilter ghi ngược lên URL (replace) và đổi `key`", async () => {
    renderProbe();
    const k0 = read("key");
    await act(async () => { screen.getByText("country").click(); });
    expect(read("search")).toContain("country=Vietnam");
    expect(read("key")).not.toBe(k0);
    expect(read("country")).toBe("Vietnam");
  });

  it("hai setFilter trong cùng một tick KHÔNG ghi đè nhau", async () => {
    renderProbe();
    await act(async () => { screen.getByText("two").click(); });
    const p = JSON.parse(read("params"));
    expect(p.owner_account_id).toBe("u9");
    expect(p.group_id).toBe("g9");
    expect(read("search")).toContain("owner_account_id=u9");
    expect(read("search")).toContain("group_id=g9");
  });

  it("đổi URL (back/forward, dán link) cập nhật bộ lọc", async () => {
    renderProbe("/dashboard?country=Vietnam");
    await act(async () => { screen.getByText("nav").click(); });
    expect(read("country")).toBe("Thailand");
    expect(JSON.parse(read("params")).platform).toBe("facebook");
  });

  it("ngày đảo thứ tự được hoán đổi; reset về mặc định xoá query", async () => {
    renderProbe();
    await act(async () => { screen.getByText("swap").click(); });
    const p = JSON.parse(read("params"));
    expect(p.date_from).toBe("2026-05-01");
    expect(p.date_to).toBe("2026-05-02");
    await act(async () => { screen.getByText("reset").click(); });
    expect(read("search")).toBe("");
  });

  it("useFilterContext ngoài provider -> lỗi rõ ràng", () => {
    const Bad = () => { useFilterContext(); return null; };
    expect(() => render(<MemoryRouter><Bad /></MemoryRouter>)).toThrow(/FilterProvider/);
  });
});

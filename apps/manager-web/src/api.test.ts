import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  adminApi, analyticsApi, authApi, groupsApi, integrationApi, normalizeCompare, permissionsApi,
  resourcesApi,
} from "./api";
import { defaultFilter } from "./filters/filterState";
import { clearTokens, setTokens } from "./auth/tokenStore";
import { __resetHttpForTests } from "./lib/http";
import { installFetch, jsonResponse } from "./test/helpers";

beforeEach(() => { __resetHttpForTests(); clearTokens(); setTokens("AT", "RT", false); });
afterEach(() => vi.unstubAllGlobals());

const F = { ...defaultFilter(new Date(2026, 9, 5)), country: "Vietnam", owner_account_id: "u1" };

describe("analyticsApi — đường dẫn /v1/analytics/* + Filter Context", () => {
  it("mọi endpoint dữ liệu dùng /v1/analytics và cùng tham số lọc", async () => {
    const f = installFetch(() => jsonResponse({ items: [] }));
    await analyticsApi.summary(F);
    await analyticsApi.traffic(F);
    await analyticsApi.topChannels(F, 20);
    await analyticsApi.viewsByCountry(F);
    await analyticsApi.channelBreakdown(F);
    await analyticsApi.compare("platform", F);
    expect(f.calls.map((c) => c.path)).toEqual([
      "/v1/analytics/summary", "/v1/analytics/traffic", "/v1/analytics/top-channels",
      "/v1/analytics/views-by-country", "/v1/analytics/channel-breakdown", "/v1/analytics/compare/platform",
    ]);
    for (const c of f.calls) {
      expect(c.params.get("country")).toBe("Vietnam");
      expect(c.params.get("owner_account_id")).toBe("u1");
      expect(c.params.get("date_from")).toBe(F.date_from);
      expect(c.params.get("date_to")).toBe(F.date_to);
      expect(c.headers.get("Authorization")).toBe("Bearer AT");
    }
    expect(f.calls[2].params.get("limit")).toBe("20");
  });

  it("không còn gọi alias legacy /v1/viewer/* cho dữ liệu", async () => {
    const f = installFetch(() => jsonResponse({ items: [], metrics: [], users: [] }));
    await analyticsApi.countryTags(F); await analyticsApi.platformTags(F); await analyticsApi.metrics();
    await analyticsApi.freshness(); await groupsApi.list(); await adminApi.flushCache();
    await adminApi.listCountryAliases(); await adminApi.listCountryCanonicals();
    for (const c of f.calls) expect(c.path.startsWith("/v1/viewer")).toBe(false);
    expect(f.calls.map((c) => c.path)).toEqual([
      "/v1/analytics/country-tags", "/v1/analytics/platform-tags", "/v1/analytics/metrics", "/v1/analytics/freshness",
      "/v1/analytics/groups", "/v1/analytics/admin/cache/flush", "/v1/analytics/admin/country-aliases",
      "/v1/analytics/admin/country-canonicals",
    ]);
  });

  it("nhóm kênh: CRUD dưới /v1/analytics/groups", async () => {
    const f = installFetch(() => jsonResponse({ ok: true }));
    await groupsApi.create("A"); await groupsApi.rename("g 1", "B"); await groupsApi.delete("g1");
    await groupsApi.addMember("g1", { channel_ref: "page:1" }); await groupsApi.removeMember("g1", "page:1");
    expect(f.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /v1/analytics/groups", "PATCH /v1/analytics/groups/g%201", "DELETE /v1/analytics/groups/g1",
      "POST /v1/analytics/groups/g1/members", "DELETE /v1/analytics/groups/g1/members/page%3A1",
    ]);
  });

  it("normalizeCompare chấp nhận vài hình dạng bao ngoài + chuẩn hoá mục", () => {
    const a = normalizeCompare({ items: [{ key: "k", name: "N", total: 3, series: [{ date: "2026-01-01", value: 3 }] }], filter: { country: "VN" }, freshness: { stale: false } }, "platform");
    expect(a.items[0]).toEqual({ key: "k", name: "N", total: 3, series: [{ date: "2026-01-01", value: 3 }] });
    expect(a.filter).toEqual({ country: "VN" });
    const b = normalizeCompare([{ id: "x", label: "L", total: 1, days: [{ date: "d", views: null }] }], "group");
    expect(b.items[0]).toMatchObject({ key: "x", name: "L", total: 1 });
    expect(b.items[0].series).toEqual([{ date: "d", value: null }]);
    expect(normalizeCompare(null, "x").items).toEqual([]);
  });
});

describe("Identity — auth / admin / permission", () => {
  it("auth giữ /v1/auth/*, endpoint công khai KHÔNG gắn Bearer", async () => {
    const f = installFetch(() => jsonResponse({ ok: true }));
    await authApi.login("a", "b");
    await authApi.me();
    expect(f.calls[0].path).toBe("/v1/auth/login");
    expect(f.calls[0].headers.get("Authorization")).toBeNull();
    expect(f.calls[1].path).toBe("/v1/auth/me");
    expect(f.calls[1].headers.get("Authorization")).toBe("Bearer AT");
  });

  it("register không còn gửi `integration`", async () => {
    const f = installFetch(() => jsonResponse({ ok: true, email: "e", message: "m" }));
    await authApi.register("K", "user1", "password1", "a@b.c");
    expect(JSON.parse(f.calls[0].body!)).toEqual({ key: "K", username: "user1", password: "password1", email: "a@b.c" });
  });

  it("verifyEmail mã hoá token trong query", async () => {
    const f = installFetch(() => jsonResponse({ ok: true }));
    await authApi.verifyEmail("a b&c=d");
    expect(f.calls[0].params.get("token")).toBe("a b&c=d");
  });

  it("tạo invite key theo preset; sửa mã mời gọi PATCH", async () => {
    const f = installFetch(() => jsonResponse({ key: "k" }));
    await adminApi.createKey({ preset: "viewer_tool", label: "x", max_uses: 1, expires_days: 7, allowed_apps: ["reward"] });
    await adminApi.updateKey("k", { preset: "viewer", label: "y" });
    expect(f.calls[0].path).toBe("/v1/admin/keys");
    expect(f.calls[0].method).toBe("POST");
    expect(JSON.parse(f.calls[0].body!)).toEqual({
      preset: "viewer_tool", label: "x", max_uses: 1, expires_days: 7, allowed_apps: ["reward"],
    });
    expect(f.calls[1].path).toBe("/v1/admin/keys/k");
    expect(f.calls[1].method).toBe("PATCH");
    expect(JSON.parse(f.calls[1].body!)).toEqual({ preset: "viewer", label: "y" });
  });

  it("listPresets đọc GET /v1/admin/keys/presets", async () => {
    const f = installFetch(() => jsonResponse({ presets: [{ key: "viewer", label: "Người xem", role: "viewer", capabilities: [], admin_only: false }] }));
    const { presets } = await adminApi.listPresets();
    expect(f.calls[0].path).toBe("/v1/admin/keys/presets");
    expect(presets[0].key).toBe("viewer");
  });

  it("permission: GET/PUT/DELETE đúng đường dẫn", async () => {
    const f = installFetch((c) => c.path === "/v1/admin/permissions" ? jsonResponse({ permissions: [{ name: "reward:access", description: "d" }, "reward:admin"] }) : jsonResponse({ ok: true, permissions: [] }));
    const cat = await permissionsApi.catalog();
    await permissionsApi.forAccount("acc1");
    await permissionsApi.grant("acc1", "reward:access");
    await permissionsApi.revoke("acc1", "reward:admin");
    expect(cat).toEqual([{ name: "reward:access", description: "d" }, { name: "reward:admin", description: undefined }]);
    expect(f.calls.slice(1).map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /v1/accounts/acc1/permissions",
      "PUT /v1/admin/accounts/acc1/permissions/reward:access",
      "DELETE /v1/admin/accounts/acc1/permissions/reward:admin",
    ]);
  });
});

describe("Integration (mặt phẳng user) — client, credential, connection-guide", () => {
  it("đường dẫn đúng thiết kế tool-key", async () => {
    const f = installFetch(() => jsonResponse({ items: [], key_id: "k", api_key: "mpk_x", scopes: [] }));
    await integrationApi.listClients("u1");
    await integrationApi.getClient("cli_1");
    await integrationApi.createSharedClient({ tool_name: "T", client_kind: "fallback_tool", purpose: "p" });
    await integrationApi.configureClient("cli_1", { allowed_platforms: ["facebook"], allowed_scopes: ["resources:write"] });
    await integrationApi.activateClient("cli_1", "T");
    await integrationApi.transferClient("cli_1", "acc2");
    await integrationApi.issueCredential("cli_1");
    await integrationApi.rotateCredential("cli_1", "key1", 60);
    await integrationApi.revokeCredential("cli_1", "key1");
    await integrationApi.patchClient("cli_1", { status: "suspended" });
    await integrationApi.connectionGuide();
    await integrationApi.myOpenapi();
    await integrationApi.revealCredential();
    expect(f.calls.map((c) => `${c.method} ${c.path}${c.params.toString() ? "?" + c.params : ""}`)).toEqual([
      "GET /v1/integrations/clients?owner=u1",
      "GET /v1/integrations/clients/cli_1",
      "POST /v1/integrations/clients",
      "PUT /v1/integrations/clients/cli_1/config",
      "POST /v1/integrations/clients/cli_1/activate",
      "POST /v1/integrations/clients/cli_1/transfer",
      "POST /v1/integrations/clients/cli_1/credentials",
      "POST /v1/integrations/clients/cli_1/credentials/key1/rotate",
      "DELETE /v1/integrations/clients/cli_1/credentials/key1",
      "PATCH /v1/integrations/clients/cli_1",
      "GET /v1/integrations/me/connection-guide",
      "GET /v1/integrations/me/openapi.json",
      "POST /v1/integrations/me/credential/reveal",
    ]);
    expect(JSON.parse(f.calls[7].body!)).toEqual({ grace_seconds: 60 });
    // mặt phẳng user: luôn Bearer token user, không bao giờ API key máy
    for (const c of f.calls) expect(c.headers.get("Authorization")).toBe("Bearer AT");
  });
});

describe("Resources (legacy-compat)", () => {
  it("giữ nguyên đường dẫn /v1/resources/*", async () => {
    const f = installFetch(() => jsonResponse({ items: [] }));
    await resourcesApi.listPlatforms(); await resourcesApi.listSources(); await resourcesApi.listResources({ q: "x", enabled: true });
    expect(f.calls.map((c) => c.path)).toEqual(["/v1/resources/platforms", "/v1/resources/sources", "/v1/resources"]);
    expect(f.calls[2].params.get("q")).toBe("x");
  });
});

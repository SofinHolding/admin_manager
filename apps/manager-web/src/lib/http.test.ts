import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetHttpForTests, apiFetch, onSessionExpired, setRefreshStrategy } from "./http";
import { ApiError, isAbortError } from "./errors";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "../auth/tokenStore";
import { deferred, installFetch, jsonResponse } from "../test/helpers";

beforeEach(() => { __resetHttpForTests(); clearTokens(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("apiFetch — Bearer, refresh 401, thu hồi phiên", () => {
  it("gắn Authorization từ access token trong bộ nhớ; endpoint public thì KHÔNG gắn", async () => {
    setTokens("AT-1", "RT-1", false);
    const f = installFetch(() => jsonResponse({ ok: 1 }));
    await apiFetch("/v1/x");
    await apiFetch("/v1/auth/login", { method: "POST", body: "{}" }, { public: true });
    expect(f.calls[0].headers.get("Authorization")).toBe("Bearer AT-1");
    expect(f.calls[1].headers.get("Authorization")).toBeNull();
  });

  it("401 -> refresh MỘT lần -> thử lại với token mới; refresh xoay vòng được lưu", async () => {
    setTokens("AT-old", "RT-old", true);
    let n = 0;
    const f = installFetch(
      (c) => c.path === "/v1/auth/refresh" ? jsonResponse({ access_token: "AT-new", refresh_token: "RT-new", expires_in: 900 }) : undefined,
      (c) => c.path === "/v1/data"
        ? (++n === 1 ? jsonResponse({ detail: "Token hết hạn" }, { status: 401 }) : jsonResponse({ ok: true, v: 1 }))
        : undefined,
    );
    const r = await apiFetch<{ v: number }>("/v1/data");
    expect(r.v).toBe(1);
    expect(f.byPath("/v1/auth/refresh")).toHaveLength(1);
    expect(JSON.parse(f.byPath("/v1/auth/refresh")[0].body!)).toEqual({ refresh_token: "RT-old" });
    expect(f.byPath("/v1/data")[1].headers.get("Authorization")).toBe("Bearer AT-new");
    expect(getAccessToken()).toBe("AT-new");
    expect(getRefreshToken()).toBe("RT-new");
    expect(sessionStorage.getItem("mp_refresh_token")).toBe("RT-new");
  });

  it("nhiều request 401 đồng thời chỉ gọi refresh MỘT lần", async () => {
    setTokens("AT-old", "RT-old", false);
    const gate = deferred<Response>();
    const seen = new Set<string>();
    const f = installFetch(
      (c) => c.path === "/v1/auth/refresh" ? gate.promise : undefined,
      (c) => {
        const bearer = c.headers.get("Authorization");
        if (bearer === "Bearer AT-old") return jsonResponse({ detail: "hết hạn" }, { status: 401 });
        seen.add(c.path);
        return jsonResponse({ ok: true });
      },
    );
    const p = Promise.all([apiFetch("/v1/a"), apiFetch("/v1/b"), apiFetch("/v1/c")]);
    await vi.waitFor(() => expect(f.byPath("/v1/auth/refresh")).toHaveLength(1));
    gate.resolve(jsonResponse({ access_token: "AT-new", expires_in: 900 }));
    await p;
    expect(f.byPath("/v1/auth/refresh")).toHaveLength(1);
    expect([...seen].sort()).toEqual(["/v1/a", "/v1/b", "/v1/c"]);
  });

  it("401 'Phiên đã bị thu hồi' -> KHÔNG refresh, xoá sạch token, báo reason=revoked", async () => {
    setTokens("AT", "RT", true);
    const cb = vi.fn();
    onSessionExpired(cb);
    const f = installFetch(() => jsonResponse({ detail: "Phiên đã bị thu hồi" }, { status: 401 }));
    await expect(apiFetch("/v1/data")).rejects.toMatchObject({ code: "AUTH_REVOKED", status: 401 });
    expect(f.byPath("/v1/auth/refresh")).toHaveLength(0);
    expect(cb).toHaveBeenCalledWith("revoked");
    expect(getAccessToken()).toBe("");
    expect(getRefreshToken()).toBe("");
    expect(sessionStorage.getItem("mp_refresh_token")).toBeNull();
  });

  it("envelope mới AUTH_REVOKED cũng được nhận ra", async () => {
    setTokens("AT", "RT", false);
    const cb = vi.fn();
    onSessionExpired(cb);
    installFetch(() => jsonResponse({ ok: false, error: { code: "AUTH_REVOKED", message: "revoked" } }, { status: 401 }));
    await expect(apiFetch("/v1/data")).rejects.toMatchObject({ code: "AUTH_REVOKED" });
    expect(cb).toHaveBeenCalledWith("revoked");
  });

  it("refresh bị từ chối (401) -> phiên hết hạn, token xoá sạch", async () => {
    setTokens("AT", "RT", true);
    const cb = vi.fn();
    onSessionExpired(cb);
    installFetch(
      (c) => c.path === "/v1/auth/refresh" ? jsonResponse({ detail: "Refresh token không hợp lệ" }, { status: 401 }) : undefined,
      () => jsonResponse({ detail: "hết hạn" }, { status: 401 }),
    );
    await expect(apiFetch("/v1/data")).rejects.toMatchObject({ code: "AUTH_INVALID" });
    expect(cb).toHaveBeenCalledWith("expired");
    expect(getRefreshToken()).toBe("");
  });

  it("mất mạng khi refresh KHÔNG bị coi là hết phiên (token còn nguyên)", async () => {
    setTokens("AT", "RT", true);
    const cb = vi.fn();
    onSessionExpired(cb);
    installFetch(
      (c) => { if (c.path === "/v1/auth/refresh") throw new TypeError("Failed to fetch"); },
      () => jsonResponse({ detail: "hết hạn" }, { status: 401 }),
    );
    await expect(apiFetch("/v1/data")).rejects.toMatchObject({ code: "UNAVAILABLE" });
    expect(cb).not.toHaveBeenCalled();
    expect(getRefreshToken()).toBe("RT");
  });

  it("chiến lược refresh thay thế (OIDC) được dùng và token xoay vòng được lưu", async () => {
    setTokens("AT-old", "RT-old", false);
    const strat = vi.fn(async () => ({ access_token: "AT-oidc", refresh_token: "RT-oidc" }));
    setRefreshStrategy(strat);
    let first = true;
    installFetch(() => first ? ((first = false), jsonResponse({ detail: "x" }, { status: 401 })) : jsonResponse({ ok: true }));
    await apiFetch("/v1/data");
    expect(strat).toHaveBeenCalledWith("RT-old");
    expect(getAccessToken()).toBe("AT-oidc");
    expect(getRefreshToken()).toBe("RT-oidc");
  });
});

describe("apiFetch — lỗi, envelope, huỷ, timeout", () => {
  it("lỗi legacy {detail} và envelope mới đều thành ApiError", async () => {
    installFetch((c) => c.path === "/v1/legacy"
      ? jsonResponse({ detail: "Không tìm thấy" }, { status: 404 })
      : jsonResponse({ ok: false, error: { code: "PLATFORM_NOT_ALLOWED", message: "Nền tảng không được phép" } }, { status: 403 }));
    await expect(apiFetch("/v1/legacy")).rejects.toMatchObject({ status: 404, message: "Không tìm thấy" });
    await expect(apiFetch("/v1/new")).rejects.toMatchObject({ status: 403, code: "PLATFORM_NOT_ALLOWED", message: "Nền tảng không được phép" });
  });

  it("bóc envelope thành công {ok:true,data}; thân legacy giữ nguyên", async () => {
    installFetch((c) => c.path === "/v1/env"
      ? jsonResponse({ ok: true, data: { a: 1 }, meta: { request_id: "r" } })
      : jsonResponse({ ok: true, removed: 3 }));
    expect(await apiFetch("/v1/env")).toEqual({ a: 1 });
    expect(await apiFetch("/v1/legacy")).toEqual({ ok: true, removed: 3 });
  });

  it("204 -> undefined", async () => {
    installFetch(() => new Response(null, { status: 204 }));
    expect(await apiFetch("/v1/x")).toBeUndefined();
  });

  it("mạng hỏng -> ApiError NETWORK", async () => {
    installFetch(() => { throw new TypeError("Failed to fetch"); });
    await expect(apiFetch("/v1/x")).rejects.toMatchObject({ code: "NETWORK" });
  });

  it("caller huỷ (AbortController) -> AbortError, KHÔNG phải ApiError", async () => {
    const f = installFetch(() => new Promise<Response>(() => {}));
    const ctl = new AbortController();
    const p = apiFetch("/v1/slow", undefined, { signal: ctl.signal });
    await vi.waitFor(() => expect(f.calls).toHaveLength(1));
    ctl.abort();
    const err = await p.catch((e) => e);
    expect(isAbortError(err)).toBe(true);
    expect(err).not.toBeInstanceOf(ApiError);
  });

  it("quá thời gian -> ApiError TIMEOUT (và huỷ request đang bay)", async () => {
    vi.useFakeTimers();
    const f = installFetch(() => new Promise<Response>(() => {}));
    const p = apiFetch("/v1/slow", undefined, { timeoutMs: 1000 }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(1100);
    const err = await p;
    expect(err).toMatchObject({ code: "TIMEOUT" });
    expect(f.calls[0].signal?.aborted).toBe(true);
  });
});

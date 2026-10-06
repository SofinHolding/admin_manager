import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  base64UrlEncode, beginLogin, buildAuthorizeUrl, codeChallengeS256, completeLogin, generateCodeVerifier,
  generateNonce, generateState, getAuthMode, getOidcConfig, oidcLogout, oidcRefresh, OidcError, safeEqual, safeReturnTo,
  type OidcConfig,
} from "./oidc";
import { installFetch, jsonResponse, fakeJwt } from "../test/helpers";

const cfg: OidcConfig = getOidcConfig({ BASE_URL: "/admin/" }, "http://127.0.0.1:5174");

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("PKCE helper (RFC 7636)", () => {
  it("code_verifier: độ dài 43..128, chỉ ký tự unreserved [A-Za-z0-9-._~], ngẫu nhiên", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 50; i++) {
      const v = generateCodeVerifier();
      expect(v).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/);
      expect(v).toHaveLength(64);
      seen.add(v);
    }
    expect(seen.size).toBe(50);
    expect(generateCodeVerifier(43)).toHaveLength(43);
    expect(generateCodeVerifier(128)).toHaveLength(128);
    expect(() => generateCodeVerifier(42)).toThrow(RangeError);
    expect(() => generateCodeVerifier(129)).toThrow(RangeError);
  });

  it("code_challenge S256 khớp vector chuẩn RFC 7636 Appendix B", async () => {
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    expect(await codeChallengeS256(verifier)).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("challenge là base64url không padding, 43 ký tự cho SHA-256", async () => {
    const c = await codeChallengeS256(generateCodeVerifier());
    expect(c).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("state / nonce: 256 bit base64url, mỗi lần khác nhau", () => {
    const a = generateState(), b = generateState(), n = generateNonce();
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(n).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
    expect(a).not.toBe(n);
  });

  it("base64UrlEncode + safeEqual", () => {
    expect(base64UrlEncode(new Uint8Array([251, 255, 254]))).toBe("-__-");
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });
});

describe("cấu hình", () => {
  it("mặc định password; oidc chỉ khi VITE_AUTH_MODE=oidc", () => {
    expect(getAuthMode({})).toBe("password");
    expect(getAuthMode({ VITE_AUTH_MODE: "password" })).toBe("password");
    expect(getAuthMode({ VITE_AUTH_MODE: "oidc" })).toBe("oidc");
    expect(getAuthMode({ VITE_AUTH_MODE: "hack" })).toBe("password");
  });
  it("redirect URI mặc định khớp client đã đăng ký; client public manager-web", () => {
    expect(cfg.redirectUri).toBe("http://127.0.0.1:5174/admin/callback");
    expect(cfg.clientId).toBe("manager-web");
    expect(cfg.authorizeUrl).toBe("/oauth/authorize");
    expect(cfg.tokenUrl).toBe("/oauth/token");
    expect(cfg.logoutUrl).toBe("/oauth/logout");
  });
});

describe("beginLogin — chuyển hướng tới /oauth/authorize", () => {
  it("URL có response_type=code, S256, state, nonce, redirect_uri, client_id; verifier KHÔNG nằm trong URL", async () => {
    let target = "";
    const url = await beginLogin(cfg, "/groups", (u) => { target = u; });
    expect(target).toBe(url);
    const u = new URL(url, "http://127.0.0.1:5174");
    expect(u.pathname).toBe("/oauth/authorize");
    expect(u.searchParams.get("response_type")).toBe("code");
    expect(u.searchParams.get("client_id")).toBe("manager-web");
    expect(u.searchParams.get("redirect_uri")).toBe("http://127.0.0.1:5174/admin/callback");
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(u.searchParams.get("nonce")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const tx = JSON.parse(sessionStorage.getItem("mp_oidc_tx")!);
    expect(url).not.toContain(tx.verifier);
    expect(u.searchParams.get("code_challenge")).toBe(await codeChallengeS256(tx.verifier));
    expect(tx.state).toBe(u.searchParams.get("state"));
    expect(tx.nonce).toBe(u.searchParams.get("nonce"));
    expect(tx.returnTo).toBe("/groups");
  });

  it("buildAuthorizeUrl không có client_secret", () => {
    const url = buildAuthorizeUrl(cfg, { challenge: "c", state: "s", nonce: "n" });
    expect(url).not.toMatch(/secret/i);
  });
});

describe("completeLogin — callback", () => {
  async function start() {
    let target = "";
    await beginLogin(cfg, "/dashboard", (u) => { target = u; });
    const q = new URL(target, "http://x").searchParams;
    return { state: q.get("state")!, nonce: q.get("nonce")!, tx: JSON.parse(sessionStorage.getItem("mp_oidc_tx")!) };
  }

  it("state đúng -> đổi code + code_verifier ở /oauth/token (form-urlencoded), trả token + returnTo; tx bị xoá (một lần)", async () => {
    const { state, nonce, tx } = await start();
    const f = installFetch((c) => c.path === "/oauth/token"
      ? jsonResponse({
          access_token: "AT", refresh_token: "RT", expires_in: 900, token_type: "Bearer",
          id_token: fakeJwt({ aud: "manager-web", nonce, iss: "http://127.0.0.1:8430" }),
        })
      : undefined);
    const r = await completeLogin(cfg, `?code=AUTHCODE&state=${state}`);
    expect(r.tokens.access_token).toBe("AT");
    expect(r.returnTo).toBe("/dashboard");
    const call = f.byPath("/oauth/token")[0];
    expect(call.method).toBe("POST");
    expect(call.headers.get("Content-Type")).toBe("application/x-www-form-urlencoded");
    const form = new URLSearchParams(call.body!);
    expect(form.get("grant_type")).toBe("authorization_code");
    expect(form.get("code")).toBe("AUTHCODE");
    expect(form.get("code_verifier")).toBe(tx.verifier);
    expect(form.get("client_id")).toBe("manager-web");
    expect(form.get("redirect_uri")).toBe(cfg.redirectUri);
    expect(form.has("client_secret")).toBe(false);
    expect(call.headers.get("Authorization")).toBeNull();
    expect(sessionStorage.getItem("mp_oidc_tx")).toBeNull();
  });

  it("TỪ CHỐI callback sai state: không gọi /oauth/token và không nhận token", async () => {
    await start();
    const f = installFetch(() => jsonResponse({ access_token: "AT" }));
    await expect(completeLogin(cfg, "?code=AUTHCODE&state=ATTACKER-STATE")).rejects.toMatchObject({ code: "state_mismatch" });
    expect(f.calls).toHaveLength(0);
    expect(sessionStorage.getItem("mp_oidc_tx")).toBeNull(); // giao dịch bị huỷ, không thử lại được
  });

  it("thiếu state -> từ chối", async () => {
    await start();
    const f = installFetch(() => jsonResponse({ access_token: "AT" }));
    await expect(completeLogin(cfg, "?code=AUTHCODE")).rejects.toMatchObject({ code: "state_mismatch" });
    expect(f.calls).toHaveLength(0);
  });

  it("không có giao dịch đang chờ (callback giả/ phát lại) -> từ chối", async () => {
    const f = installFetch(() => jsonResponse({ access_token: "AT" }));
    await expect(completeLogin(cfg, "?code=X&state=Y")).rejects.toMatchObject({ code: "missing_tx" });
    expect(f.calls).toHaveLength(0);
  });

  it("giao dịch chỉ dùng được MỘT lần (replay callback bị từ chối)", async () => {
    const { state } = await start();
    installFetch(() => jsonResponse({ access_token: "AT", refresh_token: "RT" }));
    await completeLogin(cfg, `?code=C&state=${state}`);
    await expect(completeLogin(cfg, `?code=C&state=${state}`)).rejects.toMatchObject({ code: "missing_tx" });
  });

  it("giao dịch quá 10 phút -> hết hạn", async () => {
    const { state, tx } = await start();
    sessionStorage.setItem("mp_oidc_tx", JSON.stringify({ ...tx, createdAt: Date.now() - 11 * 60_000 }));
    installFetch(() => jsonResponse({ access_token: "AT" }));
    await expect(completeLogin(cfg, `?code=C&state=${state}`)).rejects.toMatchObject({ code: "expired_tx" });
  });

  it("provider trả error -> OidcError, không đổi token", async () => {
    await start();
    const f = installFetch(() => jsonResponse({}));
    await expect(completeLogin(cfg, "?error=access_denied&error_description=Nguoi+dung+tu+choi")).rejects.toMatchObject({ code: "provider_error" });
    expect(f.calls).toHaveLength(0);
  });

  it("thiếu code -> missing_code", async () => {
    const { state } = await start();
    installFetch(() => jsonResponse({}));
    await expect(completeLogin(cfg, `?state=${state}`)).rejects.toMatchObject({ code: "missing_code" });
  });

  it("id_token sai nonce -> từ chối", async () => {
    const { state } = await start();
    installFetch(() => jsonResponse({ access_token: "AT", id_token: fakeJwt({ aud: "manager-web", nonce: "NOT-MINE" }) }));
    await expect(completeLogin(cfg, `?code=C&state=${state}`)).rejects.toMatchObject({ code: "nonce_mismatch" });
  });

  it("id_token sai audience -> từ chối", async () => {
    const { state, nonce } = await start();
    installFetch(() => jsonResponse({ access_token: "AT", id_token: fakeJwt({ aud: "other-app", nonce }) }));
    await expect(completeLogin(cfg, `?code=C&state=${state}`)).rejects.toMatchObject({ code: "invalid_id_token" });
  });

  it("token endpoint lỗi 400 -> OidcError token_exchange_failed (không lộ mã)", async () => {
    const { state } = await start();
    installFetch(() => jsonResponse({ error: "invalid_grant", error_description: "Mã đã dùng" }, { status: 400 }));
    const e = await completeLogin(cfg, `?code=C&state=${state}`).catch((x) => x);
    expect(e).toBeInstanceOf(OidcError);
    expect(e.code).toBe("token_exchange_failed");
    expect(e.message).toBe("Mã đã dùng");
  });
});

describe("refresh xoay vòng + logout", () => {
  it("oidcRefresh gửi grant_type=refresh_token + client_id, không secret", async () => {
    const f = installFetch(() => jsonResponse({ access_token: "AT2", refresh_token: "RT2" }));
    const t = await oidcRefresh(cfg, "RT1");
    expect(t.refresh_token).toBe("RT2");
    const form = new URLSearchParams(f.calls[0].body!);
    expect(form.get("grant_type")).toBe("refresh_token");
    expect(form.get("refresh_token")).toBe("RT1");
    expect(form.get("client_id")).toBe("manager-web");
    expect(form.has("client_secret")).toBe(false);
  });

  it("oidcLogout gọi /oauth/logout, lỗi mạng vẫn không ném", async () => {
    const f = installFetch((c) => c.path === "/oauth/logout" ? new Response(null, { status: 204 }) : undefined);
    await oidcLogout(cfg, "RT1");
    expect(f.byPath("/oauth/logout")).toHaveLength(1);
    installFetch(() => { throw new TypeError("offline"); });
    await expect(oidcLogout(cfg, "RT1")).resolves.toBeUndefined();
  });
});

describe("safeReturnTo — chặn open redirect", () => {
  it("chỉ nhận đường dẫn tương đối cùng origin", () => {
    expect(safeReturnTo("/groups")).toBe("/groups");
    for (const bad of ["https://evil.com", "//evil.com", "javascript:alert(1)", "/\\evil.com", "", null, undefined, "/a\r\nb"]) {
      expect(safeReturnTo(bad as string)).toBe("/dashboard");
    }
  });
});

describe("completeLogin — callback giả có ?error= không đốt giao dịch đang chờ", () => {
  async function begin() {
    let target = "";
    await beginLogin(cfg, "/dashboard", (u) => { target = u; });
    return new URL(target, "http://x").searchParams.get("state")!;
  }

  it("?error= không kèm state (callback giả) -> báo provider_error nhưng GIỮ giao dịch; callback thật sau đó vẫn thành công", async () => {
    const state = await begin();
    installFetch(() => jsonResponse({ access_token: "AT", refresh_token: "RT" }));
    await expect(completeLogin(cfg, "?error=access_denied")).rejects.toMatchObject({ code: "provider_error" });
    await expect(completeLogin(cfg, "?error=access_denied&state=SAI")).rejects.toMatchObject({ code: "provider_error" });
    expect(sessionStorage.getItem("mp_oidc_tx")).not.toBeNull();
    const ok = await completeLogin(cfg, `?code=C&state=${state}`);
    expect(ok.tokens.access_token).toBe("AT");
  });

  it("?error= với state KHỚP (lỗi thật của luồng này) -> tiêu giao dịch", async () => {
    const state = await begin();
    await expect(completeLogin(cfg, `?error=access_denied&state=${state}`)).rejects.toMatchObject({ code: "provider_error" });
    expect(sessionStorage.getItem("mp_oidc_tx")).toBeNull();
  });
});

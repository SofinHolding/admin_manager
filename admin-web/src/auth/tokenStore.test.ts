import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearTokens, decodeJwtClaims, getAccessToken, getRefreshToken, LEGACY_REFRESH_KEY, loadPersistedRefreshToken,
  purgeLegacyStorage, setTokens,
} from "./tokenStore";
import { fakeJwt } from "../test/helpers";

beforeEach(() => clearTokens());

function allStorageText(): string {
  const parts: string[] = [];
  for (const st of [localStorage, sessionStorage]) {
    for (let i = 0; i < st.length; i++) { const k = st.key(i)!; parts.push(k, st.getItem(k) ?? ""); }
  }
  return parts.join("\n");
}

describe("kho token — bảo mật lưu trữ", () => {
  it("access token CHỈ ở bộ nhớ: không có trong localStorage lẫn sessionStorage", () => {
    setTokens("ACCESS-SECRET-VALUE", "REFRESH-SECRET-VALUE", true);
    expect(getAccessToken()).toBe("ACCESS-SECRET-VALUE");
    expect(allStorageText()).not.toContain("ACCESS-SECRET-VALUE");
  });

  it("refresh token KHÔNG BAO GIỜ vào localStorage (chỉ sessionStorage khi 'ghi nhớ')", () => {
    setTokens("a", "REFRESH-SECRET-VALUE", true);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.getItem("mp_refresh_token")).toBe("REFRESH-SECRET-VALUE");
    expect(loadPersistedRefreshToken()).toBe("REFRESH-SECRET-VALUE");
  });

  it("không 'ghi nhớ' -> refresh token chỉ ở bộ nhớ, không ở storage nào", () => {
    setTokens("a", "REFRESH-SECRET-VALUE", false);
    expect(getRefreshToken()).toBe("REFRESH-SECRET-VALUE");
    expect(allStorageText()).not.toContain("REFRESH-SECRET-VALUE");
    expect(loadPersistedRefreshToken()).toBe("");
  });

  it("clearTokens xoá sạch bộ nhớ + storage + khoá legacy", () => {
    setTokens("a", "r", true);
    localStorage.setItem(LEGACY_REFRESH_KEY, "OLD-LONG-LIVED");
    clearTokens();
    expect(getAccessToken()).toBe("");
    expect(getRefreshToken()).toBe("");
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.getItem(LEGACY_REFRESH_KEY)).toBeNull();
  });

  it("purgeLegacyStorage xoá refresh token dài hạn của phiên bản cũ", () => {
    localStorage.setItem(LEGACY_REFRESH_KEY, "OLD-LONG-LIVED");
    sessionStorage.setItem(LEGACY_REFRESH_KEY, "OLD-SESSION");
    purgeLegacyStorage();
    expect(localStorage.getItem(LEGACY_REFRESH_KEY)).toBeNull();
    expect(sessionStorage.getItem(LEGACY_REFRESH_KEY)).toBeNull();
  });

  it("nạp module tự dọn khoá legacy trong localStorage", async () => {
    localStorage.setItem(LEGACY_REFRESH_KEY, "OLD-LONG-LIVED");
    vi.resetModules();
    await import("./tokenStore");
    expect(localStorage.getItem(LEGACY_REFRESH_KEY)).toBeNull();
  });

  it("decodeJwtClaims đọc claim perms (chỉ để hiển thị) và chịu được rác", () => {
    const c = decodeJwtClaims(fakeJwt({ sub: "u1", role: "viewer", perms: ["reward:access"] }));
    expect(c?.perms).toEqual(["reward:access"]);
    expect(decodeJwtClaims("not-a-jwt")).toBeNull();
    expect(decodeJwtClaims("a.b.c")).toBeNull();
  });
});

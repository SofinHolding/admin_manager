import { describe, expect, it } from "vitest";
import { ApiError, errorFromResponse, isAbortError, isUnavailableError } from "./errors";

describe("errorFromResponse — hiểu CẢ envelope mới lẫn legacy {detail}", () => {
  it("envelope mới: lấy code/message/details/request_id", () => {
    const e = errorFromResponse(403, {
      ok: false,
      error: { code: "SCOPE_DENIED", message: "Thiếu scope", details: { need: "metrics:write" } },
      meta: { request_id: "req_123" },
    });
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(403);
    expect(e.code).toBe("SCOPE_DENIED");
    expect(e.message).toBe("Thiếu scope");
    expect(e.details).toEqual({ need: "metrics:write" });
    expect(e.requestId).toBe("req_123");
  });

  it("legacy: {detail: 'chuỗi'}", () => {
    const e = errorFromResponse(400, { detail: "Mã mời không hợp lệ" });
    expect(e.message).toBe("Mã mời không hợp lệ");
    expect(e.code).toBe("");
  });

  it("legacy FastAPI validation: chỉ lấy msg, KHÔNG phản chiếu input", () => {
    const e = errorFromResponse(422, {
      detail: [{ loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error", input: "SECRET-VALUE-123" }],
    });
    expect(e.message).toBe("value is not a valid email address");
    expect(e.message).not.toContain("SECRET-VALUE-123");
  });

  it("legacy: {message}", () => {
    expect(errorFromResponse(500, { message: "boom" }).message).toBe("boom");
  });

  it("401 'Phiên đã bị thu hồi' (legacy) -> AUTH_REVOKED; envelope AUTH_REVOKED giữ nguyên", () => {
    expect(errorFromResponse(401, { detail: "Phiên đã bị thu hồi" }).code).toBe("AUTH_REVOKED");
    expect(errorFromResponse(401, { ok: false, error: { code: "AUTH_REVOKED", message: "x" } }).code).toBe("AUTH_REVOKED");
    expect(errorFromResponse(401, { detail: "Token không hợp lệ" }).code).toBe("AUTH_INVALID");
  });

  it("thân không phải JSON: dùng thông điệp mặc định theo status", () => {
    expect(errorFromResponse(403, null).message).toBe("Không có quyền");
    expect(errorFromResponse(503, null).code).toBe("UNAVAILABLE");
    expect(errorFromResponse(500, null).message).toBe("Lỗi 500");
  });
});

describe("phân loại lỗi", () => {
  it("isUnavailableError: 503/502/504/NETWORK/TIMEOUT", () => {
    expect(isUnavailableError(new ApiError("x", { status: 503 }))).toBe(true);
    expect(isUnavailableError(new ApiError("x", { status: 502 }))).toBe(true);
    expect(isUnavailableError(new ApiError("x", { code: "NETWORK" }))).toBe(true);
    expect(isUnavailableError(new ApiError("x", { code: "TIMEOUT" }))).toBe(true);
    expect(isUnavailableError(new ApiError("x", { status: 400 }))).toBe(false);
    expect(isUnavailableError(new Error("x"))).toBe(false);
  });
  it("isAbortError", () => {
    expect(isAbortError(new DOMException("a", "AbortError"))).toBe(true);
    expect(isAbortError(new Error("a"))).toBe(false);
  });
});

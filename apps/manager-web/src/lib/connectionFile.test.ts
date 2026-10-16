import { describe, expect, it } from "vitest";
import { buildConnectionFile, connectionFileCommand, connectionFileName } from "./connectionFile";

describe("buildConnectionFile", () => {
  it("dựng đúng hình dạng file kết nối từ dữ liệu cấp khóa", () => {
    const file = buildConnectionFile({
      gateway: "https://manager.example.com",
      toolName: "Fallback Collector",
      clientId: "client-1",
      keyId: "key-1",
      apiKey: "mpk_secret-value",
      allowedPlatforms: ["youtube", "tiktok"],
      contractVersion: "v1",
      issuedAt: "2026-10-15T00:00:00.000Z",
    });

    expect(file).toEqual({
      format: "manager-connection/1",
      gateway: "https://manager.example.com",
      tool_name: "Fallback Collector",
      client_id: "client-1",
      key_id: "key-1",
      api_key: "mpk_secret-value",
      allowed_platforms: ["youtube", "tiktok"],
      contract_version: "v1",
      issued_at: "2026-10-15T00:00:00.000Z",
    });
  });

  it("không truyền issuedAt -> tự lấy thời điểm hiện tại hợp lệ (ISO)", () => {
    const file = buildConnectionFile({
      gateway: "http://localhost", toolName: "T", clientId: "c1", keyId: "k1",
      apiKey: "x", allowedPlatforms: [], contractVersion: "v1",
    });
    expect(() => new Date(file.issued_at).toISOString()).not.toThrow();
    expect(new Date(file.issued_at).toISOString()).toBe(file.issued_at);
  });
});

describe("connectionFileName / connectionFileCommand", () => {
  it("tên file theo mẫu connection-<client_id>.json", () => {
    expect(connectionFileName("client-1")).toBe("connection-client-1.json");
  });

  it("lệnh chạy Tool trỏ đúng file kết nối vừa tải", () => {
    expect(connectionFileCommand("client-1")).toBe("python collect.py --connection-file connection-client-1.json");
  });
});

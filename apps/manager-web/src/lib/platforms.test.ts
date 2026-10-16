import { describe, expect, it } from "vitest";
import { platformEmoji, platformLabel } from "./platforms";

describe("platformLabel", () => {
  it("ưu tiên tên hiển thị từ Platform Registry", () => {
    expect(platformLabel("kwai", { kwai: "Kwai" })).toBe("Kwai");
  });

  it("nền tảng quen thuộc có nhãn dự phòng khi chưa có registry", () => {
    expect(platformLabel("youtube")).toBe("YouTube");
    expect(platformLabel("facebook")).toBe("Facebook");
    expect(platformLabel("tiktok")).toBe("TikTok");
  });

  it("nền tảng lạ, chưa có registry -> trả về chính khóa", () => {
    expect(platformLabel("kwai")).toBe("kwai");
  });

  it("khóa rỗng -> trả về rỗng", () => {
    expect(platformLabel("")).toBe("");
  });
});

describe("platformEmoji", () => {
  it("nền tảng quen thuộc có emoji riêng; nền tảng lạ dùng emoji liên kết chung", () => {
    expect(platformEmoji("youtube")).toBe("📹");
    expect(platformEmoji("kwai")).toBe("🔗");
  });
});

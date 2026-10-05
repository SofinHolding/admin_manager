import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "./safeUrl";

describe("safeHttpUrl (chống XSS qua href)", () => {
  it("cho phép http/https", () => {
    expect(safeHttpUrl("https://www.facebook.com/profile.php?id=1")).toBe("https://www.facebook.com/profile.php?id=1");
    expect(safeHttpUrl("http://example.com/")).toBe("http://example.com/");
  });
  it("chặn javascript:, data:, vbscript:, chuỗi rỗng, URL hỏng", () => {
    for (const bad of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "", "not a url", "//evil.com"]) {
      expect(safeHttpUrl(bad)).toBeNull();
    }
    expect(safeHttpUrl(null)).toBeNull();
  });
});

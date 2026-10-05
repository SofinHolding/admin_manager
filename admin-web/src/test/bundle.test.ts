import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { resolve, sep } from "node:path";
import { RULES, scanDir, scanText } from "../../scripts/secretScan.mjs";

const root = resolve(__dirname, "../..");

describe("không có secret trong mã nguồn / bundle", () => {
  it("công cụ quét BẮT được các mẫu bí mật (test của test)", () => {
    expect(scanText("const k='mpk_abcdefghij12.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'")).toContain("machine-api-key");
    expect(scanText("-----BEGIN RSA " + "PRIVATE KEY-----")).toContain("private-key-block");
    expect(scanText("JWT_SECRET = 'super-secret-value-123'")).toContain("secret-assignment");
    expect(scanText('{"CLIENT_SECRET":"abcdefghijkl"}')).toContain("secret-assignment");
    expect(scanText("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1MSIsInJvbGUiOiJhIn0.c2lnbmF0dXJlLXZhbHVl")).toContain("jwt-literal");
    expect(scanText("headers['X-Service-Key']=x")).toContain("service-key-header");
    expect(scanText("const a = 1; // bình thường")).toEqual([]);
    expect(RULES.length).toBeGreaterThanOrEqual(6);
  });

  it("src/ (trừ file test) sạch bí mật", () => {
    const hits = scanDir(resolve(root, "src"), { skip: (f) => /\.test\.(ts|tsx)$/.test(f) || f.includes(`${sep}test${sep}`) });
    expect(hits).toEqual([]);
  });

  it("index.html và .env.example sạch bí mật; .env.example không chứa giá trị secret", () => {
    for (const f of ["index.html", ".env.example"]) {
      expect(scanText(readFileSync(resolve(root, f), "utf8"))).toEqual([]);
    }
    const env = readFileSync(resolve(root, ".env.example"), "utf8");
    const assigned = env.split(/\r?\n/).filter((l) => /^[A-Z_]+=/.test(l));
    for (const line of assigned) {
      const [k, v] = line.split("=");
      expect(k).toMatch(/^(VITE_|DEV_GATEWAY_URL)/);
      expect(v).not.toMatch(/mpk_|eyJ|[A-Za-z0-9+/_=-]{32,}/); // không có khoá/JWT/chuỗi ngẫu nhiên dài
    }
    // client public: không có biến secret/client_secret
    expect(env).not.toMatch(/^VITE_[A-Z_]*(SECRET|PASSWORD|API_KEY|TOKEN)[A-Z_]*=/m);
  });

  it("dist/ (nếu đã build) không chứa bí mật nào", () => {
    const dist = resolve(root, "dist");
    if (!existsSync(dist)) return; // `npm run build` chạy scripts/scan-bundle.mjs ngay sau build
    expect(scanDir(dist)).toEqual([]);
  });

  it("mã nguồn không dùng dangerouslySetInnerHTML / innerHTML / eval với dữ liệu động", () => {
    const hits = scanSource(/dangerouslySetInnerHTML|\.innerHTML\s*=|\beval\(|new Function\(/);
    expect(hits).toEqual([]);
  });

  it("localStorage không bao giờ được dùng để lưu token/khoá trong mã nguồn (ngoại trừ xoá khoá legacy)", () => {
    const hits = scanSource(/localStorage\.(setItem|getItem)/);
    expect(hits).toEqual([]);
  });
});

function scanSource(re: RegExp): string[] {
  const out: string[] = [];
  const stack = [resolve(root, "src")];
  while (stack.length) {
    const d = stack.pop()!;
    for (const n of readdirSync(d)) {
      const p = resolve(d, n);
      if (statSync(p).isDirectory()) { stack.push(p); continue; }
      if (!/\.(ts|tsx)$/.test(p) || /\.test\.(ts|tsx)$/.test(p) || p.includes(`${sep}test${sep}`)) continue;
      if (re.test(readFileSync(p, "utf8"))) out.push(p);
    }
  }
  return out;
}

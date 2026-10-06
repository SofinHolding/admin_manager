// Quét bí mật trong bundle/nguồn. Dùng bởi `npm run build` (postbuild) và test `bundle.test.ts`.
// Không in giá trị khớp (chỉ loại + vị trí) để chính báo cáo không làm lộ bí mật.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname } from "node:path";

export const RULES = [
  { name: "machine-api-key", re: /mpk_[A-Za-z0-9_-]{12}\.[A-Za-z0-9_-]{20,}/ },
  { name: "private-key-block", re: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { name: "jwt-literal", re: /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/ },
  { name: "secret-assignment", re: /\b(JWT_SECRET|CLIENT_SECRET|SERVICE_KEY|FEED_KEY|ENCRYPTION_KEY|SECRET_KEY)\b["']?\s*[:=]\s*["'][^"'\s]{8,}["']/i },
  { name: "service-key-header", re: /X-Service-Key/i },
  { name: "aws-access-key", re: /AKIA[0-9A-Z]{16}/ },
];

const TEXT_EXT = new Set([".js", ".mjs", ".css", ".html", ".json", ".map", ".txt", ".svg", ".ts", ".tsx", ".env", ".example"]);

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (name === "node_modules" || name === ".git") continue;
      yield* walk(p);
    } else yield p;
  }
}

/** Trả về danh sách {file, rule}. `skip(file)` cho phép bỏ qua (vd file test chứa mẫu giả). */
export function scanDir(dir, { skip = () => false } = {}) {
  const hits = [];
  for (const file of walk(dir)) {
    if (skip(file)) continue;
    const ext = extname(file);
    if (!TEXT_EXT.has(ext) && !file.endsWith(".env.example")) continue;
    const text = readFileSync(file, "utf8");
    for (const r of RULES) if (r.re.test(text)) hits.push({ file, rule: r.name });
  }
  return hits;
}

export function scanText(text) {
  return RULES.filter((r) => r.re.test(text)).map((r) => r.name);
}

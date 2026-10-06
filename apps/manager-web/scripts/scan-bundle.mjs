// postbuild: dist/ KHÔNG được chứa bí mật (API key máy, private key, JWT literal, secret gán cứng, X-Service-Key).
import { existsSync } from "node:fs";
import { scanDir } from "./secretScan.mjs";

if (!existsSync("dist")) {
  console.error("scan-bundle: không có dist/ — chạy sau `vite build`");
  process.exit(1);
}
const hits = scanDir("dist");
if (hits.length) {
  console.error("scan-bundle: PHÁT HIỆN nghi vấn bí mật trong bundle:");
  for (const h of hits) console.error(`  - [${h.rule}] ${h.file}`);
  process.exit(1);
}
console.log("scan-bundle: OK — dist/ không chứa mẫu bí mật nào.");

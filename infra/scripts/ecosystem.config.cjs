/** PM2 ecosystem — dev services (Windows, Node.js v24).
 *  Chạy vite.js trực tiếp thay vì qua npm.cmd (PM2 không parse được .cmd).
 *  Đường dẫn tính từ GỐC repo (không phụ thuộc thư mục đang đứng):  pm2 start infra/scripts/ecosystem.config.cjs */
const path = require("path");
const ROOT = path.resolve(__dirname, "..", "..");
const app = (name) => ({
  name,
  cwd: path.join(ROOT, "apps", name),
  script: "./node_modules/vite/bin/vite.js",
  args: "",
  watch: false,
  autorestart: false,
});

module.exports = { apps: [app("manager-web"), app("local-fallback-tool")] };

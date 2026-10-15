/** PM2 ecosystem — dev services (Windows, Node.js v24).
 *  Chạy vite.js trực tiếp thay vì qua npm.cmd (PM2 không parse được .cmd). */
module.exports = {
  apps: [
    {
      name: "manager-web",
      cwd: "./apps/manager-web",
      script: "./node_modules/vite/bin/vite.js",
      args: "",
      watch: false,
      autorestart: false,
    },
    {
      name: "local-fallback-tool",
      cwd: "./apps/local-fallback-tool",
      script: "./node_modules/vite/bin/vite.js",
      args: "",
      watch: false,
      autorestart: false,
    },
  ],
};

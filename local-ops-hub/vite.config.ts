import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tool B v1 — web shell (xem README.md: lý do chưa đóng gói Tauri). Dev proxy /v1 sang
// admin-server cổng 8423, cùng quy ước với admin-web/vite.config.ts.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5176,
    proxy: {
      "/v1": { target: "http://localhost:8423", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
  },
});

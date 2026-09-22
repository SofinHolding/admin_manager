import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/admin/",
  server: {
    port: 5174,
    proxy: {
      // /v1/reward → reward-service :8422. PHẢI đứng trước /v1 (khớp cụ thể hơn) để không bị
      // /v1 nuốt mất và route nhầm sang admin-server.
      "/v1/reward": { target: "http://localhost:8422", changeOrigin: true },
      "/v1": { target: "http://localhost:8421", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: "index.html",
    },
  },
});

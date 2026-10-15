import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "path";

export default defineConfig(({ mode }) => {
  // Đọc .env.dev từ root monorepo (2 cấp lên từ apps/manager-web/).
  // loadEnv("dev", rootDir) đọc file .env.dev — khớp với file dev_stack.py sinh ra.
  // process.env.DEV_GATEWAY_URL (truyền qua pm2 --update-env hoặc shell) được ưu tiên hơn file:
  // điều này cho phép đổi gateway mà không cần sửa .env.dev (tránh vi phạm quy tắc no-env-write).
  const MONOREPO_ROOT = resolve(__dirname, "../..");
  const env = loadEnv("dev", MONOREPO_ROOT, "");
  // Mọi lời gọi API (kể cả OIDC) đi qua API Gateway cùng origin; Gateway định tuyến tới từng service.
  const gateway = process.env.DEV_GATEWAY_URL || env.DEV_GATEWAY_URL || "http://127.0.0.1:8400";
  const viaGateway = { target: gateway, changeOrigin: true };

  return {
    plugins: [react(), tailwindcss()],
    base: "/",
    server: {
      // 127.0.0.1 (không phải "localhost") để khớp redirect URI OIDC đã đăng ký: http://127.0.0.1:5174/admin/callback
      host: "127.0.0.1",
      port: 5174,
      // Sửa file test/tài liệu KHÔNG được làm tải lại cả trang (gây nhấp nháy khi đang chỉnh sửa nhiều file)
      watch: { ignored: ["**/*.test.ts", "**/*.test.tsx", "**/src/test/**", "**/dist/**", "**/*.timestamp-*.mjs"] },
      proxy: {
        "/v1": viaGateway,
        "/oauth": viaGateway,
        "/.well-known": viaGateway,
      },
    },
    build: {
      outDir: "dist",
      rollupOptions: {
        input: "index.html",
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
      restoreMocks: true,
      testTimeout: 15_000,
    },
  };
});

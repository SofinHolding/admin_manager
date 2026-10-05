import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  // Biến KHÔNG có tiền tố VITE_ (chỉ dùng ở dev server, không bao giờ vào bundle).
  const env = loadEnv(mode, process.cwd(), "");
  // Mọi lời gọi API (kể cả OIDC) đi qua API Gateway cùng origin; Gateway định tuyến tới từng service.
  const gateway = env.DEV_GATEWAY_URL || "http://127.0.0.1:8400";
  const viaGateway = { target: gateway, changeOrigin: true };

  return {
    plugins: [react(), tailwindcss()],
    base: "/admin/",
    server: {
      // 127.0.0.1 (không phải "localhost") để khớp redirect URI OIDC đã đăng ký: http://127.0.0.1:5174/admin/callback
      host: "127.0.0.1",
      port: 5174,
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

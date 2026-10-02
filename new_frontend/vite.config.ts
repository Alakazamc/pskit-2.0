import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/api": { target: process.env.DEV_API_PROXY_TARGET || "http://127.0.0.1:18080", changeOrigin: true } },
  },
  test: {
    environment: "jsdom",
    env: { VITE_AUTH_MODE: "demo" },
    setupFiles: ["./src/test/setup.ts"],
    maxWorkers: 2,
  },
});

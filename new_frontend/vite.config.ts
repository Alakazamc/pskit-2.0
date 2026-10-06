import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "DEV_");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: { "/api": { target: env.DEV_API_PROXY_TARGET || "http://127.0.0.1:18080", changeOrigin: true } },
    },
    build: {
      manifest: true,
      rolldownOptions: {
        output: {
          // Molstar has circular module initialization. Preserve its source
          // execution order while splitting the lazy engine into cacheable files.
          strictExecutionOrder: true,
          codeSplitting: {
            groups: [{
              name: "molstar-engine",
              test: /node_modules[\\/]molstar[\\/]lib[\\/]/,
              includeDependenciesRecursively: false,
              minSize: 50_000,
              maxSize: 750_000,
            }],
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      env: { VITE_AUTH_MODE: "demo" },
      setupFiles: ["./src/test/setup.ts"],
      maxWorkers: 2,
    },
  };
});

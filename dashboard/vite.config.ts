import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/v1": {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
        "/health": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});

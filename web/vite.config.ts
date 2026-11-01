import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, the cockpit runs on :5173 and proxies /api to `pablo serve` on :8710.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": { target: "http://127.0.0.1:8710", changeOrigin: true } },
  },
});

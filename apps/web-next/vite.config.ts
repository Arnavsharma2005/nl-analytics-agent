import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // In development the API runs on :8000. In production a Cloudflare Pages Function (functions/) forwards these.
    proxy: { "/v1": "http://localhost:8000", "/healthz": "http://localhost:8000" },
  },
  // ECharts is its own chunk, loaded only when the first chart is drawn.
  build: { chunkSizeWarningLimit: 600 },
});

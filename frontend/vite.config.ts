import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // During development the FastAPI backend runs on :8000.
    proxy: { "/api": "http://localhost:8000" },
  },
});

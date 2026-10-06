import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En dev : `npm run dev` + backend sur :8000 (les appels /api et /mcp sont relayés).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
});

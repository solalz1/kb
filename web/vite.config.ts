import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En dev : `npm run dev` + backend sur :8000 (les appels /api et /mcp sont relayés).
export default defineConfig({
  plugins: [react()],
  build: {
    // libraries in their own file (loaded up front, cached across deploys that only change the app)
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: "vendor", test: /node_modules/ }] } } },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
});

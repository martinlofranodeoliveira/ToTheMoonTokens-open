import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ["recharts"],
          icons: ["lucide-react"],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": "http://127.0.0.1:8010",
      "/health": "http://127.0.0.1:8010",
      "/ready": "http://127.0.0.1:8010",
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4174,
  },
});

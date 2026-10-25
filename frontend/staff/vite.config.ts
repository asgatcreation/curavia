// vitest/config rather than vite: the `test` block below is Vitest's,
// and Vite's own defineConfig type does not include it.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// The SPA talks to Django on 8010 in development. Proxying rather than using
// an absolute URL keeps cookies first-party, so session auth and CSRF behave
// exactly as they will in production.
//
// 8010 rather than 8000 because this machine runs other projects' servers on
// 8000 and 3000. Override it with VITE_API_TARGET when yours is elsewhere.
const API_TARGET = process.env["VITE_API_TARGET"] ?? "http://localhost:8010";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@curavia/shared": fileURLToPath(new URL("../shared/src/index.ts", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true },
      "/static": { target: API_TARGET, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true, target: "es2023" },
  test: { environment: "jsdom", globals: true },
});

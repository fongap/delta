import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Standalone test config (kept separate from vite.config.ts so the production `vite build` is
// untouched). Reused by later frontend phases — add new `*.test.tsx` files under src/.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@delta/i18n": path.resolve(process.cwd(), "../../packages/i18n"),
    },
  },
  server: {
    fs: { allow: [path.resolve(process.cwd(), "../..")] },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}", "../../packages/i18n/**/*.test.{ts,tsx}"],
  },
});

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
      // packages/i18n sits outside this root; vite 8's resolver no longer falls back to the
      // config root's node_modules, so pin react to this package's copy explicitly.
      react: path.resolve(process.cwd(), "node_modules/react"),
      "react/jsx-runtime": path.resolve(process.cwd(), "node_modules/react/jsx-runtime.js"),
      "react/jsx-dev-runtime": path.resolve(process.cwd(), "node_modules/react/jsx-dev-runtime.js"),
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

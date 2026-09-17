import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * Vitest — UI tests for the W1 surfaces (export buttons, saved queries,
 * history). Mirrors the tsconfig `@/*` path alias; jsdom for the components.
 * `next build` never touches this file.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    css: false,
    setupFiles: ["./test/setup.ts"],
  },
});

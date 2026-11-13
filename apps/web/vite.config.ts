import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * Production-only Content-Security-Policy. The app is a static, offline bundle:
 * every resource is same-origin, there is no inline script, and no network egress
 * is permitted (`connect-src 'self'`). Dev mode is excluded because the React
 * refresh preamble is an inline script.
 */
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'",
  "frame-src 'none'",
].join("; ");

function cspPlugin(): Plugin {
  return {
    name: "careertwin-csp",
    apply: "build",
    transformIndexHtml(html) {
      return html.replace(
        "<!--csp-->",
        `<meta http-equiv="Content-Security-Policy" content="${CSP}" />`,
      );
    },
  };
}

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  // Relative base so the built bundle also works when served from a sub-path.
  base: "./",
  plugins: [react(), cspPlugin()],
  resolve: {
    // packages/ui lives outside this package; make it use this app's single React copy.
    dedupe: ["react", "react-dom"],
    alias: {
      "@careertwin/ui": r("../../packages/ui/src/index.tsx"),
      "@": r("./src"),
    },
  },
  server: { port: 5173, strictPort: true, fs: { allow: [r("../..")] } },
  preview: { port: 4173, strictPort: true },
  build: {
    sourcemap: true,
    target: "es2022",
  },
  test: {
    environment: "node",
    include: ["tests/unit/**/*.test.ts"],
    reporters: ["default"],
  },
});

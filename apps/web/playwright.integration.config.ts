import { defineConfig, devices } from "@playwright/test";

/**
 * Config for lane E's `ui.e2e` stage only (scripts/e2e-integration.mjs).
 * Serves the already-built dist/ (with the run's artifacts copied in) and runs
 * the keyboard-only pipeline journey. A separate port keeps it from colliding
 * with the normal E2E suite.
 */
const channel = process.env.PW_CHANNEL || undefined;

export default defineConfig({
  testDir: "tests/integration",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json"]],
  timeout: 180_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    channel,
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

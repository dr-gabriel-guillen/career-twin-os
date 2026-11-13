import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E against the PRODUCTION build (vite preview), so the CSP and the
 * real bundle are what gets tested. Set PW_CHANNEL=chrome to use an installed
 * Chrome instead of Playwright's bundled Chromium.
 */
const channel = process.env.PW_CHANNEL || undefined;
/**
 * CT_E2E_PORT moves the preview server off 4173, which lane E's acceptance launcher also uses.
 * Without it, a running launcher's (older) build would be reused and tested by mistake.
 */
const port = Number(process.env.CT_E2E_PORT || 4173);
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: origin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    channel,
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: origin,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});

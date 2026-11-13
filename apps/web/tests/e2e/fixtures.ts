import { test as base, expect, type Page } from "@playwright/test";

/**
 * Every test automatically fails if the page logs an error, throws, violates the
 * CSP, or makes ANY request to an origin other than the local preview server.
 * That is the "no external calls" guarantee, enforced in the browser.
 */
export const ORIGIN = `http://127.0.0.1:${Number(process.env.CT_E2E_PORT || 4173)}`;

interface Guard {
  errors: string[];
  external: string[];
  /** Messages a test expects (e.g. simulated failures). */
  allow: (pattern: RegExp) => void;
}

export const test = base.extend<{ guard: Guard }>({
  guard: [
    async ({ page }, use) => {
      const errors: string[] = [];
      const external: string[] = [];
      const allowed: RegExp[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" || /content security policy/i.test(m.text())) errors.push(`console.${m.type()}: ${m.text()}`);
      });
      page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      page.on("request", (r) => {
        const url = r.url();
        if (!url.startsWith(ORIGIN) && !url.startsWith("data:") && !url.startsWith("blob:")) external.push(url);
      });
      await use({ errors, external, allow: (p) => allowed.push(p) });
      expect(external, "requests to origins other than the local preview server").toEqual([]);
      expect(errors.filter((e) => !allowed.some((p) => p.test(e))), "console errors / page errors / CSP violations").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export const ROUTES = [
  { path: "/", title: "Overview" },
  { path: "/jobs", title: "Discover Jobs" },
  { path: "/matching", title: "Matching" },
  { path: "/evidence", title: "Profile Evidence" },
  { path: "/resume", title: "Resume Studio" },
  { path: "/applications", title: "Applications" },
  { path: "/experiments", title: "Experiments" },
  { path: "/atlas", title: "Career Atlas" },
  { path: "/simulator", title: "Career Simulator" },
  { path: "/settings", title: "Settings" },
] as const;

export async function open(page: Page, path: string, title?: string) {
  await page.goto(`/#${path}`);
  if (title) await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
}

/** Read a file the app offered for download, as text. */
export async function downloadText(page: Page, trigger: () => Promise<void>): Promise<{ name: string; text: string }> {
  const [download] = await Promise.all([page.waitForEvent("download"), trigger()]);
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const c of stream) chunks.push(c as Buffer);
  return { name: download.suggestedFilename(), text: Buffer.concat(chunks).toString("utf8") };
}

import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, open, test } from "./fixtures";

/**
 * Regenerates the screenshots in docs/screenshots/ from the running app.
 * Skipped unless CT_SCREENSHOTS=1, so a normal test run never rewrites them:
 *   CT_SCREENSHOTS=1 npx playwright test tests/e2e/screenshots.spec.ts
 * Everything on screen is synthetic fixture data.
 */
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screenshots");

test.skip(process.env.CT_SCREENSHOTS !== "1", "set CT_SCREENSHOTS=1 to regenerate screenshots");

test("capture screenshots of the main screens", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  const shot = async (name: string) => page.screenshot({ path: join(OUT, `${name}.png`), animations: "disabled" });

  await open(page, "/jobs", "Discover Jobs");
  await page.getByTestId("job-search").fill("engineer");
  await shot("01-discover-jobs");

  await open(page, "/matching?job=fake-ai-2", "Matching");
  await page.getByTestId("match-detail").scrollIntoViewIfNeeded();
  await shot("02-matching");

  await open(page, "/resume?variant=rv-base", "Resume Studio");
  await page.getByTestId("bullet-edit-b-latency").click();
  await page.getByTestId("bullet-text").fill("Certified engineer who reduced p95 latency of the fictional order API by 83%");
  await page.getByTestId("bullet-save").click();
  await page.getByTestId("bullet-b-latency").scrollIntoViewIfNeeded();
  await shot("03-resume-truthfulness-guard");

  await open(page, "/applications?app=app-synth-001", "Applications");
  await expect(page.getByTestId("kanban")).toBeVisible();
  await shot("04-applications-board");

  await open(page, "/experiments", "Experiments");
  await page.getByTestId("exp-exp-synth-resume-framing").click();
  await page.getByRole("tab", { name: "Results" }).click();
  await page.getByTestId("exp-results").scrollIntoViewIfNeeded();
  await shot("05-experiment-results");

  await open(page, "/atlas", "Career Atlas");
  await page.getByTestId("pt-fake-cto-3").click();
  await page.getByTestId("atlas-plot").scrollIntoViewIfNeeded();
  await shot("06-career-atlas");

  await open(page, "/simulator", "Career Simulator");
  await page.getByTestId("scenario-table").scrollIntoViewIfNeeded();
  await shot("07-career-simulator");

  await page.setViewportSize({ width: 360, height: 740 });
  await open(page, "/", "Overview");
  await shot("08-overview-phone-360");
});

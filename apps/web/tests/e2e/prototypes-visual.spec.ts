import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * Visual QA for the journey prototypes. Accessibility and type checks do not catch layout failures
 * such as text squeezed into a narrow grid track (the step-3 empty state at 39675b3). These tests
 * measure rendered boxes and attach a screenshot of every case to the test output.
 */
const PIPELINE = join(dirname(fileURLToPath(import.meta.url)), "..", "upstream-samples", "pipeline");

async function go(page: Page, hash: string, heading: string, query = "") {
  await page.goto(`/prototypes.html${query}#${hash}`);
  // Six heavy tests load fixtures in parallel; give the first render more time than the 10 s default.
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible({ timeout: 30_000 });
}

async function routeArtifacts(page: Page) {
  await page.route("**/artifacts/*.json", (route) => {
    const name = new URL(route.request().url()).pathname.split("/").pop()!;
    const file = join(PIPELINE, name);
    return existsSync(file) ? route.fulfill({ contentType: "application/json", body: readFileSync(file, "utf8") }) : route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>app shell</title>" });
  });
}

/** Text elements whose rendered shape implies broken layout: very few characters per line, or a sliver width. */
async function squeezedText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("main p, main li, main span, main strong, main h1, main h2, main h3, main label, main button, main a, main pre")) {
      if (el.offsetParent === null || el.closest(".ct-sr-only")) continue;
      // Only elements that carry text themselves, measured by all the text they render.
      const ownText = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim().length > 0);
      if (!ownText) continue;
      const own = (el.innerText ?? "").replace(/\s+/g, " ").trim();
      if (own.length < 12) continue;
      const r = el.getBoundingClientRect();
      // 1x1 boxes are the visually-hidden pattern for screen-reader text, not a layout failure.
      if (r.width <= 2 || r.height <= 2) continue;
      const cs = getComputedStyle(el);
      const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
      const lines = Math.max(1, Math.round(r.height / lh));
      const perLine = own.length / lines;
      if (perLine < 6 || r.width < 48) out.push(`${el.tagName.toLowerCase()} "${own.slice(0, 40)}" ${Math.round(r.width)}x${Math.round(r.height)} (${perLine.toFixed(1)} chars/line)`);
    }
    return out;
  });
}

async function jobIds(page: Page): Promise<string[]> {
  await go(page, "/discover", "Discover");
  const ids = await page.locator('[data-testid^="pt-job-"]').evaluateAll((els) => els.map((e) => (e.getAttribute("data-testid") ?? "").replace("pt-job-", "")));
  return ids.slice(0, 10);
}

/** Opens step 2 for a posting, starting from the given base variant (by option index). */
async function openTailorContent(page: Page, jobId: string, baseIndex = 0): Promise<number> {
  await page.goto(`/prototypes.html#/tailor/${encodeURIComponent(jobId)}`);
  await expect(page.getByRole("heading", { level: 1, name: "Tailor résumé" })).toBeVisible({ timeout: 30_000 });
  // Same URL does not remount; return to step 1 through the step tabs.
  if (!(await page.getByLabel("Start from").isVisible())) await page.locator(".pt-step button").first().click();
  const start = page.getByLabel("Start from");
  const options = await start.locator("option").count();
  if (baseIndex < options) await start.selectOption({ index: baseIndex });
  await page.getByRole("button", { name: "Next: choose content" }).click();
  return options;
}

for (const scheme of ["light", "dark"] as const) {
  for (const vp of [{ w: 1440, h: 1000 }, { w: 375, h: 812 }]) {
    test(`résumé diff at ${vp.w}px ${scheme}: empty, added, mixed and removed changes render as readable rows`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width: vp.w, height: vp.h });
      const ids = await jobIds(page);
      let found = false;
      // Find a posting and a starting résumé for which the vault offers a claim not yet used.
      search: for (const id of ids) {
        const options = await openTailorContent(page, id, 0);
        for (let b = 0; b < options; b++) {
          if (b > 0) await openTailorContent(page, id, b);
          const add = page.getByTestId("pt-suggestions").getByRole("button", { name: "Add line" });
          if ((await add.count()) > 0 && (await add.first().isEnabled())) {
            found = true;
            break search;
          }
        }
      }
      expect(found, "a posting with an addable claim in the fixture vault").toBe(true);

      const review = async (label: string) => {
        await page.getByRole("button", { name: "Next: review changes" }).click();
        await expect(page.getByRole("heading", { level: 2, name: "Review the changes" })).toBeVisible();
        expect(await squeezedText(page), `${label}: squeezed text`).toEqual([]);
        // Full-page captures stitch sticky bars at the current scroll position; capture from the top.
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: test.info().outputPath(`diff-${label}-${vp.w}-${scheme}.png`), fullPage: true });
      };
      const back = () => page.getByRole("button", { name: "Back", exact: true }).click();

      // 1. Empty: nothing changed.
      await review("empty");
      const empty = page.getByTestId("pt-diff-empty");
      await expect(empty).toBeVisible();
      await expect(page.getByTestId("pt-diff")).toHaveCount(0);
      const eb = (await empty.boundingBox())!;
      expect(eb.width, "empty state spans the panel").toBeGreaterThan(vp.w < 600 ? 240 : 400);
      expect(eb.height, "empty state is a short block, not a vertical column").toBeLessThan(vp.w < 600 ? 170 : 100);
      await expect(page.getByTestId("pt-diff-summary")).toHaveText(/^0 added, 0 removed, \d+ unchanged\.$/);

      // 2. Added only.
      await back();
      await page.getByTestId("pt-suggestions").getByRole("button", { name: "Add line" }).first().click();
      await review("added");
      await expect(page.locator('[data-change="added"]')).toHaveCount(1);
      await expect(page.locator('[data-change="removed"]')).toHaveCount(0);

      // 3. Mixed: also switch one base line off.
      await back();
      await page.locator(".pt-line").filter({ has: page.locator('input[type="checkbox"]') }).first().locator('input[type="checkbox"]').uncheck();
      await review("mixed");
      await expect(page.locator('[data-change="added"]')).toHaveCount(1);
      await expect(page.locator('[data-change="removed"]')).toHaveCount(1);

      // 4. Removed only: take the added line back out.
      await back();
      await page.getByTestId("pt-suggestions").getByRole("button", { name: "Remove", exact: true }).first().click();
      await review("removed");
      await expect(page.locator('[data-change="added"]')).toHaveCount(0);
      await expect(page.locator('[data-change="removed"]')).toHaveCount(1);

      // The line text sits in the content column, to the right of a narrow marker column.
      for (const row of await page.locator(".pt-diff__row").all()) {
        const text = (await row.locator(".pt-diff__text").boundingBox())!;
        const mark = (await row.locator(".pt-diff__mark").boundingBox())!;
        expect(text.width).toBeGreaterThan(mark.width * 4);
        expect(text.x).toBeGreaterThan(mark.x);
      }
    });
  }
}

test("no squeezed text on any prototype screen at 1440 and 375", async ({ page }) => {
  for (const w of [1440, 375]) {
    await page.setViewportSize({ width: w, height: w < 600 ? 812 : 1000 });
    for (const [hash, heading] of [["/discover", "Discover"], ["/tailor", "Tailor résumé"], ["/track", "Track"], ["/atlas", "Career paths"]] as const) {
      await go(page, hash, heading);
      expect(await squeezedText(page), `${hash} at ${w}px`).toEqual([]);
    }
    const ids = await jobIds(page);
    await page.goto(`/prototypes.html#/discover/${encodeURIComponent(ids[1]!)}`);
    await expect(page.getByTestId("pt-detail")).toBeVisible();
    expect(await squeezedText(page), `discover detail at ${w}px`).toEqual([]);
  }
});

test("atlas copy describes projection variance and explains only the positions actually drawn", async ({ page }) => {
  // Fixture data: both candidate positions are projected.
  await go(page, "/atlas", "Career paths");
  const u = page.getByTestId("pt-uncertainty");
  await expect(u).toContainText("variation in the input skill vectors");
  await expect(u).not.toContainText("skill information");
  await expect(page.getByTestId("pt-position-note")).toContainText("Two marks show you");
  await expect(page.locator(".pt-legend").first()).toContainText("evidence-backed claims only");

  // Pipeline sample: explain whichever positions it actually projects.
  test.skip(!existsSync(join(PIPELINE, "atlas.pca.json")), "pipeline sample not present");
  const pca = JSON.parse(readFileSync(join(PIPELINE, "atlas.pca.json"), "utf8")) as { points: { representation_type: string; entity_id: string }[] };
  const cands = pca.points.filter((p) => p.representation_type === "candidate");
  const hasAll = cands.some((p) => !p.entity_id.endsWith("#supported-only"));
  const hasSupported = cands.some((p) => p.entity_id.endsWith("#supported-only"));
  await routeArtifacts(page);
  await go(page, "/atlas", "Career paths", "?source=artifacts");
  const note = page.getByTestId("pt-position-note");
  if (hasAll && hasSupported) await expect(note).toContainText("Two marks show you");
  else if (hasAll) {
    await expect(note).toContainText("One mark shows you, from every usable claim");
    await expect(page.locator(".pt-legend").first()).not.toContainText("evidence-backed claims only");
  } else if (hasSupported) await expect(note).toContainText("One mark shows you, from evidence-backed claims only");
  else await expect(note).toContainText("was not provided");
  await expect(page.getByTestId("pt-uncertainty")).not.toContainText("skill information");
});

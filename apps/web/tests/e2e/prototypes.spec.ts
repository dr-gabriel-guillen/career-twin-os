import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * The owner-review journey prototypes (prototypes.html). They run on the product's bundle,
 * workspace reducer and safety gates; these tests drive them as a person would.
 */
const PIPELINE = join(dirname(fileURLToPath(import.meta.url)), "..", "upstream-samples", "pipeline");
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function go(page: Page, hash: string, heading: string, query = "") {
  await page.goto(`/prototypes.html${query}#${hash}`);
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
}

async function axe(page: Page, label: string) {
  const r = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  expect(r.violations.map((v) => `${label}: ${v.id} ${v.nodes[0]?.target.join(" ")}`)).toEqual([]);
}

test("journey 1 (desktop): discover, open a posting, read why it fits, shortlist", async ({ page }) => {
  await go(page, "/discover", "Discover");
  await axe(page, "discover");
  const rows = page.locator('[data-testid^="pt-job-"]');
  expect(await rows.count()).toBeGreaterThan(2);
  const second = rows.nth(1);
  const title = (await second.locator(".pt-row__title").innerText()).trim();
  await second.click();
  const detail = page.getByTestId("pt-detail");
  await expect(detail.getByRole("heading", { level: 2 })).toHaveText(title);
  await expect(detail.getByRole("heading", { name: "Why this could fit you" })).toBeVisible();
  await expect(detail.getByRole("heading", { name: "What stands in the way" })).toBeVisible();
  await expect(detail.getByRole("heading", { name: "What would improve your fit" })).toBeVisible();
  await expect(detail).toContainText("not a probability of being hired");
  await page.getByTestId("pt-shortlist").click();
  await expect(page.getByTestId("pt-shortlist")).toHaveAttribute("aria-pressed", "true");
  await page.locator(".pt-chips").getByRole("button", { name: /Shortlisted/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText(title);
});

test("journey 1 (phone 375): list then detail, Back returns to the same posting", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await go(page, "/discover", "Discover");
  const rows = page.locator('[data-testid^="pt-job-"]');
  await expect(page.getByTestId("pt-detail")).toBeHidden();
  const target = rows.nth(3);
  const id = ((await target.getAttribute("data-testid")) ?? "").replace("pt-job-", "");
  await target.click();
  await expect(page.getByTestId("pt-detail")).toBeVisible();
  await expect(page.getByTestId("pt-detail")).toBeFocused();
  await expect(page.getByTestId("pt-shortlist")).toBeInViewport();
  await page.getByRole("button", { name: "Back to postings" }).click();
  await expect(page.getByTestId(`pt-job-${id}`)).toBeFocused();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("journeys 2 and 3: tailor from your own claims, review the diff, approve, export, then track and follow up", async ({ page }) => {
  await go(page, "/discover", "Discover");
  // Use a posting that is not in the tracker yet, so the journey starts from "saved".
  const rows = page.locator('[data-testid^="pt-job-"]');
  for (let i = 0; i < (await rows.count()); i++) {
    await rows.nth(i).click();
    if (await page.getByTestId("pt-save-tracker").count()) break;
  }
  await expect(page.getByTestId("pt-save-tracker")).toBeVisible();
  const jobTitle = (await page.getByTestId("pt-detail").getByRole("heading", { level: 2 }).innerText()).trim();
  await page.getByTestId("pt-tailor").click();
  await expect(page.getByRole("heading", { level: 1, name: "Tailor résumé" })).toBeVisible();
  await expect(page.getByTestId("pt-coverage")).toBeVisible();
  await axe(page, "tailor step 1");
  await page.getByRole("button", { name: "Next: choose content" }).click();

  // Switch off any base line that would block export, then add one usable claim from the vault if offered.
  const lines = page.locator(".pt-line").filter({ has: page.locator('input[type="checkbox"]') });
  for (const line of await lines.all()) {
    const text = (await line.innerText()).toLowerCase();
    if (/unverified|contradicted|no claim linked|missing claim/.test(text)) await line.locator('input[type="checkbox"]').uncheck();
  }
  const add = page.getByTestId("pt-suggestions").getByRole("button", { name: "Add line" }).first();
  if (await add.count()) if (await add.isEnabled()) await add.click();
  await page.getByRole("button", { name: "Next: review changes" }).click();
  await expect(page.getByTestId("pt-diff-summary")).toBeVisible();
  await axe(page, "tailor review");
  await expect(page.getByTestId("pt-save-draft")).toBeEnabled();
  await page.getByTestId("pt-save-draft").click();

  // Approval is explicit and gated; export only after it.
  await expect(page.getByTestId("pt-approve")).toBeDisabled();
  await page.getByTestId("pt-confirm").check();
  await page.getByTestId("pt-approve").click();
  await expect(page.getByTestId("pt-approved")).toBeVisible();
  await expect(page.getByTestId("pt-export")).toBeEnabled();
  await page.getByTestId("pt-record").click();

  // Journey 3: the tracker opens on this application with the next action.
  await expect(page.getByRole("heading", { level: 1, name: "Track" })).toBeVisible();
  const detail = page.getByTestId("pt-app-detail");
  await expect(detail.getByRole("heading", { level: 2 })).toHaveText(jobTitle);
  await expect(page.getByTestId("pt-next")).toContainText("Apply on the employer’s site yourself, then record it");
  await axe(page, "track");
  await page.getByTestId("pt-record-applied").click();
  await page.getByRole("checkbox", { name: /I applied myself/ }).check();
  await page.getByRole("button", { name: "Record application" }).click();
  await expect(page.getByTestId("pt-timeline")).toContainText("Marked as submitted (manual)");
  await expect(page.getByTestId("pt-next")).toContainText("Wait for a reply");
  await page.getByTestId("pt-followup").click();
  await expect(page.getByTestId("pt-template")).toContainText("CareerTwin does not write with AI and cannot send email");
  await page.getByTestId("pt-rem-date").fill("2026-12-01");
  await page.getByTestId("pt-rem-add").click();
  await expect(detail).toContainText("Follow up:");
});

test("artifact mode: generated experiment records are not presented as your applications; atlas uncertainty stays visible", async ({ page }) => {
  test.skip(!existsSync(join(PIPELINE, "jobs.json")), "pipeline sample not present");
  await page.route("**/artifacts/*.json", (route) => {
    const name = new URL(route.request().url()).pathname.split("/").pop()!;
    const file = join(PIPELINE, name);
    return existsSync(file) ? route.fulfill({ contentType: "application/json", body: readFileSync(file, "utf8") }) : route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>app shell</title>" });
  });
  await go(page, "/track", "Track", "?source=artifacts");
  const generated = page.getByTestId("pt-generated");
  await expect(generated).toBeVisible();
  await expect(generated).toContainText("records from the pipeline’s experiment run are not shown here");
  // Only the person's own application log is listed.
  await expect(page.locator('[data-testid^="pt-app-app-exp"]')).toHaveCount(0);
  expect(await page.locator('[data-testid^="pt-app-"]:not([data-testid="pt-app-detail"])').count()).toBeLessThanOrEqual(1);
  await axe(page, "track artifacts");

  await go(page, "/atlas", "Career paths", "?source=artifacts");
  await expect(page.getByTestId("pt-uncertainty")).toBeVisible();
  await expect(page.getByTestId("pt-uncertainty")).toContainText("Distance on the map is not fit");
  await expect(page.getByText("Map diagnostics")).toBeVisible();
  await axe(page, "atlas");
});

import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, open, test } from "./fixtures";

/**
 * Regressions for the premium-UI correction (CT-F):
 * - Matching on phones and narrow tablets is a list/detail flow, not a long page where a tap
 *   updates content far below the viewport.
 * - In pipeline-artifact mode the data-source safety facts stay visible and the longer pipeline
 *   notes stay reachable, without a dominant banner.
 * - Résumé Studio is a document: the truth mark and evidence of a line are inspectable.
 */

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PIPELINE = join(WEB, "tests", "upstream-samples", "pipeline");

for (const vp of [
  { name: "phone 375", width: 375, height: 812 },
  { name: "tablet 768", width: 768, height: 1024 },
]) {
  test(`matching list/detail at ${vp.name}: choosing a lower row shows its explanation in view, Back returns to the row`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await open(page, "/matching", "Matching");
    const list = page.getByTestId("match-list");
    const detail = page.getByTestId("match-detail");
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();

    const rows = list.locator("li button.app-pick");
    const count = await rows.count();
    expect(count).toBeGreaterThan(5);
    const target = rows.nth(5);
    const title = (await target.locator("strong").innerText()).trim();
    const id = ((await target.getAttribute("data-testid")) ?? "").replace(/^match-/, "");
    await target.scrollIntoViewIfNeeded();
    await target.click();

    await expect(page).toHaveURL(new RegExp(`job=${id}`));
    await expect(list).toBeHidden();
    await expect(detail).toBeVisible();
    await expect(detail).toBeFocused();
    await expect(detail).toContainText(title);
    // The explanation starts inside the viewport: no scrolling through the list to find it.
    const box = await detail.boundingBox();
    expect(box && box.y >= 0 && box.y < vp.height / 2).toBeTruthy();
    await expect(page.getByTestId("near-term-score")).toBeInViewport();

    // Explicit Back control returns to the list with focus on the row that was chosen.
    await page.getByTestId("match-back").click();
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();
    await expect(page.getByTestId(`match-${id}`)).toBeFocused();

    // Browser Back works the same way (selection is a history entry).
    await page.getByTestId(`match-${id}`).click();
    await expect(detail).toBeVisible();
    await page.goBack();
    await expect(list).toBeVisible();

    // A deep link opens the detail view directly, with a way back.
    await page.goto(`/#/matching?job=${encodeURIComponent(id)}`);
    await expect(detail).toBeVisible();
    await expect(page.getByTestId("match-back")).toBeVisible();
  });
}

test("matching keeps the desktop split view: list and detail side by side, no Back control", async ({ page }) => {
  await open(page, "/matching", "Matching");
  await expect(page.getByTestId("match-list")).toBeVisible();
  await expect(page.getByTestId("match-detail")).toBeVisible();
  await expect(page.getByTestId("match-back")).toBeHidden();
});

test("artifact mode: safety facts stay visible, pipeline notes are one click away, no machine id as a heading", async ({ page }) => {
  test.skip(!existsSync(join(PIPELINE, "jobs.json")), "pipeline sample not present");
  await page.route("**/artifacts/*.json", (route) => {
    const name = new URL(route.request().url()).pathname.split("/").pop()!;
    const file = join(PIPELINE, name);
    return existsSync(file) ? route.fulfill({ contentType: "application/json", body: readFileSync(file, "utf8") }) : route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>app shell</title>" });
  });
  await page.goto("/?source=artifacts#/resume");
  await expect(page.getByRole("heading", { level: 1, name: "Resume Studio" })).toBeVisible();

  const source = page.getByTestId("artifact-banner");
  await expect(source).toBeVisible();
  await expect(page.getByTestId("mock-sources")).toBeVisible();
  await expect(page.getByTestId("missing-artifacts")).toBeVisible();
  // The data-source line is a line, not a panel that pushes the page down.
  const box = await source.boundingBox();
  expect(box!.height).toBeLessThan(80);

  // Pipeline notes are not lost: they are behind a disclosure and readable when opened.
  const details = page.getByTestId("source-details");
  const notes = page.getByTestId("source-notes");
  if ((await notes.count()) > 0) {
    await expect(notes).toBeHidden();
    await details.locator("summary").click();
    await expect(notes).toBeVisible();
    expect((await notes.locator("li").count())).toBeGreaterThan(0);
  }

  // The résumé is named for people; the upstream machine label is kept in technical details.
  const docTitle = page.locator("#rs-doc-title");
  await expect(docTitle).toBeVisible();
  await expect(docTitle).not.toHaveText(/^[a-z][\w-]*:[\w-]+$/i);
  await expect(page.locator("h1, h2, h3").filter({ hasText: /pinned:jp_/ })).toHaveCount(0);
});

test("resume studio: each line carries a truth mark and its evidence opens in the inspector", async ({ page }) => {
  await open(page, "/resume?variant=rv-base", "Resume Studio");
  const paper = page.locator(".rs-paper");
  await expect(paper).toBeVisible();
  const line = page.getByTestId("bullet-b-latency");
  await expect(line.locator(".rs-gutter")).toHaveCount(1);
  await line.locator("button.rs-line").click();
  // Wide screens show the evidence in a side inspector; narrower ones show it under the line.
  await expect(page.locator(".rs-inspector, .rs-inline-ev").filter({ visible: true }).first()).toContainText("behind this line");
  // Editing controls are present and reachable from the keyboard on the line.
  await line.locator("button.rs-line").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("bullet-edit-b-latency")).toBeFocused();
  await expect(page.getByTestId("gate-bar")).toBeVisible();
});

import AxeBuilder from "@axe-core/playwright";
import { expect, open, ROUTES, test } from "./fixtures";

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`every screen renders and passes axe (${scheme})`, () => {
    test.use({ colorScheme: scheme });
    for (const route of ROUTES) {
      test(`${route.title}`, async ({ page }) => {
        await open(page, route.path, route.title);
        await expect(page.getByTestId("synthetic-banner")).toBeVisible();
        await expect(page).toHaveTitle(new RegExp(`^${route.title} — CareerTwin OS \\(synthetic demo\\)$`));
        // Exactly one h1, and the current nav item is marked.
        await expect(page.locator("h1")).toHaveCount(1);
        await expect(page.locator('nav[aria-label="Primary"] a[aria-current="page"]')).toHaveText(route.title);
        const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
        const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} node(s) e.g. ${v.nodes[0]?.target.join(" ")}`);
        expect(summary).toEqual([]);
      });
    }
  });
}

test("interactive states also pass axe: dialogs, tabs, table views, selected atlas point", async ({ page }) => {
  test.setTimeout(120_000);
  const check = async (label: string) => {
    const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
    expect(results.violations.map((v) => `${label}: ${v.id} ${v.nodes[0]?.target.join(" ")}`)).toEqual([]);
  };
  await open(page, "/jobs/fake-fde-1", "Discover");
  await page.getByTestId("filters").locator("summary").click();
  await check("discover filters open");
  await page.getByRole("button", { name: "Posting link…" }).click();
  await expect(page.getByTestId("link-dialog")).toBeVisible();
  await check("link dialog");
  await page.keyboard.press("Escape");

  await open(page, "/evidence", "Profile Evidence");
  await page.getByTestId("add-claim").click();
  await check("claim dialog");
  await page.keyboard.press("Escape");

  await open(page, "/matching", "Compare");
  await expect(page.getByTestId("compare-columns").locator("article")).toHaveCount(3);
  await page.getByRole("button", { name: /^Remove .* from the comparison$/ }).first().click();
  await expect(page.getByTestId("compare-columns").locator("article")).toHaveCount(2);
  await check("compare with two columns");

  await open(page, "/resume", "Résumé Studio");
  for (const tab of ["Compare", /Review & export/]) {
    await page.getByRole("tab", { name: tab }).click();
    await check(`resume tab ${tab}`);
  }
  await open(page, "/applications/app-synth-001", "Applications");
  await expect(page.getByTestId("app-detail")).toBeVisible();
  await page.getByTestId("next-event").selectOption("note");
  await check("application detail with an update in progress");

  await open(page, "/experiments", "Experiments");
  for (const tab of [/Assignment/, "Results", "Sample size"]) {
    await page.getByRole("tab", { name: tab }).click();
    await check(`experiment tab ${tab}`);
  }
  await open(page, "/atlas/map", "Career Atlas");
  await page.getByTestId("pt-fake-ai-2").click();
  await expect(page.getByTestId("atlas-detail")).toContainText("Applied AI Architect");
  await check("atlas selected");
  await open(page, "/settings", "Settings");
  await page.getByTestId("wipe-open").click();
  await check("wipe dialog");
});

test("unknown route shows a not-found state, not a blank page", async ({ page }) => {
  await open(page, "/no-such-screen", "Page not found");
  await expect(page.getByTestId("error-state")).toContainText("There is no screen at this address.");
});

test("every nav link works from the menu and moves focus to the page heading", async ({ page }) => {
  // Start somewhere that is not first in the list, so every click is a real navigation.
  await open(page, "/settings", "Settings");
  for (const route of ROUTES) {
    await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: route.title, exact: true }).click();
    const h1 = page.getByRole("heading", { level: 1, name: route.title });
    await expect(h1).toBeVisible();
    await expect(h1).toBeFocused();
  }
});

test("experiment fixture results: censoring and missingness are visible and no winner is declared", async ({ page }) => {
  await open(page, "/experiments", "Experiments");
  await page.getByTestId("exp-exp-synth-resume-framing").click();
  await page.getByRole("tab", { name: "Results" }).click();
  await expect(page.getByTestId("evidence-level")).toContainText("Insufficient evidence. No conclusion can be drawn.");
  await expect(page.getByTestId("evidence-level")).toContainText("No variant is declared better.");
  await expect(page.getByTestId("results-synthetic")).toContainText("same true rate (25%) for every variant");
  const text = await page.getByTestId("exp-results").innerText();
  expect(text).toMatch(/3 unit\(s\) still inside the 21-day window/);
  expect(text).toMatch(/2 unit\(s\) with no recorded outcome/);
  expect(text).not.toMatch(/winner|wins|better variant|significant(?!” by luck)/i);
  // The fixture experiment is locked and has no local salt: design is read-only, no assignment controls.
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.getByTestId("exp-title")).toBeDisabled();
  await page.getByRole("tab", { name: /Assignment/ }).click();
  await expect(page.getByTestId("assign-all")).toHaveCount(0);
});

test("atlas filters, evidence basis and table alternative", async ({ page }) => {
  await open(page, "/atlas/map", "Career Atlas");
  await expect(page.getByTestId("atlas-count")).toHaveText("Showing 36 of 37 points");
  await page.getByTestId("atlas-type-job").uncheck();
  await expect(page.getByTestId("atlas-count")).toHaveText("Showing 14 of 37 points");
  await page.getByTestId("atlas-family-backend").uncheck();
  await expect(page.getByTestId("atlas-count")).toHaveText("Showing 13 of 37 points");
  // Switching the evidence basis swaps which candidate point is shown and hides plan paths from the other one.
  await expect(page.getByTestId("pt-candidate-synthetic-001")).toBeVisible();
  await expect(page.getByTestId("path-scenario-fde")).toBeVisible();
  await page.getByTestId("atlas-evidence").selectOption("supported");
  await expect(page.getByTestId("pt-candidate-synthetic-001")).toHaveCount(0);
  await expect(page.getByTestId("pt-candidate-synthetic-001#supported-only")).toBeVisible();
  await expect(page.getByTestId("path-scenario-fde")).toHaveCount(0);
  // Table alternative lists the same points and can select one.
  await expect(page.getByTestId("atlas-table").locator("tbody tr")).toHaveCount(13);
  await page.getByTestId("atlas-table").getByRole("button", { name: /Select: Field CTO/ }).click();
  await expect(page.getByTestId("atlas-detail")).toContainText("Centroid of only 2 posting(s); unstable.");
  // Quality filter hides low-reliability points.
  await page.getByTestId("atlas-quality").fill("1");
  const shown = await page.getByTestId("atlas-count").innerText();
  expect(Number(/Showing (\d+)/.exec(shown)![1])).toBeLessThan(13);
});

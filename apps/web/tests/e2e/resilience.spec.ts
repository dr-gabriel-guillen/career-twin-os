import { downloadText, expect, open, ROUTES, test } from "./fixtures";

const VIEWPORTS = [
  { name: "phone 360", width: 360, height: 740 },
  { name: "tablet 768", width: 768, height: 1024 },
  { name: "desktop 1440", width: 1440, height: 900 },
];

for (const vp of VIEWPORTS) {
  test(`no horizontal page scroll and reachable navigation at ${vp.name}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    for (const route of ROUTES) {
      await open(page, route.path, route.title);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route.title} overflows by ${overflow}px at ${vp.width}`).toBeLessThanOrEqual(0);
    }
    if (vp.width < 900) {
      // Navigation collapses behind a Menu button and is still fully usable.
      await open(page, "/", "Overview");
      const nav = page.locator('nav[aria-label="Primary"]');
      await expect(nav).toBeHidden();
      const toggle = page.getByRole("button", { name: "Menu" });
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await nav.getByRole("link", { name: "Career Atlas" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Career Atlas" })).toBeVisible();
      await expect(nav).toBeHidden();
      // Interactive targets are at least 24x24 CSS px (WCAG 2.2 target size minimum).
      const small = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("button, a.ct-btn, select, input:not([type=checkbox]):not([type=range])")]
          .filter((el) => el.offsetParent !== null)
          .map((el) => ({ r: el.getBoundingClientRect(), t: el.textContent?.trim().slice(0, 30) }))
          .filter((x) => x.r.width < 24 || x.r.height < 24)
          .map((x) => `${x.t} ${Math.round(x.r.width)}x${Math.round(x.r.height)}`),
      );
      expect(small).toEqual([]);
    } else {
      await expect(page.locator('nav[aria-label="Primary"]')).toBeVisible();
    }
  });
}

test("keyboard only: skip link, tabs, dialog focus trap and atlas navigation", async ({ page }) => {
  await open(page, "/jobs", "Discover Jobs");
  // Skip link is the first tab stop and moves focus into main.
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  // Dialog: opens from the keyboard, traps focus, Escape closes and returns focus to the trigger.
  const trigger = page.getByTestId("open-fake-fde-1");
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dlg = page.getByTestId("link-dialog");
  await expect(dlg).toBeVisible();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    // A native modal <dialog> makes the rest of the page inert. Focus is either inside the
    // dialog or has left the document for the browser's own UI (activeElement === body);
    // it must never land on page content behind the dialog.
    const where = await page.evaluate(() => {
      const el = document.activeElement;
      return el === null || el === document.body ? "browser-ui" : el.closest("dialog") ? "dialog" : `outside:${el.tagName}`;
    });
    expect(["dialog", "browser-ui"], `focus went to ${where}`).toContain(where);
  }
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  await expect(trigger).toBeFocused();

  // Tabs: arrow keys move and activate; Home/End jump.
  await open(page, "/resume", "Resume Studio");
  const editTab = page.getByRole("tab", { name: "Edit" });
  await editTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Compare" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "Compare" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /Review & export/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("review-panel")).toBeVisible();
  await page.keyboard.press("Home");
  await expect(editTab).toHaveAttribute("aria-selected", "true");

  // Atlas: one tab stop into the plot, arrows move between points, Enter selects, Escape clears.
  await open(page, "/atlas", "Career Atlas");
  const first = page.locator('[data-testid="atlas-plot"] g[role="button"][tabindex="0"]');
  await expect(first).toHaveCount(1);
  await first.focus();
  await expect(page.getByTestId("atlas-tooltip")).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  const focusedLabel = await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? "");
  expect(focusedLabel.length).toBeGreaterThan(10);
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-testid="atlas-plot"] g[aria-pressed="true"]')).toHaveCount(1);
  await expect(page.getByTestId("atlas-detail")).toContainText("Neighbourhood quality");
  await page.keyboard.press("Escape");
  await expect(page.locator('[data-testid="atlas-plot"] g[aria-pressed="true"]')).toHaveCount(0);

  // Ranking list and blend slider are operable from the keyboard.
  await open(page, "/matching", "Matching");
  await page.getByTestId("blend").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByText("Ranking blend: 65% near-term fit, 35% strategic value")).toBeVisible();
  await page.getByTestId("match-fake-backend-4").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("hard-constraints")).toContainText("Fail");
});

test("reduced motion: system preference and the in-app setting both disable animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page, "/settings", "Settings");
  await page.getByTestId("set-simulate").selectOption("slow");
  const duration = () => page.evaluate(() => {
    const el = document.querySelector(".ct-skeleton");
    return el ? parseFloat(getComputedStyle(el).animationDuration) : -1;
  });
  await page.goto("/#/jobs");
  await expect(page.getByTestId("loading-state")).toBeVisible();
  expect(await duration()).toBeLessThan(0.001);
  await expect(page.getByRole("heading", { level: 1, name: "Discover Jobs" })).toBeVisible({ timeout: 15_000 });

  // With no system preference, the in-app "Reduce motion" setting does the same.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await open(page, "/settings", "Settings");
  await page.getByTestId("set-motion").selectOption("reduce");
  await expect(page.locator("html")).toHaveAttribute("data-motion", "reduce");
  await page.goto("/#/jobs");
  await page.reload();
  await expect(page.getByTestId("loading-state")).toBeVisible();
  expect(await duration()).toBeLessThan(0.001);
  await open(page, "/settings", "Settings");
  await page.getByTestId("set-motion").selectOption("allow");
  await page.goto("/#/jobs");
  await page.reload();
  await expect(page.getByTestId("loading-state")).toBeVisible();
  expect(await duration()).toBeGreaterThan(1);
});

test("offline: after the first load every screen keeps working with the network cut", async ({ page, context }) => {
  await open(page, "/", "Overview");
  await expect(page.getByTestId("connectivity")).toHaveText("Works offline");
  await context.setOffline(true);
  await expect(page.getByTestId("connectivity")).toHaveText("You are offline — everything still works");
  for (const route of ROUTES) {
    await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: route.title, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: route.title })).toBeVisible();
  }
  // State changes still work and persist while offline.
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Discover Jobs" }).click();
  await page.getByTestId("shortlist-fake-ai-2").click();
  await expect(page.getByTestId("shortlist-fake-ai-2")).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("careertwin.workspace.v1") ?? "{}").shortlist)).toEqual(["fake-ai-2"]);
  await context.setOffline(false);
  await expect(page.getByTestId("connectivity")).toHaveText("Works offline");
});

test("loading, error and empty states are real screens", async ({ page, guard }) => {
  await open(page, "/settings", "Settings");
  // Error: every screen except Settings shows a recoverable error; Retry works after turning it off.
  await page.getByTestId("set-simulate").selectOption("error");
  await expect(page.getByTestId("qa-banner")).toContainText("QA simulation is on");
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Discover Jobs" }).click();
  await expect(page.getByTestId("error-state")).toContainText("The data could not be loaded.");
  await expect(page.getByTestId("error-state")).toContainText("Simulated load failure");
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("error-state")).toBeVisible();
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();

  // Empty: every screen has a designed empty state and nothing crashes.
  await page.getByTestId("set-simulate").selectOption("empty");
  const expected: Record<string, string> = {
    "Discover Jobs": "The job feed is empty.",
    Matching: "No match explanations are available.",
    "Profile Evidence": "The evidence vault is empty.",
    "Resume Studio": "No résumé variants yet.",
    Applications: "No applications are being tracked.",
    Experiments: "No experiments yet.",
    "Career Atlas": "No atlas projection is available.",
    "Career Simulator": "No transition scenarios are available.",
  };
  for (const [title, text] of Object.entries(expected)) {
    await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: title, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByTestId("empty-state").first()).toContainText(text);
  }
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Overview" }).click();
  await expect(page.getByTestId("overview-stats")).toContainText("0/0");
  // Simulations never touch the stored workspace.
  expect(await page.evaluate(() => localStorage.getItem("careertwin.workspace.v1"))).toBeNull();

  // Slow: a loading state is shown, then content.
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Settings" }).click();
  await page.getByTestId("set-simulate").selectOption("slow");
  await page.locator('nav[aria-label="Primary"]').getByRole("link", { name: "Discover Jobs" }).click();
  await page.reload();
  await expect(page.getByTestId("loading-state")).toContainText("Loading synthetic fixtures");
  await expect(page.getByTestId("job-count")).toHaveText("Showing 22 of 22 postings", { timeout: 15_000 });
  await open(page, "/settings", "Settings");
  await page.getByTestId("set-simulate").selectOption("none");
  await expect(page.getByTestId("qa-banner")).toHaveCount(0);
  expect(guard.errors).toEqual([]);
});

test("export everything, then erase local data (typed confirmation), with and without example data", async ({ page }) => {
  await open(page, "/jobs", "Discover Jobs");
  await page.getByTestId("shortlist-fake-fde-1").click();
  await open(page, "/settings", "Settings");
  await expect(page.getByTestId("storage-summary")).toContainText("1 shortlisted postings");
  const exported = await downloadText(page, () => page.getByTestId("export-all").click());
  expect(exported.name).toBe("careertwin-workspace-export.json");
  const data = JSON.parse(exported.text) as { export_schema: string; workspace: { shortlist: string[]; claims: unknown[]; applicationEvents: unknown[] } };
  expect(data.export_schema).toBe("careertwin-workspace-export/1");
  expect(data.workspace.shortlist).toEqual(["fake-fde-1"]);
  expect(data.workspace.claims).toHaveLength(16);
  expect(data.workspace.applicationEvents).toHaveLength(25);

  await page.getByTestId("wipe-open").click();
  await expect(page.getByTestId("wipe-confirm")).toBeDisabled();
  await page.getByTestId("wipe-text").fill("erase please");
  await expect(page.getByTestId("wipe-confirm")).toBeDisabled();
  await page.getByTestId("wipe-text").fill("ERASE");
  await page.getByTestId("wipe-confirm").click();
  await expect(page.getByTestId("storage-summary")).toContainText("0 shortlisted postings");
  await expect(page.getByTestId("storage-summary")).toContainText("16 claims");
  expect(await page.evaluate(() => localStorage.getItem("careertwin.workspace.v1"))).toBeNull();

  // Erase to an empty workspace: stays empty after reload.
  await page.getByTestId("wipe-open").click();
  await page.getByTestId("wipe-seed").selectOption("empty");
  await page.getByTestId("wipe-text").fill("erase");
  await page.getByTestId("wipe-confirm").click();
  await expect(page.getByTestId("storage-summary")).toContainText("0 claims, 0 résumé variants, 0 application events");
  await page.reload();
  await expect(page.getByTestId("storage-summary")).toContainText("0 claims, 0 résumé variants, 0 application events");
  await open(page, "/evidence", "Profile Evidence");
  await expect(page.getByTestId("empty-state")).toContainText("The evidence vault is empty.");
});

test("settings: adapters table shows contract basis and the rejected feed records; connectors are disabled", async ({ page }) => {
  await open(page, "/settings", "Settings");
  const jobs = page.getByTestId("adapter-jobs");
  await expect(jobs).toContainText("Frozen contract");
  await expect(jobs).toContainText("contracts/job-posting.schema.json (v0.1)");
  await jobs.getByText("3 problems").click();
  await expect(jobs).toContainText("hiring_probability — unknown property");
  await expect(jobs).toContainText("[6].url — required property is missing");
  await expect(page.getByTestId("adapter-matches")).toContainText("Proposed stand-in");
  const connectors = page.getByTestId("connectors");
  await expect(connectors.getByRole("button", { name: "Connect" })).toHaveCount(5);
  for (const b of await connectors.getByRole("button", { name: "Connect" }).all()) await expect(b).toBeDisabled();
  await expect(connectors).toContainText("Never scraping, never automated sign-in.");
  await page.getByTestId("set-theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("evidence vault: supported needs evidence, edits are audited, stale matches are flagged", async ({ page }) => {
  await open(page, "/evidence", "Profile Evidence");
  await page.getByTestId("claim-status-claim-skill-aws").selectOption("supported");
  await expect(page.getByTestId("evidence-notice")).toContainText("cannot be marked supported without at least one evidence reference");
  await expect(page.getByTestId("claim-status-claim-skill-aws")).toHaveValue("self-reported");
  await page.getByTestId("claim-status-claim-skill-security").selectOption("self-reported");
  await expect(page.getByTestId("audit-table")).toContainText("claim-skill-security: unverified -> self-reported");
  // Rewording a supported claim drops it to unverified.
  await page.getByTestId("claim-edit-claim-achievement-latency").click();
  await page.getByTestId("claim-text").fill("Reduced p95 latency of the fictional order API by 99%");
  await page.getByTestId("claim-save").click();
  await expect(page.getByTestId("claim-claim-achievement-latency")).toContainText("Unverified");
  await expect(page.getByTestId("evidence-notice")).toContainText("now unverified");
  // Matching does not recompute; it says the scores may be out of date.
  await open(page, "/matching", "Matching");
  await expect(page.getByTestId("stale-banner")).toContainText("These scores may be out of date.");
  // And the résumé line built on that claim now blocks export.
  await open(page, "/resume?variant=rv-base", "Resume Studio");
  await expect(page.getByTestId("bullet-b-latency")).toContainText("The linked claim is unverified");
});

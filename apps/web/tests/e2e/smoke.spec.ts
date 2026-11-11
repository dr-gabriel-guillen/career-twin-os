import { expect, test } from "@playwright/test";

test("app boots", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await page.screenshot({ path: "test-results/smoke-overview.png", fullPage: true });
  expect(errors).toEqual([]);
});

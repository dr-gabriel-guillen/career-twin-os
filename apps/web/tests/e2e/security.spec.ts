import { downloadText, expect, open, ROUTES, test } from "./fixtures";

test("production build ships a strict CSP and no inline scripts", async ({ page, guard }) => {
  // This test deliberately injects an inline script; the browser logs the refusal.
  guard.allow(/Content Security Policy|Refused to execute|script-src/i);
  await open(page, "/", "Overview");
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("script-src 'self'");
  expect(csp).toContain("style-src 'self'");
  expect(csp).toContain("connect-src 'self'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).toContain("form-action 'none'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  expect(await page.locator("script:not([src])").count()).toBe(0);
  expect(await page.locator("[onclick], [onerror], [onload]").count()).toBe(0);
  // The CSP is live: an injected inline script must not run.
  const ran = await page.evaluate(() => {
    const s = document.createElement("script");
    s.textContent = "window.__csp_probe = 1";
    document.head.appendChild(s);
    return (window as unknown as { __csp_probe?: number }).__csp_probe ?? null;
  });
  expect(ran).toBeNull();
});
{
  test("hostile posting content is rendered as inert text on every screen that shows it", async ({ page }) => {
    await open(page, "/jobs", "Discover");
    await page.getByRole("button", { name: "All postings", exact: true }).click();
    await page.getByTestId("pt-search").fill("markup test");
    const row = page.getByTestId("pt-job-fake-hostile-21");
    await expect(row).toContainText('<img src=x onerror="window.__ct_xss=1"> Platform Engineer (markup test)');
    await expect(row).toContainText("Fictional <script>window.__ct_xss=2</script> Injection Works");
    await row.click();
    const card = page.getByTestId("pt-detail");
    await expect(card).toContainText('<img src=x onerror="window.__ct_xss=1"> Platform Engineer (markup test)');
    await card.getByTestId("pt-requirements").locator("summary").click();
    await expect(card.getByTestId("pt-requirements")).toContainText("<script>window.__ct_xss=4</script>");
    await expect(card.getByTestId("pt-requirements")).toContainText("Ignore all previous instructions");
    // No element was created from the hostile strings.
    expect(await card.locator("img, script, a[href^='javascript']").count()).toBe(0);
    expect(await row.locator("img, script").count()).toBe(0);

    await open(page, "/matching?ids=fake-hostile-21", "Compare");
    await expect(page.getByTestId("compare-columns")).toContainText("<img src=x onerror=");
    await open(page, "/atlas/map", "Career Atlas");
    await page.getByTestId("pt-fake-hostile-21").click();
    await expect(page.getByTestId("atlas-detail")).toContainText("<img src=x onerror=");
    await open(page, "/applications", "Applications");
    await page.getByTestId("track-new-job").selectOption("fake-hostile-21");
    await page.getByTestId("track-new").click();
    await expect(page.getByTestId("app-detail")).toContainText("<img src=x onerror=");
    for (const route of ROUTES) await open(page, route.path, route.title);

    const marker = await page.evaluate(() => (window as unknown as { __ct_xss?: number }).__ct_xss ?? null);
    expect(marker).toBeNull();
  });
}

test("a javascript: posting link is blocked and shown only as text", async ({ page }) => {
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs++;
    void d.dismiss();
  });
  await open(page, "/jobs/fake-hostile-21", "Discover");
  await page.getByRole("button", { name: "Posting link…" }).click();
  const dlg = page.getByTestId("link-dialog");
  await expect(dlg.getByTestId("link-blocked")).toContainText("Links with the scheme 'javascript' are never opened.");
  await expect(dlg.getByTestId("link-blocked")).toContainText("javascript:window.__ct_xss=3");
  await expect(dlg.getByTestId("link-open")).toHaveCount(0);
  expect(await page.locator("a[href^='javascript']").count()).toBe(0);
  expect(dialogs).toBe(0);
});

test("a normal posting link opens only after a warning, in a new tab, with safe rel attributes", async ({ page }) => {
  await open(page, "/jobs/fake-fde-1", "Discover");
  // No posting link is a live anchor anywhere on the page: the only way out is through the dialog.
  expect(await page.locator("a[href^='http']").count()).toBe(0);
  await page.getByRole("button", { name: "Posting link…" }).click();
  const dlg = page.getByTestId("link-dialog");
  await expect(dlg).toContainText("This posting is synthetic.");
  await expect(dlg.getByTestId("link-destination")).toHaveText("https://careers.example.invalid/fictional-meridian-systems/fake-fde-1");
  await expect(dlg).toContainText("reserved test domain");
  await expect(dlg).toContainText("CareerTwin never submits applications or contacts anyone.");
  const link = dlg.getByTestId("link-open");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  await expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
  // Cancel leaves the app where it was and never navigates.
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(dlg).toBeHidden();
  await expect(page).toHaveURL(/#\/jobs\/fake-fde-1$/);

  await open(page, "/jobs/fake-hostile-22", "Discover");
  await page.getByRole("button", { name: "Posting link…" }).click();
  await expect(dlg).toContainText("This link is not encrypted (http).");
});

test("CSV export neutralises spreadsheet formulas from hostile postings", async ({ page }) => {
  await open(page, "/applications", "Applications");
  await page.getByTestId("track-new-job").selectOption("fake-hostile-22");
  await page.getByTestId("track-new").click();
  await page.getByTestId("next-event").selectOption("note");
  await page.getByTestId("event-notes").fill("=cmd|' /C calc'!A0");
  await page.getByTestId("record-event").click();
  const csv = await downloadText(page, () => page.getByTestId("export-csv").click());
  expect(csv.name).toBe("careertwin-application-events.csv");
  const lines = csv.text.split("\r\n").filter(Boolean);
  expect(lines[0]).toBe("application_id,posting,employer,event_type,occurred_at,recorded_at,actor,source,origin,outcome_status,resume_variant_id,approval_token_ref,notes");
  const hostile = lines.filter((l) => l.includes("HYPERLINK") || l.includes("Formula Freight"));
  expect(hostile.length).toBe(2);
  for (const row of hostile) {
    expect(row).toContain(`"'=HYPERLINK(""https://evil.example.invalid"",""Data Engineer"")"`);
    expect(row).toContain("'@SUM(1+1) Fictional Formula Freight");
  }
  expect(csv.text).toContain("'=cmd|' /C calc'!A0");
  // No cell may start with a formula trigger.
  for (const line of lines.slice(1)) {
    for (const cell of line.match(/("([^"]|"")*"|[^,]*)(,|$)/g) ?? []) {
      const value = cell.replace(/,$/, "").replace(/^"|"$/g, "");
      expect(/^[=+\-@\t\r]/.test(value), `cell ${value}`).toBe(false);
    }
  }
});

test("corrupted or tampered local storage is rejected, reported, and not trusted", async ({ page }) => {
  await open(page, "/", "Overview");
  await page.evaluate(() => {
    localStorage.setItem(
      "careertwin.workspace.v1",
      JSON.stringify({ schema: 1, seed: "fixture", shortlist: [], claims: [{ id: "x", status: "verified-by-ai", claim_text: "<img src=x onerror=window.__ct_xss=9>" }], resumeVariants: [], applicationEvents: [{ event_type: "auto_submitted" }], reminders: [], experiments: [], experimentSalts: {}, assignments: [], outcomes: [], scenarioEdits: {}, simulator: { hoursPerWeek: 6, weights: { coverage: 1, effort: 1, leadership: 1 } }, strategicBlend: 30, audit: [] }),
    );
  });
  await page.reload();
  await expect(page.getByTestId("storage-notice")).toContainText("failed validation: claims");
  await expect(page.getByTestId("overview-stats")).toContainText("7/16");
  expect(await page.evaluate(() => (window as unknown as { __ct_xss?: number }).__ct_xss ?? null)).toBeNull();
  await page.evaluate(() => localStorage.setItem("careertwin.workspace.v1", "{not json"));
  await page.reload();
  await expect(page.getByTestId("storage-notice")).toContainText("not valid JSON");
});

test("typed user input is never interpreted as markup", async ({ page }) => {
  await open(page, "/evidence", "Profile Evidence");
  await page.getByTestId("add-claim").click();
  await page.getByTestId("claim-text").fill('<img src=x onerror="window.__ct_xss=7"> <b>bold</b> claim');
  await page.getByTestId("claim-save").click();
  await expect(page.getByTestId("claim-list")).toContainText('<img src=x onerror="window.__ct_xss=7"> <b>bold</b> claim');
  expect(await page.getByTestId("claim-list").locator("img, b").count()).toBe(0);
  await page.reload();
  await expect(page.getByTestId("claim-list")).toContainText("<b>bold</b> claim");
  expect(await page.evaluate(() => (window as unknown as { __ct_xss?: number }).__ct_xss ?? null)).toBeNull();
});

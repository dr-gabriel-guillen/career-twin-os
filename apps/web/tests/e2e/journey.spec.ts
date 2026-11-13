import { downloadText, expect, open, test } from "./fixtures";

/**
 * The synthetic vertical slice, driven end to end in a real browser:
 * discover -> compare -> résumé (editor and tailoring) -> applications -> experiment -> atlas -> simulation.
 */
test("full synthetic journey", async ({ page }) => {
  test.setTimeout(120_000);

  // ---- 1. Discover: search, filter, decide, shortlist
  await open(page, "/jobs", "Discover");
  // Malformed feed records are rejected by the adapters and reported, never shown or repaired.
  await expect(page.getByTestId("adapter-banner")).toContainText("3 upstream records were rejected");
  await page.getByRole("button", { name: "All postings", exact: true }).click();
  await expect(page.getByTestId("job-count")).toHaveText("22 postings");
  await page.getByTestId("pt-search").fill("forward deployed");
  await expect(page.getByTestId("job-count")).toHaveText("3 postings");
  await page.getByTestId("filters").locator("summary").click();
  await page.getByTestId("filter-dupes").check();
  await expect(page.getByTestId("job-count")).toHaveText("2 postings");
  await page.getByTestId("pt-job-fake-fde-1").click();
  const detail = page.getByTestId("pt-detail");
  await expect(detail.getByRole("heading", { level: 2 })).toHaveText("Forward Deployed Engineer");
  await expect(detail).toContainText("Pay not disclosed (unknown, not zero)");
  await expect(page.getByTestId("pt-why-list")).toBeVisible();
  await expect(page.getByTestId("pt-blockers")).toContainText("Customer delivery");
  await expect(detail).toContainText("not a probability of being hired");
  await expect(detail.locator(".ct-scores")).toContainText("86");
  await detail.getByText("How the engine computed these").click();
  await expect(detail).toContainText("Unknown inputs: Compensation verified");
  await page.getByTestId("pt-shortlist").click();
  await expect(page.getByTestId("pt-shortlist")).toHaveAttribute("aria-pressed", "true");

  // ---- 2. Compare: two separate scores, side by side; ordering never changes a score
  await open(page, "/matching?ids=fake-fde-1,fake-cto-3", "Compare");
  await expect(page.getByTestId("compare-columns").locator("article")).toHaveCount(2);
  await expect(page.getByTestId("near-fake-fde-1")).toHaveAttribute("aria-label", "Near-term: 86 of 100");
  await expect(page.getByTestId("strat-fake-fde-1")).toHaveAttribute("aria-label", "Strategic: 60.5 of 100");
  const firstNear = await page.getByTestId("compare-table").locator("tbody tr").first().innerText();
  await page.getByTestId("compare-sort").selectOption("strategic");
  const firstStrategic = await page.getByTestId("compare-table").locator("tbody tr").first().innerText();
  expect(firstStrategic).not.toBe(firstNear);
  expect(firstStrategic).toContain("Field CTO");
  await expect(page.getByTestId("near-fake-fde-1")).toHaveAttribute("aria-label", "Near-term: 86 of 100");

  // ---- 3. Résumé Studio: tailor in the editor, inflate (blocked), repair, approve, export
  await open(page, "/resume?job=fake-fde-1", "Résumé Studio");
  await expect(page.getByTestId("tailor-banner")).toContainText("Forward Deployed Engineer");
  await page.getByTestId("tailor-create").click();
  const editor = page.getByTestId("variant-editor");
  await expect(editor).toContainText("For Forward Deployed Engineer");
  await page.getByTestId("bullet-edit-b-latency").click();
  await page.getByTestId("bullet-text").fill("Certified engineer who reduced p95 latency of the fictional order API by 83%");
  await page.getByTestId("bullet-save").click();
  const inflated = page.getByTestId("bullet-b-latency");
  await expect(inflated).toContainText("Contains figure(s) not found in the linked claims: 83%");
  await expect(inflated).toContainText("Mentions a credential or qualification not found in the linked claims: certified");
  await page.getByRole("tab", { name: /Review & export/ }).click();
  await expect(page.getByTestId("export-blockers")).toContainText("2 blocking truthfulness issues must be resolved");
  await expect(page.getByTestId("approve-open")).toBeDisabled();
  await expect(page.getByTestId("export-txt")).toBeDisabled();
  // Compare shows the inflation as a diff against the parent variant.
  await page.getByRole("tab", { name: "Compare" }).click();
  await expect(page.getByTestId("compare-view")).toContainText("Reworded line");
  await expect(page.getByTestId("compare-view").locator("ins").first()).toBeVisible();
  // Repair: put the true figure back.
  await page.getByRole("tab", { name: "Edit" }).click();
  await page.getByTestId("bullet-edit-b-latency").click();
  await page.getByTestId("bullet-text").fill("Reduced p95 latency of the fictional order API by 38%");
  await page.getByTestId("bullet-save").click();
  await expect(page.getByTestId("bullet-b-latency")).not.toContainText("Blocks export");
  // A free-text line with no evidence link also blocks.
  const free = editor.locator('[data-testid^="free-text-sec"]').first();
  await free.fill("Led a team of 40 across three continents");
  await editor.locator('[data-testid^="free-text-btn-sec"]').first().click();
  await expect(editor).toContainText("Not linked to any claim in the evidence vault.");
  await editor.locator("li.app-bullet--block").getByRole("button", { name: "Remove" }).click();
  await page.getByRole("tab", { name: /Review & export/ }).click();
  await expect(page.getByTestId("approve-open")).toBeEnabled();
  await expect(page.getByTestId("export-txt")).toBeDisabled(); // not approved yet
  await page.getByTestId("approve-open").click();
  await expect(page.getByTestId("approve-confirm")).toBeDisabled();
  await page.getByTestId("approve-check").check();
  await page.getByTestId("approve-confirm").click();
  await expect(page.getByTestId("export-ready")).toBeVisible();
  await expect(page.getByTestId("locked-banner")).toContainText("Approved snapshot — locked.");
  const exported = await downloadText(page, () => page.getByTestId("export-txt").click());
  expect(exported.name).toMatch(/^rv-[0-9a-f]+\.txt$/);
  expect(exported.text).toContain("Reduced p95 latency of the fictional order API by 38%");
  expect(exported.text).not.toContain("83%");
  expect(exported.text).toContain("Synthetic demo export");

  // ---- 4. Applications: the approval gate cannot be skipped
  await open(page, "/applications", "Applications");
  await page.getByTestId("track-new-job").selectOption("fake-fde-8");
  await page.getByTestId("track-new").click();
  const appDetail = page.getByTestId("app-detail");
  await expect(appDetail.getByTestId("timeline")).toContainText("Saved");
  await expect(appDetail.getByTestId("timeline")).toContainText("You recorded this here");
  // From "saved" the only next steps are drafted / withdrawn / note.
  await expect(page.getByTestId("next-event").locator("option")).toHaveText(["Choose…", "Résumé variant drafted", "Withdrawn", "Note"]);
  await page.getByTestId("next-event").selectOption("drafted");
  await page.getByTestId("event-variant").selectOption("rv-base");
  await page.getByTestId("record-event").click();
  await expect(appDetail.getByTestId("timeline")).toContainText("Résumé variant drafted");
  // Approving with an unapproved résumé is refused.
  await page.getByTestId("next-event").selectOption("approved");
  await page.getByTestId("record-event").click();
  await expect(page.getByTestId("event-error")).toContainText("not approved for export yet");
  await page.getByTestId("next-event").selectOption("withdrawn");
  await page.getByTestId("record-event").click();
  await expect(appDetail).toContainText("Closed: withdrawn.");

  // ---- 4b. Tailor journey: choose your own lines, review the diff, approve the exact text, track it
  await open(page, "/jobs/fake-fde-7", "Discover");
  await page.getByTestId("pt-tailor").click();
  await expect(page.getByRole("heading", { level: 1, name: "Tailor résumé" })).toBeVisible();
  await expect(page.getByTestId("pt-coverage")).toBeVisible();
  await page.getByRole("button", { name: "Next: choose content" }).click();
  for (const line of await page.locator(".pt-line").filter({ has: page.locator('input[type="checkbox"]') }).all()) {
    if (/unverified|contradicted|no claim linked|missing claim/.test((await line.innerText()).toLowerCase())) await line.locator('input[type="checkbox"]').uncheck();
  }
  await page.getByRole("button", { name: "Next: review changes" }).click();
  await expect(page.getByTestId("pt-diff-summary")).toBeVisible();
  await page.getByTestId("pt-save-draft").click();
  await expect(page.getByTestId("pt-document")).toBeVisible();
  await expect(page.getByTestId("pt-approve")).toBeDisabled();
  await expect(page.getByTestId("pt-export")).toHaveCount(0); // nothing is exportable before approval
  await page.getByTestId("pt-confirm").check();
  await page.getByTestId("pt-approve").click();
  await expect(page.getByTestId("pt-approved")).toBeVisible();
  const tailored = await downloadText(page, () => page.getByTestId("pt-export").click());
  expect(tailored.text).toContain("Synthetic demo export");
  await page.getByTestId("pt-record").click();
  await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
  await expect(appDetail.getByRole("heading", { level: 2 })).toHaveText("Forward Deployed Engineer, Public Sector");
  await expect(page.getByTestId("next-action")).toContainText("Apply on the employer’s site yourself, then record it");
  await expect(page.getByTestId("approval-ref")).toBeVisible();
  await page.getByTestId("record-applied").click();
  await expect(page.getByTestId("submit-confirm")).toBeDisabled();
  await page.getByTestId("submit-check").check();
  await page.getByTestId("submit-confirm").click();
  await expect(appDetail.getByTestId("timeline")).toContainText("Marked as submitted (manual)");
  await expect(page.getByTestId("next-action")).toContainText("Wait for a reply");
  await page.getByTestId("followup-template").click();
  await expect(page.getByTestId("followup-text")).toContainText("CareerTwin does not write with AI and cannot send email");
  await page.getByTestId("rem-date").fill("2026-12-01");
  await page.getByTestId("rem-add").click();
  await expect(appDetail).toContainText("Follow up: Forward Deployed Engineer, Public Sector");
  // Silence is surfaced as an unknown outcome on Overview, never as a rejection.
  await open(page, "/", "Overview");
  await expect(page.getByText("No reply recorded for 28 days")).toBeVisible();

  // ---- 5. Experiment: design, lock, assign, simulate, read results
  await open(page, "/experiments", "Experiments");
  await page.getByTestId("exp-new").click();
  await expect(page.getByTestId("exp-problems")).toBeVisible();
  await expect(page.getByTestId("exp-lock")).toBeDisabled();
  await page.getByTestId("exp-title").fill("Synthetic: summary line first or last");
  await page.getByTestId("exp-hypothesis").fill("Putting the summary line first changes the recruiter screen rate within 21 days.");
  await page.getByTestId("exp-variants").fill("summary-first, summary-last");
  await page.getByTestId("exp-plan").fill("Difference in proportions with a 95% interval on the primary metric; censored units excluded.");
  await expect(page.getByTestId("exp-problems")).toHaveCount(0);
  await page.getByRole("tab", { name: /Assignment/ }).click();
  await expect(page.getByTestId("assign-blocked")).toContainText("Lock the design first.");
  await page.getByRole("tab", { name: "Design" }).click();
  await page.getByTestId("exp-lock").click();
  await page.getByTestId("lock-dialog-confirm").click();
  await expect(page.getByRole("tab", { name: /Assignment/ })).toHaveAttribute("aria-selected", "true");
  await page.getByTestId("assign-all").click();
  await expect(page.getByTestId("assign-table").locator("tbody tr")).toHaveCount(16);
  await expect(page.getByTestId("assign-all")).toBeDisabled(); // nothing left: no unit is assigned twice
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.getByTestId("exp-locked")).toBeVisible();
  await expect(page.getByTestId("exp-title")).toBeDisabled();
  await expect(page.getByTestId("exp-hypothesis")).toBeDisabled();
  await page.getByRole("tab", { name: "Results" }).click();
  await expect(page.getByTestId("evidence-level")).toContainText("No observed outcomes yet.");
  await page.getByTestId("sim-rate-summary-first").fill("90");
  await page.getByTestId("sim-rate-summary-last").fill("5");
  await page.getByTestId("simulate").click();
  // Even with a huge simulated gap, 16 units is below the planned sample: no winner is declared.
  await expect(page.getByTestId("evidence-level")).toContainText(/Insufficient evidence|Inconclusive/);
  await expect(page.getByTestId("evidence-level")).toContainText("No variant is declared better.");
  await expect(page.getByTestId("exp-results")).not.toContainText(/winner/i);
  await expect(page.getByTestId("results-synthetic")).toContainText("generated by a seeded simulator, not observed");
  await page.getByRole("tab", { name: "Sample size" }).click();
  await expect(page.getByTestId("power-needed")).toContainText("About 199 units per variant (398 in total).");
  await expect(page.getByTestId("power-mde")).toContainText("With 10 units per variant");

  // ---- 6. Atlas: transitions with stated uncertainty, then the full map
  await open(page, "/atlas", "Career Atlas");
  await expect(page.getByTestId("pt-uncertainty")).toContainText("explains 36% of the variation in the input skill vectors");
  await expect(page.getByTestId("pt-uncertainty")).toContainText("Distance on the map is not fit");
  await expect(page.getByTestId("transition-map")).toBeVisible();
  await page.getByRole("link", { name: "Full map" }).click();
  await expect(page.getByTestId("explained-variance")).toContainText("36%");
  await expect(page.getByTestId("trustworthiness")).toContainText("Trustworthiness 0.87");
  await page.getByTestId("pt-fake-cto-3").click();
  await expect(page.getByTestId("atlas-detail")).toContainText("Field CTO");
  await expect(page.getByTestId("atlas-detail")).toContainText("This map position is not a score.");
  await page.getByTestId("atlas-projection").selectOption("interpretable-axes-v1");
  await expect(page.getByTestId("distortion-banner")).toContainText("hand-defined");
  await expect(page.getByTestId("path-scenario-field-cto")).toBeVisible();

  // ---- 7. Simulator: edit a plan, enter a cost, re-weight
  await open(page, "/simulator", "Career Simulator");
  await page.getByTestId("scenario-pick-scenario-field-cto").click();
  await expect(page.getByTestId("hours-scenario-field-cto")).toHaveText("590 h");
  await expect(page.getByTestId("cost-scenario-field-cto")).toHaveText("Not entered");
  await page.getByTestId("action-hours-0").fill("100");
  await page.getByTestId("action-cost-0").fill("1200");
  await expect(page.getByTestId("hours-scenario-field-cto")).toHaveText("490 h");
  await expect(page.getByTestId("cost-scenario-field-cto")).toHaveText("1,200 (partial)");
  await expect(page.getByTestId("scenario-edited")).toBeVisible();
  await page.getByTestId("sim-hours").fill("12");
  await expect(page.getByTestId("months-scenario-field-cto")).toHaveText("9.4 months");

  // ---- 8. Everything survives a reload (local persistence only)
  await page.reload();
  await expect(page.getByTestId("hours-scenario-field-cto")).toHaveText("490 h");
  await open(page, "/", "Overview");
  await expect(page.getByTestId("overview-stats")).toContainText("3 approved");
  await open(page, "/experiments", "Experiments");
  await expect(page.getByTestId("exp-list")).toContainText("Synthetic: summary line first or last");
  await open(page, "/jobs/fake-fde-1", "Discover");
  await expect(page.getByTestId("pt-shortlist")).toHaveAttribute("aria-pressed", "true");
});

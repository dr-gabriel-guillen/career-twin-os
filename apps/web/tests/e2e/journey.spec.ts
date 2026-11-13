import { downloadText, expect, open, test } from "./fixtures";

/**
 * The synthetic vertical slice, driven end to end in a real browser:
 * feed -> match -> résumé -> application -> experiment -> atlas -> simulation.
 */
test("full synthetic journey", async ({ page }) => {
  test.setTimeout(120_000);

  // ---- 1. Discover: search, filter, shortlist
  await open(page, "/jobs", "Discover Jobs");
  await expect(page.getByTestId("job-count")).toHaveText("Showing 22 of 22 postings");
  await expect(page.getByTestId("jobs-rejected")).toContainText("3 feed records did not match the JobPosting contract");
  await page.getByTestId("job-search").fill("forward deployed");
  await expect(page.getByTestId("job-count")).toHaveText("Showing 3 of 22 postings");
  await page.getByTestId("job-collapse").check();
  await expect(page.getByTestId("job-count")).toHaveText("Showing 2 of 22 postings");
  await expect(page.getByTestId("job-fake-fde-1")).toContainText("Possible duplicate (2 postings)");
  await expect(page.getByTestId("job-fake-fde-1")).toContainText("Marked active at capture");
  await expect(page.getByTestId("comp-fake-fde-1")).toContainText("Not disclosed (unknown — not treated as zero)");
  await page.getByTestId("shortlist-fake-fde-1").click();
  await expect(page.getByTestId("shortlist-fake-fde-1")).toHaveAttribute("aria-pressed", "true");

  // ---- 2. Matching: explanation, constraints, unknowns, blend
  await page.getByTestId("job-fake-fde-1").getByRole("link", { name: "Explain match" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Matching" })).toBeVisible();
  const detail = page.getByTestId("match-detail");
  await expect(detail).toContainText("Relative heuristic, not a hiring probability");
  await expect(page.getByTestId("near-term-score")).toContainText("86");
  await expect(page.getByTestId("strategic-score")).toContainText("60.5");
  await expect(page.getByTestId("hard-constraints")).toContainText("Pass");
  await expect(page.getByTestId("skill-gaps")).toContainText("Customer delivery");
  await expect(page.getByTestId("unknown-inputs")).toContainText("Compensation verified");
  await expect(page.getByTestId("strategic")).toContainText("Unknown and excluded (not counted as zero): Compensation");
  const firstBefore = await page.getByTestId("match-list").locator("li").first().innerText();
  await page.getByTestId("blend").fill("100");
  const firstAfter = await page.getByTestId("match-list").locator("li").first().innerText();
  expect(firstAfter).not.toBe(firstBefore);
  expect(firstAfter).toContain("Field CTO");
  // Scores themselves did not change, only the order.
  await expect(page.getByTestId("near-term-score")).toContainText("86");

  // ---- 3. Résumé: tailor, inflate (blocked), repair, approve, export
  await page.getByTestId("detail-tailor").click();
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

  // ---- 4. Applications: save, draft, approve, record manual submission
  await open(page, "/applications", "Applications");
  await page.getByTestId("new-app-job").selectOption("fake-fde-8");
  await page.getByTestId("new-app-btn").click();
  const appDetail = page.getByTestId("app-detail");
  await expect(appDetail.getByTestId("timeline")).toContainText("Saved");
  // Approval cannot be skipped: from "saved" the only next steps are drafted / withdrawn / note.
  await expect(page.getByTestId("next-event").locator("option")).toHaveText(["Choose…", "Résumé variant drafted", "Withdrawn", "Note"]);
  await page.getByTestId("next-event").selectOption("drafted");
  await page.getByTestId("draft-variant").selectOption({ label: "Base résumé (from claims) (v1) — not approved" });
  await page.getByTestId("record-event").click();
  // Approving with an unapproved variant is refused.
  await page.getByTestId("next-event").selectOption("approved");
  await expect(page.getByTestId("approval-state")).toContainText("not approved yet");
  await page.getByTestId("record-event").click();
  await expect(page.getByTestId("app-error")).toContainText("not ready");
  // Withdraw this one and use a properly approved variant on a fresh application.
  await page.getByTestId("next-event").selectOption("withdrawn");
  await page.getByTestId("record-event").click();
  await expect(page.getByTestId("col-closed")).toContainText("Withdrawn");

  await page.getByTestId("new-app-job").selectOption("fake-fde-7");
  await page.getByTestId("new-app-btn").click();
  await page.getByTestId("next-event").selectOption("drafted");
  await page.getByTestId("draft-variant").selectOption({ label: "For Forward Deployed Engineer (v2) — approved" });
  await page.getByTestId("record-event").click();
  await page.getByTestId("next-event").selectOption("approved");
  await expect(page.getByTestId("approval-state")).toContainText("is approved");
  await page.getByTestId("record-event").click();
  await page.getByTestId("next-event").selectOption("submitted");
  await page.getByTestId("event-notes").fill("Applied on the fictional employer site.");
  await page.getByTestId("record-event").click();
  await expect(page.getByTestId("submit-dialog")).toContainText("CareerTwin does not submit anything.");
  await expect(page.getByTestId("submit-dialog-confirm")).toBeDisabled();
  await page.getByTestId("submit-check").check();
  await page.getByTestId("submit-dialog-confirm").click();
  await expect(appDetail.getByTestId("timeline")).toContainText("Marked as submitted (manual)");
  await expect(appDetail.getByTestId("timeline")).toContainText("Applied on the fictional employer site.");
  await expect(page.getByTestId("col-submitted")).toContainText("Forward Deployed Engineer, Public Sector");
  // Reminder
  await page.getByTestId("rem-date").fill("2026-12-01");
  await page.getByTestId("rem-text").fill("Check for a reply");
  await page.getByTestId("rem-add").click();
  await expect(page.getByTestId("reminders")).toContainText("Check for a reply");
  // Missing feedback tab lists the fixture application waiting 28 days.
  await page.getByRole("tab", { name: /Missing feedback/ }).click();
  await expect(page.getByTestId("missing-feedback")).toContainText("No reply recorded · 28 days");

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

  // ---- 6. Atlas: distortion banner, select a point, switch projection
  await open(page, "/atlas", "Career Atlas");
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
  await expect(page.getByTestId("overview-stats")).toContainText("2 approved by you");
  await open(page, "/experiments", "Experiments");
  await expect(page.getByTestId("exp-list")).toContainText("Synthetic: summary line first or last");
  await open(page, "/jobs", "Discover Jobs");
  await expect(page.getByTestId("shortlist-fake-fde-1")).toHaveAttribute("aria-pressed", "true");
});

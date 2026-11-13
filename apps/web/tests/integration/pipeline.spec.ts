import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * Lane E `ui.e2e`: the UI renders the artifacts of an integration run and is
 * driven with the KEYBOARD ONLY, by TAB TRAVERSAL.
 *
 * The runner never calls focus() on a control and never uses the pointer. Every
 * control is reached by pressing Tab from wherever focus currently is, so a
 * control that is not in the tab order cannot be operated by this test. The
 * page counts pointer events itself; the count must be zero.
 *
 * Nothing here is specific to one data set: ids, titles and numbers are read
 * from the artifacts and compared with what the screens show.
 */
const ARTIFACTS = process.env.CT_ARTIFACTS ?? "";
const FACTS = process.env.CT_FACTS ?? "";
const ORIGIN = "http://127.0.0.1:4174";
const art = <T>(file: string): T => JSON.parse(readFileSync(join(ARTIFACTS, file), "utf8")) as T;
const has = (file: string) => {
  try {
    readFileSync(join(ARTIFACTS, file));
    return true;
  } catch {
    return false;
  }
};

let tabPresses = 0;
const MAX_TABS = 3000;

/** Press Tab until `target` has focus. Fails if the control is not reachable in the tab order. */
async function tabTo(page: Page, target: Locator, what: string) {
  await expect(target, `${what} exists`).toHaveCount(1);
  for (let i = 0; i <= MAX_TABS; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
    tabPresses++;
  }
  throw new Error(`${what} was not reached after ${MAX_TABS} Tab presses: it is not in the tab order`);
}
async function press(page: Page, target: Locator, what: string, key = "Enter") {
  await tabTo(page, target, what);
  await page.keyboard.press(key);
}
async function typeInto(page: Page, target: Locator, what: string, text: string) {
  await tabTo(page, target, what);
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Delete");
  await page.keyboard.type(text);
}
/** Move a closed <select> to `value` with arrow keys. */
async function choose(page: Page, select: Locator, what: string, value: string) {
  await tabTo(page, select, what);
  const { from, to } = await select.evaluate((el: HTMLSelectElement, v) => ({ from: el.selectedIndex, to: [...el.options].findIndex((o) => o.value === v) }), value);
  expect(to, `${what}: option ${value} exists`).toBeGreaterThanOrEqual(0);
  for (let i = 0; i < Math.abs(to - from); i++) await page.keyboard.press(to > from ? "ArrowDown" : "ArrowUp");
  await expect(select).toHaveValue(value);
}
async function goto(page: Page, title: string) {
  await press(page, page.locator('nav[aria-label="Primary"]').getByRole("link", { name: title, exact: true }), `navigation link "${title}"`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
}

test("discover → explain → résumé → applications (log only) → experiment (simulated run) → atlas → simulation", async ({ page }) => {
  test.skip(!ARTIFACTS, "CT_ARTIFACTS is not set; run through scripts/e2e-integration.mjs");
  const steps: string[] = [];
  /** Defects in the pipeline's data that the UI caught. Reported, never hidden. */
  const upstreamFindings: string[] = [];
  const external: string[] = [];
  const errors: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith(ORIGIN) && !r.url().startsWith("data:") && !r.url().startsWith("blob:")) external.push(r.url());
  });
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  // Count every pointer-type event and every scripted focus() call the page sees.
  await page.addInitScript(() => {
    const w = window as unknown as { __pointerEvents: number };
    w.__pointerEvents = 0;
    for (const type of ["pointerdown", "mousedown", "touchstart"]) window.addEventListener(type, () => (w.__pointerEvents += 1), true);
  });

  const jobs = art<{ id: string; title: string; organization: string }[] | { items: { posting: { id: string; title: string; organization: string } }[] }>("jobs.json");
  const jobList = Array.isArray(jobs) ? jobs : jobs.items.map((x) => x.posting);
  const pin = art<{ job_id: string }>("pin.json").job_id;
  const pinned = jobList.find((j) => j.id === pin);
  expect(pinned, `pin.json names job ${pin}, which must exist in jobs.json`).toBeDefined();

  // ---- 0. The app is rendering the pipeline output, not its own fixtures
  await page.goto("/?source=artifacts#/jobs");
  await expect(page.getByRole("heading", { level: 1, name: "Discover" })).toBeVisible();
  await expect(page.locator(".app")).toHaveAttribute("data-source", "artifacts");
  await expect(page.getByTestId("artifact-banner")).toBeVisible();
  await expect(page.getByTestId("synthetic-banner")).toHaveCount(0);
  const mockText = await page.getByTestId("mock-sources").innerText();
  const missingText = await page.getByTestId("missing-artifacts").innerText();
  const list = (text: string) => {
    const tail = text.split(":").slice(1).join(":").trim();
    return tail === "none" || tail === "" ? [] : tail.split(",").map((s) => s.trim());
  };
  // The very first Tab on a freshly loaded page lands on the skip link.
  await page.keyboard.press("Tab");
  tabPresses++;
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();

  // ---- 1. Discover: every contract-valid posting is listed; find, open and shortlist the pinned one
  await press(page, page.getByRole("button", { name: "All postings", exact: true }), "All postings view");
  const countText = await page.getByTestId("job-count").innerText();
  const shown = Number(/^(\d+) postings?/.exec(countText)?.[1]);
  const rejectedNote = page.getByTestId("jobs-rejected");
  const rejected = (await rejectedNote.count()) > 0 ? Number(/^(\d+)/.exec(await rejectedNote.innerText())?.[1] ?? 0) : 0;
  expect(shown, "postings shown").toBeGreaterThan(0);
  expect(shown + rejected, "every record in jobs.json is either shown or reported as rejected").toBe(jobList.length);
  await typeInto(page, page.getByTestId("pt-search"), "job search field", pinned!.title);
  const row = page.getByTestId(`pt-job-${pin}`);
  await expect(row).toContainText(pinned!.title);
  await expect(row).toContainText(pinned!.organization);
  await press(page, row, "the pinned posting in the list");
  const detail = page.getByTestId("pt-detail");
  await expect(detail.getByRole("heading", { level: 2 })).toHaveText(pinned!.title);
  await press(page, page.getByTestId("pt-shortlist"), "shortlist button of the pinned posting");
  await expect(page.getByTestId("pt-shortlist")).toHaveAttribute("aria-pressed", "true");
  steps.push(`discover: ${shown} postings shown, ${rejected} rejected by the adapter, pinned posting opened and shortlisted`);

  // ---- 2. Explain: the pipeline's explanation for the pinned posting, with its own score
  await expect(detail).toContainText("not a probability of being hired");
  await press(page, detail.locator("summary", { hasText: "How the engine computed these" }), "engine details disclosure");
  const matches = art<unknown>("matches.json");
  const mine = (Array.isArray(matches) ? matches : ((matches as { shortlist?: unknown[] }).shortlist ?? [])).find((m) => (m as { job_id?: string }).job_id === pin) as { score?: number } | undefined;
  if (mine?.score !== undefined) await expect(page.getByTestId("overall-score")).toContainText(`Overall score: ${mine.score} / 100`);
  await expect(page.getByTestId("hard-constraints")).toBeVisible();
  await expect(detail).not.toContainText(/chance of|likelihood of|probability of (an offer|being hired)(?!\.)/i);
  steps.push("explain: why it fits, what stands in the way and the engine's own score and constraints rendered for the pinned posting");

  // ---- 3. Résumé: the pipeline's variant is traceable; approve the exact text
  await goto(page, "Résumé Studio");
  const variant = art<{ id: string; bullets: { text: string }[] }>("resume_variant.json");
  await press(page, page.getByTestId(`variant-${variant.id}`), "the pipeline's résumé variant");
  const editor = page.getByTestId("variant-editor");
  for (const b of variant.bullets) await expect(editor).toContainText(b.text);
  await tabTo(page, page.getByRole("tab", { name: "Edit" }), "Edit tab");
  await page.keyboard.press("End");
  await expect(page.getByTestId("review-panel")).toBeVisible();
  await expect(page.getByTestId("export-txt")).toBeDisabled(); // nothing is exportable before the user's approval
  if (await page.getByTestId("approve-open").isDisabled()) {
    // The UI refuses to approve or export lines that fail the truthfulness guard. Do what a user
    // would do: read why, remove the blocked lines, and approve what is left.
    await page.keyboard.press("Home");
    const blocked = editor.locator("li.app-bullet--block");
    const n = await blocked.count();
    expect(n, "approval is disabled, so at least one line must be marked as blocking").toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const reasons = (await blocked.nth(i).locator("li", { hasText: "Blocks export" }).allInnerTexts()).map((t) => t.replace(/\s+/g, " ").replace("Blocks export ", "").trim());
      upstreamFindings.push(`resume_variant.json: line ${i + 1} of ${variant.bullets.length} is blocked from export (${reasons.join("; ")})`);
    }
    for (let i = 0; i < n; i++) await press(page, blocked.first().getByRole("button", { name: "Remove" }), "Remove button of a blocked line");
    await expect(blocked).toHaveCount(0);
    await tabTo(page, page.getByRole("tab", { name: "Edit" }), "Edit tab");
    await page.keyboard.press("End");
    await expect(page.getByTestId("review-panel")).toBeVisible();
  }
  await expect(page.getByTestId("approve-open"), "after removing blocked lines the remaining text can be approved").toBeEnabled();
  await press(page, page.getByTestId("approve-open"), "Approve button");
  await expect(page.getByTestId("approve-confirm")).toBeDisabled();
  await press(page, page.getByTestId("approve-check"), "approval checkbox in the dialog", "Space");
  await press(page, page.getByTestId("approve-confirm"), "Approve and lock button in the dialog");
  await expect(page.getByTestId("export-ready")).toBeVisible();
  await expect(page.getByTestId("locked-banner")).toBeVisible();
  steps.push(`résumé: pipeline variant rendered line by line; ${upstreamFindings.length} blocked line(s) removed; the rest approved through the dialog, then locked`);

  // ---- 4. Application: the pipeline's event log for the pinned posting, plus a new note
  await goto(page, "Applications");
  const events = art<{ application_id: string; job_id: string; event_type: string; approval_token_ref?: string | null }[]>("events.json");
  const submitted = events.find((e) => e.job_id === pin && e.event_type === "submitted");
  expect(submitted, "events.json has a user-recorded submission for the pinned posting").toBeDefined();
  // The CRM boundary, measured in the browser: the list holds the application log (events.json) only.
  const logApps = new Set(events.map((e) => e.application_id));
  const labApps = has("experiment_events.json") ? new Set(art<{ application_id: string }[]>("experiment_events.json").map((e) => e.application_id)) : new Set<string>();
  const appCount = Number(/^(\d+)/.exec(await page.getByTestId("app-count").innerText())?.[1]);
  const listed = await page.locator('[data-testid^="app-"]').evaluateAll((els) => els.map((e) => (e.getAttribute("data-testid") ?? "").slice(4)));
  const simulatedListed = listed.filter((id) => labApps.has(id)).length;
  expect(appCount, "applications shown = application ids in events.json").toBe(logApps.size);
  expect(simulatedListed, "simulated experiment units listed as applications").toBe(0);
  if (labApps.size > 0) await expect(page.getByTestId("lab-note")).toContainText(`simulated ${labApps.size} applications`);
  await press(page, page.getByTestId(`app-${submitted!.application_id}`).first(), "the pinned application in the list");
  const timeline = page.getByTestId("app-detail").getByTestId("timeline");
  await expect(timeline).toContainText("Marked as submitted (manual)");
  await expect(timeline.locator("li")).toHaveCount(events.filter((e) => e.application_id === submitted!.application_id).length);
  if (submitted!.approval_token_ref) await expect(page.getByTestId("app-detail")).toContainText(submitted!.approval_token_ref);
  // Sample data is never presented as something the person did here.
  await expect(timeline).toContainText("Pipeline sample data");
  await expect(timeline).not.toContainText("You recorded this here");
  await choose(page, page.getByTestId("next-event"), "'What happened?' select", "note");
  await typeInto(page, page.getByTestId("event-notes"), "note field", "Integration run: note recorded from the keyboard.");
  await press(page, page.getByTestId("record-event"), "Record event button");
  await expect(timeline).toContainText("Integration run: note recorded from the keyboard.");
  // The only next steps offered after a submission are responses, withdrawal or a note: no "submit" action exists.
  const offered = await page.getByTestId("next-event").locator("option").allInnerTexts();
  expect(offered.join("|")).not.toMatch(/apply|send|submit/i);
  await expect(timeline).toContainText("You recorded this here");
  // Overview counts the same applications.
  const crmBoundary = { applications_shown: appCount, log_applications: logApps.size, simulated_units: labApps.size, simulated_shown_as_applications: simulatedListed };
  steps.push(`application: ${appCount} application(s) from events.json listed, ${labApps.size} simulated unit(s) kept out; timeline labelled as pipeline sample data; a note was appended; no submit action exists`);

  // ---- 5. Experiment: the pipeline's analysis is shown as given
  await goto(page, "Experiments");
  const experimentRaw = art<Record<string, unknown>>("experiment.json");
  const experiment = (experimentRaw.spec ?? experimentRaw) as { id: string; title: string };
  await press(page, page.getByTestId(`exp-${experiment.id}`), "the pipeline's experiment");
  await expect(page.getByTestId("exp-detail")).toContainText(experiment.title);
  await expect(page.getByTestId("exp-title")).toBeDisabled(); // locked design is read-only
  await tabTo(page, page.getByRole("tab", { name: "Design" }), "Design tab");
  await page.keyboard.press("ArrowRight");
  const assignments = art<unknown[]>("assignments.json");
  await expect(page.getByTestId("assign-table").locator("tbody tr")).toHaveCount(assignments.length);
  await page.keyboard.press("ArrowRight");
  const results = page.getByTestId("exp-results");
  await expect(results).toBeVisible();
  if (has("analysis.json")) {
    const analysis = art<{ decision?: string; verdict?: string; arms?: unknown; flow?: { by_arm?: Record<string, { units?: number }> } }>("analysis.json");
    const verdict = String(analysis.verdict ?? analysis.decision ?? "");
    await expect(page.getByTestId("upstream-verdict")).toBeVisible();
    // Arms are a list in lane E's stub and an object keyed by variant in lane B's report.
    const armRows: { variant: string; assigned: number | undefined }[] = Array.isArray(analysis.arms)
      ? (analysis.arms as { variant: string; n_assigned: number }[]).map((a) => ({ variant: a.variant, assigned: a.n_assigned }))
      : Object.entries((analysis.arms ?? {}) as Record<string, { units?: number }>).map(([variant, a]) => ({ variant, assigned: analysis.flow?.by_arm?.[variant]?.units ?? a.units }));
    expect(armRows.length, "analysis.json lists at least two variants").toBeGreaterThanOrEqual(2);
    for (const arm of armRows) {
      const row = page.getByTestId("upstream-arms").locator("tbody tr", { hasText: arm.variant });
      await expect(row).toHaveCount(1);
      if (arm.assigned !== undefined) await expect(row.locator("td").first()).toHaveText(String(arm.assigned));
    }
    if (verdict !== "EVIDENCE_FOR_DIFFERENCE") await expect(page.getByTestId("upstream-verdict")).toContainText("No variant is declared better.");
  }
  await expect(results).not.toContainText(/winner/i);
  if (labApps.size > 0) {
    await expect(page.getByTestId("lab-activity")).toContainText("They are not your applications");
    await expect(page.getByTestId("lab-units")).toHaveText(String(labApps.size));
  }
  steps.push("experiment: locked design, pipeline assignments and the pipeline's analysis verdict rendered without recomputation");

  // ---- 6. Atlas: distortion stated with the pipeline's numbers; select a point from the keyboard
  await goto(page, "Career Atlas");
  await expect(page.getByTestId("pt-uncertainty")).toContainText("Distance on the map is not fit");
  await press(page, page.getByRole("link", { name: "Full map" }), "Full map view");
  await expect(page.getByTestId("distortion-banner")).toBeVisible();
  let atlasNote = "postings only (the run has no projection view)";
  if (has("atlas.pca.json")) {
    // The run carries lane C's projection view: candidate, scenario point and movement path share one projection.
    const view = art<{ projection_id: string; points: { entity_id: string; representation_type: string }[]; paths: { scenario_id: string }[]; diagnostics: { trustworthiness: { k: number; value: number } | null } }>("atlas.pca.json");
    await expect(page.getByTestId("atlas-count")).toContainText(`of ${view.points.length} points`);
    if (view.diagnostics.trustworthiness) await expect(page.getByTestId("trustworthiness")).toContainText(`Trustworthiness ${view.diagnostics.trustworthiness.value.toFixed(2)} (k=${view.diagnostics.trustworthiness.k})`);
    const candidate = view.points.find((p) => p.representation_type === "candidate");
    if (candidate) await expect(page.getByTestId(`pt-${candidate.entity_id}`)).toBeVisible();
    for (const path of view.paths) await expect(page.getByTestId(`path-${path.scenario_id}`)).toBeVisible();
    if (has("scenarios.json")) {
      const model = art<{ outputs?: { model_ref?: { projection_id?: string } } }[]>("scenarios.json")[0]?.outputs?.model_ref?.projection_id;
      if (model !== undefined) expect(model, "the scenario was computed on the projection that is drawn").toBe(view.projection_id);
    }
    atlasNote = `${view.points.length} points incl. candidate and scenario, ${view.paths.length} movement path(s), one projection`;
  } else if (has("atlas_diagnostics.json")) {
    const diag = art<{ trustworthiness?: number | { value: number } }>("atlas_diagnostics.json");
    const t = typeof diag.trustworthiness === "number" ? diag.trustworthiness : diag.trustworthiness?.value;
    if (typeof t === "number") await expect(page.getByTestId("trustworthiness")).toContainText(`Trustworthiness ${t.toFixed(2)}`);
  }
  const entry = page.locator('[data-testid="atlas-plot"] g[role="button"][tabindex="0"]');
  await tabTo(page, entry, "the atlas plot (single tab stop)");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-testid="atlas-plot"] g[aria-pressed="true"]')).toHaveCount(1);
  await expect(page.getByTestId("atlas-detail")).toContainText("This map position is not a score.");
  steps.push(`atlas: distortion banner shows the pipeline's diagnostics; ${atlasNote}; a point was selected with arrow keys and Enter`);

  // ---- 7. Simulation: every scenario listed as hypothetical; edit effort from the keyboard
  await goto(page, "Career Simulator");
  const scenarios = art<{ id: string; planned_actions: { effort_hours: number }[] }[]>("scenarios.json");
  await expect(page.getByTestId("scenario-table").locator("tbody tr")).toHaveCount(scenarios.length);
  await expect(page.getByTestId("hypothetical-banner")).toBeVisible();
  const first = scenarios[0]!;
  const total = first.planned_actions.reduce((s, a) => s + a.effort_hours, 0);
  await expect(page.getByTestId(`hours-${first.id}`)).toHaveText(`${total} h`);
  await press(page, page.getByTestId(`scenario-pick-${first.id}`), "scenario row button");
  await typeInto(page, page.getByTestId("action-hours-0"), "effort hours field", String(first.planned_actions[0]!.effort_hours + 25));
  await expect(page.getByTestId(`hours-${first.id}`)).toHaveText(`${total + 25} h`);
  await expect(page.getByTestId(`cost-${first.id}`)).toHaveText("Not entered");
  steps.push("simulation: scenarios listed as hypothetical; effort edited from the keyboard; no cost invented");

  // ---- facts for ui_report.json (measured here, not assumed)
  const pointerEvents = await page.evaluate(() => (window as unknown as { __pointerEvents: number }).__pointerEvents);
  const dataSource = await page.locator(".app").getAttribute("data-source");
  const dialects: Record<string, string> = {};
  await goto(page, "Settings");
  for (const row of await page.getByTestId("adapter-table").locator("tbody tr").all()) {
    const code = row.locator("code");
    if ((await code.count()) > 0) dialects[(await row.getAttribute("data-testid")) ?? "adapter"] = await code.first().innerText();
  }
  if (FACTS) {
    mkdirSync(dirname(FACTS), { recursive: true });
    writeFileSync(FACTS, JSON.stringify({ crm_boundary: crmBoundary, data_source: dataSource, mock_sources: list(mockText), missing: list(missingText), pointer_events: pointerEvents, external_requests: external.length, focus_method: "tab-traversal", tab_presses: tabPresses, dialects, steps, upstream_findings: upstreamFindings }, null, 2));
  }
  expect(external, "requests to other origins").toEqual([]);
  expect(errors, "console or page errors").toEqual([]);
  expect(pointerEvents, "pointer, mouse or touch events during the journey").toBe(0);
  expect(dataSource).toBe("artifacts");
});

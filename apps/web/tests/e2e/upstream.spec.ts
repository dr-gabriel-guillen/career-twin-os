import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./fixtures";

/**
 * The same screens rendering REAL upstream payloads (pinned copies, see
 * tests/upstream-samples/PROVENANCE.md): lane A's match result and job envelope,
 * lane C's projections and scenarios, lane E's stub for the rest.
 */
const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const served = join(WEB, "dist", "artifacts");
const sample = (lane: string, file: string) => JSON.parse(readFileSync(join(WEB, "tests", "upstream-samples", lane, file), "utf8"));

const jobs = sample("lane-a", "jobs_query.json") as { items: { posting: { id: string; title: string; organization: string } }[] };
const rank = sample("lane-a", "rank_result.json") as Record<string, any>;
// The pinned match example and the pinned job example come from different runs of lane A;
// point two shortlist entries at the two postings we have so the join is exercised.
rank.shortlist = rank.shortlist.slice(0, 2).map((m: any, i: number) => ({ ...m, job_id: jobs.items[i]!.posting.id }));
const scenarios = sample("lane-c", "scenarios.json") as { id: string; target_role: string; planned_actions: { effort_hours: number }[]; outputs: { coverage_before: number; coverage_after: number } }[];
const pca = sample("lane-c", "atlas.pca.json") as { diagnostics: any; points: unknown[]; paths: { scenario_id: string }[] };

test.describe.configure({ mode: "serial" });
test.beforeAll(() => {
  mkdirSync(served, { recursive: true });
  const put = (name: string, value: unknown) => writeFileSync(join(served, name), JSON.stringify(value));
  put("jobs.json", jobs);
  put("matches.json", rank);
  put("atlas.pca.json", pca);
  put("atlas.axes.json", sample("lane-c", "atlas.axes.json"));
  put("scenarios.json", scenarios);
  for (const f of ["claims.json", "experiment.json", "assignments.json", "analysis.json", "events.json", "resume_variant.json"]) put(f, sample("e-stub", f));
});
test.afterAll(() => rmSync(served, { recursive: true, force: true }));

const open = async (page: import("@playwright/test").Page, path: string, title: string) => {
  await page.goto(`/?source=artifacts#${path}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
};

test("lane A payloads: envelope jobs, match result with interval, constraints, refusals and counterfactuals", async ({ page }) => {
  await open(page, "/jobs", "Discover Jobs");
  await expect(page.locator(".app")).toHaveAttribute("data-source", "artifacts");
  await expect(page.getByTestId("job-count")).toHaveText("Showing 2 of 2 postings");
  await expect(page.getByTestId("jobs-cards")).toContainText(jobs.items[0]!.posting.title);
  // Files the mixed set does not contain are named, not silently replaced by fixtures.
  await expect(page.getByTestId("missing-artifacts")).toHaveText("Files the pipeline did not provide: experiment_events.json");

  await open(page, `/matching?job=${encodeURIComponent(jobs.items[0]!.posting.id)}`, "Matching");
  const src = rank.shortlist[0];
  await expect(page.getByTestId("overall-score")).toContainText(`Overall score: ${src.score} / 100 (range ${src.score_interval.low} to ${src.score_interval.high})`);
  await expect(page.getByTestId("near-term-score")).toContainText(String(src.composites.near_term_fit));
  await expect(page.getByTestId("strategic-score")).toContainText(String(src.composites.strategic_utility));
  await expect(page.getByTestId("match-detail")).toContainText("relative_heuristic_not_offer_probability");
  await expect(page.getByTestId("hard-constraints").locator("li")).toHaveCount(src.constraints.length);
  await expect(page.getByTestId("hard-constraints")).toContainText("compensation unknown (absent); not inferred");
  await expect(page.getByTestId("match-refusals").locator("li")).toHaveCount(rank.refusals.length);
  await expect(page.getByTestId("match-refusals")).toContainText(rank.refusals[0].reasons[0]);
  await expect(page.getByTestId("counterfactuals")).toContainText("hypothetical");
  await expect(page.getByTestId("unknown-inputs").locator("li")).toHaveCount(src.unknown_inputs.length);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
});

test("lane C payloads: projection diagnostics, both views, paths, and coverage that follows lane C's evidence rule", async ({ page }) => {
  await open(page, "/atlas", "Career Atlas");
  await expect(page.getByTestId("explained-variance")).toContainText(`${Math.round(pca.diagnostics.cumulative_explained_variance * 100)}%`);
  await expect(page.getByTestId("trustworthiness")).toContainText(`Trustworthiness ${pca.diagnostics.trustworthiness.value.toFixed(2)} (k=${pca.diagnostics.trustworthiness.k})`);
  await expect(page.getByTestId("distortion-banner")).toContainText("true nearest neighbours in the original space");
  await expect(page.getByTestId("distortion-banner")).toContainText("low-explained-variance");
  await expect(page.getByTestId("atlas-count")).toContainText(`of ${pca.points.length} points`);
  await expect(page.getByTestId(`path-${pca.paths[0]!.scenario_id}`)).toBeVisible();
  await page.getByTestId("atlas-projection").selectOption({ index: 1 });
  await expect(page.getByTestId("distortion-banner")).toContainText("hand-defined");
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);

  await open(page, "/simulator", "Career Simulator");
  const s = scenarios[0]!;
  const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
  await expect(page.getByTestId(`coverage-${s.id}`)).toHaveText(`${pct(s.outputs.coverage_before)} → ${pct(s.outputs.coverage_after)}`);
  await page.getByTestId(`scenario-pick-${s.id}`).click();
  // Switching off an action that lane C says does not reach the evidence threshold changes nothing.
  await page.getByTestId("action-include-0").uncheck();
  await expect(page.getByTestId(`coverage-${s.id}`)).toHaveText(`${pct(s.outputs.coverage_before)} → ${pct(s.outputs.coverage_after)}`);
  await page.getByTestId("action-include-0").check();
  // Changing hours: the UI does not guess a new coverage value.
  await page.getByTestId("action-hours-0").fill(String(s.planned_actions[0]!.effort_hours + 200));
  await expect(page.getByTestId(`coverage-${s.id}`)).toContainText("needs recalculation upstream");
  await expect(page.getByTestId("coverage-recompute")).toContainText("only the analytics lane can recompute");
  await expect(page.getByTestId(`hours-${s.id}`)).toHaveText(`${s.planned_actions.reduce((t, a) => t + a.effort_hours, 0) + 200} h`);
  await expect(page.getByTestId("scenario-detail")).toContainText("evidence at or above level 0.75");
});

test("pipeline analysis is shown as given; integration mode never writes the stored workspace", async ({ page }) => {
  await open(page, "/experiments", "Experiments");
  await page.getByRole("tab", { name: "Results" }).click();
  await expect(page.getByTestId("upstream-verdict")).toContainText("Inconclusive. This is not evidence of no effect.");
  await expect(page.getByTestId("upstream-verdict")).toContainText("No variant is declared better.");
  await expect(page.getByTestId("upstream-arms").locator("tbody tr")).toHaveCount(2);
  await expect(page.getByTestId("exp-results")).toContainText("newcombe-hybrid-score");
  await expect(page.getByTestId("exp-results")).not.toContainText(/winner/i);
  await open(page, "/jobs", "Discover Jobs");
  await page.locator('[data-testid^="shortlist-"]').first().click();
  expect(await page.evaluate(() => localStorage.getItem("careertwin.workspace.v1"))).toBeNull();
  await open(page, "/settings", "Settings");
  await expect(page.getByTestId("adapter-matches")).toContainText("a-match/0.1");
  await expect(page.getByTestId("adapter-evidence")).toContainText("Not provided by this source");
});

test("lane E injection corpus: 24 poisoned postings render as inert text (active HTML, prompt injection, bidi and zero-width)", async ({ page }) => {
  // Produced by lane E's `ctint.sec_inject.poison_jobs(jobs, seed=7)` at a2e50e3 from the pinned stub jobs.
  const poisoned = readFileSync(join(WEB, "tests", "upstream-samples", "e-stub", "jobs.poisoned.json"), "utf8");
  const postings = JSON.parse(poisoned) as { id: string; requirements: string[] }[];
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs++;
    void d.dismiss();
  });
  // Serve only the poisoned feed. Every other artifact is absent: like the real static server,
  // the route answers unknown paths with the app shell, which the reader treats as "not provided".
  await page.route("**/artifacts/*.json", (route) =>
    route.request().url().endsWith("/artifacts/jobs.json") ? route.fulfill({ contentType: "application/json", body: poisoned }) : route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>app shell</title>" }),
  );
  await open(page, "/jobs", "Discover Jobs");
  await expect(page.getByTestId("job-count")).toHaveText(`Showing ${postings.length} of ${postings.length} postings`);
  const cards = page.getByTestId("jobs-cards");
  for (const summary of await cards.getByText(/^Requirements text/).all()) await summary.click();
  // Every hostile string is on the page verbatim, as text.
  const text = await cards.innerText();
  const hostile = postings.flatMap((p) => p.requirements).filter((r) => /<|CT-CANARY-/.test(r));
  expect(hostile.length).toBeGreaterThanOrEqual(24);
  for (const h of hostile) expect(text, h.slice(0, 80)).toContain(h.replace(/\s+/g, " ").trim().slice(0, 60));
  // ...and none of it became an element, a handler or a link.
  expect(await cards.locator("script, img, iframe, object, embed, svg, video, audio, form, style, link, meta, base, [onerror], [onload], [onclick], a[href^='javascript'], a[href^='data']").count()).toBe(0);
  expect(await page.locator("a[href^='http']").count()).toBe(0);
  expect(dialogs).toBe(0);
  const leaked = await page.evaluate(() => Object.keys(window).filter((k) => /canary|xss|pwn/i.test(k)));
  expect(leaked).toEqual([]);
  await page.getByTestId("job-view-table").click();
  await expect(page.getByTestId("jobs-table").locator("tbody tr")).toHaveCount(postings.length);
  await expect(page).toHaveURL(/source=artifacts#\/jobs$/);
});

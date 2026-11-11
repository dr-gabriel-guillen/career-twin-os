/**
 * Adapters against REAL upstream output, pinned under tests/upstream-samples/
 * (see PROVENANCE.md there for lane, commit SHA and the command that made each file).
 *
 * Nothing in these samples was edited. If an upstream lane changes its shape,
 * re-pin the sample and this file shows exactly what broke.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ABSENT, loadBundle, totalRejected } from "@/adapters";
import { ARTIFACT_FILES, createArtifactSource, type ArtifactReader } from "@/adapters/artifactSource";
import { isAtlasProjection, isExperimentAssignment, isMatchExplanation, isResumeVariant, isScenarioOutputs } from "@/adapters/proposedValidators";
import { isTransitionScenario } from "@/adapters/frozen";
import { analysisFromUpstream, assignmentsFromUpstream, atlasFromArtifacts, matchesFromUpstream, resumeVariantsFromUpstream } from "@/adapters/upstream";
import { Checker, parseList, parseOne } from "@/adapters/validate";
import { groupApplications } from "@/lib/applications";
import { compareScenarios, defaultEdits } from "@/lib/simulator";
import { reviewVariant } from "@/lib/resumeLint";
import { DEFAULT_SIMULATOR } from "@/state/workspace";

const dir = join(__dirname, "..", "upstream-samples");
const sample = (lane: string, file: string): unknown => JSON.parse(readFileSync(join(dir, lane, file), "utf8"));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const issues = (validate: (v: unknown, c: Checker) => boolean, v: unknown) => {
  const c = new Checker();
  validate(v, c);
  return c.issues.map((i) => `${i.path}: ${i.message}`);
};
const reader = (lane: string, overrides: Record<string, unknown> = {}, drop: string[] = []): ArtifactReader => ({
  async read(file) {
    if (drop.includes(file)) return undefined;
    if (file in overrides) return overrides[file];
    try {
      return sample(lane, file);
    } catch {
      return undefined;
    }
  },
});

describe("lane A: a-match/0.1 MatchResult", () => {
  const result = sample("lane-a", "rank_result.json") as Record<string, any>;
  const t = matchesFromUpstream(result, { subject: "unused", computedAt: "unused" });

  it("is recognised and every shortlist item becomes a valid match view", () => {
    expect(t.dialect).toBe("a-match/0.1");
    expect(t.mock).toBe(false);
    const parsed = parseList(t.payload, isMatchExplanation);
    expect(parsed.rejected.map((r) => r.issues)).toEqual([]);
    expect(parsed.items).toHaveLength(result.shortlist.length);
  });

  it("copies scores, interval, constraints and unknowns without recomputing them", () => {
    const [m] = parseList(t.payload, isMatchExplanation).items;
    const src = result.shortlist[0];
    expect(m!.job_id).toBe(src.job_id);
    expect(m!.overall).toEqual({ score: src.score, interval: { low: src.score_interval.low, high: src.score_interval.high }, note: src.score_interval.meaning });
    expect(m!.near_term_fit.score).toBe(src.composites.near_term_fit);
    expect(m!.strategic_value.score).toBe(src.composites.strategic_utility);
    expect(m!.score_type).toBe("relative_heuristic_not_offer_probability");
    expect(m!.hard_constraints.map((h) => [h.key, h.status])).toEqual(src.constraints.map((c: any) => [c.constraint, c.outcome]));
    expect(m!.unknown_inputs).toEqual(src.unknown_inputs);
    expect(m!.candidate_ref).toBe(result.subject_id);
    // Component values are the engine's; display weights within a group sum to 1.
    for (const group of [m!.near_term_fit, m!.strategic_value]) {
      for (const c of group.components) expect(c.value).toBe(src.components[c.key].value);
      expect(group.components.reduce((s, c) => s + c.weight, 0)).toBeCloseTo(1, 3);
    }
    // Missing skills surface as gaps; matched skills keep the engine's proof status.
    for (const s of src.missing_skills) expect(m!.skills.find((x) => x.skill === s)?.evidence_strength).toBe("missing");
    for (const s of src.matched_skills) expect(m!.skills.find((x) => x.skill === s.skill)?.evidence_strength).toBe(s.proof_status);
    expect(m!.counterfactuals?.every((c) => /hypothetical/i.test(c.status))).toBe(true);
  });

  it("surfaces refusals instead of dropping them", () => {
    expect(t.refusals).toHaveLength(result.refusals.length);
    expect(t.refusals[0]).toEqual({ job_id: result.refusals[0].job_id, reasons: result.refusals[0].reasons });
  });

  it("an unknown component (value null) stays unknown and carries no weight", () => {
    const r = clone(result);
    r.shortlist[0].components.workstyle.value = null;
    const [m] = parseList(matchesFromUpstream(r, { subject: "s", computedAt: "c" }).payload, isMatchExplanation).items;
    const w = m!.near_term_fit.components.find((c) => c.key === "workstyle")!;
    expect(w).toMatchObject({ value: null, status: "unknown", weight: 0 });
    expect(m!.near_term_fit.components.reduce((s, c) => s + c.weight, 0)).toBeCloseTo(1, 3);
  });

  it("a payload that calls its score a probability is refused", () => {
    const r = clone(result);
    r.shortlist[0].score_type = "offer_probability";
    r.score_type = "offer_probability";
    const parsed = parseList(matchesFromUpstream(r, { subject: "s", computedAt: "c" }).payload, isMatchExplanation);
    expect(parsed.items).toHaveLength(result.shortlist.length - 1);
    expect(parsed.rejected[0]!.issues.some((i) => i.path.endsWith("score_type"))).toBe(true);
  });

  it("a counterfactual not labelled hypothetical is refused", () => {
    const r = clone(result);
    r.shortlist[0].counterfactuals[0].status = "expected outcome";
    expect(parseList(matchesFromUpstream(r, { subject: "s", computedAt: "c" }).payload, isMatchExplanation).rejected).toHaveLength(1);
  });
});

describe("reference-engine dialect (lane E stub matches.json)", () => {
  const raw = sample("e-stub", "matches.json") as Record<string, any>[];
  const t = matchesFromUpstream(raw, { subject: "candidate-synthetic-001", computedAt: "2026-11-10T00:00:00Z" });
  it("is labelled mock and yields valid views with ONE score and no invented group scores", () => {
    expect(t.dialect).toBe("reference-engine");
    expect(t.mock).toBe(true);
    const parsed = parseList(t.payload, isMatchExplanation);
    expect(parsed.rejected).toEqual([]);
    expect(parsed.items).toHaveLength(raw.length);
    for (const [i, m] of parsed.items.entries()) {
      expect(m.near_term_fit.score).toBeNull();
      expect(m.strategic_value.score).toBeNull();
      expect(m.overall?.score).toBe(raw[i]!.score);
      expect(m.overall?.interval).toBeNull();
      expect(m.hard_constraints).toEqual([]);
      expect(m.unknown_inputs).toEqual(raw[i]!.unknown_inputs);
    }
  });
});

describe("résumé variants, assignments, analysis", () => {
  const ctx = { clock: "2026-11-10T00:00:00Z", subject: "candidate-synthetic-001" };
  it("lane E stub variant becomes a valid, UNAPPROVED view and is marked mock", () => {
    const raw = sample("e-stub", "resume_variant.json") as Record<string, any>;
    const t = resumeVariantsFromUpstream(raw, ctx);
    expect(t.mock).toBe(true);
    const parsed = parseList(t.payload, isResumeVariant);
    expect(parsed.rejected).toEqual([]);
    const v = parsed.items[0]!;
    expect(v.approval).toBeNull();
    expect(v.target_job_id).toBe(raw.job_id);
    expect(v.sections[0]!.bullets.map((b) => b.claim_ids)).toEqual(raw.bullets.map((b: any) => b.claim_ids));
    expect(v.sections[0]!.bullets.map((b) => b.text)).toEqual(raw.bullets.map((b: any) => b.text));
  });
  it("lane B's documented variant shape (claim_id per bullet, manual_override) maps to the same view", () => {
    const b = { id: "rv-b-1", lineage_id: "lin-1", version: 2, parent_id: "rv-b-0", subject_id: "candidate-synthetic-001", job_id: "job-1", label: "Tailored", content_hash: "h", created_at: "2026-03-02T09:00:00Z", created_by: "user",
      bullets: [{ claim_id: "c1", text: "Verbatim claim", claim_status: "supported", manual_override: false }, { claim_id: "c2", text: "Edited by hand", claim_status: "self-reported", manual_override: true }] };
    const t = resumeVariantsFromUpstream(b, ctx);
    expect(t.dialect).toBe("careertwin.b.resume-variant.v1");
    expect(t.mock).toBe(false);
    const v = parseList(t.payload, isResumeVariant).items[0]!;
    expect(v).toMatchObject({ id: "rv-b-1", version: 2, parent_variant_id: "rv-b-0", target_job_id: "job-1", label: "Tailored", approval: null });
    expect(v.sections[0]!.bullets.map((x) => [x.claim_ids, x.origin])).toEqual([[["c1"], "from-claims"], [["c2"], "manual-edit"]]);
  });
  it("a variant citing an unverified or unknown claim still blocks export after translation", () => {
    const claims = sample("e-stub", "claims.json") as any[];
    const raw = clone(sample("e-stub", "resume_variant.json")) as Record<string, any>;
    raw.bullets.push({ text: "Invented", claim_ids: ["claim-does-not-exist"] });
    const v = parseList(resumeVariantsFromUpstream(raw, ctx).payload, isResumeVariant).items[0]!;
    const review = reviewVariant(v, claims, ctx.clock);
    expect(review.blocking).toBeGreaterThanOrEqual(1);
    expect(review.exportAllowed).toBe(false);
  });
  it("assignments from the stub and from lane B's documented shape both validate; exposure stays unknown", () => {
    const stub = assignmentsFromUpstream(sample("e-stub", "assignments.json"));
    expect(stub.dialect).toBe("stub-assignment");
    const parsed = parseList(stub.payload, isExperimentAssignment);
    expect(parsed.rejected).toEqual([]);
    expect(parsed.items).toHaveLength(24);
    expect(parsed.items.every((a) => a.exposure_logged_at === null)).toBe(true);
    const b = assignmentsFromUpstream([{ assignment_id: "asg-0000000", experiment_id: "exp-roundtrip", application_id: "app-1", unit_kind: "application", unit_id: "app-1", stratum: "seniority=senior", variant: "control", assigned_at: "2026-03-02T09:00:00Z", reused_unit: false, design_hash: "abc" }]);
    expect(b.dialect).toBe("lane-b-assignment");
    expect(parseList(b.payload, isExperimentAssignment).items[0]).toMatchObject({ id: "asg-0000000", unit_label: "application: app-1", seed_digest: "abc", variant: "control" });
    expect(parseList(assignmentsFromUpstream([{ id: "x" }]).payload, isExperimentAssignment).rejected).toHaveLength(1);
  });
  it("analysis: stub decision and lane B verdicts are read; anything else yields no result at all", () => {
    const stub = analysisFromUpstream(sample("e-stub", "analysis.json"));
    expect(stub.payload).toMatchObject({ experiment_id: "exp-syn-s7-0002", verdict: "INCONCLUSIVE", estimand: "ITT" });
    expect(stub.payload!.arms).toEqual([{ variant: "A", n_assigned: 13, n_success: 4 }, { variant: "B", n_assigned: 11, n_success: 3 }]);
    expect(stub.payload!.effect!.ci_low).toBeLessThan(0);
    expect(stub.payload!.effect!.ci_high).toBeGreaterThan(0);
    const b = analysisFromUpstream({ experiment_id: "e", verdict: "INTERIM_NO_CLAIM", claim: null, reasons_withheld: ["planned sample not reached"], warnings: ["w"], interpretation_limits: ["l"] });
    expect(b.payload).toMatchObject({ verdict: "INTERIM_NO_CLAIM", reasons_withheld: ["planned sample not reached"], effect: null });
    expect(b.dialect).toBe("careertwin.b.analysis-report.v1");
    for (const bad of [{ experiment_id: "e", verdict: "WINNER_B" }, { experiment_id: "e", decision: "B_WINS" }, { verdict: "INCONCLUSIVE" }, "text", null]) {
      expect(analysisFromUpstream(bad).payload).toBeNull();
    }
    // An effect whose interval is inverted or incomplete is dropped rather than shown.
    expect(analysisFromUpstream({ experiment_id: "e", verdict: "INCONCLUSIVE", effect: { contrast: "B - A", estimate: 0.1, ci_low: 0.3, ci_high: 0.1 } }).payload!.effect).toBeNull();
  });
});

describe("lane C: atlas-projection/0.1 and scenario-outputs/0.1 (files published for lane D)", () => {
  it("both projections validate as-is", () => {
    for (const f of ["atlas.pca.json", "atlas.axes.json"]) {
      const p = sample("lane-c", f) as Record<string, any>;
      expect(p.schema_version).toBe("atlas-projection/0.1");
      expect(issues(isAtlasProjection, p)).toEqual([]);
    }
  });
  it("scenarios satisfy the frozen contract and the outputs view, including lane C's per-action results", () => {
    const list = sample("lane-c", "scenarios.json") as Record<string, any>[];
    expect(list).toHaveLength(3);
    for (const s of list) {
      expect(issues(isTransitionScenario, s)).toEqual([]);
      expect(issues(isScenarioOutputs, s.outputs)).toEqual([]);
      // One planned action can expand into several features (e.g. "client-exposure"), so the lists
      // are joined by `skill`, never by position.
      expect(s.outputs.applied_actions.length).toBeGreaterThanOrEqual(s.planned_actions.length);
      const planned = new Set(s.planned_actions.map((a: any) => a.skill));
      expect(s.outputs.applied_actions.every((a: any) => planned.has(a.skill))).toBe(true);
    }
  });
  it("coverage follows lane C's evidence rule, not skill names", () => {
    const list = (sample("lane-c", "scenarios.json") as any[]).map((scenario) => {
      const parsed = parseOne(scenario.outputs, isScenarioOutputs);
      return { scenario, outputs: parsed.ok ? parsed.value : null };
    });
    const untouched = compareScenarios(list, {}, DEFAULT_SIMULATOR);
    for (const r of untouched) {
      expect(r.coverageBasis).toBe("upstream");
      expect(r.coverageAfter).toBe(r.view.outputs!.coverage_after);
    }
    const view = list[0]!;
    // Lane C says the first action does NOT reach the evidenced level; skill-name counting would add it.
    expect(view.outputs!.applied_actions![0]!.reaches_evidenced_level).toBe(false);
    expect(view.outputs!.target_skills).toContain(view.outputs!.applied_actions![0]!.feature);
    const offFirst = defaultEdits(view.scenario).map((a, i) => (i === 0 ? { ...a, included: false } : a));
    const [toggled] = compareScenarios([view], { [view.scenario.id]: offFirst }, DEFAULT_SIMULATOR);
    expect(toggled!.coverageBasis).toBe("upstream-per-action");
    expect(toggled!.coverageAfter).toBeCloseTo(view.outputs!.coverage_after as number, 6); // dropping a non-reaching action changes nothing
    const hours = defaultEdits(view.scenario).map((a, i) => (i === 0 ? { ...a, effort_hours: a.effort_hours + 500 } : a));
    const [edited] = compareScenarios([view], { [view.scenario.id]: hours }, DEFAULT_SIMULATOR);
    expect(edited!.coverageBasis).toBe("needs-upstream-recompute");
    expect(edited!.coverageAfter).toBeNull();
    expect(edited!.totalHours).toBe(view.scenario.planned_actions.reduce((s: number, a: any) => s + a.effort_hours, 0) + 500);
  });
  it("scenario outputs carrying a probability-like field are refused (hub finding E-F005)", () => {
    const o = clone((sample("lane-c", "scenarios.json") as any[])[0].outputs);
    expect(issues(isScenarioOutputs, { ...o, offer_probability: 0.4 }).join(" ")).toContain("probability-like");
    expect(issues(isScenarioOutputs, { ...o, chance_of_success: 0.4 }).join(" ")).toContain("probability-like");
    // Lane C's own explicit denial flag is fine.
    expect(o.not_a_hiring_probability).toBe(true);
    expect(issues(isScenarioOutputs, o)).toEqual([]);
  });
});

describe("pipeline artifacts (lane E layout) through the artifact source", () => {
  it("loads lane E's stub artifacts into a bundle, with dialects and mock sources reported", async () => {
    const b = await loadBundle(createArtifactSource(reader("e-stub")));
    expect(b.provenance.data_source).toBe("artifacts");
    expect(b.jobs).toHaveLength(24);
    expect(b.claims.length).toBeGreaterThan(0);
    expect(b.matches).toHaveLength(24);
    expect(b.resumeVariants).toHaveLength(1);
    expect(b.experiments.map((e) => e.id)).toEqual(["exp-syn-s7-0002"]);
    expect(b.assignments).toHaveLength(24);
    expect(b.analyses).toHaveLength(1);
    expect(b.projections).toHaveLength(1);
    expect(b.projections[0]!.points).toHaveLength(24);
    // Role family on the map comes from joining points to the job feed by id.
    const families = new Set(b.jobs.map((j) => j.role_family));
    expect(b.projections[0]!.entities.every((e) => e.job_id !== null && families.has(e.role_family ?? undefined))).toBe(true);
    expect(b.scenarios).toHaveLength(1);
    expect(b.scenarios[0]!.outputs).toBeNull(); // the stub's outputs are not lane C's shape; no numbers are derived
    expect(b.provenance.dialects["matches.json"]).toBe("reference-engine");
    expect(b.provenance.mock_sources).toEqual(expect.arrayContaining(["matches.json", "resume_variant.json"]));
    expect(b.provenance.missing).toEqual([]);
    // Absent optional payloads are "absent", not errors.
    expect(b.reports.filter((r) => r.absent).map((r) => r.adapter.id).sort()).toEqual(["atlas-axes", "evidence", "outcomes", "preferences"]);
    expect(b.reports.flatMap((r) => r.fatal)).toEqual([]);
    expect(b.clock).toBe(b.jobs.map((j) => j.fetched_at).sort().at(-1));
    // The pipeline's own application reached "submitted" with an approval record.
    const pinned = (sample("e-stub", "pin.json") as { job_id: string }).job_id;
    const app = groupApplications(b.applicationEvents).find((a) => a.job_id === pinned && a.submittedAt !== null);
    expect(app?.approvalTokenRef).toBeTruthy();
  });

  it("accepts lane A's envelope for jobs and lane A's MatchResult, and lane C's projection files when present", async () => {
    const a = sample("lane-a", "jobs_query.json") as { items: { posting: { id: string } }[] };
    const rank = clone(sample("lane-a", "rank_result.json")) as Record<string, any>;
    // Point the pinned match example at postings that exist in the pinned job example.
    rank.shortlist = rank.shortlist.slice(0, 2).map((m: any, i: number) => ({ ...m, job_id: a.items[i]!.posting.id }));
    const b = await loadBundle(createArtifactSource(reader("e-stub", { "jobs.json": a, "matches.json": rank, "atlas.pca.json": sample("lane-c", "atlas.pca.json"), "atlas.axes.json": sample("lane-c", "atlas.axes.json"), "scenarios.json": sample("lane-c", "scenarios.json") })));
    expect(b.provenance.dialects["jobs.json"]).toBe("a-ext/0.1 envelope");
    expect(b.jobs.map((j) => j.id)).toEqual(a.items.map((x) => x.posting.id));
    expect(b.provenance.dialects["matches.json"]).toBe("a-match/0.1");
    expect(b.matches).toHaveLength(2);
    expect(b.matchRefusals.length).toBe(rank.refusals.length);
    expect(b.projections.map((p) => p.schema_version)).toEqual(["atlas-projection/0.1", "atlas-projection/0.1"]);
    expect(b.scenarios.every((s) => s.outputs?.schema_version === "scenario-outputs/0.1")).toBe(true);
    expect(b.provenance.mock_sources).not.toContain("matches.json");
  });

  it("missing files are reported as missing and never back-filled from lane D fixtures", async () => {
    const b = await loadBundle(createArtifactSource(reader("e-stub", {}, ["matches.json", "atlas_points.json", "analysis.json", "scenarios.json"])));
    expect(b.matches).toEqual([]);
    expect(b.projections).toEqual([]);
    expect(b.analyses).toEqual([]);
    expect(b.scenarios).toEqual([]);
    expect(b.provenance.missing).toEqual(expect.arrayContaining(["matches.json", "atlas_points.json", "analysis.json", "scenarios.json"]));
    expect(b.jobs).toHaveLength(24);
  });

  it("malformed artifacts are rejected record by record and an unreadable analysis yields a note, not a verdict", async () => {
    const jobs = clone(sample("e-stub", "jobs.json")) as Record<string, any>[];
    jobs[0]!.hiring_probability = 0.9;
    delete jobs[1]!.url;
    const b = await loadBundle(createArtifactSource(reader("e-stub", { "jobs.json": jobs, "analysis.json": { experiment_id: "exp-syn-s7-0002", decision: "B_WINS" }, "assignments.json": "not a list" })));
    expect(b.jobs).toHaveLength(22);
    expect(totalRejected(b)).toBeGreaterThanOrEqual(3);
    expect(b.analyses).toEqual([]);
    expect(b.sourceNotes.join(" ")).toContain("analysis.json was not understood");
    expect(b.assignments).toEqual([]);
  });

  it("the artifact file list matches lane E's published layout", () => {
    for (const f of ["jobs.json", "claims.json", "matches.json", "pin.json", "resume_variant.json", "events.json", "experiment.json", "assignments.json", "experiment_events.json", "analysis.json", "atlas_points.json", "atlas_diagnostics.json", "scenarios.json"]) {
      expect(ARTIFACT_FILES).toContain(f);
    }
    expect(typeof ABSENT).toBe("symbol");
  });

  it("atlasFromArtifacts refuses non-list points and keeps unknown diagnostics unknown", () => {
    expect(atlasFromArtifacts("x", {}, [], "2026-11-10T00:00:00Z").payload).toBeNull();
    const t = atlasFromArtifacts(sample("e-stub", "atlas_points.json"), { projection_id: "p", method: "pca" }, [], "2026-11-10T00:00:00Z");
    const p = t.payload as Record<string, any>;
    expect(p.diagnostics).toMatchObject({ cumulative_explained_variance: null, trustworthiness: null, continuity: null });
    // Points say projection_id "proj-stub-pca-s7" but diagnostics say "p": the strict validator catches the mismatch.
    expect(issues(isAtlasProjection, p).join(" ")).toContain("different projection");
  });
});

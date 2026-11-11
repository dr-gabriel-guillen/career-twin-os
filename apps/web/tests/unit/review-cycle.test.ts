/**
 * Regression tests for the independent adversarial review of lane D
 * (findings F-01 … F-20, see docs/QA-EVIDENCE.md). Each test reproduces the
 * reviewer's failing case and asserts the repaired behaviour.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { loadBundle, type DataBundle, type PayloadName } from "@/adapters";
import { createArtifactSource, type ArtifactReader } from "@/adapters/artifactSource";
import { isAtlasProjection, isMatchExplanation, isResumeVariant } from "@/adapters/proposedValidators";
import { ASSIGNMENT_SCHEMA, OUTCOME_SCHEMA, type ExperimentAssignment, type ExperimentOutcome, type ResumeVariant } from "@/adapters/proposed";
import { isJobPosting, isProfileClaim } from "@/adapters/frozen";
import { analysisFromUpstream, atlasFromArtifacts, matchesFromUpstream, resumeVariantsFromUpstream } from "@/adapters/upstream";
import { Checker, isRfc3339, parseList } from "@/adapters/validate";
import type { ApplicationEvent, ApplicationEventType, ExperimentSpec, ProfileClaim } from "@/contracts/types";
import { daysWithoutFeedback, groupApplications } from "@/lib/applications";
import { designProblems, plannedPerArmFromRule, summariseExperiment } from "@/lib/experimentView";
import { freshness } from "@/lib/format";
import { lintBullet, reviewVariant, variantToText } from "@/lib/resumeLint";
import { resumeContentDigest } from "@/lib/resumeDigest";
import { classifyLink, csvCell } from "@/lib/safety";
import { sha256Hex } from "@/lib/sha256";
import { niceTicks } from "@/lib/ticks";
import { compareScenarios } from "@/lib/simulator";
import { DEFAULT_SIMULATOR, parseWorkspace, reduce, seedWorkspace, type Action, type Workspace } from "@/state/workspace";

const WEB = join(__dirname, "..", "..");
const fixtureDir = join(WEB, "src", "fixtures", "generated");
const sample = (lane: string, file: string): unknown => JSON.parse(readFileSync(join(WEB, "tests", "upstream-samples", lane, file), "utf8"));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const valid = (validate: (v: unknown, c: Checker) => boolean, v: unknown) => {
  const c = new Checker();
  return validate(v, c) && c.issues.length === 0;
};
const reader = (lane: string, overrides: Record<string, unknown> = {}, drop: string[] = [], broken: string[] = []): ArtifactReader => ({
  async read(file) {
    if (broken.includes(file)) throw new SyntaxError("Unexpected token in JSON");
    if (drop.includes(file)) return undefined;
    if (file in overrides) return overrides[file];
    try {
      return sample(lane, file);
    } catch {
      return undefined;
    }
  },
});

let bundle: DataBundle;
let seeded: Workspace;
beforeAll(async () => {
  bundle = await loadBundle({ id: "t", label: "t", load: async (name: PayloadName) => JSON.parse(readFileSync(join(fixtureDir, `${name}.json`), "utf8")) as unknown });
  seeded = seedWorkspace(bundle, "fixture");
});
const apply = (state: Workspace, ...actions: Action[]) => actions.reduce((s, a) => {
  const r = reduce(s, a);
  if (r.refused) throw new Error(`unexpected refusal: ${r.refused}`);
  return r.state;
}, state);
let n = 0;
const T0 = "2026-11-10T12:00:00Z";
const ev = (application_id: string, job_id: string, event_type: ApplicationEventType, over: Partial<ApplicationEvent> = {}): ApplicationEvent => ({
  id: `evt-rc-${++n}`, application_id, job_id, event_type, recorded_at: T0, occurred_at: T0, actor: "user", source: "manual-entry",
  outcome_status: "self-reported", resume_variant_id: null, experiment_id: null, approval_token_ref: null, notes: "", ...over,
});
const approvedVariant = (id: string): ResumeVariant => {
  const sections = bundle.resumeVariants[0]!.sections.filter((s) => s.heading === "Experience");
  return { ...bundle.resumeVariants[0]!, id, sections, approval: { approved_at: T0, approval_token_ref: `approval-${id}`, content_digest: resumeContentDigest(sections) } };
};
const refusal = (state: Workspace, action: Action) => reduce(state, action).refused ?? null;

describe("F-01: the submission gate cannot be forged through a note or by rewriting an approved variant", () => {
  it("a note cannot carry an approval record or a résumé variant, and cannot move the application's approval", () => {
    let s = apply(seeded, { type: "resume/upsert", variant: approvedVariant("rv-ok") },
      { type: "event/append", event: ev("app-w2", "fake-fde-1", "saved") },
      { type: "event/append", event: ev("app-w2", "fake-fde-1", "drafted", { resume_variant_id: "rv-ok" }) },
      { type: "event/append", event: ev("app-w2", "fake-fde-1", "approved", { resume_variant_id: "rv-ok", approval_token_ref: "approval-rv-ok" }) });
    expect(refusal(s, { type: "event/append", event: ev("app-w2", "fake-fde-1", "note", { actor: "system", approval_token_ref: "FORGED-TOKEN", resume_variant_id: "rv-never-approved" }) })).toContain("Only the approval and submission steps");
    expect(refusal(s, { type: "event/append", event: ev("app-w2", "fake-fde-1", "note", { resume_variant_id: "rv-never-approved" }) })).toContain("A note cannot change");
    expect(refusal(s, { type: "event/append", event: ev("app-w2", "fake-fde-1", "submitted", { resume_variant_id: "rv-does-not-exist", approval_token_ref: "FORGED-TOKEN" }) })).toBeTruthy();
    // A plain note is fine and leaves the approval untouched.
    s = apply(s, { type: "event/append", event: ev("app-w2", "fake-fde-1", "note", { notes: "just a note" }) });
    const app = groupApplications(s.applicationEvents).find((a) => a.id === "app-w2")!;
    expect([app.stage, app.approvalTokenRef, app.approvedVariantId]).toEqual(["approved", "approval-rv-ok", "rv-ok"]);
  });
  it("an approved variant cannot be replaced in place, so 'submitted' always refers to what was approved", () => {
    const v = approvedVariant("rv-a");
    const s = apply(seeded, { type: "resume/upsert", variant: v },
      { type: "event/append", event: ev("app-w3", "fake-fde-1", "saved") },
      { type: "event/append", event: ev("app-w3", "fake-fde-1", "drafted", { resume_variant_id: "rv-a" }) },
      { type: "event/append", event: ev("app-w3", "fake-fde-1", "approved", { resume_variant_id: "rv-a", approval_token_ref: "approval-rv-a" }) });
    const rewritten: ResumeVariant = { ...v, approval: null, sections: [{ id: "s", heading: "Experience", bullets: [{ id: "b", text: "PhD, CEO of Google", claim_ids: [], origin: "manual-edit" }] }] };
    expect(refusal(s, { type: "resume/upsert", variant: rewritten })).toContain("locked");
    expect(s.resumeVariants.find((x) => x.id === "rv-a")!.sections).toEqual(v.sections);
  });
});

describe("F-02: stage follows the recorded log; backdated or misordered events are refused, not ignored", () => {
  it("a same-day picked date still advances the stage", () => {
    let s = apply(seeded, { type: "event/append", event: ev("app-w4", "fake-fde-1", "saved", { occurred_at: "2026-11-10T15:00:00Z" }) });
    s = apply(s, { type: "event/append", event: ev("app-w4", "fake-fde-1", "drafted", { occurred_at: "2026-11-10T12:00:00Z", resume_variant_id: "rv-base" }) });
    expect(groupApplications(s.applicationEvents).find((a) => a.id === "app-w4")!.stage).toBe("drafted");
    expect(refusal(s, { type: "event/append", event: ev("app-w4", "fake-fde-1", "drafted", { resume_variant_id: "rv-base" }) })).toContain("not allowed");
  });
  it("an event dated before the previous step is refused; one that is accepted changes the stage", () => {
    expect(refusal(seeded, { type: "event/append", event: ev("app-synth-002", "fake-se-5", "rejected", { occurred_at: "2026-01-01T12:00:00Z" }) })).toContain("cannot have happened before");
    const s = apply(seeded, { type: "event/append", event: ev("app-synth-002", "fake-se-5", "rejected") });
    const app = groupApplications(s.applicationEvents).find((a) => a.id === "app-synth-002")!;
    expect([app.stage, app.closedAs, daysWithoutFeedback(app, bundle.clock)]).toEqual(["closed", "rejected", null]);
    expect(refusal(s, { type: "event/append", event: ev("app-synth-002", "fake-se-5", "offer") })).toContain("not allowed");
    expect(refusal(seeded, { type: "event/append", event: ev("app-synth-002", "fake-se-5", "note", { recorded_at: "2020-01-01T00:00:00Z" }) })).toContain("goes backwards");
  });
  it("timestamps are compared as instants, not as strings", () => {
    const events = [
      ev("app-tz", "fake-fde-1", "saved", { recorded_at: "2026-03-02T01:00:00+02:00", occurred_at: "2026-03-02T01:00:00+02:00" }),
      ev("app-tz", "fake-fde-1", "drafted", { recorded_at: "2026-03-01T23:30:00Z", occurred_at: "2026-03-01T23:30:00Z", resume_variant_id: "rv-base" }),
    ];
    const app = groupApplications([events[1]!, events[0]!])[0]!;
    expect(app.events.map((e) => e.event_type)).toEqual(["saved", "drafted"]);
    expect(app.stage).toBe("drafted");
  });
});

describe("F-03: the top evidence label cannot be reached on thin data", () => {
  const spec = (rule: string, over: Partial<ExperimentSpec> = {}): ExperimentSpec => ({ id: "x", title: "T", hypothesis: "h", status: "closed", variants: ["a", "b"], assignment_unit: "job", strata: [], primary_metric: "m", design_locked_at: T0, stopping_rule: rule, consent_required: true, outcome_window_days: 21, ...over });
  it("reads the planned sample only from a number that counts units", () => {
    const cases: [string, number | null][] = [
      ["Stop when 1,000 units have a closed outcome window.", 500],
      ["Stop when 1 000 units are in.", 500],
      ["After 2 weeks, stop when 400 units have closed windows.", 200],
      ["Alpha 5%, power 80%: stop at 400 units.", 200],
      ["Stop on 2026-12-31 or at 40 units", 20],
      ["Stop at 40 units per variant.", 40],
      ["Fixed horizon: 116 job units in total or the end of the job feed.", 58],
      ["Stop when 40 units have a closed outcome window. No interim decisions.", 20],
      ["Stop at 40 units or at 60 units, whichever comes first.", null],
      ["Stop when it feels right.", null],
      ["Stop on 2026-12-31.", null],
    ];
    for (const [rule, expected] of cases) expect(plannedPerArmFromRule(spec(rule)), rule).toBe(expected);
  });
  const run = (rule: string, perArm: [number, number][], censoredExtra: number, status: ExperimentSpec["status"] = "closed") => {
    const s = spec(rule, { status });
    const assignments: ExperimentAssignment[] = [];
    const outcomes: ExperimentOutcome[] = [];
    perArm.forEach(([observed, successes], arm) => {
      for (let i = 0; i < observed + censoredExtra; i++) {
        const id = `asg-${arm}-${i}`;
        assignments.push({ schema_version: ASSIGNMENT_SCHEMA, id, experiment_id: "x", unit_id: `u-${arm}-${i}`, unit_label: "u", stratum: "all", variant: s.variants[arm]!, assigned_at: T0, algorithm: "t", seed_digest: "d", exposure_logged_at: null });
        outcomes.push({ schema_version: OUTCOME_SCHEMA, assignment_id: id, experiment_id: "x", metric: "m", status: i < observed ? "observed" : "censored", value: i < observed ? i < successes : null, recorded_at: T0, data_origin: "synthetic-simulation" });
      }
    });
    return summariseExperiment(s, assignments, outcomes, plannedPerArmFromRule(s)).evidence;
  };
  it("the reviewer's case (10/10 vs 3/10, 'Stop when 1,000 units', 40 censored) is not a result", () => {
    const e = run("Stop when 1,000 units have a closed outcome window.", [[10, 10], [10, 3]], 20);
    expect(e.level).toBe("inconclusive");
    expect(e.reasons.join(" ")).toContain("Planned sample of 500 per variant not reached");
  });
  it("a tiny plan, a running experiment, open windows or over-enrolment each prevent the top label", () => {
    expect(run("Stop when 4 units have a closed outcome window.", [[16, 16], [16, 2]], 0).level).toBe("inconclusive");
    expect(run("Stop when 4 units have a closed outcome window.", [[16, 16], [16, 2]], 0).reasons.join(" ")).toContain("below the minimum");
    expect(run("Stop when 40 units have a closed outcome window.", [[20, 20], [20, 2]], 0, "running").level).toBe("inconclusive");
    expect(run("Stop when 40 units have a closed outcome window.", [[20, 20], [20, 2]], 3).reasons.join(" ")).toContain("still inside their outcome window");
    expect(run("Stop when 40 units have a closed outcome window.", [[40, 40], [40, 4]], 0).reasons.join(" ")).toContain("More units were assigned");
    // The one configuration that does qualify: closed, locked, planned sample reached exactly, clear gap.
    expect(run("Stop when 40 units have a closed outcome window.", [[20, 20], [20, 2]], 0).level).toBe("meets-preregistered-criterion");
  });
  it("a design whose plan is below the minimum cannot be locked", () => {
    for (const total of [1, 2, 10, 18]) {
      expect(designProblems(spec(`Stop when ${total} units have a closed outcome window.`, { title: "Title", hypothesis: "A hypothesis of sufficient length.", analysis_plan: "An analysis plan of sufficient length." })).join(" "), String(total)).toContain("at least 10 units per variant");
    }
  });
});

describe("F-04 / F-13: every line is compared with its claims, whatever origin it declares", () => {
  const claim = (over: Partial<ProfileClaim> = {}): ProfileClaim => ({ id: "c1", subject_id: "s", claim_type: "achievement", claim_text: "Reduced p95 latency of the fictional order API by 38%", status: "supported", evidence_ids: ["ev1"], visibility: "shareable", updated_at: T0, skills: [], ...over });
  const codes = (text: string, origin: "from-claims" | "manual-edit" = "manual-edit", c = claim()) => lintBullet({ id: "b", text, claim_ids: [c.id], origin }, new Map([[c.id, c]]), T0).filter((f) => f.severity === "block").map((f) => f.code);
  it("a 'from-claims' line that does not read like its claim is blocked", () => {
    const upstream = resumeVariantsFromUpstream({ id: "rv-up", schema: "careertwin.b.resume-variant.v1", bullets: [{ claim_id: "c1", text: "PhD, Certified Chief Architect at Google; led 5000 engineers; grew revenue 900%" }] }, { clock: T0, subject: "s" });
    const v = parseList(upstream.payload, isResumeVariant).items[0]!;
    expect(v.sections[0]!.bullets[0]!.origin).toBe("from-claims");
    const review = reviewVariant(v, [claim({ claim_text: "Fictional Cloud Architect Certification" })], T0);
    expect(review.blocking).toBeGreaterThanOrEqual(3);
    expect(review.lines[0]!.findings.map((f) => f.code)).toEqual(expect.arrayContaining(["new-figure", "new-credential", "new-title", "new-name"]));
    // Flipping the declared origin changes nothing.
    expect(codes("Certified PhD who led 500 engineers", "from-claims")).toEqual(codes("Certified PhD who led 500 engineers", "manual-edit"));
    expect(codes("Certified PhD who led 500 engineers", "from-claims").length).toBeGreaterThanOrEqual(2);
    // A verbatim line is clean under either origin.
    expect(codes("Reduced p95 latency of the fictional order API by 38%", "manual-edit")).toEqual([]);
  });
  it("the reviewer's nine inflations are each blocked", () => {
    const cases: [string, string][] = [
      ["Led 95 engineers and cut latency by 38%", "new-figure"],
      ["Senior Director at Google; reduced p95 latency by 38%", "new-title"],
      ["Led twelve engineers; reduced p95 latency by 38%", "new-figure"],
      ["Led １２ engineers; reduced p95 latency by 38%", "new-figure"],
      ["AWS-certified, board-certified architect who reduced p95 latency by 38%", "new-credential"],
      ["Holds a Master's and a Bachelor's; reduced p95 latency by 38%", "new-credential"],
      ["CPA, PMP, CISSP, MD, JD. Reduced p95 latency by 38%", "new-credential"],
      ["M.Sc. and B.Sc. graduate (Stanford); reduced p95 latency by 38%", "new-credential"],
      ["Doubled revenue, tripled the team, won several certifications and awards", "new-figure"],
    ];
    for (const [text, code] of cases) expect(codes(text), text).toContain(code);
    expect(codes("Senior Director at Google; reduced p95 latency by 38%")).toContain("new-name");
    expect(codes("M.Sc. and B.Sc. graduate (Stanford); reduced p95 latency by 38%")).toContain("new-name");
  });
  it("still-correct cases stay as they were", () => {
    for (const text of ["Cut latency by 38.0%", "10x faster order API", "Saved $38M across 95 countries"]) expect(codes(text), text).toContain("new-figure");
    // "38 %" is the same figure as "38%" written with a space; it is not a new figure.
    expect(codes("Cut p95 latency of the fictional order API by 38 %")).toEqual([]);
    expect(codes("Cut p95 latency of the fictional order API by 38%")).toEqual([]);
  });
  it("headings are checked, the label is not exported, and duplicate line ids are caught", () => {
    const base = bundle.resumeVariants[0]!;
    const heading = reviewVariant({ ...base, sections: [{ ...base.sections[0]!, heading: "PhD — Senior Director" }] }, seeded.claims, bundle.clock);
    expect(heading.headingFindings).toHaveLength(1);
    expect(heading.exportBlockers.join(" ")).toContain("blocking");
    expect(variantToText({ ...base, label: "PhD — Stanford University" }, "Synthetic Candidate")).not.toContain("Stanford");
    const b = base.sections[0]!.bullets[0]!;
    const dup: ResumeVariant = { ...base, sections: [{ id: "s", heading: "Experience", bullets: [b, { ...b, text: "Invented", claim_ids: [] }] }] };
    expect(valid(isResumeVariant, dup)).toBe(false);
    expect(reviewVariant(dup, seeded.claims, bundle.clock).byBullet.get(b.id)!.some((f) => f.severity === "block")).toBe(true);
    expect(refusal(seeded, { type: "resume/upsert", variant: { ...dup, id: "rv-dup" } })).toContain("share the id");
  });
});

describe("F-05: 'supported' and 'contradicted' come only from evidence", () => {
  const at = { at: T0, auditId: "a" };
  it("no claim can be set to supported or contradicted by hand", () => {
    for (const id of ["claim-skill-python", "claim-skill-aws", "claim-skill-security"]) {
      expect(refusal(seeded, { type: "claim/setStatus", id, status: "supported", ...at }), id).toContain("by hand");
      expect(refusal(seeded, { type: "claim/setStatus", id, status: "contradicted", ...at }), id).toContain("by hand");
    }
  });
  it("a contradicted claim cannot be relabelled or reworded", () => {
    const c = seeded.claims.find((x) => x.id === "claim-credential-cloud")!;
    expect(c.status).toBe("contradicted");
    expect(refusal(seeded, { type: "claim/setStatus", id: c.id, status: "self-reported", ...at })).toContain("contradicts");
    expect(refusal(seeded, { type: "claim/upsert", claim: { ...c, claim_text: "Fictional Cloud Practitioner Certificate", status: "self-reported", evidence_ids: [] }, ...at })).toContain("contradicts");
  });
  it("rewording a supported claim loses its evidence and its state, and it cannot be put back", () => {
    const c = seeded.claims.find((x) => x.id === "claim-skill-python")!;
    const reworded = { ...c, claim_text: "PhD in Computer Science; Certified Principal Architect; led 500 engineers" };
    expect(refusal(seeded, { type: "claim/upsert", claim: reworded, ...at })).toContain("cannot stay supported");
    expect(refusal(seeded, { type: "claim/upsert", claim: { ...reworded, status: "unverified" }, ...at })).toContain("cannot keep evidence");
    const s = apply(seeded, { type: "claim/upsert", claim: { ...reworded, status: "unverified", evidence_ids: [] }, ...at });
    expect(refusal(s, { type: "claim/setStatus", id: c.id, status: "supported", ...at })).toContain("by hand");
    // A line built from the reworded claim is blocked while it is unverified.
    const line = lintBullet({ id: "b", text: reworded.claim_text, claim_ids: [c.id], origin: "from-claims" }, new Map(s.claims.map((x) => [x.id, x])), bundle.clock);
    expect(line.map((f) => f.code)).toContain("unverified-claim");
  });
  it("a new claim cannot arrive supported or with evidence, and an edit cannot smuggle a status change", () => {
    const fresh: ProfileClaim = { id: "claim-new", subject_id: "s", claim_type: "credential", claim_text: "Invented credential", status: "supported", evidence_ids: [], visibility: "shareable", updated_at: T0, skills: [] };
    expect(refusal(seeded, { type: "claim/upsert", claim: fresh, ...at })).toContain("starts as self-reported or unverified");
    expect(refusal(seeded, { type: "claim/upsert", claim: { ...fresh, status: "self-reported", evidence_ids: ["ev-does-not-exist"] }, ...at })).toContain("Evidence cannot be attached");
    const aws = seeded.claims.find((x) => x.id === "claim-skill-aws")!;
    expect(refusal(seeded, { type: "claim/upsert", claim: { ...aws, status: "supported" }, ...at })).toContain("changed separately");
    expect(refusal(seeded, { type: "claim/upsert", claim: { ...aws, evidence_ids: ["ev-repo-orders"] }, ...at })).toContain("cannot be changed");
  });
});

describe("F-06 / F-07 / F-08: translators copy; they do not clamp, default or invent", () => {
  it("reference-engine matches carry no weight, no compute time and no engine version", () => {
    const raw = sample("e-stub", "matches.json") as Record<string, any>[];
    const [m] = parseList(matchesFromUpstream(raw, { subject: "s" }).payload, isMatchExplanation).items;
    expect(m!.computed_at).toBeNull();
    expect(m!.engine.version).toBe("version not stated");
    for (const c of [...m!.near_term_fit.components, ...m!.strategic_value.components]) expect(c.status === "known" ? c.weight : 0).toBe(c.status === "known" ? null : 0);
  });
  it("out-of-range scores and components are rejected, not clamped", () => {
    const ref = [{ job_id: "j", score: 250, score_type: "relative_heuristic_not_hiring_probability", explanation: { skill_coverage: 7.5, trajectory_score: 0, workstyle: -3 }, missing_skills: [], unknown_inputs: [] }];
    expect(parseList(matchesFromUpstream(ref, { subject: "s" }).payload, isMatchExplanation).items).toEqual([]);
    const a = clone(sample("lane-a", "rank_result.json")) as Record<string, any>;
    a.shortlist[0].components.skill_coverage.value = 42;
    const parsed = parseList(matchesFromUpstream(a, { subject: "s" }).payload, isMatchExplanation);
    expect(parsed.rejected).toHaveLength(1);
    expect(parsed.items).toHaveLength(a.shortlist.length - 1);
  });
  it("out-of-range atlas diagnostics fail validation; unknown dimensionality stays unknown", () => {
    const points = sample("e-stub", "atlas_points.json");
    const good = sample("e-stub", "atlas_diagnostics.json") as Record<string, unknown>;
    const ok = atlasFromArtifacts(points, good, [], T0).payload as Record<string, any>;
    expect(valid(isAtlasProjection, ok)).toBe(true);
    expect(ok.input.n_dims).toBeNull();
    const bad = atlasFromArtifacts(points, { ...good, trustworthiness: 1.7, continuity: -4, information_lost: 9 }, [], T0).payload as Record<string, any>;
    expect(bad.diagnostics.trustworthiness.value).toBe(1.7);
    expect(valid(isAtlasProjection, bad)).toBe(false);
    expect(bad.warnings.join(" ")).not.toContain("900%");
  });
  it("an analysis with impossible counts, a near-miss verdict or a self-contradicting claim is not a result", () => {
    const arms = [{ variant: "A", n_assigned: 10, n_success: 3 }, { variant: "B", n_assigned: 10, n_success: 4 }];
    const bad = [
      { experiment_id: "e", decision: "evidence_for_difference", arms: [{ variant: "A" }, { variant: "B", n_assigned: -5, n_success: 99.5 }], effect: { estimate: 0.02, ci_low: -0.3, ci_high: 0.3 } },
      { experiment_id: "e", verdict: "inconclusive", arms },
      { experiment_id: "e", verdict: "INCONCLUSIVE", arms: [{ variant: "A" }, { variant: "B", n_assigned: 5 }] },
      { experiment_id: "e", verdict: "INCONCLUSIVE", arms: [{ variant: "A", n_assigned: 5, n_success: 9 }, { variant: "B", n_assigned: 5 }] },
      { experiment_id: "e", verdict: "INCONCLUSIVE", arms: [arms[0]] },
      { experiment_id: "e", verdict: "EVIDENCE_FOR_DIFFERENCE", arms, effect: { contrast: "B - A", estimate: 0.02, ci_low: -0.3, ci_high: 0.3 } },
      { experiment_id: "e", verdict: "EVIDENCE_FOR_DIFFERENCE", arms },
      { experiment_id: "e", verdict: "constructor", arms },
    ];
    for (const b of bad) expect(analysisFromUpstream(b).payload, JSON.stringify(b).slice(0, 90)).toBeNull();
    const okay = analysisFromUpstream({ experiment_id: "e", verdict: "INCONCLUSIVE", arms, effect: { contrast: "B - A", estimate: 0.1, ci_low: -0.3, ci_high: 0.5 } }).payload!;
    expect(okay.effect!.ci_level).toBeNull(); // not stated, so not assumed to be 95%
    expect(okay.arms.map((a) => [a.n_assigned, a.n_censored])).toEqual([[10, null], [10, null]]);
  });
});

describe("F-09: sibling files are checked before they are believed", () => {
  it("a matches_full.json from another run is ignored and reported", async () => {
    const full = clone(sample("pipeline", "matches_full.json")) as Record<string, any>;
    const ranked = (sample("pipeline", "matches.json") as { job_id: string }[])[0]!.job_id;
    full.subject_id = "SOMEONE-ELSE";
    full.as_of = "1999-01-01T00:00:00Z";
    full.result_hash = "hash-of-a-different-run";
    full.shortlist = [];
    full.refusals = [{ job_id: ranked, reasons: ["forged"] }];
    const b = await loadBundle(createArtifactSource(reader("pipeline", { "matches_full.json": full })));
    expect(b.matches.length).toBeGreaterThan(0);
    expect(b.matches.every((m) => m.candidate_ref === b.claims[0]!.subject_id)).toBe(true);
    expect(b.matches.some((m) => m.computed_at?.startsWith("1999"))).toBe(false);
    expect(b.matchRefusals).toEqual([]);
    expect(b.sourceNotes.join(" ")).toContain("matches_full.json does not describe the same run");
  });
  it("a refusal never names a posting that is ranked, and facets exist only for validated postings", async () => {
    const good = await loadBundle(createArtifactSource(reader("pipeline")));
    const ranked = new Set(good.matches.map((m) => m.job_id));
    expect(good.matchRefusals.some((r) => ranked.has(r.job_id))).toBe(false);
    const jobs = clone(sample("pipeline", "jobs.json")) as Record<string, any>[];
    const brokenId = jobs[0]!.id as string;
    delete jobs[0]!.url;
    const b = await loadBundle(createArtifactSource(reader("pipeline", { "jobs.json": jobs })));
    expect(b.jobs.some((j) => j.id === brokenId)).toBe(false);
    expect(Object.hasOwn(b.jobFacets, brokenId)).toBe(false);
    expect(Object.keys(b.jobFacets)).toHaveLength(b.jobs.length);
  });
  it("a file in the wrong shape or that cannot be parsed is a reported failure, not 'not provided'", async () => {
    const wrongShape = await loadBundle(createArtifactSource(reader("pipeline", { "events.json": { events: [] } })));
    const events = wrongShape.reports.find((r) => r.adapter.id === "application-events")!;
    expect(events.fatal[0]!.message).toContain("array");
    expect(events.absent).toBe(false);
    const corrupt = await loadBundle(createArtifactSource(reader("pipeline", {}, [], ["claims.json"])));
    const claims = corrupt.reports.find((r) => r.adapter.id === "claims")!;
    expect(claims.absent).toBe(false);
    expect(claims.fatal[0]!.message).toContain("could not be read");
    expect(corrupt.provenance.missing).not.toContain("claims.json");
    expect(corrupt.sourceNotes.join(" ")).toContain("claims.json exists but could not be read");
  });
  it("an empty feed cannot switch off claim expiry", async () => {
    const claims = clone(sample("pipeline", "claims.json")) as ProfileClaim[];
    claims[1] = { ...claims[1]!, valid_to: "2001-01-01T00:00:00Z" };
    const b = await loadBundle(createArtifactSource(reader("pipeline", { "claims.json": claims, "jobs.json": [] }, ["jobs_ext.json", "matches.json", "matches_full.json", "atlas_points.json"])));
    expect(Date.parse(b.clock)).toBeGreaterThan(Date.parse("2020-01-01T00:00:00Z"));
    const line = lintBullet({ id: "b", text: claims[1]!.claim_text, claim_ids: [claims[1]!.id], origin: "from-claims" }, new Map(b.claims.map((c) => [c.id, c])), b.clock);
    expect(line.map((f) => f.code)).toContain("expired-claim");
  });
});

describe("F-10 / F-11: stored data and reducer actions cannot break the invariants", () => {
  const blob = (mutate: (w: Record<string, any>) => void) => {
    const w = clone(seeded) as unknown as Record<string, any>;
    mutate(w);
    return parseWorkspace(JSON.stringify(w));
  };
  const event0 = () => clone(seeded.applicationEvents[0]!) as Record<string, any>;
  it("the unmodified seeded workspace is accepted", () => {
    expect(blob(() => undefined).error).toBeNull();
  });
  it("each tampered workspace from the review is rejected", () => {
    const cases: [string, (w: Record<string, any>) => void][] = [
      ["submission by the system with no approval", (w) => w.applicationEvents.push({ ...event0(), id: "x1", application_id: "app-x", event_type: "submitted", actor: "system" })],
      ["one application, two postings", (w) => w.applicationEvents.push({ ...event0(), id: "x2", event_type: "note", job_id: "fake-ai-2", recorded_at: "2026-11-10T12:00:00Z" })],
      ["duplicate event id", (w) => w.applicationEvents.push(event0())],
      ["a note that cites an approval record", (w) => w.applicationEvents.push({ ...event0(), id: "x3", event_type: "note", approval_token_ref: "FORGED", recorded_at: "2026-11-10T12:00:00Z" })],
      ["events out of order", (w) => w.applicationEvents.push({ ...event0(), id: "x4", application_id: "app-y", event_type: "offer" })],
      ["unit assigned twice", (w) => w.assignments.push({ ...w.assignments[0], id: "asg-again", variant: "skills-first" })],
      ["duplicate assignment id", (w) => w.assignments.push({ ...w.assignments[0], unit_id: "employer-other" })],
      ["variant not in the design", (w) => (w.assignments[0].variant = "made-up")],
      ["assignment for an unknown experiment", (w) => (w.assignments[0].experiment_id = "exp-nope")],
      ["outcome for an unknown assignment", (w) => (w.outcomes[0].assignment_id = "asg-nope")],
      ["running without a locked design", (w) => Object.assign(w.experiments[1], { status: "running" })],
      ["supported claim with no evidence", (w) => (w.claims[0].evidence_ids = [])],
      ["duplicate claim id", (w) => w.claims.push(w.claims[0])],
      ["duplicate variant id", (w) => w.resumeVariants.push(w.resumeVariants[0])],
      ["approved variant whose content changed", (w) => (w.resumeVariants[1].sections[0].bullets[0].text = "PhD, CEO")],
      ["unknown top-level key", (w) => (w.hiring_probability = 0.9)],
      ["negative weights", (w) => (w.simulator.weights.coverage = -5)],
      ["absurd pace", (w) => (w.simulator.hoursPerWeek = 1e-300)],
      ["salt that does not match the locked digest", (w) => (w.experimentSalts["exp-synth-resume-framing"] = "swapped")],
      ["salt for no experiment", (w) => (w.experimentSalts["exp-ghost"] = "s")],
    ];
    for (const [name, mutate] of cases) {
      const r = blob(mutate);
      expect(r.workspace, name).toBeNull();
      expect(r.error, name).toContain("failed validation");
    }
  });
  it("there is no action that replaces the workspace, and the event log cannot shrink", () => {
    const replaced = reduce(seeded, { type: "replace", workspace: { ...seeded, applicationEvents: [] } } as unknown as Action);
    expect(replaced?.state.applicationEvents ?? seeded.applicationEvents).toHaveLength(seeded.applicationEvents.length);
  });
  const draft = (): ExperimentSpec => ({ id: "exp-rc", title: "Test experiment", hypothesis: "Variant A changes the recruiter screen rate versus B.", status: "draft", variants: ["a", "b"], assignment_unit: "job", strata: [], primary_metric: "m", design_locked_at: null, stopping_rule: "Stop when 40 units have a closed outcome window.", consent_required: true, outcome_window_days: 21, analysis_plan: "Difference in proportions with a 95% interval." });
  const lockedSpec = (): ExperimentSpec => ({ ...draft(), status: "running", design_locked_at: T0, seed_digest: sha256Hex("salt-1") });
  const asg = (id: string, unit: string, variant: string): ExperimentAssignment => ({ schema_version: ASSIGNMENT_SCHEMA, id, experiment_id: "exp-rc", unit_id: unit, unit_label: unit, stratum: "all", variant, assigned_at: T0, algorithm: "t", seed_digest: sha256Hex("salt-1"), exposure_logged_at: null });
  const outcome = (assignment_id: string, value: boolean, over: Partial<ExperimentOutcome> = {}): ExperimentOutcome => ({ schema_version: OUTCOME_SCHEMA, assignment_id, experiment_id: "exp-rc", metric: "m", status: "observed", value, recorded_at: T0, data_origin: "synthetic-simulation", ...over });
  it("the salt is fixed at creation, the lock digest must match it, and a closed experiment stays closed", () => {
    let s = apply(seeded, { type: "experiment/upsert", spec: draft(), salt: "salt-1" });
    expect(refusal(s, { type: "experiment/upsert", spec: draft(), salt: "salt-2-chosen-later" })).toContain("cannot be set or replaced");
    expect(refusal(s, { type: "experiment/upsert", spec: { ...lockedSpec(), seed_digest: sha256Hex("another-salt") } })).toContain("digest of this experiment's salt");
    s = apply(s, { type: "experiment/upsert", spec: lockedSpec() });
    expect(refusal(s, { type: "experiment/upsert", spec: lockedSpec(), salt: "salt-2-chosen-later" })).toContain("cannot be set or replaced");
    expect(s.experimentSalts["exp-rc"]).toBe("salt-1");
    expect(refusal(seeded, { type: "experiment/upsert", spec: { ...lockedSpec(), id: "exp-injected" } })).toContain("starts as an unlocked draft");
    s = apply(s, { type: "experiment/assign", assignments: [asg("asg-1", "u1", "a"), asg("asg-2", "u2", "b")] }, { type: "experiment/outcomes", outcomes: [outcome("asg-1", true)] });
    expect(refusal(s, { type: "experiment/assign", assignments: [asg("asg-1", "u3", "b")] })).toContain("already in use");
    expect(refusal(s, { type: "experiment/assign", assignments: [asg("asg-3", "u3", "a"), asg("asg-3", "u4", "b")] })).toContain("already in use");
    expect(refusal(s, { type: "experiment/outcomes", outcomes: [outcome("asg-1", true, { experiment_id: "exp-synth-resume-framing" })] })).toContain("different experiment");
    expect(refusal(s, { type: "experiment/outcomes", outcomes: [outcome("asg-1", true, { metric: "made_up_metric" })] })).toContain("not in the design");
    s = apply(s, { type: "experiment/upsert", spec: { ...lockedSpec(), status: "closed" } });
    expect(refusal(s, { type: "experiment/outcomes", outcomes: [outcome("asg-1", false)] })).toContain("closed");
    expect(refusal(s, { type: "experiment/upsert", spec: { ...lockedSpec(), status: "running" } })).toContain("cannot be reopened");
    expect(refusal(s, { type: "experiment/assign", assignments: [asg("asg-9", "u9", "a")] })).toContain("closed");
  });
});

describe("F-12: date-times", () => {
  it("offsets that are not real offsets are rejected, as the frozen schema's format does", () => {
    for (const bad of ["2026-01-01T00:00:00+99:99", "2026-01-01T00:00:00+24:00", "2026-01-01T00:00:00-23:60"]) {
      expect(isRfc3339(bad), bad).toBe(false);
      expect(Number.isNaN(Date.parse(bad)), bad).toBe(true);
      const job = { ...(bundle.jobs[0] as object), fetched_at: bad };
      const claim = { ...(seeded.claims[0] as object), updated_at: bad };
      expect(valid(isJobPosting, job)).toBe(false);
      expect(valid(isProfileClaim, claim)).toBe(false);
    }
    for (const good of ["2026-01-01T00:00:00+23:59", "2026-01-01T00:00:00-12:00", "2026-01-01T00:00:00Z"]) expect(isRfc3339(good), good).toBe(true);
  });
  it("an unreadable time is never 'fresh' and never 'not expired'", () => {
    expect(freshness("2026-01-01T00:00:00+99:99", T0)).toEqual({ level: "stale", days: 0, label: "Capture time unreadable" });
    const c: ProfileClaim = { ...seeded.claims[0]!, valid_to: "2020-01-01T00:00:00+24:00" };
    expect(lintBullet({ id: "b", text: c.claim_text, claim_ids: [c.id], origin: "from-claims" }, new Map([[c.id, c]]), T0).map((f) => f.code)).toContain("expired-claim");
  });
});

describe("F-17 / F-18: CSV and links", () => {
  it("full-width formula characters are neutralised, semicolons are quoted, real numbers stay numbers", () => {
    for (const s of ["＝1+1", "＋1", "－1", "＠SUM(1)"]) expect(csvCell(s).startsWith("'"), s).toBe(true);
    expect(csvCell("x;=cmd|'/c calc'!A1")).toBe(`"x;=cmd|'/c calc'!A1"`);
    expect([csvCell(-5), csvCell(-0.25), csvCell(12), csvCell(Number.NaN)]).toEqual(["-5", "-0.25", "12", ""]);
    expect(csvCell("-0.25")).toBe("'-0.25");
  });
  it("private-use and wildcard-DNS names are blocked; a non-standard port is pointed out", () => {
    for (const host of ["router.home.arpa", "printer.lan", "intranet.corp", "localhost.localdomain", "kubernetes.default.svc", "127.0.0.1.nip.io", "10.0.0.1.sslip.io", "db.cluster", "x.onion"]) {
      expect(classifyLink(`https://${host}/jobs/1`).kind, host).toBe("blocked");
    }
    const port = classifyLink("https://jobs.example.invalid:22/x");
    expect(port.kind === "ok" && port.notes.some((note) => note.includes("non-standard port (22)"))).toBe(true);
    expect(classifyLink("https://careers.example.invalid/x").kind).toBe("ok");
  });
});

describe("F-19: ids that collide with object internals", () => {
  it("a scenario or experiment called 'constructor' behaves like any other", () => {
    const view = clone(bundle.scenarios[0]!);
    view.scenario.id = "constructor";
    expect(() => compareScenarios([view], {}, DEFAULT_SIMULATOR)).not.toThrow();
    expect(compareScenarios([view], {}, DEFAULT_SIMULATOR)[0]!.edited).toBe(false);
    const spec: ExperimentSpec = { ...seeded.experiments[1]!, id: "hasOwnProperty" };
    const s = apply(seeded, { type: "experiment/upsert", spec });
    expect(Object.hasOwn(s.experimentSalts, "hasOwnProperty")).toBe(false);
    expect(refusal(s, { type: "experiment/upsert", spec: { ...spec, status: "preregistered", design_locked_at: T0, seed_digest: "x" } })).toContain("no salt");
  });
});

describe("F-16: axis ticks always terminate", () => {
  it("returns a short list for ordinary, huge, degenerate and non-finite ranges", () => {
    expect(niceTicks(-1.5, 2)).toEqual([-1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2]);
    for (const [min, max] of [[1e16, 1e16 + 2], [5e15, 5e15], [2 ** 53, 2 ** 53], [-1e300, 1e300], [0, 0], [3, 3]] as const) {
      const t = niceTicks(min, max);
      expect(t.length, `${min}..${max}`).toBeLessThanOrEqual(12);
      expect(t.every(Number.isFinite)).toBe(true);
    }
    expect(niceTicks(Number.NaN, 1)).toEqual([]);
    expect(niceTicks(0, Number.POSITIVE_INFINITY)).toEqual([]);
    expect(niceTicks(2, 1)).toEqual([]);
  });
});

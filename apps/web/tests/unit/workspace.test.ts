/**
 * Workspace reducer invariants, persistence hardening, and the read-models
 * (applications, experiments, simulator) built on top of it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { loadBundle, type DataBundle, type PayloadName } from "@/adapters";
import { ASSIGNMENT_SCHEMA, OUTCOME_SCHEMA, type ExperimentAssignment, type ExperimentOutcome, type ResumeVariant } from "@/adapters/proposed";
import type { ApplicationEvent, ApplicationEventType, ExperimentSpec } from "@/contracts/types";
import { allowedNext, daysWithoutFeedback, groupApplications } from "@/lib/applications";
import { designProblems, plannedPerArmFromRule, summariseExperiment } from "@/lib/experimentView";
import { profileDigest } from "@/lib/profileDigest";
import { resumeContentDigest } from "@/lib/resumeDigest";
import { compareScenarios, defaultEdits } from "@/lib/simulator";
import { DEFAULT_SIMULATOR, STORAGE_KEY, loadWorkspace, parseSettings, parseWorkspace, reduce, saveWorkspace, seedWorkspace, type Action, type Workspace } from "@/state/workspace";

const dir = join(__dirname, "..", "..", "src", "fixtures", "generated");
let bundle: DataBundle;
let seeded: Workspace;
beforeAll(async () => {
  bundle = await loadBundle({ id: "t", label: "t", load: async (name: PayloadName) => JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8")) as unknown });
  seeded = seedWorkspace(bundle, "fixture");
});

const apply = (state: Workspace, ...actions: Action[]) => actions.reduce((s, a) => {
  const r = reduce(s, a);
  if (r.refused) throw new Error(`unexpected refusal: ${r.refused}`);
  return r.state;
}, state);
let n = 0;
const ev = (application_id: string, job_id: string, event_type: ApplicationEventType, over: Partial<ApplicationEvent> = {}): ApplicationEvent => ({
  id: `evt-test-${++n}`, application_id, job_id, event_type, recorded_at: "2026-11-10T12:00:00Z", occurred_at: "2026-11-10T12:00:00Z", actor: "user", source: "manual-entry",
  outcome_status: "self-reported", resume_variant_id: null, experiment_id: null, approval_token_ref: null, notes: "", ...over,
});
const approvedVariant = (id: string): ResumeVariant => {
  const base = bundle.resumeVariants[0]!;
  const sections = base.sections.filter((s) => s.heading === "Experience");
  return { ...base, id, sections, approval: { approved_at: "2026-11-10T12:00:00Z", approval_token_ref: `approval-${id}`, content_digest: resumeContentDigest(sections) } };
};

describe("application events are append-only and gated", () => {
  it("fixture events group into the expected stages", () => {
    const apps = groupApplications(seeded.applicationEvents);
    const stage = Object.fromEntries(apps.map((a) => [a.id, a.closedAs ?? a.stage]));
    expect(stage).toEqual({ "app-synth-001": "interview", "app-synth-002": "submitted", "app-synth-003": "rejected", "app-synth-004": "saved", "app-synth-005": "drafted", "app-synth-006": "withdrawn" });
    const waiting = apps.find((a) => a.id === "app-synth-002")!;
    expect(daysWithoutFeedback(waiting, bundle.clock)).toBe(28);
    // A rejected or interviewing application is not "missing feedback".
    expect(apps.filter((a) => daysWithoutFeedback(a, bundle.clock) !== null).map((a) => a.id)).toEqual(["app-synth-002"]);
    expect(allowedNext(apps.find((a) => a.id === "app-synth-003")!)).toEqual(["note"]);
  });

  it("no reducer action can edit or delete an existing event", () => {
    const before = JSON.stringify(seeded.applicationEvents);
    let s = apply(seeded, { type: "event/append", event: ev("app-new", "fake-fde-1", "saved") });
    s = apply(s, { type: "event/append", event: ev("app-new", "fake-fde-1", "note", { notes: "a correction is a new note" }) });
    expect(JSON.stringify(s.applicationEvents.slice(0, seeded.applicationEvents.length))).toBe(before);
    expect(s.applicationEvents).toHaveLength(seeded.applicationEvents.length + 2);
    expect(reduce(s, { type: "event/append", event: s.applicationEvents[0]! }).refused).toContain("Duplicate");
  });

  it("refuses out-of-order stages", () => {
    const s = apply(seeded, { type: "event/append", event: ev("app-x", "fake-fde-1", "saved") });
    for (const t of ["approved", "submitted", "interview", "offer", "rejected", "recruiter_screen"] as const) {
      expect(reduce(s, { type: "event/append", event: ev("app-x", "fake-fde-1", t, { approval_token_ref: "a", resume_variant_id: "rv-base" }) }).refused, t).toBeTruthy();
    }
    expect(reduce(seeded, { type: "event/append", event: ev("app-y", "fake-fde-1", "drafted") }).refused).toBeTruthy();
    expect(reduce(s, { type: "event/append", event: ev("app-x", "fake-ai-2", "note") }).refused).toContain("cannot change job");
  });

  it("'approved' needs the user's approval of the exact résumé content", () => {
    let s = apply(seeded, { type: "event/append", event: ev("app-z", "fake-fde-1", "saved") }, { type: "event/append", event: ev("app-z", "fake-fde-1", "drafted", { resume_variant_id: "rv-base" }) });
    // rv-base has never been approved.
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-base", approval_token_ref: "made-up" }) }).refused).toContain("not been approved");
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-base" }) }).refused).toContain("approval record");
    const v = approvedVariant("rv-test");
    s = apply(s, { type: "resume/upsert", variant: v });
    // Wrong token, system actor, and stale digest are all refused.
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-test", approval_token_ref: "someone-elses-token" }) }).refused).toContain("does not belong");
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-test", approval_token_ref: v.approval!.approval_token_ref, actor: "system" }) }).refused).toContain("Only you");
    const tampered = apply(s, { type: "resume/upsert", variant: { ...v, sections: [{ ...v.sections[0]!, bullets: [...v.sections[0]!.bullets, { id: "b-new", text: "Invented line", claim_ids: [], origin: "manual-edit" }] }] } });
    expect(reduce(tampered, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-test", approval_token_ref: v.approval!.approval_token_ref }) }).refused).toContain("changed after you approved");
    // The legitimate path works, and "submitted" must cite the same approval.
    s = apply(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "approved", { resume_variant_id: "rv-test", approval_token_ref: v.approval!.approval_token_ref }) });
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "submitted", { resume_variant_id: "rv-test", approval_token_ref: "different" }) }).refused).toContain("same approval");
    expect(reduce(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "submitted", { resume_variant_id: "rv-test", approval_token_ref: v.approval!.approval_token_ref, actor: "system" }) }).refused).toContain("Only you");
    s = apply(s, { type: "event/append", event: ev("app-z", "fake-fde-1", "submitted", { resume_variant_id: "rv-test", approval_token_ref: v.approval!.approval_token_ref }) });
    expect(groupApplications(s.applicationEvents).find((a) => a.id === "app-z")!.stage).toBe("submitted");
    // A variant cited by an event cannot be deleted.
    expect(reduce(s, { type: "resume/remove", id: "rv-test" }).refused).toContain("referenced");
  });
});

describe("evidence vault rules", () => {
  it("a claim cannot be marked supported without evidence", () => {
    const r = reduce(seeded, { type: "claim/setStatus", id: "claim-skill-aws", status: "supported", at: "2026-11-10T12:00:00Z", auditId: "a1" });
    expect(r.refused).toContain("without at least one evidence reference");
    const okChange = reduce(seeded, { type: "claim/setStatus", id: "claim-skill-security", status: "self-reported", at: "2026-11-10T12:00:00Z", auditId: "a2" });
    expect(okChange.refused).toBeUndefined();
    expect(okChange.state.audit.at(-1)).toMatchObject({ action: "claim.status", detail: "claim-skill-security: unverified -> self-reported" });
    expect(reduce(seeded, { type: "claim/setStatus", id: "nope", status: "unverified", at: "x", auditId: "a3" }).refused).toBeTruthy();
  });
  it("changing the vault changes the profile digest (so match scores are flagged stale)", () => {
    const snapshot = bundle.matches[0]!.profile_digest;
    expect(profileDigest(seeded.claims)).toBe(snapshot);
    const changed = apply(seeded, { type: "claim/setStatus", id: "claim-skill-security", status: "self-reported", at: "2026-11-10T12:00:00Z", auditId: "a4" });
    expect(profileDigest(changed.claims)).not.toBe(snapshot);
    const removed = apply(seeded, { type: "claim/remove", id: "claim-skill-python", at: "2026-11-10T12:00:00Z", auditId: "a5" });
    expect(profileDigest(removed.claims)).not.toBe(snapshot);
  });
});

describe("experiment rules", () => {
  const draft = (over: Partial<ExperimentSpec> = {}): ExperimentSpec => ({
    id: "exp-t", title: "Test experiment", hypothesis: "Variant A changes the recruiter screen rate versus B.", status: "draft", variants: ["a", "b"], assignment_unit: "employer-cluster", strata: [],
    primary_metric: "recruiter_screen_within_window", design_locked_at: null, stopping_rule: "Stop when 40 units have a closed outcome window. No interim decisions.", consent_required: true, outcome_window_days: 21,
    analysis_plan: "Difference in proportions with a 95% interval on the primary metric.", ...over,
  });
  const asg = (unit: string, variant: string, over: Partial<ExperimentAssignment> = {}): ExperimentAssignment => ({ schema_version: ASSIGNMENT_SCHEMA, id: `asg-${unit}`, experiment_id: "exp-t", unit_id: unit, unit_label: unit, stratum: "all", variant, assigned_at: "2026-11-10T12:00:00Z", algorithm: "t", seed_digest: "d", exposure_logged_at: null, ...over });
  const locked = () => draft({ status: "preregistered", design_locked_at: "2026-11-10T12:00:00Z", seed_digest: "d" });

  it("design problems block locking", () => {
    expect(designProblems(draft())).toEqual([]);
    expect(designProblems(draft({ hypothesis: "short", variants: ["a", "a"], stopping_rule: "whenever", analysis_plan: "", consent_required: false, title: "" })).length).toBe(6);
    expect(plannedPerArmFromRule(draft())).toBe(20);
    expect(plannedPerArmFromRule(draft({ stopping_rule: "none" }))).toBeNull();
  });

  it("units cannot be assigned before the design is locked, and never twice", () => {
    let s = apply(seeded, { type: "experiment/upsert", spec: draft(), salt: "salt" });
    expect(reduce(s, { type: "experiment/assign", assignments: [asg("u1", "a")] }).refused).toContain("before the design is locked");
    expect(reduce(s, { type: "experiment/upsert", spec: draft({ status: "running" }) }).refused).toContain("cannot leave draft");
    s = apply(s, { type: "experiment/upsert", spec: locked() }, { type: "experiment/assign", assignments: [asg("u1", "a"), asg("u2", "b")] });
    expect(reduce(s, { type: "experiment/assign", assignments: [asg("u1", "b", { id: "asg-again" })] }).refused).toContain("no crossover");
    expect(reduce(s, { type: "experiment/assign", assignments: [asg("u3", "c")] }).refused).toContain("not in the design");
    expect(reduce(s, { type: "experiment/assign", assignments: [asg("u3", "a"), asg("u3", "b", { id: "asg-dup" })] }).refused).toContain("no crossover");
    const closed = apply(s, { type: "experiment/upsert", spec: { ...locked(), status: "closed" } });
    expect(reduce(closed, { type: "experiment/assign", assignments: [asg("u9", "a")] }).refused).toContain("closed");
  });

  it("a locked design cannot be edited, un-locked or deleted, but can change status", () => {
    const s = apply(seeded, { type: "experiment/upsert", spec: draft(), salt: "salt" }, { type: "experiment/upsert", spec: locked() });
    for (const change of [{ hypothesis: "A different hypothesis written after seeing the data." }, { variants: ["a", "b", "c"] }, { primary_metric: "any_reply_within_window" }, { stopping_rule: "Stop when 10 units." }, { outcome_window_days: 3 }, { design_locked_at: null }, { analysis_plan: "p-hack" }] as Partial<ExperimentSpec>[]) {
      expect(reduce(s, { type: "experiment/upsert", spec: { ...locked(), ...change } }).refused, JSON.stringify(change)).toContain("locked");
    }
    expect(reduce(s, { type: "experiment/upsert", spec: { ...locked(), status: "draft" } }).refused).toBeTruthy();
    expect(reduce(s, { type: "experiment/remove", id: "exp-t" }).refused).toContain("cannot be deleted");
    expect(reduce(s, { type: "experiment/upsert", spec: { ...locked(), status: "running" } }).refused).toBeUndefined();
    // The fixture experiment is locked too.
    const fixture = seeded.experiments.find((e) => e.id === "exp-synth-resume-framing")!;
    expect(reduce(seeded, { type: "experiment/upsert", spec: { ...fixture, variants: ["x", "y"] } }).refused).toContain("locked");
  });

  it("exposure is logged once; outcomes need a known assignment", () => {
    let s = apply(seeded, { type: "experiment/upsert", spec: draft(), salt: "salt" }, { type: "experiment/upsert", spec: locked() }, { type: "experiment/assign", assignments: [asg("u1", "a")] });
    s = apply(s, { type: "experiment/exposure", assignmentId: "asg-u1", at: "2026-11-10T13:00:00Z" });
    expect(reduce(s, { type: "experiment/exposure", assignmentId: "asg-u1", at: "2026-11-10T14:00:00Z" }).refused).toContain("already logged");
    const outcome: ExperimentOutcome = { schema_version: OUTCOME_SCHEMA, assignment_id: "asg-nope", experiment_id: "exp-t", metric: "recruiter_screen_within_window", status: "observed", value: true, recorded_at: "2026-11-10T12:00:00Z", data_origin: "synthetic-simulation" };
    expect(reduce(s, { type: "experiment/outcomes", outcomes: [outcome] }).refused).toContain("unknown assignment");
  });

  it("results keep censored and missing units out of the rates and never name a winner on the fixture", () => {
    const spec = seeded.experiments.find((e) => e.id === "exp-synth-resume-framing")!;
    const r = summariseExperiment(spec, seeded.assignments, seeded.outcomes, plannedPerArmFromRule(spec));
    expect(r.totalAssigned).toBe(14);
    expect(r.totalObserved).toBe(9);
    expect(r.totalCensored).toBe(3);
    expect(r.totalMissing).toBe(2);
    expect(r.arms.reduce((s, a) => s + a.observed + a.censored + a.missing, 0)).toBe(14);
    for (const a of r.arms) if (a.rate !== null) expect(a.rate).toBe(a.successes / a.observed);
    expect(r.evidence.level).toBe("insufficient");
    expect(r.origins).toEqual(["synthetic-simulation"]);
    // An assigned unit with no outcome record at all is counted as missing, not as a failure.
    const fewer = summariseExperiment(spec, seeded.assignments, seeded.outcomes.slice(1), 20);
    expect(fewer.unrecorded).toBe(1);
    expect(fewer.totalMissing).toBe(3);
    expect(fewer.totalObserved).toBe(8);
  });
});

describe("simulator arithmetic", () => {
  it("reproduces upstream coverage from the upstream actions", () => {
    const rows = compareScenarios(bundle.scenarios, {}, DEFAULT_SIMULATOR);
    for (const r of rows) {
      expect(r.inconsistent).toBe(false);
      expect(r.coverageAfter).toBeCloseTo(r.view.outputs!.coverage_after, 3);
      expect(r.cost).toBeNull();
      expect(r.edited).toBe(false);
      expect(r.totalHours).toBe(r.view.scenario.planned_actions.reduce((s, a) => s + a.effort_hours, 0));
    }
  });
  it("uses the user's edits for hours, cost and coverage, and never invents a cost", () => {
    const view = bundle.scenarios.find((s) => s.scenario.id === "scenario-field-cto")!;
    const edits = defaultEdits(view.scenario).map((a, i) => (i === 0 ? { ...a, included: false } : i === 1 ? { ...a, effort_hours: 10, cost: 500 } : a));
    const [row] = compareScenarios([view], { "scenario-field-cto": edits }, { hoursPerWeek: 10, weights: { coverage: 1, effort: 0, leadership: 0 } });
    expect(row!.edited).toBe(true);
    expect(row!.totalHours).toBe(10 + 100 + 80 + 90);
    expect(row!.months).toBeCloseTo(280 / 10 / (52 / 12), 6);
    expect(row!.cost).toBe(500);
    expect(row!.costPartial).toBe(true);
    expect(row!.coverageAfter).toBeCloseTo(4 / 7, 6);
    expect(row!.priority).toBeCloseTo(4 / 7, 6);
  });
  it("ignores stored edits that no longer match the upstream action list, and yields no priority with zero weights", () => {
    const view = bundle.scenarios[0]!;
    const [row] = compareScenarios([view], { [view.scenario.id]: [{ skill: "something-else", effort_hours: 1, included: true, cost: null }] }, { hoursPerWeek: 6, weights: { coverage: 0, effort: 0, leadership: 0 } });
    expect(row!.edited).toBe(false);
    expect(row!.priority).toBeNull();
  });
});

describe("persistence hardening", () => {
  const memory = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), map: m };
  };
  it("round-trips a workspace", () => {
    const store = memory();
    expect(loadWorkspace(store)).toEqual({ workspace: null, error: null });
    expect(saveWorkspace(store, seeded)).toBeNull();
    expect(loadWorkspace(store).workspace).toEqual(seeded);
  });
  it("rejects corrupt, tampered or wrong-version data instead of trusting it", () => {
    const good = JSON.parse(JSON.stringify(seeded)) as Record<string, any>;
    const cases: [string, string][] = [
      ["{not json", "not valid JSON"],
      ["[]", "wrong shape"],
      [JSON.stringify({ ...good, schema: 99 }), "schema"],
      [JSON.stringify({ ...good, applicationEvents: [{ ...good.applicationEvents[0], event_type: "auto_submitted" }] }), "application events"],
      [JSON.stringify({ ...good, claims: [{ ...good.claims[0], status: "verified-by-ai" }] }), "claims"],
      [JSON.stringify({ ...good, shortlist: [1, 2] }), "shortlist"],
      [JSON.stringify({ ...good, strategicBlend: 500 }), "blend"],
      [JSON.stringify({ ...good, scenarioEdits: { x: [{ skill: "a", effort_hours: -5, included: true, cost: null }] } }), "scenario edits"],
      [JSON.stringify({ ...good, resumeVariants: [{ ...good.resumeVariants[0], sections: "<script>" }] }), "variants"],
      [JSON.stringify({ ...good, reminders: [{ id: "r", application_id: "a", due_on: "tomorrow", text: "t", done: false }] }), "reminders"],
      [JSON.stringify({ ...good, simulator: { hoursPerWeek: 0, weights: { coverage: 1, effort: 1, leadership: 1 } } }), "simulator"],
    ];
    for (const [raw, expected] of cases) {
      const r = parseWorkspace(raw);
      expect(r.workspace, raw.slice(0, 60)).toBeNull();
      expect(r.error).toContain(expected);
    }
  });
  it("survives storage that throws", () => {
    expect(loadWorkspace({ getItem: () => { throw new Error("denied"); } }).error).toContain("not available");
    expect(saveWorkspace({ setItem: () => { throw new Error("quota"); } }, seeded)).toContain("Could not save");
    expect(STORAGE_KEY).toBe("careertwin.workspace.v1");
  });
  it("settings fall back to safe defaults", () => {
    expect(parseSettings(null)).toEqual({ theme: "system", motion: "system", simulate: "none" });
    expect(parseSettings("{bad")).toEqual({ theme: "system", motion: "system", simulate: "none" });
    expect(parseSettings(JSON.stringify({ theme: "neon", motion: "reduce", simulate: "explode" }))).toEqual({ theme: "system", motion: "reduce", simulate: "none" });
  });
  it("an empty seed starts with nothing and a fixture seed never pre-fills the shortlist", () => {
    const empty = seedWorkspace(bundle, "empty");
    expect([empty.claims, empty.applicationEvents, empty.experiments, empty.resumeVariants, empty.assignments].every((l) => l.length === 0)).toBe(true);
    expect(seeded.shortlist).toEqual([]);
  });
});

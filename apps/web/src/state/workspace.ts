/**
 * Local workspace state: everything the user creates or changes in the browser.
 * Pure reducer + versioned localStorage persistence. No network, ever.
 *
 * Invariants enforced here (and unit-tested):
 *  - application events are append-only; no action edits or removes one;
 *  - an event that breaks the stage gate is refused (state unchanged);
 *  - "submitted" needs an approval token and is only ever a user-recorded fact;
 *  - a locked (preregistered) experiment design cannot be edited;
 *  - a unit is assigned at most once per experiment (no crossover);
 *  - an approved résumé variant is a locked snapshot and cannot be changed;
 *  - "supported" and "contradicted" are never self-declared;
 *  - a stored workspace is replayed through the same rules before it is trusted.
 */
import type { DataBundle } from "@/adapters";
import { isApplicationEvent, isExperimentSpec, isProfileClaim } from "@/adapters/frozen";
import type { ExperimentAssignment, ExperimentOutcome, ResumeVariant } from "@/adapters/proposed";
import { isExperimentAssignment, isExperimentOutcome, isResumeVariant } from "@/adapters/proposedValidators";
import { Checker } from "@/adapters/validate";
import type { ApplicationEvent, ClaimStatus, ExperimentSpec, ProfileClaim } from "@/contracts/types";
import { canAppend, dayOf, eventTime, groupApplications, sortEvents, timeOf } from "@/lib/applications";
import { sha256Hex } from "@/lib/sha256";
import { resumeContentDigest } from "@/lib/resumeDigest";

export const WORKSPACE_SCHEMA = 1 as const;
export const STORAGE_KEY = "careertwin.workspace.v1";
export const SETTINGS_KEY = "careertwin.settings.v1";

export interface Reminder {
  id: string;
  application_id: string;
  due_on: string; // YYYY-MM-DD
  text: string;
  done: boolean;
}

export interface ScenarioActionEdit {
  skill: string;
  effort_hours: number;
  included: boolean;
  /** User-entered direct cost. Null = not entered. Never defaulted. */
  cost: number | null;
}

export interface AuditEntry {
  id: string;
  at: string;
  action: string;
  detail: string;
}

export interface SimulatorSettings {
  hoursPerWeek: number;
  weights: { coverage: number; effort: number; leadership: number };
}

export interface Workspace {
  schema: typeof WORKSPACE_SCHEMA;
  seed: "fixture" | "empty";
  shortlist: string[];
  claims: ProfileClaim[];
  resumeVariants: ResumeVariant[];
  applicationEvents: ApplicationEvent[];
  reminders: Reminder[];
  experiments: ExperimentSpec[];
  /** Local salts for experiments created in this workspace (id -> salt). */
  experimentSalts: Record<string, string>;
  assignments: ExperimentAssignment[];
  outcomes: ExperimentOutcome[];
  scenarioEdits: Record<string, ScenarioActionEdit[]>;
  simulator: SimulatorSettings;
  /** 0 = rank purely by near-term fit, 100 = purely by strategic value. */
  strategicBlend: number;
  audit: AuditEntry[];
}

export const DEFAULT_SIMULATOR: SimulatorSettings = { hoursPerWeek: 6, weights: { coverage: 40, effort: 30, leadership: 30 } };

export function seedWorkspace(bundle: DataBundle, seed: "fixture" | "empty"): Workspace {
  const fixture = seed === "fixture";
  return {
    schema: WORKSPACE_SCHEMA,
    seed,
    shortlist: [],
    claims: fixture ? bundle.claims : [],
    resumeVariants: fixture ? bundle.resumeVariants : [],
    applicationEvents: fixture ? bundle.applicationEvents : [],
    reminders: [],
    experiments: fixture ? bundle.experiments : [],
    experimentSalts: {},
    assignments: fixture ? bundle.assignments : [],
    outcomes: fixture ? bundle.outcomes : [],
    scenarioEdits: {},
    simulator: DEFAULT_SIMULATOR,
    strategicBlend: 30,
    audit: [],
  };
}

export type Action =
  | { type: "shortlist/toggle"; jobId: string }
  | { type: "claim/upsert"; claim: ProfileClaim; at: string; auditId: string }
  | { type: "claim/setStatus"; id: string; status: ClaimStatus; at: string; auditId: string }
  | { type: "claim/remove"; id: string; at: string; auditId: string }
  | { type: "resume/upsert"; variant: ResumeVariant }
  | { type: "resume/remove"; id: string }
  | { type: "event/append"; event: ApplicationEvent }
  | { type: "reminder/add"; reminder: Reminder }
  | { type: "reminder/toggle"; id: string }
  | { type: "reminder/remove"; id: string }
  | { type: "experiment/upsert"; spec: ExperimentSpec; salt?: string }
  | { type: "experiment/remove"; id: string }
  | { type: "experiment/assign"; assignments: ExperimentAssignment[] }
  | { type: "experiment/exposure"; assignmentId: string; at: string }
  | { type: "experiment/outcomes"; outcomes: ExperimentOutcome[] }
  | { type: "scenario/edit"; scenarioId: string; actions: ScenarioActionEdit[] }
  | { type: "scenario/reset"; scenarioId: string }
  | { type: "simulator/set"; settings: SimulatorSettings }
  | { type: "blend/set"; value: number };

export interface ReduceResult {
  state: Workspace;
  /** Set when the action was refused; state is then unchanged. */
  refused?: string;
}

const audit = (state: Workspace, id: string, at: string, action: string, detail: string): AuditEntry[] =>
  [...state.audit, { id, at, action, detail }].slice(-500);

/** Fields that define an experiment design; frozen once `design_locked_at` is set. */
const DESIGN_FIELDS = ["title", "hypothesis", "variants", "assignment_unit", "strata", "primary_metric", "stopping_rule", "consent_required", "outcome_window_days", "secondary_metrics", "analysis_plan", "seed_digest", "design_locked_at"] as const;

/** Truth states a user may choose. "supported" and "contradicted" come only from evidence. */
export const USER_SETTABLE_STATUSES: readonly ClaimStatus[] = ["self-reported", "unverified"];

const own = <T,>(record: Record<string, T>, key: string): T | undefined => (Object.hasOwn(record, key) ? record[key] : undefined);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const duplicate = (ids: string[]): string | null => {
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) return id;
    seen.add(id);
  }
  return null;
};
const bulletIds = (v: ResumeVariant) => v.sections.flatMap((s) => s.bullets.map((b) => b.id));

export function reduce(state: Workspace, action: Action): ReduceResult {
  switch (action.type) {
    case "shortlist/toggle": {
      const on = state.shortlist.includes(action.jobId);
      return { state: { ...state, shortlist: on ? state.shortlist.filter((j) => j !== action.jobId) : [...state.shortlist, action.jobId] } };
    }

    case "claim/upsert": {
      const next = action.claim;
      const before = state.claims.find((c) => c.id === next.id);
      if (!before) {
        // A new claim is the user's statement. It cannot arrive already "supported" or with evidence attached.
        if (!USER_SETTABLE_STATUSES.includes(next.status)) return { state, refused: "A new claim starts as self-reported or unverified. Only evidence can make a claim supported." };
        if (next.evidence_ids.length > 0) return { state, refused: "Evidence cannot be attached in this build." };
        return { state: { ...state, claims: [...state.claims, next], audit: audit(state, action.auditId, action.at, "claim.added", next.id) } };
      }
      if (before.status === "contradicted") {
        return { state, refused: "Evidence contradicts this claim, so it cannot be edited. Remove it and add a new claim if you want to state something different." };
      }
      if (next.claim_text !== before.claim_text) {
        // Evidence was about the old wording. A reworded claim keeps none of it and cannot stay supported.
        if (!USER_SETTABLE_STATUSES.includes(next.status)) return { state, refused: "A reworded claim cannot stay supported: the evidence was about the old wording." };
        if (next.evidence_ids.length > 0) return { state, refused: "A reworded claim cannot keep evidence that was attached to the old wording." };
        if (before.status === "supported" && next.status !== "unverified") return { state, refused: "A reworded supported claim becomes unverified." };
      } else {
        if (next.status !== before.status) return { state, refused: "Truth state is changed separately, not by editing the claim." };
        if (!same(next.evidence_ids, before.evidence_ids)) return { state, refused: "Evidence references cannot be changed in this build." };
      }
      return { state: { ...state, claims: state.claims.map((c) => (c.id === next.id ? next : c)), audit: audit(state, action.auditId, action.at, "claim.edited", next.id) } };
    }
    case "claim/setStatus": {
      const before = state.claims.find((c) => c.id === action.id);
      if (!before) return { state, refused: "Claim not found." };
      // "Supported" and "contradicted" are conclusions drawn from evidence. They are never self-declared (finding F-05).
      if (action.status === "supported") return { state, refused: "A claim cannot be marked supported by hand. Only evidence can make a claim supported." };
      if (action.status === "contradicted") return { state, refused: "A claim cannot be marked contradicted by hand. Only evidence can do that." };
      if (before.status === "contradicted") return { state, refused: "Evidence contradicts this claim. Its state cannot be changed; remove the claim instead." };
      if (action.status === before.status) return { state };
      const claims = state.claims.map((c) => (c.id === action.id ? { ...c, status: action.status, updated_at: action.at } : c));
      return { state: { ...state, claims, audit: audit(state, action.auditId, action.at, "claim.status", `${action.id}: ${before.status} -> ${action.status}`) } };
    }
    case "claim/remove": {
      if (!state.claims.some((c) => c.id === action.id)) return { state, refused: "Claim not found." };
      return { state: { ...state, claims: state.claims.filter((c) => c.id !== action.id), audit: audit(state, action.auditId, action.at, "claim.removed", action.id) } };
    }

    case "resume/upsert": {
      const next = action.variant;
      const before = state.resumeVariants.find((v) => v.id === next.id);
      // An approved variant is a locked snapshot. Nothing about it may change (finding F-01).
      if (before && before.approval !== null) {
        return same(before, next) ? { state } : { state, refused: "An approved variant is locked. Create a new version to change it." };
      }
      const dup = duplicate(bulletIds(next));
      if (dup !== null) return { state, refused: `Two lines share the id '${dup}'.` };
      if (next.approval !== null && next.approval.content_digest !== resumeContentDigest(next.sections)) {
        return { state, refused: "The approval does not match this exact content." };
      }
      return { state: { ...state, resumeVariants: before ? state.resumeVariants.map((v) => (v.id === next.id ? next : v)) : [...state.resumeVariants, next] } };
    }
    case "resume/remove": {
      if (state.applicationEvents.some((e) => e.resume_variant_id === action.id)) {
        return { state, refused: "This variant is referenced by an application event and cannot be deleted." };
      }
      return { state: { ...state, resumeVariants: state.resumeVariants.filter((v) => v.id !== action.id) } };
    }

    case "event/append": {
      const e = action.event;
      if (state.applicationEvents.some((x) => x.id === e.id)) return { state, refused: "Duplicate event id." };
      const app = groupApplications(state.applicationEvents).find((a) => a.id === e.application_id) ?? null;
      if (app && app.job_id !== e.job_id) return { state, refused: "An application cannot change job." };
      if (!canAppend(app, e.event_type)) {
        return { state, refused: `'${e.event_type}' is not allowed from stage '${app?.stage ?? "none"}'.` };
      }
      // The log is in recorded order; a new record cannot be dated before the last one.
      if (app && timeOf(e.recorded_at) < timeOf(app.lastEvent.recorded_at)) return { state, refused: "The record time goes backwards." };
      const gated = e.event_type === "approved" || e.event_type === "submitted";
      if (!gated && e.approval_token_ref) return { state, refused: "Only the approval and submission steps may cite an approval record." };
      if (e.event_type === "note" && e.resume_variant_id) return { state, refused: "A note cannot change the résumé variant." };
      if (e.event_type !== "note" && app?.lastStageDay && dayOf(eventTime(e)) < app.lastStageDay) {
        return { state, refused: `This step cannot have happened before the previous one (${app.lastStageDay}).` };
      }
      if (e.event_type === "drafted") {
        if (!e.resume_variant_id || !state.resumeVariants.some((v) => v.id === e.resume_variant_id)) return { state, refused: "Choose an existing résumé variant to draft with." };
      }
      if (gated) {
        if (!e.approval_token_ref) return { state, refused: "This step needs your explicit approval record." };
        if (!e.resume_variant_id) return { state, refused: "This step needs an approved résumé variant." };
        if (e.actor !== "user") return { state, refused: "Only you can approve or record a submission. The system never does." };
        const variant = state.resumeVariants.find((v) => v.id === e.resume_variant_id);
        if (!variant || variant.approval === null) return { state, refused: "The résumé variant has not been approved by you." };
        if (variant.approval.approval_token_ref !== e.approval_token_ref) return { state, refused: "The approval record does not belong to this résumé variant." };
        if (variant.approval.content_digest !== resumeContentDigest(variant.sections)) {
          return { state, refused: "The résumé variant changed after you approved it. Approve it again first." };
        }
      }
      if (e.event_type === "approved" && app?.resumeVariantId !== e.resume_variant_id) {
        return { state, refused: "Approve the résumé variant this application was drafted with." };
      }
      if (e.event_type === "submitted" && (app?.approvalTokenRef !== e.approval_token_ref || app?.approvedVariantId !== e.resume_variant_id)) {
        return { state, refused: "The submission must cite the same approval and résumé variant as the approved step." };
      }
      return { state: { ...state, applicationEvents: [...state.applicationEvents, e] } };
    }

    case "reminder/add":
      return { state: { ...state, reminders: [...state.reminders, action.reminder] } };
    case "reminder/toggle":
      return { state: { ...state, reminders: state.reminders.map((r) => (r.id === action.id ? { ...r, done: !r.done } : r)) } };
    case "reminder/remove":
      return { state: { ...state, reminders: state.reminders.filter((r) => r.id !== action.id) } };

    case "experiment/upsert": {
      const next = action.spec;
      const before = state.experiments.find((x) => x.id === next.id);
      const salt = own(state.experimentSalts, next.id);
      // The salt is fixed when the experiment is created. Replacing it later would re-roll every assignment.
      if (action.salt !== undefined && (before !== undefined || salt !== undefined)) return { state, refused: "The salt of an existing experiment cannot be set or replaced." };
      if (!before && (next.status !== "draft" || next.design_locked_at !== null)) return { state, refused: "A new experiment starts as an unlocked draft." };
      if (before && before.design_locked_at !== null) {
        const changed = DESIGN_FIELDS.filter((f) => !same(before[f], next[f]));
        if (changed.length > 0) return { state, refused: `The design is locked. Changed after lock: ${changed.join(", ")}.` };
        if (next.status === "draft") return { state, refused: "A locked design cannot return to draft." };
        if (before.status === "closed" && next.status !== "closed") return { state, refused: "A closed experiment cannot be reopened." };
      }
      if (next.status !== "draft" && next.design_locked_at === null) {
        return { state, refused: "An experiment cannot leave draft before its design is locked." };
      }
      if (before && before.design_locked_at === null && next.design_locked_at !== null) {
        if (salt === undefined) return { state, refused: "This experiment has no salt in this workspace, so it cannot be locked here." };
        if (next.seed_digest !== sha256Hex(salt)) return { state, refused: "The seed digest recorded at lock must be the digest of this experiment's salt." };
      }
      const experiments = before ? state.experiments.map((x) => (x.id === next.id ? next : x)) : [...state.experiments, next];
      const experimentSalts = action.salt !== undefined ? { ...state.experimentSalts, [next.id]: action.salt } : state.experimentSalts;
      return { state: { ...state, experiments, experimentSalts } };
    }
    case "experiment/remove": {
      const spec = state.experiments.find((x) => x.id === action.id);
      if (!spec) return { state, refused: "Experiment not found." };
      if (spec.design_locked_at !== null) return { state, refused: "A preregistered experiment cannot be deleted; close it instead." };
      const experimentSalts = Object.fromEntries(Object.entries(state.experimentSalts).filter(([id]) => id !== action.id));
      return { state: { ...state, experiments: state.experiments.filter((x) => x.id !== action.id), experimentSalts } };
    }
    case "experiment/assign": {
      const taken = new Set(state.assignments.map((a) => `${a.experiment_id}␟${a.unit_id}`));
      const ids = new Set(state.assignments.map((a) => a.id));
      for (const a of action.assignments) {
        const spec = state.experiments.find((x) => x.id === a.experiment_id);
        if (!spec) return { state, refused: "Experiment not found." };
        if (spec.design_locked_at === null) return { state, refused: "Units cannot be assigned before the design is locked." };
        if (spec.status !== "preregistered" && spec.status !== "running") return { state, refused: `Units cannot be assigned while the experiment is ${spec.status}.` };
        if (!spec.variants.includes(a.variant)) return { state, refused: "Assignment names a variant that is not in the design." };
        if (ids.has(a.id)) return { state, refused: `Assignment id '${a.id}' is already in use.` };
        ids.add(a.id);
        const key = `${a.experiment_id}␟${a.unit_id}`;
        if (taken.has(key)) return { state, refused: `Unit '${a.unit_label}' is already assigned in this experiment (no crossover).` };
        taken.add(key);
      }
      return { state: { ...state, assignments: [...state.assignments, ...action.assignments] } };
    }
    case "experiment/exposure": {
      const target = state.assignments.find((a) => a.id === action.assignmentId);
      if (!target) return { state, refused: "Assignment not found." };
      // Exposure is logged once; the first record is kept (assignment precedes exposure).
      if (target.exposure_logged_at !== null) return { state, refused: "Exposure was already logged for this unit." };
      return { state: { ...state, assignments: state.assignments.map((a) => (a.id === action.assignmentId ? { ...a, exposure_logged_at: action.at } : a)) } };
    }
    case "experiment/outcomes": {
      const byId = new Map(state.assignments.map((a) => [a.id, a]));
      for (const o of action.outcomes) {
        const a = byId.get(o.assignment_id);
        if (!a) return { state, refused: "Outcome refers to an unknown assignment." };
        if (a.experiment_id !== o.experiment_id) return { state, refused: "Outcome names a different experiment than its assignment." };
        const spec = state.experiments.find((x) => x.id === a.experiment_id);
        if (!spec) return { state, refused: "Experiment not found." };
        if (spec.status === "closed") return { state, refused: "A closed experiment's outcomes cannot be changed." };
        if (o.metric !== spec.primary_metric && !(spec.secondary_metrics ?? []).includes(o.metric)) return { state, refused: "Outcome names a metric that is not in the design." };
      }
      const replaced = new Set(action.outcomes.map((o) => `${o.assignment_id}␟${o.metric}`));
      if (replaced.size !== action.outcomes.length) return { state, refused: "Two outcomes for the same unit and metric." };
      const kept = state.outcomes.filter((o) => !replaced.has(`${o.assignment_id}␟${o.metric}`));
      return { state: { ...state, outcomes: [...kept, ...action.outcomes] } };
    }

    case "scenario/edit":
      return { state: { ...state, scenarioEdits: { ...state.scenarioEdits, [action.scenarioId]: action.actions } } };
    case "scenario/reset":
      return { state: { ...state, scenarioEdits: Object.fromEntries(Object.entries(state.scenarioEdits).filter(([id]) => id !== action.scenarioId)) } };
    case "simulator/set":
      return { state: { ...state, simulator: action.settings } };
    case "blend/set":
      return { state: { ...state, strategicBlend: Math.min(100, Math.max(0, Math.round(action.value))) } };
  }
}

// ------------------------------------------------------------------ cross-record invariants
const WORKSPACE_KEYS = ["schema", "seed", "shortlist", "claims", "resumeVariants", "applicationEvents", "reminders", "experiments", "experimentSalts", "assignments", "outcomes", "scenarioEdits", "simulator", "strategicBlend", "audit"];

/**
 * Invariants that span records. A stored workspace that breaks any of them was not
 * produced by this app's reducer and is rejected as a whole (finding F-10).
 * Returns the first problem, or null.
 */
export function workspaceProblem(ws: Workspace): string | null {
  const extra = Object.keys(ws).find((k) => !WORKSPACE_KEYS.includes(k));
  if (extra) return `unknown field '${extra.slice(0, 40)}'`;
  for (const [what, ids] of [["claim", ws.claims.map((c) => c.id)], ["résumé variant", ws.resumeVariants.map((v) => v.id)], ["application event", ws.applicationEvents.map((e) => e.id)], ["experiment", ws.experiments.map((e) => e.id)], ["assignment", ws.assignments.map((a) => a.id)]] as const) {
    const dup = duplicate(ids);
    if (dup !== null) return `duplicate ${what} id`;
  }
  if (ws.claims.some((c) => c.status === "supported" && c.evidence_ids.length === 0)) return "a supported claim has no evidence";
  for (const v of ws.resumeVariants) {
    if (duplicate(bulletIds(v)) !== null) return "a résumé variant has two lines with the same id";
    if (v.approval !== null && v.approval.content_digest !== resumeContentDigest(v.sections)) return "an approved résumé variant does not match its approval";
  }
  // Replay every application through the same gate the reducer uses.
  const byApp = new Map<string, ApplicationEvent[]>();
  for (const e of sortEvents(ws.applicationEvents)) byApp.set(e.application_id, [...(byApp.get(e.application_id) ?? []), e]);
  for (const log of byApp.values()) {
    for (let i = 0; i < log.length; i++) {
      const e = log[i]!;
      const prior = i === 0 ? null : groupApplications(log.slice(0, i))[0]!;
      if (e.job_id !== log[0]!.job_id) return "an application has events for two postings";
      if (!canAppend(prior, e.event_type)) return `an application's events are out of order ('${e.event_type}' after '${prior?.stage ?? "nothing"}')`;
      const gated = e.event_type === "approved" || e.event_type === "submitted";
      if (gated && (!e.approval_token_ref || !e.resume_variant_id || e.actor === "system")) return "an approval or submission lacks its approval record or was recorded by the system";
      if (!gated && e.approval_token_ref) return "an event other than approval or submission cites an approval record";
      if (e.event_type === "submitted" && prior && (prior.approvalTokenRef !== e.approval_token_ref || prior.approvedVariantId !== e.resume_variant_id)) return "a submission does not cite its approval";
    }
  }
  const experiments = new Map(ws.experiments.map((x) => [x.id, x]));
  for (const x of ws.experiments) {
    if (x.status !== "draft" && x.design_locked_at === null) return "an experiment left draft without a locked design";
    const salt = own(ws.experimentSalts, x.id);
    if (salt !== undefined && x.design_locked_at !== null && x.seed_digest !== sha256Hex(salt)) return "an experiment's salt does not match its recorded seed digest";
  }
  if (Object.keys(ws.experimentSalts).some((id) => !experiments.has(id))) return "a salt belongs to no experiment";
  const units = new Set<string>();
  const assignments = new Map(ws.assignments.map((a) => [a.id, a]));
  for (const a of ws.assignments) {
    const spec = experiments.get(a.experiment_id);
    if (!spec) return "an assignment belongs to no experiment";
    if (spec.design_locked_at === null) return "a unit was assigned before the design was locked";
    if (!spec.variants.includes(a.variant)) return "an assignment names a variant that is not in the design";
    const key = `${a.experiment_id}␟${a.unit_id}`;
    if (units.has(key)) return "a unit is assigned twice in one experiment";
    units.add(key);
  }
  const seenOutcomes = new Set<string>();
  for (const o of ws.outcomes) {
    const a = assignments.get(o.assignment_id);
    if (!a) return "an outcome belongs to no assignment";
    if (a.experiment_id !== o.experiment_id) return "an outcome names a different experiment than its assignment";
    const key = `${o.assignment_id}␟${o.metric}`;
    if (seenOutcomes.has(key)) return "two outcomes for the same unit and metric";
    seenOutcomes.add(key);
  }
  const s = ws.simulator;
  if (!(s.hoursPerWeek >= 1 && s.hoursPerWeek <= 60)) return "simulator pace is out of range";
  if (![s.weights.coverage, s.weights.effort, s.weights.leadership].every((w) => w >= 0 && w <= 100)) return "simulator weights are out of range";
  return null;
}

// ------------------------------------------------------------------ persistence
export interface LoadResult {
  workspace: Workspace | null;
  /** Human-readable reason when stored data existed but was rejected. */
  error: string | null;
}

function allValid<T>(list: unknown, check: (v: unknown, c: Checker) => v is T): list is T[] {
  return Array.isArray(list) && list.every((item) => {
    const c = new Checker();
    return check(item, c) && c.issues.length === 0;
  });
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isStringArray = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Strictly validate a stored workspace. Anything unexpected rejects the whole blob. */
export function parseWorkspace(raw: string): LoadResult {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { workspace: null, error: "Stored workspace is not valid JSON." };
  }
  if (!isRecord(data)) return { workspace: null, error: "Stored workspace has the wrong shape." };
  if (data.schema !== WORKSPACE_SCHEMA) return { workspace: null, error: `Stored workspace has schema '${String(data.schema)}', expected ${WORKSPACE_SCHEMA}.` };
  const fail = (what: string): LoadResult => ({ workspace: null, error: `Stored workspace failed validation: ${what}.` });
  if (data.seed !== "fixture" && data.seed !== "empty") return fail("seed");
  if (!isStringArray(data.shortlist)) return fail("shortlist");
  if (!allValid(data.claims, isProfileClaim)) return fail("claims");
  if (!allValid(data.resumeVariants, isResumeVariant)) return fail("résumé variants");
  if (!allValid(data.applicationEvents, isApplicationEvent)) return fail("application events");
  if (!allValid(data.experiments, isExperimentSpec)) return fail("experiments");
  if (!allValid(data.assignments, isExperimentAssignment)) return fail("assignments");
  if (!allValid(data.outcomes, isExperimentOutcome)) return fail("outcomes");
  if (!isRecord(data.experimentSalts) || !Object.values(data.experimentSalts).every((s) => typeof s === "string")) return fail("experiment salts");
  if (!Array.isArray(data.reminders) || !data.reminders.every((r) => isRecord(r) && typeof r.id === "string" && typeof r.application_id === "string" && typeof r.due_on === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.due_on) && typeof r.text === "string" && typeof r.done === "boolean")) return fail("reminders");
  if (!isRecord(data.scenarioEdits) || !Object.values(data.scenarioEdits).every((list) => Array.isArray(list) && list.every((a) => isRecord(a) && typeof a.skill === "string" && finite(a.effort_hours) && a.effort_hours >= 0 && typeof a.included === "boolean" && (a.cost === null || (finite(a.cost) && a.cost >= 0))))) return fail("scenario edits");
  const sim = data.simulator;
  if (!isRecord(sim) || !finite(sim.hoursPerWeek) || sim.hoursPerWeek <= 0 || !isRecord(sim.weights) || !finite(sim.weights.coverage) || !finite(sim.weights.effort) || !finite(sim.weights.leadership)) return fail("simulator settings");
  if (!finite(data.strategicBlend) || data.strategicBlend < 0 || data.strategicBlend > 100) return fail("blend");
  if (!Array.isArray(data.audit) || !data.audit.every((a) => isRecord(a) && typeof a.id === "string" && typeof a.at === "string" && typeof a.action === "string" && typeof a.detail === "string")) return fail("audit log");
  const workspace = data as unknown as Workspace;
  const problem = workspaceProblem(workspace);
  if (problem !== null) return fail(problem);
  return { workspace, error: null };
}

export function loadWorkspace(storage: Pick<Storage, "getItem">): LoadResult {
  let raw: string | null;
  try {
    raw = storage.getItem(STORAGE_KEY);
  } catch {
    return { workspace: null, error: "Browser storage is not available; changes will not be kept after reload." };
  }
  if (raw === null) return { workspace: null, error: null };
  return parseWorkspace(raw);
}

export function saveWorkspace(storage: Pick<Storage, "setItem">, workspace: Workspace): string | null {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(workspace));
    return null;
  } catch {
    return "Could not save to browser storage (it may be full or disabled).";
  }
}

// ------------------------------------------------------------------ settings
export const THEMES = ["system", "light", "dark"] as const;
export const MOTIONS = ["system", "reduce", "allow"] as const;
export const SIMULATIONS = ["none", "slow", "error", "empty"] as const;
export interface Settings {
  theme: (typeof THEMES)[number];
  motion: (typeof MOTIONS)[number];
  simulate: (typeof SIMULATIONS)[number];
}
export const DEFAULT_SETTINGS: Settings = { theme: "system", motion: "system", simulate: "none" };

export function parseSettings(raw: string | null): Settings {
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const d: unknown = JSON.parse(raw);
    if (!isRecord(d)) return DEFAULT_SETTINGS;
    const pick = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
      typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
    return {
      theme: pick(d.theme, THEMES, "system"),
      motion: pick(d.motion, MOTIONS, "system"),
      simulate: pick(d.simulate, SIMULATIONS, "none"),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

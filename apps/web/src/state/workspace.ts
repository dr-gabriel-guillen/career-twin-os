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
 *  - editing a résumé variant never keeps a stale approval valid.
 */
import type { DataBundle } from "@/adapters";
import { isApplicationEvent, isExperimentSpec, isProfileClaim } from "@/adapters/frozen";
import type { ExperimentAssignment, ExperimentOutcome, ResumeVariant } from "@/adapters/proposed";
import { isExperimentAssignment, isExperimentOutcome, isResumeVariant } from "@/adapters/proposedValidators";
import { Checker } from "@/adapters/validate";
import type { ApplicationEvent, ClaimStatus, ExperimentSpec, ProfileClaim } from "@/contracts/types";
import { canAppend, groupApplications } from "@/lib/applications";
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
  | { type: "replace"; workspace: Workspace }
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

export function reduce(state: Workspace, action: Action): ReduceResult {
  switch (action.type) {
    case "replace":
      return { state: action.workspace };

    case "shortlist/toggle": {
      const on = state.shortlist.includes(action.jobId);
      return { state: { ...state, shortlist: on ? state.shortlist.filter((j) => j !== action.jobId) : [...state.shortlist, action.jobId] } };
    }

    case "claim/upsert": {
      const exists = state.claims.some((c) => c.id === action.claim.id);
      const claims = exists ? state.claims.map((c) => (c.id === action.claim.id ? action.claim : c)) : [...state.claims, action.claim];
      return { state: { ...state, claims, audit: audit(state, action.auditId, action.at, exists ? "claim.edited" : "claim.added", action.claim.id) } };
    }
    case "claim/setStatus": {
      const before = state.claims.find((c) => c.id === action.id);
      if (!before) return { state, refused: "Claim not found." };
      // "supported" means evidence exists. It cannot be self-declared without an evidence reference.
      if (action.status === "supported" && before.evidence_ids.length === 0) {
        return { state, refused: "A claim cannot be marked supported without at least one evidence reference." };
      }
      const claims = state.claims.map((c) => (c.id === action.id ? { ...c, status: action.status, updated_at: action.at } : c));
      return { state: { ...state, claims, audit: audit(state, action.auditId, action.at, "claim.status", `${action.id}: ${before.status} -> ${action.status}`) } };
    }
    case "claim/remove": {
      if (!state.claims.some((c) => c.id === action.id)) return { state, refused: "Claim not found." };
      return { state: { ...state, claims: state.claims.filter((c) => c.id !== action.id), audit: audit(state, action.auditId, action.at, "claim.removed", action.id) } };
    }

    case "resume/upsert": {
      const exists = state.resumeVariants.some((v) => v.id === action.variant.id);
      return { state: { ...state, resumeVariants: exists ? state.resumeVariants.map((v) => (v.id === action.variant.id ? action.variant : v)) : [...state.resumeVariants, action.variant] } };
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
      if (e.event_type === "approved" || e.event_type === "submitted") {
        if (!e.approval_token_ref) return { state, refused: "This step needs your explicit approval record." };
        if (!e.resume_variant_id) return { state, refused: "This step needs an approved résumé variant." };
        if (e.actor !== "user") return { state, refused: "Only you can approve or record a submission. The system never does." };
      }
      if (e.event_type === "approved") {
        const variant = state.resumeVariants.find((v) => v.id === e.resume_variant_id);
        if (!variant || variant.approval === null) return { state, refused: "The résumé variant has not been approved by you." };
        if (variant.approval.approval_token_ref !== e.approval_token_ref) return { state, refused: "The approval record does not belong to this résumé variant." };
        if (variant.approval.content_digest !== resumeContentDigest(variant.sections)) {
          return { state, refused: "The résumé variant changed after you approved it. Approve it again first." };
        }
      }
      if (e.event_type === "submitted" && app?.approvalTokenRef !== e.approval_token_ref) {
        return { state, refused: "The submission must cite the same approval as the approved step." };
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
      const before = state.experiments.find((x) => x.id === action.spec.id);
      if (before && before.design_locked_at !== null) {
        const changed = DESIGN_FIELDS.filter((f) => JSON.stringify(before[f]) !== JSON.stringify(action.spec[f]));
        if (changed.length > 0) return { state, refused: `The design is locked. Changed after lock: ${changed.join(", ")}.` };
        if (action.spec.status === "draft") return { state, refused: "A locked design cannot return to draft." };
      }
      if (action.spec.status !== "draft" && action.spec.design_locked_at === null) {
        return { state, refused: "An experiment cannot leave draft before its design is locked." };
      }
      const experiments = before ? state.experiments.map((x) => (x.id === action.spec.id ? action.spec : x)) : [...state.experiments, action.spec];
      const experimentSalts = action.salt ? { ...state.experimentSalts, [action.spec.id]: action.salt } : state.experimentSalts;
      return { state: { ...state, experiments, experimentSalts } };
    }
    case "experiment/remove": {
      const spec = state.experiments.find((x) => x.id === action.id);
      if (!spec) return { state, refused: "Experiment not found." };
      if (spec.design_locked_at !== null) return { state, refused: "A preregistered experiment cannot be deleted; close it instead." };
      const { [action.id]: _salt, ...experimentSalts } = state.experimentSalts;
      return { state: { ...state, experiments: state.experiments.filter((x) => x.id !== action.id), experimentSalts } };
    }
    case "experiment/assign": {
      const taken = new Set(state.assignments.map((a) => `${a.experiment_id}\u241f${a.unit_id}`));
      for (const a of action.assignments) {
        const spec = state.experiments.find((x) => x.id === a.experiment_id);
        if (!spec) return { state, refused: "Experiment not found." };
        if (spec.design_locked_at === null) return { state, refused: "Units cannot be assigned before the design is locked." };
        if (spec.status !== "preregistered" && spec.status !== "running") return { state, refused: `Units cannot be assigned while the experiment is ${spec.status}.` };
        if (!spec.variants.includes(a.variant)) return { state, refused: "Assignment names a variant that is not in the design." };
        const key = `${a.experiment_id}\u241f${a.unit_id}`;
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
      const known = new Set(state.assignments.map((a) => a.id));
      if (action.outcomes.some((o) => !known.has(o.assignment_id))) return { state, refused: "Outcome refers to an unknown assignment." };
      const replaced = new Set(action.outcomes.map((o) => `${o.assignment_id}\u241f${o.metric}`));
      const kept = state.outcomes.filter((o) => !replaced.has(`${o.assignment_id}\u241f${o.metric}`));
      return { state: { ...state, outcomes: [...kept, ...action.outcomes] } };
    }

    case "scenario/edit":
      return { state: { ...state, scenarioEdits: { ...state.scenarioEdits, [action.scenarioId]: action.actions } } };
    case "scenario/reset": {
      const { [action.scenarioId]: _removed, ...scenarioEdits } = state.scenarioEdits;
      return { state: { ...state, scenarioEdits } };
    }
    case "simulator/set":
      return { state: { ...state, simulator: action.settings } };
    case "blend/set":
      return { state: { ...state, strategicBlend: Math.min(100, Math.max(0, Math.round(action.value))) } };
  }
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
  return { workspace: data as unknown as Workspace, error: null };
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

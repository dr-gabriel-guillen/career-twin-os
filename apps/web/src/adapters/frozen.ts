/**
 * Runtime validators for the six FROZEN v0.1 contracts.
 * Each mirrors its JSON Schema exactly (required keys, enums, formats,
 * additionalProperties:false). tests/unit/contracts.test.ts mutates valid
 * records hundreds of ways and asserts these agree with Ajv on every one.
 */
import {
  ASSIGNMENT_UNITS,
  CLAIM_STATUSES,
  CLAIM_TYPES,
  CLAIM_VISIBILITIES,
  COMP_BASES,
  EVENT_ACTORS,
  EVENT_TYPES,
  EXPERIMENT_STATUSES,
  JOB_SOURCES,
  JOB_STATUSES,
  OUTCOME_STATUSES,
  PROJECTION_METHODS,
  REMOTE_POLICIES,
  REPRESENTATION_TYPES,
  type ApplicationEvent,
  type AtlasPoint,
  type ExperimentSpec,
  type JobPosting,
  type ProfileClaim,
  type TransitionScenario,
} from "@/contracts/types";
import type { Checker } from "./validate";

type Obj = Record<string, unknown>;

/** Check required keys exist; returns false (and reports) if any is missing. */
function required(c: Checker, v: Obj, keys: readonly string[]): boolean {
  let ok = true;
  for (const k of keys) {
    if (!(k in v)) {
      c.at(k).fail("required property is missing");
      ok = false;
    }
  }
  return ok;
}

const has = (v: Obj, k: string) => k in v;
/** `type: ["string","null"]` */
const stringOrNull = (c: Checker, v: unknown) => v === null || c.string(v);
/** `type: ["string","null"], format: date-time` (format only applies to strings). */
const dateTimeOrNull = (c: Checker, v: unknown) => v === null || c.dateTime(v);

// ------------------------------------------------------------------ JobPosting
const JOB_KEYS = ["id", "source", "source_id", "title", "organization", "url", "fetched_at", "status", "skills", "seniority", "employment_type", "location", "remote_policy", "compensation", "license_note", "role_family", "requirements", "source_evidence"] as const;
const JOB_REQUIRED = JOB_KEYS.slice(0, 11);
const COMP_KEYS = ["currency", "min", "max", "basis", "verified"] as const;

export function isJobPosting(v: unknown, c: Checker): v is JobPosting {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, JOB_REQUIRED);
  ok = c.noExtraKeys(v, JOB_KEYS) && ok;
  for (const k of ["id", "source_id", "title", "organization", "url"] as const) {
    if (has(v, k)) ok = c.at(k).string(v[k], { minLength: 1 }) && ok;
  }
  if (has(v, "source")) ok = c.at("source").oneOf(v.source, JOB_SOURCES) && ok;
  if (has(v, "fetched_at")) ok = c.at("fetched_at").dateTime(v.fetched_at) && ok;
  if (has(v, "status")) ok = c.at("status").oneOf(v.status, JOB_STATUSES) && ok;
  if (has(v, "skills")) ok = c.at("skills").stringArray(v.skills, { unique: true }) && ok;
  for (const k of ["seniority", "employment_type", "license_note", "role_family"] as const) {
    if (has(v, k)) ok = c.at(k).string(v[k]) && ok;
  }
  if (has(v, "location")) ok = stringOrNull(c.at("location"), v.location) && ok;
  if (has(v, "remote_policy")) ok = c.at("remote_policy").oneOf(v.remote_policy, REMOTE_POLICIES) && ok;
  if (has(v, "compensation") && v.compensation !== null) {
    const cc = c.at("compensation");
    const comp = v.compensation;
    if (!cc.isObject(comp)) ok = false;
    else {
      ok = required(cc, comp, COMP_KEYS) && ok;
      ok = cc.noExtraKeys(comp, COMP_KEYS) && ok;
      if (has(comp, "currency")) ok = cc.at("currency").string(comp.currency) && ok;
      for (const k of ["min", "max"] as const) {
        if (has(comp, k) && comp[k] !== null) ok = cc.at(k).number(comp[k]) && ok;
      }
      if (has(comp, "basis")) ok = cc.at("basis").oneOf(comp.basis, COMP_BASES) && ok;
      if (has(comp, "verified")) ok = cc.at("verified").boolean(comp.verified) && ok;
    }
  }
  for (const k of ["requirements", "source_evidence"] as const) {
    if (has(v, k)) ok = c.at(k).stringArray(v[k]) && ok;
  }
  return ok;
}

// ------------------------------------------------------------------ ProfileClaim
const CLAIM_KEYS = ["id", "subject_id", "claim_type", "claim_text", "status", "evidence_ids", "visibility", "updated_at", "skills", "valid_to"] as const;
const CLAIM_REQUIRED = CLAIM_KEYS.slice(0, 8);

export function isProfileClaim(v: unknown, c: Checker): v is ProfileClaim {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, CLAIM_REQUIRED);
  ok = c.noExtraKeys(v, CLAIM_KEYS) && ok;
  for (const k of ["id", "subject_id"] as const) if (has(v, k)) ok = c.at(k).string(v[k], { minLength: 1 }) && ok;
  if (has(v, "claim_type")) ok = c.at("claim_type").oneOf(v.claim_type, CLAIM_TYPES) && ok;
  if (has(v, "claim_text")) ok = c.at("claim_text").string(v.claim_text) && ok;
  if (has(v, "status")) ok = c.at("status").oneOf(v.status, CLAIM_STATUSES) && ok;
  if (has(v, "evidence_ids")) ok = c.at("evidence_ids").stringArray(v.evidence_ids) && ok;
  if (has(v, "visibility")) ok = c.at("visibility").oneOf(v.visibility, CLAIM_VISIBILITIES) && ok;
  if (has(v, "updated_at")) ok = c.at("updated_at").dateTime(v.updated_at) && ok;
  if (has(v, "skills")) ok = c.at("skills").stringArray(v.skills) && ok;
  if (has(v, "valid_to")) ok = dateTimeOrNull(c.at("valid_to"), v.valid_to) && ok;
  return ok;
}

// ------------------------------------------------------------------ ApplicationEvent
const EVENT_KEYS = ["id", "application_id", "job_id", "event_type", "recorded_at", "actor", "source", "outcome_status", "resume_variant_id", "occurred_at", "experiment_id", "approval_token_ref", "notes"] as const;
const EVENT_REQUIRED = EVENT_KEYS.slice(0, 8);

export function isApplicationEvent(v: unknown, c: Checker): v is ApplicationEvent {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, EVENT_REQUIRED);
  ok = c.noExtraKeys(v, EVENT_KEYS) && ok;
  for (const k of ["id", "application_id", "job_id", "source", "notes"] as const) if (has(v, k)) ok = c.at(k).string(v[k]) && ok;
  if (has(v, "event_type")) ok = c.at("event_type").oneOf(v.event_type, EVENT_TYPES) && ok;
  if (has(v, "recorded_at")) ok = c.at("recorded_at").dateTime(v.recorded_at) && ok;
  if (has(v, "occurred_at")) ok = dateTimeOrNull(c.at("occurred_at"), v.occurred_at) && ok;
  if (has(v, "actor")) ok = c.at("actor").oneOf(v.actor, EVENT_ACTORS) && ok;
  if (has(v, "outcome_status")) ok = c.at("outcome_status").oneOf(v.outcome_status, OUTCOME_STATUSES) && ok;
  for (const k of ["resume_variant_id", "experiment_id", "approval_token_ref"] as const) {
    if (has(v, k)) ok = stringOrNull(c.at(k), v[k]) && ok;
  }
  return ok;
}

// ------------------------------------------------------------------ ExperimentSpec
const EXP_KEYS = ["id", "title", "hypothesis", "status", "variants", "assignment_unit", "strata", "primary_metric", "design_locked_at", "stopping_rule", "consent_required", "outcome_window_days", "secondary_metrics", "analysis_plan", "seed_digest"] as const;
const EXP_REQUIRED = EXP_KEYS.slice(0, 11);

export function isExperimentSpec(v: unknown, c: Checker): v is ExperimentSpec {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, EXP_REQUIRED);
  ok = c.noExtraKeys(v, EXP_KEYS) && ok;
  for (const k of ["id", "title", "hypothesis", "primary_metric", "stopping_rule", "analysis_plan", "seed_digest"] as const) {
    if (has(v, k)) ok = c.at(k).string(v[k]) && ok;
  }
  if (has(v, "status")) ok = c.at("status").oneOf(v.status, EXPERIMENT_STATUSES) && ok;
  if (has(v, "variants")) ok = c.at("variants").stringArray(v.variants, { unique: true, minItems: 2 }) && ok;
  if (has(v, "assignment_unit")) ok = c.at("assignment_unit").oneOf(v.assignment_unit, ASSIGNMENT_UNITS) && ok;
  for (const k of ["strata", "secondary_metrics"] as const) if (has(v, k)) ok = c.at(k).stringArray(v[k]) && ok;
  if (has(v, "design_locked_at")) ok = dateTimeOrNull(c.at("design_locked_at"), v.design_locked_at) && ok;
  if (has(v, "consent_required")) ok = c.at("consent_required").boolean(v.consent_required) && ok;
  if (has(v, "outcome_window_days")) ok = c.at("outcome_window_days").number(v.outcome_window_days, { integer: true, min: 1 }) && ok;
  return ok;
}

// ------------------------------------------------------------------ AtlasPoint
const POINT_KEYS = ["entity_id", "projection_id", "label", "coords", "source_vector_ref", "representation_type", "projection_method", "neighborhood_quality", "fit_metric_ref", "warnings"] as const;
const POINT_REQUIRED = POINT_KEYS.slice(0, 7);

export function isAtlasPoint(v: unknown, c: Checker): v is AtlasPoint {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, POINT_REQUIRED);
  ok = c.noExtraKeys(v, POINT_KEYS) && ok;
  for (const k of ["entity_id", "projection_id", "label", "source_vector_ref"] as const) if (has(v, k)) ok = c.at(k).string(v[k]) && ok;
  if (has(v, "coords")) {
    const cc = c.at("coords");
    const coords = v.coords;
    if (!cc.array(coords)) ok = false;
    else {
      if (coords.length < 2 || coords.length > 3) ok = cc.fail("expected 2 or 3 coordinates");
      coords.forEach((x, i) => (ok = cc.at(i).number(x) && ok));
    }
  }
  if (has(v, "representation_type")) ok = c.at("representation_type").oneOf(v.representation_type, REPRESENTATION_TYPES) && ok;
  if (has(v, "projection_method")) ok = c.at("projection_method").oneOf(v.projection_method, PROJECTION_METHODS) && ok;
  if (has(v, "neighborhood_quality") && v.neighborhood_quality !== null) {
    ok = c.at("neighborhood_quality").number(v.neighborhood_quality, { min: 0, max: 1 }) && ok;
  }
  if (has(v, "fit_metric_ref")) ok = stringOrNull(c.at("fit_metric_ref"), v.fit_metric_ref) && ok;
  if (has(v, "warnings")) ok = c.at("warnings").stringArray(v.warnings) && ok;
  return ok;
}

// ------------------------------------------------------------------ TransitionScenario
const SCN_KEYS = ["id", "candidate_ref", "target_role", "planned_actions", "time_horizon_months", "status", "assumptions", "outputs", "risks"] as const;
const SCN_REQUIRED = SCN_KEYS.slice(0, 8);
const ACTION_KEYS = ["skill", "effort_hours", "evidence_goal"] as const;

export function isTransitionScenario(v: unknown, c: Checker): v is TransitionScenario {
  if (!c.isObject(v)) return false;
  let ok = required(c, v, SCN_REQUIRED);
  ok = c.noExtraKeys(v, SCN_KEYS) && ok;
  for (const k of ["id", "candidate_ref", "target_role"] as const) if (has(v, k)) ok = c.at(k).string(v[k]) && ok;
  if (has(v, "planned_actions")) {
    const ca = c.at("planned_actions");
    const actions = v.planned_actions;
    if (!ca.array(actions)) ok = false;
    else {
      actions.forEach((a, i) => {
        const ci = ca.at(i);
        if (!ci.isObject(a)) {
          ok = false;
          return;
        }
        ok = required(ci, a, ["skill", "effort_hours"]) && ok;
        ok = ci.noExtraKeys(a, ACTION_KEYS) && ok;
        if (has(a, "skill")) ok = ci.at("skill").string(a.skill) && ok;
        if (has(a, "effort_hours")) ok = ci.at("effort_hours").number(a.effort_hours, { min: 0 }) && ok;
        if (has(a, "evidence_goal")) ok = ci.at("evidence_goal").string(a.evidence_goal) && ok;
      });
    }
  }
  if (has(v, "time_horizon_months")) ok = c.at("time_horizon_months").number(v.time_horizon_months, { integer: true, min: 1 }) && ok;
  if (has(v, "status") && v.status !== "hypothetical") ok = c.at("status").fail("expected the constant 'hypothetical'");
  for (const k of ["assumptions", "risks"] as const) if (has(v, k)) ok = c.at(k).stringArray(v[k]) && ok;
  if (has(v, "outputs")) ok = c.at("outputs").isObject(v.outputs) && ok;
  return ok;
}

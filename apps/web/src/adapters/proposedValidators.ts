/**
 * Runtime validators for the PROPOSED payload shapes in ./proposed.ts.
 * They are strict on purpose: a payload that claims a `-proposed` schema but
 * drifts from it is rejected and reported, never silently accepted.
 */
import { CLAIM_STATUSES, PROJECTION_METHODS } from "@/contracts/types";
import { isAtlasPoint } from "./frozen";
import {
  ASSIGNMENT_SCHEMA,
  BULLET_ORIGINS,
  CONSTRAINT_STATUSES,
  DATA_ORIGINS,
  EVIDENCE_KINDS,
  EVIDENCE_SCHEMA,
  EVIDENCE_STRENGTHS,
  EXPOSURE_LEVELS,
  MATCH_SCHEMA,
  OUTCOME_SCHEMA,
  OUTCOME_STATES,
  PREFERENCES_SCHEMA,
  PROJECTION_SCHEMA,
  RESUME_SCHEMA,
  SCENARIO_OUTPUTS_SCHEMA,
  SCORE_TYPE,
  type AtlasProjection,
  type CandidatePreferences,
  type EvidenceItem,
  type ExperimentAssignment,
  type ExperimentOutcome,
  type MatchExplanation,
  type ResumeVariant,
  type ScenarioOutputs,
} from "./proposed";
import type { Checker } from "./validate";

type Obj = Record<string, unknown>;

function constant(c: Checker, v: unknown, expected: string): boolean {
  return v === expected ? true : c.fail(`expected the constant '${expected}'`);
}
function nullable(v: unknown, check: () => boolean): boolean {
  return v === null ? true : check();
}
function unit(c: Checker, v: unknown): boolean {
  return c.number(v, { min: 0, max: 1 });
}

function scoreGroup(c: Checker, v: unknown): boolean {
  if (!c.isObject(v)) return false;
  let ok = nullable(v.score, () => c.at("score").number(v.score, { min: 0, max: 100 }));
  const cc = c.at("components");
  if (!cc.array(v.components)) return false;
  v.components.forEach((raw, i) => {
    const ci = cc.at(i);
    if (!ci.isObject(raw)) return void (ok = false);
    ok = ci.at("key").string(raw.key, { minLength: 1 }) && ok;
    ok = ci.at("label").string(raw.label) && ok;
    ok = nullable(raw.value, () => unit(ci.at("value"), raw.value)) && ok;
    ok = unit(ci.at("weight"), raw.weight) && ok;
    ok = ci.at("status").oneOf(raw.status, ["known", "unknown"] as const) && ok;
    ok = ci.at("note").string(raw.note) && ok;
    // UNKNOWN must never carry a value; KNOWN must carry one.
    if (raw.status === "unknown" && raw.value !== null) ok = ci.at("value").fail("unknown component must have value null");
    if (raw.status === "known" && raw.value === null) ok = ci.at("value").fail("known component must have a value");
  });
  return ok;
}

export function isMatchExplanation(v: unknown, c: Checker): v is MatchExplanation {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, MATCH_SCHEMA);
  ok = c.at("job_id").string(v.job_id, { minLength: 1 }) && ok;
  ok = c.at("candidate_ref").string(v.candidate_ref, { minLength: 1 }) && ok;
  ok = c.at("computed_at").dateTime(v.computed_at) && ok;
  const ce = c.at("engine");
  if (ce.isObject(v.engine)) {
    ok = ce.at("name").string(v.engine.name) && ok;
    ok = ce.at("version").string(v.engine.version) && ok;
  } else ok = false;
  // A payload that calls its score a probability is refused outright.
  ok = constant(c.at("score_type"), v.score_type, SCORE_TYPE) && ok;
  ok = scoreGroup(c.at("near_term_fit"), v.near_term_fit) && ok;
  ok = scoreGroup(c.at("strategic_value"), v.strategic_value) && ok;
  const ch = c.at("hard_constraints");
  if (ch.array(v.hard_constraints)) {
    v.hard_constraints.forEach((raw, i) => {
      const ci = ch.at(i);
      if (!ci.isObject(raw)) return void (ok = false);
      ok = ci.at("key").string(raw.key, { minLength: 1 }) && ok;
      ok = ci.at("label").string(raw.label) && ok;
      ok = ci.at("status").oneOf(raw.status, CONSTRAINT_STATUSES) && ok;
      ok = ci.at("detail").string(raw.detail) && ok;
    });
  } else ok = false;
  const cs = c.at("skills");
  if (cs.array(v.skills)) {
    v.skills.forEach((raw, i) => {
      const ci = cs.at(i);
      if (!ci.isObject(raw)) return void (ok = false);
      ok = ci.at("skill").string(raw.skill, { minLength: 1 }) && ok;
      ok = ci.at("evidence_strength").oneOf(raw.evidence_strength, EVIDENCE_STRENGTHS) && ok;
      ok = ci.at("claim_ids").stringArray(raw.claim_ids) && ok;
    });
  } else ok = false;
  ok = c.at("unknown_inputs").stringArray(v.unknown_inputs) && ok;
  ok = c.at("warnings").stringArray(v.warnings) && ok;
  ok = c.at("profile_digest").string(v.profile_digest, { minLength: 1 }) && ok;
  return ok;
}

export function isEvidenceItem(v: unknown, c: Checker): v is EvidenceItem {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, EVIDENCE_SCHEMA);
  ok = c.at("id").string(v.id, { minLength: 1 }) && ok;
  ok = c.at("kind").oneOf(v.kind, EVIDENCE_KINDS) && ok;
  ok = c.at("label").string(v.label) && ok;
  ok = c.at("captured_at").dateTime(v.captured_at) && ok;
  ok = c.at("note").string(v.note) && ok;
  return ok;
}

export function isCandidatePreferences(v: unknown, c: Checker): v is CandidatePreferences {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, PREFERENCES_SCHEMA);
  ok = c.at("candidate_ref").string(v.candidate_ref, { minLength: 1 }) && ok;
  ok = c.at("display_name").string(v.display_name) && ok;
  ok = c.at("note").string(v.note) && ok;
  ok = c.at("remote").oneOf(v.remote, ["remote", "hybrid", "onsite"] as const) && ok;
  ok = c.at("seniority").string(v.seniority) && ok;
  ok = c.at("target_roles").stringArray(v.target_roles) && ok;
  return ok;
}

export function isResumeVariant(v: unknown, c: Checker): v is ResumeVariant {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, RESUME_SCHEMA);
  for (const k of ["id", "candidate_ref", "label"] as const) ok = c.at(k).string(v[k], { minLength: 1 }) && ok;
  for (const k of ["target_job_id", "parent_variant_id"] as const) ok = nullable(v[k], () => c.at(k).string(v[k])) && ok;
  ok = c.at("version").number(v.version, { integer: true, min: 1 }) && ok;
  ok = c.at("created_at").dateTime(v.created_at) && ok;
  ok = c.at("updated_at").dateTime(v.updated_at) && ok;
  const cs = c.at("sections");
  if (cs.array(v.sections)) {
    v.sections.forEach((raw, i) => {
      const ci = cs.at(i);
      if (!ci.isObject(raw)) return void (ok = false);
      ok = ci.at("id").string(raw.id, { minLength: 1 }) && ok;
      ok = ci.at("heading").string(raw.heading) && ok;
      const cb = ci.at("bullets");
      if (!cb.array(raw.bullets)) return void (ok = false);
      raw.bullets.forEach((b, j) => {
        const cj = cb.at(j);
        if (!cj.isObject(b)) return void (ok = false);
        ok = cj.at("id").string(b.id, { minLength: 1 }) && ok;
        ok = cj.at("text").string(b.text) && ok;
        ok = cj.at("claim_ids").stringArray(b.claim_ids) && ok;
        ok = cj.at("origin").oneOf(b.origin, BULLET_ORIGINS) && ok;
      });
    });
  } else ok = false;
  if (v.approval !== null) {
    const ca = c.at("approval");
    if (ca.isObject(v.approval)) {
      ok = ca.at("approved_at").dateTime(v.approval.approved_at) && ok;
      ok = ca.at("approval_token_ref").string(v.approval.approval_token_ref, { minLength: 1 }) && ok;
      ok = ca.at("content_digest").string(v.approval.content_digest, { minLength: 1 }) && ok;
    } else ok = false;
  }
  return ok;
}

export function isExperimentAssignment(v: unknown, c: Checker): v is ExperimentAssignment {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, ASSIGNMENT_SCHEMA);
  for (const k of ["id", "experiment_id", "unit_id", "variant", "algorithm", "seed_digest"] as const) {
    ok = c.at(k).string(v[k], { minLength: 1 }) && ok;
  }
  ok = c.at("unit_label").string(v.unit_label) && ok;
  ok = c.at("stratum").string(v.stratum) && ok;
  ok = c.at("assigned_at").dateTime(v.assigned_at) && ok;
  ok = nullable(v.exposure_logged_at, () => c.at("exposure_logged_at").dateTime(v.exposure_logged_at)) && ok;
  return ok;
}

export function isExperimentOutcome(v: unknown, c: Checker): v is ExperimentOutcome {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, OUTCOME_SCHEMA);
  for (const k of ["assignment_id", "experiment_id", "metric"] as const) ok = c.at(k).string(v[k], { minLength: 1 }) && ok;
  ok = c.at("status").oneOf(v.status, OUTCOME_STATES) && ok;
  ok = nullable(v.value, () => c.at("value").boolean(v.value)) && ok;
  ok = c.at("recorded_at").dateTime(v.recorded_at) && ok;
  ok = c.at("data_origin").oneOf(v.data_origin, DATA_ORIGINS) && ok;
  // Censored/missing outcomes must not carry a value; observed ones must.
  if (v.status === "observed" && typeof v.value !== "boolean") ok = c.at("value").fail("observed outcome needs a boolean value");
  if (v.status !== "observed" && v.value !== null) ok = c.at("value").fail("censored or missing outcome must have value null");
  return ok;
}

function metric(c: Checker, v: unknown): boolean {
  if (v === null) return true;
  if (!c.isObject(v)) return false;
  const a = c.at("k").number(v.k, { integer: true, min: 1 });
  const b = unit(c.at("value"), v.value);
  return a && b;
}

export function isAtlasProjection(v: unknown, c: Checker): v is AtlasProjection {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, PROJECTION_SCHEMA);
  ok = c.at("projection_id").string(v.projection_id, { minLength: 1 }) && ok;
  ok = c.at("method").oneOf(v.method, PROJECTION_METHODS) && ok;
  ok = c.at("generated_at").dateTime(v.generated_at) && ok;
  const ci = c.at("input");
  if (ci.isObject(v.input)) {
    ok = ci.at("representation").string(v.input.representation) && ok;
    ok = ci.at("n_points").number(v.input.n_points, { integer: true, min: 0 }) && ok;
    ok = ci.at("n_dims").number(v.input.n_dims, { integer: true, min: 1 }) && ok;
  } else ok = false;
  const ca = c.at("axes");
  if (ca.array(v.axes)) {
    if (v.axes.length < 2 || v.axes.length > 3) ok = ca.fail("expected 2 or 3 axes");
    v.axes.forEach((raw, i) => {
      const cx = ca.at(i);
      if (!cx.isObject(raw)) return void (ok = false);
      ok = cx.at("label").string(raw.label) && ok;
      ok = nullable(raw.explained_variance_ratio, () => unit(cx.at("explained_variance_ratio"), raw.explained_variance_ratio)) && ok;
      ok = cx.at("description").string(raw.description) && ok;
    });
  } else ok = false;
  const cd = c.at("diagnostics");
  if (cd.isObject(v.diagnostics)) {
    const d = v.diagnostics as Obj;
    ok = nullable(d.cumulative_explained_variance, () => unit(cd.at("cumulative_explained_variance"), d.cumulative_explained_variance)) && ok;
    ok = metric(cd.at("trustworthiness"), d.trustworthiness) && ok;
    ok = metric(cd.at("continuity"), d.continuity) && ok;
  } else ok = false;
  ok = c.at("warnings").stringArray(v.warnings) && ok;
  const cp = c.at("points");
  const ids = new Set<string>();
  if (cp.array(v.points)) {
    v.points.forEach((raw, i) => {
      const cx = cp.at(i);
      if (!isAtlasPoint(raw, cx)) return void (ok = false);
      if (raw.projection_id !== v.projection_id) ok = cx.at("projection_id").fail("point belongs to a different projection");
      if (raw.projection_method !== v.method) ok = cx.at("projection_method").fail("point method differs from projection method");
      if (ids.has(raw.entity_id)) ok = cx.at("entity_id").fail("duplicate entity_id in projection");
      ids.add(raw.entity_id);
    });
  } else ok = false;
  const cen = c.at("entities");
  if (cen.array(v.entities)) {
    v.entities.forEach((raw, i) => {
      const cx = cen.at(i);
      if (!cx.isObject(raw)) return void (ok = false);
      ok = cx.at("entity_id").string(raw.entity_id, { minLength: 1 }) && ok;
      ok = nullable(raw.role_family, () => cx.at("role_family").string(raw.role_family)) && ok;
      ok = cx.at("evidence_basis").oneOf(raw.evidence_basis, [...CLAIM_STATUSES, "mixed", "not-applicable"] as const) && ok;
      ok = nullable(raw.job_id, () => cx.at("job_id").string(raw.job_id)) && ok;
      ok = nullable(raw.scenario_id, () => cx.at("scenario_id").string(raw.scenario_id)) && ok;
      ok = cx.at("detail").string(raw.detail) && ok;
      ok = cx.at("top_skills").stringArray(raw.top_skills) && ok;
    });
  } else ok = false;
  const cpa = c.at("paths");
  if (cpa.array(v.paths)) {
    v.paths.forEach((raw, i) => {
      const cx = cpa.at(i);
      if (!cx.isObject(raw)) return void (ok = false);
      for (const k of ["id", "from_entity_id", "to_entity_id", "scenario_id"] as const) ok = cx.at(k).string(raw[k], { minLength: 1 }) && ok;
      ok = cx.at("label").string(raw.label) && ok;
      for (const k of ["from_entity_id", "to_entity_id"] as const) {
        if (typeof raw[k] === "string" && !ids.has(raw[k])) ok = cx.at(k).fail("path endpoint is not a point in this projection");
      }
    });
  } else ok = false;
  return ok;
}

export function isScenarioOutputs(v: unknown, c: Checker): v is ScenarioOutputs {
  if (!c.isObject(v)) return false;
  let ok = constant(c.at("schema_version"), v.schema_version, SCENARIO_OUTPUTS_SCHEMA);
  ok = c.at("target_role_family").string(v.target_role_family) && ok;
  ok = c.at("target_skills").stringArray(v.target_skills) && ok;
  ok = unit(c.at("coverage_before"), v.coverage_before) && ok;
  ok = unit(c.at("coverage_after"), v.coverage_after) && ok;
  ok = c.at("leadership_exposure").oneOf(v.leadership_exposure, EXPOSURE_LEVELS) && ok;
  ok = c.at("uncertainty").oneOf(v.uncertainty, ["low", "medium", "high"] as const) && ok;
  ok = c.at("uncertainty_note").string(v.uncertainty_note) && ok;
  ok = nullable(v.atlas_entity_id, () => c.at("atlas_entity_id").string(v.atlas_entity_id)) && ok;
  return ok;
}

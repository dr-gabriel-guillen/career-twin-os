/**
 * PROPOSED (not frozen) payload shapes that lane D needs from lanes A/B/C but that
 * have no schema in `/contracts` yet. Each one is requested through a
 * NEEDS-INTERFACE comment on the owning lane's Issue (see docs/ADAPTERS.md).
 *
 * Until the owning lane publishes its own shape, D ships fixture stand-ins that
 * follow these definitions. Every shape carries a `schema_version` ending in
 * `-proposed` so nobody can mistake it for an agreed contract.
 */
import type { AtlasPoint, ClaimStatus, ProjectionMethod } from "@/contracts/types";

// ---------- Lane A: MatchExplanation ----------
export const MATCH_SCHEMA = "match-explanation/0.1-proposed" as const;
export const SCORE_TYPE = "relative_heuristic_not_hiring_probability" as const;
export const EVIDENCE_STRENGTHS = ["supported", "self-reported", "unverified", "contradicted", "missing"] as const;
export type EvidenceStrength = (typeof EVIDENCE_STRENGTHS)[number];
export const CONSTRAINT_STATUSES = ["pass", "fail", "unknown"] as const;
export type ConstraintStatus = (typeof CONSTRAINT_STATUSES)[number];

export interface MatchComponent {
  key: string;
  label: string;
  /** 0..1, or null when the input is UNKNOWN (never coerced to zero). */
  value: number | null;
  /** Weight inside its group; weights of known components sum to 1. Null when the engine does not publish weights. */
  weight: number | null;
  status: "known" | "unknown";
  note: string;
}

export interface MatchSkillEvidence {
  skill: string;
  evidence_strength: EvidenceStrength;
  claim_ids: string[];
}

export interface HardConstraint {
  key: string;
  label: string;
  status: ConstraintStatus;
  detail: string;
}

export interface ScoreGroup {
  /** 0..100 relative heuristic, or null if it cannot be computed. */
  score: number | null;
  components: MatchComponent[];
}

export interface MatchExplanation {
  schema_version: typeof MATCH_SCHEMA;
  job_id: string;
  candidate_ref: string;
  /** Null when the engine does not say when it ran. */
  computed_at: string | null;
  engine: { name: string; version: string };
  score_type: (typeof SCORE_TYPES)[number];
  near_term_fit: ScoreGroup;
  strategic_value: ScoreGroup;
  hard_constraints: HardConstraint[];
  skills: MatchSkillEvidence[];
  unknown_inputs: string[];
  warnings: string[];
  /** Digest of the claim snapshot the engine used; lets the UI flag stale scores. */
  profile_digest: string;
  /** Single overall score, when the engine publishes one (lane A, reference engine). */
  overall?: { score: number; interval: { low: number; high: number } | null; note: string } | null;
  /** Engine-authored hypotheticals. Always labelled hypothetical; never a forecast. */
  counterfactuals?: { change: string; score_delta: number; status: string }[];
}

/** Score-type constants that explicitly deny being a probability. Anything else is refused. */
export const SCORE_TYPES = [SCORE_TYPE, "relative_heuristic_not_offer_probability"] as const;

// ---------- Lane A: EvidenceItem ----------
export const EVIDENCE_SCHEMA = "evidence-item/0.1-proposed" as const;
export const EVIDENCE_KINDS = ["document", "artifact", "attestation", "record"] as const;
export interface EvidenceItem {
  schema_version: typeof EVIDENCE_SCHEMA;
  id: string;
  kind: (typeof EVIDENCE_KINDS)[number];
  label: string;
  captured_at: string;
  note: string;
}

// ---------- Lane B: ResumeVariant ----------
export const RESUME_SCHEMA = "resume-variant/0.1-proposed" as const;
export const BULLET_ORIGINS = ["from-claims", "manual-edit"] as const;
export interface ResumeBullet {
  id: string;
  text: string;
  /** Claims this bullet is a rendering of. Empty = unsupported. */
  claim_ids: string[];
  origin: (typeof BULLET_ORIGINS)[number];
}
export interface ResumeSection {
  id: string;
  heading: string;
  bullets: ResumeBullet[];
}
export interface ResumeApproval {
  approved_at: string;
  approval_token_ref: string;
  /** SHA-256 of the exact approved content; any later edit invalidates approval. */
  content_digest: string;
}
export interface ResumeVariant {
  schema_version: typeof RESUME_SCHEMA;
  id: string;
  candidate_ref: string;
  label: string;
  target_job_id: string | null;
  parent_variant_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  sections: ResumeSection[];
  approval: ResumeApproval | null;
}

// ---------- Lane B: ExperimentAssignment / ExperimentOutcome ----------
export const ASSIGNMENT_SCHEMA = "experiment-assignment/0.1-proposed" as const;
export interface ExperimentAssignment {
  schema_version: typeof ASSIGNMENT_SCHEMA;
  id: string;
  experiment_id: string;
  unit_id: string;
  unit_label: string;
  stratum: string;
  variant: string;
  assigned_at: string;
  algorithm: string;
  seed_digest: string;
  /** Null until the variant was actually exposed (application sent manually). */
  exposure_logged_at: string | null;
}

export const OUTCOME_SCHEMA = "experiment-outcome/0.1-proposed" as const;
export const OUTCOME_STATES = ["observed", "censored", "missing"] as const;
export const DATA_ORIGINS = ["synthetic-simulation", "user-recorded"] as const;
export interface ExperimentOutcome {
  schema_version: typeof OUTCOME_SCHEMA;
  assignment_id: string;
  experiment_id: string;
  metric: string;
  /** observed: value known · censored: outcome window still open · missing: never recorded. */
  status: (typeof OUTCOME_STATES)[number];
  value: boolean | null;
  recorded_at: string;
  data_origin: (typeof DATA_ORIGINS)[number];
}

// ---------- Lane C: AtlasProjection (points + diagnostics) ----------
export const PROJECTION_SCHEMA = "atlas-projection/0.1-proposed" as const;
/** Lane C adopted this shape as "atlas-projection/0.1" (a superset). Both are accepted. */
export const PROJECTION_SCHEMAS = [PROJECTION_SCHEMA, "atlas-projection/0.1"] as const;
export interface UnplacedEntity {
  entity_id: string | null;
  reason: string;
}
export interface AtlasAxis {
  label: string;
  explained_variance_ratio: number | null;
  description: string;
}
export interface NeighbourhoodMetric {
  k: number;
  value: number;
}
export interface AtlasEntity {
  entity_id: string;
  role_family: string | null;
  /** Aggregate truth state of the claims behind this point, if it is candidate-derived. */
  evidence_basis: ClaimStatus | "mixed" | "not-applicable";
  job_id: string | null;
  scenario_id: string | null;
  detail: string;
  top_skills: string[];
}
export interface AtlasPath {
  id: string;
  from_entity_id: string;
  to_entity_id: string;
  scenario_id: string;
  label: string;
}
export interface AtlasProjection {
  schema_version: (typeof PROJECTION_SCHEMAS)[number];
  projection_id: string;
  method: ProjectionMethod;
  generated_at: string;
  /** `n_dims` is null when the producer does not state the dimensionality. */
  input: { representation: string; n_points: number; n_dims: number | null };
  axes: AtlasAxis[];
  diagnostics: {
    cumulative_explained_variance: number | null;
    trustworthiness: NeighbourhoodMetric | null;
    continuity: NeighbourhoodMetric | null;
    /** Lane C extras. */
    knn_recall?: NeighbourhoodMetric | null;
    flags?: string[];
  };
  /** Entities that could not be drawn (e.g. no observed skills). Unknown is not the map centre. */
  unplaced?: UnplacedEntity[];
  /** Computed upstream: true only if every fitted posting declares source "synthetic". */
  synthetic_only?: boolean;
  warnings: string[];
  points: AtlasPoint[];
  entities: AtlasEntity[];
  paths: AtlasPath[];
}

// ---------- Lane C: TransitionScenario.outputs (free-form object in v0.1) ----------
export const SCENARIO_OUTPUTS_SCHEMA = "scenario-outputs/0.1-proposed" as const;
export const SCENARIO_OUTPUTS_SCHEMAS = [SCENARIO_OUTPUTS_SCHEMA, "scenario-outputs/0.1"] as const;
/** Lane C: per-action result of its evidence model. D uses this, not skill names, for coverage. */
export interface AppliedAction {
  skill: string;
  feature: string;
  effort_hours: number;
  reaches_evidenced_level: boolean;
}
export const EXPOSURE_LEVELS = ["low", "medium", "high", "unknown"] as const;
export interface ScenarioOutputs {
  schema_version: (typeof SCENARIO_OUTPUTS_SCHEMAS)[number];
  target_role_family: string;
  target_skills: string[];
  /** Null when the role has no target skills. */
  coverage_before: number | null;
  coverage_after: number | null;
  coverage_definition?: string;
  leadership_exposure_definition?: string;
  applied_actions?: AppliedAction[];
  leadership_exposure: (typeof EXPOSURE_LEVELS)[number];
  uncertainty: "low" | "medium" | "high";
  uncertainty_note: string;
  atlas_entity_id: string | null;
  /**
   * How far the plan moves the skill vector, in the full space and on the 2-D map (lane C). The map
   * shows only part of a movement; the full-space distance to the role profile is the honest measure.
   */
  movement?: {
    full_space_distance_to_role?: { before: number; after: number; role: string } | null;
    full_space_shift_length?: number;
    map_shift_length?: number;
    note?: string;
  };
}

// ---------- Candidate preferences (user-stated values; mirrors fixtures/synthetic_profile.json) ----------
export const PREFERENCES_SCHEMA = "candidate-preferences/0.1-proposed" as const;
export interface CandidatePreferences {
  schema_version: typeof PREFERENCES_SCHEMA;
  candidate_ref: string;
  display_name: string;
  note: string;
  remote: "remote" | "hybrid" | "onsite";
  seniority: string;
  target_roles: string[];
}

// ---------- Upstream experiment analysis (lane B / pipeline). Rendered as given; never recomputed. ----------
export const ANALYSIS_VERDICTS = ["EVIDENCE_FOR_DIFFERENCE", "INCONCLUSIVE", "INTERIM_NO_CLAIM", "EXPLORATORY_NO_CLAIM", "NOT_ANALYZABLE", "OBSERVATIONAL_ONLY"] as const;
export type AnalysisVerdict = (typeof ANALYSIS_VERDICTS)[number];
export interface UpstreamAnalysis {
  experiment_id: string;
  verdict: AnalysisVerdict;
  estimand: string | null;
  arms: { variant: string; n_assigned: number; n_success: number | null; n_censored: number | null; n_missing: number | null }[];
  /** e.g. "FINAL" or "INTERIM", when the producer states it. */
  phase: string | null;
  /** `ci_level` is null when the producer does not state it. */
  effect: { contrast: string; estimate: number; ci_low: number; ci_high: number; ci_level: number | null; method: string } | null;
  reasons_withheld: string[];
  warnings: string[];
  interpretation_limits: string[];
  producer: string;
}

export interface MatchRefusal {
  job_id: string;
  reasons: string[];
}

/** Per-posting facts from lane A's `ext` envelope (jobs_ext.json). Display only. */
export interface JobFacet {
  /** Duplicate cluster id and its size, as computed by lane A. */
  duplicate_cluster: string | null;
  duplicate_members: string[];
  duplicate_of: string | null;
  security_flags: string[];
  /** Null when the envelope does not say: unknown provenance, never assumed real or synthetic. */
  synthetic: boolean | null;
  status_reason: string | null;
  employer_id: string | null;
}

/** What the bundle was built from, for honest labelling and for lane E's ui_report. */
export interface BundleProvenance {
  data_source: "fixtures" | "artifacts";
  /** Per payload: which upstream dialect was recognised. */
  dialects: Record<string, string>;
  /** Payloads whose producer declares itself a stub or mock. */
  mock_sources: string[];
  /** Payloads the pipeline did not provide at all. */
  missing: string[];
}

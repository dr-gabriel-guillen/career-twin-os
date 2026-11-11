/**
 * TypeScript mirrors of the FROZEN v0.1 JSON contracts in `/contracts` (repo root).
 * These types are hand-written and kept honest by `tests/unit/contracts.test.ts`,
 * which validates every shipped fixture against the frozen JSON Schemas with Ajv
 * and asserts the enum lists below equal the schema enums.
 *
 * Lane D does not own these shapes. Changing them requires an RFC.
 */

export const CONTRACT_VERSION = "0.1" as const;

// ---- job-posting.schema.json (owner: lane A) ----
export const JOB_SOURCES = ["synthetic", "greenhouse", "lever", "ashby", "user-import", "other-permitted"] as const;
export const JOB_STATUSES = ["active", "expired", "unknown"] as const;
export const REMOTE_POLICIES = ["remote", "hybrid", "onsite", "unknown"] as const;
export const COMP_BASES = ["base", "total", "unknown"] as const;
export type JobSource = (typeof JOB_SOURCES)[number];
export type JobStatus = (typeof JOB_STATUSES)[number];
export type RemotePolicy = (typeof REMOTE_POLICIES)[number];
export type CompBasis = (typeof COMP_BASES)[number];

export interface Compensation {
  currency: string;
  min: number | null;
  max: number | null;
  basis: CompBasis;
  verified: boolean;
}

export interface JobPosting {
  id: string;
  source: JobSource;
  source_id: string;
  title: string;
  organization: string;
  url: string;
  fetched_at: string;
  status: JobStatus;
  skills: string[];
  seniority: string;
  employment_type: string;
  location?: string | null;
  remote_policy?: RemotePolicy;
  compensation?: Compensation | null;
  license_note?: string;
  role_family?: string;
  requirements?: string[];
  source_evidence?: string[];
}

// ---- profile-claim.schema.json (owner: lane A) ----
export const CLAIM_TYPES = ["skill", "role", "employment", "education", "credential", "project", "achievement", "other"] as const;
export const CLAIM_STATUSES = ["supported", "self-reported", "contradicted", "unverified"] as const;
export const CLAIM_VISIBILITIES = ["private", "shareable", "public-approved"] as const;
export type ClaimType = (typeof CLAIM_TYPES)[number];
export type ClaimStatus = (typeof CLAIM_STATUSES)[number];
export type ClaimVisibility = (typeof CLAIM_VISIBILITIES)[number];

export interface ProfileClaim {
  id: string;
  subject_id: string;
  claim_type: ClaimType;
  claim_text: string;
  status: ClaimStatus;
  evidence_ids: string[];
  visibility: ClaimVisibility;
  updated_at: string;
  skills?: string[];
  valid_to?: string | null;
}

// ---- application-event.schema.json (owner: lane B) ----
export const EVENT_TYPES = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "rejected", "offer", "withdrawn", "note"] as const;
export const EVENT_ACTORS = ["user", "system", "import"] as const;
export const OUTCOME_STATUSES = ["observed", "self-reported", "unknown"] as const;
export type ApplicationEventType = (typeof EVENT_TYPES)[number];
export type EventActor = (typeof EVENT_ACTORS)[number];
export type OutcomeStatus = (typeof OUTCOME_STATUSES)[number];

export interface ApplicationEvent {
  id: string;
  application_id: string;
  job_id: string;
  resume_variant_id?: string | null;
  event_type: ApplicationEventType;
  recorded_at: string;
  occurred_at?: string | null;
  actor: EventActor;
  source: string;
  outcome_status: OutcomeStatus;
  experiment_id?: string | null;
  approval_token_ref?: string | null;
  notes?: string;
}

// ---- experiment.schema.json (owner: lane B) ----
export const EXPERIMENT_STATUSES = ["draft", "preregistered", "running", "paused", "closed"] as const;
export const ASSIGNMENT_UNITS = ["job", "employer-cluster", "application"] as const;
export type ExperimentStatus = (typeof EXPERIMENT_STATUSES)[number];
export type AssignmentUnit = (typeof ASSIGNMENT_UNITS)[number];

export interface ExperimentSpec {
  id: string;
  title: string;
  hypothesis: string;
  status: ExperimentStatus;
  variants: string[];
  assignment_unit: AssignmentUnit;
  strata: string[];
  primary_metric: string;
  design_locked_at: string | null;
  stopping_rule: string;
  consent_required: boolean;
  outcome_window_days?: number;
  secondary_metrics?: string[];
  analysis_plan?: string;
  seed_digest?: string;
}

// ---- atlas-point.schema.json (owner: lane C) ----
export const REPRESENTATION_TYPES = ["candidate", "job", "role-centroid", "scenario"] as const;
export const PROJECTION_METHODS = ["pca", "umap", "interpretable-axes"] as const;
export type RepresentationType = (typeof REPRESENTATION_TYPES)[number];
export type ProjectionMethod = (typeof PROJECTION_METHODS)[number];

export interface AtlasPoint {
  entity_id: string;
  projection_id: string;
  label: string;
  coords: number[];
  source_vector_ref: string;
  representation_type: RepresentationType;
  projection_method: ProjectionMethod;
  neighborhood_quality?: number | null;
  fit_metric_ref?: string | null;
  warnings?: string[];
}

// ---- transition-scenario.schema.json (owner: lane C) ----
export interface PlannedAction {
  skill: string;
  effort_hours: number;
  evidence_goal?: string;
}

export interface TransitionScenario {
  id: string;
  candidate_ref: string;
  target_role: string;
  planned_actions: PlannedAction[];
  time_horizon_months: number;
  status: "hypothetical";
  assumptions: string[];
  outputs: Record<string, unknown>;
  risks?: string[];
}

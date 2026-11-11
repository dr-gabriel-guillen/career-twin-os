/**
 * Versioned adapters between upstream lanes (A/B/C) and the UI.
 *
 * The UI only ever sees a validated `DataBundle`. Each adapter:
 *  - names the upstream lane and the contract (frozen or proposed) it reads,
 *  - validates every record at runtime and REJECTS non-conforming ones,
 *  - never repairs, guesses or fills in upstream data.
 *
 * To switch from the fixture stand-in to real upstream output, implement
 * `PayloadSource` for that output; nothing else in the UI changes.
 */
import type {
  ApplicationEvent,
  ExperimentSpec,
  JobPosting,
  ProfileClaim,
  TransitionScenario,
} from "@/contracts/types";
import {
  isApplicationEvent,
  isExperimentSpec,
  isJobPosting,
  isProfileClaim,
  isTransitionScenario,
} from "./frozen";
import type {
  AtlasProjection,
  BundleProvenance,
  CandidatePreferences,
  EvidenceItem,
  ExperimentAssignment,
  ExperimentOutcome,
  MatchExplanation,
  MatchRefusal,
  ResumeVariant,
  ScenarioOutputs,
  UpstreamAnalysis,
} from "./proposed";
import {
  isAtlasProjection,
  isCandidatePreferences,
  isEvidenceItem,
  isExperimentAssignment,
  isExperimentOutcome,
  isMatchExplanation,
  isResumeVariant,
  isScenarioOutputs,
} from "./proposedValidators";
import { parseList, parseOne, type AdapterIssue, type ItemValidator, type RejectedItem } from "./validate";

export type PayloadName =
  | "jobs.feed"
  | "claims"
  | "evidence"
  | "preferences"
  | "matches"
  | "scenarios"
  | "atlas.pca"
  | "atlas.axes"
  | "experiments"
  | "assignments"
  | "outcomes"
  | "resume-variants"
  | "application-events"
  | "manifest";

/** Returned by a source for a payload it does not provide. Absent is not an error and is never back-filled. */
export const ABSENT: unique symbol = Symbol("payload not provided by this source");

/** Data that travels beside the payloads: upstream analyses, refusals, and provenance. */
export interface BundleExtras {
  analyses: UpstreamAnalysis[];
  matchRefusals: MatchRefusal[];
  provenance: BundleProvenance;
  notes: string[];
}

/** Where payloads come from: bundled fixtures, or the artifacts of an integration run. */
export interface PayloadSource {
  readonly id: string;
  readonly label: string;
  load(name: PayloadName): Promise<unknown>;
  extras?(): Promise<BundleExtras>;
}

export interface AdapterSpec {
  id: string;
  /** Adapter version; bump on any behavioural change. */
  version: string;
  lane: "A" | "B" | "C" | "D";
  payload: PayloadName;
  contract: string;
  basis: "frozen-contract" | "proposed-stand-in";
  note: string;
}

export const ADAPTERS: AdapterSpec[] = [
  { id: "jobs", version: "1.0.0", lane: "A", payload: "jobs.feed", contract: "contracts/job-posting.schema.json (v0.1)", basis: "frozen-contract", note: "Normalized JobPosting list." },
  { id: "claims", version: "1.0.0", lane: "A", payload: "claims", contract: "contracts/profile-claim.schema.json (v0.1)", basis: "frozen-contract", note: "Profile claims with truth state." },
  { id: "evidence", version: "0.1.0", lane: "A", payload: "evidence", contract: "evidence-item/0.1-proposed", basis: "proposed-stand-in", note: "Evidence registry referenced by evidence_ids. No frozen schema yet." },
  { id: "preferences", version: "0.1.0", lane: "A", payload: "preferences", contract: "candidate-preferences/0.1-proposed", basis: "proposed-stand-in", note: "User-stated preferences. No frozen schema yet." },
  { id: "matches", version: "0.1.0", lane: "A", payload: "matches", contract: "match-explanation/0.1-proposed", basis: "proposed-stand-in", note: "MatchExplanation is named in CONTRACTS.md but has no frozen schema yet." },
  { id: "application-events", version: "1.0.0", lane: "B", payload: "application-events", contract: "contracts/application-event.schema.json (v0.1)", basis: "frozen-contract", note: "Immutable application events." },
  { id: "experiments", version: "1.0.0", lane: "B", payload: "experiments", contract: "contracts/experiment.schema.json (v0.1)", basis: "frozen-contract", note: "ExperimentSpec list." },
  { id: "assignments", version: "0.1.0", lane: "B", payload: "assignments", contract: "experiment-assignment/0.1-proposed", basis: "proposed-stand-in", note: "ExperimentAssignment is named in CONTRACTS.md but has no frozen schema yet." },
  { id: "outcomes", version: "0.1.0", lane: "B", payload: "outcomes", contract: "experiment-outcome/0.1-proposed", basis: "proposed-stand-in", note: "Outcome records with censoring. No frozen schema yet." },
  { id: "resume-variants", version: "0.1.0", lane: "B", payload: "resume-variants", contract: "resume-variant/0.1-proposed", basis: "proposed-stand-in", note: "Résumé variant version is named in CONTRACTS.md but has no frozen schema yet." },
  { id: "atlas-pca", version: "0.1.0", lane: "C", payload: "atlas.pca", contract: "atlas-projection/0.1-proposed wrapping contracts/atlas-point.schema.json (v0.1)", basis: "proposed-stand-in", note: "AtlasPoint is frozen; the projection envelope with diagnostics is proposed." },
  { id: "atlas-axes", version: "0.1.0", lane: "C", payload: "atlas.axes", contract: "atlas-projection/0.1-proposed wrapping contracts/atlas-point.schema.json (v0.1)", basis: "proposed-stand-in", note: "Interpretable-axes projection." },
  { id: "scenarios", version: "1.0.0", lane: "C", payload: "scenarios", contract: "contracts/transition-scenario.schema.json (v0.1) + scenario-outputs/0.1-proposed", basis: "frozen-contract", note: "TransitionScenario is frozen; the shape of `outputs` is proposed." },
];

export interface AdapterReport {
  adapter: AdapterSpec;
  accepted: number;
  rejected: RejectedItem[];
  fatal: AdapterIssue[];
  /** Cross-reference problems (e.g. a match for a job that is not in the feed). */
  integrity: string[];
  /** The source does not provide this payload at all. Not an error. */
  absent: boolean;
}

export interface ScenarioView {
  scenario: TransitionScenario;
  /** Null when `outputs` does not follow the proposed shape; the UI then shows raw facts only. */
  outputs: ScenarioOutputs | null;
}

export interface FixtureManifest {
  fixture_clock: string;
  data_origin: string;
  notice: string;
  role_family_labels: Record<string, string>;
  generator: { name: string; version: string };
  experiment: { synthetic_true_rate_all_variants: number };
}

export interface DataBundle {
  sourceId: string;
  sourceLabel: string;
  /** Reference "now" for freshness maths. Fixture mode uses the fixture clock, not the wall clock. */
  clock: string;
  manifest: FixtureManifest | null;
  jobs: JobPosting[];
  claims: ProfileClaim[];
  evidence: EvidenceItem[];
  preferences: CandidatePreferences | null;
  matches: MatchExplanation[];
  scenarios: ScenarioView[];
  projections: AtlasProjection[];
  experiments: ExperimentSpec[];
  assignments: ExperimentAssignment[];
  outcomes: ExperimentOutcome[];
  resumeVariants: ResumeVariant[];
  applicationEvents: ApplicationEvent[];
  /** Experiment analyses computed upstream. Rendered as given, in place of local statistics. */
  analyses: UpstreamAnalysis[];
  /** Postings the matching engine refused, with its reasons. Never silently dropped. */
  matchRefusals: MatchRefusal[];
  provenance: BundleProvenance;
  sourceNotes: string[];
  reports: AdapterReport[];
}

const spec = (id: string): AdapterSpec => {
  const s = ADAPTERS.find((a) => a.id === id);
  if (!s) throw new Error(`Unknown adapter ${id}`);
  return s;
};

async function listAdapter<T>(
  source: PayloadSource,
  id: string,
  validate: ItemValidator<T>,
): Promise<{ items: T[]; report: AdapterReport }> {
  const adapter = spec(id);
  let payload: unknown;
  try {
    payload = await source.load(adapter.payload);
  } catch (e) {
    return {
      items: [],
      report: { adapter, accepted: 0, rejected: [], fatal: [{ path: "$", message: `payload could not be loaded: ${errorText(e)}` }], integrity: [], absent: false },
    };
  }
  if (payload === ABSENT) return { items: [], report: { adapter, accepted: 0, rejected: [], fatal: [], integrity: [], absent: true } };
  const parsed = parseList(payload, validate);
  return { items: parsed.items, report: { adapter, accepted: parsed.items.length, rejected: parsed.rejected, fatal: parsed.fatal, integrity: [], absent: false } };
}

async function oneAdapter<T>(
  source: PayloadSource,
  id: string,
  validate: ItemValidator<T>,
): Promise<{ value: T | null; report: AdapterReport }> {
  const adapter = spec(id);
  try {
    const payload = await source.load(adapter.payload);
    if (payload === ABSENT) return { value: null, report: { adapter, accepted: 0, rejected: [], fatal: [], integrity: [], absent: true } };
    const parsed = parseOne(payload, validate);
    if (parsed.ok) return { value: parsed.value, report: { adapter, accepted: 1, rejected: [], fatal: [], integrity: [], absent: false } };
    return { value: null, report: { adapter, accepted: 0, rejected: [], fatal: parsed.issues, integrity: [], absent: false } };
  } catch (e) {
    return { value: null, report: { adapter, accepted: 0, rejected: [], fatal: [{ path: "$", message: `payload could not be loaded: ${errorText(e)}` }], integrity: [], absent: false } };
  }
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Drop duplicate ids (first wins) and report them. Upstream ids are meant to be stable and unique. */
function uniqueBy<T>(items: T[], key: (t: T) => string, report: AdapterReport): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const k = key(item);
    if (seen.has(k)) {
      report.integrity.push(`duplicate id '${k.slice(0, 80)}' dropped (first occurrence kept)`);
      report.accepted -= 1;
      return false;
    }
    seen.add(k);
    return true;
  });
}

function readManifest(raw: unknown): FixtureManifest | null {
  if (typeof raw !== "object" || raw === null) return null;
  const m = raw as Record<string, unknown>;
  if (typeof m.fixture_clock !== "string" || typeof m.notice !== "string" || typeof m.data_origin !== "string") return null;
  const labels: Record<string, string> = {};
  if (typeof m.role_family_labels === "object" && m.role_family_labels !== null) {
    for (const [k, v] of Object.entries(m.role_family_labels)) if (typeof v === "string") labels[k] = v;
  }
  const gen = (typeof m.generator === "object" && m.generator !== null ? m.generator : {}) as Record<string, unknown>;
  const exp = (typeof m.experiment === "object" && m.experiment !== null ? m.experiment : {}) as Record<string, unknown>;
  return {
    fixture_clock: m.fixture_clock,
    data_origin: m.data_origin,
    notice: m.notice,
    role_family_labels: labels,
    generator: { name: String(gen.name ?? "unknown"), version: String(gen.version ?? "unknown") },
    experiment: {
      synthetic_true_rate_all_variants:
        typeof exp.synthetic_true_rate_all_variants === "number" ? exp.synthetic_true_rate_all_variants : Number.NaN,
    },
  };
}

export async function loadBundle(source: PayloadSource): Promise<DataBundle> {
  const [jobsR, claimsR, evidenceR, prefsR, matchesR, scenariosR, pcaR, axesR, expR, asgR, outR, rvR, evtR, manifestRaw] =
    await Promise.all([
      listAdapter(source, "jobs", isJobPosting),
      listAdapter(source, "claims", isProfileClaim),
      listAdapter(source, "evidence", isEvidenceItem),
      oneAdapter(source, "preferences", isCandidatePreferences),
      listAdapter(source, "matches", isMatchExplanation),
      listAdapter(source, "scenarios", isTransitionScenario),
      oneAdapter(source, "atlas-pca", isAtlasProjection),
      oneAdapter(source, "atlas-axes", isAtlasProjection),
      listAdapter(source, "experiments", isExperimentSpec),
      listAdapter(source, "assignments", isExperimentAssignment),
      listAdapter(source, "outcomes", isExperimentOutcome),
      listAdapter(source, "resume-variants", isResumeVariant),
      listAdapter(source, "application-events", isApplicationEvent),
      source.load("manifest").catch(() => null),
    ]);

  const jobs = uniqueBy(jobsR.items, (j) => j.id, jobsR.report);
  const claims = uniqueBy(claimsR.items, (c) => c.id, claimsR.report);
  const jobIds = new Set(jobs.map((j) => j.id));

  // Referential integrity: derived records must point at records that exist.
  const matches = uniqueBy(matchesR.items, (m) => m.job_id, matchesR.report).filter((m) => {
    if (jobIds.has(m.job_id)) return true;
    matchesR.report.integrity.push(`match for unknown job '${m.job_id.slice(0, 80)}' dropped`);
    matchesR.report.accepted -= 1;
    return false;
  });
  const experiments = uniqueBy(expR.items, (e) => e.id, expR.report);
  const expIds = new Set(experiments.map((e) => e.id));
  const assignments = uniqueBy(asgR.items, (a) => a.id, asgR.report).filter((a) => {
    if (expIds.has(a.experiment_id)) return true;
    asgR.report.integrity.push(`assignment for unknown experiment '${a.experiment_id.slice(0, 80)}' dropped`);
    asgR.report.accepted -= 1;
    return false;
  });
  const asgIds = new Set(assignments.map((a) => a.id));
  const outcomes = outR.items.filter((o) => {
    if (asgIds.has(o.assignment_id)) return true;
    outR.report.integrity.push(`outcome for unknown assignment '${o.assignment_id.slice(0, 80)}' dropped`);
    outR.report.accepted -= 1;
    return false;
  });
  const applicationEvents = uniqueBy(evtR.items, (e) => e.id, evtR.report);
  for (const e of applicationEvents) {
    if (!jobIds.has(e.job_id)) evtR.report.integrity.push(`event '${e.id.slice(0, 80)}' references a job that is not in the feed`);
  }

  const scenarios: ScenarioView[] = uniqueBy(scenariosR.items, (s) => s.id, scenariosR.report).map((scenario) => {
    const parsed = parseOne(scenario.outputs, isScenarioOutputs);
    if (!parsed.ok) {
      scenariosR.report.integrity.push(
        `scenario '${scenario.id.slice(0, 80)}': outputs do not follow scenario-outputs/0.1-proposed (${parsed.issues[0]?.path ?? "$"}: ${parsed.issues[0]?.message ?? "invalid"})`,
      );
    }
    return { scenario, outputs: parsed.ok ? parsed.value : null };
  });

  const manifest = readManifest(manifestRaw === ABSENT ? null : manifestRaw);
  // Extras are read after the payloads so a source can report what it actually recognised.
  const extras: BundleExtras = (await source.extras?.().catch(() => undefined)) ?? {
    analyses: [],
    matchRefusals: [],
    provenance: { data_source: "fixtures", dialects: {}, mock_sources: [], missing: [] },
    notes: [],
  };
  return {
    sourceId: source.id,
    sourceLabel: source.label,
    clock: manifest?.fixture_clock ?? new Date().toISOString(),
    manifest,
    jobs,
    claims,
    evidence: evidenceR.items,
    preferences: prefsR.value,
    matches,
    scenarios,
    projections: [pcaR.value, axesR.value].filter((p): p is AtlasProjection => p !== null),
    experiments,
    assignments,
    outcomes,
    resumeVariants: uniqueBy(rvR.items, (r) => r.id, rvR.report),
    applicationEvents,
    analyses: extras.analyses.filter((a) => expIds.has(a.experiment_id)),
    matchRefusals: extras.matchRefusals,
    provenance: extras.provenance,
    sourceNotes: [
      ...extras.notes,
      ...extras.analyses.filter((a) => !expIds.has(a.experiment_id)).map((a) => `analysis for unknown experiment '${a.experiment_id.slice(0, 80)}' was dropped`),
    ],
    reports: [jobsR, claimsR, evidenceR, prefsR, matchesR, evtR, expR, asgR, outR, rvR, pcaR, axesR, scenariosR].map((r) => r.report),
  };
}

export function totalRejected(bundle: DataBundle): number {
  return bundle.reports.reduce((n, r) => n + r.rejected.length + r.fatal.length + r.integrity.length, 0);
}

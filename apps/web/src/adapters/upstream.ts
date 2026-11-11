/**
 * Translators from UPSTREAM dialects to the view shapes the UI renders.
 *
 * Each translator recognises a dialect by its own marker, copies facts across,
 * and never computes a score, a verdict or a coordinate. What an upstream
 * payload does not say stays unknown. The result still goes through the strict
 * view validators in ./proposedValidators before anything is rendered.
 *
 * Dialects (see docs/ADAPTERS.md for the field-by-field mapping):
 *   matches   : "a-match/0.1" (lane A) · "reference" (tools/reference_engine.py, lane E stub) · lane-D view
 *   variants  : "careertwin.b.resume-variant.v1" (lane B) · "stub" (lane E) · lane-D view
 *   assignment: lane B · lane E stub · lane-D view
 *   analysis  : "careertwin.b.analysis-report.v1" (lane B) · lane E stub
 *   atlas     : frozen AtlasPoint[] + diagnostics (lane E field names, adopted by lane C)
 */
import type { AtlasPoint, JobPosting } from "@/contracts/types";
import { humanise } from "@/lib/format";
import { ANALYSIS_VERDICTS, ASSIGNMENT_SCHEMA, MATCH_SCHEMA, PROJECTION_SCHEMA, RESUME_SCHEMA, type AnalysisVerdict, type JobFacet, type MatchRefusal, type UpstreamAnalysis } from "./proposed";

/** Lane A's canonical constant for a-match/0.1 (confirmed by lane A on Issue #12). */
const A_SCORE_TYPE = "relative_heuristic_not_offer_probability";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const round4 = (v: number) => Math.round(v * 10000) / 10000;
const MOCK_MARKER = /stub|mock/i;

export interface Translated<T> {
  payload: T;
  dialect: string;
  /** True when the producer labels its own output as a stub or mock. */
  mock: boolean;
}

// ------------------------------------------------------------------ matches
function componentFromA(key: string, raw: Obj, groupTotal: number) {
  // Lane A marks every component `state`: known | unknown | not-applicable. Anything but "known"
  // (or a null value) is excluded: it carries no value and no weight.
  const state = str(raw.state, "known");
  const value = state === "known" ? num(raw.value) : null;
  const effective = num(raw.effective_weight) ?? 0;
  const basis = str(raw.basis, "No basis given by the engine.");
  return {
    key,
    label: humanise(key),
    value: value === null ? null : clamp01(value),
    // Weight is shown as the component's share of its own group; this is display arithmetic only.
    weight: value === null || groupTotal <= 0 ? 0 : round4(clamp01(effective / groupTotal)),
    status: value === null ? "unknown" : "known",
    note: state === "not-applicable" ? `${basis} Not applicable: no such preference was given, so it is left out.` : basis,
  };
}

function matchFromA(m: Obj, result: Obj): Obj {
  const components = isObj(m.components) ? m.components : {};
  const composites = isObj(m.composites) ? m.composites : {};
  const groupOf = (group: string) => {
    const entries = Object.entries(components).filter(([, c]) => isObj(c) && c.group === group) as [string, Obj][];
    const total = entries.reduce((s, [, c]) => s + (num(c.value) === null || str(c.state, "known") !== "known" ? 0 : num(c.effective_weight) ?? 0), 0);
    return entries.map(([key, c]) => componentFromA(key, c, total));
  };
  const strengthOf = (status: unknown) => (["supported", "self-reported", "unverified", "contradicted"].includes(str(status)) ? str(status) : "unverified");
  const skills = new Map<string, Obj>();
  for (const s of Array.isArray(m.matched_skills) ? m.matched_skills : []) {
    if (isObj(s) && typeof s.skill === "string") skills.set(s.skill, { skill: s.skill, evidence_strength: strengthOf(s.proof_status), claim_ids: strings(s.claim_ids) });
  }
  for (const s of [...strings(m.missing_skills), ...strings(m.missing_preferred_skills)]) {
    if (!skills.has(s)) skills.set(s, { skill: s, evidence_strength: "missing", claim_ids: [] });
  }
  const interval = isObj(m.score_interval) ? m.score_interval : null;
  const low = interval ? num(interval.low) : null;
  const high = interval ? num(interval.high) : null;
  const score = num(m.score);
  const cautions = strings(m.cautions);
  const unsupported = (Array.isArray(m.unsupported_evidence) ? m.unsupported_evidence : [])
    .filter(isObj)
    .map((u) => `${humanise(str(u.skill))}: ${str(u.reason)}`);
  return {
    schema_version: MATCH_SCHEMA,
    job_id: str(m.job_id),
    candidate_ref: str(result.subject_id, "unknown-subject"),
    computed_at: str(result.as_of),
    engine: { name: "lane-a-matching", version: str(result.api_version) },
    // For lane A payloads only lane A's own constant is accepted; anything else is made unrecognisable
    // so the strict validator refuses the record.
    score_type: str(m.score_type ?? result.score_type) === A_SCORE_TYPE ? A_SCORE_TYPE : `refused:${str(m.score_type ?? result.score_type).slice(0, 60)}`,
    near_term_fit: { score: num(composites.near_term_fit), components: groupOf("near_term_fit") },
    strategic_value: { score: num(composites.strategic_utility), components: groupOf("strategic_utility") },
    hard_constraints: (Array.isArray(m.constraints) ? m.constraints : []).filter(isObj).map((c) => ({
      key: str(c.constraint),
      label: humanise(str(c.constraint)),
      status: ["pass", "fail", "unknown"].includes(str(c.outcome)) ? str(c.outcome) : "unknown",
      detail: str(c.detail),
    })),
    skills: [...skills.values()].sort((a, b) => str(a.skill).localeCompare(str(b.skill))),
    unknown_inputs: strings(m.unknown_inputs),
    warnings: [...cautions, ...unsupported],
    profile_digest: `upstream:${str(result.result_hash, "no-hash")}`,
    overall:
      score === null
        ? null
        : {
            score,
            interval: low !== null && high !== null ? { low, high } : null,
            note: interval ? str(interval.meaning, "Range of the score across unknown inputs.") : "The engine published no interval.",
          },
    counterfactuals: (Array.isArray(m.counterfactuals) ? m.counterfactuals : [])
      .filter(isObj)
      .filter((c) => num(c.score_delta) !== null)
      .map((c) => ({ change: str(c.change), score_delta: num(c.score_delta) as number, status: str(c.status) })),
  };
}

/** tools/reference_engine.py `score_job` output (also what lane E's stub emits). One score, three raw terms. */
function matchFromReference(m: Obj, subject: string, computedAt: string): Obj {
  const ex = isObj(m.explanation) ? m.explanation : {};
  const term = (key: string, label: string) => {
    const value = num(ex[key]);
    return { key, label, value: value === null ? null : clamp01(value), weight: 0, status: value === null ? "unknown" : "known", note: "Raw term from the reference engine. Its weight is not published in the payload." };
  };
  const score = num(m.score);
  return {
    schema_version: MATCH_SCHEMA,
    job_id: str(m.job_id),
    candidate_ref: subject,
    computed_at: computedAt,
    engine: { name: "reference-engine", version: "main" },
    score_type: str(m.score_type),
    // The reference engine publishes ONE score. No per-group score is invented here.
    near_term_fit: { score: null, components: [term("skill_coverage", "Skill coverage"), term("workstyle", "Work-style match")] },
    strategic_value: { score: null, components: [term("trajectory_score", "Trajectory term")] },
    hard_constraints: [],
    skills: strings(m.missing_skills).map((skill) => ({ skill, evidence_strength: "missing", claim_ids: [] })),
    unknown_inputs: strings(m.unknown_inputs),
    warnings: ["This engine publishes a single score with no component weights, no hard constraints and no evidence strength."],
    profile_digest: "upstream:reference",
    overall: score === null ? null : { score: Math.min(100, Math.max(0, score)), interval: null, note: "Single score from the reference engine; no interval published." },
  };
}

const refusalsOf = (result: unknown): MatchRefusal[] =>
  (isObj(result) && Array.isArray(result.refusals) ? result.refusals : []).filter(isObj).map((r) => ({ job_id: str(r.job_id), reasons: strings(r.reasons) }));

/**
 * `full` is the optional sibling file (lane A writes the bare shortlist to matches.json and the
 * complete a-match/0.1 result, with refusals, to matches_full.json).
 */
export function matchesFromUpstream(raw: unknown, ctx: { subject: string; computedAt: string; full?: unknown }): Translated<unknown> & { refusals: MatchRefusal[] } {
  if (isObj(raw) && typeof raw.api_version === "string" && raw.api_version.startsWith("a-match/")) {
    const shortlist = Array.isArray(raw.shortlist) ? raw.shortlist : [];
    return { payload: shortlist.map((m) => (isObj(m) ? matchFromA(m, raw) : m)), dialect: raw.api_version, mock: false, refusals: refusalsOf(raw) };
  }
  if (Array.isArray(raw)) {
    const first = raw.find(isObj);
    if (first && isObj(first.composites) && isObj(first.components)) {
      // Bare list of lane A MatchExplanations. Result-level facts come from the sibling file when present.
      const full = isObj(ctx.full) ? ctx.full : {};
      const result = (m: Obj): Obj => ({
        api_version: str(full.api_version, "a-match/0.1"),
        subject_id: str(full.subject_id, ctx.subject),
        as_of: str(full.as_of, isObj(m.availability) ? str(m.availability.as_of, ctx.computedAt) : ctx.computedAt),
        result_hash: str(full.result_hash, "no-hash"),
      });
      return { payload: raw.map((m) => (isObj(m) ? matchFromA(m, result(m)) : m)), dialect: "a-match/0.1 shortlist", mock: false, refusals: refusalsOf(ctx.full) };
    }
    if (first && typeof first.schema_version === "string") return { payload: raw, dialect: first.schema_version, mock: false, refusals: [] };
    if (first && isObj(first.explanation) && "score" in first) {
      return { payload: raw.map((m) => (isObj(m) ? matchFromReference(m, ctx.subject, ctx.computedAt) : m)), dialect: "reference-engine", mock: true, refusals: [] };
    }
    return { payload: raw, dialect: raw.length === 0 ? "empty" : "unrecognised", mock: false, refusals: [] };
  }
  return { payload: raw, dialect: "unrecognised", mock: false, refusals: [] };
}

// ------------------------------------------------------------------ résumé variants
function variantFrom(raw: Obj, clock: string, subject: string): { variant: Obj; dialect: string; mock: boolean; notes: string[] } {
  if (raw.schema_version === RESUME_SCHEMA) return { variant: raw, dialect: RESUME_SCHEMA, mock: false, notes: [] };
  const producer = str(raw.generated_by);
  const isB = raw.schema === "careertwin.b.resume-variant.v1" || typeof raw.lineage_id === "string";
  // Lane B says which claims it refused and why, and what the variant cannot prove. Show it, do not drop it.
  const refused = (Array.isArray(raw.refused_claims) ? raw.refused_claims : []).filter(isObj);
  const notes = [
    ...(refused.length > 0 ? [`resume_variant.json: the experiments lane refused ${refused.length} claim(s): ${refused.map((r) => `${str(r.claim_id)} (${humanise(str(r.reason))})`).join(", ")}`] : []),
    ...strings(raw.warnings).map((w) => `resume_variant.json: ${w}`),
  ];
  const bullets = (Array.isArray(raw.bullets) ? raw.bullets : []).filter(isObj).map((b, i) => {
    // Lane B: one claim per bullet (`claim_id`). Lane E stub: `claim_ids[]`.
    const claimIds = typeof b.claim_id === "string" ? [b.claim_id] : strings(b.claim_ids);
    return { id: `b-${i + 1}`, text: str(b.text), claim_ids: claimIds, origin: b.manual_override === true ? "manual-edit" : "from-claims" };
  });
  const created = str(raw.created_at, clock);
  return {
    variant: {
      schema_version: RESUME_SCHEMA,
      id: str(raw.id),
      candidate_ref: str(raw.subject_id, subject),
      label: str(raw.label, isB ? "Lane B résumé variant" : "Pipeline résumé variant"),
      target_job_id: typeof raw.job_id === "string" ? raw.job_id : null,
      parent_variant_id: typeof raw.parent_id === "string" ? raw.parent_id : null,
      version: Number.isInteger(raw.version) && (raw.version as number) >= 1 ? raw.version : 1,
      created_at: created,
      updated_at: created,
      sections: [{ id: "sec-upstream", heading: "Highlights", bullets }],
      // An upstream variant is never treated as approved here: approval is the user's act in this UI.
      approval: null,
    },
    dialect: isB ? "careertwin.b.resume-variant.v1" : "stub-resume-variant",
    mock: MOCK_MARKER.test(producer),
    notes,
  };
}

export function resumeVariantsFromUpstream(raw: unknown, ctx: { clock: string; subject: string }): Translated<unknown> & { notes: string[] } {
  const list = Array.isArray(raw) ? raw : isObj(raw) ? [raw] : null;
  if (list === null) return { payload: raw, dialect: "unrecognised", mock: false, notes: [] };
  const out = list.map((v) => (isObj(v) ? variantFrom(v, ctx.clock, ctx.subject) : { variant: v, dialect: "unrecognised", mock: false, notes: [] as string[] }));
  return { payload: out.map((o) => o.variant), dialect: out[0]?.dialect ?? "empty", mock: out.some((o) => o.mock), notes: out.flatMap((o) => o.notes) };
}

// ------------------------------------------------------------------ assignments
export function assignmentsFromUpstream(raw: unknown): Translated<unknown> {
  if (!Array.isArray(raw)) return { payload: raw, dialect: "unrecognised", mock: false };
  let dialect = raw.length === 0 ? "empty" : "unrecognised";
  const payload = raw.map((a) => {
    if (!isObj(a)) return a;
    if (a.schema_version === ASSIGNMENT_SCHEMA) {
      dialect = ASSIGNMENT_SCHEMA;
      return a;
    }
    const id = str(a.assignment_id ?? a.id);
    const digest = str(a.design_hash ?? a.spec_digest);
    dialect = typeof a.assignment_id === "string" ? "lane-b-assignment" : "stub-assignment";
    return {
      schema_version: ASSIGNMENT_SCHEMA,
      id,
      experiment_id: str(a.experiment_id),
      unit_id: str(a.unit_id ?? a.job_id ?? a.application_id),
      unit_label: typeof a.unit_kind === "string" ? `${a.unit_kind}: ${str(a.unit_id ?? a.job_id ?? a.application_id)}` : str(a.unit_id),
      stratum: str(a.stratum),
      variant: str(a.variant),
      assigned_at: str(a.assigned_at),
      algorithm: "upstream (assignment is owned by the experiments lane)",
      seed_digest: digest,
      // Exposure is not part of either upstream shape; unknown, not "never exposed".
      exposure_logged_at: null,
    };
  });
  return { payload, dialect, mock: false };
}

// ------------------------------------------------------------------ analysis
const STUB_DECISIONS: Record<string, AnalysisVerdict> = {
  INCONCLUSIVE: "INCONCLUSIVE",
  EVIDENCE_FOR_DIFFERENCE: "EVIDENCE_FOR_DIFFERENCE",
  NOT_ANALYZABLE: "NOT_ANALYZABLE",
  INSUFFICIENT: "NOT_ANALYZABLE",
};

/**
 * Normalise an upstream analysis report. Returns null when no verdict can be
 * read: the UI then shows "analysis not understood" rather than guessing one.
 */
export function analysisFromUpstream(raw: unknown): (Translated<UpstreamAnalysis> & { reason?: undefined }) | { payload: null; dialect: string; mock: boolean; reason: string } {
  if (!isObj(raw)) return { payload: null, dialect: "unrecognised", mock: false, reason: "analysis is not an object" };
  const verdictRaw = str(raw.verdict ?? raw.decision).toUpperCase();
  const verdict = (ANALYSIS_VERDICTS as readonly string[]).includes(verdictRaw) ? (verdictRaw as AnalysisVerdict) : STUB_DECISIONS[verdictRaw];
  if (!verdict) return { payload: null, dialect: "unrecognised", mock: false, reason: `unknown verdict '${verdictRaw.slice(0, 40)}'` };
  const experimentId = str(raw.experiment_id);
  if (!experimentId) return { payload: null, dialect: "unrecognised", mock: false, reason: "analysis names no experiment" };
  // Lane E stub: `effect {contrast, estimate, ci_low, ci_high}`. Lane B: `comparisons[] {treatment, control, estimate, interval_95 {low, high, method}}`.
  const effectRaw = isObj(raw.effect) ? raw.effect : null;
  const comparison = Array.isArray(raw.comparisons) ? raw.comparisons.find(isObj) : undefined;
  const interval = comparison && isObj(comparison.interval_95) ? comparison.interval_95 : null;
  const e = effectRaw
    ? { contrast: str(effectRaw.contrast), estimate: num(effectRaw.estimate), ci_low: num(effectRaw.ci_low), ci_high: num(effectRaw.ci_high), ci_level: num(effectRaw.ci_level), method: str(effectRaw.method) }
    : comparison && interval
      ? { contrast: `${str(comparison.treatment)} − ${str(comparison.control)}`, estimate: num(comparison.estimate), ci_low: num(interval.low), ci_high: num(interval.high), ci_level: 0.95, method: str(interval.method) }
      : null;
  // Arms: a list (stub) or an object keyed by variant with counts under `flow.by_arm` (lane B).
  const flow = isObj(raw.flow) && isObj(raw.flow.by_arm) ? raw.flow.by_arm : {};
  const arms = Array.isArray(raw.arms)
    ? raw.arms.filter(isObj).map((a) => ({ variant: str(a.variant), n_assigned: num(a.n_assigned) ?? 0, n_success: num(a.n_success), n_censored: num(a.n_censored), n_missing: num(a.n_missing) }))
    : isObj(raw.arms)
      ? Object.entries(raw.arms).filter(([, a]) => isObj(a)).map(([variant, a]) => {
          const f = isObj(flow[variant]) ? (flow[variant] as Obj) : {};
          return { variant, n_assigned: num(f.units) ?? num((a as Obj).units) ?? 0, n_success: num(f.success), n_censored: num(f.censored), n_missing: num(f.missing) };
        })
      : [];
  const effect = e && e.estimate !== null && e.ci_low !== null && e.ci_high !== null && e.ci_low <= e.ci_high
    ? { contrast: e.contrast, estimate: e.estimate, ci_low: e.ci_low, ci_high: e.ci_high, ci_level: e.ci_level ?? 0.95, method: e.method }
    : null;
  const isB = typeof raw.verdict === "string";
  const producer = str(raw.schema ?? raw.generated_by ?? raw.schema_version, isB ? "careertwin.b.analysis-report.v1" : "pipeline analysis (producer not stated)");
  return {
    payload: {
      experiment_id: experimentId,
      verdict,
      estimand: typeof raw.estimand === "string" ? raw.estimand : null,
      arms,
      phase: typeof raw.phase === "string" ? raw.phase : null,
      effect,
      reasons_withheld: strings(raw.reasons_withheld),
      warnings: strings(raw.warnings),
      interpretation_limits: strings(raw.interpretation_limits),
      producer,
    },
    dialect: isB ? "careertwin.b.analysis-report.v1" : "stub-analysis",
    mock: MOCK_MARKER.test(producer),
  };
}

// ------------------------------------------------------------------ atlas (points + diagnostics files)
export function atlasFromArtifacts(pointsRaw: unknown, diagnosticsRaw: unknown, jobs: JobPosting[], generatedAt: string): Translated<unknown> {
  if (!Array.isArray(pointsRaw) || !isObj(diagnosticsRaw)) return { payload: null, dialect: "unrecognised", mock: false };
  const d = diagnosticsRaw;
  const jobById = new Map(jobs.map((j) => [j.id, j]));
  const evr = Array.isArray(d.explained_variance_ratio) ? d.explained_variance_ratio.map(num) : [];
  const k = num(d.k) ?? 0;
  const metric = (v: unknown) => {
    const direct = num(v);
    if (direct !== null && k >= 1) return { k, value: clamp01(direct) };
    if (isObj(v) && num(v.value) !== null && num(v.k) !== null) return { k: num(v.k) as number, value: clamp01(num(v.value) as number) };
    return null;
  };
  const points = pointsRaw as AtlasPoint[];
  const method = str(d.method, "pca");
  const stated = num(d.explained_variance_cumulative);
  const cumulative = stated !== null ? clamp01(stated) : evr.length >= 2 && evr[0] !== null && evr[1] !== null && evr[0] !== undefined && evr[1] !== undefined ? clamp01(evr[0] + evr[1]) : null;
  const note = str(d.note);
  const lost = num(d.information_lost);
  const lossStatement = isObj(d.information_loss) ? str(d.information_loss.statement) : "";
  const knn = num(d.knn_recall_mean);
  return {
    payload: {
      schema_version: PROJECTION_SCHEMA,
      projection_id: str(d.projection_id),
      method,
      generated_at: generatedAt,
      input: { representation: `pipeline vectors (preprocessing: ${str(d.preprocessing, "not stated")})`, n_points: Array.isArray(d.fit_refs) ? d.fit_refs.length : points.length, n_dims: Math.max(1, num(d.n_features) ?? num(d.n_dims) ?? 1) },
      axes: [0, 1].map((i) => ({ label: method === "pca" ? `PC${i + 1}` : `Axis ${i + 1}`, explained_variance_ratio: evr[i] === null || evr[i] === undefined ? null : clamp01(evr[i] as number), description: "Axis produced by the analytics lane. It has no intrinsic meaning." })),
      diagnostics: {
        cumulative_explained_variance: cumulative,
        trustworthiness: metric(d.trustworthiness),
        continuity: metric(d.continuity),
        ...(knn !== null && k >= 1 ? { knn_recall: { k, value: clamp01(knn) } } : {}),
        flags: strings(d.flags),
      },
      warnings: [
        "2-D positions are a visualisation. Distance on this map is not fit and not a hiring likelihood.",
        ...(note ? [note] : []),
        ...(lossStatement ? [lossStatement] : []),
        ...strings(d.warnings),
        ...(lost !== null ? [`The pipeline reports that ${Math.round(clamp01(lost) * 100)}% of the information is not shown on this map.`] : []),
      ],
      points,
      // The pipeline's point file has no entity table; role family is joined from the job feed by id.
      entities: points.filter(isObj).map((p) => {
        const id = str((p as unknown as Obj).entity_id);
        const job = jobById.get(id);
        return {
          entity_id: id,
          role_family: job?.role_family ?? null,
          evidence_basis: "not-applicable",
          job_id: job ? job.id : null,
          scenario_id: null,
          detail: job ? `${job.organization} · ${job.seniority}` : "No detail was provided for this point by the pipeline.",
          top_skills: job ? [...job.skills].sort().slice(0, 8) : [],
        };
      }),
      paths: [],
      unplaced: (Array.isArray(d.unplaced) ? d.unplaced : []).filter(isObj).map((u) => ({ entity_id: typeof u.entity_id === "string" ? u.entity_id : typeof u.ref === "string" ? u.ref : null, reason: str(u.reason, "not placed") })),
    },
    dialect: "atlas-points+diagnostics",
    mock: MOCK_MARKER.test(str(d.projection_id)) || MOCK_MARKER.test(str(d.generated_by)),
  };
}

// ------------------------------------------------------------------ lane A job facets (jobs_ext.json)
/** Read display facets from lane A's `{posting, ext}` envelopes. Unknown stays null. */
export function facetsFromJobsExt(raw: unknown): Record<string, JobFacet> {
  const list = isObj(raw) && Array.isArray(raw.items) ? raw.items : raw;
  const out: Record<string, JobFacet> = {};
  if (!Array.isArray(list)) return out;
  for (const rec of list) {
    if (!isObj(rec) || !isObj(rec.posting) || !isObj(rec.ext) || typeof rec.posting.id !== "string") continue;
    const ext = rec.ext;
    const dedup = isObj(ext.dedup) ? ext.dedup : {};
    const employer = isObj(ext.employer) ? ext.employer : {};
    out[rec.posting.id] = {
      duplicate_cluster: typeof dedup.cluster_id === "string" ? dedup.cluster_id : null,
      duplicate_members: strings(dedup.members),
      duplicate_of: typeof dedup.duplicate_of === "string" ? dedup.duplicate_of : null,
      security_flags: strings(ext.security_flags),
      synthetic: typeof ext.synthetic === "boolean" ? ext.synthetic : null,
      status_reason: typeof ext.status_reason === "string" ? ext.status_reason : null,
      employer_id: typeof employer.id === "string" ? employer.id : null,
    };
  }
  return out;
}

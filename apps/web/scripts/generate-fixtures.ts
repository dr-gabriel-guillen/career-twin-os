/**
 * Deterministic fixture generator (lane-D stand-in for lanes A/B/C).
 *
 *   node scripts/generate-fixtures.ts           # write src/fixtures/generated/*.json
 *   node scripts/generate-fixtures.ts --check   # fail if committed files differ
 *
 * Requires Node >= 22.18 (native TypeScript type stripping). No dependencies,
 * no network, no clock: every timestamp derives from FIXTURE_CLOCK.
 */
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { JobPosting, ProfileClaim } from "../src/contracts/types.ts";
import { assignVariant, ASSIGNMENT_ALGORITHM } from "../src/lib/assign.ts";
import { resumeContentDigest } from "../src/lib/resumeDigest.ts";
import { mulberry32 } from "../src/lib/prng.ts";
import { sha256Hex } from "../src/lib/sha256.ts";
import {
  CANDIDATE_REF,
  CLAIM_SEEDS,
  EVIDENCE_SEEDS,
  EXPERIMENT_SALT,
  FIXTURE_CLOCK,
  INVALID_JOB_RECORDS,
  JOB_SEEDS,
  LEADERSHIP_SCOPE,
  LICENSE_NOTE,
  PREFERENCES,
  ROLE_FAMILY_LABELS,
  SCENARIO_SEEDS,
} from "./standin/data.ts";
import {
  candidateSkills,
  interpretableCoords,
  matchExplanation,
  meanVector,
  neighbourhoodDiagnostics,
  oneHot,
  pca2,
  round,
  STANDIN_ENGINE,
} from "./standin/engine.ts";

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "fixtures", "generated");
const clock = Date.parse(FIXTURE_CLOCK);
const daysAgo = (d: number, hour = 9) =>
  new Date(clock - d * 86_400_000 + (hour - 12) * 3_600_000).toISOString().replace(".000Z", "Z");

// ------------------------------------------------------------------ jobs (JobPosting v0.1)
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const jobs: JobPosting[] = JOB_SEEDS.map((j) => {
  const job: JobPosting = {
    id: j.id,
    source: "synthetic",
    source_id: j.sourceId ?? `synthetic-${j.id}`,
    title: j.title,
    organization: j.org,
    url: j.url ?? `https://careers.example.invalid/${slug(j.org)}/${j.id}`,
    fetched_at: daysAgo(j.fetchedDaysAgo),
    status: j.status ?? "active",
    skills: j.skills,
    seniority: j.seniority,
    employment_type: j.type ?? "full-time",
    location: j.location ?? null,
    compensation: j.comp ?? null,
    license_note: LICENSE_NOTE,
    role_family: j.family,
    requirements: j.requirements ?? [`Synthetic requirement text for the fictional role '${j.id}'.`],
    source_evidence: j.evidence ?? [`Authored fixture record ${j.id}; no external source.`],
  };
  if (j.remote !== undefined) job.remote_policy = j.remote;
  return job;
});
// Feed order interleaves malformed records so adapters cannot rely on position.
const jobFeed: unknown[] = [...jobs.slice(0, 6), INVALID_JOB_RECORDS[0], ...jobs.slice(6, 15), INVALID_JOB_RECORDS[1], ...jobs.slice(15), INVALID_JOB_RECORDS[2]];

// ------------------------------------------------------------------ claims / evidence / preferences
const claims: ProfileClaim[] = CLAIM_SEEDS.map((c, i) => {
  const claim: ProfileClaim = {
    id: c.id,
    subject_id: CANDIDATE_REF,
    claim_type: c.claim_type,
    claim_text: c.claim_text,
    status: c.status,
    evidence_ids: c.evidence_ids,
    visibility: c.visibility ?? "shareable",
    updated_at: daysAgo(c.updatedDaysAgo ?? 14 + i),
    skills: c.skills ?? [],
  };
  if (c.valid_to !== undefined) claim.valid_to = c.valid_to;
  return claim;
});
const evidence = EVIDENCE_SEEDS.map((e) => ({
  schema_version: "evidence-item/0.1-proposed",
  id: e.id,
  kind: e.kind,
  label: e.label,
  captured_at: daysAgo(e.daysAgo),
  note: e.note,
}));
const preferences = {
  schema_version: "candidate-preferences/0.1-proposed",
  candidate_ref: CANDIDATE_REF,
  display_name: "Synthetic Candidate",
  note: "Fictional. No real user attributes. Mirrors fixtures/synthetic_profile.json on main.",
  ...PREFERENCES,
};

// ------------------------------------------------------------------ matches (stand-in for lane A)
const families = [...new Set(jobs.map((j) => j.role_family!))].sort();
const targetFamilies = ["fde", "ai-architect", "field-cto"];
const targetSkillUnion = new Set(jobs.filter((j) => targetFamilies.includes(j.role_family!)).flatMap((j) => j.skills));
const matches = jobs.map((j) => matchExplanation(j, claims, PREFERENCES, targetSkillUnion, CANDIDATE_REF, FIXTURE_CLOCK));

// ------------------------------------------------------------------ scenarios (stand-in for lane C)
const have = new Set(candidateSkills(claims));
const familySkills = (family: string) => {
  const fam = jobs.filter((j) => j.role_family === family);
  const counts = new Map<string, number>();
  for (const j of fam) for (const s of j.skills) counts.set(s, (counts.get(s) ?? 0) + 1);
  return [...counts].filter(([, c]) => c >= fam.length / 2).map(([s]) => s).sort();
};
const scenarios = SCENARIO_SEEDS.map((s) => {
  const target = familySkills(s.family);
  const planned = new Set(s.actions.map((a) => a.skill));
  const before = target.filter((t) => have.has(t)).length / target.length;
  const after = target.filter((t) => have.has(t) || planned.has(t)).length / target.length;
  return {
    id: s.id,
    candidate_ref: CANDIDATE_REF,
    target_role: s.target_role,
    planned_actions: s.actions,
    time_horizon_months: s.horizon,
    status: "hypothetical",
    assumptions: s.assumptions,
    outputs: {
      schema_version: "scenario-outputs/0.1-proposed",
      target_role_family: s.family,
      target_skills: target,
      coverage_before: round(before, 3),
      coverage_after: round(after, 3),
      leadership_exposure: s.leadership,
      uncertainty: s.uncertainty,
      uncertainty_note: s.uncertainty_note,
      atlas_entity_id: `${s.id}#point`,
    },
    risks: s.risks,
  };
});

// ------------------------------------------------------------------ atlas (stand-in for lane C)
const vocabulary = [...new Set([...jobs.flatMap((j) => j.skills), ...claims.flatMap((c) => c.skills ?? []), ...SCENARIO_SEEDS.flatMap((s) => s.actions.map((a) => a.skill))])].sort();
const candidateScope = LEADERSHIP_SCOPE[PREFERENCES.seniority] ?? 0;

interface Row {
  entity_id: string;
  label: string;
  type: "candidate" | "job" | "role-centroid" | "scenario";
  vector: number[];
  scope: number;
  entity: { role_family: string | null; evidence_basis: string; job_id: string | null; scenario_id: string | null; detail: string; top_skills: string[] };
  warnings: string[];
}
const rows: Row[] = [];
for (const j of jobs) {
  rows.push({ entity_id: j.id, label: j.title, type: "job", vector: oneHot(vocabulary, j.skills), scope: LEADERSHIP_SCOPE[j.seniority] ?? 0,
    entity: { role_family: j.role_family ?? null, evidence_basis: "not-applicable", job_id: j.id, scenario_id: null,
      detail: `${j.organization} · ${j.seniority}`, top_skills: [...j.skills].sort() }, warnings: [] });
}
for (const f of families) {
  const fam = jobs.filter((j) => j.role_family === f);
  const vector = meanVector(fam.map((j) => oneHot(vocabulary, j.skills)));
  rows.push({ entity_id: `centroid-${f}`, label: `${ROLE_FAMILY_LABELS[f] ?? f} (centroid)`, type: "role-centroid", vector,
    scope: fam.reduce((s, j) => s + (LEADERSHIP_SCOPE[j.seniority] ?? 0), 0) / fam.length,
    entity: { role_family: f, evidence_basis: "not-applicable", job_id: null, scenario_id: null,
      detail: `Mean skill vector of ${fam.length} synthetic posting(s).`, top_skills: familySkills(f) },
    warnings: fam.length < 3 ? [`Centroid of only ${fam.length} posting(s); unstable.`] : [] });
}
rows.push({ entity_id: CANDIDATE_REF, label: "Synthetic Candidate (all non-contradicted claims)", type: "candidate",
  vector: oneHot(vocabulary, candidateSkills(claims)), scope: candidateScope,
  entity: { role_family: null, evidence_basis: "mixed", job_id: null, scenario_id: null,
    detail: "Skills from supported, self-reported and unverified claims.", top_skills: candidateSkills(claims) },
  warnings: ["Includes self-reported and unverified claims."] });
rows.push({ entity_id: `${CANDIDATE_REF}#supported-only`, label: "Synthetic Candidate (supported claims only)", type: "candidate",
  vector: oneHot(vocabulary, candidateSkills(claims, true)), scope: candidateScope,
  entity: { role_family: null, evidence_basis: "supported", job_id: null, scenario_id: null,
    detail: "Skills from claims with supporting evidence only.", top_skills: candidateSkills(claims, true) }, warnings: [] });
for (const s of SCENARIO_SEEDS) {
  const skills = [...new Set([...candidateSkills(claims), ...s.actions.map((a) => a.skill)])].sort();
  rows.push({ entity_id: `${s.id}#point`, label: `Hypothetical: after '${s.target_role}' plan`, type: "scenario",
    vector: oneHot(vocabulary, skills), scope: candidateScope,
    entity: { role_family: s.family, evidence_basis: "not-applicable", job_id: null, scenario_id: s.id,
      detail: "Hypothetical skill set if every planned action were completed and evidenced.", top_skills: skills },
    warnings: ["Hypothetical point. Not evidence and not a forecast."] });
}

const K = 5;
const paths = SCENARIO_SEEDS.map((s) => ({ id: `path-${s.id}`, from_entity_id: CANDIDATE_REF, to_entity_id: `${s.id}#point`, scenario_id: s.id, label: `Plan: ${s.target_role}` }));
const entities = rows.map((r) => ({ entity_id: r.entity_id, ...r.entity }));
const high = rows.map((r) => r.vector);

function projection(id: string, method: "pca" | "interpretable-axes", coords: number[][], axes: unknown[], cumulative: number | null, extraWarnings: string[]) {
  const diag = neighbourhoodDiagnostics(high, coords, K);
  return {
    schema_version: "atlas-projection/0.1-proposed",
    projection_id: id,
    method,
    generated_at: FIXTURE_CLOCK,
    input: { representation: "skill-one-hot (role centroids are means)", n_points: rows.length, n_dims: vocabulary.length },
    axes,
    diagnostics: {
      cumulative_explained_variance: cumulative,
      trustworthiness: { k: K, value: diag.trustworthiness },
      continuity: { k: K, value: diag.continuity },
    },
    warnings: [
      "2-D positions are a visualisation. Distance on this map is not fit, not similarity you can rely on, and not a hiring likelihood.",
      "One-hot skill vectors produce many tied distances; neighbour ranks break ties by record order.",
      ...extraWarnings,
    ],
    points: rows.map((r, i) => ({
      entity_id: r.entity_id,
      projection_id: id,
      label: r.label,
      coords: coords[i]!,
      source_vector_ref: `skill-onehot-v1#${r.entity_id}`,
      representation_type: r.type,
      projection_method: method,
      neighborhood_quality: diag.overlap[i]!,
      fit_metric_ref: r.type === "job" ? `match-explanation/0.1-proposed#${r.entity_id}` : null,
      warnings: r.warnings,
    })),
    entities,
    paths,
  };
}

const p = pca2(high);
const atlasPca = projection("pca-skill-onehot-v1", "pca", p.coords,
  [
    { label: "PC1", explained_variance_ratio: p.ratios[0], description: "First principal component of the skill vectors. It has no intrinsic meaning." },
    { label: "PC2", explained_variance_ratio: p.ratios[1], description: "Second principal component of the skill vectors. It has no intrinsic meaning." },
  ],
  p.cumulative,
  [`The two axes keep ${Math.round(p.cumulative * 100)}% of the variance; the rest is not visible here.`]);
const atlasAxes = projection("interpretable-axes-v1", "interpretable-axes",
  rows.map((r) => interpretableCoords(vocabulary, r.vector, r.scope)),
  [
    { label: "Technical depth ↔ customer-facing", explained_variance_ratio: null, description: "Share of listed skills tagged customer-facing, rescaled to -1 (all technical) .. +1 (all customer-facing). Tags are authored." },
    { label: "Individual scope ↔ leadership scope", explained_variance_ratio: null, description: "Half seniority-implied scope, half share of leadership-tagged skills (0..1). Tags are authored." },
  ],
  null,
  ["Axes are hand-defined from authored skill tags; they are easier to read but discard most of the skill information."]);

// ------------------------------------------------------------------ experiments (stand-in for lane B)
const EXP_ID = "exp-synth-resume-framing";
const seedDigest = sha256Hex(EXPERIMENT_SALT);
const experiments = [
  {
    id: EXP_ID,
    title: "Résumé framing: impact-first vs skills-first (synthetic)",
    hypothesis: "Leading with a quantified achievement instead of a skills summary changes the share of applications that reach a recruiter screen within 21 days.",
    status: "running",
    variants: ["impact-first", "skills-first"],
    assignment_unit: "employer-cluster",
    strata: ["role_family"],
    primary_metric: "recruiter_screen_within_window",
    design_locked_at: daysAgo(35),
    stopping_rule: "Stop when 40 employer clusters have a closed outcome window. No interim decisions.",
    consent_required: true,
    outcome_window_days: 21,
    secondary_metrics: ["any_reply_within_window"],
    analysis_plan: "Difference in proportions with a 95% Newcombe interval on the primary metric. Censored and missing units are reported and excluded from rates. Secondary metrics are descriptive only.",
    seed_digest: seedDigest,
  },
  {
    id: "exp-synth-cover-note",
    title: "Short cover note: with vs without (draft, synthetic)",
    hypothesis: "Adding a three-sentence cover note changes the recruiter-screen rate.",
    status: "draft",
    variants: ["with-note", "without-note"],
    assignment_unit: "application",
    strata: [],
    primary_metric: "recruiter_screen_within_window",
    design_locked_at: null,
    stopping_rule: "Not set yet.",
    consent_required: true,
    outcome_window_days: 21,
  },
];
const experimentJobs = jobs.filter((j) => !j.id.startsWith("fake-hostile"));
const clusters = [...new Map(experimentJobs.map((j) => [j.organization, j.role_family!])).entries()];
const assignments = clusters.map(([org, family], i) => ({
  schema_version: "experiment-assignment/0.1-proposed",
  id: `asg-synth-${String(i + 1).padStart(3, "0")}`,
  experiment_id: EXP_ID,
  unit_id: `employer-${slug(org)}`,
  unit_label: org,
  stratum: family,
  variant: assignVariant(EXP_ID, family, `employer-${slug(org)}`, ["impact-first", "skills-first"], EXPERIMENT_SALT),
  assigned_at: daysAgo(34 - i),
  algorithm: ASSIGNMENT_ALGORITHM,
  seed_digest: seedDigest,
  exposure_logged_at: i < 12 ? daysAgo(33 - i) : null,
}));
// Disclosed generating process: both variants share the SAME true rate (no real effect).
const SYNTHETIC_TRUE_RATE = 0.25;
const rng = mulberry32(20261110);
const outcomes = assignments.map((a, i) => {
  const draw = rng() < SYNTHETIC_TRUE_RATE;
  const status = i < 9 ? "observed" : i < 12 ? "censored" : "missing";
  return {
    schema_version: "experiment-outcome/0.1-proposed",
    assignment_id: a.id,
    experiment_id: EXP_ID,
    metric: "recruiter_screen_within_window",
    status,
    value: status === "observed" ? draw : null,
    recorded_at: daysAgo(Math.max(0, 12 - i)),
    data_origin: "synthetic-simulation",
  };
});

// ------------------------------------------------------------------ résumé variants (stand-in for lane B)
const claimById = new Map(claims.map((c) => [c.id, c]));
const bullet = (id: string, claimIds: string[], text?: string) => ({
  id,
  text: text ?? claimIds.map((c) => claimById.get(c)!.claim_text).join("; "),
  claim_ids: claimIds,
  origin: "from-claims",
});
const baseSections = [
  { id: "sec-experience", heading: "Experience", bullets: [
    bullet("b-emp", ["claim-employment-halcyon"]),
    bullet("b-latency", ["claim-achievement-latency"]),
    bullet("b-evals", ["claim-project-evals"]),
  ] },
  { id: "sec-skills", heading: "Skills", bullets: [
    bullet("b-skill-python", ["claim-skill-python"]),
    bullet("b-skill-ts", ["claim-skill-typescript"]),
    bullet("b-skill-api", ["claim-skill-api"]),
    bullet("b-skill-aieval", ["claim-skill-aieval"]),
    bullet("b-skill-aws", ["claim-skill-aws"]),
  ] },
  { id: "sec-education", heading: "Education", bullets: [bullet("b-edu", ["claim-education-bsc"])] },
];
const aiSections = [
  { id: "sec-experience", heading: "Experience", bullets: [
    bullet("b-evals", ["claim-project-evals"]),
    bullet("b-emp", ["claim-employment-halcyon"]),
    bullet("b-latency", ["claim-achievement-latency"]),
  ] },
  { id: "sec-skills", heading: "Skills", bullets: [
    bullet("b-skill-aieval", ["claim-skill-aieval"]),
    bullet("b-skill-python", ["claim-skill-python"]),
    bullet("b-skill-api", ["claim-skill-api"]),
  ] },
  { id: "sec-education", heading: "Education", bullets: [bullet("b-edu", ["claim-education-bsc"])] },
];
const resumeVariants = [
  { schema_version: "resume-variant/0.1-proposed", id: "rv-base", candidate_ref: CANDIDATE_REF, label: "Base résumé (from claims)",
    target_job_id: null, parent_variant_id: null, version: 1, created_at: daysAgo(40), updated_at: daysAgo(40), sections: baseSections, approval: null },
  { schema_version: "resume-variant/0.1-proposed", id: "rv-ai-architect", candidate_ref: CANDIDATE_REF, label: "AI architect emphasis",
    target_job_id: "fake-ai-9", parent_variant_id: "rv-base", version: 1, created_at: daysAgo(19), updated_at: daysAgo(18), sections: aiSections,
    approval: { approved_at: daysAgo(17), approval_token_ref: "approval-synth-001", content_digest: resumeContentDigest(aiSections) } },
];

// ------------------------------------------------------------------ application events (ApplicationEvent v0.1)
let evtN = 0;
type Ev = [type: string, daysAgo: number, extra?: Record<string, unknown>];
const application = (applicationId: string, jobId: string, variant: string | null, events: Ev[]) =>
  events.map(([event_type, d, extra]) => ({
    id: `evt-synth-${String(++evtN).padStart(4, "0")}`,
    application_id: applicationId,
    job_id: jobId,
    resume_variant_id: ["saved", "note"].includes(event_type) ? null : variant,
    event_type,
    recorded_at: daysAgo(d, 10),
    occurred_at: daysAgo(d),
    actor: "import",
    source: "synthetic-fixture",
    outcome_status: ["saved", "drafted", "approved", "note"].includes(event_type) ? "observed" : "self-reported",
    experiment_id: null,
    approval_token_ref: null,
    notes: "Synthetic event.",
    ...extra,
  }));
const applicationEvents = [
  ...application("app-synth-001", "fake-ai-9", "rv-ai-architect", [
    ["saved", 20], ["drafted", 18], ["approved", 17, { approval_token_ref: "approval-synth-001" }],
    ["submitted", 17, { approval_token_ref: "approval-synth-001", notes: "Synthetic: user recorded a manual submission." }],
    ["recruiter_screen", 9], ["interview", 3],
  ]),
  ...application("app-synth-002", "fake-se-5", "rv-base", [
    ["saved", 30], ["drafted", 29], ["approved", 28, { approval_token_ref: "approval-synth-002" }],
    ["submitted", 28, { approval_token_ref: "approval-synth-002", notes: "Synthetic: no reply has been recorded since." }],
  ]),
  ...application("app-synth-003", "fake-backend-12", "rv-base", [
    ["saved", 26], ["drafted", 25], ["approved", 25, { approval_token_ref: "approval-synth-003" }],
    ["submitted", 24, { approval_token_ref: "approval-synth-003" }], ["rejected", 6, { outcome_status: "observed" }],
  ]),
  ...application("app-synth-004", "fake-em-17", null, [["saved", 4], ["note", 3, { notes: "Synthetic note: revisit after the roadmap evidence exists." }]]),
  ...application("app-synth-005", "fake-fde-1", "rv-base", [["saved", 2], ["drafted", 1, { experiment_id: EXP_ID }]]),
  ...application("app-synth-006", "fake-cto-11", "rv-base", [
    ["saved", 22], ["drafted", 21], ["approved", 21, { approval_token_ref: "approval-synth-006" }],
    ["submitted", 20, { approval_token_ref: "approval-synth-006" }], ["recruiter_screen", 12], ["withdrawn", 8, { outcome_status: "observed" }],
  ]),
];

// ------------------------------------------------------------------ write / check
const files: Record<string, unknown> = {
  "jobs.feed.json": jobFeed,
  "claims.json": claims,
  "evidence.json": evidence,
  "preferences.json": preferences,
  "matches.json": matches,
  "scenarios.json": scenarios,
  "atlas.pca.json": atlasPca,
  "atlas.axes.json": atlasAxes,
  "experiments.json": experiments,
  "assignments.json": assignments,
  "outcomes.json": outcomes,
  "resume-variants.json": resumeVariants,
  "application-events.json": applicationEvents,
};
const serialise = (v: unknown) => JSON.stringify(v, null, 2) + "\n";
const rendered: Record<string, string> = {};
for (const [name, value] of Object.entries(files)) rendered[name] = serialise(value);
rendered["manifest.json"] = serialise({
  schema_version: "fixture-manifest/1",
  fixture_clock: FIXTURE_CLOCK,
  generator: { ...STANDIN_ENGINE, script: "apps/web/scripts/generate-fixtures.ts" },
  data_origin: "synthetic",
  notice: "All employers, postings, people, events and outcomes are fictional. Stand-in for lanes A/B/C until their payloads exist.",
  role_family_labels: ROLE_FAMILY_LABELS,
  experiment: { synthetic_true_rate_all_variants: SYNTHETIC_TRUE_RATE, prng: "mulberry32", seed: 20261110, salt_digest: seedDigest },
  skill_vocabulary: vocabulary,
  sha256: Object.fromEntries(Object.entries(rendered).map(([name, text]) => [name, sha256Hex(text)])),
});

if (process.argv.includes("--check")) {
  const stale = Object.entries(rendered).filter(([name, text]) => {
    const file = join(OUT_DIR, name);
    return !existsSync(file) || readFileSync(file, "utf8").replace(/\r\n/g, "\n") !== text;
  });
  if (stale.length) {
    console.error(`Fixtures are stale: ${stale.map(([n]) => n).join(", ")}. Run: npm run fixtures`);
    process.exit(1);
  }
  console.log(`fixtures:check OK (${Object.keys(rendered).length} files match the generator)`);
} else {
  mkdirSync(OUT_DIR, { recursive: true });
  for (const [name, text] of Object.entries(rendered)) writeFileSync(join(OUT_DIR, name), text, "utf8");
  console.log(`Wrote ${Object.keys(rendered).length} fixture files to src/fixtures/generated (${jobs.length} valid jobs, ${INVALID_JOB_RECORDS.length} malformed, ${claims.length} claims, ${rows.length} atlas points).`);
}

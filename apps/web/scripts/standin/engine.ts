/**
 * LANE-D FIXTURE STAND-IN for the engines owned by lanes A (matching) and C (atlas).
 *
 * Dev-time only. It exists so the UI has realistic, internally consistent payloads
 * to render before A/B/C publish theirs. It is NOT bundled into the app, and the UI
 * never recomputes any of this. When upstream payloads exist, delete the stand-in
 * and point the adapters at the real files.
 *
 * The skill-coverage and workstyle terms intentionally equal the frozen reference
 * engine on main (tools/reference_engine.py) — asserted by golden-vector tests.
 */
import type { ClaimStatus, JobPosting, ProfileClaim } from "../../src/contracts/types.ts";
import { profileDigest } from "../../src/lib/profileDigest.ts";
import {
  CUSTOMER_FACING_SKILLS,
  LEADERSHIP_SCOPE,
  LEADERSHIP_SKILLS,
  SENIORITY_ORDER,
  TRAJECTORY_UTILITY,
} from "./data.ts";

export const STANDIN_ENGINE = { name: "lane-d-fixture-standin", version: "0.1.0" };

export const round = (x: number, digits = 4) => {
  const f = 10 ** digits;
  const r = Math.round(x * f) / f;
  return Object.is(r, -0) ? 0 : r;
};

// ---------------------------------------------------------------- evidence
const STRENGTH_RANK: Record<ClaimStatus, number> = { supported: 3, "self-reported": 2, unverified: 1, contradicted: 0 };
export const EVIDENCE_WEIGHT: Record<string, number> = {
  supported: 1,
  "self-reported": 0.6,
  unverified: 0.3,
  contradicted: 0,
  missing: 0,
};

export interface SkillEvidence {
  strength: ClaimStatus | "missing";
  claim_ids: string[];
}

export function skillEvidence(claims: ProfileClaim[], skill: string): SkillEvidence {
  const relevant = claims.filter((c) => (c.skills ?? []).includes(skill));
  if (relevant.length === 0) return { strength: "missing", claim_ids: [] };
  let best: ClaimStatus = "contradicted";
  for (const c of relevant) if (STRENGTH_RANK[c.status] > STRENGTH_RANK[best]) best = c.status;
  return { strength: best, claim_ids: relevant.map((c) => c.id).sort() };
}

/** Skills the candidate may count: any claim that is not contradicted. */
export function candidateSkills(claims: ProfileClaim[], onlySupported = false): string[] {
  const out = new Set<string>();
  for (const c of claims) {
    if (onlySupported ? c.status !== "supported" : c.status === "contradicted") continue;
    for (const s of c.skills ?? []) out.add(s);
  }
  return [...out].sort();
}

// ---------------------------------------------------------------- matching
interface Component {
  key: string;
  label: string;
  value: number | null;
  weight: number;
  status: "known" | "unknown";
  note: string;
}

function group(components: Component[]) {
  const known = components.filter((c) => c.status === "known" && c.value !== null);
  const total = known.reduce((s, c) => s + c.weight, 0);
  if (known.length === 0 || total === 0) {
    return { score: null, components: components.map((c) => ({ ...c, weight: round(c.weight, 4) })) };
  }
  // Re-normalise weights over KNOWN components; unknown inputs are excluded, never zero-filled.
  const normalised = components.map((c) =>
    c.status === "known" && c.value !== null ? { ...c, weight: round(c.weight / total, 4) } : { ...c, weight: 0 },
  );
  const score = known.reduce((s, c) => s + (c.weight / total) * (c.value as number), 0);
  return { score: round(100 * score, 1), components: normalised };
}

export function matchExplanation(
  job: JobPosting,
  claims: ProfileClaim[],
  prefs: { remote: string; seniority: string },
  targetSkillUnion: Set<string>,
  candidateRef: string,
  computedAt: string,
) {
  const have = new Set(candidateSkills(claims));
  const jobSkills = [...new Set(job.skills.map((s) => s.toLowerCase().trim()))];
  const skills = jobSkills
    .map((skill) => {
      const ev = skillEvidence(claims, skill);
      return { skill, evidence_strength: ev.strength, claim_ids: ev.claim_ids };
    })
    .sort((a, b) => a.skill.localeCompare(b.skill));

  const n = jobSkills.length;
  const rawCoverage = n ? jobSkills.filter((s) => have.has(s)).length / n : null;
  const weighted = n ? skills.reduce((s, x) => s + (EVIDENCE_WEIGHT[x.evidence_strength] ?? 0), 0) / n : null;

  const remote = job.remote_policy ?? "unknown";
  // Same rule as reference_engine.score_job; UNKNOWN stays unknown instead of 0.1.
  const workstyle = remote === "unknown" ? null : remote === prefs.remote ? 1.0 : remote === "hybrid" ? 0.5 : 0.1;

  const ci = SENIORITY_ORDER.indexOf(prefs.seniority);
  const ji = SENIORITY_ORDER.indexOf(job.seniority);
  const seniority = ci < 0 || ji < 0 ? null : [1, 0.7, 0.5][Math.abs(ci - ji)] ?? 0.3;

  const near = group([
    { key: "evidence_weighted_skill_coverage", label: "Skill coverage weighted by evidence strength", value: weighted === null ? null : round(weighted),
      weight: 0.55, status: weighted === null ? "unknown" : "known",
      note: "Supported = 1, self-reported = 0.6, unverified = 0.3, contradicted or missing = 0." },
    { key: "skill_coverage", label: "Raw skill coverage (any non-contradicted claim)", value: rawCoverage === null ? null : round(rawCoverage, 3),
      weight: 0.15, status: rawCoverage === null ? "unknown" : "known",
      note: "Same definition as the frozen reference engine on main." },
    { key: "seniority_alignment", label: "Seniority alignment", value: seniority, weight: 0.15,
      status: seniority === null ? "unknown" : "known",
      note: seniority === null ? "Seniority label not recognised; excluded." : `Posting: ${job.seniority}; candidate preference: ${prefs.seniority}.` },
    { key: "workstyle", label: "Work-style match", value: workstyle, weight: 0.15,
      status: workstyle === null ? "unknown" : "known",
      note: workstyle === null ? "Remote policy unknown; excluded rather than assumed." : `Posting: ${remote}; preference: ${prefs.remote}.` },
  ]);

  const family = job.role_family ?? "";
  const trajectory = family in TRAJECTORY_UTILITY ? TRAJECTORY_UTILITY[family]! : null;
  const missing = jobSkills.filter((s) => !have.has(s));
  const exposure = n ? missing.filter((s) => targetSkillUnion.has(s)).length / n : null;
  const scope = job.seniority in LEADERSHIP_SCOPE ? LEADERSHIP_SCOPE[job.seniority]! : null;
  const comp = job.compensation ?? null;
  const compVerified = comp !== null && comp.verified === true;

  const strategicComponents: Component[] = [
    { key: "trajectory_utility", label: "Trajectory utility (user-stated)", value: trajectory, weight: 0.6,
      status: trajectory === null ? "unknown" : "known",
      note: trajectory === null ? "No user-stated utility for this role family." : "A stated preference for this role family, not a prediction." },
    { key: "target_skill_exposure", label: "Exposure to skills needed for target roles", value: exposure === null ? null : round(exposure, 3),
      weight: 0.25, status: exposure === null ? "unknown" : "known",
      note: "Share of this posting's skills that are missing today and appear in target-role postings." },
    { key: "leadership_scope", label: "Leadership scope implied by level", value: scope, weight: 0.15,
      status: scope === null ? "unknown" : "known", note: `Derived from the seniority label '${job.seniority}'.` },
  ];
  if (!compVerified) {
    strategicComponents.push({ key: "compensation", label: "Compensation", value: null, weight: 0, status: "unknown",
      note: comp === null ? "Not disclosed. Excluded from the score; never treated as zero."
        : "Disclosed but unverified or incomplete. Excluded from the score." });
  }
  const strategic = group(strategicComponents);

  const hard = [
    { key: "work_style", label: "Work style",
      status: remote === "unknown" ? "unknown" : remote === prefs.remote ? "pass" : remote === "hybrid" ? "unknown" : "fail",
      detail: remote === "unknown" ? "Remote policy is not stated." : remote === prefs.remote ? `Posting is ${remote}, as preferred.`
        : remote === "hybrid" ? "Hybrid: only you can decide whether that is acceptable." : `Posting is ${remote}; preference is ${prefs.remote}.` },
    { key: "posting_status", label: "Posting status at capture",
      status: job.status === "active" ? "pass" : job.status === "expired" ? "fail" : "unknown",
      detail: job.status === "active" ? "Marked active when captured. Not re-verified; this is a synthetic record."
        : job.status === "expired" ? "Marked expired when captured." : "Status unknown at capture." },
    { key: "employment_type", label: "Employment type",
      status: job.employment_type === "full-time" ? "pass" : "fail",
      detail: job.employment_type === "full-time" ? "Full-time, as preferred." : `Posting is ${job.employment_type}; preference is full-time.` },
  ];

  const unknown = ["recruiter_preferences", "job_activity_status"];
  if (!compVerified) unknown.unshift("compensation_verified");
  if (remote === "unknown") unknown.push("remote_policy");

  const warnings: string[] = [];
  if (n < 3) warnings.push("Fewer than three skills listed; coverage is unstable.");
  if (skills.some((s) => s.evidence_strength === "contradicted")) warnings.push("A required skill is backed only by a contradicted claim.");

  return {
    schema_version: "match-explanation/0.1-proposed",
    job_id: job.id,
    candidate_ref: candidateRef,
    computed_at: computedAt,
    engine: STANDIN_ENGINE,
    score_type: "relative_heuristic_not_hiring_probability",
    near_term_fit: near,
    strategic_value: strategic,
    hard_constraints: hard,
    skills,
    unknown_inputs: unknown,
    warnings,
    profile_digest: profileDigest(claims),
  };
}

// ---------------------------------------------------------------- vectors
export function oneHot(vocabulary: string[], skills: Iterable<string>): number[] {
  const set = new Set(skills);
  return vocabulary.map((s) => (set.has(s) ? 1 : 0));
}

export function meanVector(rows: number[][]): number[] {
  const d = rows[0]!.length;
  const out = new Array<number>(d).fill(0);
  for (const r of rows) for (let j = 0; j < d; j++) out[j]! += r[j]! / rows.length;
  return out;
}

/** Symmetric eigen-decomposition by cyclic Jacobi rotations. Deterministic (uses only + - * / sqrt). */
export function jacobiEigen(matrix: number[][]): { values: number[]; vectors: number[][] } {
  const n = matrix.length;
  const a = matrix.map((r) => [...r]);
  const v: number[][] = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));
  for (let sweep = 0; sweep < 100; sweep++) {
    let off = 0;
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) off += a[p]![q]! ** 2;
    if (off < 1e-22) break;
    for (let p = 0; p < n; p++) {
      for (let q = p + 1; q < n; q++) {
        const apq = a[p]![q]!;
        if (Math.abs(apq) < 1e-300) continue;
        const theta = (a[q]![q]! - a[p]![p]!) / (2 * apq);
        const t = (theta >= 0 ? 1 : -1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1);
        const s = t * c;
        for (let k = 0; k < n; k++) {
          const akp = a[k]![p]!;
          const akq = a[k]![q]!;
          a[k]![p] = c * akp - s * akq;
          a[k]![q] = s * akp + c * akq;
        }
        for (let k = 0; k < n; k++) {
          const apk = a[p]![k]!;
          const aqk = a[q]![k]!;
          a[p]![k] = c * apk - s * aqk;
          a[q]![k] = s * apk + c * aqk;
        }
        for (let k = 0; k < n; k++) {
          const vkp = v[k]![p]!;
          const vkq = v[k]![q]!;
          v[k]![p] = c * vkp - s * vkq;
          v[k]![q] = s * vkp + c * vkq;
        }
      }
    }
  }
  const order = Array.from({ length: n }, (_, i) => i).sort((i, j) => a[j]![j]! - a[i]![i]! || i - j);
  return {
    values: order.map((i) => a[i]![i]!),
    vectors: order.map((i) => v.map((row) => row[i]!)),
  };
}

export function pca2(rows: number[][]): { coords: number[][]; ratios: [number, number]; cumulative: number } {
  const n = rows.length;
  const d = rows[0]!.length;
  const mean = meanVector(rows);
  const centred = rows.map((r) => r.map((x, j) => x - mean[j]!));
  const cov = Array.from({ length: d }, () => new Array<number>(d).fill(0));
  for (const r of centred) for (let i = 0; i < d; i++) for (let j = 0; j < d; j++) cov[i]![j]! += (r[i]! * r[j]!) / (n - 1);
  const { values, vectors } = jacobiEigen(cov);
  const total = values.reduce((s, x) => s + Math.max(0, x), 0);
  const axes = [vectors[0]!, vectors[1]!].map((vec) => {
    // Sign convention: the largest-magnitude loading is positive.
    let big = 0;
    for (let i = 1; i < vec.length; i++) if (Math.abs(vec[i]!) > Math.abs(vec[big]!) + 1e-12) big = i;
    return vec[big]! < 0 ? vec.map((x) => -x) : vec;
  });
  const coords = centred.map((r) => axes.map((ax) => round(r.reduce((s, x, j) => s + x * ax[j]!, 0))));
  const r1 = values[0]! / total;
  const r2 = values[1]! / total;
  return { coords, ratios: [round(r1), round(r2)], cumulative: round(r1 + r2) };
}

// ---------------------------------------------------------------- neighbourhood diagnostics
function dist(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += (a[i]! - b[i]!) ** 2;
  return Math.sqrt(s);
}

/** rank[i][j] = 1-based rank of j among neighbours of i (ties broken by index). */
function ranks(points: number[][]): number[][] {
  return points.map((p, i) => {
    const order = points
      .map((q, j) => ({ j, d: dist(p, q) }))
      .filter((x) => x.j !== i)
      .sort((x, y) => x.d - y.d || x.j - y.j);
    const r = new Array<number>(points.length).fill(0);
    order.forEach((x, idx) => (r[x.j] = idx + 1));
    return r;
  });
}

/**
 * Trustworthiness and continuity (Venna & Kaski 2001) plus per-point k-NN overlap.
 * Trustworthiness penalises points that are close in 2-D but were far in the
 * original space; continuity penalises the opposite.
 */
export function neighbourhoodDiagnostics(high: number[][], low: number[][], k: number) {
  const n = high.length;
  const rh = ranks(high);
  const rl = ranks(low);
  const norm = 2 / (n * k * (2 * n - 3 * k - 1));
  let tSum = 0;
  let cSum = 0;
  const overlap: number[] = [];
  for (let i = 0; i < n; i++) {
    let shared = 0;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const inHigh = rh[i]![j]! <= k;
      const inLow = rl[i]![j]! <= k;
      if (inLow && !inHigh) tSum += rh[i]![j]! - k;
      if (inHigh && !inLow) cSum += rl[i]![j]! - k;
      if (inHigh && inLow) shared++;
    }
    overlap.push(round(shared / k, 2));
  }
  return { trustworthiness: round(1 - norm * tSum), continuity: round(1 - norm * cSum), overlap };
}

// ---------------------------------------------------------------- interpretable axes
export function interpretableCoords(vocabulary: string[], vector: number[], leadershipScope: number): [number, number] {
  const total = vector.reduce((s, x) => s + x, 0) || 1;
  const share = (set: string[]) => vocabulary.reduce((s, skill, i) => s + (set.includes(skill) ? vector[i]! : 0), 0) / total;
  const x = round(share(CUSTOMER_FACING_SKILLS) * 2 - 1);
  const y = round(0.5 * leadershipScope + 0.5 * share(LEADERSHIP_SKILLS));
  return [x, y];
}

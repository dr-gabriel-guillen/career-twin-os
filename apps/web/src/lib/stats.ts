/**
 * Descriptive statistics for DISPLAYING experiment results with honest uncertainty.
 *
 * Lane B owns experiment analysis. These helpers only let the UI show intervals,
 * planning guidance and an evidence label for the synthetic example; when B
 * publishes an analysis payload the UI must render that instead.
 * All formulas are standard and verified against textbook values in
 * tests/unit/stats.test.ts.
 */

/** Inverse standard normal CDF (Acklam's rational approximation, |error| < 1.2e-9). */
export function normalQuantile(p: number): number {
  if (!(p > 0 && p < 1)) throw new RangeError("p must be in (0,1)");
  const a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2, -3.066479806614716e1, 2.506628277459239];
  const b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1];
  const c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425;
  if (p < lo) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0]! * q + c[1]!) * q + c[2]!) * q + c[3]!) * q + c[4]!) * q + c[5]!) / ((((d[0]! * q + d[1]!) * q + d[2]!) * q + d[3]!) * q + 1);
  }
  if (p > 1 - lo) return -normalQuantile(1 - p);
  const q = p - 0.5;
  const r = q * q;
  return ((((((a[0]! * r + a[1]!) * r + a[2]!) * r + a[3]!) * r + a[4]!) * r + a[5]!) * q) / (((((b[0]! * r + b[1]!) * r + b[2]!) * r + b[3]!) * r + b[4]!) * r + 1);
}

export interface Interval {
  lower: number;
  upper: number;
}

/** Wilson score interval for a binomial proportion. Returns null when n = 0. */
export function wilsonInterval(successes: number, n: number, confidence = 0.95): Interval | null {
  if (!Number.isInteger(successes) || !Number.isInteger(n) || successes < 0 || n < 0 || successes > n) {
    throw new RangeError("need integers with 0 <= successes <= n");
  }
  if (n === 0) return null;
  const z = normalQuantile(1 - (1 - confidence) / 2);
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return { lower: Math.max(0, centre - half), upper: Math.min(1, centre + half) };
}

/**
 * Newcombe (1998) hybrid score interval for the difference p1 - p2.
 * Returns null if either arm has no observations.
 */
export function newcombeDifference(x1: number, n1: number, x2: number, n2: number, confidence = 0.95): (Interval & { estimate: number }) | null {
  const w1 = wilsonInterval(x1, n1, confidence);
  const w2 = wilsonInterval(x2, n2, confidence);
  if (!w1 || !w2) return null;
  const p1 = x1 / n1;
  const p2 = x2 / n2;
  const estimate = p1 - p2;
  return {
    estimate,
    lower: estimate - Math.sqrt((p1 - w1.lower) ** 2 + (w2.upper - p2) ** 2),
    upper: estimate + Math.sqrt((w1.upper - p1) ** 2 + (p2 - w2.lower) ** 2),
  };
}

/** Units per arm for a two-sided two-proportion z-test (pooled-variance formula). */
export function sampleSizePerArm(p1: number, p2: number, alpha = 0.05, power = 0.8): number {
  if (!(p1 > 0 && p1 < 1 && p2 > 0 && p2 < 1)) throw new RangeError("rates must be in (0,1)");
  if (p1 === p2) return Number.POSITIVE_INFINITY;
  const za = normalQuantile(1 - alpha / 2);
  const zb = normalQuantile(power);
  const pbar = (p1 + p2) / 2;
  const n = (za * Math.sqrt(2 * pbar * (1 - pbar)) + zb * Math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p1 - p2) ** 2;
  return Math.ceil(n);
}

/**
 * Smallest absolute increase over `baseline` detectable with `nPerArm` units.
 * Returns null when even a jump to ~100% would not be detectable.
 */
export function minimumDetectableIncrease(baseline: number, nPerArm: number, alpha = 0.05, power = 0.8): number | null {
  if (!(baseline > 0 && baseline < 1) || nPerArm < 2) return null;
  const top = 0.999;
  if (sampleSizePerArm(baseline, top, alpha, power) > nPerArm) return null;
  let lo = baseline + 1e-6;
  let hi = top;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (sampleSizePerArm(baseline, mid, alpha, power) <= nPerArm) hi = mid;
    else lo = mid;
  }
  return hi - baseline;
}

export const EVIDENCE_LEVELS = ["no-data", "insufficient", "inconclusive", "meets-preregistered-criterion"] as const;
export type EvidenceLevel = (typeof EVIDENCE_LEVELS)[number];

export interface ArmSummary {
  variant: string;
  assigned: number;
  observed: number;
  successes: number;
  censored: number;
  missing: number;
  rate: number | null;
  interval: Interval | null;
}

/** Minimum observed units per arm before the UI will even discuss a difference. */
export const MIN_OBSERVED_PER_ARM = 10;

/**
 * Evidence label. The UI never says "winner". The strongest statement,
 * "meets-preregistered-criterion", needs ALL of: a locked design, the planned
 * sample reached in every arm, exactly two variants, and an interval for the
 * difference that excludes zero.
 */
export function evidenceLevel(input: {
  arms: ArmSummary[];
  designLocked: boolean;
  plannedPerArm: number | null;
  difference: Interval | null;
}): { level: EvidenceLevel; reasons: string[] } {
  const reasons: string[] = [];
  const observed = input.arms.map((a) => a.observed);
  if (observed.every((n) => n === 0)) return { level: "no-data", reasons: ["No observed outcomes yet."] };
  const minObserved = Math.min(...observed);
  if (minObserved < MIN_OBSERVED_PER_ARM) {
    reasons.push(`Fewer than ${MIN_OBSERVED_PER_ARM} observed outcomes in at least one variant (minimum is ${minObserved}).`);
  }
  if (!input.designLocked) reasons.push("The design was not preregistered and locked before assignment.");
  if (input.plannedPerArm === null) reasons.push("No planned sample size was recorded.");
  else if (minObserved < input.plannedPerArm) reasons.push(`Planned sample of ${input.plannedPerArm} per variant not reached (minimum observed is ${minObserved}).`);
  if (input.arms.length !== 2) reasons.push("More than two variants: pairwise comparisons need a multiple-comparison plan.");
  if (minObserved < MIN_OBSERVED_PER_ARM) return { level: "insufficient", reasons };
  const excludesZero = input.difference !== null && (input.difference.lower > 0 || input.difference.upper < 0);
  if (!excludesZero) reasons.push("The interval for the difference includes zero.");
  if (reasons.length === 0) return { level: "meets-preregistered-criterion", reasons: ["Design locked, planned sample reached, interval excludes zero."] };
  return { level: "inconclusive", reasons };
}

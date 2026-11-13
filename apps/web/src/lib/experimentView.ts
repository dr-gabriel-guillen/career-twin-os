/**
 * Read-model for experiment results: per-variant counts with censoring and
 * missingness kept visible, an interval for the difference, and an evidence label.
 */
import type { ExperimentSpec } from "@/contracts/types";
import type { ExperimentAssignment, ExperimentOutcome } from "@/adapters/proposed";
import { MIN_OBSERVED_PER_ARM, evidenceLevel, newcombeDifference, wilsonInterval, type ArmSummary, type EvidenceLevel, type Interval } from "./stats";

export interface ExperimentResults {
  arms: ArmSummary[];
  totalAssigned: number;
  totalObserved: number;
  totalCensored: number;
  totalMissing: number;
  /** Assigned units with no outcome record at all. Counted as missing, never as failures. */
  unrecorded: number;
  exposed: number;
  difference: (Interval & { estimate: number }) | null;
  differenceLabel: string | null;
  evidence: { level: EvidenceLevel; reasons: string[] };
  origins: string[];
}

export function summariseExperiment(
  spec: ExperimentSpec,
  assignments: ExperimentAssignment[],
  outcomes: ExperimentOutcome[],
  plannedPerArm: number | null,
): ExperimentResults {
  const mine = assignments.filter((a) => a.experiment_id === spec.id);
  const outcomeByAssignment = new Map<string, ExperimentOutcome>();
  for (const o of outcomes) {
    if (o.experiment_id === spec.id && o.metric === spec.primary_metric) outcomeByAssignment.set(o.assignment_id, o);
  }
  let unrecorded = 0;
  const arms: ArmSummary[] = spec.variants.map((variant) => {
    const units = mine.filter((a) => a.variant === variant);
    let observed = 0;
    let successes = 0;
    let censored = 0;
    let missing = 0;
    for (const u of units) {
      const o = outcomeByAssignment.get(u.id);
      if (!o) {
        missing++;
        unrecorded++;
      } else if (o.status === "observed") {
        observed++;
        if (o.value === true) successes++;
      } else if (o.status === "censored") censored++;
      else missing++;
    }
    return {
      variant,
      assigned: units.length,
      observed,
      successes,
      censored,
      missing,
      rate: observed > 0 ? successes / observed : null,
      interval: wilsonInterval(successes, observed),
    };
  });
  const [a, b] = arms;
  const difference = arms.length === 2 && a && b ? newcombeDifference(a.successes, a.observed, b.successes, b.observed) : null;
  return {
    arms,
    totalAssigned: mine.length,
    totalObserved: arms.reduce((s, x) => s + x.observed, 0),
    totalCensored: arms.reduce((s, x) => s + x.censored, 0),
    totalMissing: arms.reduce((s, x) => s + x.missing, 0),
    unrecorded,
    exposed: mine.filter((m) => m.exposure_logged_at !== null).length,
    difference,
    differenceLabel: arms.length === 2 && a && b ? `${a.variant} − ${b.variant}` : null,
    evidence: evidenceLevel({ arms, designLocked: spec.design_locked_at !== null, plannedPerArm, difference, closed: spec.status === "closed" }),
    origins: [...new Set(outcomes.filter((o) => o.experiment_id === spec.id).map((o) => o.data_origin))],
  };
}

/**
 * Planned units per variant, read from the stopping rule.
 *
 * Only a number that is directly followed by "unit(s)" (at most two words between,
 * e.g. "116 job units") counts. Dates, percentages and durations are ignored. If the
 * rule names no such number, or more than one different one, the answer is null and no
 * result can be claimed: guessing a plan would be worse than having none (finding F-03).
 */
export function plannedPerArmFromRule(spec: ExperimentSpec): number | null {
  const re = /(?<![\d.,/-])(\d{1,3}(?:[,\u00a0\u202f ]\d{3})+|\d+)(?![\d.,%/-])\s+(?:[a-z-]+\s+){0,2}units?\b(\s+(?:per|in each|for each)\s+(?:variant|arm|cohort|group))?/gi;
  const found = [...spec.stopping_rule.matchAll(re)].map((m) => ({ n: Number(m[1]!.replace(/[^\d]/g, "")), perArm: m[2] !== undefined }));
  const distinct = [...new Set(found.map((f) => `${f.n}/${f.perArm}`))];
  if (distinct.length !== 1) return null;
  const { n, perArm } = found[0]!;
  if (!Number.isSafeInteger(n) || n <= 0 || spec.variants.length < 2) return null;
  return perArm ? n : Math.ceil(n / spec.variants.length);
}

/** Smallest plan the UI lets a design be locked with. */
export const MIN_PLANNED_PER_ARM = MIN_OBSERVED_PER_ARM;

/** Structural problems that should stop a design from being locked. */
export function designProblems(spec: ExperimentSpec): string[] {
  const problems: string[] = [];
  if (spec.title.trim().length < 3) problems.push("Give the experiment a title.");
  if (spec.hypothesis.trim().length < 20) problems.push("State a falsifiable hypothesis (at least 20 characters).");
  const variants = spec.variants.map((v) => v.trim());
  if (variants.length < 2) problems.push("At least two variants are required.");
  if (variants.some((v) => v.length === 0)) problems.push("Variant names cannot be empty.");
  if (new Set(variants).size !== variants.length) problems.push("Variant names must be unique.");
  if (spec.primary_metric.trim().length === 0) problems.push("Name exactly one primary metric.");
  if (!spec.outcome_window_days || spec.outcome_window_days < 1) problems.push("Set an outcome window of at least one day.");
  const planned = plannedPerArmFromRule(spec);
  if (planned === null) problems.push("The stopping rule must state the planned number of units (for example: “Stop when 40 units have a closed outcome window”).");
  else if (planned < MIN_PLANNED_PER_ARM) problems.push(`Plan at least ${MIN_PLANNED_PER_ARM} units per variant (${MIN_PLANNED_PER_ARM * Math.max(2, spec.variants.length)} in total). A smaller plan cannot support any claim.`);
  if ((spec.analysis_plan ?? "").trim().length < 20) problems.push("Describe the analysis plan before any data exists.");
  if (!spec.consent_required) problems.push("Consent must be required: experiments act on your own applications.");
  return problems;
}

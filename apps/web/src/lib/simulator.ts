/**
 * Arithmetic for comparing TransitionScenario payloads (lane C) under the user's
 * own edits. This is bookkeeping over upstream facts — sums, a pace conversion
 * and a preference-weighted ordering. It makes no forecast.
 *
 * Coverage is lane C's metric. The UI never derives it from skill names when
 * lane C publishes its per-action evidence results (`outputs.applied_actions`):
 *   - plan unedited            -> lane C's `coverage_after`, as given;
 *   - actions switched off     -> recount from `reaches_evidenced_level`;
 *   - effort hours changed     -> NOT shown: only lane C can recompute it.
 * The older skill-name arithmetic is kept only for payloads without
 * `applied_actions` (the lane-D stand-in), and is labelled as such.
 */
import type { ScenarioView } from "@/adapters";
import type { TransitionScenario } from "@/contracts/types";
import type { ScenarioActionEdit, SimulatorSettings } from "@/state/workspace";

export const WEEKS_PER_MONTH = 52 / 12;
const LEADERSHIP_VALUE: Record<string, number | null> = { low: 0.2, medium: 0.6, high: 1, unknown: null };
const TOLERANCE = 0.002;

export type CoverageBasis =
  /** Upstream value shown untouched. */
  | "upstream"
  /** Recounted from lane C's per-action `reaches_evidenced_level`. */
  | "upstream-per-action"
  /** Stand-in arithmetic over skill names (no per-action results in the payload). */
  | "skill-names"
  /** Effort hours were edited; lane C must recompute. Coverage is not shown. */
  | "needs-upstream-recompute"
  /** The payload has no usable outputs. */
  | "unavailable";

export interface ScenarioComputed {
  view: ScenarioView;
  actions: ScenarioActionEdit[];
  edited: boolean;
  totalHours: number;
  months: number;
  fitsHorizon: boolean;
  /** Sum of user-entered costs for included actions; null if none was entered. */
  cost: number | null;
  /** True when some included actions have no cost entered. */
  costPartial: boolean;
  coverageBefore: number | null;
  coverageAfter: number | null;
  coverageBasis: CoverageBasis;
  /** The UI could not reproduce the upstream coverage from the upstream actions. */
  inconsistent: boolean;
  /** 0..1 preference index, or null when it cannot be formed. */
  priority: number | null;
}

export function defaultEdits(scenario: TransitionScenario): ScenarioActionEdit[] {
  return scenario.planned_actions.map((a) => ({ skill: a.skill, effort_hours: a.effort_hours, included: true, cost: null }));
}

function coverage(view: ScenarioView, actions: ScenarioActionEdit[]): { after: number | null; basis: CoverageBasis; inconsistent: boolean } {
  const out = view.outputs;
  if (!out) return { after: null, basis: "unavailable", inconsistent: false };
  const before = out.coverage_before;
  const n = out.target_skills.length;
  if (before === null || out.coverage_after === null || n === 0) return { after: out.coverage_after, basis: "upstream", inconsistent: false };

  const allIncluded = actions.every((a) => a.included);
  const hoursUntouched = actions.every((a, i) => a.effort_hours === view.scenario.planned_actions[i]?.effort_hours);
  if (allIncluded && hoursUntouched) return { after: out.coverage_after, basis: "upstream", inconsistent: false };

  if (out.applied_actions) {
    const applied = out.applied_actions;
    const gain = (skills: Set<string>) =>
      new Set(applied.filter((a) => skills.has(a.skill) && a.reaches_evidenced_level && out.target_skills.includes(a.feature)).map((a) => a.feature)).size / n;
    const upstreamCheck = Math.min(1, before + gain(new Set(view.scenario.planned_actions.map((a) => a.skill))));
    const inconsistent = Math.abs(upstreamCheck - out.coverage_after) > TOLERANCE;
    if (!hoursUntouched || inconsistent) return { after: null, basis: "needs-upstream-recompute", inconsistent };
    return { after: Math.min(1, before + gain(new Set(actions.filter((a) => a.included).map((a) => a.skill)))), basis: "upstream-per-action", inconsistent: false };
  }

  // Stand-in payloads: a planned skill that is a target skill counts as covered once included.
  const count = (skills: string[]) => new Set(skills.filter((s) => out.target_skills.includes(s))).size / n;
  const upstreamCheck = Math.min(1, before + count(view.scenario.planned_actions.map((a) => a.skill)));
  const inconsistent = Math.abs(upstreamCheck - out.coverage_after) > TOLERANCE;
  if (inconsistent) return { after: null, basis: "needs-upstream-recompute", inconsistent };
  return { after: Math.min(1, before + count(actions.filter((a) => a.included).map((a) => a.skill))), basis: "skill-names", inconsistent: false };
}

export function compareScenarios(
  views: ScenarioView[],
  edits: Record<string, ScenarioActionEdit[]>,
  settings: SimulatorSettings,
): ScenarioComputed[] {
  const base = views.map((view) => {
    const stored = Object.hasOwn(edits, view.scenario.id) ? edits[view.scenario.id] : undefined;
    // Ignore stored edits that no longer line up with the upstream action list.
    const usable = stored !== undefined && stored.length === view.scenario.planned_actions.length && stored.every((a, i) => a.skill === view.scenario.planned_actions[i]?.skill);
    const actions = usable ? stored : defaultEdits(view.scenario);
    const included = actions.filter((a) => a.included);
    const totalHours = included.reduce((s, a) => s + a.effort_hours, 0);
    const months = totalHours / settings.hoursPerWeek / WEEKS_PER_MONTH;
    const entered = included.filter((a) => a.cost !== null);
    const cov = coverage(view, actions);
    return {
      view,
      actions,
      edited: usable,
      totalHours,
      months,
      fitsHorizon: months <= view.scenario.time_horizon_months,
      cost: entered.length === 0 ? null : entered.reduce((s, a) => s + (a.cost as number), 0),
      costPartial: entered.length > 0 && entered.length < included.length,
      coverageBefore: view.outputs?.coverage_before ?? null,
      coverageAfter: cov.after,
      coverageBasis: cov.basis,
      inconsistent: cov.inconsistent,
    };
  });
  const maxHours = Math.max(0, ...base.map((b) => b.totalHours));
  return base.map((b) => {
    const parts: { weight: number; value: number }[] = [];
    if (b.coverageBefore !== null && b.coverageAfter !== null) parts.push({ weight: settings.weights.coverage, value: Math.max(0, b.coverageAfter - b.coverageBefore) });
    if (maxHours > 0) parts.push({ weight: settings.weights.effort, value: 1 - b.totalHours / maxHours });
    const lead = b.view.outputs ? LEADERSHIP_VALUE[b.view.outputs.leadership_exposure] ?? null : null;
    // Unknown inputs are left out of the index rather than scored as zero.
    if (lead !== null) parts.push({ weight: settings.weights.leadership, value: lead });
    const total = parts.reduce((s, p) => s + p.weight, 0);
    const priority = total > 0 ? parts.reduce((s, p) => s + p.weight * p.value, 0) / total : null;
    return { ...b, priority };
  });
}

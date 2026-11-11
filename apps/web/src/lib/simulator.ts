/**
 * Arithmetic for comparing TransitionScenario payloads (lane C) under the user's
 * own edits. This is bookkeeping over upstream facts — sums, a pace conversion
 * and a preference-weighted ordering. It makes no forecast.
 */
import type { ScenarioView } from "@/adapters";
import type { TransitionScenario } from "@/contracts/types";
import type { ScenarioActionEdit, SimulatorSettings } from "@/state/workspace";

export const WEEKS_PER_MONTH = 52 / 12;
const LEADERSHIP_VALUE: Record<string, number | null> = { low: 0.2, medium: 0.6, high: 1, unknown: null };

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
  /** Upstream coverage could not be reproduced from the listed actions. */
  inconsistent: boolean;
  /** 0..1 preference index, or null when it cannot be formed. */
  priority: number | null;
}

export function defaultEdits(scenario: TransitionScenario): ScenarioActionEdit[] {
  return scenario.planned_actions.map((a) => ({ skill: a.skill, effort_hours: a.effort_hours, included: true, cost: null }));
}

/** Coverage after completing `skills`, assuming planned skills are current gaps. */
function coverageAfter(view: ScenarioView, skills: string[]): number | null {
  const out = view.outputs;
  if (!out || out.target_skills.length === 0) return null;
  const gained = new Set(skills.filter((s) => out.target_skills.includes(s))).size;
  return Math.min(1, out.coverage_before + gained / out.target_skills.length);
}

export function compareScenarios(
  views: ScenarioView[],
  edits: Record<string, ScenarioActionEdit[]>,
  settings: SimulatorSettings,
): ScenarioComputed[] {
  const base = views.map((view) => {
    const stored = edits[view.scenario.id];
    // Ignore stored edits that no longer line up with the upstream action list.
    const usable = stored !== undefined && stored.length === view.scenario.planned_actions.length && stored.every((a, i) => a.skill === view.scenario.planned_actions[i]?.skill);
    const actions = usable ? stored : defaultEdits(view.scenario);
    const included = actions.filter((a) => a.included);
    const totalHours = included.reduce((s, a) => s + a.effort_hours, 0);
    const months = totalHours / settings.hoursPerWeek / WEEKS_PER_MONTH;
    const entered = included.filter((a) => a.cost !== null);
    const upstreamCheck = coverageAfter(view, view.scenario.planned_actions.map((a) => a.skill));
    const inconsistent = view.outputs !== null && upstreamCheck !== null && Math.abs(upstreamCheck - view.outputs.coverage_after) > 0.002;
    const after = view.outputs === null ? null : inconsistent ? view.outputs.coverage_after : coverageAfter(view, included.map((a) => a.skill));
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
      coverageAfter: after,
      inconsistent,
    };
  });
  const maxHours = Math.max(0, ...base.map((b) => b.totalHours));
  return base.map((b) => {
    const parts: { weight: number; value: number }[] = [];
    if (b.coverageBefore !== null && b.coverageAfter !== null) parts.push({ weight: settings.weights.coverage, value: Math.max(0, b.coverageAfter - b.coverageBefore) });
    if (maxHours > 0) parts.push({ weight: settings.weights.effort, value: 1 - b.totalHours / maxHours });
    const lead = b.view.outputs ? LEADERSHIP_VALUE[b.view.outputs.leadership_exposure] ?? null : null;
    // Unknown leadership exposure is left out of the index rather than scored as zero.
    if (lead !== null) parts.push({ weight: settings.weights.leadership, value: lead });
    const total = parts.reduce((s, p) => s + p.weight, 0);
    const priority = total > 0 ? parts.reduce((s, p) => s + p.weight * p.value, 0) / total : null;
    return { ...b, priority };
  });
}

import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Banner, Button, Card, EmptyState, Facts, TableRegion, TextField, useAnnounce, type Tone } from "@careertwin/ui";
import { Page } from "@/components/common";
import type { ScenarioView } from "@/adapters";
import { humanise, percent } from "@/lib/format";
import { compareScenarios, defaultEdits, type ScenarioComputed } from "@/lib/simulator";
import { useApp } from "@/state/AppContext";
import type { ScenarioActionEdit } from "@/state/workspace";

const UNCERTAINTY_TONE: Record<string, Tone> = { low: "success", medium: "warning", high: "danger" };

export default function SimulatorPage() {
  const { bundle, workspace, dispatch } = useApp();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const sim = workspace.simulator;

  const computed = useMemo(() => compareScenarios(bundle.scenarios, workspace.scenarioEdits, sim), [bundle.scenarios, workspace.scenarioEdits, sim]);
  const selected: ScenarioComputed | null = computed.find((c) => c.view.scenario.id === params.get("scenario")) ?? computed[0] ?? null;

  if (bundle.scenarios.length === 0) {
    return (
      <Page title="Career Simulator">
        <EmptyState title="No transition scenarios are available.">
          <p>The analytics lane (C) has not provided any scenarios for this candidate.</p>
        </EmptyState>
      </Page>
    );
  }

  const setWeights = (key: "coverage" | "effort" | "leadership", value: number) => dispatch({ type: "simulator/set", settings: { ...sim, weights: { ...sim.weights, [key]: value } } });
  const editAction = (view: ScenarioView, index: number, patch: Partial<ScenarioActionEdit>) => {
    const current = (Object.hasOwn(workspace.scenarioEdits, view.scenario.id) ? workspace.scenarioEdits[view.scenario.id] : undefined) ?? defaultEdits(view.scenario);
    dispatch({ type: "scenario/edit", scenarioId: view.scenario.id, actions: current.map((a, i) => (i === index ? { ...a, ...patch } : a)) });
  };
  const ranked = [...computed].filter((c) => c.priority !== null).sort((a, b) => (b.priority as number) - (a.priority as number));
  const totalWeight = sim.weights.coverage + sim.weights.effort + sim.weights.leadership;

  return (
    <Page
      title="Career Simulator"
      lead="Compare hypothetical paths toward different roles: what you would need to learn, how long that takes at your pace, what it costs by your own numbers, and what remains uncertain. Nothing here forecasts an outcome."
    >
      <Banner tone="synthetic" title="Every scenario is hypothetical." data-testid="hypothetical-banner">
        Effort hours are placeholder assumptions for a fictional candidate, and you can overwrite them. No salary, offer or success rate is predicted, because there is no evidence to support one.
      </Banner>

      <Card title="Your pace and what you value">
        <div className="app-toolbar">
          <TextField label="Hours you can invest per week" type="number" min={1} max={60} value={sim.hoursPerWeek} onChange={(e) => dispatch({ type: "simulator/set", settings: { ...sim, hoursPerWeek: Math.min(60, Math.max(1, Number(e.target.value) || 1)) } })} data-testid="sim-hours" />
          {([
            ["coverage", "Closing the skill gap"],
            ["effort", "Keeping effort low"],
            ["leadership", "Leadership exposure"],
          ] as const).map(([key, label]) => (
            <div className="ct-field" key={key}>
              <label className="ct-field__label" htmlFor={`w-${key}`}>
                {label}: {sim.weights[key]}
              </label>
              <input id={`w-${key}`} type="range" min={0} max={100} step={5} value={sim.weights[key]} onChange={(e) => setWeights(key, Number(e.target.value))} data-testid={`sim-weight-${key}`} />
            </div>
          ))}
        </div>
        <p className="ct-small ct-muted">
          The weights only order the scenarios by what you say matters to you. They are saved in this browser. {totalWeight === 0 ? "All weights are zero, so no ordering is shown." : ""}
        </p>
      </Card>

      <Card title="Side-by-side comparison">
        <TableRegion label="Scenario comparison">
          <table className="ct-table" data-testid="scenario-table">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col" className="ct-num">Planned effort</th>
                <th scope="col" className="ct-num">Time at your pace</th>
                <th scope="col">Within stated horizon?</th>
                <th scope="col" className="ct-num">Direct cost (yours)</th>
                <th scope="col">Target-skill coverage</th>
                <th scope="col">Leadership exposure</th>
                <th scope="col">Uncertainty</th>
                <th scope="col" className="ct-num">Order by your weights</th>
              </tr>
            </thead>
            <tbody>
              {computed.map((c) => {
                const rank = ranked.findIndex((r) => r.view.scenario.id === c.view.scenario.id);
                return (
                  <tr key={c.view.scenario.id} aria-selected={selected?.view.scenario.id === c.view.scenario.id} data-testid={`scenario-row-${c.view.scenario.id}`}>
                    <th scope="row">
                      <button type="button" className="ct-sort" onClick={() => setParams({ scenario: c.view.scenario.id })} data-testid={`scenario-pick-${c.view.scenario.id}`}>
                        {c.view.scenario.target_role}
                      </button>
                      <div><Badge tone="synthetic">Hypothetical</Badge></div>
                    </th>
                    <td className="ct-num" data-testid={`hours-${c.view.scenario.id}`}>{c.totalHours} h</td>
                    <td className="ct-num" data-testid={`months-${c.view.scenario.id}`}>{c.totalHours === 0 ? "—" : `${c.months.toFixed(1)} months`}</td>
                    <td>{c.totalHours === 0 ? "No actions included" : c.fitsHorizon ? `Yes (horizon ${c.view.scenario.time_horizon_months} months)` : `No — horizon is ${c.view.scenario.time_horizon_months} months`}</td>
                    <td className="ct-num" data-testid={`cost-${c.view.scenario.id}`}>{c.cost === null ? "Not entered" : `${c.cost.toLocaleString("en-US")}${c.costPartial ? " (partial)" : ""}`}</td>
                    <td data-testid={`coverage-${c.view.scenario.id}`}>
                      {c.coverageBefore === null ? "Unknown" : c.coverageAfter === null ? `${percent(c.coverageBefore)} → needs recalculation upstream` : `${percent(c.coverageBefore)} → ${percent(c.coverageAfter)}`}
                    </td>
                    <td>{c.view.outputs ? humanise(c.view.outputs.leadership_exposure) : "Unknown"}</td>
                    <td>{c.view.outputs ? <Badge tone={UNCERTAINTY_TONE[c.view.outputs.uncertainty] ?? "warning"}>{humanise(c.view.outputs.uncertainty)}</Badge> : "Unknown"}</td>
                    <td className="ct-num" data-testid={`rank-${c.view.scenario.id}`}>{rank < 0 || totalWeight === 0 ? "—" : `#${rank + 1} (${Math.round((c.priority as number) * 100)})`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableRegion>
        <p className="ct-small ct-muted">
          “Order by your weights” is a preference ordering: the gain in target-skill coverage, how light the effort is relative to the heaviest scenario, and leadership exposure, combined with your weights. It is not a prediction, a probability or a recommendation to act.
        </p>
      </Card>

      {selected ? (
        <Card title={`Plan: ${selected.view.scenario.target_role}`} actions={<Button size="sm" onClick={() => { dispatch({ type: "scenario/reset", scenarioId: selected.view.scenario.id }); announce("Scenario reset to the upstream assumptions."); }} disabled={!Object.hasOwn(workspace.scenarioEdits, selected.view.scenario.id)} data-testid="scenario-reset">Reset to upstream assumptions</Button>} data-testid="scenario-detail">
          {selected.edited ? <Banner tone="info" title="You changed this plan." data-testid="scenario-edited">Totals and coverage below use your numbers. The upstream scenario is unchanged.</Banner> : null}
          {selected.coverageBasis === "needs-upstream-recompute" ? (
            <Banner tone="warning" title="Coverage for this edited plan is not shown." data-testid="coverage-recompute">
              {selected.inconsistent
                ? "The UI could not reproduce the analytics lane’s coverage from its own per-action results, so it does not guess a new value."
                : "You changed effort hours. Whether an action reaches the evidence threshold depends on those hours, and only the analytics lane can recompute that. Hours, time and cost below do use your numbers."}
            </Banner>
          ) : null}
          {selected.coverageBasis === "skill-names" ? (
            <Banner tone="neutral" title="Coverage here is a stand-in count." data-testid="coverage-standin">
              This payload has no per-action evidence results, so an included action counts its skill as covered. The analytics lane applies stricter evidence thresholds.
            </Banner>
          ) : null}
          <TableRegion label="Planned actions">
            <table className="ct-table" data-testid="actions-table">
              <thead>
                <tr>
                  <th scope="col">Include</th>
                  <th scope="col">Skill to build</th>
                  <th scope="col">Effort (hours)</th>
                  <th scope="col">Direct cost (you enter)</th>
                  <th scope="col">Evidence to produce</th>
                </tr>
              </thead>
              <tbody>
                {selected.actions.map((a, i) => {
                  const inTarget = selected.view.outputs ? selected.view.outputs.target_skills.includes(a.skill) : null;
                  return (
                    <tr key={`${i}-${a.skill}`}>
                      <td>
                        <input type="checkbox" checked={a.included} onChange={(e) => editAction(selected.view, i, { included: e.target.checked })} aria-label={`Include ${humanise(a.skill)}`} data-testid={`action-include-${i}`} />
                      </td>
                      <th scope="row">
                        {humanise(a.skill)}
                        {inTarget === false ? <div className="ct-small ct-muted">Not one of the target skills; it does not change coverage.</div> : null}
                      </th>
                      <td>
                        <input className="ct-input" type="number" min={0} max={5000} step={5} value={a.effort_hours} onChange={(e) => editAction(selected.view, i, { effort_hours: Math.min(5000, Math.max(0, Number(e.target.value) || 0)) })} aria-label={`Effort hours for ${humanise(a.skill)}`} data-testid={`action-hours-${i}`} />
                      </td>
                      <td>
                        <input className="ct-input" type="number" min={0} step={50} value={a.cost ?? ""} placeholder="Not entered" onChange={(e) => editAction(selected.view, i, { cost: e.target.value === "" ? null : Math.max(0, Number(e.target.value) || 0) })} aria-label={`Direct cost for ${humanise(a.skill)}`} data-testid={`action-cost-${i}`} />
                      </td>
                      <td className="ct-small">{selected.view.scenario.planned_actions[i]?.evidence_goal ?? "Not stated"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableRegion>
          <div className="ct-grid ct-grid--2">
            <Facts
              items={[
                { label: "Target skills", value: selected.view.outputs ? selected.view.outputs.target_skills.map(humanise).join(", ") : "Unknown" },
                {
                  label: "Coverage",
                  value:
                    selected.coverageBefore === null
                      ? "Unknown"
                      : selected.coverageAfter === null
                        ? `${percent(selected.coverageBefore)} today; the value after this edited plan needs recalculation upstream`
                        : `${percent(selected.coverageBefore)} today → ${percent(selected.coverageAfter)} if every included action were completed and evidenced`,
                },
                { label: "How coverage is defined", value: selected.view.outputs?.coverage_definition ?? "Share of target skills counted as covered (definition not supplied by the payload)." },
                { label: "Why uncertain", value: selected.view.outputs?.uncertainty_note ?? "Not stated" },
                { label: "Status", value: selected.view.scenario.status },
              ]}
            />
            <div className="ct-stack">
              <div>
                <h3>Assumptions</h3>
                <ul className="ct-small">{selected.view.scenario.assumptions.map((a, i) => <li key={`${i}-${a}`}>{a}</li>)}</ul>
              </div>
              <div>
                <h3>Risks</h3>
                {(selected.view.scenario.risks ?? []).length === 0 ? <p className="ct-muted">None listed.</p> : <ul className="ct-small">{(selected.view.scenario.risks ?? []).map((r, i) => <li key={`${i}-${r}`}>{r}</li>)}</ul>}
              </div>
            </div>
          </div>
          <Link to="/atlas">See the hypothetical movement path on the Career Atlas</Link>
        </Card>
      ) : null}
    </Page>
  );
}

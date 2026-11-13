import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, Icon } from "@careertwin/ui";
import type { AtlasProjection } from "@/adapters/proposed";
import { humanise, percent, skillLabel } from "@/lib/format";

/** Labels from upstream are sometimes slugs ("data-engineer"); show them as words. */
const words = (label: string) => (/^[a-z0-9]+(-[a-z0-9]+)+$|^[a-z]+$/.test(label) ? skillLabel(label) : label);
import { compareScenarios } from "@/lib/simulator";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";
import { Screen } from "./Screen";

const W = 640;
const H = 420;
const SUPPORTED = "#supported-only";

/** A quiet map: postings faint, role centres labelled, your two positions, and the selected route. */
function TransitionMap({ p, scenarioEntity, families }: { p: AtlasProjection; scenarioEntity: string | null; families: string[] }) {
  const entity = new Map(p.entities.map((e) => [e.entity_id, e]));
  const xs = p.points.map((x) => x.coords[0]!);
  const ys = p.points.map((x) => x.coords[1]!);
  const pad = 30;
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const sx = (v: number) => pad + ((v - x0) / (x1 - x0 || 1)) * (W - 2 * pad);
  const sy = (v: number) => H - pad - ((v - y0) / (y1 - y0 || 1)) * (H - 2 * pad);
  const byId = new Map(p.points.map((x) => [x.entity_id, x]));
  const route = scenarioEntity ? p.paths.find((path) => path.to_entity_id === scenarioEntity) ?? null : null;
  const fam = (id: string) => entity.get(id)?.role_family ?? null;
  const cat = (f: string | null) => (f === null ? "" : `cat-${(families.indexOf(f) % 9) + 1}`);
  const candidate = p.points.find((x) => x.representation_type === "candidate" && !x.entity_id.endsWith(SUPPORTED));
  const supported = p.points.find((x) => x.representation_type === "candidate" && x.entity_id.endsWith(SUPPORTED));
  const target = route ? byId.get(route.to_entity_id) : null;
  return (
    <svg className="pt-map" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Approximate skill map. ${route && target ? `Highlighted: the route from your position to ${target.label}.` : "Select a transition to highlight its route."} Map distance is not fit.`}>
      <defs>
        <marker id="pt-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="pt-map__arrowhead" />
        </marker>
      </defs>
      {p.points.filter((x) => x.representation_type === "job").map((x) => (
        <circle key={x.entity_id} cx={sx(x.coords[0]!)} cy={sy(x.coords[1]!)} r={3} className={`pt-map__job ${cat(fam(x.entity_id))}`} />
      ))}
      {p.points.filter((x) => x.representation_type === "role-centroid").map((x) => {
        const cx = sx(x.coords[0]!);
        const cy = sy(x.coords[1]!);
        const right = cx > W - 140;
        return (
          <g key={x.entity_id}>
            <rect x={cx - 5} y={cy - 5} width={10} height={10} transform={`rotate(45 ${cx} ${cy})`} className={`pt-map__centroid ${cat(fam(x.entity_id))}`} />
            <text x={right ? cx - 9 : cx + 9} y={cy + 4} textAnchor={right ? "end" : "start"} className="pt-map__label">
              {words(x.label.replace(/\s*\(centroid\)\s*/i, ""))}
            </text>
          </g>
        );
      })}
      {route && byId.get(route.from_entity_id) && target ? (
        <line x1={sx(byId.get(route.from_entity_id)!.coords[0]!)} y1={sy(byId.get(route.from_entity_id)!.coords[1]!)} x2={sx(target.coords[0]!)} y2={sy(target.coords[1]!)} className="pt-map__route" markerEnd="url(#pt-arrow)" />
      ) : null}
      {target ? (
        <g>
          <circle cx={sx(target.coords[0]!)} cy={sy(target.coords[1]!)} r={7} className="pt-map__target" />
          <text x={sx(target.coords[0]!)} y={sy(target.coords[1]!) + 22} textAnchor="middle" className="pt-map__label pt-map__label--target">
            After this plan (hypothetical)
          </text>
        </g>
      ) : null}
      {supported ? <rect x={sx(supported.coords[0]!) - 6} y={sy(supported.coords[1]!) - 6} width={12} height={12} className="pt-map__you pt-map__you--supported" /> : null}
      {candidate ? (
        <g>
          <rect x={sx(candidate.coords[0]!) - 7} y={sy(candidate.coords[1]!) - 7} width={14} height={14} className="pt-map__you" />
          <text x={sx(candidate.coords[0]!) + (sx(candidate.coords[0]!) > W - 120 ? -12 : 12)} y={sy(candidate.coords[1]!) - 10} textAnchor={sx(candidate.coords[0]!) > W - 120 ? "end" : "start"} className="pt-map__label pt-map__label--you">
            You
          </text>
        </g>
      ) : null}
    </svg>
  );
}

export default function AtlasConcept() {
  const { bundle, workspace } = useApp();
  const { familyLabel } = useIndexes();
  const projection = bundle.projections.find((x) => x.method === "pca") ?? bundle.projections[0] ?? null;
  const computed = useMemo(() => compareScenarios(bundle.scenarios, workspace.scenarioEdits, workspace.simulator), [bundle.scenarios, workspace.scenarioEdits, workspace.simulator]);
  const [selectedId, setSelectedId] = useState<string | null>(computed[0]?.view.scenario.id ?? null);
  const selected = computed.find((c) => c.view.scenario.id === selectedId) ?? null;

  if (!projection) {
    return (
      <Screen title="Career paths">
        <EmptyState title="No career map is available for this data." />
      </Screen>
    );
  }
  const d = projection.diagnostics;
  const families = [...new Set(projection.entities.map((e) => e.role_family).filter((f): f is string => f !== null))].sort();
  const candidate = projection.entities.find((e) => e.entity_id.startsWith("candidate") && !e.entity_id.endsWith(SUPPORTED)) ?? null;
  // Explain what is actually drawn: positions come from projected points, not from the entity list.
  const candidatePoints = projection.points.filter((x) => x.representation_type === "candidate");
  const hasAll = candidatePoints.some((x) => !x.entity_id.endsWith(SUPPORTED));
  const hasSupported = candidatePoints.some((x) => x.entity_id.endsWith(SUPPORTED));
  const hours = workspace.simulator.hoursPerWeek;

  return (
    <Screen title="Career paths" lead="Where your evidence places you, the transitions you are weighing, and what each would take. A concept: nothing here forecasts an outcome.">
      <p className="pt-uncertainty" data-testid="pt-uncertainty">
        <Icon name="info" size={16} />
        <span>
          <strong>Approximate map.</strong>{" "}
          {d.cumulative_explained_variance !== null ? `The 2-D projection explains ${percent(d.cumulative_explained_variance)} of the variation in the input skill vectors` : "Its axes are hand-defined, so variance explained does not apply"}
          {d.knn_recall ? `; on average ${percent(d.knn_recall.value)} of a point’s ${d.knn_recall.k} nearest map neighbours are true neighbours` : d.trustworthiness ? `; neighbourhood trustworthiness ${d.trustworthiness.value.toFixed(2)} of 1.00` : ""}. Distance on the map is not fit and not a likelihood.
        </span>
      </p>

      <div className="pt-atlas">
        <section className="pt-atlas__where" aria-labelledby="pt-where">
          <h2 id="pt-where">Where you are</h2>
          <p data-testid="pt-position-note">
            {hasAll && hasSupported
              ? "Two marks show you: one from every usable claim, one from evidence-backed claims only. The further apart they are, the more your position rests on self-reporting."
              : hasAll
                ? "One mark shows you, from every usable claim. An evidence-only position was not provided, so this map cannot show how much of your position rests on self-reporting."
                : hasSupported
                  ? "One mark shows you, from evidence-backed claims only. A position from all usable claims was not provided."
                  : "Your position was not provided for this map."}
          </p>
          <ul className="ct-list-plain pt-legend">
            {hasAll ? <li><i className="pt-key pt-key--you" aria-hidden="true" /> You, from all usable claims</li> : null}
            {hasSupported ? <li><i className="pt-key pt-key--supported" aria-hidden="true" /> You, evidence-backed claims only</li> : null}
            <li><i className="pt-key pt-key--centroid" aria-hidden="true" /> Centre of a role family</li>
            <li><i className="pt-key pt-key--route" aria-hidden="true" /> Selected transition, hypothetical</li>
          </ul>
          {candidate?.top_skills.length ? <p className="pt-muted">Your strongest skills on the map: {candidate.top_skills.slice(0, 5).map(skillLabel).join(", ")}.</p> : null}
          <TransitionMap p={projection} scenarioEntity={selected?.view.outputs?.atlas_entity_id ?? null} families={families} />
          {selected?.view.outputs?.atlas_entity_id ? (
            <p className="pt-muted" data-testid="pt-route-caveat">
              The end of the dashed line is where the plan’s skills would sit on this 2-D drawing. It need not land next to the role’s centre, and its distance from any mark is not a measure of fit.
            </p>
          ) : null}
          <details className="pt-tech">
            <summary>Map diagnostics</summary>
            <ul>
              <li>Method: {projection.method === "pca" ? "principal components (PCA)" : humanise(projection.method)}, from {projection.input.n_dims ?? "many"} dimensions to 2.</li>
              <li>Variation in the input vectors explained by the two axes: {d.cumulative_explained_variance === null ? "not applicable (hand-defined axes)" : percent(d.cumulative_explained_variance)}. This describes the projection of the vectors, not how much career-relevant information they hold.</li>
              <li>Trustworthiness {d.trustworthiness ? `${d.trustworthiness.value.toFixed(2)} (k=${d.trustworthiness.k})` : "unknown"}; continuity {d.continuity ? `${d.continuity.value.toFixed(2)} (k=${d.continuity.k})` : "unknown"}. 1.00 would mean neighbourhoods are perfectly preserved.</li>
              {[...(d.flags ?? []), ...projection.warnings].map((w) => <li key={w}>{w}</li>)}
            </ul>
            <p>The full map, filters and point table stay in the current app under Career Atlas.</p>
          </details>
        </section>

        <section className="pt-atlas__go" aria-labelledby="pt-go">
          <h2 id="pt-go">Where you could go</h2>
          {computed.length === 0 ? (
            <p className="pt-muted">No transition scenarios were provided for this candidate.</p>
          ) : (
            <ul className="ct-list-plain pt-routes" data-testid="pt-routes">
              {computed.map((c) => {
                const o = c.view.outputs;
                const on = c.view.scenario.id === selectedId;
                return (
                  <li key={c.view.scenario.id}>
                    <button type="button" className="pt-route" aria-pressed={on} onClick={() => setSelectedId(c.view.scenario.id)}>
                      <span className="pt-route__head">
                        <strong>{words(c.view.scenario.target_role)}</strong>
                        <span className={`pt-unc pt-unc--${o?.uncertainty ?? "unknown"}`}>{o ? `${humanise(o.uncertainty)} uncertainty` : "Uncertainty unknown"}</span>
                      </span>
                      <span className="pt-route__cov">
                        <span className="pt-cov" aria-hidden="true">
                          {c.coverageAfter !== null ? <i className="pt-cov__after" style={{ width: `${c.coverageAfter * 100}%` }} /> : null}
                          {c.coverageBefore !== null ? <i className="pt-cov__before" style={{ width: `${c.coverageBefore * 100}%` }} /> : null}
                        </span>
                        <span>
                          Target skills covered: {c.coverageBefore === null ? "unknown" : percent(c.coverageBefore)} now
                          {c.coverageAfter !== null ? `, ${percent(c.coverageAfter)} if the plan were completed and evidenced` : ""}
                        </span>
                      </span>
                      <span className="pt-route__facts">
                        {c.totalHours} planned hours, about {c.months.toFixed(1)} months at {hours} h/week. {c.fitsHorizon ? `Within your ${c.view.scenario.time_horizon_months}-month horizon.` : `Beyond your ${c.view.scenario.time_horizon_months}-month horizon.`}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {selected ? (
            <section className="pt-block" aria-labelledby="pt-takes">
              <h3 id="pt-takes">What it would take: {words(selected.view.scenario.target_role)}</h3>
              <ol className="pt-plan">
                {selected.view.scenario.planned_actions.map((a, i) => (
                  <li key={`${i}-${a.skill}`}>
                    <strong>{skillLabel(a.skill)}</strong>, {a.effort_hours} h
                    {a.evidence_goal ? <span className="pt-muted">. Evidence to produce: {a.evidence_goal}</span> : null}
                  </li>
                ))}
              </ol>
              {selected.view.outputs?.uncertainty_note ? <p className="pt-muted">Why uncertain: {selected.view.outputs.uncertainty_note}</p> : null}
              {selected.view.outputs?.target_role_family ? <p className="pt-muted">Role family: {familyLabel(selected.view.outputs.target_role_family)}.</p> : null}
              <details className="pt-tech">
                <summary>Assumptions and risks</summary>
                <ul>
                  {selected.view.scenario.assumptions.map((a) => <li key={a}>{a}</li>)}
                  {(selected.view.scenario.risks ?? []).map((r) => <li key={r}>Risk: {r}</li>)}
                </ul>
              </details>
              <p className="pt-actions">
                <Link className="ct-btn ct-btn--secondary" to="/discover">See postings</Link>
                <a className="ct-btn ct-btn--ghost" href={`./${bundle.provenance.data_source === "artifacts" ? "?source=artifacts" : ""}#/simulator?scenario=${encodeURIComponent(selected.view.scenario.id)}`}>Adjust the plan in the simulator</a>
              </p>
            </section>
          ) : null}
        </section>
      </div>
    </Screen>
  );
}

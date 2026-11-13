import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge, Banner, Card, Checkbox, EmptyState, Facts, SelectField, TableRegion } from "@careertwin/ui";
import { Page, SkillChips } from "@/components/common";
import type { AtlasEntity, AtlasProjection } from "@/adapters/proposed";
import { REPRESENTATION_TYPES, type AtlasPoint, type RepresentationType } from "@/contracts/types";
import { percent, truncate } from "@/lib/format";
import { niceTicks } from "@/lib/ticks";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

const W = 720;
const H = 480;
const PAD = { left: 56, right: 24, top: 24, bottom: 48 };
const TYPE_LABEL: Record<RepresentationType, string> = { job: "Posting", "role-centroid": "Role-family centroid", candidate: "Candidate", scenario: "Hypothetical scenario" };
const TYPE_CLASS: Record<RepresentationType, string> = { job: "pt--job", "role-centroid": "pt--centroid", candidate: "pt--candidate", scenario: "pt--scenario" };
const SUPPORTED_SUFFIX = "#supported-only";

function Shape({ type, x, y, className }: { type: RepresentationType; x: number; y: number; className: string }) {
  if (type === "job") return <circle className={className} cx={x} cy={y} r={6} />;
  if (type === "role-centroid") return <rect className={className} x={x - 6} y={y - 6} width={12} height={12} transform={`rotate(45 ${x} ${y})`} />;
  if (type === "candidate") return <rect className={className} x={x - 7} y={y - 7} width={14} height={14} />;
  return <polygon className={className} points={`${x},${y - 8} ${x + 8},${y + 7} ${x - 8},${y + 7}`} />;
}

/** Inside the Career Atlas screen the page frame (title, focus) belongs to the parent. */
function Bare({ children }: { title?: string; lead?: ReactNode; children: ReactNode }) {
  return <div className="ct-stack ct-stack--lg">{children}</div>;
}

export default function AtlasPage({ embedded = false }: { embedded?: boolean }) {
  const Frame = embedded ? Bare : Page;
  const { bundle } = useApp();
  const { familyLabel } = useIndexes();
  const [projectionId, setProjectionId] = useState(bundle.projections[0]?.projection_id ?? "");
  const [types, setTypes] = useState<RepresentationType[]>([...REPRESENTATION_TYPES]);
  const [hiddenFamilies, setHiddenFamilies] = useState<string[]>([]);
  const [minQuality, setMinQuality] = useState(0);
  const [evidenceBasis, setEvidenceBasis] = useState<"all" | "supported">("all");
  const [showPaths, setShowPaths] = useState(true);
  const [showLabels, setShowLabels] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const pointRefs = useRef<Map<string, SVGGElement>>(new Map());

  const projection: AtlasProjection | null = bundle.projections.find((p) => p.projection_id === projectionId) ?? bundle.projections[0] ?? null;

  const view = useMemo(() => {
    if (!projection) return null;
    const entityById = new Map<string, AtlasEntity>(projection.entities.map((e) => [e.entity_id, e]));
    const families = [...new Set(projection.entities.map((e) => e.role_family).filter((f): f is string => f !== null))].sort();
    const xs = projection.points.map((p) => p.coords[0]!);
    const ys = projection.points.map((p) => p.coords[1]!);
    const pad = (lo: number, hi: number) => ((hi - lo) * 0.08 || 0.5);
    const x0 = Math.min(...xs) - pad(Math.min(...xs), Math.max(...xs));
    const x1 = Math.max(...xs) + pad(Math.min(...xs), Math.max(...xs));
    const y0 = Math.min(...ys) - pad(Math.min(...ys), Math.max(...ys));
    const y1 = Math.max(...ys) + pad(Math.min(...ys), Math.max(...ys));
    const sx = (v: number) => PAD.left + ((v - x0) / (x1 - x0)) * (W - PAD.left - PAD.right);
    const sy = (v: number) => H - PAD.bottom - ((v - y0) / (y1 - y0)) * (H - PAD.top - PAD.bottom);
    return { entityById, families, sx, sy, xTicks: niceTicks(x0, x1), yTicks: niceTicks(y0, y1) };
  }, [projection]);

  const visible = useMemo(() => {
    if (!projection || !view) return [];
    return projection.points
      .filter((p) => {
        if (!types.includes(p.representation_type)) return false;
        const fam = view.entityById.get(p.entity_id)?.role_family ?? null;
        if (fam !== null && p.representation_type !== "scenario" && hiddenFamilies.includes(fam)) return false;
        if (p.representation_type === "candidate") {
          const supportedOnly = p.entity_id.endsWith(SUPPORTED_SUFFIX);
          if (evidenceBasis === "supported" ? !supportedOnly : supportedOnly) return false;
        }
        if (p.neighborhood_quality !== null && p.neighborhood_quality !== undefined && p.neighborhood_quality < minQuality) return false;
        return true;
      })
      .sort((a, b) => a.coords[0]! - b.coords[0]! || a.coords[1]! - b.coords[1]! || a.entity_id.localeCompare(b.entity_id));
  }, [projection, view, types, hiddenFamilies, minQuality, evidenceBasis]);

  if (!projection || !view) {
    return (
      <Frame title="Career Atlas">
        <EmptyState title="No atlas projection is available.">
          <p>The analytics lane (C) has not provided a projection for this data.</p>
        </EmptyState>
      </Frame>
    );
  }

  const visibleIds = new Set(visible.map((p) => p.entity_id));
  const selected = visible.find((p) => p.entity_id === selectedId) ?? null;
  const selectedEntity = selected ? view.entityById.get(selected.entity_id) ?? null : null;
  const active = visible.find((p) => p.entity_id === (hoverId ?? focusId)) ?? null;
  const tabStop = visible.find((p) => p.entity_id === (focusId ?? selectedId))?.entity_id ?? visible[0]?.entity_id ?? null;
  const d = projection.diagnostics;
  const pointById = new Map(projection.points.map((p) => [p.entity_id, p]));

  const onKey = (e: KeyboardEvent<SVGGElement>, p: AtlasPoint) => {
    const i = visible.findIndex((v) => v.entity_id === p.entity_id);
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = Math.min(visible.length - 1, i + 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = Math.max(0, i - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = visible.length - 1;
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setSelectedId(p.entity_id === selectedId ? null : p.entity_id);
      return;
    } else if (e.key === "Escape") {
      setSelectedId(null);
      return;
    }
    if (next < 0) return;
    e.preventDefault();
    const target = visible[next]!;
    setFocusId(target.entity_id);
    pointRefs.current.get(target.entity_id)?.focus();
  };

  // The neighbourhood size is whatever the producer used; it is not assumed (finding F-08).
  const k = d.knn_recall?.k ?? d.trustworthiness?.k ?? d.continuity?.k ?? null;
  const quality = (q: number | null | undefined) => (q === null || q === undefined ? "Unknown" : `${percent(q)} of its ${k === null ? "" : `${k} `}nearest neighbours are preserved`);

  return (
    <Frame
      title="Career Atlas"
      lead="A two-dimensional picture of a many-dimensional skill space. It is useful for orientation and for asking questions. It is not a measure of fit, and distance on the map is not a chance of being hired."
    >
      <Banner tone="warning" title="This map distorts the data. Read this before trusting any position." role="note" data-testid="distortion-banner">
        <ul>
          {d.cumulative_explained_variance !== null ? (
            <li data-testid="explained-variance">
              The two axes keep <strong>{percent(d.cumulative_explained_variance)}</strong> of the variance in the original {projection.input.n_dims === null ? "" : `${projection.input.n_dims}-dimensional `}skill
              vectors. The other {percent(1 - d.cumulative_explained_variance)} is not visible.
            </li>
          ) : (
            <li>These axes are hand-defined, so “variance explained” does not apply; they discard most of the skill detail on purpose.</li>
          )}
          <li data-testid="trustworthiness">
            Trustworthiness {d.trustworthiness ? `${d.trustworthiness.value.toFixed(2)} (k=${d.trustworthiness.k})` : "unknown"} · continuity{" "}
            {d.continuity ? `${d.continuity.value.toFixed(2)} (k=${d.continuity.k})` : "unknown"}. 1.00 would mean neighbourhoods are perfectly preserved; lower means points that look
            close may not be, and points that are close may look far apart.
          </li>
          {d.knn_recall ? (
            <li>
              On average {percent(d.knn_recall.value)} of a point’s {d.knn_recall.k} nearest map neighbours are true nearest neighbours in the original space.
            </li>
          ) : null}
          {[...(d.flags ?? []), ...projection.warnings].map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      </Banner>
      {projection.synthetic_only === false ? (
        <Banner tone="warning" title="Provenance of the fitted postings is not verified as synthetic." data-testid="atlas-provenance">
          The analytics lane reports that not every fitted posting declares the source “synthetic”. This build accepts synthetic data only; treat the map as unverified.
        </Banner>
      ) : null}
      {projection.unplaced && projection.unplaced.length > 0 ? (
        <Banner tone="info" title={`${projection.unplaced.length} item${projection.unplaced.length === 1 ? " is" : "s are"} not shown on the map.`} data-testid="atlas-unplaced">
          Unknown is not the centre of the map, so these were left off rather than drawn at the origin.
          <ul className="ct-small">
            {projection.unplaced.slice(0, 20).map((u, i) => (
              <li key={`${u.entity_id ?? "item"}-${i}`}>
                {u.entity_id ?? "Unnamed item"}: {u.reason}
              </li>
            ))}
            {projection.unplaced.length > 20 ? <li>and {projection.unplaced.length - 20} more</li> : null}
          </ul>
        </Banner>
      ) : null}

      <Card title="View and filters">
        <div className="app-toolbar app-atlas-toolbar">
          <SelectField label="Projection" value={projection.projection_id} onChange={(e) => { setProjectionId(e.target.value); setSelectedId(null); }} options={bundle.projections.map((p) => ({ value: p.projection_id, label: p.method === "pca" ? "PCA (principal components)" : p.method === "interpretable-axes" ? "Interpretable axes" : p.method.toUpperCase() }))} data-testid="atlas-projection" />
          <SelectField label="Candidate evidence basis" value={evidenceBasis} onChange={(e) => setEvidenceBasis(e.target.value as "all" | "supported")} options={[{ value: "all", label: "All non-contradicted claims" }, { value: "supported", label: "Supported claims only" }]} hint="Moves the candidate point to where the evidence alone puts it." data-testid="atlas-evidence" />
          <div className="ct-field">
            <label className="ct-field__label" htmlFor="atlas-quality">
              Hide points with neighbourhood quality below {percent(minQuality)}
            </label>
            <input id="atlas-quality" type="range" min={0} max={1} step={0.2} value={minQuality} onChange={(e) => setMinQuality(Number(e.target.value))} data-testid="atlas-quality" />
          </div>
        </div>
        <fieldset className="ct-stack">
          <legend className="ct-field__label">Show</legend>
          <div className="ct-row">
            {REPRESENTATION_TYPES.map((t) => (
              <Checkbox key={t} label={TYPE_LABEL[t]} checked={types.includes(t)} onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))} data-testid={`atlas-type-${t}`} />
            ))}
            <Checkbox label="Movement paths" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} data-testid="atlas-paths" />
            <Checkbox label="Labels" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
          </div>
        </fieldset>
        <fieldset className="ct-stack">
          <legend className="ct-field__label">Role families</legend>
          <div className="ct-row">
            {view.families.map((f) => (
              <Checkbox key={f} label={familyLabel(f)} checked={!hiddenFamilies.includes(f)} onChange={(e) => setHiddenFamilies(e.target.checked ? hiddenFamilies.filter((x) => x !== f) : [...hiddenFamilies, f])} data-testid={`atlas-family-${f}`} />
            ))}
          </div>
        </fieldset>
        <p role="status" data-testid="atlas-count">
          Showing {visible.length} of {projection.points.length} points
        </p>
      </Card>

      <div className="app-atlas">
        <Card title={projection.method === "pca" ? "PCA projection" : "Interpretable axes"}>
          <div className="app-legend" aria-hidden="true">
            <span><i className="app-swatch app-swatch--job" /> Posting</span>
            <span><i className="app-swatch app-swatch--centroid" /> Role-family centroid</span>
            <span><i className="app-swatch app-swatch--candidate" /> Candidate</span>
            <span><i className="app-swatch app-swatch--scenario" /> Hypothetical scenario</span>
            <span className="ct-muted">Faded: low neighbourhood quality. Dashed line: hypothetical plan.</span>
          </div>
          <div className="app-legend app-legend--families" aria-hidden="true">
            {view.families.map((f, i) => (
              <span key={f}><i className={`app-swatch app-swatch--fam cat-${(i % 9) + 1}`} /> {familyLabel(f)}</span>
            ))}
          </div>
          <p className="ct-small ct-muted" id="atlas-help">
            Keyboard: Tab into the plot, arrow keys move between points, Enter selects, Escape clears. Every point is also listed in the table below.
          </p>
          {visible.length === 0 ? (
            <EmptyState title="No points match these filters." />
          ) : (
            <div className="app-plotwrap">
              <svg className="app-plot" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Scatter plot of ${visible.length} points`} aria-describedby="atlas-help" data-testid="atlas-plot">
                <defs>
                  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
                  </marker>
                </defs>
                {view.xTicks.map((t) => (
                  <g key={`x${t}`}>
                    <line className="grid" x1={view.sx(t)} x2={view.sx(t)} y1={PAD.top} y2={H - PAD.bottom} />
                    <text x={view.sx(t)} y={H - PAD.bottom + 16} textAnchor="middle">{t}</text>
                  </g>
                ))}
                {view.yTicks.map((t) => (
                  <g key={`y${t}`}>
                    <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={view.sy(t)} y2={view.sy(t)} />
                    <text x={PAD.left - 8} y={view.sy(t) + 4} textAnchor="end">{t}</text>
                  </g>
                ))}
                <line className="axis" x1={PAD.left} x2={W - PAD.right} y1={H - PAD.bottom} y2={H - PAD.bottom} />
                <line className="axis" x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={H - PAD.bottom} />
                <text x={(W + PAD.left - PAD.right) / 2} y={H - 10} textAnchor="middle">{projection.axes[0]?.label}</text>
                <text x={14} y={(H + PAD.top - PAD.bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(H + PAD.top - PAD.bottom) / 2})`}>{projection.axes[1]?.label}</text>

                {showPaths
                  ? projection.paths.map((path) => {
                      const to = pointById.get(path.to_entity_id);
                      const from = pointById.get(path.from_entity_id);
                      if (!to || !from || !visibleIds.has(path.to_entity_id) || !visibleIds.has(path.from_entity_id)) return null;
                      return <line key={path.id} className="path" x1={view.sx(from.coords[0]!)} y1={view.sy(from.coords[1]!)} x2={view.sx(to.coords[0]!)} y2={view.sy(to.coords[1]!)} markerEnd="url(#arrow)" data-testid={`path-${path.scenario_id}`} />;
                    })
                  : null}

                {visible.map((p) => {
                  const x = view.sx(p.coords[0]!);
                  const y = view.sy(p.coords[1]!);
                  const low = p.neighborhood_quality !== null && p.neighborhood_quality !== undefined && p.neighborhood_quality < 0.4;
                  const isSelected = p.entity_id === selectedId;
                  const fam = view.entityById.get(p.entity_id)?.role_family ?? null;
                  const famIdx = fam === null || p.representation_type === "scenario" || p.representation_type === "candidate" ? -1 : view.families.indexOf(fam);
                  return (
                    <g
                      key={p.entity_id}
                      ref={(el) => {
                        if (el) pointRefs.current.set(p.entity_id, el);
                        else pointRefs.current.delete(p.entity_id);
                      }}
                      role="button"
                      tabIndex={p.entity_id === tabStop ? 0 : -1}
                      aria-pressed={isSelected}
                      aria-label={`${TYPE_LABEL[p.representation_type]}: ${p.label}. Neighbourhood quality ${quality(p.neighborhood_quality)}.`}
                      onClick={() => setSelectedId(isSelected ? null : p.entity_id)}
                      onKeyDown={(e) => onKey(e, p)}
                      onMouseEnter={() => setHoverId(p.entity_id)}
                      onMouseLeave={() => setHoverId(null)}
                      onFocus={() => setFocusId(p.entity_id)}
                      onBlur={() => setFocusId(null)}
                      data-testid={`pt-${p.entity_id}`}
                    >
                      {/* Larger transparent hit target for touch and pointer users. */}
                      <circle cx={x} cy={y} r={14} fill="transparent" />
                      <Shape type={p.representation_type} x={x} y={y} className={["pt", TYPE_CLASS[p.representation_type], famIdx >= 0 ? `cat-${(famIdx % 9) + 1}` : "", low ? "pt--low" : "", isSelected ? "pt--selected" : ""].join(" ")} />
                      {showLabels || isSelected ? <text x={x + 10} y={y - 8}>{truncate(p.label, 34)}</text> : null}
                    </g>
                  );
                })}
              </svg>
              {active ? (
                <div className="app-tooltip" role="presentation" style={{ left: `${(view.sx(active.coords[0]!) / W) * 100}%`, top: `${(view.sy(active.coords[1]!) / H) * 100}%`, transform: "translate(12px, 12px)" }} data-testid="atlas-tooltip">
                  {truncate(active.label, 80)}
                  <br />
                  {TYPE_LABEL[active.representation_type]} · quality {active.neighborhood_quality === null || active.neighborhood_quality === undefined ? "unknown" : percent(active.neighborhood_quality)}
                </div>
              ) : null}
            </div>
          )}
          <Facts items={projection.axes.slice(0, 2).map((a, i) => ({ label: i === 0 ? "Horizontal axis" : "Vertical axis", value: `${a.label}${a.explained_variance_ratio !== null ? ` (${percent(a.explained_variance_ratio)} of variance)` : ""}. ${a.description}` }))} />
        </Card>

        <Card title="Selected point" aria-label="Selected point detail" data-testid="atlas-detail">
          {selected && selectedEntity ? (
            <div className="ct-stack">
              <h3>{selected.label}</h3>
              <div className="ct-row">
                <Badge tone={selected.representation_type === "scenario" ? "synthetic" : "info"}>{TYPE_LABEL[selected.representation_type]}</Badge>
                {selectedEntity.role_family ? <Badge>{familyLabel(selectedEntity.role_family)}</Badge> : null}
              </div>
              <Facts
                items={[
                  { label: "About", value: selectedEntity.detail },
                  { label: "Position", value: `(${selected.coords[0]!.toFixed(2)}, ${selected.coords[1]!.toFixed(2)})` },
                  { label: "Neighbourhood quality", value: quality(selected.neighborhood_quality) },
                  { label: "Evidence basis", value: selectedEntity.evidence_basis === "not-applicable" ? "Not applicable" : selectedEntity.evidence_basis === "mixed" ? "Mixed: supported, self-reported and unverified claims" : selectedEntity.evidence_basis },
                  { label: "Source vector", value: <code>{selected.source_vector_ref}</code> },
                ]}
              />
              <SkillChips skills={selectedEntity.top_skills} />
              {(selected.warnings ?? []).length > 0 ? (
                <Banner tone="warning" title="Warnings for this point">
                  <ul>{(selected.warnings ?? []).map((w) => <li key={w}>{w}</li>)}</ul>
                </Banner>
              ) : null}
              {selectedEntity.job_id ? <Link className="ct-btn" to={`/matching?job=${encodeURIComponent(selectedEntity.job_id)}`}>See the match explanation</Link> : null}
              {selectedEntity.scenario_id ? <Link className="ct-btn" to={`/simulator?scenario=${encodeURIComponent(selectedEntity.scenario_id)}`}>Open this scenario in the simulator</Link> : null}
              <p className="ct-small ct-muted">For fit, use the match explanation. This map position is not a score.</p>
            </div>
          ) : (
            <p className="ct-muted">Select a point on the plot or in the table to see what it is, how reliable its position is, and where to go next.</p>
          )}
        </Card>
      </div>

      <Card title="All visible points (table view)">
        <details open className="app-atlas-table">
        <summary>Show or hide the table ({visible.length} rows)</summary>
        <TableRegion label="Atlas points table">
          <table className="ct-table" data-testid="atlas-table">
            <thead>
              <tr>
                <th scope="col">Point</th>
                <th scope="col">Type</th>
                <th scope="col">Role family</th>
                <th scope="col" className="ct-num">{projection.axes[0]?.label}</th>
                <th scope="col" className="ct-num">{projection.axes[1]?.label}</th>
                <th scope="col" className="ct-num">Neighbourhood quality</th>
                <th scope="col">Select</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <tr key={p.entity_id} aria-selected={p.entity_id === selectedId}>
                  <th scope="row">{truncate(p.label, 70)}</th>
                  <td>{TYPE_LABEL[p.representation_type]}</td>
                  <td>{view.entityById.get(p.entity_id)?.role_family ? familyLabel(view.entityById.get(p.entity_id)!.role_family) : "—"}</td>
                  <td className="ct-num">{p.coords[0]!.toFixed(2)}</td>
                  <td className="ct-num">{p.coords[1]!.toFixed(2)}</td>
                  <td className="ct-num">{p.neighborhood_quality === null || p.neighborhood_quality === undefined ? "Unknown" : percent(p.neighborhood_quality)}</td>
                  <td>
                    <button type="button" className="ct-btn ct-btn--sm" aria-pressed={p.entity_id === selectedId} onClick={() => setSelectedId(p.entity_id === selectedId ? null : p.entity_id)} aria-label={`${p.entity_id === selectedId ? "Selected" : "Select"}: ${truncate(p.label, 60)}`}>
                      {p.entity_id === selectedId ? "Selected" : "Select"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
        </details>
        <p className="ct-small ct-muted">A 3-D view is deliberately not offered: with this little variance captured, a third axis adds visual complexity without making positions more trustworthy.</p>
      </Card>
    </Frame>
  );
}

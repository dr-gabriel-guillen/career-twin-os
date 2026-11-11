import { useMemo, useState, type ReactNode } from "react";
import type { AtlasProjection } from "@/adapters/proposed";

/** Score 0..100 as a bar. Null is drawn as a hatched "unknown" track, never as zero. */
export function ScoreBar({ value, label, className = "" }: { value: number | null; label: string; className?: string }) {
  return (
    <div className={`sb ${className}`} role="img" aria-label={value === null ? `${label}: not computed` : `${label}: ${Math.round(value)} of 100`}>
      <div className={`sb-track ${value === null ? "sb-unknown" : ""}`}>
        {value !== null && <div className="sb-fill" style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />}
      </div>
    </div>
  );
}

export interface AtlasPointView {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: string;
  family: string | null;
  quality: number | null;
  detail: string;
  skills: string[];
}

export function atlasPoints(p: AtlasProjection): AtlasPointView[] {
  const ent = new Map(p.entities.map((e) => [e.entity_id, e]));
  return p.points.map((pt) => {
    const e = ent.get(pt.entity_id);
    return {
      id: pt.entity_id,
      label: pt.label,
      x: pt.coords[0] ?? 0,
      y: pt.coords[1] ?? 0,
      kind: pt.representation_type,
      family: e?.role_family ?? null,
      quality: pt.neighborhood_quality ?? null,
      detail: e?.detail ?? "",
      skills: e?.top_skills ?? [],
    };
  });
}

export interface ScatterProps {
  projection: AtlasProjection;
  colorFor: (family: string | null) => string;
  width: number;
  height: number;
  selected: string | null;
  onSelect: (id: string) => void;
  showLabels?: boolean;
  grid?: "lines" | "contours" | "none";
  pointRadius?: number;
}

/** Keyboard-operable scatter: Tab into the plot, arrows move, Enter selects. */
export function Scatter({ projection, colorFor, width, height, selected, onSelect, showLabels = false, grid = "lines", pointRadius = 5 }: ScatterProps) {
  const pts = useMemo(() => atlasPoints(projection), [projection]);
  const [focus, setFocus] = useState(0);
  const [hasFocus, setHasFocus] = useState(false);
  const pad = 28;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)], [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x: number) => pad + ((x - x0) / (x1 - x0 || 1)) * (width - 2 * pad);
  const sy = (y: number) => height - pad - ((y - y0) / (y1 - y0 || 1)) * (height - 2 * pad);
  const byId = new Map(pts.map((p) => [p.id, p]));
  const order = [...pts].sort((a, b) => a.x - b.x);

  const contours: ReactNode[] = [];
  if (grid === "contours") {
    const cand = pts.find((p) => p.kind === "candidate");
    if (cand) for (let r = 40; r < Math.max(width, height); r += 46) contours.push(<circle key={r} cx={sx(cand.x)} cy={sy(cand.y)} r={r} className="sc-contour" />);
  }

  return (
    <svg
      className="sc"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="application"
      aria-label={`${projection.method.toUpperCase()} map of ${pts.length} points. Arrow keys move between points, Enter selects.`}
      tabIndex={0}
      onFocus={() => setHasFocus(true)}
      onBlur={() => setHasFocus(false)}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); setFocus((f) => Math.min(order.length - 1, f + 1)); }
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); setFocus((f) => Math.max(0, f - 1)); }
        if (e.key === "Enter") onSelect(order[focus]!.id);
      }}
    >
      {grid === "lines" && [0.25, 0.5, 0.75].map((t) => (
        <g key={t}>
          <line x1={pad} x2={width - pad} y1={pad + t * (height - 2 * pad)} y2={pad + t * (height - 2 * pad)} className="sc-grid" />
          <line y1={pad} y2={height - pad} x1={pad + t * (width - 2 * pad)} x2={pad + t * (width - 2 * pad)} className="sc-grid" />
        </g>
      ))}
      {contours}
      {projection.paths.map((path) => {
        const a = byId.get(path.from_entity_id), b = byId.get(path.to_entity_id);
        if (!a || !b) return null;
        return <line key={path.id} x1={sx(a.x)} y1={sy(a.y)} x2={sx(b.x)} y2={sy(b.y)} className="sc-path" />;
      })}
      {pts.map((p, i) => {
        const cx = sx(p.x), cy = sy(p.y);
        const low = p.quality !== null && p.quality < 0.5;
        const isSel = selected === p.id;
        const isFocus = hasFocus && order[focus]?.id === p.id;
        const fill = p.kind === "candidate" ? "var(--sc-candidate)" : colorFor(p.family);
        const common = { className: `sc-pt sc-${p.kind} ${low ? "sc-low" : ""} ${isSel ? "sc-sel" : ""}`, onClick: () => onSelect(p.id) };
        return (
          <g key={p.id + i} {...common}>
            <title>{`${p.label} (${p.kind})${p.quality !== null ? ` · neighbourhood kept ${Math.round(p.quality * 100)}%` : ""}`}</title>
            {p.kind === "role-centroid" ? (
              <rect x={cx - pointRadius} y={cy - pointRadius} width={pointRadius * 2} height={pointRadius * 2} transform={`rotate(45 ${cx} ${cy})`} fill="none" stroke={fill} strokeWidth={2} />
            ) : p.kind === "scenario" ? (
              <circle cx={cx} cy={cy} r={pointRadius} fill="var(--sc-bg)" stroke={fill} strokeWidth={2} strokeDasharray="2 2" />
            ) : (
              <circle cx={cx} cy={cy} r={p.kind === "candidate" ? pointRadius + 3 : pointRadius} fill={fill} />
            )}
            {(isSel || isFocus) && <circle cx={cx} cy={cy} r={pointRadius + 6} className={isFocus ? "sc-focus" : "sc-ring"} />}
            {(showLabels && (p.kind === "candidate" || p.kind === "role-centroid")) || isSel ? (
              <text x={cx > width - 170 ? cx - pointRadius - 6 : cx + pointRadius + 6} y={cy + 4} textAnchor={cx > width - 170 ? "end" : "start"} className="sc-label">{shortLabel(p)}</text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

export function shortLabel(p: AtlasPointView): string {
  if (p.kind === "candidate") return /supported claims only/.test(p.label) ? "You: supported claims only" : "You: all usable claims";
  if (p.kind === "scenario") return p.label.replace(/^Hypothetical: after '(.*)' plan$/, "Plan: $1");
  return p.label.replace(/ \(centroid\)$/, "");
}

export function useAtlas(projections: AtlasProjection[]) {
  const pca = projections.find((p) => p.method === "pca") ?? projections[0] ?? null;
  return pca;
}

export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);

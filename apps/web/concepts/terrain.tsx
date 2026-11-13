import { useMemo, useState } from "react";
import { Icon } from "./icons";
import type { Screen } from "./instrument";
import { nextActions, skillLabel, type ConceptModel } from "./model";
import { atlasPoints, pct, ScoreBar, Scatter, shortLabel, useAtlas } from "./shared";
import "./terrain.css";

const NAV: [string, string][] = [["atlas", "Map"], ["discover", "Postings"], ["match", "Matching"], ["evidence", "Evidence"], ["resume", "Résumés"], ["applications", "Applications"], ["experiments", "Experiments"], ["simulator", "Routes"], ["settings", "Settings"]];
const ACTIVE: Record<Screen, string> = { dashboard: "atlas", discover: "discover", atlas: "atlas" };
const PALETTE = ["#7fc8b5", "#e2b04a", "#9fb4ff", "#e48a6f", "#b9d27a", "#d79ad6", "#8fa3a0", "#f0d27a", "#76b3d6"];

export function Terrain({ model, screen }: { model: ConceptModel; screen: Screen }) {
  return (
    <div className="tr">
      <header className="tr-bar">
        <div className="tr-brand"><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor" /></svg> CareerTwin</div>
        <nav aria-label="Primary">{NAV.map(([k, l]) => <a key={k} href="#" className={ACTIVE[screen] === k ? "on" : ""}><Icon name={k} size={16} />{l}</a>)}</nav>
        <span className="tr-flag">Synthetic data, offline</span>
      </header>
      {screen === "dashboard" && <Home model={model} />}
      {screen === "discover" && <Postings model={model} />}
      {screen === "atlas" && <MapView model={model} />}
    </div>
  );
}

function useMap(model: ConceptModel) {
  const p = useAtlas(model.bundle.projections);
  const pts = useMemo(() => (p ? atlasPoints(p) : []), [p]);
  const fams = [...new Set(pts.map((x) => x.family).filter(Boolean))] as string[];
  const color = (f: string | null) => (f ? PALETTE[fams.indexOf(f) % PALETTE.length]! : "#6f817c");
  return { p, pts, fams, color };
}

function Routes({ model }: { model: ConceptModel }) {
  return (
    <ul className="tr-routes">
      {model.bundle.scenarios.map(({ scenario: s, outputs: o }) => {
        const hours = s.planned_actions.reduce((n, a) => n + a.effort_hours, 0);
        return (
          <li key={s.id}>
            <div className="tr-route-h"><strong>{s.target_role}</strong><span className={`tr-unc ${o?.uncertainty ?? "high"}`}>{o?.uncertainty ?? "unknown"} uncertainty</span></div>
            <div className="tr-cov">
              <span>Target skills covered</span>
              <span className="tr-cov-v">{pct(o?.coverage_before)} <Icon name="arrow" size={13} /> {pct(o?.coverage_after)}</span>
            </div>
            <p>{s.planned_actions.map((a) => `${skillLabel(a.skill)} ${a.effort_hours}h`).join(", ")} over {s.time_horizon_months} months ({hours} hours). Hypothetical.</p>
          </li>
        );
      })}
    </ul>
  );
}

function Home({ model }: { model: ConceptModel }) {
  const { p, fams, color } = useMap(model);
  const actions = nextActions(model).slice(0, 4);
  const near = model.rows.filter((r) => r.near !== null && !r.failedConstraints.length).slice(0, 4);
  if (!p) return null;
  return (
    <main className="tr-home" id="main">
      <section className="tr-stage" aria-label="Your position">
        <div className="tr-overlay tl">
          <h1>Where you stand</h1>
          <p>The white marks are you: one drawn from every usable claim, one from evidence-backed claims only. Dashed lines lead to hypothetical plans for your target roles.</p>
          <p className="tr-warn"><Icon name="alert" size={14} />This drawing keeps {pct(p.diagnostics.cumulative_explained_variance)} of the skill differences. Distance is not fit.</p>
        </div>
        <Scatter projection={p} colorFor={color} width={1000} height={580} selected={null} onSelect={() => {}} showLabels grid="contours" pointRadius={5} />
        <ul className="tr-overlay bl tr-legend">{fams.map((f) => <li key={f}><i style={{ background: color(f) }} />{model.familyLabel(f)}</li>)}</ul>
      </section>
      <aside className="tr-side">
        <section><h2>Routes to your target roles</h2><Routes model={model} /></section>
        <section><h2>Next steps</h2><ol className="tr-steps">{actions.map((a, i) => <li key={i}><strong>{a.title}</strong><span>{a.detail}</span></li>)}</ol></section>
        <section><h2>Closest postings you can act on</h2>
          <ul className="tr-near">{near.map((r) => <li key={r.job.id}><div><strong>{r.job.title}</strong><span>{r.job.organization}</span></div><div className="tr-sc"><span>{Math.round(r.near ?? 0)}</span><ScoreBar value={r.near} label="Near-term fit" /></div></li>)}</ul>
        </section>
      </aside>
    </main>
  );
}

function Postings({ model }: { model: ConceptModel }) {
  const { p, color } = useMap(model);
  const [sel, setSel] = useState(model.rows[0]?.job.id ?? null);
  const r = model.rows.find((x) => x.job.id === sel)!;
  if (!p) return null;
  return (
    <main className="tr-post" id="main">
      <section className="tr-list" aria-label="Postings">
        <div className="tr-list-h"><h1>Postings</h1><input placeholder="Filter by title, employer, skill" aria-label="Filter postings" /></div>
        <ul>
          {model.rows.map((x) => (
            <li key={x.job.id}><button className={x.job.id === sel ? "on" : ""} onClick={() => setSel(x.job.id)}>
              <span className="tr-dot" style={{ background: color(x.job.role_family ?? null) }} />
              <span className="tr-t"><strong>{x.job.title}</strong><span>{x.job.organization}</span></span>
              <span className="tr-n">{x.near === null ? "–" : Math.round(x.near)}<small>now</small></span>
              <span className="tr-n">{x.strategic === null ? "–" : Math.round(x.strategic)}<small>later</small></span>
            </button></li>
          ))}
        </ul>
      </section>
      <section className="tr-right">
        <div className="tr-mini"><Scatter projection={p} colorFor={color} width={640} height={300} selected={sel} onSelect={setSel} grid="contours" pointRadius={4} /></div>
        <article className="tr-job">
          <p className="tr-meta">{r.job.organization}, {r.family}, {r.job.remote_policy ?? "work style unknown"}</p>
          <h2>{r.job.title}</h2>
          <div className="tr-two">
            <div><span>Fit now</span><b>{r.near === null ? "unknown" : Math.round(r.near)}</b><ScoreBar value={r.near} label="Near-term fit" /></div>
            <div><span>Strategic value</span><b>{r.strategic === null ? "unknown" : Math.round(r.strategic)}</b><ScoreBar value={r.strategic} label="Strategic value" className="strat" /></div>
          </div>
          <p>{r.comp.text}. {r.fresh.label}. {r.failedConstraints.length ? `Fails your ${r.failedConstraints.join(", ").toLowerCase()} constraint.` : "Passes your hard constraints."}</p>
          <p className="tr-quiet">Missing evidence: {r.missingSkills.map(skillLabel).join(", ") || "none"}. Scores are relative heuristics, not a probability of being hired.</p>
          <div className="tr-cta"><button>Shortlist</button><button>Tailor a résumé</button><button>Plan a route</button></div>
        </article>
      </section>
    </main>
  );
}

function MapView({ model }: { model: ConceptModel }) {
  const { p, pts, fams, color } = useMap(model);
  const [sel, setSel] = useState<string | null>(pts.find((x) => x.kind === "candidate")?.id ?? null);
  if (!p) return null;
  const s = pts.find((x) => x.id === sel);
  return (
    <main className="tr-home" id="main">
      <section className="tr-stage" aria-label="Career map">
        <div className="tr-overlay tl"><h1>Career map</h1><p className="tr-warn"><Icon name="alert" size={14} />{pct(p.diagnostics.cumulative_explained_variance)} of variance shown. Trustworthiness {p.diagnostics.trustworthiness?.value.toFixed(2)}, continuity {p.diagnostics.continuity?.value.toFixed(2)}. Distance is not fit.</p></div>
        <Scatter projection={p} colorFor={color} width={1000} height={600} selected={sel} onSelect={setSel} showLabels grid="contours" pointRadius={5} />
        <ul className="tr-overlay bl tr-legend">{fams.map((f) => <li key={f}><i style={{ background: color(f) }} />{model.familyLabel(f)}</li>)}<li><i className="c" />You</li><li><i className="d" />Role centre</li></ul>
      </section>
      <aside className="tr-side">
        <section>
          <h2>Layers</h2>
          <ul className="tr-layers">{["Postings", "Role centres", "Your positions", "Hypothetical routes", "Labels"].map((l) => <li key={l}><label><input type="checkbox" defaultChecked /> {l}</label></li>)}</ul>
        </section>
        {s && <section><h2>{shortLabel(s)}</h2><p>{s.detail}</p><div className="tr-two one"><div><span>Neighbourhood kept</span><b>{pct(s.quality)}</b><ScoreBar value={s.quality === null ? null : s.quality * 100} label="Neighbourhood kept" /></div></div>
          {s.skills.length > 0 && <p className="tr-quiet">Top skills: {s.skills.map(skillLabel).join(", ")}</p>}</section>}
        <section><h2>Routes</h2><Routes model={model} /></section>
      </aside>
    </main>
  );
}

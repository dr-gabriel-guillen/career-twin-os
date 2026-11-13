import { useMemo, useState } from "react";
import { formatDate } from "@/lib/format";
import type { Screen } from "./instrument";
import { nextActions, skillLabel, VERDICT_TEXT, type ConceptModel, type JobRow } from "./model";
import { atlasPoints, pct, Scatter, shortLabel, useAtlas } from "./shared";
import "./ledger.css";

const SECTIONS = ["Brief", "Postings", "Matching", "Evidence", "Résumés", "Applications", "Experiments", "Atlas", "Scenarios", "Settings"];
const ACTIVE: Record<Screen, string> = { dashboard: "Brief", discover: "Postings", atlas: "Atlas" };

export function Ledger({ model, screen }: { model: ConceptModel; screen: Screen }) {
  return (
    <div className="lg">
      <header className="lg-mast">
        <div className="lg-title">CareerTwin <span>dossier</span></div>
        <nav aria-label="Sections">{SECTIONS.map((s) => <a key={s} href="#" className={ACTIVE[screen] === s ? "on" : ""} aria-current={ACTIVE[screen] === s ? "page" : undefined}>{s}</a>)}</nav>
        <div className="lg-edition">Snapshot of {formatDate(model.clock)}. Synthetic records only.</div>
      </header>
      {screen === "dashboard" && <Brief model={model} />}
      {screen === "discover" && <Postings model={model} />}
      {screen === "atlas" && <AtlasFigure model={model} />}
    </div>
  );
}

function Num({ v }: { v: number | null }) {
  return <span className={`lg-num ${v === null ? "unk" : ""}`}>{v === null ? "unknown" : Math.round(v)}</span>;
}

function Brief({ model }: { model: ConceptModel }) {
  const actions = nextActions(model);
  const open = model.applications.filter((a) => a.app.stage !== "closed");
  const silent = model.applications.filter((a) => a.silentDays !== null);
  const c = model.claimCounts;
  const best = model.rows.find((r) => r.near !== null && !r.failedConstraints.length);
  const stratBest = [...model.rows].filter((r) => r.strategic !== null).sort((a, b) => (b.strategic ?? 0) - (a.strategic ?? 0))[0];
  return (
    <main className="lg-page" id="main">
      <article className="lg-col">
        <h1>The brief</h1>
        <p className="lg-lede">
          You have <strong>{open.length} open applications</strong>, {silent.length} of them silent for more than two weeks<sup>1</sup>.
          Of {c.total} claims in your vault, {c.supported} are backed by evidence<sup>2</sup>. The strongest near-term fit you have not acted on is
          {" "}<em>{best?.job.title}</em> at {best?.job.organization}<sup>3</sup>; the posting with the most strategic value is
          {" "}<em>{stratBest?.job.title}</em> at {stratBest?.job.organization}, where your near-term fit is {stratBest?.near === null ? "unknown" : Math.round(stratBest?.near ?? 0)}<sup>4</sup>.
        </p>
        <h2>Act on</h2>
        <ol className="lg-items">
          {actions.map((a, i) => <li key={i}><strong>{a.title}.</strong> {a.detail}</li>)}
        </ol>
        <h2>The shortlist, in the engine’s words</h2>
        <table className="lg-table">
          <thead><tr><th scope="col">Posting</th><th scope="col" className="r">Fit now</th><th scope="col" className="r">Strategic</th><th scope="col">Pay</th></tr></thead>
          <tbody>
            {model.rows.filter((r) => r.near !== null).slice(0, 7).map((r) => (
              <tr key={r.job.id}>
                <td><span className="lg-job">{r.job.title}</span><span className="lg-org">{r.job.organization}{r.failedConstraints.length ? `, fails ${r.failedConstraints[0]?.toLowerCase()}` : ""}</span></td>
                <td className="r"><Num v={r.near} /></td><td className="r"><Num v={r.strategic} /></td>
                <td className={r.comp.known ? "" : "lg-quiet"}>{r.comp.known ? r.comp.text.split(" · ")[0] : "not disclosed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </article>
      <aside className="lg-margin" aria-label="Notes">
        <ol className="lg-notes">
          <li><sup>1</sup>No reply is recorded as unknown, not as a rejection. Silent: {silent.map((s) => s.row?.job.organization).join(", ") || "none"}.</li>
          <li><sup>2</sup>{c.selfReported} self-reported, {c.unverified} unverified, {c.contradicted} contradicted. Only supported and self-reported claims can appear in a résumé.</li>
          <li><sup>3</sup>Near-term fit {best?.near}/100. Relative heuristic, not a probability of being hired.</li>
          <li><sup>4</sup>Strategic value {stratBest?.strategic}/100, from your stated target roles: {model.targetRoles.join(", ")}.</li>
        </ol>
        <figure className="lg-fig">
          <figcaption>Pipeline, by stage</figcaption>
          <ul className="lg-bars">
            {model.stageCounts.map((s) => <li key={s.stage}><span>{s.label}</span><i style={{ width: `${s.count * 22}px` }} /><b>{s.count}</b></li>)}
          </ul>
        </figure>
        <figure className="lg-fig">
          <figcaption>Experiments</figcaption>
          <ul className="lg-plain">{model.experiments.map((e) => <li key={e.id}>{e.title}: <em>{e.verdict ? VERDICT_TEXT[e.verdict] : e.status}</em></li>)}</ul>
        </figure>
      </aside>
    </main>
  );
}

function Postings({ model }: { model: ConceptModel }) {
  const [open, setOpen] = useState<string | null>(model.rows[0]?.job.id ?? null);
  return (
    <main className="lg-page wide" id="main">
      <article className="lg-col wide">
        <h1>Postings</h1>
        <p className="lg-lede short">{model.rows.length} fictional postings, ordered by near-term fit. Each opens into the engine’s reasoning, with the evidence it relied on.</p>
        <div className="lg-filter"><label>Search<input placeholder="title, employer, skill" /></label><label><input type="checkbox" /> Only postings that pass my constraints</label><label><input type="checkbox" /> Only disclosed pay</label></div>
        <table className="lg-table big">
          <thead><tr><th scope="col">Posting</th><th scope="col">Work</th><th scope="col" className="r">Fit now</th><th scope="col" className="r">Strategic</th><th scope="col">Pay</th><th scope="col">Captured</th></tr></thead>
          <tbody>
            {model.rows.slice(0, 9).map((r) => (
              <Row key={r.job.id} r={r} open={open === r.job.id} onToggle={() => setOpen(open === r.job.id ? null : r.job.id)} />
            ))}
          </tbody>
        </table>
      </article>
    </main>
  );
}

function Row({ r, open, onToggle }: { r: JobRow; open: boolean; onToggle: () => void }) {
  const m = r.match;
  return (
    <>
      <tr className={open ? "open" : ""}>
        <td><button className="lg-link" aria-expanded={open} onClick={onToggle}><span className="lg-job">{r.job.title}</span></button><span className="lg-org">{r.job.organization}, {r.family}</span></td>
        <td className="cap">{r.job.remote_policy ?? "unknown"}</td>
        <td className="r"><Num v={r.near} /></td><td className="r"><Num v={r.strategic} /></td>
        <td className={r.comp.known ? "" : "lg-quiet"}>{r.comp.known ? r.comp.text.split(" · ")[0] : "not disclosed"}</td>
        <td className={r.fresh.level === "stale" ? "lg-warn" : ""}>{r.fresh.days} days before</td>
      </tr>
      {open && m && (
        <tr className="lg-dossier"><td colSpan={6}>
          <div className="lg-dgrid">
            <div>
              <h3>Why it ranks here</h3>
              {([["Near-term fit", m.near_term_fit], ["Strategic value", m.strategic_value]] as const).map(([t, g]) => (
                <dl key={t} className="lg-comp"><dt>{t} <b>{g.score === null ? "unknown" : Math.round(g.score)}</b></dt>
                  {g.components.map((c) => <dd key={c.key}><span>{c.label}</span><span className="lg-dots"><i style={{ width: c.value === null ? 0 : `${c.value * 100}%` }} className={c.value === null ? "unk" : ""} /></span><span>{c.value === null ? "unknown" : c.weight === null ? "" : `weight ${Math.round(c.weight * 100)}%`}</span></dd>)}
                </dl>
              ))}
            </div>
            <div>
              <h3>Constraints</h3>
              <ul className="lg-plain">{m.hard_constraints.map((c) => <li key={c.key} className={c.status}><b>{c.status === "pass" ? "Passes" : c.status === "fail" ? "Fails" : "Unknown"}:</b> {c.label.toLowerCase()}. {c.detail}</li>)}</ul>
              <h3>Evidence</h3>
              <ul className="lg-plain">{m.skills.map((s) => <li key={s.skill}>{skillLabel(s.skill)}: {s.evidence_strength === "missing" ? <em>no claim covers it</em> : `${s.claim_ids.length} claim${s.claim_ids.length === 1 ? "" : "s"}, ${s.evidence_strength}`}</li>)}</ul>
              <p className="lg-quiet">Not a probability of being hired. The engine does not know: {m.unknown_inputs.map((u) => u.replace(/_/g, " ")).join("; ")}.</p>
            </div>
          </div>
        </td></tr>
      )}
    </>
  );
}

const INK = ["#22406e", "#2f6b4f", "#8a5a1f", "#6b3f78", "#4f6d2a", "#8a2d43", "#4c5a6e", "#7a6a12", "#2c6a86"];

function AtlasFigure({ model }: { model: ConceptModel }) {
  const p = useAtlas(model.bundle.projections);
  const pts = useMemo(() => (p ? atlasPoints(p) : []), [p]);
  const fams = [...new Set(pts.map((x) => x.family).filter(Boolean))] as string[];
  const color = (f: string | null) => (f ? INK[fams.indexOf(f) % INK.length]! : "#8b8f88");
  const [sel, setSel] = useState<string | null>(null);
  if (!p) return null;
  const s = pts.find((x) => x.id === sel);
  return (
    <main className="lg-page" id="main">
      <article className="lg-col">
        <h1>The atlas</h1>
        <p className="lg-lede short">A two-dimensional drawing of a {p.input.n_dims ?? "many"}-dimensional skill space. It is useful for orientation and for asking questions. It is not a measure of fit.</p>
        <figure className="lg-figure">
          <Scatter projection={p} colorFor={color} width={680} height={460} selected={sel} onSelect={setSel} showLabels grid="lines" pointRadius={4} />
          <figcaption><b>Figure 1.</b> Postings, role centres and your position projected onto the first two principal components ({pct(p.axes[0]?.explained_variance_ratio)} and {pct(p.axes[1]?.explained_variance_ratio)} of variance). Faded marks lose more than half of their true neighbours in the drawing<sup>1</sup>.</figcaption>
        </figure>
        {s && <p className="lg-sel"><b>{shortLabel(s)}.</b> {s.detail} Neighbourhood kept: {pct(s.quality)}.</p>}
      </article>
      <aside className="lg-margin" aria-label="Notes">
        <ol className="lg-notes">
          <li><sup>1</sup>The drawing keeps {pct(p.diagnostics.cumulative_explained_variance)} of the variance. Trustworthiness {p.diagnostics.trustworthiness?.value.toFixed(2)} and continuity {p.diagnostics.continuity?.value.toFixed(2)} at k={p.diagnostics.trustworthiness?.k}; 1.00 would mean neighbours are perfectly preserved.</li>
          <li><sup>2</sup>Two positions are drawn for you: one from every usable claim, one from evidence-backed claims only. The gap between them is how much of your position rests on self-reporting.</li>
        </ol>
        <figure className="lg-fig"><figcaption>Key</figcaption>
          <ul className="lg-key">{fams.map((f) => <li key={f}><i style={{ background: color(f) }} />{model.familyLabel(f)}</li>)}<li><i className="c" />You</li><li><i className="d" />Role centre</li><li><i className="h" />Hypothetical plan</li></ul>
        </figure>
      </aside>
    </main>
  );
}

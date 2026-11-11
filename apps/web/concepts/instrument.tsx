import { useMemo, useState } from "react";
import { formatDate } from "@/lib/format";
import { Icon } from "./icons";
import { nextActions, skillLabel, VERDICT_TEXT, type ConceptModel, type JobRow } from "./model";
import { atlasPoints, pct, ScoreBar, Scatter, shortLabel, useAtlas } from "./shared";
import "./instrument.css";

const NAV = [
  ["today", "Today"], ["discover", "Discover"], ["match", "Matching"], ["evidence", "Evidence"], ["resume", "Résumé studio"],
  ["applications", "Applications"], ["experiments", "Experiments"], ["atlas", "Career atlas"], ["simulator", "Simulator"], ["settings", "Settings"],
] as const;
const MOBILE_NAV = ["today", "discover", "applications", "atlas", "settings"];
const TAB_LABEL: Record<string, string> = { atlas: "Atlas", applications: "Pipeline" };

export type Screen = "dashboard" | "discover" | "atlas";
const ACTIVE: Record<Screen, string> = { dashboard: "today", discover: "discover", atlas: "atlas" };

export function Instrument({ model, screen }: { model: ConceptModel; screen: Screen }) {
  return (
    <div className="in">
      <aside className="in-rail" aria-label="Primary">
        <div className="in-brand"><span className="in-mark" aria-hidden="true" />CareerTwin</div>
        <nav>
          {NAV.map(([key, label]) => (
            <a key={key} href="#" className={ACTIVE[screen] === key ? "on" : ""} aria-current={ACTIVE[screen] === key ? "page" : undefined}>
              <Icon name={key} />{label}
            </a>
          ))}
        </nav>
        <div className="in-rail-foot">
          <span className="in-dot" aria-hidden="true" />Synthetic data, offline
        </div>
      </aside>
      <div className="in-main">
        <header className="in-top">
          <label className="in-search">
            <Icon name="discover" size={16} />
            <span className="sr-only">Search postings, claims and applications</span>
            <input placeholder="Search" aria-label="Search postings, claims and applications" />
            <kbd>/</kbd>
          </label>
          <span className="in-snap" title="All ages are measured from this fixed snapshot, not today.">
            <Icon name="clock" size={15} /> Snapshot {formatDate(model.clock)}
          </span>
          <span className="in-who">{model.candidateName}</span>
        </header>
        {screen === "dashboard" && <Dashboard model={model} />}
        {screen === "discover" && <Discover model={model} />}
        {screen === "atlas" && <Atlas model={model} />}
      </div>
      <nav className="in-tabs" aria-label="Primary (mobile)">
        {NAV.filter(([k]) => MOBILE_NAV.includes(k)).map(([key, label]) => (
          <a key={key} href="#" className={ACTIVE[screen] === key ? "on" : ""}><Icon name={key} size={20} />{TAB_LABEL[key] ?? label}</a>
        ))}
      </nav>
    </div>
  );
}

function ScorePair({ r, compact = false }: { r: JobRow; compact?: boolean }) {
  return (
    <div className={`in-pair ${compact ? "compact" : ""}`}>
      <div><span className="in-num">{r.near === null ? "—" : Math.round(r.near)}</span><ScoreBar value={r.near} label="Near-term fit" className="near" />{!compact && <span className="in-cap">Near-term fit</span>}</div>
      <div><span className="in-num">{r.strategic === null ? "—" : Math.round(r.strategic)}</span><ScoreBar value={r.strategic} label="Strategic value" className="strat" />{!compact && <span className="in-cap">Strategic value</span>}</div>
    </div>
  );
}

const ACTION_ICON: Record<string, string> = { "follow-up": "clock", evidence: "evidence", shortlist: "bookmark", experiment: "experiments", constraint: "alert" };

function Dashboard({ model }: { model: ConceptModel }) {
  const actions = nextActions(model);
  const top = model.rows.filter((r) => r.near !== null).slice(0, 6);
  const active = model.applications.filter((a) => a.app.stage !== "closed");
  const c = model.claimCounts;
  const unknowns = new Map<string, number>();
  for (const r of model.rows) for (const u of r.unknownInputs) unknowns.set(u, (unknowns.get(u) ?? 0) + 1);
  return (
    <main className="in-page" id="main">
      <div className="in-head">
        <h1>Today</h1>
        <p>{actions.length} things need you. {active.length} applications are open across {new Set(active.map((a) => a.row?.job.organization)).size} employers.</p>
      </div>
      <div className="in-grid">
        <section className="in-pane" aria-labelledby="na">
          <h2 id="na">Next actions</h2>
          <ol className="in-actions">
            {actions.map((a, i) => (
              <li key={i}>
                <span className={`in-aicon k-${a.kind}`}><Icon name={ACTION_ICON[a.kind] ?? "arrow"} size={16} /></span>
                <div><strong>{a.title}</strong><span>{a.detail}</span></div>
                <button className="in-btn ghost">Open</button>
              </li>
            ))}
          </ol>
          <h2 className="in-sub">Closest postings <span>ranked by the engine’s near-term fit; strategic value shown alongside, never blended</span></h2>
          <table className="in-table">
            <thead><tr><th scope="col">Posting</th><th scope="col">Fit now · Strategic</th><th scope="col">Pay</th><th scope="col">Status</th></tr></thead>
            <tbody>
              {top.map((r) => (
                <tr key={r.job.id}>
                  <td><strong>{r.job.title}</strong><span>{r.job.organization}</span></td>
                  <td><ScorePair r={r} compact /></td>
                  <td className={r.comp.known ? "" : "muted"}>{r.comp.known ? r.comp.text.split(" · ")[0] : "Not disclosed"}</td>
                  <td>{r.failedConstraints.length ? <span className="in-tag bad"><Icon name="alert" size={13} />Fails {r.failedConstraints[0]?.toLowerCase()}</span>
                    : r.application ? <span className="in-tag">{r.application.stage.replace("_", " ")}</span>
                    : <span className="in-tag ok"><Icon name="check" size={13} />Passes constraints</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <div className="in-side">
          <section className="in-pane" aria-labelledby="pl">
            <h2 id="pl">Pipeline</h2>
            <ul className="in-stages">
              {model.stageCounts.map((s) => (
                <li key={s.stage} className={s.count ? "" : "zero"}>
                  <span>{s.label}</span><span className="in-num">{s.count}</span>
                  <span className="bar" aria-hidden="true"><i style={{ width: `${(s.count / Math.max(1, ...model.stageCounts.map((x) => x.count))) * 100}%` }} /></span>
                </li>
              ))}
            </ul>
            <p className="in-note">{model.applications.filter((a) => a.silentDays !== null).length} submitted with no reply recorded. Silence is shown as unknown, never as rejection.</p>
          </section>
          <section className="in-pane" aria-labelledby="ev">
            <h2 id="ev">Evidence behind your claims</h2>
            <div className="in-stack" role="img" aria-label={`${c.supported} supported, ${c.selfReported} self-reported, ${c.unverified} unverified, ${c.contradicted} contradicted`}>
              <i className="s1" style={{ flex: c.supported }} /><i className="s2" style={{ flex: c.selfReported }} /><i className="s3" style={{ flex: c.unverified }} /><i className="s4" style={{ flex: c.contradicted }} />
            </div>
            <dl className="in-legend">
              <div><dt><i className="s1" />Supported</dt><dd>{c.supported}</dd></div>
              <div><dt><i className="s2" />Self-reported</dt><dd>{c.selfReported}</dd></div>
              <div><dt><i className="s3" />Unverified</dt><dd>{c.unverified}</dd></div>
              <div><dt><i className="s4" />Contradicted</dt><dd>{c.contradicted}</dd></div>
            </dl>
          </section>
          <section className="in-pane" aria-labelledby="xp">
            <h2 id="xp">Experiments</h2>
            <ul className="in-list">
              {model.experiments.map((e) => (
                <li key={e.id}><span>{e.title}</span><span className="in-tag">{e.verdict ? VERDICT_TEXT[e.verdict] ?? e.verdict : e.status}</span></li>
              ))}
            </ul>
          </section>
          <section className="in-pane quiet" aria-labelledby="uk">
            <h2 id="uk"><Icon name="unknown" size={16} />What the scores cannot see</h2>
            <ul className="in-list">
              {[...unknowns.entries()].map(([k, n]) => <li key={k}><span>{k.replace(/_/g, " ")}</span><span className="muted">{n} postings</span></li>)}
            </ul>
          </section>
        </div>
      </div>
    </main>
  );
}

function Discover({ model }: { model: ConceptModel }) {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "passes" | "pay">("all");
  const rows = useMemo(() => model.rows.filter((r) => {
    if (q && !`${r.job.title} ${r.job.organization} ${r.job.skills.join(" ")}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (only === "passes" && r.failedConstraints.length) return false;
    if (only === "pay" && !r.comp.known) return false;
    return true;
  }), [model.rows, q, only]);
  const [sel, setSel] = useState(rows[0]?.job.id ?? null);
  // Phone: list and detail are two views; ?open=1 starts on the detail (used for screenshots).
  const [showDetail, setShowDetail] = useState(new URLSearchParams(location.search).get("open") === "1");
  const r = model.rows.find((x) => x.job.id === sel) ?? rows[0];
  return (
    <main className="in-page split" id="main">
      <div className="in-head row">
        <h1>Discover</h1>
        <div className="in-filters">
          <label className="in-field"><span className="sr-only">Filter postings</span><Icon name="filter" size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, employer or skill" /></label>
          <div className="in-seg" role="radiogroup" aria-label="Show">
            {([["all", "All"], ["passes", "Passes my constraints"], ["pay", "Pay disclosed"]] as const).map(([k, l]) => (
              <button key={k} role="radio" aria-checked={only === k} className={only === k ? "on" : ""} onClick={() => setOnly(k)}>{l}</button>
            ))}
          </div>
          <span className="in-count">{rows.length} of {model.rows.length} postings</span>
        </div>
      </div>
      <div className={`in-split ${showDetail ? "show-detail" : ""}`}>
        <ul className="in-feed" aria-label="Postings">
          {rows.map((x) => (
            <li key={x.job.id}>
              <button className={x.job.id === r?.job.id ? "on" : ""} onClick={() => { setSel(x.job.id); setShowDetail(true); }} aria-current={x.job.id === r?.job.id}>
                <div className="in-feed-t"><strong>{x.job.title}</strong><span>{x.job.organization}</span></div>
                <ScorePair r={x} compact />
                <div className="in-feed-m">
                  <span>{x.job.remote_policy ?? "Work style unknown"}</span><span>{x.family}</span>
                  <span className={`${x.fresh.level} nocap`}>{x.fresh.days === 1 ? "1 day" : `${x.fresh.days} days`} old</span>
                  {x.failedConstraints.length > 0 && <span className="bad">Fails: {x.failedConstraints[0]?.toLowerCase()}</span>}
                </div>
              </button>
            </li>
          ))}
        </ul>
        {r && <JobDetail r={r} onBack={() => setShowDetail(false)} />}
      </div>
    </main>
  );
}

function JobDetail({ r, onBack }: { r: JobRow; onBack?: () => void }) {
  const m = r.match;
  return (
    <article className="in-detail" aria-label={`${r.job.title} details`}>
      <header>
        {onBack && <button className="in-btn ghost in-back" onClick={onBack}><Icon name="arrow" size={15} className="flip" />All postings</button>}
        <p className="in-eyebrowless"><span>{r.job.organization}</span><span>{r.family}</span><span>{r.job.seniority}</span></p>
        <h2>{r.job.title}</h2>
        <div className="in-facts">
          <span><Icon name="atlas" size={15} />{r.job.location ?? "Location not stated"}</span><span>{r.job.remote_policy ? `${r.job.remote_policy} work` : "Work style unknown"}</span>
          <span className={r.comp.known ? "" : "muted"}>{r.comp.text}</span>
          <span className={r.fresh.level}>{r.fresh.label}</span>
        </div>
        <div className="in-cta">
          <button className="in-btn primary"><Icon name="bookmark" size={15} />Shortlist</button>
          <button className="in-btn">Tailor a résumé</button>
          <button className="in-btn">Track application</button>
          <span className="in-note">Nothing is sent. Applying stays your own manual step.</span>
        </div>
      </header>
      {m ? (
        <>
          <section className="in-scores">
            {([["Near-term fit", "Could you credibly do this now, on the evidence you hold?", m.near_term_fit, "near"], ["Strategic value", "Does it move you toward the roles you said you want?", m.strategic_value, "strat"]] as const).map(([title, q, g, cls]) => (
              <div key={title} className="in-score">
                <div className="in-score-h"><h3>{title}</h3><span className="in-big">{g.score === null ? "—" : Math.round(g.score)}<small>/100</small></span></div>
                <p className="muted">{q}</p>
                <ul>
                  {g.components.map((c) => (
                    <li key={c.key}>
                      <span>{c.label}</span>
                      <ScoreBar value={c.value === null ? null : c.value * 100} label={c.label} className={cls} />
                      <span className="muted">{c.value === null ? "unknown" : `w ${c.weight === null ? "?" : Math.round(c.weight * 100) + "%"}`}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
          <p className="in-disclaimer"><Icon name="unknown" size={15} />Relative heuristics from {m.engine.name}. Not a probability of being hired. Unknown inputs: {m.unknown_inputs.map((u) => u.replace(/_/g, " ")).join(", ") || "none"}.</p>
          <section className="in-two">
            <div>
              <h3>Hard constraints</h3>
              <ul className="in-checks">
                {m.hard_constraints.map((c) => (
                  <li key={c.key} className={c.status}><Icon name={c.status === "pass" ? "check" : c.status === "fail" ? "cross" : "unknown"} size={15} /><span><strong>{c.label}</strong> {c.detail}</span></li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Skills and the evidence behind them</h3>
              <ul className="in-skills">
                {m.skills.map((s) => (
                  <li key={s.skill} className={s.evidence_strength}>{skillLabel(s.skill)}<span>{s.evidence_strength === "missing" ? "no claim" : `${s.claim_ids.length} claim${s.claim_ids.length === 1 ? "" : "s"}`}</span></li>
                ))}
              </ul>
            </div>
          </section>
        </>
      ) : <p className="in-note">The matching engine did not score this posting.</p>}
    </article>
  );
}

const FAMILY_COLORS = ["#2B44C9", "#0F8A7E", "#B5651D", "#8B3FB8", "#3C7A2E", "#C2336A", "#5B6B82", "#A88A12", "#2E7DB5"];
// Dark theme: lighter steps of the same hues so every point keeps >= 3:1 against the dark ground.
const FAMILY_COLORS_DARK = ["#8EA0FF", "#4FC6B5", "#E8A15C", "#C79BF0", "#8FD07A", "#F28BB0", "#A9B6CC", "#E3C45A", "#7CC0EE"];

function Atlas({ model }: { model: ConceptModel }) {
  const p = useAtlas(model.bundle.projections);
  const pts = useMemo(() => (p ? atlasPoints(p) : []), [p]);
  const families = [...new Set(pts.map((x) => x.family).filter(Boolean))] as string[];
  const palette = document.documentElement.dataset.theme === "dark" ? FAMILY_COLORS_DARK : FAMILY_COLORS;
  const color = (f: string | null) => (f ? palette[families.indexOf(f) % palette.length]! : "#8A93A3");
  const [sel, setSel] = useState<string | null>(pts.find((x) => x.kind === "candidate")?.id ?? null);
  if (!p) return <main className="in-page"><p>No projection was provided.</p></main>;
  const s = pts.find((x) => x.id === sel) ?? null;
  const d = p.diagnostics;
  const narrow = typeof window !== "undefined" && window.innerWidth < 720;
  return (
    <main className="in-page split" id="main">
      <div className="in-head row">
        <h1>Career atlas</h1>
        <div className="in-filters">
          <div className="in-seg" role="radiogroup" aria-label="Projection">
            {model.bundle.projections.map((x) => <button key={x.projection_id} role="radio" aria-checked={x === p} className={x === p ? "on" : ""}>{x.method === "pca" ? "Principal components" : "Interpretable axes"}</button>)}
          </div>
        </div>
      </div>
      <div className="in-atlas">
        <section className="in-map" aria-label="Map">
          <div className="in-warn"><Icon name="alert" size={16} /><span>This picture keeps <strong>{pct(d.cumulative_explained_variance)}</strong> of the skill differences between postings. Two points that look close may not be. Distance is not fit.</span></div>
          <Scatter projection={p} colorFor={color} width={narrow ? 360 : 760} height={narrow ? 460 : 520} selected={sel} onSelect={setSel} showLabels={!narrow} pointRadius={narrow ? 6 : 5} />
          <div className="in-axes"><span>{p.axes[0]?.label} · {pct(p.axes[0]?.explained_variance_ratio)} of variance →</span><span>↑ {p.axes[1]?.label} · {pct(p.axes[1]?.explained_variance_ratio)}</span></div>
          <ul className="in-keys" aria-label="Legend">
            {families.map((f) => <li key={f}><i style={{ background: color(f) }} />{model.familyLabel(f)}</li>)}
            <li><i className="cand" />You, from evidence</li><li><i className="cent" />Role centre</li><li><i className="scen" />Hypothetical plan</li><li><i className="faded" />Low local fidelity</li>
          </ul>
        </section>
        <aside className="in-inspect" aria-label="Inspector">
          {s ? (
            <>
              <p className="muted">{s.kind === "role-centroid" ? "Role centre" : s.kind === "scenario" ? "Hypothetical plan" : s.kind === "candidate" ? "You" : "Posting"}{s.family ? ` · ${model.familyLabel(s.family)}` : ""}</p>
              <h2>{s.kind === "candidate" || s.kind === "scenario" ? shortLabel(s) : s.label}</h2>
              {s.detail && <p>{s.detail}</p>}
              <div className="in-fid">
                <span>Neighbourhood kept</span><strong>{pct(s.quality)}</strong>
                <ScoreBar value={s.quality === null ? null : s.quality * 100} label="Neighbourhood kept" />
                <p className="muted">Share of this point’s true nearest neighbours that also look near on the map.</p>
              </div>
              {s.skills.length > 0 && <><h3>Top skills</h3><ul className="in-skills plain">{s.skills.map((k) => <li key={k}>{skillLabel(k)}</li>)}</ul></>}
            </>
          ) : <p className="muted">Select a point on the map or press Tab, then use the arrow keys.</p>}
          <div className="in-fidelity">
            <h3>How far to trust this map</h3>
            <dl>
              <div><dt>Variance shown</dt><dd>{pct(d.cumulative_explained_variance)}</dd></div>
              <div><dt>Trustworthiness (k={d.trustworthiness?.k})</dt><dd>{d.trustworthiness?.value.toFixed(2)}</dd></div>
              <div><dt>Continuity (k={d.continuity?.k})</dt><dd>{d.continuity?.value.toFixed(2)}</dd></div>
            </dl>
            <p className="muted">1.00 would mean neighbours are perfectly preserved. Use the map to ask questions, then check the full comparison in Matching.</p>
          </div>
        </aside>
      </div>
    </main>
  );
}

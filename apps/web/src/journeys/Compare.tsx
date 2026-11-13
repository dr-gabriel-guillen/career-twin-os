import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Banner, Button, EmptyState, Icon, ScoreBar, SelectField, TableRegion, useAnnounce } from "@careertwin/ui";
import { Page } from "@/components/common";
import type { MatchExplanation } from "@/adapters/proposed";
import type { JobPosting } from "@/contracts/types";
import { formatCompensation, humanise } from "@/lib/format";
import { useApp } from "@/state/AppContext";
import { blendedScore, useIndexes } from "@/state/selectors";
import { blockersFor, rankByNearTerm, reasonsFor, standingOf, STANDING_LABEL } from "./lib";

const MAX = 3;
const PAGE = 25;
type Sort = "near" | "strategic" | "blend";

const REFUSAL_LABELS: Record<string, string> = {
  jurisdiction: "Location outside your stated regions",
  "employment-type": "Employment type",
  "remote-policy": "Work style",
  "seniority-band": "Seniority",
  availability: "Availability",
  "duplicate-of": "Duplicate posting",
  "content-integrity": "Failed a content-integrity check",
  "compensation-floor": "Below your pay floor",
  "phantom-risk": "May not be a real opening",
};
const refusalLabel = (reason: string) => REFUSAL_LABELS[reason.split(":")[0]!.trim()] ?? "Other reason";
function readableRefusal(reason: string, jobById: Map<string, JobPosting>): string {
  const [key, ...rest] = reason.split(":");
  const value = rest.join(":").trim();
  if (key?.trim() === "duplicate-of") {
    const other = jobById.get(value);
    return other ? `Duplicate of “${other.title}”` : "Duplicate of another posting";
  }
  return value ? `${refusalLabel(reason)}: ${value}` : refusalLabel(reason);
}

const coverage = (m: MatchExplanation) => {
  const covered = m.skills.filter((s) => s.evidence_strength === "supported" || s.evidence_strength === "self-reported").length;
  return { covered, total: m.skills.length };
};
const constraintText = (m: MatchExplanation) => {
  const fail = m.hard_constraints.filter((h) => h.status === "fail");
  const unknown = m.hard_constraints.filter((h) => h.status === "unknown");
  if (fail.length) return { text: `Fails ${fail.map((h) => h.label.toLowerCase()).join(", ")}`, tone: "bad" as const };
  if (unknown.length) return { text: `${unknown.length} unknown`, tone: "weak" as const };
  return { text: "All pass", tone: "good" as const };
};

/**
 * Side-by-side comparison of postings the person is weighing. Near-term fit and strategic value
 * stay two separate numbers; the optional blend only orders the table and is never shown as a score.
 */
export default function CompareJourney() {
  const { bundle, workspace, dispatch } = useApp();
  const { jobById, matchByJob, matchesStale, appByJob } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [sort, setSort] = useState<Sort>("near");
  const [scope, setScope] = useState<"all" | "shortlist" | "passes">("all");
  const [limit, setLimit] = useState(PAGE);
  const { rankOf, scored } = useMemo(() => rankByNearTerm(bundle.matches), [bundle.matches]);

  const ranked = useMemo(() => {
    const val = (m: MatchExplanation) => (sort === "near" ? m.near_term_fit.score : sort === "strategic" ? m.strategic_value.score : blendedScore(m, workspace.strategicBlend)) ?? -1;
    return bundle.matches
      .filter((m) => jobById.has(m.job_id))
      .filter((m) => (scope === "shortlist" ? workspace.shortlist.includes(m.job_id) : scope === "passes" ? !m.hard_constraints.some((h) => h.status === "fail") : true))
      .sort((a, b) => val(b) - val(a) || a.job_id.localeCompare(b.job_id));
  }, [bundle.matches, jobById, sort, scope, workspace.shortlist, workspace.strategicBlend]);

  const fromUrl = (params.get("ids") ?? "").split(",").filter((id) => matchByJob.has(id)).slice(0, MAX);
  const defaults = [...workspace.shortlist.filter((id) => matchByJob.has(id)), ...[...rankOf.entries()].sort((a, b) => a[1] - b[1]).map(([id]) => id)];
  const chosen = fromUrl.length > 0 ? fromUrl : [...new Set(defaults)].slice(0, MAX);
  const setChosen = (ids: string[]) => setParams(ids.length ? { ids: ids.join(",") } : {}, { replace: true });
  const toggle = (id: string) => {
    if (chosen.includes(id)) {
      setChosen(chosen.filter((x) => x !== id));
      announce("Removed from the comparison.");
    } else if (chosen.length >= MAX) {
      announce(`You can compare up to ${MAX} postings. Remove one first.`);
    } else {
      setChosen([...chosen, id]);
      announce("Added to the comparison.");
    }
  };

  if (bundle.matches.length === 0) {
    return (
      <Page title="Compare">
        <EmptyState title="Nothing to compare yet.">
          <p>The matching engine has not ranked these postings, so their fit cannot be compared. Browse them in <Link to="/jobs">Discover</Link>.</p>
        </EmptyState>
      </Page>
    );
  }

  return (
    <Page
      title="Compare"
      lead="The postings you are weighing, side by side. Near-term fit and strategic value are two separate measures and are never merged. Neither is a chance of being hired."
    >
      {matchesStale ? (
        <Banner tone="warning" title="These scores may be out of date." data-testid="stale-banner">
          Your evidence changed after the engine computed them. They reflect the claims it saw, not your edits since, and are never recomputed in the browser.
        </Banner>
      ) : null}

      <section aria-labelledby="cmp-side" className="cmp-side" data-testid="compare-columns">
        <h2 id="cmp-side" className="ct-sr-only">
          Side by side
        </h2>
        {chosen.length === 0 ? (
          <EmptyState title="Choose up to three postings below to compare them." />
        ) : (
          <div className="cmp-cols" style={{ ["--cmp-n" as string]: chosen.length }}>
            {chosen.map((id) => {
              const m = matchByJob.get(id)!;
              const j = jobById.get(id)!;
              const st = standingOf(m, rankOf, scored);
              const cov = coverage(m);
              const con = constraintText(m);
              const comp = formatCompensation(j.compensation);
              const shortlisted = workspace.shortlist.includes(id);
              return (
                <article key={id} className="cmp-col" aria-labelledby={`cmp-${id}`} data-testid={`compare-${id}`}>
                  <header className="cmp-col__head">
                    <p className={`pt-standing pt-standing--${st}`}>{STANDING_LABEL[st]}</p>
                    <h3 id={`cmp-${id}`}>
                      <Link to={`/jobs/${encodeURIComponent(id)}`}>{j.title}</Link>
                    </h3>
                    <p className="pt-muted">
                      {j.organization}. {j.location ?? "Location not stated"}. {j.remote_policy && j.remote_policy !== "unknown" ? humanise(j.remote_policy) : "Work style unknown"}.
                    </p>
                    <button type="button" className="cmp-remove" onClick={() => toggle(id)} aria-label={`Remove ${j.title} from the comparison`}>
                      <Icon name="cross" size={14} />
                    </button>
                  </header>
                  <dl className="cmp-facts">
                    <div className="cmp-fact cmp-fact--scores">
                      <dt>Engine scores</dt>
                      <dd>
                        <ScoreBar kind="near" value={m.near_term_fit.score} testId={`near-${id}`} />
                        <ScoreBar kind="strat" value={m.strategic_value.score} testId={`strat-${id}`} />
                      </dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>Listed skills your claims cover</dt>
                      <dd>
                        {cov.covered} of {cov.total}
                      </dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>Your constraints</dt>
                      <dd className={`pt-tone--${con.tone}`}>{con.text}</dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>Pay</dt>
                      <dd>{comp.known ? comp.text.split(" · ").join(", ") : "Not disclosed (unknown, not zero)"}</dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>Seniority</dt>
                      <dd>{j.seniority === "unknown" ? "Unknown" : humanise(j.seniority)}</dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>Why it could fit</dt>
                      <dd>
                        {reasonsFor(m).length === 0 ? (
                          <span className="pt-muted">No listed skill is covered.</span>
                        ) : (
                          <ul className="ct-list-plain cmp-list">
                            {reasonsFor(m).slice(0, 4).map((r) => (
                              <li key={r.text} className={`pt-tone--${r.tone === "good" ? "good" : "weak"}`}>
                                {r.text}
                                {r.tone === "weak" ? " (self-reported)" : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </dd>
                    </div>
                    <div className="cmp-fact">
                      <dt>What stands in the way</dt>
                      <dd>
                        {blockersFor(m).length === 0 ? (
                          <span className="pt-muted">Nothing the engine knows of.</span>
                        ) : (
                          <ul className="ct-list-plain cmp-list">
                            {blockersFor(m).slice(0, 4).map((r) => (
                              <li key={r.text} className={`pt-tone--${r.tone}`}>
                                {r.text}
                              </li>
                            ))}
                          </ul>
                        )}
                      </dd>
                    </div>
                  </dl>
                  <div className="pt-actions cmp-col__actions">
                    <Button size="sm" variant={shortlisted ? "secondary" : "primary"} aria-pressed={shortlisted} onClick={() => dispatch({ type: "shortlist/toggle", jobId: id })}>
                      <Icon name="bookmark" size={14} /> {shortlisted ? "Shortlisted" : "Shortlist"}
                    </Button>
                    <Link className="ct-btn ct-btn--secondary ct-btn--sm" to={`/resume/tailor/${encodeURIComponent(id)}`}>
                      Tailor résumé
                    </Link>
                    {appByJob.get(id) ? (
                      <Link className="ct-btn ct-btn--ghost ct-btn--sm" to={`/applications/${encodeURIComponent(appByJob.get(id)!.id)}`}>
                        In applications
                      </Link>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="cmp-table" aria-labelledby="cmp-all">
        <div className="cmp-table__head">
          <h2 id="cmp-all">All ranked postings</h2>
          <div className="cmp-table__tools">
            <SelectField label="Show" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)} options={[{ value: "all", label: `All ranked (${bundle.matches.length})` }, { value: "passes", label: "Passes my constraints" }, { value: "shortlist", label: `Shortlisted (${workspace.shortlist.length})` }]} data-testid="compare-scope" />
            <SelectField
              label="Order by"
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              options={[
                { value: "near", label: "Near-term fit" },
                { value: "strategic", label: "Strategic value" },
                { value: "blend", label: `Your blend, ${workspace.strategicBlend}% strategic (order only)` },
              ]}
              data-testid="compare-sort"
            />
          </div>
        </div>
        {sort === "blend" ? (
          <div className="cmp-blend">
            <label htmlFor="cmp-blend-range">
              Ranking blend: {100 - workspace.strategicBlend}% near-term fit, {workspace.strategicBlend}% strategic value
            </label>
            <input id="cmp-blend-range" type="range" min={0} max={100} step={5} value={workspace.strategicBlend} onChange={(e) => dispatch({ type: "blend/set", value: Number(e.target.value) })} data-testid="blend" />
            <p className="pt-muted">The blend only changes the order of this table. It is never shown as a score.</p>
          </div>
        ) : null}
        <p className="pt-muted" role="status" data-testid="compare-count">
          {ranked.length} posting{ranked.length === 1 ? "" : "s"}. {chosen.length} of {MAX} chosen for comparison.
        </p>
        <TableRegion label="Ranked postings">
          <table className="ct-table cmp-grid" data-testid="compare-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="ct-sr-only">Compare</span>
                </th>
                <th scope="col">Posting</th>
                <th scope="col">Near-term fit</th>
                <th scope="col">Strategic value</th>
                <th scope="col">Skills covered</th>
                <th scope="col">Constraints</th>
              </tr>
            </thead>
            <tbody>
              {ranked.slice(0, limit).map((m) => {
                const j = jobById.get(m.job_id)!;
                const on = chosen.includes(m.job_id);
                const cov = coverage(m);
                const con = constraintText(m);
                return (
                  <tr key={m.job_id} aria-selected={on}>
                    <td>
                      <input type="checkbox" className="cmp-check" checked={on} disabled={!on && chosen.length >= MAX} onChange={() => toggle(m.job_id)} aria-label={`Compare ${j.title}, ${j.organization}`} data-testid={`pick-${m.job_id}`} />
                    </td>
                    <td>
                      <Link to={`/jobs/${encodeURIComponent(m.job_id)}`} className="cmp-title">
                        {j.title}
                      </Link>
                      <span className="pt-muted cmp-org">
                        {j.organization}
                        {workspace.shortlist.includes(m.job_id) ? ", shortlisted" : ""}
                      </span>
                    </td>
                    <td className="ct-num">{m.near_term_fit.score ?? "Unknown"}</td>
                    <td className="ct-num">{m.strategic_value.score ?? "Unknown"}</td>
                    <td className="ct-num">
                      {cov.covered}/{cov.total}
                    </td>
                    <td className={`pt-tone--${con.tone}`}>{con.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableRegion>
        {ranked.length > limit ? (
          <Button size="sm" onClick={() => setLimit(limit + PAGE)}>
            Show {Math.min(PAGE, ranked.length - limit)} more of {ranked.length - limit}
          </Button>
        ) : null}
      </section>

      {bundle.matchRefusals.length > 0 ? (
        <details className="pt-tech cmp-refused" id="not-ranked" data-testid="refusals-disclosure">
          <summary>
            {bundle.matchRefusals.length} postings were not ranked because they failed a hard constraint upstream. Listed so nothing disappears silently.
          </summary>
          <ul className="ct-list-plain cmp-refused__list" data-testid="match-refusals">
            {bundle.matchRefusals.map((r, i) => (
              <li key={`${i}-${r.job_id}`}>
                <strong>{jobById.get(r.job_id)?.title ?? "Posting not in this feed"}</strong>
                <span className="pt-muted"> {r.reasons.map((reason) => readableRefusal(reason, jobById)).join("; ")}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Page>
  );
}

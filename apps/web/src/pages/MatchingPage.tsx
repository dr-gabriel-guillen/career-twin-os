import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Banner, Button, Card, Checkbox, EmptyState, Meter, ScoreBar, ScorePair, TableRegion, useAnnounce, type Tone } from "@careertwin/ui";
import { Page, SyntheticBadge, TruthBadge } from "@/components/common";
import type { ConstraintStatus, MatchComponent, MatchExplanation, ScoreGroup } from "@/adapters/proposed";
import { formatDateTime, humanise, skillLabel } from "@/lib/format";
import { useApp } from "@/state/AppContext";
import { blendedScore, useIndexes } from "@/state/selectors";

const CONSTRAINT_TONE: Record<ConstraintStatus, Tone> = { pass: "success", fail: "danger", unknown: "warning" };
const CONSTRAINT_LABEL: Record<ConstraintStatus, string> = { pass: "Pass", fail: "Fail", unknown: "Unknown" };

/** Plain-language reading of a score group: what lifts it, what limits it, what is unknown. */
function explain(group: ScoreGroup): { ranked: boolean; lifts: MatchComponent | null; limits: MatchComponent | null; unknown: MatchComponent[] } {
  const unknown = group.components.filter((c) => c.status === "unknown");
  const known = group.components.filter((c) => c.status === "known" && c.value !== null);
  // Contributions can only be ranked when the engine published a weight for every known component.
  // Without weights nothing is said about what lifts or limits the score (finding F-06).
  if (known.length === 0 || known.some((c) => c.weight === null)) return { ranked: false, lifts: null, limits: null, unknown };
  const w = (c: MatchComponent) => c.weight as number;
  const byContribution = [...known].sort((a, b) => w(b) * (b.value as number) - w(a) * (a.value as number));
  const byLoss = [...known].sort((a, b) => w(b) * (1 - (b.value as number)) - w(a) * (1 - (a.value as number)));
  const limits = byLoss[0] && w(byLoss[0]) * (1 - (byLoss[0].value as number)) > 0.005 ? byLoss[0] : null;
  return { ranked: true, lifts: byContribution[0] ?? null, limits, unknown };
}

function ScoreBlock({ title, description, group, testId, kind }: { title: string; description: string; group: ScoreGroup; testId: string; kind: "near" | "strat" }) {
  const e = explain(group);
  return (
    <Card title={title} headingLevel={3} data-testid={testId} className={`mt-score mt-score--${kind}`}>
      <div className="mt-score__head">
        <span className={group.score === null ? "app-score app-score--unknown" : "app-score"} data-testid={`${testId}-score`}>
          {group.score === null ? "Unknown" : group.score}
          {group.score === null ? null : <span className="ct-small ct-muted"> / 100</span>}
        </span>
        <p className="ct-muted ct-small">{description}</p>
      </div>
      <ScoreBar kind={kind} value={group.score} label={title} />
      <ul className="ct-list-plain mt-components">
        {group.components.map((c) => (
          <li key={c.key}>
            <div className="app-component">
              <span>
                <strong>{c.label}</strong>
                <span className="ct-muted ct-small">{c.note}</span>
              </span>
              <Meter value={c.status === "known" ? c.value : null} label={c.label} />
              <span className="ct-small ct-muted">{c.status !== "known" ? "excluded" : c.weight === null ? "weight not published" : `weight ${Math.round(c.weight * 100)}%`}</span>
            </div>
          </li>
        ))}
      </ul>
      <p className="ct-small">
        {!e.ranked ? "The engine did not publish weights for these terms, so what lifts or limits the score cannot be stated. " : null}
        {e.ranked && e.lifts ? <>Largest contribution: <strong>{e.lifts.label}</strong>. </> : null}
        {e.ranked ? (e.limits ? <>Biggest limiter: <strong>{e.limits.label}</strong>. </> : "No known term is below its maximum. ") : null}
        {e.unknown.length > 0 ? <>Unknown and excluded (not counted as zero): {e.unknown.map((u) => u.label).join(", ")}.</> : null}
      </p>
    </Card>
  );
}

export default function MatchingPage() {
  const { bundle, workspace, dispatch } = useApp();
  const { jobById, familyLabel, matchesStale, appByJob } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [hideFailed, setHideFailed] = useState(false);
  const [shortOnly, setShortOnly] = useState(false);

  const ranked = useMemo(() => {
    return bundle.matches
      .filter((m) => !hideFailed || !m.hard_constraints.some((h) => h.status === "fail"))
      .filter((m) => !shortOnly || workspace.shortlist.includes(m.job_id))
      .map((m) => ({ match: m, blended: blendedScore(m, workspace.strategicBlend) }))
      .sort((a, b) => (b.blended ?? -1) - (a.blended ?? -1) || a.match.job_id.localeCompare(b.match.job_id));
  }, [bundle.matches, hideFailed, shortOnly, workspace.shortlist, workspace.strategicBlend]);

  const selectedId = params.get("job");
  const selected: MatchExplanation | null = (selectedId ? bundle.matches.find((m) => m.job_id === selectedId) : null) ?? ranked[0]?.match ?? null;
  const job = selected ? jobById.get(selected.job_id) ?? null : null;
  const blend = workspace.strategicBlend;

  if (bundle.matches.length === 0) {
    return (
      <Page title="Matching">
        <EmptyState title="No match explanations are available.">
          <p>The matching engine (lane A) has not provided any explanations for this feed.</p>
        </EmptyState>
      </Page>
    );
  }

  const missing = selected ? selected.skills.filter((s) => s.evidence_strength === "missing" || s.evidence_strength === "contradicted") : [];
  const weak = selected ? selected.skills.filter((s) => s.evidence_strength === "unverified" || s.evidence_strength === "self-reported") : [];

  return (
    <Page
      title="Matching"
      lead="Why each posting ranks where it does. Near-term fit asks “could you credibly do this role now, on the evidence you hold?”. Strategic value asks “does it move you toward the roles you say you want?”. Neither is a probability of being hired."
    >
      {matchesStale ? (
        <Banner tone="warning" title="These scores may be out of date." data-testid="stale-banner">
          Your evidence vault changed after the matching engine took its snapshot. Scores are computed upstream and are not
          recalculated in the browser; they will refresh when the engine runs again.
        </Banner>
      ) : null}


      {bundle.matchRefusals.length > 0 ? (
        <Card title={`Refused by the matching engine (${bundle.matchRefusals.length})`}>
          <p className="ct-small ct-muted">These postings failed a hard constraint upstream and were not ranked. They are listed so nothing disappears silently.</p>
          <ul className="ct-small" data-testid="match-refusals">
            {bundle.matchRefusals.map((r, i) => (
              <li key={`${i}-${r.job_id}`}>
                {jobById.get(r.job_id)?.title ?? r.job_id}: {r.reasons.join("; ")}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <div className="app-split">
        <Card title={`Ranked postings (${ranked.length})`} className="mt-list">
          <div className="mt-rank" role="group" aria-label="How to rank">
            <label htmlFor="blend" className="ct-field__label">
              Ranking blend: {100 - blend}% near-term fit, {blend}% strategic value
            </label>
            <input id="blend" type="range" min={0} max={100} step={5} value={blend} onChange={(e) => dispatch({ type: "blend/set", value: Number(e.target.value) })} aria-describedby="blend-hint" data-testid="blend" />
            <p id="blend-hint" className="ct-field__hint">
              This only changes the order of the list. It does not change either score. Your choice is saved in this browser.
            </p>
            <div className="ct-stack mt-rank__checks">
              <Checkbox label="Hide postings that fail a hard constraint" checked={hideFailed} onChange={(e) => setHideFailed(e.target.checked)} data-testid="hide-failed" />
              <Checkbox label="Shortlisted only" checked={shortOnly} onChange={(e) => setShortOnly(e.target.checked)} />
            </div>
          </div>
          {ranked.length === 0 ? (
            <EmptyState title="No postings match these options." />
          ) : (
            <ol className="ct-list-plain ct-stack" data-testid="match-list">
              {ranked.map(({ match, blended }, i) => {
                const j = jobById.get(match.job_id);
                const failed = match.hard_constraints.filter((h) => h.status === "fail").length;
                return (
                  <li key={match.job_id}>
                    <button type="button" className="app-pick" aria-current={selected?.job_id === match.job_id ? "true" : undefined} onClick={() => setParams({ job: match.job_id })} data-testid={`match-${match.job_id}`}>
                      <span className="mt-pick__head">
                        <span className="mt-pick__rank">{i + 1}.</span>
                        <strong>{j?.title ?? match.job_id}</strong>
                        <span className="mt-pick__order" title="Position value under your ordering blend. Not a score.">{blended ?? "Unknown"}</span>
                      </span>
                      <span className="ct-muted ct-small mt-pick__org">{j?.organization}</span>
                      <span className="mt-pick__scores">
                        {match.near_term_fit.score === null && match.strategic_value.score === null && match.overall ? (
                          <span className="ct-small">Single score {match.overall.score}</span>
                        ) : (
                          <ScorePair near={match.near_term_fit.score} strategic={match.strategic_value.score} />
                        )}
                        {failed > 0 ? <Badge tone="danger">{failed} hard constraint{failed === 1 ? "" : "s"} failed</Badge> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        {selected && job ? (
          <div className="ct-stack" data-testid="match-detail">
            <Card
              title={job.title}
              actions={
                <>
                  <Button
                    size="sm"
                    aria-pressed={workspace.shortlist.includes(job.id)}
                    onClick={() => {
                      dispatch({ type: "shortlist/toggle", jobId: job.id });
                      announce(workspace.shortlist.includes(job.id) ? "Removed from shortlist." : "Added to shortlist.");
                    }}
                    data-testid="detail-shortlist"
                  >
                    {workspace.shortlist.includes(job.id) ? "Shortlisted" : "Shortlist"}
                  </Button>
                  <Link className="ct-btn ct-btn--sm" to={`/resume?job=${encodeURIComponent(job.id)}`} data-testid="detail-tailor">
                    Tailor a résumé variant
                  </Link>
                  {appByJob.get(job.id) ? (
                    <Link className="ct-btn ct-btn--sm" to={`/applications?app=${encodeURIComponent(appByJob.get(job.id)!.id)}`}>
                      Open application
                    </Link>
                  ) : null}
                </>
              }
            >
              <div className="ct-row">
                {job.source === "synthetic" || bundle.jobFacets[job.id]?.synthetic === true ? <SyntheticBadge /> : <Badge tone="warning">Provenance not verified</Badge>}
                <Badge>{familyLabel(job.role_family)}</Badge>
                <span className="ct-muted">{job.organization}</span>
              </div>
              <Banner tone="info" title="Relative heuristic, not a hiring probability.">
                Score type: <code>{selected.score_type}</code>. {selected.computed_at ? `Computed ${formatDateTime(selected.computed_at)}` : "Compute time not stated"} by {selected.engine.name}{" "}
                {selected.engine.version}
                {selected.engine.name === "lane-d-fixture-standin" ? ", a lane-D fixture stand-in for the lane-A matching engine." : "."}
              </Banner>
              {selected.overall ? (
                <p data-testid="overall-score">
                  <strong>Overall score: {selected.overall.score} / 100</strong>
                  {selected.overall.interval ? (
                    <> (range {selected.overall.interval.low} to {selected.overall.interval.high})</>
                  ) : null}
                  <span className="ct-muted ct-small"> — {selected.overall.note}</span>
                </p>
              ) : null}
            </Card>

            <Card title="Hard constraints" headingLevel={3}>
              <ul className="ct-list-plain ct-stack" data-testid="hard-constraints">
                {selected.hard_constraints.map((h) => (
                  <li key={h.key} className="ct-row">
                    <Badge tone={CONSTRAINT_TONE[h.status]}>{CONSTRAINT_LABEL[h.status]}</Badge>
                    <span>
                      <strong>{h.label}.</strong> {h.detail}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="ct-small ct-muted">Hard constraints are shown separately and do not change either score, so a high score can still fail one.</p>
            </Card>

            <div className="mt-scores">
              <ScoreBlock kind="near" title="Near-term fit" description="How well your current, evidenced skills cover what the posting lists." group={selected.near_term_fit} testId="near-term" />
              <ScoreBlock kind="strat" title="Strategic value" description="Career utility: how much the role moves you toward your stated target roles." group={selected.strategic_value} testId="strategic" />
            </div>

            <Card title="Skills and the evidence behind them" headingLevel={3}>
              <TableRegion label="Skills and evidence strength">
                <table className="ct-table" data-testid="skill-evidence">
                  <thead>
                    <tr>
                      <th scope="col">Skill the posting lists</th>
                      <th scope="col">Evidence strength</th>
                      <th scope="col">Claims</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.skills.map((s) => (
                      <tr key={s.skill}>
                        <th scope="row">{skillLabel(s.skill)}</th>
                        <td>
                          <TruthBadge status={s.evidence_strength} />
                        </td>
                        <td>{s.claim_ids.length === 0 ? "None" : <Link to="/evidence">{s.claim_ids.length} claim{s.claim_ids.length === 1 ? "" : "s"}</Link>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableRegion>
              <div className="ct-grid ct-grid--2">
                <div>
                  <h4>Skill gaps</h4>
                  {missing.length === 0 ? <p className="ct-muted">No listed skill is missing.</p> : <ul data-testid="skill-gaps">{missing.map((s) => <li key={s.skill}>{skillLabel(s.skill)}{s.evidence_strength === "contradicted" ? " (your claim is contradicted)" : ""}</li>)}</ul>}
                </div>
                <div>
                  <h4>Claimed but weakly evidenced</h4>
                  {weak.length === 0 ? <p className="ct-muted">None.</p> : <ul>{weak.map((s) => <li key={s.skill}>{skillLabel(s.skill)} — {s.evidence_strength}</li>)}</ul>}
                </div>
              </div>
            </Card>

            {selected.counterfactuals && selected.counterfactuals.length > 0 ? (
              <Card title="Hypothetical changes (from the engine)" headingLevel={3}>
                <ul data-testid="counterfactuals">
                  {selected.counterfactuals.map((cf, i) => (
                    <li key={`${i}-${cf.change}`}>
                      {cf.change}: score would change by {cf.score_delta > 0 ? "+" : ""}
                      {cf.score_delta}. <span className="ct-muted ct-small">{cf.status}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}

            <Card title="What the engine does not know" headingLevel={3}>
              <ul data-testid="unknown-inputs">
                {selected.unknown_inputs.map((u, i) => (
                  <li key={`${i}-${u}`}>{humanise(u)}</li>
                ))}
              </ul>
              {selected.warnings.length > 0 ? (
                <Banner tone="warning" title="Warnings from the engine">
                  <ul>{selected.warnings.map((w, i) => <li key={`${i}-${w}`}>{w}</li>)}</ul>
                </Banner>
              ) : null}
              <p className="ct-small ct-muted">
                To check this yourself: read the posting’s requirements, compare them with your evidence vault, and decide whether
                the unknowns above matter to you.
              </p>
            </Card>
          </div>
        ) : (
          <EmptyState title="Select a posting to see its explanation." />
        )}
      </div>
    </Page>
  );
}

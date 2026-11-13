import { Link } from "react-router-dom";
import { Badge, Card, Icon, ScorePair, type IconName } from "@careertwin/ui";
import { Page } from "@/components/common";
import { daysWithoutFeedback, STAGE_LABELS, STAGES } from "@/lib/applications";
import { duplicateKey, formatCompensation, formatDate, humanise, skillLabel } from "@/lib/format";
import { reviewVariant } from "@/lib/resumeLint";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

interface Action {
  icon: IconName;
  title: string;
  detail: string;
  to: string;
  tone?: "warning";
}

/** One linked fact in the summary line. Counts of records only; nothing is estimated. */
function Fact({ label, value, note, to }: { label: string; value: string | number; note?: string; to: string }) {
  return (
    <Link to={to} className="ov-fact">
      <span className="ov-fact__label">{label}</span>
      <span className="ov-fact__value">{value}</span>
      {note ? <span className="ov-fact__note">{note}</span> : null}
    </Link>
  );
}

export default function OverviewPage() {
  const { bundle, workspace, clock } = useApp();
  const { applications, jobById, matchByJob, appByJob } = useIndexes();
  const jobsReport = bundle.reports.find((r) => r.adapter.id === "jobs");
  const rejectedJobs = jobsReport ? jobsReport.rejected.length : 0;
  const claims = workspace.claims;
  const count = (s: string) => claims.filter((c) => c.status === s).length;
  const supported = count("supported");
  const selfReported = count("self-reported");
  const unverified = count("unverified");
  const contradicted = count("contradicted");
  const blockedClaims = unverified + contradicted;
  const open = applications.filter((a) => a.stage !== "closed");
  const waiting = applications.filter((a) => daysWithoutFeedback(a, clock) !== null);
  const reviews = workspace.resumeVariants.map((v) => ({ v, r: reviewVariant(v, claims, clock) }));
  const approved = reviews.filter((x) => x.r.approvalCurrent).length;
  const running = workspace.experiments.filter((e) => e.status === "running" || e.status === "preregistered").length;
  const dueReminders = workspace.reminders.filter((r) => !r.done).sort((a, b) => a.due_on.localeCompare(b.due_on));

  // Postings ordered by the engine's own near-term fit; unscored last. No blended score is invented here.
  const closest = bundle.jobs
    .map((j) => ({ j, m: matchByJob.get(j.id) ?? null }))
    .filter((x) => x.m !== null)
    .sort((a, b) => (b.m!.near_term_fit.score ?? -1) - (a.m!.near_term_fit.score ?? -1) || a.j.id.localeCompare(b.j.id))
    // Likely duplicates (same employer and title) are listed once here; Discover Jobs shows every capture.
    .filter((x, i, all) => all.findIndex((y) => duplicateKey(y.j) === duplicateKey(x.j)) === i);

  const actions: Action[] = [];
  for (const a of waiting) {
    actions.push({
      icon: "clock",
      title: `No reply recorded for ${daysWithoutFeedback(a, clock)} days`,
      detail: `${jobById.get(a.job_id)?.title ?? a.job_id}. An unknown outcome, not a rejection.`,
      to: `/applications?app=${encodeURIComponent(a.id)}`,
    });
  }
  for (const x of reviews.filter((y) => y.r.approvalStale)) {
    actions.push({ icon: "resume", title: `${x.v.label} changed after approval`, detail: "Review and approve the new text before it can be exported.", to: `/resume?variant=${encodeURIComponent(x.v.id)}`, tone: "warning" });
  }
  if (blockedClaims > 0) {
    actions.push({
      icon: "evidence",
      title: `${blockedClaims} claim${blockedClaims === 1 ? "" : "s"} cannot be used in a résumé yet`,
      detail: `${unverified} unverified and ${contradicted} contradicted. Attach evidence or retire them.`,
      to: "/evidence",
    });
  }
  const top = closest.find((x) => !x.m!.hard_constraints.some((h) => h.status === "fail") && !appByJob.has(x.j.id) && x.m!.near_term_fit.score !== null);
  if (top) {
    actions.push({ icon: "discover", title: `Review ${top.j.title}`, detail: `${top.j.organization}: the highest near-term fit you have not acted on, and it passes your hard constraints.`, to: `/matching?job=${encodeURIComponent(top.j.id)}` });
  }
  const gaps = new Map<string, number>();
  for (const x of closest.slice(0, 8)) for (const s of x.m!.skills) if (s.evidence_strength === "missing") gaps.set(s.skill, (gaps.get(s.skill) ?? 0) + 1);
  const gap = [...gaps.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
  if (gap && gap[1] > 1) {
    actions.push({ icon: "evidence", title: `${skillLabel(gap[0])} is missing in ${gap[1]} of your 8 closest postings`, detail: "No claim in your evidence vault covers it.", to: "/evidence" });
  }
  for (const r of dueReminders.slice(0, 3)) {
    actions.push({ icon: "bookmark", title: `Reminder for ${formatDate(`${r.due_on}T00:00:00Z`)}`, detail: r.text, to: "/applications" });
  }

  const stageCounts = STAGES.map((s) => ({ s, n: applications.filter((a) => a.stage === s).length }));
  const maxStage = Math.max(1, ...stageCounts.map((x) => x.n));
  const evidenceParts = [
    { key: "supported", label: "Supported", n: supported },
    { key: "self", label: "Self-reported", n: selfReported },
    { key: "unverified", label: "Unverified", n: unverified },
    { key: "contradicted", label: "Contradicted", n: contradicted },
  ];

  const steps = [
    { to: "/jobs", label: "Discover jobs", detail: "Search the synthetic feed and shortlist a posting.", done: workspace.shortlist.length > 0 },
    { to: "/matching", label: "Understand the match", detail: "See why a posting scores high or low, and what is unknown.", done: workspace.shortlist.length > 0 },
    { to: "/evidence", label: "Check your evidence", detail: "Every résumé line must trace to a claim with a truth state.", done: blockedClaims === 0 && claims.length > 0 },
    { to: "/resume", label: "Build and approve a résumé variant", detail: "Edit, compare, and approve the exact text before any export.", done: approved > 0 },
    { to: "/applications", label: "Track the application", detail: "Record each step yourself. Nothing is ever submitted for you.", done: applications.length > 0 },
    { to: "/experiments", label: "Run an honest experiment", detail: "Preregister, randomise, and read results with uncertainty.", done: workspace.experiments.some((e) => e.design_locked_at !== null) },
    { to: "/atlas", label: "Explore the career atlas", detail: "A 2-D picture of skill space, with its distortion stated.", done: false },
    { to: "/simulator", label: "Compare transition scenarios", detail: "Hypothetical plans, costs you enter, and your own weights.", done: Object.keys(workspace.scenarioEdits).length > 0 },
  ];

  return (
    <Page title="Overview" lead={`What needs your attention, computed from your own records as of the ${formatDate(clock)} snapshot. Every employer, posting and outcome here is fictional.`}>
      <div className="ov-summary" data-testid="overview-stats">
        <Fact label="Postings" value={bundle.jobs.length} note={`${rejectedJobs} malformed record${rejectedJobs === 1 ? "" : "s"} rejected`} to="/jobs" />
        <Fact label="Shortlist" value={workspace.shortlist.length} note="saved by you" to="/jobs" />
        <Fact label="Evidence" value={`${supported}/${claims.length}`} note="claims supported by evidence" to="/evidence" />
        <Fact label="Resume variants" value={workspace.resumeVariants.length} note={`${approved} approved by you`} to="/resume" />
        <Fact label="Applications" value={open.length} note={`${waiting.length} with no reply recorded`} to="/applications" />
        <Fact label="Experiments" value={workspace.experiments.length} note={`${running} locked or running`} to="/experiments" />
      </div>

      <div className="ov-grid">
        <div className="ov-main">
          <Card title="Next actions" data-testid="next-actions">
            {actions.length === 0 ? (
              <p className="ct-muted">Nothing is waiting on you.</p>
            ) : (
              <ol className="ct-list-plain ov-actions">
                {actions.map((a, i) => (
                  <li key={`${i}-${a.title}`}>
                    <Link to={a.to} className="ov-action">
                      <span className={a.tone === "warning" ? "ov-action__icon ov-action__icon--warn" : "ov-action__icon"}>
                        <Icon name={a.icon} size={16} />
                      </span>
                      <span className="ov-action__text">
                        <strong>{a.title}</strong>
                        <span>{a.detail}</span>
                      </span>
                      <Icon name="arrow" size={16} className="ov-action__go" />
                    </Link>
                  </li>
                ))}
              </ol>
            )}
          </Card>

          <Card title="Closest postings" actions={<Link to="/matching" className="ct-small">All ranked postings</Link>}>
            <p className="ct-small ct-muted">Ordered by the engine’s near-term fit. Strategic value is shown beside it, never merged into it. Neither is a probability of being hired.</p>
            <ul className="ct-list-plain ov-postings">
              {closest.slice(0, 6).map(({ j, m }) => {
                const failed = m!.hard_constraints.filter((h) => h.status === "fail");
                const comp = formatCompensation(j.compensation);
                return (
                  <li key={j.id} className="ov-posting">
                    <div className="ov-posting__title">
                      <Link to={`/matching?job=${encodeURIComponent(j.id)}`}>{j.title}</Link>
                      <span className="ct-small ct-muted">
                        {j.organization}
                        {failed.length > 0 ? null : comp.known ? `, ${comp.text}` : ", pay not disclosed"}
                      </span>
                      {failed.length > 0 ? (
                        <span>
                          <Badge tone="danger">Fails {failed[0]!.label.toLowerCase()}</Badge>
                        </span>
                      ) : null}
                    </div>
                    <ScorePair near={m!.near_term_fit.score} strategic={m!.strategic_value.score} />
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>

        <div className="ov-side">
          <Card title="Pipeline" actions={<Link to="/applications" className="ct-small">Board</Link>}>
            <ul className="ct-list-plain ov-stages">
              {stageCounts.map(({ s, n }) => (
                <li key={s} className={n === 0 ? "ov-stage ov-stage--empty" : "ov-stage"}>
                  <span>{STAGE_LABELS[s]}</span>
                  <span className="ov-stage__bar" aria-hidden="true">
                    <i style={{ width: `${(n / maxStage) * 100}%` }} />
                  </span>
                  <b>{n}</b>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Evidence vault" actions={<Link to="/evidence" className="ct-small">Open</Link>}>
            {claims.length === 0 ? (
              <p className="ct-muted">The vault is empty. Add a claim to start.</p>
            ) : (
              <>
                <div className="ov-evbar" role="img" aria-label={evidenceParts.map((p) => `${p.label} ${p.n}`).join(", ")}>
                  {evidenceParts.map((p) => (p.n > 0 ? <i key={p.key} className={`ov-ev--${p.key}`} style={{ flexGrow: p.n }} /> : null))}
                </div>
                <ul className="ct-list-plain ov-evlegend ct-small">
                  {evidenceParts.map((p) => (
                    <li key={p.key}>
                      <i className={`ov-ev--${p.key}`} aria-hidden="true" />
                      {p.label} <b>{p.n}</b>
                    </li>
                  ))}
                </ul>
                <p className="ct-small ct-muted">Only supported and self-reported claims can appear in a résumé.</p>
              </>
            )}
          </Card>

          <Card title="Experiments" actions={<Link to="/experiments" className="ct-small">Open</Link>}>
            {workspace.experiments.length === 0 ? (
              <p className="ct-muted">No experiments yet.</p>
            ) : (
              <ul className="ct-list-plain ct-stack ct-small">
                {workspace.experiments.slice(0, 4).map((e) => (
                  <li key={e.id} className="ct-row ct-row--between">
                    <span>{e.title || "Untitled draft"}</span>
                    <Badge tone={e.status === "running" ? "info" : "neutral"}>{humanise(e.status)}</Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="ct-small ct-muted">No result is reported until a preregistered window closes.</p>
          </Card>

          <Card title="What this build does not do">
            <ul className="ct-stack ct-small ov-limits">
              <li>It does not submit applications, message recruiters, or contact anyone.</li>
              <li>It is not connected to Greenhouse, Lever, Ashby, LinkedIn or any other live source.</li>
              <li>It does not predict hiring. Map distance is not fit; experiment results here are simulated.</li>
              <li>It stores nothing outside this browser. Export or erase everything in <Link to="/settings">Settings</Link>.</li>
            </ul>
          </Card>
        </div>
      </div>

      <details className="ov-walk">
        <summary>How the pieces fit together (8 steps)</summary>
        <ol className="ov-steps" data-testid="pipeline-steps">
          {steps.map((s) => (
            <li key={s.to}>
              <Link to={s.to}>{s.label}</Link>
              {s.done ? <Badge tone="success">Started</Badge> : null}
              <span className="ct-muted ct-small">{s.detail}</span>
            </li>
          ))}
        </ol>
      </details>
    </Page>
  );
}

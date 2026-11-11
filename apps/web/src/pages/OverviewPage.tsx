import { Link } from "react-router-dom";
import { Badge, Banner, Card } from "@careertwin/ui";
import { Page } from "@/components/common";
import { daysWithoutFeedback } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import { reviewVariant } from "@/lib/resumeLint";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

function Stat({ label, value, note, to }: { label: string; value: string | number; note?: string; to: string }) {
  return (
    <Card as="div" className="app-stat">
      <span className="ct-muted ct-small">{label}</span>
      <span className="app-stat__value">{value}</span>
      {note ? <span className="ct-small ct-muted">{note}</span> : null}
      <Link to={to} className="ct-small">
        Open {label.toLowerCase()}
      </Link>
    </Card>
  );
}

export default function OverviewPage() {
  const { bundle, workspace, clock } = useApp();
  const { applications, jobById } = useIndexes();
  const jobsReport = bundle.reports.find((r) => r.adapter.id === "jobs");
  const rejectedJobs = jobsReport ? jobsReport.rejected.length : 0;
  const supported = workspace.claims.filter((c) => c.status === "supported").length;
  const blockedClaims = workspace.claims.filter((c) => c.status === "contradicted" || c.status === "unverified").length;
  const open = applications.filter((a) => a.stage !== "closed");
  const waiting = applications.filter((a) => daysWithoutFeedback(a, clock) !== null);
  const reviews = workspace.resumeVariants.map((v) => ({ v, r: reviewVariant(v, workspace.claims, clock) }));
  const approved = reviews.filter((x) => x.r.approvalCurrent).length;
  const running = workspace.experiments.filter((e) => e.status === "running" || e.status === "preregistered").length;
  const dueReminders = workspace.reminders.filter((r) => !r.done).sort((a, b) => a.due_on.localeCompare(b.due_on));

  const steps = [
    { to: "/jobs", label: "Discover jobs", detail: "Search the synthetic feed and shortlist a posting.", done: workspace.shortlist.length > 0 },
    { to: "/matching", label: "Understand the match", detail: "See why a posting scores high or low, and what is unknown.", done: workspace.shortlist.length > 0 },
    { to: "/evidence", label: "Check your evidence", detail: "Every résumé line must trace to a claim with a truth state.", done: blockedClaims === 0 && workspace.claims.length > 0 },
    { to: "/resume", label: "Build and approve a résumé variant", detail: "Edit, compare, and approve the exact text before any export.", done: approved > 0 },
    { to: "/applications", label: "Track the application", detail: "Record each step yourself. Nothing is ever submitted for you.", done: applications.length > 0 },
    { to: "/experiments", label: "Run an honest experiment", detail: "Preregister, randomise, and read results with uncertainty.", done: workspace.experiments.some((e) => e.design_locked_at !== null) },
    { to: "/atlas", label: "Explore the career atlas", detail: "A 2-D picture of skill space, with its distortion stated.", done: false },
    { to: "/simulator", label: "Compare transition scenarios", detail: "Hypothetical plans, costs you enter, and your own weights.", done: Object.keys(workspace.scenarioEdits).length > 0 },
  ];

  return (
    <Page title="Overview" lead="A fully offline, synthetic walk-through of CareerTwin: from a fictional job feed to matching, résumé variants, application tracking, experiments, the career atlas and transition scenarios.">
      <div className="ct-grid ct-grid--stats" data-testid="overview-stats">
        <Stat label="Postings" value={bundle.jobs.length} note={`${rejectedJobs} malformed record${rejectedJobs === 1 ? "" : "s"} rejected`} to="/jobs" />
        <Stat label="Shortlist" value={workspace.shortlist.length} to="/jobs" />
        <Stat label="Evidence" value={`${supported}/${workspace.claims.length}`} note="claims supported by evidence" to="/evidence" />
        <Stat label="Resume variants" value={workspace.resumeVariants.length} note={`${approved} approved by you`} to="/resume" />
        <Stat label="Applications" value={open.length} note={`${waiting.length} with no reply recorded`} to="/applications" />
        <Stat label="Experiments" value={workspace.experiments.length} note={`${running} locked or running`} to="/experiments" />
      </div>

      <div className="ct-grid ct-grid--2">
        <Card title="Walk through the synthetic pipeline">
          <ol className="ct-stack" data-testid="pipeline-steps">
            {steps.map((s) => (
              <li key={s.to}>
                <div className="ct-row">
                  <Link to={s.to}>
                    <strong>{s.label}</strong>
                  </Link>
                  {s.done ? <Badge tone="success">Started</Badge> : null}
                </div>
                <span className="ct-muted ct-small">{s.detail}</span>
              </li>
            ))}
          </ol>
        </Card>

        <div className="ct-stack">
          <Card title="Needs your attention">
            {blockedClaims === 0 && waiting.length === 0 && reviews.every((x) => !x.r.approvalStale) && dueReminders.length === 0 ? (
              <p className="ct-muted">Nothing is waiting on you.</p>
            ) : (
              <ul className="ct-stack">
                {blockedClaims > 0 ? (
                  <li>
                    <Link to="/evidence">{blockedClaims} claim{blockedClaims === 1 ? "" : "s"}</Link> are unverified or contradicted and cannot be used in a résumé.
                  </li>
                ) : null}
                {waiting.map((a) => (
                  <li key={a.id}>
                    <Link to={`/applications?app=${encodeURIComponent(a.id)}`}>{jobById.get(a.job_id)?.title ?? a.job_id}</Link>: no reply recorded for{" "}
                    {daysWithoutFeedback(a, clock)} days. This is an unknown outcome, not a rejection.
                  </li>
                ))}
                {reviews.filter((x) => x.r.approvalStale).map((x) => (
                  <li key={x.v.id}>
                    <Link to={`/resume?variant=${encodeURIComponent(x.v.id)}`}>{x.v.label}</Link> changed after approval.
                  </li>
                ))}
                {dueReminders.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    Reminder for {formatDate(`${r.due_on}T00:00:00Z`)}: {r.text}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="What this build does not do">
            <ul className="ct-stack ct-small">
              <li>It does not submit applications, message recruiters, or contact anyone. Those are always your own manual actions.</li>
              <li>It is not connected to Greenhouse, Lever, Ashby, LinkedIn or any other live source. All postings are fictional.</li>
              <li>It does not predict hiring. Scores are relative heuristics; map distance is not fit; experiment results here are simulated.</li>
              <li>It does not store anything outside this browser. You can export or erase everything in <Link to="/settings">Settings</Link>.</li>
            </ul>
          </Card>
        </div>
      </div>

      <Banner tone="info" title={`Snapshot date: ${formatDate(clock)}.`}>
        Ages such as “captured 3 days before the snapshot” are measured against this fixed fixture date, not today, so the demo is
        reproducible.
      </Banner>
    </Page>
  );
}

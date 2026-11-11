import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Banner, Button, Card, Checkbox, EmptyState, Facts, SelectField, TabPanel, TableRegion, Tabs, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import { ConfirmDialog, ExternalLinkDialog, Page } from "@/components/common";
import { OUTCOME_STATUSES, type ApplicationEventType, type JobPosting, type OutcomeStatus } from "@/contracts/types";
import { EVENT_LABELS, STAGES, STAGE_LABELS, allowedNext, daysWithoutFeedback, sortEvents, type Application } from "@/lib/applications";
import { formatDate, formatDateTime, truncate } from "@/lib/format";
import { reviewVariant } from "@/lib/resumeLint";
import { downloadText, newId, toCsv } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes, userEvent } from "@/state/selectors";

const RESPONSE_EVENTS: ApplicationEventType[] = ["recruiter_screen", "interview", "rejected", "offer"];

export default function ApplicationsPage() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { applications, jobById } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("board");
  const [nextType, setNextType] = useState<ApplicationEventType | "">("");
  const [occurred, setOccurred] = useState("");
  const [notes, setNotes] = useState("");
  const [variantId, setVariantId] = useState("");
  const [outcome, setOutcome] = useState<OutcomeStatus>("self-reported");
  const [submitOpen, setSubmitOpen] = useState(false);
  const [submitChecked, setSubmitChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkJob, setLinkJob] = useState<JobPosting | null>(null);
  const [newJob, setNewJob] = useState("");
  const [remDate, setRemDate] = useState("");
  const [remText, setRemText] = useState("");

  const selected: Application | null = applications.find((a) => a.id === params.get("app")) ?? null;
  const select = (id: string) => {
    setParams({ app: id });
    setNextType("");
    setNotes("");
    setOccurred("");
    setError(null);
  };
  const job = selected ? jobById.get(selected.job_id) ?? null : null;
  const options = selected ? allowedNext(selected) : [];
  const variants = workspace.resumeVariants;
  const reviews = useMemo(() => new Map(variants.map((v) => [v.id, reviewVariant(v, workspace.claims, clock)])), [variants, workspace.claims, clock]);
  const currentVariant = selected?.resumeVariantId ? variants.find((v) => v.id === selected.resumeVariantId) ?? null : null;
  const currentReview = currentVariant ? reviews.get(currentVariant.id) ?? null : null;
  const today = new Date().toISOString().slice(0, 10);
  const trackable = bundle.jobs.filter((j) => !applications.some((a) => a.job_id === j.id));

  const record = (type: ApplicationEventType) => {
    if (!selected) return;
    setError(null);
    const occurredAt = occurred ? `${occurred}T12:00:00Z` : new Date().toISOString();
    if (occurred && occurred > today) return setError("The date cannot be in the future.");
    let resume_variant_id = selected.resumeVariantId;
    let approval_token_ref: string | null = null;
    if (type === "drafted") {
      if (!variantId) return setError("Choose the résumé variant you are drafting with.");
      resume_variant_id = variantId;
    }
    if (type === "approved") {
      if (!currentVariant || !currentReview) return setError("Draft with a résumé variant first.");
      if (!currentReview.exportAllowed) return setError(`The résumé variant is not ready: ${currentReview.exportBlockers.join(" ")}`);
      approval_token_ref = currentVariant.approval?.approval_token_ref ?? null;
    }
    if (type === "submitted") approval_token_ref = selected.approvalTokenRef;
    const refused = dispatch({
      type: "event/append",
      event: userEvent({
        application_id: selected.id,
        job_id: selected.job_id,
        event_type: type,
        occurred_at: occurredAt,
        resume_variant_id: type === "note" ? null : resume_variant_id,
        approval_token_ref,
        experiment_id: selected.experimentId,
        outcome_status: RESPONSE_EVENTS.includes(type) ? outcome : type === "submitted" ? "self-reported" : "observed",
        notes: notes.trim(),
      }),
    });
    if (refused) return setError(refused);
    announce(`Recorded: ${EVENT_LABELS[type]}.`);
    setNextType("");
    setNotes("");
    setOccurred("");
    setSubmitOpen(false);
    setSubmitChecked(false);
  };

  const onRecordClick = () => {
    if (!nextType) return setError("Choose what happened.");
    if (nextType === "submitted") return setSubmitOpen(true);
    record(nextType);
  };

  const exportCsv = () => {
    const rows = sortEvents(workspace.applicationEvents).map((e) => {
      const j = jobById.get(e.job_id);
      return [e.application_id, j?.title ?? e.job_id, j?.organization ?? "", e.event_type, e.occurred_at ?? "", e.recorded_at, e.actor, e.source, e.outcome_status, e.resume_variant_id ?? "", e.approval_token_ref ?? "", e.notes ?? ""];
    });
    downloadText("careertwin-application-events.csv", toCsv(["application_id", "posting", "employer", "event_type", "occurred_at", "recorded_at", "actor", "source", "outcome_status", "resume_variant_id", "approval_token_ref", "notes"], rows), "text/csv");
    announce("Exported application events as CSV to this device.");
  };

  const startTracking = () => {
    if (!newJob) return;
    const id = newId("app");
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: id, job_id: newJob, event_type: "saved", notes: "Saved from Applications." }) });
    if (!refused) {
      select(id);
      setNewJob("");
      announce("Saved. Nothing was sent to the employer.");
    }
  };

  const reminders = selected ? workspace.reminders.filter((r) => r.application_id === selected.id) : [];
  const appLabel = (a: Application) => jobById.get(a.job_id)?.title ?? a.job_id;

  const appCard = (a: Application) => {
    const j = jobById.get(a.job_id);
    const waiting = daysWithoutFeedback(a, clock);
    return (
      <button type="button" className="app-pick" aria-current={selected?.id === a.id ? "true" : undefined} onClick={() => select(a.id)} data-testid={`app-${a.id}`}>
        <strong>{truncate(j?.title ?? a.job_id, 60)}</strong>
        <span className="ct-muted ct-small">{truncate(j?.organization ?? "Posting not in feed", 60)}</span>
        <span className="ct-row ct-small">
          <span>{EVENT_LABELS[a.lastEvent.event_type]} · {formatDate(a.lastEvent.occurred_at ?? a.lastEvent.recorded_at)}</span>
          {a.closedAs ? <Badge tone={a.closedAs === "rejected" ? "danger" : "neutral"}>{a.closedAs === "rejected" ? "Rejected" : "Withdrawn"}</Badge> : null}
          {waiting !== null ? <Badge tone="warning">No reply recorded · {waiting} days</Badge> : null}
          {a.experimentId ? <Badge tone="info">In experiment</Badge> : null}
        </span>
      </button>
    );
  };

  return (
    <Page
      title="Applications"
      lead="A record of what you did and what came back. Every entry is an immutable event you record yourself. CareerTwin never submits an application, and it never contacts an employer or recruiter."
      actions={<Button onClick={exportCsv} disabled={workspace.applicationEvents.length === 0} data-testid="export-csv">Export events (CSV)</Button>}
    >
      <Card title="Track a posting">
        <div className="app-toolbar">
          <SelectField label="Posting" value={newJob} onChange={(e) => setNewJob(e.target.value)} options={[{ value: "", label: "Choose a posting…" }, ...trackable.map((j) => ({ value: j.id, label: `${truncate(j.title, 50)} — ${truncate(j.organization, 30)}${workspace.shortlist.includes(j.id) ? " (shortlisted)" : ""}` }))]} data-testid="new-app-job" />
          <Button onClick={startTracking} disabled={!newJob} data-testid="new-app-btn">
            Save as a new application
          </Button>
        </div>
      </Card>

      {applications.length === 0 ? (
        <EmptyState title="No applications are being tracked.">
          <p>Save a posting above or from <Link to="/jobs">Discover Jobs</Link>. Saving only creates a local record.</p>
        </EmptyState>
      ) : (
        <>
          <Tabs idBase="apps" label="Application views" active={tab} onChange={setTab} tabs={[{ id: "board", label: "Board" }, { id: "table", label: "Table" }, { id: "feedback", label: `Missing feedback (${applications.filter((a) => daysWithoutFeedback(a, clock) !== null).length})` }]} />
          <TabPanel idBase="apps" id="board" active={tab}>
            <div className="app-kanban" data-testid="kanban" role="group" aria-label="Applications by stage">
              {STAGES.map((stage) => {
                const inStage = applications.filter((a) => a.stage === stage);
                return (
                  <section key={stage} className="app-kanban__col" aria-label={`${STAGE_LABELS[stage]}: ${inStage.length}`} data-testid={`col-${stage}`}>
                    <h3>
                      <span>{STAGE_LABELS[stage]}</span>
                      <Badge>{inStage.length}</Badge>
                    </h3>
                    {inStage.length === 0 ? <p className="ct-muted ct-small">None</p> : null}
                    <ul className="ct-list-plain ct-stack">
                      {inStage.map((a) => (
                        <li key={a.id}>{appCard(a)}</li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
            <p className="ct-small ct-muted">Stages change only when you record an event in the detail panel below. There is no drag-and-drop, so the board works the same with a keyboard.</p>
          </TabPanel>
          <TabPanel idBase="apps" id="table" active={tab}>
            <TableRegion label="Applications table">
              <table className="ct-table" data-testid="apps-table">
                <thead>
                  <tr>
                    <th scope="col">Posting</th>
                    <th scope="col">Stage</th>
                    <th scope="col">Last event</th>
                    <th scope="col">Submitted</th>
                    <th scope="col">Résumé variant</th>
                    <th scope="col" className="ct-num">Events</th>
                    <th scope="col">Open</th>
                  </tr>
                </thead>
                <tbody>
                  {applications.map((a) => (
                    <tr key={a.id} aria-selected={selected?.id === a.id}>
                      <th scope="row">
                        {appLabel(a)}
                        <div className="ct-muted ct-small">{jobById.get(a.job_id)?.organization}</div>
                      </th>
                      <td>{STAGE_LABELS[a.stage]}{a.closedAs ? ` (${a.closedAs})` : ""}</td>
                      <td>{EVENT_LABELS[a.lastEvent.event_type]} · {formatDate(a.lastEvent.occurred_at ?? a.lastEvent.recorded_at)}</td>
                      <td>{a.submittedAt ? formatDate(a.submittedAt) : "Not submitted"}</td>
                      <td>{variants.find((v) => v.id === a.resumeVariantId)?.label ?? "None"}</td>
                      <td className="ct-num">{a.events.length}</td>
                      <td>
                        <Button size="sm" onClick={() => select(a.id)}>Details</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableRegion>
          </TabPanel>
          <TabPanel idBase="apps" id="feedback" active={tab}>
            {applications.filter((a) => daysWithoutFeedback(a, clock) !== null).length === 0 ? (
              <EmptyState title="No submitted application is waiting past 14 days." />
            ) : (
              <div className="ct-stack">
                <Banner tone="info" title="No reply is an unknown outcome, not a rejection.">
                  These stay “unknown” in every count and in experiment results until you record what happened.
                </Banner>
                <ul className="ct-list-plain ct-grid" data-testid="missing-feedback">
                  {applications.filter((a) => daysWithoutFeedback(a, clock) !== null).map((a) => (
                    <li key={a.id}>{appCard(a)}</li>
                  ))}
                </ul>
              </div>
            )}
          </TabPanel>
        </>
      )}

      {selected ? (
        <div className="app-split" data-testid="app-detail">
          <Card title={`Timeline — ${truncate(appLabel(selected), 60)}`}>
            <Facts
              items={[
                { label: "Employer", value: job?.organization ?? "Posting not in feed" },
                { label: "Stage", value: `${STAGE_LABELS[selected.stage]}${selected.closedAs ? ` (${selected.closedAs})` : ""}` },
                { label: "Résumé variant", value: currentVariant ? <Link to={`/resume?variant=${encodeURIComponent(currentVariant.id)}`}>{currentVariant.label}</Link> : "None yet" },
                { label: "Approval record", value: selected.approvalTokenRef ? <code>{selected.approvalTokenRef}</code> : "None" },
              ]}
            />
            <ol className="app-timeline" data-testid="timeline">
              {selected.events.map((e) => (
                <li key={e.id}>
                  <div className="ct-row">
                    <strong>{EVENT_LABELS[e.event_type]}</strong>
                    <Badge tone={e.outcome_status === "observed" ? "success" : e.outcome_status === "unknown" ? "warning" : "info"}>{e.outcome_status}</Badge>
                  </div>
                  <div className="ct-small ct-muted">
                    Happened {formatDate(e.occurred_at ?? e.recorded_at)} · recorded {formatDateTime(e.recorded_at)} by {e.actor} via {e.source}
                  </div>
                  {e.notes ? <p className="ct-small">{e.notes}</p> : null}
                </li>
              ))}
            </ol>
            <p className="ct-small ct-muted">Events are append-only: they cannot be edited or deleted. To correct something, add a note.</p>
            {job ? (
              <Button variant="ghost" onClick={() => setLinkJob(job)} data-testid="app-open-link">
                Posting link…
              </Button>
            ) : null}
          </Card>

          <div className="ct-stack">
            <Card title="Record what happened">
              {error ? (
                <Banner tone="danger" role="alert" title="Not recorded" data-testid="app-error">
                  {error}
                </Banner>
              ) : null}
              <SelectField label="What happened?" value={nextType} onChange={(e) => { setNextType(e.target.value as ApplicationEventType | ""); setError(null); }} options={[{ value: "", label: "Choose…" }, ...options.map((o) => ({ value: o, label: EVENT_LABELS[o] }))]} data-testid="next-event" />
              {nextType === "drafted" ? (
                <SelectField label="Résumé variant" value={variantId} onChange={(e) => setVariantId(e.target.value)} options={[{ value: "", label: "Choose a variant…" }, ...variants.map((v) => ({ value: v.id, label: `${v.label} (v${v.version})${reviews.get(v.id)?.exportAllowed ? " — approved" : " — not approved"}` }))]} hint="You can draft with an unapproved variant, but the next step needs your approval of its exact text." data-testid="draft-variant" />
              ) : null}
              {nextType === "approved" ? (
                <Banner tone={currentReview?.exportAllowed ? "success" : "warning"} title={currentReview?.exportAllowed ? "The résumé variant is approved." : "The résumé variant is not approved yet."} data-testid="approval-state">
                  {currentVariant ? (
                    <>
                      “{currentVariant.label}”. {currentReview?.exportAllowed ? "This step will cite your approval record." : <>Open it in <Link to={`/resume?variant=${encodeURIComponent(currentVariant.id)}`}>Resume Studio</Link> to resolve issues and approve.</>}
                    </>
                  ) : "No résumé variant has been drafted."}
                </Banner>
              ) : null}
              {RESPONSE_EVENTS.includes(nextType as ApplicationEventType) ? (
                <SelectField label="How do you know?" value={outcome} onChange={(e) => setOutcome(e.target.value as OutcomeStatus)} options={OUTCOME_STATUSES.map((o) => ({ value: o, label: o === "observed" ? "Observed (you have the message or record)" : o === "self-reported" ? "Self-reported (from memory)" : "Unknown / unsure" }))} />
              ) : null}
              <TextField label="Date it happened" type="date" max={today} value={occurred} onChange={(e) => setOccurred(e.target.value)} hint="Leave empty for today." data-testid="event-date" />
              <TextArea label="Note (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} data-testid="event-notes" />
              <Button variant="primary" onClick={onRecordClick} disabled={!nextType} data-testid="record-event">
                Record event
              </Button>
              {options.length === 1 ? <p className="ct-small ct-muted">This application is closed. Only notes can be added.</p> : null}
            </Card>

            <Card title="Reminders (optional)">
              {reminders.length === 0 ? <p className="ct-muted">No reminders for this application.</p> : (
                <ul className="ct-list-plain ct-stack" data-testid="reminders">
                  {reminders.map((r) => (
                    <li key={r.id} className="ct-row ct-row--between">
                      <Checkbox label={`${formatDate(`${r.due_on}T00:00:00Z`)} — ${r.text}`} checked={r.done} onChange={() => dispatch({ type: "reminder/toggle", id: r.id })} />
                      <Button size="sm" variant="ghost" onClick={() => dispatch({ type: "reminder/remove", id: r.id })} aria-label={`Delete reminder: ${r.text}`}>
                        Delete
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="app-toolbar">
                <TextField label="Remind me on" type="date" value={remDate} onChange={(e) => setRemDate(e.target.value)} data-testid="rem-date" />
                <TextField label="About" value={remText} onChange={(e) => setRemText(e.target.value)} maxLength={120} data-testid="rem-text" />
                <Button
                  onClick={() => {
                    dispatch({ type: "reminder/add", reminder: { id: newId("rem"), application_id: selected.id, due_on: remDate, text: remText.trim(), done: false } });
                    setRemDate("");
                    setRemText("");
                    announce("Reminder added. It only appears inside this app.");
                  }}
                  disabled={!/^\d{4}-\d{2}-\d{2}$/.test(remDate) || !remText.trim()}
                  data-testid="rem-add"
                >
                  Add reminder
                </Button>
              </div>
              <p className="ct-small ct-muted">Reminders are notes inside this app. They do not send notifications, emails or messages.</p>
            </Card>
          </div>
        </div>
      ) : applications.length > 0 ? (
        <EmptyState title="Select an application to see its timeline and record events." />
      ) : null}

      <ConfirmDialog
        open={submitOpen}
        title="Record that you submitted this application yourself?"
        confirmLabel="Record my manual submission"
        confirmDisabled={!submitChecked}
        testId="submit-dialog"
        onClose={() => { setSubmitOpen(false); setSubmitChecked(false); }}
        onConfirm={() => record("submitted")}
      >
        <Banner tone="warning" title="CareerTwin does not submit anything.">
          This only records a fact. If you have not applied on the employer’s own site yet, cancel and do that first.
        </Banner>
        <Checkbox label="I applied myself, outside CareerTwin, using the approved résumé variant." checked={submitChecked} onChange={(e) => setSubmitChecked(e.target.checked)} data-testid="submit-check" />
      </ConfirmDialog>
      <ExternalLinkDialog job={linkJob} onClose={() => setLinkJob(null)} />
    </Page>
  );
}

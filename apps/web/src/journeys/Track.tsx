import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Banner, Button, Checkbox, EmptyState, Icon, SelectField, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import { Page as Screen } from "@/components/common";
import type { ApplicationEventType, OutcomeStatus } from "@/contracts/types";
import { EVENT_LABELS, MISSING_FEEDBACK_DAYS, STAGE_LABELS, allowedNext, daysWithoutFeedback, sortEvents, type Application, type Stage } from "@/lib/applications";
import { formatDate, formatDateTime } from "@/lib/format";
import { attribution, originOf } from "@/lib/provenance";
import { reviewVariant } from "@/lib/resumeLint";
import { downloadText, newId, toCsv } from "@/lib/safety";
import { artifactMode, useApp } from "@/state/AppContext";
import { useIndexes, userEvent } from "@/state/selectors";
import { variantTitle } from "@/pages/ResumePage";

const FLOW: Stage[] = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "offer"];
const GROUPS: { id: string; label: string; stages: Stage[] }[] = [
  { id: "prepare", label: "Preparing", stages: ["saved", "drafted", "approved"] },
  { id: "applied", label: "Applied", stages: ["submitted"] },
  { id: "talking", label: "In conversation", stages: ["recruiter_screen", "interview", "offer"] },
  { id: "closed", label: "Closed", stages: ["closed"] },
];
/** Replies from an employer: the person states whether they observed it or are reporting it. */
const RESPONSES: ApplicationEventType[] = ["recruiter_screen", "interview", "rejected", "offer"];

interface NextAction {
  title: string;
  detail: string;
  urgent?: boolean;
}
function nextActionFor(app: Application, clock: string, approvedVariant: boolean): NextAction {
  const silent = daysWithoutFeedback(app, clock);
  switch (app.stage) {
    case "saved":
      return { title: "Tailor and approve a résumé for this posting", detail: "Adapt your résumé from your own claims, review it and approve the exact text. Then record that you are ready to apply." };
    case "drafted":
      return approvedVariant
        ? { title: "Record your approval", detail: "The résumé for this posting is approved. Record it here so the application cites your approval." }
        : { title: "Approve the résumé you drafted", detail: "Review the draft line by line. Export stays blocked until you approve the exact text." };
    case "approved":
      return { title: "Apply on the employer’s site yourself, then record it", detail: "CareerTwin never submits applications. When you have applied, record the date so follow-ups can be tracked." };
    case "submitted":
      return silent !== null
        ? { title: `No reply recorded for ${silent} days`, detail: "Silence is an unknown outcome, not a rejection. Consider one polite follow-up, then set a date for the next check.", urgent: true }
        : { title: "Wait for a reply", detail: `Nothing to do yet. If no reply is recorded after ${MISSING_FEEDBACK_DAYS} days, this application is flagged for a follow-up.` };
    case "recruiter_screen":
    case "interview":
      return { title: "Prepare for the conversation", detail: "Add notes on what was discussed and the next step you agreed. Record outcomes as they happen." };
    case "offer":
      return { title: "Decide on the offer", detail: "Record your decision when you make it. CareerTwin does not advise on offers." };
    default:
      return { title: "Closed", detail: app.closedAs ? `Closed as ${app.closedAs}.` : "No further action." };
  }
}

export default function TrackJourney() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { jobById, applications } = useIndexes();
  const { appId } = useParams();
  const navigate = useNavigate();
  const announce = useAnnounce();
  const artifacts = artifactMode();
  const origin = useMemo(() => originOf(bundle), [bundle]);
  // The application log only: simulated experiment-run units never reach it (see bundle.labEvents).
  const mine = applications;
  const selected = (appId ? mine.find((a) => a.id === appId) : null) ?? (appId ? null : mine[0] ?? null);
  const [note, setNote] = useState("");
  const [remDate, setRemDate] = useState("");
  const [submitOpen, setSubmitOpen] = useState(false);
  const [submitChecked, setSubmitChecked] = useState(false);
  const [showTemplate, setShowTemplate] = useState(false);
  const [nextType, setNextType] = useState<ApplicationEventType | "">("");
  const [occurred, setOccurred] = useState("");
  const [eventNotes, setEventNotes] = useState("");
  const [variantId, setVariantId] = useState("");
  const [outcome, setOutcome] = useState<OutcomeStatus>("self-reported");
  const [error, setError] = useState<string | null>(null);
  const [newJob, setNewJob] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const today = new Date().toISOString().slice(0, 10);

  // Switching applications clears any half-entered update.
  useEffect(() => {
    setNextType("");
    setOccurred("");
    setEventNotes("");
    setVariantId("");
    setError(null);
    setSubmitOpen(false);
    setSubmitChecked(false);
    setShowTemplate(false);
    if (appId && typeof window !== "undefined" && window.matchMedia("(max-width: 1000px)").matches) {
      window.scrollTo(0, 0);
      detailRef.current?.focus({ preventScroll: true });
    }
  }, [appId]);

  const sampleOnly = (a: Application) => a.events.every((e) => origin(e) === "sample");
  const job = selected ? jobById.get(selected.job_id) ?? null : null;
  const variant = selected?.resumeVariantId ? workspace.resumeVariants.find((v) => v.id === selected.resumeVariantId) ?? null : null;
  const variantReview = variant ? reviewVariant(variant, workspace.claims, clock) : null;
  const approvedVariant = workspace.resumeVariants.find((v) => v.target_job_id === selected?.job_id && v.approval && reviewVariant(v, workspace.claims, clock).approvalCurrent) ?? null;
  const action = selected ? nextActionFor(selected, clock, approvedVariant !== null) : null;
  const reminders = workspace.reminders.filter((r) => selected && r.application_id === selected.id);
  const stageIndex = selected ? FLOW.indexOf(selected.stage) : -1;
  const options = selected ? allowedNext(selected) : [];
  const trackable = bundle.jobs.filter((j) => !mine.some((a) => a.job_id === j.id));
  const labUnits = bundle.experimentUnitApplicationIds.length;

  /** Append one event the person states. Every gate is the reducer's; refusals are shown, never bypassed. */
  const append = (type: ApplicationEventType, extra: Partial<Parameters<typeof userEvent>[0]> = {}): string | null => {
    if (!selected) return "No application selected.";
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: selected.id, job_id: selected.job_id, event_type: type, experiment_id: selected.experimentId, ...extra }) });
    announce(refused ? `Not recorded: ${refused}` : `${EVENT_LABELS[type]} recorded.`);
    return refused;
  };

  const recordUpdate = () => {
    if (!selected) return;
    setError(null);
    if (!nextType) return setError("Choose what happened.");
    if (occurred && occurred > today) return setError("The date cannot be in the future.");
    const occurred_at = occurred ? `${occurred}T12:00:00Z` : new Date().toISOString();
    const extra: Partial<Parameters<typeof userEvent>[0]> = { occurred_at, notes: eventNotes.trim(), resume_variant_id: nextType === "note" ? null : selected.resumeVariantId };
    if (nextType === "drafted") {
      if (!variantId) return setError("Choose the résumé you are drafting with.");
      extra.resume_variant_id = variantId;
    }
    if (nextType === "approved") {
      if (!variant || !variantReview) return setError("Draft with a résumé first.");
      if (!variantReview.exportAllowed) return setError(`The résumé is not approved for export yet: ${variantReview.exportBlockers.join(" ")}`);
      extra.approval_token_ref = variant.approval?.approval_token_ref ?? null;
    }
    if (nextType === "submitted") {
      if (!submitChecked) return setSubmitOpen(true);
      extra.approval_token_ref = selected.approvalTokenRef;
      extra.resume_variant_id = selected.approvedVariantId ?? selected.resumeVariantId;
      extra.outcome_status = "self-reported";
    }
    if (RESPONSES.includes(nextType)) extra.outcome_status = outcome;
    const refused = append(nextType, extra);
    if (refused) return setError(refused);
    setNextType("");
    setOccurred("");
    setEventNotes("");
    setSubmitOpen(false);
    setSubmitChecked(false);
  };

  const exportCsv = () => {
    const rows = sortEvents(workspace.applicationEvents).map((e) => {
      const j = jobById.get(e.job_id);
      return [e.application_id, j?.title ?? e.job_id, j?.organization ?? "", e.event_type, e.occurred_at ?? "", e.recorded_at, e.actor, e.source, origin(e) === "sample" ? "sample data" : "recorded here", e.outcome_status, e.resume_variant_id ?? "", e.approval_token_ref ?? "", e.notes ?? ""];
    });
    downloadText("careertwin-application-events.csv", toCsv(["application_id", "posting", "employer", "event_type", "occurred_at", "recorded_at", "actor", "source", "origin", "outcome_status", "resume_variant_id", "approval_token_ref", "notes"], rows), "text/csv");
    announce("Exported your application log as CSV to this device.");
  };

  const startTracking = () => {
    if (!newJob) return;
    const id = newId("app");
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: id, job_id: newJob, event_type: "saved", notes: "Saved from Applications." }) });
    if (refused) return announce(`Not recorded: ${refused}`);
    setNewJob("");
    announce("Saved. Nothing was sent to the employer.");
    navigate(`/applications/${encodeURIComponent(id)}`);
  };

  return (
    <Screen
      title="Applications"
      lead="Each application, what it needs next, and when to follow up. CareerTwin never applies or contacts anyone: every step here is something you record."
      actions={
        <Button onClick={exportCsv} disabled={workspace.applicationEvents.length === 0} data-testid="export-csv">
          Export log (CSV)
        </Button>
      }
    >
      <div className="pt-track" data-view={appId ? "detail" : "list"}>
        <nav className="pt-tlist" aria-label="Applications">
          <p className="pt-muted pt-tcount" role="status" data-testid="app-count">
            {mine.length} application{mine.length === 1 ? "" : "s"}
          </p>
          {mine.length === 0 ? (
            <EmptyState title="You are not tracking any application yet.">
              <p>
                Save a posting from <Link to="/jobs">Discover</Link>, or choose one below.
              </p>
            </EmptyState>
          ) : (
            GROUPS.map((g) => {
              const items = mine.filter((a) => g.stages.includes(a.stage));
              if (items.length === 0) return null;
              return (
                <section key={g.id} className="pt-tgroup" aria-label={g.label}>
                  <h2 className="pt-tgroup__title">
                    {g.label} <span>{items.length}</span>
                  </h2>
                  <ul className="ct-list-plain">
                    {items.map((a) => {
                      const j = jobById.get(a.job_id);
                      const n = nextActionFor(a, clock, false);
                      return (
                        <li key={a.id}>
                          <Link to={`/applications/${encodeURIComponent(a.id)}`} className={selected?.id === a.id ? "pt-row pt-row--on" : "pt-row"} aria-current={selected?.id === a.id ? "true" : undefined} data-testid={`app-${a.id}`}>
                            <span className="pt-row__title">
                              {j?.title ?? a.job_id}
                              {sampleOnly(a) ? <span className="pt-tag">{artifacts ? "Pipeline sample" : "Demo"}</span> : null}
                            </span>
                            <span className="pt-row__meta">
                              {j?.organization ?? "Posting not in this feed"}, {a.closedAs ? `closed (${a.closedAs})` : STAGE_LABELS[a.stage].toLowerCase()}
                            </span>
                            <span className={n.urgent ? "pt-row__reason pt-tone--bad" : "pt-row__reason"}>{n.title}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })
          )}
          {trackable.length > 0 ? (
            <div className="pt-tadd">
              <SelectField label="Track another posting" value={newJob} onChange={(e) => setNewJob(e.target.value)} options={[{ value: "", label: "Choose a posting…" }, ...trackable.slice(0, 200).map((j) => ({ value: j.id, label: `${j.title}, ${j.organization}` }))]} data-testid="track-new-job" />
              <Button onClick={startTracking} disabled={!newJob} data-testid="track-new">
                Save to applications
              </Button>
            </div>
          ) : null}
          {labUnits > 0 ? (
            <p className="pt-labnote" data-testid="lab-note">
              <Icon name="info" size={14} />
              <span>
                The pipeline’s experiment run also simulated {labUnits} applications to test its approval gate. They are not your activity, so they are not listed or counted here. <Link to="/experiments?view=simulated">See them in Experiments</Link>.
              </span>
            </p>
          ) : null}
        </nav>

        {selected && job ? (
          <article className="pt-tdetail" aria-labelledby="pt-app-title" tabIndex={-1} ref={detailRef} data-testid="app-detail">
            <Button variant="ghost" className="pt-back" onClick={() => navigate("/applications")}>
              <Icon name="back" size={16} /> All applications
            </Button>
            <header className="pt-detail__head">
              <h2 id="pt-app-title">{job.title}</h2>
              <p className="pt-detail__org">
                {job.organization}. <Link to={`/jobs/${encodeURIComponent(job.id)}`}>Posting and fit</Link>
              </p>
              {sampleOnly(selected) ? (
                <p className="pt-muted" data-testid="sample-note">
                  {artifacts ? "Every entry below comes from the pipeline’s sample data, not from anything you did in this browser." : "Every entry below is demo data that ships with the app, not something you did."}
                </p>
              ) : null}
              {selected.closedAs ? (
                <p className="pt-closed">Closed: {selected.closedAs === "rejected" ? "rejected" : "withdrawn"}.</p>
              ) : (
                <ol className="pt-progress" aria-label="Stage">
                  {FLOW.map((s, i) => (
                    <li key={s} className={i < stageIndex ? "pt-progress__done" : i === stageIndex ? "pt-progress__on" : ""} aria-current={i === stageIndex ? "step" : undefined}>
                      {STAGE_LABELS[s]}
                    </li>
                  ))}
                </ol>
              )}
            </header>

            {action ? (
              <section className={action.urgent ? "pt-next pt-next--urgent" : "pt-next"} aria-labelledby="pt-next-title" data-testid="next-action">
                <p className="pt-next__label">Next action</p>
                <h3 id="pt-next-title">{action.title}</h3>
                <p>{action.detail}</p>
                <div className="pt-actions">
                  {selected.stage === "saved" || (selected.stage === "drafted" && !approvedVariant) ? (
                    <Link className="ct-btn ct-btn--primary" to={`/resume/tailor/${encodeURIComponent(job.id)}`} data-testid="next-tailor">
                      Tailor résumé
                    </Link>
                  ) : null}
                  {selected.stage === "drafted" && approvedVariant ? (
                    <Button variant="primary" onClick={() => append("approved", { resume_variant_id: approvedVariant.id, approval_token_ref: approvedVariant.approval!.approval_token_ref })}>
                      Record approval
                    </Button>
                  ) : null}
                  {selected.stage === "approved" ? (
                    <Button variant="primary" onClick={() => { setNextType("submitted"); setSubmitOpen(true); }} data-testid="record-applied">
                      I applied, record it
                    </Button>
                  ) : null}
                  {selected.stage === "submitted" ? (
                    <Button variant={action.urgent ? "primary" : "secondary"} onClick={() => setShowTemplate((s) => !s)} aria-expanded={showTemplate} data-testid="followup-template">
                      Prepare a follow-up note
                    </Button>
                  ) : null}
                </div>
                {submitOpen ? (
                  <div className="pt-inline-confirm" role="group" aria-label="Record that you applied">
                    <p>CareerTwin does not submit anything. Record this only after you applied yourself, outside CareerTwin, with the approved résumé.</p>
                    <Checkbox label="I applied myself, outside CareerTwin, using the approved résumé." checked={submitChecked} onChange={(e) => setSubmitChecked(e.target.checked)} data-testid="submit-check" />
                    <div className="pt-actions">
                      <Button onClick={() => { setSubmitOpen(false); setSubmitChecked(false); }}>Cancel</Button>
                      <Button
                        variant="primary"
                        disabled={!submitChecked}
                        data-testid="submit-confirm"
                        onClick={() => {
                          if (!append("submitted", { occurred_at: occurred ? `${occurred}T12:00:00Z` : new Date().toISOString(), approval_token_ref: selected.approvalTokenRef, resume_variant_id: selected.approvedVariantId ?? selected.resumeVariantId, outcome_status: "self-reported" })) {
                            setSubmitOpen(false);
                            setSubmitChecked(false);
                            setNextType("");
                          }
                        }}
                      >
                        Record application
                      </Button>
                    </div>
                  </div>
                ) : null}
                {showTemplate ? (
                  <div className="pt-template" data-testid="followup-text">
                    <p className="pt-muted">A template for you to edit and send yourself. CareerTwin does not write with AI and cannot send email.</p>
                    <pre>{`Subject: Following up on my application for ${job.title}\n\nHello,\n\nI applied for the ${job.title} role at ${job.organization} on ${selected.submittedAt ? formatDate(selected.submittedAt) : "[date]"} and wanted to confirm it was received. I remain very interested and would be glad to share anything else that helps.\n\nThank you,\n[Your name]`}</pre>
                  </div>
                ) : null}
              </section>
            ) : null}

            <div className="pt-tcols">
              <section className="pt-block" aria-labelledby="pt-follow">
                <h3 id="pt-follow">Follow-ups</h3>
                {reminders.length === 0 ? (
                  <p className="pt-muted">No follow-up date set.</p>
                ) : (
                  <ul className="ct-list-plain pt-rem">
                    {reminders.map((r) => (
                      <li key={r.id}>
                        <Checkbox label={`${formatDate(`${r.due_on}T00:00:00Z`)}: ${r.text}`} checked={r.done} onChange={() => dispatch({ type: "reminder/toggle", id: r.id })} />
                      </li>
                    ))}
                  </ul>
                )}
                <div className="pt-rem__add">
                  <TextField label="Follow up on" type="date" value={remDate} onChange={(e) => setRemDate(e.target.value)} data-testid="rem-date" />
                  <Button
                    disabled={!remDate}
                    onClick={() => {
                      dispatch({ type: "reminder/add", reminder: { id: newId("rem"), application_id: selected.id, due_on: remDate, text: `Follow up: ${job.title}`, done: false } });
                      setRemDate("");
                      announce("Follow-up date set. It shows here and on Overview; CareerTwin sends no notifications.");
                    }}
                    data-testid="rem-add"
                  >
                    Set follow-up
                  </Button>
                </div>
                <p className="pt-muted">Follow-up dates show here and on Overview. Email and push notifications are not available.</p>
              </section>

              <section className="pt-block" aria-labelledby="pt-docs">
                <h3 id="pt-docs">Résumé and approval</h3>
                <p>
                  {variant ? (
                    <Link to={`/resume?variant=${encodeURIComponent(variant.id)}`}>{variantTitle(variant, job.title)}</Link>
                  ) : (
                    <span className="pt-muted">No résumé linked yet.</span>
                  )}
                  {variant ? <span className="pt-muted">, version {variant.version}</span> : null}
                  {variant?.approval ? <span className="pt-muted">, approved {formatDate(variant.approval.approved_at)}</span> : null}
                </p>
                {selected.approvalTokenRef ? (
                  <p className="pt-muted" data-testid="approval-ref">
                    Approval record: <code>{selected.approvalTokenRef}</code>
                  </p>
                ) : null}
                <p className="pt-muted">Contacts and file attachments are not available in this build.</p>
              </section>
            </div>

            <section className="pt-block" aria-labelledby="pt-update">
              <h3 id="pt-update">Record what happened</h3>
              <p className="pt-muted">Only what you observed or were told. A missing reply is never recorded as a rejection.</p>
              <div className="pt-update">
                <SelectField
                  label="What happened?"
                  value={nextType}
                  onChange={(e) => { setNextType(e.target.value as ApplicationEventType | ""); setError(null); setSubmitOpen(false); setSubmitChecked(false); }}
                  options={[{ value: "", label: "Choose…" }, ...options.map((t) => ({ value: t, label: EVENT_LABELS[t] }))]}
                  data-testid="next-event"
                />
                <TextField label="When (optional)" type="date" max={today} value={occurred} onChange={(e) => setOccurred(e.target.value)} data-testid="event-date" />
                {nextType === "drafted" ? (
                  <SelectField label="Résumé used" value={variantId} onChange={(e) => setVariantId(e.target.value)} options={[{ value: "", label: "Choose a résumé…" }, ...workspace.resumeVariants.map((v) => ({ value: v.id, label: `${variantTitle(v, v.target_job_id ? jobById.get(v.target_job_id)?.title : null)} (version ${v.version})` }))]} data-testid="event-variant" />
                ) : null}
                {nextType && RESPONSES.includes(nextType) ? (
                  <SelectField label="How do you know?" value={outcome} onChange={(e) => setOutcome(e.target.value as OutcomeStatus)} options={[{ value: "self-reported", label: "I am reporting it" }, { value: "observed", label: "I have it in writing" }]} data-testid="event-outcome" />
                ) : null}
              </div>
              <TextArea label="Notes (optional)" value={eventNotes} onChange={(e) => setEventNotes(e.target.value)} rows={2} maxLength={500} data-testid="event-notes" />
              {error ? (
                <p className="pt-error" role="alert" data-testid="event-error">
                  {error}
                </p>
              ) : null}
              <div className="pt-actions">
                <Button variant="primary" onClick={recordUpdate} disabled={!nextType} data-testid="record-event">
                  {nextType === "submitted" ? "Continue" : "Record"}
                </Button>
              </div>
            </section>

            <section className="pt-block" aria-labelledby="pt-activity">
              <h3 id="pt-activity">Activity</h3>
              <ol className="pt-timeline" data-testid="timeline">
                {sortEvents(selected.events).slice().reverse().map((e) => (
                  <li key={e.id} data-origin={origin(e)}>
                    <strong>{EVENT_LABELS[e.event_type]}</strong>
                    <span className="pt-muted">
                      {" "}
                      {formatDateTime(e.occurred_at ?? e.recorded_at)}. {attribution(e, origin(e), artifacts)}
                      {e.outcome_status === "self-reported" ? ", self-reported" : ""}.
                    </span>
                    {e.notes ? <p>{e.notes}</p> : null}
                  </li>
                ))}
              </ol>
              <div className="pt-note">
                <TextArea label="Add a note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} data-testid="note-text" />
                <Button disabled={!note.trim()} onClick={() => { if (!append("note", { notes: note.trim() })) setNote(""); }} data-testid="note-add">
                  Add note
                </Button>
              </div>
            </section>
          </article>
        ) : appId ? (
          <EmptyState title="This application is not in your log." action={<Link to="/applications">All applications</Link>}>
            <p>Simulated applications from the pipeline’s experiment run are listed under Experiments, not here.</p>
          </EmptyState>
        ) : null}
      </div>
      {artifacts ? (
        <Banner tone="neutral" title="Read-only data source.">
          Changes you make here stay in this tab and are not saved.
        </Banner>
      ) : null}
    </Screen>
  );
}

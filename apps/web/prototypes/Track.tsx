import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Banner, Button, Checkbox, EmptyState, Icon, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import type { ApplicationEventType } from "@/contracts/types";
import { EVENT_LABELS, MISSING_FEEDBACK_DAYS, STAGE_LABELS, daysWithoutFeedback, sortEvents, type Application, type Stage } from "@/lib/applications";
import { formatDate, formatDateTime } from "@/lib/format";
import { reviewVariant } from "@/lib/resumeLint";
import { newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes, userEvent } from "@/state/selectors";
import { variantTitle } from "@/pages/ResumePage";
import { splitApplications } from "./lib";
import { Screen } from "./Screen";

const FLOW: Stage[] = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "offer"];
const GROUPS: { id: string; label: string; stages: Stage[] }[] = [
  { id: "prepare", label: "Preparing", stages: ["saved", "drafted", "approved"] },
  { id: "applied", label: "Applied", stages: ["submitted"] },
  { id: "talking", label: "In conversation", stages: ["recruiter_screen", "interview", "offer"] },
  { id: "closed", label: "Closed", stages: ["closed"] },
];

/** Who recorded an event, in plain words. Never upgrades a system or import entry to "you". */
function byWhom(actor: string, source: string): string {
  if (actor === "user") return source.includes("approval") ? "You approved" : "You recorded";
  if (actor === "system") return "Recorded by CareerTwin";
  return "Imported";
}

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
        ? { title: `No reply recorded for ${silent} days`, detail: "Silence is an unknown outcome, not a rejection. Consider one polite follow-up, then set a reminder for the next check.", urgent: true }
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
  const { mine, generated } = splitApplications(applications, bundle.experimentUnitApplicationIds);
  const selected = (appId ? mine.find((a) => a.id === appId) : null) ?? (appId ? null : mine[0] ?? null);
  const [note, setNote] = useState("");
  const [remDate, setRemDate] = useState("");
  const [submitOpen, setSubmitOpen] = useState(false);
  const [submitChecked, setSubmitChecked] = useState(false);
  const [showTemplate, setShowTemplate] = useState(false);

  const record = (type: ApplicationEventType, extra: Partial<Parameters<typeof userEvent>[0]> = {}) => {
    if (!selected) return;
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: selected.id, job_id: selected.job_id, event_type: type, ...extra }) });
    announce(refused ? `Not recorded: ${refused}` : `${EVENT_LABELS[type]} recorded.`);
    return refused;
  };

  const job = selected ? jobById.get(selected.job_id) ?? null : null;
  const variant = selected?.resumeVariantId ? workspace.resumeVariants.find((v) => v.id === selected.resumeVariantId) ?? null : null;
  const approvedVariant = workspace.resumeVariants.find((v) => v.target_job_id === selected?.job_id && v.approval && reviewVariant(v, workspace.claims, clock).approvalCurrent) ?? null;
  const action = selected ? nextActionFor(selected, clock, approvedVariant !== null) : null;
  const reminders = workspace.reminders.filter((r) => selected && r.application_id === selected.id);
  const stageIndex = selected ? FLOW.indexOf(selected.stage) : -1;

  return (
    <Screen title="Track" lead="Your applications, what each one needs next, and when to follow up. Every entry is something you recorded; CareerTwin does not apply or contact anyone.">
      <div className="pt-track" data-view={appId ? "detail" : "list"}>
        <nav className="pt-tlist" aria-label="Your applications">
          {mine.length === 0 ? (
            <EmptyState title="You are not tracking any application yet.">
              <p>
                Save a posting from <Link to="/discover">Discover</Link> to start.
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
                          <Link to={`/track/${encodeURIComponent(a.id)}`} className={selected?.id === a.id ? "pt-row pt-row--on" : "pt-row"} aria-current={selected?.id === a.id ? "true" : undefined} data-testid={`pt-app-${a.id}`}>
                            <span className="pt-row__title">{j?.title ?? a.job_id}</span>
                            <span className="pt-row__meta">{j?.organization ?? "Posting not in this feed"}, {STAGE_LABELS[a.stage].toLowerCase()}</span>
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
          {generated.length > 0 ? (
            <details className="pt-generated" data-testid="pt-generated">
              <summary>
                {generated.length} records from the pipeline’s experiment run are not shown here
              </summary>
              <p className="pt-muted">
                They come from the experiment stage’s output, which simulates applications to test the experiment machinery. They are not your activity, so they are kept out of your applications, follow-ups and counts. See them under Experiments in the current app.
              </p>
            </details>
          ) : null}
        </nav>

        {selected && job ? (
          <article className="pt-tdetail" aria-labelledby="pt-app-title" data-testid="pt-app-detail">
            <Button variant="ghost" className="pt-back" onClick={() => navigate("/track")}>
              <Icon name="back" size={16} /> All applications
            </Button>
            <header className="pt-detail__head">
              <h2 id="pt-app-title">{job.title}</h2>
              <p className="pt-detail__org">{job.organization}. <Link to={`/discover/${encodeURIComponent(job.id)}`}>Posting and fit</Link></p>
              <ol className="pt-progress" aria-label="Stage">
                {FLOW.map((s, i) => (
                  <li key={s} className={i < stageIndex ? "pt-progress__done" : i === stageIndex ? "pt-progress__on" : ""} aria-current={i === stageIndex ? "step" : undefined}>
                    {STAGE_LABELS[s]}
                  </li>
                ))}
              </ol>
            </header>

            {action ? (
              <section className={action.urgent ? "pt-next pt-next--urgent" : "pt-next"} aria-labelledby="pt-next-title" data-testid="pt-next">
                <p className="pt-next__label">Next action</p>
                <h3 id="pt-next-title">{action.title}</h3>
                <p>{action.detail}</p>
                <div className="pt-actions">
                  {selected.stage === "saved" || (selected.stage === "drafted" && !approvedVariant) ? (
                    <Link className="ct-btn ct-btn--primary" to={`/tailor/${encodeURIComponent(job.id)}`}>
                      Tailor résumé
                    </Link>
                  ) : null}
                  {selected.stage === "drafted" && approvedVariant ? (
                    <Button variant="primary" onClick={() => record("approved", { resume_variant_id: approvedVariant.id, approval_token_ref: approvedVariant.approval!.approval_token_ref })}>
                      Record approval
                    </Button>
                  ) : null}
                  {selected.stage === "approved" ? (
                    <Button variant="primary" onClick={() => setSubmitOpen(true)} data-testid="pt-record-applied">
                      I applied, record it
                    </Button>
                  ) : null}
                  {selected.stage === "submitted" ? (
                    <Button variant={action.urgent ? "primary" : "secondary"} onClick={() => setShowTemplate((s) => !s)} aria-expanded={showTemplate} data-testid="pt-followup">
                      Prepare a follow-up note
                    </Button>
                  ) : null}
                </div>
                {submitOpen ? (
                  <div className="pt-inline-confirm" role="group" aria-label="Record that you applied">
                    <p>CareerTwin does not submit anything. Record this only after you applied yourself, outside CareerTwin, with the approved résumé.</p>
                    <Checkbox label="I applied myself, outside CareerTwin, using the approved résumé." checked={submitChecked} onChange={(e) => setSubmitChecked(e.target.checked)} />
                    <div className="pt-actions">
                      <Button onClick={() => { setSubmitOpen(false); setSubmitChecked(false); }}>Cancel</Button>
                      <Button variant="primary" disabled={!submitChecked} onClick={() => { if (!record("submitted", { approval_token_ref: selected.approvalTokenRef, resume_variant_id: selected.approvedVariantId ?? selected.resumeVariantId, outcome_status: "self-reported" })) { setSubmitOpen(false); setSubmitChecked(false); } }}>
                        Record application
                      </Button>
                    </div>
                  </div>
                ) : null}
                {showTemplate ? (
                  <div className="pt-template" data-testid="pt-template">
                    <p className="pt-muted">A template for you to edit and send yourself. CareerTwin does not write with AI and cannot send email.</p>
                    <pre>{`Subject: Following up on my application for ${job.title}\n\nHello,\n\nI applied for the ${job.title} role at ${job.organization} on ${selected.submittedAt ? formatDate(selected.submittedAt) : "[date]"} and wanted to confirm it was received. I remain very interested and would be glad to share anything else that helps.\n\nThank you,\n[Your name]`}</pre>
                  </div>
                ) : null}
              </section>
            ) : null}

            <div className="pt-tcols">
              <section className="pt-block" aria-labelledby="pt-follow">
                <h3 id="pt-follow">Follow-ups</h3>
                {reminders.length === 0 ? <p className="pt-muted">No follow-up set.</p> : (
                  <ul className="ct-list-plain pt-rem">
                    {reminders.map((r) => (
                      <li key={r.id}>
                        <Checkbox label={`${formatDate(`${r.due_on}T00:00:00Z`)}: ${r.text}`} checked={r.done} onChange={() => dispatch({ type: "reminder/toggle", id: r.id })} />
                      </li>
                    ))}
                  </ul>
                )}
                <div className="pt-rem__add">
                  <TextField label="Follow up on" type="date" value={remDate} onChange={(e) => setRemDate(e.target.value)} data-testid="pt-rem-date" />
                  <Button
                    disabled={!remDate}
                    onClick={() => {
                      dispatch({ type: "reminder/add", reminder: { id: newId("rem"), application_id: selected.id, due_on: remDate, text: `Follow up: ${job.title}`, done: false } });
                      setRemDate("");
                      announce("Follow-up date set. It shows here; CareerTwin sends no notifications.");
                    }}
                    data-testid="pt-rem-add"
                  >
                    Set follow-up
                  </Button>
                </div>
                <p className="pt-muted">Reminders live in this app only. Email and push notifications are not available.</p>
              </section>

              <section className="pt-block" aria-labelledby="pt-docs">
                <h3 id="pt-docs">Documents and contacts</h3>
                <p>
                  Résumé:{" "}
                  {variant ? (
                    <Link to={`/tailor/${encodeURIComponent(job.id)}`}>{variantTitle(variant, job.title)}</Link>
                  ) : (
                    <span className="pt-muted">none linked yet</span>
                  )}
                  {variant?.approval ? <span className="pt-muted">, approved {formatDate(variant.approval.approved_at)}</span> : null}
                </p>
                <p className="pt-muted">Contacts and file attachments are not available in this build.</p>
              </section>
            </div>

            <section className="pt-block" aria-labelledby="pt-activity">
              <h3 id="pt-activity">Activity</h3>
              <ol className="pt-timeline" data-testid="pt-timeline">
                {sortEvents(selected.events).slice().reverse().map((e) => (
                  <li key={e.id}>
                    <strong>{EVENT_LABELS[e.event_type]}</strong>
                    <span className="pt-muted">
                      {" "}
                      {formatDateTime(e.occurred_at ?? e.recorded_at)}. {byWhom(e.actor, e.source)}
                      {e.outcome_status === "self-reported" ? ", self-reported" : ""}.
                    </span>
                    {e.notes ? <p>{e.notes}</p> : null}
                  </li>
                ))}
              </ol>
              <div className="pt-note">
                <TextArea label="Add a note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} />
                <Button disabled={!note.trim()} onClick={() => { if (!record("note", { notes: note.trim() })) setNote(""); }}>
                  Add note
                </Button>
              </div>
            </section>
          </article>
        ) : appId ? (
          <EmptyState title="This application is not in your tracker." action={<Link to="/track">All applications</Link>} />
        ) : null}
      </div>
      {bundle.provenance.data_source === "artifacts" ? (
        <Banner tone="neutral" title="Read-only data source.">
          Changes you make here stay in this tab for the review and are not saved.
        </Banner>
      ) : null}
    </Screen>
  );
}

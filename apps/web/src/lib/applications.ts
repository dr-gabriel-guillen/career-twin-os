/**
 * Read-model over the immutable ApplicationEvent log (frozen contract, lane B).
 * The UI never mutates or deletes events; it appends new ones and derives
 * everything else (stage, timeline, missing feedback) from the log.
 *
 * The transition table below is a UI gate only. Lane B owns the authoritative
 * state machine; a NEEDS-INTERFACE request asks B to publish it.
 */
import type { ApplicationEvent, ApplicationEventType } from "@/contracts/types";

export const STAGES = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "offer", "closed"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  saved: "Saved",
  drafted: "Drafted",
  approved: "Approved by you",
  submitted: "Submitted (manually)",
  recruiter_screen: "Recruiter screen",
  interview: "Interview",
  offer: "Offer",
  closed: "Closed",
};

export const EVENT_LABELS: Record<ApplicationEventType, string> = {
  saved: "Saved",
  drafted: "Résumé variant drafted",
  approved: "Approved by you",
  submitted: "Marked as submitted (manual)",
  recruiter_screen: "Recruiter screen",
  interview: "Interview",
  rejected: "Rejected",
  offer: "Offer",
  withdrawn: "Withdrawn",
  note: "Note",
};

export interface Application {
  id: string;
  job_id: string;
  events: ApplicationEvent[];
  stage: Stage;
  /** Why the application is closed, when it is. */
  closedAs: "rejected" | "withdrawn" | null;
  lastEvent: ApplicationEvent;
  resumeVariantId: string | null;
  approvalTokenRef: string | null;
  experimentId: string | null;
  submittedAt: string | null;
}

const eventTime = (e: ApplicationEvent) => e.occurred_at ?? e.recorded_at;

export function sortEvents(events: ApplicationEvent[]): ApplicationEvent[] {
  return [...events].sort(
    (a, b) => eventTime(a).localeCompare(eventTime(b)) || a.recorded_at.localeCompare(b.recorded_at) || a.id.localeCompare(b.id),
  );
}

function stageOf(type: ApplicationEventType): Stage | null {
  if (type === "note") return null;
  if (type === "rejected" || type === "withdrawn") return "closed";
  return type;
}

export function groupApplications(events: ApplicationEvent[]): Application[] {
  const byApp = new Map<string, ApplicationEvent[]>();
  for (const e of events) {
    const list = byApp.get(e.application_id);
    if (list) list.push(e);
    else byApp.set(e.application_id, [e]);
  }
  const apps: Application[] = [];
  for (const [id, raw] of byApp) {
    const sorted = sortEvents(raw);
    let stage: Stage = "saved";
    let closedAs: Application["closedAs"] = null;
    let resumeVariantId: string | null = null;
    let approvalTokenRef: string | null = null;
    let experimentId: string | null = null;
    let submittedAt: string | null = null;
    for (const e of sorted) {
      const s = stageOf(e.event_type);
      if (s) {
        stage = s;
        closedAs = e.event_type === "rejected" || e.event_type === "withdrawn" ? e.event_type : null;
      }
      if (e.resume_variant_id) resumeVariantId = e.resume_variant_id;
      if (e.approval_token_ref) approvalTokenRef = e.approval_token_ref;
      if (e.experiment_id) experimentId = e.experiment_id;
      if (e.event_type === "submitted") submittedAt = eventTime(e);
    }
    apps.push({ id, job_id: sorted[0]!.job_id, events: sorted, stage, closedAs, lastEvent: sorted[sorted.length - 1]!, resumeVariantId, approvalTokenRef, experimentId, submittedAt });
  }
  return apps.sort((a, b) => eventTime(b.lastEvent).localeCompare(eventTime(a.lastEvent)) || a.id.localeCompare(b.id));
}

/** Events the UI offers from each stage. `note` is always allowed. */
const NEXT: Record<Stage, ApplicationEventType[]> = {
  saved: ["drafted", "withdrawn"],
  drafted: ["approved", "withdrawn"],
  approved: ["submitted", "withdrawn"],
  submitted: ["recruiter_screen", "interview", "rejected", "offer", "withdrawn"],
  recruiter_screen: ["interview", "rejected", "offer", "withdrawn"],
  interview: ["interview", "offer", "rejected", "withdrawn"],
  offer: ["withdrawn"],
  closed: [],
};

export function allowedNext(app: Application): ApplicationEventType[] {
  return [...NEXT[app.stage], "note"];
}

export function canAppend(app: Application | null, type: ApplicationEventType): boolean {
  if (app === null) return type === "saved";
  return allowedNext(app).includes(type);
}

export const MISSING_FEEDBACK_DAYS = 14;

/**
 * "No reply recorded" is NOT a rejection. It stays an unknown outcome and is
 * surfaced so it is not silently counted as a negative result.
 */
export function daysWithoutFeedback(app: Application, clock: string): number | null {
  if (app.stage !== "submitted" || !app.submittedAt) return null;
  const days = Math.floor((Date.parse(clock) - Date.parse(app.submittedAt)) / 86_400_000);
  return days >= MISSING_FEEDBACK_DAYS ? days : null;
}

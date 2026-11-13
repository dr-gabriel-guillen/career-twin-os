/**
 * Read-model over the immutable ApplicationEvent log (frozen contract, lane B).
 * The UI never mutates or deletes events; it appends new ones and derives
 * everything else (stage, timeline, missing feedback) from the log.
 *
 * ORDER: the log order is the order in which events were RECORDED. Stage is
 * derived in that order, because that is the order in which the gate accepted
 * them. `occurred_at` is a fact about the world that the user states; it is
 * displayed, and it is checked for plausibility when an event is appended, but
 * it never re-orders the log. (Review finding F-02: sorting by `occurred_at`
 * let a backdated event be accepted and then ignored.)
 *
 * The transition table below is a UI gate only. Lane B owns the authoritative
 * state machine.
 */
import type { ApplicationEvent, ApplicationEventType } from "@/contracts/types";

export const STAGES = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "offer", "closed"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  saved: "Saved",
  drafted: "Drafted",
  approved: "Approved",
  submitted: "Submitted (manually)",
  recruiter_screen: "Recruiter screen",
  interview: "Interview",
  offer: "Offer",
  closed: "Closed",
};

export const EVENT_LABELS: Record<ApplicationEventType, string> = {
  saved: "Saved",
  drafted: "Résumé variant drafted",
  approved: "Résumé approved",
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
  /** Events in recorded order. */
  events: ApplicationEvent[];
  stage: Stage;
  /** Why the application is closed, when it is. */
  closedAs: "rejected" | "withdrawn" | null;
  lastEvent: ApplicationEvent;
  /** Variant named by the latest `drafted` event (the one being prepared). */
  resumeVariantId: string | null;
  /** Variant and approval record named by the latest `approved` event. Notes cannot set these. */
  approvedVariantId: string | null;
  approvalTokenRef: string | null;
  experimentId: string | null;
  /** When the user says the submission happened. */
  submittedAt: string | null;
  /** Calendar day (UTC) of the latest stage-changing event, as stated by `occurred_at`. */
  lastStageDay: string | null;
}

/** Milliseconds for an RFC 3339 timestamp; NaN-safe. Timestamps are never compared as strings. */
export function timeOf(iso: string | null | undefined): number {
  const t = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(t) ? 0 : t;
}

/** When the event happened according to the user, falling back to when it was recorded. */
export const eventTime = (e: ApplicationEvent) => e.occurred_at ?? e.recorded_at;
/** UTC calendar day of an RFC 3339 timestamp. */
export const dayOf = (iso: string) => new Date(timeOf(iso)).toISOString().slice(0, 10);

/** Recorded order: by `recorded_at` as an instant, ties kept in input order. */
export function sortEvents(events: ApplicationEvent[]): ApplicationEvent[] {
  return events
    .map((e, i) => ({ e, i, t: timeOf(e.recorded_at) }))
    .sort((a, b) => a.t - b.t || a.i - b.i)
    .map((x) => x.e);
}

function stageOf(type: ApplicationEventType): Stage | null {
  if (type === "note") return null;
  if (type === "rejected" || type === "withdrawn") return "closed";
  return type;
}

export function groupApplications(events: ApplicationEvent[]): Application[] {
  const byApp = new Map<string, ApplicationEvent[]>();
  for (const e of sortEvents(events)) {
    const list = byApp.get(e.application_id);
    if (list) list.push(e);
    else byApp.set(e.application_id, [e]);
  }
  const apps: Application[] = [];
  for (const [id, log] of byApp) {
    let stage: Stage = "saved";
    let closedAs: Application["closedAs"] = null;
    let resumeVariantId: string | null = null;
    let approvedVariantId: string | null = null;
    let approvalTokenRef: string | null = null;
    let experimentId: string | null = null;
    let submittedAt: string | null = null;
    let lastStageDay: string | null = null;
    for (const e of log) {
      const s = stageOf(e.event_type);
      if (s) {
        stage = s;
        closedAs = e.event_type === "rejected" || e.event_type === "withdrawn" ? e.event_type : null;
        lastStageDay = dayOf(eventTime(e));
      }
      // Only the step that means it may set these. A note can never change them (finding F-01).
      if (e.event_type === "drafted" && e.resume_variant_id) resumeVariantId = e.resume_variant_id;
      if (e.event_type === "approved") {
        approvedVariantId = e.resume_variant_id ?? null;
        approvalTokenRef = e.approval_token_ref ?? null;
        if (e.resume_variant_id) resumeVariantId = e.resume_variant_id;
      }
      if (e.event_type !== "note" && e.experiment_id) experimentId = e.experiment_id;
      if (e.event_type === "submitted") submittedAt = eventTime(e);
    }
    apps.push({ id, job_id: log[0]!.job_id, events: log, stage, closedAs, lastEvent: log[log.length - 1]!, resumeVariantId, approvedVariantId, approvalTokenRef, experimentId, submittedAt, lastStageDay });
  }
  return apps.sort((a, b) => timeOf(b.lastEvent.recorded_at) - timeOf(a.lastEvent.recorded_at) || a.id.localeCompare(b.id));
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
  const days = Math.floor((timeOf(clock) - timeOf(app.submittedAt)) / 86_400_000);
  return days >= MISSING_FEEDBACK_DAYS ? days : null;
}

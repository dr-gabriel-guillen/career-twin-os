import { useMemo } from "react";
import type { MatchExplanation } from "@/adapters/proposed";
import type { ApplicationEvent, JobPosting, ProfileClaim } from "@/contracts/types";
import { groupApplications, type Application } from "@/lib/applications";
import { humanise } from "@/lib/format";
import { profileDigest } from "@/lib/profileDigest";
import { newId } from "@/lib/safety";
import { useApp } from "./AppContext";

export interface Indexes {
  jobById: Map<string, JobPosting>;
  matchByJob: Map<string, MatchExplanation>;
  claimById: Map<string, ProfileClaim>;
  applications: Application[];
  appByJob: Map<string, Application>;
  familyLabel: (family: string | null | undefined) => string;
  families: string[];
  /** True when the vault changed since the match engine's snapshot. */
  matchesStale: boolean;
}

export function useIndexes(): Indexes {
  const { bundle, workspace } = useApp();
  return useMemo(() => {
    const jobById = new Map(bundle.jobs.map((j) => [j.id, j]));
    const matchByJob = new Map(bundle.matches.map((m) => [m.job_id, m]));
    const claimById = new Map(workspace.claims.map((c) => [c.id, c]));
    const applications = groupApplications(workspace.applicationEvents);
    const appByJob = new Map<string, Application>();
    for (const a of applications) if (!appByJob.has(a.job_id)) appByJob.set(a.job_id, a);
    const labels = bundle.manifest?.role_family_labels ?? {};
    const familyLabel = (family: string | null | undefined) => (family ? labels[family] ?? humanise(family) : "Unlabelled role family");
    const families = [...new Set(bundle.jobs.map((j) => j.role_family).filter((f): f is string => Boolean(f)))].sort();
    const snapshot = bundle.matches[0]?.profile_digest ?? null;
    const matchesStale = snapshot !== null && workspace.claims.length > 0 && snapshot !== profileDigest(workspace.claims);
    return { jobById, matchByJob, claimById, applications, appByJob, familyLabel, families, matchesStale };
  }, [bundle, workspace.claims, workspace.applicationEvents]);
}

/** Build a contract-conformant, user-authored ApplicationEvent. */
export function userEvent(
  fields: Pick<ApplicationEvent, "application_id" | "job_id" | "event_type"> & Partial<ApplicationEvent>,
): ApplicationEvent {
  const now = new Date().toISOString();
  return {
    id: newId("evt"),
    resume_variant_id: null,
    recorded_at: now,
    occurred_at: now,
    actor: "user",
    source: "manual-entry",
    outcome_status: "self-reported",
    experiment_id: null,
    approval_token_ref: null,
    notes: "",
    ...fields,
  };
}

/** Blend near-term fit and strategic value by the user's slider. Null when neither is known. */
export function blendedScore(match: MatchExplanation, strategicBlend: number): number | null {
  const near = match.near_term_fit.score;
  const strat = match.strategic_value.score;
  if (near === null && strat === null) return null;
  if (near === null) return strat;
  if (strat === null) return near;
  const b = strategicBlend / 100;
  return Math.round(((1 - b) * near + b * strat) * 10) / 10;
}

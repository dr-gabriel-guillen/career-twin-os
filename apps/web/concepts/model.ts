/**
 * Concept view-model. Derives what the three design concepts render from the SAME validated
 * DataBundle the product uses (loadBundle + fixture source). It reuses lane D's helpers and
 * adds no scoring, ranking weights, outcomes or metrics of its own: every number shown in a
 * concept is either a field of an upstream payload or a count of records.
 */
import type { DataBundle } from "@/adapters";
import type { MatchExplanation } from "@/adapters/proposed";
import type { JobPosting } from "@/contracts/types";
import { daysWithoutFeedback, groupApplications, STAGE_LABELS, type Application, type Stage } from "@/lib/applications";
import { formatCompensation, freshness, humanise } from "@/lib/format";

export interface JobRow {
  job: JobPosting;
  match: MatchExplanation | null;
  family: string;
  near: number | null;
  strategic: number | null;
  failedConstraints: string[];
  unknownInputs: string[];
  missingSkills: string[];
  supportedSkills: string[];
  comp: { text: string; known: boolean };
  fresh: { level: "fresh" | "aging" | "stale"; days: number; label: string };
  application: Application | null;
}

export interface ConceptModel {
  bundle: DataBundle;
  clock: string;
  candidateName: string;
  targetRoles: string[];
  rows: JobRow[];
  applications: { app: Application; row: JobRow | null; silentDays: number | null }[];
  stageCounts: { stage: Stage; label: string; count: number }[];
  claimCounts: { supported: number; selfReported: number; unverified: number; contradicted: number; total: number };
  experiments: { id: string; title: string; status: string; verdict: string | null; arms: number }[];
  familyLabel: (f: string | null | undefined) => string;
}

export function familyLabeller(bundle: DataBundle): (f: string | null | undefined) => string {
  const labels = bundle.manifest?.role_family_labels ?? {};
  return (f) => (f ? labels[f] ?? humanise(f) : "Unclassified");
}

export function buildModel(bundle: DataBundle): ConceptModel {
  const clock = bundle.clock;
  const familyLabel = familyLabeller(bundle);
  const matches = new Map(bundle.matches.map((m) => [m.job_id, m]));
  const apps = groupApplications(bundle.applicationEvents);
  const appByJob = new Map(apps.map((a) => [a.job_id, a]));

  const rows: JobRow[] = bundle.jobs.map((job) => {
    const match = matches.get(job.id) ?? null;
    const comp = formatCompensation(job.compensation);
    return {
      job,
      match,
      family: familyLabel(job.role_family),
      near: match?.near_term_fit.score ?? null,
      strategic: match?.strategic_value.score ?? null,
      failedConstraints: (match?.hard_constraints ?? []).filter((c) => c.status === "fail").map((c) => c.label),
      unknownInputs: match?.unknown_inputs ?? [],
      missingSkills: (match?.skills ?? []).filter((s) => s.evidence_strength === "missing").map((s) => s.skill),
      supportedSkills: (match?.skills ?? []).filter((s) => s.evidence_strength === "supported").map((s) => s.skill),
      comp: { text: comp.known ? comp.text : comp.text === "Range unknown" ? "Pay range not disclosed" : "Pay not disclosed", known: comp.known },
      fresh: freshness(job.fetched_at, clock),
      application: appByJob.get(job.id) ?? null,
    };
  });
  // Default order: near-term fit (the engine's own score), unscored last. Not a blend: we do not invent one.
  rows.sort((a, b) => (b.near ?? -1) - (a.near ?? -1) || a.job.title.localeCompare(b.job.title));

  const rowByJob = new Map(rows.map((r) => [r.job.id, r]));
  const applications = apps
    .map((app) => ({ app, row: rowByJob.get(app.job_id) ?? null, silentDays: daysWithoutFeedback(app, clock) }))
    .sort((a, b) => Date.parse(b.app.lastEvent.recorded_at) - Date.parse(a.app.lastEvent.recorded_at));

  const order: Stage[] = ["saved", "drafted", "approved", "submitted", "recruiter_screen", "interview", "offer", "closed"];
  const stageCounts = order.map((stage) => ({ stage, label: STAGE_LABELS[stage], count: apps.filter((a) => a.stage === stage).length }));

  const c = bundle.claims;
  const claimCounts = {
    supported: c.filter((x) => x.status === "supported").length,
    selfReported: c.filter((x) => x.status === "self-reported").length,
    unverified: c.filter((x) => x.status === "unverified").length,
    contradicted: c.filter((x) => x.status === "contradicted").length,
    total: c.length,
  };

  const experiments = bundle.experiments.map((e) => ({
    id: e.id,
    title: e.title,
    status: e.status,
    verdict: bundle.analyses.find((a) => a.experiment_id === e.id)?.verdict ?? null,
    arms: e.variants.length,
  }));

  return {
    bundle,
    clock,
    candidateName: bundle.preferences?.display_name ?? "Synthetic candidate",
    targetRoles: bundle.preferences?.target_roles ?? [],
    rows,
    applications,
    stageCounts,
    claimCounts,
    experiments,
    familyLabel,
  };
}

export const VERDICT_TEXT: Record<string, string> = {
  EVIDENCE_FOR_DIFFERENCE: "Evidence of a difference",
  INCONCLUSIVE: "Inconclusive",
  INTERIM_NO_CLAIM: "Interim, no claim",
  EXPLORATORY_NO_CLAIM: "Exploratory, no claim",
  NOT_ANALYZABLE: "Not analysable yet",
  OBSERVATIONAL_ONLY: "Observational only",
};

export function skillLabel(s: string): string {
  return humanise(s).replace(/\bAi\b/g, "AI").replace(/\bApi\b/g, "API").replace(/\bAws\b/g, "AWS").replace(/\bMlops\b/g, "MLOps")
    .replace(/\bSql\b/g, "SQL").replace(/\bLlm\b/g, "LLM").replace(/\bCi cd\b/g, "CI/CD");
}

/** Actions derived from records. Each one names the record it comes from; none is invented. */
export interface NextAction {
  kind: "follow-up" | "evidence" | "shortlist" | "experiment" | "constraint";
  title: string;
  detail: string;
  target: string;
}

export function nextActions(m: ConceptModel): NextAction[] {
  const out: NextAction[] = [];
  for (const a of m.applications) {
    if (a.silentDays !== null) {
      out.push({
        kind: "follow-up",
        title: `No reply recorded for ${a.silentDays} days`,
        detail: `${a.row?.job.title ?? a.app.job_id} at ${a.row?.job.organization ?? "unknown employer"}. Not a rejection: the outcome is unknown.`,
        target: "applications",
      });
    }
  }
  const weak = m.claimCounts.unverified + m.claimCounts.contradicted;
  if (weak) out.push({ kind: "evidence", title: `${weak} claims cannot be used in a résumé yet`, detail: `${m.claimCounts.unverified} unverified and ${m.claimCounts.contradicted} contradicted. Attach evidence or retire them.`, target: "evidence" });
  const top = m.rows.find((r) => r.near !== null && r.failedConstraints.length === 0 && !r.application);
  if (top) out.push({ kind: "shortlist", title: `Review ${top.job.title}`, detail: `${top.job.organization}: highest near-term fit among postings you have not acted on, and it passes your hard constraints.`, target: "discover" });
  const gap = new Map<string, number>();
  for (const r of m.rows.slice(0, 8)) for (const s of r.missingSkills) gap.set(s, (gap.get(s) ?? 0) + 1);
  const [skill, n] = [...gap.entries()].sort((a, b) => b[1] - a[1])[0] ?? [];
  if (skill && n && n > 1) out.push({ kind: "evidence", title: `${skillLabel(skill)} is missing in ${n} of your 8 closest postings`, detail: "No claim in your evidence vault covers it. A project or work sample would close the gap.", target: "evidence" });
  const running = m.experiments.find((e) => e.status === "running");
  if (running) out.push({ kind: "experiment", title: `${running.title} is still collecting outcomes`, detail: "No result is reported until the preregistered window closes.", target: "experiments" });
  return out;
}

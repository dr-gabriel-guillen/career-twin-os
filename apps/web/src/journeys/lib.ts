/**
 * Shared derivations for the product journeys. Everything here reads upstream payloads or the
 * person's own workspace; nothing scores, predicts or generates text. Labels describe the engine's
 * relative ranking within this feed, never a chance of being hired.
 */
import type { MatchExplanation } from "@/adapters/proposed";
import type { JobPosting, ProfileClaim } from "@/contracts/types";
import { skillLabel } from "@/lib/format";

export type Standing = "closest" | "close" | "further" | "blocked" | "unscored";

/** Where a posting stands among the scored postings in this feed, by the engine's near-term fit. */
export function standingOf(m: MatchExplanation | null | undefined, rankOf: Map<string, number>, scored: number): Standing {
  if (!m || m.near_term_fit.score === null) return "unscored";
  if (m.hard_constraints.some((h) => h.status === "fail")) return "blocked";
  const r = rankOf.get(m.job_id) ?? scored;
  if (r < Math.max(3, Math.ceil(scored * 0.1))) return "closest";
  if (r < Math.ceil(scored / 3)) return "close";
  return "further";
}

export const STANDING_LABEL: Record<Standing, string> = {
  closest: "Among your closest matches",
  close: "Close to your evidence",
  further: "Further from your evidence",
  blocked: "Fails one of your constraints",
  unscored: "Not ranked by the engine",
};

/** Rank of each scored, unblocked posting by near-term fit (0 = closest). */
export function rankByNearTerm(matches: MatchExplanation[]): { rankOf: Map<string, number>; scored: number } {
  const scoredList = matches.filter((m) => m.near_term_fit.score !== null).sort((a, b) => (b.near_term_fit.score ?? 0) - (a.near_term_fit.score ?? 0) || a.job_id.localeCompare(b.job_id));
  return { rankOf: new Map(scoredList.map((m, i) => [m.job_id, i])), scored: scoredList.length };
}

export interface Reason {
  text: string;
  detail?: string;
  tone: "good" | "weak" | "bad" | "unknown";
}

/** "Why this could fit you": skills the posting lists that your claims cover, strongest evidence first. */
export function reasonsFor(m: MatchExplanation): Reason[] {
  const order = { supported: 0, "self-reported": 1, unverified: 2, contradicted: 3, missing: 4 } as const;
  return m.skills
    .filter((s) => s.evidence_strength === "supported" || s.evidence_strength === "self-reported")
    .sort((a, b) => order[a.evidence_strength] - order[b.evidence_strength] || a.skill.localeCompare(b.skill))
    .map((s) => ({
      text: skillLabel(s.skill),
      detail: s.evidence_strength === "supported" ? `backed by evidence (${s.claim_ids.length} claim${s.claim_ids.length === 1 ? "" : "s"})` : "self-reported, no evidence attached",
      tone: s.evidence_strength === "supported" ? ("good" as const) : ("weak" as const),
    }));
}

/** "What stands in the way": failed or unknown constraints, then listed skills with no usable claim. */
export function blockersFor(m: MatchExplanation): Reason[] {
  const out: Reason[] = [];
  for (const h of m.hard_constraints) {
    if (h.status === "fail") out.push({ text: `${h.label}: does not meet your constraint`, detail: h.detail, tone: "bad" });
    else if (h.status === "unknown") out.push({ text: `${h.label}: unknown`, detail: h.detail, tone: "unknown" });
  }
  for (const s of m.skills) {
    if (s.evidence_strength === "missing") out.push({ text: `${skillLabel(s.skill)}: no claim covers it`, tone: "bad" });
    else if (s.evidence_strength === "contradicted") out.push({ text: `${skillLabel(s.skill)}: your claim is contradicted by evidence`, tone: "bad" });
    else if (s.evidence_strength === "unverified") out.push({ text: `${skillLabel(s.skill)}: claim not verified yet`, tone: "weak" });
  }
  return out;
}

/** "What would improve your fit": the engine's own hypotheticals, then evidence you could attach. */
export function improvementsFor(m: MatchExplanation): Reason[] {
  const out: Reason[] = (m.counterfactuals ?? []).slice(0, 3).map((c) => ({
    text: c.change.charAt(0).toUpperCase() + c.change.slice(1),
    detail: `${c.score_delta > 0 ? "+" : ""}${c.score_delta} on the engine’s scale. Hypothetical: ${c.status}`,
    tone: "good" as const,
  }));
  if (out.length === 0) {
    for (const s of m.skills.filter((x) => x.evidence_strength === "self-reported" || x.evidence_strength === "unverified").slice(0, 3)) {
      out.push({ text: `Attach evidence for ${skillLabel(s.skill)}`, detail: "Evidence-backed claims count fully; self-reported ones count less.", tone: "weak" });
    }
  }
  return out;
}

/** One-line reason shown on a list row. */
export function rowReason(m: MatchExplanation | null | undefined): { text: string; tone: Reason["tone"] } | null {
  if (!m) return null;
  const failed = m.hard_constraints.find((h) => h.status === "fail");
  if (failed) return { text: `Fails ${failed.label.toLowerCase()}`, tone: "bad" };
  const covered = m.skills.filter((s) => s.evidence_strength === "supported" || s.evidence_strength === "self-reported").length;
  const missing = m.skills.filter((s) => s.evidence_strength === "missing").length;
  if (m.skills.length === 0) return null;
  return { text: `Your claims cover ${covered} of ${m.skills.length} listed skills${missing ? `, ${missing} missing` : ""}`, tone: missing ? "weak" : "good" };
}

/** Claims from the vault that cover skills the posting lists, usable in a résumé, not already used. */
export function suggestedClaims(job: JobPosting, m: MatchExplanation | null, claims: ProfileClaim[], used: Set<string>): { claim: ProfileClaim; skills: string[]; usable: boolean; why: string }[] {
  const wanted = new Set([...(m?.skills.map((s) => s.skill) ?? []), ...job.skills]);
  return claims
    .filter((c) => !used.has(c.id))
    .map((c) => ({ claim: c, skills: (c.skills ?? []).filter((s) => wanted.has(s)) }))
    .filter((x) => x.skills.length > 0)
    .map(({ claim, skills }) => {
      const blocked = claim.status === "unverified" || claim.status === "contradicted" || claim.visibility === "private";
      return {
        claim,
        skills,
        usable: !blocked,
        why: claim.visibility === "private" ? "Private claim: never used in a résumé" : claim.status === "contradicted" ? "Contradicted by evidence" : claim.status === "unverified" ? "Unverified: attach evidence first" : claim.status === "supported" ? "Backed by evidence" : "Self-reported",
      };
    })
    .sort((a, b) => Number(b.usable) - Number(a.usable) || b.skills.length - a.skills.length);
}

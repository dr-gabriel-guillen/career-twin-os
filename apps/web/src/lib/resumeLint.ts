/**
 * Truthfulness guard for résumé variants.
 *
 * Contract invariant (docs/coordination/CONTRACTS.md): "A résumé variant is a
 * snapshot of verified or explicitly self-reported claims; unsupported
 * qualifications prohibited." This module decides, per bullet, whether export
 * must be BLOCKED or merely WARNED about. It is a UI gate, not a fact checker:
 * it can only compare a bullet with the claims it links to.
 */
import type { ProfileClaim } from "@/contracts/types";
import type { ResumeBullet, ResumeVariant } from "@/adapters/proposed";
import { resumeContentDigest } from "./resumeDigest";

export type FindingSeverity = "block" | "warn";
export interface Finding {
  code:
    | "no-evidence-link"
    | "unknown-claim"
    | "contradicted-claim"
    | "unverified-claim"
    | "expired-claim"
    | "private-claim"
    | "new-figure"
    | "new-credential"
    | "reworded"
    | "self-reported";
  severity: FindingSeverity;
  message: string;
}

/** Words that assert a credential, title or formal qualification. */
const CREDENTIAL_TERMS = [
  "certified", "certificate", "certification", "licensed", "license", "licence", "accredited", "chartered",
  "phd", "ph.d", "doctorate", "mba", "msc", "m.s", "bsc", "b.s", "bachelor", "master", "degree", "diploma",
  "award", "awarded", "patent", "published", "valedictorian", "honors", "honours", "cum laude",
];

const normalise = (s: string) => s.toLowerCase().replace(/[–—]/g, "-");
const numbersIn = (s: string) => normalise(s).match(/\d+(?:[.,]\d+)*%?/g) ?? [];
const wordsIn = (s: string) => normalise(s).match(/[a-z][a-z.'-]*[a-z]|[a-z]/g) ?? [];

export function lintBullet(bullet: ResumeBullet, claims: Map<string, ProfileClaim>, clock: string): Finding[] {
  const findings: Finding[] = [];
  if (bullet.claim_ids.length === 0) {
    findings.push({ code: "no-evidence-link", severity: "block", message: "Not linked to any claim in the evidence vault." });
    return findings;
  }
  const linked: ProfileClaim[] = [];
  for (const id of bullet.claim_ids) {
    const claim = claims.get(id);
    if (!claim) {
      findings.push({ code: "unknown-claim", severity: "block", message: `Linked claim '${id}' no longer exists.` });
      continue;
    }
    linked.push(claim);
    if (claim.status === "contradicted") {
      findings.push({ code: "contradicted-claim", severity: "block", message: `Evidence contradicts the linked claim: “${claim.claim_text}”.` });
    } else if (claim.status === "unverified") {
      findings.push({ code: "unverified-claim", severity: "block", message: `The linked claim is unverified: “${claim.claim_text}”. Mark it self-reported or add evidence first.` });
    } else if (claim.status === "self-reported") {
      findings.push({ code: "self-reported", severity: "warn", message: "Relies on a self-reported claim with no supporting evidence." });
    }
    if (claim.valid_to && Date.parse(claim.valid_to) < Date.parse(clock)) {
      findings.push({ code: "expired-claim", severity: "block", message: `The linked claim expired on ${claim.valid_to.slice(0, 10)}.` });
    }
    if (claim.visibility === "private") {
      findings.push({ code: "private-claim", severity: "block", message: "The linked claim is marked private and may not be exported." });
    }
  }
  if (bullet.origin === "manual-edit" && linked.length > 0) {
    const source = linked.map((c) => c.claim_text).join(" \n ");
    const sourceNumbers = new Set(numbersIn(source));
    const sourceWords = new Set(wordsIn(source));
    const newNumbers = [...new Set(numbersIn(bullet.text))].filter((n) => !sourceNumbers.has(n));
    if (newNumbers.length > 0) {
      findings.push({ code: "new-figure", severity: "block", message: `Contains figure(s) not found in the linked claims: ${newNumbers.join(", ")}.` });
    }
    const bulletWords = wordsIn(bullet.text);
    const newCredentials = [...new Set(bulletWords.filter((w) => CREDENTIAL_TERMS.includes(w.replace(/\.$/, "")) && !sourceWords.has(w)))];
    if (newCredentials.length > 0) {
      findings.push({ code: "new-credential", severity: "block", message: `Mentions a credential or qualification not found in the linked claims: ${newCredentials.join(", ")}.` });
    }
    if (newNumbers.length === 0 && newCredentials.length === 0 && normalise(bullet.text).trim() !== normalise(source).trim()) {
      findings.push({ code: "reworded", severity: "warn", message: "Wording was edited by hand. Re-read it against the linked evidence before approving." });
    }
  }
  return findings;
}

export interface VariantReview {
  byBullet: Map<string, Finding[]>;
  blocking: number;
  warnings: number;
  digest: string;
  /** Approved AND the approval matches the current content. */
  approvalCurrent: boolean;
  /** An approval exists but the content changed afterwards. */
  approvalStale: boolean;
  exportAllowed: boolean;
  exportBlockers: string[];
}

export function reviewVariant(variant: ResumeVariant, claims: ProfileClaim[], clock: string): VariantReview {
  const byId = new Map(claims.map((c) => [c.id, c]));
  const byBullet = new Map<string, Finding[]>();
  let blocking = 0;
  let warnings = 0;
  let bullets = 0;
  for (const section of variant.sections) {
    for (const bullet of section.bullets) {
      bullets++;
      const findings = lintBullet(bullet, byId, clock);
      byBullet.set(bullet.id, findings);
      blocking += findings.filter((f) => f.severity === "block").length;
      warnings += findings.filter((f) => f.severity === "warn").length;
    }
  }
  const digest = resumeContentDigest(variant.sections);
  const approvalCurrent = variant.approval !== null && variant.approval.content_digest === digest;
  const approvalStale = variant.approval !== null && !approvalCurrent;
  const exportBlockers: string[] = [];
  if (bullets === 0) exportBlockers.push("The variant has no content.");
  if (blocking > 0) exportBlockers.push(`${blocking} blocking truthfulness issue${blocking === 1 ? "" : "s"} must be resolved.`);
  if (variant.approval === null) exportBlockers.push("You have not approved this exact content yet.");
  if (approvalStale) exportBlockers.push("The content changed after approval; approve it again.");
  return { byBullet, blocking, warnings, digest, approvalCurrent, approvalStale, exportAllowed: exportBlockers.length === 0, exportBlockers };
}

export function variantToText(variant: ResumeVariant, candidateName: string): string {
  const lines = [candidateName, variant.label, ""];
  for (const s of variant.sections) {
    lines.push(s.heading.toUpperCase());
    for (const b of s.bullets) lines.push(`- ${b.text}`);
    lines.push("");
  }
  lines.push("[Synthetic demo export. Fictional candidate. Not a real résumé.]");
  return lines.join("\n");
}

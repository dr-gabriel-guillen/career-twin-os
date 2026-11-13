/**
 * Truthfulness guard for résumé variants.
 *
 * Contract invariant (docs/coordination/CONTRACTS.md): "A résumé variant is a
 * snapshot of verified or explicitly self-reported claims; unsupported
 * qualifications prohibited." This module decides, per line, whether export
 * must be BLOCKED or merely WARNED about. It is a UI gate, not a fact checker:
 * it can only compare a line with the claims it links to.
 *
 * The comparison never trusts the line's declared `origin`. A line that says
 * "from-claims" but does not read like its claims is treated as hand-edited
 * (review finding F-04). Anything in a non-verbatim line that the linked claims
 * do not contain — a figure, a number word, a credential, a job title, a proper
 * name — blocks export. The guard errs toward blocking: rewording is cheap to
 * re-approve, an invented qualification is not.
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
    | "new-title"
    | "new-name"
    | "duplicate-id"
    | "reworded"
    | "self-reported";
  severity: FindingSeverity;
  message: string;
}

/** Stems that assert a credential or formal qualification. Matched as prefixes of a word. */
const CREDENTIAL_STEMS = ["certif", "licens", "licenc", "accredit", "charter", "doctor", "bachelor", "master", "degree", "diploma", "award", "patent", "publish", "valedictor", "honor", "honour", "laureate", "fellowship", "graduat", "alumn"];
/** Whole-word credential abbreviations (after removing dots). */
const CREDENTIAL_WORDS = ["phd", "dphil", "mba", "msc", "bsc", "ms", "bs", "ba", "ma", "md", "jd", "cpa", "cfa", "pmp", "cissp", "cisa", "cism", "pe", "esq", "llb", "meng", "beng", "mphil", "summa", "magna", "laude"];
/** Words that assert seniority, rank or a job title. */
const TITLE_WORDS = ["senior", "sr", "principal", "staff", "lead", "leader", "head", "director", "vp", "president", "chief", "ceo", "cto", "cfo", "coo", "cio", "ciso", "founder", "cofounder", "manager", "managing", "executive", "partner", "professor", "expert", "fellow", "officer", "chair", "chairman", "owner", "architect"];
/** Quantities written as words. */
const NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "hundreds", "thousand", "thousands", "million", "millions", "billion", "billions", "dozen", "dozens", "double", "doubled", "doubling", "triple", "tripled", "tripling", "quadrupled", "half", "halved", "twice", "thrice", "several", "many", "numerous", "tenfold", "twofold"];

/** NFKC folds full-width digits and letters to ASCII, so "１２" is read as "12". */
const fold = (s: string) => s.normalize("NFKC").replace(/[‐-―]/g, "-").replace(/[‘’]/g, "'");
const lower = (s: string) => fold(s).toLowerCase();
/** Figures as standalone tokens: "p95" is not the figure 95, "38%" and "38 %" are the same figure. */
const numbersIn = (s: string) => (lower(s).match(/(?<![a-z0-9])\d+(?:[.,]\d+)*(?:\s?%|x\b|k\b|m\b)?/g) ?? []).map((n) => n.replace(/\s/g, ""));
/** Words, split on anything that is not a letter; possessive endings and dots dropped. */
const wordsIn = (s: string) =>
  lower(s)
    .replace(/\b([a-z])\.(?=[a-z])/g, "$1")
    .replace(/'s\b/g, "")
    .split(/[^a-z]+/)
    .filter(Boolean);
/** Capitalised words that are not the first word of a sentence: likely names of people or organisations. */
function properNames(s: string): string[] {
  const out: string[] = [];
  for (const sentence of fold(s).split(/(?<=[.!?;:])\s+|\n+/)) {
    const tokens = sentence.match(/[A-Za-z][A-Za-z'&.-]*/g) ?? [];
    tokens.slice(1).forEach((t) => {
      if (/^[A-Z]/.test(t)) out.push(t.replace(/'s$/, "").replace(/[.]+$/, "").toLowerCase());
    });
  }
  return out;
}
const isCredential = (w: string) => CREDENTIAL_WORDS.includes(w) || CREDENTIAL_STEMS.some((stem) => w.startsWith(stem));
const canonical = (s: string) => lower(s).replace(/\s+/g, " ").trim();

/** Findings from comparing the wording of a line with the wording of its linked claims. */
function wordingFindings(text: string, linked: ProfileClaim[], declaredOrigin: ResumeBullet["origin"]): Finding[] {
  const sources = linked.map((c) => c.claim_text);
  const verbatim = sources.some((s) => canonical(s) === canonical(text)) || canonical(sources.join("; ")) === canonical(text);
  if (verbatim) return [];
  const findings: Finding[] = [];
  const source = sources.join(" \n ");
  const sourceWords = new Set(wordsIn(source));
  const sourceNumbers = new Set(numbersIn(source));
  const words = wordsIn(text);
  const fresh = (list: string[]) => [...new Set(list.filter((w) => !sourceWords.has(w)))];

  const figures = [...new Set([...numbersIn(text).filter((n) => !sourceNumbers.has(n)), ...fresh(words.filter((w) => NUMBER_WORDS.includes(w)))])];
  if (figures.length > 0) findings.push({ code: "new-figure", severity: "block", message: `Contains figure(s) not found in the linked claims: ${figures.join(", ")}.` });
  const credentials = fresh(words.filter(isCredential));
  if (credentials.length > 0) findings.push({ code: "new-credential", severity: "block", message: `Mentions a credential or qualification not found in the linked claims: ${credentials.join(", ")}.` });
  const titles = fresh(words.filter((w) => TITLE_WORDS.includes(w)));
  if (titles.length > 0) findings.push({ code: "new-title", severity: "block", message: `Mentions a title or level not found in the linked claims: ${titles.join(", ")}.` });
  const claimedNames = new Set(properNames(source).concat([...sourceWords]));
  const names = [...new Set(properNames(text).filter((n) => !claimedNames.has(n) && !n.split(/[^a-z]+/).every((part) => sourceWords.has(part))))];
  if (names.length > 0) findings.push({ code: "new-name", severity: "block", message: `Names something not found in the linked claims: ${names.join(", ")}.` });

  if (findings.length === 0) {
    findings.push({
      code: "reworded",
      severity: "warn",
      message: declaredOrigin === "manual-edit" ? "Wording was edited by hand. Re-read it against the linked evidence before approving." : "Wording differs from the linked claims. Re-read it against the linked evidence before approving.",
    });
  }
  return findings;
}

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
    if (claim.valid_to) {
      const until = Date.parse(claim.valid_to);
      const now = Date.parse(clock);
      // An unreadable validity date or clock is treated as expired: unknown never passes as valid.
      if (Number.isNaN(until) || Number.isNaN(now) || until < now) {
        findings.push({ code: "expired-claim", severity: "block", message: Number.isNaN(until) ? "The linked claim has an unreadable validity date." : `The linked claim expired on ${claim.valid_to.slice(0, 10)}.` });
      }
    }
    if (claim.visibility === "private") {
      findings.push({ code: "private-claim", severity: "block", message: "The linked claim is marked private and may not be exported." });
    }
  }
  if (linked.length > 0) findings.push(...wordingFindings(bullet.text, linked, bullet.origin));
  return findings;
}

export interface BulletReview {
  sectionId: string;
  bullet: ResumeBullet;
  findings: Finding[];
}

export interface VariantReview {
  /** One entry per line, in document order. Lines are addressed by position, never by a trusted id. */
  lines: BulletReview[];
  /** Findings by line id. When two lines share an id, both sets are merged under it and a blocking finding is added. */
  byBullet: Map<string, Finding[]>;
  /** Blocking problems with section headings (they are exported too). */
  headingFindings: Finding[];
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
  const lines: BulletReview[] = [];
  const byBullet = new Map<string, Finding[]>();
  const headingFindings: Finding[] = [];
  for (const section of variant.sections) {
    const headingWords = wordsIn(section.heading);
    if (numbersIn(section.heading).length > 0 || headingWords.some((w) => isCredential(w) || TITLE_WORDS.includes(w)) || section.heading.length > 60) {
      headingFindings.push({ code: "new-credential", severity: "block", message: `The section heading “${section.heading.slice(0, 60)}” states a figure, title or qualification. Headings must be plain labels.` });
    }
    for (const bullet of section.bullets) {
      const findings = lintBullet(bullet, byId, clock);
      if (byBullet.has(bullet.id)) findings.push({ code: "duplicate-id", severity: "block", message: "Another line has the same id; the variant is malformed." });
      lines.push({ sectionId: section.id, bullet, findings });
      byBullet.set(bullet.id, [...(byBullet.get(bullet.id) ?? []), ...findings]);
    }
  }
  const all = [...lines.flatMap((l) => l.findings), ...headingFindings];
  const blocking = all.filter((f) => f.severity === "block").length;
  const warnings = all.filter((f) => f.severity === "warn").length;
  const digest = resumeContentDigest(variant.sections);
  const approvalCurrent = variant.approval !== null && variant.approval.content_digest === digest;
  const approvalStale = variant.approval !== null && !approvalCurrent;
  const exportBlockers: string[] = [];
  if (lines.length === 0) exportBlockers.push("The variant has no content.");
  if (blocking > 0) exportBlockers.push(`${blocking} blocking truthfulness issue${blocking === 1 ? "" : "s"} must be resolved.`);
  if (variant.approval === null) exportBlockers.push("You have not approved this exact content yet.");
  if (approvalStale) exportBlockers.push("The content changed after approval; approve it again.");
  return { lines, byBullet, headingFindings, blocking, warnings, digest, approvalCurrent, approvalStale, exportAllowed: exportBlockers.length === 0, exportBlockers };
}

/**
 * Plain-text export. Only content covered by the approval digest is printed: the
 * variant's label is an internal name and is deliberately left out.
 */
export function variantToText(variant: ResumeVariant, candidateName: string): string {
  const lines = [candidateName, ""];
  for (const s of variant.sections) {
    lines.push(s.heading.toUpperCase());
    for (const b of s.bullets) lines.push(`- ${b.text}`);
    lines.push("");
  }
  lines.push("[Synthetic demo export. Fictional candidate. Not a real résumé.]");
  return lines.join("\n");
}

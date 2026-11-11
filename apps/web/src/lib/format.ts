import type { Compensation, JobPosting } from "@/contracts/types";

const DATE = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", hour12: false,
});

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "Unknown";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "Unknown" : DATE.format(t);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Unknown";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "Unknown" : `${DATE_TIME.format(t)} UTC`;
}

/** Whole days from `iso` to `clock`, never negative. */
export function daysBefore(iso: string, clock: string): number {
  const diff = Date.parse(clock) - Date.parse(iso);
  return Number.isNaN(diff) ? 0 : Math.max(0, Math.floor(diff / 86_400_000));
}

export type Freshness = "fresh" | "aging" | "stale";
export function freshness(fetchedAt: string, clock: string): { level: Freshness; days: number; label: string } {
  if (Number.isNaN(Date.parse(fetchedAt)) || Number.isNaN(Date.parse(clock))) return { level: "stale", days: 0, label: "Capture time unreadable" };
  const days = daysBefore(fetchedAt, clock);
  const level: Freshness = days <= 7 ? "fresh" : days <= 21 ? "aging" : "stale";
  const label = days === 0 ? "Captured on the snapshot date" : `Captured ${days} day${days === 1 ? "" : "s"} before the snapshot`;
  return { level, days, label };
}

function money(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(amount);
  } catch {
    return `${amount.toLocaleString("en-US")} ${currency}`;
  }
}

/** Compensation text. UNKNOWN stays "Not disclosed"; it is never shown as 0. */
export function formatCompensation(comp: Compensation | null | undefined): { text: string; known: boolean; verified: boolean } {
  if (!comp) return { text: "Not disclosed", known: false, verified: false };
  const { min, max, currency, basis, verified } = comp;
  if (min === null && max === null) return { text: "Range unknown", known: false, verified };
  const range = min !== null && max !== null ? `${money(min, currency)}–${money(max, currency)}`
    : min !== null ? `From ${money(min, currency)}` : `Up to ${money(max as number, currency)}`;
  const basisText = basis === "unknown" ? "basis unknown" : basis;
  return { text: `${range} · ${basisText} · ${verified ? "verified" : "unverified"}`, known: true, verified };
}

export function humanise(slug: string): string {
  const text = slug.replace(/[-_]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : slug;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "Unknown";
  return `${(value * 100).toFixed(digits)}%`;
}

export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** Key used to group likely duplicate postings (same employer + same title). Presentation-only. */
export function duplicateKey(job: Pick<JobPosting, "organization" | "title">): string {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  return `${norm(job.organization)}|${norm(job.title)}`;
}

/**
 * Output-safety helpers: external link classification and CSV export hardening.
 * Job postings are untrusted input. Nothing here "sanitises HTML" because the
 * app never renders HTML from data at all — text is always rendered as text.
 */

export type LinkVerdict =
  | { kind: "ok"; href: string; host: string; notes: string[] }
  | { kind: "blocked"; reason: string };

const RESERVED_TLDS = [".invalid", ".example", ".test", ".localhost"];
const RESERVED_HOSTS = ["example.com", "example.org", "example.net"];

function isPrivateHost(host: string): boolean {
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) return true;
  if (host.startsWith("[")) return true; // IPv6 literal: never expected for a job posting
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return /^\d+$/.test(host) || /^0x[0-9a-f]+$/i.test(host); // integer / hex IP forms
  return true; // any literal IPv4 address is refused: postings are expected on named hosts
}

/**
 * Decide whether a posting URL may be offered as a manual "open in new tab" link.
 * Only absolute http(s) URLs on public named hosts pass; everything else is blocked.
 */
export function classifyLink(raw: string): LinkVerdict {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { kind: "blocked", reason: "The posting has no link." };
  if (trimmed.length > 2048) return { kind: "blocked", reason: "The link is unreasonably long." };
  // Control characters and whitespace are stripped by URL parsers and used to smuggle schemes.
  if (/[\u0000- \u007f]/.test(trimmed)) return { kind: "blocked", reason: "The link contains whitespace or control characters." };
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return { kind: "blocked", reason: "The link is not a valid absolute URL." };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return { kind: "blocked", reason: `Links with the scheme '${url.protocol.replace(":", "")}' are never opened.` };
  }
  if (url.username || url.password) return { kind: "blocked", reason: "The link embeds credentials." };
  const host = url.hostname.toLowerCase();
  if (isPrivateHost(host)) return { kind: "blocked", reason: "The link points at a local or numeric network address." };
  // Single-label hosts are intranet-style names, never a public posting. URL parsers also turn
  // a triple-slash link with an empty authority into a single-label host, which lands here.
  if (!/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(host)) return { kind: "blocked", reason: "The link does not point at a public host name." };
  const notes: string[] = [];
  if (url.protocol === "http:") notes.push("This link is not encrypted (http).");
  if (RESERVED_TLDS.some((t) => host.endsWith(t)) || RESERVED_HOSTS.some((h) => host === h || host.endsWith(`.${h}`))) {
    notes.push("This is a reserved test domain. It will not resolve: the posting is synthetic.");
  }
  if (host.startsWith("xn--") || host.includes(".xn--")) notes.push("The host name uses punycode; check it carefully.");
  return { kind: "ok", href: url.href, host, notes };
}

const FORMULA_LEAD = /^[=+\-@\t\r]/;

/** Neutralise spreadsheet formula injection, then apply RFC 4180 quoting. */
export function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? "" : String(value);
  if (FORMULA_LEAD.test(text)) text = `'${text}`;
  if (/[",\r\n]/.test(text) || text !== text.trim()) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** Trigger a local file download. Nothing leaves the device. */
export function downloadText(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function newId(prefix: string): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return `${prefix}-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

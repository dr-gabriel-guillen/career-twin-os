/**
 * Canonical digest of résumé content. An approval is bound to this digest, so any
 * later edit to text, order or evidence links invalidates the approval.
 *
 * NOTE: only imports ./sha256.ts (with extension) so Node can run it directly.
 */
import { sha256Hex } from "./sha256.ts";

interface DigestSection {
  heading: string;
  bullets: { text: string; claim_ids: string[] }[];
}

export function resumeContentDigest(sections: DigestSection[]): string {
  const canonical = sections.map((s) => [s.heading, s.bullets.map((b) => [b.text, [...b.claim_ids].sort()])]);
  return sha256Hex(JSON.stringify(canonical));
}

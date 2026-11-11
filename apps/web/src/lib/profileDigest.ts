/**
 * Digest of the claim snapshot a match engine used. If the vault changes, the
 * digest changes and the UI flags match scores as stale instead of recomputing
 * them (matching belongs to lane A).
 * NOTE: only imports ./sha256.ts (with extension) so Node can run it directly.
 */
import { sha256Hex } from "./sha256.ts";

interface DigestClaim {
  id: string;
  status: string;
  skills?: string[];
}

export function profileDigest(claims: DigestClaim[]): string {
  const canonical = claims
    .map((c) => [c.id, c.status, [...(c.skills ?? [])].sort()] as const)
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return sha256Hex(JSON.stringify(canonical));
}

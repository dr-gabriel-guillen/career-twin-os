/**
 * Deterministic stratified variant assignment.
 *
 * This is a byte-for-byte mirror of `assign_variant` in the frozen reference
 * engine on `main` (tools/reference_engine.py): SHA-256 over
 * salt U+241F experiment_id U+241F stratum U+241F unit_id, first 8 bytes as a
 * big-endian unsigned integer, modulo the number of variants.
 *
 * Parity with the Python reference is asserted with golden vectors in
 * tests/unit/reference-parity.test.ts. Lane B owns assignment policy; when B publishes
 * its implementation this mirror must be replaced by B's adapter, not evolved.
 *
 * NOTE: only imports ./sha256.ts (with extension) so Node can run it directly.
 */
import { sha256Bytes, utf8 } from "./sha256.ts";

export const ASSIGNMENT_ALGORITHM = "reference-engine/assign_variant@main-bc957fd" as const;
const UNIT_SEPARATOR = "\u241f";

export function assignVariant(
  experimentId: string,
  stratum: string,
  unitId: string,
  variants: readonly string[],
  salt: string,
): string {
  const unique = new Set(variants);
  if (unique.size < 2 || unique.size !== variants.length || !unitId || !experimentId || !salt) {
    throw new Error("Need a valid preregistered experiment, >=2 unique variants, unit and salt");
  }
  const digest = sha256Bytes(utf8([salt, experimentId, stratum, unitId].join(UNIT_SEPARATOR)));
  let n = 0n;
  for (let i = 0; i < 8; i++) n = (n << 8n) | BigInt(digest[i]!);
  return variants[Number(n % BigInt(variants.length))]!;
}

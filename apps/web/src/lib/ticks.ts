/**
 * Axis ticks for the atlas plot: at most 12 round values between min and max.
 *
 * The loop is counted, never "while t <= max": adding a small step to a very
 * large coordinate may not change it, so an open-ended loop could spin forever
 * on input that passes validation (review finding F-16).
 */
export function niceTicks(min: number, max: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return [];
  const span = max - min || 1;
  const step = 10 ** Math.floor(Math.log10(span / 4));
  const mult = span / 4 / step >= 5 ? 5 : span / 4 / step >= 2 ? 2 : 1;
  const s = step * mult;
  const first = Math.ceil(min / s);
  const count = Math.min(12, Math.floor(max / s + 1e-9) - first + 1);
  if (!Number.isFinite(first) || !Number.isFinite(count) || count <= 0) return [];
  const ticks: number[] = [];
  for (let i = 0; i < count; i++) ticks.push(Math.round((first + i) * s * 1000) / 1000);
  return ticks;
}

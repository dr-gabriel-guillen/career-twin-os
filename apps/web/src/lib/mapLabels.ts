/**
 * Map label helpers. Pure functions, so placement can be unit-tested without a browser.
 */
import { skillLabel } from "@/lib/format";

const ACRONYMS = new Set(["tam", "fde", "sre", "cto", "ml", "ai", "qa", "ux", "ui", "api", "sql", "aws", "llm"]);

/** Labels from upstream are sometimes slugs ("data-engineer", "tam"); show them as words, keeping acronyms. */
export function words(label: string): string {
  const base = /^[a-z0-9]+(-[a-z0-9]+)+$|^[a-z]+$/.test(label) ? skillLabel(label) : label;
  return base.replace(/\b[A-Za-z]+\b/g, (w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w));
}

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
export const overlaps = (a: Box, b: Box): boolean => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

export interface LabelRequest {
  key: string;
  text: string;
  /** Marker centre and radius, in drawing pixels. */
  x: number;
  y: number;
  r: number;
  /** Font size in pixels. */
  size: number;
  /** A shorter form to try when the full text fits nowhere (e.g. the role family code). */
  short?: string;
}

export interface PlacedLabel {
  key: string;
  text: string;
  x: number;
  y: number;
  anchor: "start" | "middle" | "end";
  box: Box;
}

/** Conservative width estimate for the UI face at 600 weight (about 0.56 em per character). */
export const textWidth = (text: string, size: number): number => text.length * size * 0.56;

/**
 * Greedy label placement: each label tries eight positions around its marker, in order, and takes
 * the first that stays inside the drawing and clears every marker and every label already placed.
 * Labels are offered in priority order. A label that fits nowhere is not drawn; it is returned in
 * `hidden` so the page can list it instead of letting text collide.
 */
export function placeLabels(items: LabelRequest[], markers: Box[], width: number, height: number): { placed: PlacedLabel[]; hidden: string[] } {
  const placed: PlacedLabel[] = [];
  const hidden: string[] = [];
  for (const req of items) {
    const tries = req.short && req.short !== req.text ? [req.text, req.short] : [req.text];
    let done = false;
    for (const text of tries) {
      const it = { ...req, text };
      if (tryPlace(it, placed, markers, width, height)) {
        done = true;
        break;
      }
    }
    if (!done) hidden.push(req.text);
  }
  return { placed, hidden };
}

function tryPlace(it: LabelRequest, placed: PlacedLabel[], markers: Box[], width: number, height: number): boolean {
  {
    const w = textWidth(it.text, it.size);
    const h = it.size + 3;
    const g = it.r + 4;
    const options: Omit<PlacedLabel, "key" | "text">[] = [
      { x: it.x + g, y: it.y + h / 3, anchor: "start", box: { x0: it.x + g, y0: it.y - h / 2, x1: it.x + g + w, y1: it.y + h / 2 } },
      { x: it.x - g, y: it.y + h / 3, anchor: "end", box: { x0: it.x - g - w, y0: it.y - h / 2, x1: it.x - g, y1: it.y + h / 2 } },
      { x: it.x, y: it.y - g - 2, anchor: "middle", box: { x0: it.x - w / 2, y0: it.y - g - h, x1: it.x + w / 2, y1: it.y - g } },
      { x: it.x, y: it.y + g + h - 2, anchor: "middle", box: { x0: it.x - w / 2, y0: it.y + g, x1: it.x + w / 2, y1: it.y + g + h } },
      { x: it.x + g, y: it.y - g, anchor: "start", box: { x0: it.x + g, y0: it.y - g - h, x1: it.x + g + w, y1: it.y - g + 2 } },
      { x: it.x + g, y: it.y + g + h - 2, anchor: "start", box: { x0: it.x + g, y0: it.y + g, x1: it.x + g + w, y1: it.y + g + h } },
      { x: it.x - g, y: it.y - g, anchor: "end", box: { x0: it.x - g - w, y0: it.y - g - h, x1: it.x - g, y1: it.y - g + 2 } },
      { x: it.x - g, y: it.y + g + h - 2, anchor: "end", box: { x0: it.x - g - w, y0: it.y + g, x1: it.x - g, y1: it.y + g + h } },
    ];
    const fit = options.find(
      (o) =>
        o.box.x0 >= 2 &&
        o.box.x1 <= width - 2 &&
        o.box.y0 >= 2 &&
        o.box.y1 <= height - 2 &&
        !markers.some((m) => overlaps(m, o.box)) &&
        !placed.some((p) => overlaps(p.box, o.box)),
    );
    if (!fit) return false;
    placed.push({ key: it.key, text: it.text, ...fit });
    return true;
  }
}

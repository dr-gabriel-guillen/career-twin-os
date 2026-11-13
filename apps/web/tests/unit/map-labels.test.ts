import { describe, expect, it } from "vitest";
import { overlaps, placeLabels, words, type LabelRequest } from "@/lib/mapLabels";

const req = (key: string, x: number, y: number, text = key): LabelRequest => ({ key, text, x, y, r: 6, size: 11.5 });

describe("map label placement", () => {
  it("never lets two placed labels overlap, or a label cover a marker, or leave the drawing", () => {
    // A dense cluster like the real atlas: centres a few pixels apart near an edge.
    const items = [req("you", 300, 150, "You"), ...Array.from({ length: 14 }, (_, i) => req(`c${i}`, 280 + (i % 4) * 9, 140 + Math.floor(i / 4) * 8, `Role family ${i}`))];
    const markers = items.map((it) => ({ x0: it.x - 6, y0: it.y - 6, x1: it.x + 6, y1: it.y + 6 }));
    const { placed, hidden } = placeLabels(items, markers, 360, 240);
    for (const a of placed) {
      expect(a.box.x0).toBeGreaterThanOrEqual(2);
      expect(a.box.x1).toBeLessThanOrEqual(358);
      for (const b of placed) if (a !== b) expect(overlaps(a.box, b.box)).toBe(false);
      for (const m of markers) expect(overlaps(a.box, m)).toBe(false);
    }
    // Nothing is silently dropped: every request is either drawn or reported.
    expect(placed.length + hidden.length).toBe(items.length);
    expect(hidden.length).toBeGreaterThan(0);
  });

  it("places labels in priority order, so the first ones win the space", () => {
    const { placed } = placeLabels([req("you", 100, 100, "You"), req("other", 102, 100, "Other label")], [], 300, 200);
    expect(placed[0]!.key).toBe("you");
  });

  it("keeps acronyms in role names", () => {
    expect(words("tam")).toBe("TAM");
    expect(words("fde")).toBe("FDE");
    expect(words("data-engineer")).toMatch(/^Data engineer$/i);
  });
});

describe("short label fallback", () => {
  it("uses the short form when the full name fits nowhere, and still never overlaps", () => {
    const wide = { key: "c", text: "Forward Deployed Engineer", short: "FDE", x: 40, y: 20, r: 6, size: 11.5 };
    const { placed, hidden } = placeLabels([wide], [], 120, 40);
    expect(hidden).toEqual([]);
    expect(placed[0]!.text).toBe("FDE");
  });
});

/**
 * Parity with the FROZEN reference engine on main (tools/reference_engine.py).
 * Golden vectors are produced by really running that Python file:
 *   python scripts/reference_vectors.py
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { MatchExplanation } from "@/adapters/proposed";
import { assignVariant } from "@/lib/assign";
import { mulberry32, seedFromString } from "@/lib/prng";
import { sha256Hex } from "@/lib/sha256";

const golden = JSON.parse(readFileSync(join(__dirname, "golden", "reference-vectors.json"), "utf8")) as {
  assign: { experiment_id: string; stratum: string; unit_id: string; variants: string[]; salt: string; expected: string }[];
  sha256: { text: string; sha256: string }[];
  score_job: { job_id: string; explanation: { skill_coverage: number; trajectory_score: number; workstyle: number }; missing_skills: string[] }[];
};
const matches = JSON.parse(readFileSync(join(__dirname, "..", "..", "src", "fixtures", "generated", "matches.json"), "utf8")) as MatchExplanation[];

describe("sha256", () => {
  it("matches Python hashlib on the golden strings", () => {
    for (const c of golden.sha256) expect(sha256Hex(c.text)).toBe(c.sha256);
  });
  it("matches Node crypto on 300 generated strings across block boundaries", () => {
    const rng = mulberry32(7);
    for (let i = 0; i < 300; i++) {
      const len = i < 140 ? i : Math.floor(rng() * 4000);
      let s = "";
      for (let k = 0; k < len; k++) s += String.fromCodePoint(32 + Math.floor(rng() * (k % 9 === 0 ? 20000 : 90)));
      expect(sha256Hex(s)).toBe(createHash("sha256").update(s, "utf8").digest("hex"));
    }
  });
});

describe("assignVariant mirrors reference_engine.assign_variant", () => {
  it("returns the same variant as Python for all 60 golden cases", () => {
    expect(golden.assign).toHaveLength(60);
    for (const c of golden.assign) expect(assignVariant(c.experiment_id, c.stratum, c.unit_id, c.variants, c.salt)).toBe(c.expected);
  });
  it("is deterministic and depends on salt, stratum and unit", () => {
    const v = ["A", "B"];
    const once = assignVariant("e", "s", "u", v, "salt");
    for (let i = 0; i < 50; i++) expect(assignVariant("e", "s", "u", v, "salt")).toBe(once);
    const spread = new Set(Array.from({ length: 200 }, (_, i) => assignVariant("e", "s", `unit-${i}`, v, "salt")));
    expect(spread).toEqual(new Set(v));
  });
  it("is roughly balanced over many units (sanity, not a guarantee)", () => {
    let a = 0;
    for (let i = 0; i < 4000; i++) if (assignVariant("exp", "stratum", `u${i}`, ["A", "B"], "salt") === "A") a++;
    expect(a).toBeGreaterThan(1850);
    expect(a).toBeLessThan(2150);
  });
  it("rejects invalid designs exactly like the reference", () => {
    for (const bad of [["A"], ["A", "A"], []]) expect(() => assignVariant("e", "s", "u", bad, "salt")).toThrow();
    expect(() => assignVariant("", "s", "u", ["A", "B"], "salt")).toThrow();
    expect(() => assignVariant("e", "s", "", ["A", "B"], "salt")).toThrow();
    expect(() => assignVariant("e", "s", "u", ["A", "B"], "")).toThrow();
  });
});

describe("fixture stand-in match payload vs reference_engine.score_job", () => {
  it("skill coverage, workstyle and missing skills equal the reference for the four jobs shared with main", () => {
    expect(golden.score_job).toHaveLength(4);
    for (const ref of golden.score_job) {
      const m = matches.find((x) => x.job_id === ref.job_id);
      expect(m, ref.job_id).toBeDefined();
      const component = (key: string) => m!.near_term_fit.components.find((c) => c.key === key)?.value;
      expect(component("skill_coverage")).toBeCloseTo(ref.explanation.skill_coverage, 3);
      expect(component("workstyle")).toBeCloseTo(ref.explanation.workstyle, 6);
      const trajectory = m!.strategic_value.components.find((c) => c.key === "trajectory_utility")?.value;
      expect(trajectory).toBeCloseTo(ref.explanation.trajectory_score, 6);
      const missing = m!.skills.filter((s) => s.evidence_strength === "missing").map((s) => s.skill).sort();
      expect(missing).toEqual(ref.missing_skills);
    }
  });
});

describe("prng", () => {
  it("is deterministic for a seed and stays in [0,1)", () => {
    const a = mulberry32(seedFromString("x"));
    const b = mulberry32(seedFromString("x"));
    for (let i = 0; i < 1000; i++) {
      const v = a();
      expect(v).toBe(b());
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
    expect(seedFromString("x")).not.toBe(seedFromString("y"));
  });
});

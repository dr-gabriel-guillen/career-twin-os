/**
 * Adapter behaviour: what gets accepted, what gets rejected, and that nothing is
 * repaired or guessed. Uses an in-memory PayloadSource over the real fixtures.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ABSENT, ADAPTERS, loadBundle, totalRejected, type PayloadName, type PayloadSource } from "@/adapters";
import { isAtlasProjection, isExperimentOutcome, isMatchExplanation, isResumeVariant, isScenarioOutputs } from "@/adapters/proposedValidators";
import { Checker } from "@/adapters/validate";

const dir = join(__dirname, "..", "..", "src", "fixtures", "generated");
const read = (name: PayloadName): unknown => JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

function source(overrides: Partial<Record<PayloadName, unknown | (() => never)>> = {}): PayloadSource {
  return {
    id: "test",
    label: "test source",
    async load(name) {
      if (name in overrides) {
        const o = overrides[name];
        if (typeof o === "function") return (o as () => never)();
        return o;
      }
      // The fixtures have no simulated experiment-run stream.
      return name === "lab-events" ? ABSENT : read(name);
    },
  };
}
const ok = (validate: (v: unknown, c: Checker) => boolean, v: unknown) => {
  const c = new Checker();
  return validate(v, c) && c.issues.length === 0;
};
const report = (b: Awaited<ReturnType<typeof loadBundle>>, id: string) => b.reports.find((r) => r.adapter.id === id)!;

describe("loadBundle over the shipped fixtures", () => {
  it("accepts the valid records and rejects exactly the three malformed feed records", async () => {
    const b = await loadBundle(source());
    expect(b.jobs).toHaveLength(22);
    expect(report(b, "jobs").rejected).toHaveLength(3);
    expect(totalRejected(b)).toBe(3);
    expect(b.matches).toHaveLength(22);
    expect(b.projections.map((p) => p.method)).toEqual(["pca", "interpretable-axes"]);
    expect(b.scenarios.every((s) => s.outputs !== null)).toBe(true);
    expect(b.clock).toBe("2026-11-10T12:00:00Z");
    expect(b.reports).toHaveLength(ADAPTERS.length);
  });

  it("every fixture employer is explicitly fictional and every allowed link is a reserved test domain", async () => {
    const b = await loadBundle(source());
    for (const j of b.jobs) {
      expect(j.source).toBe("synthetic");
      expect(j.organization).toContain("Fictional");
      if (j.url.startsWith("http")) expect(new URL(j.url).hostname.endsWith(".invalid")).toBe(true);
    }
  });

  it("unknown compensation stays null and is never turned into zero", async () => {
    const b = await loadBundle(source());
    const unknown = b.jobs.filter((j) => j.compensation === null || j.compensation === undefined);
    expect(unknown.length).toBeGreaterThan(5);
    for (const m of b.matches) {
      const comp = m.strategic_value.components.find((c) => c.key === "compensation");
      if (comp) {
        expect(comp.value).toBeNull();
        expect(comp.status).toBe("unknown");
        expect(comp.weight).toBe(0);
      }
    }
  });
});

describe("loadBundle negative cases", () => {
  it("reports a fatal error when a payload is not an array, and keeps going", async () => {
    const b = await loadBundle(source({ claims: { not: "an array" } }));
    expect(b.claims).toEqual([]);
    expect(report(b, "claims").fatal[0]?.message).toMatch(/array/);
    expect(b.jobs).toHaveLength(22);
  });

  it("reports a fatal error when a payload cannot be loaded at all", async () => {
    const b = await loadBundle(source({ "jobs.feed": () => { throw new Error("disk on fire"); } }));
    expect(b.jobs).toEqual([]);
    expect(report(b, "jobs").fatal[0]?.message).toContain("disk on fire");
    // Matches for jobs that are not in the feed are dropped, not shown as orphans.
    expect(b.matches).toEqual([]);
    expect(report(b, "matches").integrity.length).toBe(22);
  });

  it("drops duplicate ids (first wins) and says so", async () => {
    const feed = read("jobs.feed") as Record<string, unknown>[];
    const dup = { ...clone(feed[0]!), title: "Second copy with the same id" } as { id: string; title: string };
    const b = await loadBundle(source({ "jobs.feed": [...feed, dup] }));
    expect(b.jobs).toHaveLength(22);
    expect(b.jobs.find((j) => j.id === dup.id)?.title).not.toBe("Second copy with the same id");
    expect(report(b, "jobs").integrity.join(" ")).toContain("duplicate id");
  });

  it("refuses a match payload that calls its score a probability", async () => {
    const matches = clone(read("matches")) as Record<string, unknown>[];
    matches[0]!.score_type = "hiring_probability";
    const b = await loadBundle(source({ matches }));
    expect(b.matches).toHaveLength(21);
    expect(report(b, "matches").rejected[0]?.issues[0]?.path).toContain("score_type");
  });

  it("refuses a match component that is 'unknown' but carries a number, and vice versa", () => {
    const m = clone((read("matches") as Record<string, any>[])[0]!);
    expect(ok(isMatchExplanation, m)).toBe(true);
    const a = clone(m);
    a.near_term_fit.components[0].status = "unknown";
    expect(ok(isMatchExplanation, a)).toBe(false);
    const b = clone(m);
    b.near_term_fit.components[0].value = null;
    expect(ok(isMatchExplanation, b)).toBe(false);
    const c = clone(m);
    c.near_term_fit.score = 140;
    expect(ok(isMatchExplanation, c)).toBe(false);
  });

  it("refuses censored or missing outcomes that carry a value, and observed ones that do not", () => {
    const o = clone((read("outcomes") as Record<string, any>[])[0]!);
    expect(ok(isExperimentOutcome, o)).toBe(true);
    expect(ok(isExperimentOutcome, { ...o, status: "censored", value: false })).toBe(false);
    expect(ok(isExperimentOutcome, { ...o, status: "missing", value: true })).toBe(false);
    expect(ok(isExperimentOutcome, { ...o, status: "observed", value: null })).toBe(false);
    expect(ok(isExperimentOutcome, { ...o, data_origin: "real" })).toBe(false);
  });

  it("drops outcomes and assignments that point at records that do not exist", async () => {
    const outcomes = clone(read("outcomes")) as Record<string, unknown>[];
    outcomes[0]!.assignment_id = "asg-does-not-exist";
    const assignments = clone(read("assignments")) as Record<string, unknown>[];
    assignments[1]!.experiment_id = "exp-does-not-exist";
    const b = await loadBundle(source({ outcomes, assignments }));
    expect(report(b, "assignments").integrity).toHaveLength(1);
    // One outcome re-pointed by hand, one orphaned because its assignment was dropped.
    expect(report(b, "outcomes").integrity).toHaveLength(2);
    expect(b.outcomes).toHaveLength(12);
  });

  it("rejects an atlas projection whose points disagree with it, and one with a dangling path", () => {
    const p = clone(read("atlas.pca")) as Record<string, any>;
    expect(ok(isAtlasProjection, p)).toBe(true);
    const wrongMethod = clone(p);
    wrongMethod.points[0].projection_method = "umap";
    expect(ok(isAtlasProjection, wrongMethod)).toBe(false);
    const dangling = clone(p);
    dangling.paths[0].to_entity_id = "nowhere";
    expect(ok(isAtlasProjection, dangling)).toBe(false);
    const dupPoint = clone(p);
    dupPoint.points[1].entity_id = dupPoint.points[0].entity_id;
    expect(ok(isAtlasProjection, dupPoint)).toBe(false);
    const badQuality = clone(p);
    badQuality.points[0].neighborhood_quality = 1.4;
    expect(ok(isAtlasProjection, badQuality)).toBe(false);
    const badDiag = clone(p);
    badDiag.diagnostics.trustworthiness.value = 2;
    expect(ok(isAtlasProjection, badDiag)).toBe(false);
  });

  it("keeps a scenario whose outputs are not understood, but exposes no derived numbers for it", async () => {
    const scenarios = clone(read("scenarios")) as Record<string, any>[];
    scenarios[0]!.outputs = { success_probability: 0.8 };
    const b = await loadBundle(source({ scenarios }));
    expect(b.scenarios).toHaveLength(4);
    expect(b.scenarios[0]!.outputs).toBeNull();
    expect(report(b, "scenarios").integrity[0]).toContain("scenario-outputs/0.1-proposed");
    expect(ok(isScenarioOutputs, { ...clone(read("scenarios") as any[])[1].outputs, coverage_after: 1.2 })).toBe(false);
  });

  it("rejects a non-hypothetical scenario (frozen contract const)", async () => {
    const scenarios = clone(read("scenarios")) as Record<string, any>[];
    scenarios[0]!.status = "forecast";
    const b = await loadBundle(source({ scenarios }));
    expect(b.scenarios).toHaveLength(3);
  });

  it("rejects a résumé variant with a malformed approval", () => {
    const v = clone((read("resume-variants") as Record<string, any>[])[1]!);
    expect(ok(isResumeVariant, v)).toBe(true);
    expect(ok(isResumeVariant, { ...v, approval: { approved_at: "yesterday", approval_token_ref: "x", content_digest: "y" } })).toBe(false);
    expect(ok(isResumeVariant, { ...v, approval: {} })).toBe(false);
    expect(ok(isResumeVariant, { ...v, schema_version: "resume-variant/9" })).toBe(false);
  });

  it("flags application events that reference a job missing from the feed", async () => {
    const events = clone(read("application-events")) as Record<string, unknown>[];
    events[0]!.job_id = "job-not-in-feed";
    const b = await loadBundle(source({ "application-events": events }));
    expect(report(b, "application-events").integrity[0]).toContain("not in the feed");
    // The event itself is kept: it is the user's history and still valid per contract.
    expect(b.applicationEvents).toHaveLength(events.length);
  });
});

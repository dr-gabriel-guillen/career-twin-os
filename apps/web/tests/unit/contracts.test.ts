/**
 * Proves the hand-written runtime validators (used in the browser, where the CSP
 * forbids Ajv's code generation) agree with the FROZEN JSON Schemas in /contracts.
 *
 * 1. Every shipped fixture record validates against the frozen schema with Ajv.
 * 2. TypeScript enum mirrors equal the schema enums.
 * 3. A mutation corpus (delete / retype / extend every field) gets the SAME
 *    accept/reject verdict from the hand validator and from Ajv.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { isApplicationEvent, isAtlasPoint, isExperimentSpec, isJobPosting, isProfileClaim, isTransitionScenario } from "@/adapters/frozen";
import { Checker, isRfc3339, parseList, type ItemValidator } from "@/adapters/validate";
import * as T from "@/contracts/types";

const ROOT = join(__dirname, "..", "..", "..", "..");
const schema = (name: string) => JSON.parse(readFileSync(join(ROOT, "contracts", name), "utf8")) as Record<string, any>;
const fixture = (name: string) => JSON.parse(readFileSync(join(__dirname, "..", "..", "src", "fixtures", "generated", name), "utf8")) as unknown;

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);

const hand = <X>(validate: ItemValidator<X>) => (value: unknown) => {
  const c = new Checker();
  return validate(value, c) && c.issues.length === 0;
};

const SUITES = [
  { name: "job-posting", file: "job-posting.schema.json", validate: hand(isJobPosting), records: () => parseList(fixture("jobs.feed.json"), isJobPosting).items as unknown[] },
  { name: "profile-claim", file: "profile-claim.schema.json", validate: hand(isProfileClaim), records: () => fixture("claims.json") as unknown[] },
  { name: "application-event", file: "application-event.schema.json", validate: hand(isApplicationEvent), records: () => fixture("application-events.json") as unknown[] },
  { name: "experiment", file: "experiment.schema.json", validate: hand(isExperimentSpec), records: () => fixture("experiments.json") as unknown[] },
  { name: "atlas-point", file: "atlas-point.schema.json", validate: hand(isAtlasPoint), records: () => [...(fixture("atlas.pca.json") as any).points, ...(fixture("atlas.axes.json") as any).points] as unknown[] },
  { name: "transition-scenario", file: "transition-scenario.schema.json", validate: hand(isTransitionScenario), records: () => fixture("scenarios.json") as unknown[] },
];

const REPLACEMENTS: unknown[] = [null, 123, -1, 0, 1.5, "x", "", true, [], {}, ["a", "a"], [1], ["a"], "2026-13-40T00:00:00Z", "2026-11-10", "2026-11-10T12:00:00Z", [0.5, 0.5], [1, 2, 3, 4], { a: 1 }];

/** Every single-step mutation of `record`, including one level of nesting. */
function mutations(record: Record<string, unknown>): unknown[] {
  const out: unknown[] = [null, 42, "string", [], [record]];
  for (const key of Object.keys(record)) {
    const { [key]: _removed, ...without } = record;
    out.push(without);
    for (const r of REPLACEMENTS) out.push({ ...record, [key]: r });
    const value = record[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const inner = value as Record<string, unknown>;
      for (const k of Object.keys(inner)) {
        const { [k]: _gone, ...rest } = inner;
        out.push({ ...record, [key]: rest });
        for (const r of REPLACEMENTS) out.push({ ...record, [key]: { ...inner, [k]: r } });
      }
      out.push({ ...record, [key]: { ...inner, extra_field: 1 } });
    }
    if (Array.isArray(value) && value.length > 0 && typeof value[0] === "object" && value[0] !== null) {
      const first = value[0] as Record<string, unknown>;
      for (const k of Object.keys(first)) {
        const { [k]: _gone, ...rest } = first;
        out.push({ ...record, [key]: [rest, ...value.slice(1)] });
        for (const r of REPLACEMENTS) out.push({ ...record, [key]: [{ ...first, [k]: r }, ...value.slice(1)] });
      }
      out.push({ ...record, [key]: [{ ...first, extra_field: 1 }] });
    }
  }
  out.push({ ...record, not_in_contract: true }, { ...record, hiring_probability: 0.9 });
  return out;
}

describe("frozen contracts: fixtures and validator agreement", () => {
  for (const suite of SUITES) {
    const ajvValidate = ajv.compile(schema(suite.file));

    it(`${suite.name}: every shipped fixture record satisfies the frozen schema (Ajv)`, () => {
      const records = suite.records();
      expect(records.length).toBeGreaterThan(0);
      for (const r of records) {
        const ok = ajvValidate(r);
        expect(ok, JSON.stringify(ajvValidate.errors)).toBe(true);
        expect(suite.validate(r)).toBe(true);
      }
    });

    it(`${suite.name}: hand validator and Ajv agree on every mutation`, () => {
      let checked = 0;
      let rejected = 0;
      const disagreements: string[] = [];
      for (const record of suite.records().slice(0, 6)) {
        for (const m of mutations(record as Record<string, unknown>)) {
          const a = ajvValidate(m) === true;
          const h = suite.validate(m);
          checked++;
          if (!a) rejected++;
          if (a !== h) disagreements.push(`ajv=${a} hand=${h} for ${JSON.stringify(m).slice(0, 300)}`);
        }
      }
      console.info(`[contracts] ${suite.name}: ${checked} mutations checked, ${rejected} rejected by Ajv, ${disagreements.length} disagreements`);
      expect(disagreements.slice(0, 5)).toEqual([]);
      expect(checked).toBeGreaterThan(200);
      // The corpus must actually exercise rejection, not just accept everything.
      expect(rejected).toBeGreaterThan(checked / 3);
    });
  }

  it("TypeScript enum mirrors equal the frozen schema enums", () => {
    const job = schema("job-posting.schema.json").properties;
    expect([...T.JOB_SOURCES]).toEqual(job.source.enum);
    expect([...T.JOB_STATUSES]).toEqual(job.status.enum);
    expect([...T.REMOTE_POLICIES]).toEqual(job.remote_policy.enum);
    expect([...T.COMP_BASES]).toEqual(job.compensation.properties.basis.enum);
    const claim = schema("profile-claim.schema.json").properties;
    expect([...T.CLAIM_TYPES]).toEqual(claim.claim_type.enum);
    expect([...T.CLAIM_STATUSES]).toEqual(claim.status.enum);
    expect([...T.CLAIM_VISIBILITIES]).toEqual(claim.visibility.enum);
    const event = schema("application-event.schema.json").properties;
    expect([...T.EVENT_TYPES]).toEqual(event.event_type.enum);
    expect([...T.EVENT_ACTORS]).toEqual(event.actor.enum);
    expect([...T.OUTCOME_STATUSES]).toEqual(event.outcome_status.enum);
    const exp = schema("experiment.schema.json").properties;
    expect([...T.EXPERIMENT_STATUSES]).toEqual(exp.status.enum);
    expect([...T.ASSIGNMENT_UNITS]).toEqual(exp.assignment_unit.enum);
    const point = schema("atlas-point.schema.json").properties;
    expect([...T.REPRESENTATION_TYPES]).toEqual(point.representation_type.enum);
    expect([...T.PROJECTION_METHODS]).toEqual(point.projection_method.enum);
  });

  it("the three deliberately malformed feed records are rejected by both validators", () => {
    const feed = fixture("jobs.feed.json") as unknown[];
    const fresh = new Ajv2020({ allErrors: true, strict: false });
    addFormats(fresh);
    const ajvValidate = fresh.compile(schema("job-posting.schema.json"));
    const bad = feed.filter((r) => !ajvValidate(r));
    expect(bad.map((r) => (r as { id: string }).id).sort()).toEqual(["fake-invalid-extra-key", "fake-invalid-missing-url", "fake-invalid-salary-zero"]);
    const parsed = parseList(feed, isJobPosting);
    expect(parsed.rejected.map((r) => r.id).sort()).toEqual(["fake-invalid-extra-key", "fake-invalid-missing-url", "fake-invalid-salary-zero"]);
    expect(parsed.items).toHaveLength(22);
    // A non-contract probability field is reported by name, not silently dropped.
    const extra = parsed.rejected.find((r) => r.id === "fake-invalid-extra-key");
    expect(extra?.issues.some((i) => i.path.endsWith("hiring_probability"))).toBe(true);
  });

  it("date-time: the hand validator is never more permissive than ajv-formats", () => {
    const dt = ajv.compile({ type: "string", format: "date-time" });
    const samples = [
      "2026-11-10T12:00:00Z", "2026-11-10T12:00:00.123Z", "2026-11-10T12:00:00+05:30", "2026-02-29T00:00:00Z", "2024-02-29T00:00:00Z",
      "2026-11-10 12:00:00Z", "2026-11-10T12:00:00+0530", "2026-11-10T12:00:00", "2026-11-10", "2026-11-10T24:00:00Z", "2026-00-10T00:00:00Z",
      "2026-11-31T00:00:00Z", "not-a-date", "", "2026-11-10T12:60:00Z", "2026-12-31T23:59:60Z",
    ];
    for (const s of samples) {
      if (isRfc3339(s)) expect(dt(s), s).toBe(true);
    }
    // Known, intentional strictness: these are accepted by ajv-formats but refused here.
    expect(isRfc3339("2026-11-10 12:00:00Z")).toBe(false);
    expect(isRfc3339("2026-11-10T12:00:00+0530")).toBe(false);
    expect(isRfc3339("2026-02-29T00:00:00Z")).toBe(false);
    expect(isRfc3339("2024-02-29T00:00:00Z")).toBe(true);
  });
});

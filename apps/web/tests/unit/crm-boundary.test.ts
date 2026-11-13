/**
 * Release blocker: simulated experiment-run activity must never become application activity.
 *
 * The pipeline's experiment stage writes experiment_events.json: approvals and submissions it
 * simulates to exercise the approval gate. These tests pin the boundary on the committed pipeline
 * sample and on hand-made sources:
 * - the application log is events.json only; the simulated stream is bundle.labEvents;
 * - provenance is the stream, never an id prefix or the actor field;
 * - applications, tracked markers, counts and follow-ups (all derived from the workspace log) are
 *   identical whether or not the simulated stream is present;
 * - experiment specs, assignments, outcomes and upstream analyses are untouched.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { loadBundle } from "@/adapters";
import { createArtifactSource, type ArtifactReader } from "@/adapters/artifactSource";
import { createFixtureSource } from "@/adapters/fixtureSource";
import type { ApplicationEvent } from "@/contracts/types";
import { daysWithoutFeedback, groupApplications } from "@/lib/applications";
import { attribution, originOf } from "@/lib/provenance";
import { seedWorkspace } from "@/state/workspace";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "upstream-samples", "pipeline");
const read = (file: string): unknown => {
  try {
    return JSON.parse(readFileSync(join(dir, file), "utf8"));
  } catch {
    return undefined;
  }
};
const reader = (drop: string[] = [], replace: Record<string, unknown> = {}): ArtifactReader => ({
  async read(file) {
    if (file in replace) return replace[file];
    return drop.includes(file) ? undefined : read(file);
  },
});
const load = (drop: string[] = [], replace: Record<string, unknown> = {}) => loadBundle(createArtifactSource(reader(drop, replace)));

const userEvents = read("events.json") as ApplicationEvent[];
const labEvents = read("experiment_events.json") as ApplicationEvent[];
const labAppIds = [...new Set(labEvents.map((e) => e.application_id))];
const logAppIds = [...new Set(userEvents.map((e) => e.application_id))];

describe("simulated experiment activity stays out of the application log (pipeline sample)", () => {
  it("the sample really contains simulated approvals and submissions", () => {
    // Guards against the suite passing vacuously on a sample without the hazard.
    expect(labAppIds.length).toBeGreaterThan(20);
    expect(labEvents.some((e) => e.event_type === "submitted" && e.actor === "user")).toBe(true);
  });

  it("loads events.json as the log and experiment_events.json as a separate simulated stream", async () => {
    const b = await load();
    expect(new Set(b.applicationEvents.map((e) => e.id))).toEqual(new Set(userEvents.map((e) => e.id)));
    expect(new Set(b.labEvents.map((e) => e.id))).toEqual(new Set(labEvents.map((e) => e.id)));
    expect([...b.experimentUnitApplicationIds].sort()).toEqual([...labAppIds].sort());
  });

  it("applications, tracked postings, counts and follow-ups ignore the simulated stream", async () => {
    const withLab = await load();
    const without = await load(["experiment_events.json"]);
    const apps = (b: typeof withLab) => groupApplications(seedWorkspace(b, "fixture").applicationEvents);
    const a = apps(withLab);
    expect(a.map((x) => x.id).sort()).toEqual([...logAppIds].sort());
    expect(a.some((x) => labAppIds.includes(x.id))).toBe(false);
    // Tracked markers come from the postings of these applications.
    expect(new Set(a.map((x) => x.job_id))).toEqual(new Set(apps(without).map((x) => x.job_id)));
    // Follow-up state is identical.
    const clock = withLab.clock;
    expect(a.map((x) => [x.id, x.stage, daysWithoutFeedback(x, clock)])).toEqual(apps(without).map((x) => [x.id, x.stage, daysWithoutFeedback(x, clock)]));
  });

  it("experimental analysis is unchanged: specs, assignments, outcomes and analyses never read the stream", async () => {
    const withLab = await load();
    const without = await load(["experiment_events.json"]);
    expect(withLab.experiments.length).toBeGreaterThan(0);
    expect(withLab.assignments.length).toBe((read("assignments.json") as unknown[]).length);
    expect(withLab.analyses.length).toBeGreaterThan(0);
    expect(without.experiments).toEqual(withLab.experiments);
    expect(without.assignments).toEqual(withLab.assignments);
    expect(without.outcomes).toEqual(withLab.outcomes);
    expect(without.analyses).toEqual(withLab.analyses);
  });
});

describe("provenance is the stream, never the id or the actor", () => {
  const base = userEvents[0]!;
  const ev = (over: Partial<ApplicationEvent>): ApplicationEvent => ({ ...base, ...over });

  it("an experiment-stream event without the app-exp prefix is still simulated; a log event with it is still the log", async () => {
    const lab = [ev({ id: "evt-neutral-name", application_id: "app-plain-123", actor: "user", source: "user:approval-dialog" })];
    const log = [...userEvents, ev({ id: "evt-log-prefixed", application_id: "app-exp-looks-generated", actor: "import", source: "import:x" })];
    const b = await load([], { "experiment_events.json": lab, "events.json": log });
    expect(b.labEvents.map((e) => e.id)).toEqual(["evt-neutral-name"]);
    expect(b.applicationEvents.map((e) => e.application_id)).toContain("app-exp-looks-generated");
    expect(groupApplications(b.applicationEvents).map((a) => a.id)).not.toContain("app-plain-123");
  });

  it("a simulated event that reuses a log id is dropped and reported; a shared application id is reported, never merged", async () => {
    const lab = [ev({}), ev({ id: "evt-sim-2", application_id: base.application_id, event_type: "note" })];
    const b = await load([], { "experiment_events.json": lab });
    expect(b.labEvents.map((e) => e.id)).toEqual(["evt-sim-2"]);
    const report = b.reports.find((r) => r.adapter.id === "lab-events")!;
    expect(report.integrity.join("\n")).toMatch(/reuses an id from the application log/);
    expect(report.integrity.join("\n")).toMatch(/appears in both the application log and the simulated experiment stream/);
    expect(b.applicationEvents.filter((e) => e.id === "evt-sim-2")).toHaveLength(0);
  });

  it("sample data is never attributed to the person, whatever its actor field says", async () => {
    const b = await load();
    const origin = originOf(b);
    const sampleUser = b.applicationEvents.find((e) => e.actor === "user")!;
    expect(origin(sampleUser)).toBe("sample");
    expect(attribution(sampleUser, "sample", true)).toMatch(/^Pipeline sample data/);
    const mine = { ...sampleUser, id: "evt-new-local" };
    expect(origin(mine)).toBe("recorded");
    expect(attribution(mine, "recorded", true)).toBe("You recorded this here");
  });

  it("fixture mode has no simulated stream", async () => {
    const b = await loadBundle(createFixtureSource());
    expect(b.labEvents).toEqual([]);
    expect(b.experimentUnitApplicationIds).toEqual([]);
  });
});

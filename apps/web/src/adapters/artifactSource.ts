/**
 * Pipeline-artifact source: renders what lanes A/B/C produced in an integration
 * run (file layout proposed by lane E on Issue #5) instead of lane D's fixtures.
 *
 * Files are read through an `ArtifactReader`, so the same code runs in the
 * browser (same-origin `./artifacts/*.json`) and in unit tests (in-memory).
 * A file that is absent is reported as ABSENT, never replaced by a stand-in.
 */
import type { JobPosting } from "@/contracts/types";
import { isJobPosting } from "./frozen";
import { ABSENT, type BundleExtras, type PayloadName, type PayloadSource } from "./index";
import type { BundleProvenance, JobFacet, MatchRefusal, UpstreamAnalysis } from "./proposed";
import { analysisFromUpstream, assignmentsFromUpstream, atlasFromArtifacts, facetsFromJobsExt, matchesFromUpstream, resumeVariantsFromUpstream } from "./upstream";
import { Checker } from "./validate";

export interface ArtifactReader {
  /** Resolve to the parsed JSON, or `undefined` when the file does not exist. */
  read(file: string): Promise<unknown>;
}

export const ARTIFACT_FILES = [
  "jobs.json", "claims.json", "matches.json", "pin.json", "resume_variant.json", "events.json", "experiment.json", "assignments.json",
  "experiment_events.json", "analysis.json", "atlas_points.json", "atlas_diagnostics.json", "scenarios.json",
  // Optional lane-shaped extras, used when a pipeline provides them.
  "jobs_ext.json", "matches_full.json", "atlas.pca.json", "atlas.axes.json", "evidence.json", "preferences.json", "outcomes.json",
] as const;
type ArtifactFile = (typeof ARTIFACT_FILES)[number];

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const MOCK_TEXT = /REFERENCE-STUB|"generated_by"\s*:\s*"[^"]*(stub|mock)[^"]*"|"mock"\s*:\s*true/i;

/** Accept a bare JobPosting[] or lane A's envelope (`[{posting, ext}]` / `{items: [...]}`). */
function unwrapJobs(raw: unknown): { payload: unknown; dialect: string } {
  const list = isObj(raw) && Array.isArray(raw.items) ? raw.items : raw;
  if (!Array.isArray(list)) return { payload: raw, dialect: "unrecognised" };
  const enveloped = list.some((x) => isObj(x) && isObj(x.posting));
  return { payload: enveloped ? list.map((x) => (isObj(x) && isObj(x.posting) ? x.posting : x)) : list, dialect: enveloped ? "a-ext/0.1 envelope" : "frozen JobPosting[]" };
}

function validJobs(payload: unknown): JobPosting[] {
  if (!Array.isArray(payload)) return [];
  return payload.filter((j): j is JobPosting => {
    const c = new Checker();
    return isJobPosting(j, c) && c.issues.length === 0;
  });
}

export function createArtifactSource(reader: ArtifactReader, label = "Pipeline artifacts"): PayloadSource {
  const cache = new Map<ArtifactFile, Promise<unknown>>();
  /** A file that exists but cannot be read or parsed. It is a failure, not an absence. */
  class Unreadable extends Error {}
  const unreadable = new Set<string>();
  const read = (file: ArtifactFile) => {
    let p = cache.get(file);
    if (!p) {
      p = reader.read(file).catch((e: unknown) => {
        unreadable.add(file);
        return new Unreadable(`${file} could not be read: ${e instanceof Error ? e.message : String(e)}`);
      });
      cache.set(file, p);
    }
    return p;
  };
  /** For optional siblings and derived facts: an unreadable file counts as not there (and is reported once). */
  const peek = async (file: ArtifactFile): Promise<unknown> => {
    const raw = await read(file);
    return raw instanceof Unreadable ? undefined : raw;
  };
  const dialects: Record<string, string> = {};
  const mock = new Set<string>();
  const missing = new Set<string>();
  const analyses: UpstreamAnalysis[] = [];
  const refusals: MatchRefusal[] = [];
  const notes: string[] = [];

  const need = async (file: ArtifactFile): Promise<unknown> => {
    const raw = await read(file);
    // Throwing makes the adapter report a fatal "could not be loaded", instead of "not provided".
    if (raw instanceof Unreadable) throw raw;
    if (raw === undefined) {
      missing.add(file);
      return undefined;
    }
    try {
      if (MOCK_TEXT.test(JSON.stringify(raw).slice(0, 200_000))) mock.add(file);
    } catch {
      /* not serialisable: leave unmarked */
    }
    return raw;
  };

  /**
   * Reference clock for ages and expiry: the latest instant the run itself mentions (capture times,
   * then event and claim times). Only if the run mentions no time at all is the wall clock used,
   * so that "expired" can never be switched off by an empty feed.
   */
  const clock = async (): Promise<string> => {
    const times: number[] = validJobs(unwrapJobs(await peek("jobs.json")).payload).map((j) => Date.parse(j.fetched_at));
    if (times.length === 0) {
      for (const [file, key] of [["events.json", "recorded_at"], ["experiment_events.json", "recorded_at"], ["claims.json", "updated_at"]] as const) {
        const raw = await peek(file);
        const list = isObj(raw) && Array.isArray(raw.claims) ? raw.claims : raw;
        if (Array.isArray(list)) for (const x of list) if (isObj(x) && typeof x[key] === "string") times.push(Date.parse(x[key] as string));
      }
    }
    const latest = Math.max(...times.filter((t) => !Number.isNaN(t)));
    return new Date(Number.isFinite(latest) ? latest : Date.now()).toISOString().replace(".000Z", "Z");
  };
  const subject = async (): Promise<string> => {
    const claims = await peek("claims.json");
    const list = isObj(claims) && Array.isArray(claims.claims) ? claims.claims : claims;
    const first = Array.isArray(list) ? list.find(isObj) : undefined;
    return typeof first?.subject_id === "string" ? first.subject_id : "unknown-subject";
  };

  return {
    id: "artifacts",
    label,
    async load(name: PayloadName) {
      switch (name) {
        case "jobs.feed": {
          const raw = await need("jobs.json");
          if (raw === undefined) return ABSENT;
          const u = unwrapJobs(raw);
          dialects["jobs.json"] = u.dialect;
          return u.payload;
        }
        case "claims": {
          const raw = await need("claims.json");
          if (raw === undefined) return ABSENT;
          return isObj(raw) && Array.isArray(raw.claims) ? raw.claims : raw;
        }
        case "matches": {
          const raw = await need("matches.json");
          if (raw === undefined) return ABSENT;
          const t = matchesFromUpstream(raw, { subject: await subject(), full: await peek("matches_full.json") });
          notes.push(...t.notes);
          dialects["matches.json"] = t.dialect;
          if (t.mock) mock.add("matches.json");
          refusals.push(...t.refusals);
          return t.payload;
        }
        case "resume-variants": {
          const raw = await need("resume_variant.json");
          if (raw === undefined) return ABSENT;
          const t = resumeVariantsFromUpstream(raw, { clock: await clock(), subject: await subject() });
          dialects["resume_variant.json"] = t.dialect;
          if (t.mock) mock.add("resume_variant.json");
          notes.push(...t.notes);
          return t.payload;
        }
        case "application-events": {
          const [a, b] = await Promise.all([need("events.json"), need("experiment_events.json")]);
          if (a === undefined && b === undefined) return ABSENT;
          // A file that is present but is not a list is handed on as it is, so the adapter reports it.
          const bad = [a, b].find((x) => x !== undefined && !Array.isArray(x));
          if (bad !== undefined) return bad;
          const seen = new Set<string>();
          return [...(Array.isArray(a) ? a : []), ...(Array.isArray(b) ? b : [])].filter((e) => {
            const id = isObj(e) && typeof e.id === "string" ? e.id : null;
            if (id === null) return true;
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
          });
        }
        case "experiments": {
          const raw = await need("experiment.json");
          if (raw === undefined) return ABSENT;
          // Lane B wraps the frozen spec as `{spec, design}`; lane E's layout is the bare spec.
          const one = (x: unknown) => (isObj(x) && isObj(x.spec) ? x.spec : x);
          return Array.isArray(raw) ? raw.map(one) : [one(raw)];
        }
        case "assignments": {
          const raw = await need("assignments.json");
          if (raw === undefined) return ABSENT;
          const t = assignmentsFromUpstream(raw);
          dialects["assignments.json"] = t.dialect;
          return t.payload;
        }
        case "outcomes": {
          const raw = await need("outcomes.json");
          missing.delete("outcomes.json");
          return raw === undefined ? ABSENT : raw;
        }
        case "scenarios": {
          const raw = await need("scenarios.json");
          return raw === undefined ? ABSENT : raw;
        }
        case "atlas.pca": {
          const ready = await peek("atlas.pca.json");
          if (ready !== undefined) {
            dialects["atlas.pca.json"] = isObj(ready) && typeof ready.schema_version === "string" ? ready.schema_version : "unrecognised";
            return ready;
          }
          const [points, diagnostics] = await Promise.all([need("atlas_points.json"), need("atlas_diagnostics.json")]);
          if (points === undefined || diagnostics === undefined) return ABSENT;
          const t = atlasFromArtifacts(points, diagnostics, validJobs(unwrapJobs(await peek("jobs.json")).payload), await clock());
          dialects["atlas_points.json"] = t.dialect;
          if (t.mock) mock.add("atlas_diagnostics.json");
          return t.payload;
        }
        case "atlas.axes": {
          const raw = await peek("atlas.axes.json");
          return raw === undefined ? ABSENT : raw;
        }
        case "evidence": {
          const raw = await peek("evidence.json");
          return raw === undefined ? ABSENT : raw;
        }
        case "preferences": {
          const raw = await peek("preferences.json");
          return raw === undefined ? ABSENT : raw;
        }
        case "manifest":
          return {
            fixture_clock: await clock(),
            data_origin: "pipeline-artifacts",
            notice: "Data produced by an integration run of lanes A/B/C. Ages are measured against the latest capture time in the feed.",
            role_family_labels: {},
            generator: { name: "pipeline", version: "as provided" },
            experiment: {},
          };
      }
    },
    async extras(): Promise<BundleExtras> {
      const raw = await need("analysis.json").catch(() => undefined);
      for (const file of [...unreadable].sort()) notes.push(`${file} exists but could not be read; it is treated as a failure, not as "not provided".`);
      if (raw !== undefined) {
        for (const item of Array.isArray(raw) ? raw : [raw]) {
          const t = analysisFromUpstream(item);
          dialects["analysis.json"] = t.dialect;
          if (t.mock) mock.add("analysis.json");
          if (t.payload) analyses.push(t.payload);
          else notes.push(`analysis.json was not understood (${t.reason}); no result is shown for it.`);
        }
      }
      const provenance: BundleProvenance = { data_source: "artifacts", dialects: { ...dialects }, mock_sources: [...mock].sort(), missing: [...missing].sort() };
      const jobFacets: Record<string, JobFacet> = facetsFromJobsExt(await peek("jobs_ext.json"));
      // Records in experiment_events.json are the experiment run's units, not the person's own log.
      const expEvents = await peek("experiment_events.json").catch(() => undefined);
      const experimentUnitApplicationIds = Array.isArray(expEvents)
        ? [...new Set(expEvents.flatMap((e) => (isObj(e) && typeof e.application_id === "string" ? [e.application_id] : [])))]
        : [];
      return { analyses: [...analyses], matchRefusals: [...refusals], provenance, notes: [...notes], jobFacets, experimentUnitApplicationIds };
    },
  };
}

/** Browser reader: same-origin files next to the app. No other origin is ever contacted. */
export function createFetchReader(base = "./artifacts/"): ArtifactReader {
  return {
    async read(file) {
      const response = await fetch(`${base}${encodeURIComponent(file)}`, { cache: "no-store", credentials: "omit" });
      if (!response.ok) return undefined;
      const text = await response.text();
      // Static servers answer unknown paths with the app shell; that is "absent", not JSON.
      if (text.trimStart().startsWith("<")) return undefined;
      return JSON.parse(text) as unknown;
    },
  };
}

/**
 * Static guarantees about the shipped source: no HTML injection sinks, no network
 * or telemetry APIs, no dev-time stand-in engine in the bundle, and fixtures that
 * are reproducible and explicitly fictional.
 */
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { jacobiEigen, neighbourhoodDiagnostics, pca2 } from "../../scripts/standin/engine.ts";
import { sha256Hex } from "@/lib/sha256";

const WEB = join(__dirname, "..", "..");
const UI = join(WEB, "..", "..", "packages", "ui", "src");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "generated" && name !== "node_modules") walk(p, out);
    } else if (/\.(ts|tsx|css|html)$/.test(name)) out.push(p);
  }
  return out;
}
const shipped = [...walk(join(WEB, "src")), ...walk(UI), join(WEB, "index.html")];
const sources = shipped.map((p) => ({ path: relative(WEB, p), text: readFileSync(p, "utf8") }));

describe("shipped source hygiene", () => {
  it("has no HTML-injection or dynamic-code sinks", () => {
    const banned = [/dangerouslySetInnerHTML/, /\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\beval\s*\(/, /new\s+Function\s*\(/, /srcdoc/i, /DOMParser/];
    const hits = sources.flatMap((s) => banned.filter((b) => b.test(s.text)).map((b) => `${s.path}: ${b}`));
    // validate.ts mentions `new Function` only inside a comment explaining why Ajv is not used at runtime.
    expect(hits.filter((h) => !h.startsWith(join("src", "adapters", "validate.ts")))).toEqual([]);
  });

  it("has no network, telemetry or cross-window APIs", () => {
    const banned = [/\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /EventSource/, /sendBeacon/, /navigator\.serviceWorker/, /\bimportScripts\b/, /postMessage/, /document\.cookie/, /window\.open\s*\(/];
    const hits = sources.flatMap((s) => banned.filter((b) => b.test(s.text)).map((b) => `${s.path}: ${b}`));
    // The ONE permitted network call: the artifact reader, which loads same-origin files next to the app.
    const artifact = join("src", "adapters", "artifactSource.ts");
    expect(hits.filter((h) => !h.startsWith(`${artifact}: /\\bfetch`))).toEqual([]);
    const reader = sources.find((s) => s.path === artifact)!.text;
    expect(reader.match(/\bfetch\s*\(/g)).toHaveLength(1);
    expect(reader).toContain('fetch(`${base}${encodeURIComponent(file)}`, { cache: "no-store", credentials: "omit" })');
    expect(reader).toContain('createFetchReader(base = "./artifacts/")');
    // No caller overrides that base with another origin.
    for (const s of sources) for (const m of s.text.matchAll(/createFetchReader\(([^)]*)\)/g)) expect(m[1] === "" || m[1] === 'base = "./artifacts/"', `${s.path}: ${m[0]}`).toBe(true);
  });

  it("references no external origin except the reserved test domains used in copy", () => {
    const urls = sources.flatMap((s) => (s.text.match(/https?:\/\/[^\s"'`)<>]+/g) ?? []).map((u) => ({ path: s.path, u })));
    const external = urls.filter(({ u }) => !/^https?:\/\/([a-z0-9.-]+\.)?(example\.invalid|invalid)\b/.test(u) && !u.startsWith("http://www.w3.org/"));
    expect(external).toEqual([]);
  });

  it("never opens an external link without noopener/noreferrer", () => {
    for (const s of sources) {
      for (const m of s.text.matchAll(/target="_blank"[^>]*/g)) expect(m[0], s.path).toMatch(/rel="[^"]*noopener[^"]*noreferrer/);
    }
  });

  it("does not bundle the dev-time stand-in engine", () => {
    // Any static or dynamic import that reaches into scripts/ (the dev-time stand-in) is a failure.
    const importsScripts = /(?:from\s+|import\s*\(\s*|require\s*\(\s*)["'`][^"'`]*(?:scripts\/|standin|generate-fixtures)[^"'`]*["'`]/;
    expect(sources.filter((s) => importsScripts.test(s.text)).map((h) => h.path)).toEqual([]);
    // The check itself must be able to fail.
    expect(importsScripts.test('const x = await import("../../scripts/standin/engine.ts")')).toBe(true);
    expect(importsScripts.test('import { pca2 } from "../scripts/standin/engine"')).toBe(true);
  });

  it("contains no probability-of-hire wording outside explicit denials", () => {
    const risky = /(chance|likelihood|probability|odds) of (being hired|an offer|getting hired|success)/i;
    const hits = sources.flatMap((s) => s.text.split("\n").filter((line) => risky.test(line) && !/\bnot\b|\bnever\b|\bno\b|\bneither\b|isn't|is not/i.test(line)).map((line) => `${s.path}: ${line.trim().slice(0, 120)}`));
    expect(hits).toEqual([]);
  });
});

describe("fixtures are reproducible and fictional", () => {
  it("committed fixtures equal the generator output", () => {
    const out = execFileSync(process.execPath, ["scripts/generate-fixtures.ts", "--check"], { cwd: WEB, encoding: "utf8" });
    expect(out).toContain("fixtures:check OK");
  });

  it("manifest hashes match the files on disk", () => {
    const dir = join(WEB, "src", "fixtures", "generated");
    const manifest = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8")) as { sha256: Record<string, string>; data_origin: string };
    expect(manifest.data_origin).toBe("synthetic");
    expect(Object.keys(manifest.sha256)).toHaveLength(13);
    for (const [name, hash] of Object.entries(manifest.sha256)) {
      expect(sha256Hex(readFileSync(join(dir, name), "utf8").replace(/\r\n/g, "\n")), name).toBe(hash);
    }
  });

  it("fixtures contain no e-mail addresses, phone numbers or real-looking domains", () => {
    const dir = join(WEB, "src", "fixtures", "generated");
    for (const name of readdirSync(dir)) {
      const text = readFileSync(join(dir, name), "utf8");
      expect(text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) ?? [], name).toEqual([]);
      expect(text.match(/\+?\d[\d\s().-]{8,}\d/g)?.filter((m) => !/^\d{4}-\d{2}-\d{2}/.test(m) && !/^[0-9.\-e]+$/.test(m)) ?? [], name).toEqual([]);
      for (const url of text.match(/https?:\/\/[^\s"\\]+/g) ?? []) expect(new URL(url).hostname.endsWith(".invalid"), `${name}: ${url}`).toBe(true);
    }
  });
});

describe("stand-in projection maths (dev-time generator)", () => {
  it("jacobiEigen recovers known eigenvalues", () => {
    const { values, vectors } = jacobiEigen([[2, 1], [1, 2]]);
    expect(values[0]).toBeCloseTo(3, 10);
    expect(values[1]).toBeCloseTo(1, 10);
    expect(Math.abs(vectors[0]![0]!)).toBeCloseTo(Math.SQRT1_2, 10);
    const sym = [[4, 1, 0.5], [1, 3, 0.2], [0.5, 0.2, 1]];
    const e = jacobiEigen(sym);
    expect(e.values.reduce((s, x) => s + x, 0)).toBeCloseTo(8, 9); // trace is preserved
    for (let k = 0; k < 3; k++) {
      const v = e.vectors[k]!;
      const Av = sym.map((row) => row.reduce((s, x, j) => s + x * v[j]!, 0));
      for (let i = 0; i < 3; i++) expect(Av[i]).toBeCloseTo(e.values[k]! * v[i]!, 8);
    }
  });
  it("pca2 keeps all variance of data that is already two-dimensional", () => {
    const rows = [[0, 0, 0], [1, 2, 0], [2, 1, 0], [3, 5, 0], [4, 3, 0], [5, 8, 0], [6, 4, 0], [7, 9, 0]];
    const p = pca2(rows);
    expect(p.cumulative).toBeCloseTo(1, 3);
    expect(p.ratios[0]).toBeGreaterThanOrEqual(p.ratios[1]);
  });
  it("trustworthiness and continuity are 1 for a distance-preserving map and drop for a scrambled one", () => {
    const high = Array.from({ length: 30 }, (_, i) => [i, (i * 7) % 11, 0, 0]);
    const same = high.map((r) => [r[0]!, r[1]!]);
    const perfect = neighbourhoodDiagnostics(high, same, 5);
    expect(perfect.trustworthiness).toBe(1);
    expect(perfect.continuity).toBe(1);
    expect(perfect.overlap.every((o) => o === 1)).toBe(true);
    const scrambled = high.map((_, i) => [(i * 17) % 30, (i * 13) % 29]);
    const bad = neighbourhoodDiagnostics(high, scrambled, 5);
    expect(bad.trustworthiness).toBeLessThan(0.8);
    expect(bad.continuity).toBeLessThan(0.8);
  });
});

/**
 * Lane E `ui.e2e` stage (see apps/web/integration-manifest.json and Issue #5).
 *
 *   npm run e2e:integration -- --artifacts <dir> [--report <file>]
 *
 * Renders the artifacts that lanes A/B/C produced in an integration run, drives
 * the UI with the keyboard only, and writes <dir>/ui_report.json.
 *
 * Offline: builds from local sources, serves from 127.0.0.1, and the browser test
 * fails on any request to another origin. Nothing is written outside <dir>,
 * apps/web/dist and apps/web/test-results.
 */
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const artifactsArg = flag("--artifacts");
if (!artifactsArg) {
  console.error("usage: npm run e2e:integration -- --artifacts <dir>");
  process.exit(2);
}
const artifacts = resolve(process.cwd(), artifactsArg);
// Default report location is the artifacts folder (lane E layout). --report overrides it, so a
// pinned sample folder is never written to.
const reportPath = flag("--report") ? resolve(process.cwd(), flag("--report")) : join(artifacts, "ui_report.json");
const factsPath = join(WEB, "test-results", "integration-facts.json");
const resultsPath = join(WEB, "test-results", "integration-results.json");
const served = join(WEB, "dist", "artifacts");

function writeReport(report) {
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  console.log(`ui_report.json -> ${reportPath}`);
}
function fail(message, extra = {}) {
  writeReport({ tool: "playwright", keyboard_only: false, data_source: "none", mock_sources: [], tests: [{ name: "ui.e2e setup", status: "fail", detail: message }], ...extra });
  console.error(message);
  process.exit(1);
}

if (!existsSync(artifacts)) fail(`artifacts directory does not exist: ${artifacts}`);
const files = readdirSync(artifacts).filter((f) => f.endsWith(".json") && f !== "ui_report.json");
if (!files.includes("jobs.json")) fail("artifacts directory has no jobs.json; nothing to render");

const node = process.execPath;
const run = (label, argv, env = {}) => {
  console.log(`> ${label}`);
  const r = spawnSync(node, argv, { cwd: WEB, stdio: "inherit", env: { ...process.env, ...env } });
  return r.status ?? 1;
};

// 1. Build the production bundle from local sources (no network needed).
if (run("vite build", [join(WEB, "node_modules", "vite", "bin", "vite.js"), "build"]) !== 0) fail("vite build failed");

// 2. Serve the artifacts next to the app (same origin). Only JSON files are copied.
rmSync(served, { recursive: true, force: true });
mkdirSync(served, { recursive: true });
for (const f of files) cpSync(join(artifacts, f), join(served, f));
rmSync(factsPath, { force: true });
rmSync(resultsPath, { force: true });

// 3. Keyboard-only browser run against the production build.
const status = run("playwright (integration)", [join(WEB, "node_modules", "@playwright", "test", "cli.js"), "test", "-c", "playwright.integration.config.ts"], {
  CT_ARTIFACTS: artifacts,
  CT_FACTS: factsPath,
  PLAYWRIGHT_JSON_OUTPUT_NAME: resultsPath,
});

// 4. Report. Every field is measured in the run, not asserted by this script.
const readJson = (p) => {
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return null;
  }
};
const facts = readJson(factsPath);
const results = readJson(resultsPath);
const tests = [];
const walk = (suite) => {
  for (const spec of suite.specs ?? []) {
    const outcomes = spec.tests.flatMap((t) => t.results.map((r) => r.status));
    const ok = spec.ok === true && outcomes.length > 0 && outcomes.every((s) => s === "passed");
    tests.push({ name: spec.title, status: ok ? "pass" : outcomes.every((s) => s === "skipped") ? "skipped" : "fail" });
  }
  for (const child of suite.suites ?? []) walk(child);
};
for (const s of results?.suites ?? []) walk(s);
if (tests.length === 0) tests.push({ name: "ui.e2e", status: "fail", detail: "no test results were produced" });

const report = {
  tool: "playwright",
  // True only if the browser observed zero pointer, mouse or touch events during the journey.
  keyboard_only: facts !== null && facts.pointer_events === 0,
  // Read from the running app, not assumed: "artifacts" when it rendered the pipeline output.
  data_source: facts?.data_source ?? "unknown",
  mock_sources: facts?.mock_sources ?? [],
  tests,
  detail: {
    session_id: "CAREER-TWIN-D",
    input_method: "keyboard events only; the runner sets focus programmatically, then sends key presses",
    pointer_events_observed: facts?.pointer_events ?? null,
    external_requests_observed: facts?.external_requests ?? null,
    missing_artifacts: facts?.missing ?? [],
    // Defects in the pipeline's own data that the UI caught and refused to act on.
    upstream_findings: facts?.upstream_findings ?? [],
    dialects: facts?.dialects ?? {},
    artifacts_rendered: files.sort(),
    steps: facts?.steps ?? [],
    exit_status: status,
  },
};
writeReport(report);
rmSync(served, { recursive: true, force: true });
process.exit(status === 0 && tests.every((t) => t.status === "pass") ? 0 : 1);

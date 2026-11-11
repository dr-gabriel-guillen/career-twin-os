# CareerTwin OS — product UI (lane CAREER-TWIN-D)

Offline, synthetic, interactive product shell for CareerTwin OS. Instructions for this lane: [Issue #5](https://github.com/dr-gabriel-guillen/career-twin-os/issues/5). Draft PR: [#10](https://github.com/dr-gabriel-guillen/career-twin-os/pull/10).

**What it is:** a static TypeScript/React app with ten working screens that walks a fictional candidate from a fictional job feed through matching, résumé variants, application tracking, an experiment, the career atlas and transition scenarios.

**What it is not:** it is not connected to any live job source, it never submits an application or contacts anyone, it calls no external or paid service, and every employer, posting, person, event and outcome in it is fictional.

## Run it

Requires Node 22.18 or newer (the fixture generator uses Node's built-in TypeScript support). Tested on Node 24.16, Windows 11.

```bash
cd apps/web
npm ci
npm run dev        # http://localhost:5173
```

Production build (this is what the browser tests exercise; it carries the Content-Security-Policy):

```bash
npm run build
npm run preview    # http://localhost:4173
```

The app keeps working with the network cut once it has loaded. It still has to be served by a local static server for the first load; opening `dist/index.html` straight from disk does not work because browsers block module scripts on `file://`.

## Verify it

```bash
npm run verify
```

runs, in order: `fixtures:check` (committed fixtures equal the generator output), `typecheck`, `test` (Vitest unit tests), `build`, `test:e2e` (Playwright against the production build).

Browser for E2E: either install Playwright's Chromium once (`npx playwright install chromium`) or use an installed Chrome with `PW_CHANNEL=chrome npm run test:e2e`.

Golden vectors for parity with the frozen reference engine on `main` are regenerated with `python scripts/reference_vectors.py` (reads `tools/reference_engine.py`, read-only).

## Screens

| Route | Screen | What works |
| --- | --- | --- |
| `#/` | Overview | Counts, attention list, guided pipeline, what the build does not do |
| `#/jobs` | Discover Jobs | Search, five filters, six sort orders, cards and table, provenance, freshness, unknown salary, likely duplicates, shortlist, track, guarded posting link |
| `#/matching` | Matching | Ranked list, near-term fit vs strategic value, component breakdown, hard constraints, evidence strength per skill, gaps, unknown inputs, user ranking blend, stale-score warning |
| `#/evidence` | Profile Evidence | Truth states, evidence references, add / edit / remove claims, audit log, rule that "supported" needs evidence |
| `#/resume` | Resume Studio | Variants built from claims, manual edits, word diff, truthfulness blockers, approval bound to exact content, gated export |
| `#/applications` | Applications | Board, table, timeline, append-only events, stage gates, manual-submission confirmation, reminders, missing feedback, CSV export |
| `#/experiments` | Experiments | Design form, preregistration lock, deterministic stratified assignment, exposure log, results with intervals, censoring and missingness, sample-size guidance |
| `#/atlas` | Career Atlas | Interactive 2-D scatter (pointer and keyboard), two projections, filters, distortion banner, movement paths, point detail, table alternative |
| `#/simulator` | Career Simulator | Side-by-side scenarios, editable effort and cost, pace, user weights, assumptions and risks |
| `#/settings` | Settings | Theme, motion, data export, erase, adapter report, disabled connectors, safety gates, QA state simulation |

## Layout

```
apps/web/
  src/contracts/     TypeScript mirrors of the six frozen v0.1 contracts
  src/adapters/      versioned adapters + runtime validators (frozen and proposed shapes)
  src/fixtures/      generated synthetic payloads (do not edit by hand)
  src/lib/           pure logic: hashing, assignment mirror, statistics, diff, link/CSV safety, résumé guard
  src/state/         workspace reducer, persistence, app context
  src/pages/         the ten screens
  scripts/           dev-time fixture generator and stand-in engine (never bundled)
  tests/unit/        Vitest
  tests/e2e/         Playwright + axe
  docs/              decisions, adapters, QA evidence, handoff
packages/ui/         small accessible component kit and design tokens
```

An independent adversarial review found 20 defects in the first complete version; all are listed with their fixes in [docs/QA-EVIDENCE.md](docs/QA-EVIDENCE.md).

More detail: [docs/DECISIONS.md](docs/DECISIONS.md), [docs/ADAPTERS.md](docs/ADAPTERS.md), [docs/QA-EVIDENCE.md](docs/QA-EVIDENCE.md), [docs/HANDOFF.md](docs/HANDOFF.md).

## Safety properties (each one is tested)

- No network: the production CSP is `default-src 'self'` with `connect-src 'self'` and no inline script; every browser test fails if any request leaves the local origin; the source contains no `fetch`, XHR, WebSocket, beacon or service-worker call.
- No HTML from data: there is no `dangerouslySetInnerHTML`, `innerHTML` or similar sink. Hostile posting text (markup, script, prompt-injection, spreadsheet formulas) is in the fixtures on purpose and renders as plain text.
- Links: a posting link can only be opened from a warning dialog that shows the full destination; `javascript:`, `data:`, credentialed, numeric and local-network links are blocked.
- No auto-apply: "submitted" is a fact the user records after applying elsewhere; the reducer refuses it without the approval record of the approved step, refuses it from any non-user actor, and refuses any attempt to set that approval through another kind of event.
- Truth states: "supported" and "contradicted" come only from evidence and cannot be chosen by hand; every résumé line is compared with the claims it cites, whatever origin the data declares.
- No fabricated certainty: unknown compensation stays unknown, scores are labelled relative heuristics, the atlas always shows its distortion metrics, experiment results never name a winner on thin evidence.
- Data control: everything lives in `localStorage` under two keys; it can be exported as JSON and erased from Settings.

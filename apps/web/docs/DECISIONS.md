# Lane D decisions

Each entry: the decision, why, and what it costs.

## D-01 — Vite + React + TypeScript instead of Next.js

Issue #5 asks for "Next.js/TypeScript or justified equivalent". This app is a fully client-side, offline bundle with no server data, no accounts and no routes that need server rendering.

- **Why Vite:** the output is plain static files with no server runtime, so there is nothing to deploy or secure beyond a static host; no framework telemetry to switch off; a strict CSP with no inline script is straightforward; unit tests share the same toolchain (Vitest).
- **Cost:** no file-system routing or server components. If lane E or the owner later wants Next.js, the pages are ordinary React components and the adapters, state and libraries are framework-free, so the move is mechanical.
- **Routing:** hash routes (`#/jobs`) so the build works from any static path without rewrite rules.

## D-02 — Three runtime dependencies

`react`, `react-dom`, `react-router-dom`. Charts are hand-written SVG and validation is hand-written, which keeps the supply chain small and the CSP strict. Everything is pinned to exact versions in `package-lock.json`.

## D-03 — Hand-written validators at runtime, Ajv only in tests

Ajv compiles validators with `new Function`, which `script-src 'self'` forbids. The browser therefore uses small hand-written validators. `tests/unit/contracts.test.ts` mutates valid records field by field (8,741 mutations across the six contracts) and asserts the hand validators and Ajv return the same verdict on every one. Known, intentional difference: the hand `date-time` check is stricter than `ajv-formats` (no space separator, no colon-less offset).

## D-04 — The UI is a presentation layer; the stand-in engine is dev-time only

`docs/ARCHITECTURE.md` says D "does not reimplement business logic". Until lanes A/B/C payloads are wired in, a deterministic generator under `scripts/` produces JSON payloads that follow the frozen contracts (and clearly labelled `-proposed` shapes where no contract exists). The generator is never bundled; a test asserts that nothing under `src/` imports it.

What the browser does compute, and why that is presentation rather than business logic:

| In the browser | Why it is there | Who owns the authoritative version |
| --- | --- | --- |
| Re-ordering matches by a user slider | Ordering only; neither score changes | A |
| Stage derived from the event log; which next events to offer | Needed to render a board; reducer also enforces it so the UI cannot be bypassed | B |
| Résumé truthfulness lint and approval digest | The export gate must hold even with no backend | B |
| Variant assignment hash | Byte-for-byte mirror of `assign_variant` on `main`, proven with 60 golden vectors from the real Python | B |
| Wilson / Newcombe intervals, sample-size guidance, evidence label | Results must show uncertainty; verified against published values | B |
| Scenario totals, pace conversion, preference ordering | Sums over upstream facts and the user's own numbers | C |

When an upstream lane publishes its own result for any row, the UI should render that and delete the local version. The match scores themselves are never recomputed: if the evidence vault changes, the Matching screen says the scores may be stale.

## D-05 — Fixed fixture clock

Ages ("captured 3 days before the snapshot", "no reply for 28 days") are measured against the fixture clock `2026-11-10T12:00:00Z`, not the wall clock, so the demo and the tests are reproducible. The Overview screen says so. Events the user records carry real timestamps.

## D-06 — Application events are append-only; approval is content-bound

The reducer has no action that edits or deletes an event. A correction is a new `note`. `approved` requires a résumé variant whose approval digest equals a SHA-256 of its current content, and `submitted` must cite the same approval and come from the user. An approved variant is a locked snapshot: changing it means creating a new version.

## D-07 — No drag and drop on the board

Stage changes happen by recording an event, never by dragging a card. That keeps the board keyboard-equivalent and keeps every stage change tied to a dated, attributable event.

## D-08 — No 3-D atlas

The PCA stand-in keeps 36% of the variance in two axes. A third axis would add interaction cost and occlusion without making positions more trustworthy, so it is not offered. The screen says this. An "interpretable axes" projection is offered instead.

## D-09 — Pages are bundled eagerly

Route-level code splitting made screens fail when the network was cut after first load. All ten screens now ship in the main bundle (about 138 kB gzipped), and the favicon is inlined, so nothing is requested after startup.

## D-10 — QA simulation never touches stored data

Settings can simulate slow, failing and empty data. While a simulation is on, the stored workspace is neither read nor written, so testing an empty state cannot overwrite real work.

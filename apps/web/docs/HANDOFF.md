# Handoff — CAREER-TWIN-D, batch CT-2026-11-10-B01

| | |
| --- | --- |
| SESSION_ID | CAREER-TWIN-D |
| BATCH_ID | CT-2026-11-10-B01 |
| Branch | `agent/ct-d-product-ux` |
| BASE_SHA | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` (`main`) |
| SOURCE_HEAD | see the DELIVERED comment on Issue #5 (a file cannot contain its own commit SHA); code state described here: `f373cbadefdae6b7c6bba9f7b5f25d581463ec5b` |
| Issue / PR | [#5](https://github.com/dr-gabriel-guillen/career-twin-os/issues/5) / draft [#10](https://github.com/dr-gabriel-guillen/career-twin-os/pull/10) |
| Changed paths | `apps/web/**`, `packages/ui/**` only. No frozen contract, shared fixture or other lane's file was touched. |

## What was delivered

A runnable, offline, interactive product UI: ten screens, versioned adapters, a pipeline-artifact data source, and lane E's `ui.e2e` integration stage.

| Workstream (Issue #5) | State | Where |
| --- | --- | --- |
| 1. App scaffold, pinned lockfile, fixture mode, setup docs | done | `package.json`, `package-lock.json`, `README.md`, `docs/DECISIONS.md` (D-01: Vite + React + TS instead of Next.js, with reasons) |
| 2. Ten navigation targets | done | `src/App.tsx`, `src/pages/*` |
| 3. Job discovery | done | `src/pages/JobsPage.tsx` |
| 4. Matching detail | done | `src/pages/MatchingPage.tsx` |
| 5. Evidence vault and résumé studio | done; real LinkedIn import deliberately absent and labelled unavailable | `EvidencePage.tsx`, `ResumePage.tsx`, `src/lib/resumeLint.ts` |
| 6. Application CRM | done; board has no drag-and-drop by design (D-07) | `ApplicationsPage.tsx`, `src/lib/applications.ts`, `src/state/workspace.ts` |
| 7. Experiment builder | done | `ExperimentsPage.tsx`, `src/lib/stats.ts`, `src/lib/experimentView.ts` |
| 8. Career atlas | done in 2-D with two projections; 3-D deliberately not offered (D-08) | `AtlasPage.tsx` |
| 9. Simulator | done | `SimulatorPage.tsx`, `src/lib/simulator.ts` |
| 10. Accessibility and security | done within the limits listed below | `tests/e2e/*`, `docs/QA-EVIDENCE.md` |
| 11. Integration through versioned adapters | done for lanes A, B, C and lane E's artifact layout; gaps listed below | `src/adapters/*`, `docs/ADAPTERS.md`, `integration-manifest.json` |
| 12. Browser E2E, QA, repair (two cycles: own testing, then an independent adversarial review), screenshots, draft PR, handoff | done | `tests/`, `docs/QA-EVIDENCE.md`, `docs/screenshots/`, this file |

## Reproduce

```bash
cd apps/web
npm ci
npm run dev                              # interactive demo on http://localhost:5173
PW_CHANNEL=chrome npm run verify         # full verification (or install Playwright's Chromium first)
npm run e2e:integration -- --artifacts <dir>   # lane E ui.e2e stage on a pipeline run
```

Requires Node 22.18 or newer. Pipeline mode in a browser: serve the build with the artifacts copied to `dist/artifacts/` and open `/?source=artifacts`.

## Evidence

| Check | Result |
| --- | --- |
| `fixtures:check` | OK, 14 files equal the generator output |
| `tsc --noEmit` | no errors |
| Unit (Vitest) | 8 files, 159 tests passed, 0 failed, 0 skipped |
| Browser (Playwright + axe, production build) | 48 passed, 0 failed, 1 skipped (the opt-in screenshot generator) |
| Integration stage (`ui.e2e`) on a real A→B→C pipeline run | pass; `focus_method: "tab-traversal"` (374 Tab presses), `keyboard_only: true` (0 pointer events observed), `data_source: "artifacts"`, `mock_sources: []`; the atlas shows the candidate, the scenario point and the path on one projection |
| Clean export of the pushed tree (`git archive`) + `npm ci` + `verify` | passed at `f373cbadefdae6b7c6bba9f7b5f25d581463ec5b` |
| Runtime validators vs Ajv on the frozen schemas | 8,741 mutations, 0 disagreements |
| Parity with `tools/reference_engine.py` | 60 golden assignment cases, 8 hash cases, 4 scoring cases |
| axe (WCAG 2.0/2.1 A + AA, best-practice) | 0 violations on all ten screens, light and dark, plus dialogs, tabs and tables |
| Independent adversarial review (same model family, separate instance) | 20 findings (0 critical, 6 high, 8 medium, 6 low); 19 fixed, 1 partly fixed and documented; 35 regression tests |

Pinned inputs for the integration evidence: lane A `0b331ea6ab365f355e3635b9b4467191a0cf9fae`, lane B `755ccae1b4208ac96cbc39d9c4071fa4188c2224`, lane C `f76e24c869bad2819810ac06e8b31f067aacf338`, lane E stub `a2e50e339bec831c9bb3f814d38b52bfb535771e`.

Full detail, including every defect found and what was done about it: `docs/QA-EVIDENCE.md`.

## Interfaces

Lane D changed no shared contract and opened no RFC. It defines no contract of its own; `src/adapters/proposed.ts` holds view shapes that upstream dialects are translated into.

| With | State | Thread |
| --- | --- | --- |
| A | ACKNOWLEDGED RFC #12 with six findings; all six answered by A and consumed. D reads `jobs.json`, `jobs_ext.json`, `claims.json`, `matches.json`, `matches_full.json`. | [#12](https://github.com/dr-gabriel-guillen/career-twin-os/issues/12) |
| B | ACKNOWLEDGED `b.v1` shapes and rules. D reads `resume_variant.json`, `events.json`, `experiment.json`, `assignments.json`, `experiment_events.json`, `analysis.json`. Three NEEDS-INTERFACE questions open (per-unit outcome state, exposure, single-arm interval). | [#3](https://github.com/dr-gabriel-guillen/career-twin-os/issues/3) |
| C | ACKNOWLEDGED `atlas-projection/0.1` and `scenario-outputs/0.1`; all six of C's requests implemented. D's finding (two projection ids in one pipeline run) was fixed by C at `f76e24c` and confirmed by D's re-run. Nothing open. | [#5](https://github.com/dr-gabriel-guillen/career-twin-os/issues/5), [#4](https://github.com/dr-gabriel-guillen/career-twin-os/issues/4) |
| E | ACKNOWLEDGED manifest and `ui_report.json`; stage delivered and now by Tab traversal, as E asked. Both of D's findings were accepted and fixed by E. Open: E's runner could not start a browser under its environment scrub; the manifest's `setup` now installs Playwright's Chromium (E's option a), which D has not verified inside E's runner. | [#5](https://github.com/dr-gabriel-guillen/career-twin-os/issues/5), [#6](https://github.com/dr-gabriel-guillen/career-twin-os/issues/6) |

## Blockers

None for this batch.

## Known gaps and regressions

No regression against `main`: `main` had no UI. Gaps (all also in `docs/QA-EVIDENCE.md`):

1. Fixture mode shows a lane-D stand-in for matching, atlas and scenarios. It is labelled on screen. Pipeline mode shows real lane output.
2. Pipeline mode: evidence references do not resolve; experiment exposure is unknown; some of lane A's `ext` facets are not rendered.
3. Tested on Windows + Chrome only. No assistive-technology testing. Not tested in Firefox or Safari.
4. Bidi and zero-width characters in upstream text are displayed, not stripped.
5. The independent review in this batch was done by another instance of the same model family. It found 20 defects, which says the first version was not as tight as its own tests suggested; a review by a person or a different system has not happened.
6. The résumé guard compares wording with the linked claims; it is not a fact checker.
7. In fixture mode all ages are measured against the fixed snapshot date.

## Owner decisions needed

- Repository visibility is PUBLIC. Nothing personal was added. Any real profile, résumé, application history or LinkedIn export must wait for a private repository and explicit approval.
- Single-user by design, or a subject id on events, experiments and atlas points (hub finding E-F004).
- Whether lane D's stack choice (Vite instead of Next.js, D-01) stands.
- Merge of draft PR #10. Lane D did not merge, deploy, or enable auto-merge.

## Precise next work

1. Have lane E re-run `ui.e2e` at this head inside its scrubbed runner with `--allow-setup`, and settle the browser policy (owner decision 6 in E's handoff).
2. When lane B answers the three questions on #3: list per-unit outcome state and exposure beside the assignment table.
3. Render lane A's `ext.compensation.state`, `ext.location.jurisdictions` and `ext.timestamps` in Discover Jobs; replace the fixture stand-in with lane A/B/C example files and delete `scripts/standin/`.
4. Run the suite on Linux (lane E's CI) and in Firefox and WebKit; do one pass with a screen reader.
5. Interview Preparation (product spec module 9) is not in Issue #5's twelve workstreams and was not built.

## No-data-exposure statement

Lane D committed only synthetic data: fictional employers (every one contains the word "Fictional"), fictional postings on reserved `.invalid` or `.example` domains, a fictional candidate, and copies of other lanes' synthetic sample output. No real person, employer, résumé, contact, application history, credential, token or LinkedIn data is in the branch. The app makes no request to any origin but its own, has no telemetry, submits nothing, and messages no one. Commits use a GitHub no-reply address.

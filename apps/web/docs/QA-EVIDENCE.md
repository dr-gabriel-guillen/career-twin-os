# QA evidence — lane D, batch CT-2026-11-10-B01

Everything here was run on one machine: Windows 11, Node 24.16.0, npm 11.13, Python 3.14.6 (only for the golden vectors and for running other lanes' pipeline stages), installed Google Chrome driven by Playwright 1.65 (`PW_CHANNEL=chrome`). It has **not** been run on Linux or macOS, in Firefox or Safari, or with a screen reader. Review by another instance of the same model is not independent external validation.

## Reproduce

```bash
cd apps/web
npm ci
PW_CHANNEL=chrome npm run verify      # or: npx playwright install chromium && npm run verify
```

`verify` = `fixtures:check` → `typecheck` → `test` → `build` → `test:e2e` → `test:integration`.

## What each suite proves

### Unit tests (`tests/unit`, Vitest)

| File | What it establishes |
| --- | --- |
| `contracts.test.ts` | Every shipped fixture record satisfies the frozen JSON Schemas (Ajv). The hand-written runtime validators and Ajv give the same verdict on 8,741 single-field mutations across the six contracts (0 disagreements). TypeScript enum lists equal the schema enums. The three deliberately malformed feed records are rejected. |
| `reference-parity.test.ts` | `sha256` equals Python `hashlib` and Node `crypto`. `assignVariant` equals `assign_variant` in `tools/reference_engine.py` on 60 golden cases produced by running that Python. Stand-in match terms equal `score_job` for the four postings shared with `main`. |
| `adapters.test.ts` | Bundle loading over the fixtures; rejection of non-array payloads, unloadable payloads, duplicate ids, a match that calls its score a probability, unknown components carrying a value, censored outcomes carrying a value, orphan assignments/outcomes, inconsistent projections, non-hypothetical scenarios, malformed approvals. |
| `lib.test.ts` | Wilson and Newcombe intervals and sample size against published values; the evidence label cannot reach its top level on small, unlocked or unplanned data; link classifier (schemes, credentials, private and numeric hosts, single-label hosts); CSV formula neutralisation; unknown compensation; word diff; résumé truthfulness guard; approval digest. |
| `workspace.test.ts` | Reducer invariants: events are append-only, stages cannot be skipped, approval must match the exact résumé content, submissions must cite that approval and come from the user, locked designs cannot change, no unit is assigned twice, exposure is logged once. Persistence rejects corrupt, tampered and wrong-version data. Simulator arithmetic. |
| `upstream.test.ts` | Adapters against **real** output of lanes A, C and E and against one **real integrated A→B→C pipeline run**, all pinned under `tests/upstream-samples/` (see `PROVENANCE.md`). |
| `review-cycle.test.ts` | One or more regression tests for each of the 20 findings of the independent review (see below). |
| `hygiene.test.ts` | No HTML or dynamic-code sink in shipped source; exactly one network call (same-origin artifact reader); no external origins; `noopener noreferrer` on the one external link; the dev-time stand-in is not bundled; no probability-of-hire wording outside denials; fixtures equal generator output and contain no e-mail addresses, phone numbers or real-looking domains; stand-in PCA and neighbourhood maths. |

### Browser tests (`tests/e2e`, Playwright + axe, production build)

Every browser test fails automatically on any console error, uncaught exception, CSP violation, or request to an origin other than the local preview server.

| File | What it establishes |
| --- | --- |
| `journey.spec.ts` | The whole synthetic slice in one session: search / filter / duplicates / shortlist → match explanation and ranking blend → tailor a résumé, inflate a figure and a credential (blocked), repair, approve, export → track an application, approval cannot be skipped, manual-submission confirmation, reminder, missing feedback → design, lock, assign and simulate an experiment, no winner declared → atlas → simulator edits → everything survives a reload. |
| `screens.spec.ts` | All ten screens render with one `h1`, correct title and current-nav marker, and have 0 axe violations (WCAG 2.0/2.1 A and AA plus best-practice rules) in light and dark; so do dialogs, tabs and table views. Unknown route. Focus moves to the heading on navigation. Fixture experiment shows censoring and missingness and names no winner. Atlas filters and table alternative. |
| `security.spec.ts` | CSP is present, strict and effective (an injected inline script does not run). Hostile posting text is inert on every screen. A `javascript:` link is blocked. A normal link opens only from the warning dialog with safe attributes. CSV export neutralises formulas. Tampered `localStorage` is rejected. Typed markup is never interpreted. |
| `resilience.spec.ts` | No horizontal page scroll at 360, 768 and 1440 px; collapsed navigation; 24 px minimum targets. Keyboard-only paths: skip link, dialog focus containment and return, tabs, atlas, slider. Reduced motion by system preference and by setting. Everything works with the network cut after load. Loading, error and empty states. Export and erase. Adapter report and disabled connectors. Evidence rules and stale-score warning. |
| `upstream.spec.ts` | The same screens rendering real lane A match results and job envelopes, real lane C projections and scenarios, a pipeline analysis verdict, and lane E's 24 poisoned postings (inert), and a posting of unverified provenance (never labelled synthetic). |
| `screenshots.spec.ts` | Skipped unless `CT_SCREENSHOTS=1`; regenerates `docs/screenshots/`. |

### Integration stage (`tests/integration`, `npm run test:integration`)

Lane E's `ui.e2e`: the app renders the artifacts of a pipeline run (`?source=artifacts`) and is driven with the keyboard only, by Tab traversal: every control is reached by pressing Tab from wherever focus is, and the test never calls focus() on a control. The report field `keyboard_only` is measured (zero pointer, mouse or touch events observed in the page).

## Results

| Run | Unit | Browser | Integration |
| --- | --- | --- | --- |
| First test cycle, `bdc5811` | 94 passed | 43 passed | — |
| Upstream adapters, `e88c060` | 116 passed | 46 passed | pass on lane E's stub (4 mock sources reported) |
| Real pipeline, `7686e8c` | 123 passed | 46 passed | pass on A `3994155` → B `84757ed` → C `85c0536`, no mock sources |
| Injection corpus, `9c24d10` | 123 passed | 47 passed, 1 skipped (screenshots) | pass |
| After the independent review and repairs, `b5948c4` | 158 passed | 48 passed, 1 skipped (screenshots) | pass on the pinned real pipeline run |
| Tab traversal, re-pinned pipeline (final) | 159 passed | 48 passed, 1 skipped (screenshots) | pass by Tab traversal on A `0b331ea` → B `755ccae` → C `f76e24c`; candidate, scenario and path on the atlas |

No test is skipped except the opt-in screenshot generator. No test is marked expected-to-fail.

## Defects found by testing and what was done

| # | Found by | Defect | Fix |
| --- | --- | --- | --- |
| 1 | unit test | The link classifier accepted a triple-slash URL, which URL parsers turn into a single-label host. | Only dotted public host names pass. Regression test kept. |
| 2 | browser test | Focus moved to the `h1` on first load, so the first Tab skipped the skip link. | Focus moves only on later navigations. |
| 3 | browser test | With the network cut, lazily loaded screens and a favicon refetch failed. | Screens are bundled eagerly; favicon inlined. |
| 4 | axe | An atlas table button's accessible name did not contain its visible text. | Name now starts with the visible text. |
| 5 | unit test on real lane C data | I assumed one applied action per planned action; lane C expands one planned action into several features. | The join is by `skill`. |
| 6 | integration run on lane E's stub | The stub résumé variant cites a claim marked private; the export gate blocks it. | Not a lane D defect. Reported to lane E; the stage records it under `upstream_findings`. |
| 7 | integration run on the real pipeline | Lane B's analysis report has `arms` as an object and counts under `flow.by_arm`; my first translator expected a list. | Translator and test updated to the real shape. |
| 8 | inspection of the real pipeline run | Lane C's two stages used different projection ids, so the scenario could not be drawn on that run's atlas. | Not a lane D defect. Reported to lanes C and E. Lane C fixed it at `f76e24c`; lane E now checks it. A fresh run confirms one projection id, and the pipeline atlas shows the candidate, the scenario point and the path (asserted in the integration stage). |
| 10 | lane E's review of the stage | The stage set focus programmatically, so it did not prove that every control is reachable in the tab order. | The stage now reaches every control by pressing Tab from wherever focus is (374 presses on the pinned run) and never calls focus() on a control. The report states `focus_method: "tab-traversal"`. |
| 11 | lane E's runner | The browser could not start inside lane E's credential-scrubbed environment. | The manifest's `setup` now also installs Playwright's Chromium, as lane E proposed. Not verified inside lane E's runner by lane D. |
| 9 | own tests | Four of my test assertions were wrong (an impossible input, a focus assertion that ignored the browser's own UI, a file I expected to be reported missing, a console error caused by my own simulated 404). | Tests corrected, none deleted. |

## Independent adversarial review (second cycle)

A separate reviewer instance, given the code and the app's stated guarantees but none of my conclusions, was asked to break them. It had read-only access and wrote its own test scripts. It is the same model family, so this is **not** independent external validation. It reported 20 findings, each with a reproduction: 0 critical, 6 high, 8 medium, 6 low. It found no script execution, no cross-origin request and no HTML sink. All 20 were reproduced and are listed here with what was done. Regression tests: `tests/unit/review-cycle.test.ts` (35 tests) plus the browser tests named below.

| ID | Sev | Finding (as reported) | Disposition |
| --- | --- | --- | --- |
| F-01 | high | A `note` event could carry a forged approval record and move the application's approval, after which `submitted` was accepted; an approved variant could be rewritten in place. Reducer-level, not reachable by clicking. | **Fixed.** Only an `approved` event sets the approval; any other event citing an approval record is refused; a note cannot name a variant; `submitted` must cite the approved step's token and variant; an approved variant is immutable in the reducer. |
| F-02 | high | Stage was derived after sorting by `occurred_at`, so a backdated or same-day picked date was accepted and then ignored (a recorded rejection still showed "No reply recorded"). Reachable in the UI. Timestamps were also compared as strings. | **Fixed.** Stage follows the recorded log. An event dated before the previous step, or recorded earlier than the last record, is refused. Times are compared as instants. |
| F-03 | high | The top evidence label was reachable on thin data: the planned sample was parsed from the first number in the stopping rule ("1,000" read as 1), and a plan of 1–2 units could be locked. | **Fixed.** Only a number that counts units is read; an ambiguous rule yields no plan. A plan below 10 per variant cannot be locked. The label also needs a closed experiment, no open outcome windows and no over-enrolment. |
| F-04 | high | Lines marked `from-claims` were never compared with their claims, so upstream or stored data could bypass the truthfulness guard; the export printed the unapproved variant label. | **Fixed.** Every line is compared with its claims regardless of declared origin. The label is no longer exported. Headings are checked. Duplicate line ids are rejected. |
| F-05 | high | "Supported by evidence" could be self-declared in two clicks (reword, then set supported); a contradicted claim could be relabelled. Reachable in the UI. | **Fixed.** "Supported" and "contradicted" can no longer be chosen by hand anywhere. Rewording drops the evidence references. A contradicted claim cannot be relabelled or reworded. |
| F-06 | high | On lane E's unmodified stub sample the Matching screen showed "weight 0%", "Nothing known is holding this score back" and a compute time taken from the job feed. | **Fixed.** Weight, compute time and engine version are shown as not published / not stated; no lift-or-limit sentence is written without weights. |
| F-07 | medium | Translators clamped out-of-range scores, components and atlas metrics into range, defaulted a missing count to 0 and a missing interval level to 95%, and matched verdicts case-insensitively. | **Fixed.** Values pass through untouched and the strict validators reject the record. Impossible counts, a near-miss verdict, or a claimed difference whose interval includes zero make an analysis unusable. An unstated interval level is shown as not stated. |
| F-08 | medium | The atlas banner printed "1-dimensional" when the dimensionality was not given and "5 nearest neighbours" when the producer used 10. | **Fixed.** Unknown dimensionality is left out; the neighbourhood size is the producer's. |
| F-09 | medium | `matches_full.json` from another run was mixed into the matches; a facet survived for a posting that failed validation; a file in the wrong shape or unparseable was reported as "not provided"; an empty feed set the clock to 1970 and disabled claim expiry. | **Fixed.** A sibling file that names another subject or shortlist is ignored and reported; facets are kept only for validated postings; wrong-shape and unreadable files are fatal reports; the clock falls back to event and claim times, then the wall clock. |
| F-10 | medium | `parseWorkspace` checked each record's shape only, so stored blobs that broke every cross-record invariant were accepted. | **Fixed.** A stored workspace is checked for unknown keys, duplicate ids, unsupported "supported" claims, approval digests, assignment and outcome references, salt digests and ranges, and every application's events are replayed through the same gate as the reducer. |
| F-11 | medium | A `replace` action existed; a locked experiment's salt could be swapped; duplicate assignment ids double-counted an outcome; a closed experiment could be reopened and its outcomes rewritten. Reducer-level. | **Fixed.** The action is removed; the salt is fixed at creation and the lock digest must match it; assignment ids are unique; closed is terminal; outcomes must match their assignment's experiment and a metric in the design. |
| F-12 | medium | The date-time check accepted offsets such as `+99:99`, which Ajv rejects; an unparseable time became "fresh" and "not expired". | **Fixed.** Offsets are range-checked; an unreadable time is "unreadable" for freshness and "expired" for claims. |
| F-13 | medium | The hand-edit lint missed number words, full-width digits, digits reused from tokens like "p95", titles, employers and many credential spellings. | **Fixed** for every case reported (new figure, number word, credential stems and abbreviations, titles, proper names). It remains a wording comparison, not a fact checker; it errs toward blocking. |
| F-14 | medium | The link dialog and the Matching badge said "synthetic" for a posting whose source was not synthetic. | **Fixed.** Both say "provenance not verified" unless the posting declares itself synthetic. Browser test added. |
| F-15 | low | Ages use the fixed fixture clock, so "missing feedback" cannot fire for events recorded after it; two tabs could overwrite each other's events. | **Partly fixed.** A tab now adopts a workspace saved by another tab before appending to it. The fixed fixture clock is by design (D-05) and stays a documented limitation of fixture mode. |
| F-16 | low | Axis ticks never terminated for coordinates near 1e16, which pass validation. | **Fixed.** Counted loop, at most 12 ticks; extracted to `src/lib/ticks.ts` with a test. |
| F-17 | low | CSV: full-width formula characters and semicolon-split locales were not handled; real negative numbers were prefixed with an apostrophe. | **Fixed.** Full-width leads are neutralised, cells with a semicolon are quoted, numbers are written as numbers. Behaviour in actual spreadsheet programs was not tested. |
| F-18 | low | Private-use names (`.lan`, `.corp`, `.home.arpa`, `.svc`, wildcard-DNS such as `nip.io`) passed the link check without a note. | **Fixed.** They are blocked; a non-standard port is pointed out. |
| F-19 | low | Ids such as `constructor` crashed the simulator or were mistaken for "has a salt"; duplicate line ids hid a blocked line; several list keys could collide. | **Fixed.** Own-property lookups, duplicate ids rejected, index-qualified keys. |
| F-20 | low | Some tests did not test what they claimed: a tautological condition in the stand-in check, no small-plan case for the evidence label, no offset case for date-times, doc comments naming test files that do not exist. | **Fixed.** The stand-in check now also catches dynamic imports and proves it can fail; the missing cases are in `review-cycle.test.ts`; the comments point at real files. |

What the reviewer checked and found correct: `assignVariant` against the Python reference on 3,000 further random cases (0 mismatches); the interval and sample-size formulas; that no dangerous scheme, credentialed or numeric-host link gets through; that no data reaches an HTML sink or an executable attribute; that export is impossible without an approval matching current content. It did not run the browser tests, review CSS, or test real spreadsheet programs.

## Known limitations

- **Platforms:** one OS, one browser engine. No assistive-technology testing. axe finds only machine-checkable problems.
- **Stand-in data:** in fixture mode the match, atlas and scenario numbers come from a dev-time stand-in, not from lanes A and C. They are labelled as such on screen and in Settings.
- **Pipeline mode gaps:** evidence references do not resolve (no evidence registry in the artifact layout); exposure of experiment units is unknown; lane A's compensation state, location parts and timestamps are not rendered.
- **Bidi and zero-width characters** in upstream text are displayed as they arrive (each string in its own block). They are not stripped; lane A strips them at ingestion.
- **First load** needs a local static server; `file://` does not work for module scripts.
- **Fixture clock:** in fixture mode every age (posting freshness, days without a reply, claim expiry) is measured against the fixed snapshot date, so an application you record after that date never shows as "no reply recorded". Pipeline mode uses the latest time in the run.
- **The résumé guard compares wording, it does not check facts.** It blocks figures, number words, credentials, titles and names that the linked claims do not contain. A false statement phrased without any of those would only get a "reworded" warning.
- **Statistics shown in fixture mode** (Wilson, Newcombe, sample size) are standard formulas checked against published values, but they are lane D display code, not lane B's analysis. In pipeline mode lane B's report is shown instead.
- **Single user:** export and erase act on one browser profile's storage. There is no subject scoping (hub finding E-F004, owner decision pending).

## Screenshots

`docs/screenshots/` (synthetic data only), regenerated with `CT_SCREENSHOTS=1 npx playwright test tests/e2e/screenshots.spec.ts`:
`01-discover-jobs`, `02-matching`, `03-resume-truthfulness-guard`, `04-applications-board`, `05-experiment-results`, `06-career-atlas`, `07-career-simulator`, `08-overview-phone-360`.

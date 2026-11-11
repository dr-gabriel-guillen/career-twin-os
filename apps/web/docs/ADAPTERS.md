# Adapters: how lane D reads lanes A, B and C

The UI never touches upstream data directly. Everything goes through `src/adapters/`, in three steps:

1. A **source** (`PayloadSource`) hands over raw payloads: bundled fixtures (`fixtureSource.ts`) or the artifacts of an integration run (`artifactSource.ts`).
2. A **translator** (`upstream.ts`) recognises the upstream dialect and copies its facts into the view shape the screens render. It computes no score, verdict or coordinate.
3. A **validator** (`frozen.ts`, `proposedValidators.ts`) checks every record. Records that fail are rejected and reported in Settings → Data sources. Nothing is repaired or guessed, and a payload the source does not provide is shown as "not provided", never back-filled from fixtures.

## Two data sources

| Source | How to select it | What it is |
| --- | --- | --- |
| Fixtures | default | Lane D's deterministic stand-in (`scripts/generate-fixtures.ts`). Labelled "Synthetic fixture mode". |
| Pipeline artifacts | open the app with `?source=artifacts` before the `#` | JSON files served next to the app under `./artifacts/`, in the layout lane E proposed on Issue #5. Labelled "Pipeline artifacts mode", with the list of sources that call themselves stub or mock and the list of files the pipeline did not provide. The stored workspace is neither read nor written. |

`npm run e2e:integration -- --artifacts <dir>` builds the app, copies `<dir>/*.json` beside it, runs the keyboard-only journey in `tests/integration/pipeline.spec.ts`, and writes `<dir>/ui_report.json`. `integration-manifest.json` declares this as lane E's `ui.e2e` stage.

## Frozen contracts (validated exactly)

| Contract (v0.1) | Owner | Validator | Evidence of agreement with the JSON Schema |
| --- | --- | --- | --- |
| `job-posting` | A | `isJobPosting` | 2,505 mutations, 0 disagreements with Ajv |
| `profile-claim` | A | `isProfileClaim` | 1,122 |
| `application-event` | B | `isApplicationEvent` | 1,602 |
| `experiment` | B | `isExperimentSpec` | 554 |
| `atlas-point` | C | `isAtlasPoint` | 1,242 |
| `transition-scenario` | C | `isTransitionScenario` | 1,716 |

## Upstream dialects that are recognised

| Artifact file | Dialect | Owner | Tested against | Notes |
| --- | --- | --- | --- | --- |
| `jobs.json` | frozen `JobPosting[]` | A | lane E stub output | |
| `jobs.json` | `a-ext/0.1` envelope `{posting, ext}` (list or `{items}`) | A | lane A `examples/jobs_query.json` | `posting` is unwrapped and validated. |
| `jobs_ext.json` | `a-ext/0.1` envelopes beside a bare `jobs.json` | A | real pipeline run | Read for display facets only: duplicate cluster and members, ingestion security flags, `synthetic`, status reason, employer id. Discover Jobs uses the clusters for duplicates and shows the flags. |
| `matches.json` | `a-match/0.1` MatchResult, or the bare shortlist array | A | lane A `examples/rank_result.json`; real pipeline run | Scores, interval, constraints, skills, unknown inputs and counterfactuals are copied. A component whose `state` is `unknown` or `not-applicable` is shown as excluded with weight 0. Display weight of a known component = its share of its group's effective weights. Only lane A's `score_type` constant is accepted for this dialect. |
| `matches_full.json` | full `a-match/0.1` result | A | real pipeline run | Read for `refusals`, `subject_id`, `as_of`. Refused postings are listed on the Matching screen. |
| `matches.json` | reference engine (`score_job`) | main / E stub | lane E stub output | One score only. No per-group score is invented; components are shown with weight "not published". Reported as a mock source. |
| `resume_variant.json` | lane E stub (`bullets[].claim_ids`) | E | lane E stub output | Imported as an unapproved draft. |
| `resume_variant.json` | `careertwin.b.resume-variant.v1` (`bullets[].claim_id`, `manual_override`) | B | real pipeline run | Imported as an unapproved draft. `refused_claims` and `warnings` are shown in the artifact banner. |
| `assignments.json` | lane E stub / lane B (`assignment_id`, `design_hash`, `unit_kind`, `unit_id`) | B | stub output; real pipeline run | Exposure is unknown in both shapes and is shown as unknown. |
| `analysis.json` | lane E stub (`decision`, `arms[]`, `effect`) / `careertwin.b.analysis-report.v1` (`verdict`, `arms{}`, `flow.by_arm`, `comparisons[]`) | B | stub output; real pipeline run | Rendered as given in place of local statistics: verdict, phase, per-variant assigned / positive / censored / missing, first comparison with its interval, reasons withheld, warnings, limits. An unknown verdict yields no result at all. |
| `atlas_points.json` + `atlas_diagnostics.json` | frozen `AtlasPoint[]` + lane E's diagnostics field names, with lane C's extras (`explained_variance_cumulative`, `knn_recall_mean`, `flags`, `warnings`, `information_loss.statement`, `unplaced`) | C | lane E stub output; real pipeline run | Role family is joined from `jobs.json` by id. |
| `atlas.pca.json`, `atlas.axes.json` | `atlas-projection/0.1` | C | lane C `analytics/reports/for-lane-d/` | Accepted as-is, including `knn_recall`, `flags`, `unplaced[]`, `synthetic_only`. Preferred over the point/diagnostics pair when present. |
| `scenarios.json` | frozen `TransitionScenario[]` with `scenario-outputs/0.1` | C | lane C `for-lane-d/scenarios.json` | Coverage follows lane C's evidence rule (below). Outputs in any other shape are kept but no number is derived from them. |
| `events.json`, `experiment_events.json` | frozen `ApplicationEvent[]` | B | lane E stub output | Merged, de-duplicated by id. |
| `experiment.json` | frozen `ExperimentSpec`, or `{spec, design}` | B | lane E stub output | |

Pinned copies of every sample, with lane, commit and command: `tests/upstream-samples/PROVENANCE.md`.

## Rules the adapters enforce

- **A score that calls itself a probability is refused.** `score_type` must be one of two constants that deny it (`relative_heuristic_not_hiring_probability`, `relative_heuristic_not_offer_probability`).
- **Unknown is not zero.** A component with `value: null` is `unknown` with weight 0; a known component must carry a value. Censored and missing experiment outcomes must have `value: null`.
- **Hypotheticals must say so.** A counterfactual whose `status` does not contain "hypothetical" is refused. A scenario whose `status` is not the constant `hypothetical` is refused by the frozen contract.
- **No probability-shaped scenario outputs.** Keys that name a probability, chance, likelihood or odds make `outputs` unusable (hub finding E-F005), except an explicit denial flag such as `not_a_hiring_probability`.
- **Projection consistency.** Every point must belong to the projection that carries it (same `projection_id` and method); path endpoints must be points of that projection; entity ids must be unique.
- **Referential integrity.** A match for a posting that is not in the feed, an assignment for an unknown experiment, an outcome for an unknown assignment and an analysis for an unknown experiment are dropped and reported. Application events for a posting outside the feed are kept (they are the user's history) and flagged.
- **An upstream résumé variant is never treated as approved.** Approval is the user's act in this UI, bound to a digest of the exact text.

## Coverage in the simulator (lane C's request)

Lane C counts a target skill as covered only when its evidence reaches level 0.75; the UI must not infer that from skill names.

| Situation | What the UI shows | Basis shown to the user |
| --- | --- | --- |
| Plan untouched | lane C's `coverage_after` | upstream |
| Actions switched off, hours untouched | recount from `outputs.applied_actions[].reaches_evidenced_level`, joined by `skill` (one planned action can expand into several features) | upstream per action |
| Effort hours edited | no coverage value; "needs recalculation upstream" | not shown |
| Payload has no `applied_actions` (lane D stand-in only) | skill-name count, labelled as a stand-in | stand-in |

Hours, time at the user's pace and the user's own cost entries are always recomputed locally: they are sums of the user's numbers.

## Gaps and open requests

| Gap | Asked of | Where |
| --- | --- | --- |
| Six points on `a-match/0.1` (canonical `score_type`, unknown components, blend formula, `source` on synthetic postings, contradicted skills, non-empty vault example) | A | Issue #12: all six answered by lane A and consumed here; closed |
| Lane A's `ext.compensation.state`, `ext.location` and `ext.timestamps` are not rendered yet. In fixture mode likely-duplicate grouping is still a title/employer heuristic (labelled "Possible duplicate"); with lane A's envelope it uses lane A's clusters (labelled "Duplicate cluster"). | D (next batch) | this document |
| Lane E's stub résumé variant cites a claim marked `private`; the UI blocks that line | E | Issue #6 |
| No evidence registry, preferences or outcome records in the artifact layout: Profile Evidence shows unresolved evidence references and the Experiments screen relies on the upstream analysis | E / B | Issue #6 |
| `ApplicationEvent`, `ExperimentSpec`, `AtlasPoint` have no subject id, so export and erase cannot be scoped by payload (hub finding E-F004). The app is single-user by construction: one browser profile, one workspace. | owner | Issue #1 |

## Replacing the stand-in

When lanes A/B/C export fixture files in their own dialects, point `fixtureSource.ts` at them (or ship them as artifacts) and delete `scripts/standin/` and `scripts/generate-fixtures.ts`. No page needs to change: pages only see the validated view shapes.

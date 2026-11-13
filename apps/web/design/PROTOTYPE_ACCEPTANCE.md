# CareerTwin OS — journey prototypes: visual acceptance report

| | |
|---|---|
| **UI code under review** | `c518437f3aa4395678fff67269168fb820c455c4` (branch `agent/ct-f-premium-ui`, draft PR #13) |
| **Previous review** | `39675b33b18192c54772ef1ac6a320bf582316d8` ([CHATGPT-REVIEW] on PR #13: diff empty state, Atlas wording) |
| **Report commit** | the commit that adds this file. It changes only `apps/web/design/**` (this report and screenshots), never UI code |
| **Scope** | prototypes only (`/prototypes.html`). The seven other production screens were **not** redesigned, and the production CRM is **not** fixed (see section 4) |

Every screenshot comes from a real Chrome run:
- **Journey and Atlas states:** captured from the lane E launcher's build of `c518437` on the real synthetic pipeline run (`run-20261113T043157Z`: 160 postings, 48 ranked), served at 127.0.0.1:4191. One exception: the Atlas two-position case uses fixture data from the same build.
- **Résumé-diff cases:** taken by `tests/e2e/prototypes-visual.spec.ts` on the same code, using fixture data.

## 1. Corrections since `39675b3`

| Finding | Fix | Proof |
|---|---|---|
| **HIGH: Tailor step 3 empty state rendered vertically.** The text was a bare `<li>` inside the 18 px marker column of a grid | Change rows have an explicit marker column and content column. The empty state is its own full-width block. A summary line counts added, removed and unchanged lines | Diff cases below. The visual test fails if any text renders with fewer than 6 characters per line, if the empty state is narrow or tall, or if row text is not in the content column |
| **MEDIUM: Atlas overstated PCA** ("keeps N% of the skill information") | "The 2-D projection explains N% of the variation in the input skill vectors". Diagnostics add: "This describes the projection of the vectors, not how much career-relevant information they hold." | Atlas captures; a text assertion fails on "skill information" |
| **MEDIUM: "Two marks show you" shown unconditionally** | The position note and legend are computed from the candidate points actually projected: two, one (all claims), one (evidence-only) or none | Pipeline run: one mark. Fixture: two marks. Tested both ways |
| Found while testing: moving between postings in Tailor kept the previous step and draft | Each posting gets its own tailoring session | Covered by the diff test, which tailors several postings in one tab |
| Found while testing: a claim added in step 2 disappeared from the list, so it could not be removed | Added claims stay listed with "Remove" | The removed-only case is reached by removing the added claim |
| Found in the captures: the Atlas route label overflowed the map edge (583..738 of 694 px) | Edge labels anchor inward | The new label-bounds test **fails on `33d05bd` and passes on `c518437`** |
| Found in the captures: phone step bar clipped the current step's label | On phones only the current step shows its label; the others are visually hidden but still read by screen readers | Phone captures; the detector skips only 1×1 visually hidden text |

## 2. Résumé diff: empty, added, removed and mixed (Tailor step 3)

| Case | Desktop 1440, light | Desktop 1440, dark | Phone 375, light | Phone 375, dark |
|---|---|---|---|---|
| Empty | ![](review/acceptance/diff-empty-1440-light.png) | ![](review/acceptance/diff-empty-1440-dark.png) | ![](review/acceptance/diff-empty-375-light.png) | ![](review/acceptance/diff-empty-375-dark.png) |
| Added only | ![](review/acceptance/diff-added-1440-light.png) | ![](review/acceptance/diff-added-1440-dark.png) | ![](review/acceptance/diff-added-375-light.png) | ![](review/acceptance/diff-added-375-dark.png) |
| Removed only | ![](review/acceptance/diff-removed-1440-light.png) | ![](review/acceptance/diff-removed-1440-dark.png) | ![](review/acceptance/diff-removed-375-light.png) | ![](review/acceptance/diff-removed-375-dark.png) |
| Mixed | ![](review/acceptance/diff-mixed-1440-light.png) | ![](review/acceptance/diff-mixed-1440-dark.png) | ![](review/acceptance/diff-mixed-375-light.png) | ![](review/acceptance/diff-mixed-375-dark.png) |

## 3. The three journeys and the Atlas concept (pipeline run, `c518437`)

Each one is submitted for **separate** approval.

**Journey 1: Discover → job details → explain fit → shortlist**

| Current production | Proposed, desktop | Proposed, phone |
|---|---|---|
| ![](review/acceptance/j1-current-discover-1440.png) | ![](review/acceptance/j1-proposed-detail-1440.png) | ![](review/acceptance/j1-proposed-discover-375.png) ![](review/acceptance/j1-proposed-detail-375.png) |

**Journey 2: Job → résumé adaptation → evidence and diff review → approval and export**

| Current production | Proposed, desktop | Proposed, phone |
|---|---|---|
| ![](review/acceptance/j2-current-resume-1440.png) | ![](review/acceptance/j2-proposed-step1-1440.png) ![](review/acceptance/j2-proposed-step2-1440.png) ![](review/acceptance/j2-proposed-step3-1440.png) | ![](review/acceptance/j2-proposed-step3-375.png) ![](review/acceptance/j2-proposed-step4-375.png) |

On the pipeline run, step 3 shows the empty case, because the pipeline vault offers no further claims for this posting. The added, removed and mixed cases are shown in section 2.

**Journey 3: Saved job → application tracking → next action and follow-up**

| Current production | Proposed, desktop | Proposed, phone |
|---|---|---|
| ![](review/acceptance/j3-current-applications-1440.png) | ![](review/acceptance/j3-proposed-tracker-1440.png) ![](review/acceptance/j3-proposed-followup-1440.png) | ![](review/acceptance/j3-proposed-list-375.png) ![](review/acceptance/j3-proposed-tracker-375.png) |

**Career Atlas concept: transitions and decisions**

| Current production | Proposed, pipeline run (one position) | Proposed, fixtures (two positions) | Phone |
|---|---|---|---|
| ![](review/acceptance/atlas-current-1440.png) | ![](review/acceptance/atlas-proposed-1440.png) | ![](review/acceptance/atlas-1440-fold.png) | ![](review/acceptance/atlas-proposed-375.png) |

The FDE plan's hypothetical end point landing next to the Frontend centre stays an **open question for lane C** (#6). The concept does not infer a model defect from 2-D proximity; it says the route end need not sit next to the role's centre and that distance is not fit.

## 4. Experiment-generated applications: what changed and what did not

- **Changed, in the prototypes only:** applications whose records come from `experiment_events.json` are listed under a disclosure ("N records from the pipeline's experiment run are not shown here"). They are kept out of the prototype's applications, next actions, follow-ups and counts.
- **Unchanged: experimental analysis.**
  - The bundle only gains a provenance list (`experimentUnitApplicationIds`).
  - Experiments, assignments, outcomes and upstream analyses are loaded exactly as before.
  - The Experiments screen reads assignments, outcomes and analyses, never application events.
  - `tests/unit/experiment-units.test.ts` asserts that analyses, assignments and experiments are **identical** whether or not `experiment_events.json` is present, and that every application event is still loaded.
- **Not fixed: the production CRM.** The current Applications and Overview screens and Discover's "Tracked" label still include the generated records. The same unit test asserts that the production application data still contains them. Fixing production is pending owner approval and the B/E decision on #6 (whether those events should stop claiming `actor: "user"`).

## 5. Evidence, kept separate

**A. Prototype-specific tests (F, local Windows run on `c518437`; agent-run, not CI).** These are the only tests that exercise `/prototypes.html`.

| Suite | Result |
|---|---|
| `tests/e2e/prototypes.spec.ts`: journey 1 desktop and phone; journeys 2→3 end to end; artifact-mode exclusion of experiment records; axe on every prototype screen | 4 / 4 passed |
| `tests/e2e/prototypes-visual.spec.ts`: diff cases at 1440 and 375 in light and dark; squeezed-text detector on every prototype screen at 1440 and 375; Atlas labels inside the map; Atlas wording and conditional positions | 7 / 7 passed |
| `tests/unit/experiment-units.test.ts` | 4 / 4 passed |

**B. Product regression suites (F, local):** `npm run typecheck` clean. vitest **163 / 163** (159 existing plus 4 above). Playwright **64 passed, 1 skipped** (53 product tests plus the 11 prototype tests above; the skip is the opt-in screenshot regenerator). `npm run test:integration` 1 / 1. These cover the main app; the prototype counts above are included in the Playwright total, not added to it.

**C. Lane E integration runner (separate evidence).** Lane E's launcher @ `9f7219e`, run locally with only the D slot set to `c518437` (scratch pins, not committed to E's branch): `python -m ctint.acceptance --pins <scratch> --install --verify-ui --pass-env PW_CHANNEL=chrome --port 4191`. Evidence: `run-20261113T043157Z`.

| Stage | Lane | Status |
|---|---|---|
| jobs.import, profile.claims, match.rank, features.vectors | A | PASS |
| resume.variant, application.record, experiment.run | B | PASS |
| atlas.project, simulator.scenario | C | PASS (simulator updated its own sidecar `atlas.pca.json`; allowed, not a contract artifact) |
| ui.e2e | D (here F `c518437`) | PASS |

The served build was checked to be `c518437`: provenance names it, and the clipped-label and Atlas-wording tests pass against port 4191 itself while the label test fails against the `33d05bd` build.

`ui.e2e` drives the **main app** through `scripts/e2e-integration.mjs` (routes such as `#/jobs`). It does **not** exercise `prototypes.html`, so E's 10/10 is not evidence for the four prototype journeys.

## 6. Known limitations, not hidden

- **Atlas label overlaps:** labels can still overlap at dense spots, such as "You" next to the route end, or two centres drawn close together. The labels now stay inside the map, but there is no collision avoidance yet, and the squeezed-text detector checks HTML text only.
- **Atlas FDE route:** the FDE route end next to Frontend remains an open data question for lane C.
- **Production CRM:** still includes the experiment-generated applications (section 4).

## 7. Approval requested, separately

1. Journey 1: Discover → job details → explain fit → shortlist.
2. Journey 2: Job → résumé adaptation → evidence and diff review → approval and export.
3. Journey 3: Saved job → application tracking → next action and follow-up.
4. Career Atlas concept: transitions and decisions.

Each can be approved, changed or rejected on its own. Nothing is integrated into production screens before that.

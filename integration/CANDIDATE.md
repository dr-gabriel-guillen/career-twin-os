# CareerTwin OS: consolidated acceptance candidate

| | |
|---|---|
| **Branch** | `agent/ct-consolidation` (one branch that merges lanes A, B, C, E and F) |
| **Code commit under test** | `aaab3ae00da5ad7d6bb63a946f449e408887c051` |
| **Report commit** | the commit that adds this file. After `aaab3ae` it changes only `integration/` (this report, the candidate pins, lint fixes and the runner's consolidated mode) and `apps/web/design/consolidation/` (screenshots). No product code |
| **Base** | `main` @ `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` |
| **Merged lane heads** | A `0b331ea6`, B `755ccae1`, C `f76e24c8`, E `9f7219e0`, F `f165286d` (which contains D `b6a4340`). No lane branch moved; no PR merged |
| **Status** | Ready for **one owner acceptance review**. Not approved, not merged, not deployed. Synthetic data only |

## 1. Launch it (one command, one address)

Prerequisites: git, Python 3.12 or later, Node.js 22.18 or later, Google Chrome.

```bash
git clone https://github.com/dr-gabriel-guillen/career-twin-os.git ct && cd ct
git checkout agent/ct-consolidation
python -m pip install -r integration/requirements.txt
cd integration
python -m ctint.acceptance --pins acceptance-pins.candidate.json --install --verify-ui --pass-env PW_CHANNEL=chrome --port 4196
```

Then open **http://127.0.0.1:4196/?source=artifacts#/**, the whole product on this run's pipeline output (read-only). Without `?source=artifacts` the same build runs on built-in demo data and keeps your changes in the browser. `http://127.0.0.1:4196/artifacts/provenance.json` names the commit and a SHA-256 for every file shown. `--install` runs `npm ci` once; later launches are offline. No browser is downloaded.

The known-good baseline stays in `acceptance-pins.json` (lane D `b6a4340`); `acceptance-pins.candidate.json` pins every lane to `aaab3ae`.

## 2. What changed for the owner

There is one app with ten screens, not a main app plus prototype pages. `prototypes.html` is gone, and its old addresses redirect.

| Screen | What it is now |
|---|---|
| **Overview** | The next actions computed from your own records: follow-ups due, silence after 14 days (an unknown outcome, not a rejection), claims that block résumés, the closest posting you haven't acted on |
| **Discover** | Search, five views and filters (role family, work style, capture age, duplicates) beside a decision panel that covers why it could fit, what stands in the way and what would improve your fit. Every reason traces to a claim. Scores, constraints, unknown inputs and per-skill evidence sit behind "How the engine computed these". Actions: Shortlist, Tailor résumé, Save to tracker |
| **Compare** | Up to three postings side by side, plus a ranked table. Near-term fit and strategic value stay two numbers; the blend only orders the table. Postings the engine refused are listed with reasons |
| **Applications** | Grouped by what each application needs, with one **next action**. You can record what happened, set follow-up dates, add notes, export CSV, and see the approval reference. The activity log says who did what: sample data is never "you" |
| **Résumé Studio** | The document editor (versions, evidence inspector, diff, approval gate) plus **Tailor for a job**: choose a starting résumé, switch your own lines on or off, add vault claims verbatim, review the diff, approve the exact text shown as a document, export, and link it to the application |
| **Profile Evidence** | The evidence vault. Unchanged; "supported" comes only from evidence |
| **Career Atlas** | **Transitions:** where you are, destinations with coverage now and after, effort, months at your pace, assumptions and risks, and the route on a legible map. **Full map:** every point, filters, table and diagnostics |
| **Career Simulator** | Edit plans, costs you enter, and your own weights (order only) |
| **Experiments** | Preregistered design, assignment and results as before, plus a clearly labelled **simulated activity** section for the pipeline's experiment run |
| **Settings** | Data sources, adapters, theme and motion, export and erase |

### Before (rejected `5cbd3b7`) and after (`aaab3ae`), desktop, pipeline run

| Rejected UI | Candidate |
|---|---|
| ![](../apps/web/design/consolidation/before/j1-current-discover-1440.png) | ![](../apps/web/design/consolidation/screens/discover-1440.png) |
| ![](../apps/web/design/consolidation/before/j1-current-matching-1440.png) | ![](../apps/web/design/consolidation/screens/compare-1440.png) |
| ![](../apps/web/design/consolidation/before/j2-current-resume-1440.png) | ![](../apps/web/design/consolidation/screens/tailor-step4-1440.png) |
| ![](../apps/web/design/consolidation/before/j3-current-applications-1440.png) | ![](../apps/web/design/consolidation/screens/applications-1440.png) |
| ![](../apps/web/design/consolidation/before/atlas-current-1440.png) | ![](../apps/web/design/consolidation/screens/atlas-1440.png) |

### All ten screens

Desktop 1440 px, tablet 820 px and phone 390 px captures of every screen are in [`apps/web/design/consolidation/screens/`](../apps/web/design/consolidation/screens/). For example: `overview-1440.png`, `experiments-820.png`, `atlas-390.png`, `discover-1440-dark.png`, `tailor-step3-390.png`. They were taken from the candidate server on port 4196 (pipeline run `run-20261113T082245Z`), except the Tailor steps, which use the interactive demo data.

| Overview | Résumé Studio | Experiments (simulated activity) |
|---|---|---|
| ![](../apps/web/design/consolidation/screens/overview-1440.png) | ![](../apps/web/design/consolidation/screens/resume-1440.png) | ![](../apps/web/design/consolidation/screens/experiments-1440.png) |

| Discover, phone | Applications, phone | Career Atlas, phone |
|---|---|---|
| ![](../apps/web/design/consolidation/screens/discover-390.png) | ![](../apps/web/design/consolidation/screens/applications-390.png) | ![](../apps/web/design/consolidation/screens/atlas-390.png) |

## 3. The 138 simulated applications (release blocker): fixed

**Cause.** Lane B's experiment stage simulates approvals and submissions to exercise the approval gate. It writes them to `experiment_events.json` as `actor: "user"`, `source: "user:approval-dialog"`. The UI merged that file into the application log, so the run's 138 simulated units appeared as tracked applications, in Overview counts and in follow-ups.

**Fix. Provenance is the stream, never an id prefix or the actor field.**
- **UI adapters:** `events.json` is the application log. `experiment_events.json` is a separate `lab-events` stream (`bundle.labEvents`). A simulated event that reuses a log id is dropped and reported; an application id found in both streams is reported and never merged.
- **Downstream screens:** Applications, Discover's tracked state, Overview counts and follow-ups all derive from the log, so none of them sees the simulated stream. Experiments shows it, labelled "Simulated", with counts and a table.
- **Attribution:** only events recorded in this browser read "You recorded this here". Sample data reads "Pipeline sample data" or "Demo data", whatever its actor field says.
- **Lane B (producer):** simulated approvals and submissions now carry `simulation:` sources and a note: "Simulated by the pipeline's experiment stage. No application was submitted and no employer was contacted." The gate logic is unchanged. `record_user_submission` takes an optional `source`.
- **Lane E (gate):**
  - `experiment.run` fails if simulated application ids reuse log ids, or if a simulated approval or submission lacks a `simulation:` source.
  - `ui.e2e` fails unless the browser-measured application count equals `events.json` and zero simulated units are shown.
- **Experimental analysis is unchanged:** specs, assignments, outcomes and analyses are identical with or without the simulated stream (unit test).
- **No contract change.**

**Evidence on the live run (`run-20261113T082245Z`):**
- 587 simulated events across 138 units.
- 138 simulated approvals and 138 simulated submissions, all labelled `simulation:`.
- Browser-measured: Applications shows **1** application, matching the log's 1 application id; **0** simulated units shown.
- Experiments shows 138 simulated units.

## 4. Career Atlas validity: the FDE endpoint

Observation: the FDE plan's hypothetical point lands next to the Frontend centre on the 2-D map. The check below was re-run with lane C's own engine on the run's artifacts.

| Check | Result |
|---|---|
| Projection identity | Candidate, scenario, role centres and route share projection `pca-dc3229688ed9cdf3-2d` (model `dc3229688ed9cdf3`). Re-projecting the candidate and scenario vectors with the fitted model reproduces the published coordinates exactly: (−0.106714, 0.607216) and (−0.596866, 0.455499) |
| Linear shift | The per-feature map shifts add up to the reported shift (−0.490, −0.152). Candidate plus shift equals the scenario point |
| Full-space movement | Distance to the **FDE** profile goes 2.253 → **2.071** (closer). Distance to **Frontend** goes 3.090 → **3.315** (further) |
| Why it looks near Frontend | About 90% of the scenario vector's variation lies outside the two drawn axes (residual share 0.896). The plan moves the point left on axis 1 (technical discovery, systems integration) but barely on axis 2. Most of what separates the candidate from the FDE centre is untouched: strong cloud architecture, distributed systems, AWS, Kubernetes and SQL, which FDE postings rarely list, and FDE's customer delivery and enterprise sales, which the plan doesn't include. Distributed systems alone accounts for −0.41 on axis 2 |
| Verdict | **No model or projection defect.** The 2-D proximity is a projection effect. Model outputs were not altered |
| Finding for the owner | Two of the four auto-generated demonstration actions, security and TypeScript (both to 0.8), overshoot the FDE profile (0.38 and 0.56) and slightly *increase* full-space distance. Lane C's demo plan picks gaps by requirement shortfall, not by profile distance. See decision 3 |

UI changes:
- The route card states the full-space movement ("closer to the FDE role profile: distance 2.25 now, 2.07 after; lower is closer; not fit").
- Labels are placed greedily without overlap at the map's real pixel size, so text stays at 11.5 px on phones. A label that can't fit tries a short form ("FDE"), and any still unplaced are listed under the map.
- PCA wording: "The 2-D projection explains 26% of the variation in the input skill vectors". Distance is not fit and not a likelihood.

New lane E gate `scenario_geometry`: the drawn route must equal the model's linear shift on one projection. Proximity to a role centre is deliberately not checked.

## 5. Verified test matrix

All runs below are **local runs by this agent** on Windows 11 with Chrome 157, on the code commit `aaab3ae`. They are **not CI**. GitHub CI has not run on this branch yet.

| Area | Command | Result |
|---|---|---|
| Web typecheck | `npm run typecheck` | clean (no JS/TS linter is configured in `apps/web`) |
| Web unit and contract | `npx vitest run` | **171 / 171** |
| Production build | `vite build` | OK, CSP meta, no inline script |
| Browser E2E, all ten screens | `CT_E2E_PORT=4195 PW_CHANNEL=chrome npx playwright test` | **63 passed, 1 skipped** (the opt-in screenshot regenerator) |
| … axe WCAG 2.1 AA, light and dark | `screens.spec` | 10 screens × 2 schemes, plus interactive states: pass |
| … four integrated journeys | `journey.spec` (Discover → Compare → Studio and Tailor → Applications → Experiment → Atlas → Simulator), `premium.spec`, `security.spec` | pass |
| … visual layout at 1440 / 768 / 375 | `visual.spec` | no squeezed text and no horizontal scroll on all ten screens. Atlas labels inside the drawing, non-overlapping and ≥ 10.5 px on fixtures and the pipeline sample. Tailor diff cases. **Layout assertions, not pixel baselines** |
| UI integration (lane E `ui.e2e` script) | `PW_CHANNEL=chrome npm run test:integration` | 1 / 1. Keyboard only, 339 Tab presses, 0 pointer events; CRM boundary 1 of 1, 0 of 69 simulated shown (committed sample) |
| Ten-stage pipeline with browser stage | launcher (section 1), `run-20261113T082245Z` | **10 / 10 PASS** |
| Full integration gate | `python -m ctint.runner … --lane A..D=aaab3ae --allow-setup` | **PASS** in consolidated mode (section 5a) |
| Lane E harness | `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -m "not network"` | clean, clean, clean, **589 passed** |
| Lane B suites | `pytest` in `packages/experiments`, `packages/applications` | **99 passed**, **32 passed** |
| Secrets, paid APIs, CI hygiene | `ctint.sec_scan --tracked`, `ctint.sec_paid --tracked`, `ctint.sec_ci` | 0, 0, 0 findings (651 files) |
| Served build is the candidate | provenance plus bundle strings on port 4196 | provenance names `aaab3ae` for every lane; the bundle contains the new screens' strings; old servers stopped |

### 5a. Full integration gate (`ctint.runner`)

First run (per-lane ownership rules): 10/10 stages PASS, every lane's own test suite PASS (A: matching, connectors; B; C; D unit), secrets 0, paid/outreach 0, manifests OK. It **failed only on ownership**, because "lane D's commit" is the merged commit and so touches A/B/C paths. The runner now detects when every lane is pinned to one merged SHA, says so in the report, and applies the equivalent rule: every changed path belongs to some lane, and no frozen contract changed. Rerun result: **Integration run: PASS**. 10/10 stages; lanes A, B, C and D each PASS with 0 ownership violations, manifests OK, 0 secrets, 0 paid/outreach findings, and all lane test suites passing (A 2/2, B 1/1, C 1/1, D 1/1). The report carries the "consolidated commit" note for each lane.

## 6. Security and truthful state

- **Destructive output directories (E's HIGH repair):** kept. `ctint/outdir.py` refuses any `--out` that isn't new, empty, or carrying the tool's marker, and inside the repo only `integration/.work/<name>`. It is covered by `tests/test_outdir.py`, which passes within the 589.
- **Symlinks and traversal, synthetic-only, no private-claim leakage, CSP, offline, no outbound calls:** E's security tests and the browser guard (every test fails on any request to another origin) pass. Private, unverified and contradicted claims can't be added to a résumé.
- **Truthful résumés:** the linter blocks invented figures and credentials, and unlinked lines block export. The exact approved text is locked by digest; export is impossible before approval.
- **Truthful application state:**
  - CareerTwin records only what the person states. "I applied" requires an explicit confirmation that they applied outside CareerTwin.
  - No submit, send or contact action exists, and the browser test asserts it.
  - Silence is never recorded as rejection.
- **Experiment integrity:** design locked before assignment; no winner declared without evidence; simulated runs labelled.
- **Projection identity:** one projection id end to end, now gated (`scenario_geometry`).
- **Frozen contracts:** unchanged, and the ownership test on this branch asserts it.

## 7. Known limitations and defects, ranked

1. **MEDIUM: not owner-accepted, and not independently reviewed.** These are agent-run results; ChatGPT hasn't reviewed `aaab3ae` yet.
2. **MEDIUM: simulated events still say `actor: "user"`.** The contract's actor enum has no "simulation" value, and the approval gate requires `user`. The stream plus `simulation:` source make them distinguishable, and the UI never relies on the actor. A contract RFC would make each record self-describing (decision 2).
3. **MEDIUM: Lane C's demonstration plan** includes two actions that overshoot the FDE profile (section 4, decision 3).
4. **LOW:** on phones the transitions map leaves unlabelled any centre it can't fit (listed under the map; the full map has every point). There is no pan or zoom.
5. **LOW:** the visual checks are layout assertions; there are no committed pixel baselines.
6. **LOW:** no ESLint in `apps/web` (typecheck only). The main JS chunk is 567 kB uncompressed (Vite's size warning).
7. **LOW:** two download tests failed once under parallel load. The helper now waits for the finished file, and the next two full runs were clean.
8. **LOW:** the committed pipeline sample (`apps/web/tests/upstream-samples/pipeline`) predates lane B's `simulation:` labels. It is kept on purpose: it proves the UI separates the streams without relying on labels.

## 8. Decisions requested from the owner

1. **Visual and product acceptance** of the integrated product: the four journeys plus the six other screens, at the address in section 1.
2. **Optional contract RFC:** add `"simulation"` to the application-event `actor` enum. The current fix needs no contract change.
3. **Lane C demonstration plan:** keep gap-by-shortfall selection, or prefer actions that reduce full-space distance to the target profile.
4. **Merge approach** (section 9).

## 9. Proposed merge and rollback (pending approval; nothing done)

- **Recommended:**
  - Open `agent/ct-consolidation` as one PR. It supersedes #10 and #13 and contains #7, #8, #9 and #11.
  - After acceptance and green CI, merge it into `main` with a merge commit, so lane history is kept.
  - Then close #7–#11 and #13 as included.
- **Alternative:** merge lane PRs in dependency order: A #7 → B #8 (with `aaab3ae`'s lane B change) → C #9 → E #11 (with the new gates) → web. This needs the web and integration commits split back out of this branch.
- **Rollback:** `git revert -m 1 <merge-sha>` restores `main`. `acceptance-pins.json` still assembles the earlier known-good build, and every lane branch and draft PR is untouched.

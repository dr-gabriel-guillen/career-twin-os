# CareerTwin OS — local acceptance candidate (batch CT-2026-11-10-B01, consolidation)

**What this is.** A locally runnable, synthetic-only assembly of the five lanes at pinned commits, for the
owner to test by hand. **What it is not:** production-ready, deployed, merged, multi-user, or evidence that
the ranking is any good on real postings. Every posting, employer, claim, outcome and pay figure is
fictional. Reviews so far are same-provider reviews plus one finding from the independent reviewer; they
are not external validation.

Nothing is merged. All five PRs (#7–#11) are drafts. The repository is public.

## 1. Pinned commits

| Lane | Branch | Commit | PR |
|---|---|---|---|
| base | `main` | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` | — |
| A data, evidence, matching | `agent/ct-a-data-matching` | `0b331ea6ab365f355e3635b9b4467191a0cf9fae` | #7 |
| B applications, résumés, experiments | `agent/ct-b-experiments` | `755ccae1b4208ac96cbc39d9c4071fa4188c2224` | #8 |
| C atlas, simulator | `agent/ct-c-atlas` | `f76e24c869bad2819810ac06e8b31f067aacf338` | #9 |
| D web UI | `agent/ct-d-product-ux` | `b6a4340836a43d9026f6e0d0b5c60db229de4a62` | #10 |
| E integration, CI, security | `agent/ct-e-integration` | the commit that contains this file (harness code: `be5eb2f7f814dda1ce9a8dc9ff923c5609d405ea`) | #11 |

The launcher reads the A–D pins from `integration/acceptance-pins.json`.

## 2. Setup and launch

Prerequisites: git, Python ≥ 3.12, Node.js ≥ 22.18. Google Chrome only for the optional browser check.

```bash
git clone https://github.com/dr-gabriel-guillen/career-twin-os.git ct && cd ct
git fetch origin
git checkout agent/ct-e-integration
python -m pip install -r integration/requirements.txt
cd integration
python -m ctint.acceptance --install
```

That one command:

1. assembles the pinned commits in `integration/.work/acceptance/tree` with `git archive` (no merge, no
   checkout, no branch moved);
2. runs the pipeline on the real lane code, offline, in a scrubbed environment: A imports 160 fictional
   postings, builds the evidence vault, ranks, exports vectors → B builds a résumé variant, records an
   approved application, runs a preregistered experiment → C fits the atlas and one transition scenario;
3. validates every artifact against the frozen contracts and lane E's cross-stage rules and prints
   PASS / FAIL / SKIP per stage. If anything fails, the UI is not started and the exit code is 1;
4. builds lane D's production bundle and serves it on `http://127.0.0.1:4173` (loopback only).

Open:

* `http://127.0.0.1:4173/?source=artifacts#/jobs` — the UI on **this run's pipeline output** (read-only);
* `http://127.0.0.1:4173/#/` — the UI on lane D's built-in fixtures (**interactive**: edits are kept in
  the browser's local storage);
* `http://127.0.0.1:4173/artifacts/provenance.json` — pins, seed, per-stage status and a SHA-256 for
  every file the UI is showing.

Stop with Ctrl+C. Later launches are offline: `python -m ctint.acceptance`.

| Flag | Effect |
|---|---|
| `--install` | allows `npm ci --prefix apps/web` from lane D's lockfile. Needs the npm registry. **No browser is downloaded.** Only needed the first time. |
| `--verify-ui --pass-env PW_CHANNEL=chrome` | also runs lane D's keyboard-only browser journey on the run's artifacts, using the installed Chrome |
| `--probes` | also runs the input-sensitivity probes (slower) |
| `--port N` | another port |
| `--no-serve` | assemble, run, build, then exit (exit code 0 only if every requested stage passed) |
| `--workspace DIR` | another workspace. On Windows use a short path if the clone path is long: `node_modules` nests deeply |
| `--rebuild` | replace a workspace tree that was assembled from other pins |

The full gate (lane test suites, ownership / secret / paid-API scans, probes, browser stage) is the
integration runner; its exact command is in section 3.

## 3. Verified integration matrix

Run `consolidation-2`, lane E harness at `be5eb2f`, Windows 11, Python 3.14.6, Node 24.16.0, installed Chrome 157.
Statuses are from lane E's runner on real lane code in a disposable detached worktree; none is a stub
or a mock.

```
cd integration && python -m ctint.runner --repo .. --base bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf --lane A=0b331ea6ab365f355e3635b9b4467191a0cf9fae --lane B=755ccae1b4208ac96cbc39d9c4071fa4188c2224 --lane C=f76e24c869bad2819810ac06e8b31f067aacf338 --lane D=b6a4340836a43d9026f6e0d0b5c60db229de4a62 --seed 7 --count 150 --allow-setup --pass-env PW_CHANNEL=chrome
```

Exit code **0**, verdict **PASS**, stages `{'PASS': 10}`, hermetic: False (`npm ci` used the network).

| # | Stage | Lane | Status | What the status rests on |
|---|---|---|---|---|
| 1 | `jobs.import` | A | **PASS** | 160 postings, all marked fictional, frozen `JobPosting` valid, input-sensitivity probe clean |
| 2 | `profile.claims` | A | **PASS** | 9 `ProfileClaim`s for `candidate-synthetic-001`; contradicted and expired claims withheld |
| 3 | `match.rank` | A | **PASS** | 48 shortlisted, `score_type: relative_heuristic_not_offer_probability`; E pinned rank 1 |
| 4 | `features.vectors` | A | **PASS** | one vector per posting, fixed 58-term vocabulary |
| 5 | `resume.variant` | B | **PASS** | 6 bullets, each the verbatim text of a shareable claim; 3 claims refused (2 private, 1 unverified) |
| 6 | `application.record` | B | **PASS** | saved (user) → drafted (system) → approved (user) → submitted (user), one approval token |
| 7 | `experiment.run` | B | **PASS** | preregistered A/A, 138 units; report `STOPPED_EARLY` / `INCONCLUSIVE`, no test, no winner claimed |
| 8 | `atlas.project` | C | **PASS** | 160 points; trustworthiness 0.88, continuity 0.93, 26% variance in 2-D — recomputed independently by E |
| 9 | `simulator.scenario` | C | **PASS** | scenario, atlas points and `atlas.pca.json` share one `projection_id` |
| 10 | `ui.e2e` | D | **PASS** | Playwright on the run's artifacts **inside the scrubbed runner**: Tab traversal only, 0 pointer events, 0 external requests, `data_source: "artifacts"`, `mock_sources: []` |

Lane gates in the same run: ownership violations 0, secrets 0, paid-API / outreach findings 0 for all four
lanes. Lane test suites inside the worktree: A 424 + 170 passed, B 131 passed, C 261 passed, D 159 passed.

Lane E's own suite at `be5eb2f`: `python -m pytest -q -m "not network"` → **581 passed**, 1 deselected;
`ruff format --check`, `ruff check`, `mypy --strict` clean. GitHub Actions (Linux, Python 3.14) is green on `18f6f53` and `be5eb2f`.

| Layer | Status | Note |
|---|---|---|
| Frozen contracts (6) | PASS for every artifact in the run | unchanged by every lane; 10 known contract gaps stay open (E-F001…E-F010, owner) |
| A → B, A → C, A → D, B → D, C → D artifact exchange | PASS | real files, validated by the consumer and by E |
| Mock / stub disclosure | PASS | no stage is a mock; D reports `mock_sources: []`; on E's stub D lists four mock sources |
| Keyboard navigation | PASS for the journey tested | every control in the seven-step journey reached by Tab; other controls are covered by D's own 48 browser tests, not by this run |
| Browser check on Linux CI | **UNKNOWN** | CI's integration job has no browser; it reports and does not gate |
| Cross-user isolation | **UNKNOWN** | no subject / owner field in three contracts (E-F004) |
| UMAP projection | **SKIP** | `umap-learn` not installed; nothing was downloaded; PCA only |
| Real-world ranking validity | **UNKNOWN** | benchmarks measure self-consistency on fictional data |
| Live ATS sources | **SKIP** | not built, by design (owner decision) |

## 4. Security review and repairs (consolidation)

Passing CI was not treated as security evidence. Each item below was reproduced or probed.

| ID | Sev | Finding | State |
|---|---|---|---|
| S-1 | HIGH | Runner deleted whatever existing directory `--out` named (independent reviewer, PR #11). Reproduced at `fcb5572`: two sentinel files gone. | **FIXED** `18f6f53`: fail-closed policy `ctint/outdir.py`; 26 regression tests with sentinel files; evidence on PR #11 |
| S-2 | MED | Same class in the pipeline: the probe scratch directory `<artifacts>.probe` was removed if it existed. | **FIXED** `18f6f53`: a pre-existing one is refused |
| S-3 | MED | Lane D's manifest ran `npx playwright install chromium` under `--allow-setup`: an unapproved browser download. | **REMOVED** in D `b6a4340`. Root cause of the missing browser fixed instead (`6889b32`: the scrubbed home had no `AppData\Local`, so installed Chrome refused to start) |
| S-4 | MED | Lane E's résumé validator accepted a bullet citing a real claim but carrying invented text ("… grew revenue 300% as Chief Technology Officer"). Lane B never produces this; a defective producer would have passed integration. | **FIXED** `be5eb2f`: bullet must equal the cited claim's text; manual-override bullets refused in automated runs |
| S-5 | MED | Lane E's event validator accepted an `offer` recorded by actor `system` (invented application outcome). | **FIXED** `be5eb2f`: system may record only saved / drafted / note; outcome before submission refused |
| S-6 | LOW | Lane B's stage replaces `<artifacts>/lane-b` and removes `<artifacts>/lane-b.partial` without an ownership marker. Fixed names inside the directory it is given; Python's `rmtree` does not follow links. | **ACCEPTED**, documented. Do not point `--artifacts` at a directory that already has a `lane-b` folder you care about |
| S-7 | LOW | Artifacts carry no producer commit. | **MITIGATED**: the launcher writes `provenance.json` (pins + SHA-256 per artifact). The UI does not verify it and says so in its banner |
| S-8 | INFO | `--allow-setup` and `--install` run `npm ci`, including dependency lifecycle scripts, with network access. No SBOM or signature check. | **OPEN**: use only on reviewed commits; owner decision on supply-chain policy |
| S-9 | INFO | Public repository: `sec_scan` (credentials, e-mail, phone, SSN, LinkedIn URLs, forbidden file types) and `sec_paid` report 0 findings on every lane and on lane E (79 tracked files). | PASS, with the limit that scanners are heuristic |
| S-10 | — | Cross-user isolation. | **UNKNOWN** until E-F004 is decided |

Probe results (`python reviews/consolidation_probes.py --artifacts <run>/artifacts --tree <workspace>/tree`):
20 of 20 behave as required at `be5eb2f` (18 of 20 before S-4 and S-5 were fixed).

* **Private-claim leakage.** Lane B's real résumé stage was re-run with every claim private, with the
  cited claims made private, contradicted, expired and removed: it refuses each time (exit 3, no résumé
  written). In the real run no cited claim is private. E's validator flags a private or unknown citation.
* **Invented achievements, credentials, outcomes.** Bullets are verbatim claims; embellished text, a
  system-recorded approval / submission / outcome and a submission without approval are flagged.
* **Experiment integrity.** Rewriting the verdict to a difference with a causal "winner" sentence and
  enrolling one unit twice are both flagged. The real report withholds any test after the early stop.
* **Projection identity.** A scenario on another projection, one atlas point with another projection id
  and coordinates moved without refitting (reported trustworthiness 0.878, recomputed 0.790) are flagged.
* **Synthetic-only.** A posting relabelled as a LinkedIn posting with a real-looking URL and an explanation
  stating an offer probability are flagged.
* **Path handling.** Tree assembly extracts `git archive` output with tarfile's `data` filter (no absolute
  paths, no parent escapes, no links out). Lane D reads a fixed list of artifact file names. Output and
  workspace directories follow `ctint/outdir.py`.

Not covered: authentication (there is no server beyond a loopback static preview and lane A's loopback API),
dependency supply chain, DNS-level SSRF (no live fetching exists), a hostile local user racing the checks.

## 5. Feature-by-feature acceptance checklist

"Auto" = asserted by the browser journey in the integration run. "Manual" = for the owner to try; lane D's
own suite (159 unit, 48 browser tests) covers these on fixtures, not on this run's artifacts.

| # | Screen | Route | What to confirm | Auto | Manual |
|---|---|---|---|---|---|
| 1 | Overview | `#/` | synthetic-demo and offline badges; mode banner names the data source | ✔ banner | ☐ |
| 2 | Discover Jobs | `#/jobs` | 160 postings; provenance, capture date, unknowns shown; search, filters, sort, duplicate collapse, cards / table | ✔ | ☐ filters |
| 3 | Matching | `#/matching` | ranking labelled as a relative heuristic, never a probability; components, unknown ≠ 0, missing and unsupported skills, refusals | ✔ | ☐ |
| 4 | Profile Evidence | `#/evidence` | 9 claims with truth state and visibility; unverified and contradicted claims blocked from export. In pipeline mode evidence references show as unresolved: lane A's evidence registry is not exchanged yet (section 7) | — | ☐ |
| 5 | Resume Studio | `#/resume` | each line traces to a claim; self-reported lines flagged; export blocked until the exact text is approved | ✔ | ☐ edit / approve (fixture mode) |
| 6 | Applications | `#/applications` | timeline saved → drafted → approved → submitted; "submitted" is the user's own report; nothing is sent | ✔ | ☐ |
| 7 | Experiments | `#/experiments` | locked preregistered design, assignment list, result with uncertainty; `INCONCLUSIVE` shown as such, no winner | ✔ | ☐ new experiment (fixture mode) |
| 8 | Career Atlas | `#/atlas` | distortion banner (26% variance kept), candidate + scenario + path on one projection, quality filter | ✔ | ☐ |
| 9 | Career Simulator | `#/simulator` | hypothetical scenario, missing evidence, effort alternatives, "not a hiring probability" | ✔ | ☐ |
| 10 | Settings | `#/settings` | theme and motion; export / erase of local data; data source in use; adapter table with accepted and rejected record counts per contract | ✔ reached | ☐ |

## 6. What to test manually

1. Launch as in section 2 and walk the ten screens in both URLs (pipeline artifacts, then fixtures).
2. Keyboard only: Tab from the address bar. First stop is the skip link; every control must be reachable
   and operable with Enter, Space and arrow keys; focus must always be visible.
3. In fixture mode: build a résumé variant, try to export before approval (must be blocked), approve,
   record an application, design and lock an experiment, try to edit it after the lock (must be refused).
4. Look for any sentence that reads as a probability of an offer or interview. There should be none.
5. Disconnect the network and reload: the app must still work.
6. Open `artifacts/provenance.json` and compare the commit ids with section 1.
7. Try to break the launcher: `python -m ctint.runner --repo .. --out .` must refuse with exit code 2.
8. Judge the product: are the explanations understandable, is anything misleading, what is missing.

## 7. Known limitations and unsupported capabilities

* Synthetic data only. `user-import` of real data is disabled while the repository is public.
* No live job sources, no network code, no accounts, no server-side storage, single user, one browser
  profile. Pipeline-artifact mode is read-only.
* Ranking weights, taxonomy priors, effort hours and every threshold in the simulator are authoring
  assumptions, not validated. Scores are relative heuristics on fictional data.
* Outcomes in experiments are simulated. One person is experimenter, subject and reporter; the software
  shows intent, not human presence.
* UMAP is not executed. English-only skill extraction with a 58-term lexicon.
* The UI stack is Vite + React rather than the originally named Next.js (lane D decision D-01, owner gate).
* The UI does not display or verify `provenance.json`.
* Evidence items are not exchanged between lanes: lane A writes them to `claims_vault.json`, lane D has no
  adapter for that file, so the Profile Evidence screen shows evidence references as unresolved in
  pipeline mode (it says so; nothing is invented). There is no frozen contract for evidence items.
* Harness isolation is an environment scrub, not a sandbox. The browser stage needs an installed Chrome;
  it has only been run on Windows.
* Three optional interface questions from D to B stay open (per-unit outcome state, exposure, single-arm
  interval): D shows "not provided" instead of guessing. Contradiction scope in lane C is a policy question.

## 8. Proposed merge order and rollback (owner action; nothing is merged)

Lanes changed disjoint paths (ownership check: 0 violations each), so the merges cannot conflict.

1. Tag the base: `git tag pre-integration-b01 bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf`.
2. Merge **#11 (E)** first: CI, guards and the harness then run on everything that follows.
3. Merge **#7 (A)**, **#8 (B)**, **#9 (C)**, **#10 (D)** in that order, each as a merge commit (no squash,
   so one revert undoes one lane). After each merge run the runner against `main` with the remaining
   lanes still pinned; do not continue on anything other than PASS.
4. After the last merge run `python -m ctint.runner --repo .. --base origin/main --allow-setup --pass-env
   PW_CHANNEL=chrome` once more and update `acceptance-pins.json` if the launcher is to track `main`.

Rollback: `git revert -m 1 <merge commit>` in reverse order (D, C, B, A, E). Because each lane owns its
directories, reverting one lane removes only its own code; consumers downstream of it then fail their
stages loudly instead of silently using stale data. Full rollback: reset the protected branch to the tag
through a revert PR. Do not delete lane branches until the owner has accepted the result.

## 9. Owner decision gates

Nothing below was decided or changed by an agent.

| # | Decision | Recommendation | Trade-off / consequence | Safest default |
|---|---|---|---|---|
| 1 | Repository visibility | Make it **private** before any real profile, CV or application data exists anywhere near it | Private ends public CI minutes on some plans and public review; public keeps every fixture and commit world-readable forever | Stay public **and synthetic-only**; real data stays blocked |
| 2 | Single-user vs multi-user (E-F004) | Declare **single-user by design** for this phase and write it into the contracts' descriptions | Adding `subject_id` to three frozen contracts later is a breaking RFC; skipping it now keeps scope small but makes any shared deployment unsafe | Single-user, local only; no shared or hosted deployment |
| 3 | Frozen contract changes / promotions (E-F001–E-F005; A's `a-ext` / `a-match`, B's `b.v1`, C's projection and scenario-output shapes; the lane manifest) | Promote the shapes D already validates strictly, one RFC per producer, after owner acceptance testing | Promotion freezes shapes that have had one batch of use; not promoting leaves cross-lane shapes guarded only by runtime validators | Promote nothing yet; keep E's semantic rules as the enforcement point |
| 4 | Baseline reference components (`fixtures/synthetic_jobs.json`, `tools/reference_engine.py`; E-F006–E-F008) | Retire them in favour of lane A's corpus and ranker, in a separate small PR | They are not contract-valid and the engine scores NaN as best; removing them changes `main`'s baseline CI job | Leave in place, marked as known-defective in the ledger |
| 5 | Live ATS sources | Do not approve yet | Real postings bring terms-of-service, rate-limit and SSRF exposure and end the "fictional only" guarantee | Not approved; `LiveFetchDisabled` stays |
| 6 | Paid APIs / external integrations | None needed for this phase | Any LLM or embedding API sends data off the machine and costs money | None; tripwire stays on |
| 7 | Personal-data ingestion | Only after 1 and 2, with an explicit data-handling note | Irreversible once committed to a public history | Blocked |
| 8 | Merges, deployment, release | Merge in the order of section 8 after your own acceptance test and the independent review of `be5eb2f` | Merging before review bakes in whatever the same-provider reviews missed | Keep all PRs draft; no deployment |
| 9 | Browser policy for integration runs | Keep "installed Chrome via `--pass-env PW_CHANNEL=chrome`" | Alternative: `npx playwright install chromium` downloads the full Chromium build pinned by Playwright 1.65 from Playwright's CDN and would also give Linux CI a browser | No download |
| 10 | UI framework (D-01: Vite + React instead of Next.js) | Accept: the app is static, offline and has no server | Switching to Next.js is a rewrite of the app shell with no functional gain for an offline tool | Keep as built |
| 11 | CI integration job gating | Make it gating only once CI has a browser (depends on 9) | Gating without a browser would block every PR on a stage CI cannot run | Non-gating, as now |

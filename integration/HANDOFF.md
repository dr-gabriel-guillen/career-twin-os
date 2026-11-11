# HANDOFF — CAREER-TWIN-E, batch CT-2026-11-10-B01

| | |
|---|---|
| SESSION_ID | `CAREER-TWIN-E` |
| Issue / PR | [#6](https://github.com/dr-gabriel-guillen/career-twin-os/issues/6) / [#11](https://github.com/dr-gabriel-guillen/career-twin-os/pull/11) (draft) |
| Branch | `agent/ct-e-integration` |
| BASE_SHA | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` (`main`) |
| SOURCE_HEAD | see the DELIVERED comment on #6 (this file is committed in that SHA) |
| Changed paths | `integration/**`, `.github/workflows/ci.yml`, `docs/coordination/STATUS_BOARD.md` — nothing outside E's ownership |

> **Superseded for current status by [`ACCEPTANCE.md`](ACCEPTANCE.md) (consolidation, 2026-11-11).** Since this
> hand-off: the `--out` deletion defect was fixed, the browser stage runs inside the runner, all ten stages
> PASS at newer pins, and two gaps in lane E's validators were closed. The text below is the batch
> hand-off as written at `fcb5572` and is kept for the record.

No data exposure: every fixture, posting, profile, claim and outcome used or produced by lane E is fictional
(`Fictional …` employers, RFC 2606 `.example` hosts, `candidate-synthetic-001`). No personal data, credentials,
LinkedIn data, paid API calls, outbound messages, merges or deployments.

## 1. What lane E delivered

| WS | Deliverable | Where | Evidence |
|---|---|---|---|
| 1, 11 | STARTED, dependency matrix, two full reconciliations of #1–#6, #12 and PRs #7–#11 | Issue comments | links in §5 |
| 2 | Draft 2020-12 conformance for the six frozen contracts; strict RFC 3339 checker; semantic rules for invariants the schemas do not encode | `ctint/contracts.py`, `fixtures/contracts/` | 33 valid, 98 schema-negative, 43 schema-gap fixtures; 235 tests |
| 3 | CI: format, lint (incl. security rules), `mypy --strict`, isolated tests with in-process network block, secret/PII/paid-API/workflow guards, non-gating integration run | `.github/workflows/ci.yml`, `ctint/sec_*.py`, `ctint/netguard.py` | GitHub Actions runs on every push (see PR #11 checks) |
| 4 | Disposable pinned-SHA runner: detached temporary worktree, owned-path overlay, static gates, lane tests, ten-stage pipeline, two-phase input-sensitivity probes, full stage logs, reproducible report; never moves refs | `ctint/runner.py`, `ctint/e2e.py`, `lane-manifest.schema.json` | 11 runner self-tests + 6 probe tests; manifest adopted by all four lanes |
| 5 | Threat model with a test per threat; CSV, SSRF, injection, secret, paid-API, CI probes | `THREAT_MODEL.md`, `ctint/sec_*.py`, `reviews/` | 175 security + 13 CI-hygiene tests; 2 review scripts |
| 6 | Contract review and RFC: 10 baseline findings, exact ledger | `known_findings.json`, RFC on #1 | ledger recomputed in both directions by `test_baseline_findings.py` |
| 7 | End-to-end synthetic pipeline over real lane code | runner + validators | §2 |
| 8 | Experiment integrity attacks; independent ITT oracle | `ctint/experiment_integrity.py`, `ctint/stats.py` | false-alarm 0.053 over 300 null worlds, power 1.000 at 0.15 vs 0.50, n = 200 |
| 9 | Atlas quality: independent trustworthiness / continuity / k-NN, reference PCA, fit leakage, 2D-neighbour claims | `ctint/atlas_quality.py` | lane C's diagnostics reproduced within tolerance |
| 10 | Scale and fuzz: 1,200 jobs, 100 applications, 20 experiments, 10 scenarios, 4,040 single-fault mutations, 18,000 random documents | `ctint/synth.py`, `ctint/fuzz.py` | `test_fuzz_scale.py` |
| 12 | This handoff, final matrix, owner decision list, demo instructions | this file | — |

## 2. Final integration matrix

**Overall: 9 of 10 stages PASS on real lane code inside lane E's disposable runner; `ui.e2e` PASSES on the
same run's artifacts only outside E's scrubbed environment (browser start-up, see below). E's runner verdict
for the run is therefore `FAIL`, and lane E does not round that up to PASS.**

Run `final5` started 2026-11-11T05:09:07Z · verdict **FAIL** · stages {'PASS': 9, 'FAIL': 1} · hermetic: False · passed env: {'PW_CHANNEL': 'chrome'}

Reproduce (from `integration/`):

```
cd integration && python -m ctint.runner --repo .. --base bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf --lane A=82427d995da1df2893c24fcee142cc106f7b087f --lane B=755ccae1b4208ac96cbc39d9c4071fa4188c2224 --lane C=f76e24c869bad2819810ac06e8b31f067aacf338 --lane D=9c24d101e67dfbd01f3516c70e50dc17478072be --seed 7 --count 150 --allow-setup --pass-env PW_CHANNEL=chrome
```

### By lane (pinned SHAs)

| Lane | SHA | Gate status | Ownership | Secrets | Paid / outreach | Declared tests in E's worktree |
|---|---|---|---|---|---|---|
| CAREER-TWIN-A | `82427d995da1` | **PASS** | 0 | 0 | 0 | `matching` PASS, `connectors` PASS |
| CAREER-TWIN-B | `755ccae1b420` | **PASS** | 0 | 0 | 0 | `lane-b` PASS |
| CAREER-TWIN-C | `f76e24c869ba` | **PASS** | 0 | 0 | 0 | `atlas` PASS |
| CAREER-TWIN-D | `9c24d101e67d` | **PASS** | 0 | 0 | 0 | `unit` PASS |

### By integration path (ten-stage synthetic pipeline)

| # | Stage | Lane | Status | Evidence |
|---|---|---|---|---|
| 1 | `jobs.import` | A | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 2 | `profile.claims` | A | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 3 | `match.rank` | A | **PASS** | lane E pinned jp_f124d8a533cd8ecdca70 (rank 1) |
| 4 | `features.vectors` | A | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 5 | `resume.variant` | B | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 6 | `application.record` | B | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 7 | `experiment.run` | B | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 8 | `atlas.project` | C | **PASS** | contract + cross-stage validators clean; input-sensitivity probe clean |
| 9 | `simulator.scenario` | C | **PASS** | updated its own lane's sidecar file(s) ['atlas.pca.json'] (allowed; not a contract artifact) |
| 10 | `ui.e2e` | D | **FAIL** | browser could not start in E's scrubbed environment (`browserType.launch`: Chrome refuses DevTools from a profile under the scrubbed `LOCALAPPDATA`); D's UI was not exercised in this run — see below |

`ui_report.json`: tool playwright, keyboard_only False, data_source 'unknown', mock_sources [], tests ['fail'], input method: 'keyboard events only; the runner sets focus programmatically, then sends key presses'

### `ui.e2e` outside the scrubbed environment
`reviews/ui_stage_two_environments.py` re-ran lane D's own stage command on the artifacts of run `final5`:

| Variant | Browser | Result |
|---|---|---|
| E's scrubbed env + local Playwright cache | bundled Chromium | **cannot start**: revision `chromium_headless_shell-1254` (needed by D's Playwright 1.65) is not cached locally |
| E's scrubbed env + `PW_CHANNEL=chrome` (run `final5`) | installed Chrome | **cannot start**: Chrome refuses DevTools remote debugging from a profile under the scrubbed `LOCALAPPDATA` |
| operator's env + `PW_CHANNEL=chrome` (D's documented method) | installed Chrome | **PASS**: `keyboard_only: true` (0 pointer events), `data_source: "artifacts"`, `mock_sources: []`, 0 external requests; 160 postings shown, B's variant, timeline, locked experiment and verdict, C's distortion banner and scenarios rendered without recomputation. E's `v_ui` validator: 0 problems. |

Keyboard reachability: D sets focus programmatically and then sends key presses, so per E's rule (posted on #5)
tab-order reachability is **UNKNOWN**, not PASS.

### Status by layer
| Layer | Status | Basis |
|---|---|---|
| Frozen contracts (6) | **PASS** for every artifact in the run; 10 contract gaps OPEN (section 3) | Draft 2020-12 + semantic rules |
| A: import, claims, ranking, vectors | **PASS** | 4 stages + 2 test suites at `82427d9`; A-F1 to A-F3 fixed and verified |
| B: résumé, application, experiment | **PASS** | 3 stages + 131 tests at `755ccae`; B-F1 to B-F3 fixed and verified; analysis STOPPED_EARLY / INCONCLUSIVE with no test, as preregistered |
| C: atlas, simulator | **PASS** | 2 stages + 261 tests at `f76e24c`; PCA, variance, trustworthiness and continuity reproduced independently |
| D: UI | **PASS outside the scrubbed env / FAIL inside**; keyboard reachability **UNKNOWN** | above |
| Cross-user export isolation | **UNKNOWN** | E-F004 (no subject field) |
| Security probes | **PASS** on lane code (0 secrets, 0 paid/outreach, 0 ownership violations, no canary leaks) | section 3, `THREAT_MODEL.md` |
| CI | **PASS** on every E push since `a2e50e3` (GitHub Actions, Linux, Python 3.14) | PR #11 checks |

## 3. Findings raised by lane E and their state

| ID | Lane | Sev | Finding | State at hand-off |
|---|---|---|---|---|
| E-F001…E-F010 | baseline / owner | 4 high, 5 medium, 1 low | frozen-contract gaps, non-contract-valid shared fixtures, reference-engine defects (`known_findings.json`) | OPEN — owner decision; A acknowledged F001–F003, F006, F007 |
| A-F1 | A | high | one unclosed `<svg …>` erases all extracted skills (Ashby, Lever) and evades the phantom-posting check | **FIXED** in A `82427d9` (fail-closed refusal); verified by `reviews/probe_lane_a_injection.py` (10 findings at `fff2cd2`, 0 at `82427d9`) |
| A-F2 | A | medium | skills extracted from free-text prose; injected text adds required skills | **FIXED** in A `82427d9` (prose adds at most preferred skills); verified |
| A-F3 | A | medium | unique injected text defeats de-duplication | **FIXED** in A `82427d9`; verified (remaining promotion is a duplicate whose canonical was refused) |
| B-F1 | B | medium | experiment stage reported success on journaled state when `jobs.json` changed | **FIXED** in B `755ccae` (`InputDrift`, exit 2; observed live) |
| B-F2 | B | low | fixture-hash test failed on `core.autocrlf=true` checkouts | **FIXED** in B `755ccae`; B's suite passes in E's Windows worktree |
| B-F3 | B | low | `stratum_key` not injective when level names contain `\|` or `=` | **FIXED** in B `755ccae` (registry refuses them) |
| D-gap | D | — | UI E2E did not read pipeline artifacts | **FIXED** in D `9c24d10` (`e2e:integration` renders the run's artifacts) |
| C/D | C | — | scenario and atlas fitted different models (found by D) | **FIXED** in C `f76e24c`; E now checks `model_ref.projection_id` (proposed by D) |

### Lane E's own mistakes found by reviewing real lane output (all fixed, all regression-tested)
1. RFC 3339 regex accepted a trailing newline.
2. Paid-API tripwire flagged lane B's guard test (a list of forbidden strings) as LinkedIn automation.
3. CI guard was a `grep` that matched its own step name (CI run 42955735573).
4. Probability lint flagged lane A's correct disclaimer "NOT an offer, interview or hiring probability".
5. Job validator rejected lane A's fictional ATS-shaped postings by source enum instead of fictional marking.
6. Validators did not accept lane B's native résumé, assignment and report shapes as promised on #3.
7. Estimand check missed "ITT" written as an abbreviation.
8. "Winner" check fired on negated text ("no winner is declared").
9. Atlas validator rejected lane C's documented `unplaced[]` vectors.
10. Required n ≥ 40 for an experiment, overriding the producer's preregistered design.
11. Called mailbox-ingested replies (`actor: "import"`) observational; only an imported submission is.
12. Required an interval even when lane B correctly withheld the result after an early stop.
13. Runner could not start `npm` on Windows with `shell=False`.
14. Injection oracle treated a legitimate refusal of a flagged posting as manipulation (agreed with A on #2).
15. Probe called lane B's correct `InputDrift` refusal a failure.
16. Tamper check failed lane C for refreshing its own sidecar.
17. Setup environment kept the dead proxy, so lane D's `npm ci` could not reach the registry.
18. E's own stub cited a `private` claim in a résumé (found by lane D).
19. Report kept only a 1,500-character log tail and lost lane D's real error; full stage logs are now saved.

## 4. Owner decision list
1. **Make the repository PRIVATE** before any personal-data work (hard gate, unchanged).
2. **E-F004:** single-user by design (document it) or add a subject/owner field to `ApplicationEvent`,
   `ExperimentSpec`, `AtlasPoint` before Phase 2. Cross-user isolation is UNKNOWN until then.
3. **E-F006/E-F007:** replace or retire `fixtures/synthetic_jobs.json` and `tools/reference_engine.py`;
   lane A's corpus and ranker already replace them for lane purposes.
4. **E-F001–E-F003, E-F005:** tighten frozen contracts (`$id`, UTC pattern, `minLength`, typed scenario outputs)
   via RFC, or accept E's semantic rules as the enforcement point.
5. Whether `integration/lane-manifest.schema.json` (adopted by all lanes) becomes a frozen contract.
6. Whether CI's integration job should become gating once all ten stages pass.
7. Browser policy for integration runs: let lane D add `npx playwright install chromium` to its manifest `setup` (downloads a browser), or let E's runner pass the operator's browser profile location through. Until then `ui.e2e` passes only outside the scrubbed environment.
8. Merge order and release: lane E approves nothing; draft PRs are not approval.

## 5. Demo (offline, about three minutes on a laptop)
```bash
git clone https://github.com/dr-gabriel-guillen/career-twin-os.git && cd career-twin-os
git fetch origin && git checkout agent/ct-e-integration
python -m pip install -r integration/requirements.txt
cd integration
python -m pytest                                   # lane E's own suite, network blocked
python -m ctint.runner --repo .. --base origin/main   # pinned-SHA integration of all lanes
# open integration/.work/run-<utc>/report.md
python -m ctint.runner --repo .. --base origin/main --allow-setup   # adds lane D's npm ci + browser E2E (non-hermetic)
```
Use the exact `reproduce` line from a report to re-run the same SHAs.

## 6. Known limits of lane E's evidence
* Same-provider review: lane E and the lanes it reviews run on the same model family. This is not independent
  external validation.
* The runner's isolation is an environment scrub (no credentials, dead proxy, empty HOME), not a sandbox.
* Pattern-based scanners (secrets, paid APIs, probability wording, injection) can be evaded deliberately.
* The SSRF classifier is lexical and offline; DNS rebinding is out of scope until live fetching is authorised.
* Experiment checks verify bookkeeping and claims against E's own ITT tally; they do not re-run lane B's
  permutation test.

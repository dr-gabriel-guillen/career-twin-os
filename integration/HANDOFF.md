# HANDOFF — CAREER-TWIN-E, batch CT-2026-11-10-B01

| | |
|---|---|
| SESSION_ID | `CAREER-TWIN-E` |
| Issue / PR | [#6](https://github.com/dr-gabriel-guillen/career-twin-os/issues/6) / [#11](https://github.com/dr-gabriel-guillen/career-twin-os/pull/11) (draft) |
| Branch | `agent/ct-e-integration` |
| BASE_SHA | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` (`main`) |
| SOURCE_HEAD | see the DELIVERED comment on #6 (this file is committed in that SHA) |
| Changed paths | `integration/**`, `.github/workflows/ci.yml`, `docs/coordination/STATUS_BOARD.md` — nothing outside E's ownership |

No data exposure: every fixture, posting, profile, claim and outcome used or produced by lane E is fictional
(`Fictional …` employers, RFC 2606 `.example` hosts, `candidate-synthetic-001`). No personal data, credentials,
LinkedIn data, paid API calls, outbound messages, merges or deployments.

## 1. What lane E delivered

| WS | Deliverable | Where | Evidence |
|---|---|---|---|
| 1, 11 | STARTED, dependency matrix, two full reconciliations of #1–#6, #12 and PRs #7–#11 | Issue comments | links in §5 |
| 2 | Draft 2020-12 conformance for the six frozen contracts; strict RFC 3339 checker; semantic rules for invariants the schemas do not encode | `ctint/contracts.py`, `fixtures/contracts/` | 33 valid, 98 schema-negative, 43 schema-gap fixtures; 235 tests |
| 3 | CI: format, lint (incl. security rules), `mypy --strict`, isolated tests with in-process network block, secret/PII/paid-API/workflow guards, non-gating integration run | `.github/workflows/ci.yml`, `ctint/sec_*.py`, `ctint/netguard.py` | GitHub Actions runs on every push (see PR #11 checks) |
| 4 | Disposable pinned-SHA runner: detached temporary worktree, owned-path overlay, static gates, lane tests, ten-stage pipeline, reproducible report; never moves refs | `ctint/runner.py`, `ctint/e2e.py`, `lane-manifest.schema.json` | 8 runner self-tests on a throwaway multi-lane repo; adopted by all four lanes |
| 5 | Threat model with a test per threat; CSV, SSRF, injection, secret, paid-API, CI probes | `THREAT_MODEL.md`, `ctint/sec_*.py` | 187 security tests |
| 6 | Contract review and RFC: 10 baseline findings, exact ledger | `known_findings.json`, RFC on #1 | ledger recomputed in both directions by `test_baseline_findings.py` |
| 7 | End-to-end synthetic pipeline over real lane code | runner + validators | §2 |
| 8 | Experiment integrity attacks; independent ITT oracle | `ctint/experiment_integrity.py`, `ctint/stats.py` | false-alarm 0.053 over 300 null worlds, power 1.000 at 0.15 vs 0.50, n = 200 |
| 9 | Atlas quality: independent trustworthiness / continuity / k-NN, reference PCA, fit leakage, 2D-neighbour claims | `ctint/atlas_quality.py` | lane C's diagnostics reproduced within tolerance |
| 10 | Scale and fuzz: 1,200 jobs, 100 applications, 20 experiments, 10 scenarios, 4,040 single-fault mutations, 18,000 random documents | `ctint/synth.py`, `ctint/fuzz.py` | `test_fuzz_scale.py` |
| 12 | This handoff, final matrix, owner decision list, demo instructions | this file | — |

## 2. Final integration matrix

FINAL_MATRIX_PLACEHOLDER

## 3. Findings raised by lane E and their state

| ID | Lane | Sev | Finding | State at hand-off |
|---|---|---|---|---|
| E-F001…E-F010 | baseline / owner | 4 high, 5 medium, 1 low | frozen-contract gaps, non-contract-valid shared fixtures, reference-engine defects (`known_findings.json`) | OPEN — owner decision; A acknowledged F001–F003, F006, F007 |
| A-F1 | A | high | one unclosed `<svg …>` erases all extracted skills (Ashby, Lever) and evades the phantom-posting check | OPEN — reproduces on A `fff2cd2` (`reviews/probe_lane_a_injection.py`) |
| A-F2 | A | medium | skills extracted from free-text prose; injected text adds required skills | OPEN — reproduces on A `fff2cd2` |
| A-F3 | A | medium | unique injected text defeats de-duplication | OPEN — reproduces on A `fff2cd2` |
| B-F1 | B | medium | experiment stage reported success on journaled state when `jobs.json` changed | see §2 for the latest run |
| B-F2 | B | low | fixture-hash test failed on `core.autocrlf=true` checkouts | B's suite passes in E's Windows worktree from `755ccae` |
| B-F3 | B | low | `stratum_key` not injective when level names contain `|` or `=` | OPEN |
| D-gap | D | — | UI E2E did not read pipeline artifacts | D added an artifacts route (`e2e:integration`); see §2 |

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
7. Merge order and release: lane E approves nothing; draft PRs are not approval.

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

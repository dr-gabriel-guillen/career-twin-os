# HANDOFF — CAREER-TWIN-B, batch CT-2026-11-10-B01

| Field | Value |
| --- | --- |
| SESSION_ID | `CAREER-TWIN-B` |
| BATCH_ID | `CT-2026-11-10-B01` |
| Branch | `agent/ct-b-experiments` · draft PR [#8](https://github.com/dr-gabriel-guillen/career-twin-os/pull/8) · Issue [#3](https://github.com/dr-gabriel-guillen/career-twin-os/issues/3) |
| BASE_SHA | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` (`main`) |
| Code SHA | `6db28b05b1043a5ac2a688353a13485a7ee163d2` (repair cycle 2 `00e8ffb` + cross-lane review fixes) |
| Fixtures SHA | `bdc4e94bfa2ed834a713f938e4f671d7b6101240` (generated from `00e8ffb`; the later changes touch only the e2e runner, strata-name validation and tests, none of which feed the fixtures) |
| SOURCE_HEAD | the commit that adds this file; quoted in the DELIVERED comment on #3 |
| Changed paths | `packages/applications/**`, `packages/experiments/**` only (no shared file, no contract touched) |

## Reproduce

```
python packages/experiments/run_tests.py                     # 131 tests, warnings are errors, stdlib only
python packages/experiments/tools/build_fixtures.py          # regenerate fixtures/v1 (~5 min); compare MANIFEST.json
python packages/experiments/tools/build_offline_package.py   # dist/careertwin-lane-b-0.1.0.zip
python packages/experiments/tools/e2e_stage.py resume --artifacts DIR   # lane E stages, see integration-manifest.json
```

Environment used: Python 3.14.6 on Windows 11. No third-party package is needed at runtime. When `jsonschema` is
installed, one test cross-checks lane B's contract checker against it.

## Evidence

| Item | Result |
| --- | --- |
| Test suite | **131 run, 0 failed, 0 errors, 0 skipped** (`run_tests.py`) |
| Offline package | 53 files, sha256 `97a4aa4f37cfd41ebfb22a03a527db6aa34bbabad249870e5f0e9046c5f6ab95`; identical across two builds; 131/131 pass from the unpacked archive |
| Synthetic cohort | 24 experiments, 3,022 fictional applications (2,782 randomised, 240 historical/observational), 113 duplicate imports collapsed |
| Cohort verdicts | 7 EVIDENCE_FOR_DIFFERENCE (all on true effects ≥ +0.20), 15 INCONCLUSIVE (including all 4 A/A negative controls, 2 clustered nulls, 3 underpowered real effects, the early stop), 2 INTERIM_NO_CLAIM; **0 false alarms** |
| Held-out cohort (seed 911) | 7 / 15 / 2; **0 false alarms** |
| Full-pipeline replication | 3 false alarms in 72 null experiments = 0.042 [0.014, 0.115]; 17 of 64 large-effect experiments missed (the conservative failure) |
| Decision-rule calibration (1,000 sims each, α = 0.05) | false-claim rate 0.035 (40/arm), 0.005 (15/arm), 0.041 (ICC 0.3), 0.041 (ICC 0.5), 0.000 (20% missing); the naive application-level test gives 0.134 and 0.202 on the clustered settings; power 0.84 at +0.25 with 60/arm |
| Observational confounder demo | the "polished" résumé looks +14 points better (0.269 vs 0.127) with a true effect of exactly 0; the report says OBSERVATIONAL_ONLY, gives no test, and the within-warmth split shrinks the gap |
| Integration on lane A's real output (A at `2d933fb`, detached worktree, read-only) | all three stages exit 0 once the synthetic claims are marked `shareable`; the A/A experiment reaches FINAL / INCONCLUSIVE (168 postings, p = 0.52); output changes when half of the jobs are removed |

Fixture hashes (first 16 hex digits of sha256; full values in `fixtures/v1/MANIFEST.json`): adapter_methods `720d7695f44c499b`,
applications_sample `60d6dc5d136549f6`, calibration `41a11940925b4f16`, experiment_reports `3675e98bb6cbac13`,
experiments `0691133bf7bd5444`, full_pipeline_replication `475cccdf46a7b7db`, heldout_cohort `ba30d5348b94366e`,
observational `d0147539bb82a8f4`, portfolio `ba6c606d1475a64d`, roundtrip `ed9cad43d389f458`, scorecard `cbb17e119e88133a`.

## The 12 workstreams

| # | Workstream | State |
| --- | --- | --- |
| 1 | Schemas / A's IDs | Done. A acknowledged all five assumptions on #2 and asked for two adjustments, both implemented: dedup-member crossover keys, and the claim-id wording. |
| 2 | Event store | Done. Hash-chained, append-only; corrections are new events; projection; lifecycle flags never invent a rejection. |
| 3 | Résumé variant engine | Done. Verbatim backed claims only; refusals; missing-proof error; immutable versions and diff; manual-overwrite flag; approval-gated export, refused on stale evidence. |
| 4 | Workflow state machine | Done. `system` cannot approve, submit or withdraw; `submitted` = the user's own report. |
| 5 | Experiment registry | Done. Explicit question, eligibility, unit, strata, metric, window, fixed-horizon stopping, power; user-approved immutable lock; consent. |
| 6 | Assignment engine | Done. Identity-keyed seeded randomisation, journaled before exposure; no crossover or double enrolment; attrition retained. |
| 7 | Outcome model | Done. Right-censoring, pending exposure, late, ambiguous and duplicate responses. |
| 8 | Analysis | Done. ITT stratified permutation test, Newcombe / t intervals, Holm, cluster-level analysis, worst-case and uncorrected-history sensitivity, blinded interim, result of record, observational labelling, portfolio multiplicity. |
| 9 | Synthetic demonstration | Done. Exceeds the brief: 3,022 applications, 24 experiments, negative controls, confounders, INCONCLUSIVE cases. |
| 10 | API + D adapter + roundtrip | Done. `LaneBApi` b.v1, `fixtures/v1/roundtrip.json`, integration manifest and runner for E. |
| 11 | Security | Done for B's scope. No network or subprocess imports (AST-checked); sockets blocked during a full run; hostile HTML/Markdown escaped; replay, altered plan, invalid consent and ledger truncation all refused or detected. |
| 12 | Red team + release candidate | Done. Two independent adversarial reviews and two repair cycles; held-out cohort; calibration; offline package. |

## Adversarial review cycles

Both reviewers were fresh agents of the same model family with no shared context. Per AGENTS.md this is **not**
independent external expert validation.

**Cycle 1** (code `9be8096`): 3 critical, 4 high, 5 medium, 7 low findings. The most serious:
- **C1:** a future `as_of` unlocked a false claim.
- **C2:** a backdated submission closed windows instantly.
- **C3:** permuted blocks made the next arm 90% predictable, so selective enrolment gave 35% false claims.
- **H3:** read models aliased a stored résumé.

All were fixed in `ea01acb`. The reviewer's scripts were re-run against the fix, and 19 regression tests were added (`tests/test_review_regressions.py`).

**Cycle 2** (code `ea01acb`): 1 critical, 3 high, 4 medium, 6 low findings. The most serious:
- **C-1:** clusters could grow in response to observed outcomes (47% false claims).
- **H-1:** void-then-record defeated the corrections check.
- **H-2:** an appended journal line replaced the result of record.
- **H-3:** lane A duplicates bricked job and cluster designs.
- **M-1:** a past `as_of` could be chosen as the result of record.

All fixed in `00e8ffb`. The reviewer's scripts were re-run against the fix, and 11 regression tests were added (`tests/test_review2_regressions.py`).

**Cross-lane review** (on #3, B at `84757ed`):
- **A:** in E's pipeline layout, B ignored A's `jobs_ext.json` sidecar and lost employer ids and duplicate links.
- **E B-F1:** a rerun on changed inputs silently returned the journaled experiment.
- **E B-F2:** the fixture hash test failed on CRLF checkouts.
- **E B-F3:** stratum keys were not injective.

All fixed in `6db28b0`, with tests in `tests/test_crosslane_feedback.py`. On A's real pipeline output (A `fff2cd2`), all 160 records now use `ext.employer.id`, 16 duplicates keep their members, and B sees 25 employers, as A does.

Real defects caught by B's own tests during development: Markdown `javascript:` links left intact; a comparison
without a usable stratum silently dropped from the withheld reasons; the disk re-read in `verify_all` comparing journal
names (a false positive on every workspace); three bugs in the first version of the e2e runner.

## Interfaces and cross-lane state (read at the end of the batch)

- **A (#2):** ACKNOWLEDGED both ways. Open finding for A: its synthetic profile has only `private` claims, so no résumé
  can be produced; A's own `resume-draft` export is empty too. Requested action: make some claims `shareable`. B will
  not relax its rule.
- **D (#5):** interface published (`LaneBApi` b.v1, shapes, rules). No ACK from D seen yet.
- **E (#3, #6):** E acknowledged B's shapes and reported `resume.variant` and `application.record` PASS on A's real data
  (B at `84757ed`). `experiment.run` passed every integrity check, but failed the input-drift probe (now fixed).
  B delivered the manifest and runner and answered E's three questions. One known policy
  difference: B's preregistered minimum can be under E's n = 30 threshold, and B asked E to keep flagging that.
- **Frozen contracts:** unchanged. B-owned shapes: `careertwin.b.application-event-ext.v1`,
  `careertwin.b.experiment-design.v1`, `careertwin.b.resume-variant.v1`, `careertwin.b.analysis-report.v1`,
  `careertwin.b.observational-summary.v1`, `careertwin.b.portfolio-summary.v1`.

## Known limitations and open risks (not hidden by the tests)

1. **One user is the experimenter, the subject and the outcome reporter.**
   - Interim reports are blinded, but `get_application` shows each application's arm.
   - The seed is in clear in the local `experiments.jsonl`. Identity-keyed allocation removes order-based prediction, but it cannot hide the seed from its owner.
   - Outcomes are self-reported or imported, never verified with employers.
2. **Anyone with file access can still write journal entries.**
   - Hash chains and anchors detect edits, deletions and truncation; a second result of record is refused.
   - But a forged *first* result of record written by someone who controls the files would be caught only by recomputation. It would show as `unchanged: false`, not as an integrity error.
   - Publishing anchors elsewhere is the mitigation.
3. **The approval gate proves intent in software, not human presence.** The UI (lane D) owns that guarantee.
4. **The stratum label is supplied by the caller**, not derived from the posting.
5. **The t-interval is approximate** for multi-stratum and clustered designs at small samples. The permutation test is the primary test.
6. **Simple randomisation balances arms only in expectation.** Designs smaller than about 15 units per arm are refused.
7. **The e2e seed is predictable** (derived from `--seed`). It is an integration negative control, not concealed allocation.
8. **One process per workspace.** There is no file locking.
9. **Not done here:** browser/UI rendering of these payloads (lane D), cross-lane CI runs (lane E), and any real data.

## Next work (precise)

1. Lane E: run `integration-manifest.json` at the SOURCE_HEAD and post PASS / FAIL / UNKNOWN per check; fix anything E finds.
2. Lane A: once A's synthetic profile has shareable claims, re-run the three stages on A's pinned SHA without the temp-copy workaround.
3. Lane D: confirm the adapter shapes, or post NEEDS-INTERFACE with JSON examples.
4. Owner decisions that remain owner-only: making the repository private before any personal data; merging; deployment.

## No-data-exposure statement

Every person, employer, posting, claim, résumé, outcome and history in this branch is fictional and generated by code
in this branch. URLs use the reserved `.invalid` TLD. No credentials, real e-mail addresses, LinkedIn data, CVs,
contacts or application history were used or committed. Nothing in lane B opens a network connection, submits an
application, sends a message or contacts anyone. No paid API was called, and nothing was merged or deployed.

# HANDOFF — CAREER-TWIN-A — CT-2026-11-10-B01

| Field | Value |
|---|---|
| SESSION_ID | CAREER-TWIN-A |
| BATCH_ID | CT-2026-11-10-B01 |
| Issue / PR | [#2](https://github.com/dr-gabriel-guillen/career-twin-os/issues/2) · draft [PR #7](https://github.com/dr-gabriel-guillen/career-twin-os/pull/7) · payload RFC [#12](https://github.com/dr-gabriel-guillen/career-twin-os/issues/12) |
| Branch | `agent/ct-a-data-matching` |
| BASE_SHA | `bc957fd7ed98ad1bf97eb5abc19d64b146de8dcf` (`main`) |
| SOURCE_HEAD | see the DELIVERED comment on #2: this file is committed *in* that commit, so it can't contain its own SHA |
| Changed paths | `connectors/**`, `packages/data-matching/**` only (lane A's owned paths; nothing in `contracts/`, `fixtures/`, `tools/` or other lanes) |

## What was delivered (12 workstreams)
| # | Workstream | Where | State |
|---|---|---|---|
| 1 | Baseline & schemas | `test_contracts.py`, vendored frozen schemas + drift check | done; baseline defect on `main` reported (shared fixture violates frozen contract) |
| 2 | Adapter framework | `connectors/ct_connectors/` Greenhouse, Lever, Ashby, native; recorded **synthetic** payloads in `connectors/fixtures/` | done; no network code; `LiveFetchDisabled` refuses |
| 3 | Normalization | `normalize.py`, `store.py` | done: provenance, versions, expiry, revocation, cross-source dedup, unknowns kept unknown |
| 4 | Synthetic corpus | `corpus.py`, `fixtures/corpus_seed20261110_n180.json` | done: 190 fictional postings from 47 captures, adversarial cases, seed-reproducible |
| 5 | Profile Evidence Vault | `vault.py` | done: derived status, evidence pointers + hashes, export gates, manual approval for public, ledger |
| 6 | Matching | `matching.py` | done: hard constraints first, 7 signals, near-term vs strategic composites, unknown ≠ 0 |
| 7 | Semantic interface | `retrieval.py` | done: BM25 baseline, offline hashed-embedding adapter, hybrid RRF, offline-only adapter guard, frozen benchmark |
| 8 | Explanation & counterfactual | `matching._explain` | done: components, missing/unsupported skills, refusals, sensitivity, counterfactuals, not-a-probability label |
| 9 | Adversarial testing | `test_adversarial.py`, `test_fuzz.py`, `test_review_cycle2.py` | done; two independent review cycles (see below) |
| 10 | API | `cli.py`, `httpapi.py` (loopback), `e2e.py` + `integration-manifest.json` | done; consumed by B, C, D and E in separate processes |
| 11 | Quality cycle | `benchmarks/` (frozen metrics, 1k/10k perf, mutation report), fuzzing | done |
| 12 | Handoff | this file, `README.md`, `docs/SOURCE_COMPLIANCE.md`, `examples/` | done |

## Reproduce
```bash
python -m pytest packages/data-matching connectors -q          # full suite
python packages/data-matching/run.py demo                      # end-to-end in one process
python packages/data-matching/tools/mutation_test.py           # ~15 min; writes benchmarks/mutation_report.json
python packages/data-matching/tools/freeze_benchmarks.py --perf 1000 10000
```
Exact test counts, mutation results and SHA are in the DELIVERED comment on #2.

## Review cycles and repairs
- **Cycle 1:** cross-lane feedback. B (ID semantics, synthetic profile could not produce a résumé), D (6 points: score_type, unknown components, provenance label, contradicted skills, empty example), C (provenance, vector fields), E (manifest, non-finite numbers, hostile text, SSRF list: 4 of 42 accepted, now 1 by decision), plus a 44-mutant run (43 killed; survivor covered).
- **Cycle 2:** an independent reviewer (fresh agent, not anchored on the author's tests) reproduced 11 defects, 4 of them high: revocation order dependence, ledger not binding state, bonus text read as salary, hourly pay read as annual. All fixed with regression tests (`test_review_cycle2.py`). E's differential injection probe found 3 more (A-F1 unclosed `<svg>` swallowing content, A-F2 prose adding required skills, A-F3 noise defeating dedup); their repairs are listed in the DELIVERED comment.
- **Same-provider caveat:** every reviewer in this batch (B, C, D, E, the independent agent) is a Claude model. Per AGENTS.md this is **not** independent external expert validation.

## Interface state
| Consumer | Shape | Status |
|---|---|---|
| B | `ext.employer.id`, `ext.dedup.members`, ProfileClaim semantics | ACKNOWLEDGED both ways (#2). Open: B's pipeline loader ignores `jobs_ext.json` (A's review finding on #3) |
| C | skill vectors (`a-api/0.1`, states, fetched_at, synthetic) | ACKNOWLEDGED and verified by C (#12) |
| D | JobRecord `a-ext/0.1`, MatchResult `a-match/0.1` | ACKNOWLEDGED (#12); D's UI is not yet integrated in E's pipeline (D's blocker, not A's) |
| E | integration manifest, 4 stages | all four stages **PASS** in E's run at `3994155` |

## Known limits / not done
- Live ATS fetching: not built by design (owner decision). Payload shapes come from vendor documentation as I know it, not re-verified online (`docs/SOURCE_COMPLIANCE.md`).
- Skill extraction depends on English headings and a 58-term lexicon; location parsing covers a fixed list of countries.
- Ranking weights and taxonomy priors are authoring assumptions; the benchmarks measure self-consistency on fictional data, not real-world relevance or hiring outcomes.
- Ledger truncation *after* the last state-changing event is only detectable against an externally published `ledger.head`; no anchor publication exists yet.
- The HTTP server is single-user and loopback-only, not production.
- `user-import` of real data stays disabled while the repository is PUBLIC.

## Owner decisions needed
1. Make the repository PRIVATE before any real profile or job-application data.
2. Approve (or not) live sources and their terms (Greenhouse, Lever, Ashby public boards).
3. Whether to promote A's `a-ext/0.1` / `a-match/0.1` schemas into `contracts/` (RFC #12).
4. Merge of PR #7. Nothing has been merged or deployed by this lane.

## No-data-exposure statement
All employers, postings, candidates, evidence and pay figures in this lane are fictional and generated (reserved `.example` hosts, `synthetic: true`). No personal data, credentials, LinkedIn data, real CVs or application history were read, created or committed. No network requests, paid APIs, outreach, applications, merges or deployments were made by lane A's code or by this session on its behalf. The only external service used was GitHub (issues, PR, branch pushes) on the owner's repository.

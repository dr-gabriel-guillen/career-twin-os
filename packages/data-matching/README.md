# CAREER-TWIN-A — Job Data, Evidence Vault & Matching

Lane A of CareerTwin OS (batch `CT-2026-11-10-B01`, [Issue #2](https://github.com/dr-gabriel-guillen/career-twin-os/issues/2), payload RFC [#12](https://github.com/dr-gabriel-guillen/career-twin-os/issues/12)).
Offline and synthetic-only. The runtime uses only the Python 3.11+ standard library; the tests need `pytest`, and `jsonschema` is optional (used for a cross-check).

```
connectors/ct_connectors/        captured payload → SourceRecord   (Greenhouse, Lever, Ashby, native; no network)
packages/data-matching/src/ct_matching/
  normalize.py   SourceRecord → {posting: frozen JobPosting v0.1, ext: a-ext/0.1}
  store.py       versioned job store, cross-source dedup, revocation, hash-chained provenance ledger
  vault.py       Profile Evidence Vault: claims, evidence pointers, derived status, export gates
  matching.py    hard constraints → signals → near-term / strategic composites → explanations
  retrieval.py   BM25 baseline, offline hashed-embedding adapter, hybrid RRF, skill vectors for lane C
  corpus.py      seeded fictional corpus incl. adversarial cases
  service.py / cli.py / httpapi.py   import, query, rank, provenance, vectors (CLI + loopback HTTP)
```

## Quick start (no install)
```bash
python packages/data-matching/run.py demo                       # generate → import → rank, compact output
python -m pytest packages/data-matching connectors -q           # full suite
```
Full pipeline, each step a separate process:
```bash
python packages/data-matching/run.py corpus --out corpus.json --profile-out profile.json
python packages/data-matching/run.py import --store store.json corpus.json
python packages/data-matching/run.py query  --store store.json --as-of 2026-11-10T00:00:00Z --status active --q "k8s on-call" --retriever hybrid
python packages/data-matching/run.py rank   --store store.json --profile profile.json --as-of 2026-11-10T00:00:00Z --top 10
python packages/data-matching/run.py provenance --store store.json <job_id>
python packages/data-matching/run.py vault-export --profile profile.json --as-of 2026-11-10T00:00:00Z --mode resume-draft
python packages/data-matching/run.py skill-vectors --store store.json --profile profile.json --as-of 2026-11-10T00:00:00Z
python packages/data-matching/run.py serve --store store.json --profile profile.json --port 8765   # 127.0.0.1 only
python packages/data-matching/run.py bench ranking|retrieval|perf [--n 10000]
```
HTTP routes: `GET /v1/health`, `GET /v1/jobs?as_of=…&status=&source=&family=&remote=&q=&retriever=&limit=&offset=`, `GET /v1/jobs/{id}?as_of=…`, `GET /v1/provenance/{id}`, `GET /v1/skill-vectors?as_of=…`, `POST /v1/import`, `POST /v1/rank` (body: `{as_of, top_n?, preferences?, profile?}`).
Errors are always JSON `{"error": {"code", "message"}}`. The CLI exits with code 3 on data errors.
Integration runner (lane E): `integration-manifest.json` with `e2e jobs|claims|rank|vectors`.

## Guarantees (each has tests)
| Guarantee | Where enforced | Tests |
|---|---|---|
| Every emitted `posting` validates against frozen `contracts/job-posting.schema.json`; every exported claim against `profile-claim.schema.json` | `store.import_result`, `vault.export` | `test_contracts.py`, `consumer_check.py` (separate process, no lane A import) |
| Same inputs → byte-identical output; the wall clock is never read (`as_of` is always explicit) | everywhere | `test_cli_output_is_byte_identical_across_processes`, `test_same_inputs_same_output_including_hash` |
| Unknown stays unknown: salary, location, remote policy, employment type and seniority are `null`/`"unknown"` unless the source stated them; contradictory statements resolve to unknown | `normalize.py` | `test_normalize.py`, `test_no_compensation_is_hallucinated_anywhere` |
| Pay is never inferred, annualized or converted; `verified` is always `false` | `resolve_compensation`, `hard_constraints` | `test_unusable_pay_statements_yield_null_with_reason`, `test_no_currency_conversion_is_attempted` |
| No false availability: `active` = listed at a capture ≤ 14 days before `as_of`; revoked, delisted or expired → `expired`; stale or time-anomalous → `unknown` | `status_at`, `store._revoke_missing` | `test_store.py`, `test_a_month_later_nothing_is_presented_as_available` |
| Hard constraints are applied before scoring; every refused job is reported with its reasons | `matching.rank` | `test_hard_constraint_refuses_before_scoring_and_says_why` |
| The score is labelled `relative_heuristic_not_offer_probability`; unknown signals are excluded and widen `score_interval` instead of counting as zero | `matching.compose` | `test_score_is_labelled_not_a_probability_everywhere`, `test_posting_without_recognizable_skills_has_unknown_coverage_not_zero` |
| A claim's status is derived from evidence, never set by the caller; résumé prose only ever yields `self-reported`; credentials need issuer or document evidence | `vault._derive` | `test_vault.py` |
| `public-approved` needs an explicit approval reference and support; it fails closed when support is lost | `vault.set_visibility`, `_rederive` | `test_contradicted_public_claim_fails_closed_to_private` |
| Hostile content (scripts, hidden text, prompt injection, bidi/zero-width characters, unsafe URLs) is stripped or flagged, and flagged postings are refused by default | `textsafe.py`, `hard_constraints` | `test_textsafe.py`, `test_adversarial.py`, `test_fuzz.py` |

## Ranking model (disclosed policy, not learned)
1. **Hard constraints:** content integrity, availability, evergreen requisitions, excluded employers, remote policy, jurisdiction, employment type, seniority band, compensation floor. Each one returns `pass`, `fail` or `unknown`; `unknown` never fails unless the user asks for strict mode (e.g. `require_known_compensation`).
2. **Signals in [0,1] or `null`:** `skill_coverage` (required = 1, preferred = ½), `proof_strength` (evidence weight of matched skills: supported 1, self-reported 0.5, unverified 0.25), `seniority_alignment`, `workstyle`, `transition_utility` (taxonomy prior on family adjacency + share of target core skills exercised), `customer_proximity`, `enterprise_delivery` (taxonomy prior + text cues).
3. **Composites:** `near_term_fit` = weighted mean of the first four signals, `strategic_utility` = weighted mean of the last three, `score = (1-λ)·near + λ·strategic` (λ = `strategy_weight`, default 0.5). When one composite has no applicable signal, the score is the other composite.
4. **Explanation per job:** components with configured and effective weights and contributions, matched, missing and unsupported skills, constraint outcomes, unknown inputs, availability, compensation state, provenance, cautions, rank sensitivity (each weight ±20%, λ ±0.1) and counterfactuals (top 5 only).

Weights, priors and adjacency are lane A's **authoring assumptions** (see `taxonomy.py`); they are not fitted to outcomes.

## Benchmarks (`benchmarks/`)
`frozen_metrics.json` is asserted exactly by the tests (regression guard). `perf_n*.json` records one machine's timings and is not asserted. The numbers come from fictional data whose labels are generated with the same taxonomy the ranker uses, so they measure self-consistency, **not** real-world relevance or hiring outcomes.

## Known limits
- Section and skill extraction is heuristic (English headings, surface-form lexicon of 58 skills). Postings written differently will under-report skills; that shows up as `skill_coverage: null` or as lower coverage, never as invented skills.
- Location parsing covers a fixed country list and US state codes. Anything else stays without a jurisdiction (`unknown`), and the ambiguous codes CA, DE, IN and AR are deliberately not guessed.
- Dedup uses employer key, title key, location, remote policy and description Jaccard ≥ 0.6. Two near-identical postings for genuinely different roles at the same employer could merge; the merge shows in `ext.dedup.members` and is never silent.
- The "hashed embedding" retriever is a lexical-hash stand-in, not a semantic model. `AdapterRetriever` accepts a real local model only if it declares `offline = True`.
- The HTTP server is a single-user, loopback-only development server, not a production deployment.

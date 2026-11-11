# CAREER-TWIN-E — Integration, contracts, CI and security

Lane E is the fail-closed verifier for CareerTwin OS. It does not implement lanes A–D; it
checks what they deliver. Authoritative instructions: [Issue #6](https://github.com/dr-gabriel-guillen/career-twin-os/issues/6).
Everything here is offline and synthetic. Nothing merges, deploys, submits or contacts anyone.

## Run it

```bash
python -m pip install -r integration/requirements.txt   # pinned; the only step that needs the network
cd integration
python -m pytest                      # isolated tests; the network is blocked in-process
python -m ruff format --check . && python -m ruff check . && python -m mypy
python -m ctint.sec_scan --root .. --tracked    # secrets / PII / private fixtures in tracked files
python -m ctint.sec_paid --root .. --tracked    # paid APIs, outbound contact, LinkedIn automation
python -m ctint.runner --repo .. --base origin/main              # pinned-SHA integration run
python -m ctint.runner --repo .. --base origin/main --allow-stub # same, undelivered stages filled by the labelled stub
```

`ctint.runner` exits `0` PASS, `1` FAIL, `2` BLOCKED / NOT-INTEGRATED and writes `report.md` + `report.json`
(with exact SHAs and a reproduce command) to `integration/.work/run-<utc>/`.

## What is here

| Path | Workstream | Purpose |
|---|---|---|
| `ctint/contracts.py`, `fixtures/contracts/*.cases.json` | 2, 6 | Draft 2020-12 conformance for the six frozen contracts with a strict `date-time` checker; semantic invariants the schemas do not encode (UTC, approval gate, design lock, no hiring-probability claims); static schema audit. 33 valid, 98 schema-negative and 43 schema-gap fixtures. |
| `ctint/fuzz.py`, `ctint/synth.py` | 10 | Single-fault schema mutations, random-JSON robustness, seeded fictional generators (1k+ jobs, 100 applications, 20 experiments, 10 scenarios). |
| `ctint/sec_csv.py`, `sec_ssrf.py`, `sec_inject.py` | 5 | CSV formula-injection oracle, offline SSRF classifier, differential prompt/HTML/Unicode injection probes with canaries. |
| `ctint/sec_scan.py`, `sec_paid.py`, `netguard.py` | 3 | Secret/PII/private-fixture scanner, paid-API and outreach tripwire, runtime network block for tests. |
| `ctint/lanes.py` | 3, 4 | Lane registry (checked against `AGENTS.md`) and path-ownership enforcement. |
| `ctint/experiment_integrity.py`, `stats.py` | 8 | Black-box attacks on assignment functions; checks on assignment journals and analyses (design change after assignment, double enrolment, crossover, SRM, late outcomes, false certainty). |
| `ctint/atlas_quality.py` | 9 | Independent trustworthiness / continuity / k-NN preservation, reference PCA, fit-leakage and "neighbours taken from 2D" detection. |
| `ctint/e2e.py`, `stub.py`, `runner.py`, `lane-manifest.schema.json` | 4, 7 | Artifact-exchange pipeline, labelled reference stub, disposable-worktree runner. |
| `known_findings.json` | 1, 6 | Ledger of open findings on shared baseline code and contracts, each tied to a test. |
| `THREAT_MODEL.md` | 5 | Threats, the test that covers each one, and residual risk. |

## How a lane plugs in

Put one `integration-manifest.json` inside your owned path (schema: `integration/lane-manifest.schema.json`, a lane E
proposal, not a frozen contract). It lists your offline test commands and the pipeline stages you provide. A stage
is a command that reads and writes JSON files in one artifacts directory:

| Stage | Lane | Reads | Writes |
|---|---|---|---|
| `jobs.import` | A | — (`{seed}`, `{count}`) | `jobs.json` — `JobPosting[]` |
| `profile.claims` | A | — | `claims.json` — `ProfileClaim[]` |
| `match.rank` | A | `jobs.json`, `claims.json` | `matches.json` |
| `features.vectors` | A | `jobs.json` | `vectors.json` |
| `resume.variant` | B | `jobs.json`, `claims.json`, `pin.json` | `resume_variant.json` |
| `application.record` | B | `pin.json`, `resume_variant.json` | `events.json` — `ApplicationEvent[]` |
| `experiment.run` | B | `jobs.json` | `experiment.json`, `assignments.json`, `experiment_events.json`, `analysis.json` |
| `atlas.project` | C | `vectors.json` | `atlas_points.json` — `AtlasPoint[]`, `atlas_diagnostics.json` |
| `simulator.scenario` | C | `claims.json`, `jobs.json`, `pin.json` | `scenarios.json` — `TransitionScenario[]` |
| `ui.e2e` | D | all of the above | `ui_report.json` |

`python -m ctint.stub <stage> --artifacts DIR` writes a contract-valid example of every artifact except `ui_report.json`.

Stage status is `PASS` only for real lane code labelled `mock: false` whose output validates, references upstream
artifacts correctly and changes when its inputs change. `STUB` (lane E's stand-in) and `MOCK` (lane-labelled) are
never integration evidence, so the overall verdict is `PASS` only when all ten stages pass on real code.

## Limits

* The subprocess isolation for lane commands is an environment scrub (no credentials, dead proxy, empty `HOME`),
  not a kernel sandbox.
* The probability-claim lint, secret scanner and paid-API tripwire are pattern based and can be evaded deliberately.
* The SSRF classifier is lexical and offline: DNS rebinding must be handled by the connector at connect time.
* Reviews by lane E are same-provider reviews and do not count as independent external validation.

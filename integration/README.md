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
python -m ctint.runner --repo .. --base origin/main --allow-setup # also run lanes' setup (e.g. npm ci); non-hermetic
python -m ctint.sec_ci --root ..                # workflow hygiene
```

Fetch every lane branch first (`git fetch origin`); the runner defaults each lane to `origin/<lane branch>`
and `--lane X=<sha>` pins one exactly.

`ctint.runner` exits `0` PASS, `1` FAIL, `2` BLOCKED / NOT-INTEGRATED and writes `report.md` + `report.json`
(with exact SHAs and a reproduce command) to a new `integration/.work/run-<utc>/`.

**Output directory policy (`ctint/outdir.py`).** The runner never deletes a directory it did not create.
`--out DIR` must be new, empty, or a previous run of this tool (it carries `.ctint-run.json` naming its own
path), and a previous run is replaced only with `--overwrite`. Refused whatever they contain: a filesystem
root or top-level directory, the home directory, the current directory, the system temp directory itself,
the repository or anything containing it, anything inside `.git`, any path inside the repository other than
`integration/.work/<name>`, and any path that is, or passes through, a symbolic link or junction out of the
work tree. A refusal prints the reason, exits `2` and creates or removes nothing.

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
| `THREAT_MODEL.md` | 5 | Threats T-01…T-14, the test that covers each one, what was observed on lane code, residual risk. |
| `reviews/probe_lane_a_injection.py` | 5, 9 | Reproduces review findings A-F1…A-F3 against lane A at any pinned SHA (needs lane A's code; not in the isolated suite). |
| `HANDOFF.md` | 12 | Final PASS / FAIL / BLOCKED / UNKNOWN matrix, evidence, owner decisions, demo instructions. |

Lane status in a run: `PASS` (manifest valid, gates and declared tests pass) · `FAIL` · `UNDECLARED` (code
pushed, no manifest) · `BLOCKED` (nothing integrable pushed). Lane-native shapes (A's envelopes, B's
`careertwin.b.*` payloads, C's diagnostics) are mapped by renaming only; see `tests/test_lane_shapes.py`.

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

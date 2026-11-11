# CAREER-TWIN-B — Application CRM & Experiment Lab

Offline, synthetic-only, standard-library Python (3.11+; developed on 3.14). No dependency to install,
no network, no real data. Source of requirements: [Issue #3](https://github.com/dr-gabriel-guillen/career-twin-os/issues/3).

```
python packages/experiments/run_tests.py                       # every lane B test, warnings are errors
python packages/experiments/tools/build_fixtures.py            # regenerate fixtures/v1 (a few minutes)
python packages/experiments/tools/build_fixtures.py --quick    # cohort + roundtrip only (~20 s)
python packages/experiments/tools/build_offline_package.py     # reproducible zip in dist/ (git-ignored)
```

## What is here

| Path | Purpose |
| --- | --- |
| `packages/applications/ct_applications/journal.py` | Append-only, hash-chained JSONL journal. Edits, deletions and reordering are detected on load; tail truncation is detected against a published anchor. |
| `…/events.py`, `workflow.py` | Application event store emitting the frozen `ApplicationEvent` v0.1, corrections as new events, stage projection, workflow state machine. |
| `…/approvals.py` | User-approval ledger: user-only, bound to (action, subject, content hash), expiring, single-use. |
| `…/resume.py` | Truthful résumé variants: verbatim evidence-backed claims only, immutable versions, diff, approval-gated export. |
| `packages/experiments/ct_experiments/registry.py` | Preregistration with an immutable design lock. |
| `…/assignment.py` | Seeded, stratified permuted-block assignment, journaled before exposure; no crossover, no double enrolment. |
| `…/outcomes.py` | success / no_success / censored / missing, with late, ambiguous and duplicate responses handled explicitly. |
| `…/analysis.py`, `stats.py` | Intention-to-treat randomisation inference, withheld-claim rules, observational labelling, portfolio multiplicity. |
| `…/simulate.py` | Fictional cohort with known ground truth; calibration of the decision rule. |
| `…/adapter.py` | Versioned JSON adapter (`b.v1`) for lanes D and E. |
| `fixtures/v1/` | Versioned, readable JSON produced by the code above (see `MANIFEST.json` for hashes). |

## Interfaces published by lane B

Frozen contracts are **not** modified. B-only data travels beside them.

1. **`ApplicationEvent` v0.1** (frozen) — every event returned by `get_application.events`.
   B's journal stores `{"event": <ApplicationEvent>, "ext": {...}}`; `ext`
   (`careertwin.b.application-event-ext.v1`) carries `employer_id`, `historical` and, for corrections,
   `correction: {target, action: void|amend, changes, reason}`. A correction is itself a contract-valid `note` event.
2. **`ExperimentSpec` v0.1** (frozen) — `get_experiment.spec`. The structured plan is in `get_experiment.design`
   (`careertwin.b.experiment-design.v1`); `design_hash` covers both and is what the user approves.
3. **ResumeVariant** (`careertwin.b.resume-variant.v1`) — `get_resume_variant`.
   `id, lineage_id, version, parent_id, subject_id, job_id, label, bullets[], unproven_requirements[],
   refused_claims[], warnings[], content_hash, created_at, created_by, change_note`;
   bullet = `claim_id, text, claim_status, evidence_ids[], evidence_hash, skills[], manual_override,
   claim_text_hash, contains_markup_like_text`.
4. **ExperimentAssignment** — `get_application.assignment`:
   `assignment_id, experiment_id, application_id, unit_kind, unit_id, stratum, variant, assigned_at, reused_unit, design_hash`.
5. **AnalysisReport** (`careertwin.b.analysis-report.v1`) — `experiment_report`. Always check `verdict`:
   `EVIDENCE_FOR_DIFFERENCE`, `INCONCLUSIVE`, `INTERIM_NO_CLAIM`, `EXPLORATORY_NO_CLAIM`, `NOT_ANALYZABLE`
   (and `OBSERVATIONAL_ONLY` for history). `claim` is `null` unless the verdict is `EVIDENCE_FOR_DIFFERENCE`;
   `reasons_withheld[]`, `warnings[]` and `interpretation_limits[]` must be shown with any result.
6. **Adapter** — `LaneBApi(lab).call(method, **params)` → `{api_version, method, ok, data | error{type,message}}`.
   Methods are listed in `fixtures/v1/adapter_methods.json`. Example payloads: `fixtures/v1/roundtrip.json`.

### Rules consumers must follow
* All strings that came from postings, claims or the user are **untrusted plain text**. Render as text.
* `grant_approval` may be called only as the direct result of the human confirming the phrase from
  `approval_request` after seeing `must_show_user`. Lane B cannot verify that a human was present; the UI must.
* Never present `INCONCLUSIVE` as "no effect", an interim report as a result, or any number as a hiring probability.
* `submitted` means *the user says they submitted it themselves*. Nothing in lane B submits, e-mails or contacts anyone.

## Statistical design, in brief
* Unit of analysis = unit of randomisation (application, posting or **employer cluster**).
* Primary test: stratified permutation test that re-randomises within strata, mirroring the assignment mechanism.
  Interval: Newcombe hybrid score (single stratum, binary units) or a t-interval on unit means (approximate; flagged).
* Fixed horizon only. Interim reports are blinded by arm and carry no test; an early stop yields no test.
* Missing outcomes count as "no success" (preregistered), with complete-case and worst-case sensitivity analyses;
  a claim that does not survive worst-case imputation is withheld.
* Holm adjustment across arms; Holm and Benjamini–Hochberg across the experiment portfolio.
* History that was not randomised is summarised with intervals only, labelled observational, never tested.

## Known limitations (also in HANDOFF.md)
* One user, self-reported outcomes: the user can see each application's arm and outcome, so interim blinding
  protects the report, not the person. Allocation concealment relies on the seed staying local.
* The approval gate proves intent in software, not human presence.
* Hash chains detect tampering; they do not prevent a party with file access from rebuilding a whole journal.
  Publishing anchors (`Lab.anchors()`) elsewhere is what makes rewriting detectable.
* The t-interval for clustered or multi-stratum designs is approximate at small samples.
* Concurrency: one process per workspace is assumed; there is no file locking.

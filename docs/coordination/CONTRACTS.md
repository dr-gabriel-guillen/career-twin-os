# Contract manifest and invariants — v0.1

Contracts are located in `contracts/` and define typed objects for job postings, profile claims, application events, experiments, atlas points and transition scenarios.

## General invariants
- Stable opaque IDs; UTC ISO-8601 timestamps; job source URLs and capture times; provenance for each claim and derived score.
- Unknown salary, location and outcome remain UNKNOWN, never converted to zero or guessed.
- Experiment assignment must be deterministic given frozen experiment version + assignment unit, saved before exposure. Do not allow crossover to the same posting and user.
- A résumé variant is a snapshot of verified or explicitly self-reported claims; unsupported qualifications prohibited.
- 2D PCA/UMAP is a visualization, NOT a likelihood of hiring or even a reliable representation of original-neighbor distances. All maps should display distortion metrics.
- Application submission and external outreach are explicit manual user actions and not part of automatic scoring.

## Interface ownership
A: normalized `JobPosting`, evidence claims and `MatchExplanation`.
B: `ApplicationEvent`, résumé variant version and `ExperimentAssignment`.
C: `AtlasPoint`, projection diagnostics, `TransitionScenario`.
D: adapters for A/B/C, UX state.
E: schema conformance, local integration harness, security gates.

Every schema change requires a GitHub RFC that names consumer lanes and shows before/after JSON plus migration and tests.

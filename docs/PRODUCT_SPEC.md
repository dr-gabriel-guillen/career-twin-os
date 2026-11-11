# CareerTwin OS — product spec v0.1

## Mission
An evidence-backed personal career intelligence system that combines relevant job discovery, explainable matching, application tracking, truthful résumé variants, experiment design, visualization and skill-path planning.

## User-visible modules
1. Profile Evidence Vault: claims with source/evidence pointer, support status, expiration, consent, export/deletion.
2. Job Discovery: permitted public ATS feeds, sourced listings, freshness, deduplication, remote/location constraints, compensation if available, no fake jobs.
3. Fit & ranking: hard gates, transparent semantic/skill match, trajectory utility, uncertain components; never call match score an offer probability.
4. Application CRM: saved jobs, contact/recruiter notes with consent, event history, stages, reminders, outcome attribution and statuses.
5. Resume Studio: versioned, traceable truthful bullet variants and user-approved exports; no fabricated achievements.
6. Experiment Lab: preregistration, stratified randomized assignments, exposure logs, reply/screen/interview/offer outcomes, uncertainty, power analysis, multiple-testing warnings, no premature causal claims.
7. Career Atlas: dimensionality reduction (PCA, optional UMAP) and labeled points; verify fidelity to original high-dimensional neighbors and disclose projection distortion.
8. Career Simulator: evidence-based skill gap and portfolio investment scenarios, path prerequisites, costs/time, uncertainty and user-set values; no career outcomes fabricated.
9. Interview Preparation: offline mock questions and evidence gaps.
10. LinkedIn compatibility: explicit user-export importer and possible approved OIDC; no unrestricted API claims, scraping or auto-application.

## Roadmap and acceptance
Phase 1: fully offline synthetic vertical slice from fake job feed to matching, chosen job, résumé variant, synthetic application, randomized experiment, atlas visualization and scenarios.
Phase 2: optional permitted ATS connectors, login, provenance verification, security and manual user data import **only after repository private and explicit owner approval**.
Phase 3: deployment/security readiness with human acceptance; no automatic deployment.

Every recommendation should expose which evidence matters, what is unknown, and how the user can independently evaluate it.

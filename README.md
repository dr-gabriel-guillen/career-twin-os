# CareerTwin OS

An original, evidence-backed AI career intelligence platform with job discovery, matching, an application CRM, truthful résumé variants, randomized experimentation, and an explainable career atlas with 2D PCA/UMAP and transition simulations.

## Status

**BOOTSTRAP / SYNTHETIC DATA ONLY.** This repository currently is **PUBLIC**. Never commit personal résumés, LinkedIn exports, contacts, application outcomes, secrets, real candidate profiles or private employer/client materials. The owner should change visibility to PRIVATE before any personal-data integration.

Project work uses five permanent parallel Claude Code lanes:

| Session | Branch | Ownership |
| --- | --- | --- |
| CAREER-TWIN-A | `agent/ct-a-data-matching` | ATS connectors, job ingestion, matching, evidence vault |
| CAREER-TWIN-B | `agent/ct-b-experiments` | Application tracking, résumé variants, experimentation |
| CAREER-TWIN-C | `agent/ct-c-atlas` | PCA, UMAP, skill gaps, career simulations |
| CAREER-TWIN-D | `agent/ct-d-product-ux` | UI, dashboards, accessible workflows |
| CAREER-TWIN-E | `agent/ct-e-integration` | Contracts, CI, integration testing, security and coordination |

**Read `AGENTS.md`, `docs/coordination/PROTOCOL.md`, and the coordination and lane-specific GitHub Issues before any coding.** The Issues carry the authoritative task instructions.

Each agent must use its own branch, open a draft PR to `main`, communicate dependencies and review feedback in GitHub Issues/PR comments, and post meaningful checkpoints with HEAD SHA + tests. No agent may merge, deploy, scrape LinkedIn, auto-apply to jobs, message recruiters, use paid APIs, or process personal records without owner authorization.

The initial development and every test must remain completely offline and synthetic. Matching scores are not hiring probabilities. PCA points do not guarantee hiring compatibility. Experiment effects require randomized assignments, comparable groups, and honest uncertainty.

This is not a completed product or a replacement for Jobright yet.

# CAREER-TWIN-C — Career Atlas & Transition Simulator

Lane C of CareerTwin OS. Execution instructions: [Issue #4](https://github.com/dr-gabriel-guillen/career-twin-os/issues/4). Draft PR: [#9](https://github.com/dr-gabriel-guillen/career-twin-os/pull/9).

**Synthetic and offline only.** Every posting, employer, claim and compensation figure used here is fictional. The 2-D map is a visualisation with measured distortion. It is **not a match score and not a hiring probability**, and nothing in this package estimates the chance of an interview, an offer or a salary.

## Run it

Requires Python ≥ 3.10 and `numpy`. `pytest` and `jsonschema` are needed for the tests only. No network, no keys, no paid API.

```bash
# from the repository root
python analytics/run_report.py                       # regenerate analytics/reports/
python -m pytest packages/atlas/tests                # full test suite

# from packages/atlas
python -m careertwin_atlas demo --out ../../analytics/reports
python -m careertwin_atlas simulate --target field-cto --action cloud-architecture:120:portfolio-project
python -m careertwin_atlas stress --n 5000
```

## What is here

| Module | Purpose |
|---|---|
| `vocabulary.py` | Versioned controlled vocabulary (51 skills in 6 groups + 1 seniority feature), aliases, effort placeholders, acquisition mode |
| `features.py` | `JobPosting` / `ProfileClaim` → feature vectors with per-feature **state** (stated / not-stated / contradicted / unknown) and provenance |
| `taxonomy.py` | Eight synthetic role families and a deterministic, contract-valid posting generator |
| `pca.py` | PCA on numpy SVD: loadings, explained variance, held-out transform, residual share, JSON round-trip |
| `diagnostics.py` | Trustworthiness, continuity, tie-aware kNN recall, distance agreement, stress, Procrustes, seed variation |
| `projectors.py` | Interpretable axes, optional UMAP plugin, seeded random-projection baseline |
| `clustering.py`, `roles.py` | k-means in the full space, bootstrap stability, role centroids, unstable-centroid flags, drift test |
| `gaps.py` | Gap analysis (missing vs weak vs contradicted evidence) and exact map-movement decomposition |
| `graph.py` | Role transition graph and candidate-specific path planning (direct vs stepping stone) |
| `simulator.py` | Counterfactual simulator → `TransitionScenario` |
| `sensitivity.py` | Weight, missing-salary, seniority, freshness, volume, lexicon and normalisation perturbations |
| `api.py` | `build_atlas_bundle` / `compare_projections` — the adapter for lane D and E's harness |
| `report.py`, `__main__.py` | Offline demo, diagnostics report, CLI |

## Interface for consumers (lanes D and E)

```python
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.features import claims_to_vector
from careertwin_atlas.api import build_atlas_bundle
from careertwin_atlas.simulator import simulate_transition

engine = AtlasEngine.fit(job_postings)                     # list of JobPosting dicts (contract v0.1)
candidate = claims_to_vector(profile_claims, engine.vocab, candidate_ref="candidate-synthetic-001")
bundle = build_atlas_bundle(engine, candidate)             # method="pca" | "interpretable-axes" | "umap"
scenario = simulate_transition(engine, candidate, "field-cto",
                               [{"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project"}],
                               time_horizon_months=12)
```

- `bundle["points"][i]` validates against the frozen `contracts/atlas-point.schema.json`.
- `scenario` validates against the frozen `contracts/transition-scenario.schema.json`.
- The envelope around the points and the shape of `scenario["outputs"]` are documented in `schemas/` (lane-C owned, not part of `contracts/`).
- `bundle["flags"]["two_d_geometry_is_not_match_metric"]` is always `true`, and every point carries the warning `map-position-is-not-a-fit-or-hiring-score`.
- `label` values are sanitised (control and bidi characters removed, 120 chars) but **not escaped**. The renderer must escape them.
- Rank roles with `bundle["candidate"]["role_table"]` (full-space `requirement_shortfall`), never with distances between map coordinates.

## Modelling decisions a reader should know

1. **Absence is not disproof.** A skill a posting does not list is `not-stated`; a skill with no claim is `no-evidence-on-file`. Only `contradicted` means "evidence says no". An empty or unreadable skills list makes the whole skill block `unknown`.
2. **Unknown values are mean-imputed for geometry and counted.** Imputed counts appear per point and in model warnings; imputation understates variance.
3. **Distances that matter are computed in the full space.** `requirement_shortfall` is one-sided (only what the role states and the candidate does not cover). Map distance is reported only to expose disagreement.
4. **Role-exposure evidence cannot be bought with study hours.** Client work, management scope and executive communication are capped for coursework-type actions and shown as entry barriers on paths.
5. **A contradiction is not simulated away.** Planned hours on a contradicted claim change nothing until the contradiction is resolved.
6. **Unknown compensation is never zero and never guessed.** It is dropped from a role's weighted sum and rank bounds show what any value could change. Synthetic compensation uses the unit `SYN`.
7. **Thresholds are reading conventions.** Trustworthiness, Jaccard, instability ratio and Kendall-tau cut-offs are returned next to the numbers and are not validated.

## Known limits

- UMAP is an optional plugin. `umap-learn` was not installed where this batch ran, so no UMAP metric has been produced; only the adapter wiring is tested, with a stub.
- Role prevalences are generator inputs. Recovering eight clusters from data built from eight families is a consistency check, not a discovery.
- Effort hours, evidence-level conventions, the 18-month tenure assumption and the conferred-evidence rule are placeholders awaiting owner review.
- Free-text requirement matching is deliberately conservative (it ignores ambiguous everyday words), so some real mentions are reported as unmapped.
- Reviews by the same model family are not independent external validation.

See `analytics/reports/DIAGNOSTICS_REPORT.md` for the current numbers and `HANDOFF.md` for the batch handoff.

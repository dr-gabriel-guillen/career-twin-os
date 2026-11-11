"""Offline demo + diagnostics report. Everything is synthetic and reproducible from a seed.

``run_demo`` regenerates the artefacts under ``analytics/reports/``:

    python -m careertwin_atlas demo --out analytics/reports

No network access, no paid API, no personal data.
"""
from __future__ import annotations

import hashlib
import html
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from . import __version__
from .api import build_atlas_bundle, canonical_json, compare_projections, digest
from .engine import AtlasEngine
from .features import claims_to_vector, job_to_vector
from .gaps import analyze_gap
from .graph import build_transition_graph, plan_paths
from .roles import centroid_drift, split_by_time
from .sensitivity import SensitivityInputs, run_sensitivity
from .simulator import simulate_transition
from .taxonomy import generate_postings
from .vocabulary import default_vocabulary

CANDIDATE_REF = "candidate-synthetic-001"
AS_OF = "2026-10-31T00:00:00Z"
#: Okabe-Ito colour-blind-safe palette, one colour per role family.
_PALETTE = ("#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000")
DEMO_PLAN = [
    {"skill": "cloud-architecture", "effort_hours": 120, "evidence_goal": "portfolio-project"},
    {"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project"},
    {"skill": "client-exposure", "effort_hours": 90, "evidence_goal": "coursework"},
]


def default_claims() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_candidate_claims.json"
    return json.loads(path.read_text(encoding="utf-8"))


def held_out_check(jobs: Sequence[Mapping[str, Any]], seed: int = 0, test_share: float = 0.2) -> dict[str, Any]:
    """Fit on a training split, project unseen postings, and measure what the map loses.

    Nearest-centroid family assignment is computed twice for the unseen postings: once in the
    full feature space and once using only map coordinates. The gap between the two is a
    direct, reproducible measure of information lost by the picture.
    """
    vocab = default_vocabulary()
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(jobs))
    n_test = max(1, int(round(len(jobs) * test_share)))
    test_idx, train_idx = order[:n_test], order[n_test:]
    engine = AtlasEngine.fit([jobs[int(i)] for i in train_idx], vocab, seed=seed, n_boot=10)
    fams = sorted(engine.profiles)
    C = np.vstack([engine.profiles[f].centroid for f in fams])
    C_map = engine.model.transform(C)
    full_hits = map_hits = total = 0
    residuals = []
    for i in test_idx:
        v = job_to_vector(jobs[int(i)], vocab)
        truth = v.meta.get("role_family")
        if truth not in engine.profiles:
            continue
        x = engine.imputed(v)
        proj = engine.project(v)
        residuals.append(proj["residual_share"])
        full = fams[int(np.argmin(np.linalg.norm((C - x) / engine.model.scale, axis=1)))]
        on_map = fams[int(np.argmin(np.linalg.norm(C_map - proj["coords"], axis=1)))]
        full_hits += int(full == truth)
        map_hits += int(on_map == truth)
        total += 1
    return {
        "n_train": int(len(train_idx)), "n_held_out": total,
        "nearest_centroid_agreement_full_space": round(full_hits / total, 4) if total else None,
        "nearest_centroid_agreement_map_only": round(map_hits / total, 4) if total else None,
        "held_out_residual_share_median": round(float(np.median(residuals)), 4) if residuals else None,
        "train_residual_share_median": round(float(np.median(engine.residual_share)), 4),
        "meaning": "Share of unseen synthetic postings whose nearest role centroid matches their "
                   "generator family. The drop from full space to map-only is information the "
                   "picture discards. This is a property of synthetic data, not a hiring metric.",
    }


def drift_check(n: int, seed: int) -> dict[str, Any]:
    """Planted drift (backend postings start stating LLM skills) versus a no-drift control."""
    vocab = default_vocabulary()
    cutoff = "2026-08-02T00:00:00Z"            # day 90 of the 180-day synthetic window
    out = {}
    for name, drift in (("planted_drift", {"family": "backend", "after_day": 90,
                                           "prevalence": {"llm-integration": 0.7, "rag": 0.45}}),
                        ("no_drift_control", None)):
        jobs = generate_postings(n, vocab.skill_names, seed=seed + 100, drift=drift)
        engine = AtlasEngine.fit(jobs, vocab, seed=seed, n_boot=10)
        early, late = split_by_time(engine.matrix.vectors, cutoff)
        res = centroid_drift(early, late, engine.model.mean, seed=seed)
        out[name] = {"cutoff": cutoff, "n_early": len(early), "n_late": len(late),
                     "drifted_families": sorted(f for f, r in res["families"].items() if r.get("drifted")),
                     "families": res["families"]}
    out["note"] = ("The generator plants a requirement change in 'backend' only. The control uses the "
                   "same seed without the change. Flagging uses a Bonferroni-adjusted permutation test.")
    return out


def render_svg(engine: AtlasEngine, bundle: Mapping[str, Any], width: int = 860, height: int = 560) -> str:
    """Self-contained SVG scatter of the 2-D map. All text is escaped."""
    coords = engine.coords[:, :2]
    fams = [v.meta.get("role_family") for v in engine.matrix.vectors]
    pad, legend_w = 46, 220
    xs, ys = coords[:, 0], coords[:, 1]
    x0, x1, y0, y1 = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
    sx = (width - legend_w - 2 * pad) / (x1 - x0 if x1 > x0 else 1.0)
    sy = (height - 2 * pad - 40) / (y1 - y0 if y1 > y0 else 1.0)

    def px(x: float, y: float) -> tuple[float, float]:
        return pad + (x - x0) * sx, height - pad - 40 - (y - y0) * sy

    legend = sorted(engine.profiles)
    colour = {f: _PALETTE[i % len(_PALETTE)] for i, f in enumerate(legend)}
    evr = engine.model.explained_variance_ratio
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Synthetic career atlas, 2-D PCA projection">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{pad}" y="24" font-family="sans-serif" font-size="15" font-weight="bold">'
        'Synthetic career atlas (2-D PCA) — a sketch, not a fit or hiring score</text>',
    ]
    for (x, y), fam in zip(coords, fams):
        cx, cy = px(float(x), float(y))
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.2" fill="{colour.get(fam, "#888888")}" '
                     'fill-opacity="0.45"/>')
    for p in bundle["points"]:
        if p["representation_type"] not in ("role-centroid", "candidate", "scenario"):
            continue
        cx, cy = px(p["coords"][0], p["coords"][1])
        label = html.escape(p["label"], quote=True)
        if p["representation_type"] == "role-centroid":
            fam = p["entity_id"].split(":", 1)[-1]
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="{colour.get(fam, "#888888")}" '
                         'stroke="#ffffff" stroke-width="2"/>')
        else:
            parts.append(f'<path d="M{cx - 7:.1f},{cy:.1f} L{cx:.1f},{cy - 7:.1f} L{cx + 7:.1f},{cy:.1f} '
                         f'L{cx:.1f},{cy + 7:.1f} Z" fill="#ffffff" stroke="#000000" stroke-width="2"/>')
            parts.append(f'<text x="{cx + 10:.1f}" y="{cy + 4:.1f}" font-family="sans-serif" '
                         f'font-size="11">{label}</text>')
    lx = width - legend_w + 10
    for i, fam in enumerate(legend[:20]):
        y = 60 + i * 22
        parts.append(f'<circle cx="{lx}" cy="{y}" r="6" fill="{colour[fam]}"/>')
        parts.append(f'<text x="{lx + 14}" y="{y + 4}" font-family="sans-serif" font-size="12">'
                     f'{html.escape(engine.profiles[fam].label)}</text>')
    parts.append(f'<text x="{pad}" y="{height - 52}" font-family="sans-serif" font-size="11">'
                 f'Axis 1: {evr[0]:.1%} of variance · Axis 2: {evr[1]:.1%} of variance · '
                 f'{1 - float(evr[:2].sum()):.1%} not shown</text>')
    d = bundle["diagnostics"]
    cap = (f'Trustworthiness {d["trustworthiness"]:.2f} · continuity {d["continuity"]:.2f} · '
           f'kNN recall {d["knn_recall_mean"]:.2f} · distance rank agreement {d["distance_spearman"]:.2f}')
    parts.append(f'<text x="{pad}" y="{height - 34}" font-family="sans-serif" font-size="11">{html.escape(cap)}</text>')
    parts.append(f'<text x="{pad}" y="{height - 16}" font-family="sans-serif" font-size="11">'
                 'All postings and employers are fictional. Large dots: role centroids. Diamonds: candidate / scenario.</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def _md_escape(text: object) -> str:
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", " ")


def render_markdown(summary: Mapping[str, Any]) -> str:
    b, cmp_, sc = summary["bundle"], summary["comparison"], summary["scenario"]
    d, held, sens, drift = b["diagnostics"], summary["held_out"], summary["sensitivity"], summary["drift"]
    L: list[str] = []
    L.append("# Career Atlas — diagnostics report (synthetic)")
    L.append("")
    L.append(f"Generated by `careertwin_atlas {__version__}` from {summary['n_postings']} fictional postings "
             f"(generator seed {summary['seed']}). **Synthetic data only. The map is a visualisation with "
             "measured distortion; it is not a match score and not a hiring probability.**")
    L.append("")
    L.append("## 1. What the 2-D map loses")
    L.append("")
    L.append(_md_escape(b["information_loss"]["statement"]))
    L.append("")
    L.append("| Metric | Value | Reading |")
    L.append("|---|---|---|")
    L.append(f"| Variance shown by 2 axes | {d['explained_variance_cumulative']:.4f} | share of total variance |")
    L.append(f"| Trustworthiness (k={d['k_neighbours']}) | {d['trustworthiness']:.4f} | 1 = no false neighbours |")
    L.append(f"| Continuity (k={d['k_neighbours']}) | {d['continuity']:.4f} | 1 = no torn neighbourhoods |")
    L.append(f"| kNN recall | {d['knn_recall_mean']:.4f} | share of map neighbours that are true neighbours |")
    L.append(f"| Distance rank agreement (Spearman) | {d['distance_spearman']:.4f} | 1 = same ordering of pairs |")
    L.append(f"| Normalised stress | {d['normalized_stress']:.4f} | 0 = distances preserved |")
    L.append("")
    for f in d["flags"]:
        L.append(f"- Flag: {_md_escape(f)}")
    L.append("")
    L.append("Held-out check (fit on a training split, project unseen postings):")
    L.append("")
    L.append(f"- Nearest role centroid matches the generator family for "
             f"**{held['nearest_centroid_agreement_full_space']:.1%}** of {held['n_held_out']} unseen postings in the "
             f"full feature space, but only **{held['nearest_centroid_agreement_map_only']:.1%}** when using map "
             "coordinates alone.")
    L.append(f"- Median residual share: {held['held_out_residual_share_median']:.2f} held-out vs "
             f"{held['train_residual_share_median']:.2f} training.")
    L.append("")
    L.append("## 2. Axes")
    L.append("")
    for ax in b["projection"]["axes"]:
        pos = ", ".join(f"{x['feature']} ({x['weight']:+.2f})" for x in ax["positive"])
        neg = ", ".join(f"{x['feature']} ({x['weight']:+.2f})" for x in ax["negative"])
        L.append(f"- Axis {ax['component']} ({ax['explained_variance_ratio']:.1%} of variance): + {pos}; − {neg}")
    L.append("")
    L.append("## 3. Projection methods compared against full-space distances")
    L.append("")
    L.append("| Method | Status | Trustworthiness | Continuity | kNN recall | Distance agreement | Stress |")
    L.append("|---|---|---|---|---|---|---|")
    for name, m in cmp_["methods"].items():
        if m["status"] != "executed":
            L.append(f"| {name} | **NOT EXECUTED** — {_md_escape(m['reason'])} | – | – | – | – | – |")
            continue
        L.append(f"| {name} | executed | {m['trustworthiness']:.4f} | {m['continuity']:.4f} | "
                 f"{m['knn_recall_mean']:.4f} | {m['distance_spearman']:.4f} | {m['normalized_stress']:.4f} |")
    rs = cmp_["methods"]["pca"]["resampling_stability"]
    sv = cmp_["methods"]["random-projection-baseline"]["seed_variation"]
    L.append("")
    L.append(f"- PCA is deterministic. Under {rs['n_boot']} bootstrap refits the fitted plane agrees with the "
             f"original at {rs['plane_agreement_mean']:.3f} on average (min {rs['plane_agreement_min']:.3f}; 1 = same plane).")
    L.append(f"- Seed variation machinery, exercised on the random-projection baseline over seeds {sv['seeds']}: "
             f"mean Procrustes disparity {sv['procrustes_disparity_mean']:.3f}, mean kNN overlap "
             f"{sv['knn_overlap_mean']:.3f}. The same check runs on UMAP when it is installed.")
    L.append("")
    L.append("## 4. Roles, clusters and stability")
    L.append("")
    L.append("| Role family | Postings | Unstable centroid | Instability ratio |")
    L.append("|---|---|---|---|")
    for c in b["role_centroids"]:
        L.append(f"| {_md_escape(c['label'])} | {c['n_postings']} | {'yes' if c['unstable'] else 'no'} | {c['instability_ratio']} |")
    L.append("")
    if b["clusters"]:
        L.append("k-means clusters (computed in the full space) versus generator families:")
        L.append("")
        L.append("| Cluster | Size | Majority family | Purity | Bootstrap Jaccard | Verdict |")
        L.append("|---|---|---|---|---|---|")
        for c in b["clusters"]["clusters"]:
            L.append(f"| {c['cluster']} | {c['size']} | {c.get('majority_family')} | {c.get('purity')} | "
                     f"{c['bootstrap_jaccard_mean']} | {c['stability']} |")
        L.append("")
    small = summary["small_sample"]
    L.append(f"With only {small['n_postings']} postings, {len(small['unstable_roles'])} of {small['n_roles']} role "
             f"centroids are flagged unstable ({', '.join(small['unstable_roles']) or 'none'}).")
    L.append("")
    L.append("Drift detection (posting text only):")
    L.append("")
    for key in ("planted_drift", "no_drift_control"):
        r = drift[key]
        fams = ", ".join(r["drifted_families"]) or "none"
        L.append(f"- {key.replace('_', ' ')}: families flagged = {fams}")
    pb = drift["planted_drift"]["families"].get("backend", {})
    if pb.get("status") == "compared":
        tops = ", ".join(f"{t['feature']} ({t['change']:+.2f})" for t in pb["top_feature_changes"][:3])
        L.append(f"- backend in the planted case: shift-to-noise {pb['shift_to_noise']}, permutation exceedance "
                 f"share {pb['permutation_exceedance_share']} (top changes: {tops})")
    L.append("")
    L.append("## 5. Gap analysis and scenario (hypothetical)")
    L.append("")
    gap = summary["gap"]
    L.append(f"Target: **{_md_escape(gap['target_label'])}**. Core requirements by candidate evidence state: "
             f"{gap['core_summary']}.")
    L.append(f"- Missing evidence (not proof of absence): {', '.join(gap['skill_gaps']) or 'none'}")
    L.append(f"- Weak evidence: {', '.join(gap['evidence_gaps']) or 'none'}")
    L.append(f"- Confidence in the role description: {gap['confidence']['level']}")
    o = sc["outputs"]
    L.append("")
    L.append(f"Scenario `{sc['id']}` ({o['effort']['planned_hours']:.0f} planned hours, horizon "
             f"{sc['time_horizon_months']} months):")
    L.append("")
    L.append(f"- Requirement shortfall {o['baseline']['requirement_shortfall']:.3f} → "
             f"{o['hypothetical']['requirement_shortfall']:.3f} (lower = fewer stated requirements uncovered).")
    L.append(f"- Fits the time budget: {'yes' if o['effort']['fits_time_budget'] else 'no'} "
             f"(capacity {o['effort']['capacity_hours_within_horizon']:.0f} h).")
    L.append(f"- Share of the movement visible on the map: {o['movement']['share_of_movement_visible_on_map']}")
    alt = o["alternatives"]["same_budget"]
    L.append(f"- Same hours spent greedily would reach shortfall {alt['requirement_shortfall']:.3f}.")
    L.append("- Path alternatives (ordered by declared weights, not by likelihood):")
    for a in o["alternatives"]["paths"]:
        L.append(f"  - {' → '.join(a['path'])}: {a['months_range'][0]}–{a['months_range'][1]} months, hardest step "
                 f"has {a['entry_barriers_hardest_step']} in-role evidence barrier(s), fits horizon: {a['fits_time_horizon']}")
    L.append("- Risks:")
    for r in sc["risks"]:
        L.append(f"  - {_md_escape(r)}")
    L.append("")
    L.append("## 6. Sensitivity")
    L.append("")
    L.append(f"- Sensitive to: {', '.join(sens['summary']['sensitive_to']) or 'nothing tested'}")
    L.append(f"- Stable under: {', '.join(sens['summary']['stable_under']) or 'nothing tested'}")
    uw = sens["utility_weights"]
    L.append(f"- Utility weights: top-ranked role unchanged in {uw['top_choice_unchanged_share']:.0%} of "
             f"{uw['n_draws']} weight perturbations (ordering stability, not an outcome likelihood).")
    ms = sens["missing_salary"]
    L.append(f"- Missing salary: top choice identifiable without compensation: "
             f"{ms['top_choice_identifiable_without_compensation']}; unit is {ms['compensation_unit']}.")
    for key in ("seniority", "job_freshness", "source_volume", "lexicon", "normalization"):
        L.append(f"- {key}: {sens[key].get('verdict', 'n/a')} (min Kendall tau {sens[key].get('min_kendall_tau')})")
    L.append("")
    L.append("## 7. Scale")
    L.append("")
    st = summary["stress"]
    L.append(f"{st['n_postings']} synthetic vectors × {st['n_features']} features: fit {st['fit_seconds']} s, "
             f"bundle with diagnostics {st['bundle_seconds']} s on this machine (timings vary by hardware).")
    L.append("")
    L.append("## 8. Limits")
    L.append("")
    for line in summary["limits"]:
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


LIMITS = [
    "Every posting, employer, claim and compensation figure is fictional. No conclusion transfers to a real labour market.",
    "Role prevalences are design inputs of the generator; clusters recover them by construction.",
    "UMAP was not executed unless the comparison table says so. No UMAP metric is reported otherwise.",
    "Effort hours, evidence-level conventions and tenure assumptions are placeholders awaiting owner review.",
    "Thresholds (trustworthiness, Jaccard, instability ratio, Kendall tau) are reading conventions, not validated cut-offs.",
    "Mean imputation of unknown features understates variance; imputed counts are reported per point.",
    "Reviews by the same model family are not independent external validation.",
]


def run_demo(n: int = 1500, seed: int = 7, out_dir: str | Path | None = None,
             stress_n: int = 5000, sample_points: int = 200) -> dict[str, Any]:
    """Run the full offline demo. Returns the summary; writes artefacts if ``out_dir`` is given."""
    vocab = default_vocabulary()
    jobs = generate_postings(n, vocab.skill_names, seed=seed)
    engine = AtlasEngine.fit(jobs, vocab, seed=seed)
    claims = default_claims()
    cand = claims_to_vector(claims, vocab, CANDIDATE_REF, as_of=AS_OF)
    graph = build_transition_graph(engine)
    scenario = simulate_transition(engine, cand, "field-cto", DEMO_PLAN, time_horizon_months=12, graph=graph)
    scen_points = [p for p in scenario["outputs"]["map_points"] if p["representation_type"] == "scenario"]
    bundle = build_atlas_bundle(engine, cand, seed=seed)
    bundle["points"].extend({**p, "projection_id": bundle["projection"]["projection_id"]} for p in scen_points)
    small_engine = AtlasEngine.fit(jobs[:24], vocab, seed=seed, n_boot=20)
    t0 = time.perf_counter()
    big = AtlasEngine.fit(generate_postings(stress_n, vocab.skill_names, seed=seed + 1), vocab, seed=seed, n_boot=5)
    t1 = time.perf_counter()
    build_atlas_bundle(big, None, seed=seed, include_clusters=False)
    t2 = time.perf_counter()
    summary: dict[str, Any] = {
        "n_postings": n, "seed": seed,
        "bundle": bundle,
        "comparison": compare_projections(engine, seed=seed),
        "gap": analyze_gap(engine, cand, "field-cto"),
        "paths": plan_paths(engine, graph, cand, "field-cto"),
        "scenario": scenario,
        "sensitivity": run_sensitivity(SensitivityInputs(jobs, claims, CANDIDATE_REF, AS_OF, vocab, seed)),
        "held_out": held_out_check(jobs, seed),
        "drift": drift_check(n, seed),
        "small_sample": {"n_postings": 24, "n_roles": len(small_engine.profiles),
                         "unstable_roles": sorted(r for r, s in small_engine.stability.items() if s["unstable"])},
        "stress": {"n_postings": stress_n, "n_features": len(vocab.features),
                   "fit_seconds": round(t1 - t0, 2), "bundle_seconds": round(t2 - t1, 2)},
        "limits": LIMITS,
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        sample = build_atlas_bundle(engine, cand, seed=seed, max_job_points=sample_points)
        files = {
            "atlas_bundle.sample.json": sample,
            "pca_model.json": engine.model.to_dict(),
            "projection_comparison.json": summary["comparison"],
            "gap_field_cto.json": summary["gap"],
            "transition_graph.json": graph.to_dict(),
            "scenario_field_cto.json": scenario,
            "sensitivity.json": summary["sensitivity"],
            "held_out_and_drift.json": {"held_out": summary["held_out"], "drift": summary["drift"],
                                        "small_sample": summary["small_sample"]},
        }
        manifest: dict[str, Any] = {"generated_by": f"careertwin_atlas {__version__}", "seed": seed,
                                    "n_postings": n, "full_bundle_sha256": digest(bundle), "files": {}}
        for name, obj in files.items():
            text = json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
            (out / name).write_text(text, encoding="utf-8", newline="\n")
            manifest["files"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in (("atlas_map.svg", render_svg(engine, bundle) + "\n"),
                           ("DIAGNOSTICS_REPORT.md", render_markdown(summary))):
            (out / name).write_text(text, encoding="utf-8", newline="\n")
            manifest["files"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        manifest["note"] = ("Timings in the report and the last digits of floating-point results can differ "
                            "between machines; regenerate with: python -m careertwin_atlas demo --out <dir>")
        (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                           encoding="utf-8", newline="\n")
        summary["manifest"] = manifest
    return summary

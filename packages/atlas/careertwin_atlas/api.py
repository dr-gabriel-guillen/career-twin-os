"""Adapter for lane D (and E's harness): one JSON-serialisable bundle per projection.

``build_atlas_bundle`` returns AtlasPoint objects that validate against the frozen
``contracts/atlas-point.schema.json`` plus a diagnostics envelope owned by lane C
(``packages/atlas/schemas/atlas-bundle.schema.json``). The bundle states, at top level and on
every point, that map geometry is not the match metric.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

import numpy as np

from . import __version__
from .clustering import cluster_stability, label_clusters
from .diagnostics import diagnose_projection, seed_variation
from .engine import AtlasEngine
from .errors import AtlasError, InvalidInputError, OptionalDependencyMissing
from .features import STATE_STATED, FeatureVector, parse_utc
from .pca import fit_pca, subspace_agreement
from .points import MAP_IS_NOT_FIT, PROJECTION_METHODS, atlas_point
from .projectors import (build_interpretable_axes, random_projection, umap_available, umap_project)
from .vocabulary import VOCAB_VERSION

BUNDLE_SCHEMA_VERSION = "atlas-bundle-0.1.0"
LOW_POINT_QUALITY = 0.3
HIGH_RESIDUAL = 0.8


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _project(engine: AtlasEngine, method: str, dims: int, seed: int) -> dict[str, Any]:
    if method == "pca":
        if dims > engine.model.n_components:
            raise InvalidInputError(f"engine was fitted with {engine.model.n_components} components; "
                                    f"refit with n_components={dims} for a {dims}-D map")
        comps = engine.model.components[:dims]
        evr = engine.model.explained_variance_ratio[:dims]
        axes_out = []
        for a in engine.model.loadings()[:dims]:
            up = ", ".join(x["feature"] for x in a["positive"][:3]) or "nothing prominent"
            down = ", ".join(x["feature"] for x in a["negative"][:3]) or "nothing prominent"
            axes_out.append({**a, "label": f"PCA axis {a['component']}",
                             "description": f"Explains {a['explained_variance_ratio']:.1%} of total variance. "
                                            f"Increases with: {up}. Decreases with: {down}."})
        return {"coords": engine.coords[:, :dims], "projection_id": f"pca-{engine.model.model_id}-{dims}d",
                "evr": evr, "apply": lambda x: ((x - engine.model.mean) / engine.model.scale) @ comps.T,
                "axes": axes_out, "warnings": list(engine.model.warnings)}
    if method == "interpretable-axes":
        axes = build_interpretable_axes(engine.vocab, dims)
        axes_out = [{**a, "label": a["name"], "explained_variance_ratio": None,
                     "description": f"{a['positive_pole']} (+) versus {a['negative_pole']} (-); {a['formula']}. "
                                    "Hand-defined axis, not a variance decomposition."}
                    for a in axes.definitions]
        return {"coords": axes.transform(engine.X), "projection_id": axes.projection_id + f"-{dims}d",
                "evr": None, "apply": lambda x: axes.transform(x)[0],
                "axes": axes_out, "warnings": list(axes.warnings),
                "axis_cosines": axes.axis_cosines()}
    if method == "umap":
        coords = umap_project(engine.Z, seed=seed, n_components=dims)
        axes_out = [{"label": f"UMAP dimension {i + 1}", "explained_variance_ratio": None,
                     "description": "No interpretable meaning: UMAP axes are arbitrary."} for i in range(dims)]
        return {"coords": coords, "projection_id": f"umap-{engine.model.model_id}-s{seed}-{dims}d",
                "evr": None, "apply": None, "axes": axes_out,
                "warnings": ["umap is stochastic and non-linear: axes have no meaning, distances "
                             "between clusters are not interpretable, held-out points are not placed"]}
    raise InvalidInputError(f"method must be one of {PROJECTION_METHODS}")


def _information_loss(diag: dict[str, Any], residual: np.ndarray | None, dims: int) -> dict[str, Any]:
    parts = []
    cum = diag["explained_variance_cumulative"]
    if cum is not None:
        parts.append(f"This {dims}-D map keeps {cum:.1%} of the total variance across "
                     f"{diag['n_features']} features; {1 - cum:.1%} is not visible.")
    if diag["knn_recall_mean"] is not None:
        parts.append(f"On average {diag['knn_recall_mean']:.0%} of a point's {diag['k_neighbours']} "
                     "nearest map neighbours are true nearest neighbours in the full feature space.")
    if diag["distance_spearman"] is not None:
        parts.append(f"Rank agreement between map distances and full-space distances is "
                     f"{diag['distance_spearman']:.2f} (1.00 would be perfect).")
    median_res = None
    if residual is not None and residual.size:
        median_res = float(np.median(residual))
        parts.append(f"For the median posting, {median_res:.0%} of its squared distance from the "
                     "average posting lies outside the map.")
    parts.append("Read the map as a sketch. Use full-space distances and the gap analysis for decisions.")
    return {"statement": " ".join(parts), "variance_not_shown": diag["variance_not_shown"],
            "median_residual_share": None if median_res is None else round(median_res, 6)}


def _evidence_basis(vec: FeatureVector) -> str:
    """Aggregate claim status behind a candidate-derived point, read from provenance only."""
    statuses = set()
    for refs in vec.provenance.values():
        for ref in refs:
            # format "claim:<id>:<status>[:expired]"; ids may contain ":" (review cycle 1, F7),
            # so the status is read from the right.
            if not ref.startswith("claim:"):
                continue
            body = ref[len("claim:"):]
            expired = body.endswith(":expired")
            if expired:
                body = body[: -len(":expired")]
            status = body.rsplit(":", 1)[-1]
            statuses.add("unverified" if expired and status != "contradicted" else status)
    known = statuses & {"supported", "self-reported", "contradicted", "unverified"}
    if not known:
        return "not-applicable"
    return next(iter(known)) if len(known) == 1 else "mixed"


def _top_features(vec: FeatureVector, limit: int = 5) -> list[str]:
    pairs = [(float(v), n) for n, v, s in zip(vec.feature_names, vec.values, vec.states)
             if s == STATE_STATED and not n.startswith("ctx:")]
    return [n for _, n in sorted(pairs, key=lambda t: (-t[0], t[1]))[:limit]]


def build_atlas_bundle(
    engine: AtlasEngine,
    candidate: FeatureVector | None = None,
    method: str = "pca",
    dims: int = 2,
    k: int = 10,
    seed: int = 0,
    include_clusters: bool = True,
    cluster_boot: int = 10,
    max_job_points: int | None = None,
    extra_vectors: Sequence[FeatureVector] = (),
) -> dict[str, Any]:
    """Build the atlas payload for one projection method.

    ``max_job_points`` only limits how many job points are *emitted* (deterministic stride);
    diagnostics are always computed on every fitted posting.
    """
    if dims not in (2, 3):
        raise InvalidInputError("dims must be 2 or 3")
    if method not in PROJECTION_METHODS:
        raise InvalidInputError(f"method must be one of {PROJECTION_METHODS}")
    if max_job_points is not None and (isinstance(max_job_points, bool)
                                       or not isinstance(max_job_points, int) or max_job_points < 0):
        raise InvalidInputError("max_job_points must be a non-negative integer or None")
    for name, value, lo in (("cluster_boot", cluster_boot, 1), ("k", k, 1), ("seed", seed, 0)):
        if isinstance(value, bool) or not isinstance(value, int) or value < lo:
            raise InvalidInputError(f"{name} must be an integer >= {lo}")
    proj = _project(engine, method, dims, seed)
    coords = proj["coords"]
    pid = proj["projection_id"]
    diag = diagnose_projection(engine.Z, coords, k=k, seed=seed, explained_variance_ratio=proj["evr"])
    recall = diag.pop("per_point_knn_recall")
    residual = engine.residual_share if method == "pca" and dims == engine.model.n_components else None

    n = len(engine.matrix)
    emit = range(n)
    if max_job_points is not None and max_job_points < n:
        step = n / max(max_job_points, 1)
        emit = sorted({int(i * step) for i in range(max_job_points)})
    points: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    unplaced: list[dict[str, Any]] = [dict(u) for u in engine.unplaced]
    for i in emit:
        v = engine.matrix.vectors[i]
        n_stated = int(((v.states == STATE_STATED) & np.array([not f.startswith("ctx:") for f in v.feature_names])).sum())
        entities.append({"entity_id": v.entity_id, "representation_type": "job",
                         "role_family": v.meta.get("role_family"), "evidence_basis": "not-applicable",
                         "job_id": v.entity_id, "scenario_id": None,
                         "detail": f"{v.meta.get('status', 'unknown')} posting; {n_stated} stated skill(s); "
                                   f"{int(engine.n_imputed[i])} unknown feature(s)",
                         "top_skills": _top_features(v)})
        warn = [MAP_IS_NOT_FIT]
        if engine.n_imputed[i]:
            warn.append(f"imputed-unknown-features:{int(engine.n_imputed[i])}")
        q = recall[i]
        if q is not None and q < LOW_POINT_QUALITY:
            warn.append("low-neighbourhood-quality: map neighbours are mostly not true neighbours")
        if residual is not None and residual[i] > HIGH_RESIDUAL:
            warn.append("poorly-represented: most of this point's variation is outside the map")
        if v.unmapped_terms:
            warn.append(f"unmapped-terms:{len(v.unmapped_terms)}")
        points.append(atlas_point(v.entity_id, v.label, coords[i], v.vector_ref, "job", method, pid,
                                  q, None, warn))

    centroids = []
    can_place = proj["apply"] is not None
    for fam in sorted(engine.profiles):
        p = engine.profiles[fam]
        stab = engine.stability[fam]
        entry = {"role": fam, "label": p.label, "n_postings": p.n_postings,
                 "unstable": stab["unstable"], "reasons": stab["reasons"],
                 "instability_ratio": stab["instability_ratio"],
                 "core_features": p.core_features()}
        centroids.append(entry)
        if not can_place:
            continue
        warn = [MAP_IS_NOT_FIT]
        if stab["unstable"]:
            warn.append("unstable-centroid: " + "; ".join(stab["reasons"]))
        vec = p.as_vector(engine.matrix.vocab_digest)
        points.append(atlas_point(vec.entity_id, p.label, proj["apply"](p.centroid), vec.vector_ref,
                                  "role-centroid", method, pid, None, None, warn))
        entities.append({"entity_id": vec.entity_id, "representation_type": "role-centroid", "role_family": fam,
                         "evidence_basis": "not-applicable", "job_id": None, "scenario_id": None,
                         "detail": f"centroid of {p.n_postings} posting(s)"
                                   + ("; unstable" if stab["unstable"] else ""),
                         "top_skills": p.core_features()[:5]})

    candidate_block = None
    placed_ids: set[str] = set()
    for vec in ([candidate] if candidate is not None else []) + list(extra_vectors):
        engine.check_vector(vec)
        if not can_place:
            continue
        if not engine.has_observed_skills(vec):
            unplaced.append({"entity_id": vec.entity_id, "label": vec.label, "source_vector_ref": vec.vector_ref,
                             "reason": "no observed skill features: unknown, not placed on the map"})
            continue
        placed_ids.add(vec.entity_id)
        scenario_id = vec.entity_id.rsplit("#", 1)[1] if vec.kind == "scenario" and "#" in vec.entity_id else None
        entities.append({"entity_id": vec.entity_id,
                         "representation_type": vec.kind if vec.kind in ("candidate", "scenario") else "candidate",
                         "role_family": None, "evidence_basis": _evidence_basis(vec), "job_id": None,
                         "scenario_id": scenario_id,
                         "detail": "hypothetical evidence after planned actions" if vec.kind == "scenario"
                         else "evidence on file (claims), held-out point",
                         "top_skills": _top_features(vec)})
        x = engine.imputed(vec)
        warn = [MAP_IS_NOT_FIT]
        n_unknown = int(np.isnan(vec.values).sum())
        if n_unknown:
            warn.append(f"imputed-unknown-features:{n_unknown}")
        if vec.kind == "scenario":
            warn.append("hypothetical-scenario")
        rep_type = vec.kind if vec.kind in ("candidate", "scenario", "job", "role-centroid") else "candidate"
        points.append(atlas_point(vec.entity_id, vec.label, proj["apply"](x), vec.vector_ref, rep_type,
                                  method, pid, None, "role_table:requirement_shortfall", warn))
    if candidate is not None:
        assessable = engine.has_observed_skills(candidate)
        candidate_block = {
            "entity_id": candidate.entity_id, "placed_on_map": candidate.entity_id in placed_ids,
            "role_table": [{k2: (round(v2, 6) if isinstance(v2, float) else v2) for k2, v2 in r.items()}
                           for r in engine.role_table(candidate)] if assessable else None,
            "role_table_status": "assessed" if assessable else
            "not-assessable: the candidate has no observed skill evidence",
            "role_table_note": "Ordered by requirement_shortfall (full feature space). map_distance is "
                               "included only to expose where the picture disagrees.",
            "warnings": list(candidate.warnings),
        }

    clusters = None
    if include_clusters and engine.profiles:
        k_c = max(2, min(len(engine.profiles), n - 1))
        stab = cluster_stability(engine.Z, k_c, seed=seed, n_boot=cluster_boot)
        fams = [v.meta.get("role_family") for v in engine.matrix.vectors]
        described = label_clusters(engine.X, stab["labels"], engine.matrix.feature_names, fams)
        for entry in described:
            c = entry["cluster"]
            entry["bootstrap_jaccard_mean"] = round(float(stab["jaccard_mean"][c]), 4)
            entry["stability"] = stab["verdict"][c]
        clusters = {"k": stab["k"], "computed_in": "full feature space (never on map coordinates)",
                    "n_boot": stab["n_boot"], "clusters": described, "thresholds": stab["thresholds"],
                    "warnings": stab["warnings"]}

    warnings = list(dict.fromkeys(list(engine.warnings) + proj["warnings"] + diag["warnings"]))
    sources: dict[str, int] = {}
    for v in engine.matrix.vectors:
        src = str(v.meta.get("source") or "unknown")
        sources[src] = sources.get(src, 0) + 1
    synthetic_only = set(sources) == {"synthetic"}
    if not synthetic_only:
        warnings.append("inputs are not all marked source='synthetic'; this package cannot verify provenance "
                        "and makes no claim that the data is synthetic")
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_by": f"careertwin_atlas {__version__}",
        "synthetic_only": synthetic_only,
        "input_sources": dict(sorted(sources.items())),
        "flags": {
            "two_d_geometry_is_not_match_metric": True,
            "not_hiring_probability": True,
            "map_distortion_flagged": bool(diag["flags"]),
        },
        "projection": {
            "projection_id": pid, "method": method, "n_map_dims": dims,
            "model_id": engine.model.model_id, "vocab_version": engine.vocab.version,
            "vocab_digest": engine.matrix.vocab_digest, "standardize": engine.model.standardize,
            "n_points_fitted": n, "n_job_points_emitted": len(emit), "axes": proj["axes"],
            "axis_cosines": proj.get("axis_cosines"),
            "held_out_points_supported": bool(can_place), "seed": seed,
        },
        "points": points,
        "entities": entities,
        "unplaced": unplaced,
        "diagnostics": diag,
        "information_loss": _information_loss(diag, residual, dims),
        "role_centroids": centroids,
        "clusters": clusters,
        "candidate": candidate_block,
        "skipped_inputs": [dict(s) for s in engine.skipped],
        "warnings": warnings,
    }
    return bundle


def projection_view(bundle: dict[str, Any], generated_at: str,
                    scenarios: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """Reshape a bundle into the projection document lane D's atlas page consumes.

    Field names follow lane D's ``atlas-projection/0.1-proposed`` stand-in (axes with ``label`` /
    ``description``, metrics as ``{k, value}``, ``entities`` and ``paths``), plus lane C's flags and
    information-loss statement. ``generated_at`` is supplied by the caller: this package never
    reads the clock. Each scenario adds its hypothetical point and a candidate -> scenario path.
    """
    try:
        return _projection_view(bundle, generated_at, scenarios)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise InvalidInputError(f"malformed bundle or scenario for projection_view: {exc!r}") from exc


def _projection_view(bundle: dict[str, Any], generated_at: str,
                     scenarios: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    if parse_utc(generated_at) is None:
        raise InvalidInputError("generated_at must be an ISO-8601 timestamp with a UTC offset")
    if not isinstance(bundle, dict) or bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise InvalidInputError(f"expected a {BUNDLE_SCHEMA_VERSION} bundle")
    proj, diag = bundle["projection"], bundle["diagnostics"]
    k = diag["k_neighbours"]

    def metric(value: float | None) -> dict[str, Any] | None:
        return None if value is None else {"k": k, "value": round(float(value), 6)}

    points = list(bundle["points"])
    entities = list(bundle["entities"])
    have = {p["entity_id"] for p in points}
    paths = []
    for sc in scenarios:
        out = sc.get("outputs", {}) if isinstance(sc, dict) else {}
        scen_method = {p.get("projection_method") for p in out.get("map_points", [])}
        if out.get("model_ref", {}).get("projection_id") != proj["projection_id"] or scen_method != {proj["method"]}:
            raise InvalidInputError(f"scenario {sc.get('id') if isinstance(sc, dict) else sc!r} was computed for "
                                    "another projection; rebuild it with this engine and method")
        cand_pt, scen_pt = out["map_points"][0], out["map_points"][1]
        for pt, kind in ((cand_pt, "candidate"), (scen_pt, "scenario")):
            if pt["entity_id"] not in have:
                have.add(pt["entity_id"])
                points.append(pt)
                entities.append({"entity_id": pt["entity_id"], "representation_type": kind, "role_family": None,
                                 "evidence_basis": "not-applicable", "job_id": None,
                                 "scenario_id": sc["id"] if kind == "scenario" else None,
                                 "detail": "hypothetical evidence after planned actions" if kind == "scenario"
                                 else "evidence on file (claims), held-out point",
                                 "top_skills": [a["feature"] for a in out["applied_actions"]][:5]
                                 if kind == "scenario" else []})
        paths.append({"id": f"path:{sc['id']}", "from_entity_id": cand_pt["entity_id"],
                      "to_entity_id": scen_pt["entity_id"], "scenario_id": sc["id"],
                      "label": f"Hypothetical move toward {out['target_label']} "
                               f"({out['movement']['share_of_movement_visible_on_map']} of it visible on the map)"})
    return {
        "schema_version": "atlas-projection/0.1",
        "projection_id": proj["projection_id"],
        "method": proj["method"],
        "generated_at": generated_at,
        "input": {"representation": f"skill-evidence vectors, vocabulary {proj['vocab_version']} "
                                    f"({proj['vocab_digest']})",
                  "n_points": proj["n_points_fitted"], "n_dims": diag["n_features"]},
        "axes": [{"label": a["label"], "explained_variance_ratio": a.get("explained_variance_ratio"),
                  "description": a["description"]} for a in proj["axes"]],
        "diagnostics": {
            "cumulative_explained_variance": diag["explained_variance_cumulative"],
            "trustworthiness": metric(diag["trustworthiness"]),
            "continuity": metric(diag["continuity"]),
            "knn_recall": metric(diag["knn_recall_mean"]),
            "distance_spearman": diag["distance_spearman"],
            "normalized_stress": diag["normalized_stress"],
            "flags": list(diag["flags"]),
        },
        "warnings": [bundle["information_loss"]["statement"]] + list(bundle["warnings"]),
        "points": points,
        "entities": entities,
        "paths": paths,
        "flags": dict(bundle["flags"]),
        "synthetic_only": bundle["synthetic_only"],
        "unplaced": list(bundle["unplaced"]),
    }


def compare_projections(engine: AtlasEngine, k: int = 10, seeds: Sequence[int] = (0, 1, 2, 3, 4),
                        n_boot: int = 20, seed: int = 0) -> dict[str, Any]:
    """Compare PCA, interpretable axes, UMAP (if installed) and a random baseline on fidelity.

    Every method is scored against the same full-space distances. UMAP is reported as
    ``not-executed`` when the optional package is absent; no numbers are invented for it.
    """
    Z = engine.Z
    out: dict[str, Any] = {"k": k, "n_points": int(Z.shape[0]), "methods": {}}

    def strip(d: dict[str, Any]) -> dict[str, Any]:
        keep = ("explained_variance_cumulative", "trustworthiness", "continuity", "knn_recall_mean",
                "distance_spearman", "normalized_stress", "flags")
        return {key: d[key] for key in keep}

    pca2 = engine.coords[:, :2]
    entry = strip(diagnose_projection(Z, pca2, k, seed, engine.model.explained_variance_ratio[:2]))
    rng = np.random.default_rng(seed)
    agree = []
    for _ in range(n_boot):
        idx = rng.integers(0, Z.shape[0], size=Z.shape[0])
        try:
            boot = fit_pca(engine.matrix.X[idx], engine.matrix.feature_names, engine.matrix.vocab_digest,
                           n_components=2, standardize=engine.model.standardize)
            agree.append(subspace_agreement(engine.model, boot))
        except AtlasError:  # a degenerate resample is itself evidence of instability
            agree.append(0.0)
    entry["status"] = "executed"
    entry["seed_variation"] = {"deterministic": True, "note": "PCA has no random seed"}
    entry["resampling_stability"] = {
        "n_boot": n_boot, "plane_agreement_min": round(float(min(agree)), 6) if agree else None,
        "plane_agreement_mean": round(float(np.mean(agree)), 6) if agree else None,
        "meaning": "smallest cosine of principal angles between the fitted plane and planes "
                   "refitted on bootstrap resamples (1 = same plane)"}
    out["methods"]["pca"] = entry

    axes = build_interpretable_axes(engine.vocab, 2)
    entry = strip(diagnose_projection(Z, axes.transform(engine.X), k, seed))
    entry.update({"status": "executed", "seed_variation": {"deterministic": True},
                  "axis_cosines": axes.axis_cosines()})
    out["methods"]["interpretable-axes"] = entry

    seeds = [int(s) for s in seeds]
    rp = strip(diagnose_projection(Z, random_projection(Z, seeds[0]), k, seed))
    rp.update({"status": "executed", "role": "diagnostic baseline only; never emitted as AtlasPoint",
               "seed_variation": seed_variation(lambda s: random_projection(Z, s), seeds, k)})
    out["methods"]["random-projection-baseline"] = rp

    if umap_available():
        try:
            emb = umap_project(Z, seed=seeds[0])
            um = strip(diagnose_projection(Z, emb, k, seed))
            um.update({"status": "executed",
                       "seed_variation": seed_variation(lambda s: umap_project(Z, seed=s), seeds, k)})
        except OptionalDependencyMissing as exc:
            um = {"status": "not-executed", "reason": str(exc)}
    else:
        um = {"status": "not-executed",
              "reason": "optional package umap-learn is not installed in this environment; "
                        "no UMAP metrics are reported"}
    out["methods"]["umap"] = um
    out["note"] = ("All methods are scored against the same full-space Euclidean distances. Higher "
                   "trustworthiness/continuity/recall and lower stress mean a more faithful picture; "
                   "none of these is a match or hiring metric.")
    return out

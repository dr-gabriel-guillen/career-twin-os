"""WS9 — independent atlas quality checks.

Lane C projects high-dimensional vectors to 2D/3D. Lane E recomputes, from the
ORIGINAL vectors, how faithful that picture is and whether the reported numbers
are honest:

* trustworthiness / continuity / k-NN preservation against the original metric,
* an independent reference PCA (SVD) to verify explained variance and coordinates,
* fit-leakage detection (held-out points must not influence the fitted axes),
* "nearest role" claims must come from the original space, not from 2D distances.

The diagnostics shape checked here is lane E's proposal (RFC on Issue #4); it is
not a frozen contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from . import contracts

Matrix = NDArray[np.float64]
EPS = 1e-9


@dataclass(frozen=True)
class AtlasFinding:
    code: str
    severity: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code} [{self.severity}] {self.detail}"


@dataclass(frozen=True)
class PcaFit:
    mean: Matrix
    scale: Matrix
    components: Matrix  # (n_components, n_features)
    explained_variance_ratio: Matrix
    coords: Matrix  # all rows projected


def as_matrix(bundle: dict[str, Any]) -> tuple[list[str], list[str], Matrix]:
    """(refs, entity_ids, matrix) from a vectors bundle; raises ValueError on ragged/non-finite input."""
    vectors = bundle.get("vectors")
    width = len(bundle.get("vocabulary", []))
    if not isinstance(vectors, list) or not vectors or width == 0:
        raise ValueError("vectors bundle is empty or has no vocabulary")
    rows = []
    for item in vectors:
        values = item.get("values")
        if not isinstance(values, list) or len(values) != width:
            raise ValueError(f"{item.get('ref')}: vector length != vocabulary length {width}")
        rows.append(values)
    matrix = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise ValueError("vectors contain NaN or infinity")
    refs = [str(v.get("ref")) for v in vectors]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate vector refs")
    return refs, [str(v.get("entity_id")) for v in vectors], matrix


def pairwise_distances(x: Matrix) -> Matrix:
    squared = np.sum(x * x, axis=1)
    d2 = squared[:, None] + squared[None, :] - 2.0 * (x @ x.T)
    np.maximum(d2, 0.0, out=d2)
    d: Matrix = np.sqrt(d2)
    np.fill_diagonal(d, 0.0)
    return d


def _ranks(d: Matrix) -> NDArray[np.int64]:
    """rank[i, j] = position of j among i's neighbours (1 = nearest, self excluded -> 0)."""
    n = d.shape[0]
    masked = d.copy()
    np.fill_diagonal(masked, -1.0)  # self sorts first
    order = np.argsort(masked, axis=1, kind="stable")
    ranks = np.empty((n, n), dtype=np.int64)
    ranks[np.arange(n)[:, None], order] = np.arange(n)[None, :]
    return ranks


def _max_k(n: int, k: int) -> int:
    return max(1, min(k, (n - 2) // 2))


def trustworthiness(high: Matrix, low: Matrix, k: int = 10) -> float:
    """1.0 when every low-dimensional neighbour is also a neighbour in the original space."""
    n = high.shape[0]
    if n < 5:
        raise ValueError("need at least 5 points")
    k = _max_k(n, k)
    rank_high = _ranks(pairwise_distances(high))
    rank_low = _ranks(pairwise_distances(low))
    intruders = (rank_low >= 1) & (rank_low <= k) & (rank_high > k)
    penalty = float(np.sum((rank_high - k)[intruders]))
    return float(1.0 - 2.0 / (n * k * (2 * n - 3 * k - 1)) * penalty)


def continuity(high: Matrix, low: Matrix, k: int = 10) -> float:
    """1.0 when every original neighbour is still a neighbour in the projection."""
    return trustworthiness(low, high, k)


def knn_preservation(high: Matrix, low: Matrix, k: int = 10) -> Matrix:
    """Per point: share of its k projected neighbours that are true neighbours in the original space.

    Tie-tolerant: a projected neighbour counts if it is no farther (in the original
    space) than the point's k-th nearest original neighbour.
    """
    n = high.shape[0]
    k = _max_k(n, k)
    d_high = pairwise_distances(high)
    d_low = pairwise_distances(low)
    np.fill_diagonal(d_high, np.inf)
    np.fill_diagonal(d_low, np.inf)
    kth = np.sort(d_high, axis=1)[:, k - 1]
    low_nn = np.argsort(d_low, axis=1, kind="stable")[:, :k]
    hits = d_high[np.arange(n)[:, None], low_nn] <= kth[:, None] + EPS
    result: Matrix = hits.mean(axis=1)
    return result


def reference_pca(
    x: Matrix, n_components: int = 2, fit_rows: list[int] | None = None, standardize: bool = False
) -> PcaFit:
    """Plain SVD PCA. The model is fitted on ``fit_rows`` only and then applied to every row."""
    train = x if fit_rows is None else x[fit_rows]
    if train.shape[0] < 2:
        raise ValueError("need at least 2 rows to fit PCA")
    mean = train.mean(axis=0)
    scale = np.ones(x.shape[1])
    if standardize:
        std = train.std(axis=0, ddof=0)
        scale = np.where(std > EPS, std, 1.0)
    centred = (train - mean) / scale
    _, singular, vt = np.linalg.svd(centred, full_matrices=False)
    variance = singular**2
    total = float(variance.sum())
    ratio = variance / total if total > EPS else np.zeros_like(variance)
    components = vt[:n_components]
    if components.shape[0] < n_components:
        raise ValueError("not enough rank for the requested number of components")
    coords = ((x - mean) / scale) @ components.T
    return PcaFit(mean, scale, components, ratio[:n_components], coords)


def procrustes_disparity(a: Matrix, b: Matrix) -> float:
    """0 when ``b`` equals ``a`` up to translation, rotation/reflection and uniform scale; at most 1."""
    a0 = a - a.mean(axis=0)
    b0 = b - b.mean(axis=0)
    norm_a, norm_b = float(np.linalg.norm(a0)), float(np.linalg.norm(b0))
    if norm_a < EPS or norm_b < EPS:
        return 0.0 if norm_a < EPS and norm_b < EPS else 1.0
    a0, b0 = a0 / norm_a, b0 / norm_b
    singular = np.linalg.svd(a0.T @ b0, compute_uv=False)
    return float(max(0.0, 1.0 - float(singular.sum()) ** 2))


def _tie_tolerant_overlap(d: Matrix, i: int, claimed: list[int]) -> float:
    if not claimed:
        return 1.0
    row = d[i].copy()
    row[i] = np.inf
    kth = float(np.sort(row)[len(claimed) - 1])
    return float(sum(bool(row[j] <= kth + EPS) for j in claimed) / len(claimed))


def verify_atlas(
    points: list[dict[str, Any]],
    vectors: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    *,
    k: int = 10,
    tolerance: float = 0.15,
) -> tuple[list[AtlasFinding], dict[str, float]]:
    """Findings plus lane E's own recomputed metrics for a set of AtlasPoints."""
    out: list[AtlasFinding] = []
    metrics: dict[str, float] = {}
    for point in points:
        for problem in contracts.check("atlas-point", point):
            out.append(AtlasFinding("ATLAS-POINT-INVALID", "high", f"{point.get('entity_id')}: {problem}"))
    try:
        refs, _, high_all = as_matrix(vectors)
    except ValueError as exc:
        return [*out, AtlasFinding("ATLAS-VECTORS-INVALID", "high", str(exc))], metrics
    index = {ref: i for i, ref in enumerate(refs)}
    entities = [str(p.get("entity_id")) for p in points]
    if len(set(entities)) != len(entities):
        out.append(
            AtlasFinding(
                "ATLAS-DUPLICATE-ENTITY", "high", "an entity appears more than once in one projection"
            )
        )
    if (
        len({p.get("projection_id") for p in points}) > 1
        or len({len(p.get("coords", [])) for p in points}) > 1
    ):
        out.append(
            AtlasFinding("ATLAS-MIXED-PROJECTION", "high", "points mix projection ids or dimensionalities")
        )
        return out, metrics
    dangling = [str(p.get("source_vector_ref")) for p in points if p.get("source_vector_ref") not in index]
    if dangling:
        out.append(
            AtlasFinding(
                "ATLAS-DANGLING-VECTOR",
                "high",
                f"{len(dangling)} unresolved source_vector_ref, e.g. {dangling[0]!r}",
            )
        )
        return out, metrics
    if out:
        return out, metrics
    rows = [index[str(p["source_vector_ref"])] for p in points]
    high = high_all[rows]
    low = np.asarray([p["coords"] for p in points], dtype=np.float64)
    n = len(points)
    if n < 12:
        out.append(
            AtlasFinding(
                "ATLAS-TOO-FEW-POINTS", "low", f"{n} points: neighbourhood metrics are not meaningful"
            )
        )
        return out, metrics
    k = _max_k(n, k)
    trust = trustworthiness(high, low, k)
    cont = continuity(high, low, k)
    preserved = knn_preservation(high, low, k)
    metrics.update(
        {
            "k": float(k),
            "trustworthiness": trust,
            "continuity": cont,
            "knn_preservation_mean": float(preserved.mean()),
        }
    )

    reported = [p.get("neighborhood_quality") for p in points]
    if all(isinstance(r, (int, float)) for r in reported):
        mean_reported = float(np.mean([float(r) for r in reported if r is not None]))
        metrics["neighborhood_quality_reported_mean"] = mean_reported
        if mean_reported > float(preserved.mean()) + tolerance:
            out.append(
                AtlasFinding(
                    "ATLAS-QUALITY-OVERSTATED",
                    "high",
                    f"mean reported neighborhood_quality {mean_reported:.3f} vs recomputed k-NN preservation "
                    f"{preserved.mean():.3f} (k={k}); definition pending RFC",
                )
            )
    low_fidelity = trust < 0.9 or float(preserved.mean()) < 0.5
    if low_fidelity and not any(p.get("warnings") for p in points):
        out.append(
            AtlasFinding(
                "ATLAS-LOW-FIDELITY-NO-WARNING",
                "high",
                f"trustworthiness {trust:.3f}, k-NN preservation {preserved.mean():.3f} and no point carries a distortion warning",
            )
        )
    if diagnostics is None:
        out.append(AtlasFinding("ATLAS-DIAGNOSTICS-MISSING", "medium", "no projection diagnostics supplied"))
        return out, metrics

    for name, mine in (("trustworthiness", trust), ("continuity", cont)):
        theirs = diagnostics.get(name)
        if isinstance(theirs, (int, float)) and abs(float(theirs) - mine) > 0.05:
            out.append(
                AtlasFinding(
                    "ATLAS-METRIC-MISREPORTED",
                    "high",
                    f"{name}: reported {theirs:.3f}, recomputed {mine:.3f} (k={k})",
                )
            )
    method = {p.get("projection_method") for p in points}
    if method == {"pca"}:
        standardize = diagnostics.get("preprocessing") == "standardize"
        fit_refs = diagnostics.get("fit_refs")
        fit_rows = None
        if isinstance(fit_refs, list) and fit_refs:
            unknown = [r for r in fit_refs if r not in index]
            if unknown:
                out.append(
                    AtlasFinding(
                        "ATLAS-DANGLING-VECTOR", "high", f"fit_refs contains unknown ref {unknown[0]!r}"
                    )
                )
                return out, metrics
            fit_rows = [index[r] for r in fit_refs]
        dims = low.shape[1]
        try:
            declared = reference_pca(high_all, dims, fit_rows, standardize)
        except (ValueError, np.linalg.LinAlgError) as exc:
            out.append(AtlasFinding("ATLAS-PCA-NOT-REPRODUCIBLE", "medium", f"reference PCA failed: {exc}"))
            return out, metrics
        d_declared = procrustes_disparity(declared.coords[rows], low)
        metrics["pca_disparity_vs_declared_fit"] = d_declared
        evr = diagnostics.get("explained_variance_ratio")
        if isinstance(evr, list) and len(evr) == dims and all(isinstance(v, (int, float)) for v in evr):
            gap = float(np.max(np.abs(np.asarray(evr, dtype=np.float64) - declared.explained_variance_ratio)))
            metrics["explained_variance_gap"] = gap
            if gap > 0.02:
                out.append(
                    AtlasFinding(
                        "ATLAS-VARIANCE-MISREPORTED",
                        "high",
                        f"explained_variance_ratio reported {[round(float(v), 3) for v in evr]}, reference PCA gives "
                        f"{[round(float(v), 3) for v in declared.explained_variance_ratio]}",
                    )
                )
        else:
            out.append(
                AtlasFinding(
                    "ATLAS-DIAGNOSTICS-MISSING", "medium", "explained_variance_ratio absent or wrong length"
                )
            )
        if fit_rows is not None and len(set(fit_rows)) < len(set(rows) | set(fit_rows)):
            everything = sorted(set(rows) | set(fit_rows))
            leaked = reference_pca(high_all, dims, everything, standardize)
            d_leaked = procrustes_disparity(leaked.coords[rows], low)
            metrics["pca_disparity_vs_fit_on_all"] = d_leaked
            if d_declared > 1e-6 and d_leaked < 0.5 * d_declared:
                out.append(
                    AtlasFinding(
                        "ATLAS-FIT-LEAKAGE",
                        "high",
                        f"coordinates match a PCA fitted on ALL points (disparity {d_leaked:.2e}) better than one "
                        f"fitted on the declared fit set (disparity {d_declared:.2e}): held-out points influenced the axes",
                    )
                )
                return out, metrics
        if d_declared > 0.01:
            out.append(
                AtlasFinding(
                    "ATLAS-PCA-NOT-REPRODUCIBLE",
                    "medium",
                    f"coordinates differ from a reference PCA on the declared inputs (Procrustes disparity {d_declared:.3f}); "
                    "preprocessing must be stated in diagnostics",
                )
            )
    d_high = pairwise_distances(high)
    d_low = pairwise_distances(low)
    position = {entity: i for i, entity in enumerate(entities)}
    for claim in diagnostics.get("neighbor_claims", []) or []:
        i = position.get(str(claim.get("entity_id")))
        claimed = [position[str(e)] for e in claim.get("neighbors", []) if str(e) in position]
        if i is None or not claimed:
            out.append(
                AtlasFinding("ATLAS-NEIGHBOR-CLAIM-UNRESOLVED", "medium", f"{claim.get('entity_id')!r}")
            )
            continue
        in_high = _tie_tolerant_overlap(d_high, i, claimed)
        in_low = _tie_tolerant_overlap(d_low, i, claimed)
        if in_high < 0.6 and in_low >= 0.9:
            out.append(
                AtlasFinding(
                    "ATLAS-NEIGHBOR-CLAIM-FROM-2D",
                    "high",
                    f"{claim.get('entity_id')}: {in_low:.0%} of claimed neighbours are 2D neighbours but only "
                    f"{in_high:.0%} are neighbours in the original space",
                )
            )
        elif in_high < 0.6:
            out.append(
                AtlasFinding(
                    "ATLAS-NEIGHBOR-CLAIM-WRONG",
                    "high",
                    f"{claim.get('entity_id')}: {in_high:.0%} overlap with original-space neighbours",
                )
            )
    return out, metrics


def build_reference_atlas(
    vectors: dict[str, Any],
    labels: dict[str, str],
    projection_id: str,
    fit_refs: list[str] | None = None,
    k: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lane E's labelled reference projection (used only by the E2E stub and self-tests)."""
    refs, entities, high = as_matrix(vectors)
    index = {ref: i for i, ref in enumerate(refs)}
    fit_rows = [index[r] for r in fit_refs] if fit_refs else None
    fit = reference_pca(high, 2, fit_rows)
    preserved = knn_preservation(high, fit.coords, k)
    warning = "2D PCA projection: distances are distorted and are not a fit or hiring score."
    points: list[dict[str, Any]] = [
        {
            "entity_id": entities[i],
            "projection_id": projection_id,
            "label": labels.get(entities[i], entities[i]),
            "coords": [float(fit.coords[i, 0]), float(fit.coords[i, 1])],
            "source_vector_ref": refs[i],
            "representation_type": "job",
            "projection_method": "pca",
            "neighborhood_quality": float(preserved[i]),
            "fit_metric_ref": f"diag:{projection_id}",
            "warnings": [warning],
        }
        for i in range(len(refs))
    ]
    diagnostics = {
        "projection_id": projection_id,
        "method": "pca",
        "preprocessing": "center",
        "k": _max_k(len(refs), k),
        "fit_refs": fit_refs or refs,
        "explained_variance_ratio": [float(v) for v in fit.explained_variance_ratio],
        "trustworthiness": trustworthiness(high, fit.coords, k),
        "continuity": continuity(high, fit.coords, k),
        "information_lost": 1.0 - float(fit.explained_variance_ratio.sum()),
        "note": "Geometry is a visualisation aid, not the match metric.",
    }
    if not all(math.isfinite(c) for p in points for c in p["coords"]):
        raise ValueError("reference projection produced non-finite coordinates")
    return points, diagnostics

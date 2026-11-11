"""How faithful is a low-dimensional map to the original feature space?

All metrics compare distances/neighbourhoods in the full ("high") space with those in the
projected ("low") space. They describe *geometric information loss*. None of them is a fit
score, a match score or a statement about hiring.

Tie handling matters because skill vectors are mostly binary and duplicates are common:
neighbourhood membership is tie-inclusive and ranks are "number of strictly closer points",
so two equidistant points are never penalised for an arbitrary ordering.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

import numpy as np

from .errors import InvalidInputError

_DIST_DECIMALS = 9
#: Heuristic reading aids, not validated cut-offs. Reported alongside the raw numbers.
THRESHOLDS = {
    "explained_variance_low": 0.5,
    "trustworthiness_low": 0.85,
    "continuity_low": 0.85,
    "knn_recall_low": 0.4,
    "distance_spearman_low": 0.7,
}


def _check_pair(high: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if high.ndim != 2 or low.ndim != 2 or high.shape[0] != low.shape[0]:
        raise InvalidInputError("high and low must be 2-D with the same number of rows")
    if not (np.all(np.isfinite(high)) and np.all(np.isfinite(low))):
        raise InvalidInputError("diagnostics need finite inputs (impute UNKNOWN first)")
    # Repair note (review cycle 1, F13): distances are rounded relative to the largest squared norm
    # so that exact ties stay ties. Without centring, a constant offset inflated that scale and
    # merged distinct distances into false ties. Distances are translation-invariant, so centre.
    if high.shape[0]:
        high = high - high.mean(axis=0)
        low = low - low.mean(axis=0)
    return high, low


def sq_dists(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Squared Euclidean distances, rounded so that exact ties stay exact ties."""
    aa = np.einsum("ij,ij->i", A, A)[:, None]
    bb = np.einsum("ij,ij->i", B, B)[None, :]
    D = aa + bb - 2.0 * (A @ B.T)
    np.clip(D, 0.0, None, out=D)
    scale = max(float(aa.max(initial=0.0)), float(bb.max(initial=0.0)), 1.0)
    return np.round(D / scale, _DIST_DECIMALS) * scale


def neighborhood_metrics(high: np.ndarray, low: np.ndarray, k: int = 10,
                         chunk: int = 256) -> dict[str, Any]:
    """Trustworthiness, continuity and tie-aware kNN recall (global and per point).

    * trustworthiness: are 2-D neighbours real neighbours? (penalises false neighbours)
    * continuity: do real neighbours stay neighbours in 2-D? (penalises torn neighbourhoods)
    * knn_recall[i]: share of point i's k nearest 2-D neighbours that are within its k-th
      nearest distance in the full space.
    """
    high, low = _check_pair(high, low)
    n = high.shape[0]
    warnings: list[str] = []
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise InvalidInputError("k must be a positive integer")
    k_max = (n - 1) // 2 if n >= 4 else 0
    if k_max < 1:
        return {"k": 0, "n": n, "trustworthiness": None, "continuity": None,
                "knn_recall_mean": None, "knn_recall_exact_mean": None, "knn_recall": [None] * n,
                "warnings": ["too-few-points: neighbourhood metrics need at least 4 points"]}
    if k > k_max:
        warnings.append(f"k reduced from {k} to {k_max} (needs k < n/2)")
        k = k_max
    norm = 2.0 / (n * k * (2.0 * n - 3.0 * k - 1.0))
    trust_pen = 0.0
    cont_pen = 0.0
    recall = np.zeros(n)
    exact = np.zeros(n)
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        rows = np.arange(start, stop)
        Dh = sq_dists(high[start:stop], high)
        Dl = sq_dists(low[start:stop], low)
        Dh[np.arange(stop - start), rows] = -1.0
        Dl[np.arange(stop - start), rows] = -1.0
        oh = np.argsort(Dh, axis=1, kind="stable")
        ol = np.argsort(Dl, axis=1, kind="stable")
        sh = np.take_along_axis(Dh, oh, axis=1)
        sl = np.take_along_axis(Dl, ol, axis=1)
        nh, nl = oh[:, 1:k + 1], ol[:, 1:k + 1]          # stable top-k, self excluded
        for r in range(stop - start):
            # strict rank = number of other points strictly closer, +1 (so the nearest has rank 1)
            rank_h_of_low = np.searchsorted(sh[r], Dh[r, nl[r]], side="left")
            rank_l_of_high = np.searchsorted(sl[r], Dl[r, nh[r]], side="left")
            trust_pen += float(np.clip(rank_h_of_low - k, 0, None).sum())
            cont_pen += float(np.clip(rank_l_of_high - k, 0, None).sum())
            recall[start + r] = float((Dh[r, nl[r]] <= sh[r, k]).mean())
            exact[start + r] = len(set(nh[r].tolist()) & set(nl[r].tolist())) / k
    return {
        "k": k, "n": n,
        "trustworthiness": float(1.0 - norm * trust_pen),
        "continuity": float(1.0 - norm * cont_pen),
        "knn_recall_mean": float(recall.mean()),
        "knn_recall_exact_mean": float(exact.mean()),
        "knn_recall": recall.tolist(),
        "warnings": warnings,
    }


def _average_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[order] = np.arange(len(x), dtype=np.float64)
    sx = x[order]
    boundaries = np.flatnonzero(np.diff(sx) != 0) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(x)]))
    for s, e in zip(starts, ends):
        if e - s > 1:
            ranks[order[s:e]] = (s + e - 1) / 2.0
    return ranks


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom <= 1e-18:
        return None
    return float(np.clip((a * b).sum() / denom, -1.0, 1.0))


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.size < 2:
        return None
    return _corr(_average_ranks(a), _average_ranks(b))


def kendall_tau(order_a: Sequence[str], order_b: Sequence[str]) -> float | None:
    """Kendall tau-a between two orderings of the same items (1 = identical order)."""
    common = [x for x in order_a if x in set(order_b)]
    if len(common) < 2:
        return None
    pos_b = {x: i for i, x in enumerate(order_b)}
    conc = disc = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            if pos_b[common[i]] < pos_b[common[j]]:
                conc += 1
            else:
                disc += 1
    return (conc - disc) / (conc + disc)


def distance_agreement(high: np.ndarray, low: np.ndarray, max_pairs: int = 200_000,
                       seed: int = 0) -> dict[str, Any]:
    """Do pairwise distances survive the projection? (Shepard-style agreement.)"""
    high, low = _check_pair(high, low)
    n = high.shape[0]
    if n < 3:
        return {"n_pairs": 0, "spearman": None, "pearson": None, "normalized_stress": None,
                "warnings": ["too-few-points: distance agreement needs at least 3 points"]}
    total = n * (n - 1) // 2
    if total <= max_pairs:
        i, j = np.triu_indices(n, k=1)
    else:
        rng = np.random.default_rng(seed)
        i = rng.integers(0, n, size=max_pairs)
        j = rng.integers(0, n - 1, size=max_pairs)
        j = np.where(j >= i, j + 1, j)
    dh = np.sqrt(np.clip(((high[i] - high[j]) ** 2).sum(axis=1), 0.0, None))
    dl = np.sqrt(np.clip(((low[i] - low[j]) ** 2).sum(axis=1), 0.0, None))
    warnings: list[str] = []
    ss_l = float((dl * dl).sum())
    ss_h = float((dh * dh).sum())
    if ss_h <= 1e-18:
        stress = None
        warnings.append("all full-space distances are zero; stress undefined")
    elif ss_l <= 1e-18:
        stress = 1.0
        warnings.append("all projected distances are zero; the map shows nothing")
    else:
        a = float((dh * dl).sum()) / ss_l
        stress = float(np.sqrt(((dh - a * dl) ** 2).sum() / ss_h))
    return {"n_pairs": int(len(i)), "sampled": bool(total > max_pairs),
            "spearman": spearman(dh, dl), "pearson": _corr(dh, dl),
            "normalized_stress": stress, "warnings": warnings}


def procrustes_disparity(A: np.ndarray, B: np.ndarray) -> float | None:
    """0 = same shape up to translation/rotation/reflection/scale, 1 = unrelated."""
    A = np.asarray(A, dtype=np.float64)
    B = np.asarray(B, dtype=np.float64)
    if A.shape != B.shape or A.ndim != 2:
        raise InvalidInputError("Procrustes needs two arrays of the same 2-D shape")
    A0 = A - A.mean(axis=0)
    B0 = B - B.mean(axis=0)
    na, nb = np.linalg.norm(A0), np.linalg.norm(B0)
    if na <= 1e-12 or nb <= 1e-12:
        return None
    s = np.linalg.svd((A0 / na).T @ (B0 / nb), compute_uv=False)
    return float(np.clip(1.0 - s.sum() ** 2, 0.0, 1.0))


def knn_sets(X: np.ndarray, k: int, chunk: int = 256) -> np.ndarray:
    n = X.shape[0]
    out = np.empty((n, k), dtype=np.int64)
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        D = sq_dists(X[start:stop], X)
        D[np.arange(stop - start), np.arange(start, stop)] = -1.0
        out[start:stop] = np.argsort(D, axis=1, kind="stable")[:, 1:k + 1]
    return out


def knn_overlap(A: np.ndarray, B: np.ndarray, k: int = 10) -> float | None:
    """Mean Jaccard overlap of k-nearest-neighbour sets between two embeddings of the same rows."""
    n = A.shape[0]
    if n != B.shape[0]:
        raise InvalidInputError("embeddings must describe the same rows")
    k = min(k, n - 1)
    if k < 1:
        return None
    ka, kb = knn_sets(A, k), knn_sets(B, k)
    total = 0.0
    for r in range(n):
        sa, sb = set(ka[r].tolist()), set(kb[r].tolist())
        total += len(sa & sb) / len(sa | sb)
    return total / n


def seed_variation(project: Callable[[int], np.ndarray], seeds: Sequence[int],
                   k: int = 10) -> dict[str, Any]:
    """Run a projector under several seeds and measure how much the picture changes."""
    seeds = list(seeds)
    if len(seeds) < 2:
        raise InvalidInputError("seed variation needs at least two seeds")
    embs = [np.asarray(project(int(s)), dtype=np.float64) for s in seeds]
    disp, over = [], []
    for a in range(len(embs)):
        for b in range(a + 1, len(embs)):
            d = procrustes_disparity(embs[a], embs[b])
            if d is not None:
                disp.append(d)
            o = knn_overlap(embs[a], embs[b], k)
            if o is not None:
                over.append(o)
    return {
        "seeds": seeds, "n_pairs": len(disp),
        "procrustes_disparity_mean": float(np.mean(disp)) if disp else None,
        "procrustes_disparity_max": float(np.max(disp)) if disp else None,
        "knn_overlap_mean": float(np.mean(over)) if over else None,
        "knn_overlap_min": float(np.min(over)) if over else None,
        "deterministic": bool(disp) and float(np.max(disp)) < 1e-12,
    }


def diagnose_projection(high: np.ndarray, low: np.ndarray, k: int = 10, seed: int = 0,
                        explained_variance_ratio: Sequence[float] | None = None) -> dict[str, Any]:
    """One diagnostics record for a (full space, projection) pair, with plain-language flags."""
    nb = neighborhood_metrics(high, low, k)
    da = distance_agreement(high, low, seed=seed)
    cum = None
    if explained_variance_ratio is not None:
        cum = float(np.sum(explained_variance_ratio))
    flags: list[str] = []
    if cum is not None and cum < THRESHOLDS["explained_variance_low"]:
        flags.append(f"low-explained-variance: the map shows {cum:.1%} of total variance; "
                     f"{1 - cum:.1%} of the variation between points is not visible")
    if nb["trustworthiness"] is not None and nb["trustworthiness"] < THRESHOLDS["trustworthiness_low"]:
        flags.append("false-neighbours: points that look close on the map are often not close "
                     "in the full feature space")
    if nb["continuity"] is not None and nb["continuity"] < THRESHOLDS["continuity_low"]:
        flags.append("torn-neighbourhoods: points that are close in the full space are often "
                     "drawn far apart")
    if nb["knn_recall_mean"] is not None and nb["knn_recall_mean"] < THRESHOLDS["knn_recall_low"]:
        flags.append("weak-local-structure: on average fewer than "
                     f"{THRESHOLDS['knn_recall_low']:.0%} of a point's map neighbours are true neighbours")
    if da["spearman"] is not None and da["spearman"] < THRESHOLDS["distance_spearman_low"]:
        flags.append("distance-order-distorted: map distances rank pairs differently from "
                     "full-space distances")
    return {
        "n_points": int(np.asarray(high).shape[0]),
        "n_features": int(np.asarray(high).shape[1]),
        "n_map_dims": int(np.asarray(low).shape[1]),
        "k_neighbours": nb["k"],
        "explained_variance_ratio": [float(x) for x in explained_variance_ratio]
        if explained_variance_ratio is not None else None,
        "explained_variance_cumulative": cum,
        "variance_not_shown": None if cum is None else float(max(0.0, 1.0 - cum)),
        "trustworthiness": nb["trustworthiness"],
        "continuity": nb["continuity"],
        "knn_recall_mean": nb["knn_recall_mean"],
        "knn_recall_exact_mean": nb["knn_recall_exact_mean"],
        "knn_recall_note": "knn_recall_mean is tie-inclusive (an equidistant point counts as a true neighbour); "
                           "knn_recall_exact_mean compares stable top-k sets and is the pessimistic end",
        "distance_spearman": da["spearman"],
        "distance_pearson": da["pearson"],
        "normalized_stress": da["normalized_stress"],
        "distance_pairs_used": da["n_pairs"],
        "flags": flags,
        "warnings": nb["warnings"] + da["warnings"],
        "thresholds_are_heuristic": dict(THRESHOLDS),
        "interpretation": "Geometric fidelity of the picture only. Not a match score and not a "
                          "hiring probability.",
        "per_point_knn_recall": nb["knn_recall"],
    }

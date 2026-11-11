"""Seeded k-means in the FULL feature space, cluster labelling and bootstrap stability.

Clustering is never run on the 2-D coordinates: the map is a picture, the clusters are
computed where the information is. Stability follows the cluster-wise Jaccard bootstrap idea
(Hennig, 2007); the cut-offs used to call a cluster "unstable" are conventions for reading
the numbers, not validated thresholds.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from .diagnostics import sq_dists
from .errors import DegenerateInputError, InvalidInputError

STABLE_JACCARD = 0.75
UNSTABLE_JACCARD = 0.6


def _kmeans_pp(X: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    n = X.shape[0]
    centers = [X[int(rng.integers(0, n))]]
    d2 = sq_dists(X, centers[0][None, :])[:, 0]
    for _ in range(1, k):
        total = float(d2.sum())
        if total <= 0:
            idx = int(rng.integers(0, n))
        else:
            idx = int(rng.choice(n, p=d2 / total))
        centers.append(X[idx])
        d2 = np.minimum(d2, sq_dists(X, X[idx][None, :])[:, 0])
    return np.vstack(centers)


def kmeans(X: np.ndarray, k: int, seed: int = 0, n_init: int = 5,
           max_iter: int = 100) -> dict[str, Any]:
    """Lloyd's algorithm with k-means++ seeding. Deterministic for a given seed."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2 or not np.all(np.isfinite(X)):
        raise InvalidInputError("kmeans needs a finite 2-D array")
    n = X.shape[0]
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise InvalidInputError("k must be a positive integer")
    warnings: list[str] = []
    n_distinct = int(np.unique(np.round(X, 9), axis=0).shape[0])
    if n_distinct < 1:
        raise DegenerateInputError("no rows to cluster")
    if k > n_distinct:
        warnings.append(f"k reduced from {k} to {n_distinct}: only {n_distinct} distinct points")
        k = n_distinct
    rng = np.random.default_rng(seed)
    best: dict[str, Any] | None = None
    for _ in range(max(1, n_init)):
        centers = _kmeans_pp(X, k, rng)
        labels = np.zeros(n, dtype=np.int64)
        for it in range(max_iter):
            D = sq_dists(X, centers)
            new_labels = np.argmin(D, axis=1)
            for c in range(k):
                members = new_labels == c
                if not members.any():
                    far = int(np.argmax(D[np.arange(n), new_labels]))
                    new_labels[far] = c
                    members = new_labels == c
                centers[c] = X[members].mean(axis=0)
            if it > 0 and np.array_equal(new_labels, labels):
                break
            labels = new_labels
        D = sq_dists(X, centers)
        labels = np.argmin(D, axis=1)
        inertia = float(D[np.arange(n), labels].sum())
        if best is None or inertia < best["inertia"] - 1e-12:
            best = {"labels": labels.copy(), "centers": centers.copy(), "inertia": inertia}
    assert best is not None
    best.update({"k": k, "warnings": warnings})
    return best


def _jaccard(a: set[int], b: set[int]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def cluster_stability(X: np.ndarray, k: int, seed: int = 0, n_boot: int = 20,
                      n_init: int = 3, base_n_init: int = 12) -> dict[str, Any]:
    """Cluster-wise Jaccard bootstrap: does each cluster reappear when the data is resampled?

    The reference clustering uses more restarts (``base_n_init``) than the bootstrap refits:
    with 3 restarts k-means sometimes stopped in a poor local optimum (two families merged, one
    split), which the Jaccard scores then correctly reported as unstable.
    """
    base = kmeans(X, k, seed=seed, n_init=max(n_init, base_n_init))
    k = base["k"]
    n = X.shape[0]
    rng = np.random.default_rng(seed + 1)
    base_sets = [set(np.flatnonzero(base["labels"] == c).tolist()) for c in range(k)]
    scores = np.zeros((n_boot, k))
    for b in range(n_boot):
        idx = np.unique(rng.integers(0, n, size=n))
        fit = kmeans(X[idx], k, seed=int(rng.integers(0, 2**31 - 1)), n_init=n_init)
        boot_sets = [set(idx[np.flatnonzero(fit["labels"] == c)].tolist()) for c in range(fit["k"])]
        present = set(idx.tolist())
        for c in range(k):
            ref = base_sets[c] & present
            scores[b, c] = max((_jaccard(ref, bs) for bs in boot_sets), default=0.0) if ref else 0.0
    mean = scores.mean(axis=0)
    verdict = ["stable" if m >= STABLE_JACCARD else "unstable" if m < UNSTABLE_JACCARD else "doubtful"
               for m in mean]
    return {"k": k, "labels": base["labels"], "centers": base["centers"], "inertia": base["inertia"],
            "jaccard_mean": mean.tolist(), "jaccard_min": scores.min(axis=0).tolist(),
            "verdict": verdict, "n_boot": n_boot, "warnings": base["warnings"],
            "thresholds": {"stable_at_or_above": STABLE_JACCARD, "unstable_below": UNSTABLE_JACCARD,
                           "note": "reading conventions after Hennig (2007), not validated cut-offs"}}


def label_clusters(X: np.ndarray, labels: np.ndarray, feature_names: Sequence[str],
                   families: Sequence[str | None] | None = None, top: int = 4) -> list[dict[str, Any]]:
    """Describe each cluster by its distinguishing features and (if known) majority family."""
    X = np.asarray(X, dtype=np.float64)
    overall = X.mean(axis=0)
    out = []
    for c in sorted(set(int(x) for x in labels)):
        members = np.flatnonzero(labels == c)
        lift = X[members].mean(axis=0) - overall
        order = np.argsort(-lift, kind="stable")[:top]
        entry: dict[str, Any] = {
            "cluster": c, "size": int(len(members)),
            "distinguishing_features": [
                {"feature": feature_names[i], "lift": round(float(lift[i]), 4)}
                for i in order if lift[i] > 0],
        }
        if families is not None:
            counts: dict[str, int] = {}
            for m in members:
                fam = families[m] or "unlabelled"
                counts[fam] = counts.get(fam, 0) + 1
            majority = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            entry["majority_family"] = majority[0]
            entry["purity"] = round(majority[1] / len(members), 4)
            entry["family_counts"] = dict(sorted(counts.items()))
        out.append(entry)
    return out


def adjusted_rand_index(a: Sequence[Any], b: Sequence[Any]) -> float | None:
    """Agreement between two labelings of the same rows (1 = identical, ~0 = chance)."""
    if len(a) != len(b):
        raise InvalidInputError("labelings must have the same length")
    n = len(a)
    if n < 2:
        return None
    ua = {v: i for i, v in enumerate(dict.fromkeys(a))}
    ub = {v: i for i, v in enumerate(dict.fromkeys(b))}
    table = np.zeros((len(ua), len(ub)), dtype=np.float64)
    for x, y in zip(a, b):
        table[ua[x], ub[y]] += 1

    def comb2(v: np.ndarray) -> float:
        return float((v * (v - 1) / 2.0).sum())

    sum_ij = comb2(table)
    sum_a = comb2(table.sum(axis=1))
    sum_b = comb2(table.sum(axis=0))
    total = n * (n - 1) / 2.0
    expected = sum_a * sum_b / total
    max_index = (sum_a + sum_b) / 2.0
    if abs(max_index - expected) < 1e-12:
        return 1.0 if abs(sum_ij - expected) < 1e-12 else 0.0
    return float((sum_ij - expected) / (max_index - expected))

"""Projection fidelity metrics (workstream 5) including ties, duplicates and tiny inputs."""
import numpy as np
import pytest

from careertwin_atlas.diagnostics import (diagnose_projection, distance_agreement, kendall_tau, knn_overlap,
                                          neighborhood_metrics, procrustes_disparity, seed_variation, spearman)
from careertwin_atlas.errors import InvalidInputError
from careertwin_atlas.pca import fit_pca


def _reference_trust_cont(high, low, k):
    """Slow, independent implementation of the textbook definitions (valid when there are no ties)."""
    n = len(high)

    def ranks(X):
        D = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
        np.fill_diagonal(D, -1.0)
        order = np.argsort(D, axis=1)
        r = np.empty_like(order)
        for i in range(n):
            r[i, order[i]] = np.arange(n)            # self has rank 0, nearest neighbour rank 1
        return r, order[:, 1:k + 1]

    rh, nh = ranks(high)
    rl, nl = ranks(low)
    trust = sum(max(0, rh[i, j] - k) for i in range(n) for j in nl[i])
    cont = sum(max(0, rl[i, j] - k) for i in range(n) for j in nh[i])
    norm = 2.0 / (n * k * (2 * n - 3 * k - 1))
    recall = np.mean([len(set(nh[i]) & set(nl[i])) / k for i in range(n)])
    return 1 - norm * trust, 1 - norm * cont, recall


@pytest.mark.parametrize("seed", range(6))
def test_matches_textbook_definition_on_tie_free_data(seed):
    rng = np.random.default_rng(seed)
    high = rng.standard_normal((70, 6))
    low = fit_pca(high, [f"f{i}" for i in range(6)]).transform(high)
    k = int(rng.integers(2, 12))
    got = neighborhood_metrics(high, low, k)
    trust, cont, recall = _reference_trust_cont(high, low, k)
    assert got["trustworthiness"] == pytest.approx(trust, abs=1e-12)
    assert got["continuity"] == pytest.approx(cont, abs=1e-12)
    assert got["knn_recall_mean"] == pytest.approx(recall, abs=1e-12)


def test_identity_projection_is_perfect():
    X = np.random.default_rng(0).standard_normal((50, 3))
    d = diagnose_projection(X, X.copy(), k=5)
    assert d["trustworthiness"] == pytest.approx(1.0) and d["continuity"] == pytest.approx(1.0)
    assert d["knn_recall_mean"] == pytest.approx(1.0)
    assert d["distance_spearman"] == pytest.approx(1.0) and d["normalized_stress"] == pytest.approx(0.0, abs=1e-9)
    assert d["flags"] == []


def test_unrelated_projection_scores_badly_and_is_flagged():
    rng = np.random.default_rng(1)
    high = rng.standard_normal((300, 8))
    low = rng.standard_normal((300, 2))
    d = diagnose_projection(high, low, k=10)
    assert d["trustworthiness"] < 0.7 and d["knn_recall_mean"] < 0.2
    assert abs(d["distance_spearman"]) < 0.2
    assert any(f.startswith("false-neighbours") for f in d["flags"])
    assert any(f.startswith("distance-order-distorted") for f in d["flags"])


def test_pca_beats_random_projection_on_structured_data():
    rng = np.random.default_rng(2)
    centers = rng.standard_normal((4, 10)) * 6
    high = np.vstack([c + rng.standard_normal((60, 10)) for c in centers])
    pca = fit_pca(high, [f"f{i}" for i in range(10)]).transform(high)
    rand = high @ rng.standard_normal((10, 2))
    a, b = diagnose_projection(high, pca), diagnose_projection(high, rand)
    assert a["trustworthiness"] > b["trustworthiness"]
    assert a["normalized_stress"] < b["normalized_stress"]


@pytest.mark.parametrize("seed", range(6))
def test_metrics_stay_in_range_on_random_inputs(seed):
    rng = np.random.default_rng(50 + seed)
    n = int(rng.integers(4, 90))
    high = rng.integers(0, 2, size=(n, int(rng.integers(1, 9)))).astype(float)     # binary: many ties
    low = rng.standard_normal((n, 2))
    d = diagnose_projection(high, low, k=int(rng.integers(1, 30)))
    for key in ("trustworthiness", "continuity", "knn_recall_mean"):
        assert d[key] is None or 0.0 <= d[key] <= 1.0
    assert d["distance_spearman"] is None or -1.0 <= d["distance_spearman"] <= 1.0
    assert all(q is None or 0.0 <= q <= 1.0 for q in d["per_point_knn_recall"])


def test_all_points_identical_does_not_crash_or_fake_quality():
    X = np.ones((20, 4))
    d = diagnose_projection(X, np.zeros((20, 2)), k=5)
    assert d["trustworthiness"] == pytest.approx(1.0)        # nothing to get wrong: all distances tie at 0
    assert d["distance_spearman"] is None and d["normalized_stress"] is None
    assert any("zero" in w for w in d["warnings"])


def test_ties_are_not_penalised_for_arbitrary_order():
    high = np.repeat(np.eye(3) * 5, 10, axis=0)              # 3 groups of 10 exact duplicates
    low = high[:, :2] + 0.0
    m = neighborhood_metrics(high, low, k=5)
    assert m["trustworthiness"] == pytest.approx(1.0) and m["continuity"] == pytest.approx(1.0)
    assert m["knn_recall_mean"] == pytest.approx(1.0)


def test_collapsed_map_is_reported_as_showing_nothing():
    high = np.random.default_rng(3).standard_normal((30, 4))
    d = distance_agreement(high, np.zeros((30, 2)))
    assert d["normalized_stress"] == 1.0 and d["spearman"] is None
    assert any("shows nothing" in w for w in d["warnings"])


def test_too_few_points_returns_none_with_warning():
    X = np.random.default_rng(0).standard_normal((3, 2))
    m = neighborhood_metrics(X, X, k=5)
    assert m["trustworthiness"] is None and m["knn_recall"] == [None, None, None]
    assert any(w.startswith("too-few-points") for w in m["warnings"])
    assert distance_agreement(X[:2], X[:2])["spearman"] is None


def test_k_is_clamped_with_warning():
    X = np.random.default_rng(0).standard_normal((10, 3))
    m = neighborhood_metrics(X, X, k=50)
    assert m["k"] == 4 and any(w.startswith("k reduced") for w in m["warnings"])


def test_invalid_inputs_raise():
    X = np.zeros((5, 2))
    with pytest.raises(InvalidInputError):
        neighborhood_metrics(X, np.zeros((4, 2)))
    with pytest.raises(InvalidInputError):
        neighborhood_metrics(np.full((5, 2), np.nan), X)
    for k in (0, -3, 2.5, True):
        with pytest.raises(InvalidInputError):
            neighborhood_metrics(X, X, k=k)
    with pytest.raises(InvalidInputError):
        procrustes_disparity(np.zeros((3, 2)), np.zeros((4, 2)))
    with pytest.raises(InvalidInputError):
        seed_variation(lambda s: X, [1])


def test_distance_sampling_is_seeded():
    rng = np.random.default_rng(5)
    high, low = rng.standard_normal((400, 5)), rng.standard_normal((400, 2))
    a = distance_agreement(high, low, max_pairs=2000, seed=1)
    assert a["sampled"] and a["n_pairs"] == 2000
    assert a == distance_agreement(high, low, max_pairs=2000, seed=1)
    assert a != distance_agreement(high, low, max_pairs=2000, seed=2)


def test_procrustes_ignores_rotation_scale_translation_and_reflection():
    rng = np.random.default_rng(6)
    A = rng.standard_normal((40, 2))
    theta = 1.1
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    B = (A @ R) * 3.7 + np.array([10.0, -4.0])
    B[:, 0] *= -1
    assert procrustes_disparity(A, B) == pytest.approx(0.0, abs=1e-9)
    assert procrustes_disparity(A, rng.standard_normal((40, 2))) > 0.5
    assert procrustes_disparity(A, np.zeros((40, 2))) is None


def test_seed_variation_separates_deterministic_from_stochastic_projectors():
    X = np.random.default_rng(7).standard_normal((80, 6))
    fixed = seed_variation(lambda s: X[:, :2], [0, 1, 2])
    assert fixed["deterministic"] and fixed["knn_overlap_min"] == pytest.approx(1.0)
    noisy = seed_variation(lambda s: X @ np.random.default_rng(s).standard_normal((6, 2)), [0, 1, 2])
    assert not noisy["deterministic"] and noisy["procrustes_disparity_mean"] > 0.05
    assert noisy["knn_overlap_mean"] < 1.0


def test_rank_correlations_against_known_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)      # ties handled with average ranks
    assert spearman([1, 1, 1], [1, 2, 3]) is None                          # constant input: undefined
    assert kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    assert kendall_tau(["a", "b", "c"], ["a", "c", "b"]) == pytest.approx(1 / 3)
    assert kendall_tau(["a"], ["a"]) is None
    assert knn_overlap(np.zeros((1, 2)), np.zeros((1, 2))) is None

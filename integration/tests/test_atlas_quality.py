"""WS9 — atlas quality: projected-neighbour claims vs the original metric, PCA fit leakage,
distortion warnings and misreported diagnostics."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pytest

from ctint import atlas_quality as aq
from ctint import synth


def _codes(findings: list[aq.AtlasFinding]) -> set[str]:
    return {f.code for f in findings}


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    return synth.gen_vectors(synth.gen_jobs(240, seed=9))


@pytest.fixture(scope="module")
def atlas(bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    labels = {v["entity_id"]: v["entity_id"] for v in bundle["vectors"]}
    return aq.build_reference_atlas(bundle, labels, "proj-test-0001")


def _blobs(seed: int = 0, n: int = 120, dims: int = 12) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centres = rng.normal(0, 6, size=(4, dims))
    return np.vstack([centres[i % 4] + rng.normal(0, 1, size=dims) for i in range(n)])


# ----------------------------------------------------------------------------- metric oracles


def test_metrics_are_perfect_for_an_isometry_and_poor_for_noise() -> None:
    x = _blobs()
    rotation, _ = np.linalg.qr(np.random.default_rng(1).normal(size=(12, 12)))
    same = x @ rotation
    assert aq.trustworthiness(x, same, 10) == pytest.approx(1.0)
    assert aq.continuity(x, same, 10) == pytest.approx(1.0)
    assert aq.knn_preservation(x, same, 10).mean() == pytest.approx(1.0)
    noise = np.random.default_rng(2).normal(size=(x.shape[0], 2))
    assert aq.trustworthiness(x, noise, 10) < 0.65
    assert aq.knn_preservation(x, noise, 10).mean() < 0.4


def test_trustworthiness_matches_a_brute_force_implementation() -> None:
    x = _blobs(seed=3, n=40, dims=6)
    low = aq.reference_pca(x, 2).coords
    k = 5
    n = x.shape[0]

    def order(m: np.ndarray, i: int) -> list[int]:
        d = np.linalg.norm(m - m[i], axis=1)
        return [j for j in sorted(range(n), key=lambda j: (d[j], j)) if j != i]

    penalty = 0
    for i in range(n):
        high_order = order(x, i)
        for j in order(low, i)[:k]:
            rank = high_order.index(j) + 1
            if rank > k:
                penalty += rank - k
    expected = 1 - 2 / (n * k * (2 * n - 3 * k - 1)) * penalty
    assert aq.trustworthiness(x, low, k) == pytest.approx(expected, abs=1e-12)


def test_reference_pca_properties() -> None:
    x = _blobs(seed=5)
    fit = aq.reference_pca(x, 2)
    assert np.allclose(fit.components @ fit.components.T, np.eye(2), atol=1e-10)
    assert fit.explained_variance_ratio[0] >= fit.explained_variance_ratio[1] > 0
    assert 0 < fit.explained_variance_ratio.sum() <= 1
    full = aq.reference_pca(x, 12)
    assert full.explained_variance_ratio.sum() == pytest.approx(1.0)
    assert np.allclose(aq.pairwise_distances(full.coords), aq.pairwise_distances(x), atol=1e-8)
    covariance = np.cov(fit.coords.T)
    assert abs(covariance[0, 1]) < 1e-8, "principal components are uncorrelated on the fit set"
    with pytest.raises(ValueError):
        aq.reference_pca(x[:1], 2)


def test_degenerate_inputs_are_handled() -> None:
    constant = np.ones((30, 5))
    fit = aq.reference_pca(constant, 2)
    assert np.allclose(fit.coords, 0.0) and np.allclose(fit.explained_variance_ratio, 0.0)
    duplicated = np.vstack([_blobs(n=20)] * 2)
    assert aq.trustworthiness(duplicated, aq.reference_pca(duplicated, 2).coords, 5) > 0.5
    assert aq.procrustes_disparity(constant[:, :2], constant[:, :2]) == 0.0
    assert aq.procrustes_disparity(constant[:, :2], _blobs(n=30, dims=2)) == 1.0
    with pytest.raises(ValueError):
        aq.trustworthiness(np.ones((3, 2)), np.ones((3, 2)), 2)
    with pytest.raises(ValueError, match="length"):
        aq.as_matrix({"vocabulary": ["a", "b"], "vectors": [{"ref": "r", "entity_id": "e", "values": [1.0]}]})
    with pytest.raises(ValueError, match="NaN"):
        aq.as_matrix(
            {"vocabulary": ["a"], "vectors": [{"ref": "r", "entity_id": "e", "values": [float("nan")]}]}
        )


def test_procrustes_ignores_rotation_reflection_and_scale() -> None:
    a = _blobs(n=50, dims=2)
    theta = 0.7
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    assert aq.procrustes_disparity(a, 3.0 * (a @ rot) + 5.0) == pytest.approx(0.0, abs=1e-10)
    assert aq.procrustes_disparity(a, a * np.array([1.0, -1.0])) == pytest.approx(0.0, abs=1e-10)
    assert aq.procrustes_disparity(a, np.random.default_rng(0).normal(size=a.shape)) > 0.3


# ----------------------------------------------------------------------------- verify_atlas


def test_reference_atlas_verifies_cleanly(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = atlas
    findings, metrics = aq.verify_atlas(points, bundle, diagnostics)
    assert [str(f) for f in findings] == []
    assert metrics["pca_disparity_vs_declared_fit"] < 1e-12
    assert metrics["explained_variance_gap"] < 1e-12
    assert 0.5 < metrics["trustworthiness"] <= 1.0
    assert diagnostics["information_lost"] > 0.3, "binary skill vectors do not fit in two dimensions"


def test_overstated_quality_is_detected(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = copy.deepcopy(atlas)
    for point in points:
        point["neighborhood_quality"] = 0.99
    assert "ATLAS-QUALITY-OVERSTATED" in _codes(aq.verify_atlas(points, bundle, diagnostics)[0])


def test_misreported_diagnostics_are_detected(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = atlas
    inflated = dict(diagnostics, explained_variance_ratio=[0.7, 0.25], trustworthiness=0.999)
    found = _codes(aq.verify_atlas(points, bundle, inflated)[0])
    assert {"ATLAS-VARIANCE-MISREPORTED", "ATLAS-METRIC-MISREPORTED"} <= found
    assert "ATLAS-DIAGNOSTICS-MISSING" in _codes(aq.verify_atlas(points, bundle, None)[0])
    no_evr = {k: v for k, v in diagnostics.items() if k != "explained_variance_ratio"}
    assert "ATLAS-DIAGNOSTICS-MISSING" in _codes(aq.verify_atlas(points, bundle, no_evr)[0])


def test_missing_distortion_warning_on_a_low_fidelity_map(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = copy.deepcopy(atlas)
    rng = np.random.default_rng(4)
    for point in points:
        point["coords"] = [float(v) for v in rng.normal(size=2)]
        point["warnings"] = []
        point["neighborhood_quality"] = 0.1
    found = _codes(aq.verify_atlas(points, bundle, diagnostics)[0])
    assert {
        "ATLAS-LOW-FIDELITY-NO-WARNING",
        "ATLAS-PCA-NOT-REPRODUCIBLE",
        "ATLAS-METRIC-MISREPORTED",
    } <= found


def test_fit_leakage_is_detected(bundle: dict[str, Any]) -> None:
    """C declares it fitted on a training subset but actually fitted on everything (held-out leakage)."""
    refs = [v["ref"] for v in bundle["vectors"]]
    labels = {v["entity_id"]: v["entity_id"] for v in bundle["vectors"]}
    # A deliberately unrepresentative fit set (one role family) makes the two fits differ clearly.
    jobs = synth.gen_jobs(240, seed=9)
    fit_refs = [f"vec:{j['id']}" for j in jobs if j["role_family"] in ("backend", "platform")]
    assert 20 < len(fit_refs) < len(refs)

    honest_points, honest_diag = aq.build_reference_atlas(bundle, labels, "proj-honest", fit_refs=fit_refs)
    findings, metrics = aq.verify_atlas(honest_points, bundle, honest_diag)
    assert "ATLAS-FIT-LEAKAGE" not in _codes(findings)
    assert metrics["pca_disparity_vs_declared_fit"] < 1e-12 < metrics["pca_disparity_vs_fit_on_all"]

    leaky_points, leaky_diag = aq.build_reference_atlas(bundle, labels, "proj-leaky")  # fitted on ALL rows
    leaky_diag = dict(leaky_diag, fit_refs=fit_refs)  # ...but claims the subset
    findings, metrics = aq.verify_atlas(leaky_points, bundle, leaky_diag)
    assert "ATLAS-FIT-LEAKAGE" in _codes(findings), [str(f) for f in findings]
    assert metrics["pca_disparity_vs_fit_on_all"] < 1e-12


def test_undeclared_standardisation_is_not_reproducible_until_declared(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = copy.deepcopy(atlas)
    _, _, high = aq.as_matrix(bundle)
    fit = aq.reference_pca(high, 2, standardize=True)
    for i, point in enumerate(points):
        point["coords"] = [float(fit.coords[i, 0]), float(fit.coords[i, 1])]
    undeclared = dict(diagnostics, explained_variance_ratio=[float(v) for v in fit.explained_variance_ratio])
    assert "ATLAS-PCA-NOT-REPRODUCIBLE" in _codes(aq.verify_atlas(points, bundle, undeclared)[0])
    declared = dict(undeclared, preprocessing="standardize",
                    trustworthiness=aq.trustworthiness(high, fit.coords, 10), continuity=aq.continuity(high, fit.coords, 10))  # fmt: skip
    remaining = _codes(aq.verify_atlas(points, bundle, declared)[0])
    assert "ATLAS-PCA-NOT-REPRODUCIBLE" not in remaining and "ATLAS-VARIANCE-MISREPORTED" not in remaining


def test_neighbor_claims_taken_from_2d_are_detected(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = atlas
    _, entities, high = aq.as_matrix(bundle)
    low = np.asarray([p["coords"] for p in points])
    d_high, d_low = aq.pairwise_distances(high), aq.pairwise_distances(low)
    np.fill_diagonal(d_high, np.inf)
    np.fill_diagonal(d_low, np.inf)
    honest, from_2d = [], []
    for i in range(len(entities)):
        true_nn = [int(j) for j in np.argsort(d_high[i], kind="stable")[:5]]
        flat_nn = [int(j) for j in np.argsort(d_low[i], kind="stable")[:5]]
        honest.append({"entity_id": entities[i], "neighbors": [entities[j] for j in true_nn]})
        kth = np.sort(d_high[i])[4]
        if sum(d_high[i, j] <= kth + 1e-9 for j in flat_nn) <= 2:
            from_2d.append({"entity_id": entities[i], "neighbors": [entities[j] for j in flat_nn]})
    assert len(from_2d) >= 3, "the 2D map should mislead about neighbours for at least a few points"
    clean, _ = aq.verify_atlas(points, bundle, dict(diagnostics, neighbor_claims=honest))
    assert not any(f.code.startswith("ATLAS-NEIGHBOR") for f in clean)
    flagged, _ = aq.verify_atlas(points, bundle, dict(diagnostics, neighbor_claims=from_2d))
    assert sum(f.code == "ATLAS-NEIGHBOR-CLAIM-FROM-2D" for f in flagged) == len(from_2d)
    print(
        f"\n{len(from_2d)}/{len(entities)} points have 2D nearest neighbours that are mostly not true neighbours"
    )


def test_structural_problems_are_reported(
    bundle: dict[str, Any], atlas: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    points, diagnostics = atlas
    dangling = copy.deepcopy(points)
    dangling[0]["source_vector_ref"] = "vec:missing"
    assert _codes(aq.verify_atlas(dangling, bundle, diagnostics)[0]) == {"ATLAS-DANGLING-VECTOR"}
    mixed = copy.deepcopy(points)
    mixed[0]["projection_id"] = "proj-other"
    assert _codes(aq.verify_atlas(mixed, bundle, diagnostics)[0]) == {"ATLAS-MIXED-PROJECTION"}
    duplicate = [*points, points[0]]
    assert "ATLAS-DUPLICATE-ENTITY" in _codes(aq.verify_atlas(duplicate, bundle, diagnostics)[0])
    invalid = copy.deepcopy(points)
    invalid[0]["label"] = "Role (hiring probability 0.9)"
    assert "ATLAS-POINT-INVALID" in _codes(aq.verify_atlas(invalid, bundle, diagnostics)[0])
    assert _codes(aq.verify_atlas(points[:8], bundle, diagnostics)[0]) == {"ATLAS-TOO-FEW-POINTS"}
    assert _codes(aq.verify_atlas(points, {"vocabulary": [], "vectors": []}, diagnostics)[0]) == {
        "ATLAS-VECTORS-INVALID"
    }


@pytest.mark.slow
def test_quality_metrics_scale_to_1200_vectors() -> None:
    import time

    big = synth.gen_vectors(synth.gen_jobs(1200, seed=21))
    labels = {v["entity_id"]: v["entity_id"] for v in big["vectors"]}
    started = time.perf_counter()
    points, diagnostics = aq.build_reference_atlas(big, labels, "proj-big")
    findings, metrics = aq.verify_atlas(points, big, diagnostics)
    elapsed = time.perf_counter() - started
    print(
        f"\n1200-point atlas built and verified in {elapsed:.2f}s; trustworthiness={metrics['trustworthiness']:.3f}"
    )
    assert findings == [] and elapsed < 60

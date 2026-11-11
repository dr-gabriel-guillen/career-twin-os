"""PCA numerics, reproducibility and degenerate inputs (workstreams 4 and 12)."""
import json

import numpy as np
import pytest

from careertwin_atlas.errors import DegenerateInputError, FeatureSpaceMismatchError, InvalidInputError
from careertwin_atlas.pca import PCAModel, fit_pca, subspace_agreement


def _names(d):
    return [f"f{i}" for i in range(d)]


def _random(seed, n=60, d=7):
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, d)) @ rng.standard_normal((d, d))


# ---------------------------------------------------------------- correctness ---------
@pytest.mark.parametrize("seed", range(8))
def test_matches_independent_eigendecomposition(seed):
    X = _random(seed)
    model = fit_pca(X, _names(7), n_components=3)
    cov = np.cov(X, rowvar=False, ddof=1)
    evals, evecs = np.linalg.eigh(cov)
    order = np.argsort(-evals)
    np.testing.assert_allclose(model.explained_variance, evals[order][:3], rtol=1e-9, atol=1e-10)
    np.testing.assert_allclose(model.explained_variance_ratio, evals[order][:3] / evals.sum(), rtol=1e-9)
    for c in range(3):                                   # same direction up to sign
        assert abs(float(model.components[c] @ evecs[:, order[c]])) == pytest.approx(1.0, abs=1e-8)


@pytest.mark.parametrize("seed", range(8))
def test_structural_properties_hold_on_random_matrices(seed):
    rng = np.random.default_rng(100 + seed)
    n, d = int(rng.integers(3, 80)), int(rng.integers(1, 12))
    X = rng.standard_normal((n, d)) * rng.uniform(0.1, 5.0, size=d)
    k = min(d, 3)
    model = fit_pca(X, _names(d), n_components=k)
    kept = min(k, model.rank)
    G = model.components[:kept] @ model.components[:kept].T
    np.testing.assert_allclose(G, np.eye(kept), atol=1e-9)                 # orthonormal axes
    scores = model.transform(X)
    np.testing.assert_allclose(scores.mean(axis=0), 0.0, atol=1e-9)       # centred
    np.testing.assert_allclose(scores.var(axis=0, ddof=1)[:kept], model.explained_variance[:kept], rtol=1e-8)
    assert np.all(np.diff(model.explained_variance) <= 1e-12)             # sorted
    assert 0.0 <= model.explained_variance_ratio.sum() <= 1.0 + 1e-9
    assert model.all_variance_ratio.sum() == pytest.approx(1.0, abs=1e-9)
    for c in range(kept):                                                 # sign convention
        assert model.components[c, np.argmax(np.abs(model.components[c]))] > 0


def test_full_rank_reconstruction_is_exact():
    X = _random(3, n=30, d=5)
    model = fit_pca(X, _names(5), n_components=5)
    np.testing.assert_allclose(model.inverse_transform(model.transform(X)), X, atol=1e-9)
    rep = model.transform_with_report(X)
    np.testing.assert_allclose(rep["residual_share"], 0.0, atol=1e-9)


def test_residual_share_is_what_the_map_does_not_show():
    X = _random(4, n=200, d=6)
    model = fit_pca(X, _names(6), n_components=2)
    rep = model.transform_with_report(X)
    assert np.all((rep["residual_share"] >= 0) & (rep["residual_share"] <= 1))
    Z = X - X.mean(axis=0)
    lost = (Z ** 2).sum() - (rep["coords"] ** 2).sum()
    assert lost / (Z ** 2).sum() == pytest.approx(1.0 - model.explained_variance_ratio.sum(), abs=1e-9)


def test_invariant_to_row_order_and_translation():
    X = _random(5)
    base = fit_pca(X, _names(7))
    perm = np.random.default_rng(0).permutation(len(X))
    np.testing.assert_allclose(fit_pca(X[perm], _names(7)).components, base.components, atol=1e-9)
    np.testing.assert_allclose(fit_pca(X + 1000.0, _names(7)).components, base.components, atol=1e-7)
    assert fit_pca(X[perm], _names(7)).model_id == base.model_id


def test_held_out_points_use_frozen_parameters():
    X = _random(6, n=120)
    train, test = X[:100], X[100:]
    model = fit_pca(train, _names(7))
    np.testing.assert_allclose(model.transform(train[:5]), model.transform(train)[:5], atol=1e-12)
    expected = (test - train.mean(axis=0)) @ model.components.T
    np.testing.assert_allclose(model.transform(test), expected, atol=1e-10)
    assert model.transform(test[0]).shape == (1, 2)                       # 1-D row accepted


def test_standardize_scales_columns_and_survives_constant_column():
    rng = np.random.default_rng(7)
    X = rng.standard_normal((50, 4)) * np.array([1.0, 100.0, 0.01, 1.0])
    X[:, 3] = 5.0
    model = fit_pca(X, _names(4), standardize=True)
    assert model.zero_variance_features == ("f3",)
    assert model.scale[3] == 1.0 and np.all(np.isfinite(model.components))
    assert model.components[:, 3] == pytest.approx(0.0, abs=1e-12)
    raw = fit_pca(X, _names(4), standardize=False)
    assert abs(raw.components[0, 1]) > 0.99                               # unscaled: big column dominates
    assert abs(model.components[0, 1]) < 0.99


def test_serialisation_round_trip_is_exact():
    X = _random(8)
    model = fit_pca(X, _names(7), vocab_digest="abc", n_components=3, standardize=True)
    clone = PCAModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert clone.model_id == model.model_id
    np.testing.assert_array_equal(clone.transform(X), model.transform(X))
    assert clone.feature_names == model.feature_names and clone.standardize is True


def test_unknown_cells_are_mean_imputed_and_reported():
    X = _random(9, n=40, d=4)
    X[3, 1] = np.nan
    X[7, 2] = np.nan
    model = fit_pca(X, _names(4))
    assert any(w.startswith("mean-imputed 2 unknown cells") for w in model.warnings)
    rep = model.transform_with_report(X)
    assert rep["n_imputed"].tolist().count(1) == 2
    row = np.full(4, np.nan)                                              # fully unknown row sits at the mean
    np.testing.assert_allclose(model.transform(row), 0.0, atol=1e-12)


def test_loadings_report_names_and_signs():
    X = np.zeros((40, 3))
    X[:, 0] = np.linspace(-1, 1, 40)
    X[:, 1] = -X[:, 0] * 0.5
    X[:, 2] = np.random.default_rng(0).standard_normal(40) * 1e-3
    top = fit_pca(X, ["a", "b", "c"]).loadings(top=2)[0]
    assert top["positive"][0]["feature"] == "a" and top["negative"][0]["feature"] == "b"


# ---------------------------------------------------------------- degenerate inputs ---
def test_zero_variance_input_is_reported_not_hidden():
    model = fit_pca(np.ones((10, 4)), _names(4))
    assert model.rank == 0 and model.total_variance == 0.0
    assert np.all(model.components == 0) and np.all(model.explained_variance_ratio == 0)
    assert any(w.startswith("zero-variance-input") for w in model.warnings)
    np.testing.assert_array_equal(model.transform(np.ones((3, 4))), np.zeros((3, 2)))
    assert np.all(np.isfinite(model.transform_with_report(np.ones((3, 4)))["residual_share"]))


def test_rank_one_data_gets_a_zero_second_axis_and_a_warning():
    t = np.linspace(0, 1, 25)[:, None]
    model = fit_pca(t @ np.array([[1.0, 2.0, 3.0]]), _names(3))
    assert model.rank == 1
    assert model.explained_variance_ratio[0] == pytest.approx(1.0)
    assert np.all(model.components[1] == 0) and model.explained_variance_ratio[1] == 0
    assert any(w.startswith("rank-deficient") for w in model.warnings)


def test_low_dimensional_input_one_feature():
    model = fit_pca(np.arange(10.0)[:, None], ["only"])
    assert model.rank == 1 and model.n_components == 2
    coords = model.transform(np.arange(10.0)[:, None])
    assert np.all(coords[:, 1] == 0) and coords[:, 0].std() > 0


def test_two_points_and_duplicate_points():
    two = fit_pca(np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]]), _names(3))
    assert two.rank == 1 and any(w.startswith("rank-deficient") for w in two.warnings)
    X = np.repeat(_random(1, n=5, d=4), 20, axis=0)                       # every point 20 times
    dup = fit_pca(X, _names(4))
    coords = dup.transform(X)
    assert np.unique(np.round(coords, 9), axis=0).shape[0] == 5
    assert np.all(np.isfinite(coords))


def test_outlier_dominates_unscaled_axis_and_is_visible_in_loadings():
    rng = np.random.default_rng(2)
    X = rng.standard_normal((100, 5))
    X[0] = [1e4, 0, 0, 0, 0]
    model = fit_pca(X, _names(5))
    assert abs(model.components[0, 0]) > 0.999                            # axis 1 is the outlier direction
    assert model.explained_variance_ratio[0] > 0.99
    assert np.all(np.isfinite(model.transform(X)))


def test_near_equal_eigenvalues_are_flagged_as_non_unique_plane():
    rng = np.random.default_rng(3)
    X = rng.standard_normal((4000, 3))                                    # isotropic: no preferred plane
    model = fit_pca(X, _names(3))
    assert any(w.startswith("near-degenerate eigenvalues") for w in model.warnings)


@pytest.mark.parametrize("bad, exc", [
    (np.zeros((1, 3)), DegenerateInputError),
    (np.zeros((0, 3)), DegenerateInputError),
    (np.zeros(5), InvalidInputError),
    (np.array([[1.0, np.inf, 0.0], [0.0, 1.0, 2.0]]), InvalidInputError),
    (np.zeros((4, 0)), InvalidInputError),
])
def test_malformed_input_raises_typed_errors(bad, exc):
    with pytest.raises(exc):
        fit_pca(bad, _names(bad.shape[1]) if bad.ndim == 2 else ["a"])


def test_bad_arguments_raise():
    X = _random(0)
    with pytest.raises(FeatureSpaceMismatchError):
        fit_pca(X, _names(6))
    with pytest.raises(InvalidInputError):
        fit_pca(X, ["a"] * 7)
    for k in (0, -1, 1.5, True):
        with pytest.raises(InvalidInputError):
            fit_pca(X, _names(7), n_components=k)


def test_inconsistent_feature_spaces_are_rejected_at_transform():
    model = fit_pca(_random(0), _names(7))
    with pytest.raises(FeatureSpaceMismatchError):
        model.transform(np.zeros((2, 6)))
    with pytest.raises(FeatureSpaceMismatchError):
        model.transform(np.zeros((2, 7)), feature_names=list(reversed(_names(7))))
    with pytest.raises(InvalidInputError):
        model.transform(np.zeros((2, 2, 7)))
    with pytest.raises(InvalidInputError):
        model.inverse_transform(np.zeros((2, 5)))
    other = fit_pca(_random(1, d=6), _names(6))
    with pytest.raises(FeatureSpaceMismatchError):
        subspace_agreement(model, other)


def test_from_dict_rejects_malformed_documents():
    good = fit_pca(_random(0), _names(7)).to_dict()
    for mutate in (lambda d: d.update(format="other"), lambda d: d.pop("mean"),
                   lambda d: d.update(mean=[0.0]), lambda d: d.update(scale=[0.0] * 7),
                   lambda d: d.update(components=[[float("nan")] * 7] * 2)):
        doc = json.loads(json.dumps(good))
        mutate(doc)
        with pytest.raises(InvalidInputError):
            PCAModel.from_dict(doc)
    with pytest.raises(InvalidInputError):
        PCAModel.from_dict("not a dict")


def test_subspace_agreement_bounds():
    X = _random(11, n=300)
    a = fit_pca(X, _names(7))
    assert subspace_agreement(a, a) == pytest.approx(1.0, abs=1e-9)
    b = fit_pca(X[:150], _names(7))
    assert 0.0 <= subspace_agreement(a, b) <= 1.0
    assert subspace_agreement(a, fit_pca(np.ones((5, 7)), _names(7))) == 0.0

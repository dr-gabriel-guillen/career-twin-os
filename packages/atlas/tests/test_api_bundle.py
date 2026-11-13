"""API adapter for lane D, contract conformance and alternative projections (workstreams 5 and 11)."""
import importlib.util
import json
import sys
import types

import numpy as np
import pytest

from careertwin_atlas import projectors
from careertwin_atlas.api import build_atlas_bundle, canonical_json, compare_projections, digest
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.errors import (DegenerateInputError, FeatureSpaceMismatchError, InvalidInputError,
                                     OptionalDependencyMissing)
from careertwin_atlas.features import claims_to_vector
from careertwin_atlas.points import MAP_IS_NOT_FIT, atlas_point
from careertwin_atlas.projectors import build_interpretable_axes, random_projection, umap_available, umap_project
from careertwin_atlas.report import render_svg
from careertwin_atlas.simulator import simulate_transition
from careertwin_atlas.taxonomy import generate_postings

from conftest import PKG_ROOT, find_forbidden_keys


@pytest.fixture(scope="module")
def bundle(engine, candidate):
    return build_atlas_bundle(engine, candidate, seed=3)


def _validators(contracts):
    jsonschema = pytest.importorskip("jsonschema")
    own = json.loads((PKG_ROOT / "schemas" / "atlas-bundle.schema.json").read_text(encoding="utf-8"))
    return (jsonschema.Draft202012Validator(contracts["atlas-point.schema.json"]),
            jsonschema.Draft202012Validator(own))


def test_every_point_validates_against_frozen_atlas_point_contract(bundle, contracts):
    point_v, bundle_v = _validators(contracts)
    assert len(bundle["points"]) == 480 + 8 + 1
    for p in bundle["points"]:
        assert not list(point_v.iter_errors(p)), p["entity_id"]
    assert not list(bundle_v.iter_errors(bundle))
    json.loads(canonical_json(bundle))                                     # strict JSON, no NaN/Infinity


def test_bundle_states_that_geometry_is_not_the_match_metric(bundle):
    assert bundle["flags"] == {"two_d_geometry_is_not_match_metric": True, "not_hiring_probability": True,
                               "map_distortion_flagged": True}
    assert all(MAP_IS_NOT_FIT in p["warnings"] for p in bundle["points"])
    assert "Not a match score" in bundle["diagnostics"]["interpretation"]
    assert bundle["synthetic_only"] is True
    assert find_forbidden_keys(bundle, ("probab", "odds", "chance", "likelihood", "salary")) == []
    kinds = {p["representation_type"] for p in bundle["points"]}
    assert kinds == {"job", "role-centroid", "candidate"}


def test_information_loss_statement_is_reproducible_and_matches_the_numbers(engine, candidate, bundle):
    again = build_atlas_bundle(engine, candidate, seed=3)
    assert digest(again) == digest(bundle)
    d = bundle["diagnostics"]
    cum = d["explained_variance_cumulative"]
    assert cum == pytest.approx(float(engine.model.explained_variance_ratio.sum()))
    assert d["variance_not_shown"] == pytest.approx(1.0 - cum)
    statement = bundle["information_loss"]["statement"]
    assert f"{cum:.1%}" in statement and f"{1 - cum:.1%}" in statement
    assert "sketch" in statement
    assert any(f.startswith("low-explained-variance") for f in d["flags"])  # 2 of 52 dims cannot show most variance


def test_neighbourhood_quality_and_point_warnings(bundle, engine):
    jobs = [p for p in bundle["points"] if p["representation_type"] == "job"]
    assert all(0.0 <= p["neighborhood_quality"] <= 1.0 for p in jobs)
    mean_q = float(np.mean([p["neighborhood_quality"] for p in jobs]))
    assert mean_q == pytest.approx(bundle["diagnostics"]["knn_recall_mean"], abs=1e-5)
    assert any(any(w.startswith("low-neighbourhood-quality") for w in p["warnings"]) for p in jobs)
    n_imputed = sum(any(w.startswith("imputed-unknown-features") for w in p["warnings"]) for p in jobs)
    assert n_imputed == int((engine.n_imputed > 0).sum()) > 0
    others = [p for p in bundle["points"] if p["representation_type"] != "job"]
    assert all(p["neighborhood_quality"] is None for p in others)          # not part of the fitted neighbour graph


def test_candidate_block_orders_roles_by_full_space_not_map(bundle):
    cand = bundle["candidate"]
    assert cand["placed_on_map"] is True
    ranks = [r["shortfall_rank"] for r in cand["role_table"]]
    assert ranks == sorted(ranks) and len(ranks) == 8
    assert "map_distance is included only to expose" in cand["role_table_note"]
    point = next(p for p in bundle["points"] if p["representation_type"] == "candidate")
    assert point["fit_metric_ref"] == "role_table:requirement_shortfall"
    assert any(w.startswith("imputed-unknown-features:1") for w in point["warnings"])


def test_max_job_points_limits_output_but_not_diagnostics(engine, candidate, bundle):
    small = build_atlas_bundle(engine, candidate, seed=3, max_job_points=50)
    assert sum(p["representation_type"] == "job" for p in small["points"]) == 50
    assert small["projection"]["n_points_fitted"] == 480 and small["projection"]["n_job_points_emitted"] == 50
    assert small["diagnostics"]["trustworthiness"] == bundle["diagnostics"]["trustworthiness"]
    none = build_atlas_bundle(engine, None, max_job_points=0, include_clusters=False)
    assert {p["representation_type"] for p in none["points"]} == {"role-centroid"}
    assert none["candidate"] is None and none["clusters"] is None
    for bad in (-1, 2.5, True):
        with pytest.raises(InvalidInputError):
            build_atlas_bundle(engine, None, max_job_points=bad)


def test_interpretable_axes_bundle_is_contract_valid_and_explained(engine, candidate, contracts):
    point_v, bundle_v = _validators(contracts)
    b = build_atlas_bundle(engine, candidate, method="interpretable-axes", include_clusters=False)
    assert not list(bundle_v.iter_errors(b))
    assert all(not list(point_v.iter_errors(p)) for p in b["points"])
    assert {p["projection_method"] for p in b["points"]} == {"interpretable-axes"}
    axes = b["projection"]["axes"]
    assert [a["name"] for a in axes] == ["customer-facing vs builder", "leadership scope vs individual contribution"]
    assert "enterprise-sales" in axes[0]["positive_features"] and "python" in axes[0]["negative_features"]
    assert b["diagnostics"]["explained_variance_cumulative"] is None       # not a variance decomposition
    cos = b["projection"]["axis_cosines"]
    assert cos[0][0] == pytest.approx(1.0) and abs(cos[0][1]) < 1.0
    cto = next(p for p in b["points"] if p["entity_id"] == "role:field-cto")
    backend = next(p for p in b["points"] if p["entity_id"] == "role:backend")
    assert cto["coords"][0] > backend["coords"][0]                         # more customer-facing
    assert cto["coords"][1] > backend["coords"][1]                         # more leadership scope


def test_interpretable_axes_survive_reduced_vocabularies(vocab):
    axes = build_interpretable_axes(vocab, 3)
    assert axes.weights.shape == (3, len(vocab.features)) and not axes.warnings
    governance = [f.name for f in vocab.features if f.group == "governance"]
    partial = build_interpretable_axes(vocab.without_features(governance), 3)
    assert any("pole has no features" in w for w in partial.warnings)
    with pytest.raises(InvalidInputError):
        build_interpretable_axes(vocab, 4)
    with pytest.raises(FeatureSpaceMismatchError):
        axes.transform(np.zeros((2, 3)))
    with pytest.raises(InvalidInputError):
        axes.transform(np.full((1, len(vocab.features)), np.nan))


def test_three_dimensional_projection(jobs, vocab, candidate, contracts):
    point_v, _ = _validators(contracts)
    eng3 = AtlasEngine.fit(jobs, vocab, n_components=3, seed=3, n_boot=5)
    b3 = build_atlas_bundle(eng3, candidate, dims=3, include_clusters=False)
    assert all(len(p["coords"]) == 3 and not list(point_v.iter_errors(p)) for p in b3["points"])
    b2 = build_atlas_bundle(eng3, candidate, dims=2, include_clusters=False)
    assert b3["diagnostics"]["explained_variance_cumulative"] > b2["diagnostics"]["explained_variance_cumulative"]
    assert b3["diagnostics"]["trustworthiness"] >= b2["diagnostics"]["trustworthiness"] - 0.02
    assert b3["projection"]["projection_id"] != b2["projection"]["projection_id"]


def test_requesting_more_dims_than_fitted_is_an_error(engine):
    with pytest.raises(InvalidInputError):
        build_atlas_bundle(engine, None, dims=3)
    with pytest.raises(InvalidInputError):
        build_atlas_bundle(engine, None, dims=4)
    with pytest.raises(InvalidInputError):
        build_atlas_bundle(engine, None, method="tsne")


# ---------------------------------------------------------------- UMAP plugin ---------
def test_umap_is_optional_and_never_faked(engine):
    """In this environment umap-learn is absent: the plugin must refuse, not invent numbers."""
    if umap_available():
        pytest.skip("umap-learn is installed here; the absence path cannot be exercised")
    assert "umap" not in sys.modules
    with pytest.raises(OptionalDependencyMissing):
        umap_project(engine.Z)
    with pytest.raises(OptionalDependencyMissing):
        build_atlas_bundle(engine, None, method="umap")
    cmp_ = compare_projections(engine, seeds=(0, 1), n_boot=3)
    assert cmp_["methods"]["umap"] == {"status": "not-executed", "reason": cmp_["methods"]["umap"]["reason"]}
    assert "trustworthiness" not in cmp_["methods"]["umap"]


def test_umap_plugin_wiring_with_a_stub_module(engine, candidate, contracts, monkeypatch):
    """WIRING TEST ONLY: a fake `umap` module stands in for umap-learn. This proves the adapter
    passes seeds and shapes correctly; it is not evidence about real UMAP behaviour."""
    calls = []

    class FakeUMAP:
        def __init__(self, n_components, n_neighbors, min_dist, random_state):
            calls.append((n_components, n_neighbors, random_state))
            self.k, self.seed = n_components, random_state

        def fit_transform(self, X):
            return np.random.default_rng(self.seed).standard_normal((X.shape[0], self.k))

    stub = types.ModuleType("umap")
    stub.UMAP = FakeUMAP
    monkeypatch.setitem(sys.modules, "umap", stub)
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(projectors.importlib.util, "find_spec",
                        lambda name, *a, **k: object() if name == "umap" else real_find_spec(name, *a, **k))
    emb = umap_project(engine.Z, seed=5)
    assert emb.shape == (480, 2) and calls[-1] == (2, 15, 5)
    b = build_atlas_bundle(engine, candidate, method="umap", seed=5, include_clusters=False)
    point_v, bundle_v = _validators(contracts)
    assert not list(bundle_v.iter_errors(b))
    assert {p["representation_type"] for p in b["points"]} == {"job"}      # no held-out placement for UMAP
    assert b["projection"]["held_out_points_supported"] is False and b["candidate"]["placed_on_map"] is False
    assert any("stochastic" in w for w in b["warnings"])
    cmp_ = compare_projections(engine, seeds=(0, 1, 2), n_boot=2)
    sv = cmp_["methods"]["umap"]["seed_variation"]
    assert cmp_["methods"]["umap"]["status"] == "executed" and sv["deterministic"] is False

    class BrokenUMAP(FakeUMAP):
        def fit_transform(self, X):
            return np.full((X.shape[0], self.k), np.nan)

    stub.UMAP = BrokenUMAP
    with pytest.raises(InvalidInputError):
        umap_project(engine.Z)
    with pytest.raises(InvalidInputError):
        umap_project(engine.Z[:3])


def test_projection_comparison_ranks_pca_above_the_random_baseline(engine):
    cmp_ = compare_projections(engine, seeds=(0, 1, 2), n_boot=5)
    m = cmp_["methods"]
    assert m["pca"]["status"] == m["interpretable-axes"]["status"] == "executed"
    assert m["pca"]["trustworthiness"] > m["random-projection-baseline"]["trustworthiness"]
    assert m["pca"]["normalized_stress"] < m["random-projection-baseline"]["normalized_stress"]
    assert m["pca"]["seed_variation"]["deterministic"] is True
    assert m["random-projection-baseline"]["seed_variation"]["deterministic"] is False
    assert 0.0 <= m["pca"]["resampling_stability"]["plane_agreement_min"] <= 1.0
    assert "never emitted" in m["random-projection-baseline"]["role"]
    np.testing.assert_array_equal(random_projection(engine.Z, 4), random_projection(engine.Z, 4))


# ---------------------------------------------------------------- hostile / odd input -
def test_engine_skips_unusable_and_duplicate_postings(jobs, vocab):
    messy = [jobs[0], dict(jobs[0]), {"title": "no id"}, "garbage", None, jobs[1], {**jobs[2], "skills": []}]
    eng = AtlasEngine.fit(messy, vocab, n_boot=2)
    assert len(eng.matrix) == 3 and len(eng.skipped) == 4
    assert any("duplicate id" in s["reason"] for s in eng.skipped)
    assert any("skipped as unusable or duplicate" in w for w in eng.warnings)
    b = build_atlas_bundle(eng, None, include_clusters=False)
    assert len(b["skipped_inputs"]) == 4 and b["diagnostics"]["trustworthiness"] is None
    assert any(w.startswith("too-few-points") for w in b["warnings"])


def test_engine_needs_two_usable_postings(jobs, vocab):
    for bad in ([], [jobs[0]], [jobs[0], jobs[0]], ["x", None]):
        with pytest.raises(DegenerateInputError):
            AtlasEngine.fit(bad, vocab)
    with pytest.raises(InvalidInputError):
        AtlasEngine.fit("jobs", vocab)


def test_identical_postings_produce_a_labelled_degenerate_map(jobs, vocab):
    clones = [{**jobs[0], "id": f"clone-{i}"} for i in range(12)]
    eng = AtlasEngine.fit(clones, vocab, n_boot=2)
    b = build_atlas_bundle(eng, None, include_clusters=True)
    assert all(p["coords"] == [0.0, 0.0] for p in b["points"])
    assert any(w.startswith("zero-variance-input") for w in b["warnings"])
    assert b["diagnostics"]["explained_variance_cumulative"] == 0.0
    assert b["clusters"]["k"] == 1                                         # nothing to cluster, said plainly


def test_postings_without_role_family_are_mapped_but_not_profiled(jobs, vocab):
    unlabelled = [{k: v for k, v in j.items() if k != "role_family"} for j in jobs[:30]]
    eng = AtlasEngine.fit(unlabelled, vocab, n_boot=2)
    assert eng.profiles == {} and any("no role_family" in w for w in eng.warnings)
    b = build_atlas_bundle(eng, None)
    assert b["role_centroids"] == [] and b["clusters"] is None and len(b["points"]) == 30


def test_vectors_from_another_vocabulary_are_rejected(engine, claims, vocab):
    foreign = claims_to_vector(claims, vocab.without_aliases(), "candidate-synthetic-001")
    with pytest.raises(FeatureSpaceMismatchError):
        build_atlas_bundle(engine, foreign)
    with pytest.raises(FeatureSpaceMismatchError):
        engine.project(foreign)


def test_hostile_labels_are_sanitised_in_points_and_escaped_in_svg(jobs, vocab, candidate):
    payload = "<script>alert('xss')</script>\"><img src=x onerror=alert(1)>" + chr(0) + chr(0x202E)
    hostile = [dict(j) for j in jobs[:60]]
    hostile[0]["title"] = payload
    eng = AtlasEngine.fit(hostile, vocab, n_boot=2)
    sc = simulate_transition(eng, candidate, sorted(eng.profiles)[0], [])
    b = build_atlas_bundle(eng, candidate, include_clusters=False)
    label = next(p["label"] for p in b["points"] if p["entity_id"] == hostile[0]["id"])
    assert chr(0) not in label and chr(0x202E) not in label
    assert label.startswith("<script>")            # sanitised, NOT escaped: escaping is the renderer's job
    b["points"].append({**sc["outputs"]["map_points"][1], "label": payload})
    svg = render_svg(eng, b)
    assert "<script>" not in svg and "onerror=alert(1)>" not in svg
    assert "&lt;script&gt;" in svg


def test_atlas_point_constructor_rejects_invalid_values():
    ok = dict(entity_id="e", label="l", coords=[0.0, 1.0], source_vector_ref="v", representation_type="job",
              projection_method="pca", projection_id="p")
    assert atlas_point(**ok)["coords"] == [0.0, 1.0]
    assert atlas_point(**{**ok, "coords": [-0.0, 1e-9]})["coords"] == [0.0, 0.0]      # no negative zero
    for bad in ({"coords": [1.0]}, {"coords": [1, 2, 3, 4]}, {"coords": [float("nan"), 0.0]},
                {"representation_type": "employer"}, {"projection_method": "tsne"}, {"entity_id": ""},
                {"neighborhood_quality": 1.5}, {"neighborhood_quality": float("nan")}):
        with pytest.raises(InvalidInputError):
            atlas_point(**{**ok, **bad})


def test_more_than_one_thousand_vectors_end_to_end(vocab, candidate, contracts):
    """Workstream 11 stress requirement: >1k synthetic job vectors through the full adapter."""
    point_v, bundle_v = _validators(contracts)
    eng = AtlasEngine.fit(generate_postings(1200, vocab.skill_names, seed=99), vocab, seed=1, n_boot=5)
    b = build_atlas_bundle(eng, candidate, seed=1, cluster_boot=3)
    assert b["projection"]["n_points_fitted"] == 1200 and len(b["points"]) == 1209
    assert not list(bundle_v.iter_errors(b))
    assert all(not list(point_v.iter_errors(p)) for p in b["points"][::25])
    assert all(np.isfinite(p["coords"]).all() for p in b["points"])
    assert len({p["entity_id"] for p in b["points"]}) == 1209
    assert 0.5 < b["diagnostics"]["trustworthiness"] <= 1.0

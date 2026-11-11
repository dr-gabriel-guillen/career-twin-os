"""Clustering, role centroids, instability and drift (workstream 6)."""
import numpy as np
import pytest

from careertwin_atlas.clustering import adjusted_rand_index, cluster_stability, kmeans, label_clusters
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.errors import DegenerateInputError, InvalidInputError
from careertwin_atlas.roles import centroid_drift, split_by_time
from careertwin_atlas.taxonomy import FAMILY_IDS, generate_postings


def _blobs(seed=0, k=4, n=50, d=6, spread=0.3):
    rng = np.random.default_rng(seed)
    centers = rng.standard_normal((k, d)) * 8
    X = np.vstack([c + rng.standard_normal((n, d)) * spread for c in centers])
    return X, np.repeat(np.arange(k), n)


def test_kmeans_recovers_separated_blobs_and_is_deterministic():
    X, truth = _blobs()
    a = kmeans(X, 4, seed=1)
    assert adjusted_rand_index(truth, a["labels"]) == pytest.approx(1.0)
    b = kmeans(X, 4, seed=1)
    np.testing.assert_array_equal(a["labels"], b["labels"])
    assert a["inertia"] == b["inertia"]


def test_kmeans_handles_more_clusters_than_distinct_points():
    X = np.repeat(np.array([[0.0, 0.0], [5.0, 5.0], [9.0, 0.0]]), 10, axis=0)
    res = kmeans(X, 8, seed=0)
    assert res["k"] == 3 and any("k reduced" in w for w in res["warnings"])
    assert len(set(res["labels"].tolist())) == 3
    assert res["inertia"] == pytest.approx(0.0, abs=1e-9)
    one = kmeans(np.ones((7, 3)), 4)
    assert one["k"] == 1 and set(one["labels"].tolist()) == {0}


def test_kmeans_rejects_bad_input():
    X, _ = _blobs()
    for k in (0, -1, 2.0, True):
        with pytest.raises(InvalidInputError):
            kmeans(X, k)
    with pytest.raises(InvalidInputError):
        kmeans(np.array([[np.nan, 1.0]]), 1)
    with pytest.raises(InvalidInputError):
        kmeans(np.zeros(5), 1)
    with pytest.raises(DegenerateInputError):
        kmeans(np.zeros((0, 2)), 1)


def test_stability_separates_real_clusters_from_arbitrary_splits():
    X, _ = _blobs(seed=3)
    good = cluster_stability(X, 4, seed=0, n_boot=15)
    assert good["verdict"] == ["stable"] * 4 and min(good["jaccard_mean"]) > 0.95
    blob = np.random.default_rng(4).standard_normal((200, 5))          # one cloud, no structure
    bad = cluster_stability(blob, 5, seed=0, n_boot=15)
    assert "stable" not in bad["verdict"]                               # every split is arbitrary
    assert max(bad["jaccard_mean"]) < 0.75


def test_adjusted_rand_index_known_values():
    assert adjusted_rand_index([0, 0, 1, 1], [1, 1, 0, 0]) == pytest.approx(1.0)      # label names irrelevant
    assert adjusted_rand_index([0, 0, 1, 1], [0, 1, 0, 1]) == pytest.approx(-0.5)
    assert adjusted_rand_index([0, 0, 0, 0], [0, 0, 0, 0]) == 1.0
    assert adjusted_rand_index([0], [0]) is None
    with pytest.raises(InvalidInputError):
        adjusted_rand_index([0, 1], [0])


def test_clusters_on_synthetic_postings_recover_generator_families(engine):
    stab = cluster_stability(engine.Z, 8, seed=3, n_boot=8)
    fams = [v.meta["role_family"] for v in engine.matrix.vectors]
    assert adjusted_rand_index(fams, stab["labels"].tolist()) > 0.9
    described = label_clusters(engine.X, stab["labels"], engine.matrix.feature_names, fams)
    assert {c["majority_family"] for c in described} == set(FAMILY_IDS)
    assert all(c["distinguishing_features"] for c in described)


def test_role_profiles_reflect_generator_design(engine):
    assert set(engine.profiles) == set(FAMILY_IDS)
    cto = engine.profiles["field-cto"]
    core = cto.core_features()
    assert {"enterprise-sales", "executive-communication", "ai-strategy"} <= set(core)
    assert "kubernetes" not in core
    assert all(not f.startswith("ctx:") for f in core)
    assert 0.0 < cto.unmapped_requirement_share < 1.0                   # opaque requirements are counted
    assert sum(p.n_postings for p in engine.profiles.values()) == len(engine.matrix)
    assert all(not s["unstable"] for s in engine.stability.values())


def test_small_samples_flag_unstable_centroids(jobs, vocab):
    small = AtlasEngine.fit(jobs[:20], vocab, seed=0, n_boot=30)
    flagged = [f for f, s in small.stability.items() if s["unstable"]]
    assert len(flagged) >= len(small.stability) // 2
    assert all(small.stability[f]["reasons"] for f in flagged)
    lone = [f for f, p in small.profiles.items() if p.n_postings == 1]
    for f in lone:                                                      # a single posting is never "stable"
        assert small.stability[f]["unstable"] and small.stability[f]["bootstrap_radius"] is None


def test_planted_drift_is_detected_and_control_is_clean(vocab):
    cutoff = "2026-08-02T00:00:00Z"
    drift = {"family": "backend", "after_day": 90, "prevalence": {"llm-integration": 0.7, "rag": 0.45}}
    for planted, expect in ((drift, ["backend"]), (None, [])):
        eng = AtlasEngine.fit(generate_postings(1200, vocab.skill_names, seed=21, drift=planted), vocab, n_boot=5)
        early, late = split_by_time(eng.matrix.vectors, cutoff)
        res = centroid_drift(early, late, eng.model.mean, seed=0)
        flagged = sorted(f for f, r in res["families"].items() if r.get("drifted"))
        assert flagged == expect
        assert res["families_compared"] == 8 and res["bonferroni_threshold"] == pytest.approx(0.00125)
        if planted:
            top = res["families"]["backend"]["top_feature_changes"][0]
            assert top["feature"] == "llm-integration" and top["change"] > 0.4
        for r in res["families"].values():
            assert 0.0 < r["permutation_exceedance_share"] <= 1.0


def test_drift_refuses_to_compare_thin_windows(engine):
    vectors = list(engine.matrix.vectors)
    res = centroid_drift(vectors[:6], vectors[6:], engine.model.mean)
    assert any(r["status"] == "not-comparable" for r in res["families"].values())
    for r in res["families"].values():
        assert r["status"] in ("compared", "not-comparable")
        assert "drifted" in r or r["status"] == "not-comparable"
    with pytest.raises(InvalidInputError):
        centroid_drift(vectors[:6], vectors[6:], engine.model.mean, alpha=0.0)
    with pytest.raises(InvalidInputError):
        split_by_time(vectors, "last tuesday")


def test_split_by_time_drops_undated_postings(jobs, vocab):
    mixed = [dict(j) for j in jobs[:40]]
    mixed[0]["fetched_at"] = "not a date"
    mixed[1].pop("fetched_at")
    eng = AtlasEngine.fit(mixed, vocab, n_boot=2)
    early, late = split_by_time(eng.matrix.vectors, "2026-08-02T00:00:00Z")
    assert len(early) + len(late) == 38

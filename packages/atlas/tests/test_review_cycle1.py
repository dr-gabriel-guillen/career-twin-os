"""Regression tests for adversarial review cycle 1 (findings F1-F14, posted on Issue #4).

Each test reproduces the reviewer's failing case. Before the repair commit every one of them failed
or raised a raw exception; see HANDOFF.md for the before/after table.
"""
import json
import math

import numpy as np
import pytest

from careertwin_atlas import api
from careertwin_atlas.adapters import A_API_VERSION, engine_from_skill_vectors
from careertwin_atlas.api import build_atlas_bundle, projection_view
from careertwin_atlas.diagnostics import neighborhood_metrics
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.errors import InvalidInputError
from careertwin_atlas.features import (STATE_CONTRADICTED, claims_to_vector, job_to_vector, sanitize_label)
from careertwin_atlas.gaps import analyze_gap
from careertwin_atlas.graph import build_transition_graph, plan_paths
from careertwin_atlas.pca import PCAModel
from careertwin_atlas.report import _md_escape, render_svg
from careertwin_atlas.roles import centroid_drift
from careertwin_atlas.sensitivity import (SensitivityInputs, freshness_sensitivity, missing_salary_sensitivity,
                                          seniority_sensitivity, weight_sensitivity)
from careertwin_atlas.simulator import apply_actions, simulate_transition

from conftest import AS_OF, CANDIDATE_REF


def claim(cid, status, skills, **kw):
    return {"id": cid, "subject_id": CANDIDATE_REF, "claim_type": "skill", "claim_text": "Fictional",
            "status": status, "evidence_ids": [], "visibility": "private", "updated_at": AS_OF,
            "skills": skills, **kw}


def _a_payload(cand_observed):
    vocab = ["python", "kubernetes", "people-management", "sql"]
    fams = {"backend": [1, 0, 0, 1], "platform": [0, 1, 0, 0], "mgmt": [0, 0, 1, 0]}
    jobs = []
    for i in range(30):
        fam = sorted(fams)[i % 3]
        jobs.append({"entity_id": f"jp_{i}", "observed": True, "values": [float(x) for x in fams[fam]],
                     "role_family": fam, "label": f"Fictional {fam}", "vocabulary_version": "t"})
    cand = {"entity_id": CANDIDATE_REF, "observed": cand_observed, "values": [0.0] * 4}
    return {"api_version": A_API_VERSION, "vocabulary": vocab, "vocabulary_version": "t", "jobs": jobs,
            "candidate": cand}


def test_f1_unknown_evidence_is_not_a_perfect_match():
    res = engine_from_skill_vectors(_a_payload(False), n_boot=2)
    with pytest.raises(InvalidInputError, match="not assessable"):
        res["engine"].role_table(res["candidate"])
    with pytest.raises(InvalidInputError):
        simulate_transition(res["engine"], res["candidate"], "backend", [])
    bundle = build_atlas_bundle(res["engine"], res["candidate"], include_clusters=False)
    assert bundle["candidate"]["role_table"] is None
    assert bundle["candidate"]["role_table_status"].startswith("not-assessable")


def test_f1_partially_unknown_evidence_counts_as_no_evidence_and_never_worsens(engine, candidate):
    import dataclasses
    vals, states = candidate.values.copy(), candidate.states.copy()
    i = candidate.feature_names.index("python")
    vals[i], states[i] = np.nan, 0                       # python now UNKNOWN
    unk = dataclasses.replace(candidate, values=vals, states=states)
    row = next(r for r in engine.role_table(unk) if r["role"] == "backend")
    assert row["unknown_features_counted_as_no_evidence"] == 1
    sc = simulate_transition(engine, unk, "backend",
                             [{"skill": "python", "effort_hours": 100, "evidence_goal": "portfolio-project"}])
    out = sc["outputs"]
    assert out["hypothetical"]["requirement_shortfall"] < out["baseline"]["requirement_shortfall"]
    assert out["applied_actions"][0]["evidence_before_was_unknown"] is True
    path = plan_paths(engine, build_transition_graph(engine), unk, "backend")["alternatives"][0]
    assert any("unknown (not observed) for" in c and "python" in c for c in path["caveats"])


def test_f1_tied_values_share_a_rank():
    payload = _a_payload(True)
    payload["candidate"]["values"] = [1.0, 0.0, 0.0, 0.0]           # python only
    payload["jobs"] += [{**j, "entity_id": j["entity_id"] + "b", "role_family": "platform2"}
                        for j in payload["jobs"] if j["role_family"] == "platform"]
    res = engine_from_skill_vectors(payload, n_boot=2)
    table = {r["role"]: r for r in res["engine"].role_table(res["candidate"])}
    assert table["platform"]["shortfall_rank"] == table["platform2"]["shortfall_rank"]


def test_f2_a_weaker_claim_does_not_erase_a_contradiction(engine, vocab):
    v = claims_to_vector([claim("c1", "contradicted", ["kubernetes"]), claim("c2", "unverified", ["kubernetes"])],
                         vocab, CANDIDATE_REF)
    assert v.state_of("kubernetes") == STATE_CONTRADICTED
    assert "kubernetes" in analyze_gap(engine, v, "platform")["contradicted"]
    res = apply_actions(engine, v, [{"skill": "kubernetes", "effort_hours": 130.0,
                                     "evidence_goal": "verified-work-sample"}], "x")
    assert res["applied"][0]["blocked_by_contradiction"] and res["applied"][0]["evidence_level_after"] == 0.0


def test_f3_seniority_check_is_reported_as_not_applicable(jobs, claims, vocab):
    capitalised = [{**j, "seniority": j["seniority"].capitalize()} for j in jobs[:200]]
    res = seniority_sensitivity(SensitivityInputs(capitalised, claims, CANDIDATE_REF, AS_OF, vocab, n_boot=2))
    assert res["verdict"] == "not-applicable"
    assert res["variants"]["20pct_bumped_one_level"]["n_bumped"] > 0      # tokens normalised before bumping


def test_f4_nothing_assessable_is_not_stable(jobs, claims, vocab):
    old = [{**j, "fetched_at": "2020-01-01T00:00:00Z"} for j in jobs[:119]]
    old.append({**jobs[119], "fetched_at": "2026-10-31T00:00:00Z"})
    res = freshness_sensitivity(SensitivityInputs(old, claims, CANDIDATE_REF, AS_OF, vocab, n_boot=2))
    assert res["verdict"] == "not-assessable"


def test_f5_drift_test_keeps_power_with_many_families(vocab):
    def vec(fam, skills, i, late):
        return job_to_vector({"id": f"{fam}-{late}-{i}", "skills": skills, "seniority": "senior",
                              "role_family": fam}, vocab)
    names = list(vocab.skill_names)
    early, late = [], []
    for f in range(11):
        a, b = names[(2 * f) % len(names)], names[(2 * f + 1) % len(names)]
        early += [vec(f"fam{f}", [a], i, 0) for i in range(40)]
        late += [vec(f"fam{f}", [b], i, 1) for i in range(40)]
    means = np.nan_to_num(np.vstack([v.values for v in early + late]).mean(axis=0))
    res = centroid_drift(early, late, means, seed=0)
    assert res["families_compared"] == 11
    assert 1.0 / (res["n_perm"] + 1) <= (0.01 / 11) / 2                 # smallest attainable share is reachable
    assert sum(r["drifted"] for r in res["families"].values()) == 11


@pytest.mark.parametrize("plan", [
    [{"skill": "ai-strategy", "effort_hours": 1e308}, {"skill": "python", "effort_hours": 1e308}],
    [{"skill": "python", "effort_hours": 10 ** 400}],
])
def test_f6_huge_numbers_are_rejected_not_turned_into_infinity(engine, candidate, plan):
    with pytest.raises(InvalidInputError):
        simulate_transition(engine, candidate, "field-cto", plan)


def test_f6_more_invalid_inputs_raise_typed_errors(engine, candidate, jobs, vocab, claims):
    with pytest.raises(InvalidInputError):
        simulate_transition(engine, candidate, "fde", [], time_horizon_months=10 ** 400)
    graph = build_transition_graph(engine)
    for kw in ({"tenure_months": float("nan")}, {"tenure_months": -100}, {"top": 0}):
        with pytest.raises(InvalidInputError):
            plan_paths(engine, graph, candidate, "fde", **kw)
    with pytest.raises(InvalidInputError):
        build_atlas_bundle(engine, candidate, cluster_boot=0)
    v = job_to_vector({**jobs[0], "compensation": {"currency": "USD", "min": 10 ** 400, "max": 10 ** 401,
                                                   "basis": "base", "verified": False}}, vocab)
    assert v.meta["compensation_mid"] is None
    eng = AtlasEngine.fit([{**jobs[0], "compensation": {"currency": "USD", "min": 10 ** 400, "max": None,
                                                        "basis": "base", "verified": False}}] + list(jobs[1:40]),
                          vocab, n_boot=2)
    assert len(eng.matrix) == 40
    w = claims_to_vector([claim("x", ["supported"], ["python"])], vocab, CANDIDATE_REF)
    assert any("unknown status" in m for m in w.warnings)


@pytest.mark.parametrize("cid, status, expected", [
    ("x:supported", "unverified", "unverified"),
    ("x:supported", "contradicted", "contradicted"),
    ("urn:claim:1", "supported", "supported"),
])
def test_f7_claim_ids_with_colons_do_not_change_evidence_basis(vocab, cid, status, expected):
    v = claims_to_vector([claim(cid, status, ["python"])], vocab, CANDIDATE_REF)
    assert api._evidence_basis(v) == expected


def test_f8_mixed_currencies_are_not_compared_and_unknown_is_never_zero(jobs, vocab, candidate):
    mixed = []
    for j in jobs:
        if j["role_family"] == "backend":
            j = {**j, "compensation": {"currency": "JPY", "min": 9e6, "max": 1.2e7, "basis": "total", "verified": False}}
        elif j["role_family"] == "field-cto":
            j = {**j, "compensation": {"currency": "USD", "min": 3e5, "max": 4e5, "basis": "base", "verified": False}}
        elif j["role_family"] == "platform":
            j = {**j, "compensation": None}
        mixed.append(j)
    eng = AtlasEngine.fit(mixed, vocab, n_boot=2)
    ms = missing_salary_sensitivity(eng, candidate)
    assert ms["compensation_comparable"] is False and "NOT compared" in ms["compensation_unit_note"]
    only_pay = weight_sensitivity(eng, candidate, {"compensation": 1.0}, n_draws=10)
    assert only_pay["baseline_ordering"] == []                          # nothing comparable: nobody ranked
    assert sorted(only_pay["unranked_at_baseline"]) == sorted(eng.profiles)
    bad = job_to_vector({**jobs[0], "compensation": {"currency": "USD", "min": 500, "max": 100,
                                                     "basis": "base", "verified": False}}, vocab)
    assert bad.meta["compensation_mid"] is None and any("min > max" in w for w in bad.warnings)
    marked = AtlasEngine.fit([{**j, "source": "greenhouse"} for j in jobs[:60]], vocab, n_boot=2)
    sc = simulate_transition(marked, candidate, sorted(marked.profiles)[0], [])
    assert not any("marked source='synthetic'" in a and "All role" in a for a in sc["assumptions"])


def test_f9_expiry_is_never_silently_skipped(vocab):
    no_as_of = claims_to_vector([claim("e", "supported", ["sql"], valid_to="2001-01-01T00:00:00Z")], vocab, CANDIDATE_REF)
    assert any(w.startswith("expiry-not-checked") for w in no_as_of.warnings)
    garbled = claims_to_vector([claim("e", "supported", ["sql"], valid_to="2001-01-01")], vocab, CANDIDATE_REF,
                               as_of=AS_OF)
    assert garbled.value_of("sql") == 0.3 and any("validity-unknown" in w for w in garbled.warnings)


def test_f10_result_does_not_depend_on_action_order(engine, candidate):
    a = [{"skill": "rag", "effort_hours": 65, "evidence_goal": "portfolio-project"},
         {"skill": "rag", "effort_hours": 130, "evidence_goal": "coursework"}]
    one = apply_actions(engine, candidate, a, "x")["vector"].value_of("rag")
    two = apply_actions(engine, candidate, list(reversed(a)), "x")["vector"].value_of("rag")
    assert one == two == pytest.approx(0.8)
    coursework = apply_actions(engine, candidate, [{"skill": "people-management", "effort_hours": 250,
                                                    "evidence_goal": "coursework"}], "x")
    in_role = apply_actions(engine, candidate, [{"skill": "people-management", "effort_hours": 250,
                                                 "evidence_goal": "in-role"}], "x")
    rate_c = coursework["applied"][0]["evidence_level_after"]
    rate_r = in_role["applied"][0]["evidence_level_after"]
    assert rate_c == 0.3 and rate_r == pytest.approx(250 / 500)        # same rate; only the ceiling differs
    assert any("user-stated assumption" in r for r in in_role["risks"])
    surplus = apply_actions(engine, candidate, [{"skill": "python", "effort_hours": 500,
                                                 "evidence_goal": "portfolio-project"}], "x")
    assert any("have no effect" in r for r in surplus["risks"])


def test_f11_negated_requirements_are_not_read(vocab):
    v = job_to_vector({"id": "n", "skills": ["python"], "seniority": "senior",
                       "requirements": ["Kubernetes experience is NOT required", "No people management duties",
                                        "Nice to have: RAG", "Must have SQL"]}, vocab)
    assert "kubernetes" not in v.stated_features() and "people-management" not in v.stated_features()
    assert v.meta["min_qualifications"] == ("sql",)
    assert "rag" in v.stated_features()
    assert any(w.startswith("negated-requirements: 2") for w in v.warnings)


def test_f12_stepping_stones_are_capped(vocab, candidate):
    from careertwin_atlas.taxonomy import generate_postings
    base = generate_postings(300, vocab.skill_names, seed=4)
    many = [{**j, "role_family": f"{j['role_family']}-{i % 6}"} for i, j in enumerate(base)]
    eng = AtlasEngine.fit(many, vocab, n_boot=2)
    assert len(eng.profiles) >= 40
    plan = plan_paths(eng, build_transition_graph(eng), candidate, sorted(eng.profiles)[0], max_hops=3)
    assert len(plan["stepping_stones_considered"]) == 8 and plan["paths_evaluated"] == 1 + 8 + 56


def test_f13_metrics_are_translation_invariant():
    rng = np.random.default_rng(0)
    high, low = rng.standard_normal((150, 8)), rng.standard_normal((150, 2))
    base = neighborhood_metrics(high, low, 10)
    for offset in (1e4, 1e5):
        moved = neighborhood_metrics(high + offset, low, 10)
        assert moved["trustworthiness"] == pytest.approx(base["trustworthiness"], abs=1e-9)
        assert moved["knn_recall_mean"] == pytest.approx(base["knn_recall_mean"], abs=1e-9)
    assert base["knn_recall_exact_mean"] <= base["knn_recall_mean"]


def test_f14_sanitiser_markdown_and_svg_edge_cases(jobs, vocab):
    nasty = "a" + "؜﻿⁠\U000e0041\ud800" + "b"
    assert sanitize_label(nasty) == "a b"
    json.dumps(sanitize_label(nasty), ensure_ascii=False).encode("utf-8")
    escaped = _md_escape("![x](http://example.invalid/p.png) [y](http://e.invalid)")
    assert "![" not in escaped and "](" not in escaped
    tiny = AtlasEngine.fit(jobs[:3], vocab, n_boot=2)
    assert render_svg(tiny, build_atlas_bundle(tiny, None, include_clusters=False)).startswith("<svg")


def test_smaller_items_projection_id_scenario_id_and_model_loading(engine, candidate, vocab, claims):
    sc = simulate_transition(engine, candidate, "fde", [])
    axes = build_atlas_bundle(engine, candidate, method="interpretable-axes", include_clusters=False)
    with pytest.raises(InvalidInputError):
        projection_view(axes, "2026-11-10T00:00:00Z", [sc])
    with pytest.raises(InvalidInputError):
        projection_view(build_atlas_bundle(engine, candidate, include_clusters=False), "2026-11-10T00:00:00Z",
                        [{"id": "x", "outputs": {"model_ref": {"projection_id": "p"}}}])
    import dataclasses
    other = claims_to_vector(claims[:2], vocab, CANDIDATE_REF)
    a = dataclasses.replace(candidate, meta={**candidate.meta, "source_vector_ref": "a-skillvec/x"})
    b = dataclasses.replace(other, meta={**other.meta, "source_vector_ref": "a-skillvec/x"})
    assert simulate_transition(engine, a, "fde", [])["id"] != simulate_transition(engine, b, "fde", [])["id"]
    doc = engine.model.to_dict()
    for mutate in (lambda d: d.update(components=[[1.0] * len(d["mean"]), [1.0] * len(d["mean"])]),
                   lambda d: d.update(explained_variance_ratio=[0.5]),
                   lambda d: d.update(scale=[1e-300] * len(d["mean"]))):
        bad = json.loads(json.dumps(doc))
        mutate(bad)
        with pytest.raises(InvalidInputError):
            PCAModel.from_dict(bad)

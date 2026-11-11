"""Regression tests for adversarial review cycle 2 (findings 1-15, posted on Issue #4).

Each test reproduces the reviewer's failing case at commit 85c0536.
"""
import copy
import importlib.util
import json

import numpy as np
import pytest

from careertwin_atlas.adapters import A_API_VERSION, engine_from_skill_vectors
from careertwin_atlas.api import build_atlas_bundle, projection_view
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.errors import InvalidInputError
from careertwin_atlas.features import claims_to_vector, job_to_vector
from careertwin_atlas.gaps import analyze_gap
from careertwin_atlas.graph import build_transition_graph, evaluate_path, plan_paths
from careertwin_atlas.roles import centroid_drift
from careertwin_atlas.sensitivity import missing_salary_sensitivity, weight_sensitivity
from careertwin_atlas.simulator import apply_actions, simulate_transition

from conftest import AS_OF, CANDIDATE_REF, PKG_ROOT


def claim(cid, status, skills):
    return {"id": cid, "subject_id": CANDIDATE_REF, "claim_type": "skill", "claim_text": "Fictional",
            "status": status, "evidence_ids": [], "visibility": "private", "updated_at": AS_OF, "skills": skills}


def test_1_guessed_requirements_never_enter_the_shortfall(jobs, vocab, candidate):
    quant = [{**jobs[i], "id": f"quant-{i}", "role_family": "quant-analyst", "skills": [],
              "requirements": ["Strong SQL required"]} for i in range(30)]
    eng = AtlasEngine.fit(list(jobs) + quant, vocab, n_boot=2)
    prof = eng.profiles["quant-analyst"]
    stated = [n for n, v in zip(prof.feature_names, prof.requirement_profile) if v > 0 and not n.startswith("ctx:")]
    assert stated == ["sql"]                                              # only what the role states
    assert prof.unknown_cell_share > 0.9
    sql_only = claims_to_vector([claim("s", "supported", ["sql"])], vocab, CANDIDATE_REF)
    row = next(r for r in eng.role_table(sql_only) if r["role"] == "quant-analyst")
    assert row["requirement_shortfall"] == pytest.approx(0.0, abs=1e-9)   # evidenced SQL covers the only requirement
    assert analyze_gap(eng, sql_only, "quant-analyst")["skill_gaps"] == []
    generic = next(r for r in eng.role_table(candidate) if r["role"] == "quant-analyst")
    assert generic["requirement_shortfall"] > row["requirement_shortfall"]
    assert generic["role_unknown_cell_share"] == prof.unknown_cell_share


def test_2_a_tiny_high_cap_action_does_not_lift_coursework_hours(engine, candidate):
    alone = apply_actions(engine, candidate, [{"skill": "people-management", "effort_hours": 500,
                                               "evidence_goal": "coursework"}], "x")
    mixed = apply_actions(engine, candidate, [{"skill": "people-management", "effort_hours": 500,
                                               "evidence_goal": "coursework"},
                                              {"skill": "people-management", "effort_hours": 0.01,
                                               "evidence_goal": "in-role"}], "x")
    assert alone["applied"][0]["evidence_level_after"] == 0.3
    assert mixed["vector"].value_of("people-management") == pytest.approx(0.3 + 0.01 / 500, abs=1e-9)
    assert not mixed["applied"][0]["reaches_evidenced_level"]
    zero = apply_actions(engine, candidate, [{"skill": "rag", "effort_hours": 300, "evidence_goal": "coursework"},
                                             {"skill": "rag", "effort_hours": 0,
                                              "evidence_goal": "verified-work-sample"}], "x")
    assert zero["applied"][0]["evidence_level_after"] == 0.6 and zero["applied"][0]["feature_ceiling"] == 0.6


def test_3_contradicted_skills_are_path_blockers_not_study_items(engine, vocab):
    v = claims_to_vector([claim("c", "contradicted", ["kubernetes"]), claim("p", "supported", ["python"])],
                         vocab, CANDIDATE_REF)
    graph = build_transition_graph(engine)
    res = evaluate_path(engine, graph, v, ("platform",), 6, 12)
    step = res["steps"][0]
    assert step["blocked_by_contradiction"] == ["kubernetes"]
    assert "kubernetes" not in [m["feature"] for m in step["missing_study_or_project_evidence"]]
    assert res["fits_time_horizon"] in ("blocked", "no") and res["contradiction_blockers_total"] == 1
    via = evaluate_path(engine, graph, v, ("backend", "platform"), 6, 12)
    assert via["steps"][-1]["blocked_by_contradiction"] == ["kubernetes"]   # tenure never confers it


def test_4_one_cost_model_and_honest_alternative(engine, candidate):
    graph = build_transition_graph(engine)
    res = evaluate_path(engine, graph, candidate, ("ai-architect",), 6, 12)
    mlops = next(m for m in res["steps"][0]["missing_study_or_project_evidence"] if m["feature"] == "mlops")
    ref = engine.vocab.get("mlops").effort_mid
    assert sum(mlops["effort_hours_range"]) / 2 == pytest.approx(0.8 * ref, abs=0.1)   # same as simulator
    sim = apply_actions(engine, candidate, [{"skill": "mlops", "effort_hours": 0.8 * ref,
                                             "evidence_goal": "portfolio-project"}], "x")
    assert sim["applied"][0]["evidence_level_after"] == pytest.approx(0.8)
    sc = simulate_transition(engine, candidate, "field-cto", [
        {"skill": "cloud-architecture", "effort_hours": 120, "evidence_goal": "portfolio-project"},
        {"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project"},
        {"skill": "client-exposure", "effort_hours": 90, "evidence_goal": "coursework"}])
    alt = sc["outputs"]["alternatives"]["same_budget"]
    assert "may not be optimal" in alt["label"]
    assert alt["better_than_your_plan"] == (alt["shortfall_vs_your_plan"] < 0)
    alt_run = apply_actions(engine, candidate, alt["planned_actions"], "y")
    assert not any("have no effect" in r for r in alt_run["risks"])      # the heuristic no longer wastes hours


@pytest.mark.parametrize("line", ["Kubernetes isn’t required", "We don’t need people management",
                                  "Kubernetes isnʼt required"])
def test_5_typographic_apostrophes_still_negate(vocab, line):
    v = job_to_vector({"id": "n", "skills": ["python"], "seniority": "senior", "requirements": [line]}, vocab)
    assert v.meta["min_qualifications"] == ()


def _a_payload(n=30):
    vocab = ["python", "kubernetes", "sql"]
    jobs = [{"entity_id": f"job-{i}", "observed": True, "values": [1.0, float(i % 2), 0.0],
             "role_family": ("a", "b")[i % 2], "vocabulary_version": "t"} for i in range(n)]
    return {"api_version": A_API_VERSION, "vocabulary": vocab, "vocabulary_version": "t", "jobs": jobs,
            "candidate": {"entity_id": CANDIDATE_REF, "observed": True, "values": [1.0, 0.0, 0.0]}}


def test_6_provenance_signals_must_agree_and_every_input_is_counted():
    payload = {**_a_payload(12), "synthetic": True}
    records = [{"posting": {"id": j["entity_id"], "source": "greenhouse"}, "ext": {"synthetic": False}}
               for j in payload["jobs"]]
    res = engine_from_skill_vectors(payload, n_boot=2, records=records)
    b = build_atlas_bundle(res["engine"], None, include_clusters=False)
    assert b["synthetic_only"] is False and "synthetic" not in b["input_sources"]
    assert any("provenance-conflict" in w for v in res["engine"].matrix.vectors for w in v.warnings)
    mixed = _a_payload(30)
    mixed["jobs"].append({"entity_id": "unread", "observed": False, "values": [0.0, 0.0, 0.0],
                          "role_family": "a", "vocabulary_version": "t", "synthetic": False})
    for j in mixed["jobs"][:30]:
        j["synthetic"] = True
    b2 = build_atlas_bundle(engine_from_skill_vectors(mixed, n_boot=2)["engine"], None, include_clusters=False)
    assert sum(b2["input_sources"].values()) == 31 and b2["synthetic_only"] is False


def _stage():
    spec = importlib.util.spec_from_file_location("e2e_stage_c2", PKG_ROOT / "e2e_stage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("values, jobs_doc", [([1e200, 0.0], None), ([-5.0, 1.0], None), ([1.0, 0.0], 42)])
def test_7_e2e_project_validates_and_never_writes_half_a_stage(tmp_path, capsys, values, jobs_doc):
    vectors = [{"ref": f"r{i}", "entity_id": f"e{i}", "values": values if i == 0 else [float(i % 2), 1.0]}
               for i in range(12)]
    (tmp_path / "vectors.json").write_text(json.dumps({"vocabulary": ["a", "b"], "vectors": vectors}),
                                           encoding="utf-8")
    if jobs_doc is not None:
        (tmp_path / "jobs.json").write_text(json.dumps(jobs_doc), encoding="utf-8")
    assert _stage().main(["project", "--vectors", str(tmp_path / "vectors.json"), "--out-dir", str(tmp_path)]) == 2
    assert not (tmp_path / "atlas_points.json").exists() and not (tmp_path / "atlas_diagnostics.json").exists()
    assert "error:" in capsys.readouterr().err


def test_8_states_cannot_bypass_observed_false():
    payload = _a_payload()
    payload["candidate"].update(observed=False, states=["supported", "none", "none"])
    with pytest.raises(InvalidInputError, match="observed must equal"):
        engine_from_skill_vectors(payload, n_boot=2)


def test_9_edges_are_lazy_and_permutations_are_capped(vocab, candidate):
    from careertwin_atlas.taxonomy import generate_postings
    many = [{**j, "role_family": f"fam-{i}"} for i, j in enumerate(generate_postings(400, vocab.skill_names, seed=9))]
    eng = AtlasEngine.fit(many, vocab, n_boot=2)
    graph = build_transition_graph(eng)
    assert len(graph.edges) == 400 * 399
    plan_paths(eng, graph, candidate, "fam-0", max_hops=2)
    assert len(graph.edges._cache) <= 400                              # only edges into the target were built
    early = [job_to_vector({"id": f"e{f}-{i}", "skills": ["python"], "seniority": "senior",
                            "role_family": f"f{f}"}, vocab) for f in range(300) for i in range(5)]
    late = [job_to_vector({"id": f"l{f}-{i}", "skills": ["sql"], "seniority": "senior",
                           "role_family": f"f{f}"}, vocab) for f in range(300) for i in range(5)]
    res = centroid_drift(early, late, np.zeros(len(vocab.features)), n_perm=100)
    assert res["n_perm"] == 20_000 and res["underpowered"] is True and res["underpowered_note"]


def test_10_missing_pay_has_bounds_in_the_reported_ordering(jobs, vocab, candidate):
    partial = [{**j, "compensation": None} if j["role_family"] == "backend" else j for j in jobs]
    eng = AtlasEngine.fit(partial, vocab, n_boot=2)
    ms = missing_salary_sensitivity(eng, candidate, {"compensation": 0.5, "skills": 0.5})
    bounds = ms["rank_bounds_for_roles_with_unknown_compensation"]
    assert set(bounds) == {"backend"}
    assert bounds["backend"]["best_possible_rank"] <= bounds["backend"]["worst_possible_rank"]
    assert "equivalent to assuming" in ms["handling"]


def test_11_projection_view_refuses_a_scenario_from_different_evidence(engine, candidate, claims, vocab):
    other = claims_to_vector(claims[:2], vocab, CANDIDATE_REF)
    sc = simulate_transition(engine, other, "fde", [])
    bundle = build_atlas_bundle(engine, candidate, include_clusters=False)
    with pytest.raises(InvalidInputError, match="different evidence vector"):
        projection_view(bundle, "2026-11-10T00:00:00Z", [sc])
    clash = copy.copy(candidate)
    object.__setattr__(clash, "entity_id", engine.matrix.ids[0])
    with pytest.raises(InvalidInputError, match="already used"):
        build_atlas_bundle(engine, clash, include_clusters=False)


def test_12_path_label_never_says_none(engine, candidate, vocab):
    v = claims_to_vector([claim("c", "contradicted", ["kubernetes"]), claim("p", "supported", ["python"])],
                         vocab, CANDIDATE_REF)
    sc = simulate_transition(engine, v, "platform", [{"skill": "kubernetes", "effort_hours": 50}])
    bundle = build_atlas_bundle(engine, v, include_clusters=False)
    label = projection_view(bundle, "2026-11-10T00:00:00Z", [sc])["paths"][0]["label"]
    assert "None" not in label and "no movement" in label


def test_13_scenario_id_ignores_numeric_type(engine, candidate):
    a = simulate_transition(engine, candidate, "fde", [], hours_per_week=6)
    b = simulate_transition(engine, candidate, "fde", [], hours_per_week=6.0)
    assert a["id"] == b["id"]


def test_14_one_role_is_not_assessable(jobs, vocab, candidate):
    eng = AtlasEngine.fit([j for j in jobs if j["role_family"] == "backend"], vocab, n_boot=2)
    res = weight_sensitivity(eng, candidate, n_draws=20)
    assert res["verdict"] == "not-assessable" and res["baseline_ordering"] == ["backend"]


def test_15_unmet_in_role_requirements_block_the_horizon_verdict(engine, candidate):
    res = evaluate_path(engine, build_transition_graph(engine), candidate, ("field-cto",), 40, 600)
    assert res["entry_barriers_total"] > 0 and res["fits_time_horizon"] == "blocked"
    assert "blocked" in res["fits_time_horizon_note"]

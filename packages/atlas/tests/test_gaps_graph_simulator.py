"""Gap analysis, transition graph and counterfactual simulator (workstreams 7-9 + acceptance)."""
import json

import numpy as np
import pytest

from careertwin_atlas.errors import FeatureSpaceMismatchError, InvalidInputError
from careertwin_atlas.features import STATE_CONTRADICTED, claims_to_vector
from careertwin_atlas.gaps import EVIDENCED_LEVEL, analyze_gap, explain_movement
from careertwin_atlas.graph import ELIGIBILITY_NOTE, build_transition_graph, evaluate_path, plan_paths
from careertwin_atlas.simulator import EVIDENCE_GOALS, NON_ROLE_CAP, apply_actions, simulate_transition
from careertwin_atlas.vocabulary import ACQ_ROLE

from conftest import PKG_ROOT, find_forbidden_keys

FORBIDDEN_KEY_FRAGMENTS = ("probab", "odds", "chance", "likelihood", "offer_rate", "hire_rate",
                           "salary_pred", "expected_salary", "win_rate")
PLAN = [
    {"skill": "cloud-architecture", "effort_hours": 120, "evidence_goal": "portfolio-project"},
    {"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project"},
    {"skill": "client-exposure", "effort_hours": 90, "evidence_goal": "coursework"},
]


@pytest.fixture(scope="module")
def graph(engine):
    return build_transition_graph(engine)


@pytest.fixture(scope="module")
def scenario(engine, candidate, graph):
    return simulate_transition(engine, candidate, "field-cto", PLAN, 12, graph=graph)


# ---------------------------------------------------------------- gap analysis --------
def test_gap_distinguishes_missing_weak_and_contradicted_evidence(engine, candidate):
    gap = analyze_gap(engine, candidate, "platform")
    by = {r["feature"]: r for r in gap["requirements"]}
    assert by["kubernetes"]["candidate_state"] == "contradicted"
    assert by["aws"]["candidate_state"] == "weak-evidence" and by["aws"]["candidate_evidence_level"] == 0.6
    assert by["sre"]["candidate_state"] == "no-evidence-on-file"
    assert "kubernetes" in gap["contradicted"] and "kubernetes" not in gap["skill_gaps"]
    assert "sre" in gap["skill_gaps"] and "aws" in gap["evidence_gaps"]
    assert by["aws"]["evidence_refs"] == ["claim:claim-syn-003:self-reported"]
    assert gap["status"] == "hypothetical_gap_not_probability"
    backend = analyze_gap(engine, candidate, "backend")
    states = {r["feature"]: r["candidate_state"] for r in backend["requirements"]}
    assert states["python"] == "evidenced" and states["api-design"] == "evidenced"


def test_gap_reports_unknowns_and_incompleteness(engine, candidate):
    gap = analyze_gap(engine, candidate, "field-cto")
    assert gap["unknown_candidate_inputs"] == ["ctx:seniority-level"]
    assert gap["unobserved_requirements"]["unmapped_requirement_share"] > 0
    assert "incomplete" in gap["unobserved_requirements"]["note"]
    assert gap["minimum_qualifications"]                       # read from requirement lines
    assert gap["confidence"]["level"] in ("low", "medium", "high")
    assert "not confidence in any hiring outcome" in gap["confidence"]["meaning"]
    prox = gap["proximity"]
    assert prox["requirement_shortfall"] <= prox["full_space_distance"] + 1e-9
    assert 1 <= prox["shortfall_rank_among_roles"] <= 8


def test_gap_confidence_drops_for_thin_role_profiles(jobs, vocab, claims):
    from careertwin_atlas.engine import AtlasEngine
    small = AtlasEngine.fit(jobs[:24], vocab, n_boot=20)
    cand = claims_to_vector(claims, vocab, "candidate-synthetic-001")
    role = sorted(small.profiles)[0]
    conf = analyze_gap(small, cand, role)["confidence"]
    assert conf["level"] == "low" and len(conf["reasons"]) >= 2


def test_gap_rejects_unknown_role_bad_thresholds_and_foreign_vectors(engine, candidate, claims, vocab):
    with pytest.raises(InvalidInputError):
        analyze_gap(engine, candidate, "astronaut")
    with pytest.raises(InvalidInputError):
        analyze_gap(engine, candidate, "backend", core_threshold=0.2, common_threshold=0.5)
    foreign = claims_to_vector(claims, vocab.without_features(["python"]), "candidate-synthetic-001")
    with pytest.raises(FeatureSpaceMismatchError):
        analyze_gap(engine, foreign, "backend")


def test_role_table_exposes_where_the_map_disagrees(engine, candidate):
    table = engine.role_table(candidate)
    assert [r["shortfall_rank"] for r in table] == list(range(1, 9))
    assert table[0]["role"] == "backend"                       # the synthetic candidate is backend-shaped
    assert all(r["requirement_shortfall"] >= 0 and r["map_distance"] >= 0 for r in table)
    assert all(r["map_rank_disagrees"] == (r["map_rank"] != r["full_space_rank"]) for r in table)


# ---------------------------------------------------------------- transition graph ----
def test_graph_edges_list_required_evidence_and_constraints(graph):
    assert len(graph.nodes) == 8 and len(graph.edges) == 56
    edge = graph.edges[("backend", "field-cto")]
    assert {"enterprise-sales", "executive-communication"} <= set(edge.role_exposure_features)
    assert set(edge.role_exposure_features) <= set(edge.required_evidence)
    assert edge.effort_hours_low < edge.effort_hours_high
    assert edge.seniority_delta > 0.25 and edge.client_exposure_delta > 0
    assert any("in-role" in c for c in edge.constraints)
    assert any("seniority" in c for c in edge.constraints)
    back = graph.edges[("field-cto", "backend")]
    assert back.required_evidence != edge.required_evidence    # directed, not symmetric
    doc = graph.to_dict()
    assert doc["eligibility_note"] == ELIGIBILITY_NOTE and len(doc["edges"]) == 56
    json.dumps(doc)


def test_paths_include_direct_and_stepping_stones_with_explicit_tradeoff(engine, candidate, graph):
    plan = plan_paths(engine, graph, candidate, "field-cto", hours_per_week=6, horizon_months=12)
    assert plan["paths_evaluated"] == 1 + 7 + 42
    kinds = {a["kind"] for a in plan["alternatives"]}
    assert kinds == {"direct", "stepping-stone"}
    direct = next(a for a in plan["alternatives"] if a["kind"] == "direct")
    via_fde = evaluate_path(engine, graph, candidate, ("fde", "field-cto"), 6, 12)
    assert via_fde["entry_barriers_hardest_step"] < direct["entry_barriers_hardest_step"]   # easier hardest step
    assert via_fde["months_range"][0] > direct["months_range"][0]                           # but slower
    assert via_fde["assumed_tenure_months_total"] == 18.0
    assert plan["eligibility_note"] == ELIGIBILITY_NOTE
    assert "not a likelihood" in plan["ranking_note"]
    assert any(a["pareto_optimal_on_time_and_barriers"] for a in plan["alternatives"])
    for a in plan["alternatives"]:
        assert a["months_range"][0] <= a["months_range"][1]
        assert a["fits_time_horizon"] in ("yes", "no", "uncertain")
        assert any("seniority is unknown" in c for c in a["caveats"])


def test_path_weights_change_the_ranking(engine, candidate, graph):
    fast = plan_paths(engine, graph, candidate, "field-cto", weights={"time": 5.0, "barriers": 0.0, "effort": 0.0})
    careful = plan_paths(engine, graph, candidate, "field-cto", weights={"time": 0.01, "barriers": 5.0, "effort": 0.0})
    assert fast["alternatives"][0]["kind"] == "direct"
    assert careful["alternatives"][0]["kind"] == "stepping-stone"


def test_path_planning_rejects_bad_arguments(engine, candidate, graph):
    for kwargs in ({"hours_per_week": 0}, {"hours_per_week": float("nan")}, {"horizon_months": 0},
                   {"horizon_months": 1.5}, {"max_hops": 9}, {"weights": {"luck": 1.0}},
                   {"weights": {"time": -1.0}}):
        with pytest.raises(InvalidInputError):
            plan_paths(engine, graph, candidate, "field-cto", **kwargs)
    with pytest.raises(InvalidInputError):
        plan_paths(engine, graph, candidate, "astronaut")
    with pytest.raises(InvalidInputError):
        build_transition_graph(engine, core_threshold=0.0)


# ---------------------------------------------------------------- simulator -----------
def test_scenario_validates_against_frozen_contract_and_lane_schema(scenario, contracts):
    jsonschema = pytest.importorskip("jsonschema")
    errors = list(jsonschema.Draft202012Validator(contracts["transition-scenario.schema.json"]).iter_errors(scenario))
    assert not errors, errors
    own = json.loads((PKG_ROOT / "schemas" / "transition-scenario-outputs.schema.json").read_text(encoding="utf-8"))
    assert not list(jsonschema.Draft202012Validator(own).iter_errors(scenario["outputs"]))
    point_validator = jsonschema.Draft202012Validator(contracts["atlas-point.schema.json"])
    for p in scenario["outputs"]["map_points"]:
        assert not list(point_validator.iter_errors(p))
    assert scenario["status"] == "hypothetical"
    json.dumps(scenario, allow_nan=False)


def test_acceptance_explicit_missing_evidence_and_effort_alternatives(scenario):
    out = scenario["outputs"]
    assert out["remaining_missing_evidence"], "simulator must list what is still missing"
    assert all({"feature", "state", "acquisition"} <= set(r) for r in out["remaining_missing_evidence"])
    assert {"enterprise-sales", "executive-communication"} <= {r["feature"] for r in out["remaining_missing_evidence"]}
    assert out["alternatives"]["same_budget"]["planned_actions"]
    assert len(out["alternatives"]["paths"]) >= 2
    assert all("effort_hours_range" in a and "months_range" in a for a in out["alternatives"]["paths"])
    assert out["effort"]["planned_hours"] == 290.0
    assert out["hypothetical"]["requirement_shortfall"] < out["baseline"]["requirement_shortfall"]


def test_no_unsupported_probability_or_compensation_claims(scenario, engine, candidate):
    assert scenario["outputs"]["not_a_hiring_probability"] is True
    assert find_forbidden_keys(scenario, FORBIDDEN_KEY_FRAGMENTS) == []
    assert find_forbidden_keys(analyze_gap(engine, candidate, "fde"), FORBIDDEN_KEY_FRAGMENTS) == []
    text = json.dumps(scenario).lower()
    for phrase in ("% chance", "probability of an offer is", "you will get", "guaranteed"):
        assert phrase not in text
    assert "compensation" not in json.dumps(scenario["outputs"]["hypothetical"]).lower()
    assert any("No path guarantees eligibility" in a for a in scenario["assumptions"])


def test_scenario_is_deterministic_and_id_depends_on_inputs(engine, candidate, graph, scenario):
    again = simulate_transition(engine, candidate, "field-cto", PLAN, 12, graph=graph)
    assert json.dumps(again, sort_keys=True) == json.dumps(scenario, sort_keys=True)
    other = simulate_transition(engine, candidate, "field-cto", PLAN, 18, graph=graph)
    assert other["id"] != scenario["id"] and other["id"].startswith("scn-")


def test_role_exposure_cannot_be_bought_with_coursework(engine, candidate):
    res = apply_actions(engine, candidate, [{"skill": "enterprise-sales", "effort_hours": 5000.0,
                                             "evidence_goal": "coursework"}], "x")
    assert res["applied"][0]["evidence_level_after"] == NON_ROLE_CAP
    assert not res["applied"][0]["reaches_evidenced_level"]
    assert any("normally gained in-role" in r for r in res["risks"])
    real = apply_actions(engine, candidate, [{"skill": "enterprise-sales", "effort_hours": 5000.0,
                                              "evidence_goal": "client-engagement"}], "x")
    assert real["applied"][0]["evidence_level_after"] == 1.0
    assert engine.vocab.get("enterprise-sales").acquisition == ACQ_ROLE


def test_contradicted_claims_are_not_simulated_away(engine, candidate):
    res = apply_actions(engine, candidate, [{"skill": "k8s", "effort_hours": 400.0,
                                             "evidence_goal": "verified-work-sample"}], "x")
    assert res["applied"][0]["blocked_by_contradiction"] is True
    assert res["vector"].state_of("kubernetes") == STATE_CONTRADICTED
    assert res["vector"].value_of("kubernetes") == 0.0
    sc = simulate_transition(engine, candidate, "platform", [{"skill": "k8s", "effort_hours": 400.0}])
    assert any("contradicted core claims remain" in r for r in sc["risks"])
    assert sc["outputs"]["delta"]["requirement_shortfall"] == 0.0


def test_unknown_skills_zero_effort_and_irrelevant_actions_are_surfaced(engine, candidate):
    sc = simulate_transition(engine, candidate, "field-cto", [
        {"skill": "underwater basket weaving", "effort_hours": 100},
        {"skill": "ai-strategy", "effort_hours": 0, "evidence_goal": "portfolio-project"},
        {"skill": "kubernetes-administration-xyz", "effort_hours": 10},
        {"skill": "frontend", "effort_hours": 60, "evidence_goal": "portfolio-project"},
    ])
    out = sc["outputs"]
    assert out["unmapped_actions"] == ["underwater basket weaving", "kubernetes-administration-xyz"]
    assert any("outside the vocabulary" in r for r in sc["risks"])
    assert any("zero effort hours" in r for r in sc["risks"])
    assert any(r.startswith("frontend: stated in only") for r in sc["risks"])
    # 'frontend' is background noise for this role (stated in ~4% of postings): the change is negligible
    assert -1e-3 < out["delta"]["requirement_shortfall"] <= 0.0


def test_empty_plan_equals_baseline(engine, candidate):
    sc = simulate_transition(engine, candidate, "fde", [])
    out = sc["outputs"]
    assert out["baseline"] == out["hypothetical"]
    assert out["delta"]["requirement_shortfall"] == 0.0 and out["movement"]["features_changed"] == 0
    assert any("hypothetical equals baseline" in r for r in sc["risks"])


def test_over_budget_plans_and_missing_goals_are_flagged(engine, candidate):
    sc = simulate_transition(engine, candidate, "ai-architect",
                             [{"skill": "mlops", "effort_hours": 900}], time_horizon_months=3, hours_per_week=5)
    assert sc["outputs"]["effort"]["fits_time_budget"] is False
    assert any(r.startswith("plan needs 900 h") for r in sc["risks"])
    assert any("evidence goal missing or not recognised" in a for a in sc["assumptions"])
    assert sc["outputs"]["applied_actions"][0]["evidence_level_after"] == EVIDENCE_GOALS["coursework"]
    assert sc["planned_actions"] == [{"skill": "mlops", "effort_hours": 900.0}]


@pytest.mark.parametrize("seed", range(10))
def test_property_relevant_evidence_never_increases_shortfall(engine, candidate, seed):
    rng = np.random.default_rng(seed)
    target = sorted(engine.profiles)[int(rng.integers(0, 8))]
    skills = list(engine.vocab.skill_names)
    goals = list(EVIDENCE_GOALS)
    plan = [{"skill": skills[int(rng.integers(0, len(skills)))], "effort_hours": float(rng.uniform(0, 400)),
             "evidence_goal": goals[int(rng.integers(0, len(goals)))]} for _ in range(int(rng.integers(1, 7)))]
    sc = simulate_transition(engine, candidate, target, plan)
    out = sc["outputs"]
    assert out["hypothetical"]["requirement_shortfall"] <= out["baseline"]["requirement_shortfall"] + 1e-9
    assert out["hypothetical"]["core_summary"]["evidenced"] >= out["baseline"]["core_summary"]["evidenced"]
    for a in out["applied_actions"]:
        assert 0.0 <= a["evidence_level_before"] <= a["evidence_level_after"] <= 1.0
        assert 0.0 <= a["completion"] <= 1.0
    assert find_forbidden_keys(sc, FORBIDDEN_KEY_FRAGMENTS) == []


def test_same_budget_alternative_is_never_worse_on_study_features(engine, candidate):
    plan = [{"skill": "frontend", "effort_hours": 200, "evidence_goal": "portfolio-project"}]
    sc = simulate_transition(engine, candidate, "ai-architect", plan)
    alt = sc["outputs"]["alternatives"]["same_budget"]
    assert alt["hours_used"] <= 200.0 + 1e-6
    assert alt["requirement_shortfall"] < sc["outputs"]["hypothetical"]["requirement_shortfall"]
    assert all(a["evidence_goal"] == "portfolio-project" for a in alt["planned_actions"])


@pytest.mark.parametrize("bad_plan", [
    "python",
    [{"skill": "python"}],
    [{"skill": "", "effort_hours": 1}],
    [{"skill": "python", "effort_hours": -1}],
    [{"skill": "python", "effort_hours": float("inf")}],
    [{"skill": "python", "effort_hours": "ten"}],
    [{"skill": "python", "effort_hours": True}],
    [{"skill": "python", "effort_hours": 1, "evidence_goal": 7}],
    [{"skill": "python", "effort_hours": 1, "probability_of_offer": 0.9}],
    [["python", 10]],
    [{"skill": "python", "effort_hours": 1}] * 51,
])
def test_invalid_plans_are_rejected(engine, candidate, bad_plan):
    with pytest.raises(InvalidInputError):
        simulate_transition(engine, candidate, "backend", bad_plan)


def test_invalid_scenario_arguments_are_rejected(engine, candidate):
    for kwargs in ({"time_horizon_months": 0}, {"time_horizon_months": 2.5}, {"time_horizon_months": True},
                   {"hours_per_week": 0}, {"hours_per_week": 500}, {"hours_per_week": float("nan")}):
        with pytest.raises(InvalidInputError):
            simulate_transition(engine, candidate, "backend", [], **kwargs)
    for target in ("astronaut", None, 5):
        with pytest.raises(InvalidInputError):
            simulate_transition(engine, candidate, target, [])


# ---------------------------------------------------------------- movement ------------
def test_map_shift_decomposition_is_exact_and_partial_visibility_is_reported(engine, candidate):
    res = apply_actions(engine, candidate, PLAN, "x")
    mv = explain_movement(engine, candidate, res["vector"], "field-cto", top=50)
    before, after = engine.project(candidate)["coords"], engine.project(res["vector"])["coords"]
    np.testing.assert_allclose(mv["map_shift"], after - before, atol=1e-5)
    total = np.sum([f["map_shift"] for f in mv["per_feature"]], axis=0)
    np.testing.assert_allclose(total, mv["map_shift"], atol=1e-4)                   # per-feature parts add up
    assert 0.0 < mv["share_of_movement_visible_on_map"] <= 1.0
    assert mv["map_shift_length"] <= mv["full_space_shift_length"] + 1e-9         # projection never lengthens
    d = mv["full_space_distance_to_role"]
    assert d["after"] < d["before"]
    assert all(f["full_space_sq_distance_change"] <= 1e-9 for f in mv["per_feature"])
    same = explain_movement(engine, candidate, candidate)
    assert same["features_changed"] == 0 and same["share_of_movement_visible_on_map"] is None
    with pytest.raises(InvalidInputError):
        explain_movement(engine, candidate, candidate, "astronaut")

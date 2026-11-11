"""Consumer-facing shapes for lane D: projection view, scenario summary fields, role lookup, goal text."""
import json

import pytest

from careertwin_atlas.api import build_atlas_bundle, projection_view
from careertwin_atlas.errors import InvalidInputError
from careertwin_atlas.simulator import EVIDENCE_GOALS, parse_evidence_goal, simulate_transition

from conftest import PKG_ROOT, find_forbidden_keys

GENERATED_AT = "2026-11-10T12:00:00Z"


def _schema(name):
    return json.loads((PKG_ROOT / "schemas" / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scenario(engine, candidate):
    return simulate_transition(engine, candidate, "field-cto", [
        {"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project: fictional AI roadmap"},
        {"skill": "cloud-architecture", "effort_hours": 120, "evidence_goal": "portfolio-project"},
    ])


def test_projection_view_validates_and_links_scenario_paths(engine, candidate, scenario, contracts):
    jsonschema = pytest.importorskip("jsonschema")
    bundle = build_atlas_bundle(engine, candidate, include_clusters=False)
    view = projection_view(bundle, GENERATED_AT, [scenario])
    assert not list(jsonschema.Draft202012Validator(_schema("atlas-projection.schema.json")).iter_errors(view))
    point_v = jsonschema.Draft202012Validator(contracts["atlas-point.schema.json"])
    assert all(not list(point_v.iter_errors(p)) for p in view["points"])
    assert view["generated_at"] == GENERATED_AT and view["schema_version"] == "atlas-projection/0.1"
    ids = {p["entity_id"] for p in view["points"]}
    assert {e["entity_id"] for e in view["entities"]} == ids               # every point has a side-table entry
    assert len(view["paths"]) == 1
    path = view["paths"][0]
    assert path["scenario_id"] == scenario["id"] and {path["from_entity_id"], path["to_entity_id"]} <= ids
    scen_entity = next(e for e in view["entities"] if e["entity_id"] == path["to_entity_id"])
    assert scen_entity["representation_type"] == "scenario" and scen_entity["scenario_id"] == scenario["id"]
    assert view["diagnostics"]["trustworthiness"]["k"] == bundle["diagnostics"]["k_neighbours"]
    assert view["warnings"][0] == bundle["information_loss"]["statement"]
    assert view["flags"]["two_d_geometry_is_not_match_metric"] is True
    assert [a["label"] for a in view["axes"]] == ["PCA axis 1", "PCA axis 2"]
    assert "Explains" in view["axes"][0]["description"]


def test_candidate_entity_reports_evidence_basis(engine, candidate):
    bundle = build_atlas_bundle(engine, candidate, include_clusters=False)
    cand = next(e for e in bundle["entities"] if e["entity_id"] == candidate.entity_id)
    assert cand["evidence_basis"] == "mixed"                                # supported + self-reported + contradicted
    assert cand["top_skills"][0] in ("python", "api-design", "distributed-systems", "testing")
    job = next(e for e in bundle["entities"] if e["representation_type"] == "job")
    assert job["job_id"] == job["entity_id"] and job["evidence_basis"] == "not-applicable"


def test_projection_view_rejects_mismatched_inputs(engine, candidate, scenario):
    bundle = build_atlas_bundle(engine, candidate, include_clusters=False)
    with pytest.raises(InvalidInputError):
        projection_view(bundle, "today", [scenario])
    with pytest.raises(InvalidInputError):
        projection_view({"schema_version": "other"}, GENERATED_AT)
    axes_bundle = build_atlas_bundle(engine, candidate, method="interpretable-axes", include_clusters=False)
    with pytest.raises(InvalidInputError, match="another projection"):
        projection_view(axes_bundle, GENERATED_AT, [scenario])             # scenario points are PCA coordinates


def test_scenario_outputs_carry_lane_d_summary_fields(scenario):
    jsonschema = pytest.importorskip("jsonschema")
    out = scenario["outputs"]
    assert not list(jsonschema.Draft202012Validator(_schema("transition-scenario-outputs.schema.json"))
                    .iter_errors(out))
    assert out["schema_version"] == "scenario-outputs/0.1" and out["target_role_family"] == "field-cto"
    assert out["target_skills"] and 0.0 <= out["coverage_before"] <= out["coverage_after"] <= 1.0
    assert out["coverage_before"] == out["baseline"]["core_summary"]["evidenced"] / len(out["target_skills"])
    assert out["leadership_exposure"] in ("low", "medium", "high")
    assert out["uncertainty"] in ("low", "medium", "high")
    assert out["uncertainty_note"].startswith("Uncertainty of the role description, not of any outcome")
    assert out["atlas_entity_id"] == out["map_points"][1]["entity_id"]
    assert find_forbidden_keys(scenario, ("probab", "odds", "chance", "likelihood")) == []


def test_target_role_accepts_label_and_keeps_callers_text(engine, candidate):
    by_label = simulate_transition(engine, candidate, "Field CTO", [])
    by_id = simulate_transition(engine, candidate, "field-cto", [])
    assert by_label["outputs"]["target_role_family"] == "field-cto"
    assert by_label["target_role"] == "Field CTO"                          # caller's text is echoed, not rewritten
    assert by_label["outputs"]["baseline"] == by_id["outputs"]["baseline"]
    assert simulate_transition(engine, candidate, "field cto", [])["outputs"]["target_role_family"] == "field-cto"
    with pytest.raises(InvalidInputError, match="unknown target_role"):
        simulate_transition(engine, candidate, "Chief Astronaut", [])


def test_evidence_goal_kind_prefix_and_free_text(engine, candidate):
    assert parse_evidence_goal("portfolio-project") == ("portfolio-project", "")
    assert parse_evidence_goal("Portfolio Project: build a fictional demo") == ("portfolio-project",
                                                                               "build a fictional demo")
    assert parse_evidence_goal("Lead one fictional customer integration end to end") == (
        None, "Lead one fictional customer integration end to end")
    assert parse_evidence_goal(None) == (None, "")
    sc = simulate_transition(engine, candidate, "fde", [
        {"skill": "customer-delivery", "effort_hours": 80,
         "evidence_goal": "Lead one fictional customer integration end to end and write it up."},
        {"skill": "sql", "effort_hours": 30, "evidence_goal": "verified-work-sample: fictional data exercise"},
    ])
    applied = {a["feature"]: a for a in sc["outputs"]["applied_actions"]}
    assert applied["customer-delivery"]["evidence_goal"] == "coursework"    # free text: default kind, stated
    assert applied["sql"]["evidence_goal"] == "verified-work-sample"
    assert any("Prefix the goal with a kind" in a for a in sc["assumptions"])
    assert sc["planned_actions"][0]["evidence_goal"].startswith("Lead one fictional customer")
    long_goal = "x" * 1000 + chr(0)
    sc2 = simulate_transition(engine, candidate, "fde", [{"skill": "sql", "effort_hours": 1, "evidence_goal": long_goal}])
    assert len(sc2["planned_actions"][0]["evidence_goal"]) <= 300 and chr(0) not in sc2["planned_actions"][0]["evidence_goal"]
    assert set(EVIDENCE_GOALS) >= {"coursework", "portfolio-project", "client-engagement"}

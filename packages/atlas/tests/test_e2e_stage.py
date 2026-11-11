"""Lane E integration stages (packages/atlas/e2e_stage.py) on artifacts shaped like E's stub output.

Artifacts are produced here with lane C's own generator in E's documented shapes; E's code is not imported.
The run against E's real stub and validators at a pinned SHA is recorded in HANDOFF.md.
"""
import importlib.util
import json

import numpy as np
import pytest

from conftest import PKG_ROOT

spec = importlib.util.spec_from_file_location("e2e_stage", PKG_ROOT / "e2e_stage.py")
e2e_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e_stage)


@pytest.fixture()
def artifacts(tmp_path, jobs, claims):
    vocab = sorted({s for j in jobs[:120] for s in j["skills"]})
    vectors = [{"ref": f"vec:{j['id']}", "entity_id": j["id"],
                "values": [1.0 if s in j["skills"] else 0.0 for s in vocab] + [0.5]} for j in jobs[:120]]
    (tmp_path / "jobs.json").write_text(json.dumps(jobs[:120]), encoding="utf-8")
    (tmp_path / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    (tmp_path / "vectors.json").write_text(json.dumps({"vocabulary": vocab + ["seniority_ordinal"],
                                                       "missing_value": "absent=0.0", "vectors": vectors}),
                                           encoding="utf-8")
    (tmp_path / "pin.json").write_text(json.dumps({"job_id": jobs[0]["id"]}), encoding="utf-8")
    return tmp_path


def test_project_stage_emits_one_contract_point_per_vector(artifacts, contracts):
    jsonschema = pytest.importorskip("jsonschema")
    assert e2e_stage.main(["project", "--vectors", str(artifacts / "vectors.json"), "--out-dir", str(artifacts)]) == 0
    points = json.loads((artifacts / "atlas_points.json").read_text(encoding="utf-8"))
    diag = json.loads((artifacts / "atlas_diagnostics.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(contracts["atlas-point.schema.json"])
    assert len(points) == 120 and all(not list(validator.iter_errors(p)) for p in points)
    assert sorted(p["source_vector_ref"] for p in points) == sorted(diag["fit_refs"])     # input refs preserved
    assert all(r.startswith("vec:") for r in diag["fit_refs"])
    assert diag["preprocessing"] == "center" and diag["method"] == "pca" and diag["k"] == 10
    assert len(diag["explained_variance_ratio"]) == 2
    assert np.mean([p["neighborhood_quality"] for p in points]) == pytest.approx(diag["knn_recall_mean"], abs=1e-5)
    claim = diag["neighbor_claims"][0]
    assert len(claim["neighbors"]) == 5 and claim["entity_id"] not in claim["neighbors"]
    assert "full feature space" in diag["neighbor_claims_space"]
    assert any("seniority_ordinal->seniority-ordinal" in n for n in diag["notes"])
    labels = {p["entity_id"]: p["label"] for p in points}
    assert labels[points[0]["entity_id"]] != points[0]["entity_id"]     # titles joined from jobs.json


def test_scenario_stage_targets_the_pinned_family_and_labels_its_plan(artifacts, contracts, jobs):
    jsonschema = pytest.importorskip("jsonschema")
    assert e2e_stage.main(["scenario", "--artifacts", str(artifacts)]) == 0
    scenarios = json.loads((artifacts / "scenarios.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(contracts["transition-scenario.schema.json"])
    assert len(scenarios) == 1 and not list(validator.iter_errors(scenarios[0]))
    sc = scenarios[0]
    assert sc["outputs"]["target_role_family"] == jobs[0]["role_family"]
    assert sc["candidate_ref"] == "candidate-synthetic-001"
    assert any(a.startswith("DEMONSTRATION PLAN") for a in sc["assumptions"])
    assert 0 < len(sc["planned_actions"]) <= 4
    assert all(a["evidence_goal"].startswith("portfolio-project:") for a in sc["planned_actions"])


@pytest.mark.parametrize("breakage, message", [
    (lambda d: (d / "pin.json").write_text('{"job_id": "nope"}', encoding="utf-8"), "not in jobs.json"),
    (lambda d: (d / "pin.json").unlink(), "missing input file"),
    (lambda d: (d / "claims.json").write_text("[]", encoding="utf-8"), "non-empty list"),
    (lambda d: (d / "jobs.json").write_text("{not json", encoding="utf-8"), "malformed JSON"),
])
def test_scenario_stage_fails_with_exit_code_2(artifacts, capsys, breakage, message):
    breakage(artifacts)
    assert e2e_stage.main(["scenario", "--artifacts", str(artifacts)]) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("payload, message", [
    ('{"vocabulary": ["a", "b"], "vectors": [{"ref": "r", "entity_id": "e", "values": [1, NaN]}]}', "non-finite"),
    ('{"vocabulary": ["a", "b"], "vectors": [{"ref": "r", "entity_id": "e", "values": [1]}]}', "values must be 2"),
    ('{"vocabulary": ["a"], "vectors": [{"ref": "r", "entity_id": "e", "values": [1]}, '
     '{"ref": "s", "entity_id": "e", "values": [0]}]}', "duplicate entity_id"),
    ('{"vocabulary": ["a"], "vectors": "x"}', "must be an object"),
])
def test_project_stage_rejects_bad_vectors(tmp_path, capsys, payload, message):
    (tmp_path / "vectors.json").write_text(payload, encoding="utf-8")
    assert e2e_stage.main(["project", "--vectors", str(tmp_path / "vectors.json"), "--out-dir", str(tmp_path)]) == 2
    assert message in capsys.readouterr().err


def test_manifest_matches_lane_e_shape():
    manifest = json.loads((PKG_ROOT / "integration-manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "0.1" and manifest["session_id"] == "CAREER-TWIN-C"
    assert set(manifest["stages"]) == {"atlas.project", "simulator.scenario"}
    assert all(stage["mock"] is False for stage in manifest["stages"].values())
    assert all(t["network"] is False for t in manifest["tests"])


def test_both_stages_share_one_model_and_write_the_projection_view(artifacts, contracts):
    """Lane D finding on #5 (C at 85c0536): the stages fitted two different models in one run."""
    jsonschema = pytest.importorskip("jsonschema")
    assert e2e_stage.main(["project", "--vectors", str(artifacts / "vectors.json"), "--out-dir", str(artifacts)]) == 0
    points = json.loads((artifacts / "atlas_points.json").read_text(encoding="utf-8"))
    first_view = json.loads((artifacts / "atlas.pca.json").read_text(encoding="utf-8"))
    assert e2e_stage.main(["scenario", "--artifacts", str(artifacts)]) == 0
    sc = json.loads((artifacts / "scenarios.json").read_text(encoding="utf-8"))[0]
    view = json.loads((artifacts / "atlas.pca.json").read_text(encoding="utf-8"))
    pid = points[0]["projection_id"]
    assert sc["outputs"]["model_ref"]["projection_id"] == pid == view["projection_id"] == first_view["projection_id"]
    assert {p["projection_id"] for p in sc["outputs"]["map_points"]} == {pid}
    kinds = {p["representation_type"] for p in view["points"]}
    assert {"job", "role-centroid", "candidate", "scenario"} <= kinds
    assert len(view["paths"]) == 1 and view["paths"][0]["scenario_id"] == sc["id"]
    assert any("Fitted on vectors.json (same model as atlas.project)" in a for a in sc["assumptions"])
    schema = json.loads((PKG_ROOT / "schemas" / "atlas-projection.schema.json").read_text(encoding="utf-8"))
    assert not list(jsonschema.Draft202012Validator(schema).iter_errors(view))
    assert view["generated_at"] != "1970-01-01T00:00:00Z"               # taken from the inputs' capture times
    assert all(p["representation_type"] == "job" for p in points)        # E's file stays postings-only

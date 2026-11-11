"""Adapters for lane A's published outputs: frozen JobPosting envelopes and a-api/0.1 skill vectors.

The payloads below are built in-process in the SHAPE lane A publishes (examples/skill_vectors.json and
examples/job_record.json at A's commit 3dd9b10). They are not copies of lane A's data. The run against
lane A's real output at a pinned SHA is recorded in HANDOFF.md, not here, because this suite must pass on
lane C's branch alone.
"""
import copy

import numpy as np
import pytest

from careertwin_atlas.adapters import (A_API_VERSION, engine_from_skill_vectors, unwrap_job_records,
                                       vocabulary_from_names)
from careertwin_atlas.api import build_atlas_bundle
from careertwin_atlas.engine import AtlasEngine
from careertwin_atlas.errors import DegenerateInputError, FeatureSpaceMismatchError, InvalidInputError
from careertwin_atlas.features import STATE_NOT_STATED, STATE_STATED, STATE_UNKNOWN, job_to_vector
from careertwin_atlas.gaps import analyze_gap
from careertwin_atlas.simulator import simulate_transition

A_VOCAB = ["ai-strategy", "api-design", "customer-delivery", "enterprise-sales", "executive-communication",
           "kubernetes", "llm-applications", "python", "spark", "sql", "systems-integration", "terraform"]
A_FAMILIES = {
    "fde": ("python", "api-design", "customer-delivery", "llm-applications", "systems-integration"),
    "field-cto": ("executive-communication", "enterprise-sales", "ai-strategy", "customer-delivery"),
    "data-engineer": ("sql", "spark", "python", "terraform"),
}


def _payload(n=90, seed=0):
    rng = np.random.default_rng(seed)
    fams = sorted(A_FAMILIES)
    jobs = []
    for i in range(n):
        fam = fams[i % len(fams)]
        values = []
        for name in A_VOCAB:
            if name in A_FAMILIES[fam]:
                values.append(1.0 if rng.random() < 0.8 else 0.5)
            else:
                values.append(0.5 if rng.random() < 0.05 else 0.0)
        jobs.append({"dimensions": len(A_VOCAB), "encoding": "1=required,0.5=preferred,0=not-listed",
                     "entity_id": f"jp_fake{i:04d}", "label": f"Fictional {fam} role {i}", "observed": True,
                     "representation_type": "job", "role_family": fam, "status": "active",
                     "source_vector_ref": f"a-skillvec/tax-test/jp_fake{i:04d}", "values": values,
                     "vocabulary_version": "tax-test"})
    cand = [0.0] * len(A_VOCAB)
    cand[A_VOCAB.index("python")] = 1.0
    cand[A_VOCAB.index("api-design")] = 1.0
    cand[A_VOCAB.index("sql")] = 0.5
    cand[A_VOCAB.index("kubernetes")] = 0.25
    return {"api_version": A_API_VERSION, "as_of": "2026-11-10T00:00:00Z", "vocabulary": list(A_VOCAB),
            "vocabulary_version": "tax-test", "jobs": jobs,
            "candidate": {"entity_id": "candidate-synthetic-001", "observed": True, "values": cand,
                          "representation_type": "candidate",
                          "source_vector_ref": "a-skillvec/tax-test/candidate-synthetic-001"}}


def test_skill_vectors_fit_in_lane_a_feature_space_and_keep_its_references(contracts):
    jsonschema = pytest.importorskip("jsonschema")
    res = engine_from_skill_vectors(_payload(), n_boot=10)
    engine, cand = res["engine"], res["candidate"]
    assert engine.matrix.feature_names == tuple(A_VOCAB) and len(engine.matrix) == 90
    assert sorted(engine.profiles) == sorted(A_FAMILIES)
    assert engine.vocab.version == "upstream:tax-test"
    bundle = build_atlas_bundle(engine, cand, cluster_boot=3)
    validator = jsonschema.Draft202012Validator(contracts["atlas-point.schema.json"])
    assert all(not list(validator.iter_errors(p)) for p in bundle["points"])
    refs = {p["entity_id"]: p["source_vector_ref"] for p in bundle["points"]}
    assert refs["jp_fake0000"] == "a-skillvec/tax-test/jp_fake0000"          # upstream provenance preserved
    assert refs["candidate-synthetic-001"] == "a-skillvec/tax-test/candidate-synthetic-001"
    assert bundle["candidate"]["role_table"][0]["role"] in ("data-engineer", "fde")
    assert any("no 'contradicted' state" in n for n in res["notes"])
    assert any("spark" in n and "systems-integration" in n for n in res["notes"])   # unknown upstream features listed


def test_downstream_analysis_runs_on_lane_a_vectors(contracts):
    jsonschema = pytest.importorskip("jsonschema")
    res = engine_from_skill_vectors(_payload(), n_boot=10)
    engine, cand = res["engine"], res["candidate"]
    gap = analyze_gap(engine, cand, "field-cto")
    assert "enterprise-sales" in gap["skill_gaps"] and gap["unknown_candidate_inputs"] == []
    by = {r["feature"]: r for r in analyze_gap(engine, cand, "data-engineer")["requirements"]}
    assert by["python"]["candidate_state"] == "evidenced" and by["sql"]["candidate_state"] == "weak-evidence"
    assert by["spark"]["acquisition"] == "study-or-project"                  # external feature: default placeholder
    sc = simulate_transition(engine, cand, "field-cto",
                             [{"skill": "ai-strategy", "effort_hours": 100, "evidence_goal": "portfolio-project"}])
    assert not list(jsonschema.Draft202012Validator(contracts["transition-scenario.schema.json"]).iter_errors(sc))
    assert sc["outputs"]["delta"]["requirement_shortfall"] < 0
    assert any("seniority is unknown" in c for a in sc["outputs"]["alternatives"]["paths"] for c in a["caveats"])


def test_unobserved_rows_become_unknown_not_zero():
    payload = _payload()
    payload["jobs"][0]["values"] = [0.0] * len(A_VOCAB)
    payload["jobs"][0]["observed"] = False
    engine = engine_from_skill_vectors(payload, n_boot=5)["engine"]
    first = engine.matrix.vectors[0]
    assert np.all(first.states == STATE_UNKNOWN) and np.all(np.isnan(first.values))
    assert engine.n_imputed[0] == len(A_VOCAB)
    np.testing.assert_allclose(engine.coords[0], 0.0, atol=1e-9)             # sits at the mean, flagged as imputed
    second = engine.matrix.vectors[1]
    assert set(second.states.tolist()) <= {STATE_STATED, STATE_NOT_STATED}
    lying = _payload()
    lying["jobs"][0]["observed"] = False                                     # claims unobserved but carries values
    engine2 = engine_from_skill_vectors(lying, n_boot=5)["engine"]
    assert len(engine2.skipped) == 1 and "observed is false but non-zero" in engine2.skipped[0]["reason"]


def test_placeholder_families_are_unlabelled_on_both_routes(jobs, vocab):
    payload = _payload()
    for j in payload["jobs"][:10]:
        j["role_family"] = "unknown"
    engine = engine_from_skill_vectors(payload, n_boot=5)["engine"]
    assert "unknown" not in engine.profiles
    assert any("no role_family" in w for w in engine.warnings)
    v = job_to_vector({**jobs[0], "role_family": "Unknown"}, vocab)
    assert v.meta["role_family"] is None


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(api_version="a-api/9.9"), "unsupported api_version"),
    (lambda p: p.pop("api_version"), "unsupported api_version"),
    (lambda p: p.update(vocabulary=[]), "non-empty list"),
    (lambda p: p.update(vocabulary=["python", "python"]), "duplicated"),
    (lambda p: p.update(vocabulary=["Python!"]), "not a normalised feature id"),
    (lambda p: p.update(vocabulary_version=""), "vocabulary_version"),
    (lambda p: p.update(jobs="nope"), "payload.jobs must be a list"),
    (lambda p: p["candidate"].update(values=[0.0]), "candidate: values must be a list"),
    (lambda p: p["candidate"].update(observed="yes"), "observed must be true or false"),
])
def test_malformed_payloads_fail_loudly(mutate, message):
    payload = _payload()
    mutate(payload)
    with pytest.raises(InvalidInputError, match=message):
        engine_from_skill_vectors(payload, n_boot=2)
    with pytest.raises(InvalidInputError):
        engine_from_skill_vectors(["not", "an", "object"])


def test_bad_rows_are_skipped_with_reasons_not_silently_dropped():
    payload = _payload()
    good = copy.deepcopy(payload["jobs"][0])
    payload["jobs"][1]["values"] = payload["jobs"][1]["values"][:-1]            # wrong length
    payload["jobs"][2]["values"][0] = 7.0                                       # out of range
    payload["jobs"][3]["values"][0] = float("nan")
    payload["jobs"][4]["entity_id"] = ""
    payload["jobs"][5]["vocabulary_version"] = "tax-other"
    payload["jobs"][6]["values"][0] = True
    payload["jobs"].append(good)                                               # duplicate id
    payload["jobs"].append("garbage")
    engine = engine_from_skill_vectors(payload, n_boot=2)["engine"]
    assert len(engine.matrix) == 90 - 6 and len(engine.skipped) == 8
    reasons = " | ".join(s["reason"] for s in engine.skipped)
    for fragment in ("values must be a list of 12", "within [0, 1]", "entity_id", "vocabulary_version differs",
                     "values must be numbers", "duplicate id", "row must be an object"):
        assert fragment in reasons
    with pytest.raises(DegenerateInputError):
        engine_from_skill_vectors({**_payload(), "jobs": payload["jobs"][:1]}, n_boot=2)


def test_vectors_and_vocabulary_must_agree(vocab, jobs):
    upstream, _ = vocabulary_from_names(A_VOCAB, "tax-test")
    own = [job_to_vector(j, vocab) for j in jobs[:5]]
    with pytest.raises(FeatureSpaceMismatchError):
        AtlasEngine.from_vectors(own, upstream)
    assert upstream.get("terraform").group == "cloud-platform"                 # inherited through C's alias
    assert upstream.get("spark").group == "external"
    assert upstream.get("enterprise-sales").acquisition == "role-exposure"


def test_job_record_envelopes_are_unwrapped_for_the_frozen_contract_route(jobs, vocab):
    wrapped = [{"api_version": A_API_VERSION, "posting": j, "ext": {"synthetic": True}} for j in jobs[:40]]
    mixed = wrapped[:20] + list(jobs[20:40]) + ["garbage", {"posting": "not-a-mapping"}]
    engine = AtlasEngine.fit(unwrap_job_records(mixed), vocab, n_boot=2)
    assert len(engine.matrix) == 40 and len(engine.skipped) == 2
    direct = AtlasEngine.fit(jobs[:40], vocab, n_boot=2)
    np.testing.assert_allclose(engine.coords, direct.coords, atol=1e-12)       # envelope adds nothing, loses nothing
    with pytest.raises(InvalidInputError):
        unwrap_job_records("jobs")


def test_lane_a_skill_ids_map_through_lane_c_aliases(vocab):
    v = job_to_vector({"id": "x", "skills": ["llm-applications", "solution-demos", "compliance",
                                             "systems-integration"], "seniority": "senior"}, vocab)
    assert {"llm-integration", "pre-sales-demos", "regulatory-compliance"} <= set(v.stated_features())
    assert v.unmapped_terms == ("systems-integration",)                        # preserved, reported, not guessed
    assert any(w.startswith("lossy-alias-used") for w in v.warnings)

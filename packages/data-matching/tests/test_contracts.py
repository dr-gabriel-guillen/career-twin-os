"""Frozen-contract conformance: what lane A emits must validate against contracts/ on main."""
import json

import pytest

from a_testkit import AS_OF, ROOT
from ct_matching import schema

CONTRACTS = ROOT / "contracts"


@pytest.mark.parametrize("name", ["job-posting", "profile-claim"])
def test_vendored_contract_is_identical_to_frozen_contract(name):
    def lf(path):  # tolerate git autocrlf checkouts; everything else must match byte for byte
        return path.read_bytes().replace(b"\r\n", b"\n")
    assert lf(schema.SCHEMA_DIR / f"{name}.schema.json") == lf(CONTRACTS / f"{name}.schema.json")


def test_every_emitted_posting_validates_against_frozen_contract(service):
    contract = json.loads((CONTRACTS / "job-posting.schema.json").read_text(encoding="utf-8"))
    jobs = service.store.view(AS_OF)
    assert len(jobs) >= 150
    for env in jobs:
        assert schema.errors(env["posting"], contract) == [], env["posting"]["id"]
        schema.check(env, "a-job-record")


def test_every_stored_version_validates_not_just_the_latest(service):
    for chain in service.store.versions.values():
        for env in chain:
            schema.check(env["posting"], "job-posting")


def test_emitted_claims_validate_against_frozen_contract(service):
    contract = json.loads((CONTRACTS / "profile-claim.schema.json").read_text(encoding="utf-8"))
    exported = service.vault.export("internal-matching", AS_OF)["claims"]
    assert exported
    for claim in exported:
        assert schema.errors(claim, contract) == []


def test_mini_validator_agrees_with_jsonschema_library(service):
    jsonschema = pytest.importorskip("jsonschema")
    contract = json.loads((CONTRACTS / "job-posting.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(contract, format_checker=jsonschema.FormatChecker())
    good = service.store.view(AS_OF)[0]["posting"]
    mutations = [
        lambda p: p.pop("url"), lambda p: p.update(status="open"), lambda p: p.update(extra=1),
        lambda p: p.update(skills=["a", "a"]), lambda p: p.update(skills="python"), lambda p: p.update(title=""),
        lambda p: p.update(compensation={"currency": "USD"}), lambda p: p.update(source="linkedin"),
        lambda p: p.update(location=5), lambda p: p.update(remote_policy="anywhere"),
        lambda p: p.update(compensation={"currency": "USD", "min": "1", "max": 2, "basis": "base", "verified": False}),
        lambda p: p.update(compensation={"currency": "USD", "min": 1, "max": 2, "basis": "base", "verified": 0}),
        lambda p: p.update(requirements=[1]), lambda p: p.update(id=None),
    ]
    assert validator.is_valid(good) and not schema.errors(good, contract)
    for mutate in mutations:
        bad = json.loads(json.dumps(good))
        mutate(bad)
        assert not validator.is_valid(bad)
        assert schema.errors(bad, contract), "mini validator accepted what jsonschema rejects"


def test_mini_validator_checks_date_time_format():
    contract = schema.load("job-posting")
    base = {"id": "x", "source": "synthetic", "source_id": "1", "title": "t", "organization": "o", "url": "u",
            "fetched_at": "2026-11-08T00:00:00Z", "status": "active", "skills": [], "seniority": "s", "employment_type": "e"}
    assert schema.errors(base, contract) == []
    for bad in ("2026-11-08", "yesterday", "2026-11-08 00:00:00Z", "2026-13-01T00:00:00Z", "2026-11-08T00:00:00"):
        assert schema.errors(dict(base, fetched_at=bad), contract)


def test_mini_validator_refuses_unknown_keywords_instead_of_passing_silently():
    with pytest.raises(NotImplementedError):
        schema.errors({}, {"type": "object", "oneOf": []})


def test_baseline_finding_main_fixture_does_not_satisfy_frozen_contract():
    """Observed defect on main (reported in Issue #2 STARTED): shared fixture predates the contract."""
    contract = json.loads((CONTRACTS / "job-posting.schema.json").read_text(encoding="utf-8"))
    jobs = json.loads((ROOT / "fixtures" / "synthetic_jobs.json").read_text(encoding="utf-8"))
    assert all(schema.errors(job, contract) for job in jobs)


def test_match_result_validates_against_lane_a_schema(service):
    result = service.rank({"as_of": AS_OF, "top_n": 15})
    schema.check(result, "a-match-result")
    broken = json.loads(json.dumps(result))
    broken["score_type"] = "hiring_probability"
    assert not schema.is_valid(broken, "a-match-result")

"""WS2 — Draft 2020-12 conformance for all six frozen contracts."""

from __future__ import annotations

import json
import math

import pytest

from ctint import contracts
from ctint.cases import Case, all_cases, apply_patch, load_cases
from ctint.timeutil import is_rfc3339, is_utc

CASES = all_cases()
VALID = [c for c in CASES if c.layer == "valid"]
SCHEMA_NEG = [c for c in CASES if c.layer == "schema"]
SEMANTIC_NEG = [c for c in CASES if c.layer == "semantic"]


def test_fixture_inventory_is_substantial() -> None:
    """Guard against an accidentally empty fixture set making everything 'pass'."""
    for name in contracts.CONTRACTS:
        cases = load_cases(name)
        assert sum(c.layer == "valid" for c in cases) >= 5, name
        assert sum(c.layer == "schema" for c in cases) >= 12, name
        assert sum(c.layer == "semantic" for c in cases) >= 5, name


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_schema_is_draft_2020_12_and_self_consistent(name: str) -> None:
    schema = contracts.load_schema(name)
    assert schema["$schema"] == contracts.DRAFT_2020_12
    assert schema["title"] == contracts.CONTRACTS[name][1]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])
    contracts.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("case", VALID, ids=lambda c: c.id)
def test_valid_fixture_passes_schema_and_semantics(case: Case) -> None:
    assert contracts.schema_errors(case.contract, case.doc) == []
    assert [str(v) for v in contracts.semantic_violations(case.contract, case.doc)] == []


@pytest.mark.parametrize("case", SCHEMA_NEG, ids=lambda c: c.id)
def test_negative_fixture_is_rejected_by_schema(case: Case) -> None:
    assert contracts.schema_errors(case.contract, case.doc), f"schema accepted {case.id}"


@pytest.mark.parametrize("case", SEMANTIC_NEG, ids=lambda c: c.id)
def test_schema_gap_is_caught_by_semantic_layer(case: Case) -> None:
    """These documents PASS the frozen schema (a recorded gap) but break a stated invariant."""
    assert contracts.schema_errors(case.contract, case.doc) == [], "no longer a gap: move to layer=schema"
    rules = {v.rule for v in contracts.semantic_violations(case.contract, case.doc)}
    assert case.rule in rules, f"{case.id}: expected {case.rule}, got {sorted(rules)}"


def test_every_semantic_rule_has_a_negative_fixture() -> None:
    # NaN/Infinity cannot be written in a strict-JSON fixture; ATLAS-NUM-001 has its own test below.
    exercised = {c.rule for c in SEMANTIC_NEG} | {"ATLAS-NUM-001"}
    assert exercised == set(contracts.RULES), sorted(set(contracts.RULES) ^ exercised)


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_every_enum_value_is_accepted_and_neighbours_rejected(name: str) -> None:
    schema = contracts.load_schema(name)
    base = next(c.doc for c in load_cases(name) if c.layer == "valid" and c.name.startswith("full"))
    for prop, spec in schema["properties"].items():
        if "enum" not in spec:
            continue
        for value in spec["enum"]:
            doc = dict(base, **{prop: value})
            assert contracts.schema_errors(name, doc) == [], (prop, value)
        for bad in (spec["enum"][0].upper(), spec["enum"][0] + " ", "", None, 0):
            doc = dict(base, **{prop: bad})
            assert contracts.schema_errors(name, doc), (prop, bad)


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_each_required_property_is_individually_enforced(name: str) -> None:
    schema = contracts.load_schema(name)
    base = next(c.doc for c in load_cases(name) if c.layer == "valid")
    for prop in schema["required"]:
        doc = {k: v for k, v in base.items() if k != prop}
        errors = contracts.schema_errors(name, doc)
        assert any(repr(prop) in e for e in errors), (prop, errors)


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_date_time_fields_reject_malformed_and_flag_non_utc(name: str) -> None:
    fields = contracts.date_time_fields(name)
    base = next(c.doc for c in load_cases(name) if c.layer == "valid" and c.name.startswith("full"))
    malformed = [
        "2026-01-05",
        "2026-01-05T09:00:00",
        "2026-02-30T09:00:00Z",
        "2026-01-05T25:00:00Z",
        "now",
        "",
    ]
    for field in fields:
        for bad in malformed:
            assert contracts.schema_errors(name, dict(base, **{field: bad})), (field, bad)
        offset_doc = dict(base, **{field: "2026-01-05T09:00:00+05:30"})
        assert contracts.schema_errors(name, offset_doc) == [], "offsets are legal RFC 3339"
        assert "UTC-001" in {v.rule for v in contracts.semantic_violations(name, offset_doc)}, field
    if name in ("job-posting", "profile-claim", "application-event", "experiment"):
        assert fields, f"{name} is expected to carry timestamps"


@pytest.mark.parametrize(
    ("value", "rfc", "utc"),
    [
        ("2026-01-05T09:00:00Z", True, True),
        ("2026-01-05t09:00:00z", True, True),
        ("2026-01-05T09:00:00.5Z", True, True),
        ("2026-01-05T09:00:00+00:00", True, True),
        ("2026-01-05T09:00:00-00:00", True, False),
        ("2026-01-05T09:00:00+02:00", True, False),
        ("2026-01-05T09:00:60Z", False, False),
        ("2026-01-05T09:00:00+24:00", False, False),
        ("2026-01-05T09:00Z", False, False),
        ("2026-1-5T09:00:00Z", False, False),
        ("2026-01-05T09:00:00Z\n", False, False),
        (" 2026-01-05T09:00:00Z", False, False),
        (None, False, False),
        (20260105, False, False),
    ],
)
def test_rfc3339_and_utc_oracle(value: object, rfc: bool, utc: bool) -> None:
    assert is_rfc3339(value) is rfc
    assert is_utc(value) is utc


def test_python_json_nan_is_caught_for_atlas_coords() -> None:
    """``json.loads`` accepts NaN/Infinity; the schema's 'number' does too."""
    point = json.loads(
        '{"entity_id":"e","projection_id":"p","label":"l","coords":[NaN, Infinity],'
        '"source_vector_ref":"v","representation_type":"job","projection_method":"pca",'
        '"neighborhood_quality":0.5}'
    )
    assert math.isnan(point["coords"][0])
    assert contracts.schema_errors("atlas-point", point) == [], "schema gap: non-finite numbers accepted"
    assert "ATLAS-NUM-001" in {v.rule for v in contracts.semantic_violations("atlas-point", point)}


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("relative_heuristic_not_hiring_probability", False),
        ("hypothetical_gap_not_probability", False),
        ("This score is not a probability of being hired.", False),
        ("Scores are never hiring probabilities.", False),
        # regression: lane A's disclaimer @ 2d933fb was flagged by the first version of the lint
        (
            "Score is a relative ordering heuristic; it is NOT an offer, interview or hiring probability.",
            False,
        ),
        ("relative_heuristic_not_offer_probability", False),
        ("Not bad: the offer probability is 0.8", True),
        ("We do not estimate the likelihood of an offer.", False),
        ("skill coverage 0.6", False),
        ("probability_of_precipitation", False),
        ("hiring_probability", True),
        ("offer_probability", True),
        ("p_hire", True),
        ("interviewLikelihood", True),
        ("Your chances of getting an interview are 80%", True),
        ("odds of success: 3 to 1", True),
        ("Hire-probability 0.9", True),
    ],
)
def test_probability_claim_lint(text: str, claim: bool) -> None:
    assert contracts.is_probability_claim(text) is claim


def test_semantic_layer_never_raises_on_arbitrary_input() -> None:
    for name in contracts.CONTRACTS:
        for junk in (None, 1, "x", [], [1, {"a": 2}], {"coords": "x", "compensation": 5, "variants": 3}):
            contracts.semantic_violations(name, junk)
            contracts.schema_errors(name, junk)


def test_check_returns_only_blocking_problems() -> None:
    advisory = next(c for c in SEMANTIC_NEG if c.rule == "JOB-URL-002")
    assert contracts.check(advisory.contract, advisory.doc) == []
    blocking = next(c for c in SEMANTIC_NEG if c.rule == "APP-APPROVAL-001")
    assert any("APP-APPROVAL-001" in e for e in contracts.check(blocking.contract, blocking.doc))


def test_apply_patch_does_not_mutate_and_validates_targets() -> None:
    base = {"a": {"b": [1, 2]}}
    patched = apply_patch(
        base, [{"op": "add", "path": "/a/b/-", "value": 3}, {"op": "remove", "path": "/a/b/0"}]
    )
    assert base == {"a": {"b": [1, 2]}}
    assert patched == {"a": {"b": [2, 3]}}
    with pytest.raises(KeyError):
        apply_patch(base, [{"op": "replace", "path": "/missing", "value": 1}])
    with pytest.raises(ValueError):
        apply_patch(base, [{"op": "move", "path": "/a", "value": 1}])

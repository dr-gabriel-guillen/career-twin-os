"""WS8 — attacks on experiment integrity: post-assignment design changes, selection,
multiple exposure, late outcomes and false certainty."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from typing import Any

import pytest

from ctint import experiment_integrity as ei
from ctint import stats, synth
from ctint.baseline import load_reference_engine

# ----------------------------------------------------------------------------- statistics


@pytest.mark.parametrize(
    ("statistic", "dof", "expected"),
    [
        (3.841459, 1, 0.05),
        (5.991465, 2, 0.05),
        (23.20925, 10, 0.01),
        (6.634897, 1, 0.01),
        (0.0, 3, 1.0),
        (124.342, 100, 0.05),
    ],
)
def test_chi_square_survival_matches_reference_tables(statistic: float, dof: int, expected: float) -> None:
    assert stats.chi2_sf(statistic, dof) == pytest.approx(expected, rel=2e-4)


def test_chi_square_tail_is_monotone_and_bounded() -> None:
    values = [stats.chi2_sf(x / 4, 3) for x in range(400)]
    assert all(0.0 <= v <= 1.0 for v in values)
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))
    assert stats.chi2_sf(1000.0, 2) < 1e-200


def test_intervals_match_published_examples() -> None:
    low, high = stats.wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-3)
    low, high = stats.wilson_interval(81, 263)
    assert (low, high) == (pytest.approx(0.2553, abs=1e-3), pytest.approx(0.3662, abs=1e-3))
    # Newcombe (1998), method 10: 56/70 vs 48/80 -> +0.2000 (0.0524, 0.3339)
    estimate, low, high = stats.newcombe_diff_interval(48, 80, 56, 70)
    assert estimate == pytest.approx(0.2)
    assert (low, high) == (pytest.approx(0.0524, abs=1e-3), pytest.approx(0.3339, abs=1e-3))
    assert stats.newcombe_diff_interval(0, 0, 5, 10) == (0.0, -1.0, 1.0)
    with pytest.raises(ValueError):
        stats.wilson_interval(11, 10)


def test_independence_test_detects_association() -> None:
    assert stats.chi_square_independence([[50, 50], [50, 50]])[2] == pytest.approx(1.0)
    assert stats.chi_square_independence([[90, 10], [10, 90]])[2] < 1e-20
    assert stats.chi_square_uniform([500, 500])[2] == pytest.approx(1.0)
    assert stats.chi_square_uniform([600, 400])[2] < 1e-9


# ----------------------------------------------------------------------------- assignment probes


def _rendezvous(exp: str, stratum: str, unit: str, variants: list[str], salt: str) -> str:
    """A well-behaved assigner: validated inputs, injective key, order-insensitive, stable under added arms."""
    if not all(isinstance(x, str) and x.strip() for x in (exp, stratum, unit, salt, *variants)):
        raise ValueError("blank input")
    if len(set(variants)) < 2 or len(set(variants)) != len(variants):
        raise ValueError("need >= 2 unique variants")

    def weight(variant: str) -> bytes:
        return hashlib.sha256(json.dumps([salt, exp, stratum, unit, variant]).encode()).digest()

    return max(variants, key=weight)


def test_well_behaved_assigner_passes_every_probe() -> None:
    assert [str(f) for f in ei.probe_assignment_function(_rendezvous)] == []


REFERENCE_ENGINE_FINDINGS = {
    "ASSIGN-VARIANT-ORDER-SENSITIVE",
    "ASSIGN-RESHUFFLE-ON-DESIGN-CHANGE",
    "ASSIGN-KEY-COLLISION",
    "ASSIGN-ACCEPTS-BLANK-STRATUM",
    "ASSIGN-ACCEPTS-BLANK-UNIT",
    "ASSIGN-ACCEPTS-BLANK-SALT",
    "ASSIGN-ACCEPTS-BLANK-EXPERIMENT",
    "ASSIGN-ACCEPTS-BLANK-VARIANT",
}


def test_reference_engine_assignment_characterisation() -> None:
    """Exact set of integrity findings for ``tools/reference_engine.assign_variant`` on main.

    This is a characterisation: if main fixes one, this test fails and the ledger
    (known_findings.json) must be updated — a finding can never silently disappear.
    """
    engine = load_reference_engine()
    findings = ei.probe_assignment_function(engine.assign_variant)
    assert ei.codes(findings) == REFERENCE_ENGINE_FINDINGS, [str(f) for f in findings]
    collisions = [f for f in findings if f.code == "ASSIGN-KEY-COLLISION"]
    assert len(collisions) == 1 and "U+241F" in collisions[0].detail
    assert not any(
        f.code in ("ASSIGN-IMBALANCE", "ASSIGN-NONDETERMINISTIC", "ASSIGN-SALT-IGNORED") for f in findings
    )


def test_probes_catch_broken_assigners() -> None:
    def always_first(exp: str, stratum: str, unit: str, variants: list[str], salt: str) -> str:
        return variants[0]

    def ignores_experiment(exp: str, stratum: str, unit: str, variants: list[str], salt: str) -> str:
        return variants[hashlib.sha256(f"{salt}|{stratum}|{unit}".encode()).digest()[0] % len(variants)]

    def by_unit_parity(exp: str, stratum: str, unit: str, variants: list[str], salt: str) -> str:
        return variants[int(unit[-1]) % len(variants)] if unit.strip() and unit[-1].isdigit() else variants[0]

    rng = random.Random(1)

    def coin(exp: str, stratum: str, unit: str, variants: list[str], salt: str) -> str:
        return rng.choice(variants)

    assert {"ASSIGN-IMBALANCE", "ASSIGN-SALT-IGNORED", "ASSIGN-STRATUM-IGNORED"} <= ei.codes(
        ei.probe_assignment_function(always_first, 800)
    )
    assert "ASSIGN-CORRELATED-ACROSS-EXPERIMENTS" in ei.codes(
        ei.probe_assignment_function(ignores_experiment, 800)
    )
    assert {"ASSIGN-SALT-IGNORED", "ASSIGN-CORRELATED-ACROSS-EXPERIMENTS"} <= ei.codes(
        ei.probe_assignment_function(by_unit_parity, 800)
    )
    assert ei.codes(ei.probe_assignment_function(coin, 200)) == {"ASSIGN-NONDETERMINISTIC"}


# ----------------------------------------------------------------------------- bundle checks

World = tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]


@pytest.fixture
def world() -> World:
    return copy.deepcopy(ei.synth_experiment_world(seed=1, n_units=160))


def test_honest_world_has_no_findings(world: World) -> None:
    spec, assignments, events, analysis = world
    assert [
        str(f) for f in ei.check_experiment_bundle(spec, assignments, events, analysis, strict_digest=True)
    ] == []
    assert analysis["estimand"] == "ITT" and analysis["decision"] in ei.DECISIONS
    assert sum(arm["n_assigned"] for arm in analysis["arms"]) == 160


def test_design_changed_after_first_assignment_is_detected(world: World) -> None:
    spec, assignments, events, analysis = world
    for edit in ({"variants": ["B", "A"]}, {"primary_metric": "offer_within_window"}, {"outcome_window_days": 90},
                 {"stopping_rule": "Stop when B looks better."}, {"strata": []}):  # fmt: skip
        changed = dict(spec, **edit)
        strict = ei.codes(
            ei.check_experiment_bundle(changed, assignments, events, analysis, strict_digest=True)
        )
        assert "EXP-DESIGN-CHANGED-AFTER-ASSIGNMENT" in strict, edit
        lenient = ei.codes(ei.check_experiment_bundle(changed, assignments, events, analysis))
        assert "EXP-DIGEST-UNRATIFIED-MISMATCH" in lenient, edit
    # cosmetic, non-design fields do not change the digest
    assert ei.design_digest(dict(spec, title="Renamed", status="paused")) == ei.design_digest(spec)
    assignments[5]["spec_digest"] = "sha256:" + "f" * 64
    assert "EXP-DESIGN-CHANGED-AFTER-ASSIGNMENT" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, analysis)
    )


def test_double_enrolment_multiple_exposure_and_crossover(world: World) -> None:
    spec, assignments, events, analysis = world
    dup = copy.deepcopy(assignments)
    dup.append(dict(dup[0], id="asg-dup", variant="B" if dup[0]["variant"] == "A" else "A"))
    found = ei.codes(ei.check_experiment_bundle(spec, dup, events, analysis))
    assert {"EXP-DOUBLE-ENROLLMENT", "EXP-MULTIPLE-EXPOSURE"} <= found

    a_idx = next(i for i, a in enumerate(assignments) if a["variant"] == "A")
    b_idx = next(i for i, a in enumerate(assignments) if a["variant"] == "B")
    crossed = copy.deepcopy(events)
    shared_job = next(e["job_id"] for e in crossed if e["application_id"] == assignments[a_idx]["unit_id"])
    for event in crossed:
        if event["application_id"] == assignments[b_idx]["unit_id"]:
            event["job_id"] = shared_job
    assert "EXP-CROSSOVER" in ei.codes(ei.check_experiment_bundle(spec, assignments, crossed, analysis))


def test_assignment_timing_attacks(world: World) -> None:
    spec, assignments, events, analysis = world
    early = copy.deepcopy(assignments)
    early[0]["assigned_at"] = synth.ts(-60)
    assert "EXP-ASSIGNED-BEFORE-LOCK" in ei.codes(ei.check_experiment_bundle(spec, early, events, analysis))

    late = copy.deepcopy(assignments)
    late[3]["assigned_at"] = synth.ts(10_000_000)  # journaled long after the application was submitted
    assert "EXP-EXPOSURE-BEFORE-ASSIGNMENT" in ei.codes(
        ei.check_experiment_bundle(spec, late, events, analysis)
    )

    missing = copy.deepcopy(assignments)
    del missing[7]
    found = ei.codes(ei.check_experiment_bundle(spec, missing, events, analysis))
    assert {"EXP-EXPOSURE-WITHOUT-ASSIGNMENT", "EXP-ANALYSIS-COUNT-MISMATCH"} <= found

    assert "EXP-ASSIGNED-WHILE-DRAFT" in ei.codes(
        ei.check_experiment_bundle(dict(spec, status="draft", design_locked_at=None), assignments, events)
    )
    bad_variant = copy.deepcopy(assignments)
    bad_variant[0]["variant"] = "C"
    assert "EXP-UNKNOWN-VARIANT" in ei.codes(ei.check_experiment_bundle(spec, bad_variant, events))


def test_selection_after_assignment_shows_up_as_sample_ratio_mismatch(world: World) -> None:
    spec, assignments, events, _ = world
    rng = random.Random(3)
    kept = [a for a in assignments if a["variant"] == "A" or rng.random() < 0.45]
    kept_ids = {a["unit_id"] for a in kept}
    kept_events = [e for e in events if e["application_id"] in kept_ids]
    cherry_picked = ei.honest_analysis(spec, kept, kept_events)
    assert "EXP-SAMPLE-RATIO-MISMATCH" in ei.codes(
        ei.check_experiment_bundle(spec, kept, kept_events, cherry_picked)
    )
    # Same selection hidden only in the analysis (journal intact) is a count mismatch instead.
    assert "EXP-ANALYSIS-COUNT-MISMATCH" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, cherry_picked)
    )


def test_late_unknown_and_imported_outcomes_must_not_be_counted(world: World) -> None:
    spec, assignments, events, analysis = world
    target = next(
        a
        for a in assignments
        if a["unit_id"] not in {e["application_id"] for e in events if e["event_type"] == "recruiter_screen"}
    )

    def with_extra(event_patch: dict[str, Any]) -> set[str]:
        extra = dict(
            next(e for e in events if e["application_id"] == target["unit_id"] and e["event_type"] == "submitted"),
            id="evt-extra", event_type="recruiter_screen", approval_token_ref=None, **event_patch,
        )  # fmt: skip
        tampered = dict(analysis, counted_event_ids=[*analysis["counted_event_ids"], "evt-extra"])
        return ei.codes(ei.check_experiment_bundle(spec, assignments, [*events, extra], tampered))

    late = synth.ts(200 + 160 * 50 + 60 * 24 * 45)
    assert "EXP-LATE-OUTCOME-COUNTED" in with_extra({"recorded_at": late, "occurred_at": late})
    assert "EXP-UNKNOWN-OUTCOME-COUNTED" in with_extra({"outcome_status": "unknown"})
    # A reply ingested by an importer on a randomised application is a normal outcome (cycle-2 correction,
    # lane B's synthetic mailbox @ 84757ed) ...
    assert "EXP-OBSERVATIONAL-AS-RANDOMIZED" not in with_extra(
        {"actor": "import", "outcome_status": "observed"}
    )
    # ... but an outcome on an application whose submission was imported history is observational.
    historical = [
        dict(e, actor="import")
        if e["application_id"] == target["unit_id"] and e["event_type"] == "submitted"
        else e
        for e in events
    ]
    extra = dict(
        next(e for e in historical if e["application_id"] == target["unit_id"] and e["event_type"] == "submitted"),
        id="evt-extra", event_type="recruiter_screen", approval_token_ref=None,
    )  # fmt: skip
    tampered = dict(analysis, counted_event_ids=[*analysis["counted_event_ids"], "evt-extra"])
    found = ei.codes(ei.check_experiment_bundle(spec, assignments, [*historical, extra], tampered))
    assert "EXP-OBSERVATIONAL-AS-RANDOMIZED" in found
    ghost = dict(analysis, counted_event_ids=[*analysis["counted_event_ids"], "evt-does-not-exist"])
    assert "EXP-COUNTED-UNKNOWN-EVENT" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, ghost)
    )


def test_false_certainty_is_flagged(world: World) -> None:
    spec, assignments, events, analysis = world
    assert analysis["decision"] == "INCONCLUSIVE", "seed 1 is a null world and must not detect a difference"
    overclaim = dict(analysis, decision="DIFFERENCE-DETECTED")
    assert "EXP-FALSE-CERTAINTY" in ei.codes(ei.check_experiment_bundle(spec, assignments, events, overclaim))
    winner = dict(analysis, warnings=["Variant B is the winner"])
    assert "EXP-FALSE-CERTAINTY" in ei.codes(ei.check_experiment_bundle(spec, assignments, events, winner))
    odds = dict(analysis, summary="B doubles your chances of getting an interview")
    assert "EXP-FALSE-CERTAINTY" in ei.codes(ei.check_experiment_bundle(spec, assignments, events, odds))
    no_interval = dict(analysis, effect={"estimate": 0.2})
    assert "EXP-NO-UNCERTAINTY" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, no_interval)
    )
    per_protocol = dict(analysis, estimand="per-protocol")
    assert "EXP-NOT-INTENTION-TO-TREAT" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, per_protocol)
    )
    vocabulary = dict(analysis, decision="B WINS")
    assert "EXP-DECISION-VOCABULARY" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, vocabulary)
    )


def test_small_sample_stays_inconclusive_even_with_a_large_observed_gap() -> None:
    spec, assignments, events, analysis = ei.synth_experiment_world(
        seed=4, n_units=24, rate_a=0.05, rate_b=0.9
    )
    assert analysis["decision"] == "INCONCLUSIVE"
    assert any("Small sample" in w for w in analysis["warnings"])
    forced = dict(analysis, decision="DIFFERENCE-DETECTED")
    assert "EXP-FALSE-CERTAINTY" in ei.codes(ei.check_experiment_bundle(spec, assignments, events, forced))


@pytest.mark.slow
def test_reference_analysis_false_alarm_rate_and_power_on_held_out_seeds() -> None:
    """Calibration of lane E's own ITT oracle on 300 null worlds and 60 strong-effect worlds."""
    null = [ei.synth_experiment_world(seed=1000 + s, n_units=160)[3]["decision"] for s in range(300)]
    false_alarm = sum(d == "DIFFERENCE-DETECTED" for d in null) / len(null)
    strong = [
        ei.synth_experiment_world(seed=5000 + s, n_units=200, rate_a=0.15, rate_b=0.50)[3]["decision"]
        for s in range(60)
    ]
    power = sum(d == "DIFFERENCE-DETECTED" for d in strong) / len(strong)
    print(
        f"\nITT oracle: false-alarm rate {false_alarm:.3f} over {len(null)} null worlds; power {power:.3f} at 0.15 vs 0.50, n=200"
    )
    assert false_alarm <= 0.08, "nominal 5% two-sided interval"
    assert power >= 0.95

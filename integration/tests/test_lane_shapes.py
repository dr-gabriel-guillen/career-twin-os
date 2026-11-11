"""Mappings from lane-native payloads onto E's checks, and the validator corrections made after the
first real integration run against lanes A, B and C (cycle 1 → cycle 2).

Each test pins a mistake E made and fixed; the shapes are copied from real lane output at the SHAs
named in the docstrings (synthetic data only)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from ctint import e2e, synth
from ctint import experiment_integrity as ei

# ----------------------------------------------------------------------------- lane B report / journal


def _b_world() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """E's honest synthetic world re-expressed in lane B's native field names (B @ 84757ed)."""
    spec, assignments, events, analysis = copy.deepcopy(ei.synth_experiment_world(seed=2, n_units=120))
    spec["analysis_plan"] = "ITT on job units; stratified permutation test; Holm; missing = no success."
    native_assignments = [
        {"assignment_id": a["id"], "experiment_id": a["experiment_id"], "design_hash": "d" * 64,
         "application_id": a["application_ids"][0], "unit_id": a["unit_id"], "unit_kind": "application",
         "stratum": a["stratum"], "variant": a["variant"], "assigned_at": a["assigned_at"]}
        for a in assignments
    ]  # fmt: skip
    arms = {arm["variant"]: arm for arm in analysis["arms"]}
    report = {
        "schema": ei.B_REPORT_SCHEMA, "experiment_id": spec["id"], "design_hash": "d" * 64, "phase": "FINAL",
        "verdict": "INCONCLUSIVE", "claim": None, "evidence_type": "randomized_controlled",
        "reasons_withheld": ["B vs A: NOT_SIGNIFICANT; no winner is declared"],
        "warnings": ["UNDERPOWERED_BY_DESIGN"], "interpretation_limits": ["Not a hiring forecast."],
        "comparisons": [{"treatment": "B", "control": "A", "estimate": analysis["effect"]["estimate"],
                         "interval_95": {"low": analysis["effect"]["ci_low"], "high": analysis["effect"]["ci_high"],
                                         "method": "newcombe"}}],
        "flow": {"by_arm": {v: {"units": arms[v]["n_assigned"], "success": arms[v]["n_success"]} for v in arms}},
    }  # fmt: skip
    return spec, native_assignments, events, report


def test_b_native_bundle_is_checked_without_false_findings() -> None:
    spec, assignments, events, report = _b_world()
    findings = [
        f for f in ei.check_experiment_bundle(spec, assignments, events, report) if f.severity != "low"
    ]
    assert [str(f) for f in findings] == []


def test_b_native_bundle_still_catches_real_problems() -> None:
    spec, assignments, events, report = _b_world()
    dropped = copy.deepcopy(report)
    dropped["flow"]["by_arm"]["B"]["units"] -= 5
    assert "EXP-ANALYSIS-COUNT-MISMATCH" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, dropped)
    )
    second_design = copy.deepcopy(assignments)
    second_design[3]["design_hash"] = "e" * 64
    found = ei.codes(ei.check_experiment_bundle(spec, second_design, events, report))
    assert "EXP-DESIGN-CHANGED-AFTER-ASSIGNMENT" in found
    claimed = dict(report, verdict="EVIDENCE_FOR_DIFFERENCE")
    assert "EXP-FALSE-CERTAINTY" in ei.codes(ei.check_experiment_bundle(spec, assignments, events, claimed))
    interim = dict(report, verdict="EVIDENCE_FOR_DIFFERENCE", phase="INTERIM")
    assert "EXP-DECISION-VOCABULARY" in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, interim)
    )
    orphan = copy.deepcopy(assignments)
    del orphan[0]
    assert "EXP-EXPOSURE-WITHOUT-ASSIGNMENT" in ei.codes(
        ei.check_experiment_bundle(spec, orphan, events, report)
    )


def _with_counted_outcomes(
    report: dict[str, Any],
    assignments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Add B's terminal-report `counted_outcomes[]` (B @ ea01acb), built from E's own honest tally."""
    _, counted = ei.itt_counts(spec, [ei.assignment_view(a) for a in assignments], events)
    by_app = {e["application_id"]: e["id"] for e in events if e["id"] in set(counted)}
    outcomes = [
        {"application_id": a["application_id"], "unit_id": a["unit_id"], "arm": a["variant"],
         "status": "success" if a["application_id"] in by_app else "no_success",
         "counted_event_id": by_app.get(a["application_id"]), "flags": []}
        for a in assignments
    ]  # fmt: skip
    return dict(report, counted_outcomes=outcomes)


def test_b_counted_outcomes_enable_the_outcome_exclusion_checks() -> None:
    spec, assignments, events, report = _b_world()
    full = _with_counted_outcomes(report, assignments, events, spec)
    view = ei.analysis_view(full, spec)
    assert view["counted_event_ids"] and view["counted_success_application_ids"]
    clean = [f for f in ei.check_experiment_bundle(spec, assignments, events, full) if f.severity != "low"]
    assert [str(f) for f in clean] == []

    # a late response counted as a success must now be caught
    target = next(
        a for a in assignments if a["application_id"] not in view["counted_success_application_ids"]
    )
    submitted = next(
        e
        for e in events
        if e["application_id"] == target["application_id"] and e["event_type"] == "submitted"
    )
    late_ts = synth.ts(10_000_000)
    late = dict(submitted, id="evt-late", event_type="recruiter_screen", recorded_at=late_ts, occurred_at=late_ts,
                approval_token_ref=None)  # fmt: skip
    tampered = copy.deepcopy(full)
    for outcome in tampered["counted_outcomes"]:
        if outcome["application_id"] == target["application_id"]:
            outcome.update(status="success", counted_event_id="evt-late")
    found = ei.codes(ei.check_experiment_bundle(spec, assignments, [*events, late], tampered))
    assert {"EXP-LATE-OUTCOME-COUNTED", "EXP-COUNTED-EVENTS-DIFFER"} <= found


def test_duplicate_replies_do_not_create_a_false_counted_mismatch() -> None:
    """B collapses duplicate responses and may cite a different event than E for the same application."""
    spec, assignments, events, report = _b_world()
    full = _with_counted_outcomes(report, assignments, events, spec)
    success = next(o for o in full["counted_outcomes"] if o["status"] == "success")
    original = next(e for e in events if e["id"] == success["counted_event_id"])
    duplicate = dict(original, id="evt-duplicate-reply")
    success["counted_event_id"] = "evt-duplicate-reply"
    found = ei.codes(ei.check_experiment_bundle(spec, assignments, [*events, duplicate], full))
    assert "EXP-COUNTED-EVENTS-DIFFER" not in found


def test_without_counted_event_ids_the_comparison_is_unknown_not_a_failure() -> None:
    """B publishes no counted-event list; E must not invent a mismatch (asked on #3, Q3)."""
    spec, assignments, events, report = _b_world()
    assert "EXP-COUNTED-EVENTS-DIFFER" not in ei.codes(
        ei.check_experiment_bundle(spec, assignments, events, report)
    )
    view = ei.analysis_view(report, spec)
    assert "counted_event_ids" not in view


@pytest.mark.parametrize(
    ("plan", "estimand"),
    [
        ("ITT on job units; stratified permutation test", "ITT"),  # regression: B @ 84757ed
        ("Intention-to-treat; Holm across arms", "ITT"),
        ("intention to treat difference in proportions", "ITT"),
        ("Per-protocol analysis of submitted applications only", "UNSTATED"),
        ("Submitted units, permitted adjustments", "UNSTATED"),  # 'permitted' must not match \bitt\b
    ],
)
def test_estimand_recognition(plan: str, estimand: str) -> None:
    spec, _, _, report = _b_world()
    assert ei.analysis_view(report, dict(spec, analysis_plan=plan))["estimand"] == estimand


@pytest.mark.parametrize(
    ("text", "declares"),
    [
        ("Variant B is the winner", True),
        ("Winning variant: B", True),
        ("No winner is declared", False),
        ("winner claim withheld: interval includes zero", False),
        ("B vs A: NOT_SIGNIFICANT; no winner is declared", False),
        ("We never name a winner at interim", False),
    ],
)
def test_affirmative_winner(text: str, declares: bool) -> None:
    assert ei.affirmative_winner({"nested": [text]}) is declares


def test_assignment_view_accepts_both_shapes() -> None:
    native = {"assignment_id": "asg-1", "design_hash": "h", "application_id": "app-1", "variant": "A"}
    view = ei.assignment_view(native)
    assert (view["id"], view["spec_digest"], view["application_ids"]) == ("asg-1", "h", ["app-1"])
    mine = {"id": "x", "spec_digest": "s", "application_ids": ["a", "b"]}
    assert ei.assignment_view(mine) == mine
    assert ei.assignment_view("junk") == {}  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- stage validators


def _write(directory: Path, name: str, value: Any) -> None:
    (directory / name).write_text(json.dumps(value), encoding="utf-8")


def test_v_jobs_accepts_fictional_ats_shaped_postings_and_rejects_unmarked(tmp_path: Path) -> None:
    """Lane A emits source 'greenhouse' with a fictional employer (RFC #12); E's first rule rejected it."""
    jobs = synth.gen_jobs(12, seed=4)
    ats_shaped = [dict(j, source="greenhouse") for j in jobs]
    _write(tmp_path, "jobs.json", ats_shaped)
    assert e2e.v_jobs(tmp_path, 12) == []
    unmarked = copy.deepcopy(ats_shaped)
    unmarked[0]["organization"] = "Meridian Systems"
    unmarked[0]["license_note"] = "Imported posting"
    _write(tmp_path, "jobs.json", unmarked)
    assert any("not marked fictional" in p for p in e2e.v_jobs(tmp_path, 12))


def test_v_resume_accepts_b_native_variant(tmp_path: Path) -> None:
    claims = synth.gen_claims(seed=1)
    usable = [c for c in claims if c["status"] in e2e.USABLE_CLAIM_STATUSES][:3]
    _write(tmp_path, "claims.json", claims)
    _write(tmp_path, "pin.json", {"job_id": "job-x"})
    native = {
        "schema": "careertwin.b.resume-variant.v1", "id": "rv-1", "job_id": "job-x",
        "bullets": [{"claim_id": c["id"], "text": c["claim_text"], "claim_status": c["status"]} for c in usable],
    }  # fmt: skip
    _write(tmp_path, "resume_variant.json", native)
    assert e2e.v_resume(tmp_path, 0) == []
    forged = copy.deepcopy(native)
    forged["bullets"][0]["claim_id"] = "claim-that-does-not-exist"
    _write(tmp_path, "resume_variant.json", forged)
    assert any("fabricated evidence" in p for p in e2e.v_resume(tmp_path, 0))


def test_v_atlas_allows_reported_unplaced_vectors_only(tmp_path: Path) -> None:
    from ctint import atlas_quality

    jobs = synth.gen_jobs(60, seed=6)
    vectors = synth.gen_vectors(jobs)
    labels = {j["id"]: j["title"] for j in jobs}
    points, diagnostics = atlas_quality.build_reference_atlas(vectors, labels, "proj-t")
    vectors["vectors"].append(
        {
            "ref": "vec:blank",
            "entity_id": "job-blank",
            "values": [0.0] * len(vectors["vocabulary"]),
            "observed": False,
        }
    )
    _write(tmp_path, "vectors.json", vectors)
    _write(tmp_path, "atlas_points.json", points)
    _write(
        tmp_path,
        "atlas_diagnostics.json",
        dict(diagnostics, unplaced=[{"entity_id": "job-blank", "reason": "no skills"}]),
    )
    assert e2e.v_atlas(tmp_path, 0) == []
    _write(tmp_path, "atlas_diagnostics.json", diagnostics)
    assert any("without an `unplaced` entry" in p for p in e2e.v_atlas(tmp_path, 0))
    _write(tmp_path, "atlas_points.json", points[1:])
    assert any("neither projected nor reported" in p for p in e2e.v_atlas(tmp_path, 0))

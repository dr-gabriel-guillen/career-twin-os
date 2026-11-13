"""Pipeline probes against in-process fake providers: input sensitivity with and without persisted
lane state (lane B's InputDrift fix, 755ccae) and same-lane sidecar updates (lane C, f76e24c)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ctint import e2e, synth
from ctint.e2e import RunOutcome, Stage


class FakeJobs:
    """jobs.import provider whose behaviour on re-runs is configurable."""

    name = "fake"
    mock = False
    is_stub = False

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def run(self, stage: Stage, artifacts: Path, seed: int, count: int) -> RunOutcome:
        state = artifacts / "lane-x"
        if self.mode in ("stale", "refuse") and state.is_dir():
            if self.mode == "refuse" and json.loads((state / "inputs.json").read_text()) != [seed, count]:
                return RunOutcome(False, "exit code 2; InputDrift")
            (artifacts / "jobs.json").write_text((state / "jobs.json").read_text(), encoding="utf-8")
            return RunOutcome(True)
        jobs = synth.gen_jobs(count if self.mode != "canned" else 12, seed if self.mode != "canned" else 0)
        (artifacts / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        if self.mode in ("stale", "refuse"):
            state.mkdir(exist_ok=True)
            (state / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
            (state / "inputs.json").write_text(json.dumps([seed, count]), encoding="utf-8")
        return RunOutcome(True)


def _run(tmp_path: Path, provider: Any) -> list[str]:
    stage = e2e.STAGE_BY_ID["jobs.import"]
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    assert provider.run(stage, artifacts, 7, 12).ok
    return e2e._probe(stage, provider, artifacts, tmp_path / "probe", 7, 12)


def test_stage_that_computes_from_inputs_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, FakeJobs("honest")) == []


def test_canned_output_is_reported(tmp_path: Path) -> None:
    assert any(p.startswith("CANNED-OUTPUT") for p in _run(tmp_path, FakeJobs("canned")))


def test_refusing_drifted_input_with_persisted_state_is_correct(tmp_path: Path) -> None:
    """Lane B @ 755ccae exits 2 (InputDrift) on a stateful re-run; the fresh re-run still recomputes."""
    assert _run(tmp_path, FakeJobs("refuse")) == []


def test_silently_reusing_persisted_state_is_stale(tmp_path: Path) -> None:
    """Lane B @ 84757ed returned the journaled result for drifted inputs (B-F1)."""
    problems = _run(tmp_path, FakeJobs("stale"))
    assert [p.split(":")[0] for p in problems] == ["STALE-STATE"]


def test_scenario_must_use_the_atlas_stage_projection(tmp_path: Path) -> None:
    """D's proposal on #5 after C's two stages fitted different models (fixed in C f76e24c)."""
    claims = synth.gen_claims(seed=1)
    scenario = synth.gen_scenarios(1, seed=1)[0]
    scenario["outputs"]["model_ref"] = {"projection_id": "pca-model-a"}
    (tmp_path / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    (tmp_path / "scenarios.json").write_text(json.dumps([scenario]), encoding="utf-8")
    (tmp_path / "atlas_diagnostics.json").write_text(
        json.dumps({"projection_id": "pca-model-a"}), encoding="utf-8"
    )
    assert e2e.v_scenarios(tmp_path, 0) == []
    (tmp_path / "atlas_diagnostics.json").write_text(
        json.dumps({"projection_id": "pca-model-b"}), encoding="utf-8"
    )
    assert any("cannot be drawn" in p for p in e2e.v_scenarios(tmp_path, 0))


def test_same_lane_sidecar_update_is_allowed_but_cross_lane_or_contract_changes_are_not() -> None:
    scenario = e2e.STAGE_BY_ID["simulator.scenario"]  # lane C
    producer = {"atlas.pca.json": "C", "jobs_ext.json": "A", "atlas_points.json": "C"}
    tampered, own = e2e.classify_modified(["atlas.pca.json"], producer, scenario)
    assert (tampered, own) == ([], ["atlas.pca.json"])
    tampered, own = e2e.classify_modified(
        ["jobs_ext.json", "atlas_points.json", "mystery.json"], producer, scenario
    )
    assert own == [] and tampered == ["jobs_ext.json", "atlas_points.json", "mystery.json"]
    assert "pin.json" in e2e.DECLARED_ARTIFACTS
